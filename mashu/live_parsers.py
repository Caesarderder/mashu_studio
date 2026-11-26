"""Gemini（agy）/ Codex / OpenCode 实时会话解析器（#8）。

纯函数模块，不依赖 studio.py，方便用真实会话记录夹具做单测。
三家解析器输出统一结构（与 Claude 的 session_detail 保持同一套字段）：
  text        这一轮助手已写出的文字（中间文字 + 最新回复）
  tool        当前正在执行的一个工具标签（"工具 · 参数"）或空串
  last_reply  这一轮最终回复文字（Stop hook 转发用）
  tools       这一轮全部工具调用列表 [{id, name, detail, state, start, end}]
  tools_total 这一轮工具调用总数
  thinking    最新思考摘要片段（最多 1500 字）
  todos       待办列表 [{text, state}]
  agents      子代理 / 工作流列表 [{id, kind, tool, desc, type, model, state, start, end, bg}]
  shells      后台命令 / 后台任务列表 [{id, kind, tool, desc, state, start, end, bg}]
  turn_start  本轮开始时间戳（秒）或 None
  turn_out    本轮累计输出 tokens
  model       记录里看到的模型名或 None
  context_pct 记录里算出的上下文占比（0..1）或 None
  ctx_window  记录里看到的上下文窗口或 None
"""
from datetime import datetime
import json
from pathlib import Path
import re
import time

import platforms as plat


def empty_detail():
    return dict(
        text='', tool='', last_reply='',
        tools=[], tools_total=0, thinking='', todos=[],
        agents=[], shells=[], turn_start=None, turn_out=0,
        model=None, context_pct=None, ctx_window=None,
    )


def parse_ts(val):
    """ISO-8601 字符串或 Unix 时间戳（秒或毫秒）→ Unix 秒（float）或 None。"""
    if val is None or val == '':
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        v = float(val)
        return v / 1000.0 if v > 1e12 else (v if v > 0 else None)
    try:
        return datetime.fromisoformat(str(val).strip().replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def first_line(s, limit=60):
    s = str(s or '').strip()
    if not s:
        return ''
    line = s.splitlines()[0].strip()
    return line[:limit]


def tool_label_str(name, detail=''):
    name = str(name or '工具')
    d = first_line(detail, 60)
    return f'{name} · {d}' if d else name


def _normalize_todo_state(s):
    s = str(s or '').strip().lower()
    if s in ('completed', 'done', 'finished', 'x'):
        return 'completed'
    if s in ('in_progress', 'in-progress', 'doing', 'running', 'active', '/'):
        return 'in_progress'
    return 'pending'


def _extract_todos(items):
    out = []
    if not isinstance(items, list):
        return out
    for x in items:
        if isinstance(x, dict):
            text = str(x.get('content') or x.get('step') or x.get('subject') or x.get('text') or x.get('title') or '').strip()
            if text:
                out.append(dict(text=text, state=_normalize_todo_state(x.get('status') or x.get('state'))))
        elif isinstance(x, str) and x.strip():
            out.append(dict(text=x.strip(), state='pending'))
    return out


def _filter_bg_items(items_dict, finished_dict, turn_start, now, stale=False):
    """按与 Claude session_detail 相同的窗口过滤子代理 / 后台任务。"""
    agents, shells = [], []
    for tid, a in items_dict.items():
        fin = finished_dict.get(tid)
        running = not fin
        if stale and running:
            continue
        if not running and (fin[1] or 0) < (turn_start or 0) and not a.get('bg'):
            continue
        if not running and not (turn_start and (fin[1] or 0) >= turn_start) and now - (fin[1] or 0) > 600:
            continue
        item = dict(a, state='running' if running else fin[0], end=None if running else fin[1])
        if item.get('kind') == 'agent':
            agents.append(item)
        else:
            shells.append(item)
    return agents, shells


# ======================================================================
# 1. Gemini (agy) 解析器
# ======================================================================

_AGY_CREATED_RE = re.compile(r'^Created At:\s*(.+)$', re.M)
_AGY_COMPLETED_RE = re.compile(r'^Completed At:\s*(.+)$', re.M)
_AGY_BG_TASK_RE = re.compile(r'Tool is running as a background task with task id:\s*(\S+)')
_AGY_BG_DESC_RE = re.compile(r'^Task Description:\s*(.+)$', re.M)
_AGY_EXIT_CODE_RE = re.compile(r'Command exited with code (\d+)\.')
_AGY_SYS_MSG_RE = re.compile(
    r'\[Message\]\s+timestamp=(\S+)\s+sender=(\S+)\s+.*?content=(.*)', re.S
)
_AGY_SUBAGENT_CONV_RE = re.compile(r'conversation(?:Id|_id)["\s:=]+([A-Za-z0-9_-]{6,64})')
_MD_TODO_RE = re.compile(r'^\s*[-*]\s+\[([ xX/])\]\s+(.+?)\s*$')


def resolve_agy_transcript(path=None, conv=None, gemini_home=None):
    """定位 agy 的 transcript.jsonl。
    注意：agy 1.5.x 状态栏和 hook 有时会报 ~/.gemini/antigravity/brain/...（缺 -cli 后缀），
    实际文件在 ~/.gemini/antigravity-cli/brain/...；这里自动修正并回退。"""
    candidates = []
    if path:
        p = Path(path)
        candidates.append(p)
        s = str(p)
        if '/.gemini/antigravity/brain/' in s:
            candidates.append(Path(s.replace('/.gemini/antigravity/brain/', '/.gemini/antigravity-cli/brain/')))
    if conv and gemini_home:
        candidates.append(Path(gemini_home) / 'brain' / str(conv) / '.system_generated' / 'logs' / 'transcript.jsonl')
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return candidates[-1] if candidates else None


def _agy_unquote(v):
    """agy 压缩版 transcript.jsonl 里 tool_calls[*].args 的字段常被二次 JSON 字符串化（如 '"/a/b.py"' 或 '"ls -la"'），这里自动解包。"""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == '[' and s[-1] == ']') or (s[0] == '{' and s[-1] == '}')):
        try:
            return json.loads(s)
        except ValueError:
            if s[0] == '"' and s[-1] == '"':
                return s[1:-1]
    return v


