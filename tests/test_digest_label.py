"""未结上报在简报手上（holder=digest）时，status / callsheet / reports 不崩：mashu-studio #1 追加项。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import Base, studio  # noqa: E402


class TestDigestLabel(Base):
    def test_digest_holder_listed(self):
        self.assertEqual(studio.LEVEL_LABEL['digest'], '简报')
        repo = self.make_repo('builda')
        studio.greenlight(self.conn, repo, 'builda', 'Builda')
        r = studio.create_report(self.conn, 'stage-done', None, 'builda', None, '方案完成', holder='digest')
        self.assertEqual(studio.report(self.conn, r['id'])['holder'], 'digest')
        for cmd in ('callsheet', 'reports', 'status'):
            with self.subTest(cmd=cmd):
                out = self.cli(None, cmd)
                if cmd != 'status':
                    self.assertIn('在简报手上', out)


if __name__ == '__main__':
    unittest.main()
