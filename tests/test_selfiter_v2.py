"""#19 自迭代 v2：生效信号与自动验效、事故即时小复盘、素材按优先级截断、用量异常、用户操作统计、重启按钮改成发布。"""
import datetime
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_selfiter import REPORT, SelfIterBase  # noqa: E402
from test_studio import studio  # noqa: E402
import selfiter  # noqa: E402

DAY = 86400


def iso_at(ts):
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec='seconds')


class V2Base(SelfIterBase):
    def item(self, title='往期建议', status='采纳', decided=None, signal=None, section='suggestion', day=None):
        selfiter.ensure_tables(self.conn)
        decided = decided if decided is not None else time.time() - 2 * DAY
        cur = self.conn.execute('INSERT INTO self_iterate_items(run_id, day, section, area, target, title, evidence, change, '
                                'priority, status, decided_at, signal) VALUES(0,?,?,?,?,?,?,?,?,?,?,?)',
                                (day or studio.today(), section, 'harness', 'mashu/studio.py', title, '依据', '改法', '中', status,
                                 iso_at(decided) if decided else '', json.dumps(signal, ensure_ascii=False) if signal else ''))
        self.conn.commit()
        return cur.lastrowid

    def event(self, kind, ts, level='info', **detail):
        self.conn.executescript(studio.EVENTS_SCHEMA)
        self.conn.execute('INSERT INTO events(ts, kind, level, detail) VALUES(?,?,?,?)',
                          (ts, kind, level, json.dumps(detail, ensure_ascii=False)))
        self.conn.commit()

    def audit_at(self, ts, action, actor='executive', detail='网页按钮重启服务'):
        self.conn.execute('INSERT INTO audit(ts, actor, action, detail) VALUES(?,?,?,?)', (iso_at(ts), actor, action, detail))
        self.conn.commit()