def agy_tool_info(tc):
    """agy 的 tool_call ({name, args}) → (统一工具名, 关键参数摘要)。"""
    raw_name = str((tc or {}).get('name') or '工具')
    args = (tc or {}).get('args')
    if not isinstance(args, dict):
        args = {}
    low = {str(k).lower(): _agy_unquote(v) for k, v in args.items()}
    ln = raw_name.lower()

    if ln in ('read_file', 'view_file'):
        fp = str(low.get('path') or low.get('absolutepath') or low.get('file_path') or '')
        return 'Read', Path(fp).name if fp else ''
    if ln in ('edit_file', 'replace_file_content', 'multi_replace_file_content'):
        fp = str(low.get('path') or low.get('targetfile') or low.get('file_path') or '')
        return 'Edit', Path(fp).name if fp else ''
    if ln in ('write_file', 'write_to_file'):
        fp = str(low.get('path') or low.get('targetfile') or low.get('file_path') or '')
        return 'Write', Path(fp).name if fp else ''
    if ln == 'run_command':
        cmd = str(low.get('commandline') or low.get('command') or low.get('toolsummary') or '')
        return 'Bash', first_line(cmd, 60)
    if ln in ('search_web', 'web_search'):
        return 'WebSearch', first_line(low.get('query') or low.get('url') or '', 60)
    if ln == 'invoke_subagent':
        desc = str(low.get('role') or low.get('typename') or low.get('prompt') or '')
        return 'Agent', first_line(desc, 60)
    if ln == 'run_workflow':
        return 'Workflow', first_line(low.get('description') or low.get('scriptpath') or '', 60)
    if ln == 'manage_task':
        act = str(low.get('action') or '')
        tid = str(low.get('taskid') or '').split('/')[-1]
        return 'Task', first_line(f'{act} {tid}'.strip(), 60)
    if ln in ('ask_question',):
        qs = low.get('questions')
        q = qs[0].get('question', '') if isinstance(qs, list) and qs and isinstance(qs[0], dict) else ''
        return 'AskQuestion', first_line(q, 60)
    if ln == 'schedule':
        return 'Schedule', first_line(low.get('prompt') or low.get('cronexpression') or '', 60)
    if ln in ('todo_write', 'todowrite', 'update_todo', 'task_list', 'update_plan'):
        return 'TodoWrite', ''
    if ln in ('grep_search', 'find_by_name', 'list_dir'):
        d = low.get('query') or low.get('pattern') or low.get('directorypath') or low.get('searchdirectory') or ''
        name = {'grep_search': 'Grep', 'find_by_name': 'Glob', 'list_dir': 'LS'}[ln]
        return name, first_line(d, 60)

    detail = ''
    for k in ('path', 'targetfile', 'file_path', 'commandline', 'command', 'query', 'description', 'prompt', 'url', 'toolsummary'):
        v = low.get(k)
        if isinstance(v, str) and v.strip():
            detail = Path(v).name if 'path' in k or 'file' in k else first_line(v, 60)
            break
    return raw_name, detail


def _agy_msg_result_status(content):
    text = str(content or '')
    m = _AGY_EXIT_CODE_RE.search(text)
    if m and int(m.group(1)) != 0:
        return 'failed'
    low = text.lower()
    if 'encountered error' in low or 'failed with error' in low or 'task was killed' in low:
        return 'failed'
    return 'completed'


def _parse_task_md(path):
    """解析 agy artifact task.md 中的 markdown 待办（若存在）。"""
    try:
        raw = Path(path).read_text(errors='ignore')
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        m = _MD_TODO_RE.match(line)
        if m:
            mark, text = m.group(1), m.group(2).strip()
            st = 'completed' if mark in ('x', 'X') else ('in_progress' if mark == '/' else 'pending')
            out.append(dict(text=text, state=st))
    return out


