"""账号池（mashu-studio #4）：Codex / Gemini 多账号的账号库、脱敏、选号、续期（假的 Google / OpenAI 接口）、按号目录找 rollout、
开会话按号选 CODEX_HOME、Gemini 全局切号的条件。不联网、不碰真钥匙串。

  python3 -m unittest tests.test_accounts -v
"""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import studio, wait_for  # noqa: E402
from test_platforms import PlatBase  # noqa: E402
import accounts as acct  # noqa: E402
import platforms as plat  # noqa: E402

RT1 = '1//0gFAKE-refresh-token-number-one-abcdefghijklmnopqrstuvwxyz'
RT2 = '1//0gFAKE-refresh-token-number-two-abcdefghijklmnopqrstuv0002'
CX_RT = 'rt_FAKEcodexrefreshtokenABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'


def jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()  # noqa: E731
    return f'{enc({"alg": "none"})}.{enc(claims)}.sig'


def codex_auth(email='a@x.com', plan='plus', account='acc-1', rt=CX_RT, last='2026-11-20T08:33:19.971821Z'):
    return dict(auth_mode='chatgpt', OPENAI_API_KEY=None, last_refresh=last,
                tokens=dict(id_token=jwt({'email': email, 'https://api.openai.com/auth': {'chatgpt_plan_type': plan, 'chatgpt_account_id': account}}),
                            access_token=jwt({'exp': int(time.time()) + 3600}), refresh_token=rt, account_id=account,
                            oauth_provenance={'issuer': 'https://auth.openai.com', 'client_id': 'app_test'}))


def rollout(home, sid, used5, usedw, day='2026/11/26'):
    p = Path(home) / 'sessions' / day / f'rollout-2026-11-26T00-00-00-{sid}.jsonl'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({'type': 'event_msg', 'timestamp': '2026-11-26T00:00:00Z', 'payload': {'type': 'token_count', 'rate_limits': {
        'primary': {'used_percent': used5, 'window_minutes': 300, 'resets_at': 1795500000},
        'secondary': {'used_percent': usedw, 'window_minutes': 10080, 'resets_at': 1795600000}}}}) + '\n')
    return p


class FakeHTTP:
    """假的 Google / OpenAI 接口：按 URL 和 RT 回预设的结果，记下所有请求。"""

    def __init__(self):
        self.calls = []
        self.revoked = set()
        self.good_client = 'cid-good.apps.googleusercontent.com'
        self.rotate = {}        # Google RT → 新 RT
        self.codex_rotate = {}

    def __call__(self, method, url, data=None, headers=None, timeout=20):
        self.calls.append((method, url, dict(data or {}), dict(headers or {})))
        if url == acct.GOOGLE_TOKEN_URL:
            if data.get('client_id') != self.good_client or data.get('client_secret') != 'GOCSPX-good':
                return 401, {'error': 'invalid_client'}
            rt = data.get('refresh_token')
            if rt in self.revoked:
                return 400, {'error': 'invalid_grant', 'error_description': f'Token has been expired or revoked: {rt}'}
            body = {'access_token': f'ya29.FAKE-access-{rt[-6:]}-{len(self.calls)}', 'expires_in': 3599, 'token_type': 'Bearer',
                    'id_token': jwt({'email': 'g1@x.com' if rt == RT1 else 'g2@x.com'})}
            if rt in self.rotate:
                body['refresh_token'] = self.rotate[rt]
            return 200, body
        if url == acct.GOOGLE_USERINFO_URL:
            at = headers['Authorization'].split()[-1]
            return 200, {'email': 'g1@x.com' if RT1[-6:] in at else 'g2@x.com', 'verified_email': True}
        if url == acct.CODE_ASSIST_MODELS_URL:
            return 200, {'models': {
                'gemini-3-flash': {'displayName': 'Gemini 3 Flash', 'quotaInfo': {'remainingFraction': 0.8, 'resetTime': '2026-11-26T11:32:58Z'}},
                'claude-x': {'displayName': 'Claude X', 'quotaInfo': {'remainingFraction': 0.25, 'resetTime': '2026-12-03T06:32:58Z'}},
                'tab_internal': {'quotaInfo': {'remainingFraction': 0.01}}}}
        if url == acct.CODEX_TOKEN_URL:
            rt = data.get('refresh_token')
            if rt in self.revoked:
                return 401, {'error': {'code': 'refresh_token_reused', 'message': f'reused {rt}'}}
            new = self.codex_rotate.get(rt, rt + 'X')
            return 200, {'access_token': jwt({'exp': int(time.time()) + 7200}), 'refresh_token': new,
                         'id_token': jwt({'email': 'a@x.com', 'https://api.openai.com/auth': {'chatgpt_plan_type': 'pro'}})}
        return 404, {'error': 'not found'}


