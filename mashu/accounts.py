"""账号池：麻薯自己管多个 Codex、Gemini（agy）账号——加号、续期、按号额度、选号、Gemini 全局切号。

不依赖 studio.py（studio 调这里），方便单测；网络请求都走 http()，单测里换成假的。
账号清单、凭据（Gemini RT / AT、非本机 Codex auth）、OAuth 客户端缓存统一存在主配置文件 studio.json（0600，加锁原子写）：
  <root>/codex/<号>/           这个号的 CODEX_HOME（运行前由主文件生成 auth.json；本机默认号用 state/codex-home 软链接到 ~/.codex）
令牌原文只在 studio.json / 运行时 auth.json / 钥匙串里；返回给网页和日志的一律经过 mask() / redact()。
说明见 docs/references/platforms.md「账号池」。
"""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import config_file as cfgfile

PLATFORMS = ('codex', 'gemini')
PREFIX = dict(codex='cx', gemini='gm')
STATUS_CN = dict(ok='正常', pending='等登录', logged_out='未登录', refresh_failed='续期失败', revoked='已撤销')
CODEX_TOKEN_URL = 'https://auth.openai.com/oauth/token'
CODEX_CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'     # Codex CLI 的公开 OAuth 客户端（auth.json 的 oauth_provenance 里有就用那个）
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'
CODE_ASSIST_MODELS_URL = 'https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels'
GOOGLE_SCOPES = ('openid https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email '
                 'https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/cclog '
                 'https://www.googleapis.com/auth/experimentsandconfigs')
KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT = 'gemini', 'antigravity'
KEYRING_PREFIX = 'go-keyring-base64:'
AGY_TOKEN_FILE = Path.home() / '.gemini' / 'jetski-standalone-oauth-token'    # agy 的备用凭据文件（存在才同步改）


class AccountError(ValueError):
    """加号 / 续期失败（消息已脱敏，可直接给用户看）。"""


class Revoked(AccountError):
    """RT 被撤销或过期（invalid_grant）：这个号要重新登录。"""


# ---------- 脱敏 ----------

def mask(s):
    """令牌只显示前 4 位加上 …。"""
    s = str(s or '')
    return f'{s[:4]}…' if s else ''


_TOKEN_RE = re.compile(r'ya29\.[\w.-]{8,}|1//[\w-]{16,}|eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*|\brt_[\w.-]{16,}|GOCSPX-[\w-]{8,}|sk-[\w-]{16,}')


def redact(text, extra=()):
    """日志 / 报错里的令牌换成前 4 位 + …：已知的几种令牌格式，加上调用方给的原文。"""
    out = str(text or '')
    for s in sorted({str(x) for x in extra if x and len(str(x)) >= 8}, key=len, reverse=True):
        out = out.replace(s, mask(s))
    return _TOKEN_RE.sub(lambda m: mask(m.group(0)), out)


def rt_hash(rt):
    return hashlib.sha256(str(rt or '').encode()).hexdigest()[:16] if rt else ''


# ---------- 文件 ----------

write_private = cfgfile.write_private
write_json = cfgfile.write_json
read_json = cfgfile.read_json
file_lock = cfgfile.file_lock


# ---------- 网络（单测换掉 HTTP）----------