def parse_agy_entries(entries, messages=None, full_by_step=None, now=None, mtime=None):
    """解析 agy transcript.jsonl 条目列表 → 统一结构。"""
    now = time.time() if now is None else float(now)
    full_by_step = full_by_step or {}
    res = empty_detail()

    texts = []
    tools = []
    pending_tcs = []        # [(tool_dict, raw_tc, agent_key_or_None)]
    thinking = ''
    todos = []
    turn_start = None
    turn_out = 0
    model = None
    last_reply_turn = ''
    last_reply_since_user = ''
    last_planner_had_tools = False

    launched = {}           # task_id / agent_id → dict
    finished = {}           # task_id / agent_id → (state, ts)

    for raw_e in entries or []:
        if not isinstance(raw_e, dict):
            continue
        si = raw_e.get('step_index')
        e = full_by_step.get(si, raw_e) if (si is not None and si in full_by_step) else raw_e
        etype = e.get('type') or ''
        source = e.get('source') or ''
        ts = parse_ts(e.get('created_at'))

        if etype == 'USER_INPUT':
            texts, tools, pending_tcs = [], [], []
            thinking, turn_start, turn_out = '', ts, 0
            last_reply_turn = ''
            last_reply_since_user = ''
            last_planner_had_tools = False
            continue

        if etype == 'SYSTEM_MESSAGE':
            content = str(e.get('content') or '')
            if '[Message]' in content:
                m = _AGY_SYS_MSG_RE.search(content)
                if m:
                    mts = parse_ts(m.group(1)) or ts
                    sender = m.group(2).strip()
                    body = m.group(3)
                    finished[sender] = (_agy_msg_result_status(body), mts)
                    # 如果上一条 PLANNER_RESPONSE 没有工具调用（说明上一个 turn 已停下，现在是后台任务完成唤醒的新 turn），
                    # 重置当前轮进度，免得把上一轮已发出去的文字和工具重复算进这一轮
                    if not last_planner_had_tools and not pending_tcs:
                        texts, tools, thinking, turn_start, turn_out = [], [], '', ts, 0
                        last_reply_turn = ''
            continue

        if etype == 'PLANNER_RESPONSE':
            # 如果还有上一批未匹配的 pending_tcs，说明那批工具已经结束才触发了新一轮模型思考
            for tdict, _, _ in pending_tcs:
                if tdict['state'] == 'doing':
                    tdict['state'] = 'done'
                    tdict['end'] = tdict['end'] or ts
            pending_tcs = []

            if e.get('model'):
                model = e['model']
            if isinstance(e.get('output_tokens'), (int, float)):
                turn_out += int(e['output_tokens'])
            th = (e.get('thinking') or '').strip()
            if th:
                thinking = th
            txt = (e.get('content') or '').strip()
            if txt:
                texts.append(txt)
                last_reply_turn = txt
                last_reply_since_user = txt

            tcs = e.get('tool_calls') or []
            last_planner_had_tools = bool(tcs)
            for idx, tc in enumerate(tcs):
                if not isinstance(tc, dict):
                    continue
                name, detail = agy_tool_info(tc)
                args = tc.get('args') if isinstance(tc.get('args'), dict) else {}
                low = {str(k).lower(): _agy_unquote(v) for k, v in args.items()}
                tid = f'agy-{si}-{idx}'
                tdict = dict(id=tid, name=name, detail=detail, state='doing', start=ts, end=None)
                tools.append(tdict)

                ln = str(tc.get('name') or '').lower()
                agent_key = None
                if ln in ('todo_write', 'todowrite', 'update_todo', 'task_list', 'update_plan'):
                    parsed_td = _extract_todos(low.get('todos') or low.get('items') or low.get('plan'))
                    if parsed_td:
                        todos = parsed_td
                elif ln == 'manage_task' and str(low.get('action') or '').lower() == 'kill':
                    ktid = str(low.get('taskid') or '').strip()
                    if ktid:
                        finished[ktid] = ('killed', ts)
                elif ln in ('invoke_subagent', 'run_workflow'):
                    agent_key = tid
                    launched[tid] = dict(
                        id=tid, kind='agent', tool='',
                        type=str(low.get('role') or low.get('typename') or ('workflow' if ln == 'run_workflow' else 'subagent')),
                        desc=first_line(low.get('prompt') or low.get('description') or '', 80),
                        model=str(low.get('model') or ''), start=ts, bg=True,
                    )
                pending_tcs.append((tdict, tc, agent_key))
            continue

        if etype == 'GENERIC' and source == 'MODEL':
            content = str(e.get('content') or '')
            status = str(e.get('status') or '').upper()
            err = str(e.get('error') or '').strip()
            cm = _AGY_CREATED_RE.search(content)
            dm = _AGY_COMPLETED_RE.search(content)
            t_start = parse_ts(cm.group(1)) if cm else ts
            t_end = parse_ts(dm.group(1)) if dm else (ts if status in ('DONE', 'ERROR') else None)

            if pending_tcs:
                tdict, tc, agent_key = pending_tcs.pop(0)
            else:
                tc = {}
                agent_key = None
                tdict = dict(id=f'agy-step-{si}', name='工具', detail='', state='doing', start=t_start, end=None)
                tools.append(tdict)

            if t_start and not tdict['start']:
                tdict['start'] = t_start
            elif t_start:
                tdict['start'] = t_start

            bg_m = _AGY_BG_TASK_RE.search(content)
            if bg_m:
                bg_id = bg_m.group(1).strip()
                desc_m = _AGY_BG_DESC_RE.search(content)
                args = tc.get('args') if isinstance(tc.get('args'), dict) else {}
                low = {str(k).lower(): _agy_unquote(v) for k, v in args.items()}
                desc = (str(low.get('toolsummary') or '') or (desc_m.group(1).strip() if desc_m else '') or tdict['detail'])[:80]
                if agent_key and agent_key in launched:
                    entry = launched.pop(agent_key)
                    entry['id'] = bg_id
                    entry['start'] = t_start or entry['start']
                    launched[bg_id] = entry
                else:
                    launched[bg_id] = dict(
                        id=bg_id, kind='shell', tool=tdict['name'], desc=desc,
                        start=t_start or ts, bg=True,
                    )
                tdict['state'] = 'done'
                tdict['end'] = t_end or t_start or ts
            elif status == 'ERROR' or err:
                tdict['state'] = 'error'
                tdict['end'] = t_end or ts
                if agent_key:
                    finished[agent_key] = ('failed', t_end or ts)
            elif status == 'RUNNING':
                tdict['state'] = 'doing'
            else:
                exit_m = _AGY_EXIT_CODE_RE.search(content)
                if exit_m and int(exit_m.group(1)) != 0:
                    tdict['state'] = 'error'
                else:
                    tdict['state'] = 'done'
                tdict['end'] = t_end or ts
                if agent_key:
                    sub_m = _AGY_SUBAGENT_CONV_RE.search(content)
                    if sub_m and agent_key in launched:
                        sub_id = sub_m.group(1)
                        entry = launched.pop(agent_key)
                        entry['id'] = sub_id
                        launched[sub_id] = entry
                    elif tdict['state'] == 'error':
                        finished[agent_key] = ('failed', t_end or ts)

    # 合并 .system_generated/messages/*.json 里的后台任务完成通知
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get('sender') or '').strip()
        if not sender:
            continue
        mts = parse_ts(msg.get('timestamp'))
        st = _agy_msg_result_status(msg.get('content'))
        if sender not in finished or (mts and (finished[sender][1] or 0) < mts):
            finished[sender] = (st, mts)

    stale = bool(mtime and now - mtime > 6 * 3600)
    agents, shells = _filter_bg_items(launched, finished, turn_start, now, stale=stale)

    active = [t for t in tools if t['state'] == 'doing']
    cur_tool = tool_label_str(active[-1]['name'], active[-1]['detail']) if active else ''

    res.update(
        text='\n\n'.join(texts).strip(),
        tool=cur_tool,
        last_reply=(last_reply_turn or last_reply_since_user).strip(),
        tools=tools,
        tools_total=len(tools),
        thinking=thinking[-1500:],
        todos=todos,
        agents=agents,
        shells=shells,
        turn_start=turn_start,
        turn_out=turn_out,
        model=model,
    )
    return res


