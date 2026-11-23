"""每日自迭代：调度器每天用便宜模型跑一次 claude -p，复盘最近 24 小时，产出改进建议，由主脑决定采纳哪些。

不建 tmux 会话、不占并发槽：tick 在网页服务进程里开后台线程跑；`mashu self-iterate run` 同步跑。
studio.py 只有入口（tick 钩子、CLI、简报一行、status_data 字段），逻辑都在这里。
"""
import datetime
import json
import os
import re
import signal
import subprocess
import threading
import time
import traceback

import studio
from studio import cfg, one, rows

for _k, _v in dict(self_iterate_enabled=True, self_iterate_time='03:00', self_iterate_model='claude-haiku-5-5',
                   self_iterate_hours=24, self_iterate_max_chars=80000, self_iterate_budget_usd=1.0,
                   self_iterate_timeout_minutes=15).items():
    studio.DEFAULTS.setdefault(_k, _v)

TABLES = """
CREATE TABLE IF NOT EXISTS self_iterate_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, trigger TEXT, status TEXT, started_at REAL, ended_at REAL, pid INTEGER,
  model TEXT, input_chars INTEGER, tokens_in INTEGER, tokens_out INTEGER, cache_read INTEGER, cache_write INTEGER, cost REAL,
  error TEXT, archive TEXT, report TEXT);
CREATE TABLE IF NOT EXISTS self_iterate_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, day TEXT, section TEXT, area TEXT, target TEXT, title TEXT,
  evidence TEXT, change TEXT, priority TEXT, status TEXT DEFAULT '待定', note TEXT, decided_by TEXT, decided_at TEXT);
"""
SECTIONS = {'suggestion': '改进建议', 'memory': '记忆候选', 'workflow': '固定工作流候选'}
AREAS = ('prompt', 'memory', 'workflow', 'rules', 'harness')
# 已覆盖：别的改动已经解决了；过时：情况变了，问题不再成立。和「不采纳」分开，采纳率才看得准
STATUS_OF = {'adopt': '采纳', 'reject': '不采纳', 'cover': '已覆盖', 'outdated': '过时', 'reset': '待定'}
THREADS = []                      # tick 启动的后台线程（测试里 join）


def ensure_tables(conn):
    conn.executescript(TABLES)


def clip(text, n):
    text = text or ''
    if len(text) <= n:
        return text
    note = f'\n（已截断，原 {len(text)} 字）'
    return text[:max(0, n - len(note))] + note


def iso(ts):
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec='seconds')


