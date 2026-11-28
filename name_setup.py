"""ワンボタン更新版の初回配布（3ファイル入り zip）。

保存先はデータフォルダ（git 管理外）の name-label-setup/。常に最新の1件だけを残す。
既存の name-label-exe/ とは別スロットで、そちらは消さない。
アップロードは主管理者のみ、ダウンロードはログイン済みの全員。
"""
import hashlib
import html
import io
import json
import os
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import name_exe

MAX_BYTES = name_exe.MAX_BYTES
JST = name_exe.JST
META_NAME = 'meta.json'
HISTORY_NAME = 'history.json'
STORE_NAME = 'package.zip'
DOWNLOAD_NAME = '統合ネームラベル.zip'
CHUNK = name_exe.CHUNK
NOT_UPLOADED = '配布ファイルはまだアップロードされていません。主管理者にアップロードを依頼してください。'
GITHUB_EXE_URL = 'https://github.com/afm11o77u-png/togo-name-label/releases/latest/download/togo-name-label.exe'
GITHUB_TIMEOUT = 5
GITHUB_CACHE_OK = 300
GITHUB_CACHE_MISS = 60
EXE_FILE = '統合ネームラベル.exe'
DLL_FILE = 'zxing.dll'
CONFIG_FILE = '統合ネームラベル.exe.config'
OPTIONAL_DLL = 'DotNetBarcode.dll'
EXE_ALIASES = {EXE_FILE.lower(), 'togo-name-label.exe'}

ExeError = name_exe.ExeError

_github_cache = {'at': 0.0, 'ok': False, 'data': None}  # data is bytes or None
_github_lock = threading.Lock()
_history_lock = threading.Lock()


def folder(data_dir):
    return Path(data_dir) / 'name-label-setup'


def check_user(user, upload=False):
    if not user or user['must_change']:
        raise ExeError(401, 'ログインしてください。')
    if upload and user['role'] != 'owner':
        raise ExeError(403, '配布ファイルは主管理者だけがアップロードできます。')


