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


class AgentCliStage2Tests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('builda')
        self.film, _ = studio.greenlight(self.conn, self.repo, 'builda', 'Builda')

    def run_bin(self, cam, *argv, stdin_text=None):
        env = dict(os.environ, MASHU_HOME=str(studio.HOME), MASHU_MEMORY=str(studio.MEMORY),
                   MASHU_TMUX_SOCKET=self.socket, MASHU_TMUX='t')
        if cam:
            env['MASHU_SESSION'] = str(cam['id'])
        else:
            env.pop('MASHU_SESSION', None)
        return subprocess.run([str(BIN_MASHU), *argv], input=stdin_text, capture_output=True, text=True, env=env)

    def test_ac6_schema_help_and_10_commands_structured_errors(self):
        """AC6：mashu schema --json 列出全部命令；抽查 ≥10 个命令报错都带 code、error、fix 和准确退出码。"""
        r = self.run_bin(None, 'schema', '--json')
        self.assertEqual(r.returncode, 0)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertTrue(data['ok'])
        self.assertEqual(data['api_version'], 1)
        names = {c['name'] for c in data['commands']}
        for must in ('me', 'inbox', 'send', 'reply', 'forward', 'close', 'schema', 'help', 'status', 'stop', 'new-feature'):
            self.assertIn(must, names)
        self.assertEqual(set(data['exit_codes'].keys()), {'0', '1', '2', '3', '4', '5', '6'})

        # help 带真实例子
        rh = self.run_bin(None, 'help', 'send')
        self.assertEqual(rh.returncode, 0)
        self.assertIn('示例：', rh.stdout)
        self.assertIn('mashu send', rh.stdout)

        # 抽查 12 个不同命令报错，全部返回单行 JSON、带 code / error / fix 和正确退出码
        f, key, crew = studio.pitch(self.conn, 'builda', 'err-case', '测报错', playbook='direct')
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        cases = [
            (crew, ('send', 'user', '越级', '--json'), studio.EXIT_RULE, 'route_denied'),
            (crew, ('send', 'nope-proj', '找不到', '--json'), studio.EXIT_NOT_FOUND, 'not_found'),
            (prod, ('reply', '99999', 'ok', '--json'), studio.EXIT_NOT_FOUND, 'not_found'),
            (prod, ('forward', '99999', 'user', '意见', '--json'), studio.EXIT_NOT_FOUND, 'not_found'),
            (prod, ('close', '99999', '--why', '过时', '--json'), studio.EXIT_NOT_FOUND, 'not_found'),
            (None, ('note', '16', '缺项目前缀', '--json'), studio.EXIT_USAGE, 'invalid_ref'),
            (None, ('status', 'ghost-proj', '--json'), studio.EXIT_NOT_FOUND, 'not_found'),
            (prod, ('stop', f'builda/{studio.seq_num(key)}', '--json'), studio.EXIT_USAGE, 'missing_reason'),
            (None, ('ac', 'pass', 'AC-1', '--ref', f'builda/{studio.seq_num(key)}', '--json'), studio.EXIT_NOT_FOUND, 'not_found'),
            (None, ('handoff', '--json'), studio.EXIT_RULE, 'session_required'),
            (None, ('help', 'no-such-cmd', '--json'), studio.EXIT_NOT_FOUND, 'not_found'),
            (None, ('playbook', 'show', '--json'), studio.EXIT_USAGE, 'usage_error'),
        ]
        # 让 crew 处于运行态以便 stop 检查到活跃机位
        self.conn.execute('UPDATE cameras SET state="running", pane="%1" WHERE id=?', (crew['id'],))
        self.conn.commit()
        for cam, argv, want_rc, want_code in cases:
            res = self.run_bin(cam, *argv)
            self.assertEqual(res.returncode, want_rc, f'{argv} stdout={res.stdout!r} stderr={res.stderr!r}')
            out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
            self.assertEqual(len(out_lines), 1, f'{argv} 非单行 JSON：{res.stdout!r}')
            j = json.loads(out_lines[0])
            self.assertFalse(j['ok'])
            self.assertEqual(j['api_version'], 1)
            self.assertEqual(j['code'], want_code, f'{argv} got {j}')
            self.assertTrue(j['error'])
            self.assertTrue(j['fix'])

        # 非 --json 报错输出两行中文（错误： / 下一步：）
        r_cn = self.run_bin(None, 'note', '16', 'hi')
        self.assertEqual(r_cn.returncode, studio.EXIT_USAGE)
        self.assertIn('错误：', r_cn.stderr)
        self.assertIn('下一步：', r_cn.stderr)

    def test_ac7_confirmation_envelopes(self):
        """AC7：stop（有生效批准）、restart-server、close 用户卡、forward user 审批卡不带 --confirm 均返回退出码 4。"""
        f, key, crew = studio.pitch(self.conn, 'builda', 'cfm-case', '测确认信封', playbook='direct')
        self.conn.execute('UPDATE cameras SET state="running", pane="%1" WHERE id=?', (crew['id'],))
        self.conn.execute('INSERT INTO approvals(film_id, seq_key, gate, granted_at, expires_at, granted_by) VALUES(?,?,?,?,?,?)',
                          ('builda', key, 'prod', time.time(), time.time() + 3600, 'producer'))
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        asst = studio.create_camera(self.conn, 'assistant', None, None, 'hi')
        self.conn.commit()

        # 1) stop 有生效 prod 批准 → exit 4
        r_stop = self.run_bin(prod, 'stop', f'builda/{studio.seq_num(key)}', '--reason', '需要中止', '--json')
        self.assertEqual(r_stop.returncode, studio.EXIT_CONFIRM)
        j_stop = json.loads(r_stop.stdout)
        self.assertTrue(j_stop['confirm_required'])
        self.assertIn('--confirm', j_stop['confirm_cmd'])
        # 带 --confirm 放行
        r_stop2 = self.run_bin(prod, 'stop', f'builda/{studio.seq_num(key)}', '--reason', '需要中止', '--confirm', '--json')
        self.assertEqual(r_stop2.returncode, 0)
        self.assertTrue(json.loads(r_stop2.stdout)['stopped'])

        # 2) restart-server 不带 --confirm → exit 4
        r_rs = self.run_bin(asst, 'restart-server', '--reason', '新版本', '--json')
        self.assertEqual(r_rs.returncode, studio.EXIT_CONFIRM)
        self.assertIn('--confirm', json.loads(r_rs.stdout)['confirm_cmd'])

        # 3) forward user 审批类卡不带 --confirm → exit 4
        ap_card = studio.create_report(self.conn, 'gate', crew, 'builda', key, '删旧表', gate='irreversible',
                                       holder='producer', msg_type='approve')
        r_fwd = self.run_bin(prod, 'forward', str(ap_card['id']), 'user', '建议审批', '--json')
        self.assertEqual(r_fwd.returncode, studio.EXIT_CONFIRM)
        self.assertIn('--confirm', json.loads(r_fwd.stdout)['confirm_cmd'])
        r_fwd2 = self.run_bin(prod, 'forward', str(ap_card['id']), 'user', '建议审批', '--confirm', '--json')
        self.assertEqual(r_fwd2.returncode, 0)
        self.assertEqual(studio.report(self.conn, ap_card['id'])['holder'], 'executive')

        # 4) 全局 --json 在命令前后均可，且 status / preset / platforms 均返回单行 JSON
        for argv in (('--json', 'status'), ('status', '--json'), ('platforms', '--json'), ('scan', '--json')):
            ro = self.run_bin(None, *argv)
            self.assertEqual(ro.returncode, 0, f'{argv}: {ro.stderr}')
            j = json.loads(ro.stdout.strip())
            self.assertTrue(j['ok'])
            self.assertEqual(j['api_version'], 1)


