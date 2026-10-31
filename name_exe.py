"""統合ネームラベル（togo-name-label）Windows exe の配布。

保存先はデータフォルダ（git 管理外）の name-label-exe/。常に最新の1件だけを残す。
アップロードは主管理者のみ、ダウンロードはログイン済みの全員。
"""
import hashlib
import json
import threading
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


def save(data_dir, user, filename, stream, length, now=None, notes=None):
    """stream から length バイトを一時ファイルに書き、置き換える。notes は更新内容（任意）。"""
    check_user(user, upload=True)
    if notes is not None:
        notes = clean_notes(notes)
    meta = _store(data_dir, filename, stream, length, user['name'], now)
    if notes:
        set_notes(data_dir, user, notes, meta['sha256'])
    return meta


def _store(data_dir, filename, stream, length, uploaded_by, now=None, extra=None):
    name = clean_name(filename)
    if length is None or length <= 0:
        raise ExeError(400, 'ファイルが空です。')
    if length > MAX_BYTES:
        raise ExeError(413, f'ファイルが大きすぎます（上限 {MAX_BYTES // (1024 * 1024)}MB）。')
    base = folder(data_dir)
    base.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.upload-', suffix='.exe', dir=base)
    try:
        remaining, head, sha = length, b'', hashlib.sha256()
        with os.fdopen(fd, 'wb') as f:
            while remaining:
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:
                    raise ExeError(400, 'アップロードが途中で切れました。もう一度お試しください。')
                if len(head) < 2:
                    head += chunk[:2 - len(head)]
                f.write(chunk)
                sha.update(chunk)
                remaining -= len(chunk)
            if extra is not None and stream.read(1):  # 置き場のファイルだけ（HTTPでは読むと待ってしまう）
                raise ExeError(400, '読み込み中にファイルが変わりました。')
            f.flush(); os.fsync(f.fileno())
        if head != b'MZ':
            raise ExeError(400, 'Windowsの実行ファイル（exe）ではないようです。')
        uploaded = (now or datetime.now(JST)).astimezone(JST)
        meta = {
            'filename': name, 'size': length,
            'uploadedAt': uploaded.strftime('%Y-%m-%d %H:%M'),
            'uploadedBy': uploaded_by, 'sha256': sha.hexdigest(),
        }
        meta.update(extra or {})
        os.replace(tmp, base / EXE_NAME)
        _atomic_json(base / META_NAME, meta)
        _remember_version(data_dir, meta)
        return meta
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


DROP_UPLOADER = 'フォルダから自動取込'
DROP_INTERVAL = 15


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(CHUNK), b''):
            h.update(chunk)
    return h.hexdigest()


class DropWatcher:
    """置き場フォルダの exe を見張り、安定したら配布用 exe を差し替える（元ファイルは残す）。

    check() を1回呼ぶごとに1回分の確認。同じ大きさ・更新時刻が2回続けて見えたら「置き終わり」とみなす。
    複数あれば更新時刻が最も新しいもの。同じ中身（SHA-256）は取り込み直さない。
    """

    def __init__(self, data_dir, drop_dir, log=print, interval=DROP_INTERVAL):
        self.data_dir, self.drop_dir = Path(data_dir), Path(drop_dir)
        self.log, self.interval = log, interval
        self.pending = None      # (path, size, mtime_ns) 前回見たもの
        self.done = None         # 処理済み（成功・失敗とも）の (path, size, mtime_ns)
        self._stop = threading.Event()
        self.thread = None

    def _newest(self):
        try:
            files = [p for p in self.drop_dir.iterdir() if p.is_file() and p.suffix.lower() == '.exe' and not p.name.startswith('.')]
        except OSError:
            return None
        best = None
        for p in files:
            try: st = p.stat()
            except OSError: continue
            key = (p, st.st_size, st.st_mtime_ns)
            if best is None or key[2] > best[2]: best = key
        return best

    def check(self, now=None):
        """戻り値: 'imported' / 'waiting' / 'same' / 'failed' / None（何もなし）。"""
        key = self._newest()
        if key is None:
            self.pending = None
            return None
        if key == self.done:
            return None
        if key != self.pending:
            self.pending = key
            return 'waiting'
        path, size, _ = key
        self.done = key
        try:
            current = info(self.data_dir)
            digest = _sha256(path)
            if current and current.get('sha256') == digest:
                return 'same'
            with open(path, 'rb') as f:
                meta = _store(self.data_dir, path.name, f, size, DROP_UPLOADER, now, {'source': 'drop-folder'})
        except ExeError as e:
            self.log(f'[統合ネームラベルexe] 自動取込に失敗: {path.name}: {e.message}')
            return 'failed'
        except OSError as e:
            self.done = None  # 読めない（使用中など）なら次回やり直す
            self.pending = None
            self.log(f'[統合ネームラベルexe] 自動取込に失敗: {path.name}: {e}')
            return 'failed'
        self.log(f"[統合ネームラベルexe] 自動取込しました: {meta['filename']}（{meta['size']} バイト）")
        return 'imported'

    def _run(self):
        # 起動時：すぐ1回、数秒後にもう1回確認（安定していればここで取り込む）
        try: self.check()
        except Exception as e: self.log(f'[統合ネームラベルexe] 自動取込の確認でエラー: {e}')
        if self._stop.wait(3): return
        while not self._stop.is_set():
            try: self.check()
            except Exception as e:
                self.log(f'[統合ネームラベルexe] 自動取込の確認でエラー: {e}')
            self._stop.wait(self.interval)

    def start(self):
        self.drop_dir.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._run, name='name-exe-drop', daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()


def download(data_dir, user):
    check_user(user)
    meta = info(data_dir)
    if not meta:
        raise ExeError(404, NOT_UPLOADED)
    return folder(data_dir) / EXE_NAME, meta, content_disposition(meta['filename'])


