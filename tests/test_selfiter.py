"""#4 每日自迭代（mashu/selfiter.py）：假 claude -p 跑完整流程。用例按 AC 编号。"""
import datetime
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import Base, studio  # noqa: E402
import selfiter  # noqa: E402

# -p 调用走这里；交互式启动（主脑等会话）交给 Base 的假 claude
FAKE_P = """#!/usr/bin/env bash
if [ "$1" != "-p" ]; then exec "$FAKE_SI_INTERACTIVE" "$@"; fi
d="$FAKE_SI_DIR"
for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$d/args"; done
printf 'SESSION=%s\\n' "$MASHU_SESSION" >> "$d/args"
cat > "$d/stdin"
[ -n "$FAKE_SI_SLEEP" ] && sleep "$FAKE_SI_SLEEP"
echo '[claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"sdk"}' >&2
echo 'Warning: model claude-sonnet-5-5 not in catalog, assuming 200k context'
if [ -f "$d/raw" ]; then cat "$d/raw"; else
python3 - "$d/result" "${FAKE_SI_ERROR:-}" <<'EOF'
import json, sys
err = bool(sys.argv[2])
print(json.dumps({'type': 'result', 'subtype': 'error_max_budget_usd' if err else 'success', 'is_error': err,
                  'result': '' if err else open(sys.argv[1]).read(), 'total_cost_usd': 0.0123,
                  'usage': {'input_tokens': 1000, 'output_tokens': 200, 'cache_read_input_tokens': 30,
                            'cache_creation_input_tokens': 40},
                  'modelUsage': {'claude-sonnet-5-5': {}}}))
EOF
fi
exit ${FAKE_SI_CODE:-0}
"""

REPORT = dict(
    summary='整体顺利，#4 上报绕了一圈。',
    sessions=[dict(session='#1 遇见', role='执行者', project='Builda #1', purpose='写方案', chain='子脑派 → 写文档 → 交接',
                   effect='好', comment='按时完成')],
    suggestions=[dict(area='prompt', target='mashu/prompts/crew.md「需要人的时候」', title='请示前先查项目说明',
                      evidence='上报 #1', change='加一句：请示前先读 .mashu/README.md', priority='高',
                      signal=dict(kind='audit', query='sign@executive', expect='下降')),   # #19 起采纳要有生效信号
                 dict(area='weird', target='x', title='未知类别归 other', evidence='e', change='c', priority='低'),
                 dict(area='rules', title='  ')],
    memory_candidates=[dict(target='memory/方针.md', title='重启服务由用户安排', evidence='#1', change='原文')],
    workflow_candidates=[dict(target='playbooks/quick.json', title='小改动三阶段', evidence='3 次', change='方案→实现→测试')])


