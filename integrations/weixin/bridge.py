#!/usr/bin/env python3
"""微信 ClawBot 与麻薯主对话的本机桥接。"""
import base64
import datetime
import fcntl
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
import media

ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home() / '.mashu-weixin'
STUDIO_HOME = Path(os.environ.get('MASHU_HOME') or ROOT / 'state')
STUDIO_URL = 'http://127.0.0.1:' + os.environ.get('MASHU_PORT', '8770')
ACCOUNT = json.loads((HOME / 'account.json').read_text())
BASE = (ACCOUNT.get('baseurl') or 'https://ilinkai.weixin.qq.com').rstrip('/')
OWNER = ACCOUNT['ilink_user_id']
TOKEN_FILE = STUDIO_HOME / 'token'
DB = HOME / 'bridge.db'
INFO = {'channel_version': '2.4.10', 'bot_agent': 'Mashu/1.0'}
INTERVAL = 3
MAX_ATTEMPTS = 5   # 单条最多重试次数，超过就跳过，不挡后面的消息
MAX_DELAY = 60     # 失败退避上限（秒）；会话过期（-14）另算
QUOTA = 10         # 用户发一句后机器人最多主动发约 10 条，超了微信返回 -2；第 10 条留给“回 1 继续”的提醒
RENEW = ('1', '1。', '继续')   # 只用来续订额度的消息
CARD_SWEEP = 60    # 每隔这么久核对一次：等用户处理的上报都要有一条送到微信的卡片
MERGE_LIMIT = 1200 # 排队的短消息合并成一条发，省额度


def database():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    return db