def content_disposition(name):
    ascii_name = ''.join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else '_' for ch in name) or 'togo-name-label.exe'
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


# --- 利用者ごとのダウンロード記録（更新のお知らせ用） ---

def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS name_exe_downloads (user_id INTEGER PRIMARY KEY, version TEXT NOT NULL, filename TEXT, at TEXT NOT NULL)')


def version_of(meta):
    """exe の版の識別子。SHA-256（無ければアップロード日時＋サイズ）。"""
    if not meta:
        return None
    return meta.get('sha256') or f"{meta.get('uploadedAt', '')}|{meta.get('size', '')}"


def record_download(c, user, meta, now=None):
    schema(c)
    at = (now or datetime.now(JST)).astimezone(JST).strftime('%Y-%m-%d %H:%M')
    c.execute('INSERT INTO name_exe_downloads(user_id, version, filename, at) VALUES(?,?,?,?) '
              'ON CONFLICT(user_id) DO UPDATE SET version=excluded.version, filename=excluded.filename, at=excluded.at',
              (user['id'], version_of(meta), meta.get('filename'), at))


def update_status(c, user, meta):
    """{'pending': bool, 'lastDownloadAt': str|None}。exe が無ければ pending=False。"""
    schema(c)
    row = c.execute('SELECT version, at FROM name_exe_downloads WHERE user_id=?', (user['id'],)).fetchone()
    last = row[1] if row else None
    if not meta:
        return {'pending': False, 'lastDownloadAt': last}
    return {'pending': not row or row[0] != version_of(meta), 'lastDownloadAt': last}


def download_report(c, meta):
    """主管理者向け：有効な利用者ごとの 最新版ダウンロード済み／未。"""
    schema(c)
    ver = version_of(meta)
    rows = c.execute('SELECT u.id, u.login, u.name, u.role, d.version, d.at FROM users u '
                     'LEFT JOIN name_exe_downloads d ON d.user_id=u.id WHERE u.active=1 ORDER BY u.id').fetchall()
    return [{'id': r[0], 'login': r[1], 'name': r[2], 'role': r[3],
             'latest': bool(ver) and r[4] == ver, 'lastDownloadAt': r[5]} for r in rows]


# --- 更新内容（リリースノート）と版の履歴：name-label-exe/history.json ---

HISTORY_NAME = 'history.json'
NOTES_MAX = 4000
HISTORY_MAX = 100
_history_lock = threading.Lock()


def clean_notes(text):
    if not isinstance(text, str):
        raise ExeError(400, '更新内容は文字で入力してください。')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = ''.join(ch for ch in text if ch in '\n\t' or (ch >= ' ' and ch != '\x7f'))
    text = '\n'.join(line.rstrip() for line in text.split('\n')).strip('\n')
    if len(text) > NOTES_MAX:
        raise ExeError(400, f'更新内容が長すぎます（{NOTES_MAX}文字まで）。')
    return text


def _read_history(data_dir):
    try:
        data = json.loads((folder(data_dir) / HISTORY_NAME).read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _remember_version(data_dir, meta):
    """新しい版を履歴の先頭へ。同じ SHA-256 が既にあれば日時などを更新し、更新内容は残す。"""
    with _history_lock:
        items = _read_history(data_dir)
        old = next((h for h in items if h.get('sha256') == meta['sha256']), None)
        items = [h for h in items if h.get('sha256') != meta['sha256']]
        entry = {'sha256': meta['sha256'], 'filename': meta['filename'], 'size': meta['size'],
                 'uploadedAt': meta['uploadedAt'], 'uploadedBy': meta['uploadedBy'],
                 'notes': (old or {}).get('notes', ''), 'notesUpdatedAt': (old or {}).get('notesUpdatedAt')}
        _atomic_json(folder(data_dir) / HISTORY_NAME, ([entry] + items)[:HISTORY_MAX])


def history(data_dir):
    """新しい順。sha256 は外に出さない（current フラグで現行版を示す）。"""
    current = version_of(info(data_dir))
    out = []
    for h in _read_history(data_dir):
        item = {k: h.get(k) for k in ('filename', 'size', 'uploadedAt', 'uploadedBy', 'notes', 'notesUpdatedAt')}
        item['notes'] = item['notes'] or ''
        item['current'] = bool(current) and h.get('sha256') == current
        out.append(item)
    return out


def current_notes(data_dir):
    cur = version_of(info(data_dir))
    for h in _read_history(data_dir):
        if cur and h.get('sha256') == cur:
            return h.get('notes') or ''
    return ''


def set_notes(data_dir, user, notes, version=None, now=None):
    """現行版（または version 指定の版）の更新内容を保存。更新のお知らせ（SHA-256）には影響しない。"""
    check_user(user, upload=True)
    notes = clean_notes(notes)
    meta = info(data_dir)
    if not meta:
        raise ExeError(404, NOT_UPLOADED)
    target = version or version_of(meta)
    with _history_lock:
        items = _read_history(data_dir)
        entry = next((h for h in items if h.get('sha256') == target), None)
        if entry is None:
            if target != version_of(meta):
                raise ExeError(404, 'この版が見つかりません。')
            entry = {'sha256': target, 'filename': meta.get('filename'), 'size': meta.get('size'),
                     'uploadedAt': meta.get('uploadedAt'), 'uploadedBy': meta.get('uploadedBy')}
            items.insert(0, entry)
        entry['notes'] = notes
        entry['notesUpdatedAt'] = (now or datetime.now(JST)).astimezone(JST).strftime('%Y-%m-%d %H:%M')
        _atomic_json(folder(data_dir) / HISTORY_NAME, items[:HISTORY_MAX])
    return notes
