"""Gemini（agy）/ Codex / OpenCode 实时会话同步解析与集成测试（#8）。

  python3 -m unittest tests.test_live_parsers -v
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import Base, studio  # noqa: E402
import live_parsers as lp  # noqa: E402

FIX = Path(__file__).resolve().parent / 'fixtures'
CODE_ROOT = Path(__file__).resolve().parent.parent


class TestAgyParser(unittest.TestCase):
    def test_real_fixture_full_pipeline(self):
        """用真实脱敏 agy 会话验证：截断补齐、唤醒轮最终回复、思考、工具、输出 token、messages 完成通知。"""
        with tempfile.TemporaryDirectory() as td:
            sg = Path(td) / '.system_generated'
            (sg / 'logs').mkdir(parents=True)
            (sg / 'messages').mkdir(parents=True)
            tp = sg / 'logs' / 'transcript.jsonl'
            tf = sg / 'logs' / 'transcript_full.jsonl'
            shutil.copy(FIX / 'agy_transcript.jsonl', tp)
            shutil.copy(FIX / 'agy_transcript_full.jsonl', tf)
            shutil.copy(FIX / 'agy_message_task8.json', sg / 'messages' / 'task8.json')

            d = lp.parse_agy(tp, now=1795682300.0)
            # 最后是 step 204 唤醒轮：包含 step 205 的 run_command 和 step 207 的完整回复（从 transcript_full 补齐）
            self.assertEqual(d['tools_total'], 1)
            self.assertEqual(d['tools'][0]['name'], 'Bash')
            self.assertEqual(d['tools'][0]['state'], 'done')
            self.assertIsNotNone(d['tools'][0]['start'])
            self.assertIsNotNone(d['tools'][0]['end'])
            self.assertGreater(d['turn_out'], 0)
            self.assertIn('All baseline tests pass', d['thinking'])
            self.assertIn('#7 7-one-config-file', d['last_reply'])
            self.assertNotIn('...[truncated]', d['last_reply'])
            # agy_last_reply 也取到未截断的最终回复
            self.assertEqual(lp.agy_last_reply(tp), d['last_reply'])

    def test_first_turn_tools_error_and_bg_task(self):
        """截取真实 agy 夹具前半段（step 0..13）：4 个 Read + 1 个后台 Bash + 1 个 exit 1 错误 Bash + 3 个 Read。"""
        entries = [json.loads(l) for l in (FIX / 'agy_transcript.jsonl').read_text().splitlines() if l.strip()]
        head = [e for e in entries if e['step_index'] <= 13]
        d = lp.parse_agy_entries(head, now=1795681410.0)
        self.assertEqual(d['tools_total'], 9)
        self.assertEqual([t['name'] for t in d['tools'][:5]], ['Read', 'Read', 'Read', 'Read', 'Bash'])
        self.assertEqual(d['tools'][5]['name'], 'Bash')
        self.assertEqual(d['tools'][5]['state'], 'error')      # ls -la mashu/web 退出码 1
        self.assertEqual(len(d['shells']), 1)
        self.assertEqual(d['shells'][0]['state'], 'running')   # task-8 还在跑
        self.assertTrue(d['shells'][0]['id'].endswith('/task-8'))

        # 加上真实 messages/task8.json 后，task-8 标记为已完成
        msg = json.loads((FIX / 'agy_message_task8.json').read_text())
        d2 = lp.parse_agy_entries(head, messages=[msg], now=1795681500.0)
        self.assertEqual(d2['shells'][0]['state'], 'completed')
        self.assertIsNotNone(d2['shells'][0]['end'])

    def test_in_progress_tools_subagent_and_todos(self):
        entries = [
            {'step_index': 0, 'source': 'USER_EXPLICIT', 'type': 'USER_INPUT', 'status': 'DONE',
             'created_at': '2026-11-26T08:00:00Z', 'content': '帮我查一下'},
            {'step_index': 1, 'source': 'MODEL', 'type': 'PLANNER_RESPONSE', 'status': 'DONE',
             'created_at': '2026-11-26T08:00:01Z', 'model': 'gemini-4-neon-high', 'output_tokens': 120,
             'thinking': 'Checking files and spawning subagent.',
             'content': '我先并行读文件和派一个子代理。',
             'tool_calls': [
                 {'name': 'read_file', 'args': {'Path': '/repo/a.py'}},
                 {'name': 'todo_write', 'args': {'todos': [
                     {'content': '读 a.py', 'status': 'completed'},
                     {'content': '改 b.py', 'status': 'in_progress'},
                 ]}},
                 {'name': 'invoke_subagent', 'args': {'Role': 'Code Researcher', 'TypeName': 'research', 'Prompt': 'check logs'}},
                 {'name': 'run_command', 'args': {'CommandLine': 'pytest -q'}},
             ]},
            {'step_index': 2, 'source': 'MODEL', 'type': 'GENERIC', 'status': 'DONE',
             'created_at': '2026-11-26T08:00:02Z',
             'content': 'Created At: 2026-11-26T08:00:01Z\nCompleted At: 2026-11-26T08:00:02Z\nok'},
            {'step_index': 3, 'source': 'MODEL', 'type': 'GENERIC', 'status': 'DONE',
             'created_at': '2026-11-26T08:00:02Z', 'content': 'ok'},
            {'step_index': 4, 'source': 'MODEL', 'type': 'GENERIC', 'status': 'DONE',
             'created_at': '2026-11-26T08:00:03Z', 'content': 'Launched subagent conversationId: sub-12345678'},
            # step 5 (pytest -q) 尚未完成（还没有 GENERIC 行）
        ]
        d = lp.parse_agy_entries(entries, now=1795680005.0)
        self.assertEqual(d['text'], '我先并行读文件和派一个子代理。')
        self.assertEqual(d['tool'], 'Bash · pytest -q')
        self.assertEqual([t['state'] for t in d['tools']], ['done', 'done', 'done', 'doing'])
        self.assertEqual(d['todos'], [{'text': '读 a.py', 'state': 'completed'}, {'text': '改 b.py', 'state': 'in_progress'}])
        self.assertEqual(len(d['agents']), 1)
        self.assertEqual(d['agents'][0]['id'], 'sub-12345678')
        self.assertEqual(d['agents'][0]['state'], 'running')

    def test_resolve_agy_transcript_fixes_missing_cli_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / '.gemini' / 'antigravity-cli'
            real = home / 'brain' / 'conv-1' / '.system_generated' / 'logs' / 'transcript.jsonl'
            real.parent.mkdir(parents=True)
            real.write_text('{}\n')
            wrong = str(real).replace('/.gemini/antigravity-cli/brain/', '/.gemini/antigravity/brain/')
            self.assertEqual(lp.resolve_agy_transcript(wrong, 'conv-1', home), real)

    def test_agy_unquotes_double_encoded_args(self):
        self.assertEqual(lp.agy_tool_info({'name': 'read_file', 'args': {'Path': '"/repo/alpha.txt"'}}), ('Read', 'alpha.txt'))
        self.assertEqual(lp.agy_tool_info({'name': 'run_command', 'args': {'CommandLine': '"ls -la alpha.txt"'}}), ('Bash', 'ls -la alpha.txt'))


class TestCodexParser(unittest.TestCase):
    def test_real_rollout_fixture(self):
        """用真实脱敏 Codex rollout 验证：Code Mode 内层命令展开、出错 exit code、中间文字、token、最终回复。"""
        d = lp.parse_codex(FIX / 'codex_rollout.jsonl', now=1795682200.0)
        self.assertTrue(d['model'])
        self.assertGreaterEqual(d['tools_total'], 5)
        names = [t['name'] for t in d['tools']]
        self.assertIn('Bash', names)
        details = [t['detail'] for t in d['tools']]
        self.assertTrue(any('wf check' in x for x in details))
        self.assertTrue(any('wf context 1095' in x for x in details))
        # 包含非零退出码的命令应标 error
        self.assertTrue(any(t['state'] == 'error' for t in d['tools']))
        self.assertTrue(any(t['state'] == 'done' for t in d['tools']))
        self.assertEqual(d['tool'], '')  # task_complete 后无运行中工具
        self.assertIn('#1095', d['text'])
        self.assertTrue(len(d['last_reply']) > 10)
        self.assertGreater(d['turn_out'], 0)
        self.assertIsNotNone(d['turn_start'])

    def test_reasoning_plan_bg_shell_subagent_and_filechange(self):
        entries = [
            {'timestamp': '2026-11-26T09:00:00Z', 'type': 'turn_context', 'payload': {'model': 'gpt-6-luna'}},
            {'timestamp': '2026-11-26T09:00:00Z', 'type': 'event_msg', 'payload': {'type': 'task_started', 'model_context_window': 200000}},
            {'timestamp': '2026-11-26T09:00:01Z', 'type': 'response_item', 'payload': {
                'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': '**Inspecting live sync**'}]}},
            {'timestamp': '2026-11-26T09:00:02Z', 'type': 'response_item', 'payload': {
                'type': 'function_call', 'call_id': 'c_plan', 'name': 'update_plan',
                'arguments': json.dumps({'plan': [
                    {'step': 'Read studio.py', 'status': 'completed'},
                    {'step': 'Add parser', 'status': 'in_progress'},
                ]})}},
            {'timestamp': '2026-11-26T09:00:02Z', 'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'c_plan', 'output': 'Plan updated'}},
            {'timestamp': '2026-11-26T09:00:03Z', 'type': 'response_item', 'payload': {
                'type': 'function_call', 'call_id': 'c_sh', 'name': 'exec_command',
                'arguments': json.dumps({'cmd': 'npm run dev'})}},
            {'timestamp': '2026-11-26T09:00:04Z', 'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'c_sh',
                'output': '{"session_id": 48618, "output": "Server listening..."}'}},
            {'timestamp': '2026-11-26T09:00:05Z', 'type': 'response_item', 'payload': {
                'type': 'function_call', 'call_id': 'c_ag', 'name': 'spawn_agent',
                'arguments': json.dumps({'agent_type': 'explorer', 'message': 'scan tests'})}},
            {'timestamp': '2026-11-26T09:00:05Z', 'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'c_ag', 'output': '{"agent_id": "ag-7"}'}},
            {'timestamp': '2026-11-26T09:00:06Z', 'type': 'event_msg', 'payload': {
                'type': 'item_completed', 'started_at_ms': 1795683605000, 'completed_at_ms': 1795683606000,
                'item': {'type': 'FileChange', 'id': 'fc1', 'status': 'completed',
                         'changes': {'/repo/mashu/live_parsers.py': {'type': 'add'}}}}},
            {'timestamp': '2026-11-26T09:00:07Z', 'type': 'response_item', 'payload': {
                'type': 'function_call', 'call_id': 'c_run', 'name': 'exec_command',
                'arguments': json.dumps({'cmd': 'python3 -m unittest'})}},
        ]
        d = lp.parse_codex_entries(entries, now=1795683610.0)
        self.assertEqual(d['thinking'], '**Inspecting live sync**')
        self.assertEqual(d['todos'], [{'text': 'Read studio.py', 'state': 'completed'}, {'text': 'Add parser', 'state': 'in_progress'}])
        self.assertEqual(len(d['shells']), 1)
        self.assertEqual(d['shells'][0]['id'], 'shell-48618')
        self.assertEqual(d['shells'][0]['state'], 'running')
        self.assertEqual(len(d['agents']), 1)
        self.assertEqual(d['agents'][0]['id'], 'ag-7')
        self.assertEqual(d['agents'][0]['state'], 'running')
        self.assertEqual(d['tool'], 'Bash · python3 -m unittest')
        self.assertTrue(any(t['name'] == 'Write' and t['detail'] == 'live_parsers.py' for t in d['tools']))

    def test_bg_shell_finished_by_item_completed(self):
        """新版 Codex：后台 shell 不再 write_stdin 轮询，结束只记在 CommandExecution 的 item_completed 里（#13/#19 一直显示「等待后台任务」）。"""
        entries = [
            {'timestamp': '2026-11-27T09:49:11Z', 'type': 'response_item', 'payload': {
                'type': 'function_call', 'call_id': 'c1', 'name': 'exec_command', 'arguments': json.dumps({'cmd': 'bash .mashu/check.sh'})}},
            {'timestamp': '2026-11-27T09:49:12Z', 'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'c1',
                'output': 'Chunk ID: 1\nWall time: 1.0 seconds\nProcess running with session ID 11430\nOutput:\n'}},
            {'timestamp': '2026-11-27T09:49:13Z', 'type': 'response_item', 'payload': {
                'type': 'function_call', 'call_id': 'c2', 'name': 'exec_command', 'arguments': json.dumps({'cmd': 'go test ./...'})}},
            {'timestamp': '2026-11-27T09:49:14Z', 'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'c2', 'output': 'Process running with session ID 222\nOutput:\n'}},
            {'timestamp': '2026-11-27T09:50:40Z', 'type': 'event_msg', 'payload': {
                'type': 'item_completed', 'item': {'type': 'CommandExecution', 'id': 'c1', 'process_id': '11430',
                                                   'status': 'completed', 'exit_code': 0, 'command': ['bash', '.mashu/check.sh']}}},
        ]
        d = lp.parse_codex_entries(entries, now=1795773100.0)
        st = {s['id']: s['state'] for s in d['shells']}
        self.assertNotEqual(st.get('shell-11430'), 'running')
        self.assertEqual(st.get('shell-222'), 'running')