def initialize():
    with database() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbound (id TEXT PRIMARY KEY, text TEXT, status TEXT DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS outgoing (id TEXT PRIMARY KEY, text TEXT, status TEXT DEFAULT 'pending');''')
        if 'report_id' not in {row['name'] for row in db.execute('PRAGMA table_info(outgoing)')}:
            db.execute('ALTER TABLE outgoing ADD COLUMN report_id INTEGER')
        if 'payload' not in {row['name'] for row in db.execute('PRAGMA table_info(inbound)')}:
            db.execute('ALTER TABLE inbound ADD COLUMN payload TEXT')
        outgoing_columns = {row['name'] for row in db.execute('PRAGMA table_info(outgoing)')}
        if 'media_path' not in outgoing_columns:
            db.execute('ALTER TABLE outgoing ADD COLUMN media_path TEXT')
        if 'attempts' not in outgoing_columns:
            db.execute('ALTER TABLE outgoing ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')


def state(db, key, default=''):
    row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def set_state(db, key, value):
    db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def request(url, data=None, headers=None, timeout=20):
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def weixin(endpoint, data, timeout=20):
    headers = {'Content-Type': 'application/json', 'AuthorizationType': 'ilink_bot_token',
               'Authorization': 'Bearer ' + ACCOUNT['bot_token'],
               'X-WECHAT-UIN': base64.b64encode(str(random.getrandbits(32)).encode()).decode(),
               'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': str((2 << 16) | (4 << 8) | 10)}
    result = request(BASE + '/ilink/bot/' + endpoint, {**data, 'base_info': INFO}, headers, timeout)
    if result.get('ret', 0) or result.get('errcode', 0):
        raise RuntimeError('微信接口错误 ' + str(result.get('errcode') or result.get('ret')))
    return result


def mashu(path, data=None):
    token = TOKEN_FILE.read_text().strip()
    return request(STUDIO_URL + path, data,
                   {'X-Mashu-Token': token, 'Content-Type': 'application/json'}, 15)


def read_blob(path):
    token = TOKEN_FILE.read_text().strip()
    url = STUDIO_URL + '/api/blob?' + urllib.parse.urlencode({'path': path})
    req = urllib.request.Request(url, headers={'X-Mashu-Token': token})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read(media.MAX_BYTES + 1)
    if len(data) > media.MAX_BYTES:
        raise media.MediaError('附件超过 50 MB')
    return data


def queue_message(db, message_id, text, report_id=None):
    # 麻薯网页用本地 Markdown 链接表示附件。
    pattern = re.compile(r'(?:📎\s*)?(!?)\[([^\]]*)\]\(((?:/|~/)[^\n)]+)\)')
    segments = []
    position = 0
    for match in pattern.finditer(text):
        before = text[position:match.start()]
        if before.strip():
            segments.append(('text', before.strip()))
        segments.append(('media', match.group(3)))
        position = match.end()
    if text[position:].strip():
        segments.append(('text', text[position:].strip()))
    part = 0
    for kind, value in segments:
        chunks = [value] if kind == 'media' else [value[i:i + 1200] for i in range(0, len(value), 1200)]
        for chunk in chunks:
            db.execute('INSERT OR IGNORE INTO outgoing(id,text,report_id,media_path) VALUES (?,?,?,?)',
                       (f'message:{message_id}:{part}', chunk if kind == 'text' else '', report_id, chunk if kind == 'media' else None))
            part += 1


def card_text(report):
    """要用户决定的上报（请示、申请、审批）：精简成一条，省额度；详情和截图在网页看。"""
    where = report.get('film_title') or ('主对话' if not report.get('film_id') else report['film_id'])
    body = (report.get('text') or report.get('summary') or '').strip()
    body = re.sub(r'(?:📎\s*)?!?\[([^\]]*)\]\([^)]*\)', r'[\1]', body)    # 附件只留名字，不占额度发图
    if len(body) > 500:
        body = body[:500].rstrip() + '……（详情看麻薯网页）'
    text = f'待你处理 #{report["id"]}（{where}）\n{body}'
    if report.get('options'):
        text += '\n选项：' + ' / '.join(report['options']) + '\n（在麻薯网页点按钮，或在这里直接回复）'
    return text


def queue_card(db, report):
    db.execute('INSERT OR IGNORE INTO outgoing(id,text,report_id) VALUES (?,?,?)',
               (f'card:{report["id"]}', card_text(report), report['id']))


def sweep_cards():
    """等用户处理的上报，没有一条在排队或已送达的，就补一张卡（以前被额度拒掉的也会补）。"""
    reports = [r for r in mashu('/api/reports')['reports'] if r.get('holder') == 'executive' and r.get('status') == 'open']
    with database() as db:
        for r in reports:
            ok = db.execute("SELECT 1 FROM outgoing WHERE report_id=? AND status IN ('pending','sent') LIMIT 1", (r['id'],)).fetchone()
            if not ok:
                db.execute("DELETE FROM outgoing WHERE id=? AND status!='sent'", (f'card:{r["id"]}',))
                queue_card(db, r)


def receive():
    delay = 2
    while True:
        try:
            with database() as db:
                cursor = state(db, 'cursor')
            result = weixin('getupdates', {'get_updates_buf': cursor}, 45)
            with database() as db:
                for msg in result.get('msgs', []):
                    if msg.get('from_user_id') != OWNER or msg.get('message_type') != 1:
                        continue
                    created = msg.get('create_time_ms') or 0
                    if created and time.time() * 1000 - created > 20000:
                        print('微信入站延迟', int(time.time() - created / 1000), '秒', flush=True)
                    if msg.get('context_token'):
                        set_state(db, 'context', msg['context_token'])
                    set_state(db, 'sent_since', 0)     # 用户发了话，额度重新算
                    text = '\n'.join(item.get('text_item', {}).get('text', '') for item in msg.get('item_list', []) if item.get('type') == 1).strip()
                    ident = str(msg.get('message_id') or msg.get('client_id') or '')
                    has_media = any(item.get('type') in (2, 3, 4, 5) for item in msg.get('item_list', []))
                    if (text or has_media) and ident:
                        db.execute('INSERT OR IGNORE INTO inbound(id,text,payload) VALUES (?,?,?)',
                                   (ident, text, json.dumps(msg.get('item_list', []), ensure_ascii=False)))
                if result.get('get_updates_buf'):
                    set_state(db, 'cursor', result['get_updates_buf'])
            delay = 2
        except Exception as exc:
            if isinstance(exc, TimeoutError) or (isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)):
                delay = 2
                continue
            print('微信接收异常', type(exc).__name__, flush=True)
            if '-14' in str(exc): delay = 3600
            time.sleep(delay)
            delay = min(delay * 2, 3600)


def incoming():
    while True:
        try:
            with database() as db:
                item = db.execute("SELECT * FROM inbound WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if not item:
                time.sleep(1)
                continue
            ident, text = item['id'], item['text']
            items = json.loads(item['payload'] or '[]')
            attachments = [part for part in items if part.get('type') in (2, 3, 4, 5)]
            if attachments:
                parts = [text] if text else []
                for index, attachment in enumerate(attachments):
                    voice_text = (attachment.get('voice_item') or {}).get('text') if attachment.get('type') == 3 else None
                    if voice_text:
                        parts.append(voice_text)
                    try:
                        data, name, kind, _ = media.fetch_inbound(attachment)
                        folder = STUDIO_HOME / 'uploads' / 'weixin'
                        folder.mkdir(parents=True, exist_ok=True)
                        unique = hashlib.sha256((ident + ':' + str(index)).encode()).hexdigest()[:16]
                        path = folder / (unique + '-' + name)
                        if not path.exists():
                            tmp = folder / (unique + '.tmp')
                            tmp.write_bytes(data)
                            os.replace(tmp, path)
                        parts.append(('!' if kind == 2 else '📎 ') + '[' + name + '](' + str(path) + ')')
                    except (media.MediaError, ValueError) as exc:
                        parts.append('[微信附件无法接收：' + str(exc) + ']')
                text = '\n'.join(parts)
            with database() as db:
                waiting = state(db, 'waiting')
            if not attachments and text.strip() in RENEW:
                # 用户发「1」只为续上 ClawBot 的发送额度（receive 已清零计数），不转给主脑、不回复，省 token
                with database() as db:
                    set_state(db, 'waiting', '')
                    db.execute("UPDATE inbound SET status='done' WHERE id=?", (ident,))
                continue
            if waiting:
                with database() as db:
                    set_state(db, 'waiting', '')
            if text == '/new' and not attachments:
                # 执行前落盘，崩溃时不自动重复切换会话。
                with database() as db:
                    db.execute("UPDATE inbound SET status='started' WHERE id=?", (ident,))
                try:
                    result = mashu('/api/camera', {'action': 'new-session', 'channel': 'assistant'})
                    reply = result.get('result', '已提交新会话请求')
                except Exception:
                    reply = '新会话请求的结果未确认，请在麻薯网页查看后再操作。'
                with database() as db:
                    db.execute('INSERT OR IGNORE INTO outgoing(id,text) VALUES (?,?)', ('command:' + ident, reply))
                    db.execute("UPDATE inbound SET status='done' WHERE id=?", (ident,))
            elif text.startswith('/') and not attachments:
                with database() as db:
                    db.execute('INSERT OR IGNORE INTO outgoing(id,text) VALUES (?,?)', ('command:' + ident, '未知命令。目前支持 /new。'))
                    db.execute("UPDATE inbound SET status='done' WHERE id=?", (ident,))
            else:
                # 在麻薯数据库内原子记录去重标识和原文，沿用网页消息的字段。
                key = 'weixin:inbound:' + ident
                with sqlite3.connect(STUDIO_HOME / 'studio.db', timeout=15) as studio_db:
                    studio_db.execute('BEGIN IMMEDIATE')
                    if not studio_db.execute('SELECT 1 FROM meta WHERE k=?', (key,)).fetchone():
                        timestamp = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
                        studio_db.execute('INSERT INTO messages(ts,sender,channel,target,kind,text,delivered) VALUES (?,?,?,?,?,?,0)',
                                          (timestamp, 'executive', 'assistant', 'assistant', 'chat', text))
                        studio_db.execute('INSERT INTO meta(k,v) VALUES (?,?)', (key, '1'))
                with database() as db:
                    db.execute("UPDATE inbound SET status='done' WHERE id=?", (ident,))
        except Exception as exc:
            print('麻薯入站异常', type(exc).__name__, flush=True)
            time.sleep(5)


def collect():
    swept = 0
    while True:
        try:
            with database() as db:
                last = state(db, 'message_cursor')
            if not last:
                messages = mashu('/api/messages?tail=1')['messages']
                last = str(messages[-1]['id'] if messages else 0)
                with database() as db: set_state(db, 'message_cursor', last)
            messages = mashu('/api/messages?after=' + last)['messages']
            report_ids = [str(msg['report_id']) for msg in messages if msg.get('kind') == 'card' and msg.get('report_id')]
            reports = {}
            if report_ids:
                reports = {r['id']: r for r in mashu('/api/reports?ids=' + ','.join(report_ids))['reports']}
            with database() as db:
                for msg in messages:
                    if msg['kind'] == 'card' and msg.get('report_id'):
                        report = reports.get(msg['report_id'])
                        if report and report.get('status') == 'open' and report.get('holder') == 'executive':
                            queue_card(db, report)
                    elif msg['sender'] == 'assistant' and msg['target'] == 'web' and msg['channel'] == 'assistant' and msg.get('text'):
                        queue_message(db, msg['id'], msg['text'], None)
                    set_state(db, 'message_cursor', msg['id'])
            if time.time() - swept > CARD_SWEEP:
                sweep_cards()
                swept = time.time()
            time.sleep(1)
        except Exception as exc:
            print('麻薯出站读取异常', type(exc).__name__, flush=True)
            time.sleep(5)


def send():
    delay = INTERVAL
    while True:
        item = None
        try:
            with database() as db:
                item = db.execute("SELECT * FROM outgoing WHERE status='pending' ORDER BY report_id IS NULL, rowid LIMIT 1").fetchone()
                context = state(db, 'context')
                sent = int(state(db, 'sent_since', '0') or 0)
                backlog = db.execute("SELECT COUNT(*) n FROM outgoing WHERE status='pending'").fetchone()['n']
            if not item or not context or sent >= QUOTA:
                time.sleep(1)
                continue
            if sent == QUOTA - 1:
                # 最后一条额度：提醒用户回 1，排队的消息留着，续订后接着发
                notice = f'微信额度快用完了，还有 {backlog} 条在排队。回「1」继续接收。'
                weixin('sendmessage', {'msg': {'from_user_id': '', 'to_user_id': OWNER,
                       'client_id': 'mashu-quota-' + hashlib.sha256(f'{item["id"]}:{time.time()}'.encode()).hexdigest()[:20],
                       'message_type': 2, 'message_state': 2, 'context_token': context,
                       'item_list': [{'type': 1, 'text_item': {'text': notice}}]}})
                with database() as db:
                    set_state(db, 'sent_since', QUOTA)
                    set_state(db, 'waiting', '1')
                continue
            if item['report_id']:
                reports = mashu('/api/reports?ids=' + str(item['report_id']))['reports']
                if not reports or reports[0]['status'] != 'open' or reports[0]['holder'] != 'executive':
                    with database() as db:
                        db.execute("UPDATE outgoing SET status='cancelled' WHERE id=?", (item['id'],))
                    continue
            client_id = 'mashu-' + hashlib.sha256(item['id'].encode()).hexdigest()[:24]
            if item['media_path']:
                message_item = media.send_item(item['media_path'], OWNER,
                                               lambda data: weixin('getuploadurl', data), read_blob)
            merged = [item]
            if not item['media_path']:
                # 后面排着的纯文字消息合并成一条（不超过 MERGE_LIMIT），一条额度带走多条
                with database() as db:
                    more = db.execute("SELECT * FROM outgoing WHERE status='pending' ORDER BY report_id IS NULL, rowid LIMIT 21").fetchall()[1:]
                size = len(item['text'] or '')
                for nxt in more:
                    if nxt['media_path'] or not nxt['text'] or size + len(nxt['text']) + 2 > MERGE_LIMIT:
                        break
                    if nxt['report_id']:
                        r = mashu('/api/reports?ids=' + str(nxt['report_id']))['reports']
                        if not r or r[0]['status'] != 'open' or r[0]['holder'] != 'executive':
                            with database() as db:
                                db.execute("UPDATE outgoing SET status='cancelled' WHERE id=?", (nxt['id'],))
                            continue
                    merged.append(nxt)
                    size += len(nxt['text']) + 2
                message_item = {'type': 1, 'text_item': {'text': '\n\n'.join(m['text'] for m in merged)}}
            weixin('sendmessage', {'msg': {'from_user_id': '', 'to_user_id': OWNER, 'client_id': client_id,
                   'message_type': 2, 'message_state': 2, 'context_token': context,
                   'item_list': [message_item]}})
            with database() as db:
                for m in merged:
                    db.execute("UPDATE outgoing SET status='sent' WHERE id=?", (m['id'],))
                set_state(db, 'sent_since', int(state(db, 'sent_since', '0') or 0) + 1)
            delay = INTERVAL
            time.sleep(INTERVAL)
        except Exception as exc:
            # 记下错误码和条目，方便查原因；失败条目重试有上限，避免一条卡住整个队列。
            print('微信发送异常', type(exc).__name__, str(exc)[:200], item['id'] if item else '', flush=True)
            expired = '-14' in str(exc)
            if '错误 -2' in str(exc):
                # 额度用完（或窗口过期）：不算失败、不丢消息，停发等用户回话后续发
                with database() as db:
                    set_state(db, 'sent_since', QUOTA)
                    set_state(db, 'waiting', '1')
                continue
            if item and not expired:
                with database() as db:
                    db.execute('UPDATE outgoing SET attempts=attempts+1 WHERE id=?', (item['id'],))
                    attempts = db.execute('SELECT attempts FROM outgoing WHERE id=?', (item['id'],)).fetchone()['attempts']
                    permanent = isinstance(exc, media.MediaError) or (
                        isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500 and exc.code != 429)
                    if permanent or attempts >= MAX_ATTEMPTS:
                        db.execute("UPDATE outgoing SET status='failed' WHERE id=?", (item['id'],))
                        if item['media_path']:
                            db.execute('INSERT OR IGNORE INTO outgoing(id,text) VALUES (?,?)',
                                       ('failed:' + item['id'], '附件没发出去，请在麻薯网页查看：' + Path(item['media_path']).name))
                        delay = INTERVAL
                        continue
            time.sleep(3600 if expired else delay)
            delay = INTERVAL if expired else min(delay * 2, MAX_DELAY)


def main():
    os.umask(0o077)
    lock = (HOME / 'bridge.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('微信连接已在运行', flush=True)
        return
    initialize()
    with database() as db:
        first_start = not state(db, 'message_cursor')
    if first_start:
        messages = mashu('/api/messages?tail=1')['messages']
        reports = mashu('/api/reports')['reports']
        with database() as db:
            set_state(db, 'message_cursor', messages[-1]['id'] if messages else 0)
            for report in reports:
                if report.get('holder') != 'executive':
                    continue
                text = '待确认 #' + str(report['id']) + '\n' + (report.get('text') or report.get('summary') or '')
                options = report.get('options') or []
                if options:
                    text += '\n选项：' + ' / '.join(options)
                for part, start in enumerate(range(0, len(text), 1200)):
                    db.execute('INSERT OR IGNORE INTO outgoing(id,text,report_id) VALUES (?,?,?)', (f"open-report:{report['id']}:{part}", text[start:start + 1200], report['id']))
    with database() as db:
        for row in db.execute("SELECT id FROM inbound WHERE status='started'"):
            db.execute('INSERT OR IGNORE INTO outgoing(id,text) VALUES (?,?)', ('command:' + row['id'], '上次新会话请求结果未确认，请在麻薯网页查看。'))
        db.execute("UPDATE inbound SET status='done' WHERE status='started'")
    for function in (receive, incoming, collect):
        threading.Thread(target=function, daemon=True).start()
    send()


if __name__ == '__main__':
    main()
