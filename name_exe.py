"""統合ネームラベル（togo-name-label）Windows exe の配布。

保存先はデータフォルダ（git 管理外）の name-label-exe/。常に最新の1件だけを残す。
アップロードは主管理者のみ、ダウンロードはログイン済みの全員。
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

MAX_BYTES = 200 * 1024 * 1024
JST = timezone(timedelta(hours=9))
EXE_NAME = 'togo-name-label.exe'
META_NAME = 'meta.json'
CHUNK = 1024 * 1024
NOT_UPLOADED = '統合ネームラベルのexeはまだアップロードされていません。主管理者にアップロードを依頼してください。'


class ExeError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def folder(data_dir):
    return Path(data_dir) / 'name-label-exe'


def check_user(user, upload=False):
    if not user or user['must_change']:
        raise ExeError(401, 'ログインしてください。')
    if upload and user['role'] != 'owner':
        raise ExeError(403, '統合ネームラベルのexeは主管理者だけがアップロードできます。')


def clean_name(name):
    name = str(name or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    name = ''.join(ch for ch in name if ch >= ' ' and ch != '\x7f')
    if not name or not name.lower().endswith('.exe') or name.lower() == '.exe':
        raise ExeError(400, '拡張子が .exe のファイルを選んでください。')
    return name[:200]


def info(data_dir):
    base = folder(data_dir)
    exe, meta = base / EXE_NAME, base / META_NAME
    if not exe.is_file() or not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    data['size'] = exe.stat().st_size
    return data


def _atomic_json(path, data):
    fd, tmp = tempfile.mkstemp(prefix='.meta-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(data_dir, user, filename, stream, length, now=None):
    """stream から length バイトを一時ファイルに書き、置き換える。"""
    check_user(user, upload=True)
    name = clean_name(filename)
    if length is None or length <= 0:
        raise ExeError(400, 'ファイルが空です。')
    if length > MAX_BYTES:
        raise ExeError(413, f'ファイルが大きすぎます（上限 {MAX_BYTES // (1024 * 1024)}MB）。')
    base = folder(data_dir)
    base.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.upload-', suffix='.exe', dir=base)
    try:
        remaining, head = length, b''
        with os.fdopen(fd, 'wb') as f:
            while remaining:
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:
                    raise ExeError(400, 'アップロードが途中で切れました。もう一度お試しください。')
                if len(head) < 2:
                    head += chunk[:2 - len(head)]
                f.write(chunk)
                remaining -= len(chunk)
            f.flush(); os.fsync(f.fileno())
        if head != b'MZ':
            raise ExeError(400, 'Windowsの実行ファイル（exe）ではないようです。')
        uploaded = (now or datetime.now(JST)).astimezone(JST)
        meta = {
            'filename': name, 'size': length,
            'uploadedAt': uploaded.strftime('%Y-%m-%d %H:%M'),
            'uploadedBy': user['name'],
        }
        os.replace(tmp, base / EXE_NAME)
        _atomic_json(base / META_NAME, meta)
        return meta
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def download(data_dir, user):
    check_user(user)
    meta = info(data_dir)
    if not meta:
        raise ExeError(404, NOT_UPLOADED)
    return folder(data_dir) / EXE_NAME, meta, content_disposition(meta['filename'])


def content_disposition(name):
    ascii_name = ''.join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else '_' for ch in name) or 'togo-name-label.exe'
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"