def parse_agy(path, tail=1_500_000, now=None):
    """读取 agy 的 transcript.jsonl（必要时补读 transcript_full.jsonl 和 messages/*.json）→ 统一结构。"""
    p = resolve_agy_transcript(path)
    if not p or not p.is_file():
        return empty_detail()
    try:
        st = p.stat()
    except OSError:
        return empty_detail()
    entries = plat.read_jsonl_tail(p, tail)
    if not entries:
        return empty_detail()

    # 若本轮有被截断的 PLANNER_RESPONSE，从同目录 transcript_full.jsonl 补齐完整 content / tool_calls
    need_steps = set()
    for e in entries:
        if e.get('type') == 'PLANNER_RESPONSE' and e.get('truncated_fields') and e.get('step_index') is not None:
            need_steps.add(e['step_index'])
    full_by_step = {}
    full_p = p.with_name('transcript_full.jsonl')
    if need_steps and full_p != p and full_p.is_file():
        for fe in plat.read_jsonl_tail(full_p, max(tail, 2_000_000)):
            si = fe.get('step_index')
            if si in need_steps:
                full_by_step[si] = fe

    # 读取同会话 .system_generated/messages/*.json（后台任务完成通知）
    messages = []
    sg_dir = p.parent.parent if p.parent.name == 'logs' else None
    if sg_dir:
        mdir = sg_dir / 'messages'
        if mdir.is_dir():
            for mf in mdir.glob('*.json'):
                try:
                    d = json.loads(mf.read_text(errors='ignore'))
                    if isinstance(d, dict):
                        messages.append(d)
                except (OSError, ValueError):
                    continue

    res = parse_agy_entries(entries, messages=messages, full_by_step=full_by_step, now=now, mtime=st.st_mtime)
    if not res['todos'] and sg_dir and sg_dir.name == '.system_generated':
        task_md = sg_dir.parent / 'task.md'
        if task_md.is_file():
            res['todos'] = _parse_task_md(task_md)
    return res


def agy_last_reply(path, tail=400_000):
    """从 agy 的真实 transcript（优先 transcript_full.jsonl，回退 transcript.jsonl）取本轮最终回复。"""
    p = resolve_agy_transcript(path)
    if not p:
        return ''
    full_p = p.with_name('transcript_full.jsonl') if p.name == 'transcript.jsonl' else p
    target = full_p if full_p.is_file() else p
    if not target.is_file():
        return ''
    entries = plat.read_jsonl_tail(target, tail)
    if not entries and target != p and p.is_file():
        entries = plat.read_jsonl_tail(p, tail)
    if not entries:
        return ''
    return parse_agy_entries(entries).get('last_reply', '')


# ======================================================================
# 2. Codex 解析器
# ======================================================================

