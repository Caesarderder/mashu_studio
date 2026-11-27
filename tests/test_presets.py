"""模型池（#14 14-model-pool）：迁移、预设管理、统一解析顺序、备选预设自动切换、HTTP & CLI 接口测试。

  python3 -m unittest tests.test_presets -v
"""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_accounts import codex_auth, CX_RT  # noqa: E402
from test_platforms import PlatBase  # noqa: E402
import config_file as cfgfile  # noqa: E402
import server as srv  # noqa: E402
import studio  # noqa: E402

BIN_MASHU = Path(__file__).resolve().parent.parent / 'bin' / 'mashu'


def _write_codex_rollout(sessions_dir, sid, used_5h=None, used_7d=None):
    """在指定 Codex sessions 目录写一个带 rate_limits 的 rollout 文件。"""
    p = Path(sessions_dir) / '2026' / '11' / '27'
    p.mkdir(parents=True, exist_ok=True)
    fp = p / f'rollout-2026-11-27T08-00-00-{sid}.jsonl'
    rl = {}
    if used_5h is not None:
        rl['primary'] = {'used_percent': used_5h, 'window_minutes': 300, 'resets_at': int(time.time()) + 3600}
    if used_7d is not None:
        rl['secondary'] = {'used_percent': used_7d, 'window_minutes': 10080, 'resets_at': int(time.time()) + 86400}
    rec = {'timestamp': '2026-11-27T08:00:00Z', 'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': rl}}
    fp.write_text(json.dumps(rec) + '\n', encoding='utf-8')
    return fp


class TestPresetMigration(PlatBase):
    def test_migrate_v1_defaults_to_presets_and_idempotent(self):
        conf = studio.HOME / 'studio.json'
        v1 = {
            'schema_version': 1,
            'settings': {'stages': 3},
            'defaults': {
                'providers': {'crew': 'codex'},
                'models': {'assistant': 'opus'},
                'gemini_models': {},
                'codex_models': {'crew': 'gpt-6-luna'},
                'opencode_models': {},
                'efforts': {'assistant': 'high', 'crew': 'medium'},
                'code_modes': {'assistant': 'on'},
            },
            'accounts': {'defaults': {}, 'items': [], 'gemini_clients': {}},
            'providers': {},
            'web': {'token': 'tok-mig-test'},
        }
        conf.write_text(json.dumps(v1, ensure_ascii=False, indent=2), encoding='utf-8')

        res = cfgfile.migrate_if_needed(studio.HOME)
        self.assertTrue(res['migrated'])
        self.assertIn('studio.json', res['files'])
        bdir = Path(res['backup_dir'])
        self.assertTrue((bdir / 'studio.json').exists())
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode), 0o600)

        data = json.loads(conf.read_text(encoding='utf-8'))
        self.assertEqual(data['schema_version'], 2)
        # 旧 defaults 保留只读
        self.assertEqual(data['defaults']['models']['assistant'], 'opus')
        self.assertEqual(data['defaults']['providers']['crew'], 'codex')
        # 生成了预设与角色默认
        by_id = {p['id']: p for p in data['presets']}
        rp = data['role_presets']
        self.assertIn('assistant', rp)
        self.assertIn('producer', rp)
        self.assertIn('crew', rp)

        a_pr = by_id[rp['assistant']]
        self.assertEqual(a_pr['cli'], 'claude')
        self.assertEqual(a_pr['model'], 'opus')
        self.assertEqual(a_pr['effort'], 'high')
        self.assertTrue(a_pr['code_mode'])
        self.assertIn('opus', a_pr['name'])
        self.assertIn('high', a_pr['name'])

        p_pr = by_id[rp['producer']]
        self.assertEqual(p_pr['cli'], 'claude')
        self.assertEqual(p_pr['model'], '')
        self.assertEqual(p_pr['effort'], '')
        self.assertEqual(p_pr['name'], 'Claude 默认')

        c_pr = by_id[rp['crew']]
        self.assertEqual(c_pr['cli'], 'codex')
        self.assertEqual(c_pr['model'], 'gpt-6-luna')
        self.assertEqual(c_pr['effort'], 'medium')
        self.assertFalse(c_pr['code_mode'])

        # 幂等重跑
        before = conf.read_text(encoding='utf-8')
        res2 = cfgfile.migrate_if_needed(studio.HOME)
        self.assertFalse(res2['migrated'])
        self.assertEqual(conf.read_text(encoding='utf-8'), before)


