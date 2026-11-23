"""片场安全员（risky）按命令结构判定：mashu-studio #1。

例子编号 B01…B24（必须拦）/ A01…A18（必须放）对应 .mashu/features/1-safety-guard-narrow/docs/requirement.md §4。

  python3 -m unittest tests.test_safety -v
"""
import shlex
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import Base, studio  # noqa: E402

F = dict(path='/x/proj', safety=dict(protected_branches=['main', 'master', 'release/*', 'staging/*', 'production'],
                                     extra_patterns=[]))

# (编号, AC, 命令, 闸门)
BLOCK = [
    ('B01', 'AC-5', 'git push origin main', 'prod'),
    ('B02', 'AC-5', 'git push origin HEAD:release/1.2', 'prod'),
    ('B03', 'AC-5', 'git -C /x/proj push upstream refs/heads/master', 'prod'),
    ('B04a', 'AC-5', 'git push --force origin feat/x', 'irreversible'),
    ('B04b', 'AC-5', 'git push origin +feat/x', 'irreversible'),
    ('B04c', 'AC-5', 'git push -uf origin feat/x', 'irreversible'),
    ('B04d', 'AC-5', 'git push --force-with-lease origin feat/x', 'irreversible'),
    ('B04e', 'AC-5', 'git push --mirror backup', 'irreversible'),
    ('B04f', 'AC-5', 'git push --all origin', 'prod'),
    ('B04g', 'AC-5', 'git push origin --delete main', 'prod'),
    ('B04h', 'AC-5', 'git -c user.name=x push origin main:production', 'prod'),
    ('B05', 'AC-3', 'mashu say 好 && git push origin main', 'prod'),
    ('B06', 'AC-3', 'cd /x/proj; git status | cat\ngit push origin main', 'prod'),
    ('B06b', 'AC-3', '(cd /x/proj && git push origin main)', 'prod'),
    ('B06c', 'AC-3', '{ echo hi; git push origin main; }', 'prod'),
    ('B06d', 'AC-3', 'make test || git push origin main', 'prod'),
    ('B06e', 'AC-3', 'sleep 1 & git push origin main', 'prod'),
    ('B07', 'AC-4', 'mashu say "进展 $(git push origin main)"', 'prod'),
    ('B07b', 'AC-4', 'mashu say "进展 $(echo "$(git push origin main)")"', 'prod'),
    ('B07c', 'AC-4', 'diff <(git push origin main) b.txt', 'prod'),
    ('B07d', 'AC-4', 'cat > notes.md <<EOF\n进展 $(git push origin main)\nEOF', 'prod'),
    ('B08', 'AC-4', 'mashu ask "要不要跑 `./deploy.sh prod`"', 'prod'),
    ('B09a', 'AC-4', 'bash -c "git push origin main"', 'prod'),
    ('B09b', 'AC-4', "sh -c 'git push origin main'", 'prod'),
    ('B09c', 'AC-4', 'eval "git push origin main"', 'prod'),
    ('B09d', 'AC-4', "bash -lc 'git push origin main'", 'prod'),
    ('B10', 'AC-4', "ssh build@ci 'cd app && git push origin main'", 'prod'),
    ('B10b', 'AC-4', "ssh -p 2222 -i key build@ci git push origin main", 'prod'),
    ('B10c', 'AC-4', "ssh build@ci <<'EOF'\ngit push origin main\nEOF", 'prod'),
    ('B11', 'AC-4', "bash <<'EOF'\ngit push origin main\nEOF", 'prod'),
    ('B11b', 'AC-4', "cat <<'EOF' | bash\ngit push origin main\nEOF", 'prod'),
    ('B11c', 'AC-4', "echo 'git push origin main' | sh", 'prod'),
    ('B11d', 'AC-4', 'bash <<< "git push origin main"', 'prod'),
    ('B12a', 'AC-4', 'sudo -u ops ./deploy.sh production', 'prod'),
    ('B12b', 'AC-6', 'ENV=prod make deploy', 'prod'),
    ('B12c', 'AC-4', 'env ENV=prod ./deploy.sh', 'prod'),
    ('B12d', 'AC-4', 'nohup timeout 60 git push origin main', 'prod'),
    ('B12e', 'AC-4', 'echo origin | xargs -I R git push R main', 'prod'),
    ('B12f', 'AC-4', "find . -name x -exec git push origin main \\;", 'prod'),
    ('B12g', 'AC-4', 'time nice -n 5 command git push origin main', 'prod'),
    ('B13', 'AC-6', 'kubectl apply -n prod -f a.yaml', 'prod'),
    ('B13b', 'AC-6', 'kubectl --context production rollout restart deploy/api', 'prod'),
    ('B14', 'AC-6', 'helm upgrade api ./chart --kube-context production', 'prod'),
    ('B15a', 'AC-6', './scripts/deploy.sh prod', 'prod'),
    ('B15b', 'AC-6', 'npm run deploy:prod', 'prod'),
    ('B15c', 'AC-6', 'python3 scripts/release.py --env production', 'prod'),
    ('B15d', 'AC-6', 'bash scripts/deploy.sh prod', 'prod'),
    ('B15e', 'AC-6', 'vercel --prod', 'prod'),
    ('B16a', 'AC-6', 'cd deploy/prod && ./deploy.sh', 'prod'),
    ('B16b', 'AC-6', 'export ENV=production; make deploy', 'prod'),
    ('B17', 'AC-6', 'terraform apply -var-file=prod.tfvars', 'prod'),
    ('B17b', 'AC-6', 'terraform -chdir=envs/prod destroy', 'prod'),
    ('B18a', 'AC-7', "ssh ops@prod-web-1 'sudo systemctl restart api'", 'prod'),
    ('B18b', 'AC-7', 'rsync -av dist/ web@prod-1:/srv/app', 'prod'),
    ('B18c', 'AC-7', 'ssh ops@prod-web-1', 'prod'),
    ('B18d', 'AC-7', 'scp app.tar ops@prod-web-1:/tmp/', 'prod'),
    ('B19', 'AC-7', 'curl -X POST https://api.prod.example.com/v1/cache/flush', 'prod'),
    ('B19b', 'AC-7', "curl -d 'a=1' https://api.prod.example.com/v1/x", 'prod'),
    ('B19c', 'AC-7', 'http DELETE https://api.prod.example.com/v1/x', 'prod'),
    ('B19d', 'AC-7', 'wget --method=PUT https://api.prod.example.com/v1/x', 'prod'),
    ('B23a', 'AC-8', 'gh pr merge 12', 'prod'),
    ('B23b', 'AC-8', 'glab mr merge 3', 'prod'),
    ('B23c', 'AC-8', 'rm -rf /x/proj', 'irreversible'),
    ('B23d', 'AC-8', 'psql -c "DROP TABLE users"', 'irreversible'),
    ('B23e', 'AC-8', 'rm -r -f /x/proj/', 'irreversible'),
    ('B23f', 'AC-8', 'mysql app <<EOF\ndrop database app;\nEOF', 'irreversible'),
    ('B23g', 'AC-8', 'curl -X PUT https://gitlab.example.com/api/v4/projects/1/merge_requests/7/merge', 'prod'),
    ('B24', 'AC-12', 'mashu say "prod deploy', 'prod'),
]

