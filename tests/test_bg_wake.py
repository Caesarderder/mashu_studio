"""非 Claude 平台后台任务结束后自动叫醒空闲会话（caesar #431）。"""
from pathlib import Path
import sys
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'mashu'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio  # noqa: E402
from test_studio import Base  # noqa: E402


class BgWakeTests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('demo')
        studio.greenlight(self.conn, str(self.repo), 'demo', '演示项目')
        _, self.key, cam = studio.pitch(self.conn, 'demo', 'bgw', '实现 bgw', title='后台')
        studio.tick(self.conn)
        self.stop_turn(self.cam(cam['id']), '排了测量任务')
        self.c = self.cam(cam['id'])
        self.detail = dict(shells=[], agents=[])
        self.patches = [mock.patch.object(studio, 'cam_provider', return_value='codex'),
                        mock.patch.object(studio, 'live_detail_of', side_effect=lambda conn, c, **k: self.detail)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def wakes(self):
        return studio.rows(self.conn, 'SELECT * FROM messages WHERE target=? AND text LIKE "后台任务已结束%"',
                           studio.target_of(self.c))

    def test_wake_once_after_all_done(self):
        ls = self.c['last_stop']
        self.detail = dict(shells=[dict(desc='lockf 测量 A', state='running', start=ls - 5)], agents=[])
        studio.bg_done_wake(self.conn)
        self.assertEqual(self.wakes(), [])
        self.detail = dict(shells=[dict(desc='lockf 测量 A', state='done', start=ls - 5, end=ls + 60)], agents=[])
        studio.bg_done_wake(self.conn)
        studio.bg_done_wake(self.conn)
        w = self.wakes()
        self.assertEqual(len(w), 1)
        self.assertIn('lockf 测量 A', w[0]['text'])

    def test_waits_while_others_running_then_grace(self):
        ls = self.c['last_stop']
        now = ls + 20
        self.detail = dict(shells=[dict(desc='A', state='done', end=ls + 5),
                                   dict(desc='B', state='running')], agents=[])
        with mock.patch.object(studio.time, 'time', return_value=now):
            studio.bg_done_wake(self.conn)
        self.assertEqual(self.wakes(), [])
        with mock.patch.object(studio.time, 'time', return_value=ls + 5 + studio.BG_WAKE_GRACE):
            studio.bg_done_wake(self.conn)
        self.assertEqual(len(self.wakes()), 1)
        self.assertIn('还有 1 个在跑', self.wakes()[0]['text'])

    def test_ignores_tasks_done_before_stop_and_claude(self):
        ls = self.c['last_stop']
        self.detail = dict(shells=[dict(desc='A', state='done', end=ls - 30)], agents=[])
        studio.bg_done_wake(self.conn)
        self.assertEqual(self.wakes(), [])
        self.detail = dict(shells=[dict(desc='A', state='done', end=ls + 30)], agents=[])
        with mock.patch.object(studio, 'cam_provider', return_value='claude'):
            studio.bg_done_wake(self.conn)
        self.assertEqual(self.wakes(), [])

    def test_new_idle_period_can_wake_again(self):
        ls = self.c['last_stop']
        self.detail = dict(shells=[dict(desc='A', state='done', end=ls + 30)], agents=[])
        studio.bg_done_wake(self.conn)
        self.conn.execute('UPDATE cameras SET last_active=?, last_stop=? WHERE id=?', (ls + 40, ls + 50, self.c['id']))
        self.c = self.cam(self.c['id'])
        studio.bg_done_wake(self.conn)
        self.assertEqual(len(self.wakes()), 1)          # A 在新一轮之前结束，不再叫
        self.detail = dict(shells=[dict(desc='B', state='done', end=ls + 80)], agents=[])
        studio.bg_done_wake(self.conn)
        self.assertEqual(len(self.wakes()), 2)
