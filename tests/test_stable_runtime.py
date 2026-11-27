"""#17 stable-runtime 测试：稳定运行快照、入口分流、发布与回滚、内容与代码分离、网页重启接口。

全部在临时 git 仓库与临时 MASHU_HOME 中运行，不触碰真实 state/，不重启真实服务。
"""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'mashu'))
import studio  # noqa: E402
import server  # noqa: E402


def _git(repo, *args):
    r = subprocess.run(
        ['git', '-C', str(repo), *args],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise AssertionError(f'git {args} failed: {r.stderr}\n{r.stdout}')
    return r.stdout.strip()


def _commit_all(repo, msg):
    _git(repo, 'add', '-A')
    _git(repo, '-c', 'user.email=test@mashu.local', '-c', 'user.name=Test', 'commit', '-q', '-m', msg)
    return _git(repo, 'rev-parse', 'HEAD')


def _build_temp_mashu_repo(base_dir):
    """复制一份轻量麻薯仓库到临时目录并初始化 git，用于隔离测试 worktree / release / 分流器。"""
    repo = (base_dir / 'repo').resolve()
    repo.mkdir(parents=True)
    for rel in ('bin', 'mashu', 'playbooks', 'web'):
        shutil.copytree(REPO_ROOT / rel, repo / rel, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(REPO_ROOT / 'start.sh', repo / 'start.sh')
    (repo / 'docs' / 'memory').mkdir(parents=True)
    (repo / 'docs' / 'indexes').mkdir(parents=True)
    (repo / 'docs' / 'references' / 'roles').mkdir(parents=True)
    (repo / 'docs' / 'workflows').mkdir(parents=True)
    (repo / 'docs' / 'indexes' / 'docs.md').write_text('# 知识地图\n')
    (repo / 'docs' / 'memory' / 'index.md').write_text('# 记忆索引\n')
    (repo / 'docs' / 'memory' / '人格.md').write_text('# 人格\n基础人格\n')
    (repo / 'docs' / 'memory' / '守则.md').write_text('# 守则\n初始守则-V1\n')
    (repo / 'docs' / 'memory' / '在办.md').write_text('# 在办\n')
    (repo / 'docs' / 'memory' / '用户.md').write_text('# 用户\n')
    (repo / 'docs' / 'memory' / '方针.md').write_text('# 方针\n')
    (repo / 'docs' / 'memory' / '复盘.md').write_text('# 复盘\n')
    (repo / '.gitignore').write_text('state/\n__pycache__/\n*.pyc\n')
    (repo / '.mashu').mkdir(parents=True)
    (repo / '.mashu' / 'check.sh').write_text('#!/usr/bin/env bash\nset -eu\necho "check ok"\n')
    (repo / '.mashu' / 'check.sh').chmod(0o755)
    (repo / 'bin' / 'mashu').chmod(0o755)
    (repo / 'bin' / 'mashu-dev').chmod(0o755)
    _git(repo, 'init', '-q', '-b', 'main')
    v1 = _commit_all(repo, 'init v1')
    return repo, v1


class StableRuntimeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mashu-rt-test-')).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, self.v1 = _build_temp_mashu_repo(self.tmp)
        self.home = self.tmp / 'state'
        self.home.mkdir()

        self._saved = {
            'HOME': studio.HOME,
            'CONTENT': studio.CONTENT,
            'BIN': studio.BIN,
            'PLAYBOOKS': studio.PLAYBOOKS,
            'KMAP': studio.KMAP,
            'MEMORY': studio.MEMORY,
            'MEMORY_INDEX': studio.MEMORY_INDEX,
        }
        studio.HOME = self.home
        studio.CONTENT = self.repo
        studio.BIN = self.repo / 'bin'
        studio.PLAYBOOKS = self.repo / 'playbooks'
        studio.KMAP = self.repo / 'docs'
        studio.MEMORY = self.repo / 'docs' / 'memory'
        studio.MEMORY_INDEX = studio.MEMORY / 'index.md'
        self.addCleanup(self._restore_studio)
        self.conn = studio.db()
        self.addCleanup(self.conn.close)

    def _restore_studio(self):
        for k, v in self._saved.items():
            setattr(studio, k, v)

    def _clean_env(self, extra=None):
        env = dict(os.environ)
        for k in ('MASHU_SESSION', 'MASHU_ROLE', 'MASHU_NAME', 'MASHU_FILM', 'MASHU_SEQ',
                  'MASHU_SEQ_DIR', 'MASHU_DEV', 'MASHU_MEMORY'):
            env.pop(k, None)
        env['MASHU_HOME'] = str(self.home)
        env['MASHU_CONTENT'] = str(self.repo)
        if extra:
            env.update(extra)
        return env

    def _run_cmd(self, argv, extra_env=None, stdin_text=None):
        return subprocess.run(
            argv, cwd=str(self.repo), env=self._clean_env(extra_env),
            input=stdin_text, capture_output=True, text=True, timeout=30,
        )


class TestStableRuntime(StableRuntimeBase):
    def test_ac1_broken_workspace_does_not_affect_snapshot_cli_and_hooks(self):
        """AC1：工作区把 studio.py 改坏（不提交），bin/mashu status 和 hooks 照常工作，bin/mashu-dev status 报错。"""
        restarts = []
        res = studio.release_runtime(self.conn, actor='executive', restart_fn=lambda: restarts.append(1))
        self.assertEqual(res['sha'], self.v1)
        self.assertEqual(len(restarts), 1)
        self.assertTrue((self.home / 'runtime' / 'mashu' / 'studio.py').is_file())

        # 在工作区里故意把 studio.py 改坏（不提交）
        (self.repo / 'mashu' / 'studio.py').write_text('def broken_syntax(:\n    pass\n')

        # 1) bin/mashu status 走快照，照常工作
        r = self._run_cmd([str(self.repo / 'bin' / 'mashu'), 'status', '--json'])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['runtime']['sha'], self.v1)
        self.assertTrue(payload['runtime']['dirty'])

        # 2) hooks 走快照，照常工作
        rh = self._run_cmd([str(self.repo / 'bin' / 'mashu'), 'hook-prompt'], stdin_text='{}')
        self.assertEqual(rh.returncode, 0, msg=rh.stderr)

        # 3) bin/mashu-dev status 跑工作区损坏代码，必须报错
        rd = self._run_cmd([str(self.repo / 'bin' / 'mashu-dev'), 'status'])
        self.assertNotEqual(rd.returncode, 0)
        self.assertIn('SyntaxError', rd.stderr)

    def test_ac2_check_failed_rejects_release_and_keeps_online_sha(self):
        """AC2：对单测失败的提交执行 release 被拒绝，线上 sha 不变，返回 code=check_failed。"""
        restarts = []
        studio.release_runtime(self.conn, actor='executive', restart_fn=lambda: restarts.append('v1'))

        # 提交一个 check 失败的版本 v2
        (self.repo / '.mashu' / 'check.sh').write_text('#!/usr/bin/env bash\necho "FAIL: broken unit test" >&2\nexit 1\n')
        (self.repo / '.mashu' / 'check.sh').chmod(0o755)
        v2 = _commit_all(self.repo, 'bad commit v2')
        self.assertNotEqual(v2, self.v1)

        with self.assertRaises(studio.MashuError) as ctx:
            studio.release_runtime(self.conn, actor='executive', restart_fn=lambda: restarts.append('v2'))
        self.assertEqual(ctx.exception.code, 'check_failed')
        self.assertIn('broken unit test', ctx.exception.data.get('check_output', ''))
        # 线上快照、runtime.json 和重启次数都未变化
        self.assertEqual(studio.runtime_info()['sha'], self.v1)
        self.assertEqual(studio.runtime_info()['ahead'], 1)
        self.assertEqual(studio.read_runtime_meta()['sha'], self.v1)
        self.assertEqual(restarts, ['v1'])

        # 通过 CLI --json 也返回 code=check_failed
        r = self._run_cmd([str(self.repo / 'bin' / 'mashu'), 'release', '--json'])
        self.assertNotEqual(r.returncode, 0)
        err_json = json.loads(r.stdout)
        self.assertFalse(err_json['ok'])
        self.assertEqual(err_json['code'], 'check_failed')
        self.assertEqual(_git(self.home / 'runtime', 'rev-parse', 'HEAD'), self.v1)

    def test_ac3_release_records_prev_sha_dry_run_and_rollback(self):
        """AC3：通过的提交 release 后切换快照并记录 prev_sha；--dry-run 不切换；--rollback 回到上一个（跳过 check）。"""
        restarts = []
        studio.release_runtime(self.conn, actor='executive', restart_fn=lambda: restarts.append('v1'))

        # 做出通过检查的新提交 v2
        (self.repo / 'mashu' / 'marker.txt').write_text('v2\n')
        v2 = _commit_all(self.repo, 'good commit v2')

        # 工作区留一个未提交的坏改动，不应影响发布已提交的 v2
        (self.repo / 'mashu' / 'uncommitted_bad.py').write_text('def oops(:\n')

        # dry-run：校验通过但不切换、不重启
        dr = studio.release_runtime(self.conn, dry_run=True, actor='assistant', restart_fn=lambda: restarts.append('dry'))
        self.assertTrue(dr['dry_run'])
        self.assertTrue(dr['check_passed'])
        self.assertFalse(dr['restarting'])
        self.assertEqual(_git(self.home / 'runtime', 'rev-parse', 'HEAD'), self.v1)
        self.assertEqual(restarts, ['v1'])

        # 正式发布 v2
        r2 = studio.release_runtime(self.conn, actor='assistant', reason='上线 v2', restart_fn=lambda: restarts.append('v2'))
        self.assertEqual(r2['sha'], v2)
        self.assertEqual(r2['prev_sha'], self.v1)
        self.assertEqual(_git(self.home / 'runtime', 'rev-parse', 'HEAD'), v2)
        self.assertFalse((self.home / 'runtime' / 'mashu' / 'uncommitted_bad.py').exists())
        meta = studio.read_runtime_meta()
        self.assertEqual(meta['sha'], v2)
        self.assertEqual(meta['prev_sha'], self.v1)
        self.assertEqual(meta['released_by'], 'assistant')
        self.assertTrue(meta['released_at'])
        self.assertEqual(restarts, ['v1', 'v2'])

        # 重复发布同一个 v2 不应把 prev_sha 覆盖成 v2
        studio.release_runtime(self.conn, actor='assistant', restart_fn=lambda: restarts.append('v2-again'))
        self.assertEqual(studio.read_runtime_meta()['prev_sha'], self.v1)

        # 回滚到上一个 sha（不跑 check）
        # 故意改坏 v1? 无需改 v1，验证 check_skipped=True 且回到 v1
        rb = studio.release_runtime(self.conn, rollback=True, actor='executive', restart_fn=lambda: restarts.append('rb'))
        self.assertEqual(rb['mode'], 'rollback')
        self.assertTrue(rb['check_skipped'])
        self.assertEqual(rb['sha'], self.v1)
        self.assertEqual(rb['prev_sha'], v2)
        self.assertEqual(_git(self.home / 'runtime', 'rev-parse', 'HEAD'), self.v1)
        self.assertEqual(studio.read_runtime_meta()['sha'], self.v1)
        self.assertEqual(studio.read_runtime_meta()['prev_sha'], v2)
        self.assertEqual(restarts[-1], 'rb')

        # 此时工作区 HEAD (v2) 领先线上快照 (v1) 1 个提交，status 第一行应体现
        rs = self._run_cmd([str(self.repo / 'bin' / 'mashu'), 'status'])
        self.assertEqual(rs.returncode, 0, msg=rs.stderr)
        first_line = rs.stdout.splitlines()[0]
        self.assertIn(f'线上版本 {self.v1[:7]}', first_line)
        self.assertIn('工作区已领先 1 个提交', first_line)

    def test_ac4_docs_live_from_workspace_prompts_pinned_to_snapshot(self):
        """AC4：改 docs/memory/守则.md 无需发布立即可见；改 mashu/prompts/crew.md 需发布后才生效。"""
        studio.release_runtime(self.conn, actor='executive', restart=False)

        # 修改工作区的守则（不提交、不发布）和 crew.md（不提交、不发布）
        (self.repo / 'docs' / 'memory' / '守则.md').write_text('# 守则\n实时新守则-UNRELEASED\n')
        (self.repo / 'mashu' / 'prompts' / 'crew.md').write_text('工作区新版执行者提示词-UNRELEASED\n')

        probe = (
            'import sys, json; '
            'sys.path.insert(0, sys.argv[1]); '
            'import studio; '
            'print(json.dumps({'
            '"code": str(studio.CODE), '
            '"content": str(studio.CONTENT), '
            '"profile": studio.brain_profile("assistant"), '
            '"crew_prompt": (studio.PROMPTS / "crew.md").read_text()'
            '}, ensure_ascii=False))'
        )
        runtime_mashu = str(self.home / 'runtime' / 'mashu')
        p1 = self._run_cmd([sys.executable, '-c', probe, runtime_mashu])
        self.assertEqual(p1.returncode, 0, msg=p1.stderr)
        d1 = json.loads(p1.stdout)
        self.assertEqual(Path(d1['code']).resolve(), (self.home / 'runtime').resolve())
        self.assertEqual(Path(d1['content']).resolve(), self.repo.resolve())
        self.assertIn('实时新守则-UNRELEASED', d1['profile'])
        self.assertNotIn('工作区新版执行者提示词-UNRELEASED', d1['crew_prompt'])

        # 提交 crew.md 并发布后，快照里的 prompts 生效
        _commit_all(self.repo, 'update crew prompt')
        studio.release_runtime(self.conn, actor='executive', restart=False)
        p2 = self._run_cmd([sys.executable, '-c', probe, runtime_mashu])
        self.assertEqual(p2.returncode, 0, msg=p2.stderr)
        d2 = json.loads(p2.stdout)
        self.assertIn('工作区新版执行者提示词-UNRELEASED', d2['crew_prompt'])

    def test_ac5_existing_session_path_and_hooks_auto_switch_to_snapshot(self):
        """AC5：切换前已启动的会话（PATH 与 hooks 指向工作区 bin/mashu）在发布后自动走快照。"""
        # 切换前：生成 hooks 配置，记录工作区 bin 路径
        settings_path = studio.hooks_settings()
        settings = json.loads(settings_path.read_text())
        expected_bin = str(self.repo / 'bin' / 'mashu')
        self.assertIn(expected_bin, settings['statusLine']['command'])

        # 模拟已有会话的 PATH（最前面是工作区 bin/）
        sess_env = {'PATH': f'{self.repo / "bin"}:{os.environ.get("PATH", "/usr/bin:/bin")}'}

        # 切换前：快照不存在，走工作区
        before = self._run_cmd(['mashu', 'status', '--json'], extra_env=sess_env)
        self.assertEqual(before.returncode, 0, msg=before.stderr)
        self.assertEqual(json.loads(before.stdout)['runtime']['sha'], self.v1)

        # 首次发布：创建 state/runtime/
        studio.release_runtime(self.conn, actor='executive', restart=False)

        # 工作区改坏 studio.py（不提交）；不修改已有会话的 PATH 和 claude-settings.json
        (self.repo / 'mashu' / 'studio.py').write_text('raise RuntimeError("workspace broken")\n')

        # 原会话继续用 PATH 里的 mashu 和 hooks 命令，自动走快照，不受工作区影响
        after_cli = self._run_cmd(['mashu', 'status', '--json'], extra_env=sess_env)
        self.assertEqual(after_cli.returncode, 0, msg=after_cli.stderr)
        self.assertEqual(json.loads(after_cli.stdout)['runtime']['sha'], self.v1)

        hook_cmd = settings['hooks']['UserPromptSubmit'][0]['hooks'][0]['command']
        after_hook = self._run_cmd(['bash', '-c', hook_cmd], extra_env={**sess_env, 'MASHU_SESSION': '99999'}, stdin_text='{}')
        self.assertEqual(after_hook.returncode, 0, msg=after_hook.stderr)

    def test_permissions_and_restart_server_modes(self):
        """权限：仅主脑和终端用户可发布/重启，子脑和执行者被拒；restart-server 保留 --confirm 且支持 --same。"""
        # 插入不同角色的机位
        self.conn.execute(
            "INSERT INTO cameras(id, role, name, state) VALUES(101, 'crew', '测试员', 'idle')"
        )
        self.conn.execute(
            "INSERT INTO cameras(id, role, name, state) VALUES(102, 'producer', '子脑甲', 'idle')"
        )
        self.conn.execute(
            "INSERT INTO cameras(id, role, name, state) VALUES(103, 'assistant', '麻薯', 'idle')"
        )
        self.conn.commit()

        def call_as(cid, argv):
            old = os.environ.get('MASHU_SESSION')
            if cid is None:
                os.environ.pop('MASHU_SESSION', None)
            else:
                os.environ['MASHU_SESSION'] = str(cid)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    studio.main(argv)
            finally:
                if old is None:
                    os.environ.pop('MASHU_SESSION', None)
                else:
                    os.environ['MASHU_SESSION'] = old
            return buf.getvalue()

        for cid in (101, 102):
            with self.assertRaises(studio.CliExit) as ctx:
                call_as(cid, ['release', '--json'])
            self.assertEqual(ctx.exception.exit_code, studio.EXIT_RULE)
            self.assertEqual(ctx.exception.payload['code'], 'permission_denied')

        # restart-server 不带 --confirm 仍返回退出码 4
        with self.assertRaises(studio.CliExit) as ctx:
            call_as(103, ['restart-server', '--reason', '测试', '--json'])
        self.assertEqual(ctx.exception.exit_code, studio.EXIT_CONFIRM)

        # 主脑带 --confirm 走 release
        with mock.patch.object(studio, 'schedule_server_restart') as m_restart:
            out = call_as(103, ['restart-server', '--confirm', '--reason', '发布', '--json'])
            data = json.loads(out)
            self.assertTrue(data['ok'])
            self.assertEqual(data['sha'], self.v1)
            self.assertEqual(m_restart.call_count, 1)

            # --same --confirm 不跑 check、不换版本，只重启
            (self.repo / '.mashu' / 'check.sh').write_text('#!/usr/bin/env bash\nexit 1\n')
            _commit_all(self.repo, 'broken head')
            out_same = call_as(103, ['restart-server', '--same', '--confirm', '--json'])
            data_same = json.loads(out_same)
            self.assertTrue(data_same['ok'])
            self.assertEqual(data_same['mode'], 'same')
            self.assertEqual(data_same['sha'], self.v1)
            self.assertEqual(m_restart.call_count, 2)

    def test_ac6_web_api_runtime_and_restart_error_handling(self):
        """AC6：/api/state 含 runtime 字段（sha/ahead/dirty）；/api/restart-server 失败返回原因且不卡住，成功触发重启。"""
        studio.release_runtime(self.conn, actor='executive', restart=False)

        # 启动隔离 HTTP 测试服务（随机空闲端口，不启调度器）
        srv = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        port = srv.server_address[1]
        token = 'test-token-stable-runtime'
        old_tok, old_port = server.Handler.token, server.Handler.port
        server.Handler.token = token
        server.Handler.port = port
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()

        def cleanup():
            srv.shutdown()
            srv.server_close()
            server.Handler.token = old_tok
            server.Handler.port = old_port

        self.addCleanup(cleanup)

        def req(method, path, body=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
            headers = {'Host': f'127.0.0.1:{port}', 'X-Mashu-Token': token, 'Content-Type': 'application/json'}
            conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode()
            conn.close()
            return resp.status, json.loads(raw)

        # 1) /api/state 包含 runtime 全部字段
        code, st = req('GET', '/api/state')
        self.assertEqual(code, 200)
        self.assertIn('runtime', st)
        rt = st['runtime']
        self.assertEqual(set(rt.keys()), {'sha', 'released_at', 'released_by', 'ahead', 'dirty'})
        self.assertEqual(rt['sha'], self.v1)
        self.assertEqual(rt['ahead'], 0)

        # 2) 提交一个 check 失败的版本，/api/state 显示 ahead=1，/api/restart-server 返回 400 + check_failed
        (self.repo / '.mashu' / 'check.sh').write_text('#!/usr/bin/env bash\necho "unit test boom" >&2\nexit 1\n')
        (self.repo / '.mashu' / 'check.sh').chmod(0o755)
        _commit_all(self.repo, 'bad commit')

        code, st2 = req('GET', '/api/state')
        self.assertEqual(code, 200)
        self.assertEqual(st2['runtime']['ahead'], 1)

        with mock.patch.object(server, 'restart_later') as m_restart:
            code, err = req('POST', '/api/restart-server', {})
            self.assertEqual(code, 400)
            self.assertFalse(err['ok'])
            self.assertEqual(err['code'], 'check_failed')
            self.assertIn('unit test boom', err.get('check_output', '') + err.get('error', ''))
            self.assertEqual(m_restart.call_count, 0)

            # 3) same=True 跳过检查，直接重启
            code, ok_same = req('POST', '/api/restart-server', {'same': True})
            self.assertEqual(code, 200)
            self.assertTrue(ok_same['ok'])
            self.assertEqual(ok_same['mode'], 'same')
            self.assertEqual(m_restart.call_count, 1)

            # 4) 修复 check 并提交，/api/restart-server 发布成功并触发重启
            (self.repo / '.mashu' / 'check.sh').write_text('#!/usr/bin/env bash\necho "all good"\n')
            (self.repo / '.mashu' / 'check.sh').chmod(0o755)
            v3 = _commit_all(self.repo, 'fixed commit')

            code, ok_rel = req('POST', '/api/restart-server', {})
            self.assertEqual(code, 200)
            self.assertTrue(ok_rel['ok'])
            self.assertEqual(ok_rel['sha'], v3)
            self.assertEqual(m_restart.call_count, 2)

        # 5) 校验 start.sh 语法与快照路径选择逻辑（不执行 tmux）
        chk_sh = subprocess.run(['bash', '-n', str(REPO_ROOT / 'start.sh')], capture_output=True, text=True)
        self.assertEqual(chk_sh.returncode, 0, msg=chk_sh.stderr)
        sh_text = (REPO_ROOT / 'start.sh').read_text()
        self.assertIn('runtime/mashu/server.py', sh_text)
        self.assertIn('MASHU_CONTENT', sh_text)

    def test_legacy_snapshot_fallback_reads_workspace_docs_and_start_sh_auto_upgrades(self):
        """旧快照（比分流器旧、不支持 MASHU_CONTENT / release）时：docs 仍从工作区读，且 start.sh restart 自动升级。"""
        # 构造一个独立临时仓库：v_legacy 模拟 #17 之前的旧代码（不含 MASHU_CONTENT / release_runtime），v_new 是当前新版代码
        legacy_tmp = self.tmp / 'legacy_case'
        repo, v_new = _build_temp_mashu_repo(legacy_tmp)
        home = repo / 'state'
        home.mkdir()

        # 基于 v_new 做一个独立的 legacy 分支/提交，把 studio.py 与 server.py 替换成无 MASHU_CONTENT/release 的旧版占位
        _git(repo, 'checkout', '-q', '-b', 'legacy-base')
        (repo / 'mashu' / 'studio.py').write_text(
            '#!/usr/bin/env python3\n'
            'import sys\n'
            'from pathlib import Path\n'
            'CODE = Path(__file__).resolve().parent.parent\n'
            'KMAP = CODE / "docs"\n'
            'if len(sys.argv) > 1 and sys.argv[1] == "status":\n'
            '    print("LEGACY_SNAPSHOT_RAN")\n'
            '    sys.exit(0)\n'
            'sys.stderr.write("legacy studio: unsupported command\\n")\n'
            'sys.exit(2)\n'
        )
        (repo / 'mashu' / 'server.py').write_text('# legacy server without release support\n')
        v_legacy = _commit_all(repo, 'legacy pre-17 commit')

        # 切回 main（含 #17 新版代码），并把 state/runtime 建在旧提交 v_legacy 上
        _git(repo, 'checkout', '-q', 'main')
        rdir = home / 'runtime'
        _git(repo, 'worktree', 'add', '--detach', str(rdir), v_legacy)
        (home / 'runtime.json').write_text(json.dumps({
            'sha': v_legacy, 'released_at': '2026-11-27T14:29:00-05:00',
            'released_by': 'executive', 'prev_sha': '', 'reason': 'legacy',
        }))

        # 在工作区修改守则与在办（不提交），旧快照里仍是旧内容
        (repo / 'docs' / 'memory' / '守则.md').write_text('# 守则\n工作区实时守则-LEGACY-FALLBACK\n')
        (repo / 'docs' / 'memory' / '在办.md').write_text('# 在办\n工作区实时在办-LEGACY-FALLBACK\n')

        env = self._clean_env({'MASHU_HOME': str(home), 'MASHU_CONTENT': str(repo)})

        # 1) 分流器检测到旧快照不支持 MASHU_CONTENT，自动回退工作区代码：
        #    不跑旧快照，且 release --dry-run 可直接执行，hook-session / docs 也从工作区读
        r_status = subprocess.run(
            [str(repo / 'bin' / 'mashu'), 'status', '--json'],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r_status.returncode, 0, msg=r_status.stderr)
        st = json.loads(r_status.stdout)
        self.assertTrue(st['ok'])
        self.assertEqual(st['runtime']['sha'], v_legacy)

        r_dry = subprocess.run(
            [str(repo / 'bin' / 'mashu'), 'release', '--dry-run', '--json'],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r_dry.returncode, 0, msg=r_dry.stderr)
        dry_data = json.loads(r_dry.stdout)
        self.assertTrue(dry_data['ok'])
        self.assertEqual(dry_data['sha'], v_new)

        # 验证走工作区 bin/mashu 触发的 hook-session 读的是工作区 docs/memory 与知识地图路径，而非旧快照
        import sqlite3
        lconn = sqlite3.connect(str(home / 'studio.db'))
        lconn.execute("INSERT INTO cameras(id, role, name, state) VALUES(201, 'assistant', '麻薯', 'idle')")
        lconn.commit()
        lconn.close()
        r_hook = subprocess.run(
            [str(repo / 'bin' / 'mashu'), 'hook-session'],
            cwd=str(repo), env={**env, 'MASHU_SESSION': '201'}, input='{}',
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r_hook.returncode, 0, msg=r_hook.stderr)
        self.assertIn('工作区实时在办-LEGACY-FALLBACK', r_hook.stdout)
        self.assertIn(str(repo / 'docs' / 'indexes' / 'docs.md'), r_hook.stdout)
        self.assertNotIn(str(rdir / 'docs'), r_hook.stdout)

        # 2) 工作区 start.sh restart 检测到旧快照不支持 release_runtime/MASHU_CONTENT，自动发布升级到 v_new 后再启动新快照
        fake_bin = legacy_tmp / 'fake_bin'
        fake_bin.mkdir()
        tmux_log = legacy_tmp / 'tmux.log'
        (fake_bin / 'tmux').write_text(
            '#!/usr/bin/env bash\n'
            f'echo "$*" >> "{tmux_log}"\n'
            'case "${1:-}" in\n'
            '  has-session) exit 0 ;;\n'
            '  list-windows) exit 0 ;;\n'
            '  *) exit 0 ;;\n'
            'esac\n'
        )
        (fake_bin / 'tmux').chmod(0o755)
        (fake_bin / 'claude').write_text('#!/usr/bin/env bash\nexit 0\n')
        (fake_bin / 'claude').chmod(0o755)
        (fake_bin / 'curl').write_text('#!/usr/bin/env bash\nexit 0\n')
        (fake_bin / 'curl').chmod(0o755)

        sh_env = dict(env)
        sh_env['PATH'] = f'{fake_bin}:{os.environ.get("PATH", "/usr/bin:/bin")}'
        sh_env['MASHU_TMUX'] = 'mashu-test-isolated'
        sh_env['NO_OPEN'] = '1'
        r_restart = subprocess.run(
            ['bash', str(repo / 'start.sh'), 'restart'],
            cwd=str(repo), env=sh_env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(r_restart.returncode, 0, msg=r_restart.stderr + '\n' + r_restart.stdout)
        self.assertEqual(_git(rdir, 'rev-parse', 'HEAD'), v_new)
        meta = json.loads((home / 'runtime.json').read_text())
        self.assertEqual(meta['sha'], v_new)
        self.assertEqual(meta['prev_sha'], v_legacy)
        logged = tmux_log.read_text()
        self.assertIn(str(rdir / 'mashu' / 'server.py'), logged)
        self.assertIn(f"MASHU_CONTENT='{repo}'", logged)

        # 3) 升级后，即便历史会话直接调 state/runtime/bin/mashu 且未传 MASHU_CONTENT，仍自动归一化到工作区并读工作区 docs
        bare_env = self._clean_env({'MASHU_HOME': str(home)})
        bare_env.pop('MASHU_CONTENT', None)
        probe_rt = (
            'import sys, json; sys.path.insert(0, sys.argv[1]); import studio; '
            'print(json.dumps({"content": str(studio.CONTENT), "profile": studio.brain_profile("assistant")}, ensure_ascii=False))'
        )
        # 先验证 state/runtime/bin/mashu 正常运行 status
        r_rt_bin = subprocess.run(
            [str(rdir / 'bin' / 'mashu'), 'status', '--json'],
            cwd=str(rdir), env=bare_env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r_rt_bin.returncode, 0, msg=r_rt_bin.stderr)
        self.assertEqual(json.loads(r_rt_bin.stdout)['runtime']['sha'], v_new)
        # 再验证通过 state/runtime/mashu 加载且无 MASHU_CONTENT 时，仍能自动识别工作区读到最新守则
        r_rt_docs = subprocess.run(
            [sys.executable, '-c', probe_rt, str(rdir / 'mashu')],
            cwd=str(rdir), env=bare_env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r_rt_docs.returncode, 0, msg=r_rt_docs.stderr)
        rt_data = json.loads(r_rt_docs.stdout)
        self.assertEqual(Path(rt_data['content']).resolve(), repo.resolve())
        self.assertIn('工作区实时守则-LEGACY-FALLBACK', rt_data['profile'])


if __name__ == '__main__':
    unittest.main()


class TestRuntimeDataDirGuard(StableRuntimeBase):
    """2026-11-27 事故：网页「重启服务」把快照切到 #17 之前的旧提交，旧代码把数据目录认成 state/runtime/state，新建空库。"""

    def _legacy_commit(self):
        _git(self.repo, 'checkout', '-q', '-b', 'legacy-base')
        (self.repo / 'mashu' / 'studio.py').write_text(
            'from pathlib import Path\nCODE = Path(__file__).resolve().parent.parent\nHOME = CODE / "state"\n')
        (self.repo / 'mashu' / 'server.py').write_text('# legacy server\n')
        legacy = _commit_all(self.repo, 'legacy pre-17')
        _git(self.repo, 'checkout', '-q', 'main')
        return legacy

    def test_release_and_rollback_refuse_legacy_commit(self):
        legacy = self._legacy_commit()
        studio.release_runtime(self.conn, actor='executive', restart_fn=lambda: None)
        with self.assertRaises(studio.MashuError) as cm:
            studio.release_runtime(self.conn, sha=legacy, actor='executive', restart_fn=lambda: None)
        self.assertEqual(cm.exception.code, 'runtime_incompatible')
        # 上一版记录指向旧提交时，回滚也必须拒绝，快照保持不变
        meta = studio.read_runtime_meta()
        meta['prev_sha'] = legacy
        studio.write_runtime_meta(meta)
        restarts = []
        with self.assertRaises(studio.MashuError) as cm2:
            studio.release_runtime(self.conn, rollback=True, actor='executive', restart_fn=lambda: restarts.append(1))
        self.assertEqual(cm2.exception.code, 'runtime_incompatible')
        self.assertEqual(_git(self.home / 'runtime', 'rev-parse', 'HEAD'), self.v1)
        self.assertEqual(restarts, [])

    def test_home_inside_runtime_snapshot_is_redirected_to_real_state(self):
        bad = self.repo / 'state' / 'runtime' / 'state'
        bad.mkdir(parents=True)
        env = self._clean_env({'MASHU_HOME': str(bad), 'MASHU_CONTENT': str(self.repo)})
        r = subprocess.run(
            [sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); import studio; print(studio.HOME)',
             str(self.repo / 'mashu')],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(Path(r.stdout.strip()).resolve(), (self.repo / 'state').resolve())