# 本项目（麻薯电脑秘书自己）的 extra_patterns
BLOCK_SELF = [
    ('B20a', 'AC-9', './start.sh restart', 'irreversible'),
    ('B20b', 'AC-9', 'cd ~/workspace/mashu_studio && ./start.sh stop', 'irreversible'),
    ('B21a', 'AC-9', 'kill $(pgrep -f server.py)', 'irreversible'),
    ('B21b', 'AC-9', 'tmux kill-server', 'irreversible'),
    ('B22', 'AC-9', "mashu say ok; bash -c './start.sh restart'", 'irreversible'),
    ('B22b', 'AC-9', 'mashu say "好 $(./start.sh restart)"', 'irreversible'),
    ('B22c', 'AC-9', "sudo ./start.sh stop", 'irreversible'),
]

# (编号, AC, 命令)
ALLOW = [
    ('A01', 'AC-1', 'mashu ask --gate prod "上线正式环境：跑 ./deploy.sh production，影响 api，回滚 helm rollback" '
                    '--summary "上线" --options "审批|不批" --recommend 审批 --why "已测"'),
    ('A02a', 'AC-1', 'mashu pitch shop "正式环境发布流水线改造：deploy 走 jenkins pipeline"'),
    ('A02b', 'AC-1', 'mashu memo shop "合并后 git push origin main 再 kubectl apply -n prod"'),
    ('A03b', 'AC-1', 'bin/mashu say "prod deploy：git push origin main"'),
    ('A03c', 'AC-1', './bin/mashu tell "rm -rf /x/proj 不要做；drop table 也不要"'),
    ('A04a', 'AC-1', 'python3 mashu/studio.py answer 3 "签字：可以 deploy prod"'),
    ('A04b', 'AC-1', 'mashu pass 4 "release prod 交给出品人"'),
    ('A04c', 'AC-1', 'mashu decide 5 "正式环境 发布 走 helm upgrade --kube-context production"'),
    ('A04d', 'AC-1', 'mashu tell "deploy prod 前要先 git push --force？不会"'),
    ('A04e', 'AC-1', "mashu feature handoff --progress 'B01 git push origin main 已拦' --next 'ssh ops@prod-web-1'"),
    ('A05', 'AC-4', "mashu say '$(git push origin main)'"),
    ('A05b', 'AC-4', "mashu say 'run `./deploy.sh prod`'"),
    ('A05c', 'AC-4', 'mashu say "价格 \\$(git push origin main)"'),
    ('A06', 'AC-3', 'mashu ask "要不要 deploy prod" && mashu tell "正式 发布 等签字"'),
    ('A06b', 'AC-3', 'cd /x/proj && mashu say "ok" | cat; echo "prod deploy"'),
    ('A07a', 'AC-2', 'grep -rn "git push origin main" tests/'),
    ('A07b', 'AC-2', "rg 'kubectl apply -n prod'"),
    ('A07c', 'AC-2', 'grep -n "PROD_WORD\\|DEPLOY_WORD" mashu/studio.py | head'),
    ('A08a', 'AC-2', 'cat docs/release.md'),
    ('A08b', 'AC-2', "sed -n '1,40p' scripts/deploy-prod.sh"),
    ('A08c', 'AC-2', 'head deploy.sh'),
    ('A08d', 'AC-2', 'ls deploy/prod'),
    ('A09a', 'AC-2', 'echo "prod deploy checklist" > notes.md'),
    ('A09b', 'AC-2', "printf '正式环境 发布\\n'"),
    ('A10', 'AC-2', "cat > docs/requirement.md <<'EOF'\ngit push origin main\n./deploy.sh prod\nEOF"),
    ('A10b', 'AC-2', "cat > docs/x.md <<-\"EOF\"\n\tgit push origin main $(./deploy.sh prod)\n\tEOF\necho done"),
    ('A10c', 'AC-2', "tee docs/x.md <<'EOF' >/dev/null\ndrop table users; git push --force\nEOF"),
    ('A11a', 'AC-2', 'git commit -m "fix prod deploy script"'),
    ('A11b', 'AC-2', 'git log --grep "release prod"'),
    ('A12a', 'AC-5', 'git push origin feat/safety'),
    ('A12b', 'AC-5', 'git push -u origin mainline-fix'),
    ('A12c', 'AC-5', 'git push origin main-fix:feat/main'),
    ('A13a', 'AC-6', 'kubectl get pods -n prod'),
    ('A13b', 'AC-6', 'helm list --kube-context production'),
    ('A13c', 'AC-6', 'terraform plan -var-file=prod.tfvars'),
    ('A13d', 'AC-6', 'kubectl rollout status deploy/api -n prod'),
    ('A14a', 'AC-6', './scripts/deploy.sh staging'),
    ('A14b', 'AC-6', 'make deploy'),
    ('A14c', 'AC-6', 'cd /x/product && make test'),
    ('A15a', 'AC-7', "ssh ops@prod-web-1 'tail -n 100 /var/log/app.log'"),
    ('A15b', 'AC-7', 'curl https://api.prod.example.com/health'),
    ('A15c', 'AC-7', 'scp ops@prod-web-1:/var/log/app.log .'),
    ('A15d', 'AC-7', 'curl -X POST https://api.staging.example.com/v1/x'),
    ('A16', 'AC-8', 'grep -n "drop table" migrations/*.sql'),
    ('A16b', 'AC-8', 'rm -rf /x/proj/build'),
    ('A17', 'AC-3', '# git push origin main'),
    ('A17b', 'AC-3', 'ls  # git push origin main'),
    ('A18a', 'AC-13', 'python3 -m unittest discover -s tests'),
    ('A18b', 'AC-13', 'ls -la'),
    ('A18c', 'AC-13', "python3 - <<'EOF'\nprint('deploy to production')\nEOF"),
]