class FakeKeychain:
    def __init__(self, value=None):
        self.value = value
        self.writes = 0

    def read(self):
        return json.loads(json.dumps(self.value)) if self.value else None

    def write(self, value):
        self.value = json.loads(json.dumps(value))
        self.writes += 1


class AcctBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, True))
        self.pool = acct.Pool(self.tmp / 'accounts')
        self.http = FakeHTTP()
        saved = acct.HTTP
        acct.HTTP = self.http
        self.addCleanup(setattr, acct, 'HTTP', saved)
        self.kc = FakeKeychain()
        self.g = acct.Gemini(self.pool, 'agy-not-installed', keychain=self.kc, token_file=self.tmp / 'no-token-file')
        cache = self.pool.root / 'gemini' / 'clients.json'      # 假装从 agy 二进制里扫出来三组候选
        acct.write_json(cache, dict(sig='', pairs=[['cid-bad.apps.googleusercontent.com', 'GOCSPX-bad'],
                                                    [self.http.good_client, 'GOCSPX-bad'], [self.http.good_client, 'GOCSPX-good']]))


class TestPool(AcctBase):
    def test_store_and_permissions(self):
        a = self.pool.add('codex', source='login', email='a@x.com', status='ok')
        b = self.pool.add('codex', source='login', email='b@x.com')
        g = self.pool.add('gemini', source='rt')
        self.assertEqual((a['id'], b['id'], g['id']), ('cx-1', 'cx-2', 'gm-1'))
        self.assertEqual(stat.S_IMODE(self.pool.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.pool.index_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.pool.root / 'codex' / 'cx-1').stat().st_mode), 0o700)
        self.pool.update('cx-2', status='ok', disabled=True)
        self.assertTrue(self.pool.get('cx-2')['disabled'])
        self.assertEqual(self.pool.default('codex')['id'], 'cx-1')      # 没设过：第一个
        self.pool.set_default('codex', 'cx-2')
        self.assertEqual(self.pool.default('codex')['id'], 'cx-2')
        with self.assertRaises(acct.AccountError):
            self.pool.set_default('gemini', 'cx-1')
        self.pool.remove('cx-2')
        self.assertFalse((self.pool.root / 'codex' / 'cx-2').exists())
        self.assertEqual(self.pool.default('codex')['id'], 'cx-1')
        self.assertEqual([x['id'] for x in self.pool.list()], ['cx-1', 'gm-1'])
        p = acct.write_json(self.tmp / 'x' / 'cred.json', {'refresh_token': RT1})
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(list(p.parent.glob('.*tmp')), [])          # 临时文件不留

    def test_concurrent_writes_locked(self):
        errs = []

        def worker(i):
            try:
                a = self.pool.add('gemini', email=f'{i}@x.com')
                self.pool.update(a['id'], status='ok')
            except Exception as exc:     # pragma: no cover
                errs.append(exc)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        ids = [a['id'] for a in self.pool.list()]
        self.assertEqual(len(ids), 24)
        self.assertEqual(len(set(ids)), 24)
        self.assertTrue(all(a['status'] == 'ok' for a in self.pool.list()))

    def test_mask_and_redact(self):
        self.assertEqual(acct.mask(RT1), '1//0…')
        self.assertEqual(acct.mask(''), '')
        text = f'boom {RT1} and ya29.abcdefghijklmnop and {CX_RT} and eyJabcdefghij.eyJklmnopqrs.sig123'
        out = acct.redact(text, [RT1])
        for s in (RT1, 'ya29.abcdefghijklmnop', CX_RT, 'eyJabcdefghij.eyJklmnopqrs.sig123'):
            self.assertNotIn(s, out)
        self.assertIn('1//0…', out)
        a = self.pool.add('gemini')
        self.pool.update(a['id'], error=f'failed with {RT2}')
        self.assertNotIn(RT2, json.dumps(self.pool.view()))
        self.assertNotIn(RT2, self.pool.index_path.read_text())


