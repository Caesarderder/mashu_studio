"""caesar-docs distribution and drift detection."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


NAME = 'caesar-docs'
_CODE = Path(__file__).resolve().parents[1]


def content_root(code, env=os.environ):
    """线上跑 state/runtime 快照时，技能来源仍取工作区那份（和守则、文档一样即时生效；全局软链接也指向工作区）。"""
    if env.get('MASHU_CONTENT'):
        return Path(env['MASHU_CONTENT'])
    code = Path(code)
    if code.name == 'runtime' and code.parent.name == 'state' and (code.parent.parent / '.git').exists():
        return code.parent.parent
    return code


_ROOT = content_root(_CODE)
SOURCE = _ROOT / 'skills' / NAME
SUBMODULE = '子模块（随上游）'
WORKTREE = '工作树（随分支）'
PINNED = (SUBMODULE, WORKTREE)
LEGACY = {'SKILL.md': '54f6b347ddafaaf4e4b04d79e08dea7f666034c205fbc44a52cbdffab9fde17e'}
IGNORED = {'.ruff_cache', '__pycache__', '.DS_Store'}
PRUNE = {'.git', '.agents', '.mashu', 'state', 'node_modules', '.venv', '.build', 'Library', 'DerivedData'}


def walk(root):
    for directory, dirs, _ in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in PRUNE and not name.startswith('.'))
        yield Path(directory)


def lock_path(root):
    return root / '.agents' / 'skills-lock.json'


def nested_checkout(root, directory):
    """directory 在 root 下的 git 子模块或工作树里（根目录的 .git 是文件）：内容跟着上游提交 / 自己的分支走，不由 sync 覆盖。
    返回 SUBMODULE / WORKTREE / None。"""
    while directory != root and root in directory.parents:
        git = directory / '.git'
        if git.is_file():
            try:
                pointer = git.read_text(errors='replace')
            except OSError:
                pointer = ''
            return WORKTREE if '/worktrees/' in pointer else SUBMODULE
        directory = directory.parent
    return None


def project_paths(projects, include_submodules=False):
    seen = set()
    for project in projects:
        root = Path(project['path']).resolve()
        for directory in walk(root):
            target = directory / '.agents' / 'skills' / NAME
            if directory not in seen and (lock_path(directory).is_file() or target.exists() or target.is_symlink()):
                seen.add(directory)
                pinned = nested_checkout(root, directory)
                if include_submodules or not pinned:
                    yield project['id'], directory, target, pinned


def digest(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file()
            and not IGNORED.intersection(path.relative_to(root).parts) and path.suffix != '.pyc'}


def fingerprint(files):
    return hashlib.sha256(''.join(files[name] for name in sorted(files)).encode()).hexdigest() if files else None


def version(root):
    path = root / 'VERSION'
    return path.read_text().strip() if path.is_file() else None


def load_lock(root):
    path = lock_path(root)
    if not path.exists():
        return {'version': 1, 'skills': {}}
    lock = json.loads(path.read_text())
    entries = lock.get('skills') if isinstance(lock, dict) else None
    if not isinstance(entries, dict):
        raise ValueError(f'无效的 skills-lock.json：{path}')
    entry = entries.get(NAME, {})
    if not isinstance(entry, dict) or ('files' in entry and not isinstance(entry['files'], dict)):
        raise ValueError(f'无效的 caesar-docs 锁记录：{path}')
    return lock


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix='.mashu-') as tmp:
        ready = Path(tmp) / path.name
        ready.write_text(text)
        os.replace(ready, path)


def link_skill(target, source=SOURCE, backup=None):
    if target.is_symlink() and target.resolve() == source.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.mashu-skill-') as tmp:
        ready = Path(tmp) / NAME
        ready.symlink_to(source.resolve())
        if not target.exists() and not target.is_symlink():
            os.replace(ready, target)
            return
        if backup is None:
            raise ValueError(f'现有安装需先备份：{target}')
        backup.mkdir(parents=True, exist_ok=True)
        saved = Path(tempfile.mkdtemp(dir=backup, prefix='caesar-docs-')) / NAME
        shutil.move(str(target), str(saved))
        try:
            os.replace(ready, target)
        except OSError:
            shutil.move(str(saved), str(target))
            raise
        return saved


def claude_plugin(home, source=SOURCE):
    plugin = home / 'claude-plugins' / NAME
    manifest = plugin / '.claude-plugin' / 'plugin.json'
    text = json.dumps({'name': NAME, 'version': version(source), 'description': 'Caesar knowledge map'}, indent=2) + '\n'
    if not manifest.exists() or manifest.read_text() != text:
        atomic_text(manifest, text)
    link_skill(plugin / 'skills' / NAME, source, home / 'backup')
    return plugin


def status_row(target, wanted, source, root=None, project=None):
    current = digest(target)
    expected = None
    if root is not None:
        expected = load_lock(root).get('skills', {}).get(NAME, {}).get('files')
        expected = ({path: sha for path, sha in expected.items() if not IGNORED.intersection(Path(path).parts)
                     and Path(path).suffix != '.pyc'} if expected is not None else None)
    if not current:
        status = '缺失'
    elif current == wanted:
        status = '一致'
    elif current == expected or current == LEGACY:
        status = '落后'
    else:
        status = '本地改过'
    linked = target.is_symlink() and target.resolve() == source.resolve()
    return dict(path=str(target), project=project, version=version(target), fingerprint=fingerprint(current),
                status=status, linked=linked)


def sync_copy(root, target, source, wanted, apply):
    row = status_row(target, wanted, source, root)
    if (row['status'] == '本地改过' or target.is_symlink() or target.exists() and not target.is_dir()
            or root not in target.parent.resolve().parents):
        raise ValueError(f'工程副本需人工复核，未覆盖：{target}')
    lock = load_lock(root)
    entry = dict(category='docs', index='docs/indexes/docs.md', version=version(source),
                 source='mashu_studio/skills/caesar-docs', files=wanted)
    entries = lock.setdefault('skills', {})
    prior = entries.get(NAME, {})
    if row['status'] == '一致' and prior.get('files') == wanted:
        return False
    if not apply:
        return True
    if row['status'] != '一致':
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent, prefix='.mashu-skill-') as tmp:
            ready, previous = Path(tmp) / NAME, Path(tmp) / 'previous'
            shutil.copytree(source, ready, ignore=shutil.ignore_patterns(*IGNORED, '*.pyc'))
            if target.exists():
                target.rename(previous)
            try:
                ready.rename(target)
                entries[NAME] = entry
                atomic_text(lock_path(root), json.dumps(lock, ensure_ascii=False, indent=2) + '\n')
            except OSError:
                if target.exists():
                    target.rename(ready)
                if previous.exists():
                    previous.rename(target)
                raise
    else:
        entries[NAME] = entry
        atomic_text(lock_path(root), json.dumps(lock, ensure_ascii=False, indent=2) + '\n')
    return True


def installations(home, user_home, codex_homes):
    opencode = Path(os.environ.get('XDG_CONFIG_HOME') or user_home / '.config') / 'opencode'
    return [user_home / '.claude' / 'skills' / NAME, user_home / '.codex' / 'skills' / NAME,
            user_home / '.gemini' / 'skills' / NAME, opencode / 'skills' / NAME,
            home / 'skill-host' / '.claude' / 'skills' / NAME,
            home / 'claude-plugins' / NAME / 'skills' / NAME, home / 'opencode-config' / 'skills' / NAME,
            *(path / 'skills' / NAME for path in codex_homes)]


def inventory(home, user_home, codex_homes, projects, source=SOURCE):
    wanted = digest(source)
    rows = [dict(path=str(source), project=None, version=version(source), fingerprint=fingerprint(wanted), status='来源', linked=False)]
    targets = [(None, None, path, None) for path in installations(home, user_home, codex_homes)]
    for project, root, target, pinned in [*targets, *project_paths(projects, include_submodules=True)]:
        try:
            if pinned:
                rows.append({**status_row(target, wanted, source, None, project), 'status': pinned})
                continue
            rows.append(status_row(target, wanted, source, root, project))
        except (OSError, ValueError, TypeError) as exc:
            rows.append(dict(path=str(target), project=project, version=None, fingerprint=None,
                             status='无法读取', linked=False, error=str(exc)))
    return rows


def sync(home, user_home, codex_homes, projects, *, apply=False, managed_only=False, project=None, source=SOURCE):
    wanted = digest(source)
    if 'SKILL.md' not in wanted or 'VERSION' not in wanted:
        raise ValueError(f'缺少 caesar-docs 来源：{source}')
    if apply:
        home.mkdir(parents=True, exist_ok=True)
        with (home / '.caesar-docs-sync.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            return sync_targets(home, user_home, codex_homes, projects, source, wanted, apply, managed_only, project)
    return sync_targets(home, user_home, codex_homes, projects, source, wanted, apply, managed_only, project)


def sync_targets(home, user_home, codex_homes, projects, source, wanted, apply, managed_only, project_filter):
    changes, errors = [], []
    paths = installations(home, user_home, codex_homes)
    for target in paths[4:] if managed_only else paths:
        try:
            if not target.is_symlink() or target.resolve() != source.resolve():
                saved = link_skill(target, source, home / 'backup') if apply else None
                changes.append(f'{target}' + (f'（原件备份 {saved}）' if saved else ''))
        except (ValueError, OSError) as exc:
            errors.append(f'{target}：{exc}')
    if apply:
        claude_plugin(home, source)
    for project, root, target, _ in project_paths(projects):
        if project_filter and project != project_filter:
            continue
        try:
            if sync_copy(root, target, source, wanted, apply):
                changes.append(str(target))
        except (ValueError, OSError, TypeError) as exc:
            errors.append(f'{target}：{exc}')
    return changes, errors