class SelfIterBase(Base):
    def setUp(self):
        super().setUp()
        self.si = self.tmp / 'si'
        self.si.mkdir()
        fake = self.tmp / 'fake-claude-p'
        fake.write_text(FAKE_P)
        fake.chmod(0o755)
        env = dict(FAKE_SI_DIR=str(self.si), FAKE_SI_INTERACTIVE=studio.CONFIG_OVERRIDES['claude'])
        for k, v in env.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        studio.CONFIG_OVERRIDES['claude'] = str(fake)
        self.set_result(REPORT)
        self.addCleanup(self.join_threads)

    def join_threads(self):
        for t in selfiter.THREADS:
            t.join(30)
        selfiter.THREADS.clear()

    def setenv(self, **kw):
        for k, v in kw.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)

    def set_result(self, rep, fenced=True):
        text = json.dumps(rep, ensure_ascii=False) if not isinstance(rep, str) else rep
        (self.si / 'result').write_text(f'好的，报告如下：\n```json\n{text}\n```\n' if fenced else text)

    def runs(self):
        selfiter.ensure_tables(self.conn)
        return studio.rows(self.conn, 'SELECT * FROM self_iterate_runs ORDER BY id')

    def seed(self):
        """造一天的活动：功能、执行者会话、回复、上报、失败的检查、用户消息、用量、往期条目。"""
        repo = self.make_repo('builda')
        f, _ = studio.greenlight(self.conn, repo, 'builda', 'Builda')
        f, key, cam = studio.pitch(self.conn, 'builda', 'export', '导出 CSV', playbook='standard')
        now = time.time()
        self.conn.execute('UPDATE cameras SET state="ended", ended_reason="wrap", started_at=?, last_active=?, '
                          'tokens_in=5000, tokens_out=700 WHERE id=?', (now - 1800, now - 60, cam['id']))
        self.conn.commit()
        cam = self.cam(cam['id'])
        studio.post(self.conn, 'crew', f'builda/{key}', 'reply', '方案写完了，交接给导演。', camera=cam)
        studio.create_report(self.conn, 'ask', cam, 'builda', key, '用哪个库导出？', summary='选导出库',
                             options=['csv', 'pandas'])
        studio.post(self.conn, 'executive', 'assistant', 'chat', '今天先把导出做完')
        data = studio.seq_state(f, key)
        (studio.seq_dir(f, key) / 'tests' / 'check-1.md').write_text('ok line\nFAIL: test_export (tests.T)\nERROR: boom\n')
        data['stages'][0].update(status='已完成', updated_at=studio.now_iso(),
                                 check_result=dict(ok=False, path='tests/check-1.md', code=1))
        studio.save_state(f, key, data)
        self.conn.execute('INSERT INTO usage(day, camera_id, role, film_id, seq_key, model, tokens_in, tokens_out, cache_read, '
                          'cache_write, cost) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (studio.today(), cam['id'], 'crew', 'builda', key, 'claude-opus-5-5', 5000, 700, 0, 0, 1.5))
        selfiter.ensure_tables(self.conn)
        self.conn.execute('INSERT INTO self_iterate_items(run_id, day, section, area, title, status, note) VALUES(0,?,?,?,?,?,?)',
                          (studio.today(), 'suggestion', 'prompt', '往期：交接写清生效方式', '采纳', '已改 crew.md'))
        self.conn.commit()
        return f, key, cam

    def baseline_yesterday(self):
        studio.meta_set(self.conn, 'self_iterate_since', time.time() - 2 * 86400)
        studio.CONFIG_OVERRIDES['self_iterate_time'] = '00:00'

    def assistant_cam(self):
        cur = self.conn.execute('INSERT INTO cameras(role, name, state) VALUES("assistant", "麻薯", "ended")')
        self.conn.commit()
        return self.cam(cur.lastrowid)

    def tick(self):
        studio.tick(self.conn)
        self.join_threads()


class TestSchedule(SelfIterBase):
    def test_ac1_tick_runs_in_background(self):
        self.seed()
        self.baseline_yesterday()
        before = studio.one(self.conn, 'SELECT COUNT(*) n FROM cameras WHERE role="crew"')['n']
        self.setenv(FAKE_SI_SLEEP='1')
        t0 = time.time()
        studio.tick(self.conn)
        self.assertLess(time.time() - t0, 1.0, 'tick 不能等 claude 跑完')
        self.assertEqual([r['status'] for r in self.runs()], ['running'])
        self.join_threads()
        r = self.runs()
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]['status'], r[0]['trigger']), ('ok', 'tick'))
        self.assertEqual(studio.one(self.conn, 'SELECT COUNT(*) n FROM cameras WHERE role="crew"')['n'], before)
        self.assertNotIn('selfiter', studio.alive_panes())
        # 没到点 / 关掉时不跑
        studio.CONFIG_OVERRIDES['self_iterate_time'] = '23:59'
        self.conn.execute('DELETE FROM self_iterate_runs')
        self.conn.commit()
        if datetime.datetime.now().strftime('%H:%M') < '23:59':
            self.tick()
            self.assertEqual(self.runs(), [])
        studio.CONFIG_OVERRIDES.update(self_iterate_time='00:00', self_iterate_enabled=False)
        self.tick()
        self.assertEqual(self.runs(), [])

    def test_ac2_no_rerun_same_day(self):
        self.seed()
        self.baseline_yesterday()
        for st in ('ok', 'failed', 'skipped'):
            with self.subTest(status=st):
                self.conn.execute('DELETE FROM self_iterate_runs')
                self.conn.execute('INSERT INTO self_iterate_runs(day, trigger, status, started_at) VALUES(?,?,?,?)',
                                  (studio.today(), 'tick', st, time.time()))
                self.conn.commit()
                self.tick()
                self.assertEqual(len(self.runs()), 1)
                with self.assertRaises(SystemExit) as cm:
                    self.cli(None, 'self-iterate', 'run')
                self.assertIn('今天已跑过', str(cm.exception))
        out = self.cli(None, 'self-iterate', 'run', '--force')
        self.assertIn('请示前先查项目说明', out)
        self.assertEqual([r['status'] for r in self.runs()], ['skipped', 'ok'])
        # 进行中的不能再起，--force 也不行
        self.conn.execute('INSERT INTO self_iterate_runs(day, trigger, status, started_at, pid) VALUES(?,?,?,?,?)',
                          (studio.today(), 'manual', 'running', time.time(), os.getpid()))
        self.conn.commit()
        with self.assertRaises(SystemExit) as cm:
            self.cli(None, 'self-iterate', 'run', '--force')
        self.assertIn('在跑', str(cm.exception))

    def test_ac2_reap_stale_running(self):
        self.seed()
        self.conn.execute('INSERT INTO self_iterate_runs(day, trigger, status, started_at, pid) VALUES(?,?,?,?,?)',
                          ('2026-01-01', 'tick', 'running', time.time(), 999999))
        self.conn.commit()
        selfiter.maybe_run(self.conn)
        r = self.runs()[0]
        self.assertEqual(r['status'], 'failed')
        self.assertIn('中断', r['error'])

    def test_ac3_first_tick_only_baseline(self):
        self.seed()
        studio.CONFIG_OVERRIDES['self_iterate_time'] = '00:00'
        self.assertIsNone(studio.meta_get(self.conn, 'self_iterate_since'))
        self.tick()
        self.assertIsNotNone(studio.meta_get(self.conn, 'self_iterate_since'))
        self.assertEqual(self.runs(), [])
        self.tick()                      # 今天的到点时刻早于基准时间 → 也不跑
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.si / 'args').exists())

    def test_ac13_tick_survives_selfiter_error(self):
        self.seed()
        self.baseline_yesterday()
        with mock.patch.object(selfiter, 'claim_run', side_effect=Exception('炸了')):
            studio.tick(self.conn)
        with mock.patch.object(selfiter, 'maybe_run', side_effect=Exception('炸了')):
            studio.tick(self.conn)
        with mock.patch.object(selfiter, 'collect_material', side_effect=Exception('素材炸了')):
            self.tick()
        r = self.runs()
        self.assertEqual(r[-1]['status'], 'failed')
        self.assertIn('素材炸了', r[-1]['error'])
        self.assertIsNotNone(studio.active_assistant(self.conn))   # tick 后面的步骤照常跑了
        # 简报和状态接口也不受影响
        with mock.patch.object(selfiter, 'pending_count', side_effect=Exception('x')):
            self.assertIn('简报', studio.digest_text(self.conn, time.time() - 3600))
            self.assertIn('films', studio.status_data(self.conn))