_CODEX_SHELL_WRAPPER = re.compile(r'^(?:/bin/)?(?:zsh|bash|sh)$')
_CODEX_BG_SID_RE = re.compile(r'"session_id"\s*:\s*(\d+)|Process running with session ID\s+(\d+)', re.I)
_CODEX_EXIT_RE = re.compile(r'"exit_code"\s*:\s*(-?\d+)|exit\s+code\s*[:=]\s*(-?\d+)', re.I)
_CODEX_JS_PLAN_RE = re.compile(r'update_plan\s*\(\s*(\{.*?\})\s*\)', re.S)


def _codex_cmd_str(cmd):
    if isinstance(cmd, list):
        # 去掉 ["/bin/zsh", "-lc", "..."] 这类壳前缀，保留用户真正在跑的命令
        if len(cmd) >= 3 and _CODEX_SHELL_WRAPPER.match(str(cmd[0])) and str(cmd[1]) in ('-c', '-lc'):
            return str(cmd[2])
        return ' '.join(str(x) for x in cmd)
    return str(cmd or '')


def _codex_output_text(output):
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts = []
        for b in output:
            if isinstance(b, dict) and isinstance(b.get('text'), str):
                parts.append(b['text'])
            elif isinstance(b, str):
                parts.append(b)
        return '\n'.join(parts)
    if isinstance(output, dict):
        return json.dumps(output, ensure_ascii=False)
    return str(output or '')


def _codex_output_is_error(text):
    if not text:
        return False
    m = _CODEX_EXIT_RE.search(text)
    if m:
        code = int(m.group(1) if m.group(1) is not None else m.group(2))
        if code != 0:
            return True
    if '"status":"rejected"' in text or '"status": "rejected"' in text:
        return True
    first = text.strip().splitlines()[0].lower() if text.strip() else ''
    if first.startswith(('script failed', 'error:', 'failed:', 'tool execution failed')):
        return True
    return False


def _codex_call_parts(p):
    """Codex 的 function_call / custom_tool_call / local_shell_call → (工具名, 参数摘要, 解析出的 dict 参数)。"""
    raw_name = str(p.get('name') or ('Bash' if p.get('type') == 'local_shell_call' else '工具'))
    ns = str(p.get('namespace') or '').strip()
    args = {}
    if p.get('type') == 'custom_tool_call':
        inp = str(p.get('input') or '')
        if raw_name == 'exec':
            n, d = plat._js_cmd(inp)
            return n, first_line(d, 60), {'input': inp}
        if raw_name == 'apply_patch':
            files = plat.patch_files(inp)
            detail = Path(files[0]).name + (f' (+{len(files) - 1})' if len(files) > 1 else '') if files else ''
            return 'Edit', detail, {'files': files}
        return raw_name, first_line(inp, 60), {'input': inp}

    if p.get('type') == 'local_shell_call':
        act = p.get('action') or {}
        cmd = _codex_cmd_str(act.get('command') or '')
        return 'Bash', first_line(cmd, 60), act if isinstance(act, dict) else {}

    try:
        parsed = json.loads(p.get('arguments') or '{}')
        if isinstance(parsed, dict):
            args = parsed
    except ValueError:
        args = {}

    if raw_name in ('exec_command', 'shell', 'local_shell'):
        cmd = _codex_cmd_str(args.get('cmd') or args.get('command') or '')
        return 'Bash', first_line(cmd, 60), args
    if raw_name == 'write_stdin':
        chars = str(args.get('chars') or '').strip()
        sid = args.get('session_id')
        detail = first_line(chars, 50) if chars else (f'session {sid}' if sid is not None else '')
        return 'Bash', detail, args
    if raw_name == 'apply_patch':
        files = plat.patch_files(str(args.get('patch') or args.get('input') or ''))
        detail = Path(files[0]).name + (f' (+{len(files) - 1})' if len(files) > 1 else '') if files else ''
        return 'Edit', detail, args
    if raw_name == 'update_plan':
        return 'TodoWrite', '', args
    if raw_name == 'spawn_agent':
        return 'Agent', first_line(args.get('message') or args.get('prompt') or args.get('agent_type') or '', 60), args
    if raw_name in ('wait_agent', 'close_agent', 'interrupt_agent', 'send_message'):
        return 'Agent', first_line(f'{raw_name} {args.get("id") or args.get("agent_id") or ""}', 60), args

    disp = f'{ns}__{raw_name}' if ns and not raw_name.startswith(ns) else raw_name
    detail = str(args.get('path') or args.get('file_path') or args.get('query') or args.get('url') or args.get('code') or '')
    if args.get('path') or args.get('file_path'):
        detail = Path(detail).name
    return disp, first_line(detail, 60), args


