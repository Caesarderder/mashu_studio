"""主配置文件管理（#7）：state/studio.json 统一存储、加锁原子写、迁移、加密导出与差异预览导入。

只依赖 Python 标准库，供 accounts.py / studio.py / server.py 共用。
"""
from contextlib import contextmanager
import copy
import datetime
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import zlib
import base64

SCHEMA_VERSION = 1
BUNDLE_FORMAT = 'mashu-config-bundle'
OMITTED = '已省略'
KDF_ITERATIONS = 200_000
DEFAULT_KEYS = ('providers', 'models', 'gemini_models', 'codex_models', 'opencode_models', 'efforts', 'code_modes')
SECTION_KEYS = ('schema_version', 'settings', 'defaults', 'accounts', 'providers', 'web')
SAFE_PROVIDER_META = {'source', 'note', 'base_url', 'url', 'model', 'models', 'name', 'type', 'enabled'}
RT_ROTATION_WARNING = (
    'Codex / Gemini 的 refresh_token 会轮换，导入新电脑后旧电脑上这些号要停用，否则两边会互相顶掉登录；'
    '导入的账号默认标为「已禁用」，请在新电脑确认后再启用。'
)
_SECRET_KEY_RE = re.compile(r'(?:^|_)(?:api_key|secret|password|passphrase|token|refresh_token|access_token)$', re.I)

_LOCK_STATE = threading.local()
_PROC_LOCKS_GUARD = threading.Lock()
_PROC_LOCKS = {}


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def mask(s):
    s = str(s or '')
    return f'{s[:4]}…' if s else ''


def write_private(path, text):
    """原子写 0600 文件：同目录临时文件写完 fsync 再 rename，目录不存在就建（0700）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_json(path, data):
    return write_private(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _thread_lock_for(key):
    with _PROC_LOCKS_GUARD:
        lk = _PROC_LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _PROC_LOCKS[key] = lk
        return lk


@contextmanager
def file_lock(path):
    """线程 + 进程间互斥（支持同线程重入，避免同一线程重复 flock 死锁）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = str(path.resolve())
    depths = getattr(_LOCK_STATE, 'depths', None)
    if depths is None:
        depths = {}
        _LOCK_STATE.depths = depths
    if depths.get(key, 0) > 0:
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return
    tlock = _thread_lock_for(key)
    tlock.acquire()
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        depths[key] = 1
        try:
            yield
        finally:
            depths[key] = 0
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
        tlock.release()


def config_lock(config_path):
    p = Path(config_path)
    return file_lock(p.with_name(f'.{p.name}.lock'))


def is_sectioned(raw):
    return isinstance(raw, dict) and any(k in raw for k in ('schema_version', 'settings', 'defaults', 'accounts', 'web'))


def normalize_config(raw):
    """把扁平旧配置或部分分节配置规范成统一的分节主配置。"""
    raw = raw if isinstance(raw, dict) else {}
    sectioned = is_sectioned(raw)
    settings = copy.deepcopy(raw.get('settings')) if isinstance(raw.get('settings'), dict) else {}
    defaults_raw = raw.get('defaults') if isinstance(raw.get('defaults'), dict) else {}
    defaults = {}
    for k in DEFAULT_KEYS:
        v = defaults_raw.get(k)
        if not isinstance(v, dict) and not sectioned and isinstance(raw.get(k), dict):
            v = raw.get(k)
        elif not isinstance(v, dict) and k != 'providers' and isinstance(raw.get(k), dict):
            v = raw.get(k)
        defaults[k] = copy.deepcopy(v) if isinstance(v, dict) else {}

    for k, v in raw.items():
        if k in SECTION_KEYS or k in DEFAULT_KEYS:
            continue
        settings.setdefault(k, copy.deepcopy(v))

    acc_raw = raw.get('accounts') if isinstance(raw.get('accounts'), dict) else {}
    acc_defaults = copy.deepcopy(acc_raw.get('defaults')) if isinstance(acc_raw.get('defaults'), dict) else {}
    raw_items = acc_raw.get('items') if isinstance(acc_raw.get('items'), list) else acc_raw.get('accounts')
    items = [copy.deepcopy(x) for x in (raw_items or []) if isinstance(x, dict) and x.get('id')]
    gclients = copy.deepcopy(acc_raw.get('gemini_clients')) if isinstance(acc_raw.get('gemini_clients'), dict) else {}

    providers = copy.deepcopy(raw.get('providers')) if (sectioned and isinstance(raw.get('providers'), dict)) else {}
    web = copy.deepcopy(raw.get('web')) if isinstance(raw.get('web'), dict) else {}
    web.setdefault('token', '')

    return {
        'schema_version': SCHEMA_VERSION,
        'settings': settings,
        'defaults': defaults,
        'accounts': {
            'defaults': acc_defaults,
            'items': items,
            'gemini_clients': gclients,
        },
        'providers': providers,
        'web': web,
    }


