"""Validate affected knowledge maps and guard feature-owned sections."""

from collections import Counter
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from skill_sync import SOURCE, walk


ENTRY = Path('docs/indexes/docs.md')
INDEXES = ('docs', 'design', 'art', 'program', 'builda', 'producer')
TAG = re.compile(r'\[([\w.-]+#\d+)\]')


def routes(root):
    root = Path(root)
    indexes = [root / 'docs' / 'indexes' / f'{name}.md' for name in INDEXES]
    return '\n\n[工程知识地图 · 路径]\n' + '\n'.join(str(path) for path in indexes if path.is_file()) if indexes[0].is_file() else (
        f'\n\n[工程入口] {root / "AGENTS.md"}' if (root / 'AGENTS.md').is_file() else '')


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, errors='replace', timeout=30)
    return result.stdout if result.returncode == 0 else ''


def feature_repos(root, work, tag):
    root, work = Path(root).resolve(), Path(work).resolve()
    candidates = {path for path in walk(root) if (path / '.git').exists()}
    repo = git(work, 'rev-parse', '--show-toplevel').strip()
    if repo:
        candidates.add(Path(repo))
    result = {}
    for path in sorted(candidates):
        lines = git(path, 'log', '--reverse', '--format=%H%x09%s', '-F', f'--grep={tag}').splitlines()
        commits = [sha for line in lines for sha, subject in [line.split('\t', 1)] if tag[1:-1] in TAG.findall(subject)]
        if commits or path in (root, Path(repo) if repo else work):
            result[path] = commits
    for path in {root, work}:
        if (path / 'docs').is_dir():
            result.setdefault(path, [])
    return result


def changed_paths(root, sha):
    return set(git(root, 'diff-tree', '--root', '-m', '--no-commit-id', '--name-only', '--no-renames', '-r', '-z', sha).strip('\0').split('\0')) - {''}


def headings(text):
    fence = ''
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not marker[2].strip():
                fence = ''
            continue
        match = re.match(r'^ {0,3}(#{1,3})\s+(.+?)\s*#*\s*$', line)
        if match and not fence:
            yield number, match[2]


def section_errors(root, sha, path, tag, handoff):
    parent = git(root, 'rev-parse', f'{sha}^').strip()
    if not parent:
        return []
    before = list(headings(git(root, 'show', f'{parent}:{path}')))
    after = Counter(title for _, title in headings(git(root, 'show', f'{sha}:{path}')))
    if not Counter(title for _, title in before) - after:
        return []
    restored = Counter(title for _, title in headings(git(root, 'show', f'HEAD:{path}')))
    deleted = Counter(title for _, title in before) - (after | restored)
    if not deleted:
        return []
    out = []
    for number, title in reversed(before):
        if not deleted[title]:
            continue
        deleted[title] -= 1
        blame = git(root, 'blame', '-M', '-C', '--line-porcelain', '-L', f'{number},{number}', parent, '--', path)
        blamed = blame.split(' ', 1)[0]
        subject = git(root, 'show', '-s', '--format=%s', blamed) if re.fullmatch(r'[0-9a-f]{40,64}', blamed) else ''
        for owner in TAG.findall(subject):
            if owner == tag[1:-1]:
                continue
            number_only = '#' + owner.rsplit('#', 1)[1]
            if f'允许删除 {number_only}「{title}」' not in handoff:
                out.append(f'ERROR {root / path}：提交 {sha[:8]} 删掉了 {number_only} 的小节「{title}」（{owner}）')
    return out


def finish_issues(repos, tag, handoff, complete):
    code_changed, docs_changed, issues = False, False, []
    for root, commits in repos.items():
        for sha in commits:
            for path in sorted(changed_paths(root, sha)):
                if path.startswith('docs/'):
                    docs_changed = True
                    if path.endswith('.md'):
                        issues.extend(section_errors(root, sha, path, tag, handoff))
                else:
                    code_changed = True
    if complete and code_changed and not docs_changed and not handoff.strip():
        issues.append('ERROR 本功能改了 docs/ 以外的文件，请回写 docs/ 或用 mashu handoff --docs "无需回写的理由" 补交接。')
    return list(dict.fromkeys(issues))


def validate(root, source=SOURCE):
    result = subprocess.run([sys.executable, str(source / 'scripts' / 'caesar_docs.py'), 'validate'], cwd=root,
                            capture_output=True, text=True, errors='replace', timeout=90)
    if result.returncode == 0:
        return []
    output = (result.stderr or result.stdout).strip()
    if output.startswith('ERROR knowledge-map validation failed:'):
        return [line[2:] for line in output.splitlines() if line.startswith('- ')] or [output]
    raise ValueError(output or f'validate 退出码 {result.returncode}')


