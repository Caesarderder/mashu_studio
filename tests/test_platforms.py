"""Codex / OpenCode 接入（mashu-studio #2）：平台表、hooks 格式转换与安全拦截、独立 CODEX_HOME、开工审批的执行配置。

  python3 -m unittest tests.test_platforms -v
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import Base, studio, wait_for  # noqa: E402
import platforms as plat  # noqa: E402

FAKE_CLI = """#!/usr/bin/env bash
if [ "$1" = "--version" ]; then echo "{name} {version}"; exit 0; fi
log="$MASHU_TEST_LOG/{name}-${{MASHU_ROLE}}-${{MASHU_FILM:-x}}-${{MASHU_SEQ:-x}}"
printf '%s\\n' "START[$MASHU_NAME] CODEX_HOME=$CODEX_HOME OCDIR=$OPENCODE_CONFIG_DIR TMUX=${{TMUX:-none}}" >> "$log"
printf 'OCCONF=%s\\n' "$OPENCODE_CONFIG_CONTENT" >> "$log"
for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$log"; done
while IFS= read -r line; do printf 'IN:%s\\n' "$line" >> "$log"; done
"""


def tree_hash(d):
    h = hashlib.sha256()
    for p in sorted(Path(d).rglob('*')):
        if p.is_file() and not p.is_symlink():
            h.update(str(p).encode() + b'\0' + p.read_bytes())
    return h.hexdigest()


class TestPure(unittest.TestCase):
    def test_nearest_effort(self):
        self.assertEqual(plat.nearest_effort('high', ['low', 'high', 'max']), 'high')
        self.assertEqual(plat.nearest_effort('xhigh', ['low', 'high', 'max']), 'high')
        self.assertEqual(plat.nearest_effort('low', ['high', 'max']), 'high')
        self.assertEqual(plat.nearest_effort('medium', []), 'medium')
        self.assertEqual(plat.nearest_effort('', ['low']), '')

    def test_patch_files(self):
        patch = ('*** Begin Patch\n*** Add File: /w/a.txt\n+x\n*** Update File: src/b.py\n@@\n-a\n+b\n'
                 '*** Move to: src/c.py\n*** Delete File: d.md\n*** End Patch')
        self.assertEqual(plat.patch_files(patch, '/w'), ['/w/a.txt', '/w/src/b.py', '/w/src/c.py', '/w/d.md'])

    def test_codex_rollout(self):
        entries = [
            {'type': 'turn_context', 'payload': {'model': 'gpt-6-luna'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'model_context_window': 200000}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '先看看'}]}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command', 'call_id': 'c1', 'arguments': '{"cmd": "ls -la"}'}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'total_token_usage': {'input_tokens': 1000, 'cached_input_tokens': 400, 'output_tokens': 50},
                'last_token_usage': {'input_tokens': 19000, 'output_tokens': 1000}, 'model_context_window': 200000}}},
        ]
        self.assertEqual(plat.codex_progress(entries), ('先看看', 'Bash · ls -la'))
        tot, ctx, model, win = plat.codex_usage(entries)
        self.assertEqual(tot, dict(tokens_in=600, tokens_out=50, cache_read=400, cache_write=0))
        self.assertAlmostEqual(ctx, 0.1)
        self.assertEqual((model, win), ('gpt-6-luna', 200000))
        entries.append({'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'c1'}})
        entries.append({'type': 'event_msg', 'payload': {'type': 'task_complete', 'last_agent_message': '做完了'}})
        self.assertEqual(plat.codex_progress(entries)[1], '')
        self.assertEqual(plat.codex_last_reply(entries), '做完了')
        # code mode：JS 里调的命令也认得
        js = {'type': 'custom_tool_call', 'name': 'exec', 'input': 'await tools.exec_command({cmd:"git status", workdir:"/w"})'}
        self.assertEqual(plat.codex_tool_label(js), 'Bash · git status')

    def test_codex_config_filter(self):
        src = ('model = "m"\n[hooks.state]\n[hooks.state."x"]\ntrusted_hash = "h"\n[[hooks.Stop]]\nmatcher = "*"\n'
               '[projects."/a"]\ntrust_level = "trusted"\n[plugins."p@x"]\nenabled = true\n[mcp_servers.s]\nurl = "u"\n')
        out = plat.codex_config_text(src)
        self.assertIn('model = "m"', out)
        self.assertIn('[mcp_servers.s]', out)
        for gone in ('hooks', 'projects', 'plugins', 'trusted_hash'):
            self.assertNotIn(gone, out)

    def test_opencode_models_parse(self):
        text = ('opencode/a-free\n{\n  "id": "a-free",\n  "variants": {"low": {}, "high": {}},\n  "limit": {"context": 200000},\n'
                '  "cost": {"input": 0, "output": 0}\n}\nx/b\n{\n  "id": "b",\n  "limit": {"context": 1000}, "cost": {"input": 1}\n}\n')
        got = plat.opencode_models_parse(text)
        self.assertEqual(got['opencode/a-free'], dict(variants=['low', 'high'], context=200000, free=True))
        self.assertEqual(got['x/b']['free'], False)

    def test_opencode_tool_mapping(self):
        self.assertEqual(plat.opencode_tool('bash', {'command': 'ls'}), [('Bash', {'command': 'ls'})])
        self.assertEqual(plat.opencode_tool('edit', {'filePath': 'a.py'}, '/w'), [('Edit', {'file_path': '/w/a.py'})])
        self.assertEqual(plat.opencode_tool('apply_patch', {'patchText': '*** Update File: a\n*** Add File: b\n'}, '/w'),
                         [('Write', {'file_path': '/w/a'}), ('Write', {'file_path': '/w/b'})])
        self.assertEqual(plat.opencode_tool('cysj-ai-support-v2_change_apply', {}, mcp_names=['mcp__cysj-ai-support-v2__change_apply']),
                         [('mcp__cysj-ai-support-v2__change_apply', {})])
        self.assertEqual(plat.opencode_tool('foo_bar', {})[0][0], 'mcp__foo_bar')

    def test_codex_rate_limits(self):
        # 1. 正常带 rate_limits
        entries = [
            {'type': 'event_msg', 'timestamp': '2026-11-24T07:30:28.351Z', 'payload': {
                'type': 'token_count',
                'rate_limits': {
                    'limit_id': 'codex',
                    'primary': {'used_percent': 58.0, 'window_minutes': 300, 'resets_at': 1795437230},
                    'secondary': {'used_percent': 39.0, 'window_minutes': 10080, 'resets_at': 1795572495},
                    'credits': {'has_credits': True, 'balance': '61516.12'},
                    'plan_type': 'plus',
                }
            }}
        ]
        res = plat.codex_rate_limits(entries)
        self.assertIsNotNone(res)
        self.assertEqual(res['five_hour']['used_percentage'], 58.0)
        self.assertEqual(res['five_hour']['resets_at'], 1795437230)
        self.assertEqual(res['seven_day']['used_percentage'], 39.0)
        self.assertEqual(res['plan_type'], 'plus')
        self.assertAlmostEqual(res['at'], 1795505428.351, places=1)

        # 2. 旧文件：只有 token_count 没有 rate_limits
        old_entries = [
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'input_tokens': 100}}}}
        ]
        self.assertIsNone(plat.codex_rate_limits(old_entries))

        # 3. 空 entries
        self.assertIsNone(plat.codex_rate_limits([]))

    def test_find_codex_rate_limits(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / 'sessions' / '2026' / '11' / '24'
            base.mkdir(parents=True)
            # 旧文件没有 rate_limits
            old_f = base / 'rollout-2026-11-24T01-00-00-old.jsonl'
            old_f.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count'}}) + '\n')
            # 新文件带 rate_limits
            new_f = base / 'rollout-2026-11-24T02-00-00-new.jsonl'
            new_f.write_text(json.dumps({'type': 'event_msg', 'timestamp': '2026-11-24T02:00:00Z', 'payload': {
                'type': 'token_count',
                'rate_limits': {
                    'primary': {'used_percent': 10.0, 'resets_at': 1795500000},
                    'secondary': {'used_percent': 20.0, 'resets_at': 1795600000},
                }
            }}) + '\n')
            res = plat.find_codex_rate_limits([Path(td) / 'sessions'])
            self.assertIsNotNone(res)
            self.assertEqual(res['five_hour']['used_percentage'], 10.0)

            # 空目录或不存在目录
            self.assertIsNone(plat.find_codex_rate_limits([Path(td) / 'nonexistent']))

    def test_opencode_costs(self):
        import tempfile, sqlite3
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / 'opencode.db'
            conn = sqlite3.connect(db_p)
            conn.execute('CREATE TABLE session (id text primary key, time_created integer, cost real)')
            # 设基准时间：周三 12:00
            base_dt = datetime(2026, 11, 25, 12, 0, 0)
            now_ts = base_dt.timestamp()

            # 今日 session (周三 10:00): $1.5
            t_today = int(datetime(2026, 11, 25, 10, 0, 0).timestamp() * 1000)
            conn.execute('INSERT INTO session VALUES ("s1", ?, 1.5)', (t_today,))

            # 本周较早 session (周一 10:00): $2.0
            t_this_week = int(datetime(2026, 11, 23, 10, 0, 0).timestamp() * 1000)
            conn.execute('INSERT INTO session VALUES ("s2", ?, 2.0)', (t_this_week,))

            # 上周 session (周日 10:00): $5.0
            t_last_week = int(datetime(2026, 11, 22, 10, 0, 0).timestamp() * 1000)
            conn.execute('INSERT INTO session VALUES ("s3", ?, 5.0)', (t_last_week,))
            conn.commit()
            conn.close()

            res = plat.opencode_costs(db_p, now=now_ts)
            self.assertIsNotNone(res)
            self.assertAlmostEqual(res['today'], 1.5)
            self.assertAlmostEqual(res['week'], 3.5)
            self.assertAlmostEqual(res['at'], t_today / 1000.0)

            # 不存在的 db
            self.assertIsNone(plat.opencode_costs(Path(td) / 'missing.db'))


class PlatBase(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('builda')
        self.film, _ = studio.greenlight(self.conn, self.repo, 'builda', 'Builda')
        # 假的 Codex / OpenCode（版本在已验证范围内）和假的用户 Codex 配置目录
        for name, ver in (('codex', 'codex-cli 0.166.0'), ('opencode', '1.18.31')):
            fp = self.tmp / f'fake-{name}'
            fp.write_text(FAKE_CLI.replace('{name}', name).replace('{version}', ver.split()[-1]).replace('{{', '{').replace('}}', '}'))
            fp.chmod(0o755)
            studio.CONFIG_OVERRIDES[name] = str(fp)
        self.user_codex = self.tmp / 'user-codex'
        self.user_codex.mkdir()
        (self.user_codex / 'config.toml').write_text('model = "gpt-6-luna"\n[hooks.state]\n[projects."/x"]\ntrust_level = "trusted"\n')
        (self.user_codex / 'auth.json').write_text('{"auth_mode": "chatgpt"}')
        (self.user_codex / 'hooks.json').write_text('{"hooks": {}}')
        self.saved_cuh = studio.CODEX_USER_HOME
        studio.CODEX_USER_HOME = self.user_codex
        self.addCleanup(setattr, studio, 'CODEX_USER_HOME', self.saved_cuh)
        studio._PLAT_CACHE.clear()

    def hookf(self, name, flag, cam, payload):
        os.environ['MASHU_SESSION'] = str(cam['id']) if cam else ''
        old_in = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                studio.main([name] + ([flag] if flag else []))
        finally:
            sys.stdin = old_in
            os.environ.pop('MASHU_SESSION', None)
        return buf.getvalue()

    def crew(self, slug='x'):
        f, key, cam = studio.pitch(self.conn, 'builda', slug, f'需求 {slug}')
        studio.tick(self.conn)
        return key, studio.camera(self.conn, cam['id'])

    def flog(self, name, key):
        p = self.log / f'{name}-crew-builda-{key}'
        return p.read_text() if p.exists() else ''


class TestCodexHooks(PlatBase):
    def deny(self, cam, tool, ti):
        out = self.hookf('hook-pretool', '--codex', cam, dict(tool_name=tool, tool_input=ti, cwd=str(self.repo), session_id='s1'))
        return json.loads(out)['hookSpecificOutput']['permissionDecisionReason'] if out.strip() else ''

    def test_codex_pretool_blocks(self):
        key, crew = self.crew()
        state = f'.mashu/features/{key}/state.json'
        self.assertIn('git stash', self.deny(crew, 'Bash', {'command': 'git stash'}))
        self.assertIn('state.json', self.deny(crew, 'apply_patch', {'command': f'*** Begin Patch\n*** Update File: {state}\n@@\n-a\n+b\n*** End Patch'}))
        self.assertIn('state.json', self.deny(crew, 'apply_patch', {'command': f'*** Begin Patch\n*** Add File: ok.txt\n+x\n*** Update File: {self.repo / state}\n*** End Patch'}))
        self.assertIn('state.json', self.deny(crew, 'Bash', {'command': f'echo x > {state}'}))
        self.assertIn('git stash', self.deny(crew, 'write_stdin', {'session_id': 1, 'chars': 'git stash\n'}))
        self.assertEqual(self.deny(crew, 'apply_patch', {'command': '*** Begin Patch\n*** Add File: src/ok.py\n+x\n*** End Patch'}), '')
        self.assertEqual(self.deny(crew, 'Bash', {'command': 'git status'}), '')
        # 正式操作 / 不可逆操作：拦下，审批后放行
        why = self.deny(crew, 'Bash', {'command': 'git push origin main'})
        self.assertIn('mashu ask --gate prod', why)
        self.assertIn('irreversible', self.deny(crew, 'Bash', {'command': 'git push -f origin feat'}))
        r = studio.create_report(self.conn, 'gate', crew, 'builda', key, '推 main', gate='prod')
        studio.answer_report(self.conn, r['id'], None, '审批')
        self.assertEqual(self.deny(crew, 'Bash', {'command': 'git push origin main'}), '')
        acts = [x['action'] for x in studio.rows(self.conn, 'SELECT action FROM audit')]
        self.assertIn('blocked-git', acts)
        self.assertIn('blocked', acts)

    def test_codex_session_prompt_stop(self):
        key, crew = self.crew()
        studio.meta_set(self.conn, f'camprov:{crew["id"]}', 'codex')
        sid = '01a20c6f-9513-7a02-820d-00cef462b4e0'
        roll = self.tmp / f'rollout-x-{sid}.jsonl'
        roll.write_text('\n'.join(json.dumps(x) for x in [
            {'type': 'turn_context', 'payload': {'model': 'gpt-6-luna'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'model_context_window': 100000}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'total_token_usage': {'input_tokens': 5000, 'cached_input_tokens': 1000, 'output_tokens': 300},
                'last_token_usage': {'input_tokens': 40000, 'output_tokens': 1000}, 'model_context_window': 100000}}}]) + '\n')
        out = self.hookf('hook-session', '--codex', crew, dict(session_id=sid, transcript_path=str(roll), source='startup'))
        self.assertIn('[上下文恢复 · 执行者]', out)
        crew = studio.camera(self.conn, crew['id'])
        self.assertEqual(crew['claude_sid'], sid)
        self.assertEqual(studio.provider_of_sid(self.conn, sid), 'codex')
        self.assertEqual(studio.transcript_file(self.conn, crew), roll)
        self.assertIn('状态文件', self.hookf('hook-prompt', '--codex', crew, dict(session_id=sid, prompt='继续 #1')))
        self.hookf('hook-stop', '--codex', crew, dict(session_id=sid, transcript_path=str(roll), last_assistant_message='A 做完了'))
        reply = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="reply" AND channel=?', f'builda/{key}')
        self.assertEqual(reply['text'], 'A 做完了')
        crew = studio.camera(self.conn, crew['id'])
        self.assertEqual((crew['tokens_in'], crew['tokens_out'], crew['cache_read']), (4000, 300, 1000))
        self.assertAlmostEqual(crew['context_pct'], 0.41)
        self.assertEqual(crew['state'], 'idle')
        # 打断以后迟到的 Stop 不重复发
        n = studio.one(self.conn, 'SELECT COUNT(*) n FROM messages')['n']
        self.hookf('hook-stop', '--codex', crew, dict(session_id=sid, last_assistant_message='又一遍'))
        self.assertEqual(studio.one(self.conn, 'SELECT COUNT(*) n FROM messages')['n'], n)

    def test_launch_codex_isolated_home(self):
        before = tree_hash(self.user_codex)
        f, key, cam = studio.pitch(self.conn, 'builda', 'cx', '需求 cx')
        studio.set_pref(self.conn, f'builda/{key}', provider='codex', xmodel='gpt-6-luna', effort='xhigh')
        studio.tick(self.conn)
        log = wait_for(lambda: self.flog('codex', key))
        self.assertIn(f'CODEX_HOME={studio.HOME / "codex-home"}', log)
        for a in ('ARG:--dangerously-bypass-approvals-and-sandbox', 'ARG:--dangerously-bypass-hook-trust', 'ARG:-m', 'ARG:gpt-6-luna',
                  'ARG:model_reasoning_effort="xhigh"', 'ARG:developer_instructions=', f'ARG:{os.path.realpath(self.repo)}', 'ARG:--'):
            self.assertIn(a, log)
        home = studio.HOME / 'codex-home'
        conf = (home / 'config.toml').read_text()
        self.assertIn(f'[projects.{json.dumps(os.path.realpath(self.repo))}]\ntrust_level = "trusted"', conf)
        self.assertIn('model = "gpt-6-luna"', conf)
        self.assertNotIn('[projects."/x"]', conf)
        self.assertIn('[[hooks.PreToolUse]]', conf)
        self.assertIn('hook-pretool --codex', conf)
        self.assertTrue((home / 'auth.json').is_symlink())
        self.assertFalse((home / 'hooks.json').exists())       # 用户自己的 hooks 不进麻薯会话
        self.assertEqual(tree_hash(self.user_codex), before)   # 用户的 ~/.codex 一点没改
        self.assertEqual(studio.meta_get(self.conn, f'camprov:{cam["id"]}'), 'codex')
        # 恢复：codex resume <ID>
        c = studio.camera(self.conn, cam['id'])
        studio.meta_set(self.conn, 'provider:01a2-resume-id', 'codex')
        self.assertEqual(studio.provider_of_sid(self.conn, '01a2-resume-id'), 'codex')


class TestOpenCodeHooks(PlatBase):
    def deny(self, cam, tool, args):
        out = self.hookf('hook-pretool', '--opencode', cam, dict(session_id='ses_1', tool=tool, args=args))
        return json.loads(out)['reason'] if out.strip() else ''

    def test_opencode_pretool_blocks(self):
        key, crew = self.crew()
        state = self.repo / '.mashu' / 'features' / key / 'state.json'
        self.assertIn('git stash', self.deny(crew, 'bash', {'command': 'git stash'}))
        self.assertIn('state.json', self.deny(crew, 'write', {'filePath': str(state), 'content': '{}'}))
        self.assertIn('state.json', self.deny(crew, 'edit', {'filePath': str(state.with_name('acceptance.json'))}))
        self.assertIn('state.json', self.deny(crew, 'apply_patch', {'patchText': f'*** Begin Patch\n*** Update File: {state}\n*** End Patch'}))
        self.assertIn('mashu ask --gate prod', self.deny(crew, 'bash', {'command': 'git push origin main'}))
        self.assertIn('irreversible', self.deny(crew, 'bash', {'command': 'git push --force origin feat'}))
        self.assertEqual(self.deny(crew, 'bash', {'command': 'git status'}), '')
        self.assertEqual(self.deny(crew, 'write', {'filePath': str(self.repo / 'src' / 'a.py')}), '')

    def test_opencode_prompt_statusline_stop(self):
        key, crew = self.crew()
        studio.meta_set(self.conn, f'camprov:{crew["id"]}', 'opencode')
        (studio.HOME / 'opencode-models.json').write_text(json.dumps(dict(models={'opencode/a-free': dict(variants=['low', 'high'], context=100000)})))
        out = self.hookf('hook-prompt', '--opencode', crew, dict(session_id='ses_abc', prompt='继续 #1', first=True))
        self.assertIn('[上下文恢复 · 执行者]', out)
        self.assertEqual(out.count('状态文件') <= 2, True)
        crew = studio.camera(self.conn, crew['id'])
        self.assertEqual(crew['claude_sid'], 'ses_abc')
        self.assertTrue(studio.has_transcript(self.repo, 'ses_abc'))
        self.assertEqual(self.hookf('hook-prompt', '--opencode', crew, dict(session_id='ses_abc', prompt='再做 B')), '')
        self.hookf('statusline', '--opencode', crew, dict(session_id='ses_abc', model='opencode/a-free', variant='high',
                                                        last=dict(input=20000, output=1000, cache=dict(read=4000, write=0))))
        crew = studio.camera(self.conn, crew['id'])
        self.assertAlmostEqual(crew['context_pct'], 0.25)
        self.assertEqual((crew['model'], crew['effort']), ('opencode/a-free', 'high'))
        # 实时动态：插件写的 live 文件
        (studio.HOME / 'live').mkdir(exist_ok=True)
        (studio.HOME / 'live' / f'{crew["id"]}.json').write_text(json.dumps(dict(sid='ses_abc', text='写到一半', tool='Bash · ls')))
        self.assertEqual(studio.turn_progress_of(self.conn, crew), ('写到一半', 'Bash · ls'))
        self.hookf('hook-stop', '--opencode', crew, dict(session_id='ses_abc', text='B 做完了', total=dict(input=900, output=100, reasoning=20,
                                                                                                         cache=dict(read=50, write=0)), cost=0.012))
        reply = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="reply" AND channel=?', f'builda/{key}')
        self.assertEqual(reply['text'], 'B 做完了')
        crew = studio.camera(self.conn, crew['id'])
        self.assertEqual((crew['tokens_in'], crew['tokens_out'], crew['cache_read']), (900, 120, 50))
        self.assertAlmostEqual(crew['cost'], 0.012)
        self.assertAlmostEqual(studio.one(self.conn, 'SELECT SUM(cost) c FROM usage')['c'], 0.012)

    def test_launch_opencode(self):
        before_home = {k: v for k, v in os.environ.items() if k.startswith('OPENCODE')}
        (studio.HOME).mkdir(parents=True, exist_ok=True)
        (studio.HOME / 'opencode-models.json').write_text(json.dumps(dict(models={'opencode/a-free': dict(variants=['low', 'high', 'max'], context=1)})))
        f, key, cam = studio.pitch(self.conn, 'builda', 'oc', '需求 oc')
        studio.set_pref(self.conn, f'builda/{key}', provider='opencode', omodel='opencode/a-free', effort='xhigh')
        studio.tick(self.conn)
        log = wait_for(lambda: self.flog('opencode', key))
        self.assertIn(f'OCDIR={studio.HOME / "opencode-config"}', log)
        self.assertIn('TMUX=none', log)
        conf = json.loads(log.split('OCCONF=', 1)[1].splitlines()[0])
        self.assertEqual(conf, {'agent': {'build': {'model': 'opencode/a-free', 'variant': 'high'}}})
        self.assertIn('ARG:--auto', log)
        self.assertIn('ARG:--prompt', log)
        link = studio.HOME / 'opencode-config' / 'plugins' / 'mashu.js'
        self.assertEqual(os.path.realpath(link), str(studio.CODE / 'integrations' / 'opencode' / 'mashu.js'))
        self.assertEqual({k: v for k, v in os.environ.items() if k.startswith('OPENCODE')}, before_home)


class TestPlatformGate(PlatBase):
    def test_unverified_version_cannot_be_selected(self):
        fp = self.tmp / 'old-codex'
        fp.write_text('#!/bin/sh\necho "codex-cli 0.150.0"\n')
        fp.chmod(0o755)
        studio.CONFIG_OVERRIDES['codex'] = str(fp)
        st = studio.platform_status('codex')
        self.assertFalse(st['ok'])
        self.assertIn('没做过安全拦截实测', st['reason'])
        with self.assertRaises(ValueError):
            studio.set_pref(self.conn, 'assistant', provider='codex')
        with self.assertRaises(ValueError):
            studio.set_defaults(self.conn, providers={'crew': 'codex'})
        with self.assertRaises(ValueError):
            studio.check_exec(dict(cli='codex'))
        view = {p['id']: p for p in studio.platforms_view()}
        self.assertFalse(view['codex']['ok'])
        self.assertTrue(view['opencode']['ok'])
        # 偏好里写着但现在不能用：新会话退回 Claude，并说明
        f, key, cam = studio.pitch(self.conn, 'builda', 'fb', '需求 fb')
        studio.meta_set(self.conn, f'pref:builda/{key}', json.dumps({'provider': 'codex'}))
        studio.tick(self.conn)
        self.assertEqual(studio.meta_get(self.conn, f'camprov:{cam["id"]}'), 'claude')
        self.assertTrue(studio.one(self.conn, 'SELECT id FROM messages WHERE text LIKE "%Codex 现在不能用%"'))
        # 版本加进 platform_versions 就能选
        studio.CONFIG_OVERRIDES['platform_versions'] = {'codex': ['0.150']}
        self.assertTrue(studio.platform_status('codex')['ok'])

    def test_platforms_selfcheck(self):
        for prov in ('codex', 'opencode'):
            res = studio.platform_selfcheck(prov)
            self.assertEqual(len(res), 5)
            self.assertTrue(all(ok for _, ok in res), (prov, res))


class TestKickoffExec(PlatBase):
    def test_kickoff_card_shows_and_edits_exec(self):
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        f, key, cam = studio.pitch(self.conn, 'builda', 'k', '需求 k', by=prod, plan='方案',
                                   exec_cfg=dict(cli='codex', model='gpt-6-luna', effort='high'))
        self.assertIsNone(cam)
        r = studio.one(self.conn, 'SELECT * FROM reports WHERE gate="kickoff"')
        self.assertIn('执行者：Codex · gpt-6-luna · 思考 high', r['text'])
        self.assertEqual(json.loads(r['meta'])['exec'], dict(cli='codex', model='gpt-6-luna', effort='high'))
        with self.assertRaises(ValueError):
            studio.answer_report(self.conn, r['id'], None, '批准开工', exec_cfg=dict(cli='nope'))
        self.assertEqual(studio.report(self.conn, r['id'])['status'], 'open')
        # 用户在卡片上改成 OpenCode 再批准：存进 state.exec，执行者按它启动
        studio.answer_report(self.conn, r['id'], None, '批准开工', exec_cfg=dict(cli='opencode', model='opencode/a-free', effort='low'))
        data = studio.seq_state(f, key)
        self.assertEqual(data['kickoff'], 'approved')
        self.assertEqual(data['exec'], dict(cli='opencode', model='opencode/a-free', effort='low'))
        crew = studio.active_crew(self.conn, 'builda', key)
        self.assertEqual(studio.provider_pref(self.conn, crew), 'opencode')
        self.assertEqual(studio.session_pref(self.conn, crew, 'opencode'), dict(provider='opencode', model='opencode/a-free', effort='low'))
        studio.tick(self.conn)
        self.assertEqual(studio.meta_get(self.conn, f'camprov:{crew["id"]}'), 'opencode')
        self.assertIn('ARG:--prompt', wait_for(lambda: self.flog('opencode', key)))
        # 之后在 ⚙ 里改：对话单独设的优先（只影响新会话）
        studio.set_pref(self.conn, f'builda/{key}', provider='claude')
        self.assertEqual(studio.provider_pref(self.conn, crew), 'claude')

    def test_web_start_uses_card_exec_and_cli_flags(self):
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        out = self.cli(prod, 'new-feature', 'builda', 'w', '--brief', '需求 w', '--plan', str(self._plan()),
                       '--cli', 'codex', '--model', 'gpt-6-luna', '--effort', 'max')
        self.assertIn('申请批准开工', out)
        f = studio.film(self.conn, 'builda')
        key = [s['key'] for s in studio.rows(self.conn, 'SELECT key FROM sequences') if s['key'].endswith('-w')][0]
        self.assertEqual(studio.kickoff_exec(self.conn, f, key), dict(cli='codex', model='gpt-6-luna', effort='max'))
        studio.grant_kickoff(self.conn, f, key, '用户')        # 网页上亲手启动 = 批准开工，按卡上的配置
        self.assertEqual(studio.seq_state(f, key)['exec'], dict(cli='codex', model='gpt-6-luna', effort='max'))
        # 重新申请开工的卡也写明执行配置
        f2, key2, _ = studio.pitch(self.conn, 'builda', 'w2', '需求 w2', by=prod)
        self.cli(prod, 'ask', '申请开工 w2', '--gate', 'kickoff', '--ref', f'builda/{studio.seq_num(key2)}')
        r = studio.one(self.conn, 'SELECT * FROM reports WHERE gate="kickoff" ORDER BY id DESC LIMIT 1')
        self.assertIn('执行者：Claude · 默认模型 · 思考 默认', r['text'])
        self.assertEqual(json.loads(r['meta'])['exec']['cli'], 'claude')

    def _plan(self):
        p = self.tmp / 'plan.md'
        p.write_text('# 方案\n')
        return p


class TestQuotaStudio(PlatBase):
    def test_collect_limits_and_cache(self):
        rf = self.tmp / 'ratelimits.json'
        rf.write_text(json.dumps({
            'five_hour': {'used_percentage': 50, 'resets_at': 1795500000},
            'seven_day': {'used_percentage': 80, 'resets_at': 1795600000},
            'at': 1000.0
        }))
        res = studio.collect_limits(home=self.tmp)
        self.assertIn('claude', res)
        self.assertEqual(res['claude']['five_hour']['used_percentage'], 50)
        self.assertEqual(res['five_hour']['used_percentage'], 50)
        self.assertIn('codex', res)
        self.assertIn('opencode', res)
        self.assertIn('gemini', res)
        self.assertEqual(res['gemini']['reason'], 'agy CLI 未提供额度查询接口')

        # 缓存与失效测试
        saved_cache = studio._LIMITS_CACHE.copy()
        try:
            studio._LIMITS_CACHE['data'] = {'claude': {'mock': True}}
            studio._LIMITS_CACHE['at'] = time.time()
            self.assertEqual(studio.read_limits(), {'claude': {'mock': True}})
            # 失效后重新计算
            studio._LIMITS_CACHE['at'] = 0
            new_data = studio.read_limits()
            self.assertIn('gemini', new_data)
        finally:
            studio._LIMITS_CACHE.update(saved_cache)


if __name__ == '__main__':
    unittest.main()
