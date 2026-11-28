"""每日自迭代：调度器每天用便宜模型跑一次 claude -p，复盘最近 24 小时，产出改进建议，由主脑决定采纳哪些。

不建 tmux 会话、不占并发槽：tick 在网页服务进程里开后台线程跑；`mashu self-iterate run` 同步跑。
studio.py 只有入口（tick 钩子、CLI、简报一行、status_data 字段），逻辑都在这里。
"""
import datetime
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import threading
import time
import traceback

import studio
from studio import cfg, one, rows

for _k, _v in dict(self_iterate_enabled=True, self_iterate_time='03:00', self_iterate_model='claude-sonnet-5-5',
                   self_iterate_hours=24, self_iterate_max_chars=80000, self_iterate_budget_usd=1.0,
                   self_iterate_timeout_minutes=15, self_iterate_incident_enabled=True,
                   self_iterate_incident_max_per_day=4, self_iterate_incident_max_chars=40000).items():
    studio.DEFAULTS.setdefault(_k, _v)

TABLES = """
CREATE TABLE IF NOT EXISTS self_iterate_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, trigger TEXT, status TEXT, started_at REAL, ended_at REAL, pid INTEGER,
  model TEXT, input_chars INTEGER, tokens_in INTEGER, tokens_out INTEGER, cache_read INTEGER, cache_write INTEGER, cost REAL,
  error TEXT, archive TEXT, report TEXT);
CREATE TABLE IF NOT EXISTS self_iterate_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, day TEXT, section TEXT, area TEXT, target TEXT, title TEXT,
  evidence TEXT, change TEXT, priority TEXT, status TEXT DEFAULT '待定', note TEXT, decided_by TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS self_iterate_incidents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, kind TEXT, title TEXT, detail TEXT, ts REAL, status TEXT DEFAULT 'pending',
  run_id INTEGER);
"""
# signal：怎么看出这条改动生效了（JSON：kind/query/expect）；source：日报 / 事故 / 验效（自动补的复发条目）
ITEM_COLUMNS = (('signal', 'TEXT'), ('source', 'TEXT'))
SECTIONS = {'suggestion': '改进建议', 'memory': '记忆候选', 'workflow': '固定工作流候选'}
AREAS = ('prompt', 'memory', 'workflow', 'rules', 'harness')
# 已覆盖：别的改动已经解决了；过时：情况变了，问题不再成立。和「不采纳」分开，采纳率才看得准
STATUS_OF = {'adopt': '采纳', 'reject': '不采纳', 'cover': '已覆盖', 'outdated': '过时', 'reset': '待定'}
THREADS = []                      # tick 启动的后台线程（测试里 join）


def ensure_tables(conn):
    conn.executescript(TABLES)
    have = {r[1] for r in conn.execute('PRAGMA table_info(self_iterate_items)')}
    added = False
    for col, typ in ITEM_COLUMNS:
        if col not in have:
            conn.execute(f'ALTER TABLE self_iterate_items ADD COLUMN {col} {typ}')
            added = True
    if added:
        conn.commit()


def clip(text, n):
    text = text or ''
    if len(text) <= n:
        return text
    note = f'\n（已截断，原 {len(text)} 字）'
    return text[:max(0, n - len(note))] + note


def short(text, n):
    """单行截断：行内用，末尾加…（clip 会另起一行写说明）。"""
    text = (text or '').replace('\n', ' ')
    return text if len(text) <= n else text[:max(0, n - 1)] + '…'


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
        if slot and now >= slot and slot.timestamp() > float(since):
            try:
                rid = claim_run(conn, 'tick')
            except RuntimeError:
                rid = None
            if rid:
                return start_thread(rid)
        if cfg('self_iterate_incident_enabled'):
            scan_incidents(conn)
            return run_pending_incident(conn)
        return None
    except Exception:
        traceback.print_exc()
        return None


def start_thread(rid, incident_id=None):
    t = threading.Thread(target=execute, args=(rid, incident_id), name=f'selfiter-{rid}', daemon=False)
    THREADS.append(t)
    t.start()
    return t


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
        # 事故小复盘不算「今天跑过」，不挡日报
        r = one(conn, 'SELECT * FROM self_iterate_runs WHERE day=? AND trigger!="incident" ORDER BY id DESC LIMIT 1', studio.today())
        if r and not force and trigger != 'incident':
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
    for r in rows(conn, 'SELECT i.id FROM self_iterate_incidents i JOIN self_iterate_runs r ON r.id=i.run_id '
                        'WHERE i.status="running" AND r.status!="running"'):
        conn.execute('UPDATE self_iterate_incidents SET status="failed" WHERE id=?', (r['id'],))
        conn.commit()


# ---------- 事故即时小复盘 ----------
# 触发后不等第二天日报：tick 扫一遍近 1 小时，命中的记一条 incident；同一触发 6 小时内只记一次，每天最多跑 N 次。
INCIDENT_DEDUPE_HOURS = 6
INCIDENT_WINDOW_HOURS = 2           # 素材取触发前 2 小时到现在
ERROR_RUN = 5                       # 同一 where 的 error 连续 5 次
RESTART_CLICKS = 3                  # 用户 1 小时内点重启 / 发布 3 次
SCAN_EVERY = 60


def incident_candidates(conn, now):
    """近 1 小时命中的触发 → [(key, kind, title, detail, ts)]。只读，不写库。"""
    hour, hour_iso = now - 3600, iso(now - 3600)
    out = []
    try:
        conn.executescript(studio.EVENTS_SCHEMA)
        errs = rows(conn, 'SELECT * FROM events WHERE level="error" AND ts>? ORDER BY id', hour)
    except Exception:
        errs = []
    run_where, run = None, []
    for e in errs:
        d = json.loads(e['detail'] or '{}')
        where = d.get('where') or e['kind']
        run = run + [(e, d)] if where == run_where else [(e, d)]
        run_where = where
        if len(run) == ERROR_RUN:
            out.append((f'error:{where}', 'error', f'同一处报错连续 {ERROR_RUN} 次：{where}',
                        short(str(d.get('err') or ''), 300), e['ts']))
    try:
        for e in rows(conn, 'SELECT * FROM events WHERE kind="release-fail" AND ts>? ORDER BY id', hour)[-1:]:
            d = json.loads(e['detail'] or '{}')
            out.append(('release-fail', 'release', f'发布失败：{d.get("code") or ""}', short(str(d.get('err') or ''), 300), e['ts']))
        for e in rows(conn, 'SELECT * FROM events WHERE kind="incident-declared" AND ts>? ORDER BY id', hour)[-1:]:
            d = json.loads(e['detail'] or '{}')
            out.append(('decide', 'decide', '主脑记录了事故', short(str(d.get('text') or ''), 300), e['ts']))
    except Exception:
        pass
    for a in rows(conn, 'SELECT * FROM audit WHERE action="release-rollback" AND ts>? ORDER BY id', hour_iso)[-1:]:
        out.append(('release-rollback', 'release', '线上快照回滚', short(f'{a["actor"]}：{a["detail"]}', 300), ts_of(a['ts'])))
    clicks = rows(conn, 'SELECT * FROM audit WHERE actor="executive" AND action IN ("restart-server","release") AND ts>? ORDER BY id',
                  hour_iso)
    if len(clicks) >= RESTART_CLICKS:
        out.append(('restart-clicks', 'user', f'用户 1 小时内点了 {len(clicks)} 次重启 / 发布',
                    '；'.join(f'{c["ts"][11:16]} {c["action"]} {short(c["detail"], 60)}' for c in clicks[-6:]), ts_of(clicks[-1]['ts'])))
    return out


