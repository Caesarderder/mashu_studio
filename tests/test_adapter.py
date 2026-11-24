"""项目适配器扩展点（<工程>/.mashu/adapter.py）与功能工作目录（state.workdir）的测试：用假适配器覆盖每个钩子。

  python3 -m unittest tests.test_adapter -v
"""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import Base, FAKE_CLAUDE, wait_for  # noqa: E402
import studio  # noqa: E402

FAKE_ADAPTER = r'''
import json, re
SYNC_SECONDS = 0


def _st(ctx):
    p = ctx.docs / 'fake.json'
    return json.loads(p.read_text()) if p.exists() else {}


def _log(ctx, *a):
    with open(ctx.docs / 'calls.jsonl', 'a') as fh:
        fh.write(json.dumps(a, ensure_ascii=False, default=str) + '\n')


def on_new(ctx, feature):
    _log(ctx, 'on_new', feature['key'])
    m = re.match(r'(\d+)-', feature['slug'])
    if not m:
        return None
    d = ctx.root / 'issues' / feature['slug']
    d.mkdir(parents=True, exist_ok=True)
    return {'workdir': 'issues/' + feature['slug'], 'adapter': {'ticket': int(m.group(1))}}


def launch_env(ctx, feature, session):
    _log(ctx, 'launch_env', feature['key'], session['resume'], session['env'])
    if not feature['adapter']:
        return {}
    return {'WF_TICKET': feature['adapter']['ticket'], 'MASHU_ROLE': 'hack',
            'WF_SESSION': session['env'].get('WF_SESSION') or 'sess-%s' % session['id']}


def launch_prompt(ctx, feature):
    if not feature['adapter']:
        return None
    return {'text': '## 工单规则\nFAKE-WF-PROMPT', 'replace': True,
            'first': '先 wf check，再 wf context %s' % feature['adapter']['ticket']}


def pretool(ctx, feature, tool, inp, risky):
    cmd = inp.get('command', '')
    if _st(ctx).get('allow_all'):
        return True
    if 'wf answer' in cmd:
        return '不能自答'
    if cmd.startswith('wf '):
        return True
    if risky and risky[0] == 'prod':
        return True if _st(ctx).get('release_ok') else 'release 没批'
    return None


def sync(ctx, known):
    _log(ctx, 'sync', known)
    st = _st(ctx)
    if st.get('boom'):
        raise RuntimeError('boom')
    return {'cards': st.get('cards', []), 'close': st.get('close', []), 'messages': st.get('messages', [])}


def answer(ctx, card, text):
    _log(ctx, 'answer', card['ref'], text)
    return _st(ctx).get('answer_result') or {'status': 'answered', 'answer': text,
                                             'deliver': '[工单请求 %s 已回答] %s' % (card['ref'], text)}


def on_idle(ctx, feature, session):
    _log(ctx, 'on_idle', feature['key'], session['env'])
    return (_st(ctx).get('idle') or {}).get(feature['key'])


def cron(ctx):
    ctx.set('cron_n', ctx.get('cron_n', 0) + 1)
'''

ENV_LINE = 'printf \'%s\\n\' "ENV WF_TICKET=$WF_TICKET WF_SESSION=$WF_SESSION ROLE=$MASHU_ROLE" >> "$log"\n'