class TestMaterial(SelfIterBase):
    def test_ac4_material_sections_and_cap(self):
        f, key, cam = self.seed()
        text, active = selfiter.collect_material(self.conn)
        self.assertTrue(active)
        for kw in ('## 0 概况', '## 1 会话', f'#{cam["id"]}「{cam["name"]}」', '目的：', '链路：', '效果（回复）：方案写完了',
                   '## 2 上报与处理', '选导出库', '## 3 功能与阶段', 'FAIL: test_export', 'ERROR: boom', '## 4 用户原话',
                   '今天先把导出做完', '## 5 决策', '## 6 用量', 'claude-opus-5-5', '## 7 系统事件', '## 8 复盘数据',
                   '## 9 往期建议', '往期：交接写清生效方式', '已改 crew.md', '## 10 现行规则原文', 'prompts/crew.md'):
            self.assertIn(kw, text)
        studio.CONFIG_OVERRIDES['self_iterate_max_chars'] = 1500
        text, _ = selfiter.collect_material(self.conn)
        self.assertLessEqual(len(text), 1500)
        self.assertIn('已截断', text)
        out = self.cli(None, 'self-iterate', 'material')
        self.assertIn('有活动', out)

    def test_ac5_skip_when_idle(self):
        out = self.cli(None, 'self-iterate', 'run')
        self.assertIn('skipped', out)
        r = self.runs()[0]
        self.assertEqual(r['status'], 'skipped')
        self.assertFalse((self.si / 'args').exists(), '没活动不该调用 claude')
        self.assertTrue((Path(r['archive']) / 'input.md').exists())