class TestPick(unittest.TestCase):
    def acc(self, aid, used5=None, usedw=None, status='ok', disabled=False, created=0, plat_='codex', remaining=None):
        q = {}
        if used5 is not None:
            q['five_hour'] = {'used_percentage': used5}
        if usedw is not None:
            q['seven_day'] = {'used_percentage': usedw}
        if remaining is not None:
            q = {'remaining': remaining}
        return dict(id=aid, platform=plat_, status=status, disabled=disabled, created=created, quota=q or None)

    def test_rules(self):
        accs = [self.acc('cx-1', 70, 10, created=1), self.acc('cx-2', 20, 40, created=2), self.acc('cx-3', 5, 5, status='revoked'),
                self.acc('cx-4', 1, 1, disabled=True)]
        a, why = acct.pick(accs)
        self.assertEqual(a['id'], 'cx-2')            # 最大用量 40 < 70；撤销 / 禁用的不选
        self.assertIn('自动', why)
        a, why = acct.pick(accs, want='cx-1')
        self.assertEqual((a['id'], why), ('cx-1', '指定'))
        a, why = acct.pick(accs, want='cx-3')
        self.assertEqual(a['id'], 'cx-2')
        self.assertIn('改为自动', why)
        # 额度一样：默认号优先；额度未知当 0
        accs = [self.acc('cx-1', 10, 10, created=1), self.acc('cx-2', 10, 10, created=2)]
        self.assertEqual(acct.pick(accs, default_id='cx-2')[0]['id'], 'cx-2')
        self.assertEqual(acct.pick([self.acc('cx-1', 30, 0), self.acc('cx-2')])[0]['id'], 'cx-2')
        # 用满的排最后
        self.assertEqual(acct.pick([self.acc('cx-1', 100, 0), self.acc('cx-2', 99, 99)])[0]['id'], 'cx-2')
        self.assertEqual(acct.pick([self.acc('cx-1', status='pending')]), (None, '没有能用的号'))
        # Gemini：剩余比例
        g = [self.acc('gm-1', plat_='gemini', remaining=0.2), self.acc('gm-2', plat_='gemini', remaining=0.9)]
        self.assertEqual(acct.pick(g)[0]['id'], 'gm-2')
        self.assertEqual(acct.used_pct(g[0]), 80.0)