class TestPresetResolutionAndCrud(PlatBase):
    def _seed_presets(self):
        p1 = studio.preset_action(self.conn, 'save', dict(
            name='Claude Opus high', cli='claude', model='opus', effort='high', code_mode=True,
        ))['preset']
        p2 = studio.preset_action(self.conn, 'save', dict(
            name='Codex Luna med', cli='codex', model='gpt-6-luna', effort='medium',
        ))['preset']
        p3 = studio.preset_action(self.conn, 'save', dict(
            name='OpenCode Free low', cli='opencode', model='opencode/a-free', effort='low',
        ))['preset']
        studio.preset_action(self.conn, 'set-roles', dict(
            role_presets={'assistant': p1['id'], 'producer': p1['id'], 'crew': p1['id']},
        ))
        return p1, p2, p3

    def test_resolution_priority_order(self):
        p1, p2, p3 = self._seed_presets()
        f, key, cam = studio.pitch(self.conn, 'builda', 'prio', '测试解析顺序')
        ch = f'builda/{key}'

        # 1) 角色默认预设
        r1 = studio.resolve_exec(self.conn, cam)
        self.assertEqual((r1['cli'], r1['model'], r1['effort'], r1['code'], r1['preset']),
                         ('claude', 'opus', 'high', True, p1['id']))
        self.assertFalse(r1['customized'])
        self.assertEqual(studio.provider_pref(self.conn, cam), 'claude')
        self.assertEqual(studio.session_pref(self.conn, cam, 'claude'), dict(model='opus', effort='high'))
        self.assertTrue(studio.code_pref(self.conn, cam))

        # 2) 功能开工 exec（预设）覆盖角色默认
        st = studio.seq_state(f, key)
        st['exec'] = studio.check_exec(dict(preset=p2['id']))
        studio.save_state(f, key, st)
        r2 = studio.resolve_exec(self.conn, cam)
        self.assertEqual((r2['cli'], r2['model'], r2['effort'], r2['code'], r2['preset']),
                         ('codex', 'gpt-6-luna', 'medium', False, p2['id']))

        # 3) 对话选的预设覆盖功能 exec
        studio.set_pref(self.conn, ch, preset=p3['id'], defer=True)
        r3 = studio.resolve_exec(self.conn, cam)
        self.assertEqual((r3['cli'], r3['model'], r3['effort'], r3['preset']),
                         ('opencode', 'opencode/a-free', 'low', p3['id']))
        self.assertFalse(r3['customized'])

        # 4) 对话单改字段覆盖对话预设
        studio.set_pref(self.conn, ch, effort='max', defer=True)
        r4 = studio.resolve_exec(self.conn, cam)
        self.assertEqual((r4['cli'], r4['model'], r4['effort']), ('opencode', 'opencode/a-free', 'max'))
        self.assertTrue(r4['customized'])

        # 5) 选回空 preset 清除单改字段，回到功能 exec
        studio.set_pref(self.conn, ch, preset='', defer=True)
        r5 = studio.resolve_exec(self.conn, cam)
        self.assertEqual((r5['cli'], r5['model'], r5['effort'], r5['preset']),
                         ('codex', 'gpt-6-luna', 'medium', p2['id']))
        self.assertFalse(r5['customized'])

    def test_crud_move_delete_clears_references_and_blocks_cycle(self):
        p1, p2, p3 = self._seed_presets()
        # 给 p1 设 p2 备选
        studio.preset_action(self.conn, 'save', dict(
            id=p1['id'], name=p1['name'], cli='claude', model='opus', effort='high', fallback=p2['id'],
        ))
        # 环检测：p2 -> p1 应拒绝
        with self.assertRaises(ValueError):
            studio.preset_action(self.conn, 'save', dict(
                id=p2['id'], name=p2['name'], cli='codex', model='gpt-6-luna', effort='medium', fallback=p1['id'],
            ))
        # 上下排序
        studio.preset_action(self.conn, 'move', dict(id=p2['id'], direction='up'))
        self.assertEqual([x['id'] for x in studio.presets_list()[:2]], [p2['id'], p1['id']])

        # 删除被角色默认和 p1 备选引用的 p2（先设为 crew 默认）
        studio.preset_action(self.conn, 'set-roles', dict(role_presets={'crew': p2['id']}))
        res = studio.preset_action(self.conn, 'delete', dict(id=p2['id']))
        self.assertTrue(any('执行者默认' in x for x in res['cleared_refs']))
        self.assertTrue(any(p1['name'] in x for x in res['cleared_refs']))
        self.assertEqual(studio.preset_by_id(p1['id'])['fallback'], '')
        self.assertEqual(studio.role_presets_map().get('crew', ''), '')