def get_setting(data, key, defaults_dict):
    """从主配置（分节或兼容扁平格式）读取配置项，缺失则回落 DEFAULTS。"""
    if not isinstance(data, dict):
        return defaults_dict[key]
    if is_sectioned(data):
        if key in DEFAULT_KEYS:
            d = data.get('defaults')
            if isinstance(d, dict) and key in d:
                return d[key]
        else:
            s = data.get('settings')
            if isinstance(s, dict) and key in s:
                return s[key]
        if key in data and key not in SECTION_KEYS:
            return data[key]
        return defaults_dict[key]
    return data.get(key, defaults_dict[key])


def read_config(config_path):
    return normalize_config(read_json(config_path, {}))


def write_config(config_path, data):
    config_path = Path(config_path)
    norm = normalize_config(data)
    with config_lock(config_path):
        write_json(config_path, norm)
    return norm


def update_config(config_path, fn):
    """在文件锁内读-改-原子写（0600），返回新配置。"""
    config_path = Path(config_path)
    with config_lock(config_path):
        cur = read_config(config_path)
        res = fn(cur)
        norm = normalize_config(res if isinstance(res, dict) else cur)
        write_json(config_path, norm)
        return norm


def ensure_web_token(config_path):
    """读取主文件里的 web.token；没有就生成并原子写回 0600。"""
    config_path = Path(config_path)
    with config_lock(config_path):
        cur = read_config(config_path)
        tok = str((cur.get('web') or {}).get('token') or '').strip()
        if tok:
            if config_path.exists() and (config_path.stat().st_mode & 0o777) != 0o600:
                os.chmod(config_path, 0o600)
            return tok
        tok = secrets.token_urlsafe(24)
        cur.setdefault('web', {})['token'] = tok
        write_json(config_path, cur)
        return tok