class TestGemini(AcctBase):
    def test_import_refresh_quota(self):
        a = self.g.import_rt(RT1)
        self.assertEqual((a['id'], a['email'], a['status']), ('gm-1', 'g1@x.com', 'ok'))
        self.assertAlmostEqual(a['quota']['remaining'], 0.25)
        self.assertEqual(a['quota']['low_model'], 'Claude X')
        self.assertEqual(len(a['quota']['models']), 2)          # 没有 displayName 的内部模型不算
        cred = self.g.cred('gm-1')
        self.assertEqual(cred['refresh_token'], RT1)
        self.assertEqual(stat.S_IMODE(self.g.cred_path('gm-1').stat().st_mode), 0o600)
        # 三组候选客户端：不认的换下一个，认的记住、下次排第一
        tries = [c[2]['client_id'] + '/' + c[2]['client_secret'] for c in self.http.calls if c[1] == acct.GOOGLE_TOKEN_URL]
        self.assertEqual(tries[-1], f'{self.http.good_client}/GOCSPX-good')
        self.assertEqual(self.g.clients()[0], (self.http.good_client, 'GOCSPX-good'))
        # 同一个邮箱再导入：更新原来那个号
        self.assertEqual(self.g.import_rt(RT1)['id'], 'gm-1')
        self.assertEqual(len(self.pool.list('gemini')), 1)
        # 索引和接口里没有令牌原文
        idx = self.pool.index_path.read_text()
        self.assertNotIn(RT1, idx)
        self.assertNotIn('ya29.', idx)
        self.assertNotIn(RT1, json.dumps(self.pool.view()))

    def test_ensure_token_margin_and_rotation(self):
        self.g.import_rt(RT1)
        n = len(self.http.calls)
        self.g.ensure_token('gm-1')                 # 还有 1 小时：不续
        self.assertEqual(len(self.http.calls), n)
        c = self.g.cred('gm-1')
        c['expiry'] = time.time() + 120             # 不到 5 分钟：续期；Google 回了新 RT 就换上
        acct.write_json(self.g.cred_path('gm-1'), c)
        self.http.rotate[RT1] = RT2
        at = self.g.ensure_token('gm-1')
        self.assertTrue(at.startswith('ya29.FAKE-access'))
        self.assertEqual(self.g.cred('gm-1')['refresh_token'], RT2)
        self.assertGreater(self.g.cred('gm-1')['expiry'], time.time() + 3000)

    def test_invalid_grant_marks_revoked(self):
        self.g.import_rt(RT1)
        c = self.g.cred('gm-1')
        c['expiry'] = 0
        acct.write_json(self.g.cred_path('gm-1'), c)
        self.http.revoked.add(RT1)
        with self.assertRaises(acct.Revoked):
            self.g.check('gm-1')
        a = self.pool.get('gm-1')
        self.assertEqual(a['status'], 'revoked')
        self.assertIn('invalid_grant', a['error'])
        self.assertNotIn(RT1, a['error'])
        self.assertIsNone(acct.pick(self.pool.list('gemini'))[0])
        with self.assertRaises(acct.Revoked):        # 导入时就失效：不建号
            self.g.import_rt(RT1)

    def test_bad_rt_format(self):
        for bad in ('', 'short', '1//abc def ghijklmnopqrstuvwxyz'):
            with self.assertRaises(acct.AccountError):
                self.g.import_rt(bad)
        self.assertEqual(self.pool.list(), [])

    def test_keychain_import_and_switch(self):
        self.kc.value = {'token': {'access_token': 'ya29.old', 'token_type': 'Bearer', 'refresh_token': RT1, 'expiry': 'x'},
                         'auth_method': 'consumer', 'id_token_email': 'g1@x.com'}
        a = self.g.import_local()
        self.assertEqual((a['source'], self.pool.default('gemini')['id']), ('local', 'gm-1'))
        self.assertEqual(self.g.active_id(), 'gm-1')
        b = self.g.import_rt(RT2)
        self.assertEqual(b['id'], 'gm-2')
        self.g.activate('gm-2')
        self.assertEqual(self.g.active_id(), 'gm-2')
        v = self.kc.value
        self.assertEqual(v['token']['refresh_token'], RT2)
        self.assertEqual(v['auth_method'], 'consumer')
        self.assertEqual(v['id_token_email'], 'g2@x.com')
        self.assertTrue(v['token']['access_token'].startswith('ya29.FAKE'))
        self.assertFalse((self.tmp / 'no-token-file').exists())        # 备用文件本来没有：不建
        raw = acct.encode_keyring(v)
        self.assertTrue(raw.startswith('go-keyring-base64:'))
        self.assertEqual(acct.decode_keyring(raw), v)

    def test_scan_clients_from_binary(self):
        b = self.tmp / 'fake-agy-bin'
        b.write_bytes(b'\x00junk 123456789-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com \x00 GOCSPX-' + b'A' * 28 + b'\x00')
        ids, secs = acct.scan_clients(b)
        self.assertEqual(ids, ['123456789-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com'])
        self.assertEqual(secs, ['GOCSPX-' + 'A' * 28])
        w = self.tmp / 'agy'
        w.write_text(f'#!/bin/bash\nexec "{b}" "$@"\n')
        self.assertEqual(acct.agy_binary(str(w)).resolve(), b.resolve())