class TestCall(SelfIterBase):
    def test_ac6_cli_args_and_env(self):
        self.seed()
        boss = self.assistant_cam()
        self.cli(boss, 'self-iterate', 'run')
        args = (self.si / 'args').read_text().splitlines()
        argv = [x[4:] for x in args if x.startswith('ARG:')]
        self.assertEqual(argv[0], '-p')
        for a, b in (('--model', 'claude-sonnet-5-5'), ('--output-format', 'json'), ('--tools', ''), ('--max-budget-usd', '1.0')):
            self.assertEqual(argv[argv.index(a) + 1], b)
        for a in ('--no-session-persistence', '--strict-mcp-config', '--system-prompt'):
            self.assertIn(a, argv)
        self.assertIn('只输出一个 JSON 对象', (self.si / 'args').read_text())      # 提示词多行，日志里被拆成多行
        self.assertIn('SESSION=', args)
        r = self.runs()[0]
        self.assertEqual((self.si / 'stdin').read_text(), (Path(r['archive']) / 'input.md').read_text())
        studio.CONFIG_OVERRIDES.update(self_iterate_model='claude-sonnet-5-5', self_iterate_budget_usd=0.2)
        (self.si / 'args').unlink()
        self.cli(None, 'self-iterate', 'run', '--force')
        argv = [x[4:] for x in (self.si / 'args').read_text().splitlines() if x.startswith('ARG:')]
        self.assertEqual(argv[argv.index('--model') + 1], 'claude-sonnet-5-5')
        self.assertEqual(argv[argv.index('--max-budget-usd') + 1], '0.2')

    def test_ac7_warning_ignored(self):
        res, err = selfiter.parse_cli_output(
            'Warning: model not in catalog\n{"type":"result","subtype":"success","is_error":false,"result":"{}"}\n',
            '[claude-code:unrecognized_model] {"model":"claude-sonnet-5-5"}', 0)
        self.assertIsNone(err)
        self.assertEqual(res['result'], '{}')
        # 多行美化的 JSON 也认
        res, err = selfiter.parse_cli_output(json.dumps(dict(type='result', subtype='success', is_error=False, result='x'),
                                                        indent=2), '', 0)
        self.assertIsNone(err)
        # 完整流程：假 claude 带警告行 → ok
        self.seed()
        self.cli(None, 'self-iterate', 'run')
        r = self.runs()[0]
        self.assertEqual(r['status'], 'ok', r['error'])
        self.assertIn('unrecognized_model', (Path(r['archive']) / 'output.txt').read_text())
        # 报告没有围栏也能解析
        self.set_result(REPORT, fenced=False)
        self.cli(None, 'self-iterate', 'run', '--force')
        self.assertEqual(self.runs()[-1]['status'], 'ok')

    def test_ac8_failures_recorded_no_retry(self):
        self.seed()
        self.baseline_yesterday()
        cases = [('report-not-json', dict(), '报告不是合法 JSON'),
                 ('is-error', dict(FAKE_SI_ERROR='1'), 'error_max_budget_usd'),
                 ('exit-1-no-json', dict(FAKE_SI_CODE='1'), '退出码 1'),
                 ('timeout', dict(FAKE_SI_SLEEP='10'), '超时')]
        for name, env, why in cases:
            with self.subTest(case=name):
                self.conn.execute('DELETE FROM self_iterate_runs')
                self.conn.execute('DELETE FROM messages')
                self.conn.commit()
                for k in ('FAKE_SI_ERROR', 'FAKE_SI_CODE', 'FAKE_SI_SLEEP'):
                    os.environ.pop(k, None)
                self.setenv(**env)
                (self.si / 'raw').unlink(missing_ok=True)
                self.set_result('这不是 JSON', fenced=False)
                if name == 'exit-1-no-json':
                    (self.si / 'raw').write_text('Error: something broke\n')
                studio.CONFIG_OVERRIDES['self_iterate_timeout_minutes'] = 0.02 if name == 'timeout' else 15
                t0 = time.time()
                self.tick()
                self.assertLess(time.time() - t0, 8)
                r = self.runs()
                self.assertEqual(len(r), 1)
                self.assertEqual(r[0]['status'], 'failed')
                self.assertIn(why, r[0]['error'])
                self.assertTrue((Path(r[0]['archive']) / 'output.txt').exists())
                n = studio.rows(self.conn, 'SELECT * FROM messages WHERE kind="notice" AND channel="assistant" AND target="web" '
                                           'AND text LIKE "%自迭代%失败%"')
                self.assertEqual(len(n), 1)
                self.tick()                                            # 当天不自动重试
                self.assertEqual(len(self.runs()), 1)
        # 超预算的那次也记了用量
        self.assertTrue(studio.rows(self.conn, 'SELECT * FROM usage WHERE role="self-iterate"'))