def _codex_item_completed_tool(item, start_ts, end_ts):
    """Codex event_msg.item_completed 里的真实子操作 → tool dict 或 None。"""
    itype = item.get('type')
    status = str(item.get('status') or '').lower()
    iid = str(item.get('id') or '')

    if itype == 'CommandExecution':
        cmd = _codex_cmd_str(item.get('command') or '')
        ec = item.get('exit_code')
        failed = status in ('failed', 'error', 'declined') or (ec is not None and ec != 0)
        return dict(id=iid or f'cmd-{start_ts}', name='Bash', detail=first_line(cmd, 60),
                    state='error' if failed else 'done', start=start_ts, end=end_ts)
    if itype == 'FileChange':
        changes = item.get('changes') or {}
        files = list(changes.keys()) if isinstance(changes, dict) else []
        all_add = bool(files) and all(isinstance(v, dict) and v.get('type') == 'add' for v in changes.values())
        name = 'Write' if all_add else 'Edit'
        detail = Path(files[0]).name + (f' (+{len(files) - 1})' if len(files) > 1 else '') if files else ''
        failed = status in ('failed', 'error', 'declined')
        return dict(id=iid or f'fc-{start_ts}', name=name, detail=detail,
                    state='error' if failed else 'done', start=start_ts, end=end_ts)
    if itype == 'McpToolCall':
        srv = item.get('server') or ''
        tn = item.get('tool') or 'mcp'
        name = f'{srv}__{tn}' if srv else tn
        args = item.get('arguments') if isinstance(item.get('arguments'), dict) else {}
        detail = first_line(args.get('query') or args.get('path') or args.get('url') or '', 60)
        failed = status in ('failed', 'error', 'declined')
        return dict(id=iid or f'mcp-{start_ts}', name=name, detail=detail,
                    state='error' if failed else 'done', start=start_ts, end=end_ts)
    if itype == 'Extension':
        kind = str(item.get('kind') or 'Extension')
        name = 'WebSearch' if 'search' in kind.lower() else kind
        detail = first_line(item.get('query') or item.get('url') or '', 60)
        failed = status in ('failed', 'error', 'declined')
        return dict(id=iid or f'ext-{start_ts}', name=name, detail=detail,
                    state='error' if failed else 'done', start=start_ts, end=end_ts)
    if itype == 'ImageView':
        fp = str(item.get('path') or '').replace('file://', '')
        return dict(id=iid or f'img-{start_ts}', name='Read', detail=Path(fp).name if fp else '',
                    state='done', start=start_ts, end=end_ts)
    return None


