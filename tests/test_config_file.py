"""主配置文件（#7）：迁移、并发加锁写入、Codex 凭据同步、脱敏导出、加密导出与差异预览导入、HTTP/CLI 接口测试。

  python3 -m unittest tests.test_config_file -v
"""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_accounts import FakeHTTP, codex_auth, RT1, CX_RT  # noqa: E402
from test_platforms import PlatBase  # noqa: E402
import accounts as acct  # noqa: E402
import config_file as cfgfile  # noqa: E402
import server as srv  # noqa: E402
import studio  # noqa: E402

BIN_MASHU = Path(__file__).resolve().parent.parent / 'bin' / 'mashu'


class FakeCliIO:
    """隔离测试用本机 CLI 凭据读写，不碰真 ~/.codex / ~/.local/share/opencode / 钥匙串。"""

    def __init__(self, initial=None):
        self.data = dict(initial or {})
        self.writes = []

    def read(self):
        return json.loads(json.dumps(self.data))

    def write(self, cli_secrets, backup_dir=None):
        self.writes.append((json.loads(json.dumps(cli_secrets)), backup_dir))
        self.data.update(json.loads(json.dumps(cli_secrets)))
        return list(cli_secrets.keys())


class TestMigrationAndSync(PlatBase):
    def test_migrate_legacy_files_idempotent_and_0600(self):
        # 构造旧版 state 布局：扁平 studio.json + token + accounts/index.json + gemini cred.json/clients.json + codex auth.json
        (studio.HOME / 'studio.json').write_text(json.dumps({
            'stages': 4,
            'scan_roots': ['~/work'],
            'models': {'crew': 'opus'},
            'providers': {'crew': 'codex'},
        }, ensure_ascii=False))
        (studio.HOME / 'token').write_text('web-token-secret-XYZ123\n')
        acc = studio.HOME / 'accounts'
        (acc / 'gemini' / 'gm-1').mkdir(parents=True)
        (acc / 'codex' / 'cx-2').mkdir(parents=True)
        (acc / 'index.json').write_text(json.dumps({
            'defaults': {'codex': 'cx-1', 'gemini': 'gm-1'},
            'accounts': [
                {'id': 'cx-1', 'platform': 'codex', 'source': 'local', 'email': 'me@x.com', 'status': 'ok'},
                {'id': 'cx-2', 'platform': 'codex', 'source': 'login', 'email': 'two@x.com', 'status': 'ok'},
                {'id': 'gm-1', 'platform': 'gemini', 'source': 'local', 'email': 'g@x.com', 'status': 'ok'},
            ],
        }))
        (acc / 'gemini' / 'gm-1' / 'cred.json').write_text(json.dumps({
            'refresh_token': RT1, 'access_token': 'ya29.OLD', 'expiry': 1795500000, 'client_id': 'cid',
        }))
        (acc / 'gemini' / 'clients.json').write_text(json.dumps({'sig': 's', 'pairs': [['cid', 'sec']], 'good': ['cid', 'sec']}))
        (acc / 'codex' / 'cx-2' / 'auth.json').write_text(json.dumps(codex_auth(email='two@x.com', rt=CX_RT)))
        (self.user_codex / 'auth.json').write_text(json.dumps(codex_auth(email='me@x.com', rt='rt_LOCAL_DO_NOT_COPY')))

        res = cfgfile.migrate_if_needed(studio.HOME)
        self.assertTrue(res['migrated'])
        conf = studio.HOME / 'studio.json'
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode), 0o600)
        data = json.loads(conf.read_text())
        self.assertEqual(data['settings']['stages'], 4)
        self.assertEqual(data['defaults']['models']['crew'], 'opus')
        self.assertEqual(data['defaults']['providers']['crew'], 'codex')
        self.assertEqual(data['web']['token'], 'web-token-secret-XYZ123')
        self.assertEqual(data['accounts']['defaults'], {'codex': 'cx-1', 'gemini': 'gm-1'})
        by_id = {a['id']: a for a in data['accounts']['items']}
        self.assertEqual(by_id['cx-1'].get('auth_ref'), 'local_cli')
        self.assertNotIn('credentials', by_id['cx-1'])       # 本机 Codex 号不复制 RT
        self.assertNotIn('rt_LOCAL_DO_NOT_COPY', conf.read_text())
        self.assertEqual(by_id['cx-2']['credentials']['tokens']['refresh_token'], CX_RT)
        self.assertEqual(by_id['gm-1']['credentials']['refresh_token'], RT1)
        self.assertEqual(data['accounts']['gemini_clients']['good'], ['cid', 'sec'])

        # 旧文件已备份并移走
        self.assertFalse((studio.HOME / 'token').exists())
        self.assertFalse((acc / 'index.json').exists())
        self.assertFalse((acc / 'gemini' / 'gm-1' / 'cred.json').exists())
        self.assertFalse((acc / 'gemini' / 'clients.json').exists())
        bdir = Path(res['backup_dir'])
        self.assertTrue((bdir / 'token').exists())
        self.assertTrue((bdir / 'studio.json').exists())
        self.assertTrue((bdir / 'accounts' / 'index.json').exists())
        self.assertTrue((bdir / 'accounts' / 'gemini' / 'gm-1' / 'cred.json').exists())

        # cfg() 与 load_token() 正常读取主文件，且不重建 state/token
        studio.CONFIG_OVERRIDES.pop('stages', None)
        studio._cfg_cache['mtime'] = None
        self.assertEqual(studio.cfg('stages'), 4)
        self.assertEqual(studio.cfg('providers'), {'crew': 'codex'})
        self.assertEqual(srv.load_token(), 'web-token-secret-XYZ123')
        self.assertFalse((studio.HOME / 'token').exists())

        # 幂等重跑：结果不变
        before = conf.read_text()
        res2 = cfgfile.migrate_if_needed(studio.HOME)
        self.assertFalse(res2['migrated'])
        self.assertEqual(conf.read_text(), before)

    def test_codex_auth_sync_roundtrip(self):
        http = FakeHTTP()
        saved_http = acct.HTTP
        acct.HTTP = http
        self.addCleanup(setattr, acct, 'HTTP', saved_http)

        p = studio.pool()
        cx2 = p.add('codex', source='login', status='ok', email='two@x.com')
        auth_old = codex_auth(email='two@x.com', rt=CX_RT, last='2026-10-01T00:00:00Z')
        p.set_credentials(cx2['id'], auth_old)
        home2 = studio.codex_acct_home(cx2)
        self.assertFalse((home2 / 'auth.json').exists())

        # 开会话前从主文件生成 auth.json
        p.sync_codex_to_home(cx2['id'], home2)
        self.assertTrue((home2 / 'auth.json').exists())
        self.assertEqual(stat.S_IMODE((home2 / 'auth.json').stat().st_mode), 0o600)
        self.assertEqual(json.loads((home2 / 'auth.json').read_text())['tokens']['refresh_token'], CX_RT)

        # Codex 运行时自己续期写入了更新的 last_refresh：体检/同步时回写主文件
        auth_newer = codex_auth(email='two@x.com', rt=CX_RT + '_CLI_ROTATED', last='2026-11-25T12:00:00Z')
        acct.write_json(home2 / 'auth.json', auth_newer)
        p.sync_codex_to_home(cx2['id'], home2)
        self.assertEqual(p.get_credentials(cx2['id'])['tokens']['refresh_token'], CX_RT + '_CLI_ROTATED')

        # 麻薯自己续期后也同步回主文件
        http.codex_rotate[CX_RT + '_CLI_ROTATED'] = CX_RT + '_MASHU_ROTATED'
        studio.accounts_check(force='refresh', only=cx2['id'])
        self.assertEqual(p.get_credentials(cx2['id'])['tokens']['refresh_token'], CX_RT + '_MASHU_ROTATED')

    def test_concurrent_config_updates_locked(self):
        conf = studio.HOME / 'studio.json'
        cfgfile.write_config(conf, {'settings': {'stages': 2}})
        errs = []

        def worker(i):
            try:
                cfgfile.update_config(conf, lambda d: d['settings'].__setitem__(f'k_{i}', i))
            except Exception as exc:     # pragma: no cover
                errs.append(exc)

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        loaded = cfgfile.read_config(conf)
        self.assertEqual(loaded['settings']['stages'], 2)
        for i in range(20):
            self.assertEqual(loaded['settings'][f'k_{i}'], i)
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode), 0o600)