def _backup_file(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(dst, Path(src).read_text(encoding='utf-8'))


def _ensure_provider_refs(cfg):
    prov = cfg.setdefault('providers', {})
    for k, note in (
        ('claude', '本机 Claude Code 钥匙串'),
        ('gemini', '本机 agy 钥匙串'),
        ('codex', '本机 ~/.codex/auth.json'),
        ('opencode', '本机 ~/.local/share/opencode/auth.json'),
    ):
        if k not in prov:
            prov[k] = {'source': 'local', 'note': note}


def migrate_if_needed(home):
    """首次迁移：把旧版 studio.json / accounts/index.json / gemini cred.json / clients.json / token 合并进主文件，
    旧文件备份到 state/backup/ 后删除（不再读）。幂等可重跑。"""
    home = Path(home)
    conf = home / 'studio.json'
    tok_file = home / 'token'
    acc_dir = home / 'accounts'
    idx_file = acc_dir / 'index.json'
    clients_file = acc_dir / 'gemini' / 'clients.json'
    gemini_dir = acc_dir / 'gemini'
    cred_files = sorted(gemini_dir.glob('*/cred.json')) if gemini_dir.is_dir() else []

    raw_fast = read_json(conf, None) if conf.exists() else None
    needs = (
        tok_file.exists()
        or idx_file.exists()
        or clients_file.exists()
        or bool(cred_files)
        or (conf.exists() and not is_sectioned(raw_fast))
    )
    if not needs:
        if conf.exists() and (conf.stat().st_mode & 0o777) != 0o600:
            try:
                os.chmod(conf, 0o600)
            except OSError:
                pass
        return {'migrated': False, 'files': []}

    with config_lock(conf):
        raw = read_json(conf, {}) if conf.exists() else {}
        cred_files = sorted(gemini_dir.glob('*/cred.json')) if gemini_dir.is_dir() else []
        needs_locked = (
            tok_file.exists()
            or idx_file.exists()
            or clients_file.exists()
            or bool(cred_files)
            or (conf.exists() and not is_sectioned(raw))
        )
        if not needs_locked:
            return {'migrated': False, 'files': []}

        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + f'-{secrets.token_hex(2)}'
        bdir = home / 'backup' / f'one-config-{stamp}'
        migrated_files = []

        if conf.exists() and not is_sectioned(raw):
            _backup_file(conf, bdir / 'studio.json')
            migrated_files.append('studio.json')

        cfg = normalize_config(raw)
        _ensure_provider_refs(cfg)

        if tok_file.exists():
            _backup_file(tok_file, bdir / 'token')
            old_tok = tok_file.read_text(encoding='utf-8').strip()
            if old_tok and not cfg['web'].get('token'):
                cfg['web']['token'] = old_tok
            tok_file.unlink()
            migrated_files.append('token')

        items_by_id = {a['id']: a for a in cfg['accounts']['items']}
        if idx_file.exists():
            _backup_file(idx_file, bdir / 'accounts' / 'index.json')
            old_idx = read_json(idx_file, {}) or {}
            for pl, aid in (old_idx.get('defaults') or {}).items():
                if aid and pl not in cfg['accounts']['defaults']:
                    cfg['accounts']['defaults'][pl] = aid
            for old_a in old_idx.get('accounts') or []:
                if not isinstance(old_a, dict) or not old_a.get('id'):
                    continue
                aid = old_a['id']
                a = items_by_id.get(aid)
                if a is None:
                    a = copy.deepcopy(old_a)
                    cfg['accounts']['items'].append(a)
                    items_by_id[aid] = a
                if a.get('platform') == 'gemini':
                    cp = gemini_dir / aid / 'cred.json'
                    if cp.exists():
                        _backup_file(cp, bdir / 'accounts' / 'gemini' / aid / 'cred.json')
                        c = read_json(cp, {}) or {}
                        if c and not a.get('credentials'):
                            a['credentials'] = c
                        cp.unlink()
                        migrated_files.append(f'accounts/gemini/{aid}/cred.json')
                elif a.get('platform') == 'codex':
                    if a.get('source') == 'local':
                        a.setdefault('auth_ref', 'local_cli')
                    else:
                        ap = acc_dir / 'codex' / aid / 'auth.json'
                        if ap.exists() and not ap.is_symlink():
                            _backup_file(ap, bdir / 'accounts' / 'codex' / aid / 'auth.json')
                            ad = read_json(ap, {}) or {}
                            if ad and not a.get('credentials'):
                                a['credentials'] = ad
            idx_file.unlink()
            migrated_files.append('accounts/index.json')

        for cp in sorted(gemini_dir.glob('*/cred.json')) if gemini_dir.is_dir() else []:
            aid = cp.parent.name
            _backup_file(cp, bdir / 'accounts' / 'gemini' / aid / 'cred.json')
            c = read_json(cp, {}) or {}
            a = items_by_id.get(aid)
            if a is not None and c and not a.get('credentials'):
                a['credentials'] = c
            cp.unlink()
            migrated_files.append(f'accounts/gemini/{aid}/cred.json')

        if clients_file.exists():
            _backup_file(clients_file, bdir / 'accounts' / 'gemini' / 'clients.json')
            gc = read_json(clients_file, {}) or {}
            if gc and not (cfg['accounts'].get('gemini_clients') or {}).get('pairs'):
                cfg['accounts']['gemini_clients'] = gc
            clients_file.unlink()
            migrated_files.append('accounts/gemini/clients.json')

        write_json(conf, cfg)
        return {'migrated': True, 'backup_dir': str(bdir), 'files': migrated_files}


# ---------- 加密 / 解密（纯 Python 标准库：PBKDF2-HMAC-SHA256 + SHA256-CTR + HMAC-SHA256）----------

def _derive_keys(passphrase, salt, iterations):
    if not passphrase:
        raise ValueError('带密钥导出 / 导入需要输入口令')
    dk = hashlib.pbkdf2_hmac('sha256', passphrase.encode('utf-8'), salt, iterations, dklen=64)
    return dk[:32], dk[32:]


def _xor_stream(enc_key, nonce, data):
    out = bytearray(len(data))
    pos = 0
    ctr = 0
    while pos < len(data):
        block = hashlib.sha256(enc_key + nonce + ctr.to_bytes(8, 'big')).digest()
        chunk = data[pos:pos + 32]
        for i, b in enumerate(chunk):
            out[pos + i] = b ^ block[i]
        pos += len(chunk)
        ctr += 1
    return bytes(out)


def encrypt_payload(payload, passphrase):
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    enc_key, mac_key = _derive_keys(passphrase, salt, KDF_ITERATIONS)
    plain = zlib.compress(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), level=6)
    ct = _xor_stream(enc_key, nonce, plain)
    tag = hmac.new(mac_key, b'mashu-cfg-v1' + salt + nonce + ct, 'sha256').digest()
    return {
        'format': BUNDLE_FORMAT,
        'version': 1,
        'encrypted': True,
        'kdf': 'pbkdf2-hmac-sha256',
        'iterations': KDF_ITERATIONS,
        'cipher': 'sha256-ctr+hmac-sha256',
        'salt': base64.b64encode(salt).decode('ascii'),
        'nonce': base64.b64encode(nonce).decode('ascii'),
        'tag': base64.b64encode(tag).decode('ascii'),
        'ciphertext': base64.b64encode(ct).decode('ascii'),
    }