ALLOW_SELF = [
    ('A03a', 'AC-9', '/opt/mashu_studio/bin/mashu note 1 "别跑 ./start.sh restart，也别 tmux kill-server"'),
    ('A07d', 'AC-9', 'grep -n "start.sh restart" tests/'),
    ('A07e', 'AC-9', "rg 'tmux kill-server|kill.*server.py' mashu/"),
    ('A07f', 'AC-9', "cat > notes.md <<'EOF'\n./start.sh restart\nEOF"),
    ('A18d', 'AC-13', 'python3 -m unittest discover -s tests'),
]


class TestSafety(Base):
    def setUp(self):
        super().setUp()
        self.self_film = studio.greenlight(self.conn, studio.CODE)[0]

    def check_block(self, cases, f):
        for cid, ac, cmd, gate in cases:
            with self.subTest(id=cid, ac=ac, cmd=cmd):
                hit = studio.risky('Bash', {'command': cmd}, f)
                self.assertIsNotNone(hit, f'{cid}（{ac}）应拦：{cmd!r}')
                self.assertEqual(hit[0], gate, f'{cid}（{ac}）闸门不对：{hit}')
                self.assertIsInstance(hit[1], str)

    def check_allow(self, cases, f):
        for cid, ac, cmd in cases:
            with self.subTest(id=cid, ac=ac, cmd=cmd):
                self.assertIsNone(studio.risky('Bash', {'command': cmd}, f), f'{cid}（{ac}）应放：{cmd!r}')

    def by_ac(self, ac):
        self.check_block([c for c in BLOCK if c[1] == ac], F)
        self.check_allow([c for c in ALLOW if c[1] == ac], F)
        self.check_block([c for c in BLOCK_SELF if c[1] == ac], self.self_film)
        self.check_allow([c for c in ALLOW_SELF if c[1] == ac], self.self_film)

    def test_ac1_mashu_text(self):
        self.by_ac('AC-1')

    def test_ac2_readonly_text(self):
        self.by_ac('AC-2')

    def test_ac3_segments(self):
        self.by_ac('AC-3')

    def test_ac4_nested_exec(self):
        self.by_ac('AC-4')
        # 深度上限：嵌套超过 5 层按拦处理
        cmd = 'ls'
        for _ in range(7):
            cmd = 'bash -c ' + shlex.quote(cmd)
        self.assertEqual(studio.risky('Bash', {'command': cmd}, F), ('prod', '命令嵌套过深'))
        cmd = 'ls'
        for _ in range(3):
            cmd = 'bash -c ' + shlex.quote(cmd)
        self.assertIsNone(studio.risky('Bash', {'command': cmd}, F))

    def test_ac5_git_push(self):
        self.by_ac('AC-5')
        self.assertEqual(studio.risky('Bash', {'command': 'git push origin main'}, F), ('prod', '推送到受保护分支'))
        self.assertEqual(studio.risky('Bash', {'command': 'git push -f origin x'}, F), ('irreversible', '强制推送'))

    def test_ac6_prod_deploy(self):
        self.by_ac('AC-6')

    def test_ac7_remote_write(self):
        self.by_ac('AC-7')

    def test_ac8_merge_rm_drop(self):
        self.by_ac('AC-8')

    def test_ac9_extra_patterns(self):
        self.by_ac('AC-9')
        # 其他片没有这条规则：同样的命令放行
        self.assertIsNone(studio.risky('Bash', {'command': './start.sh restart'}, F))

    def test_ac10_mcp_unchanged(self):
        f = dict(F, safety=dict(F['safety'], mcp_tools=['mcp__x__deploy']))
        self.assertEqual(studio.risky('mcp__x__deploy', {}, f)[0], 'prod')
        self.assertEqual(studio.risky('mcp__a__change_apply', {'env': 'production'}, f)[0], 'prod')
        self.assertIsNone(studio.risky('mcp__a__change_apply', {'env': 'staging'}, f))
        self.assertIsNone(studio.risky('mcp__a__read', {'env': 'production'}, f))
        self.assertIsNone(studio.risky('Read', {'file_path': '/x/prod/deploy.sh'}, f))

    def test_ac12_fallback_to_legacy(self):
        self.check_block([c for c in BLOCK if c[1] == 'AC-12'], F)
        with mock.patch.object(studio, '_shell_segments', side_effect=RuntimeError('boom')):
            self.assertEqual(studio.risky('Bash', {'command': 'git push origin main'}, F)[0], 'prod')
            self.assertEqual(studio.risky('Bash', {'command': 'kubectl apply -n prod -f a.yaml'}, F)[0], 'prod')
        with mock.patch.object(studio, '_seg_risk', side_effect=KeyError('x')):
            self.assertEqual(studio.risky('Bash', {'command': 'git push --force origin x'}, F)[0], 'irreversible')
        # 解析失败的各种写法都退回现行规则
        for cmd in ("git push origin main 'oops", 'mashu say "$(git push origin main"', "bash <<EOF\ngit push origin main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(studio.risky('Bash', {'command': cmd}, F)[0], 'prod')


class TestSafetyHook(Base):
    """AC-11：签字机制不变（同场同闸门 2 小时内放行，过期再拦）。"""

    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('builda')
        self.film, _ = studio.greenlight(self.conn, self.repo, 'builda', 'Builda')

    def test_ac11_approval_unchanged(self):
        self.assertEqual(studio.cfg('approval_ttl_hours'), 2)
        f, key, crew = studio.pitch(self.conn, 'builda', 'x', '需求 x')
        studio.tick(self.conn)
        crew = self.cam(crew['id'])
        push = {'tool_name': 'Bash', 'tool_input': {'command': 'git push origin main'}}
        out = self.hook('hook-pretool', crew, push)
        self.assertIn('"permissionDecision": "deny"', out)
        self.assertIn('安全拦截：推送到受保护分支', out)
        self.assertIn('mashu ask --gate prod', out)
        # 签字请求本身不拦（原来的死锁）
        ask = {'tool_name': 'Bash', 'tool_input': {'command': 'mashu ask --gate prod "git push origin main，正式环境 deploy" '
                                                              '--recommend 审批 --why "x"'}}
        self.assertEqual(self.hook('hook-pretool', crew, ask), '')
        now = time.time()
        self.conn.execute('INSERT INTO approvals(film_id, seq_key, gate, granted_at, expires_at, granted_by) VALUES(?,?,?,?,?,?)',
                          ('builda', key, 'prod', now, now + 2 * 3600, 'test'))
        self.conn.commit()
        self.assertEqual(self.hook('hook-pretool', crew, push), '')
        acts = [x['action'] for x in studio.rows(self.conn, 'SELECT action FROM audit')]
        self.assertIn('blocked', acts)
        self.assertIn('use-approval', acts)
        # 别的闸门不通用
        force = {'tool_name': 'Bash', 'tool_input': {'command': 'git push --force origin feat/x'}}
        self.assertIn('"permissionDecision": "deny"', self.hook('hook-pretool', crew, force))
        # 过期后再拦
        self.conn.execute('UPDATE approvals SET expires_at=?', (now - 1,))
        self.conn.commit()
        self.assertIn('"permissionDecision": "deny"', self.hook('hook-pretool', crew, push))

    def cam(self, cid):
        return studio.camera(self.conn, cid)


if __name__ == '__main__':
    unittest.main()