class TestReport(SelfIterBase):
    def test_ac9_full_flow_store_and_adopt(self):
        self.seed()
        out = self.cli(None, 'self-iterate', 'run')
        r = self.runs()[0]
        self.assertEqual(r['status'], 'ok', r['error'])
        items = studio.rows(self.conn, 'SELECT * FROM self_iterate_items WHERE run_id=? ORDER BY id', r['id'])
        self.assertEqual([(x['section'], x['area']) for x in items],
                         [('suggestion', 'prompt'), ('suggestion', 'other'), ('memory', 'memory'), ('workflow', 'workflow')])
        self.assertTrue(all(x['status'] == '待定' for x in items))
        self.assertEqual(items[0]['change'], '加一句：请示前先读 .mashu/README.md')
        self.assertEqual(json.loads(r['report'])['sessions'][0]['effect'], '好')
        arch = Path(r['archive'])
        for name in ('input.md', 'output.txt', 'report.json', 'report.md'):
            self.assertTrue((arch / name).exists(), name)
        self.assertIn(f'#{items[0]["id"]}', (arch / 'report.md').read_text())
        u = studio.rows(self.conn, 'SELECT * FROM usage WHERE role="self-iterate"')
        self.assertEqual(len(u), 1)
        self.assertEqual((u[0]['tokens_in'], u[0]['tokens_out'], u[0]['cost'], u[0]['model']), (1000, 200, 0.0123, 'claude-sonnet-5-5'))
        self.assertEqual((r['tokens_in'], r['cache_read'], r['cache_write']), (1000, 30, 40))
        self.assertTrue(studio.rows(self.conn, 'SELECT * FROM messages WHERE target="web" AND text LIKE "%自迭代报告%好了%"'))
        self.assertIn(f'#{items[0]["id"]}', out)
        show = self.cli(None, 'self-iterate', 'show')
        self.assertIn('请示前先查项目说明', show)
        self.assertIn('整体顺利', show)
        self.assertIn('请示前先查项目说明', self.cli(None, 'self-iterate', 'show', studio.today()))
        # 采纳 / 不采纳 / 撤销
        boss = self.assistant_cam()
        self.cli(boss, 'self-iterate', 'adopt', str(items[0]['id']), str(items[2]['id']), '--note', '已改 crew.md')
        x = studio.one(self.conn, 'SELECT * FROM self_iterate_items WHERE id=?', items[0]['id'])
        self.assertEqual((x['status'], x['note'], x['decided_by']), ('采纳', '已改 crew.md', '麻薯（主脑）'))
        self.cli(None, 'self-iterate', 'reject', f'#{items[1]["id"]}', '--note', '没依据')
        self.assertEqual(studio.one(self.conn, 'SELECT * FROM self_iterate_items WHERE id=?', items[1]['id'])['decided_by'], '用户')
        self.cli(None, 'self-iterate', 'reset', str(items[2]['id']))
        self.assertEqual(studio.one(self.conn, 'SELECT status FROM self_iterate_items WHERE id=?', items[2]['id'])['status'], '待定')
        acts = [a['action'] for a in studio.rows(self.conn, 'SELECT action FROM audit WHERE action LIKE "self-iterate-%" ORDER BY id')]
        self.assertEqual(acts, ['self-iterate-adopt', 'self-iterate-adopt', 'self-iterate-reject', 'self-iterate-reset'])
        lst = self.cli(None, 'self-iterate', 'list', '--status', '待定')
        self.assertIn(f'#{items[2]["id"]}', lst)
        self.assertNotIn(f'#{items[0]["id"]} ', lst)
        with self.assertRaises(SystemExit):
            self.cli(None, 'self-iterate', 'adopt', '99999')

    def test_ac10_adopt_permission(self):
        f, key, crew = self.seed()
        self.cli(None, 'self-iterate', 'run')
        iid = studio.one(self.conn, 'SELECT id FROM self_iterate_items WHERE status="待定" LIMIT 1')['id']
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'x')
        for c in (self.cam(crew['id']), prod):
            with self.subTest(role=c['role']):
                with self.assertRaises(SystemExit) as cm:
                    self.cli(c, 'self-iterate', 'adopt', str(iid))
                self.assertIn('只有主脑', str(cm.exception))
        self.assertEqual(studio.one(self.conn, 'SELECT status FROM self_iterate_items WHERE id=?', iid)['status'], '待定')
        self.cli(self.assistant_cam(), 'self-iterate', 'adopt', str(iid))
        self.assertEqual(studio.one(self.conn, 'SELECT status FROM self_iterate_items WHERE id=?', iid)['status'], '采纳')

    def test_ac11_api_data(self):
        self.assertEqual(selfiter.api_data(self.conn, '')['runs'], [])
        self.seed()
        self.cli(None, 'self-iterate', 'run')
        d = selfiter.api_data(self.conn, '')
        self.assertEqual(d['day'], studio.today())
        self.assertEqual([x['day'] for x in d['days']], [studio.today()])
        self.assertEqual(len(d['runs']), 1)
        self.assertNotIn('report', d['runs'][0])
        self.assertEqual(d['report']['summary'], REPORT['summary'])
        self.assertEqual(len(d['items']), 4)
        self.assertEqual(d['pending'], 4)          # seed 里的往期条目是“采纳”，不算
        self.assertEqual(studio.status_data(self.conn)['self_iterate_pending'], d['pending'])
        self.assertEqual(selfiter.api_data(self.conn, '2000-01-01')['runs'], [])

    def test_ac12_digest_line(self):
        self.assertNotIn('自迭代', studio.digest_text(self.conn, time.time() - 3600))
        self.seed()
        self.cli(None, 'self-iterate', 'run')
        self.assertIn('自迭代：4 条待定', studio.digest_text(self.conn, time.time() - 3600))


if __name__ == '__main__':
    unittest.main()
