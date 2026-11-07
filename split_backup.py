"""管理・修正の分割バックアップ／復元（メニュー単位の ZIP）。

区分（アプリのメニューに合わせる）:
  desk     指示書デスク     … 指示書・履歴・ひな形・修正依頼・ラベル・ピッキング・持出し・その他の設定
  customer 顧客・商品情報   … 貼付け位置・出荷情報・ネーム情報（画像・name-import を含む）
  stock    在庫管理         … stock.sqlite3 全体
  mark     マーク管理       … kintai_*（勤怠・シフト・備品）
  users    利用者管理       … users / login_gates / user_display_modes / audit / recovery 設定

会社PCで取った ZIP を自宅PCで復元する（逆もある）前提:
- 復元はその区分の表・ファイルだけを置き換える（1トランザクション、移行ロック中）。
- 復元前に自動で安全バックアップを data/backups/ へ作る。
- 列がこのPCに無い／必須列が ZIP に無いときは何も変えずに中止する。
- 利用者ID（作成者・担当など）はログインIDで照合して付け替える。照合できない ID は
  復元した主管理者へ寄せる（NULL 可の列でも同じ。記録が消えないようにするため）。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import migration

FORMAT = 'desk-split-backup'
FORMAT_VERSION = 1
DB_NAME = 'data.sqlite3'
STOCK_NAME = 'stock.sqlite3'

DESK, CUSTOMER, STOCK, MARK, USERS = 'desk', 'customer', 'stock', 'mark', 'users'

CATEGORIES = {
    DESK: {
        'label': '指示書デスク',
        'tables': (
            'orders', 'history', 'templates', 'template_repairs', 'placement_corrections',
            'label_sets', 'fixed_label_formats', 'picking_lists', 'picking_sources', 'picking_history',
            'carry_inputs', 'carry_checks', 'carry_prints',
        ),
        'files': (),
    },
    CUSTOMER: {
        'label': '顧客・商品情報（貼付け位置・出荷情報・ネーム情報）',
        'tables': (
            'placements', 'placement_history', 'shipments', 'shipment_history',
            'name_customers', 'name_formats', 'name_samples', 'name_reviews',
            'name_review_history', 'name_exe_downloads',
        ),
        'files': ('images/placement', 'images/names', 'name-import'),
    },
    STOCK: {'label': '在庫管理', 'tables': (), 'files': ()},
    MARK: {'label': 'マーク管理（勤怠・シフト・備品）', 'tables': (), 'prefix': 'kintai_', 'files': ()},
    USERS: {
        'label': '利用者管理',
        'tables': ('users', 'login_gates', 'user_display_modes', 'audit'),
        'files': (),
    },
}
ORDER = (DESK, CUSTOMER, STOCK, MARK, USERS)

# バックアップしない（一時的なログイン情報）
SKIP_TABLES = {'sessions', 'auth_attempts', 'sqlite_sequence'}

CUSTOMER_SETTING_KEYS = {'placement_import', 'shipment_import', 'name_import'}
USERS_SETTING_KEYS = {'recovery'}

# 利用者IDを持つ列（ログインIDで付け替える）
USER_ID_COLUMN_NAMES = {'user_id', 'creator', 'actor_id', 'author_id'}


class SplitBackupError(Exception):
    pass


def _now():
    return datetime.now().strftime('%Y%m%d-%H%M%S')


def _customer_setting_keys():
    keys = set(CUSTOMER_SETTING_KEYS)
    try:
        import names
        for k, v in vars(names).items():
            if k.endswith('_SETTINGS_KEY') and isinstance(v, str):
                keys.add(v)
    except Exception:
        pass
    return keys


def setting_category(key):
    if key in USERS_SETTING_KEYS:
        return USERS
    if key in _customer_setting_keys():
        return CUSTOMER
    return DESK


def table_category(table):
    """表 → 区分。どこにも書かれていない表は最も近いメニューへ（kintai_ → マーク管理、他 → 指示書デスク）。"""
    if table in SKIP_TABLES or table == 'settings':
        return None
    for key in ORDER:
        if table in CATEGORIES[key]['tables']:
            return key
    if table.startswith('kintai_'):
        return MARK
    return DESK


def _tables(c):
    return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def category_tables(c, category):
    return [t for t in _tables(c) if table_category(t) == category]


def unplaced_tables(c):
    """明示の区分表に無く、既定の振り分けになった表（報告用）。"""
    listed = {t for k in ORDER for t in CATEGORIES[k]['tables']}
    return [t for t in _tables(c)
            if t not in listed and t not in SKIP_TABLES and t != 'settings'
            and not t.startswith('kintai_')]


def _cols(c, table):
    return [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]


def _user_columns(c, table):
    if table_category(table) == USERS:
        return []
    return [col for col in _cols(c, table)
            if col in USER_ID_COLUMN_NAMES or col.endswith('_by_id') or col.endswith('_user_id')]


def _users_list(c):
    if 'users' not in _tables(c):
        return []
    rows = c.execute('SELECT id, login, name, role, active FROM users').fetchall()
    return [{'id': r[0], 'login': r[1], 'name': r[2], 'role': r[3], 'active': r[4]} for r in rows]


# ------------------------------------------------------------------ export

def _copy_tables(src_path, dest_path, tables, settings_category=None):
    """src の指定表（＋該当設定キー）だけを dest へ。"""
    out = sqlite3.connect(str(dest_path))
    try:
        out.execute('ATTACH DATABASE ? AS src', (str(src_path),))
        meta = {}
        for t in tables:
            sql = out.execute("SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
            out.execute(sql)
            out.execute(f'INSERT INTO main."{t}" SELECT * FROM src."{t}"')
            meta[t] = out.execute(f'SELECT COUNT(*) FROM main."{t}"').fetchone()[0]
        if settings_category:
            out.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
            rows = out.execute('SELECT key, value FROM src.settings').fetchall() if 'settings' in [
                r[0] for r in out.execute("SELECT name FROM src.sqlite_master WHERE type='table'")] else []
            keep = [(k, v) for k, v in rows if setting_category(k) == settings_category]
            out.executemany('INSERT INTO settings VALUES(?,?)', keep)
            meta['settings'] = len(keep)
        out.commit()
        out.execute('DETACH DATABASE src')
        return meta
    finally:
        out.close()


def _iter_files(data_dir, rel_tree):
    root = Path(data_dir) / rel_tree
    if not root.exists():
        return
    for p in sorted(root.rglob('*')):
        if p.is_file() and not p.name.startswith('.'):
            yield p, str(Path(rel_tree) / p.relative_to(root)).replace('\\', '/')


def export_category(category, data_dir, *, actor=None):
    """区分の ZIP を data/backups/ に作り、Path を返す。"""
    if category not in CATEGORIES:
        raise SplitBackupError('バックアップの種類が不明です。')
    data_dir = Path(data_dir)
    folder = data_dir / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _now()
    dest = folder / f'desk-{category}-{stamp}.zip'
    instr = data_dir / migration.INSTRUCTIONS_DB
    work = Path(tempfile.mkdtemp(prefix='split-bk-', dir=str(folder)))
    try:
        snap = work / 'snap.sqlite3'
        migration._online_backup(instr, snap)
        sc = sqlite3.connect(str(snap))
        try:
            users = _users_list(sc)
            tables = category_tables(sc, category) if category != STOCK else []
        finally:
            sc.close()
        manifest = {
            'format': FORMAT, 'formatVersion': FORMAT_VERSION, 'category': category,
            'label': CATEGORIES[category]['label'], 'createdAt': datetime.now().isoformat(timespec='seconds'),
            'createdBy': (actor or {}).get('login') or '', 'sourceUsers': users, 'counts': {}, 'files': [],
        }
        with zipfile.ZipFile(dest, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            if category == STOCK:
                stock_db = data_dir / migration.STOCK_DB
                if not stock_db.exists():
                    raise SplitBackupError('在庫管理のデータベースがありません。')
                part = work / STOCK_NAME
                migration._online_backup(stock_db, part)
                zf.write(part, STOCK_NAME)
                manifest['counts'] = {'stock.sqlite3': part.stat().st_size}
            else:
                part = work / DB_NAME
                manifest['counts'] = _copy_tables(
                    snap, part, tables,
                    settings_category=category if category in (DESK, CUSTOMER, USERS) else None)
                zf.write(part, DB_NAME)
            for rel_tree in CATEGORIES[category]['files']:
                for p, arc in _iter_files(data_dir, rel_tree):
                    zf.write(p, 'files/' + arc)
                    manifest['files'].append(arc)
            zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=1))
        return dest, manifest
    except Exception:
        try: dest.unlink()
        except OSError: pass
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ restore

def _read_zip(zip_path, work):
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if 'manifest.json' not in names:
            raise SplitBackupError('分割バックアップのZIPではありません（manifest.json がありません）。')
        manifest = json.loads(zf.read('manifest.json').decode('utf-8'))
        if manifest.get('format') != FORMAT:
            raise SplitBackupError('分割バックアップのZIPではありません。')
        if int(manifest.get('formatVersion') or 0) > FORMAT_VERSION:
            raise SplitBackupError('このPCのアプリより新しい形式のZIPです。アプリを更新してから復元してください。')
        for n in names:
            p = Path(n)
            if p.is_absolute() or '..' in p.parts:
                raise SplitBackupError('ZIPに不正なパスが含まれています。')
        zf.extractall(work)
    return manifest


def inspect(zip_path):
    work = Path(tempfile.mkdtemp(prefix='split-inspect-'))
    try:
        m = _read_zip(zip_path, work)
        return {'category': m.get('category'), 'label': m.get('label'), 'createdAt': m.get('createdAt'),
                'createdBy': m.get('createdBy'), 'counts': m.get('counts') or {}, 'fileCount': len(m.get('files') or [])}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _check_schema(live, src, tables):
    """ZIP の表をこのPCへ入れられるか。列の過不足を確認し、問題を日本語で返す。"""
    problems = []
    live_tables = set(_tables(live))
    for t in tables:
        if t not in live_tables:
            problems.append(f'表「{t}」がこのPCにありません（アプリが古い可能性があります）。')
            continue
        src_cols = _cols(src, t)
        info = {r[1]: r for r in live.execute(f'PRAGMA table_info("{t}")')}
        extra = [x for x in src_cols if x not in info]
        if extra:
            problems.append(f'表「{t}」の列 {", ".join(extra)} がこのPCにありません（アプリを更新してください）。')
        for name, r in info.items():
            notnull, default, pk = r[3], r[4], r[5]
            if name not in src_cols and notnull and default is None and not pk:
                problems.append(f'表「{t}」の必須列「{name}」がZIPにありません（ZIP側のアプリが古い可能性があります）。')
    return problems


def _user_map(source_users, dest_users, fallback_id):
    m = migration.match_users_by_login(source_users, dest_users)['matches']
    return {int(k): int(v) for k, v in m.items()}, fallback_id


def _remap_value(v, umap, fallback):
    if v is None or v == '':
        return v
    try:
        sid = int(v)
    except (TypeError, ValueError):
        return v
    return umap.get(sid, fallback)


def _remap_tables(c, tables, umap, fallback, dest_names):
    for t in tables:
        cols = _user_columns(c, t)
        if cols:
            for col in cols:
                rows = c.execute(f'SELECT rowid, "{col}" FROM "{t}" WHERE "{col}" IS NOT NULL').fetchall()
                for rid, v in rows:
                    nv = _remap_value(v, umap, fallback)
                    if nv != v:
                        c.execute(f'UPDATE "{t}" SET "{col}"=? WHERE rowid=?', (nv, rid))
        if t in ('orders', 'templates', 'picking_lists') and 'payload' in _cols(c, t):
            for rid, payload in c.execute(f'SELECT rowid, payload FROM "{t}"').fetchall():
                try:
                    d = json.loads(payload)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                before = json.dumps(d, ensure_ascii=False, sort_keys=True)
                for key in migration.ORDER_USER_ID_KEYS:
                    if d.get(key) in (None, ''):
                        continue
                    nv = _remap_value(d.get(key), umap, fallback)
                    if nv != d.get(key):
                        d[key] = nv
                        for nk in migration.ORDER_NAME_KEYS.get(key, ()):
                            if nk in d:
                                d[nk] = dest_names.get(nv, d[nk])
                if json.dumps(d, ensure_ascii=False, sort_keys=True) != before:
                    c.execute(f'UPDATE "{t}" SET payload=? WHERE rowid=?', (json.dumps(d, ensure_ascii=False), rid))


def _safety_backup(data_dir, category):
    folder = Path(data_dir) / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _now()
    out = []
    if category == STOCK:
        src = Path(data_dir) / migration.STOCK_DB
        if src.exists():
            dest = folder / f'pre-restore-stock-{stamp}.sqlite3'
            migration._online_backup(src, dest)
            out.append(dest.name)
    else:
        dest = folder / f'pre-restore-{category}-{stamp}.sqlite3'
        migration._online_backup(Path(data_dir) / migration.INSTRUCTIONS_DB, dest)
        out.append(dest.name)
    for rel_tree in CATEGORIES[category]['files']:
        src = Path(data_dir) / rel_tree
        if src.exists():
            dest = folder / f'pre-restore-{category}-{stamp}-files' / rel_tree
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest)
            out.append(str(Path(dest).relative_to(folder)))
    return out


def _restore_files(data_dir, work, category, manifest):
    for rel_tree in CATEGORIES[category]['files']:
        live = Path(data_dir) / rel_tree
        src = Path(work) / 'files' / rel_tree
        if live.exists():
            shutil.rmtree(live)
        if src.exists():
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, live)


def _restore_stock(data_dir, work, prepare_stock=None):
    src = Path(work) / STOCK_NAME
    if not src.exists():
        raise SplitBackupError('ZIPに在庫管理のデータがありません。')
    if not migration._integrity_ok(src):
        raise SplitBackupError('ZIP内の在庫データが壊れています。')
    if prepare_stock:
        try:
            prepare_stock(src)  # このPCのアプリの版へ表・列をそろえる
        except Exception as e:
            raise SplitBackupError(f'在庫データをこのPCの形式へそろえられませんでした: {e}') from e
    live = Path(data_dir) / migration.STOCK_DB
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(live), timeout=30)
    try:
        if live.exists():
            problems = []
            lt = set(_tables(d))
            missing = [t for t in lt if t not in set(_tables(s)) and t != 'sqlite_sequence']
            if missing:
                problems.append('ZIPに無い在庫の表があります: ' + ', '.join(sorted(missing)))
            if problems:
                raise SplitBackupError('\n'.join(problems))
        s.backup(d)
    finally:
        s.close(); d.close()


def restore_category(zip_path, data_dir, *, actor, expect_category=None, prepare_stock=None):
    """ZIP の区分だけを置き換える。戻り値: dict（件数・安全バックアップ名など）。"""
    data_dir = Path(data_dir)
    work = Path(tempfile.mkdtemp(prefix='split-restore-'))
    migration._enter_busy()
    try:
        manifest = _read_zip(zip_path, work)
        category = manifest.get('category')
        if category not in CATEGORIES:
            raise SplitBackupError('ZIPのバックアップ種類が不明です。')
        if expect_category and expect_category != category:
            raise SplitBackupError(
                f'選んだZIPは「{CATEGORIES[category]["label"]}」のバックアップです。'
                f'「{CATEGORIES[expect_category]["label"]}」の復元には使えません。')
        safety = _safety_backup(data_dir, category)
        if category == STOCK:
            _restore_stock(data_dir, work, prepare_stock)
            counts = {'stock.sqlite3': 'replaced'}
        else:
            counts = _restore_instruction_tables(data_dir, work, category, manifest, actor)
        _restore_files(data_dir, work, category, manifest)
        return {'category': category, 'label': CATEGORIES[category]['label'], 'counts': counts,
                'safetyBackup': safety, 'sourceCreatedAt': manifest.get('createdAt')}
    finally:
        migration._leave_busy()
        shutil.rmtree(work, ignore_errors=True)


def _restore_instruction_tables(data_dir, work, category, manifest, actor):
    src_path = Path(work) / DB_NAME
    if not src_path.exists():
        raise SplitBackupError('ZIPにデータベースがありません。')
    live = sqlite3.connect(str(Path(data_dir) / migration.INSTRUCTIONS_DB), timeout=30, isolation_level=None)
    try:
        live.execute('ATTACH DATABASE ? AS z', (str(src_path),))
        srcc = sqlite3.connect(str(src_path))
        try:
            tables = [t for t in _tables(srcc) if t != 'settings' and table_category(t) == category]
            src_has_settings = 'settings' in _tables(srcc)
            problems = _check_schema(live, srcc, tables)
        finally:
            srcc.close()
        if problems:
            raise SplitBackupError('このPCのデータ形式と合わないため復元を中止しました。\n' + '\n'.join(problems))
        live_users_before = _users_list(live)
        live.execute('PRAGMA foreign_keys=OFF')
        live.execute('BEGIN IMMEDIATE')
        try:
            counts = {}
            # このPCにあってZIPに無い同区分の表も空にする（ZIPの時点に戻す）
            for t in category_tables(live, category):
                live.execute(f'DELETE FROM main."{t}"')
            for t in tables:
                src_cols = [r[1] for r in live.execute(f'PRAGMA z.table_info("{t}")')]
                cols = ', '.join(f'"{x}"' for x in src_cols)
                live.execute(f'INSERT INTO main."{t}" ({cols}) SELECT {cols} FROM z."{t}"')
                counts[t] = live.execute(f'SELECT COUNT(*) FROM main."{t}"').fetchone()[0]
                migration._sync_sqlite_sequence(live, t)
            if src_has_settings and 'settings' in _tables(live):
                for (k,) in live.execute('SELECT key FROM main.settings').fetchall():
                    if setting_category(k) == category:
                        live.execute('DELETE FROM main.settings WHERE key=?', (k,))
                for k, v in live.execute('SELECT key, value FROM z.settings').fetchall():
                    if setting_category(k) == category:
                        live.execute('INSERT OR REPLACE INTO main.settings(key,value) VALUES(?,?)', (k, v))
            if category == USERS:
                _after_users_restore(live, live_users_before, actor)
            else:
                dest_users = _users_list(live)
                fallback = _actor_dest_id(dest_users, actor)
                umap, fb = _user_map(manifest.get('sourceUsers') or [], dest_users, fallback)
                _remap_tables(live, tables, umap, fb, {u['id']: u['name'] for u in dest_users})
            live.execute('COMMIT')
        except Exception:
            live.execute('ROLLBACK')
            raise
        live.execute('DETACH DATABASE z')
        return counts
    finally:
        live.close()


def _actor_dest_id(dest_users, actor):
    login = str((actor or {}).get('login') or '').casefold()
    for u in dest_users:
        if str(u.get('login') or '').casefold() == login and login:
            return u['id']
    owners = [u for u in dest_users if u.get('role') == 'owner' and u.get('active')]
    return owners[0]['id'] if owners else None


def _after_users_restore(live, before_users, actor):
    after = _users_list(live)
    if not any(u.get('role') == 'owner' and u.get('active') for u in after):
        raise SplitBackupError('ZIPに有効な主管理者がいないため、利用者の復元を中止しました。')
    # 他区分の利用者IDを、旧ID → 新ID（ログインIDで照合）へ付け替える
    fallback = _actor_dest_id(after, actor)
    umap, fb = _user_map(before_users, after, fallback)
    others = [t for t in _tables(live) if table_category(t) not in (None, USERS)]
    _remap_tables(live, others, umap, fb, {u['id']: u['name'] for u in after})
    # 旧ログイン状態は無効化（再ログインしてもらう）
    if 'sessions' in _tables(live):
        live.execute('DELETE FROM sessions')