def decrypt_bundle(bundle, passphrase):
    if not isinstance(bundle, dict) or not bundle.get('encrypted'):
        raise ValueError('不是加密的配置包')
    if bundle.get('cipher') != 'sha256-ctr+hmac-sha256' or bundle.get('kdf') != 'pbkdf2-hmac-sha256':
        raise ValueError('不支持的加密格式')
    try:
        salt = base64.b64decode(bundle['salt'])
        nonce = base64.b64decode(bundle['nonce'])
        tag = base64.b64decode(bundle['tag'])
        ct = base64.b64decode(bundle['ciphertext'])
        iterations = int(bundle.get('iterations') or KDF_ITERATIONS)
    except (KeyError, ValueError, TypeError):
        raise ValueError('配置文件损坏（缺少加密字段）')
    enc_key, mac_key = _derive_keys(passphrase, salt, iterations)
    expected = hmac.new(mac_key, b'mashu-cfg-v1' + salt + nonce + ct, 'sha256').digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError('口令不对或文件已损坏')
    try:
        plain = _xor_stream(enc_key, nonce, ct)
        data = json.loads(zlib.decompress(plain).decode('utf-8'))
    except (zlib.error, UnicodeDecodeError, ValueError):
        raise ValueError('口令不对或文件已损坏')
    if not isinstance(data, dict):
        raise ValueError('配置内容格式不对')
    return data


# ---------- 本机 CLI 自己的凭据（不搬家，只在带密钥导出 / 导入时读写）----------