class AdapterBase(Base):
    def setUp(self):
        super().setUp()
        fake = self.tmp / 'fake-claude'          # 假 claude 额外记下适配器给的环境变量
        lines = FAKE_CLAUDE.splitlines(keepends=True)
        fake.write_text(''.join(lines[:3]) + ENV_LINE + ''.join(lines[3:]))
        self.saved_threaded = studio.ADAPTER_THREADED
        studio.ADAPTER_THREADED = False
        self.addCleanup(setattr, studio, 'ADAPTER_THREADED', self.saved_threaded)
        studio._adapter_jobs.clear()
        studio._adapter_mods.clear()
        self.repo = self.make_repo('ws1')
        self.film, _ = studio.greenlight(self.conn, self.repo, 'ws', 'WS')
        self.docs = studio.film_docs(self.film)
        (self.docs / 'adapter.py').write_text(FAKE_ADAPTER)
        studio.CONFIG_OVERRIDES['stages'] = 4
        conf = json.loads(studio.film_file('ws').read_text())
        conf['max_stages'] = 4
        studio.film_file('ws').write_text(json.dumps(conf))

    def put(self, **kw):
        p = self.docs / 'fake.json'
        st = json.loads(p.read_text()) if p.exists() else {}
        st.update(kw)
        p.write_text(json.dumps(st, ensure_ascii=False))

    def calls(self, kind=None):
        p = self.docs / 'calls.jsonl'
        out = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [c for c in out if kind is None or c[0] == kind]

    def sync(self):
        studio._adapter_jobs.clear()
        studio.adapters_tick(self.conn, threaded=False)

    def new(self, slug):
        f, key, cam = studio.pitch(self.conn, 'ws', slug, f'需求 {slug}')
        return key, cam

    def launched(self, key):
        studio.tick(self.conn)
        return wait_for(lambda: 'ENV ' in self.logtext('crew', 'ws', key) and self.logtext('crew', 'ws', key))

    def opened(self):
        return studio.external_cards(self.conn, 'ws')

    def pretool(self, cam, cmd):
        return self.hook('hook-pretool', cam, {'tool_name': 'Bash', 'tool_input': {'command': cmd}})


class TestWorkdirAndLaunch(AdapterBase):
    def test_on_new_sets_workdir_env_prompt(self):
        key, cam = self.new('1069-ticket')
        data = studio.seq_state(self.film, key)
        self.assertEqual(data['workdir'], 'issues/1069-ticket')
        self.assertEqual(data['adapter'], {'ticket': 1069})
        wd = (self.repo / 'issues' / '1069-ticket').resolve()
        self.assertEqual(studio.set_dir(self.film, key), wd)
        self.assertIn(f'工作目录：{wd}', studio.status_text(self.film, key))
        self.assertIn('issues/1069-ticket/**', studio.claims_of(self.film, key))
        log = self.launched(key)
        self.assertIn(f'PWD={wd}', log)
        self.assertIn(f'ENV WF_TICKET=1069 WF_SESSION=sess-{cam["id"]} ROLE=crew', log)    # 不能覆盖 MASHU_*
        self.assertIn('先 wf check，再 wf context 1069', log)
        self.assertTrue(log.split('ARG:')[-1].startswith('继续 #1 1-1069-ticket'))       # 「继续」仍在开头
        prompt = studio.system_prompt(self.conn, self.cam(cam['id']))
        self.assertIn('FAKE-WF-PROMPT', prompt)
        self.assertNotIn('这是一个**直接做**的任务', prompt)          # replace=True 换掉默认的「怎么做」
        self.assertIn('项目规则', prompt)

    def test_plain_feature_and_manual_workdir(self):
        key, cam = self.new('fix-readme')
        self.assertNotIn('workdir', studio.seq_state(self.film, key))
        self.assertEqual(studio.set_dir(self.film, key), self.repo.resolve())
        log = self.launched(key)
        self.assertIn(f'PWD={self.repo.resolve()}', log)
        self.assertIn('ENV WF_TICKET= ', log)
        self.assertIn('这是一个**直接做**的任务', studio.system_prompt(self.conn, self.cam(cam['id'])))
        with self.assertRaises(ValueError):
            studio.pitch(self.conn, 'ws', 'x', 'x', workdir='nope/dir')
        with self.assertRaises(ValueError):
            studio.pitch(self.conn, 'ws', 'y', 'y', workdir='../..')
        (self.repo / 'sub').mkdir()
        f, key2, _ = studio.pitch(self.conn, 'ws', 'z', 'z', workdir='sub')
        self.assertEqual(studio.set_dir(f, key2), (self.repo / 'sub').resolve())

    def test_resume_keeps_env(self):
        key, cam = self.new('1070-a')
        self.launched(key)
        env1 = json.loads(studio.meta_get(self.conn, f'adapter-env:cam:{cam["id"]}'))
        sid = self.cam(cam['id'])['claude_sid']
        self.assertEqual(json.loads(studio.meta_get(self.conn, f'adapter-env:{sid}')), env1)
        studio.end_camera(self.conn, cam['id'], 'exited')
        c2 = studio.create_camera(self.conn, 'crew', 'ws', key, '接着做', resume_sid=sid)
        studio.launch(self.conn, c2['id'])
        last = self.calls('launch_env')[-1]
        self.assertEqual(last[2:], [True, env1])
        self.assertEqual(json.loads(studio.meta_get(self.conn, f'adapter-env:cam:{c2["id"]}'))['WF_SESSION'], env1['WF_SESSION'])
        sess = studio.session_view(self.conn, self.cam(c2['id']))
        self.assertEqual(sess['env']['WF_SESSION'], env1['WF_SESSION'])

    def test_broken_adapter_does_not_break_scheduling(self):
        (self.docs / 'adapter.py').write_text('def on_new(ctx, f):\n    raise RuntimeError("坏了")\nsyntax error here(')
        key, cam = self.new('1071-b')
        self.assertNotIn('workdir', studio.seq_state(self.film, key))
        studio.tick(self.conn)
        studio.tick(self.conn)
        self.assertEqual(self.cam(cam['id'])['state'], 'running')
        errs = studio.rows(self.conn, 'SELECT * FROM events WHERE kind="adapter-error"')
        self.assertTrue(errs)
        (self.docs / 'adapter.py').write_text('def sync(ctx, known):\n    raise RuntimeError("boom")\n')
        self.sync()
        self.assertTrue(any('boom' in e['detail'] for e in studio.rows(self.conn, 'SELECT * FROM events WHERE kind="adapter-error"')))