class TestOpenCodeParserAndPlugin(unittest.TestCase):
    def test_opencode_fixture_and_legacy(self):
        d = lp.parse_opencode_file(FIX / 'opencode_live.json', now=1795659930.0)
        self.assertEqual(d['tools_total'], 4)
        self.assertEqual([t['state'] for t in d['tools']], ['done', 'error', 'done', 'doing'])
        self.assertEqual(d['tool'], 'Bash · python3 -m unittest')
        self.assertEqual(len(d['todos']), 3)
        self.assertEqual(len(d['agents']), 1)
        self.assertEqual(d['agents'][0]['tool'], 'Read · app.js')
        self.assertEqual(d['turn_out'], 512)
        self.assertIn('verify parser', d['thinking'])

        # 兼容旧版只写 {sid, text, tool}
        old = lp.parse_opencode({'sid': 'ses_1', 'text': '写到一半', 'tool': 'Bash · ls'})
        self.assertEqual((old['text'], old['tool']), ('写到一半', 'Bash · ls'))
        self.assertEqual(old['tools_total'], 1)
        self.assertEqual(old['tools'][0]['state'], 'doing')

    def test_opencode_plugin_writes_rich_live_json(self):
        """直接用 Node 驱动 integrations/opencode/mashu.js，验证事件 → 原子写 live/<cid>.json → parse_opencode_file 全链路。"""
        if not shutil.which('node'):
            self.skipTest('node not installed')
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            fake_bin = home / 'fake_mashu'
            fake_bin.write_text('#!/usr/bin/env bash\nexit 0\n')
            fake_bin.chmod(0o755)
            plugin_url = (CODE_ROOT / 'integrations' / 'opencode' / 'mashu.js').as_uri()
            script = f"""
import {{ MashuPlugin }} from {json.dumps(plugin_url)};
const p = await MashuPlugin();
await p["chat.message"]({{ sessionID: "ses_root" }}, {{ message: {{ id: "m_u1" }}, parts: [{{ type: "text", text: "帮我检查" }}] }});
await p.event({{ event: {{ type: "session.updated", properties: {{ info: {{ id: "ses_root", model: {{ providerID: "opencode", id: "demo-free" }} }} }} }} }});
await p.event({{ event: {{ type: "message.updated", properties: {{ info: {{ id: "m_a1", role: "assistant", sessionID: "ses_root", tokens: {{ output: 240, reasoning: 60 }} }} }} }} }});
await p.event({{ event: {{ type: "message.part.updated", properties: {{ part: {{ id: "p_r1", sessionID: "ses_root", messageID: "m_a1", type: "reasoning", text: "Checking README first." }} }} }} }});
await p.event({{ event: {{ type: "message.part.updated", properties: {{ part: {{ id: "p_t1", callID: "c1", sessionID: "ses_root", messageID: "m_a1", type: "tool", tool: "read", state: {{ status: "completed", input: {{ filePath: "/repo/README.md" }}, time: {{ start: 1795680001000, end: 1795680001500 }} }} }} }} }} }});
await p.event({{ event: {{ type: "message.part.updated", properties: {{ part: {{ id: "p_t2", callID: "c2", sessionID: "ses_root", messageID: "m_a1", type: "tool", tool: "todowrite", state: {{ status: "completed", input: {{ todos: [{{ content: "读 README", status: "completed" }}, {{ content: "跑命令", status: "in_progress" }}] }}, time: {{ start: 1795680002000, end: 1795680002100 }} }} }} }} }} }});
await p.event({{ event: {{ type: "message.part.updated", properties: {{ part: {{ id: "p_t3", callID: "c3", sessionID: "ses_root", messageID: "m_a1", type: "tool", tool: "task", state: {{ status: "running", input: {{ subagent_type: "explore", description: "扫描 web/app.js", run_in_background: true }}, metadata: {{ sessionId: "ses_child" }}, time: {{ start: 1795680002500 }} }} }} }} }} }});
await p.event({{ event: {{ type: "session.created", properties: {{ info: {{ id: "ses_child", parentID: "ses_root", title: "扫描 web/app.js" }} }} }} }});
await p.event({{ event: {{ type: "message.part.updated", properties: {{ part: {{ id: "p_ct1", callID: "cc1", sessionID: "ses_child", messageID: "m_c1", type: "tool", tool: "grep", state: {{ status: "running", input: {{ pattern: "detailHtml" }} }} }} }} }} }});
await p.event({{ event: {{ type: "message.part.updated", properties: {{ part: {{ id: "p_t4", callID: "c4", sessionID: "ses_root", messageID: "m_a1", type: "tool", tool: "bash", state: {{ status: "running", input: {{ command: "git status -sb" }}, time: {{ start: 1795680003000 }} }} }} }} }} }});
await p.event({{ event: {{ type: "message.part.updated", properties: {{ part: {{ id: "p_txt", sessionID: "ses_root", messageID: "m_a1", type: "text", text: "已经读完 README，正在跑 git status。" }} }} }} }});
await new Promise((r) => setTimeout(r, 350));
"""
            env = dict(os.environ, MASHU_SESSION='42', MASHU_HOME=str(home), MASHU_BIN=str(fake_bin))
            r = subprocess.run(['node', '--input-type=module', '-e', script], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            live_file = home / 'live' / '42.json'
            self.assertTrue(live_file.is_file())
            d = lp.parse_opencode_file(live_file)
            self.assertEqual(d['tools_total'], 4)
            self.assertEqual([t['name'] for t in d['tools']], ['Read', 'TodoWrite', 'Task', 'Bash'])
            self.assertEqual([t['state'] for t in d['tools']], ['done', 'done', 'doing', 'doing'])
            self.assertEqual(d['tool'], 'Bash · git status -sb')
            self.assertEqual(d['text'], '已经读完 README，正在跑 git status。')
            self.assertEqual(d['thinking'], 'Checking README first.')
            self.assertEqual(len(d['todos']), 2)
            self.assertEqual(len(d['agents']), 1)
            self.assertEqual(d['agents'][0]['tool'], 'Grep · detailHtml')
            self.assertEqual(d['turn_out'], 300)


class TestStudioLiveSyncIntegration(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(studio._LIVE_CACHE.clear)
        self.repo = self.make_repo('builda')
        studio.greenlight(self.conn, self.repo, 'builda', '测试项目')

    def _make_crew(self, key_suffix, prov, sid):
        f, key, _ = studio.pitch(self.conn, 'builda', f'f-{key_suffix}', f'需求 {key_suffix}')
        studio.tick(self.conn)
        crew = studio.active_crew(self.conn, 'builda', key)
        studio.meta_set(self.conn, f'camprov:{crew["id"]}', prov)
        if sid:
            studio.meta_set(self.conn, f'provider:{sid}', prov)
        self.conn.execute('UPDATE cameras SET claude_sid=?, state="running", last_active=?, last_stop=0 WHERE id=?',
                          (sid, time.time(), crew['id']))
        self.conn.commit()
        return key, studio.camera(self.conn, crew['id'])

    def test_gemini_codex_opencode_detail_live_and_stop(self):
        # 1. Gemini (agy)
        saved_g = studio.GEMINI_HOME
        studio.GEMINI_HOME = self.tmp / 'gemini-cli'
        self.addCleanup(setattr, studio, 'GEMINI_HOME', saved_g)
        g_key, g_cam = self._make_crew('agy', 'gemini', 'conv-agy-1')
        sg = studio.GEMINI_HOME / 'brain' / 'conv-agy-1' / '.system_generated'
        (sg / 'logs').mkdir(parents=True)
        (sg / 'messages').mkdir(parents=True)
        # 放前半段（带进行中 task-8 + 9 个工具）
        entries = [json.loads(l) for l in (FIX / 'agy_transcript.jsonl').read_text().splitlines() if l.strip()]
        head = [e for e in entries if e['step_index'] <= 13]
        (sg / 'logs' / 'transcript.jsonl').write_text('\n'.join(json.dumps(e, ensure_ascii=False) for e in head) + '\n')

        sd_g = studio.session_detail(self.conn, g_cam)
        self.assertEqual(sd_g['provider'], 'gemini')
        self.assertEqual(sd_g['tools_total'], 9)
        self.assertEqual(len(sd_g['shells']), 1)
        self.assertEqual(sd_g['shells'][0]['state'], 'running')
        self.assertEqual(studio.bg_tasks_of(self.conn, g_cam), 1)
        lv = studio.live_replies(self.conn)[f'builda/{g_key}']
        self.assertEqual(lv['tools_total'], 9)
        self.assertEqual(len(lv['tools']), 8)

        # 替换成完整 transcript + transcript_full，验证 hook-stop 转发真实未截断最终回复
        shutil.copy(FIX / 'agy_transcript.jsonl', sg / 'logs' / 'transcript.jsonl')
        shutil.copy(FIX / 'agy_transcript_full.jsonl', sg / 'logs' / 'transcript_full.jsonl')
        wrong_tp = str(sg / 'logs' / 'transcript.jsonl').replace('/gemini-cli/', '/antigravity/')
        os.environ['MASHU_SESSION'] = str(g_cam['id'])
        try:
            studio._hook_stop({'conversationId': 'conv-agy-1', 'transcriptPath': wrong_tp, 'terminationReason': 'model_stop'}, True)
        finally:
            os.environ.pop('MASHU_SESSION', None)
        rep = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="reply" AND channel=?', f'builda/{g_key}')
        self.assertIsNotNone(rep)
        self.assertIn('#7 7-one-config-file', rep['text'])
        self.assertNotIn('...[truncated]', rep['text'])

        # 2. Codex
        c_key, c_cam = self._make_crew('codex', 'codex', '01a216fd-c675-7600-b1b6-c494f29bb1ee')
        rpath = self.tmp / 'rollout-codex.jsonl'
        shutil.copy(FIX / 'codex_rollout.jsonl', rpath)
        studio.meta_set(self.conn, f'transcript:{c_cam["claude_sid"]}', str(rpath))
        sd_c = studio.session_detail(self.conn, c_cam)
        self.assertEqual(sd_c['provider'], 'codex')
        self.assertGreaterEqual(sd_c['tools_total'], 5)
        self.assertTrue(sd_c['status']['model']['display_name'])
        self.assertGreater(sd_c['turn_out'], 0)
        lv_c = studio.live_replies(self.conn)[f'builda/{c_key}']
        self.assertIn('#1095', lv_c['text'])
        self.assertTrue(len(lv_c['tools']) >= 5)

        # 3. OpenCode
        o_key, o_cam = self._make_crew('oc', 'opencode', '')  # 无 claude_sid 时也能读 live/<id>.json
        (studio.HOME / 'live').mkdir(exist_ok=True)
        shutil.copy(FIX / 'opencode_live.json', studio.HOME / 'live' / f'{o_cam["id"]}.json')
        sd_o = studio.session_detail(self.conn, o_cam)
        self.assertEqual(sd_o['provider'], 'opencode')
        self.assertEqual(sd_o['tools_total'], 4)
        self.assertEqual(len(sd_o['todos']), 3)
        self.assertEqual(len(sd_o['agents']), 1)
        self.assertEqual(studio.bg_tasks_of(self.conn, o_cam), 1)
        self.assertEqual(studio.turn_progress_of(self.conn, o_cam)[1], 'Bash · python3 -m unittest')
        lv_o = studio.live_replies(self.conn)[f'builda/{o_key}']
        self.assertEqual(lv_o['tool'], 'Bash · python3 -m unittest')
        self.assertEqual(len(lv_o['tools']), 4)

    def test_session_detail_limits_per_provider(self):
        """会话详细模式顶部状态栏额度按平台与会话所用账号对齐，不把 Claude 的 5h/7d 串到 Gemini / Codex / OpenCode。"""
        fake_limits = {
            'claude': {'five_hour': {'used_percentage': 16}, 'seven_day': {'used_percentage': 26}},
            'five_hour': {'used_percentage': 16},
            'seven_day': {'used_percentage': 26},
            'codex': {
                'account': 'cx-1',
                'five_hour': {'used_percentage': 10}, 'seven_day': {'used_percentage': 28},
                'accounts': [
                    {'id': 'cx-1', 'quota': {'five_hour': {'used_percentage': 10}, 'seven_day': {'used_percentage': 28}}},
                    {'id': 'cx-2', 'quota': {'five_hour': {'used_percentage': 45}, 'seven_day': {'used_percentage': 62}}},
                ],
            },
            'gemini': {
                'available': True, 'remaining': 0.85, 'reset': '2026-11-26T12:00:00Z', 'low_model': 'Gemini 4 Neon', 'account': 'gm-2',
                'accounts': [
                    {'id': 'gm-2', 'quota': {'remaining': 0.85, 'reset': '2026-11-26T12:00:00Z', 'low_model': 'Gemini 4 Neon'}},
                ],
            },
            'opencode': {'today': 0.42, 'week': 1.25, 'at': 1795680000.0},
        }
        saved_rl = studio.read_limits
        studio.read_limits = lambda: fake_limits
        self.addCleanup(setattr, studio, 'read_limits', saved_rl)

        _, c_cl = self._make_crew('cl-lim', 'claude', 'cl-sid')
        _, c_gm = self._make_crew('gm-lim', 'gemini', 'gm-sid')
        _, c_cx = self._make_crew('cx-lim', 'codex', 'cx-sid')
        _, c_oc = self._make_crew('oc-lim', 'opencode', 'oc-sid')
        studio.meta_set(self.conn, f'camacct:{c_cx["id"]}', 'cx-2')
        studio.meta_set(self.conn, f'camacct:{c_gm["id"]}', 'gm-2')

        # Claude 不变
        lim_cl = studio.session_detail(self.conn, c_cl)['limits']
        self.assertEqual(lim_cl['five_hour']['used_percentage'], 16)
        self.assertEqual(lim_cl['seven_day']['used_percentage'], 26)

        # Gemini：显示该号剩余额度，不带 Claude 的 five_hour / seven_day
        lim_gm = studio.session_detail(self.conn, c_gm)['limits']
        self.assertNotIn('five_hour', lim_gm)
        self.assertAlmostEqual(lim_gm['remaining'], 0.85)
        self.assertEqual(lim_gm['low_model'], 'Gemini 4 Neon')

        # Codex：显示该会话所用 cx-2 号的 5h / 7d（不是 Claude 的 16/26，也不是默认号 cx-1 的 10/28）
        lim_cx = studio.session_detail(self.conn, c_cx)['limits']
        self.assertEqual(lim_cx['five_hour']['used_percentage'], 45)
        self.assertEqual(lim_cx['seven_day']['used_percentage'], 62)
        self.assertEqual(lim_cx['account'], 'cx-2')

        # OpenCode：显示花费，不带 Claude 的 five_hour / seven_day
        lim_oc = studio.session_detail(self.conn, c_oc)['limits']
        self.assertNotIn('five_hour', lim_oc)
        self.assertAlmostEqual(lim_oc['today'], 0.42)
        self.assertAlmostEqual(lim_oc['week'], 1.25)


if __name__ == '__main__':
    unittest.main()