class TestCodex(AcctBase):
    def test_auth_info_and_refresh(self):
        home = self.pool.root / 'codex' / 'cx-1'
        acct.write_json(home / 'auth.json', codex_auth())
        info = acct.codex_auth_info(home / 'auth.json')
        self.assertEqual((info['logged_in'], info['email'], info['plan'], info['account_id']), (True, 'a@x.com', 'plus', 'acc-1'))
        self.assertAlmostEqual(info['last_refresh'], datetime(2026, 11, 20, 8, 33, 19, 971821, tzinfo=timezone.utc).timestamp(), places=2)
        self.assertFalse(acct.codex_auth_info(home / 'missing.json')['logged_in'])
        self.http.codex_rotate[CX_RT] = CX_RT + 'NEW'
        info = acct.codex_refresh(home)
        d = json.loads((home / 'auth.json').read_text())
        self.assertEqual(d['tokens']['refresh_token'], CX_RT + 'NEW')     # OpenAI 换了新 RT：写回
        self.assertEqual(stat.S_IMODE((home / 'auth.json').stat().st_mode), 0o600)
        self.assertEqual(info['plan'], 'pro')
        self.assertGreater(info['last_refresh'], time.time() - 60)
        req = [c for c in self.http.calls if c[1] == acct.CODEX_TOKEN_URL][-1]
        self.assertEqual(req[2]['client_id'], 'app_test')                  # 用 auth.json 里记的客户端
        self.http.revoked.add(CX_RT + 'NEW')
        with self.assertRaises(acct.Revoked) as cm:
            acct.codex_refresh(home)
        self.assertNotIn(CX_RT, str(cm.exception))
        # 本机默认号（软链接）不碰
        link_home = self.tmp / 'linked'
        link_home.mkdir()
        (link_home / 'auth.json').symlink_to(home / 'auth.json')
        with self.assertRaises(acct.AccountError):
            acct.codex_refresh(link_home)

    def test_parse_login_output(self):
        dev = ('Welcome to Codex [v\x1b[90m0.166.0\x1b[0m]\n1. Open this link in your browser and sign in to your account\n'
               '   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n\n2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n'
               '   \x1b[94mABCD-EFGH2\x1b[0m\n')
        self.assertEqual(acct.parse_login_output(dev), dict(url='https://auth.openai.com/codex/device', code='ABCD-EFGH2'))
        br = 'Starting local login server on http://localhost:1455.\nnavigate to this URL to authenticate:\n\nhttps://auth.openai.com/oauth/authorize?a=1&b=2\n'
        self.assertEqual(acct.parse_login_output(br), dict(url='https://auth.openai.com/oauth/authorize?a=1&b=2', code=''))

    def test_find_rollout_across_homes(self):
        h1, h2 = self.tmp / 'h1', self.tmp / 'h2'
        rollout(h1, '0199aaaa-1111', 10, 10)
        p = rollout(h2, '0199bbbb-2222', 10, 10)
        self.assertEqual(plat.find_codex_session([h1, h2], '0199bbbb-2222'), (p, h2))
        self.assertEqual(plat.find_codex_session([h1, h2], '0199cccc-3333'), (None, None))


