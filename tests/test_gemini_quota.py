"""Gemini 额度改读 agy -p /quota --output-format json（#18 gemini-quota）。

覆盖：
- AC2：14:16 真实输出样例（脱敏）解析出 4 个窗口、1970 重置时间记为空、remaining 取最低值、source='agy_cli'、groups 完整
- AC3：agy 失败 / 超时时保留上一次真实额度并标出更新时间；没有任何真实值时标 api_placeholder、used_pct 为 None、显示「额度未知」而非 100%
- studio.accounts_check / collect_limits / session_limits_for / fmt_account 集成

  python3 -m unittest tests.test_gemini_quota -v
"""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_accounts import FakeHTTP, FakeKeychain, RT1, RT2  # noqa: E402
from test_platforms import PlatBase  # noqa: E402
from test_studio import studio  # noqa: E402
import accounts as acct  # noqa: E402

# 2026-11-27 14:16 本机 agy 1.5.15 真实输出样例（无邮箱 / 凭据，0 token）
SAMPLE_1416 = {
    'conversation_id': '',
    'status': 'SUCCESS',
    'response': (
        'Gemini Models\tWeekly Limit Remaining\t63%\t2026-12-03T07:37:23Z\n'
        'Gemini Models\tFive Hour Limit Remaining\t65%\t2026-11-27T22:56:58Z\n'
        'Claude and GPT models\tWeekly Limit Remaining\t100%\t1970-01-01T00:00:00Z\n'
        'Claude and GPT models\tFive Hour Limit Remaining\t100%\t1970-01-01T00:00:00Z\n'
    ),
    'duration_seconds': 0,
    'num_turns': 0,
    'usage': {
        'input_tokens': 0,
        'output_tokens': 0,
        'thinking_tokens': 0,
        'cache_read_tokens': 0,
        'total_tokens': 0,
    },
    'command': {
        'name': 'usage',
        'data': {
            'description': 'Within each group, models share a weekly limit and a 5-hour limit.',
            'groups': [
                {
                    'name': 'Gemini Models',
                    'description': 'Models within this group: Gemini 4 Neon, Gemini 4 Carbon, Gemini 3.8 Flash',
                    'buckets': [
                        {
                            'id': 'gemini-weekly',
                            'name': 'Weekly Limit Remaining',
                            'window': 'weekly',
                            'remaining_fraction': 0.6312,
                            'reset_time': '2026-12-03T07:37:23Z',
                        },
                        {
                            'id': 'gemini-5h',
                            'name': 'Five Hour Limit Remaining',
                            'window': '5h',
                            'remaining_fraction': 0.6504,
                            'reset_time': '2026-11-27T22:56:58Z',
                        },
                    ],
                },
                {
                    'name': 'Claude and GPT models',
                    'description': 'Models within this group: Claude Opus 5.5, Claude Sonnet 5.5, GPT-OSS 120B',
                    'buckets': [
                        {
                            'id': '3p-weekly',
                            'name': 'Weekly Limit Remaining',
                            'window': 'weekly',
                            'remaining_fraction': 1,
                            'reset_time': '1970-01-01T00:00:00Z',
                        },
                        {
                            'id': '3p-5h',
                            'name': 'Five Hour Limit Remaining',
                            'window': '5h',
                            'remaining_fraction': 1,
                            'reset_time': '1970-01-01T00:00:00Z',
                        },
                    ],
                },
            ],
        },
    },
}


def make_fake_agy(directory: Path, name='fake-agy', payload=None, exit_code=0, stderr=''):
    p = directory / name
    if exit_code == 0 and payload is not None:
        raw = json.dumps(payload, ensure_ascii=False)
        p.write_text(f"#!/bin/sh\nif [ \"$1\" = \"-p\" ] && [ \"$2\" = \"/quota\" ]; then\ncat <<'EOF'\n{raw}\nEOF\nexit 0\nfi\nexit 2\n")
    else:
        p.write_text(f'#!/bin/sh\necho "{stderr or "agy quota failed"}" >&2\nexit {exit_code or 1}\n')
    p.chmod(0o755)
    return p


def pin_fake_clients(pool, agy_path: Path, http):
    b = agy_path.resolve()
    sig = f'{b}:{b.stat().st_mtime}'
    pool.set_gemini_clients(dict(sig=sig, pairs=[[http.good_client, 'GOCSPX-good']]))


