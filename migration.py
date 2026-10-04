"""Owner-only full business-data export / restore for USB migration.

PC-specific config (network.json, tls/, Thunderbird path, launch paths) stays
outside the ZIP. Business DBs and related files under INSTRUCTION_DATA move together.
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

FORMAT_VERSION = 1
MANIFEST_NAME = 'manifest.json'
INSTRUCTIONS_DB = 'instructions.sqlite3'
STOCK_DB = 'stock.sqlite3'
ZIP_PREFIX = 'desk-migration'

# Directories under DATA that hold business files (relative paths in ZIP).
FILE_TREES = (
    'images/placement',
    'images/names',
    'name-import',
)

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
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
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
            if any(part in SKIP_DIR_NAMES for part in path.relative_to(data_dir).parts):
                continue
            rel = path.relative_to(data_dir).as_posix()
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


def _build_export_zip_unlocked(data_dir, dest_zip=None):
    """Create a complete migration ZIP. Caller must hold the busy gate."""
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

        manifest = {
            'formatVersion': FORMAT_VERSION,
            'createdAt': _iso_now(),
            'app': 'instruction-desk',
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
            'excludes': sorted(SKIP_DIR_NAMES) + ['network.json', 'tls/', 'source code', 'mail-out'],
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
        with zipfile.ZipFile(tmp_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for path in stage.rglob('*'):
                if path.is_file():
                    arc = path.relative_to(stage).as_posix()
                    zf.write(path, arc)
            bad = zf.testzip()
            if bad is not None:
                raise MigrationError('ZIPの作成に失敗しました。')
        os.replace(tmp_zip, final)
        tmp_zip = None
        return final, manifest
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


def build_export_zip(data_dir, dest_zip=None):
    """Create a complete migration ZIP. Returns (zip_path, manifest)."""
    _enter_busy()
    try:
        return _build_export_zip_unlocked(data_dir, dest_zip)
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


def inspect_zip(zip_path):
    """Validate ZIP and return confirmation summary (does not modify DATA)."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise MigrationError('バックアップファイルが見つかりません。')
    work = Path(tempfile.mkdtemp(prefix='inspect-'))
    try:
        _extract_zip_safe(zip_path, work)
        return _validate_extracted(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _validate_extracted(root):
    root = Path(root)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise MigrationError('移行用バックアップではありません（manifest.json がありません）。')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        raise MigrationError('manifest.json を読めません。') from e
    if int(manifest.get('formatVersion') or 0) != FORMAT_VERSION:
        raise MigrationError(f"バックアップ形式が対応していません（形式 {manifest.get('formatVersion')}）。このソフトの版を揃えてください。")
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
    for entry in files_meta:
        rel = _safe_zip_name(entry.get('path'))
        path = root / rel
        if not path.is_file():
            raise MigrationError(f'必要なファイルがありません：{rel}')
        if entry.get('sha256') and _sha256_file(path) != entry['sha256']:
            raise MigrationError(f'ファイルの改ざん／破損を検出しました：{rel}')
    # Reject absolute-looking stored paths inside DBs for images (path independence).
    _assert_relative_image_paths(instr)
    summary = {
        'createdAt': manifest.get('createdAt'),
        'formatVersion': manifest.get('formatVersion'),
        'keyCounts': manifest.get('keyCounts') or _key_counts(instr, stock),
        'fileCount': len(files_meta),
        'instructionsSchema': schema,
        'stockPresent': stock.is_file(),
        'warning': 'このパソコン上の業務データはすべて置き換わります。利用者・指示書・在庫・画像も含みます。パソコン固有の設定（通信・起動・Thunderbird）は変わりません。',
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


def _replace_tree(src, dest):
    dest = Path(dest)
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    if Path(src).exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)


def restore_from_zip(data_dir, zip_path, actor=None, auto_backup_dir=None):
    """Replace business data from a validated migration ZIP.

    Creates an automatic backup of the destination first. On failure, rolls back
    to the pre-restore snapshot. Invalidates sessions after success.
    """
    data_dir = Path(data_dir)
    zip_path = Path(zip_path)
    _enter_busy()
    incoming = None
    rollback = None
    auto_zip = None
    try:
        # Auto-backup current destination before any replace (same busy gate).
        backup_root = Path(auto_backup_dir) if auto_backup_dir else (data_dir / 'backups')
        backup_root.mkdir(parents=True, exist_ok=True)
        auto_zip, _ = _build_export_zip_unlocked(data_dir, backup_root / f'pre-restore-{_now_stamp()}.zip')

        incoming = Path(tempfile.mkdtemp(prefix='incoming-', dir=str(_work_root(data_dir))))
        _extract_zip_safe(zip_path, incoming)
        summary, manifest = _validate_extracted(incoming)

        # Quiesce live DBs (checkpoint WAL) before swapping files.
        live_instr = data_dir / INSTRUCTIONS_DB
        live_stock = data_dir / STOCK_DB
        _checkpoint_and_close(live_instr)
        _checkpoint_and_close(live_stock)

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
        try:
            for rel in (
                INSTRUCTIONS_DB, STOCK_DB,
                f'{INSTRUCTIONS_DB}-wal', f'{INSTRUCTIONS_DB}-shm',
                f'{STOCK_DB}-wal', f'{STOCK_DB}-shm',
                *FILE_TREES,
            ):
                src = data_dir / rel
                if src.exists():
                    park(rel)
                    parked.append(rel)
            for path in list(data_dir.glob('*-import-report.json')):
                park(path.name)
                parked.append(path.name)

            # Install new DBs / files.
            os.replace(incoming / INSTRUCTIONS_DB, live_instr)
            if (incoming / STOCK_DB).is_file():
                os.replace(incoming / STOCK_DB, live_stock)
            for rel_root in FILE_TREES:
                src = incoming / rel_root
                if src.exists():
                    dest = data_dir / rel_root
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(src, dest)
            for path in incoming.glob('*-import-report.json'):
                shutil.copy2(path, data_dir / path.name)

            # Invalidate sessions / gates; append restore audit.
            c = sqlite3.connect(str(live_instr), timeout=30)
            try:
                c.row_factory = sqlite3.Row
                tables = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'sessions' in tables:
                    c.execute('DELETE FROM sessions')
                if 'login_gates' in tables:
                    c.execute('DELETE FROM login_gates')
                if 'auth_attempts' in tables:
                    c.execute('DELETE FROM auth_attempts')
                actor_id = None
                actor_label = '移行復元'
                if actor:
                    actor_id = actor.get('id')
                    actor_label = f"{actor.get('name')} ({actor.get('login')})"
                if 'audit' in tables:
                    c.execute(
                        "INSERT INTO audit(at,actor_id,actor,action,target) VALUES(datetime('now','localtime'),?,?,?,?)",
                        (actor_id, actor_label, '移行バックアップから復元', f"createdAt={manifest.get('createdAt')}; auto={auto_zip.name if auto_zip else ''}"),
                    )
                c.commit()
            finally:
                c.close()

            shutil.rmtree(rollback, ignore_errors=True)
            rollback = None
        except Exception as e:
            _checkpoint_and_close(live_instr)
            _checkpoint_and_close(live_stock)
            # Remove partially installed business targets, then put parked data back.
            for rel in (
                INSTRUCTIONS_DB, STOCK_DB,
                f'{INSTRUCTIONS_DB}-wal', f'{INSTRUCTIONS_DB}-shm',
                f'{STOCK_DB}-wal', f'{STOCK_DB}-shm',
                *FILE_TREES,
            ):
                cur = data_dir / rel
                if cur.exists():
                    if cur.is_dir():
                        shutil.rmtree(cur, ignore_errors=True)
                    else:
                        try:
                            cur.unlink()
                        except OSError:
                            pass
            for path in list(data_dir.glob('*-import-report.json')):
                try:
                    path.unlink()
                except OSError:
                    pass
            if rollback and rollback.exists():
                for rel in parked:
                    parked_path = rollback / rel
                    if parked_path.exists():
                        dest = data_dir / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(parked_path, dest)
            raise MigrationError(f'復元に失敗したため、復元前の状態に戻しました。{e}') from e

        return {
            'ok': True,
            'createdAt': summary['createdAt'],
            'keyCounts': summary['keyCounts'],
            'autoBackup': auto_zip.name if auto_zip else None,
            'restartSteps': [
                '画面をいったん閉じてください（ログアウトされます）。',
                '会社PCで「停止.cmd」があれば実行し、続けて「起動.cmd」で起動し直してください。',
                '移行前と同じログインIDとパスワードで、もう一度ログインしてください。',
                '貼付け位置・ネーム見本・在庫の表示を数件確認してください。',
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