def parse_codex_entries(entries, now=None, mtime=None):
    """解析 Codex rollout jsonl 条目列表 → 统一结构。"""
    now = time.time() if now is None else float(now)
    res = empty_detail()

    texts = []
    tools = []
    pending = {}            # call_id → {tool, raw_name, args, is_exec, expanded}
    thinking = ''
    todos = []
    turn_start = None
    turn_out = 0
    model = None
    ctx_window = None
    context_pct = None
    last_agent_message = ''

    launched = {}           # id → dict (agents / shells)
    finished = {}           # id → (state, ts)
    agent_by_call = {}      # spawn_agent call_id → launched key

    for d in entries or []:
        if not isinstance(d, dict):
            continue
        t = d.get('type')
        p = d.get('payload') or {}
        pt = p.get('type') if isinstance(p, dict) else None
        ts = parse_ts(d.get('timestamp'))

        if t == 'turn_context':
            if p.get('model'):
                model = p['model']
            continue

        if t == 'event_msg':
            if pt == 'task_started':
                texts, tools, pending = [], [], {}
                thinking, turn_start, turn_out = '', ts, 0
                last_agent_message = ''
                if p.get('model_context_window'):
                    ctx_window = p['model_context_window']
            elif pt == 'task_complete':
                for info in pending.values():
                    if info['tool']['state'] == 'doing':
                        info['tool']['state'] = 'done'
                        info['tool']['end'] = info['tool']['end'] or ts
                pending.clear()
                lam = (p.get('last_agent_message') or '').strip()
                if lam:
                    last_agent_message = lam
                    if not texts:
                        texts.append(lam)
            elif pt == 'turn_aborted':
                for info in pending.values():
                    if info['tool']['state'] == 'doing':
                        info['tool']['state'] = 'error'
                        info['tool']['end'] = info['tool']['end'] or ts
                pending.clear()
            elif pt == 'token_count' and isinstance(p.get('info'), dict):
                info = p['info']
                last = info.get('last_token_usage') or {}
                if isinstance(last.get('output_tokens'), (int, float)):
                    turn_out += int(last['output_tokens'])
                ctx_window = info.get('model_context_window') or ctx_window
                if ctx_window and last:
                    used = (last.get('input_tokens') or 0) + (last.get('output_tokens') or 0)
                    context_pct = min(1.0, used / float(ctx_window))
            elif pt == 'agent_reasoning' and (p.get('text') or '').strip():
                thinking = p['text'].strip()
            elif pt == 'plan_update':
                parsed_td = _extract_todos(p.get('plan'))
                if parsed_td:
                    todos = parsed_td
            elif pt == 'item_completed' and isinstance(p.get('item'), dict):
                item = p['item']
                if item.get('type') == 'Reasoning':
                    stxt = item.get('summary_text') or []
                    if isinstance(stxt, list):
                        joined = '\n'.join(str(x).strip() for x in stxt if str(x).strip())
                        if joined:
                            thinking = joined
                else:
                    s_ts = parse_ts(p.get('started_at_ms')) or ts
                    e_ts = parse_ts(p.get('completed_at_ms')) or ts
                    td = _codex_item_completed_tool(item, s_ts, e_ts)
                    if td:
                        exec_pend = next((v for v in reversed(list(pending.values())) if v['is_exec']), None)
                        if exec_pend is not None:
                            # Code mode exec 内部子调用：插在 exec 占位条之前，并标记 exec 已展开
                            exec_pend['expanded'] = True
                            try:
                                idx = tools.index(exec_pend['tool'])
                                tools.insert(idx, td)
                            except ValueError:
                                tools.append(td)
                        else:
                            # 标准模式：如果当前有未完成的同名调用，就地补全，不重复追加
                            matched = next((v for v in reversed(list(pending.values()))
                                            if not v['is_exec'] and v['tool']['name'] == td['name'] and v['tool']['state'] == 'doing'), None)
                            if matched:
                                matched['tool'].update(state=td['state'], end=td['end'] or ts)
                                if td['detail'] and not matched['tool']['detail']:
                                    matched['tool']['detail'] = td['detail']
                            else:
                                tools.append(td)
            continue

        if t == 'response_item':
            if pt == 'reasoning':
                summary = p.get('summary') or []
                parts = []
                for s in summary:
                    if isinstance(s, dict) and (s.get('text') or '').strip():
                        parts.append(s['text'].strip())
                    elif isinstance(s, str) and s.strip():
                        parts.append(s.strip())
                if parts:
                    thinking = '\n'.join(parts)
            elif pt == 'message' and p.get('role') == 'assistant':
                for b in p.get('content') or []:
                    if isinstance(b, dict) and b.get('type') == 'output_text' and (b.get('text') or '').strip():
                        texts.append(b['text'].strip())
            elif pt in ('function_call', 'custom_tool_call', 'local_shell_call'):
                cid = str(p.get('call_id') or p.get('id') or f'codex-{len(tools)}')
                name, detail, args = _codex_call_parts(p)
                raw_name = str(p.get('name') or '')
                is_exec = (pt == 'custom_tool_call' and raw_name == 'exec')
                tdict = dict(id=cid, name=name, detail=detail, state='doing', start=ts, end=None)
                tools.append(tdict)
                pending[cid] = dict(tool=tdict, raw_name=raw_name, args=args, is_exec=is_exec, expanded=False)

                if raw_name == 'update_plan':
                    parsed_td = _extract_todos(args.get('plan'))
                    if parsed_td:
                        todos = parsed_td
                elif is_exec and 'update_plan' in str(args.get('input') or ''):
                    pm = _CODEX_JS_PLAN_RE.search(str(args.get('input') or ''))
                    if pm:
                        try:
                            parsed_td = _extract_todos(json.loads(pm.group(1)).get('plan'))
                            if parsed_td:
                                todos = parsed_td
                        except ValueError:
                            pass
                elif raw_name == 'spawn_agent':
                    launched[cid] = dict(
                        id=cid, kind='agent', tool='',
                        type=str(args.get('agent_type') or 'subagent'),
                        desc=first_line(args.get('message') or args.get('prompt') or '', 80),
                        model=str(args.get('model') or ''), start=ts, bg=True,
                    )
                    agent_by_call[cid] = cid
            elif pt in ('function_call_output', 'custom_tool_call_output', 'local_shell_call_output'):
                cid = str(p.get('call_id') or '')
                out_text = _codex_output_text(p.get('output'))
                is_err = _codex_output_is_error(out_text)
                info = pending.pop(cid, None)

                # 检测后台 shell（返回了 session_id 且尚未退出）
                sid_m = _CODEX_BG_SID_RE.search(out_text)
                exit_m = _CODEX_EXIT_RE.search(out_text)
                if sid_m and not exit_m:
                    sh_id = 'shell-' + (sid_m.group(1) or sid_m.group(2))
                    desc = info['tool']['detail'] if info else sh_id
                    launched.setdefault(sh_id, dict(
                        id=sh_id, kind='shell', tool='Bash', desc=desc[:80],
                        start=(info['tool']['start'] if info else ts), bg=True,
                    ))

                if info:
                    rn = info['raw_name']
                    args = info['args']
                    if rn == 'write_stdin' and args.get('session_id') is not None and exit_m:
                        sh_id = f'shell-{args["session_id"]}'
                        code = int(exit_m.group(1) if exit_m.group(1) is not None else exit_m.group(2))
                        finished[sh_id] = ('completed' if code == 0 else 'failed', ts)
                    elif rn == 'spawn_agent':
                        try:
                            od = json.loads(out_text)
                            aid = str(od.get('agent_id') or od.get('id') or '') if isinstance(od, dict) else ''
                        except ValueError:
                            aid = ''
                        if aid and cid in launched:
                            entry = launched.pop(cid)
                            entry['id'] = aid
                            launched[aid] = entry
                            agent_by_call[cid] = aid
                        if is_err:
                            finished[agent_by_call.get(cid, cid)] = ('failed', ts)
                    elif rn in ('wait_agent', 'close_agent', 'interrupt_agent'):
                        aid = str(args.get('id') or args.get('agent_id') or '')
                        if aid:
                            st = 'killed' if rn == 'interrupt_agent' else ('failed' if is_err else 'completed')
                            finished[aid] = (st, ts)

                    if info['is_exec'] and info['expanded']:
                        try:
                            tools.remove(info['tool'])
                        except ValueError:
                            pass
                    else:
                        if info['tool']['state'] == 'doing':
                            info['tool']['state'] = 'error' if is_err else 'done'
                            info['tool']['end'] = ts

    stale = bool(mtime and now - mtime > 6 * 3600)
    agents, shells = _filter_bg_items(launched, finished, turn_start, now, stale=stale)

    active = [t for t in tools if t['state'] == 'doing']
    cur_tool = tool_label_str(active[-1]['name'], active[-1]['detail']) if active else ''
    full_text = '\n\n'.join(texts).strip()
    final_reply = last_agent_message or (texts[-1] if texts else '')

    res.update(
        text=full_text,
        tool=cur_tool,
        last_reply=final_reply.strip(),
        tools=tools,
        tools_total=len(tools),
        thinking=thinking[-1500:],
        todos=todos,
        agents=agents,
        shells=shells,
        turn_start=turn_start,
        turn_out=turn_out,
        model=model,
        context_pct=context_pct,
        ctx_window=ctx_window,
    )
    return res


