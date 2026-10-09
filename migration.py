"""Owner-only full business-data export / restore for USB migration.

PC-specific config (network.json, tls/, Thunderbird path, launch paths) stays
outside the ZIP. Business DBs and non-image files under INSTRUCTION_DATA move
together. Placement photos, name samples, and other image binaries are omitted
to keep ZIP size and export time manageable. Restore does not replace or delete
destination image directories.

Destination users / auth are never overwritten: company accounts keep their
IDs, logins, passwords, permissions, and settings. Imported business rows that
reference source user IDs are remapped via an explicit operator choice.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

FORMAT_VERSION = 2
SUPPORTED_FORMAT_VERSIONS = frozenset({1, 2})
MANIFEST_NAME = 'manifest.json'
INSTRUCTIONS_DB = 'instructions.sqlite3'
STOCK_DB = 'stock.sqlite3'
ZIP_PREFIX = 'desk-migration'

# Marker written into exported users.password so hashes never leave the PC.
EXCLUDED_PASSWORD = 'excluded:migration'

# Image-only trees: never packed, never replaced on restore.
IMAGE_TREES = (
    'images/placement',
    'images/names',
)

# Non-image business file trees (relative paths in ZIP).
FILE_TREES = (
    'name-import',
)

IMAGE_SUFFIXES = {
    '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.ico', '.heic', '.heif',
}

# Never pack these (backup history, generated mail, probes, PC-local noise).
SKIP_DIR_NAMES = {
    'backups',
    'code-backups',
    'mail-out',
    '__pycache__',
    '.migration-work',
    '.migration-incoming',
    '.migration-rollback',
}

AUTH_SETTINGS_KEYS = frozenset({'recovery'})

# Ownership / assignee columns remapped to destination users.
OWNERSHIP_COLUMNS = (
    ('templates', 'user_id'),
    ('label_sets', 'user_id'),
    ('picking_lists', 'creator'),
    ('placements', 'created_by_id'),
    ('placements', 'updated_by_id'),
    ('shipments', 'created_by_id'),
    ('shipments', 'updated_by_id'),
    ('name_reviews', 'assignee_id'),
    ('name_reviews', 'confirmed_by'),
)

# Past actions: keep display text as 移行元の記録; do not bind actor_id to dest users.
HISTORY_ACTOR_COLUMNS = (
    ('history', 'actor_id', 'actor'),
    ('audit', 'actor_id', 'actor'),
    ('placement_history', 'actor_id', 'actor'),
    ('shipment_history', 'actor_id', 'actor'),
    ('name_review_history', 'actor_id', 'actor_name'),
)

# Order JSON fields that identify people (same numeric id ≠ same person across PCs).
ORDER_USER_ID_KEYS = (
    'assigneeId',
    'authorId',
    'shipWorkerId',
    'barcodeScanById',
    'stockScanById',
    'setScanById',
)

ORDER_NAME_KEYS = {
    'assigneeId': ('owner', 'assigneeName'),
    'authorId': ('authorName',),
    'shipWorkerId': ('shipWorker',),
    'barcodeScanById': ('barcodeScanBy',),
    'stockScanById': ('stockScanBy',),
    'setScanById': ('setScanBy',),
}

_busy = threading.Event()
_busy_lock = threading.RLock()


class MigrationError(ValueError):
    """User-facing migration failure."""


def is_busy():
    return _busy.is_set()


def _enter_busy():
    with _busy_lock:
        if _busy.is_set():
            raise MigrationError('別の移行処理が実行中です。完了してからやり直してください。')
        _busy.set()


def _leave_busy():
    _busy.clear()


def _now_stamp():
    return datetime.now().strftime('%Y%m%d-%H%M%S')


def _iso_now():
    return datetime.now().isoformat(timespec='seconds')


def oserror_message(exc, what='書き込み'):
    """Map common OSError cases to a short Japanese user-facing message."""
    errno = getattr(exc, 'errno', None)
    # ENOSPC / Windows ERROR_DISK_FULL
    if errno in (28, 112) or 'No space' in str(exc) or 'not enough space' in str(exc).lower():
        return f'{what}に失敗しました。ディスクの空き容量を確認してからやり直してください。'
    # EACCES / EPERM
    if errno in (13, 1) or 'Permission' in str(exc) or 'Access is denied' in str(exc):
        return f'{what}に失敗しました。保存先フォルダの書き込み権限を確認してください。'
    return f'{what}に失敗しました。ディスク容量・書き込み権限を確認してからやり直してください。'


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _safe_zip_name(name):
    text = str(name or '').replace('\\', '/').lstrip('/')
    if not text or text.endswith('/') or '..' in text.split('/'):
        raise MigrationError('バックアップ内のパスが不正です。')
    if text.startswith('/') or (len(text) > 1 and text[1] == ':'):
        raise MigrationError('バックアップ内のパスが不正です。')
    return text


def _work_root(data_dir):
    root = Path(data_dir) / '.migration-work'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db_schema_fingerprint(db_path):
    if not Path(db_path).is_file():
        return {'tables': [], 'userVersion': None, 'exists': False}
    c = sqlite3.connect(str(db_path))
    try:
        c.row_factory = sqlite3.Row
        tables = [r['name'] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        user_version = c.execute('PRAGMA user_version').fetchone()[0]
        return {'tables': tables, 'userVersion': user_version, 'exists': True}
    finally:
        c.close()


def _table_counts(db_path, tables=None):
    if not Path(db_path).is_file():
        return {}
    c = sqlite3.connect(str(db_path))
    try:
        c.row_factory = sqlite3.Row
        if tables is None:
            tables = [r['name'] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
        out = {}
        for name in tables:
            try:
                out[name] = c.execute(f'SELECT COUNT(*) AS n FROM "{name}"').fetchone()['n']
            except sqlite3.Error:
                out[name] = None
        return out
    finally:
        c.close()


def _integrity_ok(db_path):
    if not Path(db_path).is_file():
        return True
    try:
        c = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return False
    try:
        row = c.execute('PRAGMA integrity_check').fetchone()
        return bool(row) and row[0] == 'ok'
    except sqlite3.Error:
        return False
    finally:
        c.close()


def _checkpoint_and_close(db_path):
    path = Path(db_path)
    if not path.is_file():
        return
    c = sqlite3.connect(str(path), timeout=30)
    try:
        result = c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if result and result[0]:
            raise MigrationError('データベースが使用中です。ほかの画面の操作を終了してから再実行してください。')
    finally:
        c.close()
    for side in (str(path) + '-wal', str(path) + '-shm'):
        try:
            Path(side).unlink()
        except FileNotFoundError:
            pass


def _online_backup(src_path, dest_path):
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        dest_path.unlink()
    if not Path(src_path).is_file():
        # Create empty placeholder DB so restore always has a stock file slot when desired.
        return False
    # Hold a writer lock on a separate connection so other writers pause, then
    # use the Online Backup API on another connection (BEGIN on the backup
    # connection itself can hang under WAL).
    lock = sqlite3.connect(str(src_path), timeout=30)
    src = sqlite3.connect(str(src_path), timeout=30)
    dst = sqlite3.connect(str(dest_path), timeout=30)
    try:
        lock.execute('BEGIN IMMEDIATE')
        src.backup(dst)
        lock.commit()
    finally:
        dst.close()
        src.close()
        lock.close()
    return True


def _is_image_rel(rel):
    text = str(rel or '').replace('\\', '/').lstrip('/')
    if not text:
        return False
    if any(text == tree or text.startswith(tree + '/') for tree in IMAGE_TREES):
        return True
    return Path(text).suffix.lower() in IMAGE_SUFFIXES


def _iter_business_files(data_dir):
    data_dir = Path(data_dir)
    for rel_root in FILE_TREES:
        root = data_dir / rel_root
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if not path.is_file():
                continue
            if path.name.startswith('.'):
                continue
            rel = path.relative_to(data_dir).as_posix()
            if any(part in SKIP_DIR_NAMES for part in rel.split('/')):
                continue
            if _is_image_rel(rel):
                continue
            yield path, rel
    # Import reports at DATA root (business metadata; not PC config).
    for path in data_dir.glob('*-import-report.json'):
        if path.is_file():
            yield path, path.name


def _required_instruction_tables():
    return {
        'orders', 'history', 'settings', 'users', 'sessions',
        'templates', 'picking_lists',
    }


def _table_names(c):
    return {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def _column_names(c, table):
    return [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]


def _list_public_users(db_path):
    """Public user rows for mapping UI (never includes password/gate)."""
    if not Path(db_path).is_file():
        return []
    c = sqlite3.connect(str(db_path))
    try:
        c.row_factory = sqlite3.Row
        if 'users' not in _table_names(c):
            return []
        cols = set(_column_names(c, 'users'))
        want = [k for k in ('id', 'login', 'name', 'role', 'manage', 'overview', 'active') if k in cols]
        if 'id' not in want:
            return []
        rows = []
        for r in c.execute(f'SELECT {", ".join(want)} FROM users ORDER BY id'):
            item = {k: r[k] for k in want}
            login = str(item.get('login') or '')
            if login.startswith('*name:'):
                item['login'] = ''
                item['nameOnly'] = True
            rows.append(item)
        return rows
    finally:
        c.close()


def _scrub_auth_secrets(db_path):
    """Remove password hashes and auth secrets from a staged instructions DB."""
    c = sqlite3.connect(str(db_path))
    try:
        tables = _table_names(c)
        if 'users' in tables:
            cols = set(_column_names(c, 'users'))
            sets = []
            if 'password' in cols:
                sets.append("password=?")
            params = []
            if 'password' in cols:
                params.append(EXCLUDED_PASSWORD)
            if 'gate' in cols:
                sets.append('gate=NULL')
            if sets:
                c.execute(f"UPDATE users SET {', '.join(sets)}", params)
        for name in ('sessions', 'login_gates', 'auth_attempts'):
            if name in tables:
                c.execute(f'DELETE FROM "{name}"')
        if 'settings' in tables:
            keys = tuple(AUTH_SETTINGS_KEYS)
            if keys:
                placeholders = ','.join('?' for _ in keys)
                c.execute(f'DELETE FROM settings WHERE key IN ({placeholders})', keys)
        c.commit()
    finally:
        c.close()


def _key_counts(instructions_path, stock_path):
    ic = _table_counts(instructions_path)
    sc = _table_counts(stock_path) if Path(stock_path).is_file() else {}
    return {
        'orders': ic.get('orders', 0),
        'users': ic.get('users', 0),
        'templates': ic.get('templates', 0),
        'pickingLists': ic.get('picking_lists', 0),
        'placements': ic.get('placements', 0),
        'shipments': ic.get('shipments', 0),
        'nameFormats': ic.get('name_formats', 0),
        'labelSets': ic.get('label_sets', 0),
        'stockCustomers': sc.get('customers', 0),
        'stockProducts': sc.get('products', 0),
        'stockLots': sc.get('lots', 0),
    }


def summarize_data_dir(data_dir):
    data_dir = Path(data_dir)
    instr = data_dir / INSTRUCTIONS_DB
    stock = data_dir / STOCK_DB
    files = list(_iter_business_files(data_dir))
    return {
        'counts': _key_counts(instr, stock),
        'fileCount': len(files),
        'instructionsSchema': _db_schema_fingerprint(instr),
        'stockSchema': _db_schema_fingerprint(stock),
    }


def _build_export_zip_unlocked(data_dir, dest_zip=None, *, scrub_auth=True):
    """Create a complete migration ZIP. Caller must hold the busy gate.

    scrub_auth=True (USB export): strip password hashes / recovery secrets.
    scrub_auth=False (local pre-restore backup): keep destination auth intact in the ZIP.
    """
    data_dir = Path(data_dir)
    work = None
    final = None
    tmp_zip = None
    try:
        work = Path(tempfile.mkdtemp(prefix='export-', dir=str(_work_root(data_dir))))
        stage = work / 'stage'
        stage.mkdir()
        instr_src = data_dir / INSTRUCTIONS_DB
        stock_src = data_dir / STOCK_DB
        instr_dst = stage / INSTRUCTIONS_DB
        stock_dst = stage / STOCK_DB
        if not instr_src.is_file():
            raise MigrationError('指示書データベースが見つかりません。')
        if not _online_backup(instr_src, instr_dst):
            raise MigrationError('指示書データベースのバックアップに失敗しました。')
        stock_present = _online_backup(stock_src, stock_dst)
        if scrub_auth:
            _scrub_auth_secrets(instr_dst)
        if not _integrity_ok(instr_dst):
            raise MigrationError('指示書データベースの整合性確認に失敗しました。')
        if stock_present and not _integrity_ok(stock_dst):
            raise MigrationError('在庫データベースの整合性確認に失敗しました。')

        file_entries = []
        for src, rel in _iter_business_files(data_dir):
            out = stage / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            digest = _sha256_file(out)
            file_entries.append({'path': rel, 'sha256': digest, 'size': out.stat().st_size})

        source_users = _list_public_users(instr_dst)
        manifest = {
            'formatVersion': FORMAT_VERSION,
            'createdAt': _iso_now(),
            'app': 'instruction-desk',
            'preservesDestinationUsers': True,
            'authSecretsExcluded': bool(scrub_auth),
            'sourceUsers': source_users,
            'databases': {
                'instructions': {
                    'file': INSTRUCTIONS_DB,
                    'sha256': _sha256_file(instr_dst),
                    'schema': _db_schema_fingerprint(instr_dst),
                    'counts': _table_counts(instr_dst),
                },
                'stock': {
                    'file': STOCK_DB,
                    'present': stock_present,
                    'sha256': _sha256_file(stock_dst) if stock_present else None,
                    'schema': _db_schema_fingerprint(stock_dst) if stock_present else {'tables': [], 'userVersion': None, 'exists': False},
                    'counts': _table_counts(stock_dst) if stock_present else {},
                },
            },
            'files': sorted(file_entries, key=lambda x: x['path']),
            'keyCounts': _key_counts(instr_dst, stock_dst if stock_present else Path(stock_dst)),
            'excludes': sorted(SKIP_DIR_NAMES) + [
                'network.json', 'tls/', 'source code', 'mail-out',
                'images/placement', 'images/names', 'image files',
                'password hashes', 'auth secrets', 'destination users (restore preserves)',
            ],
        }
        body = json.dumps({k: v for k, v in manifest.items()}, ensure_ascii=False, indent=2, sort_keys=True).encode('utf-8')
        manifest['integrity'] = {
            'manifestBodySha256': _sha256_bytes(body),
            'instructionsSha256': manifest['databases']['instructions']['sha256'],
            'stockSha256': manifest['databases']['stock']['sha256'],
            'filesSha256': _sha256_bytes('\n'.join(f"{e['sha256']}  {e['path']}" for e in manifest['files']).encode()),
        }
        (stage / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

        stamp = _now_stamp()
        if dest_zip is None:
            final = data_dir / 'backups' / f'{ZIP_PREFIX}-{stamp}.zip'
        else:
            final = Path(dest_zip)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp_zip = final.with_name(final.name + '.partial')
        if tmp_zip.exists():
            tmp_zip.unlink()
        try:
            with zipfile.ZipFile(tmp_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
                for path in stage.rglob('*'):
                    if path.is_file():
                        arc = path.relative_to(stage).as_posix()
                        zf.write(path, arc)
            # Close first so the central directory is finalized, then reopen read-only for testzip.
            with zipfile.ZipFile(tmp_zip, 'r') as zf:
                bad = zf.testzip()
            if bad is not None:
                raise MigrationError(f'ZIPの作成に失敗しました（破損メンバー: {bad}）。')
        except MigrationError:
            raise
        except OSError as e:
            raise MigrationError(oserror_message(e, 'ZIPの作成')) from e
        os.replace(tmp_zip, final)
        tmp_zip = None
        return final, manifest
    except MigrationError:
        if tmp_zip and Path(tmp_zip).exists():
            try:
                Path(tmp_zip).unlink()
            except OSError:
                pass
        raise
    except OSError as e:
        if tmp_zip and Path(tmp_zip).exists():
            try:
                Path(tmp_zip).unlink()
            except OSError:
                pass
        raise MigrationError(oserror_message(e, '移行用バックアップの作成')) from e
    except Exception:
        if tmp_zip and Path(tmp_zip).exists():
            try:
                Path(tmp_zip).unlink()
            except OSError:
                pass
        raise
    finally:
        if work and work.exists():
            shutil.rmtree(work, ignore_errors=True)


def build_export_zip(data_dir, dest_zip=None, *, scrub_auth=True):
    """Create a complete migration ZIP. Returns (zip_path, manifest)."""
    _enter_busy()
    try:
        return _build_export_zip_unlocked(data_dir, dest_zip, scrub_auth=scrub_auth)
    finally:
        _leave_busy()


def _extract_zip_safe(zip_path, dest_dir):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            try:
                bad = zf.testzip()
            except (zipfile.BadZipFile, OSError, RuntimeError, zlib.error) as e:
                raise MigrationError('ZIPが破損しています。') from e
            if bad is not None:
                raise MigrationError('ZIPが破損しています。')
            names = zf.namelist()
            if MANIFEST_NAME not in names and f'./{MANIFEST_NAME}' not in names:
                raise MigrationError('移行用バックアップではありません（manifest.json がありません）。')
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = _safe_zip_name(info.filename)
                if _is_image_rel(name):
                    continue
                target = (dest_dir / name).resolve()
                if dest_dir.resolve() not in target.parents and target != dest_dir.resolve():
                    raise MigrationError('バックアップ内のパスが不正です。')
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(info, 'r') as src, open(target, 'wb') as out:
                        shutil.copyfileobj(src, out)
                except (zipfile.BadZipFile, OSError, RuntimeError, zlib.error) as e:
                    raise MigrationError('ZIPが破損しています。') from e
    except MigrationError:
        raise
    except zipfile.BadZipFile as e:
        raise MigrationError('ZIPが破損しています。') from e
    except zlib.error as e:
        raise MigrationError('ZIPが破損しています。') from e


def inspect_zip(zip_path, data_dir=None):
    """Validate ZIP and return confirmation summary (does not modify DATA)."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise MigrationError('バックアップファイルが見つかりません。')
    work = Path(tempfile.mkdtemp(prefix='inspect-'))
    try:
        _extract_zip_safe(zip_path, work)
        return _validate_extracted(work, data_dir=data_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _warning_for_format(format_version, legacy_auth_present):
    parts = [
        'このパソコン上の業務データ（指示書・在庫・ひな形など）は置き換わります。',
        '利用者アカウント（ログインID・パスワード・権限）は変更しません。',
        '貼付け位置・ネーム見本などの画像はZIPに含まれず、このパソコン上の画像はそのまま残ります。',
        'パソコン固有の設定（通信・起動・Thunderbird）は変わりません。',
    ]
    if int(format_version or 0) == 1 or legacy_auth_present:
        parts.append('このZIPは旧形式です。含まれていてもパスワード等の認証情報は適用しません。')
    return ''.join(parts)


def _zip_has_usable_password_hashes(instr_db):
    """True if ZIP users table still contains verifiable password material (legacy)."""
    c = sqlite3.connect(str(instr_db))
    try:
        if 'users' not in _table_names(c):
            return False
        cols = set(_column_names(c, 'users'))
        if 'password' not in cols:
            return False
        for (pw,) in c.execute('SELECT password FROM users'):
            text = str(pw or '')
            if text and text != EXCLUDED_PASSWORD and ':' in text:
                return True
        return False
    finally:
        c.close()


def _validate_extracted(root, data_dir=None):
    root = Path(root)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise MigrationError('移行用バックアップではありません（manifest.json がありません）。')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        raise MigrationError('manifest.json を読めません。') from e
    format_version = int(manifest.get('formatVersion') or 0)
    if format_version not in SUPPORTED_FORMAT_VERSIONS:
        raise MigrationError(
            f'バックアップ形式が対応していません（形式 {manifest.get("formatVersion")}）。'
            'このソフトの版を揃えてください。何も変更していません。'
        )
    instr = root / INSTRUCTIONS_DB
    if not instr.is_file():
        raise MigrationError('指示書データベースが含まれていません。')
    if not _integrity_ok(instr):
        raise MigrationError('指示書データベースが壊れています。')
    schema = _db_schema_fingerprint(instr)
    missing = sorted(_required_instruction_tables() - set(schema['tables']))
    if missing:
        raise MigrationError('指示書データベースの構成が古い／不正です：' + '、'.join(missing))
    stock = root / STOCK_DB
    stock_meta = manifest.get('databases', {}).get('stock') or {}
    if stock_meta.get('present', stock.is_file()):
        if not stock.is_file():
            raise MigrationError('在庫データベースの記載がありますがファイルがありません。')
        if not _integrity_ok(stock):
            raise MigrationError('在庫データベースが壊れています。')
    expected_instr = (manifest.get('databases', {}).get('instructions') or {}).get('sha256')
    if expected_instr and _sha256_file(instr) != expected_instr:
        raise MigrationError('指示書データベースの改ざん／破損を検出しました。')
    expected_stock = stock_meta.get('sha256')
    if expected_stock and stock.is_file() and _sha256_file(stock) != expected_stock:
        raise MigrationError('在庫データベースの改ざん／破損を検出しました。')
    files_meta = manifest.get('files') or []
    packed_files = []
    for entry in files_meta:
        rel = _safe_zip_name(entry.get('path'))
        if _is_image_rel(rel):
            continue
        path = root / rel
        if not path.is_file():
            raise MigrationError(f'必要なファイルがありません：{rel}')
        if entry.get('sha256') and _sha256_file(path) != entry['sha256']:
            raise MigrationError(f'ファイルの改ざん／破損を検出しました：{rel}')
        packed_files.append(entry)
    # Reject absolute-looking stored paths inside DBs for images (path independence).
    _assert_relative_image_paths(instr)
    source_users = manifest.get('sourceUsers') or _list_public_users(instr)
    legacy_auth = format_version == 1 or _zip_has_usable_password_hashes(instr)
    destination_users = []
    destination_counts = None
    if data_dir is not None:
        data_dir = Path(data_dir)
        destination_users = _list_public_users(data_dir / INSTRUCTIONS_DB)
        destination_counts = summarize_data_dir(data_dir)['counts']
        if source_users and not destination_users:
            raise MigrationError(
                'このパソコンに利用者が登録されていません。'
                '先に利用者を用意してから、割り当てを選んで復元してください。何も変更していません。'
            )
    summary = {
        'createdAt': manifest.get('createdAt'),
        'formatVersion': format_version,
        'keyCounts': manifest.get('keyCounts') or _key_counts(instr, stock),
        'fileCount': len(packed_files),
        'instructionsSchema': schema,
        'stockPresent': stock.is_file(),
        'preservesDestinationUsers': True,
        'legacyFormat': format_version == 1,
        'legacyAuthPresent': legacy_auth,
        'sourceUsers': source_users,
        'destinationUsers': destination_users,
        'destinationCounts': destination_counts,
        'replaces': [
            '指示書', 'ひな形', 'ピッキングリスト', '出荷情報', '貼付け位置（DB）',
            'ネーム形式', '宛名の記憶', '在庫データ', '業務用ファイル（name-import など）',
        ],
        'preserves': [
            '利用者アカウント（ID・ログイン・パスワード・権限・停止状態）',
            '確認用パスワード・復旧コード',
            '表示モードなど利用者ごとの設定',
            '貼付け位置・ネーム見本の画像ファイル',
            '通信・起動・Thunderbird などパソコン固有の設定',
        ],
        'warning': _warning_for_format(format_version, legacy_auth),
    }
    return summary, manifest


def _assert_relative_image_paths(instr_db):
    c = sqlite3.connect(str(instr_db))
    try:
        c.row_factory = sqlite3.Row
        tables = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'placements' in tables:
            for row in c.execute('SELECT id, relative_path, thumb_path FROM placements'):
                for col in ('relative_path', 'thumb_path'):
                    rel = row[col] or ''
                    if not rel:
                        continue
                    if rel.startswith('/') or (len(rel) > 1 and rel[1] == ':') or '..' in rel.replace('\\', '/').split('/'):
                        raise MigrationError(f'貼付け位置画像のパスが不正です（id={row["id"]}）。')
        if 'name_samples' in tables:
            for row in c.execute('SELECT id, relative_path FROM name_samples'):
                rel = row['relative_path'] or ''
                if not rel:
                    continue
                if rel.startswith('/') or (len(rel) > 1 and rel[1] == ':') or '..' in rel.replace('\\', '/').split('/'):
                    raise MigrationError(f'ネーム見本画像のパスが不正です（id={row["id"]}）。')
    finally:
        c.close()


def _snapshot_dest_auth(data_dir):
    """Capture destination users and auth-related rows before DB swap."""
    instr = Path(data_dir) / INSTRUCTIONS_DB
    if not instr.is_file():
        raise MigrationError('このパソコンの指示書データベースが見つかりません。')
    c = sqlite3.connect(str(instr))
    try:
        c.row_factory = sqlite3.Row
        tables = _table_names(c)
        if 'users' not in tables:
            raise MigrationError('このパソコンに利用者テーブルがありません。')
        user_cols = _column_names(c, 'users')
        users = [dict(r) for r in c.execute(f'SELECT {", ".join(user_cols)} FROM users ORDER BY id')]
        if not users:
            raise MigrationError(
                'このパソコンに利用者が登録されていません。'
                '先に利用者を用意してから復元してください。何も変更していません。'
            )
        display_modes = []
        if 'user_display_modes' in tables:
            dm_cols = _column_names(c, 'user_display_modes')
            display_modes = [dict(r) for r in c.execute(
                f'SELECT {", ".join(dm_cols)} FROM user_display_modes'
            )]
        recovery = None
        if 'settings' in tables:
            row = c.execute("SELECT value FROM settings WHERE key='recovery'").fetchone()
            if row:
                recovery = row['value']
        return {
            'userColumns': user_cols,
            'users': users,
            'displayModes': display_modes,
            'recovery': recovery,
        }
    finally:
        c.close()


def normalize_user_map(user_map, source_users, destination_users):
    """Validate source→destination id map. Keys may be str or int."""
    if not source_users:
        return {}
    if not isinstance(user_map, dict) or not user_map:
        raise MigrationError('移行元の利用者の割り当てを指定してください。何も変更していません。')
    source_ids = {int(u['id']) for u in source_users}
    dest_ids = {int(u['id']) for u in destination_users}
    if not dest_ids:
        raise MigrationError(
            'このパソコンに利用者が登録されていません。'
            '先に利用者を用意してから復元してください。何も変更していません。'
        )
    normalized = {}
    try:
        for raw_src, raw_dst in user_map.items():
            sid = int(raw_src)
            did = int(raw_dst)
            if sid not in source_ids:
                raise MigrationError(f'移行元にない利用者番号です：{sid}')
            if did not in dest_ids:
                raise MigrationError(f'このパソコンにない利用者への割り当てです：{did}')
            normalized[sid] = did
    except (TypeError, ValueError) as e:
        raise MigrationError('利用者の割り当てが正しくありません。何も変更していません。') from e
    missing = sorted(source_ids - set(normalized))
    if missing:
        raise MigrationError('すべての移行元利用者の割り当てを選んでください。何も変更していません。')
    return normalized


def _annotate_source_actor(text):
    text = str(text or '').strip()
    if not text:
        return '移行元の記録'
    if text.startswith('移行元の記録'):
        return text
    return f'移行元の記録：{text}'


def _map_optional_id(value, user_map):
    if value is None or value == '':
        return value
    try:
        sid = int(value)
    except (TypeError, ValueError):
        return value
    if sid in user_map:
        return user_map[sid]
    return None


def _remap_order_payload(payload, user_map, dest_names):
    if not isinstance(payload, dict):
        return payload
    for key in ORDER_USER_ID_KEYS:
        if key not in payload:
            continue
        mapped = _map_optional_id(payload.get(key), user_map)
        if mapped is None and payload.get(key) not in (None, ''):
            payload[key] = None
            for name_key in ORDER_NAME_KEYS.get(key, ()):
                if name_key in payload:
                    payload[name_key] = ''
            continue
        if mapped is None:
            continue
        payload[key] = mapped
        name = dest_names.get(mapped) or ''
        for name_key in ORDER_NAME_KEYS.get(key, ()):
            payload[name_key] = name
    return payload


def _apply_dest_users_and_remap(live_instr, auth_snapshot, user_map, actor, manifest, auto_zip):
    """Replace imported users with destination auth and remap ownership IDs."""
    c = sqlite3.connect(str(live_instr), timeout=30)
    try:
        c.row_factory = sqlite3.Row
        tables = _table_names(c)
        dest_names = {int(u['id']): u.get('name') or '' for u in auth_snapshot['users']}

        # 1) Ownership columns → destination IDs
        for table, column in OWNERSHIP_COLUMNS:
            if table not in tables:
                continue
            cols = set(_column_names(c, table))
            if column not in cols:
                continue
            for sid, did in user_map.items():
                c.execute(f'UPDATE "{table}" SET "{column}"=? WHERE "{column}"=?', (did, sid))
            # Clear nullable creator-like fields that still point at unknown source ids.
            if column in ('created_by_id', 'updated_by_id', 'assignee_id', 'confirmed_by'):
                dest_id_list = list(dest_names)
                if dest_id_list:
                    marks = ','.join('?' for _ in dest_id_list)
                    c.execute(
                        f'UPDATE "{table}" SET "{column}"=NULL '
                        f'WHERE "{column}" IS NOT NULL AND "{column}" NOT IN ({marks})',
                        dest_id_list,
                    )

        # templates / label_sets / picking_lists require valid user ids — remap already applied.
        # If a NOT NULL column somehow still has a source id, map orphans to any mapped dest via second pass.
        for table, column in (('templates', 'user_id'), ('label_sets', 'user_id'), ('picking_lists', 'creator')):
            if table not in tables:
                continue
            cols = set(_column_names(c, table))
            if column not in cols:
                continue
            dest_id_list = list(dest_names)
            if not dest_id_list:
                continue
            marks = ','.join('?' for _ in dest_id_list)
            orphans = c.execute(
                f'SELECT DISTINCT "{column}" AS uid FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL AND "{column}" NOT IN ({marks})',
                dest_id_list,
            ).fetchall()
            if orphans:
                raise MigrationError(
                    '利用者の割り当て後も、移行データに未割り当ての担当が残っています。'
                    '割り当てを確認してやり直してください。'
                )

        # 2) Orders JSON assignee/author/worker ids
        if 'orders' in tables:
            for row in c.execute('SELECT id, payload FROM orders'):
                try:
                    payload = json.loads(row['payload'])
                except json.JSONDecodeError:
                    continue
                updated = _remap_order_payload(payload, user_map, dest_names)
                c.execute(
                    'UPDATE orders SET payload=? WHERE id=?',
                    (json.dumps(updated, ensure_ascii=False), row['id']),
                )

        # 3) History / audit: keep text as 移行元の記録; clear actor_id binding
        for table, id_col, text_col in HISTORY_ACTOR_COLUMNS:
            if table not in tables:
                continue
            cols = set(_column_names(c, table))
            if id_col not in cols:
                continue
            if text_col in cols:
                rows = c.execute(f'SELECT rowid AS rid, "{text_col}" AS actor FROM "{table}"').fetchall()
                for row in rows:
                    c.execute(
                        f'UPDATE "{table}" SET "{id_col}"=NULL, "{text_col}"=? WHERE rowid=?',
                        (_annotate_source_actor(row['actor']), row['rid']),
                    )
            else:
                c.execute(f'UPDATE "{table}" SET "{id_col}"=NULL')

        # 4) Restore destination users (full replace of imported users table)
        if 'users' not in tables:
            raise MigrationError('復元先データベースに利用者テーブルがありません。')
        live_cols = _column_names(c, 'users')
        c.execute('DELETE FROM users')
        for user in auth_snapshot['users']:
            values = []
            cols_used = []
            for col in live_cols:
                if col in user:
                    cols_used.append(col)
                    values.append(user[col])
            if 'id' not in cols_used:
                raise MigrationError('利用者データの形式が不正です。')
            placeholders = ','.join('?' for _ in cols_used)
            col_sql = ','.join(f'"{c}"' for c in cols_used)
            c.execute(f'INSERT INTO users({col_sql}) VALUES({placeholders})', values)

        # 5) Auth sessions / gates — force re-login; keep dest recovery + display modes
        if 'sessions' in tables:
            c.execute('DELETE FROM sessions')
        if 'login_gates' in tables:
            c.execute('DELETE FROM login_gates')
        if 'auth_attempts' in tables:
            c.execute('DELETE FROM auth_attempts')

        if 'user_display_modes' in tables:
            c.execute('DELETE FROM user_display_modes')
            dm_cols = _column_names(c, 'user_display_modes')
            for row in auth_snapshot.get('displayModes') or []:
                cols_used = [col for col in dm_cols if col in row]
                if not cols_used:
                    continue
                placeholders = ','.join('?' for _ in cols_used)
                col_sql = ','.join(f'"{c}"' for c in cols_used)
                c.execute(
                    f'INSERT INTO user_display_modes({col_sql}) VALUES({placeholders})',
                    [row[col] for col in cols_used],
                )

        if 'settings' in tables:
            if auth_snapshot.get('recovery') is not None:
                c.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES('recovery',?)",
                    (auth_snapshot['recovery'],),
                )
            else:
                c.execute("DELETE FROM settings WHERE key='recovery'")

        # 6) Restore audit entry (destination actor)
        actor_id = None
        actor_label = '移行復元'
        if actor:
            actor_id = actor.get('id')
            actor_label = f"{actor.get('name')} ({actor.get('login')})"
        if 'audit' in tables:
            c.execute(
                "INSERT INTO audit(at,actor_id,actor,action,target) VALUES(datetime('now','localtime'),?,?,?,?)",
                (
                    actor_id,
                    actor_label,
                    '移行バックアップから復元（利用者は保持）',
                    f"createdAt={manifest.get('createdAt')}; auto={auto_zip.name if auto_zip else ''}; map={len(user_map)}",
                ),
            )
        c.commit()
    finally:
        c.close()