class TestExportImport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.h1 = self.tmp / 'h1'
        self.h2 = self.tmp / 'h2'
        self.h1.mkdir()
        self.h2.mkdir()
        self.secrets = {
            'gm_rt': '1//0gFAKE-gemini-refresh-secret-9999999999',
            'gm_at': 'ya29.FAKE-gemini-access-secret-88888888',
            'cx_rt': 'rt_FAKE_codex_refresh_secret_777777777777',
            'oc_key': 'sk-FAKE-opencode-provider-secret-666666',
            'web_tok': 'FAKE_WEB_TOKEN_SECRET_5555555555',
            'local_cx_rt': 'rt_FAKE_local_codex_secret_44444444',
            'agy_kc': 'go-keyring-base64:FAKE_KEYCHAIN_3333',
            'claude_kc': 'FAKE_CLAUDE_KEYCHAIN_SECRET_2222',
        }
        cfgfile.write_config(self.h1 / 'studio.json', {
            'schema_version': 1,
            'settings': {'stages': 5, 'scan_roots': ['~/code']},
            'defaults': {'providers': {'crew': 'codex'}, 'models': {'assistant': 'opus'}},
            'accounts': {
                'defaults': {'codex': 'cx-2', 'gemini': 'gm-1'},
                'items': [
                    {'id': 'cx-1', 'platform': 'codex', 'source': 'local', 'email': 'me@x.com', 'label': '本机', 'status': 'ok', 'disabled': False, 'auth_ref': 'local_cli'},
                    {'id': 'cx-2', 'platform': 'codex', 'source': 'login', 'email': 'pool@x.com', 'label': '二号', 'status': 'ok', 'disabled': False,
                     'credentials': codex_auth(email='pool@x.com', rt=self.secrets['cx_rt'])},
                    {'id': 'gm-1', 'platform': 'gemini', 'source': 'local', 'email': 'g@x.com', 'label': 'g@x.com', 'status': 'ok', 'disabled': False,
                     'credentials': {'refresh_token': self.secrets['gm_rt'], 'access_token': self.secrets['gm_at'], 'expiry': 1799999999}},
                ],
                'gemini_clients': {'sig': 's', 'pairs': [['cid', 'GOCSPX-FAKEsecret123456789012345678']]},
            },
            'providers': {
                'openai': {'api_key': self.secrets['oc_key'], 'base_url': 'https://api.example.com'},
            },
            'web': {'token': self.secrets['web_tok']},
        })
        self.cli1 = FakeCliIO({
            'codex_local_auth': codex_auth(email='me@x.com', rt=self.secrets['local_cx_rt']),
            'opencode_auth': {'openai': {'type': 'api', 'key': self.secrets['oc_key']}},
            'gemini_keychain': self.secrets['agy_kc'],
            'claude_keychain': self.secrets['claude_kc'],
        })

    def test_public_export_strips_all_secrets(self):
        out = self.tmp / 'public.json'
        res = cfgfile.export_to_file(self.h1, out, with_secrets=False, cli_io=self.cli1)
        self.assertFalse(res['encrypted'])
        text = out.read_text()
        for name, val in self.secrets.items():
            self.assertNotIn(val, text, f'泄漏了 {name}')
        self.assertNotIn('GOCSPX-', text)
        bundle = json.loads(text)
        self.assertEqual(bundle['config']['web']['token'], cfgfile.OMITTED)
        self.assertEqual(bundle['config']['providers']['openai']['api_key'], cfgfile.OMITTED)
        self.assertEqual(bundle['config']['providers']['openai']['base_url'], 'https://api.example.com')
        self.assertEqual(bundle['config']['accounts']['items'][1]['email'], 'pool@x.com')
        self.assertEqual(bundle['config']['accounts']['items'][1]['credentials'], cfgfile.OMITTED)

    def test_encrypted_export_and_import_to_empty_instance(self):
        out = self.tmp / 'secret-bundle.json'
        res = cfgfile.export_to_file(self.h1, out, with_secrets=True, passphrase='my-strong-passphrase', cli_io=self.cli1)
        self.assertTrue(res['encrypted'])
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        raw_text = out.read_text()
        for name, val in self.secrets.items():
            self.assertNotIn(val, raw_text, f'加密包里出现明文 {name}')

        # 口令错误不能解密
        with self.assertRaises(ValueError):
            cfgfile.preview_import(self.h2, out, passphrase='wrong-passphrase')

        # 预览不写入
        preview = cfgfile.preview_import(self.h2, out, passphrase='my-strong-passphrase')
        self.assertTrue(preview['encrypted'])
        self.assertTrue(preview['with_secrets'])
        self.assertGreater(preview['counts']['add'], 0)
        self.assertFalse((self.h2 / 'studio.json').exists())

        # 确认导入到空实例 h2（隔离 cli_io）
        cli2 = FakeCliIO()
        applied = cfgfile.apply_import(self.h2, out, passphrase='my-strong-passphrase', cli_io=cli2)
        self.assertTrue(applied['applied'])
        self.assertEqual(stat.S_IMODE((self.h2 / 'studio.json').stat().st_mode), 0o600)

        restored = cfgfile.read_config(self.h2 / 'studio.json')
        self.assertEqual(restored['settings']['stages'], 5)
        self.assertEqual(restored['defaults']['providers']['crew'], 'codex')
        self.assertEqual(restored['web']['token'], self.secrets['web_tok'])
        self.assertEqual(restored['providers']['openai']['api_key'], self.secrets['oc_key'])
        by_id = {a['id']: a for a in restored['accounts']['items']}
        self.assertEqual(by_id['cx-2']['credentials']['tokens']['refresh_token'], self.secrets['cx_rt'])
        self.assertEqual(by_id['gm-1']['credentials']['refresh_token'], self.secrets['gm_rt'])
        # 导入的账号默认标为「已禁用」
        self.assertTrue(all(a['disabled'] for a in restored['accounts']['items']))
        # Codex 非本机池号已生成运行时 auth.json
        cx2_auth = self.h2 / 'accounts' / 'codex' / 'cx-2' / 'auth.json'
        self.assertTrue(cx2_auth.exists())
        self.assertEqual(stat.S_IMODE(cx2_auth.stat().st_mode), 0o600)
        # 本机 CLI 登录通过 cli_io 写回
        self.assertEqual(cli2.data['opencode_auth']['openai']['key'], self.secrets['oc_key'])
        self.assertEqual(cli2.data['gemini_keychain'], self.secrets['agy_kc'])

    def test_omitted_does_not_overwrite_and_import_creates_backup(self):
        cfgfile.write_config(self.h2 / 'studio.json', {
            'schema_version': 1,
            'settings': {'stages': 2},
            'defaults': {'providers': {'crew': 'claude'}},
            'accounts': {
                'defaults': {'gemini': 'gm-1'},
                'items': [{'id': 'gm-1', 'platform': 'gemini', 'source': 'local', 'email': 'g@x.com', 'status': 'ok',
                           'credentials': {'refresh_token': '1//KEEP_LOCAL_RT'}}],
            },
            'providers': {'openai': {'api_key': 'sk-KEEP_LOCAL_KEY', 'base_url': 'https://old.example.com'}},
            'web': {'token': 'KEEP_LOCAL_WEB_TOKEN'},
        })
        pub = self.tmp / 'pub.json'
        cfgfile.export_to_file(self.h1, pub, with_secrets=False, cli_io=self.cli1)

        preview = cfgfile.preview_import(self.h2, pub)
        self.assertGreater(preview['counts']['skip'], 0)
        self.assertEqual(cfgfile.read_config(self.h2 / 'studio.json')['settings']['stages'], 2)

        res = cfgfile.apply_import(self.h2, pub)
        self.assertTrue(Path(res['backup_path']).exists())
        self.assertEqual(stat.S_IMODE(Path(res['backup_path']).stat().st_mode), 0o600)

        after = cfgfile.read_config(self.h2 / 'studio.json')
        self.assertEqual(after['settings']['stages'], 5)
        self.assertEqual(after['defaults']['providers']['crew'], 'codex')
        self.assertEqual(after['providers']['openai']['base_url'], 'https://api.example.com')
        # 所有「已省略」项都保留了本机原有值
        self.assertEqual(after['web']['token'], 'KEEP_LOCAL_WEB_TOKEN')
        self.assertEqual(after['providers']['openai']['api_key'], 'sk-KEEP_LOCAL_KEY')
        self.assertEqual(after['accounts']['items'][0]['credentials']['refresh_token'], '1//KEEP_LOCAL_RT')