class TestPresetFallback(PlatBase):
    def _add_codex_acct(self, label, status='ok', disabled=False, used_5h=None, used_7d=None):
        p = studio.pool()
        a = p.add('codex', source='login', label=label, email=f'{label}@x.com', status=status, disabled=disabled)
        p.set_credentials(a['id'], codex_auth(email=f'{label}@x.com', rt=CX_RT + label))
        if used_5h is not None or used_7d is not None:
            _write_codex_rollout(studio.codex_acct_home(a) / 'sessions', f'sid-{a["id"]}', used_5h=used_5h, used_7d=used_7d)
        studio._ACCT_Q.clear()
        return p.get(a['id'])

    def test_fallback_on_cli_unavailable(self):
        # 备选：Claude；主预设：Codex（随后把 codex 换成未验证版本使其不可用）
        fb = studio.preset_action(self.conn, 'save', dict(name='Claude 备选', cli='claude', model='sonnet', effort='medium'))['preset']
        main_p = studio.preset_action(self.conn, 'save', dict(name='Codex 主力', cli='codex', model='gpt-6-luna', effort='high', fallback=fb['id']))['preset']
        studio.preset_action(self.conn, 'set-roles', dict(role_presets={'crew': main_p['id']}))

        bad = self.tmp / 'old-codex'
        bad.write_text('#!/bin/sh\necho "codex-cli 0.150.0"\n')
        bad.chmod(0o755)
        studio.CONFIG_OVERRIDES['codex'] = str(bad)

        f, key, cam = studio.pitch(self.conn, 'builda', 'fb-cli', '测试 CLI 不可用切换')
        studio.tick(self.conn)
        self.assertEqual(studio.meta_get(self.conn, f'camprov:{cam["id"]}'), 'claude')
        # 对话内发了一条系统提示，审计有记录，对话偏好未被改写
        msg = studio.one(self.conn, 'SELECT text FROM messages WHERE channel=? AND text LIKE "%已改用%"', f'builda/{key}')
        self.assertIsNotNone(msg)
        self.assertIn('Codex 主力', msg['text'])
        self.assertIn('Claude 备选', msg['text'])
        aud = studio.one(self.conn, 'SELECT * FROM audit WHERE action="preset-fallback" AND seq_key=?', key)
        self.assertIsNotNone(aud)
        self.assertEqual(studio.get_pref(self.conn, f'builda/{key}'), {})

    def test_fallback_on_account_error_and_quota_and_unknown_stays(self):
        a_bad = self._add_codex_acct('revoked', status='revoked')
        a_high = self._add_codex_acct('high', status='ok', used_5h=96)
        a_unk = self._add_codex_acct('unk', status='ok')
        fb = studio.preset_action(self.conn, 'save', dict(name='OpenCode 备选', cli='opencode', model='opencode/a-free', effort='low'))['preset']
        p_bad = studio.preset_action(self.conn, 'save', dict(name='坏号预设', cli='codex', model='gpt-6-luna', account=a_bad['id'], fallback=fb['id']))['preset']
        p_high = studio.preset_action(self.conn, 'save', dict(name='满额预设', cli='codex', model='gpt-6-luna', account=a_high['id'], fallback=fb['id']))['preset']
        p_unk = studio.preset_action(self.conn, 'save', dict(name='未知额度预设', cli='codex', model='gpt-6-luna', account=a_unk['id'], fallback=fb['id']))['preset']

        f, key, cam = studio.pitch(self.conn, 'builda', 'fb-acct', '测试号异常和额度')
        ch = f'builda/{key}'

        # 1) 指定号异常 → 切备选
        studio.set_pref(self.conn, ch, preset=p_bad['id'], defer=True)
        chosen, info = studio.resolve_launch_exec(self.conn, cam)
        self.assertTrue(info['switched'])
        self.assertEqual(chosen['preset'], fb['id'])

        # 2) 指定号额度 ≥95% → 切备选
        studio.set_pref(self.conn, ch, preset=p_high['id'], defer=True)
        chosen, info = studio.resolve_launch_exec(self.conn, cam)
        self.assertTrue(info['switched'])
        self.assertEqual(chosen['preset'], fb['id'])

        # 3) 额度未知 → 视为可用，不换
        studio.set_pref(self.conn, ch, preset=p_unk['id'], defer=True)
        chosen, info = studio.resolve_launch_exec(self.conn, cam)
        self.assertIsNone(info)
        self.assertEqual(chosen['preset'], p_unk['id'])

    def test_claude_quota_95_triggers_fallback_and_auto_account_pool_stays(self):
        # Claude 本机额度 ≥95% → 切备选
        (studio.HOME / 'ratelimits.json').write_text(json.dumps({'five_hour': {'used_percentage': 95}}))
        fb = studio.preset_action(self.conn, 'save', dict(name='OpenCode 兜底', cli='opencode', model='opencode/a-free'))['preset']
        p_cl = studio.preset_action(self.conn, 'save', dict(name='Claude 主', cli='claude', model='opus', fallback=fb['id']))['preset']
        f, key, cam = studio.pitch(self.conn, 'builda', 'fb-cl', '测试 Claude 额度切换')
        studio.set_pref(self.conn, f'builda/{key}', preset=p_cl['id'], defer=True)
        chosen, info = studio.resolve_launch_exec(self.conn, cam)
        self.assertTrue(info['switched'])
        self.assertEqual(chosen['preset'], fb['id'])

        # Codex 自动选号：池里有一个满额、一个可用 → 不换备选
        self._add_codex_acct('full', status='ok', used_5h=99)
        self._add_codex_acct('good', status='ok', used_5h=20)
        p_auto = studio.preset_action(self.conn, 'save', dict(name='Codex 自动', cli='codex', model='gpt-6-luna', fallback=fb['id']))['preset']
        studio.set_pref(self.conn, f'builda/{key}', preset=p_auto['id'], defer=True)
        chosen2, info2 = studio.resolve_launch_exec(self.conn, cam)
        self.assertIsNone(info2)
        self.assertEqual(chosen2['preset'], p_auto['id'])

    def test_fallback_max_3_hops_cycle_and_resume_no_switch(self):
        # 构造 5 节链（前 4 节满额，第 5 节可用）：最多 3 跳应停下，不切到第 5 节
        a_full = self._add_codex_acct('f1', status='ok', used_5h=98)
        p5 = studio.preset_action(self.conn, 'save', dict(name='P5 可用', cli='opencode', model='opencode/a-free'))['preset']
        p4 = studio.preset_action(self.conn, 'save', dict(name='P4 满', cli='codex', account=a_full['id'], fallback=p5['id']))['preset']
        p3 = studio.preset_action(self.conn, 'save', dict(name='P3 满', cli='codex', account=a_full['id'], fallback=p4['id']))['preset']
        p2 = studio.preset_action(self.conn, 'save', dict(name='P2 满', cli='codex', account=a_full['id'], fallback=p3['id']))['preset']
        p1 = studio.preset_action(self.conn, 'save', dict(name='P1 满', cli='codex', account=a_full['id'], fallback=p2['id']))['preset']

        f, key, cam = studio.pitch(self.conn, 'builda', 'fb-hops', '测试跳数和环')
        ch = f'builda/{key}'
        studio.set_pref(self.conn, ch, preset=p1['id'], defer=True)
        chosen, info = studio.resolve_launch_exec(self.conn, cam)
        self.assertFalse(info['switched'])
        self.assertTrue(info['exhausted'])
        self.assertEqual(chosen['preset'], p1['id'])

        # p2 → p3 → p4 → p5 刚好 3 跳：能切到 p5
        studio.set_pref(self.conn, ch, preset=p2['id'], defer=True)
        chosen3, info3 = studio.resolve_launch_exec(self.conn, cam)
        self.assertTrue(info3['switched'])
        self.assertEqual(info3['hops'], 3)
        self.assertEqual(chosen3['preset'], p5['id'])

        # 手工在 studio.json 构造环（p1 -> p2 -> p1）：应安全停下不死循环
        def _make_cycle(d):
            for x in d['presets']:
                if x['id'] == p2['id']:
                    x['fallback'] = p1['id']
        cfgfile.update_config(studio.HOME / 'studio.json', _make_cycle)
        studio._cfg_cache['mtime'] = None
        studio.set_pref(self.conn, ch, preset=p1['id'], defer=True)
        chosen_c, info_c = studio.resolve_launch_exec(self.conn, cam)
        self.assertFalse(info_c['switched'])
        self.assertTrue(info_c['exhausted'])
        self.assertEqual(chosen_c['preset'], p1['id'])

        # 恢复会话（resume_sid 非空）不走备选切换
        (studio.HOME / 'ratelimits.json').write_text(json.dumps({'five_hour': {'used_percentage': 99}}))
        p_cl = studio.preset_action(self.conn, 'save', dict(name='Claude 恢复测', cli='claude', fallback=p5['id']))['preset']
        studio.set_pref(self.conn, ch, preset=p_cl['id'], defer=True)
        sid = '11111111-1111-1111-1111-111111111111'
        res_cam = studio.create_camera(self.conn, 'crew', 'builda', key, '继续', resume_sid=sid, quiet=True)
        studio.launch(self.conn, res_cam['id'])
        self.assertEqual(studio.meta_get(self.conn, f'camprov:{res_cam["id"]}'), 'claude')
        self.assertIsNone(studio.one(self.conn, 'SELECT id FROM audit WHERE action="preset-fallback" AND seq_key=?', key))