class TestParseAgyQuota(unittest.TestCase):
    def test_sample_1416_parses_four_windows_and_1970_reset_empty(self):
        q = acct.parse_agy_quota(SAMPLE_1416)
        self.assertEqual(q['source'], 'agy_cli')
        self.assertEqual(len(q['models']), 4)
        self.assertEqual(len(q['groups']), 2)
        self.assertEqual(
            [m['name'] for m in q['models']],
            ['Gemini · 5 小时', 'Gemini · 每周', 'Claude/GPT · 5 小时', 'Claude/GPT · 每周'],
        )
        # remaining 取所有窗口里最低的（Gemini 每周 63.12%）
        self.assertAlmostEqual(q['remaining'], 0.6312, places=4)
        self.assertEqual(q['low_model'], 'Gemini · 每周')
        self.assertIsNotNone(q['reset'])
        # 1970 重置时间记为空（None）
        by_id = {m['id']: m for m in q['models']}
        self.assertIsNotNone(by_id['gemini-5h']['reset'])
        self.assertIsNotNone(by_id['gemini-weekly']['reset'])
        self.assertIsNone(by_id['3p-5h']['reset'])
        self.assertIsNone(by_id['3p-weekly']['reset'])
        # groups 结构里同样清洗了 1970 重置时间
        cg = next(g for g in q['groups'] if g['label'] == 'Claude/GPT')
        self.assertTrue(all(b['reset'] is None for b in cg['buckets']))
        # used_pct 走真实剩余值，占位值返回 None
        self.assertAlmostEqual(acct.used_pct({'platform': 'gemini', 'quota': q}), 36.9, places=1)
        placeholder = acct.parse_quota({'models': {'g1': {'displayName': 'Gemini 4', 'quotaInfo': {'remainingFraction': 1.0}}}})
        self.assertEqual(placeholder['source'], 'api_placeholder')
        self.assertIsNone(acct.used_pct({'platform': 'gemini', 'quota': placeholder}))

    def test_parse_invalid_or_error_status_returns_empty(self):
        self.assertIsNone(acct.parse_agy_quota({'status': 'ERROR', 'command': SAMPLE_1416['command']})['remaining'])
        self.assertIsNone(acct.parse_agy_quota('not json')['remaining'])
        self.assertIsNone(acct.parse_agy_quota({})['remaining'])


class TestGeminiQuotaBehavior(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, True))
        self.pool = acct.Pool(self.tmp / 'accounts')
        self.http = FakeHTTP()
        saved = acct.HTTP
        acct.HTTP = self.http
        self.addCleanup(setattr, acct, 'HTTP', saved)
        self.kc = FakeKeychain({'token': {'refresh_token': RT1, 'access_token': 'ya29.old'}, 'auth_method': 'consumer'})
        self.pool.set_gemini_clients(dict(sig='', pairs=[[self.http.good_client, 'GOCSPX-good']]))

    def test_quota_prefers_agy_cli_and_no_arg_works(self):
        agy = make_fake_agy(self.tmp, 'agy-ok', SAMPLE_1416)
        g = acct.Gemini(self.pool, str(agy), keychain=self.kc, token_file=self.tmp / 'none')
        q = g.quota()
        self.assertEqual(q['source'], 'agy_cli')
        self.assertAlmostEqual(q['remaining'], 0.6312, places=4)
        self.assertEqual(len(q['models']), 4)
        # 没调旧的 fetchAvailableModels
        self.assertFalse(any(c[1] == acct.CODE_ASSIST_MODELS_URL for c in self.http.calls))

    def test_check_preserves_previous_real_quota_on_agy_failure_and_timeout(self):
        agy_ok = make_fake_agy(self.tmp, 'agy-ok', SAMPLE_1416)
        pin_fake_clients(self.pool, agy_ok, self.http)
        g = acct.Gemini(self.pool, str(agy_ok), keychain=self.kc, token_file=self.tmp / 'none')
        a = g.import_local()
        self.assertEqual(a['quota']['source'], 'agy_cli')
        # 把上次真实额度的时间改到 25 分钟前
        old_at = time.time() - 25 * 60
        old_q = dict(a['quota'], at=old_at)
        self.pool.update(a['id'], quota=old_q)

        # 场景 1：agy 退出码非 0 -> 保留上一次真实值与时间戳，不被 api_placeholder 覆盖
        agy_bad = make_fake_agy(self.tmp, 'agy-bad', exit_code=1, stderr='boom')
        pin_fake_clients(self.pool, agy_bad, self.http)
        g.agy_exe = str(agy_bad)
        b = g.check(a['id'])
        self.assertEqual(b['quota']['source'], 'agy_cli')
        self.assertAlmostEqual(b['quota']['remaining'], 0.6312, places=4)
        self.assertEqual(b['quota']['at'], old_at)
        self.assertIn('保留上次额度', b['error'])

        # 场景 2：agy 超时 -> 同样保留上次真实值
        with patch.object(acct.subprocess, 'run', side_effect=acct.subprocess.TimeoutExpired('agy', 30)):
            c = g.check(a['id'])
        self.assertEqual(c['quota']['source'], 'agy_cli')
        self.assertEqual(c['quota']['at'], old_at)
        self.assertIn('超时', c['error'])
        self.assertIn('保留上次额度', c['error'])

    def test_no_previous_real_quota_marks_api_placeholder(self):
        agy_bad = make_fake_agy(self.tmp, 'agy-bad', exit_code=1, stderr='offline')
        pin_fake_clients(self.pool, agy_bad, self.http)
        g = acct.Gemini(self.pool, str(agy_bad), keychain=self.kc, token_file=self.tmp / 'none')
        a = g.import_local()
        self.assertEqual(a['quota']['source'], 'api_placeholder')
        self.assertIsNone(acct.used_pct(a))
        # 非本机活跃号不调用 agy
        agy_ok = make_fake_agy(self.tmp, 'agy-ok', SAMPLE_1416)
        pin_fake_clients(self.pool, agy_ok, self.http)
        g.agy_exe = str(agy_ok)
        b = g.import_rt(RT2, source='rt')
        self.assertEqual(b['quota']['source'], 'api_placeholder')