def restore_from_zip(data_dir, zip_path, actor=None, auto_backup_dir=None, user_map=None):
    """Replace business data from a validated migration ZIP.

    Destination users/auth are preserved. Ownership fields are remapped using
    user_map (source user id → destination user id). Creates an automatic
    backup of the destination first. On failure, rolls back to the pre-restore
    snapshot. Invalidates sessions after success.
    """
    data_dir = Path(data_dir)
    zip_path = Path(zip_path)
    _enter_busy()
    incoming = None
    rollback = None
    auto_zip = None
    try:
        # Validate ZIP + mapping before any destination rewrite.
        incoming = Path(tempfile.mkdtemp(prefix='incoming-', dir=str(_work_root(data_dir))))
        _extract_zip_safe(zip_path, incoming)
        summary, manifest = _validate_extracted(incoming, data_dir=data_dir)
        source_users = summary['sourceUsers']
        destination_users = summary['destinationUsers']
        normalized_map = normalize_user_map(user_map, source_users, destination_users)
        auth_snapshot = _snapshot_dest_auth(data_dir)

        # Auto-backup current destination (keep auth secrets for local recovery).
        backup_root = Path(auto_backup_dir) if auto_backup_dir else (data_dir / 'backups')
        backup_root.mkdir(parents=True, exist_ok=True)
        auto_zip, _ = _build_export_zip_unlocked(
            data_dir,
            backup_root / f'pre-restore-{_now_stamp()}.zip',
            scrub_auth=False,
        )

        # Quiesce live DBs (checkpoint WAL) before swapping files.
        live_instr = data_dir / INSTRUCTIONS_DB
        live_stock = data_dir / STOCK_DB
        import gc
        gc.collect()  # Release unreachable SQLite connections before Windows file operations.
        try:
            _checkpoint_and_close(live_instr)
            _checkpoint_and_close(live_stock)
        except OSError as e:
            raise MigrationError('データベースが使用中のため復元を中止しました。業務データは置き換えていません。ほかの画面の操作を終了し、再起動後に再実行してください。') from e

        stamp = _now_stamp()
        rollback = data_dir / f'.migration-rollback-{stamp}'
        rollback.mkdir(parents=True, exist_ok=True)

        def park(rel):
            src = data_dir / rel
            if src.exists():
                dest = rollback / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dest)

        parked = []
        installed_files = []
        installed_dbs = []
        try:
            for rel in (
                INSTRUCTIONS_DB, STOCK_DB,
                f'{INSTRUCTIONS_DB}-wal', f'{INSTRUCTIONS_DB}-shm',
                f'{STOCK_DB}-wal', f'{STOCK_DB}-shm',
            ):
                src = data_dir / rel
                if src.exists():
                    park(rel)
                    parked.append(rel)
            for path in list(data_dir.glob('*-import-report.json')):
                park(path.name)
                parked.append(path.name)

            # Install DBs. Image trees are left on the destination as-is.
            os.replace(incoming / INSTRUCTIONS_DB, live_instr)
            installed_dbs.append(INSTRUCTIONS_DB)
            if (incoming / STOCK_DB).is_file():
                os.replace(incoming / STOCK_DB, live_stock)
                installed_dbs.append(STOCK_DB)
            for src, rel in _iter_business_files(incoming):
                dest = data_dir / rel
                if dest.exists():
                    park(rel)
                    parked.append(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                installed_files.append(rel)

            _apply_dest_users_and_remap(
                live_instr, auth_snapshot, normalized_map, actor, manifest, auto_zip,
            )

            shutil.rmtree(rollback, ignore_errors=True)
            rollback = None
        except Exception as e:
            # Only remove files actually installed by this attempt, never originals
            # whose initial move failed (for example a Windows file lock).
            for db_name in installed_dbs:
                _checkpoint_and_close(data_dir / db_name)
            for rel in [*installed_dbs, *installed_files]:
                cur = data_dir / rel
                if cur.is_file():
                    cur.unlink()
            if rollback and rollback.exists():
                for rel in parked:
                    parked_path = rollback / rel
                    if parked_path.exists():
                        dest = data_dir / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(parked_path, dest)
            if isinstance(e, MigrationError):
                raise MigrationError(f'復元に失敗したため、復元前の状態に戻しました。{e}') from e
            raise MigrationError(f'復元に失敗したため、復元前の状態に戻しました。{e}') from e

        post_counts = summarize_data_dir(data_dir)['counts']
        return {
            'ok': True,
            'createdAt': summary['createdAt'],
            'keyCounts': post_counts,
            'autoBackup': auto_zip.name if auto_zip else None,
            'preservesDestinationUsers': True,
            'userMapCount': len(normalized_map),
            'restartSteps': [
                '画面をいったん閉じてください（ログアウトされます）。',
                '会社PCで「停止.cmd」があれば実行し、続けて「起動.cmd」で起動し直してください。',
                'このパソコンにもともとあったログインIDとパスワードで、もう一度ログインしてください（移行元の利用者は追加されません）。',
                '割り当てた担当で指示書・ひな形が開けるか、数件確認してください。',
                '貼付け位置・ネーム見本の画像はこのZIPに含まれません。',
            ],
        }
    finally:
        if incoming and Path(incoming).exists():
            shutil.rmtree(incoming, ignore_errors=True)
        _leave_busy()


def owner_guard(user):
    if not user:
        raise MigrationError('ログインしてください。')
    if user['role'] != 'owner':
        raise MigrationError('移行用バックアップは主管理者だけが使用できます。')
