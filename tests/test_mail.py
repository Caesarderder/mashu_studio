"""邮箱：上报和 agent 之间的往来按信箱分收件 / 发件。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'mashu'))
import mail  # noqa: E402
import studio  # noqa: E402
from test_studio import Base  # noqa: E402


class TestMail(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('builda')
        self.film, _ = studio.greenlight(self.conn, self.repo, 'builda', 'Builda')

    def keys(self, box, which):
        return [(x['kind'], x['pending']) for x in mail.mailbox(self.conn, box)[which]]

    def test_memo_event_and_report_routing(self):
        studio.post(self.conn, 'assistant', 'builda', 'memo', '先做导出', target='producer:builda')
        studio.event(self.conn, 'builda', None, 'done|#1 完成')
        rid = studio.create_report(self.conn, 'ask', None, 'builda', None, '要不要发版？', summary='发版？', holder='executive')['id']
        self.assertIn(('memo', False), self.keys('producer:builda', 'inbox'))
        self.assertIn(('memo', False), self.keys('assistant', 'outbox'))
        me = mail.mailbox(self.conn, 'executive')['inbox']
        self.assertIn(('event', False), [(x['kind'], x['pending']) for x in me])
        ask = next(x for x in me if x.get('report_id') == rid)
        self.assertTrue(ask['pending'])
        self.assertEqual(next(b for b in mail.boxes(self.conn) if b['id'] == 'executive')['pending'], 1)
        # 批复后不再待处理，发件里记一条批复
        studio.answer_report(self.conn, rid, None, '发')
        self.assertFalse(next(x for x in mail.mailbox(self.conn, 'executive')['inbox'] if x.get('report_id') == rid)['pending'])
        out = mail.mailbox(self.conn, 'executive')['outbox']
        self.assertTrue(any(x['kind_label'] == '批复' and x['subject'].startswith('发') for x in out))


if __name__ == '__main__':
    unittest.main()
