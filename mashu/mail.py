"""邮箱：主脑、各子脑和用户之间的往来（上报、批复、转呈、指示、通报），从聊天里分出来单独看。

信箱地址：'executive'（我）| 'assistant'（主脑）| 'producer:<项目id>'（子脑）。执行者不设信箱，只作为发件人 / 收件人出现。
上报和批复以 reports 表为准（带状态和流转路线）；指示、条子、转呈、项目动态、简报数据来自 messages 表。
"""
import datetime
import json
import re

import studio

MAIL_KINDS = ('memo', 'note', 'forward', 'event', 'digest')
KIND_LABEL = dict(memo='主脑指示', note='补充指令', forward='转呈回复', event='项目动态', digest='简报数据',
                  ask='请示', confirm='申请进入下一阶段', gate='申请审批', tell='通报', report='汇报',
                  **{'stage-done': '阶段完成', 'sequence-done': '功能完成'})


def iso(ts):
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec='seconds') if ts else ''


def level_box(level, film_id):
    if level == 'producer':
        return f'producer:{film_id}' if film_id else None
    return {'assistant': 'assistant', 'digest': 'assistant', 'executive': 'executive'}.get(level)


def titles(conn):
    return {f['id']: f['title'] for f in studio.films(conn)}


def box_label(box, t):
    if box == 'executive':
        return '我'
    if box == 'assistant':
        return '麻薯 · 主脑'
    if box and box.startswith('producer:'):
        fid = box.split(':', 1)[1]
        return f'子脑 · {t.get(fid, fid)}'
    return box or ''


def target_label(target, t):
    if target.startswith('crew:'):
        fid, _, key = target[5:].partition('/')
        return f'执行者 · {t.get(fid, fid)} #{studio.seq_num(key) or key}'
    return box_label('assistant' if target == 'assistant' else target, t)


def subject(text):
    for line in str(text or '').splitlines():
        line = re.sub(r'^\s*\[[^\]]*\]\s*', '', line).strip()
        if line:
            return line[:90]
    return ''


def report_boxes(r):
    """(收到过它的信箱, 发出/转呈/批复过它的信箱)。"""
    fid = r['film_id']
    route = json.loads(r['route'] or '[]')
    src = level_box(r['source_role'], fid)
    got = {level_box(st.get('level'), fid) for st in route if st.get('level')}
    got.add(level_box(r['holder'], fid))
    sent = {src} | {level_box(st.get('level'), fid) for st in route if st.get('action') in ('pass', 'answer') and st.get('level')}
    got.discard(src)
    return got - {None}, sent - {None}


def report_item(r, t):
    where = studio.seq_label({'title': t.get(r['film_id'], r['film_id'])}, r['seq_key']) if r['film_id'] and r['seq_key'] \
        else t.get(r['film_id'], '全局') if r['film_id'] else '全局'
    who = f'{studio.LEVEL_LABEL.get(r["source_role"], r["source_role"])}「{r["source_name"]}」' if r['source_name'] else '调度器'
    if r['source_role'] in ('assistant', 'producer'):
        who = box_label(level_box(r['source_role'], r['film_id']), t)
    return dict(key=f'r{r["id"]}', ts=iso(r['created_at']), type='report', kind=r['kind'], kind_label=KIND_LABEL.get(r['kind'], r['kind']),
                frm=who, to=box_label(level_box(r['holder'], r['film_id']), t) if r['holder'] != 'digest' else '简报',
                subject=r['summary'] or subject(r['text']), where=where, report_id=r['id'], status=r['status'],
                film_id=r['film_id'], seq_key=r['seq_key'])


def message_boxes(m):
    frm = 'executive' if m['sender'] == 'executive' else level_box(m['sender'], m['film_id'])
    if m['kind'] == 'event':
        to = 'executive'
    elif m['target'] == 'assistant':
        to = 'assistant'
    elif m['target'].startswith('producer:'):
        to = m['target']
    else:
        to = None
    return frm, to