class TestPresetHttpAndCli(PlatBase):
    def test_http_pitch_with_preset_and_cli_commands(self):
        p1 = studio.preset_action(self.conn, 'save', dict(name='Codex 冲刺', cli='codex', model='gpt-6-luna', effort='high'))['preset']
        p2 = studio.preset_action(self.conn, 'save', dict(name='Claude 稳健', cli='claude', model='opus', effort='medium', fallback=p1['id']))['preset']
        studio.preset_action(self.conn, 'set-roles', dict(role_presets={'assistant': p2['id'], 'crew': p1['id']}))

        # 1) HTTP /api/pitch 带 exec.preset
        tok = srv.load_token()
        srv.Handler.token = tok
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
        port = httpd.server_address[1]
        srv.Handler.port = port
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)

        def post(path, body):
            req = urllib.request.Request(
                f'http://127.0.0.1:{port}{path}',
                data=json.dumps(body).encode(),
                headers={'Host': f'127.0.0.1:{port}', 'X-Mashu-Token': tok, 'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())

        code, res = post('/api/pitch', dict(film='builda', slug='hp', brief='HTTP 预设开功能', exec={'preset': p1['id']}))
        self.assertEqual(code, 200)
        f = studio.film(self.conn, 'builda')
        ex = studio.seq_state(f, res['key'])['exec']
        self.assertEqual(ex['preset'], p1['id'])
        self.assertEqual(ex['cli'], 'codex')
        self.assertEqual(ex['model'], 'gpt-6-luna')
        self.assertEqual(ex['effort'], 'high')

        # 2) CLI: mashu preset list --json
        env = dict(os.environ, MASHU_HOME=str(studio.HOME), MASHU_TMUX=studio.session_name(),
                   MASHU_TMUX_SOCKET=studio.cfg('tmux_socket') or '',
                   MASHU_CLAUDE=studio.cfg('claude'), MASHU_CODEX=studio.cfg('codex'),
                   MASHU_OPENCODE=studio.cfg('opencode'), MASHU_AGY=studio.cfg('agy'))
        r_list = subprocess.run([str(BIN_MASHU), 'preset', 'list', '--json'], capture_output=True, text=True, env=env)
        self.assertEqual(r_list.returncode, 0, r_list.stderr)
        j = json.loads(r_list.stdout)
        by_id = {x['id']: x for x in j['presets']}
        self.assertEqual(by_id[p2['id']]['fallback'], p1['id'])
        self.assertEqual(by_id[p2['id']]['fallback_name'], 'Codex 冲刺')
        self.assertEqual(by_id[p2['id']]['roles'], ['assistant'])
        self.assertEqual(by_id[p1['id']]['roles'], ['crew'])

        # 3) CLI: new-feature --preset 与 --cli 同时给时报错
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        plan_fp = self.tmp / 'plan.md'
        plan_fp.write_text('# 方案\n测试方案\n', encoding='utf-8')
        env_p = dict(env, MASHU_SESSION=str(prod['id']))
        r_err = subprocess.run([str(BIN_MASHU), 'new-feature', 'builda', 'bad-mix', '--brief', 'x', '--plan', str(plan_fp),
                                '--preset', p1['name'], '--cli', 'claude'], capture_output=True, text=True, env=env_p)
        self.assertNotEqual(r_err.returncode, 0)
        self.assertIn('不能和', r_err.stderr + r_err.stdout)

        # 4) CLI: new-feature --preset 按名字成功，开工审批卡默认选中该预设
        r_ok = subprocess.run([str(BIN_MASHU), 'new-feature', 'builda', 'use-pr', '--brief', '用预设开工', '--plan', str(plan_fp),
                               '--preset', p1['name']], capture_output=True, text=True, env=env_p)
        self.assertEqual(r_ok.returncode, 0, r_ok.stderr)
        rep = studio.one(self.conn, 'SELECT * FROM reports WHERE gate="kickoff" ORDER BY id DESC LIMIT 1')
        self.assertIn('Codex 冲刺', rep['text'])
        self.assertEqual(json.loads(rep['meta'])['exec']['preset'], p1['id'])


if __name__ == '__main__':
    unittest.main()