def _read_keychain(service, account=None):
    if os.environ.get('MASHU_NO_KEYCHAIN') == '1':
        return None
    cmd = ['security', 'find-generic-password', '-s', service]
    if account:
        cmd += ['-a', account]
    cmd.append('-w')
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _write_keychain(service, account, raw_value):
    if os.environ.get('MASHU_NO_KEYCHAIN') == '1' or not raw_value:
        return False
    acct_part = f' -a {account}' if account else ''
    cmd = f'add-generic-password -U -s {service}{acct_part} -w {raw_value}\n'
    try:
        r = subprocess.run(['security', '-i'], input=cmd, capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _default_cli_paths():
    codex_user = Path(os.environ.get('MASHU_CODEX_USER_HOME') or Path.home() / '.codex')
    oc_auth = Path(os.environ.get('MASHU_OPENCODE_AUTH_FILE') or Path.home() / '.local' / 'share' / 'opencode' / 'auth.json')
    agy_token = Path(os.environ.get('MASHU_AGY_TOKEN_FILE') or Path.home() / '.gemini' / 'jetski-standalone-oauth-token')
    kc_file = os.environ.get('MASHU_KEYCHAIN_FILE')
    return codex_user, oc_auth, agy_token, Path(kc_file) if kc_file else None


def read_cli_secrets(cli_io=None):
    """读取本机各 CLI 自己存的登录（只用于带密钥导出）。"""
    if cli_io and hasattr(cli_io, 'read'):
        return cli_io.read() or {}
    codex_user, oc_auth, agy_token, kc_file = _default_cli_paths()
    out = {}
    ca = read_json(codex_user / 'auth.json')
    if isinstance(ca, dict) and ca:
        out['codex_local_auth'] = ca
    oa = read_json(oc_auth)
    if isinstance(oa, dict) and oa:
        out['opencode_auth'] = oa
    if kc_file:
        kc = read_json(kc_file, {}) or {}
        if kc.get('gemini'):
            out['gemini_keychain'] = kc['gemini']
        if kc.get('claude'):
            out['claude_keychain'] = kc['claude']
    else:
        g_raw = _read_keychain('gemini', 'antigravity')
        if g_raw:
            out['gemini_keychain'] = g_raw
        c_raw = _read_keychain('Claude Code-credentials')
        if c_raw:
            out['claude_keychain'] = c_raw
    at_raw = read_json(agy_token)
    if isinstance(at_raw, dict) and at_raw:
        out['agy_token_file'] = at_raw
    return out


def write_cli_secrets(cli_secrets, backup_dir=None, cli_io=None):
    """把导入包里的本机 CLI 登录写回对应位置（写前备份已有文件）。"""
    if not isinstance(cli_secrets, dict) or not cli_secrets:
        return []
    if cli_io and hasattr(cli_io, 'write'):
        return cli_io.write(cli_secrets, backup_dir=backup_dir) or []
    codex_user, oc_auth, agy_token, kc_file = _default_cli_paths()
    written = []
    bdir = Path(backup_dir) if backup_dir else None

    if isinstance(cli_secrets.get('codex_local_auth'), dict):
        p = codex_user / 'auth.json'
        if p.exists() and bdir:
            _backup_file(p, bdir / 'cli' / 'codex-auth.json')
        write_json(p, cli_secrets['codex_local_auth'])
        written.append('codex_local_auth')

    if isinstance(cli_secrets.get('opencode_auth'), dict):
        if oc_auth.exists() and bdir:
            _backup_file(oc_auth, bdir / 'cli' / 'opencode-auth.json')
        write_json(oc_auth, cli_secrets['opencode_auth'])
        written.append('opencode_auth')

    if isinstance(cli_secrets.get('agy_token_file'), dict):
        if agy_token.exists() and bdir:
            _backup_file(agy_token, bdir / 'cli' / 'agy-token.json')
        write_json(agy_token, cli_secrets['agy_token_file'])
        written.append('agy_token_file')

    if kc_file:
        kc = read_json(kc_file, {}) or {}
        changed = False
        for k in ('gemini_keychain', 'claude_keychain'):
            short = k.split('_')[0]
            if cli_secrets.get(k) is not None:
                kc[short] = cli_secrets[k]
                written.append(k)
                changed = True
        if changed:
            write_json(kc_file, kc)
    else:
        if isinstance(cli_secrets.get('gemini_keychain'), str) and _write_keychain('gemini', 'antigravity', cli_secrets['gemini_keychain']):
            written.append('gemini_keychain')
        if isinstance(cli_secrets.get('claude_keychain'), str) and _write_keychain('Claude Code-credentials', None, cli_secrets['claude_keychain']):
            written.append('claude_keychain')
    return written


# ---------- 脱敏与导出 ----------

def _redact_dict_secrets(d):
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            if _SECRET_KEY_RE.search(str(k)) and v not in (None, ''):
                out[k] = OMITTED
            else:
                out[k] = _redact_dict_secrets(v)
        return out
    if isinstance(d, list):
        return [_redact_dict_secrets(x) for x in d]
    return d


def _redact_provider_value(v):
    if isinstance(v, str):
        return OMITTED if v else ''
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            if k in SAFE_PROVIDER_META:
                out[k] = copy.deepcopy(val)
            elif isinstance(val, dict):
                out[k] = _redact_provider_value(val)
            elif val not in (None, ''):
                out[k] = OMITTED
            else:
                out[k] = val
        return out
    return OMITTED if v else v


def _sanitize_config_for_export(cfg):
    norm = normalize_config(cfg)
    safe_accounts = []
    for a in norm['accounts']['items']:
        safe_accounts.append({
            'id': a.get('id'),
            'platform': a.get('platform'),
            'label': a.get('label') or '',
            'email': a.get('email') or '',
            'plan': a.get('plan') or '',
            'source': a.get('source') or '',
            'credentials': OMITTED,
        })
    safe_providers = {k: _redact_provider_value(v) for k, v in (norm.get('providers') or {}).items()}
    return {
        'schema_version': SCHEMA_VERSION,
        'settings': _redact_dict_secrets(norm['settings']),
        'defaults': copy.deepcopy(norm['defaults']),
        'accounts': {
            'defaults': copy.deepcopy(norm['accounts']['defaults']),
            'items': safe_accounts,
            'gemini_clients': OMITTED,
        },
        'providers': safe_providers,
        'web': {'token': OMITTED},
    }


def build_export_bundle(home, with_secrets=False, passphrase=None, cli_io=None):
    """生成导出包：默认不带密钥（明文 JSON，密钥替换为「已省略」）；with_secrets=True 时整包用口令加密。"""
    home = Path(home)
    migrate_if_needed(home)
    cfg = read_config(home / 'studio.json')
    if not with_secrets:
        return {
            'format': BUNDLE_FORMAT,
            'version': 1,
            'encrypted': False,
            'with_secrets': False,
            'exported_at': now_iso(),
            'config': _sanitize_config_for_export(cfg),
        }
    if not passphrase:
        raise ValueError('带密钥导出需要输入口令')
    payload = {
        'format': BUNDLE_FORMAT,
        'version': 1,
        'with_secrets': True,
        'exported_at': now_iso(),
        'warning': RT_ROTATION_WARNING,
        'config': cfg,
        'cli_secrets': read_cli_secrets(cli_io=cli_io),
    }
    return encrypt_payload(payload, passphrase)


def export_to_file(home, out_path, with_secrets=False, passphrase=None, cli_io=None):
    bundle = build_export_bundle(home, with_secrets=with_secrets, passphrase=passphrase, cli_io=cli_io)
    out_path = Path(out_path)
    write_json(out_path, bundle)
    return {'path': str(out_path), 'with_secrets': bool(with_secrets), 'encrypted': bool(bundle.get('encrypted')),
            'warning': RT_ROTATION_WARNING if with_secrets else ''}


# ---------- 导入预览（差异）与写入 ----------

def parse_bundle(bundle_or_path, passphrase=None):
    if isinstance(bundle_or_path, (str, os.PathLike)):
        s = str(bundle_or_path)
        if s.lstrip().startswith('{'):
            bundle = json.loads(s)
        else:
            bundle = json.loads(Path(bundle_or_path).read_text(encoding='utf-8'))
    elif isinstance(bundle_or_path, dict):
        bundle = bundle_or_path
    else:
        raise ValueError('配置包格式不对')

    if not isinstance(bundle, dict):
        raise ValueError('配置包格式不对')
    if bundle.get('encrypted'):
        payload = decrypt_bundle(bundle, passphrase)
        return payload, True
    if bundle.get('format') == BUNDLE_FORMAT or 'config' in bundle:
        return bundle, False
    return {'format': BUNDLE_FORMAT, 'with_secrets': False, 'config': bundle}, False


def _merge_skip_omitted(cur_val, inc_val):
    """递归合并：遇到「已省略」不覆盖本机已有值；本机没有也不写入「已省略」。"""
    if inc_val == OMITTED:
        return copy.deepcopy(cur_val)
    if isinstance(inc_val, dict):
        base = copy.deepcopy(cur_val) if isinstance(cur_val, dict) else {}
        for k, v in inc_val.items():
            if v == OMITTED:
                continue
            merged_sub = _merge_skip_omitted(base.get(k), v)
            if merged_sub is not None:
                base[k] = merged_sub
        return base
    return copy.deepcopy(inc_val)


def _plan_import(current, payload, encrypted=False):
    cur = normalize_config(current)
    inc_raw = payload.get('config') if isinstance(payload.get('config'), dict) else {}
    inc = normalize_config(inc_raw)
    with_secrets = bool(payload.get('with_secrets'))
    cli_secrets = payload.get('cli_secrets') if (with_secrets and isinstance(payload.get('cli_secrets'), dict)) else {}

    merged = copy.deepcopy(cur)
    changes = []
    warnings = []

    # 1. settings
    for k, v in inc['settings'].items():
        if v == OMITTED:
            if k in cur['settings']:
                changes.append({'section': 'settings', 'key': k, 'action': 'skip', 'summary': f'{k}：已省略（保留本机值）'})
            continue
        if k not in cur['settings']:
            merged['settings'][k] = copy.deepcopy(v)
            changes.append({'section': 'settings', 'key': k, 'action': 'add', 'summary': f'新增设置 {k}'})
        elif cur['settings'][k] != v:
            merged['settings'][k] = copy.deepcopy(v)
            changes.append({'section': 'settings', 'key': k, 'action': 'update', 'summary': f'更新设置 {k}'})

    # 2. defaults
    for k in DEFAULT_KEYS:
        inc_d = inc['defaults'].get(k) or {}
        cur_d = cur['defaults'].get(k) or {}
        for role, v in inc_d.items():
            if v == OMITTED:
                continue
            if role not in cur_d:
                merged['defaults'].setdefault(k, {})[role] = copy.deepcopy(v)
                changes.append({'section': 'defaults', 'key': f'{k}.{role}', 'action': 'add',
                                'summary': f'新增默认 {k}.{role} = {v}'})
            elif cur_d[role] != v:
                merged['defaults'].setdefault(k, {})[role] = copy.deepcopy(v)
                changes.append({'section': 'defaults', 'key': f'{k}.{role}', 'action': 'update',
                                'summary': f'更新默认 {k}.{role}：{cur_d[role]} → {v}'})

    # 3. providers
    for pname, pval in inc['providers'].items():
        if pval == OMITTED:
            if pname in cur['providers']:
                changes.append({'section': 'providers', 'key': pname, 'action': 'skip',
                                'summary': f'provider {pname}：已省略（保留本机值）'})
            continue
        old_p = cur['providers'].get(pname)
        new_p = _merge_skip_omitted(old_p, pval)
        if old_p is None:
            merged['providers'][pname] = new_p
            changes.append({'section': 'providers', 'key': pname, 'action': 'add', 'summary': f'新增 provider {pname}'})
        elif new_p != old_p:
            merged['providers'][pname] = new_p
            changes.append({'section': 'providers', 'key': pname, 'action': 'update', 'summary': f'更新 provider {pname}'})
        elif isinstance(pval, dict) and OMITTED in json.dumps(pval, ensure_ascii=False):
            changes.append({'section': 'providers', 'key': pname, 'action': 'skip',
                            'summary': f'provider {pname}：密钥已省略（保留本机值）'})

    # 4. web.token
    inc_tok = (inc.get('web') or {}).get('token') or ''
    cur_tok = (cur.get('web') or {}).get('token') or ''
    web_token_changed = False
    if inc_tok == OMITTED or not inc_tok:
        if cur_tok:
            changes.append({'section': 'web', 'key': 'token', 'action': 'skip', 'summary': '网页口令：已省略（保留本机口令）'})
    elif inc_tok != cur_tok:
        merged['web']['token'] = inc_tok
        web_token_changed = True
        changes.append({'section': 'web', 'key': 'token', 'action': 'update' if cur_tok else 'add',
                        'summary': f'{"更新" if cur_tok else "导入"}网页口令（{mask(inc_tok)}）'})

    # 5. accounts
    cur_by_id = {a['id']: i for i, a in enumerate(merged['accounts']['items'])}
    imported_secret_accounts = 0
    for acc in inc['accounts']['items']:
        aid = acc['id']
        idx = cur_by_id.get(aid)
        cred = acc.get('credentials')
        has_cred_secret = isinstance(cred, dict) and bool(cred)
        omitted_cred = cred == OMITTED or not has_cred_secret
        if idx is None:
            new_acc = copy.deepcopy(acc)
            if omitted_cred:
                new_acc.pop('credentials', None)
                new_acc['status'] = 'logged_out'
                new_acc['disabled'] = True
                changes.append({'section': 'accounts', 'key': aid, 'action': 'add',
                                'summary': f'新增账号元数据 {aid}（{acc.get("platform")} · {acc.get("email") or acc.get("label") or "-"}，凭据已省略）'})
            else:
                new_acc['disabled'] = True     # 导入含密钥的号默认禁用，确认后再启用（防 RT 轮换冲突）
                imported_secret_accounts += 1
                changes.append({'section': 'accounts', 'key': aid, 'action': 'add',
                                'summary': f'导入账号 {aid}（{acc.get("platform")} · {acc.get("email") or acc.get("label") or "-"}，含凭据，默认已禁用）'})
            merged['accounts']['items'].append(new_acc)
            cur_by_id[aid] = len(merged['accounts']['items']) - 1
        else:
            existing = merged['accounts']['items'][idx]
            if omitted_cred:
                changed_meta = False
                for mf in ('label', 'email', 'plan'):
                    if acc.get(mf) and acc.get(mf) != existing.get(mf):
                        existing[mf] = acc[mf]
                        changed_meta = True
                changes.append({'section': 'accounts', 'key': aid,
                                'action': 'update' if changed_meta else 'skip',
                                'summary': f'账号 {aid}：凭据已省略（保留本机凭据）'})
            else:
                updated = copy.deepcopy(acc)
                updated['disabled'] = True
                merged['accounts']['items'][idx] = updated
                imported_secret_accounts += 1
                changes.append({'section': 'accounts', 'key': aid, 'action': 'update',
                                'summary': f'覆盖账号 {aid}（{acc.get("platform")} · {acc.get("email") or acc.get("label") or "-"}，含凭据，默认已禁用）'})

    for pl, aid in (inc['accounts'].get('defaults') or {}).items():
        if aid and merged['accounts']['defaults'].get(pl) != aid:
            old_d = merged['accounts']['defaults'].get(pl)
            merged['accounts']['defaults'][pl] = aid
            changes.append({'section': 'accounts', 'key': f'defaults.{pl}',
                            'action': 'update' if old_d else 'add',
                            'summary': f'{pl} 默认号：{old_d or "无"} → {aid}'})

    inc_gc = (inc_raw.get('accounts') or {}).get('gemini_clients')
    if isinstance(inc_gc, dict) and inc_gc.get('pairs'):
        if merged['accounts'].get('gemini_clients') != inc_gc:
            merged['accounts']['gemini_clients'] = copy.deepcopy(inc_gc)
            changes.append({'section': 'accounts', 'key': 'gemini_clients', 'action': 'update',
                            'summary': '更新 Gemini OAuth 客户端缓存'})

    # 6. cli_secrets
    for k in ('codex_local_auth', 'opencode_auth', 'gemini_keychain', 'claude_keychain', 'agy_token_file'):
        if cli_secrets.get(k):
            label = {'codex_local_auth': '本机 ~/.codex/auth.json',
                     'opencode_auth': '本机 OpenCode auth.json',
                     'gemini_keychain': '本机 agy 钥匙串',
                     'claude_keychain': '本机 Claude Code 钥匙串',
                     'agy_token_file': '本机 agy 备用凭据文件'}[k]
            changes.append({'section': 'cli_secrets', 'key': k, 'action': 'write', 'summary': f'写回 {label}'})

    if imported_secret_accounts or cli_secrets:
        warnings.append(RT_ROTATION_WARNING)
    if web_token_changed:
        warnings.append('网页登录口令已变更，重启网页服务后生效。')

    counts = {a: sum(1 for c in changes if c['action'] == a) for a in ('add', 'update', 'skip', 'write')}
    plan = {
        'encrypted': encrypted,
        'with_secrets': with_secrets,
        'changes': changes,
        'counts': counts,
        'warnings': warnings,
        'web_token_changed': web_token_changed,
    }
    return merged, plan


def preview_import(home, bundle_or_path, passphrase=None):
    home = Path(home)
    migrate_if_needed(home)
    payload, encrypted = parse_bundle(bundle_or_path, passphrase=passphrase)
    cur = read_config(home / 'studio.json')
    _, plan = _plan_import(cur, payload, encrypted=encrypted)
    return plan


def apply_import(home, bundle_or_path, passphrase=None, cli_io=None):
    home = Path(home)
    migrate_if_needed(home)
    payload, encrypted = parse_bundle(bundle_or_path, passphrase=passphrase)
    conf = home / 'studio.json'
    backup_path = None
    with config_lock(conf):
        cur = read_config(conf)
        merged, plan = _plan_import(cur, payload, encrypted=encrypted)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + f'-{secrets.token_hex(2)}'
        bdir = home / 'backup' / f'pre-import-{stamp}'
        if conf.exists():
            backup_path = str(bdir / 'studio.json')
            _backup_file(conf, backup_path)
        write_json(conf, merged)

        # 为导入的非本机 Codex 号生成运行时 auth.json
        for a in merged['accounts']['items']:
            if a.get('platform') == 'codex' and a.get('source') != 'local' and isinstance(a.get('credentials'), dict):
                ap = home / 'accounts' / 'codex' / a['id'] / 'auth.json'
                write_json(ap, a['credentials'])

        written_cli = []
        if plan['with_secrets'] and isinstance(payload.get('cli_secrets'), dict) and payload['cli_secrets']:
            written_cli = write_cli_secrets(payload['cli_secrets'], backup_dir=bdir, cli_io=cli_io)

    return {'applied': True, 'backup_path': backup_path, 'written_cli': written_cli, 'preview': plan}