def clean_name(name):
    name = str(name or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    name = ''.join(ch for ch in name if ch >= ' ' and ch != '\x7f')
    if not name or not name.lower().endswith('.zip') or Path(name).stem == '':
        raise ExeError(400, '初回導入用の zip（統合ネームラベル.exe、zxing.dll、統合ネームラベル.exe.config）を選んでください。')
    return name[:200]


def _path(data_dir):
    path = folder(data_dir) / STORE_NAME
    return path if path.is_file() else None


def _read_meta(data_dir):
    meta_path = folder(data_dir) / META_NAME
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('filename') else None


def info(data_dir):
    meta = _read_meta(data_dir)
    path = _path(data_dir)
    if not meta or not path:
        return None
    meta = dict(meta)
    meta['size'] = path.stat().st_size
    return meta


def _zip_write(zf, name, data):
    info = zipfile.ZipInfo(name)
    info.date_time = datetime.now(JST).timetuple()[:6]
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.flag_bits |= 1 << 11  # UTF-8 のファイル名
    zf.writestr(info, data)


def _build_zip(parts):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name in (EXE_FILE, DLL_FILE, CONFIG_FILE, OPTIONAL_DLL):
            if name in parts:
                _zip_write(zf, name, parts[name])
    return buf.getvalue()


def normalize_zip(data):
    """zip から必要ファイルを抜き、名前を揃えた zip バイト列を返す。"""
    if not data.startswith(b'PK'):
        raise ExeError(400, 'zipファイルではないようです。')
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExeError(400, 'zipを開けませんでした。ファイルが壊れていないか確認してください。')
    parts = {}
    uncompressed = 0
    try:
        for item in zf.infolist():
            if item.is_dir():
                continue
            base = Path(item.filename.replace('\\', '/')).name
            if not base or base in ('.', '..') or base.startswith('.'):
                continue
            uncompressed += item.file_size
            if uncompressed > MAX_BYTES:
                raise ExeError(413, f'ファイルが大きすぎます（上限 {MAX_BYTES // (1024 * 1024)}MB）。')
            raw = zf.read(item)
            key = base.lower()
            if key.endswith('.exe') and raw.startswith(b'MZ'):
                preferred = key in EXE_ALIASES
                if EXE_FILE not in parts or preferred:
                    parts[EXE_FILE] = raw
            elif key == DLL_FILE.lower() and raw.startswith(b'MZ'):
                parts[DLL_FILE] = raw
            elif key == CONFIG_FILE.lower() or key.endswith('.exe.config'):
                parts[CONFIG_FILE] = raw
            elif key == OPTIONAL_DLL.lower() and raw.startswith(b'MZ'):
                parts[OPTIONAL_DLL] = raw
    finally:
        zf.close()
    missing = [name for name in (EXE_FILE, DLL_FILE, CONFIG_FILE) if name not in parts]
    if missing:
        raise ExeError(400, 'zipに 統合ネームラベル.exe、zxing.dll、統合ネームラベル.exe.config を入れてください。')
    return _build_zip(parts)


def save(data_dir, user, filename, stream, length, now=None, notes=None):
    check_user(user, upload=True)
    if notes is not None:
        notes = name_exe.clean_notes(notes)
    clean_name(filename)
    if length is None or length <= 0:
        raise ExeError(400, 'ファイルが空です。')
    if length > MAX_BYTES:
        raise ExeError(413, f'ファイルが大きすぎます（上限 {MAX_BYTES // (1024 * 1024)}MB）。')
    raw = stream.read(length)
    if len(raw) != length:
        raise ExeError(400, 'アップロードが途中で切れました。もう一度お試しください。')
    body = normalize_zip(raw)
    meta = _store(data_dir, body, user['name'], now)
    if notes:
        set_notes(data_dir, user, notes, meta['sha256'])
    return meta


def _store(data_dir, body, uploaded_by, now=None):
    base = folder(data_dir)
    base.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.upload-', suffix='.zip', dir=base)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(body)
            f.flush(); os.fsync(f.fileno())
        uploaded = (now or datetime.now(JST)).astimezone(JST)
        meta = {
            'filename': DOWNLOAD_NAME, 'size': len(body),
            'uploadedAt': uploaded.strftime('%Y-%m-%d %H:%M'),
            'uploadedBy': uploaded_by, 'sha256': hashlib.sha256(body).hexdigest(),
        }
        dest = base / STORE_NAME
        os.replace(tmp, dest)
        for old in base.iterdir():
            if old.is_file() and old.name.startswith('package') and old != dest:
                old.unlink(missing_ok=True)
        name_exe._atomic_json(base / META_NAME, meta)
        _remember_version(data_dir, meta)
        return meta
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _clean_serve_temps(base):
    now = datetime.now().timestamp()
    for p in base.glob('.serve-*.zip'):
        try:
            if now - p.stat().st_mtime > 600:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def fetch_github_exe(url=GITHUB_EXE_URL, timeout=GITHUB_TIMEOUT):
    """最新 exe を取る。失敗・404・形式違いなら None。短いキャッシュあり。"""
    now = datetime.now().timestamp()
    with _github_lock:
        age = now - _github_cache['at']
        if _github_cache['at'] and age < (GITHUB_CACHE_OK if _github_cache['ok'] else GITHUB_CACHE_MISS):
            return _github_cache['data'] if _github_cache['ok'] else None
    data, ok = None, False
    try:
        req = Request(url, headers={'User-Agent': 'instruction-desk-name-setup'})
        with urlopen(req, timeout=timeout) as resp:
            if getattr(resp, 'status', 200) >= 400:
                raise OSError('bad status')
            chunks, total = [], 0
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise OSError('too large')
                chunks.append(chunk)
            data = b''.join(chunks)
        ok = bool(data) and data.startswith(b'MZ')
        if not ok:
            data = None
    except Exception:
        data, ok = None, False
    with _github_lock:
        _github_cache.update(at=datetime.now().timestamp(), ok=ok, data=data)
    return data if ok else None


def _zip_parts(data):
    parts = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for item in zf.infolist():
            if item.is_dir():
                continue
            base = Path(item.filename.replace('\\', '/')).name
            if base:
                parts[base] = zf.read(item)
    return parts


def _with_latest_exe(zip_bytes):
    latest = fetch_github_exe()
    if not latest:
        return None
    parts = _zip_parts(zip_bytes)
    if parts.get(EXE_FILE) == latest:
        return None
    parts[EXE_FILE] = latest
    return _build_zip(parts)


def download(data_dir, user):
    check_user(user)
    meta = info(data_dir)
    path = _path(data_dir)
    if not meta or not path:
        raise ExeError(404, NOT_UPLOADED)
    raw = path.read_bytes()
    refreshed = _with_latest_exe(raw)
    if refreshed:
        base = folder(data_dir)
        _clean_serve_temps(base)
        fd, tmp = tempfile.mkstemp(prefix='.serve-', suffix='.zip', dir=base)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(refreshed)
            path = Path(tmp)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return path, meta, name_exe.content_disposition(DOWNLOAD_NAME), 'application/zip'


def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS name_setup_downloads (user_id INTEGER PRIMARY KEY, version TEXT NOT NULL, filename TEXT, at TEXT NOT NULL)')


def record_download(c, user, meta, now=None):
    schema(c)
    at = (now or datetime.now(JST)).astimezone(JST).strftime('%Y-%m-%d %H:%M')
    c.execute('INSERT INTO name_setup_downloads(user_id, version, filename, at) VALUES(?,?,?,?) '
              'ON CONFLICT(user_id) DO UPDATE SET version=excluded.version, filename=excluded.filename, at=excluded.at',
              (user['id'], name_exe.version_of(meta), meta.get('filename'), at))


def update_status(c, user, meta):
    schema(c)
    row = c.execute('SELECT version, at FROM name_setup_downloads WHERE user_id=?', (user['id'],)).fetchone()
    last = row[1] if row else None
    if not meta:
        return {'pending': False, 'lastDownloadAt': last}
    return {'pending': not row or row[0] != name_exe.version_of(meta), 'lastDownloadAt': last}


def _read_history(data_dir):
    try:
        data = json.loads((folder(data_dir) / HISTORY_NAME).read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _remember_version(data_dir, meta):
    with _history_lock:
        items = _read_history(data_dir)
        old = next((h for h in items if h.get('sha256') == meta['sha256']), None)
        items = [h for h in items if h.get('sha256') != meta['sha256']]
        entry = {'sha256': meta['sha256'], 'filename': meta['filename'], 'size': meta['size'],
                 'uploadedAt': meta['uploadedAt'], 'uploadedBy': meta['uploadedBy'],
                 'notes': (old or {}).get('notes', ''), 'notesUpdatedAt': (old or {}).get('notesUpdatedAt')}
        name_exe._atomic_json(folder(data_dir) / HISTORY_NAME, ([entry] + items)[:name_exe.HISTORY_MAX])


def history(data_dir):
    current = name_exe.version_of(info(data_dir))
    out = []
    for h in _read_history(data_dir):
        item = {k: h.get(k) for k in ('filename', 'size', 'uploadedAt', 'uploadedBy', 'notes', 'notesUpdatedAt')}
        item['notes'] = item['notes'] or ''
        item['current'] = bool(current) and h.get('sha256') == current
        out.append(item)
    return out


def current_notes(data_dir):
    cur = name_exe.version_of(info(data_dir))
    for h in _read_history(data_dir):
        if cur and h.get('sha256') == cur:
            return h.get('notes') or ''
    return ''


def set_notes(data_dir, user, notes, version=None, now=None):
    check_user(user, upload=True)
    notes = name_exe.clean_notes(notes)
    meta = info(data_dir)
    if not meta:
        raise ExeError(404, NOT_UPLOADED)
    target = version or name_exe.version_of(meta)
    with _history_lock:
        items = _read_history(data_dir)
        entry = next((h for h in items if h.get('sha256') == target), None)
        if entry is None:
            if target != name_exe.version_of(meta):
                raise ExeError(404, 'この版が見つかりません。')
            entry = {'sha256': target, 'filename': meta.get('filename'), 'size': meta.get('size'),
                     'uploadedAt': meta.get('uploadedAt'), 'uploadedBy': meta.get('uploadedBy')}
            items.insert(0, entry)
        entry['notes'] = notes
        entry['notesUpdatedAt'] = (now or datetime.now(JST)).astimezone(JST).strftime('%Y-%m-%d %H:%M')
        name_exe._atomic_json(folder(data_dir) / HISTORY_NAME, items[:name_exe.HISTORY_MAX])
    return notes


def display_history(data_dir):
    """モーダル用。配布ファイルがあればその履歴、なければ既存 exe の履歴。"""
    return history(data_dir) if info(data_dir) else name_exe.history(data_dir)


def display_notes(data_dir):
    """モーダル用。配布ファイルがあればその更新内容、なければ既存 exe の更新内容。"""
    return current_notes(data_dir) if info(data_dir) else name_exe.current_notes(data_dir)


def iso_jst(text):
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=JST).isoformat()
    except (TypeError, ValueError):
        return text or ''


def release_notes(data_dir):
    """ログイン不要の公開用。アップロード者名は出さない。モーダルと同じ更新内容・履歴。"""
    hist = display_history(data_dir)
    items = [{
        'uploaded_at': iso_jst(h.get('uploadedAt')),
        'file_name': h.get('filename') or '',
        'notes': h.get('notes') or '',
    } for h in hist]
    current = next((h for h in hist if h.get('current')), hist[0] if hist else None)
    latest = None
    if current:
        latest = {
            'file_name': current.get('filename') or '',
            'uploaded_at': iso_jst(current.get('uploadedAt')),
            'notes': current.get('notes') or '',
        }
    return {'latest': latest, 'history': items}


def release_notes_html(data_dir):
    data = release_notes(data_dir)
    latest = data['latest']
    if latest:
        latest_block = (
            f"<section><h2>最新</h2><p>{html.escape(latest['file_name'])}　"
            f"{html.escape(latest['uploaded_at'])}（日本時間）</p>"
            f"<pre>{html.escape(latest['notes'] or '更新内容なし')}</pre></section>"
        )
    else:
        latest_block = '<section><h2>最新</h2><p>まだ更新内容はありません。</p></section>'
    rows = []
    for item in data['history']:
        rows.append(
            f"<section><h3>{html.escape(item['file_name'])}　{html.escape(item['uploaded_at'])}（日本時間）</h3>"
            f"<pre>{html.escape(item['notes'] or '更新内容なし')}</pre></section>"
        )
    history_block = ''.join(rows) or '<p>履歴はありません。</p>'
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>統合ネームラベル 更新内容</title>'
        '<style>body{font-family:sans-serif;max-width:40rem;margin:1.5rem auto;padding:0 1rem;line-height:1.6}'
        'pre{white-space:pre-wrap;background:#f4faf4;border:1px solid #d5ead4;border-radius:8px;padding:12px}'
        'h1{font-size:1.25rem}h2,h3{font-size:1rem}</style></head><body>'
        '<h1>統合ネームラベル 更新内容</h1>'
        f'{latest_block}<h2>更新履歴</h2>{history_block}</body></html>'
    )
