"""工作流面板（#7）：项目页「工作流」视图的数据。只读，只用标准库。

dashboard(conn, film_id) 列出项目的工作流（项目默认工作流、项目 .mashu/playbooks/*.json、功能用过的模板），
每个工作流的阶段，以及每个功能在面板上要的额外数据：所在列、在这一列待了多久、完成时间、
按 <工作流id>.dashboard.json 声明取出的专属字段。功能的状态、执行者、上报这些网页已经从 /api/state 拿到，这里不重复。

面板配置放在工作流模板旁边的 dashboards/ 子目录：<工程>/.mashu/playbooks/dashboards/<工作流id>.json（项目自己的），
麻薯通用的在 playbooks/dashboards/（不和模板放同一层，免得被当成工作流模板），
字段说明见 docs/workflows/project-assets.md「工作流面板配置」。解析任何配置 / 文件出错只让那一项为空。
"""
import datetime
import json
import re
from pathlib import Path

import studio

MAX_READ = 512_000          # 每个文件最多读这么多字节
MAX_FIELDS = 12             # 每张卡片最多几个专属字段
FIELD_TYPES = ('image', 'text', 'count', 'link', 'metrics')


# ---------- 配置 ----------

def _read_json(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def _config(pid, f):
    """dashboards/<工作流id>.json：先找项目的，再找麻薯通用的。返回 (配置, 出错说明)。"""
    if not pid:
        return {}, ''
    name = f'{str(pid).rpartition("/")[2].replace(":", "-")}.json'
    for d in (studio.film_templates_dir(f) / 'dashboards', studio.PLAYBOOKS / 'dashboards'):
        p = d / name
        if p.exists():
            try:
                cfg = json.loads(p.read_text())
            except ValueError as exc:
                return {}, f'dashboards/{p.name} 不是合法的 JSON：{exc}'
            except OSError:
                return {}, ''
            return (cfg, '') if isinstance(cfg, dict) else ({}, f'dashboards/{p.name} 应该是一个对象')
    return {}, ''


def _stage_view(s, i):
    return dict(id=i, name=s.get('name') or f'阶段 {i}', plain=s.get('plain') or '', role=s.get('role') or '',
                entry=s.get('entry'))


def _workflow(pid, pb, source, f):
    cfg, err = _config(pid, f)
    stages = [_stage_view(s, i) for i, s in enumerate(pb.get('stages') or [], 1)]
    cols = cfg.get('columns') if isinstance(cfg.get('columns'), dict) else {}
    for s in stages:
        hint = cols.get(str(s['id'])) or cols.get(s['name'])
        if isinstance(hint, str):
            s['hint'] = hint
    done_hint = cols.get('done') or cols.get('已完成')
    return dict(id=pid, name=cfg.get('title') or pb.get('name') or pid, version=pb.get('version'), source=source,
                summary=cfg.get('summary') or pb.get('summary') or '', stages=stages,
                done_hint=done_hint if isinstance(done_hint, str) else '',
                order=cfg.get('order') if isinstance(cfg.get('order'), (int, float)) else 50,
                aliases=[a for a in (cfg.get('aliases') or []) if isinstance(a, str)] if isinstance(cfg.get('aliases'), list) else [],
                fields=[x for x in (cfg.get('card') or []) if isinstance(x, dict)][:MAX_FIELDS] if isinstance(cfg.get('card'), list) else [],
                overview_spec=[x for x in (cfg.get('overview') or []) if isinstance(x, dict)] if isinstance(cfg.get('overview'), list) else [],
                config_error=err, features=[])


def workflows(conn, f, states):
    """项目的工作流：项目默认工作流 → 项目专用模板 → 功能用过的其他模板。同一个工作流只列一次
    （项目默认工作流 based_on 某个项目模板、或 dashboard.json 的 aliases 写了它，就并进那个标签）。"""
    out, alias = {}, {}
    fpb = studio.film_playbook(f)
    for t in studio.templates(f):
        if t.get('id'):
            out[t['id']] = _workflow(t['id'], t, 'project', f)
    base = fpb.get('based_on')
    names = [s.get('name') for s in fpb.get('stages') or []]
    if base and base not in out:      # 基于麻薯通用模板（如 direct）且阶段没改过：就当那个模板
        try:
            t = studio.load_template(base, f)
            if [s.get('name') for s in t.get('stages') or []] == names:
                out[base] = _workflow(base, t, 'mashu', f)
        except (ValueError, OSError):
            pass
    if base and base in out and [s['name'] for s in out[base]['stages']] == names:
        alias[fpb.get('id')] = base
    elif fpb.get('id') and fpb.get('id') not in out:
        out[fpb['id']] = _workflow(fpb['id'], fpb, 'project' if (studio.film_docs(f) / 'playbook.json').exists() else 'mashu', f)
    default = alias.get(fpb.get('id'), fpb.get('id'))
    for w in list(out.values()):
        for a in w['aliases']:
            alias.setdefault(a, w['id'])
    for key, data in states.items():
        pid = ((data or {}).get('playbook') or {}).get('id') or ''
        pid = alias.get(pid, pid)
        if not pid:      # 老状态文件没记工作流：按阶段数归到直接做 / 标准流程
            pid = studio.DEFAULT_PLAYBOOK if len((data or {}).get('stages') or []) <= 1 else 'standard'
        if pid not in out:
            try:
                pb = studio.load_template(pid.rpartition('/')[2], f)
                src = 'mashu' if studio.template_path(pid.rpartition('/')[2]) == studio.template_path(pid.rpartition('/')[2], f) else 'project'
            except (ValueError, OSError):
                pb = dict((data or {}).get('playbook') or {}, stages=(data or {}).get('stages') or [])
                src = 'feature'
            out[pid] = _workflow(pid, pb, src, f)
        out[pid]['features'].append(key)
    ws = sorted(out.values(), key=lambda w: (w['id'] != default, w['order'], w['id']))
    for w in ws:
        w['default'] = w['id'] == default
    return ws


# ---------- 功能的工作目录 ----------

def workdir(f, data):
    """功能的工作目录（相对工程根目录）：从开功能时的 --touches 推出来，取第一个指向工程内某个目录的。推不出返回 None。"""
    root = Path(f['path']).expanduser().resolve()
    for pat in ((data or {}).get('claims') or {}).get('touches') or []:
        if not isinstance(pat, str) or not pat.strip():
            continue
        pre = studio.glob_prefix(pat.strip())
        if not any(ch in pat for ch in '*?[') and not pat.endswith('/'):
            pre = pat.strip()
        pre = pre.rstrip('/')
        if not pre:
            continue
        p = Path(pre).expanduser()
        p = (p if p.is_absolute() else root / p).resolve()
        if p == root or root not in p.parents:
            continue
        if p.is_file() or (not p.exists() and p.suffix and not pat.rstrip('/').endswith('**')):
            continue      # 指向单个文件的（如 mashu/server.py）不算工作目录
        return str(p.relative_to(root))
    return None


# ---------- 字段取值 ----------

class Ctx:
    def __init__(self, f, key, data, wd):
        self.root = Path(f['path']).expanduser().resolve()
        self.feat = studio.seq_dir(f, key).resolve()
        self.work = (self.root / wd).resolve() if wd else None
        self.vars = dict(key=key, num=str(studio.seq_num(key) or ''), title=(data or {}).get('title') or '',
                         workdir=wd or '', workdir_name=Path(wd).name if wd else '')
        self._text = {}

    def sub(self, s):
        return re.sub(r'\{(\w+)\}', lambda m: self.vars.get(m.group(1), m.group(0)), s) if isinstance(s, str) else s

    def bases(self, frm):
        if frm == 'feature':
            return [self.feat]
        if frm == 'project':
            return [self.root]
        return [b for b in (self.work, self.feat) if b]       # 默认：先找工作目录，再找功能资料目录

    def files(self, spec):
        """spec 的 path（字符串或列表，可用 glob）→ 按顺序找到的文件，只限工程目录 / 功能资料目录内。"""
        pats = spec.get('path')
        pats = [pats] if isinstance(pats, str) else [x for x in (pats or []) if isinstance(x, str)]
        out = []
        for base in self.bases(spec.get('from')):
            for pat in pats:
                pat = self.sub(pat).lstrip('/')
                if not pat or '..' in Path(pat).parts:
                    continue
                try:
                    hits = sorted(base.glob(pat)) if any(ch in pat for ch in '*?[') else [base / pat]
                except (ValueError, OSError):
                    continue
                for h in hits:
                    try:
                        r = h.resolve()
                    except OSError:
                        continue
                    if (r == base or base in r.parents) and r.is_file() and r not in out \
                            and not any(part.startswith('.') and part != '.mashu' for part in r.relative_to(self.root if self.root in r.parents else base).parts[:-1]):
                        out.append(r)
        return out

    def text(self, p):
        if p not in self._text:
            try:
                with open(p, 'rb') as fh:
                    self._text[p] = fh.read(MAX_READ).decode('utf-8', 'replace')
            except OSError:
                self._text[p] = ''
        return self._text[p]

    def rel(self, p):
        for b, tag in ((self.work, ''), (self.feat, '功能资料/'), (self.root, '')):
            if b and (b == p or b in p.parents):
                return tag + str(p.relative_to(b))
        return p.name


def _section(text, spec, ctx):
    """section：只看 Markdown 的某一节。字符串 = 标题里含这几个字；{"has": "…"} = 正文里含这几个字的节（取最后一个）。"""
    sec = spec.get('section')
    if not sec:
        return text
    parts = re.split(r'(?m)^(?=#{1,6}\s)', text)
    if isinstance(sec, str):
        want = ctx.sub(sec)
        hit = [p for p in parts if p.startswith('#') and want in p.split('\n', 1)[0]]
    elif isinstance(sec, dict) and sec.get('has'):
        want = ctx.sub(sec['has'])
        hit = [p for p in parts if p.startswith('#') and want and want in p]
    else:
        return text
    return hit[-1] if hit else ''


def _json_get(obj, path):
    for k in str(path).split('.'):
        if isinstance(obj, list) and k.lstrip('-').isdigit() and -len(obj) <= int(k) < len(obj):
            obj = obj[int(k)]
        elif isinstance(obj, dict) and k in obj:
            obj = obj[k]
        else:
            return None
    return obj


def _pick(text, spec, ctx, path=None):
    """从一段文本里取一个值：json / kv（「名称：值」行）/ heading / match，都没写就取第一行正文。"""
    if not text:
        return None
    if spec.get('json'):
        try:
            v = _json_get(json.loads(text), spec['json'])
        except ValueError:
            return None
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        return None if v is None else v
    text = _section(text, spec, ctx)
    if not text:
        return None
    if spec.get('kv'):
        k = re.escape(ctx.sub(spec['kv']))
        ms = re.findall(rf'(?m)^\s*(?:[-*]\s*)?(?:\*\*)?{k}(?:\*\*)?\s*[:：]\s*(.+?)\s*$', text)
        return (ms[-1] if spec.get('last') else ms[0]) if ms else None
    if spec.get('heading'):
        m = re.search(r'(?m)^#{1,6}\s+(.+?)\s*#*\s*$', text)
        return m.group(1) if m else None
    if spec.get('match'):
        try:
            ms = list(re.finditer(ctx.sub(spec['match']), text, re.M))
        except re.error:
            return None
        if not ms:
            return None
        m = ms[-1] if spec.get('last') else ms[0]
        return next((g for g in m.groups() if g), m.group(0))      # 有几组括号（a|b 两种写法）就取第一个匹配上的
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(('#', '---', '|-', '```')):
            return line
    return None


def _text_value(spec, ctx):
    for p in ctx.files(spec):
        v = _pick(ctx.text(p), spec, ctx, p)
        if v not in (None, ''):
            v = str(v).strip()
            n = spec.get('max') if isinstance(spec.get('max'), int) else 80
            return (v[:n] + '…') if len(v) > n else v, p
    return None, None


def _number(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    m = re.search(r'-?\d+(?:\.\d+)?', str(v or '').replace(',', ''))
    if not m:
        return None
    x = float(m.group(0))
    if re.search(r'\d\s*[wW万]', str(v)):
        x *= 10000
    return int(x) if x.is_integer() else x


def _count(spec, ctx):
    pats = spec.get('path')
    pats = [pats] if isinstance(pats, str) else [x for x in (pats or []) if isinstance(x, str)]
    for base in ctx.bases(spec.get('from')):
        n = 0
        for pat in pats:
            pat = ctx.sub(pat).lstrip('/')
            if not pat or '..' in Path(pat).parts:
                continue
            try:
                n += sum(1 for h in base.glob(pat) if h.is_file() and not h.name.startswith('.'))
            except (ValueError, OSError):
                pass
        if n:
            return n
    return 0 if ctx.work else None


def _field(spec, ctx):
    """一个字段 → {key, label, type, value, ...}；取不到 value 为 None。try：依次试几种取法，取第一个有值的。"""
    alts = spec.get('try') if isinstance(spec.get('try'), list) else [{}]
    out = dict(key=str(spec.get('key') or spec.get('label') or ''), label=str(spec.get('label') or ''),
               type=spec.get('type') if spec.get('type') in FIELD_TYPES else 'text', value=None)
    for k in ('as', 'wide', 'tone', 'unit', 'empty'):
        if k in spec and isinstance(spec[k], (str, bool, dict)):
            out[k] = spec[k]
    for alt in alts:
        s = dict(spec, **alt) if isinstance(alt, dict) else spec
        s.pop('try', None)
        try:
            _fill(out, s, ctx)
        except Exception as exc:        # 配置写错只让这一项空着
            out['error'] = f'{type(exc).__name__}: {exc}'
        if out['value'] not in (None, '', []):
            break
    if isinstance(out.get('tone'), dict):     # 按值上色：{"须勾": "warn"}
        tone = next((t for w, t in out['tone'].items() if w and w in str(out['value'] or '')), None)
        out['tone'] = tone if isinstance(tone, str) else ''
    return out


def _fill(out, s, ctx):
    t = out['type']
    if t == 'image':
        p = next((x for x in ctx.files(s) if re.search(r'\.(png|jpe?g|gif|webp|svg|bmp)$', x.name, re.I)), None)
        out['value'] = str(p) if p else None
        if p:
            out['name'] = ctx.rel(p)
    elif t == 'text':
        if 'value' in s and not s.get('path'):
            out['value'] = ctx.sub(s['value'])
        else:
            v, p = _text_value(s, ctx)
            out['value'] = v
            if p:
                out['src'] = ctx.rel(p)
    elif t == 'count':
        n = _count(s, ctx)
        out['value'] = n
        tot = s.get('total')
        if isinstance(tot, (int, float)):
            out['total'] = tot
        elif isinstance(tot, dict):
            sub = _field(dict(tot, type=tot.get('type') or 'text', key='total'), ctx)
            out['total'] = sub['value'] if sub['type'] == 'count' else _number(sub['value'])
        if out['value'] == 0 and not out.get('total'):
            out['value'] = 0
    elif t == 'link':
        if s.get('url'):
            out['value'] = ctx.sub(s['url'])
            out['href'] = out['value']
        elif s.get('path') and not any(k in s for k in ('match', 'kv', 'json', 'heading')):
            p = next(iter(ctx.files(s)), None)
            out['value'] = ctx.rel(p) if p else None
            if p:
                out['file'] = str(p)
        else:
            v, p = _text_value(s, ctx)
            if v and re.match(r'https?://', v):
                out['value'] = out['href'] = v
    elif t == 'metrics':
        items = []
        for it in s.get('items') or []:
            if not isinstance(it, dict):
                continue
            sub = dict(s, **it)
            sub.pop('items', None)
            v, _ = _text_value(sub, ctx)
            items.append(dict(label=str(it.get('label') or ''), value=_number(v)))
        out['items'] = items
        out['value'] = items if any(x['value'] is not None for x in items) else None


# ---------- 时间 ----------

def _ts(iso):
    try:
        return datetime.datetime.fromisoformat(iso).timestamp() if iso else None
    except ValueError:
        return None


def timing(data):
    """(所在列, 进这一列的时间戳, 完成时间戳)。列号从 1 起，全部完成 = 阶段数 + 1。"""
    stages = (data or {}).get('stages') or []
    stamps = [t for t in (_ts(s.get('updated_at')) for s in stages) if t]
    if studio.seq_done(data):
        done = max(stamps) if stamps else _ts((data or {}).get('updated_at'))
        return len(stages) + 1, done, done
    cur = studio.current_stage(data)
    if not cur:
        return 1, _ts((data or {}).get('updated_at')), None
    since = _ts(cur.get('updated_at'))
    if not since or cur['status'] == '未开始':      # 还没进这一阶段（多半在等确认）：从上一阶段结束算
        prev = [t for t in (_ts(s.get('updated_at')) for s in stages[:cur['id'] - 1]) if t]
        since = max(prev) if prev else since
    return cur['id'], since, None


# ---------- 概况 ----------

def _overview(w, feats, keys=None):
    out = []
    for o in w['overview_spec']:
        key, agg = o.get('field'), o.get('agg') or 'count'
        vals = []
        for k in w['features'] if keys is None else keys:
            fd = next((x for x in feats[k]['fields'] if x['key'] == key), None)
            if not fd or fd['value'] in (None, '', []):
                continue
            v = fd['value']
            if o.get('metric') and fd['type'] == 'metrics':
                v = next((x['value'] for x in fd.get('items') or [] if x['label'] == o['metric']), None)
                if v is None:
                    continue
            if o.get('match'):
                try:
                    if not re.search(o['match'], str(v)):
                        continue
                except re.error:
                    continue
            vals.append(v)
        if agg == 'sum':
            nums = [_number(v) for v in vals]
            val = sum(x for x in nums if x is not None) if any(x is not None for x in nums) else None
        else:
            val = len(vals)
        out.append(dict(label=str(o.get('label') or key or ''), value=val, unit=str(o.get('unit') or ''),
                        hint=str(o.get('hint') or '')))
    return out


# ---------- 入口 ----------

def dashboard(conn, film_id):
    f = studio.require_film(conn, film_id)
    keys = [s['key'] for s in studio.sequences(conn, f)]
    states = {k: studio.seq_state(f, k) for k in keys}
    ws = workflows(conn, f, states)
    acc = studio.film_accounts(f)       # 按账号分组（#8）：没有 accounts.json 时为 None，面板照旧
    feats = {}
    for w in ws:
        for k in w['features']:
            data = states.get(k)
            col, since, done_at = timing(data)
            try:
                wd = workdir(f, data)
            except Exception:
                wd = None
            fields = []
            if w['fields']:       # 推不出工作目录时只在功能资料目录里找（from: feature 的字段照样能用）
                ctx = Ctx(f, k, data, wd)
                fields = [_field(spec, ctx) for spec in w['fields']]
            feats[k] = dict(workflow=w['id'], col=min(col, len(w['stages']) + 1), since=since, done_at=done_at,
                            workdir=wd, fields=fields, account=studio.feature_account(f, acc, k, data))
    for w in ws:
        w['overview'] = _overview(w, feats)
        if acc:       # 每个账号单独算一份概况，切账号时前端直接换
            w['overview_by'] = {a['id']: _overview(w, feats, [k for k in w['features'] if feats[k]['account'] == a['id']])
                                for a in acc['accounts']}
        w['has_fields'] = bool(w['fields'])
        w['field_labels'] = [dict(key=str(x.get('key') or x.get('label') or ''), label=str(x.get('label') or ''),
                                  type=x.get('type')) for x in w['fields']]
        for k in ('fields', 'overview_spec', 'aliases'):
            w.pop(k, None)
    return dict(film=f['id'], title=f['title'], path=f['path'], workflows=ws, features=feats,
                accounts=acc and dict(accounts=[{k: a[k] for k in ('id', 'name', 'account', 'order')} for a in acc['accounts']],
                                      workflows=acc['workflows'], default=acc['default']),
                generated_at=studio.now_iso())