def scan_incidents(conn, now=None):
    """tick 钩子：近 1 小时命中的触发记成待跑的 incident（去重、限次）。返回新记的条数。"""
    ensure_tables(conn)
    now = now or time.time()
    last = studio.meta_get(conn, 'self_iterate_incident_scan')
    if last and now - float(last) < SCAN_EVERY:
        return 0
    studio.meta_set(conn, 'self_iterate_incident_scan', now)
    n = 0
    for key, kind, title, detail, ts in incident_candidates(conn, now):
        if one(conn, 'SELECT id FROM self_iterate_incidents WHERE key=? AND ts>?', key, now - INCIDENT_DEDUPE_HOURS * 3600):
            continue
        day0 = datetime.datetime.combine(datetime.date.fromtimestamp(now), datetime.time()).timestamp()
        used = one(conn, 'SELECT COUNT(*) n FROM self_iterate_incidents WHERE ts>=? AND status!="capped"', day0)['n']
        status = 'pending' if used < int(cfg('self_iterate_incident_max_per_day')) else 'capped'
        conn.execute('INSERT INTO self_iterate_incidents(key, kind, title, detail, ts, status) VALUES(?,?,?,?,?,?)',
                     (key, kind, title, detail, ts or now, status))
        conn.commit()
        studio.log_event(conn, 'incident', level='warn', key=key, title=title, status=status)
        n += 1
    return n


def run_pending_incident(conn):
    """有待跑的事故且没有别的自迭代在跑 → 占位、开后台线程。"""
    inc = one(conn, 'SELECT * FROM self_iterate_incidents WHERE status="pending" ORDER BY id LIMIT 1')
    if not inc:
        return None
    try:
        rid = claim_run(conn, 'incident')
    except RuntimeError:
        return None                      # 日报或别的复盘在跑，下一拍再来
    conn.execute('UPDATE self_iterate_incidents SET status="running", run_id=? WHERE id=?', (rid, inc['id']))
    conn.commit()
    return start_thread(rid, inc['id'])