def _http(method, url, data=None, headers=None, timeout=20):
    """返回 (状态码, 解析后的 JSON 或文本)。data 是 dict：headers 里 Content-Type 为 json 就发 JSON，否则表单。"""
    headers = dict(headers or {})
    body = None
    if data is not None:
        if 'json' in headers.get('Content-Type', ''):
            body = json.dumps(data).encode()
        else:
            body = urllib.parse.urlencode(data).encode()
            headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
    req = urllib.request.Request(url, body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except (urllib.error.URLError, OSError) as e:
        raise AccountError(f'网络请求失败（{urllib.parse.urlparse(url).netloc}）：{e}')
    try:
        return code, json.loads(raw or b'{}')
    except ValueError:
        return code, raw.decode(errors='ignore')[:500]


HTTP = _http


def http(method, url, data=None, headers=None, timeout=20):
    return HTTP(method, url, data=data, headers=headers, timeout=timeout)


def _err_code(body):
    if isinstance(body, dict):
        e = body.get('error')
        if isinstance(e, dict):
            return str(e.get('code') or e.get('status') or e.get('message') or '')
        return str(e or '')
    return ''


# ---------- 账号库（统一存主配置文件 studio.json）----------

class Pool:
    def __init__(self, root, config_path=None):
        self.root = Path(root)
        self.config_path = Path(config_path) if config_path else self.root.parent / 'studio.json'

    def exists(self):
        return bool(self._read().get('accounts'))

    def lock(self, name='studio'):
        return cfgfile.config_lock(self.config_path)

    def _read_cfg(self):
        return cfgfile.read_config(self.config_path)

    def _read(self):
        acc = self._read_cfg().get('accounts') or {}
        return dict(accounts=list(acc.get('items') or []), defaults=dict(acc.get('defaults') or {}))

    def _write_cfg(self, cfg):
        cfgfile.write_config(self.config_path, cfg)

    def list(self, platform=None):
        return [a for a in self._read()['accounts'] if not platform or a.get('platform') == platform]

    def get(self, aid):
        return next((a for a in self.list() if a.get('id') == aid), None)

    def adir(self, aid, platform=None):
        a = self.get(aid) if platform is None else dict(platform=platform)
        return self.root / (a or {}).get('platform', 'codex') / aid

    def default(self, platform):
        """默认号：设过的优先，其次本机导入的，再其次第一个。"""
        d = self._read()
        accs = [a for a in d['accounts'] if a.get('platform') == platform]
        want = d['defaults'].get(platform)
        for a in accs:
            if a['id'] == want:
                return a
        return next((a for a in accs if a.get('source') == 'local'), accs[0] if accs else None)

    def add(self, platform, **meta):
        if platform not in PLATFORMS:
            raise AccountError(f'只支持 {" / ".join(PLATFORMS)}：{platform}')
        with self.lock():
            cfg = self._read_cfg()
            items = cfg['accounts']['items']
            nums = [int(m.group(1)) for a in items for m in [re.fullmatch(rf'{PREFIX[platform]}-(\d+)', a.get('id', ''))] if m]
            aid = f'{PREFIX[platform]}-{max(nums + [0]) + 1}'
            a = dict(id=aid, platform=platform, label='', email='', plan='', source='', status='pending', disabled=False,
                     created=time.time(), last_refresh=None, last_check=None, error='', quota=None)
            a.update({k: v for k, v in meta.items() if k != 'id'})
            items.append(a)
            self._write_cfg(cfg)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        for p in (self.root / platform, self.root / platform / aid):
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(p, 0o700)
        return a

    def update(self, aid, **kw):
        with self.lock():
            cfg = self._read_cfg()
            for a in cfg['accounts']['items']:
                if a.get('id') == aid:
                    if 'error' in kw:
                        kw['error'] = redact(kw['error'])[:300]
                    a.update(kw)
                    self._write_cfg(cfg)
                    return dict(a)
        raise AccountError(f'没有这个号：{aid}')

    def get_credentials(self, aid):
        a = self.get(aid)
        return dict(a.get('credentials') or {}) if a else {}

    def set_credentials(self, aid, cred):
        with self.lock():
            cfg = self._read_cfg()
            for a in cfg['accounts']['items']:
                if a.get('id') == aid:
                    a['credentials'] = dict(cred or {})
                    self._write_cfg(cfg)
                    return dict(a)
        raise AccountError(f'没有这个号：{aid}')

    def gemini_clients(self):
        return dict(self._read_cfg()['accounts'].get('gemini_clients') or {})

    def set_gemini_clients(self, cache):
        with self.lock():
            cfg = self._read_cfg()
            cfg['accounts']['gemini_clients'] = dict(cache or {})
            self._write_cfg(cfg)

    def sync_codex_from_home(self, aid, home):
        """把 Codex 运行时 auth.json 同步回主文件（Codex 续期写了新 RT 或刚登录完时调用）。"""
        ap = Path(home) / 'auth.json'
        if ap.is_symlink() or not ap.exists():
            return False
        d = read_json(ap)
        if not isinstance(d, dict):
            return False
        t = d.get('tokens') if isinstance(d.get('tokens'), dict) else {}
        if not t.get('refresh_token') and not d.get('OPENAI_API_KEY'):
            return False
        cur = self.get_credentials(aid)
        if cur != d:
            self.set_credentials(aid, d)
            return True
        return False

    def sync_codex_to_home(self, aid, home):
        """开会话 / 体检前：主文件是来源，确保非本机 Codex 号目录里有 auth.json；若磁盘 auth.json 更新（Codex 刚续期）则先回写主文件。"""
        a = self.get(aid)
        if not a or a.get('platform') != 'codex' or a.get('source') == 'local':
            return False
        home = Path(home)
        ap = home / 'auth.json'
        cred = dict(a.get('credentials') or {})
        disk = None if (ap.is_symlink() or not ap.exists()) else read_json(ap)
        disk_ok = isinstance(disk, dict) and bool((disk.get('tokens') or {}).get('refresh_token') or disk.get('OPENAI_API_KEY'))
        cred_ok = bool((cred.get('tokens') or {}).get('refresh_token') or cred.get('OPENAI_API_KEY'))
        if disk_ok and not cred_ok:
            self.set_credentials(aid, disk)
            return True
        if disk_ok and cred_ok:
            disk_ts = _iso_ts(disk.get('last_refresh')) or 0
            cred_ts = _iso_ts(cred.get('last_refresh')) or 0
            if disk_ts > cred_ts:
                self.set_credentials(aid, disk)
                return True
        if cred_ok and disk != cred:
            if ap.is_symlink():
                ap.unlink()
            write_json(ap, cred)
            return True
        return False

    def remove(self, aid):
        """删号：删元数据和它的目录。本机默认号（source=local）的 Codex 目录是 state/codex-home，不在池里，不动。"""
        import shutil
        with self.lock():
            cfg = self._read_cfg()
            items = cfg['accounts']['items']
            a = next((x for x in items if x.get('id') == aid), None)
            if not a:
                raise AccountError(f'没有这个号：{aid}')
            cfg['accounts']['items'] = [x for x in items if x.get('id') != aid]
            if cfg['accounts']['defaults'].get(a['platform']) == aid:
                cfg['accounts']['defaults'].pop(a['platform'], None)
            self._write_cfg(cfg)
        p = self.root / a['platform'] / aid
        if p.is_dir() and not p.is_symlink() and p.resolve().is_relative_to(self.root.resolve()):
            shutil.rmtree(p)
        return a

    def set_default(self, platform, aid):
        with self.lock():
            cfg = self._read_cfg()
            items = cfg['accounts']['items']
            if aid and not any(a['id'] == aid and a['platform'] == platform for a in items):
                raise AccountError(f'{platform} 里没有这个号：{aid}')
            if aid:
                cfg['accounts']['defaults'][platform] = aid
            else:
                cfg['accounts']['defaults'].pop(platform, None)
            self._write_cfg(cfg)

    def view(self, platform=None):
        """给网页 / 命令行看的（不含令牌）。"""
        out = []
        for a in self.list(platform):
            v = {k: a.get(k) for k in ('id', 'platform', 'label', 'email', 'plan', 'source', 'status', 'disabled', 'created',
                                       'last_refresh', 'last_check', 'error', 'quota')}
            v['status_cn'] = STATUS_CN.get(a.get('status'), a.get('status') or '')
            v['error'] = redact(v.get('error') or '')
            out.append(v)
        return out


# ---------- 选号 ----------

def used_pct(a):
    """这个号额度用掉的百分比（越小越好）：Codex 取 5 小时和本周里大的那个；Gemini 取各模型剩余比例最小的那个。不知道 = None。"""
    q = (a or {}).get('quota') or {}
    if a.get('platform') == 'gemini':
        r = q.get('remaining')
        return None if r is None else round((1 - float(r)) * 100, 1)
    vals = [w.get('used_percentage') for w in (q.get('five_hour'), q.get('seven_day')) if isinstance(w, dict) and w.get('used_percentage') is not None]
    return max(float(v) for v in vals) if vals else None


def usable(a):
    return bool(a) and not a.get('disabled') and a.get('status') == 'ok'


def pick(accounts, want=None, default_id=None):
    """开新会话时选号：返回 (号 或 None, 原因)。
    指定了号（want）且能用就用它；否则自动：状态正常、没禁用的号里额度用得最少的（额度未知按 0），一样多时默认号优先，再按加号先后。
    用满（≥100%）的号排在最后。池里这个平台没有能用的号返回 None（studio 按以前的单号行为开会话）。"""
    accs = list(accounts or [])
    if want:
        a = next((x for x in accs if x.get('id') == want), None)
        if usable(a):
            return a, '指定'
        why = '不存在' if not a else '已禁用' if a.get('disabled') else STATUS_CN.get(a.get('status'), a.get('status') or '不可用')
        cand, _ = pick(accs, None, default_id)
        return cand, f'指定的 {want} {why}，改为自动选'
    cand = [a for a in accs if usable(a)]
    if not cand:
        return None, '没有能用的号'

    def key(a):
        u = used_pct(a)
        u = 0.0 if u is None else u
        return (u >= 100, u, a.get('id') != default_id, a.get('created') or 0)
    best = min(cand, key=key)
    return best, '自动（额度剩得最多）' if len(cand) > 1 else '自动（唯一能用的号）'


# ---------- Codex ----------

def jwt_claims(token):
    try:
        p = str(token).split('.')[1]
        p += '=' * (-len(p) % 4)
        v = json.loads(base64.urlsafe_b64decode(p))
        return v if isinstance(v, dict) else {}
    except (IndexError, ValueError, TypeError):
        return {}


def _iso_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError):
        return None