class TestSignal(V2Base):
    def test_parse_shorthand_json_none_and_reject_writes(self):
        p = selfiter.parse_signal
        self.assertEqual(p('event:launch-fail | 降到 0'), dict(kind='event', query='launch-fail', expect='降到0'))
        self.assertEqual(p('audit:restart-server@executive|下降'), dict(kind='audit', query='restart-server@executive', expect='下降'))
        self.assertEqual(p('msg:打回了结单请求')['expect'], '下降')                     # 没写趋势默认「下降」
        self.assertEqual(p('{"kind": "event", "query": "error@tick", "expect": "不再出现"}')['expect'], '不再出现')
        self.assertEqual(p({'kind': 'sql', 'query': "SELECT ts FROM events WHERE kind='a' || 'b';"})['query'],
                         "SELECT ts FROM events WHERE kind='a' || 'b'")            # SQL 里的 || 不当成趋势分隔
        self.assertEqual(p('无')['kind'], 'none')
        self.assertIsNone(p(''))
        for bad in ('restart-server', 'foo:bar', 'sql:DELETE FROM events', 'sql:SELECT 1; DROP TABLE events', {'kind': 'event'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                p(bad)

    def test_sql_signal_runs_read_only(self):
        sig = selfiter.parse_signal("sql:WITH x AS (SELECT 1) SELECT ts FROM events")
        self.assertEqual(selfiter.signal_times(self.conn, sig), [])
        with self.assertRaises(ValueError):
            selfiter.signal_times(self.conn, selfiter.parse_signal('sql:SELECT ts FROM no_such_table'))

    def test_evaluate_before_after_and_verdicts(self):
        now = time.time()
        decided = now - 2 * DAY
        for k in range(6):                                       # 采纳前 3 天 6 次（2/天）
            self.event('launch-fail', decided - (k + 1) * 0.4 * DAY, level='warn')
        self.event('launch-fail', decided + 0.5 * DAY, level='warn')   # 采纳后 2 天 1 次（0.5/天）
        ev = selfiter.evaluate_signal(self.conn, selfiter.parse_signal('event:launch-fail/warn | 下降'), decided, now)
        self.assertEqual((ev['before'], ev['after'], ev['verdict']), (6, 1, '生效'))
        self.assertEqual((ev['rate_before'], ev['rate_after']), (2.0, 0.5))
        self.assertEqual(sum(a + b for a, b in ev['daily'].values()), 7)
        ev = selfiter.evaluate_signal(self.conn, selfiter.parse_signal('event:launch-fail | 降到0'), decided, now)
        self.assertEqual(ev['verdict'], '复发')                   # 期望降到 0，之后又出现 1 次
        ev = selfiter.evaluate_signal(self.conn, selfiter.parse_signal('event:launch-fail | 降到0'), now - 2 * 3600, now)
        self.assertEqual(ev['verdict'], '待观察')                 # 落实不到半天
        ev = selfiter.evaluate_signal(self.conn, selfiter.parse_signal('event:launch-fail/error | 降到0'), decided, now)
        self.assertEqual(ev['verdict'], '看不出')                 # 前后都没有
        for k in range(-3, 2):                                   # 每天 1 次，采纳前后一样多
            self.event('steady', decided + (k + 0.5) * DAY)
        ev = selfiter.evaluate_signal(self.conn, selfiter.parse_signal('event:steady | 下降'), decided, now)
        self.assertEqual((ev['before'], ev['after'], ev['verdict']), (3, 2, '未见效'))

    def test_effects_flag_recurrence_including_cover_and_auto_item_once(self):
        now = time.time()
        decided = now - 2 * DAY
        for t in (decided - 0.5 * DAY, decided - 1.5 * DAY, decided + 0.3 * DAY):
            self.audit_at(t, 'restart-server')
        ok_id = self.item('拦住验证码', status='采纳', decided=decided, signal=dict(kind='audit', query='cut', expect='降到0'))
        bad_id = self.item('用户反复点重启', status='已覆盖', decided=decided,
                           signal=dict(kind='audit', query='restart-server@executive', expect='降到0'))
        none_id = self.item('没写信号的', status='采纳', decided=decided)
        effs = selfiter.effects(self.conn, now)
        self.assertEqual([e['id'] for e in effs][0], bad_id)       # 复发排最前
        by = {e['id']: e for e in effs}
        self.assertEqual(by[bad_id]['verdict'], '复发')
        self.assertEqual(by[ok_id]['verdict'], '看不出')
        self.assertEqual(by[none_id]['verdict'], '无信号')
        text = selfiter.sec_effects(self.conn, effs)
        self.assertIn('## 上次采纳的效果', text)
        self.assertIn(f'#{bad_id} [已覆盖]', text)
        self.assertIn('当时判「已覆盖」', text)
        self.assertIn(f'没写信号、看不出效果：#{none_id}', text)
        rid = selfiter.claim_run(self.conn, 'manual')
        run = selfiter.run_row(self.conn, rid)
        made = selfiter.recurrence_items(self.conn, run, effs)
        self.assertEqual(len(made), 1)
        x = studio.one(self.conn, 'SELECT * FROM self_iterate_items WHERE id=?', made[0])
        self.assertTrue(x['title'].startswith(f'复发：#{bad_id} '))
        self.assertEqual((x['priority'], x['source'], x['status']), ('高', '验效', '待定'))
        self.assertEqual(selfiter.recurrence_items(self.conn, run, effs), [])     # 只补一次

    def test_adopt_requires_signal_and_signal_action(self):
        boss = self.assistant_cam()
        iid = self.item('待定的建议', status='待定', decided=0)
        with self.assertRaises(SystemExit) as cm:
            self.cli(boss, 'self-iterate', 'adopt', str(iid))
        self.assertIn('--signal', str(cm.exception))
        with self.assertRaises(SystemExit) as cm:
            self.cli(boss, 'self-iterate', 'adopt', str(iid), '--signal', 'sql:SELECT ts FROM nope')
        self.assertIn('查不了', str(cm.exception))
        self.assertEqual(studio.one(self.conn, 'SELECT status FROM self_iterate_items WHERE id=?', iid)['status'], '待定')
        out = self.cli(boss, 'self-iterate', 'adopt', str(iid), '--signal', 'audit:restart-server@executive | 下降', '--note', '改了')
        self.assertIn('信号', out)
        x = studio.one(self.conn, 'SELECT * FROM self_iterate_items WHERE id=?', iid)
        self.assertEqual(x['status'], '采纳')
        self.assertEqual(json.loads(x['signal'])['query'], 'restart-server@executive')
        self.cli(boss, 'self-iterate', 'signal', str(iid), '--signal', 'event:launch-fail | 降到0')
        x = studio.one(self.conn, 'SELECT * FROM self_iterate_items WHERE id=?', iid)
        self.assertEqual((x['status'], json.loads(x['signal'])['kind']), ('采纳', 'event'))
        mem = self.item('记忆候选', status='待定', decided=0, section='memory')
        self.cli(boss, 'self-iterate', 'adopt', str(mem))                       # 记忆 / 工作流候选不要求信号
        cov = self.item('判已覆盖', status='待定', decided=0)
        self.cli(boss, 'self-iterate', 'cover', str(cov), '--signal', '无', '--note', 'BILLING_RE 覆盖')
        self.assertEqual(json.loads(studio.one(self.conn, 'SELECT signal FROM self_iterate_items WHERE id=?', cov)['signal'])['kind'],
                         'none')
        self.assertIn(f'#{iid}', self.cli(None, 'self-iterate', 'effects'))

    def test_report_signal_parsed_and_stored(self):
        self.seed()
        rep = dict(REPORT, suggestions=[dict(REPORT['suggestions'][0], signal='event:launch-fail | 降到0'),
                                        dict(REPORT['suggestions'][1], title='复发：#9 某问题', priority='低',
                                             signal={'kind': 'bogus'})])
        self.set_result(rep)
        self.cli(None, 'self-iterate', 'run')
        r = self.runs()[0]
        self.assertEqual(r['status'], 'ok', r['error'])
        items = studio.rows(self.conn, 'SELECT * FROM self_iterate_items WHERE run_id=? ORDER BY id', r['id'])
        self.assertEqual(json.loads(items[0]['signal'])['query'], 'launch-fail')
        self.assertEqual(items[1]['signal'], '')                                 # 不合法的信号丢掉，不报错
        self.assertEqual(items[1]['priority'], '高')                             # 「复发：」强制高优先级
        self.assertEqual(items[0]['source'], '日报')
        self.assertIn('effects', json.loads(r['report']))
        self.assertIn('生效信号：event:launch-fail | 降到0', (Path(r['archive']) / 'report.md').read_text())
        self.assertIn('signal', (studio.PROMPTS / 'self-iterate.md').read_text())


class TestIncident(V2Base):
    def scan(self, now=None):
        self.conn.execute('DELETE FROM meta WHERE k="self_iterate_incident_scan"')
        self.conn.commit()
        return selfiter.scan_incidents(self.conn, now)

    def incidents(self):
        selfiter.ensure_tables(self.conn)
        return studio.rows(self.conn, 'SELECT * FROM self_iterate_incidents ORDER BY id')

    def test_triggers_and_dedupe(self):
        now = time.time()
        for k in range(4):
            self.event('error', now - 600 + k, level='error', where='tick.launch', err='boom')
        self.event('error', now - 590, level='error', where='other', err='x')     # 打断连续
        self.event('error', now - 580, level='error', where='tick.launch', err='boom')
        self.assertEqual(self.scan(now), 0)                                      # 不连续，不算
        for k in range(5):
            self.event('error', now - 500 + k, level='error', where='tick.launch', err='boom')
        self.assertEqual(self.scan(now), 1)
        self.assertEqual(self.incidents()[0]['key'], 'error:tick.launch')
        self.assertEqual(self.scan(now), 0)                                      # 6 小时内同一触发只记一次
        for k in range(3):
            self.audit_at(now - 1200 + k * 60, 'restart-server')
        self.event('release-fail', now - 100, level='warn', code='check_failed', err='检查没过')
        self.audit_at(now - 50, 'release-rollback', actor='assistant', detail='b2 -> b1')
        self.assertEqual(self.scan(now), 3)
        keys = [x['key'] for x in self.incidents()]
        self.assertEqual(keys, ['error:tick.launch', 'release-fail', 'release-rollback', 'restart-clicks'])
        self.assertEqual(self.scan(now + 7 * 3600), 0)                           # 都已滑出 1 小时窗口
        self.assertTrue(selfiter.ts_of('2026-11-27T15:02:21-05:00'))

    def test_daily_cap(self):
        studio.CONFIG_OVERRIDES['self_iterate_incident_max_per_day'] = 1
        now = time.time()
        for k in range(3):
            self.audit_at(now - 300 + k, 'restart-server')
        self.event('release-fail', now - 100, level='warn', code='check_failed', err='x')
        self.scan(now)
        self.assertEqual(sorted(x['status'] for x in self.incidents()), ['capped', 'pending'])

    def test_decide_with_incident_word_logs_event(self):
        boss = self.assistant_cam()
        self.cli(boss, 'decide', '重启服务读到空库的事故：已回滚')
        self.cli(boss, 'decide', '普通决定')
        evs = studio.rows(self.conn, 'SELECT * FROM events WHERE kind="incident-declared"')
        self.assertEqual(len(evs), 1)
        self.scan()
        self.assertEqual([x['key'] for x in self.incidents()], ['decide'])

    def test_incident_run_flow(self):
        self.seed()
        now = time.time()
        for k in range(3):
            self.audit_at(now - 300 + k, 'restart-server')
        self.set_result(dict(summary='用户反复点重启，是子脑提示词让用户点的。', sessions=[],
                             suggestions=[dict(area='prompt', target='mashu/prompts/producer.md', title='别让用户点重启',
                                               evidence='审计 3 次', change='删掉那句', priority='高',
                                               signal='audit:restart-server@executive | 下降')]))
        studio.CONFIG_OVERRIDES['self_iterate_time'] = '23:59'                    # 日报不到点
        studio.meta_set(self.conn, 'self_iterate_since', time.time())
        t = selfiter.maybe_run(self.conn)
        self.assertIsNotNone(t)
        self.join_threads()
        inc = self.incidents()[0]
        self.assertEqual((inc['key'], inc['status']), ('restart-clicks', 'done'))
        run = selfiter.run_row(self.conn, inc['run_id'])
        self.assertEqual((run['trigger'], run['status']), ('incident', 'ok'))
        stdin = (self.si / 'stdin').read_text()
        self.assertIn('事故小复盘素材', stdin)
        self.assertIn('用户 1 小时内点了 3 次重启', stdin)
        items = studio.rows(self.conn, 'SELECT * FROM self_iterate_items WHERE run_id=?', run['id'])
        self.assertEqual([(x['source'], x['title']) for x in items], [('事故', '别让用户点重启')])
        rep = studio.rows(self.conn, 'SELECT * FROM messages WHERE target="assistant" AND kind="report" AND text LIKE "%事故小复盘%"')
        self.assertEqual(len(rep), 1)
        self.assertIn('别让用户点重启', rep[0]['text'])
        self.assertIn('事故复盘 1 次', selfiter.digest_line(self.conn))
        d = selfiter.api_data(self.conn, '')
        self.assertIsNone(d['run_id'])                                           # 事故复盘不顶替日报
        self.assertEqual(d['incidents'][0]['items'][0]['signal_text'], 'audit:restart-server@executive | 下降')
        self.assertIn('事故小复盘', selfiter.show_text(self.conn))
        self.assertTrue(selfiter.claim_run(self.conn, 'manual'))                 # 当天日报照样能跑

    def test_incident_failure_marks_failed(self):
        self.seed()
        selfiter.ensure_tables(self.conn)
        self.conn.execute('INSERT INTO self_iterate_incidents(key, kind, title, detail, ts) VALUES("x","error","报错","d",?)',
                          (time.time(),))
        self.conn.commit()
        self.setenv(FAKE_SI_ERROR='1')
        t = selfiter.run_pending_incident(self.conn)
        t.join(30)
        self.assertEqual(self.incidents()[0]['status'], 'failed')
        self.assertTrue(studio.rows(self.conn, 'SELECT * FROM messages WHERE text LIKE "事故小复盘%失败%"'))


class TestMaterialV2(V2Base):
    def test_assemble_priority_order_and_note(self):
        parts = [(0, '# 头', 100), (5, '## 低 会话明细\n' + 'x' * 3000, 5000), (1, '## 用户操作\n' + 'u' * 500, 1000),
                 (2, '## 事故\n' + 'e' * 2000, 3000), (6, '## 文件索引\n' + 'f' * 800, 1000)]
        text = selfiter.assemble(parts, 4000)
        self.assertLessEqual(len(text), 4000)
        self.assertIn('u' * 500, text)
        self.assertIn('e' * 2000, text)
        self.assertLess(text.index('## 低 会话明细'), text.index('## 用户操作'))    # 输出保持原顺序
        self.assertIn('截断「低 会话明细」', text)
        self.assertIn('省略「文件索引」', text)
        self.assertNotIn('f' * 800, text)

    def test_material_has_new_sections_and_docs_only_aggregates(self):
        f, key, cam = self.seed()
        text, _ = selfiter.collect_material(self.conn)
        for kw in ('## 用户操作', '## 上次采纳的效果', '## 用量异常', '## 事故复盘', '## docs 使用统计', '### 异常会话'):
            self.assertIn(kw, text)
        self.assertNotIn('无 docs 工具记录', text)                               # 不再逐会话列
        self.assertLess(text.index('## 用户操作'), text.index('## 1 会话'))

    def test_docs_anomaly_needs_code_edit(self):
        line = json.dumps
        tr = self.tmp / 't.jsonl'
        tr.write_text('\n'.join(line(dict(type='assistant', cwd=str(self.tmp), message=dict(content=[
            dict(type='tool_use', name='Edit', input=dict(file_path=str(self.tmp / 'a.py'))),
            dict(type='tool_use', name='Write', input=dict(file_path=str(self.tmp / 'docs' / 'x.md')))]))) for _ in range(2)))
        seen = selfiter.claude_docs_usage(tr, self.tmp)
        self.assertEqual(seen.get('code_edits'), 2)

    def test_budget_parse(self):
        pb = selfiter.parse_budget
        self.assertEqual(pb('- 执行者会话 token 目标 ≤ 30M，超了先交接')[:2], (30_000_000, True))
        self.assertEqual(pb('- token 预算约 20M，单会话超过 30M 就写 handoff')[:2], (20_000_000, False))
        self.assertEqual(pb('Token 上限：3000 万')[:2], (30_000_000, False))
        self.assertEqual(pb('token 预算 1.5亿')[:2], (150_000_000, False))
        self.assertIsNone(pb('没有预算这一说'))

    def test_usage_anomaly(self):
        f, key, cam = self.seed()
        (studio.seq_dir(f, key) / 'docs').mkdir(exist_ok=True)
        (studio.seq_dir(f, key) / 'docs' / 'plan.md').write_text('## 预算\n- 执行者会话 token 目标 ≤ 1M\n')
        now = time.time()
        for i in range(6):
            cur = self.conn.execute('INSERT INTO cameras(role, name, state, film_id, started_at, last_active, tokens_in, cache_read) '
                                    'VALUES("crew", ?, "ended", "builda", ?, ?, 1000, ?)', (f'n{i}', now - 100, now - 50, 0))
        self.conn.execute('UPDATE cameras SET cache_read=5000000 WHERE id=?', (cam['id'],))
        for d, t in ((3, 1e7), (2, 1e7), (1, 1e7)):
            self.conn.execute('INSERT INTO usage(day, film_id, tokens_in) VALUES(?,?,?)',
                              ((datetime.date.today() - datetime.timedelta(days=d)).isoformat(), 'builda', t))
        self.conn.execute('INSERT INTO usage(day, film_id, tokens_in) VALUES(?,?,?)', (studio.today(), 'builda', 5e7))
        self.conn.commit()
        text = selfiter.sec_usage_anomaly(self.conn, now - DAY)
        self.assertIn(f'#{cam["id"]}「{cam["name"]}」', text)
        self.assertIn('超过 5 倍的 1 个', text)
        self.assertIn('预算 100 万/会话', text)
        self.assertIn('超出', text)
        self.assertIn('5.0 倍', text)                                            # 今天 5000 万 vs 前 3 天均 1000 万

    def test_user_ops_counts_and_cues(self):
        f, key, cam = self.seed()
        now = time.time()
        for k in range(11):
            self.audit_at(now - 3600 + k * 60, 'restart-server')
        self.audit_at(now - 60, 'sign', detail='#1 kickoff：批准开工')
        studio.post(self.conn, 'producer', 'builda', 'reply', '做完了，请点右上角「重启服务」生效')
        rep = studio.create_report(self.conn, 'ask', cam, 'builda', key, '推不推？', summary='推送', options=['推', '不推'])
        rid = rep['id'] if isinstance(rep, dict) else rep
        self.conn.execute('UPDATE reports SET status="answered", answered_by="executive", answer="推", closed_at=?, meta=? WHERE id=?',
                          (now, json.dumps(dict(recommend='推')), rid))
        self.conn.commit()
        text = selfiter.sec_user_ops(self.conn, now - DAY, iso_at(now - DAY))
        self.assertIn('重启服务 11', text)
        self.assertIn('重启服务 11 次（全天计数）', text)
        self.assertIn('请点右上角「重启服务」', text)
        self.assertIn('点了推荐 1 张（100%）', text)
        self.assertIn('聊天 1 条', text)


class TestRestartAuto(unittest.TestCase):
    """网页按钮：落后已提交代码 → 发布并重启（检查不过不切换，记 release-fail）；不落后 → 只重启。"""

    def test_auto_release_or_same(self):
        from test_stable_runtime import StableRuntimeBase, _commit_all

        class T(StableRuntimeBase):
            def runTest(inner):
                restarts = []
                studio.release_runtime(inner.conn, actor='executive', restart=False)
                res = studio.restart_auto(inner.conn, actor='executive', reason='x', same=False, restart_fn=lambda: restarts.append(1))
                inner.assertEqual((res['mode'], res['ahead_before']), ('same', 0))
                (inner.repo / '.mashu' / 'check.sh').write_text('#!/usr/bin/env bash\necho boom >&2\nexit 1\n')
                bad = _commit_all(inner.repo, 'bad')
                with inner.assertRaises(studio.MashuError) as cm:
                    studio.restart_auto(inner.conn, actor='executive', restart_fn=lambda: restarts.append(2))
                inner.assertEqual(cm.exception.code, 'check_failed')
                inner.assertNotEqual(studio.runtime_info()['sha'], bad)            # 检查不过不切换
                ev = studio.rows(inner.conn, 'SELECT * FROM events WHERE kind="release-fail"')
                inner.assertEqual(json.loads(ev[0]['detail'])['code'], 'check_failed')
                (inner.repo / '.mashu' / 'check.sh').write_text('#!/usr/bin/env bash\necho ok\n')
                good = _commit_all(inner.repo, 'good')
                res = studio.restart_auto(inner.conn, actor='executive', restart_fn=lambda: restarts.append(3))
                inner.assertEqual((res['mode'], res['sha'], res['ahead_before']), ('release', good, 2))
                inner.assertEqual(restarts, [1, 3])
                inner.assertIn('restartServer({ auto: true })', (Path(studio.__file__).parent.parent / 'web' / 'app.js').read_text())

        r = unittest.TestResult()
        T().run(r)
        self.assertEqual(r.errors + r.failures, [], [x[1] for x in r.errors + r.failures])


if __name__ == '__main__':
    unittest.main()