class AgentCliStage3Tests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('builda')
        self.film, _ = studio.greenlight(self.conn, self.repo, 'builda', 'Builda')

    def test_ac8_prompts_trimmed_and_placeholders_preserved(self):
        """AC8：四份提示词模板总字数较改造前（9887 字）减少 ≥ 30%，且全部占位符完整保留。"""
        root = Path(__file__).resolve().parent.parent / 'mashu' / 'prompts'
        required_vars = {
            'crew.md': ['{{name}}', '{{label}}', '{{seq}}', '{{film}}', '{{set_dir}}', '{{seq_dir}}',
                        '{{entry}}', '{{docs}}', '{{manual}}', '{{kmap}}', '{{handoff}}'],
            'direct.md': [],
            'producer.md': ['{{title}}', '{{film}}', '{{film_path}}', '{{entry}}', '{{docs}}',
                            '{{manual}}', '{{kdir}}', '{{kmap}}', '{{memory}}', '{{handoff}}'],
            'assistant.md': ['{{home}}', '{{memory_index}}', '{{manual}}', '{{kmap}}', '{{handoff}}'],
        }
        total = 0
        for name, placeholders in required_vars.items():
            text = (root / name).read_text()
            total += len(text)
            for ph in placeholders:
                self.assertIn(ph, text, f'{name} 缺少占位符 {ph}')
        self.assertLessEqual(total, int(9887 * 0.7), f'总字数 {total} 未达到减少 ≥30%（≤{int(9887 * 0.7)}）')

    def test_ac9_server_report_view_and_state_additive_fields(self):
        """AC9：server.report_view 与 /api/state inbox 只增 msg_type、superseded_by、stale_reason。"""
        import server
        f, key, crew = studio.pitch(self.conn, 'builda', 'web-card', '测试网页卡片字段', playbook='direct')
        r1 = studio.create_report(self.conn, 'ask', crew, 'builda', key, '第一版请示', options=['A', 'B'],
                                  holder='executive', msg_type='ask')
        r2 = studio.create_report(self.conn, 'ask', crew, 'builda', key, '第二版请示', options=['A', 'B'],
                                  holder='executive', msg_type='ask', supersedes=r1['id'])
        v1 = server.report_view(self.conn, studio.report(self.conn, r1['id']))
        self.assertEqual(v1['msg_type'], 'ask')
        self.assertEqual(v1['superseded_by'], r2['id'])
        self.assertIn(f'#{r2["id"]}', v1['stale_reason'])
        # 归档后 r2 标已过时
        studio.web_seq_action(self.conn, self.film, key, 'archive', {})
        v2 = server.report_view(self.conn, studio.report(self.conn, r2['id']))
        self.assertEqual(v2['msg_type'], 'ask')
        self.assertIn('已归档', v2['stale_reason'])
        # 覆盖 meta 已经是字典时再调 report_superseded_by / evaluate_stale 不报错（原 /api/mail 500 回归）
        v1_again = dict(v1)
        self.assertIsInstance(v1_again['meta'], dict)
        self.assertEqual(studio.report_superseded_by(self.conn, v1_again), r2['id'])
        self.assertIn(f'#{r2["id"]}', studio.evaluate_stale(self.conn, v1_again))
        # 对已解析的 dict 再次经过 report_meta 兼容
        self.assertEqual(studio.report_meta(v1_again)['superseded_by'], r2['id'])

    def test_reopened_done_feature_card_not_marked_stale(self):
        """返工重开场景（如 #15 截图请示 #391）：功能 state 仍为已完成但有运行中执行者，或卡在完工后发出，不标过时。"""
        f, key, crew = studio.pitch(self.conn, 'builda', 'rework-card', '返工截图确认', playbook='direct')
        # 1) 阶段标为已完成，但执行者仍在运行（返工中）
        data = studio.seq_state(f, key)
        data['stages'][0]['status'] = '已完成'
        data['stages'][0]['updated_at'] = studio.now_iso()
        studio.save_state(f, key, data)
        self.conn.execute('UPDATE cameras SET state="running", pane="%1" WHERE id=?', (crew['id'],))
        self.conn.commit()
        r_active = studio.create_report(self.conn, 'ask', crew, 'builda', key, '界面改好了，请看截图',
                                        options=['确认', '还要改'], holder='executive', msg_type='ask')
        # 即使 DB 之前被误标过，triage_reports 也会清掉
        self.conn.execute('UPDATE reports SET stale_reason="所属功能已完成" WHERE id=?', (r_active['id'],))
        self.conn.commit()
        studio.triage_reports(self.conn, notify_remind=False)
        self.assertFalse(studio.report(self.conn, r_active['id'])['stale_reason'])

        # 2) 执行者退出后：如果是完工后发出的卡，仍不标过时
        studio.end_camera(self.conn, crew['id'], 'wrap', 'done')
        self.conn.execute('UPDATE reports SET created_at=? WHERE id=?', (time.time() + 60, r_active['id']))
        self.conn.commit()
        self.assertEqual(studio.evaluate_stale(self.conn, studio.report(self.conn, r_active['id'])), '')

        # 3) 只有无活跃执行者且卡在完工之前遗留的旧卡，才标「所属功能已完成」
        self.conn.execute('UPDATE reports SET created_at=? WHERE id=?', (time.time() - 3600, r_active['id']))
        self.conn.commit()
        self.assertEqual(studio.evaluate_stale(self.conn, studio.report(self.conn, r_active['id'])), '所属功能已完成')


if __name__ == '__main__':
    unittest.main()