class TestCards(AdapterBase):
    def setUp(self):
        super().setUp()
        self.key, self.cam0 = self.new('1069-ticket')
        self.put(cards=[dict(ref='#1069/req/1', key=self.key, title='方案确认', text='**方案**\n\n正文', options=['批准', '要改'],
                             url='http://wf/t/1069#req-1', source='工单'),
                        dict(ref='#888/req/2', key=None, title='发布批准（2FA）', text='去网页', url='http://wf/t/888#req-2',
                             web_only=True, source='工单')])

    def test_sync_creates_dedupes_and_routes(self):
        self.sync()
        cards = self.opened()
        self.assertEqual(sorted(c['meta']['ref'] for c in cards), ['#1069/req/1', '#888/req/2'])
        a = next(c for c in cards if c['meta']['ref'] == '#1069/req/1')
        self.assertEqual((a['holder'], a['need'], a['seq_key']), ('executive', 'executive', self.key))
        self.assertEqual(json.loads(a['options']), ['批准', '要改'])
        b = next(c for c in cards if c['meta']['ref'] == '#888/req/2')
        self.assertIsNone(b['seq_key'])                 # 没对上功能的卡片进项目频道
        self.assertTrue(b['meta']['web_only'])
        self.assertTrue(studio.one(self.conn, 'SELECT 1 x FROM messages WHERE kind="card" AND report_id=?', a['id']))
        self.sync()
        self.assertEqual(len(self.opened()), 2)         # 按 ref 去重
        self.assertEqual(self.calls('sync')[-1][1], [dict(ref=c['meta']['ref'], key=c['seq_key'], rid=c['id']) for c in self.opened()])
        self.assertEqual(studio.feature_view(self.conn, self.film, self.key)['open_cards'], ['#1069/req/1'])
        # 麻薯（子脑）不能代答外部卡片
        prod = studio.create_camera(self.conn, 'producer', 'ws', None, 'x')
        with self.assertRaises(ValueError):
            studio.answer_report(self.conn, a['id'], prod, '批准')
        with self.assertRaises(ValueError):
            studio.answer_external(self.conn, studio.report(self.conn, a['id']), prod, '批准')
        # 静默的不再出卡
        studio.mute_report(self.conn, b['id'])
        self.sync()
        self.assertEqual([c['meta']['ref'] for c in self.opened()], ['#1069/req/1'])

    def test_close_and_messages(self):
        self.sync()
        a = self.opened()[0]
        self.put(cards=[], close=[dict(ref='#1069/req/1', answer='批准（在工单网页）', deliver='[工单请求 #1069/req/1 已回答] 批准')],
                 messages=[dict(key=self.key, text='[工单反馈 #1069] 反馈作者答复了'), dict(key=None, text='工单服务不可达')])
        self.sync()
        r = studio.report(self.conn, a['id'])
        self.assertEqual((r['status'], r['answer'], r['answered_by']), ('closed', '批准（在工单网页）', 'external'))
        to_crew = [m['text'] for m in studio.rows(self.conn, 'SELECT * FROM messages WHERE target=?', f'crew:ws/{self.key}')]
        self.assertIn('[工单请求 #1069/req/1 已回答] 批准', to_crew)
        self.assertIn('[工单反馈 #1069] 反馈作者答复了', to_crew)
        self.assertTrue(studio.one(self.conn, 'SELECT 1 x FROM messages WHERE channel="ws" AND text="工单服务不可达"'))
        # 改派回来（同一个 ref 又开着）：重新出卡
        self.put(cards=[dict(ref='#1069/req/1', key=self.key, title='方案确认', text='再看')], close=[], messages=[])
        self.sync()
        again = [c for c in self.opened() if c['meta']['ref'] == '#1069/req/1']
        self.assertEqual(len(again), 1)
        self.assertNotEqual(again[0]['id'], a['id'])

    def test_user_answer_goes_through_adapter(self):
        self.sync()
        a = next(c for c in self.opened() if c['meta']['ref'] == '#1069/req/1')
        r = studio.answer_report(self.conn, a['id'], None, '批准')
        self.assertEqual((r['status'], r['answered_by'], r['answer']), ('answered', 'executive', '批准'))
        self.assertEqual(self.calls('answer')[-1], ['answer', '#1069/req/1', '批准'])
        msgs = studio.rows(self.conn, 'SELECT * FROM messages WHERE target=?', f'crew:ws/{self.key}')
        self.assertTrue(any(m['text'] == '[工单请求 #1069/req/1 已回答] 批准' for m in msgs))
        self.assertEqual(studio.delivery_text(self.conn, msgs[-1]), '[系统] [工单请求 #1069/req/1 已回答] 批准')
        # 写回失败：卡片保持打开，报错给网页
        b = next(c for c in self.opened() if c['meta']['ref'] == '#888/req/2')
        self.put(answer_result=dict(status='error', message='需要 2FA：去工单网页'))
        with self.assertRaisesRegex(ValueError, '2FA'):
            studio.answer_report(self.conn, b['id'], None, '批准')
        self.assertEqual(studio.report(self.conn, b['id'])['status'], 'open')
        # 已在别处处理：卡片关掉、照样投递，再告诉网页
        self.put(answer_result=dict(status='closed', answer='已批准（工单网页）', deliver='[工单请求 #888/req/2 已回答] 已批准',
                                    message='已在别处处理'))
        with self.assertRaisesRegex(ValueError, '别处'):
            studio.answer_report(self.conn, b['id'], None, '批准')
        self.assertEqual(studio.report(self.conn, b['id'])['status'], 'closed')