def hm(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M') if ts else '—'


def run_row(conn, rid):
    return one(conn, 'SELECT * FROM self_iterate_runs WHERE id=?', rid)


# ---------- 调度与占位 ----------

def due_slot(now):
    try:
        hh, mm = (int(x) for x in str(cfg('self_iterate_time')).split(':'))
        return datetime.datetime.combine(now.date(), datetime.time(hh, mm))
    except ValueError:
        return None


def maybe_run(conn):
    """tick 钩子：到点且今天还没有运行记录 → 占位并开后台线程。任何异常都不外抛。"""
    try:
        ensure_tables(conn)
        reap_stale(conn)
        if not cfg('self_iterate_enabled'):
            return None
        since = studio.meta_get(conn, 'self_iterate_since')
        if since is None:
            studio.meta_set(conn, 'self_iterate_since', time.time())
            return None
        now = datetime.datetime.now()
        slot = due_slot(now)
        if not slot or now < slot or slot.timestamp() <= float(since):
            return None
        try:
            rid = claim_run(conn, 'tick')
        except RuntimeError:
            return None
        t = threading.Thread(target=execute, args=(rid,), name=f'selfiter-{rid}', daemon=False)
        THREADS.append(t)
        t.start()
        return t
    except Exception:
        traceback.print_exc()
        return None


def claim_run(conn, trigger, force=False):
    """原子地检查并占位，返回 run_id；冲突抛 RuntimeError。"""
    ensure_tables(conn)
    if conn.in_transaction:
        conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        r = one(conn, 'SELECT * FROM self_iterate_runs WHERE status="running" ORDER BY id DESC LIMIT 1')
        if r:
            raise RuntimeError(f'已有一次自迭代在跑（#{r["id"]}，开始于 {hm(r["started_at"])}）')
        r = one(conn, 'SELECT * FROM self_iterate_runs WHERE day=? ORDER BY id DESC LIMIT 1', studio.today())
        if r and not force:
            raise RuntimeError(f'今天已跑过（#{r["id"]} {r["status"]}），重跑加 --force')
        cur = conn.execute('INSERT INTO self_iterate_runs(day, trigger, status, started_at, pid, model) VALUES(?,?,?,?,?,?)',
                           (studio.today(), trigger, 'running', time.time(), os.getpid(), cfg('self_iterate_model')))
        conn.commit()
        return cur.lastrowid
    except BaseException:
        conn.rollback()
        raise


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def reap_stale(conn):
    """进程没了或超时太久的 running → failed（服务重启时会这样；不通知，避免刷屏）。"""
    limit = (float(cfg('self_iterate_timeout_minutes')) + 5) * 60
    for r in rows(conn, 'SELECT * FROM self_iterate_runs WHERE status="running"'):
        same = r['pid'] == os.getpid()
        if (not same and not pid_alive(r['pid'])) or time.time() - (r['started_at'] or 0) > limit:
            fail(conn, r['id'], '运行中断（进程退出或超时，可能是服务重启）', notify=False)


# ---------- 素材汇总 ----------

def headings(path, limit=40):
    try:
        lines = path.read_text(errors='replace').splitlines()
    except OSError:
        return []
    return [x.strip() for x in lines if re.match(r'#{1,2}\s', x)][:limit]


def failed_lines(f, key, rel):
    try:
        text = (studio.seq_dir(f, key) / rel).read_text(errors='replace')
    except (OSError, TypeError):
        return []
    return [x.strip()[:200] for x in text.splitlines() if re.search(r'ERROR|FAIL', x)][:10]


def sec_sessions(conn, since, since_iso):
    cams = rows(conn, 'SELECT * FROM cameras WHERE started_at>? OR last_active>? ORDER BY id', since, since)
    out = [f'## 1 会话（{len(cams)} 个）']
    for c in cams:
        f = studio.film(conn, c['film_id']) if c['film_id'] else None
        where = (f['title'] if f else c['film_id'] or '全局') + (f' #{studio.seq_num(c["seq_key"])}' if c['seq_key'] else '')
        role = studio.LEVEL_LABEL.get(c['role'], c['role']) + (f'·{c["crew_role"]} 阶段{c["stage_no"]}' if c['crew_role'] else '')
        end = c['last_active'] or c['started_at'] or 0
        tokens = sum(c[k] or 0 for k in ('tokens_in', 'tokens_out', 'cache_read', 'cache_write'))
        out.append(f'\n### #{c["id"]}「{c["name"]}」{role} · {where}')
        out.append(f'- 状态：{c["state"]}' + (f'，结束原因 {c["ended_reason"]}' if c['ended_reason'] else '')
                   + (f'，交接原因 {clip(c["wrap_reason"], 120)}' if c['wrap_reason'] else '')
                   + f'；开始 {hm(c["started_at"])}，时长 {max(0, end - (c["started_at"] or end)) / 60:.0f} 分钟'
                   + f'；token {tokens:,}' + (f'，上下文 {c["context_pct"]:.0%}' if c['context_pct'] else '')
                   + (f'，模型 {c["model"]}' if c['model'] else '') + (f'；由 {c["last_from"]} 唤起' if c['last_from'] else ''))
        out.append('- 目的：' + clip((c['prompt'] or '').replace('\n', ' '), 300))
        kinds = rows(conn, 'SELECT kind, COUNT(*) n FROM messages WHERE camera_id=? GROUP BY kind', c['id'])
        reps = rows(conn, 'SELECT id, kind, status, answered_by, summary FROM reports WHERE source_camera=? ORDER BY id', c['id'])
        out.append('- 链路：消息 ' + ('，'.join(f'{k["kind"]} {k["n"]}' for k in kinds) or '无')
                   + '；发起上报 ' + ('，'.join(f'#{r["id"]} {studio.KIND_LABEL.get(r["kind"], r["kind"])}→{r["status"]}'
                                              f'{"/" + studio.LEVEL_LABEL.get(r["answered_by"], r["answered_by"]) if r["answered_by"] else ""}'
                                              for r in reps) or '无'))
        replies = rows(conn, 'SELECT text FROM messages WHERE camera_id=? AND kind="reply" AND ts>? ORDER BY id DESC LIMIT 2',
                       c['id'], since_iso)
        for r in reversed(replies):
            out.append('- 效果（回复）：' + clip((r['text'] or '').replace('\n', ' '), 400))
    return '\n'.join(out), len(cams)


def sec_reports(conn, since):
    rs = rows(conn, 'SELECT * FROM reports WHERE created_at>? OR closed_at>? ORDER BY id', since, since)
    out = [f'## 2 上报与处理（{len(rs)} 张）']
    for r in rs:
        route = json.loads(r['route'] or '[]')
        levels = [studio.LEVEL_LABEL.get(x['level'], x['level']) for x in route if x.get('level')]
        auto = sum(1 for x in route if x.get('auto'))
        took = f'，{(r["closed_at"] - r["created_at"]) / 60:.0f} 分钟结案' if r['closed_at'] and r['created_at'] else ''
        out.append(f'- #{r["id"]} {studio.KIND_LABEL.get(r["kind"], r["kind"])}' + (f'[{r["gate"]}]' if r['gate'] else '')
                   + f'「{clip(r["summary"], 80)}」来自 {r["source_name"] or r["source_role"]}'
                   + (f'；经 {"→".join(levels)}' if levels else '') + (f'；超时自动上交 {auto} 次' if auto else '')
                   + f'；{r["status"]}' + (f' by {studio.LEVEL_LABEL.get(r["answered_by"], r["answered_by"])}' if r['answered_by'] else '')
                   + (f'：{clip((r["answer"] or "").replace(chr(10), " "), 200)}' if r['answer'] else '') + took)
    return '\n'.join(out), len(rs)


def sec_features(conn, since):
    out = ['## 3 功能与阶段']
    for f in studio.films(conn):
        for s in studio.sequences(conn, f):
            data = studio.seq_state(f, s['key']) or {}
            stamps = []
            for st in data.get('stages', []):
                try:
                    stamps.append((st, datetime.datetime.fromisoformat(st['updated_at']).timestamp()))
                except (KeyError, TypeError, ValueError):
                    pass
            if not any(t > since for _, t in stamps):
                continue
            name = lambda n: studio.stage_def(data, n).get('name', str(n))  # noqa: E731
            spans = [f'{name(a["id"])} {max(0, tb - ta) / 3600:.1f}h' for (a, ta), (_, tb) in zip(stamps, stamps[1:])]
            retakes = one(conn, 'SELECT COUNT(*) n FROM cameras WHERE role="crew" AND film_id=? AND seq_key=? AND ended_reason="retake"',
                          f['id'], s['key'])['n']
            out.append(f'\n### {studio.seq_label(f, s["key"])}')
            out.append('- 阶段：' + ' → '.join(f'{name(st["id"])}[{st["status"]}]' for st in data.get('stages', []))
                       + (f'；耗时 {"、".join(spans)}' if spans else '') + f'；重启 {retakes} 次')
            if data.get('blocker'):
                out.append(f'- 阻塞：{clip(data["blocker"], 200)}')
            h = data.get('handoff') or {}
            if h:
                out.append(f'- 交接：做到 {clip(h.get("progress"), 200)}；下一步 {clip(h.get("next"), 200)}')
            for st in data.get('stages', []):
                cr = st.get('check_result')
                if not isinstance(cr, dict):
                    continue
                out.append(f'- 自动检查 阶段{st["id"]}：{"通过" if cr.get("ok") else "失败"}' + (f'（{cr["path"]}）' if cr.get('path') else ''))
                if not cr.get('ok'):
                    out += [f'  - {x}' for x in failed_lines(f, s['key'], cr.get('path'))]
    return '\n'.join(out)


def sec_user(conn, since_iso):
    ms = rows(conn, 'SELECT * FROM (SELECT * FROM messages WHERE sender="executive" AND kind="chat" AND ts>? ORDER BY id DESC LIMIT 30) '
                    'ORDER BY id', since_iso)
    out = [f'## 4 用户原话（{len(ms)} 条）']
    out += [f'- [{m["ts"][5:16]} {m["channel"]}] {clip((m["text"] or "").replace(chr(10), " "), 300)}' for m in ms]
    return '\n'.join(out), len(ms)


def sec_decisions(conn, since_iso):
    ds = rows(conn, 'SELECT * FROM decisions WHERE ts>? ORDER BY id', since_iso)
    over = rows(conn, 'SELECT * FROM decisions WHERE status="已推翻" ORDER BY id DESC LIMIT 5')
    out = [f'## 5 决策（{len(ds)} 条）']
    out += [f'- {d["actor_name"]}：{clip(d["summary"], 200)}（{d["status"]}）' for d in ds]
    out += [f'- 被推翻：{d["actor_name"]}：{clip(d["summary"], 150)}' + (f'（用户：{clip(d["review_note"], 150)}）' if d['review_note'] else '')
            for d in over]
    return '\n'.join(out)


def sec_usage(conn, since):
    day = datetime.date.fromtimestamp(since).isoformat()
    us = rows(conn, 'SELECT role, model, SUM(tokens_in) ti, SUM(tokens_out) tout, SUM(cache_read) cr, SUM(cache_write) cw, '
                    'SUM(cost) cost FROM usage WHERE day>=? GROUP BY role, model ORDER BY role', day)
    out = ['## 6 用量']
    out += [f'- {studio.LEVEL_LABEL.get(u["role"], u["role"])} {u["model"] or "?"}：输入 {u["ti"] or 0:,} 输出 {u["tout"] or 0:,} '
            f'缓存读 {u["cr"] or 0:,} 缓存写 {u["cw"] or 0:,}' + (f'，${u["cost"]:.2f}' if u['cost'] else '') for u in us]
    top = rows(conn, 'SELECT id, name, role, tokens_in+tokens_out+cache_read+cache_write t FROM cameras '
                     'WHERE started_at>? OR last_active>? ORDER BY t DESC LIMIT 10', since, since)
    if top:
        out.append('- token 最多的会话：' + '，'.join(f'#{c["id"]}「{c["name"]}」{c["t"] or 0:,}' for c in top))
    return '\n'.join(out)


def sec_system(conn, since_iso):
    # 同一条通知会同时 cc 到功能频道和项目频道，按内容去重后再计数，免得被当成重复事件
    cnt = rows(conn, 'SELECT kind, COUNT(DISTINCT text) n FROM messages WHERE kind IN ("notice","event") AND ts>? GROUP BY kind', since_iso)
    ms = rows(conn, 'SELECT * FROM (SELECT kind, text, MIN(id) id, MIN(ts) ts FROM messages WHERE kind IN ("notice","event") AND ts>? '
                    'GROUP BY kind, text ORDER BY id DESC LIMIT 15) ORDER BY id', since_iso)
    au = rows(conn, 'SELECT action, COUNT(*) n FROM audit WHERE ts>? GROUP BY action ORDER BY n DESC', since_iso)
    out = ['## 7 系统事件', '- 计数：' + ('，'.join(f'{x["kind"]} {x["n"]}' for x in cnt) or '无')]
    out += [f'- [{m["ts"][11:16]}] {clip((m["text"] or "").replace(chr(10), " "), 150)}' for m in ms]
    out.append('- 审计：' + ('，'.join(f'{x["action"]} {x["n"]}' for x in au) or '无'))
    return '\n'.join(out)


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def sec_runtime(conn, since):
    """运行日志（events 表）：本窗口 vs 前 7 天的日均，量化哪里在变好 / 变坏；错误按内容去重列出。"""
    try:
        conn.executescript(studio.EVENTS_SCHEMA)
    except Exception:
        return '## 11 运行日志\n- （没有事件表）'
    week = since - 7 * 86400
    evs = rows(conn, 'SELECT * FROM events WHERE ts>? ORDER BY id', week)
    if not evs:
        return '## 11 运行日志（结构化事件）\n- 窗口内没有事件（事件日志 2026-11-16 起记录）'
    first = evs[0]['ts']
    days_before = max(0.0, (since - max(week, first)) / 86400)
    cur = [e for e in evs if e['ts'] > since]
    old = [e for e in evs if e['ts'] <= since]
    det = lambda e: json.loads(e['detail'] or '{}')  # noqa: E731
    out = ['## 11 运行日志（结构化事件；mashu logs 可查原始记录。建议优先引用这里的数字）']
    kinds = sorted({(e['kind'], e['level']) for e in evs})
    line = []
    for k, lv in kinds:
        n = sum(1 for e in cur if e['kind'] == k and e['level'] == lv)
        m = sum(1 for e in old if e['kind'] == k and e['level'] == lv)
        line.append(f'{k}/{lv} {n}' + (f'（前期日均 {m / days_before:.1f}）' if days_before >= 1 else ''))
    out.append('- 事件计数：' + '，'.join(line))
    ends = [e for e in cur if e['kind'] == 'session-end']
    for role in sorted({e['role'] or '?' for e in ends}):
        es = [e for e in ends if (e['role'] or '?') == role]
        ds = [det(e) for e in es]
        reasons = {}
        for d in ds:
            reasons[d.get('reason') or '?'] = reasons.get(d.get('reason') or '?', 0) + 1
        cost = sum(d.get('cost') or 0 for d in ds)
        out.append(f'- 会话结束 · {studio.LEVEL_LABEL.get(role, role)} {len(es)} 个：时长中位数 {_median([e["value"] for e in es]) or 0:.0f} 分钟，'
                   f'token 中位数 {_median([d.get("tokens") for d in ds]) or 0:,}，结束时上下文中位数 {(_median([d.get("ctx") for d in ds]) or 0):.0%}'
                   + (f'，费用合计 ${cost:.2f}' if cost else '') + '；原因 ' + '，'.join(f'{k} {v}' for k, v in sorted(reasons.items(), key=lambda x: -x[1])))
    hand = [e for e in ends if det(e).get('reason') in ('handoff', 'handoff-forced')]
    if hand:
        forced = sum(1 for e in hand if det(e).get('reason') == 'handoff-forced')
        out.append(f'- 上下文交接 {len(hand)} 次，其中强制 {forced} 次；提醒 {sum(1 for e in cur if e["kind"] == "ctx-warn")} 次')
    for e in [e for e in cur if e['kind'] in ('ctx-anomaly', 'handoff-forced', 'launch-fail')][-8:]:
        d = det(e)
        out.append(f'- [{hm(e["ts"])}] {e["kind"]} #{e["camera_id"] or "-"} {e["film_id"] or ""}/{e["seq_key"] or ""} value={e["value"]} '
                   + clip(json.dumps(d, ensure_ascii=False), 200))
    errs = {}
    for e in cur:
        if e['level'] == 'error':
            d = det(e)
            key = (d.get('where') or e['kind'], (d.get('err') or '')[:160])
            errs.setdefault(key, []).append(e['ts'])
    if errs:
        out.append('- 错误（按内容去重）：')
        out += [f'  - {w}：{err}（{len(ts)} 次，最近 {hm(max(ts))}）' for (w, err), ts in sorted(errs.items(), key=lambda x: -len(x[1]))[:10]]
    slow = [e['value'] for e in cur if e['kind'] == 'tick-slow']
    if slow:
        out.append(f'- 调度器慢拍 {len(slow)} 次，最长 {max(slow):.1f} 秒')
    return '\n'.join(out)


def sec_past(conn):
    day = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()
    items = rows(conn, 'SELECT * FROM self_iterate_items WHERE day>=? ORDER BY id', day)
    out = [f'## 9 往期建议（最近 14 天 {len(items)} 条；待定以外的都不要再提，待定的可以合并；复发的写进 followups）']
    out += [f'- #{x["id"]} [{x["status"]}] {clip(x["title"], 120)}' + (f'（主脑：{clip(x["note"], 120)}）' if x['note'] else '')
            for x in items]
    done = [x for x in items if x['status'] == '采纳' and x['decided_at']]
    out.append(f'\n### 9b 已采纳的改动（{len(done)} 条，逐条看落实时间之后的素材：问题还在吗）')
    out += [f'- #{x["id"]} 落实于 {x["decided_at"][5:16]}：{clip(x["title"], 100)}' for x in done]
    return '\n'.join(out)


RULE_FILES = ('守则.md', '用户.md', '方针.md')
RULE_PROMPTS = ('crew.md', 'producer.md', 'assistant.md', 'direct.md')


def sec_rules(conn):
    """现行规则原文：模型只看标题时判断不了某条规则在不在，会提重复或过时的建议。"""
    demote = lambda t: re.sub(r'(?m)^(#{1,2})(?=\s)', r'\1##', t.strip())  # noqa: E731  文件里的 # / ## 降两级，不和素材的节混
    out = ['## 10 现行规则原文（提建议前先查这里：已经写了的不要再提；写了但没被遵守的，建议要说明为什么没生效）']
    for name in RULE_FILES:
        p = studio.MEMORY / name
        if p.exists():
            out.append(f'\n### docs/memory/{name}\n' + demote(studio.strip_frontmatter(p.read_text(errors='replace'))))
    for name in RULE_PROMPTS:
        p = studio.PROMPTS / name
        if p.exists():
            out.append(f'\n### mashu/prompts/{name}\n' + demote(p.read_text(errors='replace')))
    return '\n'.join(out)


def sec_files(conn):
    out = ['## 13 提示词 / 记忆 / 工作流文件索引']
    files = sorted(studio.PROMPTS.glob('*.md')) + sorted(studio.MEMORY.glob('**/*.md')) + sorted(studio.PLAYBOOKS.glob('*.json'))
    for f in studio.films(conn):
        p = studio.film_docs(f) / 'rules.md'
        if p.exists():
            files.append(p)
    for p in files:
        try:
            n = len(p.read_text(errors='replace'))
        except OSError:
            continue
        rel = str(p.relative_to(studio.CODE)) if studio.CODE in p.parents else str(p)
        hs = headings(p) if p.suffix == '.md' else []
        out.append(f'- {rel}（{n} 字）' + (('：' + ' / '.join(hs)) if hs else ''))
    readme = studio.MEMORY_INDEX
    if readme.exists():
        out.append('\n### docs/memory/index.md\n' + clip(studio.strip_frontmatter(readme.read_text(errors='replace')), 1500))
    return '\n'.join(out)


def sec_changes(conn, since):
    """最近改过的代码 / 文档和最近完成的功能：建议前先对照，已经做了的不要再提。"""
    out = ['## 12 已经改过什么（提建议前先对照：素材里的问题如果发生在这些改动之前，先判断是否已解决）']
    week = time.time() - 7 * 86400
    for f in studio.films(conn):
        for s in studio.sequences(conn, f):
            data = studio.seq_state(f, s['key']) or {}
            if not studio.seq_done(data):
                continue
            ts = [st.get('updated_at') for st in data.get('stages', []) if st.get('updated_at')]
            try:
                if not ts or datetime.datetime.fromisoformat(max(ts)).timestamp() < week:
                    continue
            except ValueError:
                continue
            h = data.get('handoff') or {}
            out.append(f'- 已完成 {studio.seq_label(f, s["key"])}（{max(ts)[:16]}）：{clip(h.get("progress") or data.get("title") or "", 300)}'
                       + (f'；改动：{clip(h.get("files"), 200)}' if h.get('files') else ''))
    files = []
    for base in (studio.CODE / 'mashu', studio.CODE / 'web', studio.CODE / 'docs', studio.CODE / 'playbooks'):
        if base.is_dir():
            files += [p for p in base.rglob('*') if p.is_file() and p.suffix in ('.py', '.js', '.md', '.json', '.html', '.css')]
    recent = sorted(((p.stat().st_mtime, p) for p in files if p.stat().st_mtime > since), reverse=True)[:40]
    out.append('- 窗口内改过的文件：' + ('，'.join(f'{p.relative_to(studio.CODE)}（{hm(t)}）' for t, p in recent) or '无'))
    return '\n'.join(out)


def collect_material(conn, hours=None):
    """最近 N 小时的素材 → (文字, 是否有活动)。节的顺序就是优先级，超长先砍后面的。"""
    ensure_tables(conn)
    hours = float(hours or cfg('self_iterate_hours'))
    since = time.time() - hours * 3600
    since_iso = iso(since)
    s1, n_cam = sec_sessions(conn, since, since_iso)
    s2, n_rep = sec_reports(conn, since)
    s4, n_user = sec_user(conn, since_iso)
    head = (f'# 麻薯电脑秘书 · 最近 {hours:g} 小时素材（{iso(since)} 至 {studio.now_iso()}）\n\n## 0 概况\n'
            f'- 项目：' + ('，'.join(f'{f["title"]}[{f["id"]}]' for f in studio.films(conn)) or '无') +
            f'\n- 会话 {n_cam} 个，上报 {n_rep} 张，用户消息 {n_user} 条')
    # 往期建议、规则原文、已改过什么提前：超长时宁可砍细节，也不能让模型看不到现状而提重复建议
    parts = [(head, 2000), (s1, 20000), (s2, 8000), (sec_features(conn, since), 8000), (s4, 6000),
             (sec_past(conn), 5000), (sec_rules(conn), 18000), (sec_changes(conn, since), 4000),
             (sec_decisions(conn, since_iso), 3000), (sec_usage(conn, since), 2000), (sec_system(conn, since_iso), 3000), (sec_runtime(conn, since), 4000),
             ('## 8 复盘数据（近 7 天）\n' + studio.retro_text(conn, days=7), 5000), (sec_files(conn), 4000)]
    text = '\n\n'.join(clip(t, n) for t, n in parts)
    return clip(text, int(cfg('self_iterate_max_chars'))), bool(n_cam or n_rep or n_user)


# ---------- 调用与解析 ----------

def call_claude(material):
    """一次性 claude -p。返回 (stdout, stderr, 退出码)；超时抛 TimeoutError，找不到可执行文件抛 FileNotFoundError。"""
    cmd = [cfg('claude'), '-p', '--model', cfg('self_iterate_model'), '--output-format', 'json', '--tools', '',
           '--max-budget-usd', str(cfg('self_iterate_budget_usd')), '--no-session-persistence', '--strict-mcp-config',
           '--system-prompt', (studio.PROMPTS / 'self-iterate.md').read_text()]
    env = {k: v for k, v in os.environ.items() if not k.startswith('MASHU_') and k != 'CLAUDECODE'}   # 不被 hooks 算进任何会话
    cwd = studio.HOME / 'self-iterate'                                                                  # 中性目录，不读项目的 CLAUDE.md
    cwd.mkdir(parents=True, exist_ok=True)
    timeout = float(cfg('self_iterate_timeout_minutes')) * 60
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd,
                         env=env, start_new_session=True)
    try:
        out, err = p.communicate(material, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise TimeoutError(f'超时（{timeout / 60:g} 分钟），已杀掉')
    return out or '', err or '', p.returncode


def parse_cli_output(stdout, stderr, code):
    """找 type=result 的 JSON，其余行（模型目录警告等）忽略。返回 (结果, 错误)。"""
    res = None
    for line in reversed((stdout or '').splitlines()):
        s = line.strip()
        if s.startswith('{'):
            try:
                d = json.loads(s)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get('type') == 'result':
                res = d
                break
    if res is None:
        try:
            d = json.loads(stdout)
            res = d if isinstance(d, dict) and d.get('type') == 'result' else None
        except ValueError:
            pass
    if res is None:
        return None, f'claude 没有返回结果（退出码 {code}）：' + ((stderr or stdout or '').strip()[-400:] or '无输出')
    if res.get('is_error') or res.get('subtype') != 'success':
        return res, f'claude 报错：{res.get("subtype")} {str(res.get("result") or "")[:300]}'.strip()
    return res, None


def _s(v, n=1000):
    return clip(v if isinstance(v, str) else ('' if v is None else json.dumps(v, ensure_ascii=False)), n)


def _lst(v):
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def parse_report(text):
    """模型输出 → 报告 dict；先找 ```json 围栏，否则取第一个 { 到最后一个 }。不合法抛 ValueError。"""
    text = text or ''
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    raw = m.group(1) if m else text[text.find('{'): text.rfind('}') + 1]
    d = json.loads(raw)
    if not isinstance(d, dict):
        raise ValueError('不是 JSON 对象')
    pick = lambda x, keys: {k: _s(x.get(k)) for k in keys}  # noqa: E731
    out = dict(summary=_s(d.get('summary'), 600),
               sessions=[pick(x, ('session', 'role', 'project', 'purpose', 'chain', 'effect', 'comment'))
                         for x in _lst(d.get('sessions'))[:40]],
               suggestions=[pick(x, ('area', 'target', 'title', 'evidence', 'change', 'priority'))
                            for x in _lst(d.get('suggestions'))[:12]],
               memory_candidates=[pick(x, ('target', 'title', 'evidence', 'change')) for x in _lst(d.get('memory_candidates'))[:8]],
               workflow_candidates=[pick(x, ('target', 'title', 'evidence', 'change')) for x in _lst(d.get('workflow_candidates'))[:4]],
               followups=[pick(x, ('item', 'status', 'evidence')) for x in _lst(d.get('followups'))[:20]])
    for k in ('suggestions', 'memory_candidates', 'workflow_candidates'):
        out[k] = [x for x in out[k] if x['title'].strip()]
    for x in out['suggestions']:
        x['area'] = x['area'] if x['area'] in AREAS else 'other'
    return out


# ---------- 运行、入库、存档 ----------

def archive_dir(run):
    d = studio.HOME / 'self-iterate' / f'{run["day"]}-{run["id"]}'
    d.mkdir(parents=True, exist_ok=True)
    return d


def fail(conn, rid, why, notify=True):
    conn.execute('UPDATE self_iterate_runs SET status="failed", error=?, ended_at=? WHERE id=?', (why, time.time(), rid))
    conn.commit()
    if notify:
        r = run_row(conn, rid)
        studio.post(conn, 'system', 'assistant', 'notice',
                    f'自迭代（{r["day"]}）失败：{why}。今天不再自动重试；要重跑：mashu self-iterate run --force', target='web')


def record_cost(conn, rid, res):
    if not res:
        return
    u = res.get('usage') or {}
    t = dict(tokens_in=u.get('input_tokens') or 0, tokens_out=u.get('output_tokens') or 0,
             cache_read=u.get('cache_read_input_tokens') or 0, cache_write=u.get('cache_creation_input_tokens') or 0)
    cost = res.get('total_cost_usd')
    model = next(iter(res.get('modelUsage') or {}), None) or cfg('self_iterate_model')
    conn.execute('UPDATE self_iterate_runs SET tokens_in=?, tokens_out=?, cache_read=?, cache_write=?, cost=?, model=? WHERE id=?',
                 (t['tokens_in'], t['tokens_out'], t['cache_read'], t['cache_write'], cost, model, rid))
    if any(t.values()) or cost:
        conn.execute('INSERT INTO usage(day, camera_id, role, film_id, seq_key, model, tokens_in, tokens_out, cache_read, '
                     'cache_write, cost) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (studio.today(), None, 'self-iterate', None, None, model, t['tokens_in'], t['tokens_out'], t['cache_read'],
                      t['cache_write'], cost))
    conn.commit()