class TestStudioPool(PlatBase):
    """studio 里的账号池：开 Codex 会话按号选 CODEX_HOME、指定号、恢复回原号目录、额度按号、接口不带令牌、Gemini 切号条件。"""

    def setUp(self):
        super().setUp()
        self.http = FakeHTTP()
        saved = acct.HTTP
        acct.HTTP = self.http
        self.addCleanup(setattr, acct, 'HTTP', saved)
        self.saved_thr = studio.ACCOUNTS_THREADED
        studio.ACCOUNTS_THREADED = False
        self.addCleanup(setattr, studio, 'ACCOUNTS_THREADED', self.saved_thr)
        studio._ACCT_Q.clear()
        self.addCleanup(studio._ACCT_Q.clear)
        (self.user_codex / 'auth.json').write_text(json.dumps(codex_auth(email='me@x.com', account='acc-me')))
        self.p = studio.pool()
        self.local = studio.codex_import_local()
        self.cx2 = self.p.add('codex', source='login', status='ok', email='two@x.com', label='two@x.com')
        acct.write_json(studio.codex_acct_home(self.cx2) / 'auth.json', codex_auth(email='two@x.com', account='acc-2', last='2026-10-01T00:00:00Z'))

    def test_local_registered_without_copy(self):
        self.assertEqual((self.local['id'], self.local['source'], self.local['email']), ('cx-1', 'local', 'me@x.com'))
        self.assertEqual(studio.codex_acct_home(self.local), studio.HOME / 'codex-home')
        self.assertFalse((self.p.root / 'codex' / 'cx-1' / 'auth.json').exists())
        self.assertEqual(self.p.default('codex')['id'], 'cx-1')

    def test_launch_picks_account_home(self):
        rollout(studio.HOME / 'codex-home', '0199aaaa-0001', 80, 30)        # 本机号用了 80%
        rollout(studio.codex_acct_home(self.cx2), '0199aaaa-0002', 10, 20)  # 第二个号用了 20%
        f, key, cam = studio.pitch(self.conn, 'builda', 'pa', '需求 pa', touches=['a/**'])
        studio.set_pref(self.conn, f'builda/{key}', provider='codex')
        studio.tick(self.conn)
        log = wait_for(lambda: self.flog('codex', key))
        home2 = studio.codex_acct_home(self.cx2)
        self.assertIn(f'CODEX_HOME={home2}', log)
        self.assertEqual(studio.meta_get(self.conn, f'camacct:{cam["id"]}'), 'cx-2')
        self.assertFalse((home2 / 'auth.json').is_symlink())                # 池里的号用自己的 auth.json
        self.assertIn('hook-pretool --codex', (home2 / 'config.toml').read_text())
        self.assertIn(f'[projects.{json.dumps(os.path.realpath(self.repo))}]', (home2 / 'config.toml').read_text())
        # 指定号：用指定的
        f, key2, cam2 = studio.pitch(self.conn, 'builda', 'pb', '需求 pb', touches=['b/**'])
        studio.set_pref(self.conn, f'builda/{key2}', provider='codex')
        msg = studio.set_pref(self.conn, f'builda/{key2}', xacct='cx-1')
        self.assertIn('下次新会话', msg)
        studio.tick(self.conn)
        log2 = wait_for(lambda: self.flog('codex', key2))
        self.assertIn(f'CODEX_HOME={studio.HOME / "codex-home"}', log2)
        with self.assertRaises(ValueError):
            studio.set_pref(self.conn, f'builda/{key2}', xacct='gm-9')
        # 恢复会话：回到 rollout 所在的号目录（中途不换号），即使对话改成指定别的号
        studio.set_pref(self.conn, f'builda/{key}', xacct='cx-1')
        c = studio.camera(self.conn, cam['id'])
        cmd, sid, env, _ = studio.codex_cmd(self.conn, c, self.repo, '0199aaaa-0002', lambda t, x: x, True)
        self.assertEqual(env['CODEX_HOME'], str(home2))
        self.assertEqual(cmd[1:3], ['resume', '0199aaaa-0002'])
        self.assertEqual(studio.find_codex_rollout('0199aaaa-0002').parent.parent.parent.parent.parent, home2)
        self.assertTrue(studio.has_transcript(self.repo, '0199aaaa-0002'))

    def test_no_pool_keeps_old_behavior(self):
        self.p.remove('cx-2')
        self.p.remove('cx-1')
        f, key, cam = studio.pitch(self.conn, 'builda', 'pc', '需求 pc')
        studio.set_pref(self.conn, f'builda/{key}', provider='codex')
        studio.tick(self.conn)
        log = wait_for(lambda: self.flog('codex', key))
        self.assertIn(f'CODEX_HOME={studio.HOME / "codex-home"}', log)
        self.assertTrue((studio.HOME / 'codex-home' / 'auth.json').is_symlink())

    def test_exec_account_and_limits(self):
        self.assertEqual(studio.check_exec(dict(cli='codex', account='cx-2'))['account'], 'cx-2')
        self.assertNotIn('account', studio.check_exec(dict(cli='claude', account='cx-2')))
        with self.assertRaises(ValueError):
            studio.check_exec(dict(cli='codex', account='gm-1'))
        rollout(studio.HOME / 'codex-home', '0199aaaa-0003', 55, 66)
        rollout(studio.codex_acct_home(self.cx2), '0199aaaa-0004', 11, 22)
        studio._ACCT_Q.clear()
        lim = studio.collect_limits()
        self.assertEqual(lim['codex']['account'], 'cx-1')                       # 左下角显示默认号
        self.assertEqual(lim['codex']['five_hour']['used_percentage'], 55)
        by = {a['id']: a for a in lim['codex']['accounts']}
        self.assertEqual(by['cx-2']['quota']['five_hour']['used_percentage'], 11)
        self.assertEqual(by['cx-2']['used'], 22)

    def test_account_api_no_tokens(self):
        self.p.update('cx-2', error=f'boom {CX_RT}')
        out = json.dumps(studio.account_action('list'), ensure_ascii=False, default=str)
        self.assertNotIn(CX_RT, out)
        self.assertIn('cx-2', out)
        self.assertIn('已禁用', studio.account_action('disable', dict(id='cx-2'))['result'])
        self.assertTrue(self.p.get('cx-2')['disabled'])
        self.assertIn('默认号改成', studio.account_action('default', dict(id='cx-2'))['result'])
        self.assertEqual(self.p.default('codex')['id'], 'cx-2')
        with self.assertRaises(ValueError):
            studio.account_action('activate', dict(id='cx-2'))
        # 体检：读 auth.json 补邮箱 / 套餐；超过 7 天没续期的池号用 RT 续一次（本机号不碰）
        studio.account_action('enable', dict(id='cx-2'))
        res = dict(studio.accounts_check(force=True))
        self.assertEqual(res['cx-2'], 'ok')
        self.assertEqual(self.p.get('cx-2')['plan'], 'pro')
        self.assertEqual([c for c in self.http.calls if c[1] == acct.CODEX_TOKEN_URL][-1][2]['refresh_token'], CX_RT)
        self.assertEqual(json.loads((self.user_codex / 'auth.json').read_text())['tokens']['refresh_token'], CX_RT)   # 用户的没动
        # 正在被会话用的号不能删
        studio.meta_set(self.conn, 'camacct:999', 'cx-2')
        self.conn.execute('INSERT INTO cameras(id, role, name, state) VALUES(999, "crew", "x", "running")')
        self.conn.commit()
        with self.assertRaises(ValueError):
            studio.account_action('remove', dict(id='cx-2'))
        self.conn.execute('DELETE FROM cameras WHERE id=999')
        self.conn.commit()
        self.assertIn('只是取消登记', studio.account_action('remove', dict(id='cx-1'))['result'])
        self.assertTrue((self.user_codex / 'auth.json').exists())

    def test_web_codex_login_job(self):
        """网页「加号（设备码）」：codex login 只往终端打链接和一次性码（管道里什么都没有），任务要用伪终端读出来；登录完成自动登记。"""
        auth = json.dumps(codex_auth(email='three@x.com', account='acc-3'))
        fake = self.tmp / 'fake-codex-login'
        fake.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "codex-cli 0.166.0"; exit 0; fi\n'
                        '[ -t 1 ] || exit 3\n'
                        'printf "1. Open this link\\n   \\033[94mhttps://auth.openai.com/codex/device\\033[0m\\n2. Enter this one-time code (expires)\\n   ABCD-12345\\n"\n'
                        'sleep 1\n'
                        f"cat > \"$CODEX_HOME/auth.json\" <<'J'\n{auth}\nJ\n")
        fake.chmod(0o755)
        studio.CONFIG_OVERRIDES['codex'] = str(fake)
        d = studio.account_action('add-codex', dict(device=True))
        seen = wait_for(lambda: studio.account_action('job', dict(job=d['job']))['job'].get('code'), timeout=10)
        self.assertEqual(seen, 'ABCD-12345')
        self.assertEqual(studio.account_action('job', dict(job=d['job']))['job']['url'], 'https://auth.openai.com/codex/device')
        j = wait_for(lambda: (lambda x: x if x['state'] in ('done', 'failed') else None)(studio.account_action('job', dict(job=d['job']))['job']), timeout=15)
        self.assertEqual(j['state'], 'done', j)
        a = next(x for x in self.p.list('codex') if x.get('email') == 'three@x.com')
        self.assertEqual(a['status'], 'ok')
        self.assertFalse((studio.codex_acct_home(a) / 'auth.json').is_symlink())

    def test_gemini_single_account(self):
        """Gemini 只留单号（#157）：只跟着本机钥匙串的号续期、查额度；不能加第二个号、不能指定 / 自动挑号、不切号。"""
        kc = FakeKeychain({'token': {'refresh_token': RT1, 'access_token': 'ya29.old'}, 'auth_method': 'consumer'})
        g = acct.Gemini(self.p, 'agy-not-installed', keychain=kc, token_file=self.tmp / 'none')
        acct.write_json(self.p.root / 'gemini' / 'clients.json', dict(sig='', pairs=[[self.http.good_client, 'GOCSPX-good']]))
        saved = studio.gemini_ops
        self.addCleanup(setattr, studio, 'gemini_ops', saved)
        studio.gemini_ops = lambda: g
        self.assertIn('g1@x.com', studio.account_action('import-local', dict(platform='gemini'))['result'])
        self.assertEqual([a['id'] for a in self.p.list('gemini')], ['gm-1'])
        for act, data in (('add-gemini-rt', dict(rt=RT2)), ('add-gemini-login', {}), ('activate', dict(id='gm-1')), ('relogin', dict(id='gm-1'))):
            with self.assertRaises(ValueError) as cm:
                studio.account_action(act, data)
            self.assertIn('只管本机 agy 当前登录的这一个号', str(cm.exception))
        with self.assertRaises(SystemExit):
            studio.main(['account', 'add', 'gemini'])
        self.assertEqual(len(self.p.list('gemini')), 1)
        cam = dict(id=1, role='assistant', film_id=None, seq_key=None, name='麻薯')
        self.assertEqual(studio.choose_account(self.conn, cam, 'gemini'), (None, ''))     # 不挑号
        self.assertNotIn('account', studio.check_exec(dict(cli='gemini', account='gm-1')))
        # 用户自己用 agy 换了号：体检时跟上，旧号删掉；钥匙串一次也没被麻薯写过
        kc.value = {'token': {'refresh_token': RT2, 'access_token': 'ya29.new'}, 'auth_method': 'consumer'}
        studio._ACCT_Q.clear()
        res = dict(studio.accounts_check())
        self.assertIn('已跟上', res['gm-2'])
        self.assertEqual([(a['id'], a['email']) for a in self.p.list('gemini')], [('gm-2', 'g2@x.com')])
        self.assertEqual(self.p.default('gemini')['id'], 'gm-2')
        self.assertEqual(kc.writes, 0)
        studio._ACCT_Q.clear()
        lim = studio.collect_limits()
        self.assertTrue(lim['gemini']['available'])
        self.assertEqual(lim['gemini']['account'], 'gm-2')
        self.assertAlmostEqual(lim['gemini']['remaining'], 0.25)
        self.assertNotIn(RT2, json.dumps(studio.cli_accounts(), default=str))

if __name__ == '__main__':
    unittest.main()
