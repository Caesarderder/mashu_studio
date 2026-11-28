"""通报里写了要对方动手的事（截图、登录、充值）就拒发，改用 ask（自迭代 #136）。

  python3 -m unittest tests.test_tell_todo -v
"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mashu.studio import tell_todo  # noqa: E402


class TellTodo(unittest.TestCase):
    def test_blocks_requests(self):
        for t in ['请打开官网截一张用量图发我', '麻烦你登录一下后台', '需要用户充值后才能继续', '请充值 50 元']:
            self.assertTrue(tell_todo(t), t)

    def test_allows_statements(self):
        for t in ['阶段 1 已完成，需要你判断要不要返工', '已截图存到 a.png，登录页正常', '用户已充值，余额 50 元', '']:
            self.assertFalse(tell_todo(t), t)


if __name__ == '__main__':
    unittest.main()
