"""#15 左栏会话状态细化 + 点对点等待：单元测试。"""
import json
from pathlib import Path
import re
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'mashu'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio  # noqa: E402
from test_studio import Base  # noqa: E402


class StatusDetailTests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('demo')
        self.film, _ = studio.greenlight(self.conn, str(self.repo), 'demo', '演示项目')

    def _pitch_live(self, slug, title=None, idle=True):
        _, key, cam = studio.pitch(self.conn, 'demo', slug, f'实现 {slug}', title=title or slug)
        studio.tick(self.conn)
        c = self.cam(cam['id'])
        if idle:
            self.stop_turn(c, f'{slug} 第一步完成')
            c = self.cam(cam['id'])
        return key, c

    def _seq_of(self, data, key):
        for f in data['films']:
            if f['id'] == 'demo':
                for s in f['sequences']:
                    if s['key'] == key:
                        return s
        raise AssertionError(f'seq not found: {key}')

    def test_all_status_details_and_brain_details(self):
        studio.CONFIG_OVERRIDES['stages'] = 10
        studio.CONFIG_OVERRIDES['max_stages_per_film'] = 10
        fp = studio.film_file('demo')
        fcfg = json.loads(fp.read_text())
        fcfg['max_stages'] = 10
        fp.write_text(json.dumps(fcfg, ensure_ascii=False, indent=2))

        # 1) 待审批（开工审批）
        prod = studio.create_camera(self.conn, 'producer', 'demo', None, 'hi')
        studio.tick(self.conn)
        plan = self.tmp / 'plan.md'
        plan.write_text('# 方案\n做审批测试\n')
        self.cli(prod, 'new-feature', 'demo', 'kick', '--brief', '开工需求', '--title', '开工功能', '--plan', str(plan))
        kick_key = studio.one(self.conn, 'SELECT key FROM sequences WHERE slug="kick"')['key']

        # 2) 待回复（请示子脑）
        ask_key, ask_cam = self._pitch_live('askit', '请示功能')
        self.cli(ask_cam, 'ask', '这个接口字段叫什么？', '--options', 'A|B', '--recommend', 'A', '--why', '简洁')

        # 3) 运行中 + 4) 可能卡住 + 5) API 报错
        run_key, run_cam = self._pitch_live('runit', '运行功能', idle=False)
        stuck_key, stuck_cam = self._pitch_live('stuckit', '卡住功能', idle=False)
        self.conn.execute(
            'UPDATE cameras SET health="stuck", health_note="15 分钟没有任何进展，可能卡住了", health_at=? WHERE id=?',
            (time.time() - 900, stuck_cam['id']),
        )
        err_key, err_cam = self._pitch_live('errit', '报错功能', idle=False)
        self.conn.execute(
            'UPDATE cameras SET health="apierr", health_note="API 报错（529），已自动让它继续，第 1 次", health_at=? WHERE id=?',
            (time.time() - 180, err_cam['id']),
        )

        # 6) 阻塞
        blk_key, blk_cam = self._pitch_live('blkit', '阻塞功能')
        studio.end_camera(self.conn, blk_cam['id'], 'cut')
        d = studio.seq_state(self.film, blk_key)
        d['blocker'] = '等测试服恢复'
        studio.save_state(self.film, blk_key, d)

        # 7) 已完成（待重启服务 vs 普通下一镜）
        done_r_key, done_r_cam = self._pitch_live('doner', '完成待重启')
        studio.end_camera(self.conn, done_r_cam['id'], 'done')
        dr = studio.seq_state(self.film, done_r_key)
        dr['stages'][0]['status'] = '已完成'
        dr['handoff']['next'] = '请用户点网页右上角「重启服务」生效'
        studio.save_state(self.film, done_r_key, dr)

        done_n_key, done_n_cam = self._pitch_live('donen', '完成普通')
        studio.end_camera(self.conn, done_n_cam['id'], 'done')
        dn = studio.seq_state(self.film, done_n_key)
        dn['stages'][0]['status'] = '已完成'
        dn['handoff']['next'] = '后续观察线上指标'
        studio.save_state(self.film, done_n_key, dn)

        # 8) 空闲（带上次回复首句）
        idle_key, idle_cam = self._pitch_live('idleit', '空闲功能')
        now_t = time.time()
        self.conn.execute('UPDATE cameras SET last_active=?, last_stop=? WHERE id=?', (now_t - 420, now_t - 360, idle_cam['id']))

        # 9) 已停（带 ended_reason）与 10) 未开始
        stop_key, stop_cam = self._pitch_live('stopit', '已停功能')
        studio.end_camera(self.conn, stop_cam['id'], 'cut')
        raw_key = '99-never'
        studio.seq_dir(self.film, raw_key).mkdir(parents=True, exist_ok=True)
        studio.save_state(self.film, raw_key, studio.new_seq_state(raw_key, '未开始功能', 'brief'))
        self.conn.execute(
            'INSERT INTO sequences(film_id, key, num, slug, title, brief, branch, created_at) VALUES(?,?,?,?,?,?,?,?)',
            ('demo', raw_key, 99, 'never', '未开始功能', 'brief', '', studio.now_iso()),
        )
        self.conn.commit()

        data = studio.status_data(self.conn)
        # 检查旧字段保持不变，新字段存在
        self.assertIn('assistant', data)
        self.assertIn('assistant_detail', data)
        self.assertIn('films', data)
        film_view = data['films'][0]
        self.assertIn('detail', film_view)
        self.assertIn('待处理上报', film_view['detail'])  # 子脑手上有 ask_key 的上报

        s_kick = self._seq_of(data, kick_key)
        self.assertEqual(s_kick['status'], 'sign')
        self.assertIn('等用户批准开工 #', s_kick['detail'])
        self.assertIsNotNone(s_kick['detail_since'])

        s_ask = self._seq_of(data, ask_key)
        self.assertEqual(s_ask['status'], 'asking')
        self.assertIn('等子脑回复 #', s_ask['detail'])
        self.assertIn('这个接口字段叫什么', s_ask['detail'])

        s_run = self._seq_of(data, run_key)
        self.assertEqual(s_run['status'], 'shooting')
        self.assertTrue(s_run['detail'])
        self.assertIsNotNone(s_run['detail_since'])

        s_stuck = self._seq_of(data, stuck_key)
        self.assertEqual(s_stuck['status'], 'stuck')
        self.assertIn('可能卡住了', s_stuck['detail'])
        self.assertIsNotNone(s_stuck['detail_since'])

        s_err = self._seq_of(data, err_key)
        self.assertEqual(s_err['status'], 'apierr')
        self.assertIn('API 报错', s_err['detail'])

        s_blk = self._seq_of(data, blk_key)
        self.assertEqual(s_blk['status'], 'blocked')
        self.assertEqual(s_blk['detail'], '阻塞：等测试服恢复')

        s_dr = self._seq_of(data, done_r_key)
        self.assertEqual(s_dr['status'], 'done')
        self.assertEqual(s_dr['detail'], '已完成 · 待重启服务')

        s_dn = self._seq_of(data, done_n_key)
        self.assertEqual(s_dn['status'], 'done')
        self.assertEqual(s_dn['detail'], '已完成 · 后续观察线上指标')

        s_idle = self._seq_of(data, idle_key)
        self.assertEqual(s_idle['status'], 'idle')
        self.assertIn('空闲 · 上次：idleit 第一步完成', s_idle['detail'])
        self.assertIsNotNone(s_idle['detail_since'])

        s_stop = self._seq_of(data, stop_key)
        self.assertEqual(s_stop['status'], 'stopped')
        self.assertEqual(s_stop['detail'], '已停：手动停止')

        s_never = self._seq_of(data, raw_key)
        self.assertEqual(s_never['status'], 'stopped')
        self.assertEqual(s_never['detail'], '未开始')

        # mashu status 文本带详情
        cli_out = self.cli(None, 'status', 'demo')
        self.assertIn('等用户批准开工 #', cli_out)
        self.assertIn('等子脑回复 #', cli_out)
        self.assertIn('已完成 · 待重启服务', cli_out)
        self.assertIn('阻塞：等测试服恢复', cli_out)
        self.assertIn('空闲 6 分钟 · 上次：idleit 第一步完成', cli_out)

    def test_queue_reasons_three_kinds_and_clear_on_launch(self):
        # 1) 项目不限并发（用户 2026-11-27）；2) 账号满（账号上限 stages=2）
        studio.CONFIG_OVERRIDES.update(stages=2, read_slots=1)
        self._pitch_live('q1', '先跑1', idle=False)
        self._pitch_live('q2', '先跑2', idle=False)
        _, k3, c3 = studio.pitch(self.conn, 'demo', 'q3', '等账号', title='账号满')
        studio.launch_queue(self.conn)
        data = studio.status_data(self.conn)
        s3 = self._seq_of(data, k3)
        self.assertEqual(s3['status'], 'queued')
        self.assertIn('已满 2/2', s3['detail'])

        # 3) 只读名额满（standard 工作流阶段 1 writes=False，read_slots=1）
        _, kr1, cr1 = studio.pitch(self.conn, 'demo', 'r1', '只读1', title='只读1', playbook='standard')
        _, kr2, cr2 = studio.pitch(self.conn, 'demo', 'r2', '只读2', title='只读2', playbook='standard')
        studio.launch_queue(self.conn)
        self.assertEqual(self.cam(cr1['id'])['state'], 'running')
        self.assertEqual(self.cam(cr2['id'])['state'], 'queued')
        data = studio.status_data(self.conn)
        sr2 = self._seq_of(data, kr2)
        self.assertEqual(sr2['status'], 'queued')
        self.assertIn('排队：只读名额已满 1/1', sr2['detail'])

        # 放宽限制后启动，qreason 自动清除
        studio.CONFIG_OVERRIDES.update(stages=5, read_slots=5)
        studio.launch_queue(self.conn)
        self.assertEqual(self.cam(c3['id'])['state'], 'running')
        self.assertIsNone(studio.meta_get(self.conn, f'qreason:{c3["id"]}'))

    def test_bg_tasks_list_and_detail(self):
        key, cam = self._pitch_live('bgit', '后台功能')
        sid = cam['claude_sid']
        cwd = studio.camera_cwd(self.conn, cam)
        proj_dir = studio.CLAUDE_PROJECTS / re.sub(r'[^A-Za-z0-9]', '-', str(cwd))
        proj_dir.mkdir(parents=True, exist_ok=True)
        tfile = proj_dir / f'{sid}.jsonl'
        self.addCleanup(lambda: tfile.unlink(missing_ok=True))
        studio._BG_CACHE.clear()

        ts1 = '2026-11-27T12:00:00Z'
        ts2 = '2026-11-27T12:02:00Z'
        lines = [
            {'timestamp': ts1, 'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'tu_1', 'name': 'Bash',
                 'input': {'command': 'npm test', 'description': 'npm test', 'run_in_background': True}},
            ]}},
            {'timestamp': ts2, 'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'tu_2', 'name': 'Bash',
                 'input': {'command': 'pytest -q', 'description': 'pytest -q', 'run_in_background': True}},
            ]}},
        ]
        tfile.write_text('\n'.join(json.dumps(x) for x in lines) + '\n')

        # 数量接口保持兼容
        self.assertEqual(studio.bg_tasks_of(self.conn, cam), 2)
        items = studio.bg_tasks_of(self.conn, cam, detail=True)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['desc'], 'npm test')
        self.assertIsNotNone(items[0]['start'])

        data = studio.status_data(self.conn)
        s = self._seq_of(data, key)
        self.assertEqual(s['status'], 'waiting')
        self.assertEqual(s['crew']['bg'], 2)
        self.assertEqual(len(s['crew']['bg_tasks']), 2)
        self.assertEqual(s['detail'], '后台：npm test 等 2 个')

        # 完成其中一个，剩余 1 个
        lines.append({'timestamp': '2026-11-27T12:03:00Z', 'type': 'user', 'message': {
            'content': '<task-notification><tool-use-id>tu_2</tool-use-id><status>completed</status></task-notification>',
        }})
        tfile.write_text('\n'.join(json.dumps(x) for x in lines) + '\n')
        studio._BG_CACHE.clear()
        data = studio.status_data(self.conn)
        s = self._seq_of(data, key)
        self.assertEqual(s['detail'], '后台：npm test')

    def test_peer_wait_lifecycle(self):
        studio.CONFIG_OVERRIDES.update(stages=5, max_stages_per_film=5, peer_wait_minutes=30)
        ka, ca = self._pitch_live('pa', '功能A', idle=True)
        kb, cb = self._pitch_live('pb', '功能B', idle=True)
        na, nb = studio.seq_num(ka), studio.seq_num(kb)

        # 1) --fyi 只通知，不记等待
        out_fyi = self.cli(ca, 'peer', kb, '顺便知会一声', '--fyi')
        self.assertNotIn('等待对方回复', out_fyi)
        self.assertEqual(studio.active_peer_waits(self.conn, 'demo'), [])

        # 2) 默认 peer 记等待；A 空闲 → peer 大类；B 详情加「#A 在等它回复」；协作上下文带提示
        out = self.cli(ca, 'peer', kb, '请确认接口字段')
        self.assertIn('已记为等待对方回复', out)
        data = studio.status_data(self.conn)
        sa, sb = self._seq_of(data, ka), self._seq_of(data, kb)
        self.assertEqual(sa['status'], 'peer')
        self.assertEqual(sa['status_label'], '等会话回复')
        self.assertEqual(sa['detail'], f'等 #{nb} 回复')
        self.assertEqual(len(sa['waits']), 1)
        self.assertEqual(sa['waits'][0]['to'], kb)
        self.assertEqual(len(sb['waited_by']), 1)
        self.assertEqual(sb['waited_by'][0]['from'], f'#{na}')
        self.assertIn(f'#{na} 在等它回复', sb['detail'])

        # 协作上下文注入
        ctx_a = studio.collaboration_context(self.conn, self.cam(ca['id']))
        ctx_b = studio.collaboration_context(self.conn, self.cam(cb['id']))
        self.assertIn(f'你在等 #{nb} 回复', ctx_a)
        self.assertIn(f'#{na} 在等你回复', ctx_b)

        # 3) A 在忙时：大类保持 shooting，详情末尾加「· 在等 #B」
        self.conn.execute('UPDATE cameras SET state="running", last_active=?, last_stop=0 WHERE id=?',
                          (time.time(), ca['id']))
        self.conn.commit()
        data = studio.status_data(self.conn)
        sa = self._seq_of(data, ka)
        self.assertEqual(sa['status'], 'shooting')
        self.assertIn(f'在等 #{nb}', sa['detail'])
        # 恢复 A 空闲
        self.stop_turn(self.cam(ca['id']), '继续等')

        # 4) B 给 A 发 peer 回复（带 --fyi），自动清除 A 的等待
        self.cli(cb, 'peer', ka, '已确认，用 string', '--fyi')
        self.assertEqual(studio.active_peer_waits(self.conn, 'demo'), [])
        data = studio.status_data(self.conn)
        self.assertEqual(self._seq_of(data, ka)['status'], 'idle')

        # 5) 会话结束自动清除等待
        self.cli(ca, 'peer', kb, '再问一个问题')
        self.assertEqual(len(studio.active_peer_waits(self.conn, 'demo')), 1)
        studio.end_camera(self.conn, ca['id'], 'cut')
        self.assertEqual(studio.active_peer_waits(self.conn, 'demo'), [])

        # 6) 超时：第 1 拍显示「已超时」，下一拍消失
        kc, cc = self._pitch_live('pc', '功能C', idle=True)
        self.cli(cc, 'peer', kb, '等回复超时测试')
        self.conn.execute('UPDATE peer_waits SET since=? WHERE from_camera=?', (time.time() - 35 * 60, cc['id']))
        self.conn.commit()
        studio.peer_wait_tick(self.conn)
        data = studio.status_data(self.conn)
        sc = self._seq_of(data, kc)
        self.assertEqual(sc['status'], 'peer')
        self.assertEqual(sc['detail'], f'等 #{nb} 回复已超时')
        # 下一拍清除
        studio.peer_wait_tick(self.conn)
        self.assertEqual(studio.active_peer_waits(self.conn, 'demo'), [])
        data = studio.status_data(self.conn)
        self.assertEqual(self._seq_of(data, kc)['status'], 'idle')

        # 7) 子脑 note 不算 peer
        prod = studio.create_camera(self.conn, 'producer', 'demo', None, 'hi')
        studio.tick(self.conn)
        self.cli(prod, 'note', f'demo/{nb}', '子脑普通补充指令')
        self.assertEqual(studio.active_peer_waits(self.conn, 'demo'), [])


if __name__ == '__main__':
    unittest.main()