def parse_codex(path, tail=1_500_000, now=None):
    """读取 Codex rollout jsonl → 统一结构。"""
    p = Path(path) if path else None
    if not p or not p.is_file():
        return empty_detail()
    try:
        st = p.stat()
    except OSError:
        return empty_detail()
    entries = plat.read_jsonl_tail(p, tail)
    return parse_codex_entries(entries, now=now, mtime=st.st_mtime)


# ======================================================================
# 3. OpenCode 解析器
# ======================================================================

def parse_opencode(data, now=None, mtime=None):
    """解析 OpenCode 插件写的 state/live/<机位>.json → 统一结构。
    兼容旧格式 {sid, text, tool, turn_start, at} 与新格式（带 tools / thinking / todos / agents / shells / turn_out）。"""
    now = time.time() if now is None else float(now)
    res = empty_detail()
    if not isinstance(data, dict):
        return res

    stale = bool(mtime and now - mtime > 6 * 3600)
    text = str(data.get('text') or '').strip()
    last_reply = str(data.get('last_reply') or text).strip()
    thinking = str(data.get('thinking') or '').strip()[-1500:]
    turn_start = parse_ts(data.get('turn_start'))
    turn_out = int(data.get('turn_out') or 0) if isinstance(data.get('turn_out'), (int, float)) else 0
    model = str(data.get('model') or '') or None

    raw_tools = data.get('tools')
    tools = []
    if isinstance(raw_tools, list):
        for idx, t in enumerate(raw_tools):
            if not isinstance(t, dict):
                continue
            st = str(t.get('state') or 'done')
            if st not in ('doing', 'done', 'error'):
                st = 'doing' if st in ('pending', 'running') else ('error' if st == 'failed' else 'done')
            tools.append(dict(
                id=str(t.get('id') or f'oc-{idx}'),
                name=str(t.get('name') or '工具'),
                detail=first_line(t.get('detail') or '', 60),
                state=st,
                start=parse_ts(t.get('start')),
                end=parse_ts(t.get('end')),
            ))
    elif data.get('tool'):
        # 兼容旧版只写单个 tool 字符串的 live 文件
        raw_t = str(data['tool'])
        name, _, det = raw_t.partition(' · ')
        tools.append(dict(id='live', name=name, detail=det, state='doing', start=turn_start, end=None))

    tools_total = int(data.get('tools_total')) if isinstance(data.get('tools_total'), int) and data.get('tools_total') >= len(tools) else len(tools)
    active = [t for t in tools if t['state'] == 'doing']
    cur_tool = tool_label_str(active[-1]['name'], active[-1]['detail']) if active else str(data.get('tool') or '')

    todos = _extract_todos(data.get('todos'))

    agents = []
    for idx, a in enumerate(data.get('agents') or []):
        if not isinstance(a, dict):
            continue
        st = str(a.get('state') or 'running')
        if stale and st == 'running':
            continue
        agents.append(dict(
            id=str(a.get('id') or f'oca-{idx}'),
            kind='agent',
            tool=str(a.get('tool') or ''),
            type=str(a.get('type') or 'subagent'),
            desc=first_line(a.get('desc') or '', 80),
            model=str(a.get('model') or ''),
            state=st,
            start=parse_ts(a.get('start')),
            end=parse_ts(a.get('end')),
            bg=bool(a.get('bg')),
        ))

    shells = []
    for idx, s in enumerate(data.get('shells') or []):
        if not isinstance(s, dict):
            continue
        st = str(s.get('state') or 'running')
        if stale and st == 'running':
            continue
        shells.append(dict(
            id=str(s.get('id') or f'ocs-{idx}'),
            kind='shell',
            tool=str(s.get('tool') or 'Bash'),
            desc=first_line(s.get('desc') or '', 80),
            state=st,
            start=parse_ts(s.get('start')),
            end=parse_ts(s.get('end')),
            bg=bool(s.get('bg', True)),
        ))

    res.update(
        text=text,
        tool=cur_tool,
        last_reply=last_reply,
        tools=tools,
        tools_total=tools_total,
        thinking=thinking,
        todos=todos,
        agents=agents,
        shells=shells,
        turn_start=turn_start,
        turn_out=turn_out,
        model=model,
    )
    return res


def parse_opencode_file(path, now=None):
    p = Path(path) if path else None
    if not p or not p.is_file():
        return empty_detail()
    try:
        st = p.stat()
        d = json.loads(p.read_text(errors='ignore'))
    except (OSError, ValueError):
        return empty_detail()
    return parse_opencode(d, now=now, mtime=st.st_mtime)