class TestPretool(AdapterBase):
    def test_adapter_gate(self):
        key, cam = self.new('1069-ticket')
        self.launched(key)
        cam = self.cam(cam['id'])
        self.assertIn('不能自答', self.pretool(cam, 'wf answer 1069/req/1 --approve'))
        self.assertEqual(self.pretool(cam, 'wf progress 1069 -b "准备正式发布"'), '')
        out = self.pretool(cam, 'git push origin main')
        self.assertIn('release 没批', out)
        self.assertIn('"deny"', out)
        self.put(release_ok=True)
        self.assertEqual(self.pretool(cam, 'git push origin main'), '')           # 适配器担保：不再要麻薯的 prod 审批
        self.assertTrue(studio.one(self.conn, 'SELECT 1 x FROM audit WHERE action="adapter-allow"'))
        self.assertEqual(self.pretool(cam, 'ls'), '')
        self.put(allow_all=True)
        self.assertIn('"deny"', self.pretool(cam, f'rm -rf {self.repo.resolve()}'))         # 不可逆操作适配器放不了
        self.assertIn('"deny"', self.pretool(cam, 'git stash'))                   # 麻薯自己的 git 规矩在前

    def test_no_adapter_hook_keeps_mashu_gate(self):
        (self.docs / 'adapter.py').write_text('X = 1\n')
        key, cam = self.new('fix')
        self.launched(key)
        self.assertIn('安全拦截', self.pretool(self.cam(cam['id']), 'git push origin main'))