class TestStudioGeminiQuota(PlatBase):
    def setUp(self):
        super().setUp()
        self.http = FakeHTTP()
        saved = acct.HTTP
        acct.HTTP = self.http
        self.addCleanup(setattr, acct, 'HTTP', saved)
        self.saved_thr = studio.ACCOUNTS_THREADED
        studio.ACCOUNTS_THREADED = False
        self.addCleanup(setattr, studio, 'ACCOUNTS_THREADED', self.saved_thr)
        studio._ACCT_Q.clear()
        self.addCleanup(studio._ACCT_Q.clear)
        self.p = studio.pool()
        self.p.set_gemini_clients(dict(sig='', pairs=[[self.http.good_client, 'GOCSPX-good']]))
        self.kc = FakeKeychain({'token': {'refresh_token': RT1, 'access_token': 'ya29.old'}, 'auth_method': 'consumer'})

    def test_accounts_check_limits_and_fmt_account(self):
        agy_ok = make_fake_agy(self.tmp, 'agy-ok', SAMPLE_1416)
        pin_fake_clients(self.p, agy_ok, self.http)
        g = acct.Gemini(self.p, str(agy_ok), keychain=self.kc, token_file=self.tmp / 'none')
        saved = studio.gemini_ops
        self.addCleanup(setattr, studio, 'gemini_ops', saved)
        studio.gemini_ops = lambda: g

        a = studio.gemini_sync_local()
        self.assertEqual(a['quota']['source'], 'agy_cli')

        studio._ACCT_Q.clear()
        lim = studio.collect_limits()['gemini']
        self.assertEqual(lim['source'], 'agy_cli')
        self.assertEqual(len(lim['groups']), 2)
        self.assertEqual(len(lim['models']), 4)
        self.assertAlmostEqual(lim['remaining'], 0.6312, places=4)

        # session_limits_for 透传 source 与 groups
        slim = studio.session_limits_for(self.conn, {'id': 1}, 'gemini', {'gemini': lim})
        self.assertEqual(slim['source'], 'agy_cli')
        self.assertEqual(len(slim['groups']), 2)

        # fmt_account 按组展示
        line = studio.fmt_account(studio.cli_accounts('gemini')[0])
        self.assertIn('Gemini：5 小时剩 65% / 每周剩 63%', line)
        self.assertIn('Claude/GPT：5 小时剩 100% / 每周剩 100%', line)

        # 改回 20 分钟前并让 agy 失败：accounts_check 保留旧值并在结果里标出分钟数
        old_at = time.time() - 20 * 60
        self.p.update(a['id'], quota=dict(a['quota'], at=old_at))
        agy_fail = make_fake_agy(self.tmp, 'agy-fail', exit_code=1, stderr='boom')
        pin_fake_clients(self.p, agy_fail, self.http)
        g.agy_exe = str(agy_fail)
        res = dict(studio.accounts_check(force=True))
        self.assertIn('20 分钟前', res[a['id']])
        self.assertIn('保留旧值', res[a['id']])
        kept = self.p.get(a['id'])
        self.assertEqual(kept['quota']['source'], 'agy_cli')
        self.assertEqual(kept['quota']['at'], old_at)

        # 没有任何真实值时：fmt_account 显示「额度未知」，不显示 100%
        self.p.update(a['id'], quota=acct.parse_quota({'models': {'m1': {'displayName': 'Gemini 4', 'quotaInfo': {'remainingFraction': 1.0}}}}), error='')
        studio._ACCT_Q.clear()
        line_unk = studio.fmt_account(studio.cli_accounts('gemini')[0])
        self.assertIn('额度未知', line_unk)
        self.assertNotIn('100%', line_unk)
        lim_unk = studio.collect_limits()['gemini']
        self.assertEqual(lim_unk['source'], 'api_placeholder')

        # status_data 增 today_usage（含主脑 + 各项目今日 token 与费用汇总）
        today = studio.today()
        self.conn.execute(
            'INSERT INTO usage(day, film_id, camera_id, tokens_in, tokens_out, cache_read, cache_write, cost) '
            'VALUES(?, NULL, 1, 1000, 500, 0, 0, 0.05), (?, "builda", 2, 2000, 1000, 500, 0, 0.15)',
            (today, today),
        )
        self.conn.commit()
        st = studio.status_data(self.conn)
        self.assertEqual(st['today_usage']['tokens'], 5000)
        self.assertAlmostEqual(st['today_usage']['cost'], 0.2, places=4)


if __name__ == '__main__':
    unittest.main()