def message_item(m, t):
    frm, to = message_boxes(m)
    if m['sender'] == 'crew':
        frm_label = f'执行者「{m["camera_name"] or ""}」'
    elif m['sender'] == 'system':
        frm_label = '调度器'
    else:
        frm_label = box_label(frm, t)
    text = m['text']
    if m['kind'] == 'event' and '|' in text[:8]:
        text = text.split('|', 1)[1]
    to_label = box_label(to, t) if to else target_label(m['target'], t)
    where = (studio.seq_label({'title': t.get(m['film_id'], m['film_id'])}, m['seq_key']) if m['seq_key']
             else t.get(m['film_id'], m['film_id'])) if m['film_id'] else '全局'
    return dict(key=f'm{m["id"]}', ts=m['ts'], type='message', kind=m['kind'], kind_label=KIND_LABEL.get(m['kind'], m['kind']),
                frm=frm_label, to=to_label, subject=subject(text), text=text, where=where, status='info',
                film_id=m['film_id'], seq_key=m['seq_key'], channel=m['channel'])


def boxes(conn):
    t = titles(conn)
    pending = {}
    for r in studio.rows(conn, 'SELECT holder, film_id, kind FROM reports WHERE status="open" AND holder!="digest"'):
        b = level_box(r['holder'], r['film_id'])
        pending[b] = pending.get(b, 0) + 1
    out = [dict(id='executive', label='我', sub='等我审批、回答的事', pending=pending.get('executive', 0)),
           dict(id='assistant', label='麻薯 · 主脑', sub='全局', pending=pending.get('assistant', 0))]
    for f in studio.films(conn):
        b = f'producer:{f["id"]}'
        out.append(dict(id=b, label=f'子脑 · {f["title"]}', sub=f['id'], pending=pending.get(b, 0)))
    return out


def mailbox(conn, box, limit=300):
    """一个信箱的收件和发件（新的在前）；收件里待这个信箱处理的上报标 pending。"""
    t = titles(conn)
    inbox, outbox = [], []
    for r in studio.rows(conn, 'SELECT * FROM reports ORDER BY id DESC LIMIT 1000'):
        got, sent = report_boxes(r)
        if box not in got and box not in sent:
            continue
        it = report_item(r, t)
        it['pending'] = r['status'] == 'open' and r['holder'] != 'digest' and level_box(r['holder'], r['film_id']) == box
        if box in got:
            inbox.append(it)
        elif level_box(r['source_role'], r['film_id']) == box:
            outbox.append(it)
        # 这个信箱的批复 / 转呈：发件里记一条
        for i, st in enumerate(json.loads(r['route'] or '[]')):
            if st.get('action') in ('pass', 'answer') and level_box(st.get('level'), r['film_id']) == box:
                act = '批复' if st['action'] == 'answer' else '转呈'
                outbox.append(dict(it, key=f'r{r["id"]}-{i}', ts=st.get('ts') or it['ts'], kind_label=act, frm=box_label(box, t),
                                   to=it['frm'] if act == '批复' else '上级', pending=False,
                                   subject=f'{st.get("note") or "（无意见）"}　—— 回 #{r["id"]} {it["subject"]}'[:120]))
    for m in studio.rows(conn, f'SELECT * FROM messages WHERE kind IN {MAIL_KINDS} ORDER BY id DESC LIMIT 3000'):
        frm, to = message_boxes(m)
        if box not in (frm, to):
            continue
        it = message_item(m, t)
        it['pending'] = False
        (inbox if to == box else outbox).append(it)
    key = lambda x: x['ts'] or ''                    # noqa: E731
    inbox.sort(key=key, reverse=True)
    outbox.sort(key=key, reverse=True)
    return dict(box=box, label=box_label(box, t), inbox=inbox[:limit], outbox=outbox[:limit])
