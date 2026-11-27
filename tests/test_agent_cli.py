"""#16 agent-cli 测试：统一通信面、收件箱自动分拣、输出协议与结构化报错。"""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'mashu'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio  # noqa: E402
from test_studio import Base, wait_for  # noqa: E402

BIN_MASHU = Path(__file__).resolve().parent.parent / 'bin' / 'mashu'


class AgentCliStage1Tests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('builda')
        self.film, _ = studio.greenlight(self.conn, self.repo, 'builda', 'Builda')

    def tick(self, n=1):
        for _ in range(n):
            studio.tick(self.conn)

    def pitch(self, slug):
        f, key, cam = studio.pitch(self.conn, 'builda', slug, f'需求 {slug}', playbook='direct')
        return key, cam
    def cli_json(self, cam, *argv):
        out = self.cli(cam, *argv, '--json')
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f'期望单行 JSON，实际：{out!r}')
        return json.loads(lines[0])

    def cli_err(self, cam, *argv):
        with self.assertRaises(studio.CliExit) as ctx:
            self.cli(cam, *argv)
        return ctx.exception

    def test_ac1_send_up_and_reply_choice(self):
        """AC1：执行者 send up --type ask --options 走同一条路，子脑 reply --choice A 批复送回执行者。"""
        key, crew = self.pitch('agent-cli-ac1')
        self.tick()
        crew = self.cam(crew['id'])
        res = self.cli_json(crew, 'send', 'up', '选 A 还是 B？', '--type', 'ask',
                            '--options', '方案甲|方案乙', '--recommend', 'A', '--why', '改动小')
        self.assertTrue(res['ok'])
        self.assertEqual(res['api_version'], 1)
        self.assertEqual(res['msg_type'], 'ask')
        self.assertEqual(res['holder'], 'producer')
        rid = res['id']
        r = studio.report(self.conn, rid)
        self.assertEqual(r['kind'], 'ask')
        self.assertEqual(r['msg_type'], 'ask')
        self.assertEqual(r['to_addr'], 'up')
        self.assertEqual(json.loads(r['meta'])['recommend'], '方案甲')
        # 子脑自动上线并收到上报，用 --choice A + 补充文字批复
        self.tick(2)
        prod = studio.active_producer(self.conn, 'builda')
        self.assertIsNotNone(prod)
        rep = self.cli_json(prod, 'reply', str(rid), '--choice', 'A', '按甲推进')
        self.assertTrue(rep['ok'])
        self.assertEqual(rep['answer'], '方案甲')
        self.assertEqual(rep['extra'], '按甲推进')
        updated = studio.report(self.conn, rid)
        self.assertEqual(updated['status'], 'answered')
        self.assertEqual(updated['answer'], '方案甲')
        self.tick()
        clog = wait_for(lambda: f'批复 #{rid}' in self.logtext('crew', 'builda', key) and self.logtext('crew', 'builda', key))
        self.assertIn('方案甲', clog)
        self.assertIn('补充：按甲推进', clog)

    def test_ac2_supersede_explicit_and_auto(self):
        """AC2：--supersedes 和同功能新建同类请示卡自动结掉旧卡，从网页待处理消失。"""
        key, crew = self.pitch('agent-cli-ac2')
        self.tick()
        crew = self.cam(crew['id'])
        r1 = self.cli_json(crew, 'send', 'up', '第一版问题', '--type', 'ask', '--options', 'A|B')
        id1 = r1['id']
        # 显式 --supersedes
        r2 = self.cli_json(crew, 'send', 'up', '第二版问题', '--type', 'ask', '--options', 'A|B', '--supersedes', str(id1))
        id2 = r2['id']
        self.assertIn(id1, r2['superseded'])
        old1 = studio.report(self.conn, id1)
        self.assertEqual(old1['status'], 'superseded')
        self.assertIn(f'#{id2}', old1['stale_reason'])
        # 不在 open_reports 和网页 inbox 中
        self.assertNotIn(id1, [x['id'] for x in studio.open_reports(self.conn)])
        # 同功能新建同类卡（--urgent 绕过 30min 合并限制）自动取代 id2
        r3 = self.cli_json(crew, 'send', 'up', '第三版紧急问题', '--type', 'ask', '--urgent')
        id3 = r3['id']
        self.assertIn(id2, r3['superseded'])
        old2 = studio.report(self.conn, id2)
        self.assertEqual(old2['status'], 'superseded')
        self.assertIn(f'#{id3}', old2['stale_reason'])

    def test_ac3_stale_triage_long_pending_and_hook_summary(self):
        """AC3：用户卡挂久回到子脑 inbox 标挂久了；归档后标已过时；fyi 看过自动结案；hook_prompt 一行摘要。"""
        studio.CONFIG_OVERRIDES.update(user_ask_remind_minutes=1)
        key, crew = self.pitch('agent-cli-ac3')
        self.tick()
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        # 1) 子脑发给用户的 ask，把 holder_since 拨到 3 小时前
        r = studio.create_report(self.conn, 'ask', prod, 'builda', key, '请选个方向', options=['一', '二'],
                                 holder='executive', msg_type='ask', to_addr='user')
        self.conn.execute('UPDATE reports SET holder_since=?, created_at=? WHERE id=?',
                          (time.time() - 7200, time.time() - 7200, r['id']))
        self.conn.commit()
        res = studio.triage_reports(self.conn, notify_remind=True)
        self.assertEqual(res['reminded'], 1)
        # 再次运行不会重复提醒
        self.assertEqual(studio.triage_reports(self.conn, notify_remind=True)['reminded'], 0)
        # 子脑 inbox 看到这张挂久了的卡
        ib = self.cli_json(prod, 'inbox')
        hit = next((x for x in ib['items'] if x['id'] == r['id']), None)
        self.assertIsNotNone(hit)
        self.assertTrue(hit['reminded'])
        # hook_prompt 注入一行摘要（不再贴全文）
        h_out = self.hook('hook-prompt', prod, {'session_id': 's1', 'prompt': '看看'})
        self.assertIn('收件箱', h_out)
        self.assertIn('用户挂久待复核', h_out)
        self.assertNotIn('请选个方向', h_out)
        # 2) 功能归档后，triage 标「已过时」
        studio.web_seq_action(self.conn, self.film, key, 'archive', {})
        ib2 = self.cli_json(prod, 'inbox')
        hit2 = next((x for x in ib2['items'] if x['id'] == r['id']), None)
        self.assertIn('已归档', hit2['stale_reason'])
        self.assertIn('mashu close', hit2['suggested_action'])
        # 3) 通报类（fyi）被收件人 inbox 看过后自动结案
        t = studio.create_report(self.conn, 'tell', crew, 'builda', key, '进度同步一下', holder='producer', msg_type='fyi')
        ib3 = self.cli_json(prod, 'inbox')
        self.assertIn(t['id'], ib3['auto_closed'])
        self.assertEqual(studio.report(self.conn, t['id'])['status'], 'closed')

    def test_ac4_routing_me_peer_and_close_confirm(self):
        """AC4：执行者 send user 返回 route_denied + fix；me / send #N 复用 peer_waits / close 用户卡确认信封。"""
        k1, c1 = self.pitch('feat-one')
        k2, c2 = self.pitch('feat-two')
        self.tick()
        c1, c2 = self.cam(c1['id']), self.cam(c2['id'])
        # 执行者 send user 被拦截
        exc = self.cli_err(c1, 'send', 'user', '越级问用户', '--type', 'ask')
        self.assertEqual(exc.exit_code, studio.EXIT_RULE)
        self.assertEqual(exc.payload['code'], 'route_denied')
        self.assertIn('send up', exc.payload['fix'])
        # me 返回角色与可达地址
        me = self.cli_json(c1, 'me')
        self.assertEqual(me['role'], 'crew')
        self.assertEqual(me['film']['id'], 'builda')
        self.assertEqual(me['feature']['key'], k1)
        self.assertTrue(any('up' in x for x in me['can_send']))
        # send #2 走 #15 的 peer_waits 逻辑
        p_res = self.cli_json(c1, 'send', f'#{studio.seq_num(k2)}', '接口改了吗', '--type', 'ask')
        self.assertTrue(p_res['sent'])
        self.assertTrue(p_res['waiting'])
        self.assertEqual(len(studio.active_peer_waits(self.conn, 'builda')), 1)
        # close 用户手上的卡：不带 --confirm 退出码 4，带 --confirm 结案
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        u_card = studio.create_report(self.conn, 'ask', prod, 'builda', k1, '挂在用户手上的卡', holder='executive', msg_type='ask')
        exc_cfm = self.cli_err(prod, 'close', str(u_card['id']), '--why', '过时：不再需要')
        self.assertEqual(exc_cfm.exit_code, studio.EXIT_CONFIRM)
        self.assertTrue(exc_cfm.payload['confirm_required'])
        self.assertIn('--confirm', exc_cfm.payload['confirm_cmd'])
        closed = self.cli_json(prod, 'close', str(u_card['id']), '--why', '过时：不再需要', '--silent', '--confirm')
        self.assertEqual(closed['status'], 'muted')
        self.assertEqual(studio.report(self.conn, u_card['id'])['status'], 'muted')

    def test_send_dedupe_and_stdin(self):
        """重试安全 --key 去重，正文 '-' 从 stdin 读取（含 $ 不展开）。"""
        key, crew = self.pitch('dedupe-stdin')
        self.tick()
        crew = self.cam(crew['id'])
        old_in = sys.stdin
        sys.stdin = io.StringIO('价格是 $100 且不展开 $HOME')
        try:
            r1 = self.cli_json(crew, 'send', 'up', '-', '--type', 'fyi', '--key', 'sync-v1')
        finally:
            sys.stdin = old_in
        self.assertFalse(r1['deduped'])
        self.assertIn('$100', studio.report(self.conn, r1['id'])['text'])
        r2 = self.cli_json(crew, 'send', 'up', '重复发送', '--type', 'fyi', '--key', 'sync-v1')
        self.assertTrue(r2['deduped'])
        self.assertEqual(r2['id'], r1['id'])


if __name__ == '__main__':
    unittest.main()