def report_md(run, rep, items):
    out = [f'# 自迭代报告 {run["day"]}（#{run["id"]}）', '', rep['summary'] or '（没有总结）']
    for sec, label in SECTIONS.items():
        xs = [x for x in items if x['section'] == sec]
        out += ['', f'## {label}（{len(xs)}）'] + (['无'] if not xs else [])
        for x in xs:
            out += [f'- #{x["id"]} [{x["status"]}]' + (f'[{x["area"]}]' if sec == 'suggestion' else '')
                    + (f'[{x["priority"]}]' if x['priority'] else '') + f' {x["title"]}',
                    f'  - 位置：{x["target"]}', f'  - 依据：{x["evidence"]}', f'  - 改法：{x["change"]}']
    fu = rep.get('followups') or []
    out += ['', f'## 已采纳改动的效果（{len(fu)}）'] + [f'- {x["item"]} {x["status"]}：{x["evidence"]}' for x in fu]
    out += ['', f'## 会话 review（{len(rep["sessions"])}）']
    out += [f'- {s["session"]}（{s["role"]} · {s["project"]}）效果 {s["effect"]}：目的 {s["purpose"]}；链路 {s["chain"]}；{s["comment"]}'
            for s in rep['sessions']]
    return '\n'.join(out) + '\n'