def codex_auth_info(auth_path):
    """auth.json → dict(logged_in, email, plan, account_id, last_refresh, access_exp)；不含令牌。"""
    d = read_json(auth_path)
    if not isinstance(d, dict):
        return dict(logged_in=False)
    t = d.get('tokens') if isinstance(d.get('tokens'), dict) else {}
    if not t.get('refresh_token') and not d.get('OPENAI_API_KEY'):
        return dict(logged_in=False)
    c = jwt_claims(t.get('id_token'))
    auth = c.get('https://api.openai.com/auth') or {}
    return dict(logged_in=True, mode=d.get('auth_mode') or '', email=c.get('email') or '', plan=auth.get('chatgpt_plan_type') or '',
                account_id=t.get('account_id') or auth.get('chatgpt_account_id') or '', last_refresh=_iso_ts(d.get('last_refresh')),
                access_exp=jwt_claims(t.get('access_token')).get('exp'))


def codex_refresh(home):
    """用 RT 给这个号续期（只给麻薯自己持有的号用：OpenAI 的 RT 每次续期都换新的，本机默认号让 Codex 自己续）。
    在 <home>/auth.lock 上加锁，写回 auth.json（原子）。invalid_grant 之类抛 Revoked。返回新的 codex_auth_info。"""
    home = Path(home)
    ap = home / 'auth.json'
    if ap.is_symlink():
        raise AccountError('这是本机默认号（软链接到 ~/.codex/auth.json），由 Codex 自己续期，麻薯不碰')
    with file_lock(home / 'auth.lock'):
        d = read_json(ap)
        t = (d or {}).get('tokens') if isinstance(d, dict) else None
        if not t or not t.get('refresh_token'):
            raise AccountError('这个号还没登录（auth.json 里没有 refresh_token）')
        cid = ((t.get('oauth_provenance') or {}).get('client_id')) or CODEX_CLIENT_ID
        code, body = http('POST', CODEX_TOKEN_URL, dict(client_id=cid, grant_type='refresh_token', refresh_token=t['refresh_token'],
                                                        scope='openid profile email'),
                          headers={'Content-Type': 'application/json'})
        if code != 200 or not isinstance(body, dict) or not body.get('access_token'):
            err = _err_code(body) or f'HTTP {code}'
            msg = redact(f'Codex 续期失败：{err}', [t.get('refresh_token')])
            if code in (400, 401) and re.search(r'invalid_grant|expired|reused|revoked|invalidated', json.dumps(body) if isinstance(body, dict) else str(body)):
                raise Revoked(msg)
            raise AccountError(msg)
        for k in ('id_token', 'access_token', 'refresh_token'):
            if body.get(k):
                t[k] = body[k]
        d['tokens'] = t
        d['last_refresh'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        write_json(ap, d)
    return codex_auth_info(ap)


DEVICE_CODE_RE = re.compile(r'\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b')
ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


def parse_login_output(text):
    """codex login 的输出 → dict(url, code)：设备码登录是 https://auth.openai.com/codex/device + 一次性码；浏览器登录是 authorize 链接。"""
    text = ANSI_RE.sub('', text or '')
    urls = re.findall(r'https://[^\s\'"<>]+', text)
    url = next((u for u in urls if 'authorize' in u), None) or next((u for u in urls if 'device' in u), None) or (urls[0] if urls else '')
    m = DEVICE_CODE_RE.search(text.split('one-time code', 1)[-1]) if 'one-time code' in text else None
    return dict(url=url, code=m.group(0) if m else '')


# ---------- Gemini（agy）：OAuth 客户端、续期、额度 ----------

def agy_binary(exe):
    """agy 可执行文件 → 真正的二进制（Homebrew 装的是一层 shell 包装：exec "<路径>"）。"""
    p = Path(exe)
    if not p.is_absolute():
        import shutil
        w = shutil.which(str(exe))
        p = Path(w) if w else p
    try:
        p = p.resolve()
        head = p.read_bytes()[:4096] if p.stat().st_size < 1_000_000 else b''
    except OSError:
        return None
    m = re.search(rb'exec\s+"([^"]+)"', head)
    if m and Path(m.group(1).decode()).exists():
        return Path(m.group(1).decode())
    return p if p.exists() else None


def scan_clients(binary):
    """从 agy 二进制里认出 Google OAuth 桌面客户端（client_id 和 client_secret 各几组，不知道怎么配对，全列出来）。"""
    try:
        data = Path(binary).read_bytes()
    except OSError:
        return [], []
    ids = sorted({m.decode() for m in re.findall(rb'[0-9]{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com', data)})
    secs = sorted({m.decode() for m in re.findall(rb'GOCSPX-[A-Za-z0-9_-]{28}', data)})
    return ids, secs


class Gemini:
    """Gemini 号的操作：池 + agy 的可执行文件路径。"""

    def __init__(self, pool, agy_exe='agy', keychain=None, token_file=None):
        self.pool = pool
        self.agy_exe = agy_exe
        self.kc = keychain or Keychain()
        self.token_file = Path(token_file) if token_file else AGY_TOKEN_FILE

    # --- OAuth 客户端 ---
    def clients(self):
        """候选客户端 [(id, secret)]：上次能用的排第一。从 agy 二进制里扫（缓存在主文件，二进制变了重扫）。"""
        b = agy_binary(self.agy_exe)
        sig = f'{b}:{b.stat().st_mtime if b else 0}' if b else ''
        cache = self.pool.gemini_clients()
        if not b and cache.get('pairs'):
            pairs = cache['pairs']
        elif cache.get('sig') == sig and cache.get('pairs'):
            pairs = cache['pairs']
        else:
            ids, secs = scan_clients(b) if b else ([], [])
            pairs = [[i, s] for i in ids for s in secs]
            self.pool.set_gemini_clients(dict(sig=sig, pairs=pairs, good=cache.get('good')))
            cache['pairs'] = pairs
        good = cache.get('good')
        pairs = sorted(pairs, key=lambda p: p != good)
        if not pairs:
            raise AccountError(f'没在 agy（{self.agy_exe}）里找到 OAuth 客户端：装了 Antigravity CLI 吗？')
        return [tuple(p) for p in pairs]

    def _remember_client(self, pair):
        cache = self.pool.gemini_clients()
        if cache.get('good') != list(pair):
            cache['good'] = list(pair)
            self.pool.set_gemini_clients(cache)

    def refresh_token(self, rt):
        """RT 换 access_token：依次试候选客户端（invalid_client / unauthorized_client 换下一个）。
        返回 dict(access_token, expiry, refresh_token, client_id, id_token)；invalid_grant 抛 Revoked。"""
        last = ''
        for cid, sec in self.clients():
            code, body = http('POST', GOOGLE_TOKEN_URL, dict(client_id=cid, client_secret=sec, refresh_token=rt, grant_type='refresh_token'))
            if code == 200 and isinstance(body, dict) and body.get('access_token'):
                self._remember_client([cid, sec])
                return dict(access_token=body['access_token'], expiry=time.time() + int(body.get('expires_in') or 3600),
                            refresh_token=body.get('refresh_token') or rt, client_id=cid, id_token=body.get('id_token') or '',
                            scope=body.get('scope') or '')
            err = _err_code(body)
            if err == 'invalid_grant':
                raise Revoked('Google 说这个 RT 已失效（invalid_grant）：被撤销、过期，或不是 Antigravity 签发的，请重新登录')
            last = err or f'HTTP {code}'
        raise AccountError(f'agy 的 OAuth 客户端都不认这个 RT（{redact(last, [rt])}）：RT 必须是 Antigravity 签发的')

    def userinfo(self, at):
        code, body = http('GET', GOOGLE_USERINFO_URL, headers={'Authorization': f'Bearer {at}'})
        return body if code == 200 and isinstance(body, dict) else {}

    def quota(self, at):
        """Code Assist 的 fetchAvailableModels：每个模型的剩余比例和重置时间。
        返回 dict(remaining=最小剩余比例, reset=对应的重置时间戳, models=[{id, name, remaining, reset}], at)。"""
        code, body = http('POST', CODE_ASSIST_MODELS_URL, {}, headers={'Authorization': f'Bearer {at}', 'Content-Type': 'application/json',
                                                                       'User-Agent': 'antigravity'})
        if code != 200 or not isinstance(body, dict):
            raise AccountError(f'查 Gemini 额度失败：HTTP {code} {redact(_err_code(body))}')
        return parse_quota(body)

    # --- 凭据（统一存在主文件 studio.json 里）---
    def cred(self, aid):
        return self.pool.get_credentials(aid)

    def ensure_token(self, aid, margin=300):
        """这个号的 access_token：离过期不到 margin 秒就用 RT 续期（加锁，原子写回主文件）；Google 回了新 RT 就换上。
        invalid_grant → 状态「已撤销」并抛 Revoked；其它失败 → 「续期失败」。返回 access_token。"""
        with file_lock(self.pool.root / 'gemini' / f'.{aid}.lock'):
            c = self.cred(aid)
            if not c.get('refresh_token'):
                self.pool.update(aid, status='logged_out', error='没有 RT')
                raise AccountError(f'{aid} 没有 RT')
            if c.get('access_token') and (c.get('expiry') or 0) - time.time() > margin:
                return c['access_token']
            try:
                t = self.refresh_token(c['refresh_token'])
            except Revoked as exc:
                self.pool.update(aid, status='revoked', error=str(exc), last_check=time.time())
                raise
            except AccountError as exc:
                self.pool.update(aid, status='refresh_failed', error=str(exc), last_check=time.time())
                raise
            c.update(access_token=t['access_token'], expiry=t['expiry'], refresh_token=t['refresh_token'], client_id=t['client_id'])
            self.pool.set_credentials(aid, c)
            self.pool.update(aid, status='ok', error='', last_refresh=time.time(), last_check=time.time())
            return c['access_token']

    def check(self, aid):
        """体检：续期（需要时）+ 查额度，写回元数据。返回更新后的号。"""
        at = self.ensure_token(aid)
        try:
            q = self.quota(at)
        except AccountError as exc:
            return self.pool.update(aid, last_check=time.time(), error=str(exc))
        return self.pool.update(aid, quota=q, last_check=time.time(), error='')

    def import_rt(self, rt, source='rt', extra=None):
        """导入一个 RT：先换一次 access_token 校验，再取邮箱、查额度。同一个邮箱已经在池里就更新它的 RT。返回号。"""
        rt = (rt or '').strip()
        if not rt or len(rt) < 20 or any(ch.isspace() for ch in rt):
            raise AccountError('RT 格式不对（应是一整串，没有空格换行）')
        t = self.refresh_token(rt)
        info = self.userinfo(t['access_token'])
        email = info.get('email') or jwt_claims(t.get('id_token')).get('email') or (extra or {}).get('email') or ''
        same = next((a for a in self.pool.list('gemini') if email and a.get('email') == email), None)
        a = same or self.pool.add('gemini', source=source, email=email, label=email or 'Gemini 号', status='pending')
        cred = dict(refresh_token=t['refresh_token'], access_token=t['access_token'], expiry=t['expiry'], client_id=t['client_id'],
                    email=email, added=time.time())
        self.pool.set_credentials(a['id'], cred)
        upd = dict(status='ok', error='', email=email, label=email or a.get('label'), last_refresh=time.time(), last_check=time.time())
        if same and source == 'local':
            upd['source'] = 'local'
        try:
            upd['quota'] = self.quota(t['access_token'])
        except AccountError as exc:
            upd['error'] = str(exc)
        return self.pool.update(a['id'], **upd)

    # --- 钥匙串（agy 全机只认这一个位置）---
    def import_local(self):
        """把本机 agy 当前登录（钥匙串 gemini / antigravity）登记成默认号：复制 RT（Google 的 RT 续期不换新，两边都能用）。"""
        v = self.kc.read()
        tok = (v or {}).get('token') or {}
        if not tok.get('refresh_token'):
            raise AccountError('钥匙串里没有 agy 的登录（服务 gemini / 账户 antigravity）：先在终端运行一次 agy 登录')
        a = self.import_rt(tok['refresh_token'], source='local', extra=dict(email=v.get('id_token_email')))
        if not self.pool._read()['defaults'].get('gemini'):
            self.pool.set_default('gemini', a['id'])
        return a

    def active_id(self):
        """钥匙串里现在是池里哪个号（按 RT 的哈希比）；不是池里的号或读不到是 None。"""
        v = self.kc.read()
        h = rt_hash(((v or {}).get('token') or {}).get('refresh_token'))
        if not h:
            return None
        for a in self.pool.list('gemini'):
            if rt_hash(self.cred(a['id']).get('refresh_token')) == h:
                return a['id']
        return None

    def activate(self, aid):
        """全局切号：把这个号写进钥匙串（agy 全机共用；调用方先确认没有运行中的 agy）。备用文件存在就同步改。返回号。"""
        a = self.pool.get(aid)
        if not a or a.get('platform') != 'gemini':
            raise AccountError(f'没有这个 Gemini 号：{aid}')
        at = self.ensure_token(aid)
        c = self.cred(aid)
        with file_lock(self.pool.root / 'gemini' / '.keychain.lock'):
            cur = self.kc.read() or {}
            exp = datetime.fromtimestamp(c.get('expiry') or time.time(), timezone.utc).astimezone()
            tok = dict(access_token=at, token_type='Bearer', refresh_token=c['refresh_token'],
                       expiry=exp.isoformat(timespec='microseconds'), expires_in=max(0, int((c.get('expiry') or 0) - time.time())))
            val = dict(cur, token=tok, auth_method=cur.get('auth_method') or 'consumer')
            if c.get('email') or a.get('email'):
                val['id_token_email'] = c.get('email') or a.get('email')
            self.kc.write(val)
            if self.token_file.exists():         # 备用文件：只在已经有的时候按原格式改 token 部分
                old = read_json(self.token_file)
                if isinstance(old, dict):
                    new = dict(old, **({'token': tok} if 'token' in old else tok))
                    write_json(self.token_file, new)
        return a

    # --- 网页授权（本地回调）---
    def login_url(self, port, verifier, state):
        cid, _ = self.clients()[0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        q = dict(client_id=cid, redirect_uri=f'http://127.0.0.1:{port}/oauth-callback', response_type='code', scope=GOOGLE_SCOPES,
                 access_type='offline', prompt='consent', code_challenge=challenge, code_challenge_method='S256', state=state)
        return f'{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(q)}'

    def exchange_code(self, code, port, verifier):
        cid, sec = self.clients()[0]
        st, body = http('POST', GOOGLE_TOKEN_URL, dict(client_id=cid, client_secret=sec, code=code, code_verifier=verifier,
                                                       grant_type='authorization_code',
                                                       redirect_uri=f'http://127.0.0.1:{port}/oauth-callback'))
        if st != 200 or not isinstance(body, dict) or not body.get('refresh_token'):
            raise AccountError(f'用授权码换 RT 失败：{redact(_err_code(body)) or f"HTTP {st}"}')
        return body['refresh_token']

    def oauth_login(self, on_url, timeout=600):
        """网页授权加号：起 127.0.0.1 上的临时回调，on_url(链接) 交给调用方展示 / 打开，等用户在浏览器授权，换 RT 后导入。"""
        from http.server import BaseHTTPRequestHandler, HTTPServer
        verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(16)
        got = {}

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get('state', [''])[0] == state and (q.get('code') or q.get('error')):
                    got.update(code=q.get('code', [''])[0], error=q.get('error', [''])[0])
                ok = bool(got.get('code'))
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(('<meta charset="utf-8"><h3>' + ('授权完成，可以关掉这个页面回到麻薯' if ok else '授权没有完成：' + (got.get('error') or '缺少授权码'))
                                  + '</h3>').encode())

            def log_message(self, *a):
                pass
        srv = HTTPServer(('127.0.0.1', 0), H)
        srv.timeout = 2
        port = srv.server_address[1]
        try:
            on_url(self.login_url(port, verifier, state))
            end = time.time() + timeout
            while not got and time.time() < end:
                srv.handle_request()
        finally:
            srv.server_close()
        if not got.get('code'):
            raise AccountError('网页授权没完成：' + (got.get('error') or '超时'))
        return self.import_rt(self.exchange_code(got['code'], port, verifier), source='login')


def parse_quota(body):
    models = []
    for mid, m in ((body or {}).get('models') or {}).items():
        if not isinstance(m, dict):
            continue
        q = m.get('quotaInfo') or {}
        if q.get('remainingFraction') is None or not m.get('displayName'):
            continue
        models.append(dict(id=mid, name=m.get('displayName'), remaining=float(q['remainingFraction']), reset=_iso_ts(q.get('resetTime'))))
    models.sort(key=lambda x: (x['remaining'], x['reset'] or 0))
    low = models[0] if models else None
    return dict(remaining=low['remaining'] if low else None, reset=low['reset'] if low else None, low_model=low['name'] if low else '',
                models=models, at=time.time())


class Keychain:
    """macOS 钥匙串里 agy 的凭据（服务 gemini、账户 antigravity，值是 go-keyring-base64: + base64 JSON）。
    写入走 security -i 的标准输入，令牌不进命令行参数（ps 看不到）。"""

    def __init__(self, service=KEYCHAIN_SERVICE, account=KEYCHAIN_ACCOUNT):
        self.service, self.account = service, account

    def read(self):
        try:
            r = subprocess.run(['security', 'find-generic-password', '-s', self.service, '-a', self.account, '-w'],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return decode_keyring(r.stdout.strip()) if r.returncode == 0 else None

    def write(self, value):
        raw = encode_keyring(value)
        cmd = f'add-generic-password -U -s {self.service} -a {self.account} -w {raw}\n'
        try:
            r = subprocess.run(['security', '-i'], input=cmd, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AccountError(f'写钥匙串失败：{exc}')
        if r.returncode != 0 or 'error' in (r.stderr or '').lower():
            raise AccountError(f'写钥匙串失败：{redact(r.stderr.strip())[:200]}')
        back = self.read()
        if not back or (back.get('token') or {}).get('refresh_token') != ((value or {}).get('token') or {}).get('refresh_token'):
            raise AccountError('写钥匙串后读回来对不上')


def decode_keyring(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        if raw.startswith(KEYRING_PREFIX):
            raw = base64.b64decode(raw[len(KEYRING_PREFIX):]).decode()
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def encode_keyring(value):
    return KEYRING_PREFIX + base64.b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()


def agy_running():
    """有没有运行中的 agy（Antigravity CLI）进程：麻薯的、用户手开的都算。有就不切全局账号。"""
    try:
        r = subprocess.run(['pgrep', '-f', '(^|/)(antigravity|agy)( |$)'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    mine = {str(os.getpid()), str(os.getppid())}
    return any(x.strip() and x.strip() not in mine for x in r.stdout.splitlines())
