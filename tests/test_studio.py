"""麻薯电脑秘书测试：单元 + 假 claude 的 tmux 集成测试（独立 tmux 服务器，不碰用户的 tmux）。

  python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'mashu'))
import studio  # noqa: E402

FAKE_CLAUDE = """#!/usr/bin/env bash
# 记录每个会话的启动参数和收到的每行输入
log="$MASHU_TEST_LOG/${MASHU_ROLE}-${MASHU_FILM:-x}-${MASHU_SEQ:-x}"
printf '%s\\n' "START[$MASHU_NAME] PWD=$PWD SESSION=$MASHU_SESSION" >> "$log"
for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$log"; done
while IFS= read -r line; do printf 'IN[%s]:%s\\n' "$MASHU_NAME" "$line" >> "$log"; done
"""


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    return fn()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved_home = studio.HOME
        studio.HOME = self.tmp / 'home'
        self.addCleanup(setattr, studio, 'HOME', self.saved_home)
        self.saved_memory = studio.MEMORY
        studio.MEMORY = self.tmp / 'memory'
        self.addCleanup(setattr, studio, 'MEMORY', self.saved_memory)
        self.socket = f'mashu-test-{os.getpid()}-{id(self)}'
        self.log = self.tmp / 'log'
        self.log.mkdir()
        fake = self.tmp / 'fake-claude'
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        studio.CONFIG_OVERRIDES.clear()
        studio.CONFIG_OVERRIDES.update(tmux_socket=self.socket, tmux_session='t', claude=str(fake), notify=False, stages=2,
                                       producer_desks=2)
        self.addCleanup(studio.CONFIG_OVERRIDES.clear)
        self.addCleanup(lambda: subprocess.run(['tmux', '-L', self.socket, 'kill-server'], capture_output=True))
        os.environ['MASHU_TEST_LOG'] = str(self.log)
        self.saved_warm = studio.WARMUP_SECONDS
        studio.WARMUP_SECONDS = 0
        self.addCleanup(setattr, studio, 'WARMUP_SECONDS', self.saved_warm)
        self.conn = studio.db()

    def make_repo(self, name):
        d = self.tmp / 'ws' / name
        d.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(d)], check=True)
        (d / 'README.md').write_text('hi\n')
        subprocess.run(['git', '-C', str(d), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(d), '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'init'],
                       check=True, capture_output=True)
        return d

    def as_camera(self, cam):
        """在某个机位的身份下运行 CLI。"""
        old = os.environ.get('MASHU_SESSION')
        os.environ['MASHU_SESSION'] = str(cam['id'])
        self.addCleanup(lambda: os.environ.pop('MASHU_SESSION', None) if old is None else os.environ.__setitem__('MASHU_SESSION', old))
        return contextlib.ExitStack()

    def cli(self, cam, *argv):
        env_old = os.environ.get('MASHU_SESSION')
        if cam:
            os.environ['MASHU_SESSION'] = str(cam['id'])
        else:
            os.environ.pop('MASHU_SESSION', None)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                studio.main(list(argv))
        finally:
            if env_old is None:
                os.environ.pop('MASHU_SESSION', None)
            else:
                os.environ['MASHU_SESSION'] = env_old
        return buf.getvalue()

    def hook(self, name, cam, payload):
        os.environ['MASHU_SESSION'] = str(cam['id']) if cam else ''
        old_in = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                studio.main([name])
        finally:
            sys.stdin = old_in
            os.environ.pop('MASHU_SESSION', None)
        return buf.getvalue()

    def logtext(self, role, film='x', seq='x'):
        p = self.log / f'{role}-{film}-{seq}'
        return p.read_text() if p.exists() else ''

    def cam(self, cid):
        return studio.camera(self.conn, cid)

    def stop_turn(self, cam, text=''):
        """模拟一轮结束（Stop hook）。"""
        return self.hook('hook-stop', cam, {'session_id': cam['claude_sid'], 'last_assistant_message': text})


class TestUnits(Base):
    def test_risky(self):
        f = dict(path='/x/proj', safety=dict(protected_branches=['main', 'release/*'], extra_patterns=[]))
        self.assertEqual(studio.risky('Bash', {'command': 'git push origin main'}, f)[0], 'prod')
        self.assertEqual(studio.risky('Bash', {'command': 'git push origin release/1.2'}, f)[0], 'prod')
        self.assertIsNone(studio.risky('Bash', {'command': 'git push origin mainline-fix'}, f))
        self.assertIsNone(studio.risky('Bash', {'command': 'git push -u origin feat/x'}, f))
        self.assertEqual(studio.risky('Bash', {'command': 'git push --force origin feat/x'}, f)[0], 'irreversible')
        self.assertEqual(studio.risky('Bash', {'command': 'gh pr merge 12'}, f)[0], 'prod')
        self.assertEqual(studio.risky('Bash', {'command': 'rm -rf /x/proj'}, f)[0], 'irreversible')
        self.assertEqual(studio.risky('Bash', {'command': 'kubectl apply -n prod -f a.yaml'}, f)[0], 'prod')
        self.assertIsNone(studio.risky('Bash', {'command': 'ls -la'}, f))
        # 说明文字里的“部署 / 生产 / git push”不拦；真正执行的照拦
        self.assertIsNone(studio.risky('Bash', {'command': 'mashu memo myapp "测试服部署好了，准备生产发布，别 git push origin main"'}, f))
        self.assertIsNone(studio.risky('Bash', {'command': "grep -n 'kubectl apply -n prod' notes.md"}, f))
        self.assertIsNone(studio.risky('Bash', {'command': "python3 - <<'EOF'\nprint('deploy to production')\nEOF"}, f))
        self.assertEqual(studio.risky('Bash', {'command': 'bash -c "git push origin main"'}, f)[0], 'prod')
        self.assertEqual(studio.risky('Bash', {'command': 'echo "x" && git push origin main'}, f)[0], 'prod')
        self.assertEqual(studio.risky('Bash', {'command': 'ENV=prod ./deploy.sh'}, f)[0], 'prod')

    def test_usage_dedupe_and_context(self):
        entries = [
            {'type': 'user', 'message': {'content': 'hi'}},
            {'type': 'assistant', 'message': {'id': 'm1', 'model': 'x', 'usage': {'input_tokens': 10, 'output_tokens': 5,
             'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 100}, 'content': [{'type': 'text', 'text': 'a'}]}},
            {'type': 'assistant', 'message': {'id': 'm1', 'model': 'x', 'usage': {'input_tokens': 10, 'output_tokens': 5,
             'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 100}, 'content': [{'type': 'text', 'text': 'b'}]}},
            {'type': 'assistant', 'isSidechain': True, 'message': {'id': 'm9', 'usage': {'input_tokens': 999}}},
        ]
        tot, ctx, model = studio.usage_totals(entries)
        self.assertEqual(tot, dict(tokens_in=10, tokens_out=5, cache_read=1000, cache_write=100))
        self.assertAlmostEqual(ctx, 1110 / 200000)
        self.assertEqual(studio.last_turn_text(entries), 'a\n\nb')

    def test_hooks_noop_without_session(self):
        os.environ.pop('MASHU_SESSION', None)
        old_in = sys.stdin
        for h in ('hook-stop', 'hook-pretool', 'hook-prompt', 'hook-notify'):
            sys.stdin = io.StringIO(json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'git push origin main'}}))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                studio.main([h])
            self.assertEqual(buf.getvalue(), '', h)
        sys.stdin = old_in
        self.assertEqual(studio.one(self.conn, 'SELECT COUNT(*) n FROM messages')['n'], 0)

    def test_hooks_settings_only_via_flag(self):
        data = json.loads(studio.hooks_settings().read_text())
        cmd = data['hooks']['Stop'][0]['hooks'][0]['command']
        self.assertIn('[ -n "$MASHU_SESSION" ]', cmd)
        self.assertIn('|| true', cmd)

    def test_greenlight_any_folder(self):
        d = self.tmp / 'ws' / 'plain-folder'
        d.mkdir(parents=True)
        f, created = studio.greenlight(self.conn, d)
        self.assertTrue(created)
        self.assertEqual(f['path'], str(d.resolve()))
        self.assertTrue(f['producer_name'])
        f2, created2 = studio.greenlight(self.conn, d)
        self.assertFalse(created2)
        with self.assertRaises(ValueError):
            studio.greenlight(self.conn, studio.HOME)
        f3, _ = studio.greenlight(self.conn, studio.CODE)       # 自我开发可以立项，但停服务要签字
        self.assertEqual(studio.risky('Bash', {'command': './start.sh restart'}, f3)[0], 'irreversible')
        self.assertIsNone(studio.risky('Bash', {'command': 'python3 -m unittest discover -s tests'}, f3))


class TestStudio(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('builda')
        self.film, _ = studio.greenlight(self.conn, self.repo, 'builda', 'Builda')

    def tick(self, n=1):
        for _ in range(n):
            studio.tick(self.conn)

    def assistant(self):
        return wait_for(lambda: studio.active_assistant(self.conn))

    def pitch(self, slug):
        # 这些用例测的是分阶段工作流的机制，显式用标准模板
        f, key, cam = studio.pitch(self.conn, 'builda', slug, f'需求 {slug}', playbook='standard')
        return key, cam

    def test_pitch_defaults_to_direct(self):
        f, key, cam = studio.pitch(self.conn, 'builda', 'fix-typo', '改个错字')
        data = studio.seq_state(f, key)
        self.assertEqual([s['name'] for s in data['stages']], ['直接做'])
        self.assertFalse(studio.staged(data))
        self.assertTrue(studio.stage_writes(f, key, 1))
        prompt = studio.system_prompt(self.conn, self.cam(cam['id']))
        self.assertIn('直接做', prompt)
        self.assertNotIn('这功能的工作流', prompt)
        self.assertIn('直接做，不分阶段', studio.status_text(f, key))
        # 项目有专门工作流时，--workflow 才分阶段
        pb = studio.load_template('standard')
        pb.update(id='film:builda', name='图文工作流')
        studio.write_playbook(studio.film_docs(f) / 'playbook.json', pb)
        f2, key2, cam2 = studio.pitch(self.conn, 'builda', 'post', '一篇帖子', workflow=True)
        data2 = studio.seq_state(f2, key2)
        self.assertTrue(studio.staged(data2))
        self.assertEqual(data2['playbook']['name'], '图文工作流')
        self.assertIn('这功能的工作流', studio.system_prompt(self.conn, self.cam(cam2['id'])))

    def test_pitch_works_in_project_folder_and_no_branch(self):
        key, cam = self.pitch('export-csv')
        self.assertEqual(key, '1-export-csv')
        self.tick()
        cam = self.cam(cam['id'])
        self.assertEqual(cam['state'], 'running')
        log = wait_for(lambda: '继续 #1' in self.logtext('crew', 'builda', key) and self.logtext('crew', 'builda', key))
        self.assertIn(f'PWD={self.repo.resolve()}', log)
        self.assertIn('ARG:--add-dir', log)
        self.assertIn('ARG:--settings', log)
        self.assertIn('继续 #1 1-export-csv', log)
        branches = subprocess.run(['git', '-C', str(self.repo), 'branch'], capture_output=True, text=True).stdout
        self.assertEqual(branches.strip(), '* main')
        self.assertTrue((studio.seq_dir(self.film, key) / 'state.json').exists())
        # 一场戏只有一个主创
        again, created = studio.request_crew(self.conn, self.film, key, 'x')
        self.assertFalse(created)
        # 进组分隔线
        msgs = [m['text'] for m in studio.rows(self.conn, 'SELECT * FROM messages WHERE kind="handover"')]
        self.assertTrue(any('身份接手' in t for t in msgs))
        # 继续时注入场记单
        out = self.hook('hook-prompt', cam, {'prompt': '继续 #1', 'session_id': 's1'})
        self.assertIn('状态文件', out)
        self.assertIn('方案', out)
        self.assertIn('工作流：', out)
        self.assertIn('验收标准', out)

    def test_stage_limit_queues(self):
        studio.CONFIG_OVERRIDES['read_slots'] = 2      # 方案阶段只读，用只读名额
        self.film['max_stages'] = 5
        conf = json.loads(studio.film_file('builda').read_text())
        conf['max_stages'] = 5
        studio.film_file('builda').write_text(json.dumps(conf))
        cams = [self.pitch(s)[1] for s in ('a', 'b', 'c')]
        self.tick()
        states = [self.cam(c['id'])['state'] for c in cams]
        self.assertEqual(states.count('running'), 2)
        self.assertEqual(states[2], 'queued')
        studio.end_camera(self.conn, cams[0]['id'], 'cut')
        self.tick()
        self.assertEqual(self.cam(cams[2]['id'])['state'], 'running')

    def writing(self, key, claims=None):
        """把功能推到第 2 阶段（写代码），换一个执行者排队。"""
        cur = studio.active_crew(self.conn, 'builda', key)
        if cur:
            studio.end_camera(self.conn, cur['id'], 'cut')
        f = studio.film(self.conn, 'builda')
        data = studio.seq_state(f, key)
        data['stages'][0]['status'] = '已完成'
        data['stages'][1]['status'] = '进行中'
        if claims is not None:
            data['claims'] = {'touches': claims}
        studio.save_state(f, key, data)
        return studio.request_crew(self.conn, f, key, 'go')[0]

    def test_no_per_film_limit(self):
        """项目不限并发（用户 2026-11-27）：film.json 里旧的 max_stages=1 不再生效，只按账号限。"""
        studio.CONFIG_OVERRIDES['stages'] = 5
        conf = json.loads(studio.film_file('builda').read_text())
        conf['max_stages'] = 1
        studio.film_file('builda').write_text(json.dumps(conf))
        ka, kb = self.pitch('a')[0], self.pitch('b')[0]
        a, b = self.writing(ka, ['src/a/**']), self.writing(kb, ['src/b/**'])
        self.tick()
        self.assertEqual(self.cam(a['id'])['state'], 'running')
        self.assertEqual(self.cam(b['id'])['state'], 'running')

    def test_stage_limit_is_per_account(self):
        """并发槽按账号算：同一个号满了就排队，占着名额的换成别的号（Codex cx-9）后，Claude 这边空出来就能开。"""
        studio.CONFIG_OVERRIDES['stages'] = 1
        conf = json.loads(studio.film_file('builda').read_text())
        conf['max_stages'] = 5
        studio.film_file('builda').write_text(json.dumps(conf))
        ka, kb = self.pitch('a')[0], self.pitch('b')[0]
        a, b = self.writing(ka, ['src/a/**']), self.writing(kb, ['src/b/**'])
        self.tick()
        self.assertEqual(self.cam(a['id'])['state'], 'running')
        self.assertEqual(self.cam(b['id'])['state'], 'queued')
        self.assertEqual(studio.crew_by_account(self.conn), {'claude': 1})
        studio.meta_set(self.conn, f'camprov:{a["id"]}', 'codex')
        studio.meta_set(self.conn, f'camacct:{a["id"]}', 'cx-9')
        self.tick()
        self.assertEqual(self.cam(b['id'])['state'], 'running')
        self.assertEqual(studio.crew_by_account(self.conn), {'claude': 1, 'codex:cx-9': 1})
        st = studio.status_data(self.conn)['stages']
        self.assertEqual((st['used'], st['max']), (2, 1))
        self.assertEqual([x['label'] for x in st['by_account']], ['Claude', 'Codex cx-9'])

    def test_user_chat_answers_open_card(self):
        """用户不点卡片、直接在功能对话里回：话是某个选项就当点了它；否则唯一的普通请示收起；闸门卡不会被随口一句话批掉。"""
        key, crew = self.pitch('chat-answer')
        self.tick()
        crew = self.cam(crew['id'])
        ch = f'builda/{key}'
        mk = lambda **kw: studio.create_report(self.conn, 'ask', crew, 'builda', key, '选哪个？', holder='executive', **kw)
        r1 = mk(options=['异星荒漠+生物机械遗迹', '冷色废墟'])
        studio.user_chat(self.conn, ch, '异星荒漠 + 生物机械遗迹。', f'crew:builda/{key}')
        r1 = studio.report(self.conn, r1['id'])
        self.assertEqual((r1['status'], r1['answer']), ('answered', '异星荒漠+生物机械遗迹'))
        r2 = mk(options=['A', 'B'])
        studio.user_chat(self.conn, ch, '都不要，换个思路', f'crew:builda/{key}')
        r2 = studio.report(self.conn, r2['id'])
        self.assertEqual((r2['status'], r2['answer']), ('answered', '都不要，换个思路'))
        g = studio.create_report(self.conn, 'gate', crew, 'builda', key, '要推送', options=['批准', '不批准'], gate='irreversible', holder='executive')
        studio.user_chat(self.conn, ch, '等等再说', f'crew:builda/{key}')
        self.assertEqual(studio.report(self.conn, g['id'])['status'], 'open')
        studio.user_chat(self.conn, ch, '批准', f'crew:builda/{key}')
        self.assertEqual(studio.report(self.conn, g['id'])['status'], 'answered')

    def test_report_chain_up_to_executive_and_answer_back(self):
        key, crew = self.pitch('export-csv')
        self.tick()
        crew = self.cam(crew['id'])
        out = self.cli(crew, 'ask', '用 A 还是 B？', '--options', 'A|B')
        self.assertIn('交给子脑', out)
        rid = studio.one(self.conn, 'SELECT id FROM reports ORDER BY id DESC LIMIT 1')['id']
        # 制片人不在岗 → 统筹带着上报单叫制片人上班
        self.tick()
        prod = studio.active_producer(self.conn, 'builda')
        self.assertIsNotNone(prod)
        self.tick()
        plog = wait_for(lambda: f'上报 #{rid}' in self.logtext('producer', 'builda') and self.logtext('producer', 'builda'))
        self.assertIn(f'上报 #{rid}', plog)
        # 子脑直接交给用户：卡片出现在这个项目的对话里（不经主脑）
        prod = self.cam(prod['id'])
        self.cli(prod, 'pass', str(rid), '涉及接口，建议 A', '--recommend', 'A', '--why', '改动小')
        r = studio.report(self.conn, rid)
        self.assertEqual(r['holder'], 'executive')
        self.assertEqual(json.loads(r['meta'])['recommend'], 'A')
        card = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="card" AND report_id=?', rid)
        self.assertEqual(card['channel'], 'builda')
        # 子脑和主脑都不能再碰
        with self.assertRaises(SystemExit):
            self.cli(prod, 'answer', str(rid), 'A')
        # 出品人在网页回答 → 直接投回原主创，电影频道有抄送
        studio.answer_report(self.conn, rid, None, 'A')
        self.tick()
        clog = wait_for(lambda: f'批复 #{rid}' in self.logtext('crew', 'builda', key) and self.logtext('crew', 'builda', key))
        self.assertIn('用户] A', clog)
        cc = studio.rows(self.conn, 'SELECT * FROM messages WHERE channel="builda" AND kind="notice" AND text LIKE ?', f'#{rid}%')
        self.assertTrue(cc)

    def test_producer_answers_within_authority(self):
        key, crew = self.pitch('x')
        self.tick()
        self.cli(self.cam(crew['id']), 'ask', '测试放哪个目录？')
        rid = studio.one(self.conn, 'SELECT id FROM reports ORDER BY id DESC LIMIT 1')['id']
        self.tick(2)
        prod = studio.active_producer(self.conn, 'builda')
        self.cli(prod, 'answer', str(rid), '放 tests/ 下')
        r = studio.report(self.conn, rid)
        self.assertEqual(r['status'], 'answered')
        self.assertEqual(r['answered_by'], 'producer')
        self.assertIsNone(studio.one(self.conn, 'SELECT id FROM messages WHERE kind="card" AND report_id=?', rid))
        self.assertTrue(studio.one(self.conn, 'SELECT id FROM decisions WHERE actor="producer"'))

    def strict(self):
        """严格模式：所有闸门要用户审批，检查不过就打回（strict_checks）。"""
        studio.CONFIG_OVERRIDES.update(strict_checks=True, user_gates=list(studio.GATES) + ['stage-entry'])

    def test_gate_must_reach_executive(self):
        self.strict()
        key, crew = self.pitch('x')
        self.tick()
        self.cli(self.cam(crew['id']), 'ask', '推 main', '--gate', 'prod')
        rid = studio.one(self.conn, 'SELECT id FROM reports ORDER BY id DESC LIMIT 1')['id']
        self.tick(2)
        prod = studio.active_producer(self.conn, 'builda')
        with self.assertRaises(SystemExit):
            self.cli(prod, 'answer', str(rid), '签字')
        self.cli(prod, 'pass', str(rid), '建议签')
        a = self.assistant()
        with self.assertRaises(SystemExit):
            self.cli(a, 'answer', str(rid), '签字')

    def test_timeout_auto_pass(self):
        studio.CONFIG_OVERRIDES.update(report_timeout_minutes=0, urgent_timeout_minutes=0)
        key, crew = self.pitch('x')
        self.tick()
        self.cli(self.cam(crew['id']), 'ask', '问个事')
        rid = studio.one(self.conn, 'SELECT id FROM reports ORDER BY id DESC LIMIT 1')['id']
        self.cli(self.cam(crew['id']), 'ask', '删掉旧数据目录', '--gate', 'irreversible')
        rid2 = studio.one(self.conn, 'SELECT id FROM reports ORDER BY id DESC LIMIT 1')['id']
        time.sleep(0.05)
        self.tick()
        self.assertEqual(studio.report(self.conn, rid)['holder'], 'producer')     # 麻薯的事不会因为超时推给用户
        r = studio.report(self.conn, rid2)
        self.assertEqual(r['holder'], 'executive')                                # 只有用户才能批的事才会上交
        self.assertIn('未处理', r['route'])

    def test_mashu_leads_by_default(self):
        """默认麻薯主导：除不可逆操作外的闸门由子脑批；检查不过不打回，只交给子脑判断。"""
        key, crew = self.pitch('x')
        crew = self.writing(key, ['src/**'])
        self.tick()
        crew = self.cam(crew['id'])
        self.cli(crew, 'ask', '推 main', '--gate', 'prod')
        rid = studio.one(self.conn, 'SELECT id FROM reports ORDER BY id DESC LIMIT 1')['id']
        self.assertEqual(studio.report(self.conn, rid)['need'], 'producer')
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        self.cli(prod, 'answer', str(rid), '审批')
        self.assertEqual(self.hook('hook-pretool', crew, {'tool_name': 'Bash', 'tool_input': {'command': 'git push origin main'}}), '')
        r = studio.create_report(self.conn, 'gate', crew, 'builda', key, '删库', gate='irreversible')
        self.assertEqual(r['need'], 'executive')
        # 检查失败、改动没提交：照样完成，提醒交给子脑
        (studio.film_docs(self.film) / 'check.sh').write_text('echo "ERROR test_x failed"; exit 1\n')
        (self.repo / 'src').mkdir()
        (self.repo / 'src' / 'a.py').write_text('print(1)\n')
        self.cli(crew, 'feature', 'set', '2', '已完成')
        out = self.cli(crew, 'finish', '--reason', 'done')
        self.assertIn('提醒已交给子脑', out)
        self.assertEqual(studio.seq_state(self.film, key)['stages'][1]['status'], '已完成')
        tell = studio.one(self.conn, 'SELECT * FROM reports WHERE kind="tell" ORDER BY id DESC LIMIT 1')
        self.assertEqual(tell['holder'], 'producer')
        self.assertIn('自动检查没通过', tell['text'])
        self.assertIn('未提交', tell['text'])
        # 写别人范围里的文件：不拦，只提醒
        kb = self.pitch('b')[0]
        b = self.cam(self.writing(kb, ['docs/**'])['id'])
        out = self.hook('hook-pretool', b, {'tool_name': 'Write', 'tool_input': {'file_path': str(self.repo / 'src/x.py')}})
        self.assertEqual(out, '')

    def test_wrap_after_script_waits_for_approval_same_crew(self):
        key, crew = self.pitch('x')
        self.tick()
        crew = self.cam(crew['id'])
        # 编剧不能自己跳进拍摄
        with self.assertRaises(SystemExit):
            self.cli(crew, 'seq', 'set', '2', '进行中')
        self.cli(crew, 'seq', 'set', '1', '已完成')
        self.cli(crew, 'finish', '--reason', '阶段 1 完成')
        self.stop_turn(crew, '剧本写好了')
        self.tick()
        self.assertNotEqual(self.cam(crew['id'])['state'], 'ended')      # 阶段之间不换人：同一个执行者留着等批准
        conf = studio.one(self.conn, 'SELECT * FROM reports WHERE kind="confirm"')
        self.assertEqual(conf['confirm_stage'], 2)
        # 子脑就是麻薯：方案定稿（需要麻薯批准）子脑可以直接批 → 同一个执行者接着做
        prod = wait_for(lambda: studio.active_producer(self.conn, 'builda'))
        self.cli(prod, 'answer', str(conf['id']), '批准')
        nxt = studio.active_crew(self.conn, 'builda', key)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt['name'], crew['name'])
        self.cli(nxt, 'feature', 'set', '2', '进行中')   # 有批准就能进

    def test_stage_done_goes_to_digest(self):
        key, crew = self.pitch('x')
        self.tick()
        crew = self.cam(crew['id'])
        self.cli(crew, 'seq', 'set', '1', '已完成')
        self.tick()
        r = studio.one(self.conn, 'SELECT * FROM reports WHERE kind="stage-done"')
        self.assertEqual(r['holder'], 'digest')
        self.assertIsNone(studio.active_producer(self.conn, 'builda'))       # 例行通报不唤醒负责人
        ev = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="event"')    # 主对话里有完成提醒，只给网页看
        self.assertEqual((ev['channel'], ev['film_id'], ev['seq_key'], ev['delivered']), ('assistant', 'builda', key, 1))
        self.assertTrue(ev['text'].startswith('stage|方案完成'))
        studio.meta_set(self.conn, 'digest_last', time.time() - 3600)
        studio.CONFIG_OVERRIDES['digest_times'] = [time.strftime('%H:%M')]
        self.tick()
        dg = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="digest"')
        self.assertIn('简报', dg['text'])
        self.assertIn('方案完成', dg['text'])
        self.assertEqual(studio.report(self.conn, r['id'])['status'], 'closed')
        self.tick()
        self.assertEqual(studio.one(self.conn, 'SELECT COUNT(*) n FROM messages WHERE kind="digest"')['n'], 1)

    def test_pretool_gate_blocks_then_signed(self):
        key, crew = self.pitch('x')
        self.tick()
        crew = self.cam(crew['id'])
        payload = {'tool_name': 'Bash', 'tool_input': {'command': 'git push origin main'}}
        out = self.hook('hook-pretool', crew, payload)
        self.assertIn('"permissionDecision": "deny"', out)
        self.assertIn('mashu ask --gate prod', out)
        r = studio.create_report(self.conn, 'gate', crew, 'builda', key, '推 main', gate='prod')
        studio.CONFIG_OVERRIDES['report_timeout_minutes'] = 99
        studio.answer_report(self.conn, r['id'], None, '审批')
        self.assertEqual(self.hook('hook-pretool', crew, payload), '')
        # 审批绑定提交：代码变了就失效
        (self.repo / 'new.txt').write_text('x')
        subprocess.run(['git', '-C', str(self.repo), 'add', 'new.txt'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'more'], check=True)
        self.assertIn('代码变了', self.hook('hook-pretool', crew, payload))
        acts = [x['action'] for x in studio.rows(self.conn, 'SELECT action FROM audit')]
        self.assertIn('blocked', acts)
        self.assertIn('sign', acts)
        self.assertIn('blocked-stale', acts)
        self.assertIn('use-approval', acts)

    def test_stop_hook_reply_and_forward_to_superior(self):
        key, crew = self.pitch('x')
        self.tick()
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        self.tick()
        studio.post(self.conn, 'producer', f'builda/{key}', 'note', '先做 A', target=f'crew:builda/{key}', camera=self.cam(prod['id']))
        self.tick()
        crew = self.cam(crew['id'])
        self.assertEqual(crew['last_from'], 'producer')
        self.stop_turn(crew, '好的，A 做完了')
        reply = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="reply" AND channel=?', f'builda/{key}')
        self.assertEqual(reply['text'], '好的，A 做完了')
        fwd = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="forward"')
        self.assertEqual(fwd['target'], 'producer:builda')
        # （无）不转发
        n = studio.one(self.conn, 'SELECT COUNT(*) n FROM messages')['n']
        self.stop_turn(crew, '（无）')
        self.assertEqual(studio.one(self.conn, 'SELECT COUNT(*) n FROM messages')['n'], n)

    def test_memo_reply_goes_back_to_assistant(self):
        a = self.assistant() or (self.tick() or self.assistant())
        self.tick()
        a = self.assistant()
        self.cli(a, 'memo', 'builda', '本周优先导出功能')
        self.tick(2)
        prod = studio.active_producer(self.conn, 'builda')
        self.tick()
        prod = self.cam(prod['id'])
        plog = wait_for(lambda: '指示' in self.logtext('producer', 'builda') and self.logtext('producer', 'builda'))
        self.assertIn('本周优先导出功能', plog)
        self.stop_turn(prod, '收到，安排第 2 场')
        fwd = studio.one(self.conn, 'SELECT * FROM messages WHERE kind="forward" AND target="assistant"')
        self.assertIn('收到，安排第 2 场', fwd['text'])

    def test_exit_and_resume(self):
        key, crew = self.pitch('x')
        self.tick()
        crew = self.cam(crew['id'])
        self.stop_turn(crew, '做了一半')
        saved = studio.CLAUDE_PROJECTS
        studio.CLAUDE_PROJECTS = self.tmp / 'projects'
        self.addCleanup(setattr, studio, 'CLAUDE_PROJECTS', saved)
        tdir = studio.CLAUDE_PROJECTS / __import__('re').sub(r'[^A-Za-z0-9]', '-', str(self.repo.resolve()))
        tdir.mkdir(parents=True)
        (tdir / f'{crew["claude_sid"]}.jsonl').write_text('{}\n')
        studio.tmux('kill-pane', '-t', crew['pane'])
        self.tick()
        self.assertEqual(self.cam(crew['id'])['ended_reason'], 'exited')
        studio.post(self.conn, 'executive', f'builda/{key}', 'chat', '继续做', target=f'crew:builda/{key}')
        self.tick()
        new = studio.active_crew(self.conn, 'builda', key)
        self.assertEqual(new['resume_sid'], crew['claude_sid'])

    def test_turn_progress_text_and_pending_tool(self):
        entries = [
            {'type': 'user', 'message': {'content': '旧问题'}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '旧回答'}]}},
            {'type': 'user', 'message': {'content': '新问题'}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '我看一下'}]}},
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash',
                                                           'input': {'command': 'mashu status\nmore'}}]}},
        ]
        self.assertEqual(studio.turn_progress(entries), ('我看一下', 'Bash · mashu status'))
        entries.append({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}]}})
        self.assertEqual(studio.turn_progress(entries), ('我看一下', ''))

    def test_live_replies_waiting_then_streaming(self):
        studio.post(self.conn, 'executive', 'assistant', 'chat', '在吗', target='assistant')
        self.assertTrue(studio.live_replies(self.conn)['assistant']['waiting'])
        self.tick()
        self.tick()     # 第一轮拉起麻薯，第二轮投递
        a = studio.active_assistant(self.conn)
        self.assertEqual(a['state'], 'running')
        saved = studio.CLAUDE_PROJECTS
        studio.CLAUDE_PROJECTS = self.tmp / 'projects'
        self.addCleanup(setattr, studio, 'CLAUDE_PROJECTS', saved)
        tdir = studio.CLAUDE_PROJECTS / __import__('re').sub(r'[^A-Za-z0-9]', '-', str(studio.camera_cwd(self.conn, a)))
        tdir.mkdir(parents=True)
        tp = tdir / f'{a["claude_sid"]}.jsonl'
        tp.write_text(json.dumps({'type': 'user', 'message': {'content': '在吗'}}) + '\n')
        self.conn.execute('UPDATE cameras SET last_active=?, last_stop=0 WHERE id=?', (time.time(), a['id']))
        self.conn.commit()
        live = studio.live_replies(self.conn)['assistant']
        self.assertEqual((live['waiting'], live['text']), (False, ''))
        with tp.open('a') as fh:
            fh.write(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '在的'}]}}) + '\n')
        self.assertEqual(studio.live_replies(self.conn)['assistant']['text'], '在的')
        self.conn.execute('UPDATE cameras SET last_stop=? WHERE id=?', (time.time() + 1, a['id']))
        self.conn.commit()
        self.assertNotIn('assistant', studio.live_replies(self.conn))

    def test_no_resume_without_transcript_and_crash_guard(self):
        self.tick()
        for _ in range(3):
            a = studio.active_assistant(self.conn)
            self.assertIsNone(a['resume_sid'])     # 没有 transcript 就开新会话，不瞎 resume
            studio.tmux('kill-pane', '-t', a['pane'])
            self.tick()
        self.assertIsNone(studio.active_assistant(self.conn))   # 连续秒退 3 次：暂停拉起
        self.assertTrue(studio.one(self.conn, 'SELECT id FROM messages WHERE text LIKE "麻薯连续%"'))

    def test_producer_offduty_when_idle(self):
        studio.CONFIG_OVERRIDES['producer_idle_minutes'] = 0
        conf = json.loads(studio.film_file('builda').read_text())
        conf['producer']['idle_minutes'] = 0
        studio.film_file('builda').write_text(json.dumps(conf))
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        self.tick()
        prod = self.cam(prod['id'])
        self.stop_turn(prod, '（无）')
        time.sleep(0.05)
        self.tick()
        self.assertEqual(self.cam(prod['id'])['ended_reason'], 'offduty')

    def test_film_docs_and_memory(self):
        d = studio.film_docs(self.film)
        for name in ('README.md', 'rules.md', 'producer-notes.md', 'playbook.json'):
            self.assertTrue((d / name).exists(), name)
        self.assertTrue((d / 'history' / 'playbook-v1.json').exists())
        key, _ = self.pitch('x')
        self.assertTrue((d / 'features' / key / 'state.json').exists())
        self.assertTrue((d / 'check.sh').exists() and (d / 'hotspots.txt').exists())
        self.tick()
        a = studio.active_assistant(self.conn)
        studio.MEMORY.mkdir(parents=True, exist_ok=True)
        (studio.MEMORY / '守则.md').write_text('# 守则\n\n- 汇报先给结论\n')
        self.assertIn('汇报先给结论', studio.system_prompt(self.conn, a))

    def test_quick_playbook_three_acts(self):
        f, key, cam = studio.pitch(self.conn, 'builda', 'typo', '修个错别字', playbook='quick')
        data = studio.seq_state(f, key)
        self.assertEqual([x['name'] for x in data['stages']], ['方案', '实现与自测', '发布'])
        self.tick()
        crew = self.cam(cam['id'])
        prompt = studio.system_prompt(self.conn, crew)
        self.assertIn('快速流程', prompt)
        self.assertIn('进入要子脑批准', prompt)
        self.cli(crew, 'seq', 'set', '1', '已完成')
        self.cli(crew, 'finish', '--reason', '方案完成')
        self.stop_turn(crew, '好了')
        self.tick()
        conf = studio.one(self.conn, 'SELECT * FROM reports WHERE kind="confirm"')
        self.assertEqual(conf['need'], 'producer')
        prod = wait_for(lambda: studio.active_producer(self.conn, 'builda'))
        self.cli(prod, 'answer', str(conf['id']), '批准')     # 快拍三幕：制片人就能批
        self.assertEqual(studio.active_crew(self.conn, 'builda', key)['crew_role'], '开发')

    def test_playbook_commit_overrule_restores(self):
        self.tick()
        a = studio.active_assistant(self.conn)
        path = studio.film_docs(self.film) / 'playbook.json'
        base = studio.load_template('standard') | {'id': 'film:builda', 'version': 1}   # 新项目默认直接做；这里测分阶段的专门工作流
        studio.write_playbook(path, base)
        studio.snapshot_playbook(path, base)
        pb = json.loads(path.read_text())
        pb['stages'][2]['do'] = '审片时必须跑一遍 lint'
        studio.write_playbook(path, pb)
        out = self.cli(a, 'playbook', 'commit', 'film:builda', '--note', '审片加 lint')
        self.assertIn('v2', out)
        d = studio.one(self.conn, 'SELECT * FROM decisions WHERE undo IS NOT NULL')
        self.assertIn('v2', d['summary'])
        # 坏剧本提交不了
        bad = json.loads(path.read_text())
        bad['stages'][1]['entry'] = 'boss'
        studio.write_playbook(path, bad)
        with self.assertRaises(SystemExit):
            self.cli(a, 'playbook', 'commit', 'film:builda', '--note', '坏的')
        studio.write_playbook(path, pb | {'version': 2})
        # 出品人推翻 → 回滚到 v1 的内容，版本号继续往上
        undo = json.loads(d['undo'])
        restored = studio.restore_playbook(self.conn, undo['target'], undo['version'])
        self.assertEqual(restored['version'], 3)
        self.assertNotIn('lint', json.loads(path.read_text())['stages'][2]['do'])
        # 新开的戏用新剧本
        self.assertIn('工作流', studio.retro_text(self.conn, 'builda'))

    def test_destructive_git_blocked(self):
        key, crew = self.pitch('x')
        self.tick()
        crew = self.cam(crew['id'])
        for cmd in ('git stash', 'git add -A', 'git add .', 'git checkout -- .', 'git reset --hard HEAD', 'git clean -fd',
                    'git commit -am "x"', 'cd src && git restore .'):
            out = self.hook('hook-pretool', crew, {'tool_name': 'Bash', 'tool_input': {'command': cmd}})
            self.assertIn('deny', out, cmd)
        for cmd in ('git add src/a.py', 'git commit -m "[builda#1] add all"', 'git add .mashu/x.md', 'git commit --amend -m x',
                    'git status', 'git diff src'):
            out = self.hook('hook-pretool', crew, {'tool_name': 'Bash', 'tool_input': {'command': cmd}})
            self.assertEqual(out, '', cmd)

    def test_finish_runs_check_and_waiver(self):
        self.strict()
        key, crew = self.pitch('x')
        crew = self.writing(key, ['src/**'])
        self.tick()
        crew = self.cam(crew['id'])
        script = studio.film_docs(self.film) / 'check.sh'
        script.write_text('echo "ERROR test_x failed"; exit 1\n')
        self.cli(crew, 'feature', 'set', '2', '已完成')
        with self.assertRaises(SystemExit) as cm:
            self.cli(crew, 'finish', '--reason', 'done')
        self.assertIn('ERROR test_x failed', str(cm.exception))
        data = studio.seq_state(self.film, key)
        self.assertEqual(data['stages'][1]['status'], '进行中')         # 打回
        self.assertTrue(list((studio.seq_dir(self.film, key) / 'tests').glob('check-stage2-*.md')))
        # 负责人豁免一次
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        self.cli(prod, 'waive', f'builda/{key}', '--reason', '失败来自别的功能')
        self.cli(crew, 'feature', 'set', '2', '已完成')
        self.assertIn('豁免', self.cli(crew, 'finish', '--reason', 'done'))
        # 检查通过
        script.write_text('echo ok; exit 0\n')
        crew = self.cam(crew['id'])
        self.cli(crew, 'feature', 'set', str(crew['stage_no']), '已完成')
        self.assertIn('自动检查通过', self.cli(crew, 'finish', '--reason', 'done'))

    def test_require_ac_and_own_commits(self):
        self.strict()
        f, key, cam = studio.pitch(self.conn, 'builda', 'q', '小改动', playbook='quick')
        self.tick()
        crew = self.cam(cam['id'])
        self.cli(crew, 'ac', 'add', 'WHEN 打开页面 THE system SHALL 显示 hello')
        with self.assertRaises(SystemExit):
            self.cli(crew, 'ac', 'pass', 'AC-1')                     # 没证据不行
        crew = self.writing(key, ['src/**'])
        self.tick()
        crew = self.cam(crew['id'])
        (studio.film_docs(f) / 'check.sh').write_text('exit 0\n')
        (self.repo / 'src').mkdir()
        (self.repo / 'src' / 'a.py').write_text('print(1)\n')
        self.cli(crew, 'feature', 'set', '2', '已完成')
        with self.assertRaises(SystemExit) as cm:
            self.cli(crew, 'finish', '--reason', 'done')
        self.assertIn('验收标准', str(cm.exception))
        self.cli(crew, 'ac', 'pass', 'AC-1', '--evidence', 'pytest -k hello → 1 passed')
        with self.assertRaises(SystemExit) as cm:
            self.cli(crew, 'finish', '--reason', 'done')
        self.assertIn('未提交', str(cm.exception))
        subprocess.run(['git', '-C', str(self.repo), 'add', 'src/a.py'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm',
                        f'[builda#{studio.seq_num(key)}] hello'], check=True)
        self.assertIn('本阶段提交 1 个', self.cli(crew, 'finish', '--reason', 'done'))
        self.assertEqual(len(studio.seq_state(f, key)['stages'][1]['commits']), 1)
        # 卡片证据
        r = studio.create_report(self.conn, 'gate', crew, 'builda', key, '发布', gate='release-plan')
        m = json.loads(r['meta'])
        self.assertEqual(m['ac']['passed'], 1)
        self.assertEqual(m['diff']['commits'], 1)

    def test_scope_conflict_runs_and_collaborates(self):
        self.strict()
        studio.CONFIG_OVERRIDES['stages'] = 5
        ka, kb = self.pitch('a')[0], self.pitch('b')[0]
        a = self.writing(ka, ['mashu/studio.py'])
        self.tick()
        b = self.writing(kb, ['mashu/studio.py'])
        self.tick()
        self.assertEqual(self.cam(a['id'])['state'], 'running')
        self.assertEqual(self.cam(b['id'])['state'], 'running')
        self.assertEqual(len(studio.rows(self.conn, 'SELECT id FROM messages WHERE target="producer:builda" AND text LIKE "%改动范围重叠%"')), 1)
        for cam in (a, b):
            context = self.hook('hook-prompt', cam, {})
            for expected in ('mashu/studio.py', '#' + str(studio.seq_num(ka)), '#' + str(studio.seq_num(kb))):
                self.assertIn(expected, context)
        self.cli(a, 'sync', '接口约定' + '甲' * 1000)
        self.assertNotIn('接口约定', self.hook('hook-prompt', a, {}))
        message = self.hook('hook-prompt', b, {})
        self.assertIn('接口约定', message)
        self.assertLess(message.count('甲'), 321)
        self.assertNotIn('接口约定', self.hook('hook-prompt', b, {}))
        self.tick(2)
        prod = studio.active_producer(self.conn, 'builda')
        self.assertIn('协作板', self.hook('hook-prompt', prod, {}))
        self.cli(a, 'peer', kb, '请复核签名')
        self.assertIn('请复核签名', self.hook('hook-prompt', b, {}))
        self.assertNotIn('请复核签名', self.hook('hook-prompt', b, {}))
        self.assertTrue(studio.one(self.conn, 'SELECT id FROM messages WHERE target="producer:builda" AND text LIKE "%请复核签名%"'))
        self.assertIn('工程法典', studio.system_prompt(self.conn, a))
        self.cli(prod, 'codex', 'note', '乙负责输入校验')
        self.assertIn('+', self.hook('hook-prompt', a, {}))
        self.assertIn('乙负责输入校验', self.hook('hook-prompt', b, {}))
        self.assertNotIn('乙负责输入校验', self.hook('hook-prompt', b, {}))
        out = self.hook('hook-pretool', b, {'tool_name': 'Edit', 'tool_input': {'file_path': str(self.repo / 'mashu/studio.py')}})
        self.assertEqual(out, '')
        self.assertIn('写入提醒', self.hook('hook-prompt', a, {}))
        self.hook('hook-pretool', b, {'tool_name': 'Edit', 'tool_input': {'file_path': str(self.repo / 'README.md')}})
        self.assertIn('README.md', studio.seq_state(self.film, kb)['claims']['extra'])
        out = self.hook('hook-pretool', b, {'tool_name': 'Write', 'tool_input': {
            'file_path': str(studio.seq_dir(self.film, kb) / 'acceptance.json')}})
        self.assertIn('deny', out)
        studio.end_camera(self.conn, b['id'], 'cut')
        self.assertIn('不在线', self.cli(a, 'peer', kb, '复核'))
        self.assertIsNone(studio.active_crew(self.conn, 'builda', kb))

    def test_brain_context_role_and_project_scope(self):
        self.tick()
        main = studio.active_assistant(self.conn)
        producer = studio.create_camera(self.conn, 'producer', 'builda', None, '开始')
        studio.MEMORY.mkdir(exist_ok=True, parents=True)
        (studio.MEMORY / '人格.md').write_text('给出判断依据')
        (studio.film_docs(self.film) / '子脑职责.md').write_text('交接先验导出接口')
        for cam in (main, producer):
            self.assertIn('给出判断依据', studio.system_prompt(self.conn, cam))
            context = studio.session_context(self.conn, cam)
            self.assertEqual('交接先验导出接口' in context, cam == producer)
            self.hook('hook-prompt', cam, {})
        (studio.MEMORY / '人格.md').write_text('交接附验收证据')
        for cam in (main, producer):
            self.assertIn('交接附验收证据', self.hook('hook-prompt', cam, {}))
            self.assertNotIn('交接附验收证据', self.hook('hook-prompt', cam, {}))

    def test_hotspot_lease_release(self):
        ka, a = self.pitch('a')
        kb, b = self.pitch('b')
        self.tick()
        (studio.film_docs(self.film) / 'hotspots.txt').write_text('*.md\n')
        path = self.repo / 'README.md'
        guard = lambda cam: studio.edit_guard(self.conn, cam, self.film, path)
        self.assertIsNone(guard(a))
        self.assertIn(f'#{studio.seq_num(ka)} 正持有', guard(b))
        calls = studio.codex_tool_calls({'tool_name': 'exec_command', 'tool_input': {
            'workdir': str(self.repo), 'cmd': "apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: README.md\n*** End Patch\nPATCH"}})
        self.assertEqual(calls[-1], ('Write', {'file_path': str(path)}))
        self.assertIsNone(studio.edit_guard(self.conn, b, self.film, self.repo / 'other.md'))
        path.write_text('完成\n')
        studio.git(self.repo, '-c', 'user.name=test', '-c', 'user.email=t@t', 'commit', '-qm', studio.feature_tag(self.film, ka), '--', 'README.md')
        self.assertIsNone(guard(b))
        self.assertIn(f'#{studio.seq_num(kb)} 正持有', guard(a))
        self.conn.execute('UPDATE hotspot_leases SET until=0')
        self.conn.commit()
        self.assertIsNone(guard(a))
        self.assertEqual(self.cam(b['id'])['state'], 'running')

    def test_project_board_render(self):
        studio.board_post(self.film, '子脑', '共同目标已登记。接口与页面分别由两个功能负责。')
        studio.board_post(self.film, '#1', '导出接口已提交，参数和返回值已同步给页面负责人。')
        studio.board_post(self.film, '#2', '页面已接入导出接口，正在验证空列表。')
        state = studio.status_data(self.conn)
        self.assertEqual([item['author'] for item in state['films'][0]['board']], ['子脑', '#1', '#2'])
        screenshot = os.environ.get('MASHU_BOARD_SCREENSHOT')
        if not screenshot:
            return
        web = studio.CODE / 'web'
        state.update(limits={}, cli_accounts=[], vendors=[])
        page = (web / 'index.html').read_text().replace('/static/', web.as_uri() + '/')
        fixture = ('<script>window.EventSource=undefined; const data=' + json.dumps(state) + ';'
                   'window.fetch=async path=>({ok:true,json:async()=>path==="/api/state"?data:{messages:[],boxes:[]}});'
                   'setTimeout(()=>{document.querySelector("#projectBoard summary")?.click();'
                   f'document.querySelectorAll(\'img[src="/static/mashu.jpg"]\').forEach(img=>img.src="{web.as_uri()}/mashu.jpg");'
                   '},300);</script>')
        page = page.replace('</head>', fixture + '</head>')
        path = self.tmp / 'project.html'
        path.write_text(page)
        chrome = shutil.which('chromium') or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        rendered = subprocess.run([chrome, '--headless', '--disable-gpu', '--disable-background-networking',
            '--no-first-run', '--allow-file-access-from-files', '--user-data-dir=' + str(self.tmp / 'profile'),
            '--virtual-time-budget=1500', '--window-size=1320,860', '--screenshot=' + screenshot, '--dump-dom',
            path.as_uri() + '#/chat/builda'], capture_output=True, text=True, timeout=35, check=True)
        self.assertRegex(rendered.stdout, r'<details[^>]*id="projectBoard"[^>]*open[^>]*>')
        self.assertIn('<p>页面已接入导出接口，正在验证空列表。</p>', rendered.stdout)
        self.assertTrue(Path(screenshot).is_file())

    def test_user_opened_features_without_scope_do_not_queue_each_other(self):
        # 用户从网页开的功能 / 会话（by=None）没写范围：自动认领自己的资料夹，不再因「有一方没声明范围」互相排队（#6、#10）
        studio.CONFIG_OVERRIDES['stages'] = 5
        fa, ka, a = studio.pitch(self.conn, 'builda', 'chat-a', '聊聊')
        fb, kb, b = studio.pitch(self.conn, 'builda', 'chat-b', '再聊')
        self.assertEqual(studio.seq_state(fb, kb)['claims']['touches'], [f'.mashu/features/{kb}/**'])
        self.tick()
        self.assertEqual(self.cam(a['id'])['state'], 'running')
        self.assertEqual(self.cam(b['id'])['state'], 'running')
        self.assertFalse(studio.one(self.conn, 'SELECT id FROM messages WHERE text LIKE "%还没有声明改动范围%"'))

    def test_trust_ladder(self):
        self.strict()
        studio.CONFIG_OVERRIDES['trust_streak'] = 2
        key, crew = self.pitch('x')
        self.tick()
        crew = self.cam(crew['id'])
        for _ in range(2):
            r = studio.create_report(self.conn, 'gate', crew, 'builda', key, '部署测试环境', gate='preview')
            studio.answer_report(self.conn, r['id'], None, '审批')
        prop = studio.one(self.conn, 'SELECT * FROM reports WHERE summary LIKE "放权提议%"')
        self.assertEqual(prop['holder'], 'executive')
        studio.answer_report(self.conn, prop['id'], None, '交给麻薯')
        r = studio.create_report(self.conn, 'gate', crew, 'builda', key, '再部署', gate='preview')
        self.assertEqual(r['need'], 'assistant')
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        self.cli(prod, 'answer', str(r['id']), '审批')          # 已放权：子脑（麻薯）直接批
        d = studio.one(self.conn, 'SELECT * FROM decisions WHERE undo LIKE "%trust%"')
        self.assertIsNotNone(d)
        studio.demote_trust(self.conn, 'builda', 'gate:preview', '不行')
        r2 = studio.create_report(self.conn, 'gate', crew, 'builda', key, '再来', gate='preview')
        self.assertEqual(r2['need'], 'executive')
        # 生产操作永远不放权
        self.assertIsNone(studio.trust_category({'gate': 'prod'}))

    def test_session_context_and_statusline(self):
        key, crew = self.pitch('x')
        self.tick()
        crew = self.cam(crew['id'])
        out = self.hook('hook-session', crew, {'source': 'compact'})
        self.assertIn('上下文恢复', out)
        self.assertIn('验收标准', out)
        os.environ['MASHU_SESSION'] = str(crew['id'])
        old_in = sys.stdin
        sys.stdin = io.StringIO(json.dumps({'context_window': {'used_percentage': 42}}))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                studio.main(['statusline'])
        finally:
            sys.stdin = old_in
            os.environ.pop('MASHU_SESSION', None)
        self.assertAlmostEqual(self.cam(crew['id'])['context_pct'], 0.42)

    def test_subbrain_reports_cross_project_to_main_brain(self):
        prod = studio.create_camera(self.conn, 'producer', 'builda', None, 'hi')
        self.assertEqual(prod['name'], '麻薯')
        self.cli(prod, 'report', '两个项目都要用测试环境，冲突了')
        r = studio.one(self.conn, 'SELECT * FROM reports WHERE kind="report"')
        self.assertEqual(r['holder'], 'assistant')
        self.assertIsNone(studio.one(self.conn, 'SELECT id FROM messages WHERE kind="card" AND report_id=?', r['id']))
        self.assertIn('子脑', studio.camera_title(self.conn, prod))

    def test_status_and_callsheet(self):
        self.pitch('x')
        self.tick()
        data = studio.status_data(self.conn)
        self.assertEqual(data['films'][0]['sequences'][0]['status'], 'shooting')
        out = self.cli(None, 'callsheet')
        self.assertIn('Builda', out)
        self.assertIn('#1', out)


if __name__ == '__main__':
    unittest.main()
