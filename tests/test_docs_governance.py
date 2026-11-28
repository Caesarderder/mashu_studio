import json
import subprocess
import time
from unittest.mock import patch

from test_studio import Base, studio

import docs_governance as docs
import selfiter
import skill_sync


def commit(repo, subject):
    subprocess.run(['git', '-C', str(repo), 'add', '--', 'README.md', 'docs'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.email=test@local', '-c', 'user.name=Test',
                    'commit', '-qm', subject], check=True, capture_output=True)


def make_map(repo, body=''):
    entry = repo / docs.ENTRY
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text('''---
km_id: reference.index
km_type: reference
domain: test
status: active
owner: test
last_verified: 2026-11-27
source_of_truth:
  - README.md
validated_by:
  - manual-test
---
# Map
''' + body)
    return entry


class DocsGovernanceTests(Base):
    def test_sync_backups_cache_cleanup_and_local_changes(self):
        root = self.tmp / 'project'
        target = root / '.agents' / 'skills' / skill_sync.NAME
        target.mkdir(parents=True)
        (target / 'SKILL.md').write_text('older')
        lock = skill_sync.lock_path(root)
        lock.write_text(json.dumps({'version': 1, 'skills': {skill_sync.NAME: {'files': {
            **skill_sync.digest(target), 'scripts/.ruff_cache/stale': 'not-installed'}}, 'another': {'version': 'keep'}}}))
        user_home = self.tmp / 'user'
        global_skill = user_home / '.claude' / 'skills' / skill_sync.NAME
        global_skill.mkdir(parents=True)
        (global_skill / 'SKILL.md').write_text('user copy')
        projects = [{'id': 'test', 'path': root}]
        options = (studio.HOME, user_home, [studio.HOME / 'codex-home'], projects)
        with patch.dict('os.environ', {'XDG_CONFIG_HOME': str(user_home / '.config')}):
            changes, errors = skill_sync.sync(*options)
            self.assertTrue(changes)
            self.assertFalse(errors)
            self.assertFalse(global_skill.is_symlink())
            _, errors = skill_sync.sync(*options, apply=True)
            self.assertFalse(errors)
            self.assertTrue(global_skill.samefile(skill_sync.SOURCE))
            backup = next((studio.HOME / 'backup').glob('*/caesar-docs/SKILL.md'))
            self.assertEqual(backup.read_text(), 'user copy')
            rows = skill_sync.inventory(*options)
            self.assertTrue(all(row['status'] == '一致' and row['version'] == '1.2.0' for row in rows[1:]))
            entries = json.loads(lock.read_text())['skills']
            self.assertEqual(entries['another'], {'version': 'keep'})
            self.assertEqual(entries[skill_sync.NAME]['files'], skill_sync.digest(skill_sync.SOURCE))
            (target / 'SKILL.md').write_text('local changes')
            _, errors = skill_sync.sync(*options, apply=True)
            self.assertIn('未覆盖', errors[0])
            self.assertEqual((target / 'SKILL.md').read_text(), 'local changes')

    def test_source_stays_in_worktree_when_running_snapshot(self):
        work = self.tmp / 'work'
        (work / '.git').mkdir(parents=True)
        runtime = work / 'state' / 'runtime'
        runtime.mkdir(parents=True)
        self.assertEqual(skill_sync.content_root(runtime, {}), work)
        self.assertEqual(skill_sync.content_root(work, {}), work)
        self.assertEqual(skill_sync.content_root(runtime, {'MASHU_CONTENT': str(self.tmp / 'x')}), self.tmp / 'x')

    def test_submodule_copies_are_not_drift_and_not_synced(self):
        root = (self.tmp / 'game').resolve()
        sub = root / 'engine'
        target = sub / '.agents' / 'skills' / skill_sync.NAME
        target.mkdir(parents=True)
        (sub / '.git').write_text('gitdir: ../.git/modules/engine\n')
        (target / 'SKILL.md').write_text('pinned upstream')
        skill_sync.lock_path(sub).write_text(json.dumps({'version': 1, 'skills': {skill_sync.NAME: {
            'files': skill_sync.digest(target)}}}))
        user_home = self.tmp / 'user'
        options = (studio.HOME, user_home, [studio.HOME / 'codex-home'], [{'id': 'game', 'path': root}])
        with patch.dict('os.environ', {'XDG_CONFIG_HOME': str(user_home / '.config')}):
            row = next(r for r in skill_sync.inventory(*options) if r['path'] == str(target))
            self.assertEqual(row['status'], skill_sync.SUBMODULE)
            changes, errors = skill_sync.sync(*options, apply=True)
        self.assertNotIn(str(target), changes)
        self.assertFalse(errors)
        self.assertEqual((target / 'SKILL.md').read_text(), 'pinned upstream')
        with patch.object(studio, 'skill_inventory', return_value=[row]):
            summary = selfiter.docs_sections(self.conn, time.time() - 60)
        self.assertNotIn(str(target), summary)
        tree = root / 'issues' / 'work'
        (tree / '.agents' / 'skills' / skill_sync.NAME).mkdir(parents=True)
        (tree / '.git').write_text('gitdir: /repo/.git/worktrees/work\n')
        self.assertEqual(skill_sync.nested_checkout(root, tree), skill_sync.WORKTREE)
        self.assertEqual(skill_sync.nested_checkout(root, sub / '.agents'), skill_sync.SUBMODULE)
        self.assertIsNone(skill_sync.nested_checkout(root, root))

    def test_new_errors_block_without_project_script_and_baseline_is_advisory(self):
        repo = self.make_repo('docs-check')
        film, _ = studio.greenlight(self.conn, repo, 'example')
        make_map(repo, '[old](old.md)\n')
        commit(repo, 'existing map')
        make_map(repo, '[old](old.md)\n[new](new.md)\n')
        commit(repo, '[example#2] introduce broken link')
        key = '2-example'
        repo_commits = docs.feature_repos(repo, repo, '[example#2]')
        bases = {}
        result = studio.run_check(film, key, 1, repo_commits, bases)
        self.assertFalse(result['ok'])
        self.assertTrue(result['docs_failed'])
        self.assertTrue(result['unconfigured'])
        self.assertIn('missing relative link old.md（基线已有）', result['tail'])
        self.assertIn('ERROR', result['tail'])
        (studio.film_docs(film) / 'docs-governance.json').write_text('{"validate":"warn"}')
        self.assertTrue(studio.run_check(film, key, 1, repo_commits, bases)['ok'])
        (studio.film_docs(film) / 'docs-governance.json').unlink()
        make_map(repo, '[old](old.md)\n')
        self.assertTrue(studio.run_check(film, key, 1, repo_commits, bases)['ok'])

    def test_finish_requires_docs_writeback_or_reason(self):
        repo = self.make_repo('docs-finish')
        studio.greenlight(self.conn, repo, 'example')
        film, key, cam = studio.pitch(self.conn, 'example', 'fix', 'fix')
        make_map(repo)
        commit(repo, 'add starting map')
        (repo / 'README.md').write_text('run the change')
        commit(repo, f'{studio.feature_tag(film, key)} implementation')
        self.cli(cam, 'feature', 'set', '1', '已完成')
        with self.assertRaisesRegex(SystemExit, 'handoff --docs'):
            self.cli(cam, 'finish', '--reason', '完成')
        self.cli(cam, 'handoff', '--now', '完成', '--next', '无', '--docs', '内部重构，行为不变')
        self.assertEqual(studio.seq_state(film, key)['handoff']['docs'], '内部重构，行为不变')
        self.assertIn('已登记', self.cli(cam, 'finish', '--reason', '完成'))

    def test_finish_blocks_new_docs_error_in_non_strict_mode(self):
        repo = self.make_repo('docs-finish-check')
        studio.greenlight(self.conn, repo, 'example')
        film, key, cam = studio.pitch(self.conn, 'example', 'fix', 'fix')
        make_map(repo)
        commit(repo, 'add starting map')
        make_map(repo, '[broken](missing.md)\n')
        commit(repo, f'{studio.feature_tag(film, key)} change the map')
        self.cli(cam, 'feature', 'set', '1', '已完成')
        with patch.dict(studio.CONFIG_OVERRIDES, {'strict_checks': False}):
            with self.assertRaisesRegex(SystemExit, 'missing relative link missing.md'):
                self.cli(cam, 'finish', '--reason', '完成')
        self.assertEqual(studio.seq_state(film, key)['stages'][0]['status'], '进行中')

    def test_parallel_deletion_names_the_original_feature(self):
        repo = self.make_repo('sections')
        make_map(repo, '## 并行验收\n说明\n\n```md\n## 示例\n```\n')
        commit(repo, '[example#4] acceptance')
        make_map(repo)
        commit(repo, '[example#5] edit docs')
        commits = docs.feature_repos(repo, repo, '[example#5]')
        issues = docs.finish_issues(commits, '[example#5]', '', True)
        self.assertEqual(len(issues), 1)
        self.assertIn('ERROR', issues[0])
        self.assertIn('删掉了 #4 的小节「并行验收」', issues[0])
        self.assertFalse(docs.finish_issues(commits, '[example#5]', '允许删除 #4「并行验收」，用户同意', True))
        make_map(repo, '## 并行验收\n恢复\n')
        commit(repo, '[example#5] restore section')
        self.assertFalse(docs.finish_issues(commits, '[example#5]', '', True))

    def test_report_groups_observed_usage_and_drift(self):
        repo = self.make_repo('docs-usage')
        studio.greenlight(self.conn, repo, 'example')
        film, _, cam = studio.pitch(self.conn, 'example', 'fix', 'fix')
        self.conn.execute('UPDATE cameras SET started_at=? WHERE id=?', (time.time(), cam['id']))
        self.conn.commit()
        studio.record_docs_usage(self.conn, cam, film, 'Bash', {
            'command': 'pwd; cat docs/indexes/docs.md docs/other.md; python3 scripts/caesar_docs.py validate', 'workdir': str(repo)})
        studio.record_docs_usage(self.conn, cam, film, 'Edit', {'file_path': str(repo / docs.ENTRY)})
        with patch.object(studio, 'skill_inventory', return_value=[dict(status='落后', path='/fake')]):
            summary = selfiter.docs_sections(self.conn, time.time() - 60)
        self.assertIn('## caesar-docs 漂移\n- 落后 /fake', summary)
        self.assertIn('## docs 使用统计', summary)
        self.assertIn('首读入口 1 · 阅读合计 2 篇 · validate 1 · docs 写入 1', summary)
        self.assertIn('首读入口 是 · 2 篇', summary)