class TestIdleAndCron(AdapterBase):
    def idle(self, cam):
        self.stop_turn(self.cam(cam['id']), '做完了这一步')
        return self.cam(cam['id'])

    def test_continue_new_actor(self):
        key, cam = self.new('1069-ticket')
        self.launched(key)
        cam = self.idle(cam)
        self.put(idle={key: dict(action='continue', note='wf handoff：开发完成', prompt='[系统] 你是新的会话，负责独立 Review')})
        self.sync()
        self.assertEqual(self.calls('on_idle')[-1], ['on_idle', key, {'WF_TICKET': '1069', 'WF_SESSION': f'sess-{cam["id"]}'}])
        old = self.cam(cam['id'])
        self.assertEqual((old['state'], old['ended_reason']), ('ended', 'wrap'))
        new = studio.active_crew(self.conn, 'ws', key)
        self.assertNotEqual(new['id'], cam['id'])
        self.assertNotEqual(new['name'], cam['name'])
        self.assertIn('独立 Review', new['prompt'])
        self.put(idle={})
        studio.tick(self.conn)
        env = json.loads(studio.meta_get(self.conn, f'adapter-env:cam:{new["id"]}'))
        self.assertEqual(env['WF_SESSION'], f'sess-{new["id"]}')              # 新会话 = 新 actor

    def test_stop_done_and_busy(self):
        key, cam = self.new('1069-ticket')
        self.launched(key)
        self.put(idle={key: dict(action='done', note='#1069 已结单')})
        self.sync()                                   # 还在忙：不问 on_idle
        self.assertEqual(self.calls('on_idle'), [])
        self.assertEqual(self.cam(cam['id'])['state'], 'running')
        self.idle(cam)
        self.sync()
        self.assertEqual(self.cam(cam['id'])['state'], 'ended')
        self.assertTrue(studio.seq_done(studio.seq_state(self.film, key)))
        self.assertIsNone(studio.active_crew(self.conn, 'ws', key))
        key2, cam2 = self.new('1070-x')
        self.launched(key2)
        self.idle(cam2)
        self.put(idle={key2: dict(action='stop', note='交给别人了')})
        self.sync()
        self.assertEqual(self.cam(cam2['id'])['state'], 'ended')
        self.assertIsNone(studio.active_crew(self.conn, 'ws', key2))
        self.assertFalse(studio.seq_done(studio.seq_state(self.film, key2)))

    def test_cron_runs_each_cycle_and_persists(self):
        self.sync()
        self.sync()
        self.assertEqual(studio.AdapterCtx(self.conn, self.film).get('cron_n'), 2)
        self.assertTrue(studio.meta_get(self.conn, 'adapter:ws:cron_n'))

    def test_threaded_result_applied_next_tick(self):
        key, cam = self.new('1069-ticket')
        self.put(cards=[dict(ref='#1069/req/9', key=key, title='提问', text='问')])
        studio._adapter_jobs.clear()
        studio.adapters_tick(self.conn, threaded=True)
        wait_for(lambda: not studio._adapter_jobs['ws']['thread'].is_alive())
        self.assertEqual(self.opened(), [])
        studio.adapters_tick(self.conn, threaded=True)
        self.assertEqual([c['meta']['ref'] for c in self.opened()], ['#1069/req/9'])


if __name__ == '__main__':
    unittest.main()