def store_report(conn, rid, rep):
    run = run_row(conn, rid)
    groups = (('suggestion', rep['suggestions']), ('memory', rep['memory_candidates']), ('workflow', rep['workflow_candidates']))
    for sec, xs in groups:
        for x in xs:
            conn.execute('INSERT INTO self_iterate_items(run_id, day, section, area, target, title, evidence, change, priority) '
                         'VALUES(?,?,?,?,?,?,?,?,?)',
                         (rid, run['day'], sec, x.get('area') or sec, x['target'], x['title'], x['evidence'], x['change'],
                          x.get('priority') or ''))
    conn.execute('UPDATE self_iterate_runs SET status="ok", ended_at=?, report=? WHERE id=?',
                 (time.time(), json.dumps(dict(summary=rep['summary'], sessions=rep['sessions'], followups=rep.get('followups') or []),
                                          ensure_ascii=False), rid))
    conn.commit()
    items = rows(conn, 'SELECT * FROM self_iterate_items WHERE run_id=? ORDER BY id', rid)
    d = archive_dir(run)
    (d / 'report.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    (d / 'report.md').write_text(report_md(run, rep, items))
    n = len(rep['suggestions'])
    studio.post(conn, 'system', 'assistant', 'notice',
                f'自迭代报告（{run["day"]}）好了：{n} 条改进建议、{len(items) - n} 条可沉淀项待定。请在下一份简报前处理完：'
                f'mashu self-iterate show 看，能自己定的 adopt / reject / cover / outdated，只把产品方向拿不准的问用户；'
                f'复发的已采纳项要查为什么没生效',
                target='web')
    return items


def execute(rid):
    """跑一次（已占位的 running 记录）。在后台线程或 CLI 里调用，用自己的连接；任何异常都记成 failed。"""
    conn = studio.db()
    try:
        ensure_tables(conn)
        run = run_row(conn, rid)
        d = archive_dir(run)
        try:
            text, active = collect_material(conn)
            (d / 'input.md').write_text(text)
            conn.execute('UPDATE self_iterate_runs SET input_chars=?, archive=? WHERE id=?', (len(text), str(d), rid))
            conn.commit()
            if not active:
                conn.execute('UPDATE self_iterate_runs SET status="skipped", error=?, ended_at=? WHERE id=?',
                             (f'最近 {cfg("self_iterate_hours")} 小时没有活动', time.time(), rid))
                conn.commit()
                return
            try:
                out, err, code = call_claude(text)
            except TimeoutError as exc:
                (d / 'output.txt').write_text(str(exc))
                return fail(conn, rid, str(exc))
            except FileNotFoundError:
                return fail(conn, rid, f'找不到 claude 可执行文件：{cfg("claude")}')
            (d / 'output.txt').write_text(out + '\n--- stderr ---\n' + err)
            res, e = parse_cli_output(out, err, code)
            record_cost(conn, rid, res)
            if e:
                return fail(conn, rid, e)
            try:
                rep = parse_report(res.get('result'))
            except ValueError as exc:
                return fail(conn, rid, f'报告不是合法 JSON：{exc}')
            store_report(conn, rid, rep)
        except Exception as exc:
            traceback.print_exc()
            fail(conn, rid, f'{type(exc).__name__}: {exc}')
    finally:
        conn.close()


# ---------- 查询（CLI / 网页 / 简报） ----------

def pending_count(conn):
    ensure_tables(conn)
    return one(conn, 'SELECT COUNT(*) n FROM self_iterate_items WHERE status="待定"')['n']


def digest_line(conn):
    n = pending_count(conn)
    return f'7. 自迭代：{n} 条待定（mashu self-iterate show）' if n else ''


def api_data(conn, day=''):
    ensure_tables(conn)
    days = rows(conn, 'SELECT day, GROUP_CONCAT(status) statuses FROM self_iterate_runs GROUP BY day ORDER BY day DESC LIMIT 14')
    day = day or (days[0]['day'] if days else '')
    runs = rows(conn, 'SELECT id, day, trigger, status, started_at, ended_at, model, input_chars, tokens_in, tokens_out, cache_read, '
                      'cache_write, cost, error, archive FROM self_iterate_runs WHERE day=? ORDER BY id', day)
    ok = one(conn, 'SELECT id, report FROM self_iterate_runs WHERE day=? AND status="ok" ORDER BY id DESC LIMIT 1', day)
    report = json.loads(ok['report'] or '{}') if ok else {}
    items = rows(conn, 'SELECT * FROM self_iterate_items WHERE run_id=? ORDER BY id', ok['id']) if ok else []
    return dict(days=days, day=day, runs=runs, run_id=ok['id'] if ok else None, report=report, items=items,
                pending=pending_count(conn))


# ---------- CLI ----------

def add_parser(sub):
    s = sub.add_parser('self-iterate', help='每日自迭代报告')
    s.add_argument('action', choices=['run', 'material', 'show', 'list', 'adopt', 'reject', 'cover', 'outdated', 'reset'])
    s.add_argument('ids', nargs='*', help='adopt/reject/cover/outdated/reset：条目编号；show：日期')
    s.add_argument('--force', action='store_true'); s.add_argument('--note'); s.add_argument('--status')
    s.add_argument('--days', type=int, default=14); s.add_argument('--hours', type=float)


def item_line(x):
    return (f'#{x["id"]} [{x["status"]}][{SECTIONS.get(x["section"], x["section"])}' + (f'·{x["area"]}' if x['section'] == 'suggestion' else '')
            + (f'·{x["priority"]}' if x['priority'] else '') + f'] {x["title"]}'
            + (f'\n    位置：{x["target"]}' if x['target'] else '') + (f'\n    依据：{x["evidence"]}' if x['evidence'] else '')
            + (f'\n    改法：{x["change"]}' if x['change'] else '') + (f'\n    备注：{x["note"]}（{x["decided_by"]}）' if x['note'] else ''))


def show_text(conn, day=''):
    data = api_data(conn, day)
    if not data['runs']:
        return '还没有自迭代记录' + (f'（{day}）' if day else '')
    out = [f'自迭代 {data["day"]}']
    for r in data['runs']:
        out.append(f'  运行 #{r["id"]} {r["trigger"]} {r["status"]} 模型 {r["model"]}'
                   + (f' ${r["cost"]:.4f}' if r['cost'] else '') + (f' 原因：{r["error"]}' if r['error'] else ''))
    if data['run_id']:
        rep = data['report']
        out += ['', '总结：' + (rep.get('summary') or '（无）'), '', '条目：'] + [item_line(x) for x in data['items']]
        if not data['items']:
            out.append('  没有建议')
        fu = rep.get('followups') or []
        if fu:
            out += ['', f'已采纳改动的效果（{len(fu)}）：'] + [f'  {x["item"]} {x["status"]}：{x["evidence"]}' for x in fu]
        out += ['', f'会话 review（{len(rep.get("sessions") or [])}）：']
        out += [f'  {s["session"]} {s["role"]} {s["effect"]}：{s["comment"]}' for s in rep.get('sessions') or []]
        out += ['', '采纳：mashu self-iterate adopt <编号…> --note "怎么落实"；不采纳：reject；已被别的改动解决：cover；不再成立：outdated；撤销：reset']
    return '\n'.join(out)


def cmd(args):
    conn = studio.db()
    ensure_tables(conn)
    if args.action == 'material':
        text, active = collect_material(conn, args.hours)
        print(text)
        print(f'\n---\n{len(text)} 字；' + ('有活动' if active else '没有活动（run 会记为 skipped）'))
    elif args.action == 'run':
        rid = claim_run(conn, 'manual', args.force)
        print(f'自迭代 #{rid} 开始（模型 {cfg("self_iterate_model")}，可能要几分钟）…', flush=True)
        execute(rid)
        r = run_row(conn, rid)
        if r['status'] != 'ok':
            print(f'#{rid} {r["status"]}：{r["error"]}' + (f'\n存档：{r["archive"]}' if r['archive'] else ''))
            return
        print(show_text(conn, r['day']))
    elif args.action == 'show':
        print(show_text(conn, args.ids[0] if args.ids else ''))
    elif args.action == 'list':
        day = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
        sql, a = 'SELECT * FROM self_iterate_items WHERE day>=?', [day]
        if args.status:
            sql, a = sql + ' AND status=?', a + [args.status]
        items = rows(conn, sql + ' ORDER BY id', *a)
        print('\n'.join(item_line(x) for x in items) or '没有条目')
    else:
        c = studio.env_camera(conn)
        if c and c['role'] != 'assistant':
            raise SystemExit('只有主脑能标记自迭代建议（或用户在终端里）')
        if not args.ids:
            raise SystemExit('给出条目编号，如 mashu self-iterate adopt 12 13')
        who = studio.actor_label(c)
        for i in args.ids:
            x = one(conn, 'SELECT * FROM self_iterate_items WHERE id=?', int(i.lstrip('#'))) if i.lstrip('#').isdigit() else None
            if not x:
                raise SystemExit(f'没有条目 {i}')
            status = STATUS_OF[args.action]
            note = args.note if args.note is not None else ('' if args.action == 'reset' else x['note'])
            conn.execute('UPDATE self_iterate_items SET status=?, note=?, decided_by=?, decided_at=? WHERE id=?',
                         (status, note, '' if args.action == 'reset' else who,
                          '' if args.action == 'reset' else studio.now_iso(), x['id']))
            conn.commit()
            studio.audit(conn, who, f'self-iterate-{args.action}', detail=f'#{x["id"]} {x["title"]}' + (f'：{note}' if note else ''))
            print(f'#{x["id"]} → {status}：{x["title"]}')