class TestServerAndCLI(PlatBase):
    def test_http_export_and_import_endpoints(self):
        studio.set_defaults(self.conn, models={'crew': 'opus'})
        tok = srv.load_token()
        srv.Handler.token = tok
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
        port = httpd.server_address[1]
        srv.Handler.port = port
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)

        def post(path, body, token=tok):
            req = urllib.request.Request(
                f'http://127.0.0.1:{port}{path}',
                data=json.dumps(body).encode(),
                headers={'Host': f'127.0.0.1:{port}', 'X-Mashu-Token': token, 'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())

        # 无口令被拒
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post('/api/config/export', {'with_secrets': False}, token='bad')
        self.assertEqual(cm.exception.code, 403)

        # 导出明文 + 导出加密
        code, exp_pub = post('/api/config/export', {'with_secrets': False})
        self.assertEqual(code, 200)
        self.assertFalse(exp_pub['bundle']['encrypted'])
        self.assertNotIn(tok, json.dumps(exp_pub['bundle']))

        code, exp_sec = post('/api/config/export', {'with_secrets': True, 'passphrase': 'pw123'})
        self.assertEqual(code, 200)
        self.assertTrue(exp_sec['bundle']['encrypted'])

        # 修改后预览 + 确认导入
        exp_pub['bundle']['config']['settings']['stages'] = 9
        code, prev = post('/api/config/import', {'bundle': exp_pub['bundle'], 'dry_run': True})
        self.assertEqual(code, 200)
        self.assertFalse(prev['applied'])
        self.assertEqual(studio.cfg('stages'), 2)        # CONFIG_OVERRIDES 之外的磁盘值未变
        self.assertEqual(cfgfile.read_config(studio.HOME / 'studio.json')['settings'].get('stages'), None)

        code, applied = post('/api/config/import', {'bundle': exp_pub['bundle'], 'confirm': True})
        self.assertEqual(code, 200)
        self.assertTrue(applied['applied'])
        self.assertEqual(cfgfile.read_config(studio.HOME / 'studio.json')['settings']['stages'], 9)

    def test_cli_export_and_import_isolated(self):
        h1, h2 = self.tmp / 'cli_h1', self.tmp / 'cli_h2'
        h1.mkdir()
        h2.mkdir()
        (h1 / 'studio.json').write_text(json.dumps({'stages': 6, 'models': {'crew': 'sonnet'}}))
        (h1 / 'token').write_text('CLI_TOK_SECRET_ABC')
        env1 = dict(os.environ, MASHU_HOME=str(h1), MASHU_NO_KEYCHAIN='1',
                    MASHU_CODEX_USER_HOME=str(self.tmp / 'no_cx'), MASHU_OPENCODE_AUTH_FILE=str(self.tmp / 'no_oc'))
        env2 = dict(os.environ, MASHU_HOME=str(h2), MASHU_NO_KEYCHAIN='1',
                    MASHU_CODEX_USER_HOME=str(self.tmp / 'no_cx2'), MASHU_OPENCODE_AUTH_FILE=str(self.tmp / 'no_oc2'))

        bundle = self.tmp / 'cli-sec.json'
        r = subprocess.run([str(BIN_MASHU), 'config', 'export', '--out', str(bundle), '--with-secrets'],
                           input='pass123\n', text=True, env=env1, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('refresh_token 会轮换', r.stdout)
        self.assertNotIn('CLI_TOK_SECRET_ABC', bundle.read_text())

        r_dry = subprocess.run([str(BIN_MASHU), 'config', 'import', str(bundle), '--dry-run'],
                               input='pass123\n', text=True, env=env2, capture_output=True)
        self.assertEqual(r_dry.returncode, 0, r_dry.stderr)
        self.assertFalse((h2 / 'studio.json').exists())

        r_imp = subprocess.run([str(BIN_MASHU), 'config', 'import', str(bundle), '--yes'],
                               input='pass123\n', text=True, env=env2, capture_output=True)
        self.assertEqual(r_imp.returncode, 0, r_imp.stderr)
        restored = cfgfile.read_config(h2 / 'studio.json')
        self.assertEqual(restored['settings']['stages'], 6)
        self.assertEqual(restored['web']['token'], 'CLI_TOK_SECRET_ABC')

    def test_migrate_dedups_accounts_by_platform_and_email(self):
        """二次迁移合并时按（平台 + 邮箱）去重：旧进程新写入的 gm-1 与主文件已有 gm-2 同邮箱时只更新 gm-2，不新建重复号。"""
        h = self.tmp / 'dedup_mig'
        h.mkdir()
        # 主文件已有 gm-2（默认号）
        cfg = cfgfile.normalize_config({})
        cfg['accounts']['defaults']['gemini'] = 'gm-2'
        cfg['accounts']['items'].append({
            'id': 'gm-2', 'platform': 'gemini', 'email': 'alice@example.com', 'label': 'alice@example.com',
            'source': 'local', 'status': 'ok', 'last_check': 200,
            'credentials': {'refresh_token': 'RT_EXISTING'},
        })
        cfgfile.write_config(h / 'studio.json', cfg)
        # 模拟旧服务进程又重建了 accounts/index.json（带同邮箱不同 ID 的 gm-1）和 cred.json
        (h / 'accounts' / 'gemini' / 'gm-1').mkdir(parents=True)
        (h / 'accounts' / 'index.json').write_text(json.dumps({
            'defaults': {'gemini': 'gm-1'},
            'accounts': [{
                'id': 'gm-1', 'platform': 'gemini', 'email': 'ALICE@example.com', 'label': 'ALICE@example.com',
                'source': 'local', 'status': 'ok', 'last_check': 300, 'quota': {'remaining': 0.9},
            }],
        }))
        (h / 'accounts' / 'gemini' / 'gm-1' / 'cred.json').write_text(json.dumps({'refresh_token': 'RT_DUP'}))

        res = cfgfile.migrate_if_needed(h)
        self.assertTrue(res['migrated'])
        after = cfgfile.read_config(h / 'studio.json')
        gm_items = [a for a in after['accounts']['items'] if a['platform'] == 'gemini']
        self.assertEqual(len(gm_items), 1)
        self.assertEqual(gm_items[0]['id'], 'gm-2')
        self.assertEqual(after['accounts']['defaults']['gemini'], 'gm-2')
        self.assertEqual(gm_items[0]['quota']['remaining'], 0.9)


if __name__ == '__main__':
    unittest.main()