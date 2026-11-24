#!/usr/bin/env python3
"""麻薯电脑秘书网页服务（控制台）+ 调度器线程。

  python3 mashu/server.py [--port 8770]
只监听 127.0.0.1。接口要求请求头 X-Mashu-Token（首页加载时注入），并校验 Host，
防止其他网页借浏览器向 bypass 模式的会话注入指令（04-D18）。
"""
import argparse
import html
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
import traceback
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio  # noqa: E402
import mail  # noqa: E402

WEB = studio.CODE / 'web'
STARTED = time.time()     # 服务启动时间：mashu/*.py 比它新，说明代码改过、要重启才生效


def code_stale():
    return any(p.stat().st_mtime > STARTED for p in (studio.CODE / 'mashu').glob('*.py'))


def restart_later():
    """网页上的「重启服务」按钮：先回应页面，1 秒后在独立进程组里跑 start.sh restart（会关掉本进程所在的 office 窗口再重开）。"""
    import subprocess
    subprocess.Popen(['bash', '-c', 'sleep 1; NO_OPEN=1 ./start.sh restart'], cwd=str(studio.CODE), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
DASH_CACHE = {}           # 项目 id → (时间, 工作流面板数据)
MSG_COLS = 'id, ts, sender, camera_id, camera_name, channel, film_id, seq_key, target, kind, text, report_id'


def load_token():
    studio.HOME.mkdir(parents=True, exist_ok=True)
    f = studio.HOME / 'token'
    if not f.exists():
        f.write_text(secrets.token_urlsafe(24))
        f.chmod(0o600)
    return f.read_text().strip()


# ---------- 终端画面：ANSI → HTML ----------

BASE16 = ['#1d1f21', '#cc6666', '#b5bd68', '#f0c674', '#81a2be', '#b294bb', '#8abeb7', '#c5c8c6',
          '#666666', '#ff7b72', '#d7e88a', '#ffe08a', '#9ecbff', '#d2a8ff', '#a5e4dc', '#ffffff']


def xterm256(n):
    if n < 16:
        return BASE16[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return '#%02x%02x%02x' % (steps[n // 36], steps[(n // 6) % 6], steps[n % 6])
    v = 8 + (n - 232) * 10
    return '#%02x%02x%02x' % (v, v, v)


def ansi_html(text):
    out, style = [], {}
    pos = 0

    def span(chunk):
        if not chunk:
            return
        css = ';'.join(f'{k}:{v}' for k, v in style.items())
        out.append(f'<span style="{css}">{html.escape(chunk)}</span>' if css else html.escape(chunk))

    for m in re.finditer(r'\x1b\[([0-9;]*)m', text):
        span(text[pos:m.start()])
        pos = m.end()
        codes = [int(c) if c else 0 for c in m.group(1).split(';')] or [0]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                style = {}
            elif c == 1:
                style['font-weight'] = '700'
            elif c == 2:
                style['opacity'] = '.65'
            elif c == 3:
                style['font-style'] = 'italic'
            elif c == 4:
                style['text-decoration'] = 'underline'
            elif c == 7:
                style['filter'] = 'invert(1)'
            elif c in (22, 23, 24, 27):
                style.pop({22: 'font-weight', 23: 'font-style', 24: 'text-decoration', 27: 'filter'}[c], None)
            elif 30 <= c <= 37 or 90 <= c <= 97:
                style['color'] = BASE16[c - 30 if c < 90 else c - 82]
            elif 40 <= c <= 47 or 100 <= c <= 107:
                style['background'] = BASE16[c - 40 if c < 100 else c - 92]
            elif c in (39, 49):
                style.pop('color' if c == 39 else 'background', None)
            elif c in (38, 48) and i + 1 < len(codes):
                key = 'color' if c == 38 else 'background'
                if codes[i + 1] == 5 and i + 2 < len(codes):
                    style[key] = xterm256(codes[i + 2])
                    i += 2
                elif codes[i + 1] == 2 and i + 4 < len(codes):
                    style[key] = '#%02x%02x%02x' % tuple(max(0, min(255, x)) for x in codes[i + 2:i + 5])
                    i += 4
            i += 1
    span(text[pos:])
    return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', ''.join(out))


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    token = ''
    port = 8770
    lock = threading.Lock()
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def send_body(self, body, ctype, code=200, cache='no-store'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', cache)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code=200):
        self.send_body(json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', code)

    def host_ok(self):
        host = (self.headers.get('Host') or '').lower()
        return host in (f'127.0.0.1:{self.port}', f'localhost:{self.port}')

    def api_ok(self, query=None):
        given = self.headers.get('X-Mashu-Token') or ((query or {}).get('token') or [''])[0]
        return self.host_ok() and secrets.compare_digest(given, self.token)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    def do_GET(self):
        try:
            return self._do_get()
        except ValueError as exc:
            return self.send_json({'error': str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:
            traceback.print_exc()
            studio.log_event(studio.db(), 'error', level='error', where=f'GET {self.path.split("?")[0]}', err=f'{type(exc).__name__}: {exc}',
                             tb=traceback.format_exc()[-1500:])
            return self.send_json({'error': f'{type(exc).__name__}: {exc}'}, 500)

    def _do_get(self):
        if not self.host_ok():
            return self.send_error(403)
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path in ('/', '/index.html'):
            v = str(int(max(f.stat().st_mtime for f in WEB.iterdir())))
            page = (WEB / 'index.html').read_text().replace('__TOKEN__', self.token).replace('__V__', v)
            return self.send_body(page.encode(), 'text/html; charset=utf-8')
        if url.path.startswith('/static/'):
            f = (WEB / url.path[len('/static/'):]).resolve()
            if WEB.resolve() not in f.parents or not f.is_file():
                return self.send_error(404)
            return self.send_body(f.read_bytes(), mimetypes.guess_type(f.name)[0] or 'application/octet-stream',
                                  cache='no-cache')
        if not self.api_ok(q):
            return self.send_json({'error': 'forbidden'}, 403)
        if url.path == '/api/events':
            return self.events()
        conn = studio.db()
        if url.path == '/api/state':
            with self.lock:
                d = studio.status_data(conn)
            d['server'] = dict(started=STARTED, stale=code_stale())
            add_behaviors(d, conn)
            return self.send_json(d)
        if url.path == '/api/messages':
            if 'tail' in q:
                data = studio.rows(conn, f'SELECT * FROM (SELECT {MSG_COLS} FROM messages ORDER BY id DESC LIMIT ?) ORDER BY id',
                                   min(int(q['tail'][0]), 3000))
            else:
                data = studio.rows(conn, f'SELECT {MSG_COLS} FROM messages WHERE id>? ORDER BY id LIMIT 1000',
                                   int(q.get('after', ['0'])[0]))
            return self.send_json({'messages': data})
        if url.path == '/api/reports':
            ids = [int(x) for x in q.get('ids', [''])[0].split(',') if x.isdigit()]
            sql = 'SELECT * FROM reports' + (f' WHERE id IN ({",".join("?" * len(ids))})' if ids else ' WHERE status="open"')
            return self.send_json({'reports': [report_view(conn, r) for r in studio.rows(conn, sql, *ids)]})
        if url.path == '/api/mail':
            if 'box' not in q:
                return self.send_json({'boxes': mail.boxes(conn)})
            data = mail.mailbox(conn, q['box'][0])
            ids = sorted({x['report_id'] for x in data['inbox'] + data['outbox'] if x.get('report_id')})
            data['reports'] = [report_view(conn, r) for r in
                               (studio.rows(conn, f'SELECT * FROM reports WHERE id IN ({",".join("?" * len(ids))})', *ids) if ids else [])]
            return self.send_json(data)
        if url.path == '/api/screen':
            c = studio.camera(conn, int(q.get('camera', ['0'])[0]))
            if not c or not c['pane'] or c['pane'] not in studio.alive_panes():
                return self.send_json({'html': '', 'alive': False})
            return self.send_json({'html': ansi_html(studio.screen(c['pane'], ansi=True)), 'alive': True})
        if url.path == '/api/session':      # 「详细」模式：工具参数、子代理、待办、思考、状态栏
            c = studio.camera(conn, int(q.get('camera', ['0'])[0]))
            return self.send_json(studio.session_detail(conn, c) if c else {})
        if url.path == '/api/docs':
            f, s = studio.resolve_ref(conn, q.get('ref', [''])[0])
            if 'path' in q:
                return self.send_json({'path': q['path'][0], 'text': studio.read_seq_doc(f, s['key'], q['path'][0])})
            return self.send_json({'docs': studio.seq_docs(f, s['key'])})
        if url.path == '/api/library':
            return self.send_json(library(conn))
        if url.path == '/api/file':
            root = library_root(conn, q.get('root', [''])[0])
            rel = q.get('path', [''])[0]
            p = (root / rel).resolve()
            if root.resolve() not in p.parents or not p.is_file():
                raise ValueError('没有这个文件')
            return self.send_json({'path': rel, 'text': p.read_text(errors='replace')[:400_000]})
        if url.path == '/api/decisions':
            return self.send_json({'decisions': studio.rows(conn, 'SELECT * FROM decisions ORDER BY id DESC LIMIT 300')})
        if url.path == '/api/budget':
            by_day = studio.rows(conn, 'SELECT day, SUM(tokens_in) tin, SUM(tokens_out) tout, SUM(cache_read) cr, '
                                       'SUM(cache_write) cw, SUM(cost) cost FROM usage GROUP BY day ORDER BY day DESC LIMIT 30')
            by_film = studio.rows(conn, 'SELECT IFNULL(film_id,"(麻薯)") film_id, role, SUM(tokens_in) tin, SUM(tokens_out) tout, '
                                        'SUM(cache_read) cr, SUM(cache_write) cw, SUM(cost) cost FROM usage WHERE day=? '
                                        'GROUP BY film_id, role ORDER BY film_id', q.get('day', [studio.today()])[0])
            cams = studio.rows(conn, 'SELECT id, role, name, film_id, seq_key, crew_role, state, tokens_in, tokens_out, cache_read, '
                                     'cache_write, cost, context_pct, model FROM cameras ORDER BY id DESC LIMIT 60')
            return self.send_json({'by_day': by_day, 'by_film': by_film, 'cameras': cams,
                                   'priced': bool(studio.cfg('prices'))})
        if url.path == '/api/candidates':
            return self.send_json({'candidates': studio.scan_candidates(conn)})
        if url.path == '/api/self-iterate':
            import selfiter
            return self.send_json(selfiter.api_data(conn, q.get('day', [''])[0]))
        if url.path == '/api/dashboard':      # 项目页「工作流」视图（#7）：几秒内重复请求直接用缓存
            import dashboard
            fid = q.get('film', [''])[0]
            hit = DASH_CACHE.get(fid)
            if not hit or time.time() - hit[0] > 4 or 'fresh' in q:
                hit = DASH_CACHE[fid] = (time.time(), dashboard.dashboard(conn, fid))
            return self.send_json(hit[1])
        if url.path == '/api/blob':
            return self.blob(conn, q.get('path', [''])[0])
        if url.path == '/api/audit':
            return self.send_json({'audit': studio.rows(conn, 'SELECT * FROM audit ORDER BY id DESC LIMIT 200')})
        self.send_error(404)

    def events(self):
        """SSE：消息或状态有变化就推一条；正在写的回复单独推 live；断线时前端退回轮询。"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        conn = studio.db()
        last = live_last = None
        beat = time.time()
        try:
            while True:
                live = json.dumps(studio.live_replies(conn), ensure_ascii=False)
                if live != live_last:      # 正在写的回复：分段推给页面
                    live_last = live
                    self.wfile.write(f'event: live\ndata: {live}\n\n'.encode())
                    self.wfile.flush()
                mid = studio.one(conn, 'SELECT MAX(id) m FROM messages')['m'] or 0
                sig = studio.one(conn, 'SELECT (SELECT IFNULL(MAX(updated),0)+IFNULL(MAX(last_active),0)+IFNULL(MAX(last_stop),0) FROM cameras) a, '
                                       '(SELECT COUNT(*) FROM reports WHERE status="open") b, '
                                       '(SELECT IFNULL(MAX(id),0) FROM decisions) c, (SELECT COUNT(*) FROM decisions WHERE status="待追认") d')
                cur = (mid, tuple(sig.values()))
                if cur != last:
                    last = cur
                    self.wfile.write(f'data: {json.dumps({"msg": mid})}\n\n'.encode())
                    self.wfile.flush()
                elif time.time() - beat > 15:
                    self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
                    beat = time.time()
                time.sleep(0.8)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def blob(self, conn, raw):
        """聊天里的图片 / 文件：只给上传目录、项目目录、麻薯代码目录、临时目录和桌面 / 下载 / 图片里的文件。"""
        p = Path(raw).expanduser().resolve()
        if not p.is_file() or not viewable(conn, p):
            return self.send_error(404)
        if p.stat().st_size > MAX_UPLOAD:
            raise ValueError('文件太大')
        ctype = mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or p.suffix in ('.md', '.json', '.log', '.sh', '.py', '.ts', '.js', '.yaml', '.yml'):
            ctype = 'text/plain; charset=utf-8'
        st = p.stat()
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'      # 同路径换了文件也能刷新（交付图常原地替换）
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'private, no-cache')
        self.send_header('ETag', etag)
        self.send_header('Content-Disposition', f"inline; filename*=UTF-8''{quote_name(p.name)}")
        self.send_header('X-Content-Type-Options', 'nosniff')
        if ctype in ('text/html', 'image/svg+xml'):
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; img-src data:")
        body = p.read_bytes()
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def upload(self):
        """聊天框粘贴 / 拖进来 / 📎 选的文件：原样存进 state/uploads/<日期>/，返回绝对路径，消息里带路径给 agent 用 Read 看。"""
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_UPLOAD:
            return self.send_json({'error': f'文件太大（上限 {MAX_UPLOAD // 1_000_000}MB）'}, 400)
        name = unquote(self.headers.get('X-File-Name') or '') or 'file'
        safe = re.sub(r'[\\/:*?"<>|()\[\]\s\x00-\x1f]+', '_', Path(name).name).strip(' ._') or 'file'   # 消息里用 markdown 链接带路径
        if len(safe) > 80:
            stem, dot, ext = safe.rpartition('.')
            safe = (stem[:70] + dot + ext) if dot and len(ext) <= 8 else safe[:80]
        d = studio.HOME / 'uploads' / studio.today()
        clean = lambda x: re.sub(r'[\\/:*?"<>|()\[\]\s\x00-\x1f]+', '_', x).strip(' ._')   # noqa: E731
        group = clean(unquote(self.headers.get('X-Upload-Dir') or ''))
        if group:          # 拖进来的文件夹：存进 uploads/<日期>/<组>/，保留里面的目录结构
            segs = [clean(x) for x in unquote(self.headers.get('X-Rel-Path') or '').split('/')[:-1]]
            d = d.joinpath(group, *[x for x in segs if x])
        d.mkdir(parents=True, exist_ok=True)
        f = d / safe if group else d / f'{time.strftime("%H%M%S")}-{secrets.token_hex(2)}-{safe}'
        f.write_bytes(self.rfile.read(n))
        mime = self.headers.get('Content-Type') or mimetypes.guess_type(safe)[0] or ''
        return self.send_json({'path': str(f), 'name': name, 'size': n, 'mime': mime, 'dir': str(studio.HOME / 'uploads' / studio.today() / group) if group else '',
                               'image': mime.startswith('image/') or f.suffix.lower() in IMAGE_EXT})

    def do_POST(self):
        url = urlparse(self.path)
        if not self.api_ok():
            return self.send_json({'error': 'forbidden'}, 403)
        if url.path == '/api/upload':
            try:
                return self.upload()
            except OSError as exc:
                return self.send_json({'error': f'保存失败：{exc}'}, 500)
        try:
            data = self.body()
            conn = studio.db()
            with self.lock:
                result = self.route(conn, url.path, data)
            if result is None:
                return self.send_error(404)
            return self.send_json(result)
        except ValueError as exc:
            self.send_json({'error': str(exc)}, 400)
        except Exception as exc:  # 返回给页面，避免静默失败
            traceback.print_exc()
            studio.log_event(studio.db(), 'error', level='error', where=f'POST {self.path.split("?")[0]}', err=f'{type(exc).__name__}: {exc}',
                             tb=traceback.format_exc()[-1500:])
            self.send_json({'error': f'{type(exc).__name__}: {exc}'}, 500)

    def route(self, conn, path, data):
        if path == '/api/send':
            text = (data.get('text') or '').strip()
            if not text:
                raise ValueError('内容为空')
            channel = data.get('channel') or 'assistant'
            if channel == 'assistant':
                target = 'assistant'
            else:
                film_id, seq = studio.split_channel(channel)
                f = studio.require_film(conn, film_id)
                target = f'crew:{f["id"]}/{studio.find_seq(conn, f, seq)["key"]}' if seq else f'producer:{f["id"]}'
            return {'id': studio.post(conn, 'executive', channel, 'chat', text, target=target)}
        if path == '/api/mute':        # 静默：不处理这件上报，维持现状
            r = studio.mute_report(conn, int(data['report_id']))
            return {'ok': True, 'status': r['status']}
        if path == '/api/answer':
            text = (data.get('text') or '').strip()
            if not text:
                raise ValueError('批复内容为空')
            r = studio.answer_report(conn, int(data['report_id']), None, text, exec_cfg=data.get('exec') or None)
            return {'ok': True, 'status': r['status']}
        if path == '/api/camera':
            return {'result': self.camera_action(conn, data)}
        if path == '/api/decision':
            d = studio.one(conn, 'SELECT * FROM decisions WHERE id=?', int(data['id']))
            if not d:
                raise ValueError('没有这条决策')
            status = data.get('status')
            if status not in ('已追认', '已推翻'):
                raise ValueError('状态只能是已追认 / 已推翻')
            note = (data.get('note') or '').strip()
            conn.execute('UPDATE decisions SET status=?, review_note=?, reviewed_at=? WHERE id=?',
                         (status, note, studio.now_iso(), d['id']))
            conn.commit()
            studio.audit(conn, 'executive', 'ratify' if status == '已追认' else 'overrule', d['film_id'], d['seq_key'],
                         f'决策 #{d["id"]}：{d["summary"]}')
            undo = json.loads(d.get('undo') or 'null')
            if status == '已推翻' and undo and undo.get('type') == 'playbook':
                pb = studio.restore_playbook(conn, undo['target'], undo['version'])
                studio.post(conn, 'executive', 'assistant', 'chat',
                            f'我推翻了工作流改版（决策记录 #{d["id"]}：{d["summary"]}），已自动回滚，现在是 v{pb["version"]}。'
                            + (f'原因：{note}。' if note else '') + '把这条记进复盘，别再这样改。', target='assistant')
            elif status == '已推翻' and undo and undo.get('type') == 'trust':
                studio.demote_trust(conn, undo['film'], undo['category'], note)
                studio.post(conn, 'executive', 'assistant', 'chat',
                            f'我推翻了决策记录 #{d["id"]}（{d["summary"]}）。「{undo["category"].split(":", 1)[1]}」这类审批收回由我来批。'
                            + (f'原因：{note}。' if note else '') + '请安排补救，并记进复盘。', target='assistant')
            elif status == '已推翻':
                studio.post(conn, 'executive', 'assistant', 'chat',
                            f'我推翻了决策记录 #{d["id"]}（{d["actor_name"]}：{d["summary"]}）' + (f'。原因：{note}' if note else '') +
                            '。请安排补救。', target='assistant')
            return {'ok': True}
        if path == '/api/greenlight':
            f, created = studio.greenlight(conn, data.get('path', ''), data.get('id') or None, data.get('title') or None)
            return {'film': f['id'], 'created': created, 'producer': f['producer_name']}
        if path == '/api/pitch':
            f, key, cam = studio.pitch(conn, data['film'], data['slug'], data['brief'], data.get('title') or None,
                                          workflow=bool(data.get('workflow')))
            return {'key': key, 'crew': cam['name']}
        if path == '/api/reveal':      # 聊天里的文件 / 文件夹：在访达里打开（文件选中显示，文件夹直接打开）
            import subprocess
            p = Path(str(data.get('path') or '')).expanduser().resolve()
            if not p.exists():
                raise ValueError('这个文件已经不在了')
            plain_dir = p.is_dir() and p.suffix.lower() not in ('.app', '.bundle', '.pkg', '.framework', '.plugin')
            subprocess.run(['open', str(p)] if plain_dir else ['open', '-R', str(p)], timeout=10, check=False)
            return {'ok': True}
        if path == '/api/restart-server':
            studio.audit(conn, 'executive', 'restart-server', None, None, '网页按钮重启服务')
            restart_later()
            return {'ok': True}
        if path == '/api/priority':
            f, s = studio.resolve_ref(conn, data['ref'])
            conn.execute('UPDATE sequences SET priority=? WHERE film_id=? AND key=?', (int(data['value']), f['id'], s['key']))
            conn.commit()
            return {'ok': True}
        return None

    def camera_action(self, conn, data):
        action = data.get('action')
        if action == 'digest-now':
            studio.send_digest(conn)
            return '已让麻薯整理简报'
        if action == 'interrupt':
            return studio.interrupt(conn, studio.camera_of_channel(conn, data.get('channel') or 'assistant'))
        if action == 'handoff':
            return studio.request_handoff(conn, studio.camera_of_channel(conn, data.get('channel') or 'assistant'))
        if action == 'new-session':
            return studio.fresh_session(conn, data.get('channel') or 'assistant')
        if action == 'defaults':
            return studio.set_defaults(conn, models=data.get('models'), efforts=data.get('efforts'), codes=data.get('codes'),
                                       clear=bool(data.get('clear')), providers=data.get('providers'), gmodels=data.get('gmodels'),
                                       xmodels=data.get('xmodels'), omodels=data.get('omodels'))
        if action == 'gemini-models':
            return studio.refresh_gemini_models()
        if action == 'opencode-models':
            return studio.refresh_opencode_models()
        if action == 'pref':
            return studio.set_pref(conn, data.get('channel') or 'assistant', model=data.get('model'), effort=data.get('effort'),
                                   code=data.get('code'), provider=data.get('provider'), gmodel=data.get('gmodel'),
                                   xmodel=data.get('xmodel'), omodel=data.get('omodel'))
        if action == 'interrupt-all':
            busy = [c for c in studio.rows(conn, f'SELECT * FROM cameras WHERE state IN {studio.LIVE} AND last_active>last_stop')]
            for c in busy:
                studio.interrupt(conn, c)
            return f'打断了 {len(busy)} 个正在忙的会话' if busy else '现在没有会话在忙'
        if action == 'slash':
            cmd = (data.get('cmd') or '').strip()
            if cmd not in SLASH_PASSTHROUGH:
                raise ValueError('不支持的命令')
            c = studio.camera_of_channel(conn, data.get('channel') or 'assistant')
            if not c or c['pane'] not in studio.alive_panes():
                return '没有在运行的会话'
            if c['last_active'] > c['last_stop']:
                return f'「{c["name"]}」正在忙，先打断（Esc）或等它这一轮做完'
            studio.paste(c['pane'], cmd)
            conn.execute('UPDATE cameras SET state="running", last_active=? WHERE id=?', (time.time(), c['id']))
            conn.commit()
            studio.post(conn, 'system', studio.channel_of(c), 'handover', f'—— 你让「{c["name"]}」执行 {cmd} ——')
            return f'已让「{c["name"]}」执行 {cmd}'
        if action == 'restart-assistant':
            a = studio.active_assistant(conn)
            if a:
                studio.end_camera(conn, a['id'], 'restart', '用户在网页让麻薯交接')
            return '麻薯交接中'
        if action in ('producer-call', 'producer-off'):
            f = studio.require_film(conn, data['film'])
            p = studio.active_producer(conn, f['id'])
            if action == 'producer-off':
                if p:
                    studio.end_camera(conn, p['id'], 'offduty', '用户在网页停止子脑')
                    studio.post(conn, 'system', f['id'], 'handover', f'—— 负责人「{p["name"]}」被用户叫停止 ——')
                return '已停止'
            if p:
                return f'负责人「{p["name"]}」已运行中'
            studio.create_camera(conn, 'producer', f['id'], None, studio.producer_prompt(f, '[用户] 叫你启动，先看看本项目的状态总览，简短汇报现状。'))
            return f'负责人「{f["producer_name"]}」启动中'
        f, s = studio.resolve_ref(conn, data['ref'])
        key = s['key']
        if action == 'action':
            studio.grant_kickoff(conn, f, key, '用户')       # 用户亲手启动 = 批准开工
            cam, created = studio.request_crew(conn, f, key, studio.continue_prompt(f, key))
            return f'「{cam["name"]}」{"排队" if cam["state"] == "queued" else "已在拍"}'
        if action == 'cut':
            cam = studio.active_crew(conn, f['id'], key)
            if not cam:
                return '没有在拍的执行者'
            studio.end_camera(conn, cam['id'], 'cut', '用户在网页停止')
            studio.notice(conn, f'{f["id"]}/{key}', f'用户停止「{cam["name"]}」')
            studio.audit(conn, 'executive', 'cut', f['id'], key, cam['name'])
            return f'已停止「{cam["name"]}」'
        if action == 'retake':
            row = studio.retake(conn, f, key, '用户')
            return f'新执行者「{row["name"]}」排队'
        fn = getattr(studio, 'web_seq_action', None)      # 归档 / 取消归档 / 已查看（studio.web_seq_action）
        r = fn(conn, f, key, action, data) if fn else None
        if r is not None:
            return r
        raise ValueError('未知动作')


DOC_EXT = ('.md', '.json', '.txt', '.sh')
MAX_UPLOAD = 50_000_000
IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.heic')
SLASH_PASSTHROUGH = ('/compact',)     # 直接交给 Claude Code 的斜杠命令


def quote_name(name):
    from urllib.parse import quote
    return quote(name)


def viewable(conn, p):
    home = Path.home()
    roots = [studio.HOME / 'uploads', studio.CODE, Path(tempfile.gettempdir()), Path('/tmp'), Path('/private/tmp'),
             home / 'Desktop', home / 'Downloads', home / 'Pictures']
    roots += [Path(f['path']).expanduser() for f in studio.films(conn) if f.get('path')]
    if any(part.startswith('.') and part not in ('.mashu', '.claude-shots') for part in p.parts[:-1]) and \
            (studio.HOME / 'uploads').resolve() not in p.parents:
        return False      # 不给 .ssh / .git / .env 目录之类
    if p.name.startswith('.env') or p.suffix in ('.pem', '.key'):
        return False
    return any(r.resolve() in p.parents for r in roots if r.exists())


def report_view(conn, r):
    """上报加上来源、路线、审批状态、文档等，给网页的卡片用。"""
    r['origin'] = ' · '.join(studio.report_origin(conn, r))
    r['route'] = json.loads(r['route'] or '[]')
    r['options'] = json.loads(r['options'] or '[]')
    r['meta'] = json.loads(r.get('meta') or '{}')
    r['required'] = studio.required_level(r)
    r['gate_label'] = studio.GATES.get(r['gate'] or '', r['gate'])
    ap = studio.one(conn, 'SELECT granted_at, expires_at, gate FROM approvals WHERE report_id=? ORDER BY id DESC LIMIT 1', r['id'])
    r['approval'] = ap
    f = studio.film(conn, r['film_id']) if r['film_id'] else None
    r['film_title'] = f['title'] if f else ''
    r['docs'] = studio.seq_docs(f, r['seq_key']) if f and r['seq_key'] else []
    if r['confirm_stage']:
        r['stage_label'] = studio.stage_text_of(studio.seq_state(f, r['seq_key']) if f and r['seq_key'] else None,
                                                r['confirm_stage'])
    return r


def library_root(conn, key):
    if key == 'memory':
        return studio.MEMORY
    if key == 'kmap':
        return studio.KMAP
    if key == 'playbooks':
        return studio.PLAYBOOKS
    if key == 'behaviors':
        return BEHAVIORS
    if key.startswith('film:'):
        return studio.film_docs(studio.require_film(conn, key.split(':', 1)[1]))
    if key.startswith('filmdocs:'):     # 项目自己的文档 / 知识地图：<工程>/docs/
        return Path(studio.require_film(conn, key.split(':', 1)[1])['path']) / 'docs'
    if key.startswith('filmpb:'):       # 项目专用工作流模板：<工程>/.mashu/playbooks/
        return studio.film_templates_dir(studio.require_film(conn, key.split(':', 1)[1]))
    if key.startswith('filmbh:'):       # 项目专用行为模板：<工程>/.mashu/playbooks/behaviors/
        return studio.film_templates_dir(studio.require_film(conn, key.split(':', 1)[1])) / 'behaviors'
    raise ValueError('未知资料目录')


def library(conn):
    """资料库目录树：麻薯的记忆、通用工作流模板、每部项目的 .mashu/（项目专用的工作流和行为模板单列在项目下）。"""
    def files(root, skip=('history',)):
        out = []
        if not root.is_dir():
            return out
        for p in sorted(root.rglob('*')):
            rel = p.relative_to(root)
            if p.is_file() and p.suffix in DOC_EXT and not (set(rel.parts) & set(skip)) and p.stat().st_size < 2_000_000:
                out.append(dict(path=str(rel), mtime=p.stat().st_mtime))
        return out
    # 按归属分区：麻薯自己的（mashu_studio 里）一块，每个项目一块（全在各自工程文件夹里），互不混放
    owners = [dict(key='mashu', title='麻薯', path=str(studio.CODE))]
    groups = [dict(key='memory', owner='mashu', title='记忆', files=files(studio.MEMORY)),
              dict(key='kmap', owner='mashu', title='知识地图（docs）', files=files(studio.KMAP, skip=('history', 'memory', 'reference'))),
              dict(key='playbooks', owner='mashu', title='通用工作流模板', files=files(studio.PLAYBOOKS, skip=('history', 'behaviors')))]
    scopes = behavior_scopes(conn)
    if scopes.get(''):
        groups.append(dict(key='behaviors', owner='mashu', title='通用行为模板', files=bh_files(scopes[''])))
    for f in studio.films(conn):
        studio.ensure_film_docs(f)
        o = f'film:{f["id"]}'
        owners.append(dict(key=o, title=f['title'], path=f['path'], film=f['id']))
        groups.append(dict(key=o, owner=o, title='项目资料（.mashu）', film=f['id'],
                           files=files(studio.film_docs(f), skip=('history', 'playbooks'))))
        pbs = [dict(path=p.name, mtime=p.stat().st_mtime) for p in sorted(studio.film_templates_dir(f).glob('*.json'))]
        if pbs:
            groups.append(dict(key=f'filmpb:{f["id"]}', owner=o, title='工作流模板', film=f['id'], files=pbs))
        if scopes.get(f['id']):
            groups.append(dict(key=f'filmbh:{f["id"]}', owner=o, title='行为模板', film=f['id'], files=bh_files(scopes[f['id']])))
        pdocs = Path(f['path']) / 'docs'
        if pdocs.resolve() != studio.KMAP.resolve():     # 麻薯电脑秘书的 docs 就是麻薯的知识地图，不重复列
            ds = [x for x in files(pdocs, skip=('history', 'node_modules')) if x['path'].endswith('.md')]
            if ds:
                groups.append(dict(key=f'filmdocs:{f["id"]}', owner=o, title='项目文档（docs）', film=f['id'], files=ds))
    return {'owners': owners, 'groups': groups, 'behaviors': scopes}


BEHAVIORS = studio.PLAYBOOKS / 'behaviors'     # 通用行为模板（目前没有；项目专用的在 <工程>/.mashu/playbooks/behaviors/）


def bh_files(idx):
    return [dict(path=f'{k}.md', title=v['title'], mtime=v['mtime']) for k, v in idx.items()]


def behavior_ids(v):
    """阶段的 behavior 字段：一个 id 或 id 列表。"""
    return [v] if isinstance(v, str) and v else [x for x in (v if isinstance(v, list) else []) if isinstance(x, str) and x]


def behavior_index(bdir, sources):
    """bdir 下的行为模板 → {id: {title, mtime, used: [{root, path, name, stage, stage_name}]}}。标题取 md 第一行 # 标题，
    去掉「行为模板：」和末尾的（id）；README 叫「对照表」。used 从 sources=[(资料库 root, path, 文件)] 的 stages[].behavior 反查。"""
    if not bdir.is_dir():
        return {}
    out = {}
    for p in sorted(bdir.glob('*.md'), key=lambda x: (x.stem != 'README', x.stem)):
        title = ''
        for line in p.read_text(errors='replace').splitlines():
            if line.startswith('# '):
                title = line[2:].strip()
                break
        title = re.sub(r'（[^（）]*）$|\([^()]*\)$', '', re.sub(r'^行为模板[：:]\s*', '', title)).strip()
        out[p.stem] = dict(title='对照表' if p.stem == 'README' else (title or p.stem), mtime=p.stat().st_mtime, used=[])
    for root, rel, pb in sources:
        try:
            data = json.loads(pb.read_text())
        except (OSError, ValueError):
            continue
        for i, st in enumerate(data.get('stages') or [], 1):
            for b in behavior_ids(st.get('behavior')):
                if b in out:
                    out[b]['used'].append(dict(root=root, path=rel, name=data.get('name') or pb.stem,
                                               stage=st.get('id') or i, stage_name=st.get('name', '')))
    return out


def behavior_scopes(conn):
    """行为模板按归属分：{'': 通用, 项目id: 该项目 .mashu/playbooks/behaviors/}；反查只看同一归属下的工作流。"""
    out = {'': behavior_index(BEHAVIORS, [('playbooks', p.name, p) for p in sorted(studio.PLAYBOOKS.glob('*.json'))])}
    for f in studio.films(conn):
        d = studio.film_templates_dir(f)
        out[f['id']] = behavior_index(d / 'behaviors', [(f'filmpb:{f["id"]}', p.name, p) for p in sorted(d.glob('*.json'))]
                                      + [(f'film:{f["id"]}', 'playbook.json', studio.film_docs(f) / 'playbook.json')])
    return {k: v for k, v in out.items() if v}


def add_behaviors(d, conn):
    """给 /api/state 补上行为模板：behaviors={归属('' 或项目id): {id: 标题}}，每个功能的 stage_defs[i].behavior=[id...]
    （功能状态文件不存 behavior，按它记的工作流 id 回工作流 JSON 里按阶段名对上；模板先找本项目的）。"""
    try:
        d['behaviors'] = {sc: {k: v['title'] for k, v in idx.items()} for sc, idx in behavior_scopes(conn).items()}
        cache = {}
        for f in d.get('films') or []:
            for s in f.get('sequences') or []:
                pid = (s.get('playbook') or {}).get('id') or ''
                ck = (f.get('id'), pid)
                if ck not in cache:
                    try:
                        cache[ck] = (studio.film_playbook(studio.film(conn, pid.split(':', 1)[1]))
                                     if pid.startswith('film:') else studio.load_template(pid, f)) if pid else {}
                    except (ValueError, OSError, TypeError, KeyError):
                        cache[ck] = {}
                st = cache[ck].get('stages') or []
                for i, sd in enumerate(s.get('stage_defs') or []):
                    m = st[i] if i < len(st) and st[i].get('name') == sd.get('name') else \
                        next((x for x in st if x.get('name') == sd.get('name')), {})
                    sd['behavior'] = behavior_ids(m.get('behavior'))
    except Exception:       # 行为模板只是展示，出错不影响状态接口
        traceback.print_exc()


def scheduler(interval):
    while True:
        try:
            with Handler.lock:
                studio.tick(studio.db())
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=None)
    p.add_argument('--interval', type=float, default=3.0)
    p.add_argument('--no-scheduler', action='store_true')
    args = p.parse_args()
    port = args.port or studio.cfg('port')
    Handler.token, Handler.port = load_token(), port
    studio.hooks_settings()
    studio.ensure_memory()
    conn = studio.db()
    for f in studio.films(conn):
        studio.ensure_film_docs(f)
    if not args.no_scheduler:
        threading.Thread(target=scheduler, args=(args.interval,), daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    print(f'麻薯电脑秘书：http://127.0.0.1:{port}/  （调度器{"关闭" if args.no_scheduler else "每 %.0fs 跑一轮" % args.interval}）', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