def baseline(root, ref):
    with tempfile.TemporaryDirectory(prefix='mashu-docs-baseline-') as temp:
        archive = Path(temp) / 'base.tar'
        snapshot = Path(temp) / 'tree'
        snapshot.mkdir()
        subprocess.run(['git', '-C', str(root), 'archive', '-o', str(archive), ref], check=True, capture_output=True, timeout=90)
        subprocess.run(['tar', '-xf', str(archive), '-C', str(snapshot)], check=True, capture_output=True, timeout=90)
        return validate(snapshot) if (snapshot / ENTRY).is_file() else []


def check_maps(repos, saved, warn_only=False):
    failed, lines = False, []
    for root, commits in repos.items():
        if not (root / ENTRY).is_file() and not warn_only:
            continue
        if not (root / 'docs').is_dir():
            continue
        try:
            errors = validate(root)
            if not errors:
                lines.append(f'docs validate 通过：{root}')
                continue
            ref = git(root, 'rev-parse', f'{commits[0]}^' if commits else 'HEAD').strip() if (root / '.git').exists() else ''
            existing = saved.get(str(root)) or {}
            if existing.get('ref') != ref:
                prior = baseline(root, ref) if ref else (errors if not commits else [])
                saved[str(root)] = existing = dict(ref=ref, errors=prior)
            inherited = set(existing['errors'])
            new = set(errors) - inherited
            failed |= bool(new and not warn_only)
            lines.extend(f'{"ERROR" if error in new and not warn_only else "提示"} {root}：{error}' +
                         ('（基线已有）' if error in inherited else '') for error in errors)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            failed |= not warn_only
            lines.append(f'{"提示" if warn_only else "ERROR"} {root}：无法验证知识地图（{exc}）')
    return failed, '\n'.join(lines)


def warn_only(film, config_dir):
    path = config_dir / 'docs-governance.json'
    if path.is_file():
        return json.loads(path.read_text()).get('validate') == 'warn'
    return film['id'] == 'cysj'


def usage_events(tool, args):
    path = str(args.get('file_path') or args.get('filePath') or args.get('path') or args.get('AbsolutePath') or '')
    if tool.lower() in ('read', 'read_file', 'view_file') and path:
        return [('read', path)]
    if tool.lower() in ('edit', 'write', 'multiedit', 'apply_patch', 'edit_file', 'write_to_file') and path:
        return [('write', path)]
    if tool != 'Bash':
        return []
    command = args.get('command') or args.get('cmd') or ''
    if not isinstance(command, str) or not re.search(r'docs[/.]|caesar_docs\.py|check\.sh', command):
        return []
    events = []
    for line in command.splitlines():
        patch = re.match(r'^\*\*\* (?:Add|Update|Delete) File: (.+)', line)
        if patch:
            events.append(('write', patch[1]))
            continue
        for segment in re.split(r'\s*(?:&&|\|\||[;|])\s*', line):
            try:
                words = shlex.split(segment, comments=True)
            except ValueError:
                continue
            if not words:
                continue
            if any(Path(word).name == 'caesar_docs.py' and words[index + 1:index + 2] == ['validate']
                   for index, word in enumerate(words)):
                events.append(('validate', ''))
            command_name = Path(words[0].lstrip('(')).name
            if command_name in ('cat', 'head', 'tail', 'sed', 'nl', 'less', 'bat', 'rg'):
                mode = 'write' if command_name == 'sed' and any(word.startswith('-i') for word in words[1:]) else 'read'
                events.extend((mode, word) for word in words[1:] if word.endswith('.md'))
    return events


def update_usage(summary, tool, args, root, cwd):
    changed = False
    for action, path in usage_events(tool, args):
        if action == 'validate':
            if not summary.get('validated'):
                summary['validated'] = changed = True
            continue
        full = Path(cwd) / path
        try:
            rel = full.resolve().relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        if rel.suffix != '.md' or 'docs' not in parts[:-1] or '.mashu' in parts or 'skills' in parts:
            continue
        if action == 'write':
            if not summary.get('edited'):
                summary['edited'] = changed = True
            continue
        read = summary.setdefault('read', [])
        if str(rel) not in read:
            read.append(str(rel))
            changed = True
    return changed
