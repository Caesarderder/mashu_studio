"""发布检查失败详情（#146）与发布结果通知请求发布的子脑（#145 #148）。

  python3 -m unittest tests.test_release_notify -v
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import Base, studio  # noqa: E402

OUT = '''..F.
======================================================================
FAIL: test_on_new (test_adapter.TestWorkdirAndLaunch)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "x.py", line 1, in test_on_new
    self.assertTrue(x)
AssertionError: False is not true

----------------------------------------------------------------------
Ran 4 tests in 0.1s

FAILED (failures=1)
ERROR 麻薯单测失败'''


class ReleaseNotify(Base):
    def test_failures_and_log(self):
        self.assertEqual(studio.release_failures(OUT), ['test_adapter.TestWorkdirAndLaunch.test_on_new：AssertionError: False is not true'])
        self.assertEqual(studio.release_failures('blah\nERROR 麻薯单测失败'), ['ERROR 麻薯单测失败'])
        msg, fails, log, tail = studio.release_fail_detail('a' * 40, OUT, '发布前检查未通过（aaaaaaa）')
        self.assertTrue(msg.splitlines()[1].startswith('失败项：test_adapter'))
        self.assertEqual(Path(log).read_text(), OUT)
        self.assertIn('FAILED', tail)

    def test_notify_requester(self):
        repo = self.make_repo('ws1')
        f, _ = studio.greenlight(self.conn, repo, 'ws', 'WS')
        sha = '95e9f8b' + '0' * 33
        self.conn.execute('INSERT INTO reports(kind, film_id, source_role, text, summary, holder, status, created_at) '
                          'VALUES("report","ws","producer","请发布 95e9f8b","","assistant","open",?)', (time.time(),))
        self.conn.commit()
        rid = studio.notify_release(self.conn, sha, False, '发布 95e9f8b 失败：失败项：x；线上仍是 f8b97df。')
        self.assertTrue(rid)
        self.assertIn('[', studio.report(self.conn, rid)['text'].split('\n\n')[-1])
        self.assertIn('发布结果', studio.report(self.conn, rid)['text'])
        self.assertTrue(studio.one(self.conn, 'SELECT 1 x FROM messages WHERE channel="ws" AND text LIKE "%发布结果%"'))
        self.assertIsNone(studio.notify_release(self.conn, 'deadbee' + '0' * 33, True, 'x'))


if __name__ == '__main__':
    unittest.main()
