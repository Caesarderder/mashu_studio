"""微信 CDN 多媒体格式，依据 Tencent/openclaw-weixin 的 MIT 许可实现。"""
import base64
import hashlib
import mimetypes
import os
import re
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CDN = 'https://novac2c.cdn.weixin.qq.com/c2c'
MAX_BYTES = 50_000_000


class MediaError(Exception):
    pass


def https_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise MediaError('媒体地址无效')
    return url


def crypt(data, key, encrypt):
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    if encrypt:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        worker = cipher.encryptor()
        return worker.update(padded) + worker.finalize()
    worker = cipher.decryptor()
    padded = worker.update(data) + worker.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def media_key(value):
    raw = base64.b64decode(value, validate=True)
    if len(raw) == 32:
        raw = bytes.fromhex(raw.decode('ascii'))
    if len(raw) != 16:
        raise MediaError('媒体密钥格式错误')
    return raw


def fetch_inbound(item):
    kind = item.get('type')
    field = {2: 'image_item', 3: 'voice_item', 4: 'file_item', 5: 'video_item'}.get(kind)
    if not field:
        raise MediaError('不支持的附件类型')
    detail = item.get(field) or {}
    media = detail.get('media') or {}
    url = media.get('full_url')
    if not url and media.get('encrypt_query_param'):
        url = CDN + '/download?' + urllib.parse.urlencode({'encrypted_query_param': media['encrypt_query_param']})
    if not url:
        raise MediaError('微信消息没有媒体下载地址')
    key = bytes.fromhex(detail['aeskey']) if kind == 2 and detail.get('aeskey') else (media_key(media['aes_key']) if media.get('aes_key') else None)
    if not key and kind != 2:
        raise MediaError('微信消息缺少媒体解密密钥')
    try:
        with urllib.request.urlopen(https_url(url), timeout=90) as response:
            data = response.read(MAX_BYTES + 17)
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500 and exc.code != 429:
            raise MediaError('微信媒体下载失败，状态 ' + str(exc.code)) from exc
        raise
    if len(data) > MAX_BYTES + 16:
        raise MediaError('附件超过 50 MB')
    if key:
        data = crypt(data, key, False)
    if len(data) > MAX_BYTES:
        raise MediaError('附件超过 50 MB')
    if kind == 2:
        ext = '.png' if data.startswith(b'\x89PNG\r\n\x1a\n') else '.gif' if data.startswith(b'GIF8') else '.webp' if data.startswith(b'RIFF') and data[8:12] == b'WEBP' else '.jpg'
        name = '图片' + ext
    elif kind == 3:
        ext = {5: '.amr', 6: '.silk', 7: '.mp3'}.get(detail.get('encode_type'), '.silk')
        name = '语音' + ext
    elif kind == 5:
        name = '视频.mp4'
    else:
        name = re.sub(r'[\\/:*?"<>|()\[\]\s\x00-\x1f]+', '_', Path(detail.get('file_name') or '文件.bin').name).strip(' ._') or '文件.bin'
        if len(name.encode('utf-8')) > 180:
            name = name.encode('utf-8')[:150].decode('utf-8', 'ignore') + Path(name).suffix[:16]
    return data, name, kind, detail.get('text') if kind == 3 else None


def send_item(path, owner, get_upload, read_blob):
    data = read_blob(path)
    if len(data) > MAX_BYTES:
        raise MediaError('附件超过 50 MB')
    mime = mimetypes.guess_type(path)[0] or ''
    kind = 1 if mime.startswith('image/') else 2 if mime.startswith('video/') else 3
    key = os.urandom(16)
    filekey = os.urandom(16).hex()
    encrypted = crypt(data, key, True)
    response = get_upload({'filekey': filekey, 'media_type': kind, 'to_user_id': owner,
                           'rawsize': len(data), 'rawfilemd5': hashlib.md5(data).hexdigest(),
                           'filesize': len(encrypted), 'no_need_thumb': True, 'aeskey': key.hex()})
    url = response.get('upload_full_url')
    if not url and response.get('upload_param'):
        url = CDN + '/upload?' + urllib.parse.urlencode({'encrypted_query_param': response['upload_param'], 'filekey': filekey})
    if not url:
        raise MediaError('微信没有返回上传地址')
    req = urllib.request.Request(https_url(url), data=encrypted, headers={'Content-Type': 'application/octet-stream'})
    with urllib.request.urlopen(req, timeout=120) as result:
        param = result.headers.get('x-encrypted-param')
    if not param:
        raise MediaError('微信没有返回媒体下载参数')
    reference = {'encrypt_query_param': param, 'aes_key': base64.b64encode(key.hex().encode()).decode(), 'encrypt_type': 1}
    if kind == 1:
        return {'type': 2, 'image_item': {'media': reference, 'mid_size': len(encrypted)}}
    if kind == 2:
        return {'type': 5, 'video_item': {'media': reference, 'video_size': len(encrypted)}}
    return {'type': 4, 'file_item': {'media': reference, 'file_name': Path(path).name, 'len': str(len(data))}}
