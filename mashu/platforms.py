"""各家 CLI（Codex、OpenCode）的纯函数部分：会话记录解析、补丁里的文件、模型列表、思考程度映射、独立 CODEX_HOME。

不依赖 studio.py（studio 调这里），方便单测。各家怎么注入 hooks、会话记录在哪、已知限制见 docs/references/platforms.md。
"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import time

EFFORT_ORDER = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')


def nearest_effort(want, supported):
    """麻薯的思考程度 → 这个模型支持的档位：有同名的用同名，没有就取不超过它的最高一档，再没有取最低一档；supported 为空原样返回。"""
    if not want or not supported:
        return want or ''
    if want in supported:
        return want
    rank = {e: i for i, e in enumerate(EFFORT_ORDER)}
    w = rank.get(want, rank['medium'])
    known = sorted((s for s in supported if s in rank), key=rank.get)
    if not known:
        return ''
    lower = [s for s in known if rank[s] <= w]
    return lower[-1] if lower else known[0]


# ---------- 补丁（Codex apply_patch / OpenCode apply_patch）----------

_PATCH_FILE = re.compile(r'^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$|^\*\*\* Move to:\s*(.+?)\s*$', re.M)


def patch_files(text, cwd=None):
    """apply_patch 补丁里会写到的文件（新增、修改、删除、改名目标），相对路径按 cwd 补全。"""
    out = []
    for m in _PATCH_FILE.finditer(text or ''):
        p = (m.group(1) or m.group(2) or '').strip()
        if not p:
            continue
        if cwd and not os.path.isabs(p):
            p = os.path.join(str(cwd), p)
        if p not in out:
            out.append(p)
    return out


# ---------- Codex 会话记录（rollout-*.jsonl）----------

def read_jsonl_tail(path, tail=1_500_000):
    try:
        p = Path(path)
        size = p.stat().st_size
        with p.open('rb') as fh:
            if size > tail:
                fh.seek(size - tail)
                fh.readline()
            raw = fh.read().decode(errors='ignore')
    except (OSError, TypeError):
        return []
    out = []
    for line in raw.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _js_cmd(src):
    """code mode 的 exec 工具（JS 脚本里调 tools.exec_command / apply_patch）：取第一个命令做标签。"""
    m = re.search(r'exec_command\(\{\s*cmd\s*:\s*"((?:[^"\\]|\\.)*)"', src or '')
    if m:
        try:
            return 'Bash', json.loads('"' + m.group(1) + '"')
        except ValueError:
            return 'Bash', m.group(1)
    m = re.search(r'apply_patch\(\s*"((?:[^"\\]|\\.)*)"', src or '')
    if m:
        try:
            files = patch_files(json.loads('"' + m.group(1) + '"'))
        except ValueError:
            files = []
        return 'Edit', Path(files[0]).name if files else ''
    return 'exec', (src or '').strip().splitlines()[0] if (src or '').strip() else ''


def codex_tool_label(p):
    """rollout 里一次工具调用（function_call / custom_tool_call）→ 「工具 · 参数」。"""
    name = p.get('name') or '工具'
    detail = ''
    if p.get('type') == 'custom_tool_call':
        if name == 'exec':
            name, detail = _js_cmd(p.get('input'))
        elif name == 'apply_patch':
            files = patch_files(p.get('input') or '')
            name, detail = 'Edit', Path(files[0]).name if files else ''
        else:
            detail = str(p.get('input') or '')
    else:
        try:
            a = json.loads(p.get('arguments') or '{}')
        except ValueError:
            a = {}
        a = a if isinstance(a, dict) else {}
        if name in ('exec_command', 'shell', 'local_shell'):
            c = a.get('cmd') or a.get('command') or ''
            name, detail = 'Bash', ' '.join(c) if isinstance(c, list) else str(c)
        else:
            detail = str(a.get('path') or a.get('query') or a.get('url') or '')
    detail = detail.strip().splitlines()[0] if detail.strip() else ''
    return f'{name} · {detail[:60]}' if detail else name


def codex_progress(entries):
    """最后一轮（最后一个 task_started 之后）：(已写出的文字, 正在用的工具)。"""
    texts, tools = [], {}
    for d in entries:
        t, p = d.get('type'), d.get('payload') or {}
        if t == 'event_msg' and p.get('type') == 'task_started':
            texts, tools = [], {}
        elif t == 'response_item':
            k = p.get('type')
            if k == 'message' and p.get('role') == 'assistant':
                for b in p.get('content') or []:
                    if isinstance(b, dict) and b.get('type') == 'output_text' and (b.get('text') or '').strip():
                        texts.append(b['text'])
            elif k in ('function_call', 'custom_tool_call', 'local_shell_call'):
                tools[p.get('call_id')] = codex_tool_label(p)
            elif k in ('function_call_output', 'custom_tool_call_output', 'local_shell_call_output'):
                tools.pop(p.get('call_id'), None)
        elif t == 'event_msg' and p.get('type') == 'task_complete':
            tools = {}
    return '\n\n'.join(texts).strip(), (list(tools.values())[-1] if tools else '')


def codex_last_reply(entries):
    for d in reversed(entries):
        p = d.get('payload') or {}
        if d.get('type') == 'event_msg' and p.get('type') == 'task_complete' and (p.get('last_agent_message') or '').strip():
            return p['last_agent_message'].strip()
    return codex_progress(entries)[0]


def codex_usage(entries):
    """累计用量、上下文占比、模型、窗口：取最后一个 token_count 事件。
    返回 (dict(tokens_in, tokens_out, cache_read, cache_write), ctx 或 None, 模型, 窗口)。tokens_in 不含缓存命中部分。"""
    tot = dict(tokens_in=0, tokens_out=0, cache_read=0, cache_write=0)
    ctx, model, window = None, None, None
    for d in entries:
        t, p = d.get('type'), d.get('payload') or {}
        if t == 'turn_context' and p.get('model'):
            model = p['model']
        elif t == 'event_msg' and p.get('type') == 'task_started' and p.get('model_context_window'):
            window = p['model_context_window']
        elif t == 'event_msg' and p.get('type') == 'token_count' and isinstance(p.get('info'), dict):
            info = p['info']
            u, last = info.get('total_token_usage') or {}, info.get('last_token_usage') or {}
            cached = u.get('cached_input_tokens') or 0
            tot = dict(tokens_in=max(0, (u.get('input_tokens') or 0) - cached), tokens_out=u.get('output_tokens') or 0,
                       cache_read=cached, cache_write=u.get('cache_write_input_tokens') or 0)
            window = info.get('model_context_window') or window
            if window and last:
                ctx = min(1.0, ((last.get('input_tokens') or 0) + (last.get('output_tokens') or 0)) / float(window))
    return tot, ctx, model, window


def codex_session_file(codex_home, sid):
    """独立 CODEX_HOME 里某个会话的 rollout 文件（按文件名末尾的会话 ID 找，最新的在前）。"""
    if not sid or not re.fullmatch(r'[0-9a-fA-F-]{8,64}', sid):
        return None
    base = Path(codex_home) / 'sessions'
    hits = sorted(base.glob(f'*/*/*/rollout-*-{sid}.jsonl'), reverse=True) if base.is_dir() else []
    return hits[0] if hits else None


def find_codex_session(homes, sid):
    """账号池：每个号一个 CODEX_HOME，会话在哪个号目录里就去哪找。返回 (rollout 文件, 所在的 CODEX_HOME) 或 (None, None)。"""
    for h in homes:
        p = codex_session_file(h, sid)
        if p:
            return p, Path(h)
    return None, None


def codex_models(cache_path):
    """Codex 可选模型：~/.codex/models_cache.json 里 visibility=list 的，带支持的思考档位和窗口。"""
    try:
        d = json.loads(Path(cache_path).read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for m in (d.get('models') if isinstance(d, dict) else d) or []:
        if not isinstance(m, dict) or not m.get('slug') or m.get('visibility') not in (None, 'list'):
            continue
        out[m['slug']] = dict(efforts=[e.get('effort') for e in m.get('supported_reasoning_levels') or [] if isinstance(e, dict)],
                              context=m.get('context_window'), default_effort=m.get('default_reasoning_level'))
    return out


_SKIP_SECTIONS = re.compile(r'(?:hooks|plugins|marketplaces|projects)(?:\.|$)')


def codex_config_text(user_text):
    """麻薯会话用的 config.toml：照抄用户的（模型提供方、MCP、默认模型……都一样），去掉 hooks / 插件 / 项目信任这几节：
    用户自己的 hooks 不进麻薯会话，信任由麻薯每次按会话传。只按节头逐行过滤，不解析 TOML。"""
    out, skip = [], False
    for line in (user_text or '').splitlines(keepends=True):
        m = re.match(r'\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$', line)
        if m:
            head = m.group(1).strip()
            head = head[1:].split('"', 1)[0] if head.startswith('"') else head
            skip = bool(_SKIP_SECTIONS.match(head))
        if not skip:
            out.append(line)
    return ''.join(out)


def toml_str(s):
    """TOML 基本字符串（JSON 的转义是 TOML 的子集）。"""
    return json.dumps(str(s), ensure_ascii=False)


def build_codex_home(home, user_home, skills=None, extra='', link_auth=True, api_base_url=None):
    """建麻薯专用的 CODEX_HOME（不改用户的 ~/.codex）：
    - config.toml：用户 config.toml 的副本（去掉 hooks / 插件 / 项目信任），用户的改了就重抄；Codex 自己往里写的信任只落在这份副本；
    - auth.json（和 auth.lock）：软链接到用户的，登录状态共用，Codex 刷新令牌时写回同一个文件；
    - AGENTS.md：软链接到用户的全局说明（和 Claude 会话读用户的 CLAUDE.md 一样）；
    - skills/<名>：麻薯给的 skill（caesar-docs）；extra：追加到 config.toml 末尾的内容（麻薯的 hooks）。
    link_auth=False：账号池里麻薯自己持有的号，auth.json / auth.lock 是这个号目录自己的（codex login 写进来），不软链接。
    返回 home。"""
    home, user_home = Path(home), Path(user_home)
    home.mkdir(parents=True, exist_ok=True)
    try:
        src = (user_home / 'config.toml').read_text()
    except OSError:
        src = ''
    text = codex_config_text(src)
    if api_base_url:
        lines, section, skip = [], False, False
        for line in text.splitlines(keepends=True):
            head = re.match(r'\s*\[\[?\s*([^\]]+?)\s*\]\]?', line)
            if head:
                section = True
                skip = bool(re.fullmatch(r'''model_providers\s*\.\s*(?:mashu_api|"mashu_api"|'mashu_api')(?:\s*\..*)?''', head.group(1).strip()))
            if skip or (not section and re.match(r'\s*(?:model_provider|cli_auth_credentials_store)\s*=', line)):
                continue
            lines.append(line)
        text = 'model_provider = "mashu_api"\ncli_auth_credentials_store = "file"\n' + ''.join(lines) + '\n[model_providers.mashu_api]\nname = "Mashu API"\nbase_url = ' + toml_str(api_base_url) + '\nwire_api = "responses"\nrequires_openai_auth = true\n'
    text += extra
    cfgp = home / 'config.toml'
    mark = home / '.mashu-source-config'
    marker = src + extra + (api_base_url or '')
    if not cfgp.exists() or (mark.read_text() if mark.exists() else None) != marker:
        tmp = home / '.config.toml.mashu-tmp'
        tmp.write_text(text)
        os.replace(tmp, cfgp)
        mark.write_text(marker)
    for name in ('auth.json', 'auth.lock', 'AGENTS.md') if link_auth else ('AGENTS.md',):
        target, link = user_home / name, home / name
        if not target.exists():
            continue
        if link.is_symlink() and os.readlink(link) == str(target):
            continue
        if link.exists() and not link.is_symlink() and name == 'auth.json':
            continue        # Codex 把软链接换成了普通文件（不该发生）：不覆盖，留给 studio 提醒
        tmp = home / f'.{name}.mashu-tmp'
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(target)
        os.replace(tmp, link)
    for name, target in (skills or {}).items():
        link = home / 'skills' / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and os.readlink(link) == str(target):
            continue
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                continue
            link.unlink()
        link.symlink_to(target)
    return home


def codex_trust(home, dirs):
    """在麻薯 CODEX_HOME 的 config.toml（副本）里把这些目录记成信任，免得 TUI 弹「Trust this folder?」（-c 覆盖对这个对话框不起作用）。
    只往副本末尾追加缺的，不动用户的 ~/.codex。"""
    cfgp = Path(home) / 'config.toml'
    try:
        text = cfgp.read_text()
    except OSError:
        text = ''
    add = ''
    for d in dict.fromkeys(str(x) for x in dirs):
        head = f'[projects.{toml_str(d)}]'
        if head not in text:
            add += f'\n{head}\ntrust_level = "trusted"\n'
    if add:
        with cfgp.open('a') as fh:
            fh.write(add)
    return bool(add)


def codex_hooks_toml(exe):
    """麻薯的 Codex hooks（写进麻薯 CODEX_HOME 的 config.toml 末尾；命令开头判断 $MASHU_SESSION）。"""
    def h(name, t):
        cmd = f'[ -n "$MASHU_SESSION" ] && {exe} {name} --codex 2>/dev/null || true'
        return f'[[hooks.{{ev}}.hooks]]\ntype = "command"\ntimeout = {t}\ncommand = {toml_str(cmd)}\n'
    parts = ['\n# ---- 麻薯 hooks（mashu 生成，别手改）----\n']
    for ev, name, t, matcher in (('SessionStart', 'hook-session', 20, 'startup|resume|clear|compact'),
                                 ('UserPromptSubmit', 'hook-prompt', 20, None),
                                 ('Stop', 'hook-stop', 30, None),
                                 ('PreToolUse', 'hook-pretool', 10, '*')):
        parts.append(f'[[hooks.{ev}]]\n' + (f'matcher = {toml_str(matcher)}\n' if matcher else '') + h(name, t).replace('{ev}', ev))
    return '\n'.join(parts)


# ---------- OpenCode ----------

def opencode_models_parse(text):
    """`opencode models --verbose` 的输出（一行 provider/model，后面跟一段 JSON）→ {名字: dict(variants, context)}。"""
    out = {}
    chunks = re.split(r'\n(?=[A-Za-z0-9][\w.\-]*/\S+\s*\n\s*\{)', '\n' + (text or ''))
    for ch in chunks:
        ch = ch.strip('\n')
        name, _, js = ch.partition('\n')
        name = name.strip()
        if '/' not in name or not js.strip().startswith('{'):
            continue
        try:
            d = json.loads(js)
        except ValueError:
            continue
        out[name] = dict(variants=list((d.get('variants') or {}).keys()), context=(d.get('limit') or {}).get('context'),
                         free=not any(((d.get('cost') or {}).get(k) or 0) for k in ('input', 'output')))
    return out


OPENCODE_BUILTIN = {'bash': 'Bash', 'edit': 'Edit', 'write': 'Write', 'multiedit': 'MultiEdit', 'patch': 'ApplyPatch',
                    'apply_patch': 'ApplyPatch', 'read': 'Read', 'glob': 'Glob', 'grep': 'Grep', 'list': 'LS', 'webfetch': 'WebFetch',
                    'websearch': 'WebSearch', 'todowrite': 'TodoWrite', 'todoread': 'TodoRead', 'task': 'Task', 'skill': 'Skill',
                    'question': 'Question', 'lsp': 'LSP', 'codesearch': 'CodeSearch', 'batch': 'Batch', 'invalid': 'Invalid'}


def opencode_tool(tool, args, cwd=None, mcp_names=()):
    """OpenCode 的 tool.execute.before → 麻薯内部的 [(工具名, 参数), …]（一次补丁改多个文件就是多项）。
    MCP 工具在 OpenCode 里叫「服务器_工具」：和项目登记的 mcp__服务器__工具 对上就换成那个名字，其余加 mcp__ 前缀。"""
    tool, a = str(tool or ''), args if isinstance(args, dict) else {}
    kind = OPENCODE_BUILTIN.get(tool.lower())
    if kind == 'Bash':
        return [('Bash', {'command': str(a.get('command') or '')})]
    if kind in ('Edit', 'Write', 'MultiEdit'):
        fp = a.get('filePath') or a.get('file_path') or a.get('path') or ''
        if fp and cwd and not os.path.isabs(fp):
            fp = os.path.join(str(cwd), fp)
        return [(kind, {'file_path': fp})]
    if kind == 'ApplyPatch':
        files = patch_files(a.get('patchText') or a.get('patch') or a.get('input') or '', cwd)
        return [('Write', {'file_path': p}) for p in files] or [('Write', {'file_path': ''})]
    if kind:
        return [(kind, a)]
    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())  # noqa: E731
    for full in mcp_names:
        parts = full.split('__')
        if len(parts) >= 3 and norm(tool) == norm(''.join(parts[1:])):
            return [(full, a)]
    return [('mcp__' + tool, a)]


# ---------- 额度与花费采集（mashu-studio #3）----------

def codex_rate_limits(entries, mtime=None):
    """从 rollout entries 中提取最后一个 token_count 里的 rate_limits。
    若无有效 rate_limits 返回 None。"""
    for d in reversed(entries or []):
        t, p = d.get('type'), d.get('payload') or {}
        if t == 'event_msg' and p.get('type') == 'token_count' and isinstance(p.get('rate_limits'), dict):
            rl = p['rate_limits']
            pri = rl.get('primary') or {}
            sec = rl.get('secondary') or {}
            five_hour = None
            if pri.get('used_percent') is not None:
                five_hour = {
                    'used_percentage': pri.get('used_percent'),
                    'resets_at': pri.get('resets_at'),
                    'window_minutes': pri.get('window_minutes', 300),
                }
            seven_day = None
            if sec.get('used_percent') is not None:
                seven_day = {
                    'used_percentage': sec.get('used_percent'),
                    'resets_at': sec.get('resets_at'),
                    'window_minutes': sec.get('window_minutes', 10080),
                }
            at = mtime
            ts = d.get('timestamp')
            if ts:
                try:
                    at = datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
                except Exception:
                    pass
            return {
                'five_hour': five_hour,
                'seven_day': seven_day,
                'credits': rl.get('credits'),
                'plan_type': rl.get('plan_type'),
                'at': at,
            }
    return None


def find_codex_rate_limits(dirs):
    """在一组 sessions 目录里按修改时间倒序找最新一条 Codex rate_limits。只读。"""
    candidates = []
    for d in dirs:
        p_dir = Path(d).expanduser().resolve()
        if not p_dir.is_dir():
            continue
        try:
            for p in p_dir.glob('*/*/*/rollout-*.jsonl'):
                try:
                    candidates.append((p.stat().st_mtime, p))
                except OSError:
                    pass
        except OSError:
            pass
    candidates.sort(reverse=True, key=lambda x: x[0])
    for mtime, p in candidates[:10]:
        entries = read_jsonl_tail(p, tail=500_000)
        res = codex_rate_limits(entries, mtime=mtime)
        if res and (res.get('five_hour') or res.get('seven_day')):
            return res
    return None


def opencode_costs(db_path=None, now=None):
    """从 opencode.db 只读汇总今日和本周花费。
    返回 dict(today=float, week=float, at=float) 或 None（若数据库不存在或读取失败）。"""
    path = Path(db_path) if db_path else (Path.home() / '.local' / 'share' / 'opencode' / 'opencode.db')
    path = path.expanduser().resolve()
    if not path.is_file():
        return None
    try:
        now_ts = now if now is not None else time.time()
        dt_now = datetime.fromtimestamp(now_ts)
        today_start = datetime(dt_now.year, dt_now.month, dt_now.day)
        today_ms = int(today_start.timestamp() * 1000)
        week_start = today_start - timedelta(days=today_start.weekday())
        week_ms = int(week_start.timestamp() * 1000)

        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2.0)
        cur = conn.cursor()
        cur.execute('SELECT COALESCE(SUM(cost), 0.0) FROM session WHERE time_created >= ?', (today_ms,))
        today_cost = float(cur.fetchone()[0] or 0.0)
        cur.execute('SELECT COALESCE(SUM(cost), 0.0) FROM session WHERE time_created >= ?', (week_ms,))
        week_cost = float(cur.fetchone()[0] or 0.0)
        cur.execute('SELECT MAX(time_created) FROM session')
        row = cur.fetchone()
        latest_at = (row[0] / 1000.0) if row and row[0] else now_ts
        conn.close()
        return {
            'today': today_cost,
            'week': week_cost,
            'at': latest_at,
        }
    except Exception:
        return None