def ts_of(v):
    """epoch 数字或 ISO 文本 → epoch；解析不了返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except ValueError:
        pass
    try:
        dt = datetime.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.astimezone()).timestamp()


# ---------- 生效信号与自动验效 ----------
# signal = {"kind": event|audit|msg|sql|none, "query": "...", "expect": 降到0|下降|不再出现}
# 简写：「event:launch-fail | 降到0」「event:error@tick | 下降」「audit:restart-server@executive | 下降」「msg:打回了结单请求 | 下降」
#      「sql:SELECT ts FROM events WHERE … | 降到0」（只读，第一列是时间：epoch 或 ISO 文本）；无法量化写「无」
SIGNAL_KINDS = ('event', 'audit', 'msg', 'sql', 'none')
TRENDS = ('降到0', '下降', '不再出现')
SIGNAL_HELP = ('生效信号：event:<kind>[/<level>][@<where>] | audit:<action>[@<actor>] | msg:<关键词> | '
               'sql:SELECT <时间列> FROM …（只读）；后面接「| 降到0 / 下降 / 不再出现」；无法量化写「无」')


def norm_trend(t):
    t = re.sub(r'\s+', '', str(t or ''))
    if t in ('0', '降为0', '降到零', '归零', '为0'):
        return '降到0'
    return t if t in TRENDS else '下降'


def parse_signal(v):
    """dict / JSON 文本 / 简写 → {kind, query, expect}；空返回 None；格式不对抛 ValueError。"""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s in ('无', 'none', '-'):
            return dict(kind='none', query='', expect='')
        if s.startswith('{'):
            try:
                v = json.loads(s)
            except ValueError as exc:
                raise ValueError(f'信号 JSON 不合法：{exc}')
        else:
            expect = ''
            head, sep, tail = s.rpartition('|')
            if sep and tail.strip() and re.sub(r'\s+', '', tail) in TRENDS + ('0', '降为0', '归零'):
                s, expect = head.strip(), tail
            kind, sep, query = s.partition(':')
            if not sep:
                raise ValueError(f'信号要以 event: / audit: / msg: / sql: 开头：{v}')
            v = dict(kind=kind.strip().lower(), query=query.strip(), expect=expect)
    if not isinstance(v, dict):
        raise ValueError('信号要是对象或简写文本')
    kind = str(v.get('kind') or '').strip().lower()
    if kind not in SIGNAL_KINDS:
        raise ValueError(f'信号类型 {kind or "（空）"} 不认识，只能是 {" / ".join(SIGNAL_KINDS)}')
    if kind == 'none':
        return dict(kind='none', query='', expect='')
    query = str(v.get('query') or '').strip()
    if not query:
        raise ValueError('信号缺 query')
    if kind == 'sql':
        q = query.rstrip().rstrip(';')
        if ';' in q or not re.match(r'(?is)\s*(select|with)\b', q):
            raise ValueError('sql 信号只能是一条 SELECT')
        query = q
    return dict(kind=kind, query=query, expect=norm_trend(v.get('expect')))


def signal_text(sig):
    if not sig:
        return '（无）'
    if sig['kind'] == 'none':
        return '无法量化'
    return f'{sig["kind"]}:{sig["query"]} | {sig["expect"]}'


def load_signal(raw):
    try:
        return parse_signal(raw)
    except ValueError:
        return None


def signal_sql(sig):
    k, q = sig['kind'], sig['query']
    if k == 'event':
        name, _, where = q.partition('@')
        kind, _, level = name.partition('/')
        sql, a = 'SELECT ts FROM events WHERE kind=?', [kind.strip()]
        if level.strip():
            sql, a = sql + ' AND level=?', a + [level.strip()]
        if where.strip():
            sql, a = sql + " AND json_extract(detail, '$.where') LIKE ?", a + [f'%{where.strip()}%']
        return sql, a
    if k == 'audit':
        action, _, actor = q.partition('@')
        sql, a = 'SELECT ts FROM audit WHERE action=?', [action.strip()]
        if actor.strip():
            sql, a = sql + ' AND actor=?', a + [actor.strip()]
        return sql, a
    if k == 'msg':      # 同一条通知会 cc 到几个频道，按发送者 + 原文去重；自迭代自己的通知不算
        return ("SELECT MIN(ts) FROM messages WHERE text LIKE ? AND text NOT LIKE '%自迭代%' GROUP BY sender, text", [f'%{q}%'])
    return q, []


def signal_times(conn, sig):
    """信号命中的时间点（epoch）。sql 用只读连接跑，写不了库。查询出错抛 ValueError。"""
    sql, a = signal_sql(sig)
    path = studio.HOME / 'studio.db'
    ro = None
    try:
        if sig['kind'] == 'sql':
            ro = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=15)
            ro.execute('PRAGMA query_only=ON')
            got = ro.execute(sql, a).fetchall()
        else:
            got = conn.execute(sql, a).fetchall()
    except Exception as exc:
        raise ValueError(f'信号查询出错：{exc}')
    finally:
        if ro:
            ro.close()
    return sorted(t for t in (ts_of(r[0]) for r in got) if t is not None)


def evaluate_signal(conn, sig, decided_ts, now=None, before_days=3):
    """采纳前 3 天（建议针对的是最近的问题，基线拉太长会被稀释）vs 采纳后到现在：次数、日均、每日计数、判定。"""
    now = now or time.time()
    start = decided_ts - before_days * 86400
    ts = [t for t in signal_times(conn, sig) if start <= t <= now]
    before = [t for t in ts if t < decided_ts]
    after = [t for t in ts if t >= decided_ts]
    after_days = max(0.0, (now - decided_ts) / 86400)
    rb, ra = len(before) / before_days, (len(after) / after_days if after_days else 0.0)
    daily = {}
    for t in ts:
        d = datetime.date.fromtimestamp(t).isoformat()
        daily.setdefault(d, [0, 0])[0 if t < decided_ts else 1] += 1
    if after_days < 0.5:
        verdict = '待观察'
    elif sig['expect'] in ('降到0', '不再出现'):
        verdict = ('生效' if before else '看不出') if not after else '复发'
    elif not before and not after:
        verdict = '看不出'
    elif not before:
        verdict = '复发'
    else:
        verdict = '生效' if ra <= rb * 0.5 else '未见效'
    return dict(before=len(before), after=len(after), before_days=before_days, after_days=round(after_days, 1),
                rate_before=round(rb, 2), rate_after=round(ra, 2), daily=daily, verdict=verdict)


def daily_text(daily, decided_day):
    out = []
    for d in sorted(daily):
        b, a = daily[d]
        out.append(f'{d[5:]} 前{b}/后{a}' if d == decided_day else f'{d[5:]} {b + a}')
    return ' · '.join(out) or '无'


def effects(conn, now=None, days=14):
    """近 N 天采纳 / 已覆盖的建议逐条验效 → [dict]（复发在前）。没写信号的 verdict=无信号。"""
    now = now or time.time()
    day = (datetime.date.fromtimestamp(now) - datetime.timedelta(days=days)).isoformat()
    out = []
    for x in rows(conn, 'SELECT * FROM self_iterate_items WHERE day>=? AND status IN ("采纳","已覆盖") AND section="suggestion" '
                        'ORDER BY id', day):
        sig = load_signal(x.get('signal'))
        base = dict(id=x['id'], title=x['title'], status=x['status'], decided_at=x['decided_at'] or '', signal=signal_text(sig))
        decided = ts_of(x['decided_at'])
        if not sig or sig['kind'] == 'none' or not decided:
            out.append(dict(base, verdict='无信号' if not sig else '无法量化'))
            continue
        try:
            ev = evaluate_signal(conn, sig, decided, now)
        except ValueError as exc:
            out.append(dict(base, verdict='查询出错', error=str(exc)))
            continue
        out.append(dict(base, **ev, daily_text=daily_text(ev['daily'], (x['decided_at'] or '')[:10])))
    order = {'复发': 0, '未见效': 1, '查询出错': 2, '生效': 3, '待观察': 4, '看不出': 5, '无法量化': 6, '无信号': 7}
    return sorted(out, key=lambda e: (order.get(e['verdict'], 9), e['id']))


def effect_line(e):
    head = f'#{e["id"]} [{e["status"]}] {short(e["title"], 80)}'
    if e['verdict'] in ('无信号', '无法量化'):
        return f'- {head} → {e["verdict"]}'
    if e['verdict'] == '查询出错':
        return f'- {head} · 信号 {e["signal"]} → 查询出错：{short(e.get("error"), 160)}'
    cover = '（当时判「已覆盖」，也算复发）' if e['verdict'] in ('复发', '未见效') and e['status'] == '已覆盖' else ''
    return (f'- {head} · 信号 {short(e["signal"], 200)} · 落实 {e["decided_at"][5:16]}：采纳前 {e["before_days"]} 天 {e["before"]} 次'
            f'（{e["rate_before"]}/天）→ 之后 {e["after_days"]} 天 {e["after"]} 次（{e["rate_after"]}/天）；每日 {e["daily_text"]}'
            f' → **{e["verdict"]}**{cover}')


def sec_effects(conn, effs=None):
    effs = effects(conn) if effs is None else effs
    bad = [e for e in effs if e['verdict'] in ('复发', '未见效')]
    nosig = [e for e in effs if e['verdict'] == '无信号']
    out = [f'## 上次采纳的效果（自动验效：近 14 天采纳 / 已覆盖 {len(effs)} 条，复发或未见效 {len(bad)} 条，没写信号 {len(nosig)} 条）',
           '复发 / 未见效的每条都要出一条「复发：」改进建议（优先级高），写清上次的改法为什么没起作用；判「已覆盖」的复发了同样算。']
    out += [effect_line(e) for e in effs if e['verdict'] != '无信号']
    if nosig:
        out.append('- 没写信号、看不出效果：' + '，'.join(f'#{e["id"]}' for e in nosig))
    return '\n'.join(out)


def recurrence_items(conn, run, effs):
    """复发 / 未见效、模型又没出对应「复发：」条目的，自动补一条高优先级待定条目（同一条只补一次）。"""
    made = []
    for e in effs:
        if e['verdict'] not in ('复发', '未见效'):
            continue
        tag = f'复发：#{e["id"]} '
        if one(conn, 'SELECT id FROM self_iterate_items WHERE title LIKE ?', tag + '%'):
            continue
        ref = re.compile(rf'#{e["id"]}(?!\d)')
        if any(ref.search((y['title'] or '') + ' ' + (y['evidence'] or '')) for y in
               rows(conn, 'SELECT title, evidence FROM self_iterate_items WHERE run_id=? AND title LIKE "复发%"', run['id'])):
            continue
        x = one(conn, 'SELECT * FROM self_iterate_items WHERE id=?', e['id'])
        cur = conn.execute('INSERT INTO self_iterate_items(run_id, day, section, area, target, title, evidence, change, priority, '
                           'signal, source) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (run['id'], run['day'], 'suggestion', x['area'], x['target'], tag + short(x['title'], 100),
                            effect_line(e)[2:], '查清上次的改法为什么没生效（看信号命中的具体记录），改法落到 harness 或工作流这类调度器能强制的地方；'
                            f'原改法：{short(x["change"], 300)}', '高', x['signal'], '验效'))
        made.append(cur.lastrowid)
    conn.commit()
    return made


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
        tokens = sum(c[k] or 0 for k in ('tokens_in', 'tokens_out', 'cache_read', 'cache_write'))
        out.append(f'\n### #{c["id"]}「{c["name"]}」{role} · {where}')
        out.append(f'- 状态：{c["state"]}' + (f'，结束原因 {c["ended_reason"]}' if c['ended_reason'] else '')
                   + (f'，交接原因 {short(c["wrap_reason"], 120)}' if c['wrap_reason'] else '')
                   + f'；开始 {hm(c["started_at"])}，时长 {studio.camera_minutes(c) or 0:.0f} 分钟'
                   + f'；token {tokens:,}' + (f'，上下文 {c["context_pct"]:.0%}' if c['context_pct'] else '')
                   + (f'，模型 {c["model"]}' if c['model'] else '') + (f'；由 {c["last_from"]} 唤起' if c['last_from'] else ''))
        out.append('- 目的：' + short((c['prompt'] or '').replace('\n', ' '), 300))
        kinds = rows(conn, 'SELECT kind, COUNT(*) n FROM messages WHERE camera_id=? GROUP BY kind', c['id'])
        reps = rows(conn, 'SELECT id, kind, status, answered_by, summary FROM reports WHERE source_camera=? ORDER BY id', c['id'])
        out.append('- 链路：消息 ' + ('，'.join(f'{k["kind"]} {k["n"]}' for k in kinds) or '无')
                   + '；发起上报 ' + ('，'.join(f'#{r["id"]} {studio.KIND_LABEL.get(r["kind"], r["kind"])}→{r["status"]}'
                                              f'{"/" + studio.LEVEL_LABEL.get(r["answered_by"], r["answered_by"]) if r["answered_by"] else ""}'
                                              for r in reps) or '无'))
        replies = rows(conn, 'SELECT text FROM messages WHERE camera_id=? AND kind="reply" AND ts>? ORDER BY id DESC LIMIT 2',
                       c['id'], since_iso)
        for r in reversed(replies):
            out.append('- 效果（回复）：' + short((r['text'] or '').replace('\n', ' '), 400))
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
                   + f'「{short(r["summary"], 80)}」来自 {r["source_name"] or r["source_role"]}'
                   + (f'；经 {"→".join(levels)}' if levels else '') + (f'；超时自动上交 {auto} 次' if auto else '')
                   + f'；{r["status"]}' + (f' by {studio.LEVEL_LABEL.get(r["answered_by"], r["answered_by"])}' if r['answered_by'] else '')
                   + (f'：{short((r["answer"] or "").replace(chr(10), " "), 200)}' if r['answer'] else '') + took)
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
                out.append(f'- 阻塞：{short(data["blocker"], 200)}')
            h = data.get('handoff') or {}
            if h:
                out.append(f'- 交接：做到 {short(h.get("progress"), 200)}；下一步 {short(h.get("next"), 200)}')
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
    out += [f'- [{m["ts"][5:16]} {m["channel"]}] {short((m["text"] or "").replace(chr(10), " "), 300)}' for m in ms]
    return '\n'.join(out), len(ms)


def sec_decisions(conn, since_iso):
    ds = rows(conn, 'SELECT * FROM decisions WHERE ts>? ORDER BY id', since_iso)
    over = rows(conn, 'SELECT * FROM decisions WHERE status="已推翻" ORDER BY id DESC LIMIT 5')
    out = [f'## 5 决策（{len(ds)} 条）']
    out += [f'- {d["actor_name"]}：{short(d["summary"], 200)}（{d["status"]}）' for d in ds]
    out += [f'- 被推翻：{d["actor_name"]}：{short(d["summary"], 150)}' + (f'（用户：{short(d["review_note"], 150)}）' if d['review_note'] else '')
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
    out += [f'- [{m["ts"][11:16]}] {short((m["text"] or "").replace(chr(10), " "), 150)}' for m in ms]
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
                   + short(json.dumps(d, ensure_ascii=False), 200))
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
    out += [f'- #{x["id"]} [{x["status"]}] {short(x["title"], 120)}' + (f'（主脑：{short(x["note"], 120)}）' if x['note'] else '')
            for x in items]
    done = [x for x in items if x['status'] == '采纳' and x['decided_at']]
    out.append(f'\n### 9b 已采纳的改动（{len(done)} 条，逐条看落实时间之后的素材：问题还在吗）')
    out += [f'- #{x["id"]} 落实于 {x["decided_at"][5:16]}：{short(x["title"], 100)}' for x in done]
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
            out.append(f'- 已完成 {studio.seq_label(f, s["key"])}（{max(ts)[:16]}）：{short(h.get("progress") or data.get("title") or "", 300)}'
                       + (f'；改动：{short(h.get("files"), 200)}' if h.get('files') else ''))
    files = []
    for base in (studio.CODE / 'mashu', studio.CODE / 'web', studio.CODE / 'docs', studio.CODE / 'playbooks'):
        if base.is_dir():
            files += [p for p in base.rglob('*') if p.is_file() and p.suffix in ('.py', '.js', '.md', '.json', '.html', '.css')]
    recent = sorted(((p.stat().st_mtime, p) for p in files if p.stat().st_mtime > since), reverse=True)[:40]
    out.append('- 窗口内改过的文件：' + ('，'.join(f'{p.relative_to(studio.CODE)}（{hm(t)}）' for t, p in recent) or '无'))
    return '\n'.join(out)


def claude_docs_usage(transcript, root):
    seen = {}
    for entry in studio.read_transcript(transcript):
        if entry.get('type') != 'assistant' or entry.get('isSidechain'):
            continue
        content = (entry.get('message') or {}).get('content')
        for tool in content if isinstance(content, list) else []:
            if tool.get('type') == 'tool_use' and isinstance(tool.get('input'), dict):
                studio.docs.update_usage(seen, tool.get('name', ''), tool['input'], root, entry.get('cwd') or root)
                path = str(tool['input'].get('file_path') or tool['input'].get('notebook_path') or '')
                if tool.get('name') in CODE_TOOLS and path and not path.endswith('.md'):
                    seen['code_edits'] = seen.get('code_edits', 0) + 1
    return seen


CODE_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')


def docs_sections(conn, since):
    drift = [row for row in studio.skill_inventory(conn) if row['status'] not in ('来源', '一致', *studio.skills_dist.PINNED)
             or row['project'] is None and row['status'] == '一致' and not row['linked']]
    lines = ['## caesar-docs 漂移', *(f'- {row["status"] if row["status"] != "一致" else "需改为软链接"} {row["path"]}'
                                     for row in drift)]
    if not drift:
        lines.append('所有已登记安装位置一致。')
    totals, odd = {}, []
    cameras = rows(conn, '''SELECT c.*, m.v AS docs_usage FROM cameras c
                           LEFT JOIN meta m ON m.k='docs-usage:' || c.id
                           WHERE c.started_at>? OR c.last_active>? ORDER BY c.id''', since, since)
    for cam in cameras:
        project, role = cam['film_id'] or '全局', studio.LEVEL_LABEL.get(cam['role'], cam['role'])
        counts = totals.setdefault((project, role), [0, 0, 0, 0, 0, 0, 0])
        counts[0] += 1
        seen = json.loads(cam['docs_usage'] or '{}')
        if studio.cam_provider(conn, cam) == 'claude':
            f = studio.f_of(conn, cam)
            root = Path(f['path']).resolve() if f else studio.CODE
            seen = claude_docs_usage(studio.transcript_file(conn, cam), root) or seen
        if not seen:
            continue
        counts[1] += 1
        read = seen.get('read') or []
        first = bool(read and read[0].endswith('docs/indexes/docs.md'))
        num, validated, edited, code = len(read), bool(seen.get('validated')), bool(seen.get('edited')), seen.get('code_edits') or 0
        for index, value in enumerate((first, num, validated, edited, bool(code)), 2):
            counts[index] += value
        miss = [name for name, ok in (('首读入口', first), ('docs 回写', edited), ('validate', validated)) if not ok]
        if code and miss:     # 只列异常：改了代码却没读入口 / 没回写 / 没校验
            odd.append(f'- #{cam["id"]} {project} · {role} · 改代码 {code} 处 · 读 {num} 篇 · 缺 {"、".join(miss)}')
    lines += ['', '## docs 使用统计', '按项目与角色汇总可观察的直接阅读、校验和写入工具调用（只统计 Claude 会话的代码改动）。']
    lines += [f'- {project} · {role} · 会话 {total}（有记录 {observed}） · 首读入口 {first} · 阅读合计 {read} 篇 · '
              f'validate {validated} · docs 写入 {edited} · 改了代码 {coded}'
              for (project, role), (total, observed, first, read, validated, edited, coded) in sorted(totals.items())]
    lines += ['', f'### 异常会话（改了代码却缺入口 / 回写 / validate，{len(odd)} 个）'] + (odd[:20] or ['无'])
    if len(odd) > 20:
        lines.append(f'- 其余 {len(odd) - 20} 个略')
    return '\n'.join(lines)


ACTION_LABEL = {'restart-server': '重启服务', 'release': '发布并重启', 'release-rollback': '回滚', 'sign': '签收 / 批复',
                'kickoff': '批准开工', 'kickoff-exec': '开工执行配置', 'archive': '归档', 'external-answer': '回复外部请求',
                'interrupt': '打断会话', 'mute': '静音', 'cut': '停止会话', 'retake': '重开会话', 'preset-save': '保存预设',
                'reject': '打回'}
# 高频操作是被什么引出来的：在别人发的消息 / 卡片里找这些说法
ACTION_CUES = {'restart-server': ('重启服务',), 'release': ('重启服务', '发布'), 'kickoff': ('批准开工',),
               'kickoff-exec': ('批准开工',), 'sign': ('批准', '签收'), 'archive': ('归档',), 'external-answer': ('批准',),
               'interrupt': ('打断',), 'cut': ('停掉', '停止')}
HEAVY_PER_DAY = 10


def fmt_tok(n):
    n = n or 0
    if n >= 1e8:
        return f'{n / 1e8:.2f} 亿'
    if n >= 1e4:
        return f'{n / 1e4:.0f} 万'
    return f'{n:,}'


def cue_sources(conn, cues, day):
    """某天里提到这些说法的消息 / 卡片，按来源计数；再在提示词和角色手册里找同样的说法。"""
    like = ' OR '.join('text LIKE ?' for _ in cues)
    a = [f'%{c}%' for c in cues]
    ms = rows(conn, f'SELECT sender, camera_name, channel, MIN(text) text, COUNT(*) n FROM (SELECT * FROM messages '
                    f'WHERE substr(ts,1,10)=? AND sender NOT IN ("executive") AND kind!="chat" AND ({like}) GROUP BY sender, text) '
                    f'GROUP BY sender, camera_name ORDER BY n DESC LIMIT 5', day, *a)
    out = [f'{studio.LEVEL_LABEL.get(m["sender"], m["sender"])}{("「" + m["camera_name"] + "」") if m["camera_name"] else ""}'
           f' {m["n"]} 条（如：{short((m["text"] or "").replace(chr(10), " "), 80)}）' for m in ms]
    files = []
    for base in (studio.PROMPTS, studio.KMAP / 'references' / 'roles', studio.MEMORY):
        for p in sorted(base.glob('*.md')) if base.is_dir() else []:
            try:
                for i, line in enumerate(p.read_text(errors='replace').splitlines(), 1):
                    if any(c in line for c in cues) and re.search(r'点|请用户|让用户|告诉用户', line):
                        files.append(f'{p.name}:{i} {short(line.strip(), 80)}')
            except OSError:
                continue
    return out, files[:5]


def sec_user_ops(conn, since, since_iso):
    """用户自己的操作：最耗用户精力的地方（11-26/27 一天点 35 次重启，日报一直没看见）。"""
    day0 = datetime.date.fromtimestamp(since).isoformat()
    out = ['## 用户操作（网页 / 终端里用户亲手做的事；同类一天 ≥10 次的单列并找出是谁引出来的）']
    au = rows(conn, 'SELECT action, COUNT(*) n FROM audit WHERE actor="executive" AND ts>? GROUP BY action ORDER BY n DESC', since_iso)
    out.append('- 窗口内按类型：' + ('，'.join(f'{ACTION_LABEL.get(x["action"], x["action"])} {x["n"]}' for x in au) or '无'))
    rs = rows(conn, 'SELECT kind, msg_type, gate, answer, meta FROM reports WHERE answered_by="executive" AND closed_at>?', since)
    if rs:
        by = {}
        rec = hit = 0
        for r in rs:
            label = studio.KIND_LABEL.get(r['kind'], r['kind']) + (f'[{r["gate"]}]' if r['gate'] else '')
            by[label] = by.get(label, 0) + 1
            meta = json.loads(r['meta'] or '{}') if (r['meta'] or '').startswith('{') else {}
            if meta.get('recommend'):
                rec += 1
                hit += (r['answer'] or '').strip().startswith(str(meta['recommend']).strip())
        out.append(f'- 用户批复卡片 {len(rs)} 张：' + '，'.join(f'{k} {v}' for k, v in sorted(by.items(), key=lambda x: -x[1]))
                   + (f'；带推荐项的 {rec} 张，点了推荐 {hit} 张（{hit / rec:.0%}）' if rec else '；都没带推荐项'))
    chats = rows(conn, 'SELECT channel, COUNT(*) n FROM messages WHERE sender="executive" AND kind="chat" AND ts>? '
                       'GROUP BY channel ORDER BY n DESC', since_iso)
    out.append(f'- 聊天 {sum(c["n"] for c in chats)} 条' + (('：' + '，'.join(f'{c["channel"]} {c["n"]}' for c in chats[:6])) if chats else ''))
    heavy = rows(conn, 'SELECT substr(ts,1,10) d, action, COUNT(*) n FROM audit WHERE actor="executive" AND substr(ts,1,10)>=? '
                       'GROUP BY d, action HAVING n>=? ORDER BY d, n DESC', day0, HEAVY_PER_DAY)
    for h in heavy:
        label = ACTION_LABEL.get(h['action'], h['action'])
        out.append(f'\n### {h["d"][5:]} {label} {h["n"]} 次（全天计数）')
        details = rows(conn, 'SELECT detail, COUNT(*) n FROM audit WHERE actor="executive" AND action=? AND substr(ts,1,10)=? '
                             'GROUP BY detail ORDER BY n DESC LIMIT 3', h['action'], h['d'])
        out.append('- 记录：' + '，'.join(f'{short(x["detail"] or "（无说明）", 60)} ×{x["n"]}' for x in details))
        cues = ACTION_CUES.get(h['action'])
        if cues:
            src, files = cue_sources(conn, cues, h['d'])
            out.append('- 当天提到「' + '/'.join(cues) + '」的消息：' + ('；'.join(src) or '没找到'))
            if files:
                out.append('- 提示词 / 手册里同样的说法：' + '；'.join(files))
    return '\n'.join(out)


BUDGET_RE = re.compile(r'token[^\n]{0,12}?(?:预算|目标|上限)[^\n\d]{0,20}?(\d+(?:\.\d+)?)\s*(亿|万|[KkMmBbGg])?', re.I)
UNIT = {'亿': 1e8, '万': 1e4, 'k': 1e3, 'm': 1e6, 'b': 1e9, 'g': 1e9}


def parse_budget(text):
    """方案里的「token 预算 / 目标 / 上限」行 → (token 数, 是否按单个会话, 原文行)；没有返回 None。"""
    for line in (text or '').splitlines():
        m = BUDGET_RE.search(line)
        if m:
            n = float(m.group(1)) * UNIT.get((m.group(2) or '').lower(), 1)
            return int(n), '会话' in line[:m.end()], line.strip()   # 「会话 token 目标 30M」按单会话；「预算 20M，单会话超 30M 交接」按合计
    return None


def plan_text(f, key):
    d = studio.seq_dir(f, key)
    parts = []
    for p in [d / 'docs' / 'plan.md', *sorted((d / 'docs').glob('*.md')), d / 'plan.md'] if d else []:
        try:
            if p.is_file():
                parts.append(p.read_text(errors='replace'))
        except OSError:
            continue
    return '\n'.join(parts)


def sec_usage_anomaly(conn, since):
    out = ['## 用量异常（token 含缓存读写）']
    cams = rows(conn, 'SELECT id, name, role, film_id, seq_key, COALESCE(tokens_in,0)+COALESCE(tokens_out,0)+COALESCE(cache_read,0)'
                      '+COALESCE(cache_write,0) t FROM cameras WHERE started_at>? OR last_active>?', since, since)
    med = _median([c['t'] for c in cams if c['t']]) or 0
    big = sorted((c for c in cams if med and c['t'] > 5 * med), key=lambda c: -c['t'])
    where = lambda c: (c['film_id'] or '全局') + (f' #{studio.seq_num(c["seq_key"])}' if c['seq_key'] else '')  # noqa: E731
    out.append(f'- 会话 {len(cams)} 个，token 中位数 {fmt_tok(med)}；超过 5 倍的 {len(big)} 个'
               + ('：' + '，'.join(f'#{c["id"]}「{c["name"]}」{studio.LEVEL_LABEL.get(c["role"], c["role"])} {where(c)} '
                                  f'{fmt_tok(c["t"])}（{c["t"] / med:.0f} 倍）' for c in big[:10]) if big else ''))
    feats = {}
    for c in cams:
        if c['film_id'] and c['seq_key']:
            feats.setdefault((c['film_id'], c['seq_key']), 0)
    rows_b, nobudget = [], []
    for (fid, key) in feats:
        tot = one(conn, 'SELECT COUNT(*) n, SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)+COALESCE(cache_read,0)+COALESCE(cache_write,0)) t, '
                        'MAX(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)+COALESCE(cache_read,0)+COALESCE(cache_write,0)) mx '
                        'FROM cameras WHERE film_id=? AND seq_key=?', fid, key)
        f = studio.film(conn, fid)
        b = parse_budget(plan_text(f, key)) if f else None
        label = f'{fid} #{studio.seq_num(key)}'
        if not b:
            nobudget.append((tot['t'] or 0, label))
            continue
        n, per, line = b
        actual = tot['mx'] if per else tot['t']
        rows_b.append(f'- {label}：预算 {fmt_tok(n)}{"/会话" if per else "（合计）"}（原文：{short(line, 60)}）；实际 '
                      f'{"单会话最大" if per else "合计"} {fmt_tok(actual)}，{tot["n"]} 个会话'
                      + (f' → **超出 {(actual or 0) / n:.1f} 倍**' if n and (actual or 0) > n else ' → 未超'))
    out += rows_b
    if nobudget:
        nobudget.sort(reverse=True)
        out.append(f'- 未写预算的功能 {len(nobudget)} 个（按累计 token）：' + '，'.join(f'{lb} {fmt_tok(t)}' for t, lb in nobudget[:10]))
    today = datetime.date.today()
    days = [(today - datetime.timedelta(days=i)).isoformat() for i in range(5, -1, -1)]
    us = rows(conn, 'SELECT COALESCE(film_id, "全局") f, day, SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)+COALESCE(cache_read,0)'
                    '+COALESCE(cache_write,0)) t FROM usage WHERE day>=? GROUP BY f, day', days[0])
    per = {}
    for u in us:
        per.setdefault(u['f'], {})[u['day']] = u['t'] or 0
    for fid, by in sorted(per.items(), key=lambda x: -sum(x[1].values())):
        recent = [d for d in days[-3:]]
        cells = []
        for d in recent:
            prev = [by.get(x, 0) for x in days[days.index(d) - 3: days.index(d)]]
            avg = sum(prev) / 3
            v = by.get(d, 0)
            flag = ('' if v <= 1e7 or v < 2 * avg else
                    f'（前 3 天均 {fmt_tok(avg)}，**{v / avg:.1f} 倍**）' if avg else '（前 3 天没有用量，**新增**）')
            cells.append(f'{d[5:]} {fmt_tok(v)}' + ('（进行中）' if d == today.isoformat() else '') + flag)
        out.append(f'- {fid} 每日：' + ' · '.join(cells))
    return '\n'.join(out)


def sec_incidents(conn, since):
    ensure_tables(conn)
    xs = rows(conn, 'SELECT * FROM self_iterate_incidents WHERE ts>? ORDER BY id', since)
    out = [f'## 事故复盘（窗口内 {len(xs)} 次；已出条目在「往期建议」里，来源标「事故」）']
    for x in xs:
        n = one(conn, 'SELECT COUNT(*) n FROM self_iterate_items WHERE run_id=?', x['run_id'])['n'] if x['run_id'] else 0
        out.append(f'- [{hm(x["ts"])}] {x["title"]}（{x["status"]}' + (f'，复盘 #{x["run_id"]} 出 {n} 条' if x['run_id'] else '')
                   + f'）：{short(x["detail"], 160)}')
    return '\n'.join(out)


def assemble(parts, limit):
    """parts = [(优先级, 文字, 单节上限)]：先按单节上限截，再按优先级（数小的先）分总预算，超出的截断或省略，并写明省略了什么。
    输出保持 parts 的顺序。"""
    limit = int(limit)
    texts = [clip(t, n) for _, t, n in parts]
    budget = limit - 600                         # 给省略说明留位置
    keep = [''] * len(parts)
    notes = []
    for i in sorted(range(len(parts)), key=lambda i: (parts[i][0], i)):
        t = texts[i]
        title = (t.splitlines() or [''])[0].lstrip('# ')[:40]
        if len(t) + 2 <= budget:
            keep[i], budget = t, budget - len(t) - 2
        elif budget > 800:
            keep[i] = clip(t, budget - 2)
            notes.append(f'截断「{title}」（原 {len(t)} 字，留 {budget - 2} 字）')
            budget = 0
        else:
            notes.append(f'省略「{title}」（{len(t)} 字）')
    text = '\n\n'.join(t for t in keep if t)
    if notes:
        text += f'\n\n## 已截断 / 省略的节（总长上限 {limit} 字，按「用户原话与操作 > 事故与复发 > 异常 > 其余」保留）\n' + '\n'.join(f'- {n}' for n in notes)
    return text


def collect_material(conn, hours=None, governance=None, incident=None):
    """最近 N 小时的素材 → (文字, 是否有活动)。incident：只复盘这一件事故的小素材。"""
    ensure_tables(conn)
    if incident:
        since = min(float(incident['ts'] or time.time()), time.time()) - INCIDENT_WINDOW_HOURS * 3600
        hours = (time.time() - since) / 3600
    else:
        hours = float(hours or cfg('self_iterate_hours'))
        since = time.time() - hours * 3600
    since_iso = iso(since)
    s1, n_cam = sec_sessions(conn, since, since_iso)
    s2, n_rep = sec_reports(conn, since)
    s4, n_user = sec_user(conn, since_iso)
    if incident:
        head = (f'# 麻薯电脑秘书 · 事故小复盘素材（{iso(since)} 至 {studio.now_iso()}）\n\n## 0 事故\n'
                f'- 触发：{incident["title"]}（{incident["key"]}，{hm(incident["ts"])}）\n- 详情：{incident["detail"] or "无"}\n'
                '- 只复盘这一件事：原因、影响、为什么没被拦住、怎么防止再发生。sessions 只写和它有关的会话，建议最多 4 条。')
        parts = [(0, head, 3000), (1, sec_user_ops(conn, since, since_iso), 3000), (1, s4, 4000),
                 (2, sec_runtime(conn, since), 5000), (2, sec_system(conn, since_iso), 3000), (2, sec_effects(conn), 4000),
                 (3, s1, 10000), (3, s2, 5000), (3, sec_decisions(conn, since_iso), 2000), (4, sec_changes(conn, since), 3000),
                 (4, sec_past(conn), 4000), (4, sec_rules(conn), 12000)]
        return assemble(parts, cfg('self_iterate_incident_max_chars')), True
    head = (f'# 麻薯电脑秘书 · 最近 {hours:g} 小时素材（{iso(since)} 至 {studio.now_iso()}）\n\n## 0 概况\n'
            f'- 项目：' + ('，'.join(f'{f["title"]}[{f["id"]}]' for f in studio.films(conn)) or '无') +
            f'\n- 会话 {n_cam} 个，上报 {n_rep} 张，用户消息 {n_user} 条')
    # 优先级：用户原话与操作 > 事故与复发 > 异常（用量、docs、运行日志）> 往期 / 规则 / 改动（防重复建议）> 其余明细
    parts = [(0, head, 2000), (1, sec_user_ops(conn, since, since_iso), 5000), (1, s4, 6000),
             (2, sec_incidents(conn, since), 3000), (2, sec_effects(conn), 8000), (2, sec_runtime(conn, since), 4000),
             (3, sec_usage_anomaly(conn, since), 4000), (3, governance if governance is not None else docs_sections(conn, since), 5000),
             (4, sec_past(conn), 5000), (4, sec_rules(conn), 18000), (4, sec_changes(conn, since), 4000),
             (5, s1, 20000), (5, s2, 8000), (5, sec_features(conn, since), 8000), (5, sec_decisions(conn, since_iso), 3000),
             (5, sec_usage(conn, since), 2000), (5, sec_system(conn, since_iso), 3000),
             (6, '## 8 复盘数据（近 7 天）\n' + studio.retro_text(conn, days=7), 5000), (6, sec_files(conn), 4000)]
    return assemble(parts, cfg('self_iterate_max_chars')), bool(n_cam or n_rep or n_user)


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
    raw = [x for x in _lst(d.get('suggestions'))[:12] if _s(x.get('title')).strip()]
    for x, r in zip(out['suggestions'], raw):
        x['area'] = x['area'] if x['area'] in AREAS else 'other'
        sig = load_signal(r.get('signal'))
        x['signal'] = json.dumps(sig, ensure_ascii=False) if sig else ''
        if x['title'].startswith('复发'):
            x['priority'] = '高'
    return out


# ---------- 运行、入库、存档 ----------

def archive_dir(run):
    d = studio.HOME / 'self-iterate' / f'{run["day"]}-{run["id"]}'
    d.mkdir(parents=True, exist_ok=True)
    return d


def fail(conn, rid, why, notify=True):
    conn.execute('UPDATE self_iterate_runs SET status="failed", error=?, ended_at=? WHERE id=?', (why, time.time(), rid))
    conn.commit()
    conn.execute('UPDATE self_iterate_incidents SET status="failed" WHERE run_id=?', (rid,))
    conn.commit()
    if notify:
        r = run_row(conn, rid)
        if r['trigger'] == 'incident':
            studio.post(conn, 'system', 'assistant', 'notice', f'事故小复盘（#{rid}）失败：{why}。这件事请主脑直接看日志处理。', target='web')
            return
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
            if x.get('signal'):
                out.append(f'  - 生效信号：{signal_text(load_signal(x["signal"]))}')
    effs = rep.get('effects') or []
    if effs:
        out += ['', f'## 自动验效（{len(effs)}）'] + [effect_line(e) for e in effs]
    fu = rep.get('followups') or []
    out += ['', f'## 已采纳改动的效果（{len(fu)}）'] + [f'- {x["item"]} {x["status"]}：{x["evidence"]}' for x in fu]
    out += ['', f'## 会话 review（{len(rep["sessions"])}）']
    out += [f'- {s["session"]}（{s["role"]} · {s["project"]}）效果 {s["effect"]}：目的 {s["purpose"]}；链路 {s["chain"]}；{s["comment"]}'
            for s in rep['sessions']]
    if rep.get('docs_governance'):
        out += ['', rep['docs_governance']]
    return '\n'.join(out) + '\n'


def store_report(conn, rid, rep, incident=None):
    run = run_row(conn, rid)
    source = '事故' if incident else '日报'
    groups = (('suggestion', rep['suggestions']), ('memory', rep['memory_candidates']), ('workflow', rep['workflow_candidates']))
    for sec, xs in groups:
        for x in xs:
            conn.execute('INSERT INTO self_iterate_items(run_id, day, section, area, target, title, evidence, change, priority, '
                         'signal, source) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (rid, run['day'], sec, x.get('area') or sec, x['target'], x['title'], x['evidence'], x['change'],
                          x.get('priority') or '', x.get('signal') or '', source))
    conn.commit()
    effs = rep.get('effects') or []
    auto = recurrence_items(conn, run, effs) if not incident else []
    conn.execute('UPDATE self_iterate_runs SET status="ok", ended_at=?, report=? WHERE id=?',
                 (time.time(), json.dumps(dict(summary=rep['summary'], sessions=rep['sessions'], followups=rep.get('followups') or [],
                                               docs_governance=rep.get('docs_governance', ''), effects=effs,
                                               incident=dict(incident) if incident else None),
                                          ensure_ascii=False), rid))
    if incident:
        conn.execute('UPDATE self_iterate_incidents SET status="done" WHERE id=?', (incident['id'],))
    conn.commit()
    items = rows(conn, 'SELECT * FROM self_iterate_items WHERE run_id=? ORDER BY id', rid)
    d = archive_dir(run)
    (d / 'report.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    (d / 'report.md').write_text(report_md(run, rep, items))
    n = len(rep['suggestions'])
    if incident:
        lines = '\n'.join(f'- #{x["id"]} [{x["priority"] or "-"}] {x["title"]}' for x in items) or '- （没有建议）'
        studio.post(conn, 'system', 'assistant', 'report',
                    f'[系统] 事故小复盘（#{rid}）：{incident["title"]}。\n{clip(rep["summary"], 300)}\n{lines}\n'
                    f'请当轮处理：mashu self-iterate list --status 待定 看全文，能定的 adopt（带 --signal）/ reject / cover；'
                    f'不用打扰用户，结果会写进下一份简报。报告：{d / "report.md"}', target='assistant')
        return items
    bad = [e for e in effs if e['verdict'] in ('复发', '未见效')]
    studio.post(conn, 'system', 'assistant', 'notice',
                f'自迭代报告（{run["day"]}）好了：{n} 条改进建议、{len(items) - n - len(auto)} 条可沉淀项待定'
                + (f'；自动验效发现 {len(bad)} 条复发 / 未见效（' + '、'.join(f'#{e["id"]}' for e in bad) + '），已补高优先级条目' if bad else '')
                + '。请在下一份简报前处理完：mashu self-iterate show 看，能自己定的 adopt（带 --signal）/ reject / cover / outdated，'
                '只把产品方向拿不准的问用户；复发的要查为什么上次没生效',
                target='web')
    return items


def execute(rid, incident_id=None):
    """跑一次（已占位的 running 记录）。在后台线程或 CLI 里调用，用自己的连接；任何异常都记成 failed。"""
    conn = studio.db()
    try:
        ensure_tables(conn)
        run = run_row(conn, rid)
        d = archive_dir(run)
        incident = one(conn, 'SELECT * FROM self_iterate_incidents WHERE id=?', incident_id) if incident_id else None
        try:
            if incident:
                governance, effs = '', []
                text, active = collect_material(conn, incident=incident)
            else:
                governance = docs_sections(conn, time.time() - float(cfg('self_iterate_hours')) * 3600)
                effs = effects(conn)
                text, active = collect_material(conn, governance=governance)
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
            rep['docs_governance'] = governance
            rep['effects'] = effs
            store_report(conn, rid, rep, incident)
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
    incs = rows(conn, 'SELECT title, status FROM self_iterate_incidents WHERE ts>? ORDER BY id', time.time() - 86400)
    inc = ('；近 24 小时事故复盘 ' + str(len(incs)) + ' 次：' + '；'.join(f'{x["title"]}（{x["status"]}）' for x in incs[-3:])) if incs else ''
    if not n and not inc:
        return ''
    return f'7. 自迭代：{n} 条待定（mashu self-iterate show）' + inc


def api_data(conn, day=''):
    ensure_tables(conn)
    days = rows(conn, 'SELECT day, GROUP_CONCAT(status) statuses FROM self_iterate_runs GROUP BY day ORDER BY day DESC LIMIT 14')
    day = day or (days[0]['day'] if days else '')
    runs = rows(conn, 'SELECT id, day, trigger, status, started_at, ended_at, model, input_chars, tokens_in, tokens_out, cache_read, '
                      'cache_write, cost, error, archive FROM self_iterate_runs WHERE day=? ORDER BY id', day)
    ok = one(conn, 'SELECT id, report FROM self_iterate_runs WHERE day=? AND status="ok" AND trigger!="incident" '
                   'ORDER BY id DESC LIMIT 1', day)
    report = json.loads(ok['report'] or '{}') if ok else {}
    items = rows(conn, 'SELECT * FROM self_iterate_items WHERE run_id=? ORDER BY id', ok['id']) if ok else []
    # 当天的事故复盘：条目单列，来源标「事故」
    incidents = []
    for r in rows(conn, 'SELECT id, report FROM self_iterate_runs WHERE day=? AND status="ok" AND trigger="incident" ORDER BY id', day):
        rr = json.loads(r['report'] or '{}')
        incidents.append(dict(run_id=r['id'], incident=rr.get('incident') or {}, summary=rr.get('summary') or '',
                              items=rows(conn, 'SELECT * FROM self_iterate_items WHERE run_id=? ORDER BY id', r['id'])))
    for x in items + [y for i in incidents for y in i['items']]:
        x['signal_text'] = signal_text(load_signal(x.get('signal'))) if x.get('signal') else ''
    return dict(days=days, day=day, runs=runs, run_id=ok['id'] if ok else None, report=report, items=items,
                incidents=incidents, pending=pending_count(conn))


# ---------- CLI ----------

def add_parser(sub):
    s = sub.add_parser('self-iterate', help='每日自迭代报告')
    s.add_argument('action', choices=['run', 'material', 'show', 'list', 'effects', 'adopt', 'reject', 'cover', 'outdated', 'reset',
                                      'signal'])
    s.add_argument('ids', nargs='*', help='adopt/reject/cover/outdated/reset/signal：条目编号；show：日期')
    s.add_argument('--force', action='store_true'); s.add_argument('--note'); s.add_argument('--status')
    s.add_argument('--signal', help='采纳 / 已覆盖时写明怎么看出生效（signal 动作只改信号不改状态）。' + SIGNAL_HELP
                   + '。例：--signal "audit:restart-server@executive | 下降"')
    s.add_argument('--days', type=int, default=14); s.add_argument('--hours', type=float)


def item_line(x):
    return (f'#{x["id"]} [{x["status"]}][{SECTIONS.get(x["section"], x["section"])}' + (f'·{x["area"]}' if x['section'] == 'suggestion' else '')
            + (f'·{x["priority"]}' if x['priority'] else '') + f'] {x["title"]}'
            + (f'\n    位置：{x["target"]}' if x['target'] else '') + (f'\n    依据：{x["evidence"]}' if x['evidence'] else '')
            + (f'\n    改法：{x["change"]}' if x['change'] else '')
            + (f'\n    生效信号：{signal_text(load_signal(x["signal"]))}' if x.get('signal') else '')
            + (f'\n    来源：{x["source"]}' if x.get('source') and x['source'] != '日报' else '')
            + (f'\n    备注：{x["note"]}（{x["decided_by"]}）' if x['note'] else ''))


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
        effs = rep.get('effects') or []
        if effs:
            out += ['', f'自动验效（{len(effs)}）：'] + ['  ' + effect_line(e)[2:] for e in effs]
        fu = rep.get('followups') or []
        if fu:
            out += ['', f'已采纳改动的效果（{len(fu)}）：'] + [f'  {x["item"]} {x["status"]}：{x["evidence"]}' for x in fu]
        out += ['', f'会话 review（{len(rep.get("sessions") or [])}）：']
        out += [f'  {s["session"]} {s["role"]} {s["effect"]}：{s["comment"]}' for s in rep.get('sessions') or []]
        if rep.get('docs_governance'):
            out += ['', rep['docs_governance']]
    for inc in data['incidents']:
        out += ['', f'事故小复盘 #{inc["run_id"]}：{inc["incident"].get("title", "")}', '  ' + (inc['summary'] or '（无总结）')]
        out += [item_line(x) for x in inc['items']]
    if data['run_id'] or data['incidents']:
        out += ['', '采纳：mashu self-iterate adopt <编号…> --signal "…" --note "怎么落实"；不采纳：reject；已被别的改动解决：cover（--note 写清是哪个机制覆盖的）；'
                '不再成立：outdated；撤销：reset；只补信号：signal <编号> --signal "…"；验效：effects']
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
    elif args.action == 'effects':
        effs = effects(conn, days=args.days)
        print('\n'.join(effect_line(e) for e in effs) or '近期没有采纳 / 已覆盖的建议')
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
        try:
            given = parse_signal(args.signal) if args.signal is not None else None
        except ValueError as exc:
            raise SystemExit(f'--signal 不对：{exc}\n{SIGNAL_HELP}')
        if given and given['kind'] != 'none':
            try:
                signal_times(conn, given)
            except ValueError as exc:
                raise SystemExit(f'--signal 查不了：{exc}')
        if args.action == 'signal' and not given:
            raise SystemExit('signal 动作要带 --signal "…"。' + SIGNAL_HELP)
        items = []
        for i in args.ids:
            x = one(conn, 'SELECT * FROM self_iterate_items WHERE id=?', int(i.lstrip('#'))) if i.lstrip('#').isdigit() else None
            if not x:
                raise SystemExit(f'没有条目 {i}')
            sig = given or load_signal(x.get('signal'))
            if args.action in ('adopt', 'cover') and x['section'] == 'suggestion' and not sig:
                raise SystemExit(f'#{x["id"]} 没有生效信号，采纳 / 判已覆盖要带 --signal（之后的报告据此自动验效；确实无法量化写 --signal 无）。\n'
                                 + SIGNAL_HELP)
            items.append((x, sig))
        for x, sig in items:
            sig_json = json.dumps(sig, ensure_ascii=False) if sig else (x.get('signal') or '')
            if args.action == 'signal':
                conn.execute('UPDATE self_iterate_items SET signal=? WHERE id=?', (sig_json, x['id']))
                conn.commit()
                studio.audit(conn, who, 'self-iterate-signal', detail=f'#{x["id"]} {signal_text(sig)}')
                print(f'#{x["id"]} 信号 → {signal_text(sig)}')
                continue
            status = STATUS_OF[args.action]
            note = args.note if args.note is not None else ('' if args.action == 'reset' else x['note'])
            conn.execute('UPDATE self_iterate_items SET status=?, note=?, decided_by=?, decided_at=?, signal=? WHERE id=?',
                         (status, note, '' if args.action == 'reset' else who,
                          '' if args.action == 'reset' else studio.now_iso(), sig_json, x['id']))
            conn.commit()
            studio.audit(conn, who, f'self-iterate-{args.action}', detail=f'#{x["id"]} {x["title"]}' + (f'：{note}' if note else ''))
            print(f'#{x["id"]} → {status}：{x["title"]}' + (f'（信号 {signal_text(sig)}）' if sig else ''))
