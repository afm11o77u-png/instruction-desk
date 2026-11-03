"""在庫モジュール。数量と金額はロット（入荷分）から計算し、画面から現在庫を手入力させない。"""
import re
import sqlite3
import unicodedata
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kana TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  contact TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  address TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  fax TEXT NOT NULL DEFAULT '',
  contact TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  maker_code TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  color TEXT NOT NULL DEFAULT '',
  material TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  supplier_id INTEGER REFERENCES suppliers(id),
  active INTEGER NOT NULL DEFAULT 1,
  merged_into_id INTEGER REFERENCES products(id),
  merged_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_suppliers (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id),
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(product_id, supplier_id)
);
CREATE INDEX IF NOT EXISTS product_suppliers_product
  ON product_suppliers(product_id, sort_order, id);
CREATE TABLE IF NOT EXISTS product_sizes (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id),
  label TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  UNIQUE(product_id, label)
);
CREATE TABLE IF NOT EXISTS product_merges (
  id INTEGER PRIMARY KEY,
  keep_product_id INTEGER NOT NULL,
  actor_name TEXT NOT NULL DEFAULT '',
  client_token TEXT NOT NULL UNIQUE,
  before_qty INTEGER NOT NULL,
  after_qty INTEGER NOT NULL,
  before_amount INTEGER NOT NULL,
  after_amount INTEGER NOT NULL,
  fingerprint TEXT NOT NULL DEFAULT '',
  request_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  backup_path TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_merge_sources (
  id INTEGER PRIMARY KEY,
  merge_id INTEGER NOT NULL REFERENCES product_merges(id),
  source_product_id INTEGER NOT NULL,
  source_snapshot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS product_merge_sources_merge ON product_merge_sources(merge_id);
CREATE INDEX IF NOT EXISTS product_merge_sources_product ON product_merge_sources(source_product_id);
CREATE TABLE IF NOT EXISTS customer_products (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  embroidery TEXT NOT NULL DEFAULT '',
  handling_kind TEXT,
  is_standard INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(customer_id, product_id, embroidery)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_standard_spec
  ON customer_products(customer_id, product_id) WHERE is_standard=1;
CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES locations(id),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS locations_root_name ON locations(name) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS locations_child_name ON locations(parent_id, name) WHERE parent_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS opening_batches (
  id INTEGER PRIMARY KEY,
  client_token TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS move_batches (
  id INTEGER PRIMARY KEY,
  client_token TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  actor_name TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lots (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER REFERENCES opening_batches(id),
  customer_product_id INTEGER NOT NULL REFERENCES customer_products(id),
  size_label TEXT NOT NULL,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  unit_cost INTEGER NOT NULL,
  qty_received INTEGER NOT NULL,
  qty_remaining INTEGER NOT NULL,
  basis_date TEXT NOT NULL,
  received_on TEXT,
  received_unknown INTEGER NOT NULL DEFAULT 0,
  payout_order INTEGER NOT NULL DEFAULT 0,
  condition TEXT NOT NULL DEFAULT 'new',
  cost_pending INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS moves (
  id INTEGER PRIMARY KEY,
  lot_id INTEGER NOT NULL REFERENCES lots(id),
  kind TEXT NOT NULL,
  qty_delta INTEGER NOT NULL,
  unit_cost INTEGER NOT NULL,
  qty_before INTEGER,
  qty_after INTEGER,
  cost_before INTEGER,
  cost_after INTEGER,
  location_id INTEGER,
  related_lot_id INTEGER,
  loan_id INTEGER,
  move_batch_id INTEGER REFERENCES move_batches(id),
  actor_name TEXT NOT NULL DEFAULT '',
  business_on TEXT,
  corrects_move_id INTEGER REFERENCES moves(id),
  note TEXT NOT NULL DEFAULT '',
  price_source TEXT,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS moves_lot ON moves(lot_id);
CREATE TABLE IF NOT EXISTS loans (
  id INTEGER PRIMARY KEY,
  stock_customer_id INTEGER NOT NULL REFERENCES customers(id),
  use_customer_id INTEGER NOT NULL REFERENCES customers(id),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loan_lines (
  id INTEGER PRIMARY KEY,
  loan_id INTEGER NOT NULL REFERENCES loans(id),
  lot_id INTEGER NOT NULL REFERENCES lots(id),
  qty INTEGER NOT NULL,
  unit_cost INTEGER NOT NULL,
  received_on TEXT,
  received_unknown INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS loan_events (
  id INTEGER PRIMARY KEY,
  loan_id INTEGER NOT NULL REFERENCES loans(id),
  kind TEXT NOT NULL,
  qty INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS legacy_csv_import_runs (
  id INTEGER PRIMARY KEY,
  file_sha256 TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  row_count INTEGER NOT NULL,
  basis_date TEXT NOT NULL,
  default_site_name TEXT NOT NULL,
  actor_name TEXT NOT NULL DEFAULT '',
  preview_at TEXT NOT NULL,
  preview_fingerprint TEXT NOT NULL DEFAULT '',
  commit_token TEXT UNIQUE,
  committed_at TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS legacy_csv_import_runs_sha ON legacy_csv_import_runs(file_sha256, created_at DESC);
CREATE TABLE IF NOT EXISTS legacy_csv_row_maps (
  id INTEGER PRIMARY KEY,
  product_map_key TEXT NOT NULL,
  stock_map_key TEXT NOT NULL,
  file_sha256 TEXT NOT NULL DEFAULT '',
  file_name TEXT NOT NULL DEFAULT '',
  source_row_no INTEGER,
  csv_customer TEXT NOT NULL DEFAULT '',
  csv_supplier TEXT NOT NULL DEFAULT '',
  csv_product_code TEXT NOT NULL DEFAULT '',
  csv_product_name TEXT NOT NULL DEFAULT '',
  csv_size TEXT NOT NULL DEFAULT '',
  csv_condition TEXT NOT NULL DEFAULT '',
  csv_unit_cost TEXT NOT NULL DEFAULT '',
  csv_site TEXT NOT NULL DEFAULT '',
  csv_shelf TEXT NOT NULL DEFAULT '',
  customer_id INTEGER,
  product_id INTEGER,
  customer_product_id INTEGER,
  opening_token TEXT NOT NULL DEFAULT '',
  lot_ids_json TEXT NOT NULL DEFAULT '[]',
  run_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS legacy_csv_row_maps_product_key
  ON legacy_csv_row_maps(product_map_key, id DESC);
CREATE INDEX IF NOT EXISTS legacy_csv_row_maps_stock_key
  ON legacy_csv_row_maps(stock_map_key, id DESC);
CREATE INDEX IF NOT EXISTS legacy_csv_row_maps_file
  ON legacy_csv_row_maps(file_sha256, source_row_no);
CREATE TABLE IF NOT EXISTS product_ledger_import_runs (
  id INTEGER PRIMARY KEY,
  file_sha256 TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  row_count INTEGER NOT NULL,
  actor_name TEXT NOT NULL DEFAULT '',
  preview_at TEXT NOT NULL,
  preview_fingerprint TEXT NOT NULL DEFAULT '',
  commit_token TEXT UNIQUE,
  committed_at TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  backup_path TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS product_ledger_import_runs_sha
  ON product_ledger_import_runs(file_sha256, created_at DESC);
CREATE TABLE IF NOT EXISTS product_ledger_row_maps (
  id INTEGER PRIMARY KEY,
  product_map_key TEXT NOT NULL,
  row_map_key TEXT NOT NULL,
  match_group_key TEXT NOT NULL DEFAULT '',
  file_sha256 TEXT NOT NULL DEFAULT '',
  file_name TEXT NOT NULL DEFAULT '',
  source_row_no INTEGER,
  csv_customer TEXT NOT NULL DEFAULT '',
  csv_supplier TEXT NOT NULL DEFAULT '',
  csv_product_code TEXT NOT NULL DEFAULT '',
  csv_product_name TEXT NOT NULL DEFAULT '',
  csv_size TEXT NOT NULL DEFAULT '',
  customer_id INTEGER,
  product_id INTEGER,
  customer_product_id INTEGER,
  run_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS product_ledger_row_maps_product_key
  ON product_ledger_row_maps(product_map_key, id DESC);
CREATE INDEX IF NOT EXISTS product_ledger_row_maps_row_key
  ON product_ledger_row_maps(row_map_key, id DESC);
CREATE INDEX IF NOT EXISTS product_ledger_row_maps_file
  ON product_ledger_row_maps(file_sha256, source_row_no);
"""


def now():
    return datetime.now().isoformat(timespec='seconds')


# 顧客名の並び替えで無視する先頭の会社種別（登録名は変えない）
_COMPANY_MARKER_WORDS = (
    # 長いものから（一般社団法人 → 社団法人 の順に照合）
    '特定非営利活動法人', '一般社団法人', '一般財団法人', '公益社団法人', '公益財団法人',
    '社会福祉法人', '医療法人社団', '医療法人財団', '医療法人', '財団法人', '社団法人', '学校法人', 'NPO法人',
    '株式会社', '有限会社', '合同会社', '合名会社', '合資会社',
)
# 括弧付きの略号（NFKC 後）。㈱㈲㈳㈴㈵㈶㈹㈻㈾ は NFKC で (株) などになる。（福）の1文字の囲み文字は Unicode にない。
_COMPANY_MARKER_ABBR = ('特非', '株', '有', '合', '名', '資', '代', '福', '医', '財', '社', '学', '特')
_COMPANY_MARKER_RE = re.compile(
    r'^\s*(?:\(\s*(?:' + '|'.join(_COMPANY_MARKER_ABBR) + r')\s*\)|'
    + '|'.join(_COMPANY_MARKER_WORDS) + r')\s*',
    re.IGNORECASE,
)
# 丸囲みの略号（㊑㊒㊓㊔㊕㊖㊩㊫㊮）は NFKC で1文字の漢字になるため、正規化前に外す
_CIRCLED_MARKER_RE = re.compile(r'^\s*[\u3291-\u3296\u32a9\u32ab\u32ae]\s*')


def customer_sort_key(name):
    """顧客名の並び替えキー。先頭の（株）・(株)・㈱・株式会社・有限会社などと続く空白を除く。

    NFKC で全角括弧・㈱㈲㈹・全角英数を半角にそろえてから判定し、英字は大文字小文字を区別しない。
    """
    raw = name or ''
    key = unicodedata.normalize('NFKC', _CIRCLED_MARKER_RE.sub('', raw, count=1) or raw)
    stripped = _COMPANY_MARKER_RE.sub('', key, count=1)
    # 全角英字（ＮＰＧ）も半角にそろえ、大文字小文字を区別しない（ローマ字名をまとめる）
    return (stripped or key).casefold()


def connect(path):
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    c.create_function('customer_sort_key', 1, customer_sort_key, deterministic=True)
    c.execute('PRAGMA foreign_keys=ON')
    return c


class StockConflict(Exception):
    """Client must re-confirm because stock breakdown changed."""

    def __init__(self, payload):
        super().__init__(payload.get('error') or '在庫の内訳が変わったため、確認し直してください。')
        self.payload = payload


# 旧システム由来の拠点空欄在庫をいったん置く仮拠点。棚は作らない。
UNCONFIRMED_SITE_NAME = '未確認'


def initialize(c):
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript(SCHEMA)
    migrate_schema(c)
    ensure_unconfirmed_site(c)


# 商品カテゴリ（空欄可。旧CSVの品番なし行で「制服」「部品・資材」「加工サービス」を使う）
CATEGORY_UNIFORM = '制服'
CATEGORY_PARTS = '部品・資材'
CATEGORY_PROCESS_SERVICE = '加工サービス'
CODELESS_CATEGORIES = (CATEGORY_PARTS, CATEGORY_PROCESS_SERVICE)
PRODUCT_CATEGORIES = frozenset({
    '', CATEGORY_UNIFORM, CATEGORY_PARTS, CATEGORY_PROCESS_SERVICE,
})
# UI・プレビュー用の並び（空欄→制服→部品・資材→加工サービス）
PRODUCT_CATEGORY_OPTIONS = (
    '', CATEGORY_UNIFORM, CATEGORY_PARTS, CATEGORY_PROCESS_SERVICE,
)
BLANK_SKU_DISPLAY = '品番なし'
BLANK_SKU_UNIFORM_DISPLAY = '品番なし（要確認）'


def normalize_category(value):
    t = text(value)
    if t not in PRODUCT_CATEGORIES:
        raise ValueError('カテゴリは空欄・制服・部品・資材・加工サービスのいずれかです。')
    return t


def is_process_service_category(value):
    return text(value) == CATEGORY_PROCESS_SERVICE


def is_physical_parts_category(value):
    return text(value) == CATEGORY_PARTS


def maker_code_of(row_or_code, maker_code=None):
    """発注・メーカー向けに出す品番。maker_code が空なら社内品番 code をそのまま使う。"""
    if isinstance(row_or_code, dict) or hasattr(row_or_code, 'keys'):
        code = text(row_or_code['code'] if 'code' in row_or_code.keys() else '')
        mk = ''
        if 'maker_code' in row_or_code.keys():
            mk = text(row_or_code['maker_code'] or '')
        elif 'makerCode' in row_or_code.keys():
            mk = text(row_or_code['makerCode'] or '')
        return mk or code
    code = text(row_or_code)
    mk = text(maker_code or '')
    return mk or code


def order_product_code_label(*, code, maker_code='', color='', internal_code=None):
    """発注画面・発注書用: メーカー品番＋色。社内品番が別なら併記。"""
    maker = maker_code_of(code, maker_code)
    color_t = text(color)
    internal = text(internal_code if internal_code is not None else code)
    bits = [maker]
    if color_t:
        bits.append(color_t)
    label = ' '.join(x for x in bits if x).strip()
    if internal and maker and internal != maker:
        label = f'{label}（社内 {internal}）' if label else f'社内 {internal}'
    return label


def product_code_display(code, category=''):
    """品番の画面表示。空品番は保存せず、表示だけ「品番なし」系にする。"""
    t = text(code)
    if t:
        return t
    cat = text(category)
    if cat in CODELESS_CATEGORIES:
        # 部品・資材／加工サービスは品番を持たないのが普通。何も出さない
        return ''
    if cat == CATEGORY_UNIFORM:
        return BLANK_SKU_UNIFORM_DISPLAY
    return BLANK_SKU_DISPLAY


def needs_migration(c):
    tables = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'move_batches' not in tables:
        return True
    move_cols = {r['name'] for r in c.execute('PRAGMA table_info(moves)')}
    if (
        'move_batch_id' not in move_cols
        or 'actor_name' not in move_cols
        or 'business_on' not in move_cols
        or 'corrects_move_id' not in move_cols
    ):
        return True
    if 'lots' in tables:
        lot_cols = {r['name'] for r in c.execute('PRAGMA table_info(lots)')}
        if 'cost_pending' not in lot_cols:
            return True
    if 'products' in tables:
        product_cols = {r['name'] for r in c.execute('PRAGMA table_info(products)')}
        if 'category' not in product_cols:
            return True
        if 'merged_into_id' not in product_cols:
            return True
    if 'product_suppliers' not in tables or 'product_merges' not in tables:
        return True
    if 'legacy_csv_row_maps' not in tables:
        return True
    if 'customer_product_processes' not in tables:
        return True
    if 'customer_products' in tables:
        cp_cols = {r['name'] for r in c.execute('PRAGMA table_info(customer_products)')}
        if 'handling_kind' not in cp_cols:
            return True
        if c.execute('SELECT 1 FROM customer_products WHERE handling_kind IS NULL LIMIT 1').fetchone():
            return True
    if 'product_ledger_row_maps' not in tables or 'product_ledger_import_runs' not in tables:
        return True
    import stock_orders
    import stock_product_merge
    import stock_prices
    return (
        stock_orders.needs_purchase_order_migration(c)
        or stock_product_merge.needs_merge_migration(c)
        or stock_prices.needs_migration(c)
    )


BACKUP_FAILED_MESSAGE = '登録前のバックアップに失敗したため、登録を中止しました'
# Bound for lock waits / backup page retries. Never block forever on a locked DB.
_BACKUP_TIMEOUT_SEC = 20.0
_BACKUP_PAGE_CHUNK = 200
_BACKUP_RETRY_SLEEP_SEC = 0.05


class BackupError(RuntimeError):
    """Pre-mutation SQLite backup failed; caller must abort without registering."""

    def __init__(self, message=None, *, cause=None):
        super().__init__(message or BACKUP_FAILED_MESSAGE)
        self.cause = cause


def _unlink_quiet(path):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f'backup_sqlite cleanup failed path={path}: {type(e).__name__}: {e}', flush=True)


def _backup_sqlite_online(src_path, dest_path, *, timeout_sec=None):
    """Consistent online backup via SQLite Backup API (includes committed WAL pages).

    Uses a separate source connection so a caller's open write transaction is never
    the connection that drives backup (BEGIN+backup on the same connection can hang
    under WAL). Optionally takes a short IMMEDIATE lock on a third connection; if
    that lock is already held (typical during commit_preview), skip it immediately
    instead of waiting forever.
    """
    import time

    if timeout_sec is None:
        timeout_sec = _BACKUP_TIMEOUT_SEC
    src_path = Path(src_path)
    dest_path = Path(dest_path)
    partial = Path(str(dest_path) + '.partial')
    _unlink_quiet(partial)
    for suffix in ('-wal', '-shm'):
        _unlink_quiet(Path(str(partial) + suffix))
        _unlink_quiet(Path(str(dest_path) + suffix))

    deadline = time.monotonic() + max(0.5, float(timeout_sec))
    last_err = None

    while time.monotonic() < deadline:
        lock = src = dst = None
        lock_held = False
        try:
            remaining = max(0.05, deadline - time.monotonic())
            # Try a brief writer lock so concurrent writers pause during backup.
            # timeout=0 → fail immediately when the caller's BEGIN IMMEDIATE holds it.
            lock = sqlite3.connect(str(src_path), timeout=0)
            try:
                lock.execute('BEGIN IMMEDIATE')
                lock_held = True
            except sqlite3.OperationalError:
                lock.close()
                lock = None

            busy_timeout = min(2.0, remaining)
            src = sqlite3.connect(str(src_path), timeout=busy_timeout)
            dst = sqlite3.connect(str(partial), timeout=busy_timeout)
            # sleep=0: do not spin forever inside backup(); outer loop bounds waits.
            src.backup(dst, pages=_BACKUP_PAGE_CHUNK, sleep=0)

            row = dst.execute('PRAGMA integrity_check').fetchone()
            check = (row[0] if row else '') or ''
            if str(check).lower() != 'ok':
                raise BackupError(f'{BACKUP_FAILED_MESSAGE}（整合性チェック: {check}）')

            dst.close()
            dst = None
            src.close()
            src = None
            if lock_held and lock is not None:
                lock.commit()
                lock.close()
                lock = None
                lock_held = False

            # Only promote a verified partial file to the final backup name.
            partial.replace(dest_path)
            return dest_path
        except BackupError:
            raise
        except (sqlite3.Error, OSError, TimeoutError) as e:
            last_err = e
            print(
                f'backup_sqlite attempt failed src={src_path} dest={dest_path}: '
                f'{type(e).__name__}: {e}',
                flush=True,
            )
            if time.monotonic() >= deadline:
                break
            time.sleep(_BACKUP_RETRY_SLEEP_SEC)
        finally:
            if dst is not None:
                try:
                    dst.close()
                except sqlite3.Error:
                    pass
            if src is not None:
                try:
                    src.close()
                except sqlite3.Error:
                    pass
            if lock is not None:
                try:
                    if lock_held:
                        lock.rollback()
                except sqlite3.Error:
                    pass
                try:
                    lock.close()
                except sqlite3.Error:
                    pass
            _unlink_quiet(partial)
            for suffix in ('-wal', '-shm'):
                _unlink_quiet(Path(str(partial) + suffix))

    cause = last_err
    detail = f'{type(cause).__name__}: {cause}' if cause else 'timeout'
    print(f'backup_sqlite gave up src={src_path}: {detail}', flush=True)
    raise BackupError(BACKUP_FAILED_MESSAGE, cause=cause) from cause


def backup_sqlite(db_path, *, tag='pre-shortage'):
    """Consistent restorable backup beside the original (SQLite Backup API).

    Replaces file-copy of main/WAL/SHM, which fails on Windows when another
    process holds those files (WinError 33). Incomplete files are never left
    under the final backup name.
    """
    src = Path(db_path)
    if not src.is_file():
        return None
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = src.with_name(f'{src.stem}.{tag}-{stamp}{src.suffix}')
    return _backup_sqlite_online(src, dest)


# 区分ごとの表示番号（内部 id・参照は変えない）。K=加工サービス B=部品・資材 S=制服・その他
DISPLAY_NO_PREFIX_SQL = (
    "CASE {col} WHEN '加工サービス' THEN 'K' WHEN '部品・資材' THEN 'B' ELSE 'S' END"
)


def display_no_prefix(category):
    cat = text(category)
    if cat == CATEGORY_PROCESS_SERVICE:
        return 'K'
    if cat == CATEGORY_PARTS:
        return 'B'
    return 'S'


DISPLAY_NO_WIDTH = {'K': 4, 'B': 4, 'S': 5}


def format_display_no(prefix, n):
    return f'{prefix}-{int(n):0{DISPLAY_NO_WIDTH.get(prefix, 4)}d}'


def migrate_display_no(c):
    """products.display_no を追加し、未採番行を id 順に補完。採番はトリガーで作成時／区分変更時に行う。

    採番カウンタ（product_display_no_seq）は削除で戻さない＝番号を再利用・振り直ししない。
    """
    cols = {r['name'] for r in c.execute('PRAGMA table_info(products)')}
    if 'display_no' not in cols:
        c.execute("ALTER TABLE products ADD COLUMN display_no TEXT NOT NULL DEFAULT ''")
    c.execute('''CREATE TABLE IF NOT EXISTS product_display_no_seq (
      prefix TEXT PRIMARY KEY,
      last_no INTEGER NOT NULL DEFAULT 0
    )''')
    # 既存の番号より後ろから振る（復元・手修正後もカウンタが追い越されないように）
    for prefix in ('K', 'B', 'S'):
        mx = 0
        for r in c.execute("SELECT display_no FROM products WHERE display_no LIKE ?", (prefix + '-%',)):
            try:
                mx = max(mx, int(str(r['display_no']).split('-', 1)[1]))
            except (ValueError, IndexError):
                pass
        c.execute('INSERT OR IGNORE INTO product_display_no_seq(prefix,last_no) VALUES(?,0)', (prefix,))
        c.execute('UPDATE product_display_no_seq SET last_no=? WHERE prefix=? AND last_no<?', (mx, prefix, mx))
    pending = c.execute(
        f"SELECT id, {DISPLAY_NO_PREFIX_SQL.format(col='category')} AS px FROM products "
        "WHERE COALESCE(display_no,'')='' ORDER BY id"
    ).fetchall()
    for r in pending:
        c.execute('UPDATE product_display_no_seq SET last_no=last_no+1 WHERE prefix=?', (r['px'],))
        n = c.execute('SELECT last_no FROM product_display_no_seq WHERE prefix=?', (r['px'],)).fetchone()[0]
        c.execute('UPDATE products SET display_no=? WHERE id=?', (format_display_no(r['px'], n), r['id']))
    new_px = DISPLAY_NO_PREFIX_SQL.format(col='NEW.category')
    old_px = DISPLAY_NO_PREFIX_SQL.format(col='OLD.category')
    assign = f'''
      INSERT OR IGNORE INTO product_display_no_seq(prefix,last_no) VALUES({new_px},0);
      UPDATE product_display_no_seq SET last_no=last_no+1 WHERE prefix={new_px};
      UPDATE products SET display_no = {new_px} || '-' || printf(CASE WHEN {new_px}='S' THEN '%05d' ELSE '%04d' END,
        (SELECT last_no FROM product_display_no_seq WHERE prefix={new_px})) WHERE id=NEW.id;
    '''
    c.execute(f'''CREATE TRIGGER IF NOT EXISTS products_display_no_ins AFTER INSERT ON products
      WHEN COALESCE(NEW.display_no,'')='' BEGIN {assign} END''')
    c.execute(f'''CREATE TRIGGER IF NOT EXISTS products_display_no_cat AFTER UPDATE OF category ON products
      WHEN {new_px} <> {old_px} BEGIN {assign} END''')


def display_no_code_key(row):
    """品番順（画面の codeAsc 相当）: 停止・統合済みは後ろ、品番空欄は後ろ、数字部分は数値比較、同順は ID。"""
    import re
    import unicodedata
    hidden = 1 if (row['merged_into_id'] or not row['active']) else 0
    code = unicodedata.normalize('NFKC', text(row['code'])).strip()
    # ICU（ja, numeric）と同じく 記号 < 数字 < 文字
    parts = tuple((1, int(t), '') if t.isdigit()
                  else ((2, 0, t.casefold()) if t[0].isalnum() else (0, 0, t))
                  for t in re.findall(r'\d+|[^\W\d_]+|[\W_]+', code))
    return (hidden, 1 if not code else 0, parts, int(row['id']))


def display_no_name_key(name):
    """サーバー側の商品名順（画面の並び順がない商品用の予備）。"""
    import unicodedata
    return unicodedata.normalize('NFKC', text(name)).casefold()


def renumber_display_no(c, ordered_ids=None):
    """表示番号を区分（K/B/S）ごとに品番順で 1 から振り直す。内部 ID は変えない。

    ordered_ids: 画面の品番順（codeAsc: 空欄は後ろ・localeCompare('ja',numeric)・同順は ID）で
    有効品→停止/統合済みの順に並べた商品 ID。含まれない商品は display_no_code_key 順で後ろに付ける。
    カウンタは各区分の件数（＝最大番号）にそろえ、以後の新規はその次から。
    """
    rows = c.execute(
        f"SELECT id, name, code, active, merged_into_id, {DISPLAY_NO_PREFIX_SQL.format(col='category')} AS px FROM products"
    ).fetchall()
    by_id = {int(r['id']): r for r in rows}
    order = []
    seen = set()
    for raw in ordered_ids or []:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid in by_id and pid not in seen:
            seen.add(pid)
            order.append(pid)
    rest = sorted((r for r in rows if int(r['id']) not in seen),
                  key=display_no_code_key)
    order.extend(int(r['id']) for r in rest)
    counts = {'K': 0, 'B': 0, 'S': 0}
    # 一時的に重複しないよう先に空ける（トリガーは category 変更時のみなので発火しない）
    c.execute("UPDATE products SET display_no=''")
    for pid in order:
        px = by_id[pid]['px']
        counts[px] = counts.get(px, 0) + 1
        c.execute('UPDATE products SET display_no=? WHERE id=?', (format_display_no(px, counts[px]), pid))
    for px, n in counts.items():
        c.execute('INSERT OR IGNORE INTO product_display_no_seq(prefix,last_no) VALUES(?,0)', (px,))
        c.execute('UPDATE product_display_no_seq SET last_no=? WHERE prefix=?', (n, px))
    return counts


def migrate_schema(c):
    # 在庫一覧でロットごとの移動種別を引くため（既存DBにも追加。冪等）
    c.execute('CREATE INDEX IF NOT EXISTS moves_lot ON moves(lot_id)')
    migrate_handling_kind(c)
    c.execute('''CREATE TABLE IF NOT EXISTS move_batches (
      id INTEGER PRIMARY KEY,
      client_token TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL,
      actor_name TEXT NOT NULL DEFAULT '',
      note TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS legacy_csv_import_runs (
      id INTEGER PRIMARY KEY,
      file_sha256 TEXT NOT NULL,
      file_name TEXT NOT NULL,
      file_size INTEGER NOT NULL,
      row_count INTEGER NOT NULL,
      basis_date TEXT NOT NULL,
      default_site_name TEXT NOT NULL,
      actor_name TEXT NOT NULL DEFAULT '',
      preview_at TEXT NOT NULL,
      preview_fingerprint TEXT NOT NULL DEFAULT '',
      commit_token TEXT UNIQUE,
      committed_at TEXT,
      result_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS legacy_csv_import_runs_sha ON legacy_csv_import_runs(file_sha256, created_at DESC)')
    c.execute('''CREATE TABLE IF NOT EXISTS legacy_csv_row_maps (
      id INTEGER PRIMARY KEY,
      product_map_key TEXT NOT NULL,
      stock_map_key TEXT NOT NULL,
      file_sha256 TEXT NOT NULL DEFAULT '',
      file_name TEXT NOT NULL DEFAULT '',
      source_row_no INTEGER,
      csv_customer TEXT NOT NULL DEFAULT '',
      csv_supplier TEXT NOT NULL DEFAULT '',
      csv_product_code TEXT NOT NULL DEFAULT '',
      csv_product_name TEXT NOT NULL DEFAULT '',
      csv_size TEXT NOT NULL DEFAULT '',
      csv_condition TEXT NOT NULL DEFAULT '',
      csv_unit_cost TEXT NOT NULL DEFAULT '',
      csv_site TEXT NOT NULL DEFAULT '',
      csv_shelf TEXT NOT NULL DEFAULT '',
      customer_id INTEGER,
      product_id INTEGER,
      customer_product_id INTEGER,
      opening_token TEXT NOT NULL DEFAULT '',
      lot_ids_json TEXT NOT NULL DEFAULT '[]',
      run_id INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS legacy_csv_row_maps_product_key '
        'ON legacy_csv_row_maps(product_map_key, id DESC)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS legacy_csv_row_maps_stock_key '
        'ON legacy_csv_row_maps(stock_map_key, id DESC)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS legacy_csv_row_maps_file '
        'ON legacy_csv_row_maps(file_sha256, source_row_no)'
    )
    c.execute('''CREATE TABLE IF NOT EXISTS product_ledger_import_runs (
      id INTEGER PRIMARY KEY,
      file_sha256 TEXT NOT NULL,
      file_name TEXT NOT NULL,
      file_size INTEGER NOT NULL,
      row_count INTEGER NOT NULL,
      actor_name TEXT NOT NULL DEFAULT '',
      preview_at TEXT NOT NULL,
      preview_fingerprint TEXT NOT NULL DEFAULT '',
      commit_token TEXT UNIQUE,
      committed_at TEXT,
      result_json TEXT NOT NULL DEFAULT '{}',
      backup_path TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_import_runs_sha '
        'ON product_ledger_import_runs(file_sha256, created_at DESC)'
    )
    c.execute('''CREATE TABLE IF NOT EXISTS product_ledger_row_maps (
      id INTEGER PRIMARY KEY,
      product_map_key TEXT NOT NULL,
      row_map_key TEXT NOT NULL,
      match_group_key TEXT NOT NULL DEFAULT '',
      file_sha256 TEXT NOT NULL DEFAULT '',
      file_name TEXT NOT NULL DEFAULT '',
      source_row_no INTEGER,
      csv_customer TEXT NOT NULL DEFAULT '',
      csv_supplier TEXT NOT NULL DEFAULT '',
      csv_product_code TEXT NOT NULL DEFAULT '',
      csv_product_name TEXT NOT NULL DEFAULT '',
      csv_size TEXT NOT NULL DEFAULT '',
      customer_id INTEGER,
      product_id INTEGER,
      customer_product_id INTEGER,
      run_id INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_product_key '
        'ON product_ledger_row_maps(product_map_key, id DESC)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_row_key '
        'ON product_ledger_row_maps(row_map_key, id DESC)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_file '
        'ON product_ledger_row_maps(file_sha256, source_row_no)'
    )
    customer_cols = {r['name'] for r in c.execute('PRAGMA table_info(customers)')}
    if 'office_code' not in customer_cols:
        c.execute("ALTER TABLE customers ADD COLUMN office_code TEXT NOT NULL DEFAULT ''")
    move_cols = {r['name'] for r in c.execute('PRAGMA table_info(moves)')}
    if 'move_batch_id' not in move_cols:
        c.execute('ALTER TABLE moves ADD COLUMN move_batch_id INTEGER REFERENCES move_batches(id)')
    if 'actor_name' not in move_cols:
        c.execute("ALTER TABLE moves ADD COLUMN actor_name TEXT NOT NULL DEFAULT ''")
    if 'business_on' not in move_cols:
        c.execute('ALTER TABLE moves ADD COLUMN business_on TEXT')
    if 'corrects_move_id' not in move_cols:
        c.execute('ALTER TABLE moves ADD COLUMN corrects_move_id INTEGER REFERENCES moves(id)')
    if 'price_source' not in move_cols:
        # 不足出庫で登録単価（special=特値 / base=現単価）を記録したときだけ入る
        c.execute('ALTER TABLE moves ADD COLUMN price_source TEXT')
    # Ensure lots.condition exists. Do not rewrite existing values (including empty /
    # unexpected codes): legacy 「＊」product names are not used to infer 中古, and
    # ambiguous stock must not be force-converted to 新品.
    tables = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'lots' in tables:
        lot_cols = {r['name'] for r in c.execute('PRAGMA table_info(lots)')}
        if 'condition' not in lot_cols:
            c.execute("ALTER TABLE lots ADD COLUMN condition TEXT NOT NULL DEFAULT 'new'")
        if 'cost_pending' not in lot_cols:
            # cost_pending=1: 原価未確定（unit_cost の数値を確定原価として使わない）
            c.execute('ALTER TABLE lots ADD COLUMN cost_pending INTEGER NOT NULL DEFAULT 0')
    if 'products' in tables:
        product_cols = {r['name'] for r in c.execute('PRAGMA table_info(products)')}
        if 'category' not in product_cols:
            c.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT ''")
        if 'material' not in product_cols:
            c.execute("ALTER TABLE products ADD COLUMN material TEXT NOT NULL DEFAULT ''")
        if 'maker_code' not in product_cols:
            # 社内管理品番(code)とメーカー品番を分離。空なら従来どおり code をメーカー品番扱い。
            c.execute("ALTER TABLE products ADD COLUMN maker_code TEXT NOT NULL DEFAULT ''")
        migrate_display_no(c)
    import stock_product_merge
    stock_product_merge.migrate_product_merge(c)
    # Backfill business dates from lot fields only (no invented dates for outbound).
    c.execute(
        '''UPDATE moves SET business_on=(
              SELECT l.basis_date FROM lots l WHERE l.id=moves.lot_id
            )
            WHERE kind='opening' AND (business_on IS NULL OR business_on='')'''
    )
    c.execute(
        '''UPDATE moves SET business_on=(
              SELECT CASE
                WHEN l.received_unknown=1 THEN NULL
                ELSE l.received_on
              END
              FROM lots l WHERE l.id=moves.lot_id
            )
            WHERE kind='inbound' AND (business_on IS NULL OR business_on='')'''
    )
    import stock_orders
    stock_orders.migrate_purchase_orders(c)
    ensure_handle_process_schema(c)
    import stock_prices
    stock_prices.migrate(c)


def ensure_handle_process_schema(c):
    """取扱 → 加工サービス（複数可）のリンク表。初回作成時だけ既存の加工仕様文字を名前で対応付け。

    既存の embroidery（加工仕様の文字）は消さない・書き換えない。
    """
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='customer_product_processes'").fetchone():
        return
    c.execute('''CREATE TABLE customer_product_processes (
      customer_product_id INTEGER NOT NULL REFERENCES customer_products(id) ON DELETE CASCADE,
      process_product_id INTEGER NOT NULL REFERENCES products(id),
      sort_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(customer_product_id, process_product_id)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS customer_product_processes_proc '
              'ON customer_product_processes(process_product_id)')
    backfill_handle_processes(c)


def resolve_process_service_ids(c, spec):
    """加工仕様の文字から加工サービス商品IDを探す（見つからなければ空）。"""
    import product_ledger_processing_service as proc_svc
    name = proc_svc.unify_processing_service_name(spec or '')
    if not name:
        return []
    hit = proc_svc.find_existing_processing_service_any_vendor(c, name)
    return [int(hit['id'])] if hit else []


def backfill_handle_processes(c):
    """リンクの無い取扱に、加工仕様の文字と同名の加工サービスを付ける（追加のみ）。"""
    added = 0
    rows = c.execute(
        '''SELECT cp.id, cp.embroidery FROM customer_products cp
           WHERE cp.embroidery<>'' AND NOT EXISTS (
             SELECT 1 FROM customer_product_processes x WHERE x.customer_product_id=cp.id)'''
    ).fetchall()
    for r in rows:
        for pid in resolve_process_service_ids(c, r['embroidery']):
            added += add_handle_processes(c, r['id'], [pid])
    return added


def handle_processes(c, cp_id):
    return [
        {'id': r['id'], 'name': r['name']}
        for r in c.execute(
            '''SELECT p.id, p.name FROM customer_product_processes x JOIN products p ON p.id=x.process_product_id
               WHERE x.customer_product_id=? ORDER BY x.sort_order, p.id''', (cp_id,))
    ]


def _valid_process_ids(c, ids):
    out = []
    for v in ids or []:
        try:
            pid = int(v)
        except (TypeError, ValueError):
            raise ValueError('加工サービスを候補から選んでください。')
        r = c.execute('SELECT category FROM products WHERE id=?', (pid,)).fetchone()
        if not r or (r['category'] or '') != CATEGORY_PROCESS_SERVICE:
            raise ValueError('加工サービスを候補から選んでください。')
        if pid not in out:
            out.append(pid)
    return out


def add_handle_processes(c, cp_id, ids):
    """取扱へ加工サービスを追加（既にあれば何もしない）。追加件数を返す。"""
    n = 0
    base = c.execute('SELECT COALESCE(MAX(sort_order),-1)+1 FROM customer_product_processes WHERE customer_product_id=?',
                     (cp_id,)).fetchone()[0]
    for pid in _valid_process_ids(c, ids):
        cur = c.execute('INSERT OR IGNORE INTO customer_product_processes(customer_product_id,process_product_id,sort_order) '
                        'VALUES(?,?,?)', (cp_id, pid, base + n))
        n += cur.rowcount or 0
    return n


def set_handle_processes(c, cp_id, ids):
    """取扱の加工サービスを指定どおりに置き換える。"""
    valid = _valid_process_ids(c, ids)
    c.execute('DELETE FROM customer_product_processes WHERE customer_product_id=?', (cp_id,))
    for i, pid in enumerate(valid):
        c.execute('INSERT INTO customer_product_processes(customer_product_id,process_product_id,sort_order) VALUES(?,?,?)',
                  (cp_id, pid, i))


def ensure_unconfirmed_site(c):
    """起動時補正: 仮拠点「未確認」を idempotent に確保する（棚は作らない）。

    同名の拠点（parent_id IS NULL）が既にあればそのまま返す。新規作成しない。
    空の拠点名を「未確認」へ変換する処理は行わない（拠点必須の仕様は維持）。
    """
    existing = c.execute(
        'SELECT id FROM locations WHERE name=? AND parent_id IS NULL',
        (UNCONFIRMED_SITE_NAME,),
    ).fetchone()
    if existing:
        return location_row(c, existing['id'])
    t = stamp()
    try:
        c.execute(
            'INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
            (UNCONFIRMED_SITE_NAME, None, 1, t, t),
        )
    except sqlite3.IntegrityError:
        # UNIQUE(locations_root_name) との競合時は既存行を返す
        existing = c.execute(
            'SELECT id FROM locations WHERE name=? AND parent_id IS NULL',
            (UNCONFIRMED_SITE_NAME,),
        ).fetchone()
        if not existing:
            raise
        return location_row(c, existing['id'])
    site_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return location_row(c, site_id)


CONDITION_NEW = 'new'
CONDITION_USED = 'used'
CONDITION_LABELS = {CONDITION_NEW: '新品', CONDITION_USED: '中古'}
CONDITION_PARSE = {
    'new': CONDITION_NEW,
    '新品': CONDITION_NEW,
    'used': CONDITION_USED,
    '中古': CONDITION_USED,
}


def condition_label(code):
    """Human label for a stored condition code. Unknown/empty → 未設定 (not rewritten)."""
    if code in CONDITION_LABELS:
        return CONDITION_LABELS[code]
    return '未設定'


def parse_condition(value, *, default=CONDITION_NEW):
    """Parse 新品/中古 (or new/used). Blank/omitted → default. Invalid → error."""
    if value is None:
        return default
    if isinstance(value, str) and text(value) == '':
        return default
    raw = text(value) if not isinstance(value, str) else value.strip()
    mapped = CONDITION_PARSE.get(raw) or CONDITION_PARSE.get(raw.lower())
    if not mapped:
        raise ValueError('状態は「新品」または「中古」を指定してください。')
    return mapped


def condition_filter_sql(args, alias='l'):
    """Return (sql_fragment, params) for stock/period filters. all/blank → no filter."""
    raw = args.get('condition') if args else None
    if raw in (None, '', 'all', 'すべて'):
        return '', []
    try:
        code = parse_condition(raw)
    except ValueError as e:
        raise ValueError('状態の絞り込みが不正です。') from e
    return f' AND {alias}.condition=?', [code]


def text(value):
    return (value or '').strip()


# 未加工の取扱は DB に空文字で保存し、画面・API の表示だけこの名称を使う。
EMPTY_EMBROIDERY_LABEL = '加工なし'
# 表示名をそのまま入力・Excel記入された場合の互換（完全一致のみ。説明文の部分置換はしない）。
EMPTY_EMBROIDERY_ALIASES = frozenset({EMPTY_EMBROIDERY_LABEL, '刺繍なし'})
# 旧CSV取込で自動作成した「加工サービス未設定」の取扱の保存値（データは変えない）。
# 画面・出力の表示は「加工なし」。照合では加工なしと同じ扱いで既存取扱に寄せる。
PENDING_EMBROIDERY = '加工未整理'


# 取扱の区分（販売／レンタル）。リース等の販売以外はすべてレンタル。
HANDLING_KIND_SALE = '販売'
HANDLING_KIND_RENTAL = 'レンタル'
HANDLING_KINDS = (HANDLING_KIND_SALE, HANDLING_KIND_RENTAL)
# 区分の語だけが加工欄に入っていた場合は加工なしとみなす（保存キーは変えない）
_KIND_ONLY_WORDS = frozenset({'レンタル', 'リース', 'レンタル用', 'リース用'})


def handling_kind_of(embroidery):
    """保存キー（旧仕様）から区分を決める。販売の印があれば販売、それ以外はレンタル。"""
    import product_ledger_paren as paren
    return HANDLING_KIND_SALE if paren.handling_has_sales_usage(text(embroidery)) else HANDLING_KIND_RENTAL


def handling_processing(embroidery):
    """保存キーから加工部分だけを返す（販売の印を除く）。加工なしは空。"""
    import product_ledger_paren as paren
    t = text(embroidery)
    if not t or t == PENDING_EMBROIDERY or t in EMPTY_EMBROIDERY_ALIASES:
        return ''
    if t == paren.USAGE_KIND_SALES:
        return ''
    suffix = paren.USAGE_HANDLING_SEP + paren.USAGE_KIND_SALES
    if t.endswith(suffix):
        t = t[:-len(suffix)].strip()
    elif paren.handling_has_sales_usage(t):
        t = text(paren.strip_sales_usage_markers(t)[0])
    return '' if t in _KIND_ONLY_WORDS else t


def handling_key(processing, kind):
    """加工＋区分 → 保存キー。販売は既存の取込と同じ「加工｜販売」（加工なしは「販売」）。"""
    import product_ledger_paren as paren
    if kind not in HANDLING_KINDS:
        raise ValueError('区分は販売かレンタルを選んでください。')
    proc = handling_processing(processing)
    return normalize_embroidery(
        paren.compose_handling_embroidery(proc, paren.USAGE_KIND_SALES if kind == HANDLING_KIND_SALE else '')
    )


def sync_handling_kind(c):
    """区分が未設定の取扱へ、保存キーから区分を入れる（冪等・追加のみ）。"""
    rows = c.execute('SELECT id, embroidery FROM customer_products WHERE handling_kind IS NULL').fetchall()
    for r in rows:
        c.execute('UPDATE customer_products SET handling_kind=? WHERE id=?', (handling_kind_of(r['embroidery']), r['id']))
    return len(rows)


def migrate_handling_kind(c):
    cols = {r['name'] for r in c.execute('PRAGMA table_info(customer_products)')}
    if 'handling_kind' not in cols:
        c.execute('ALTER TABLE customer_products ADD COLUMN handling_kind TEXT')
    sync_handling_kind(c)


def _link_embroidery(raw_embroidery, raw_kind):
    """画面から区分付きで来たら保存キーを合成。区分なし（取込など）は従来どおり。"""
    emb = normalize_embroidery(optional(raw_embroidery, '加工', 80))
    if raw_kind is None:
        return emb
    return handling_key(emb, text(raw_kind))


def match_existing_spec(existing, value):
    """既存の仕様一覧から value に当たる保存値を返す（なければ value）。

    表記ゆれは spec_key で寄せる。加工なし（空・「加工なし」）で同じ空の取扱が無く、
    「加工未整理」の取扱があればそれに寄せる（表示「加工なし」の再取込互換）。
    """
    k = spec_key(value)
    vals = list(existing or [])
    for e in vals:
        if spec_key(e) == k:
            return e
    if k == '' and PENDING_EMBROIDERY in vals:
        return PENDING_EMBROIDERY
    return value


def normalize_embroidery(value):
    """未加工の別名を空文字にそろえる。既存の取扱キーとExcel取込の一致を保つ。"""
    t = text(value)
    if t in EMPTY_EMBROIDERY_ALIASES:
        return ''
    return t


def spec_key(value):
    """仕様の照合キー（全角半角・空白の差を無視）。保存値は変えない。"""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', normalize_embroidery(value)))


def embroidery_label(value):
    """空（および未加工の別名）の加工仕様を表示名にする。保存キーそのものは変えない。"""
    t = normalize_embroidery(value)
    if not t or t == PENDING_EMBROIDERY:
        return EMPTY_EMBROIDERY_LABEL
    if handling_kind_of(t) == HANDLING_KIND_SALE:
        # 区分が販売の取扱は「加工・販売」（加工なしは「加工なし・販売」）
        return f'{handling_processing(t) or EMPTY_EMBROIDERY_LABEL}・{HANDLING_KIND_SALE}'
    return text(value)


def need(value, label, limit=80):
    t = text(value)
    if not t:
        raise ValueError(f'{label}を入力してください。')
    if len(t) > limit:
        raise ValueError(f'{label}が長すぎます。')
    return t


def optional(value, label, limit=200):
    t = text(value)
    if len(t) > limit:
        raise ValueError(f'{label}が長すぎます。')
    return t


def flag(value):
    return 1 if value in (True, 1, '1') else 0


def parse_date(value, label):
    t = text(value)
    if not t:
        raise ValueError(f'{label}を入力してください。')
    try:
        date.fromisoformat(t)
    except ValueError:
        raise ValueError(f'{label}は日付で入力してください。')
    return t


def parse_money(value, label='仕入単価'):
    if value is None or (isinstance(value, str) and text(value) == ''):
        raise ValueError(f'{label}を入力してください。空欄を0円にはしません。')
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f'{label}は円の整数で入力してください。')
    if isinstance(value, str):
        t = text(value).replace(',', '')
        if not t.isdigit():
            raise ValueError(f'{label}は円の整数で入力してください。空欄を0円にはしません。')
        value = int(t)
    if not isinstance(value, int):
        raise ValueError(f'{label}は円の整数で入力してください。')
    if value < 0 or value > 9999999:
        raise ValueError(f'{label}は0円から9999999円までです。')
    return value


def parse_qty(value, label='数量', allow_zero=False, allow_negative=False):
    if value is None or (isinstance(value, str) and text(value) == ''):
        raise ValueError(f'{label}を入力してください。')
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f'{label}は整数で入力してください。')
    if isinstance(value, str):
        t = text(value).replace(',', '')
        if allow_negative and t.startswith('-'):
            if not t[1:].isdigit():
                raise ValueError(f'{label}は整数で入力してください。')
            value = -int(t[1:])
        elif not t.isdigit():
            raise ValueError(f'{label}は整数で入力してください。')
        else:
            value = int(t)
    if not isinstance(value, int):
        raise ValueError(f'{label}は整数で入力してください。')
    if allow_negative:
        if value < -99999 or value > 99999 or (value == 0 and not allow_zero):
            lo = 0 if allow_zero else (1 if value >= 0 else -99999)
            raise ValueError(f'{label}は{lo}から99999までです（マイナス可）。')
        return value
    if value < 0 or value > 99999 or (value == 0 and not allow_zero):
        raise ValueError(f'{label}は1から99999までです。' if not allow_zero else f'{label}は0から99999までです。')
    return value


def stamp():
    return now()


def save_customer(c, body):
    name = need(body.get('name'), '顧客名')
    kana = optional(body.get('kana'), 'フリガナ', 80)
    address = optional(body.get('address'), '住所', 200)
    phone = optional(body.get('phone'), '電話番号', 40)
    contact = optional(body.get('contact'), '担当者', 80)
    has_office = 'officeCode' in body
    office_code = optional(body.get('officeCode'), '営業所コード', 40) if has_office else ''
    active = flag(body.get('active', True))
    t = stamp()
    cid = body.get('id')
    if cid:
        row = c.execute('SELECT id FROM customers WHERE id=?', (cid,)).fetchone()
        if not row:
            raise ValueError('顧客が見つかりません。')
        other = c.execute('SELECT id FROM customers WHERE name=? AND id<>?', (name, cid)).fetchone()
        if other:
            raise ValueError('同じ名前の顧客が既にあります。名前の変更は、開いている顧客の修正として保存してください。')
        c.execute('UPDATE customers SET name=?,kana=?,address=?,phone=?,contact=?,active=?,updated_at=? WHERE id=?',
                  (name, kana, address, phone, contact, active, t, cid))
        if has_office:
            c.execute('UPDATE customers SET office_code=? WHERE id=?', (office_code, cid))
    else:
        if c.execute('SELECT id FROM customers WHERE name=?', (name,)).fetchone():
            raise ValueError('同じ名前の顧客が既にあります。新しい顧客を増やさず、一覧から修正してください。')
        c.execute('INSERT INTO customers(name,kana,address,phone,contact,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                  (name, kana, address, phone, contact, active, t, t))
        cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        if office_code:
            c.execute('UPDATE customers SET office_code=? WHERE id=?', (office_code, cid))
    return customer_row(c, cid)


def save_supplier(c, body):
    name = need(body.get('name'), '仕入先名')
    address = optional(body.get('address'), '住所', 200)
    phone = optional(body.get('phone'), '電話番号', 40)
    fax = optional(body.get('fax'), 'FAX', 40)
    contact = optional(body.get('contact'), '担当者', 80)
    active = flag(body.get('active', True))
    t = stamp()
    sid = body.get('id')
    cols = {r['name'] for r in c.execute('PRAGMA table_info(suppliers)')}
    has_fax = 'fax' in cols
    if sid:
        if not c.execute('SELECT id FROM suppliers WHERE id=?', (sid,)).fetchone():
            raise ValueError('仕入先が見つかりません。')
        if c.execute('SELECT id FROM suppliers WHERE name=? AND id<>?', (name, sid)).fetchone():
            raise ValueError('同じ名前の仕入先が既にあります。一覧から修正してください。')
        if has_fax:
            c.execute('UPDATE suppliers SET name=?,address=?,phone=?,fax=?,contact=?,active=?,updated_at=? WHERE id=?',
                      (name, address, phone, fax, contact, active, t, sid))
        else:
            c.execute('UPDATE suppliers SET name=?,address=?,phone=?,contact=?,active=?,updated_at=? WHERE id=?',
                      (name, address, phone, contact, active, t, sid))
    else:
        if c.execute('SELECT id FROM suppliers WHERE name=?', (name,)).fetchone():
            raise ValueError('同じ名前の仕入先が既にあります。一覧から修正してください。')
        if has_fax:
            c.execute('INSERT INTO suppliers(name,address,phone,fax,contact,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                      (name, address, phone, fax, contact, active, t, t))
        else:
            c.execute('INSERT INTO suppliers(name,address,phone,contact,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                      (name, address, phone, contact, active, t, t))
        sid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return supplier_row(c, sid)


def _sizes(body):
    labels = []
    for raw in body.get('sizes') or []:
        if not isinstance(raw, str):
            raise ValueError('サイズの指定が不正です。')
        label = raw.strip()
        if not label:
            continue
        if len(label) > 40:
            raise ValueError('サイズ名が長すぎます。')
        if label not in labels:
            labels.append(label)
    if not labels:
        raise ValueError('サイズを1つ以上選んでください。特殊サイズは、その場で追加できます。')
    return labels


def _links(c, body):
    links = []
    seen = set()
    standards = set()
    for raw in body.get('links') or []:
        if not isinstance(raw, dict):
            raise ValueError('顧客ごとの取扱の指定が不正です。')
        customer_id = raw.get('customerId')
        if not isinstance(customer_id, int) or not c.execute('SELECT id FROM customers WHERE id=?', (customer_id,)).fetchone():
            raise ValueError('取扱の顧客を、顧客台帳から選んでください。')
        embroidery = _link_embroidery(raw.get('embroidery'), raw.get('handlingKind'))
        process_ids = raw.get('processIds')
        if process_ids is not None:
            if not isinstance(process_ids, list):
                raise ValueError('加工サービスを候補から選んでください。')
            process_ids = _valid_process_ids(c, process_ids)
            if not embroidery and process_ids:
                # 加工仕様の文字が空なら、選んだ加工サービス名を入れる（指示書・ピッキング等の表示用）
                names = [c.execute('SELECT name FROM products WHERE id=?', (i,)).fetchone()['name'] for i in process_ids]
                embroidery = normalize_embroidery('・'.join(names)[:80])
                if raw.get('handlingKind') is not None:
                    embroidery = handling_key(embroidery, text(raw.get('handlingKind')))
        key = (customer_id, spec_key(embroidery))
        if key in seen:
            raise ValueError('同じ顧客で、同じ仕様が重複しています。')
        seen.add(key)
        standard = flag(raw.get('isStandard'))
        if standard:
            if customer_id in standards:
                raise ValueError('同じ顧客の標準仕様は1つだけにしてください。')
            standards.add(customer_id)
        links.append({'customerId': customer_id, 'embroidery': embroidery, 'isStandard': standard,
                      'active': flag(raw.get('active', True)), 'processIds': process_ids})
    return links


def _save_link_processes(c, pid, links):
    for link in links:
        if link.get('processIds') is None:
            continue
        r = c.execute('SELECT id FROM customer_products WHERE customer_id=? AND product_id=? AND embroidery=?',
                      (link['customerId'], pid, link['embroidery'])).fetchone()
        if r:
            set_handle_processes(c, r['id'], link['processIds'])


def _sub_supplier_ids(c, body, primary_id):
    """サブ仕入先ID一覧。通常仕入先と重複不可。未指定なら既存を維持しない（呼び出し側で明示）。"""
    raw = body.get('subSupplierIds')
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError('サブ仕入先の指定が不正です。')
    out = []
    seen = set()
    for item in raw:
        if item in ('', None):
            continue
        if isinstance(item, str) and item.isdigit():
            item = int(item)
        if not isinstance(item, int) or not c.execute('SELECT id FROM suppliers WHERE id=?', (item,)).fetchone():
            raise ValueError('サブ仕入先を、仕入先台帳から選んでください。')
        if primary_id is not None and item == primary_id:
            raise ValueError('通常の仕入先と同じ仕入先をサブにできません。')
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _replace_sub_suppliers(c, product_id, sub_ids, when):
    c.execute('DELETE FROM product_suppliers WHERE product_id=?', (product_id,))
    for i, sid in enumerate(sub_ids or []):
        c.execute(
            'INSERT INTO product_suppliers(product_id,supplier_id,sort_order,created_at) VALUES(?,?,?,?)',
            (product_id, sid, i, when),
        )


def save_product(c, body):
    # 品番は空欄可（品番なし商品）。「品番なし」「品番なし（要確認）」は表示用で、実品番としては保存しない。
    code = optional(body.get('code'), '品番', 40)
    if code in (BLANK_SKU_DISPLAY, BLANK_SKU_UNIFORM_DISPLAY):
        code = ''
    # メーカー品番（空＝社内品番と同じ扱い。色分割登録時のみ明示）
    if 'makerCode' in body or 'maker_code' in body:
        maker_code = optional(body.get('makerCode', body.get('maker_code')), 'メーカー品番', 40)
    else:
        maker_code = None  # 更新時は既存維持
    name = need(body.get('name'), '商品名', 80)
    color = optional(body.get('color'), '色', 40)
    category = normalize_category(body.get('category', ''))
    active = flag(body.get('active', True))
    supplier_id = body.get('supplierId')
    if supplier_id in ('', None):
        supplier_id = None
    elif not isinstance(supplier_id, int) or not c.execute('SELECT id FROM suppliers WHERE id=?', (supplier_id,)).fetchone():
        raise ValueError('通常の仕入先を、仕入先台帳から選んでください。未定なら空のままにできます。')
    sub_ids = _sub_supplier_ids(c, body, supplier_id)
    labels = _sizes(body)
    links = _links(c, body)
    t = stamp()
    pid = body.get('id')
    import stock_product_merge
    stock_product_merge.migrate_product_merge(c)
    migrate_schema(c)
    if pid:
        existing_prod = c.execute(
            'SELECT id, code, name, merged_into_id, material, maker_code FROM products WHERE id=?', (pid,)
        ).fetchone()
        if not existing_prod:
            raise ValueError('商品が見つかりません。')
        if existing_prod['merged_into_id'] not in (None, ''):
            raise ValueError(
                f'この商品は商品ID {existing_prod["merged_into_id"]} へ統合済みのため編集できません。'
            )
        # material 未指定の更新では既存値を維持（Excel等の旧呼び出し互換）
        if 'material' in body:
            material = optional(body.get('material'), '生地・素材', 80)
        else:
            material = existing_prod['material'] if 'material' in existing_prod.keys() else ''
        if maker_code is None:
            maker_code = text(existing_prod['maker_code'] or '') if 'maker_code' in existing_prod.keys() else ''
        stock_product_merge.assert_normal_code_available(
            c, code, product_id=pid,
            allow_historical_duplicate=bool(body.get('_allowHistoricalDuplicateCode')),
        )
        # 公式名を変えるとき、旧名を照合別名として残す（再取込で重複新規を防ぐ）
        if text(existing_prod['name']) != name:
            stock_product_merge.ensure_name_alias(c, pid, existing_prod['name'])
        c.execute(
            'UPDATE products SET code=?,maker_code=?,name=?,color=?,material=?,category=?,supplier_id=?,active=?,updated_at=? WHERE id=?',
            (code, maker_code, name, color, material, category, supplier_id, active, t, pid),
        )
        # 利用者が足す照合用別名（例: 加工サービスの表記ゆれ スナッパ加工 → スナッパー加工）
        extra = body.get('addNameAliases') or []
        if isinstance(extra, str):
            extra = [extra]
        for alias in extra[:20]:
            alias = text(alias)[:120]
            if alias:
                stock_product_merge.ensure_name_alias(c, pid, alias)
        existing = {r['label']: r['id'] for r in c.execute('SELECT id,label FROM product_sizes WHERE product_id=?', (pid,))}
        for label in existing:
            if label not in labels and c.execute(
                '''SELECT 1 FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
                   WHERE cp.product_id=? AND l.size_label=?''', (pid, label)).fetchone():
                raise ValueError(f'サイズ「{label}」には在庫の履歴があるため外せません。')
        for label, sid in existing.items():
            if label not in labels:
                c.execute('DELETE FROM product_sizes WHERE id=?', (sid,))
        for i, label in enumerate(labels):
            if label in existing:
                c.execute('UPDATE product_sizes SET sort_order=? WHERE id=?', (i, existing[label]))
            else:
                c.execute('INSERT INTO product_sizes(product_id,label,sort_order) VALUES(?,?,?)', (pid, label, i))
        current = {(r['customer_id'], r['embroidery']): r for r in c.execute(
            'SELECT * FROM customer_products WHERE product_id=?', (pid,))}
        # 表記ゆれ（全角半角・空白）だけの違いは既存の仕様に寄せる
        by_cust = {}
        for (cid, e) in current:
            by_cust.setdefault(cid, []).append(e)
        for link in links:
            link['embroidery'] = match_existing_spec(by_cust.get(link['customerId']), link['embroidery'])
        incoming = {(link['customerId'], link['embroidery']) for link in links}
        for key, row in current.items():
            if key not in incoming and c.execute('SELECT 1 FROM lots WHERE customer_product_id=?', (row['id'],)).fetchone():
                raise ValueError('在庫の履歴がある顧客・仕様は外せません。使用停止にして残してください。')
        for key, row in current.items():
            if key not in incoming:
                c.execute('DELETE FROM customer_products WHERE id=?', (row['id'],))
        c.execute('UPDATE customer_products SET is_standard=0 WHERE product_id=?', (pid,))
        for link in links:
            key = (link['customerId'], link['embroidery'])
            if key in current:
                c.execute('UPDATE customer_products SET is_standard=?,active=?,updated_at=? WHERE id=?',
                          (link['isStandard'], link['active'], t, current[key]['id']))
            else:
                c.execute('''INSERT INTO customer_products(customer_id,product_id,embroidery,is_standard,active,created_at,updated_at)
                             VALUES(?,?,?,?,?,?,?)''',
                          (link['customerId'], pid, link['embroidery'], link['isStandard'], link['active'], t, t))
        if sub_ids is not None:
            _replace_sub_suppliers(c, pid, sub_ids, t)
    else:
        stock_product_merge.assert_normal_code_available(
            c, code, product_id=None,
            allow_historical_duplicate=bool(body.get('_allowHistoricalDuplicateCode')),
        )
        material = optional(body.get('material'), '生地・素材', 80)
        if maker_code is None:
            maker_code = ''
        c.execute(
            'INSERT INTO products(code,maker_code,name,color,material,category,supplier_id,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (code, maker_code, name, color, material, category, supplier_id, active, t, t),
        )
        pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        for i, label in enumerate(labels):
            c.execute('INSERT INTO product_sizes(product_id,label,sort_order) VALUES(?,?,?)', (pid, label, i))
        for link in links:
            c.execute('''INSERT INTO customer_products(customer_id,product_id,embroidery,is_standard,active,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?)''',
                      (link['customerId'], pid, link['embroidery'], link['isStandard'], link['active'], t, t))
        _replace_sub_suppliers(c, pid, sub_ids or [], t)
    sync_handling_kind(c)
    _save_link_processes(c, pid, links)
    return product_row(c, pid)


def _as_positive_id(value, label):
    if isinstance(value, bool) or value in ('', None):
        raise ValueError(f'{label}を候補から選んでください。')
    if isinstance(value, int):
        vid = value
    elif isinstance(value, str) and value.isdigit():
        vid = int(value)
    else:
        raise ValueError(f'{label}を候補から選んでください。')
    if vid <= 0:
        raise ValueError(f'{label}を候補から選んでください。')
    return vid


def customer_product_payload(c, cp_id, *, already=False):
    row = c.execute('''SELECT cp.*, cu.name AS customer_name, p.code, p.name AS product_name, p.color,
                              p.supplier_id, COALESCE(s.name, '') AS supplier_name
                       FROM customer_products cp
                       JOIN customers cu ON cu.id=cp.customer_id
                       JOIN products p ON p.id=cp.product_id
                       LEFT JOIN suppliers s ON s.id=p.supplier_id
                       WHERE cp.id=?''', (cp_id,)).fetchone()
    if not row:
        raise ValueError('取扱が見つかりません。')
    return {
        'id': row['id'],
        'customerId': row['customer_id'],
        'productId': row['product_id'],
        'embroidery': row['embroidery'] or '',
        'embroideryLabel': embroidery_label(row['embroidery']),
        'processing': handling_processing(row['embroidery']),
        'handlingKind': handling_kind_of(row['embroidery']),
        'isStandard': bool(row['is_standard']),
        'active': bool(row['active']),
        'already': bool(already),
        'customerName': row['customer_name'],
        'code': row['code'],
        'productName': row['product_name'],
        'color': row['color'] or '',
        'supplierId': row['supplier_id'],
        'supplierName': row['supplier_name'] or '',
    }


def ensure_customer_product(c, body):
    """Idempotent 取扱 create: customer_id + product_id + embroidery.

    Does not create customers/products by name, does not reactivate stopped links,
    and does not rewrite existing is_standard flags on other rows.
    Saving a 取扱 alone never creates or adjusts inventory lots.
    """
    customer_id = _as_positive_id(body.get('customerId'), '顧客')
    product_id = _as_positive_id(body.get('productId'), '商品')
    embroidery = _link_embroidery(body.get('embroidery'), body.get('handlingKind'))
    customer = c.execute('SELECT id,active FROM customers WHERE id=?', (customer_id,)).fetchone()
    if not customer:
        raise ValueError('顧客が未登録です。顧客台帳で登録してから取扱を作成してください。名前だけでは自動作成しません。')
    if not customer['active']:
        raise ValueError('この顧客は使用停止です。顧客台帳で使用中に戻してから取扱を作成してください。')
    product = c.execute(
        'SELECT id,active,merged_into_id FROM products WHERE id=?', (product_id,)
    ).fetchone()
    if not product:
        raise ValueError('商品が未登録です。商品台帳で登録してから取扱を作成してください。名前だけでは自動作成しません。')
    if product['merged_into_id'] not in (None, ''):
        raise ValueError(
            f'この商品は商品ID {product["merged_into_id"]} へ統合済みです。統合先を選んでください。'
        )
    if not product['active']:
        raise ValueError('この商品は使用停止です。商品台帳で使用中に戻してから取扱を作成してください。')

    def existing_row():
        return c.execute(
            'SELECT * FROM customer_products WHERE customer_id=? AND product_id=? AND embroidery=?',
            (customer_id, product_id, embroidery),
        ).fetchone()

    row = existing_row()
    if not row:
        k = spec_key(embroidery)
        for peer in c.execute('SELECT * FROM customer_products WHERE customer_id=? AND product_id=?',
                              (customer_id, product_id)):
            if spec_key(peer['embroidery']) == k:
                row = peer
                break
    if row:
        if not row['active']:
            raise ValueError(
                '同じ顧客・商品・仕様の取扱は既にありますが、使用停止です。'
                '商品台帳で取扱を使用中に戻してから初期在庫に選んでください。自動では再開しません。'
            )
        return customer_product_payload(c, row['id'], already=True)

    peers = c.execute(
        'SELECT id FROM customer_products WHERE customer_id=? AND product_id=?',
        (customer_id, product_id),
    ).fetchall()
    is_standard = 0 if peers else 1
    t = stamp()
    try:
        c.execute(
            '''INSERT INTO customer_products(customer_id,product_id,embroidery,is_standard,active,created_at,updated_at)
               VALUES(?,?,?,?,1,?,?)''',
            (customer_id, product_id, embroidery, is_standard, t, t),
        )
    except sqlite3.IntegrityError:
        row = existing_row()
        if not row:
            raise
        if not row['active']:
            raise ValueError(
                '同じ顧客・商品・仕様の取扱は既にありますが、使用停止です。'
                '商品台帳で取扱を使用中に戻してから初期在庫に選んでください。自動では再開しません。'
            )
        return customer_product_payload(c, row['id'], already=True)
    cp_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    sync_handling_kind(c)
    return customer_product_payload(c, cp_id, already=False)


def _site_id(value):
    if value in ('', None):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError('拠点を選んでください。')


def location_has_stock(c, lid):
    """この場所に紐付く在庫ロットがあれば True（数量0でも行があれば削除不可）。"""
    return c.execute('SELECT 1 FROM lots WHERE location_id=? LIMIT 1', (lid,)).fetchone() is not None


def _insert_shelf(c, site_id, shelf_name, active, t):
    clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id=?', (shelf_name, site_id)).fetchone()
    if clash:
        raise ValueError('同じ棚・区画名がこの拠点に既にあります。新しい場所を増やさず、一覧から選んでください。')
    c.execute('INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
              (shelf_name, site_id, active, t, t))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _rename_shelf(c, shelf_id, site_id, shelf_name, active, t):
    row = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (shelf_id,)).fetchone()
    if not row or row['parent_id'] != site_id:
        raise ValueError('棚・区画が見つかりません。')
    clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id=? AND id<>?',
                      (shelf_name, site_id, shelf_id)).fetchone()
    if clash:
        raise ValueError('同じ棚・区画名がこの拠点に既にあります。一覧から選んで修正してください。')
    # 親拠点は変えない（名称変更と在庫の移動を混ぜない）
    c.execute('UPDATE locations SET name=?,active=?,updated_at=? WHERE id=?', (shelf_name, active, t, shelf_id))
    return shelf_id


def _delete_shelf(c, shelf_id, site_id):
    row = c.execute('SELECT id,parent_id,name FROM locations WHERE id=?', (shelf_id,)).fetchone()
    if not row or row['parent_id'] != site_id:
        raise ValueError('棚・区画が見つかりません。')
    if location_has_stock(c, shelf_id):
        raise ValueError(
            f'「{row["name"]}」には在庫が紐付いているため削除できません。'
            '在庫を別の場所へ移すか、使い切ってから削除してください。'
        )
    c.execute('DELETE FROM locations WHERE id=?', (shelf_id,))


def _parse_shelves(body):
    """shelves キーがあれば正規化した一覧を返す。無ければ None（単一棚API）。"""
    if 'shelves' not in body:
        return None
    raw = body.get('shelves')
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('棚・区画の一覧の形式が正しくありません。')
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError('棚・区画の一覧の形式が正しくありません。')
        shelf_id = entry.get('id')
        if shelf_id in ('', None):
            shelf_id = None
        elif isinstance(shelf_id, int):
            pass
        elif isinstance(shelf_id, str) and shelf_id.isdigit():
            shelf_id = int(shelf_id)
        else:
            raise ValueError('棚・区画が見つかりません。')
        remove = entry.get('remove') in (True, 1, '1')
        name = optional(entry.get('name', entry.get('shelfName')), '棚・区画名', 40)
        items.append({'id': shelf_id, 'name': name, 'remove': remove})
    return items


def _apply_shelves(c, site_id, shelves, active, t):
    """拠点下の棚を一括で追加・改名・削除。空欄の新規行は無視する。"""
    pending = []
    seen_names = {}
    for item in shelves:
        if item['remove']:
            if item['id'] is None:
                continue
            pending.append(item)
            continue
        if not item['name']:
            if item['id'] is not None:
                raise ValueError('棚・区画名を入力してください。')
            continue
        if item['name'] in seen_names:
            raise ValueError('同じ棚・区画名がこの拠点に既にあります。新しい場所を増やさず、一覧から選んでください。')
        seen_names[item['name']] = item
        pending.append(item)

    for item in pending:
        if item['remove']:
            _delete_shelf(c, item['id'], site_id)
            continue
        if item['id'] is not None:
            shelf_active = c.execute('SELECT active FROM locations WHERE id=?', (item['id'],)).fetchone()
            keep_active = active if shelf_active is None else shelf_active['active']
            _rename_shelf(c, item['id'], site_id, item['name'], keep_active, t)
        else:
            _insert_shelf(c, site_id, item['name'], active, t)


def _site_result(c, site_id):
    result = location_row(c, site_id)
    result['shelves'] = [
        location_row(c, r['id'])
        for r in c.execute('SELECT id FROM locations WHERE parent_id=? ORDER BY id', (site_id,))
    ]
    return result


def _resolve_or_create_site(c, body, active, t):
    """編集・既存選択・新規作成のいずれかで拠点IDを返す。"""
    lid = body.get('id')
    if lid:
        row = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (lid,)).fetchone()
        if not row:
            raise ValueError('保管場所が見つかりません。')
        if row['parent_id'] is not None:
            site_id = row['parent_id']
        else:
            site_id = lid
            name = need(body.get('siteName', body.get('name')), '拠点名', 40)
            clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL AND id<>?',
                              (name, site_id)).fetchone()
            if clash:
                raise ValueError('同じ拠点名が既にあります。一覧から選んで修正してください。')
            c.execute('UPDATE locations SET name=?,active=?,updated_at=? WHERE id=?', (name, active, t, site_id))
        return site_id

    site_id = _site_id(body.get('siteId'))
    if site_id is not None:
        parent = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (site_id,)).fetchone()
        if not parent or parent['parent_id'] is not None:
            raise ValueError('棚・区画は、登録済みの拠点の下にだけ作れます。')
        if body.get('siteName') not in ('', None):
            name = need(body.get('siteName'), '拠点名', 40)
            clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL AND id<>?',
                              (name, site_id)).fetchone()
            if clash:
                raise ValueError('同じ拠点名が既にあります。一覧から選んで修正してください。')
            c.execute('UPDATE locations SET name=?,updated_at=? WHERE id=?', (name, t, site_id))
        return site_id

    site_name = need(body.get('siteName', body.get('name')), '拠点名', 40)
    existing = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL', (site_name,)).fetchone()
    if existing:
        raise ValueError('同じ拠点名が既にあります。一覧からその拠点を選んで、棚・区画を追加してください。')
    c.execute('INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
              (site_name, None, active, t, t))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def save_location(c, body):
    """拠点名＋棚・区画。shelves 一覧があれば一括保存。単一 shelfName も互換維持。"""
    active = flag(body.get('active', True))
    t = stamp()
    shelves = _parse_shelves(body)

    if shelves is not None:
        site_id = _resolve_or_create_site(c, body, active, t)
        _apply_shelves(c, site_id, shelves, active, t)
        return _site_result(c, site_id)

    lid = body.get('id')
    if lid:
        row = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (lid,)).fetchone()
        if not row:
            raise ValueError('保管場所が見つかりません。')
        if row['parent_id'] is None:
            name = need(body.get('siteName', body.get('name')), '拠点名', 40)
            clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL AND id<>?',
                              (name, lid)).fetchone()
            if clash:
                raise ValueError('同じ拠点名が既にあります。一覧から選んで修正してください。')
            c.execute('UPDATE locations SET name=?,active=?,updated_at=? WHERE id=?', (name, active, t, lid))
        else:
            name = need(body.get('shelfName', body.get('name')), '棚・区画名', 40)
            clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id=? AND id<>?',
                              (name, row['parent_id'], lid)).fetchone()
            if clash:
                raise ValueError('同じ棚・区画名がこの拠点に既にあります。一覧から選んで修正してください。')
            # 親拠点は変えない（名称変更と在庫の移動を混ぜない）
            c.execute('UPDATE locations SET name=?,active=?,updated_at=? WHERE id=?', (name, active, t, lid))
        return location_row(c, lid)

    site_id = _site_id(body.get('siteId'))
    shelf_name = optional(body.get('shelfName'), '棚・区画名', 40)
    if site_id is not None:
        parent = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (site_id,)).fetchone()
        if not parent or parent['parent_id'] is not None:
            raise ValueError('棚・区画は、登録済みの拠点の下にだけ作れます。')
        if not shelf_name:
            raise ValueError('棚・区画名を入力してください。')
        lid = _insert_shelf(c, site_id, shelf_name, active, t)
        return location_row(c, lid)

    site_name = need(body.get('siteName', body.get('name')), '拠点名', 40)
    # 旧API互換: parentId 付きの name だけ渡された場合は棚として登録
    parent_id = body.get('parentId')
    if parent_id not in ('', None) and 'siteId' not in body and 'siteName' not in body and 'shelfName' not in body:
        parent_id = _site_id(parent_id)
        parent = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (parent_id,)).fetchone()
        if not parent or parent['parent_id'] is not None:
            raise ValueError('棚・区画は、登録済みの拠点の下にだけ作れます。')
        lid = _insert_shelf(c, parent_id, site_name, active, t)
        return location_row(c, lid)

    existing = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL', (site_name,)).fetchone()
    if existing:
        if shelf_name:
            raise ValueError('同じ拠点名が既にあります。一覧からその拠点を選んで、棚・区画を追加してください。')
        raise ValueError('同じ拠点名が既にあります。新しい場所を増やさず、一覧から選んでください。')
    c.execute('INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
              (site_name, None, active, t, t))
    site_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    if not shelf_name:
        return location_row(c, site_id)
    lid = _insert_shelf(c, site_id, shelf_name, active, t)
    return location_row(c, lid)


def _token(value):
    token = text(value)
    if len(token) < 8 or len(token) > 80:
        raise ValueError('登録の確認が途中で切れました。もう一度、内容を確認して保存してください。')
    return token


def register_opening(c, body):
    token = _token(body.get('token'))
    existing = c.execute('SELECT id FROM opening_batches WHERE client_token=?', (token,)).fetchone()
    if existing:
        return batch_result(c, existing['id'], True)
    cp_id = body.get('customerProductId')
    if not isinstance(cp_id, int):
        raise ValueError('顧客と商品の取扱を選んでください。')
    cp = c.execute('''SELECT cp.*, p.active AS product_active, cu.active AS customer_active
                      FROM customer_products cp
                      JOIN products p ON p.id=cp.product_id
                      JOIN customers cu ON cu.id=cp.customer_id
                      WHERE cp.id=?''', (cp_id,)).fetchone()
    if not cp or not cp['active'] or not cp['product_active'] or not cp['customer_active']:
        raise ValueError('使用中の顧客・商品・仕様を選んでください。')
    location_id = body.get('locationId')
    loc = c.execute('SELECT * FROM locations WHERE id=?', (location_id,)).fetchone() if isinstance(location_id, int) else None
    if not loc or not loc['active']:
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    condition = parse_condition(body.get('condition'), default=CONDITION_NEW)
    basis = parse_date(body.get('basisDate'), '在庫基準日')
    unknown = body.get('receivedUnknown') in (True, 1, '1')
    if unknown:
        if text(body.get('receivedOn')):
            raise ValueError('入荷日が不明な場合は、架空の日付を入れず「入荷日不明」のままにしてください。')
        payout = parse_qty(body.get('payoutOrder'), '払出し順', allow_zero=False)
        received = None
    else:
        received = parse_date(body.get('receivedOn'), '入荷日')
        payout = 0
    allow_zero = body.get('allowZero') in (True, 1, '1')
    allow_negative = body.get('allowNegative') in (True, 1, '1')
    sizes = {r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (cp['product_id'],))}
    cleaned = []
    seen = set()
    for raw in body.get('lines') or []:
        if not isinstance(raw, dict):
            raise ValueError('数量の表が不正です。')
        size = raw.get('size')
        if not isinstance(size, str) or size.strip() not in sizes or size != size.strip():
            raise ValueError('商品に登録したサイズだけを入力してください。新しいサイズは商品台帳で追加します。')
        qty = parse_qty(raw.get('qty'), f'{size}の数量', allow_zero=allow_zero, allow_negative=allow_negative)
        cost = parse_money(raw.get('unitCost'), f'{size}の仕入単価')
        key = (size, cost)
        if key in seen:
            raise ValueError('同じサイズで同じ仕入単価の行が重複しています。数量を1行にまとめてください。単価が違う場合は行を分けたままにしてください。')
        seen.add(key)
        cleaned.append((size, qty, cost))
    if not cleaned:
        raise ValueError('数量のある行がありません。')
    actor = _actor_name(body)
    note = optional(body.get('note'), '備考', 200) or '初期在庫'
    t = stamp()
    c.execute('INSERT INTO opening_batches(client_token,created_at) VALUES(?,?)', (token, t))
    batch_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    for size, qty, cost in cleaned:
        c.execute('''INSERT INTO lots(batch_id,customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
                     basis_date,received_on,received_unknown,payout_order,condition,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (batch_id, cp_id, size, location_id, cost, qty, qty, basis, received, 1 if unknown else 0, payout, condition, t, t))
        lot_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,location_id,actor_name,business_on,note,at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (lot_id, 'opening', qty, cost, 0, qty, None, cost, location_id, actor, basis, note, t))
    return batch_result(c, batch_id, False)


def adjust_opening_qty_diff(c, body):
    """差分調整: 既存ロットの残数を CSV 数量へ合わせる（履歴は残す・FIFO ロットを消さない）。"""
    _require_write(body)
    lot_ids = body.get('lotIds') or []
    if not isinstance(lot_ids, list) or not lot_ids or not all(isinstance(x, int) for x in lot_ids):
        raise ValueError('数量差異の対象在庫を選んでください。')
    target_qty = parse_qty(body.get('targetQty'), 'CSV数量', allow_zero=True, allow_negative=True)
    reason = need(body.get('reason'), '更新の理由', 200)
    actor = _actor_name(body)
    fingerprint = text(body.get('fingerprint'))
    lots = []
    for lid in lot_ids:
        row = c.execute('SELECT * FROM lots WHERE id=?', (lid,)).fetchone()
        if not row:
            raise ValueError('在庫が見つかりません。')
        lots.append(row)
    # Same match key for all lots in the comparison scope
    key0 = (lots[0]['customer_product_id'], lots[0]['size_label'], lots[0]['location_id'],
            lots[0]['condition'], lots[0]['unit_cost'])
    for lot in lots[1:]:
        if (lot['customer_product_id'], lot['size_label'], lot['location_id'],
                lot['condition'], lot['unit_cost']) != key0:
            raise ValueError('比較範囲の在庫キーが一致しません。')
    current = sum(int(l['qty_remaining']) for l in lots)
    expected_fp = '|'.join(f"{l['id']}:{l['qty_remaining']}" for l in sorted(lots, key=lambda r: r['id']))
    if fingerprint and fingerprint != expected_fp:
        raise StockConflict({
            'error': 'プレビュー後に在庫が変わったため、確認し直してください。',
            'code': 'stale_preview',
            'fingerprint': expected_fp,
        })
    if current == target_qty:
        raise ValueError('数量が変わっていません。')
    delta = target_qty - current
    # Apply delta to the newest lot (highest id) so older FIFO lots stay intact.
    target = max(lots, key=lambda r: r['id'])
    before = int(target['qty_remaining'])
    after = before + delta
    t = stamp()
    business_on = parse_date(body.get('businessOn') or t[:10], '更新の業務日')
    unconsumed = before == int(target['qty_received'])
    if unconsumed:
        c.execute(
            'UPDATE lots SET qty_remaining=?, qty_received=?, updated_at=? WHERE id=? AND qty_remaining=?',
            (after, after, t, target['id'], before),
        )
    else:
        c.execute(
            'UPDATE lots SET qty_remaining=?, updated_at=? WHERE id=? AND qty_remaining=?',
            (after, t, target['id'], before),
        )
    if c.execute('SELECT changes()').fetchone()[0] != 1:
        raise ValueError('同時に入出庫されたため、保存を中止しました。最新の数量を確認してください。')
    c.execute(
        '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
           location_id,actor_name,business_on,note,at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (target['id'], 'legacy_csv_adjust', delta, target['unit_cost'], before, after,
         target['unit_cost'], target['unit_cost'], target['location_id'], actor, business_on, reason, t),
    )
    fresh = list(c.execute(
        f"SELECT id,qty_remaining FROM lots WHERE id IN ({','.join('?' * len(lot_ids))}) ORDER BY id",
        lot_ids,
    ))
    return {
        'lotId': target['id'],
        'lotIds': lot_ids,
        'beforeQty': current,
        'afterQty': target_qty,
        'delta': delta,
        'fingerprint': '|'.join(f"{r['id']}:{r['qty_remaining']}" for r in fresh),
        'actorName': actor,
        'note': reason,
        'at': t,
    }


def correct_opening(c, body):
    lot_id = body.get('lotId')
    if not isinstance(lot_id, int):
        raise ValueError('訂正する在庫を選んでください。')
    lot = c.execute('SELECT * FROM lots WHERE id=?', (lot_id,)).fetchone()
    if not lot:
        raise ValueError('在庫が見つかりません。')
    kinds = {r['kind'] for r in c.execute('SELECT DISTINCT kind FROM moves WHERE lot_id=?', (lot_id,))}
    if not kinds or not kinds <= {'opening', 'opening_correction'}:
        raise ValueError('入荷・出荷・移動が入った在庫は、この初期在庫の訂正では変えられません。')
    if lot['qty_remaining'] != lot['qty_received']:
        raise ValueError('残数量と入荷数量が一致しないため、訂正を中止しました。金額は変えていません。')
    qty = parse_qty(body.get('qty'), '訂正後の数量', allow_zero=True)
    cost = parse_money(body.get('unitCost'), '訂正後の仕入単価')
    reason = need(body.get('reason'), '訂正の理由', 200)
    if qty == lot['qty_remaining'] and cost == lot['unit_cost']:
        raise ValueError('数量も単価も変わっていません。')
    t = stamp()
    before_qty = lot['qty_remaining']
    before_cost = lot['unit_cost']
    business_on = parse_date(body.get('businessOn') or t[:10], '訂正の業務日')
    c.execute('UPDATE lots SET qty_received=?,qty_remaining=?,unit_cost=?,updated_at=? WHERE id=?',
              (qty, qty, cost, t, lot_id))
    c.execute('''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,location_id,business_on,note,at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
              (lot_id, 'opening_correction', qty - before_qty, cost, before_qty, qty, before_cost, cost, lot['location_id'], business_on, reason, t))
    fresh = c.execute('SELECT qty_remaining,unit_cost FROM lots WHERE id=?', (lot_id,)).fetchone()
    if fresh['qty_remaining'] != qty or fresh['unit_cost'] != cost or fresh['qty_remaining'] * fresh['unit_cost'] != qty * cost:
        raise ValueError('訂正後の数量と金額が一致しないため、保存を中止しました。')
    return {'lotId': lot_id, 'beforeQty': before_qty, 'afterQty': qty, 'beforeCost': before_cost, 'afterCost': cost,
            'beforeAmount': before_qty * before_cost, 'afterAmount': qty * cost}


def batch_result(c, batch_id, already):
    rows = c.execute(
        'SELECT size_label,unit_cost,qty_remaining,condition FROM lots WHERE batch_id=? ORDER BY id',
        (batch_id,),
    ).fetchall()
    lines = [{
        'size': r['size_label'], 'unitCost': r['unit_cost'], 'qty': r['qty_remaining'],
        'amount': r['qty_remaining'] * r['unit_cost'],
        'condition': r['condition'], 'conditionLabel': condition_label(r['condition']),
    } for r in rows]
    condition = rows[0]['condition'] if rows else CONDITION_NEW
    return {'already': already, 'batchId': batch_id, 'lines': lines,
            'condition': condition, 'conditionLabel': condition_label(condition),
            'totalQty': sum(x['qty'] for x in lines), 'totalAmount': sum(x['amount'] for x in lines)}


def customer_row(c, cid):
    r = c.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone()
    return {'id': r['id'], 'name': r['name'], 'kana': r['kana'], 'address': r['address'], 'phone': r['phone'],
            'contact': r['contact'], 'active': bool(r['active']), 'sortKey': customer_sort_key(r['name']),
            'officeCode': (r['office_code'] if 'office_code' in r.keys() else '') or ''}


def supplier_row(c, sid):
    r = c.execute('SELECT * FROM suppliers WHERE id=?', (sid,)).fetchone()
    fax = r['fax'] if 'fax' in r.keys() else ''
    return {'id': r['id'], 'name': r['name'], 'address': r['address'], 'phone': r['phone'],
            'fax': fax or '', 'contact': r['contact'], 'active': bool(r['active'])}


def product_sub_suppliers(c, pid):
    """サブ仕入先（通常仕入先以外）。テーブル未作成の旧DBでも空配列。"""
    tables = {x['name'] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'product_suppliers' not in tables:
        return []
    rows = []
    for x in c.execute(
        '''SELECT ps.supplier_id, s.name AS supplier_name, s.active
           FROM product_suppliers ps
           JOIN suppliers s ON s.id=ps.supplier_id
           WHERE ps.product_id=?
           ORDER BY ps.sort_order, ps.id''',
        (pid,),
    ):
        rows.append({
            'id': x['supplier_id'],
            'name': x['supplier_name'] or '',
            'active': bool(x['active']),
        })
    return rows


def product_has_supplier(c, product_id, supplier_id):
    """通常仕入先またはサブ仕入先に含まれるか。"""
    if supplier_id is None:
        return False
    row = c.execute('SELECT supplier_id FROM products WHERE id=?', (product_id,)).fetchone()
    if not row:
        return False
    if row['supplier_id'] == supplier_id:
        return True
    return bool(c.execute(
        'SELECT 1 FROM product_suppliers WHERE product_id=? AND supplier_id=?',
        (product_id, supplier_id),
    ).fetchone())


def product_row(c, pid):
    r = c.execute('''SELECT p.*, s.name AS supplier_name FROM products p
                     LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?''', (pid,)).fetchone()
    sizes = [x['label'] for x in c.execute('SELECT label FROM product_sizes WHERE product_id=? ORDER BY sort_order,id', (pid,))]
    links = []
    for x in c.execute('''SELECT cp.*, cu.name AS customer_name FROM customer_products cp
                          JOIN customers cu ON cu.id=cp.customer_id WHERE cp.product_id=? ORDER BY cp.id''', (pid,)):
        links.append(_product_link_dict(x, handle_processes(c, x['id'])))
    sub_suppliers = product_sub_suppliers(c, pid)
    import stock_product_merge
    stock_product_merge.migrate_product_merge(c)
    name_aliases = stock_product_merge.list_name_aliases(c, pid)
    return _product_dict(r, sizes, links, sub_suppliers, name_aliases)


def _product_link_dict(x, processes):
    return {'id': x['id'], 'customerId': x['customer_id'], 'customerName': x['customer_name'],
            'embroidery': x['embroidery'], 'processing': handling_processing(x['embroidery']),
            'handlingKind': handling_kind_of(x['embroidery']),
            'isStandard': bool(x['is_standard']), 'active': bool(x['active']),
            'processes': processes}


def _product_dict(r, sizes, links, sub_suppliers, name_aliases):
    category = r['category'] if 'category' in r.keys() else ''
    code = r['code'] or ''
    merged_into = r['merged_into_id'] if 'merged_into_id' in r.keys() else None
    material = r['material'] if 'material' in r.keys() else ''
    maker_code = text(r['maker_code'] or '') if 'maker_code' in r.keys() else ''
    return {
        'id': r['id'], 'code': code, 'codeDisplay': product_code_display(code, category),
        'displayNo': (r['display_no'] if 'display_no' in r.keys() else '') or '',
        'codeNeedsReview': text(code) == '要確認',
        'makerCode': maker_code,
        'makerCodeDisplay': maker_code_of(code, maker_code),
        'name': r['name'], 'color': r['color'], 'material': material or '',
        'category': category or '',
        'supplierId': r['supplier_id'],
        'supplierName': r['supplier_name'] or '',
        'subSupplierIds': [s['id'] for s in sub_suppliers],
        'subSuppliers': sub_suppliers,
        'active': bool(r['active']),
        'mergedIntoId': merged_into,
        'mergedAt': r['merged_at'] if 'merged_at' in r.keys() else None,
        'sizes': sizes, 'links': links,
        'nameAliases': name_aliases,
    }


def product_rows_all(c):
    """全商品を product_row と同じ形で返す（商品ごとの問い合わせをまとめて実行）。"""
    import stock_product_merge
    stock_product_merge.migrate_product_merge(c)
    rows = c.execute('''SELECT p.*, s.name AS supplier_name FROM products p
                        LEFT JOIN suppliers s ON s.id=p.supplier_id ORDER BY p.id''').fetchall()
    sizes = {}
    for x in c.execute('SELECT product_id, label FROM product_sizes ORDER BY product_id, sort_order, id'):
        sizes.setdefault(x['product_id'], []).append(x['label'])
    processes = {}
    for x in c.execute('''SELECT x.customer_product_id, p.id, p.name FROM customer_product_processes x
                          JOIN products p ON p.id=x.process_product_id
                          ORDER BY x.customer_product_id, x.sort_order, p.id'''):
        processes.setdefault(x['customer_product_id'], []).append({'id': x['id'], 'name': x['name']})
    links = {}
    for x in c.execute('''SELECT cp.*, cu.name AS customer_name FROM customer_products cp
                          JOIN customers cu ON cu.id=cp.customer_id ORDER BY cp.product_id, cp.id'''):
        links.setdefault(x['product_id'], []).append(_product_link_dict(x, processes.get(x['id'], [])))
    subs = {}
    for x in c.execute('''SELECT ps.product_id, ps.supplier_id, s.name AS supplier_name, s.active
                          FROM product_suppliers ps JOIN suppliers s ON s.id=ps.supplier_id
                          ORDER BY ps.product_id, ps.sort_order, ps.id'''):
        subs.setdefault(x['product_id'], []).append(
            {'id': x['supplier_id'], 'name': x['supplier_name'] or '', 'active': bool(x['active'])})
    aliases = {}
    for x in c.execute('''SELECT product_id, value FROM product_match_aliases WHERE kind=? ORDER BY product_id, id''',
                       (stock_product_merge.ALIAS_KIND_NAME,)):
        aliases.setdefault(x['product_id'], []).append(x['value'])
    merged = {r['id']: r['merged_into_id'] for r in rows}

    def live(pid):
        # stock_product_merge.resolve_live_product_id と同じ辿り方
        pid = int(pid)
        seen = set()
        for _ in range(20):
            if pid in seen:
                break
            seen.add(pid)
            if pid not in merged:
                return pid
            nxt = merged[pid]
            if nxt in (None, ''):
                return pid
            pid = int(nxt)
        return pid

    return [_product_dict(r, sizes.get(r['id'], []), links.get(r['id'], []), subs.get(r['id'], []),
                          list(aliases.get(live(r['id']), []))) for r in rows]


def location_label(c, row):
    if row['parent_id']:
        parent = c.execute('SELECT name FROM locations WHERE id=?', (row['parent_id'],)).fetchone()
        return f"{parent['name']}／{row['name']}"
    return row['name']


def location_row(c, lid):
    r = c.execute('SELECT * FROM locations WHERE id=?', (lid,)).fetchone()
    return {
        'id': r['id'], 'name': r['name'], 'parentId': r['parent_id'], 'active': bool(r['active']),
        'label': location_label(c, r), 'hasStock': location_has_stock(c, lid),
    }


SHELF_PREVIEW_LIMIT = 3
SITE_ONLY_SHELF_LABEL = '棚指定なし'
SITE_STOCK_SHELF_LABEL = '棚・区画指定なし'


def format_shelf_summary(shelves, *, preview_limit=SHELF_PREVIEW_LIMIT):
    """一覧用の棚・区画表示。棚が無ければ「棚なし」、多いときは先頭＋ほか○件。"""
    if not shelves:
        return '棚なし', 0
    names = []
    for shelf in shelves:
        name = shelf.get('name') or ''
        if shelf.get('active', True) is False:
            name = f'{name}（停止）'
        names.append(name)
    if len(names) <= preview_limit:
        return '、'.join(names), 0
    extra = len(names) - preview_limit
    return '、'.join(names[:preview_limit]) + f'、ほか{extra}件', extra


def group_locations_by_site(locations, *, q='', include_stopped=False, preview_limit=SHELF_PREVIEW_LIMIT):
    """拠点ごとに1行の一覧へまとめる。棚名検索は親拠点行を返す（データは変更しない）。"""
    sites = [loc for loc in locations if loc.get('parentId') is None]
    by_parent = {}
    for loc in locations:
        parent_id = loc.get('parentId')
        if parent_id is not None:
            by_parent.setdefault(parent_id, []).append(loc)

    query = (q or '').strip()
    rows = []
    for site in sites:
        shelves = list(by_parent.get(site['id'], []))
        if not include_stopped:
            if not site.get('active', True):
                continue
            shelves = [s for s in shelves if s.get('active', True)]
        if query:
            site_hit = query in (site.get('name') or '') or query in (site.get('label') or '')
            shelf_hit = any(
                query in (s.get('name') or '') or query in (s.get('label') or '')
                for s in shelves
            )
            if not site_hit and not shelf_hit:
                continue
        summary, extra = format_shelf_summary(shelves, preview_limit=preview_limit)
        rows.append({
            'id': site['id'],
            'name': site['name'],
            'active': bool(site.get('active', True)),
            'parentId': None,
            'label': site.get('label') or site['name'],
            'hasStock': bool(site.get('hasStock')),
            'shelves': shelves,
            'shelfSummary': summary,
            'shelfExtraCount': extra,
        })
    return rows


def opening_picker_shelves(locations, site_id, *, current_location_id=None, include_stopped=False):
    """初期在庫などの拠点→棚ピッカー用。戻り値は (options, require_shelf)。
    棚が無い拠点は require_shelf=False（拠点自体が場所）。
    拠点直下に在庫がある既存参照は「棚指定なし」として残す。
    """
    if site_id in ('', None):
        return [], False
    site_id = int(site_id)
    shelves = [loc for loc in locations if loc.get('parentId') == site_id]
    if not include_stopped:
        shelves = [s for s in shelves if s.get('active', True)]
    if not shelves:
        return [], False
    options = [
        {
            'id': s['id'],
            'name': s['name'],
            'label': s.get('label') or s['name'],
            'siteOnly': False,
            'active': bool(s.get('active', True)),
        }
        for s in shelves
    ]
    if current_location_id not in ('', None) and int(current_location_id) == site_id:
        options.insert(0, {
            'id': site_id,
            'name': SITE_ONLY_SHELF_LABEL,
            'label': SITE_ONLY_SHELF_LABEL,
            'siteOnly': True,
            'active': True,
        })
    return options, True


def resolve_opening_location(site_id, shelf_id, locations):
    """拠点＋棚選択から保存用 locationId を決める。既存の拠点直下参照は維持する。"""
    if site_id in ('', None):
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    site_id = int(site_id)
    site = next((loc for loc in locations if loc['id'] == site_id and loc.get('parentId') is None), None)
    if not site:
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    active_shelves = [
        loc for loc in locations
        if loc.get('parentId') == site_id and loc.get('active', True)
    ]
    if not active_shelves:
        return site_id
    if shelf_id in ('', None):
        raise ValueError('棚・区画を選んでください。')
    shelf_id = int(shelf_id)
    if shelf_id == site_id:
        # 既存の拠点直下在庫（棚指定なし）を壊さない
        return site_id
    shelf = next(
        (loc for loc in locations if loc['id'] == shelf_id and loc.get('parentId') == site_id),
        None,
    )
    if not shelf:
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    return shelf_id


def bootstrap(c):
    customers = [customer_row(c, r['id']) for r in c.execute('SELECT id FROM customers ORDER BY id')]
    suppliers = [supplier_row(c, r['id']) for r in c.execute('SELECT id FROM suppliers ORDER BY id')]
    products = product_rows_all(c)
    locations = [location_row(c, r['id']) for r in c.execute('SELECT id FROM locations ORDER BY parent_id IS NOT NULL, id')]
    import stock_orders
    return {
        'customers': customers, 'suppliers': suppliers, 'products': products, 'locations': locations,
        'company': stock_orders.company_profile(c),
        'categoryOptions': list(PRODUCT_CATEGORY_OPTIONS),
    }


def opening_entries(c):
    """Registered 初期在庫 lots from existing stock rows (no duplicate display records)."""
    sql = '''SELECT l.*, cp.customer_id, cp.product_id, cp.embroidery,
                    cu.name AS customer_name,
                    p.code, p.name AS product_name, p.color,
                    loc.name AS location_name, loc.parent_id
             FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             WHERE EXISTS (SELECT 1 FROM moves m WHERE m.lot_id=l.id AND m.kind='opening')
             ORDER BY l.basis_date DESC, l.id DESC'''
    rows = []
    for r in c.execute(sql):
        loc = {'name': r['location_name'], 'parent_id': r['parent_id']}
        rows.append({
            'id': r['id'],
            'customerId': r['customer_id'],
            'customerName': r['customer_name'],
            'productId': r['product_id'],
            'code': r['code'],
            'productName': r['product_name'],
            'color': r['color'],
            'embroidery': r['embroidery'],
            'embroideryLabel': embroidery_label(r['embroidery']),
            'size': r['size_label'],
            'locationId': r['location_id'],
            'locationLabel': location_label(c, loc),
            'unitCost': r['unit_cost'],
            'qty': r['qty_received'],
            'basisDate': r['basis_date'],
            'receivedOn': r['received_on'],
            'receivedUnknown': bool(r['received_unknown']),
            'payoutOrder': r['payout_order'],
            'condition': r['condition'],
            'conditionLabel': condition_label(r['condition']),
        })
    return {'entries': rows}


_STOCK_LOT_SELECT = '''SELECT l.*, cp.id AS customer_product_id, cp.customer_id, cp.product_id, cp.embroidery,
                    cp.is_standard, cp.active AS handle_active,
                    cu.name AS customer_name, cu.active AS customer_active, cu.office_code AS office_code,
                    p.code, p.name AS product_name, p.color, p.active AS product_active, p.supplier_id,
                    s.name AS supplier_name, loc.name AS location_name, loc.parent_id,
                    COALESCE(parent.name, loc.name) AS site_name,
                    COALESCE(loc.parent_id, loc.id) AS site_id,
                    COALESCE(l.cost_pending,0) AS cost_pending_flag
             FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             LEFT JOIN suppliers s ON s.id=p.supplier_id
             JOIN locations loc ON loc.id=l.location_id
             LEFT JOIN locations parent ON parent.id=loc.parent_id
             WHERE 1=1'''


def _stock_lot_filters(args):
    """在庫一覧の絞り込み（stock_query と stock_lot_page で共通）。"""
    sql = ' AND l.qty_remaining<>0'
    params = []
    cond_sql, cond_params = condition_filter_sql(args)
    sql += cond_sql
    params.extend(cond_params)
    if args.get('customerId'):
        sql += ' AND cp.customer_id=?'
        params.append(int(args['customerId']))
    if args.get('productId'):
        sql += ' AND cp.product_id=?'
        params.append(int(args['productId']))
    if args.get('size'):
        sql += ' AND l.size_label=?'
        params.append(args['size'])
    spec = args.get('spec')
    if spec == 'none':
        sql += " AND cp.embroidery=''"
    elif spec:
        sql += ' AND cp.embroidery=?'
        params.append(spec)
    if args.get('locationId'):
        sql += ' AND (l.location_id=? OR loc.parent_id=?)'
        params.append(int(args['locationId']))
        params.append(int(args['locationId']))
    stock_filter = (args.get('stockFilter') or args.get('qtyFilter') or '').strip()
    if stock_filter in ('shortage', 'negative', '不足', 'マイナス'):
        sql += ' AND l.qty_remaining<0'
    return sql, params, stock_filter


def stock_query(c, args):
    sql = _STOCK_LOT_SELECT
    where, params, stock_filter = _stock_lot_filters(args)
    sql += where
    sql += ' ORDER BY customer_sort_key(cu.name), cu.name, p.code, p.id, cp.embroidery, l.size_label, l.condition, l.id'
    lots = []
    for r in c.execute(sql, params):
        loc = {'name': r['location_name'], 'parent_id': r['parent_id']}
        kinds = {m['kind'] for m in c.execute('SELECT DISTINCT kind FROM moves WHERE lot_id=?', (r['id'],))}
        unconsumed = r['qty_remaining'] == r['qty_received']
        correctable = kinds <= {'opening', 'opening_correction'} and unconsumed
        inbound_correctable = kinds <= {'inbound', 'inbound_correction'} and unconsumed
        site_only = r['parent_id'] is None
        cost_pending = bool(r['cost_pending_flag'])
        qty = r['qty_remaining']
        amount = lot_confirmed_amount(qty, r['unit_cost'], cost_pending)
        lots.append({
            'id': r['id'], 'customerProductId': r['customer_product_id'],
            'customerId': r['customer_id'], 'customerName': r['customer_name'], 'officeCode': r['office_code'] or '',
            'customerActive': bool(r['customer_active']), 'productId': r['product_id'], 'code': r['code'],
            'productName': r['product_name'], 'color': r['color'], 'supplierName': r['supplier_name'] or '',
            'embroidery': r['embroidery'], 'embroideryLabel': embroidery_label(r['embroidery']),
            'isStandard': bool(r['is_standard']), 'size': r['size_label'], 'locationId': r['location_id'],
            'locationLabel': location_label(c, loc),
            'siteId': r['site_id'], 'siteName': r['site_name'],
            'shelfLabel': SITE_STOCK_SHELF_LABEL if site_only else r['location_name'],
            'unitCost': None if cost_pending else r['unit_cost'],
            'qty': qty,
            'amount': amount,
            'costPending': cost_pending,
            'isShortage': qty < 0,
            'basisDate': r['basis_date'],
            'receivedOn': r['received_on'], 'receivedUnknown': bool(r['received_unknown']),
            'payoutOrder': r['payout_order'], 'condition': r['condition'],
            'conditionLabel': condition_label(r['condition']),
            'correctable': correctable, 'inboundCorrectable': inbound_correctable,
        })
    group = args.get('group') or 'lot'
    size_orders = (
        product_size_orders(c, {lot['productId'] for lot in lots})
        if group in ('product', 'customer', 'location') else {}
    )
    groups = group_lots(lots, group, size_orders=size_orders)
    qty_new = sum(x['qty'] for x in lots if x['condition'] == CONDITION_NEW)
    qty_used = sum(x['qty'] for x in lots if x['condition'] == CONDITION_USED)
    confirmed_lots = [x for x in lots if not x['costPending'] and x['amount'] is not None]
    pending_qty = sum(abs(x['qty']) for x in lots if x['costPending'])
    return {'lots': lots, 'groups': groups, 'group': group,
            'totalQty': sum(x['qty'] for x in lots),
            'totalQtyNew': qty_new, 'totalQtyUsed': qty_used,
            'totalAmount': sum(x['amount'] for x in confirmed_lots),
            'costPendingQty': abs(pending_qty),
            'hasShortage': any(x['qty'] < 0 for x in lots),
            'stockFilter': stock_filter if stock_filter in ('shortage', 'negative', '不足', 'マイナス') else '',
            'amountLabel': '仕入原価ベースの在庫金額（原価未確定分を除く）'}


SHORTAGE_FILTERS = ('shortage', 'negative', '不足', 'マイナス')


def _customer_rank_table(c):
    """顧客の並び順（customer_sort_key, 名前）を一時表に入れる。SQL の ORDER BY と同じ順序。"""
    c.execute('CREATE TEMP TABLE IF NOT EXISTS stock_customer_rank(customer_id INTEGER PRIMARY KEY, rk INTEGER NOT NULL)')
    c.execute('DELETE FROM temp.stock_customer_rank')
    rows = c.execute('SELECT id, name FROM customers').fetchall()
    keys = sorted({(customer_sort_key(r['name']), r['name'] is not None, r['name'] or '') for r in rows})
    rank = {k: i for i, k in enumerate(keys)}
    c.executemany(
        'INSERT INTO temp.stock_customer_rank(customer_id, rk) VALUES(?,?)',
        [(r['id'], rank[(customer_sort_key(r['name']), r['name'] is not None, r['name'] or '')]) for r in rows],
    )


def _lot_move_kinds(c, lot_ids):
    kinds = {i: set() for i in lot_ids}
    ids = list(lot_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = f"SELECT DISTINCT lot_id, kind FROM moves WHERE lot_id IN ({','.join('?' * len(chunk))})"
        for m in c.execute(q, chunk):
            kinds[m['lot_id']].add(m['kind'])
    return kinds


def stock_lot_page(c, args, lot_limit=None):
    """在庫明細（group=lot）の高速版。slim_stock_view(stock_query(...), lot_limit) と同じ結果を返す。

    合計は SQL で集計し、表示する先頭 lot_limit 件だけを取り出して行を組み立てる。
    """
    where, params, stock_filter = _stock_lot_filters(args)
    base = """FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             WHERE 1=1""" + where
    t = c.execute(
        f"""SELECT COUNT(*) AS n,
                   COALESCE(SUM(l.qty_remaining),0) AS qty,
                   COALESCE(SUM(CASE WHEN l.condition=? THEN l.qty_remaining END),0) AS qty_new,
                   COALESCE(SUM(CASE WHEN l.condition=? THEN l.qty_remaining END),0) AS qty_used,
                   COALESCE(SUM(CASE WHEN COALESCE(l.cost_pending,0)=0 AND l.unit_cost IS NOT NULL
                                     THEN l.qty_remaining*l.unit_cost END),0) AS amount,
                   COALESCE(SUM(CASE WHEN COALESCE(l.cost_pending,0)<>0
                                     THEN ABS(l.qty_remaining) END),0) AS pending_qty,
                   COALESCE(MAX(l.qty_remaining<0),0) AS has_shortage
            {base}""",
        [CONDITION_NEW, CONDITION_USED] + params,
    ).fetchone()
    _customer_rank_table(c)
    sql = (_STOCK_LOT_SELECT.replace('WHERE 1=1', 'JOIN temp.stock_customer_rank cr ON cr.customer_id=cu.id\n             WHERE 1=1')
           + where
           + ' ORDER BY cr.rk, p.code, p.id, cp.embroidery, l.size_label, l.condition, l.id')
    page_params = list(params)
    if lot_limit is not None and lot_limit >= 0:
        sql += ' LIMIT ?'
        page_params.append(lot_limit)
    rows = c.execute(sql, page_params).fetchall()
    kinds = _lot_move_kinds(c, [r['id'] for r in rows])
    lots = [_stock_lot_row(r, kinds[r['id']]) for r in rows]
    return {'lots': lots, 'groups': [], 'group': 'lot',
            'totalQty': t['qty'], 'totalQtyNew': t['qty_new'], 'totalQtyUsed': t['qty_used'],
            'totalAmount': t['amount'],
            'costPendingQty': abs(t['pending_qty']),
            'hasShortage': bool(t['has_shortage']),
            'stockFilter': stock_filter if stock_filter in SHORTAGE_FILTERS else '',
            'amountLabel': '仕入原価ベースの在庫金額（原価未確定分を除く）',
            'lotCount': t['n']}


# 顧客別・商品別・場所別で一度に返すグループ数（さらに表示で同じ数ずつ追加）
STOCK_GROUP_PAGE_SIZES = {'customer': 20, 'product': 50, 'location': 5}

# グループの並び（従来の group_lots_by_* の並びと同じ）と、ロットを絞るための列
_STOCK_GROUP_KEYS = {
    'customer': ('cu.id', 'cr.rk, cu.id'),
    'product': ('p.id', 'p.code, p.id'),
    'location': ('COALESCE(loc.parent_id, loc.id)', 'MIN(COALESCE(parent.name, loc.name)), COALESCE(loc.parent_id, loc.id)'),
}


def _stock_totals(c, where, params):
    """絞り込み後の全ロットの合計（stock_query の合計と同じ値）。"""
    return c.execute(
        f"""SELECT COUNT(*) AS n,
                   COALESCE(SUM(l.qty_remaining),0) AS qty,
                   COALESCE(SUM(CASE WHEN l.condition=? THEN l.qty_remaining END),0) AS qty_new,
                   COALESCE(SUM(CASE WHEN l.condition=? THEN l.qty_remaining END),0) AS qty_used,
                   COALESCE(SUM(CASE WHEN COALESCE(l.cost_pending,0)=0 AND l.unit_cost IS NOT NULL
                                     THEN l.qty_remaining*l.unit_cost END),0) AS amount,
                   COALESCE(SUM(CASE WHEN COALESCE(l.cost_pending,0)<>0
                                     THEN ABS(l.qty_remaining) END),0) AS pending_qty,
                   COALESCE(MAX(l.qty_remaining<0),0) AS has_shortage
            FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             WHERE 1=1{where}""",
        [CONDITION_NEW, CONDITION_USED] + params,
    ).fetchone()


def _product_size_orders_batch(c, product_ids):
    """product_size_orders と同じ結果を、まとめた問い合わせで返す。"""
    orders = {pid: {} for pid in product_ids}
    ids = list(orders)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = (f"SELECT product_id, label, sort_order FROM product_sizes WHERE product_id IN ({','.join('?' * len(chunk))})"
             ' ORDER BY product_id, sort_order, id')
        for r in c.execute(q, chunk):
            orders[r['product_id']][r['label']] = r['sort_order']
    return orders


def stock_group_page(c, args, group_limit=None, group_offset=0):
    """顧客別・商品別・場所別の高速版。グループ単位で区切り、途中で切らない。

    slim_stock_view(stock_query(...)) の groups[offset:offset+limit] と同じグループ・行・小計を返し、
    合計は絞り込み後の全件を SQL で集計する。group_limit が None / 負なら残り全部。
    """
    group = args.get('group')
    if group not in GROUPED_STOCK_VIEWS:
        raise ValueError('集計の種類が不正です。')
    where, params, stock_filter = _stock_lot_filters(args)
    t = _stock_totals(c, where, params)
    _customer_rank_table(c)
    key_sql, order_sql = _STOCK_GROUP_KEYS[group]
    group_from = """FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN temp.stock_customer_rank cr ON cr.customer_id=cu.id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             LEFT JOIN locations parent ON parent.id=loc.parent_id
             WHERE 1=1""" + where
    keys = [r[0] for r in c.execute(
        f'SELECT {key_sql} AS k {group_from} GROUP BY k ORDER BY {order_sql}', params)]
    offset = max(int(group_offset or 0), 0)
    page_keys = keys[offset:] if group_limit is None or group_limit < 0 else keys[offset:offset + group_limit]
    lots = []
    if page_keys:
        base_sql = (_STOCK_LOT_SELECT.replace('WHERE 1=1', 'JOIN temp.stock_customer_rank cr ON cr.customer_id=cu.id\n             WHERE 1=1')
                    + where)
        order = ' ORDER BY cr.rk, p.code, p.id, cp.embroidery, l.size_label, l.condition, l.id'
        rows = []
        for i in range(0, len(page_keys), 500):
            chunk = page_keys[i:i + 500]
            rows.extend(c.execute(
                base_sql + f" AND {key_sql} IN ({','.join('?' * len(chunk))})" + order, list(params) + chunk))
        # グループは1つの塊にまとまって入るので、塊をまたぐ順序は結果に影響しない（グループ内の順序は従来と同じ）
        kinds = _lot_move_kinds(c, [r['id'] for r in rows])
        lots = [_stock_lot_row(r, kinds[r['id']]) for r in rows]
    size_orders = _product_size_orders_batch(c, {lot['productId'] for lot in lots})
    body = {'groups': group_lots(lots, group, size_orders=size_orders), 'group': group,
            'totalQty': t['qty'], 'totalQtyNew': t['qty_new'], 'totalQtyUsed': t['qty_used'],
            'totalAmount': t['amount'],
            'costPendingQty': abs(t['pending_qty']),
            'hasShortage': bool(t['has_shortage']),
            'stockFilter': stock_filter if stock_filter in SHORTAGE_FILTERS else '',
            'amountLabel': '仕入原価ベースの在庫金額（原価未確定分を除く）'}
    body = slim_stock_view(body)
    body['groupCount'] = len(keys)
    body['groupOffset'] = offset
    return body


def _stock_lot_row(r, kinds):
    """在庫明細1行（場所名は JOIN の親拠点名を使い、追加の問い合わせをしない）。"""
    unconsumed = r['qty_remaining'] == r['qty_received']
    site_only = r['parent_id'] is None
    cost_pending = bool(r['cost_pending_flag'])
    qty = r['qty_remaining']
    location_label_text = r['location_name'] if site_only else f"{r['site_name']}／{r['location_name']}"
    return {
        'id': r['id'], 'customerProductId': r['customer_product_id'],
        'customerId': r['customer_id'], 'customerName': r['customer_name'], 'officeCode': r['office_code'] or '',
        'customerActive': bool(r['customer_active']), 'productId': r['product_id'], 'code': r['code'],
        'productName': r['product_name'], 'color': r['color'], 'supplierName': r['supplier_name'] or '',
        'embroidery': r['embroidery'], 'embroideryLabel': embroidery_label(r['embroidery']),
        'isStandard': bool(r['is_standard']), 'size': r['size_label'], 'locationId': r['location_id'],
        'locationLabel': location_label_text,
        'siteId': r['site_id'], 'siteName': r['site_name'],
        'shelfLabel': SITE_STOCK_SHELF_LABEL if site_only else r['location_name'],
        'unitCost': None if cost_pending else r['unit_cost'],
        'qty': qty,
        'amount': lot_confirmed_amount(qty, r['unit_cost'], cost_pending),
        'costPending': cost_pending,
        'isShortage': qty < 0,
        'basisDate': r['basis_date'],
        'receivedOn': r['received_on'], 'receivedUnknown': bool(r['received_unknown']),
        'payoutOrder': r['payout_order'], 'condition': r['condition'],
        'conditionLabel': condition_label(r['condition']),
        'correctable': kinds <= {'opening', 'opening_correction'} and unconsumed,
        'inboundCorrectable': kinds <= {'inbound', 'inbound_correction'} and unconsumed,
    }


GROUPED_STOCK_VIEWS = ('product', 'customer', 'location')


def slim_stock_view(body, lot_limit=None):
    """/api/stock 応答の軽量化（画面表示は同じ）。

    - 顧客別・商品別・場所別では画面が groups だけを使うので、平坦な lots を送らない。
    - 単価が1つで入荷も1件の行は「単価の内訳」を出さないので receipts を省く。
    """
    if body.get('group') not in GROUPED_STOCK_VIEWS:
        # 在庫明細: 絞り込み後の全件で合計を出したうえで、先頭 lot_limit 件だけ送る
        lots = body.get('lots') or []
        if lot_limit is None or lot_limit < 0:
            return dict(body, lotCount=len(lots))
        return dict(body, lots=lots[:lot_limit], lotCount=len(lots))
    out = dict(body)
    out.pop('lots', None)
    for g in out.get('groups') or []:
        for r in g.get('rows') or []:
            prices = r.get('prices') or []
            if len(prices) == 1 and len(prices[0].get('receipts') or []) <= 1:
                prices[0] = {k: v for k, v in prices[0].items() if k != 'receipts'}
    return out


def product_size_orders(c, product_ids):
    """Map productId -> {size_label: sort_order} for registered sizes."""
    orders = {}
    for pid in product_ids:
        orders[pid] = {
            r['label']: r['sort_order']
            for r in c.execute(
                'SELECT label, sort_order FROM product_sizes WHERE product_id=? ORDER BY sort_order, id',
                (pid,),
            )
        }
    return orders


def product_heading(lot):
    parts = [lot['code'], lot['productName'], lot['color']]
    return '　'.join(p for p in parts if p)


def _bump_condition_qty(row, lot):
    row['qty'] += lot['qty']
    if lot.get('amount') is not None and not lot.get('costPending'):
        row['amount'] = (row.get('amount') or 0) + lot['amount']
    if lot.get('costPending') and lot['qty']:
        row['costPendingQty'] = row.get('costPendingQty', 0) + abs(lot['qty'])
    if lot.get('qty') is not None and lot['qty'] < 0:
        row['isShortage'] = True
    if lot.get('condition') == CONDITION_NEW:
        row['qtyNew'] = row.get('qtyNew', 0) + lot['qty']
    elif lot.get('condition') == CONDITION_USED:
        row['qtyUsed'] = row.get('qtyUsed', 0) + lot['qty']
    conds = row.setdefault('conditions', set())
    if lot.get('condition'):
        conds.add(lot['condition'])


def _add_lot_price(row, lot):
    _bump_condition_qty(row, lot)
    if lot.get('customerProductId') is not None:
        row['customerProductId'] = lot['customerProductId']
    if lot.get('costPending'):
        pending = row.setdefault('pendingReceipts', [])
        pending.append({
            'lotId': lot['id'],
            'qty': lot['qty'],
            'costPending': True,
            'condition': lot.get('condition'),
            'conditionLabel': lot.get('conditionLabel') or condition_label(lot.get('condition')),
        })
        return
    key = lot['unitCost']
    price = row['prices'].setdefault(key, {
        'unitCost': lot['unitCost'], 'qty': 0, 'amount': 0, 'receipts': [],
    })
    price['qty'] += lot['qty']
    if lot.get('amount') is not None:
        price['amount'] += lot['amount']
    price['receipts'].append({
        'lotId': lot['id'],
        'qty': lot['qty'],
        'unitCost': lot['unitCost'],
        'amount': lot['amount'],
        'receivedOn': lot.get('receivedOn'),
        'receivedUnknown': bool(lot.get('receivedUnknown')),
        'payoutOrder': lot.get('payoutOrder') or 0,
        'condition': lot.get('condition'),
        'conditionLabel': lot.get('conditionLabel') or condition_label(lot.get('condition')),
    })


def _sorted_prices(row):
    prices = sorted(row['prices'].values(), key=lambda p: (p['unitCost'] if p['unitCost'] is not None else 10**9, p['qty']))
    for price in prices:
        receipts = price.get('receipts') or []
        receipts.sort(key=lambda r: (
            0 if r.get('receivedUnknown') else 1,
            r.get('payoutOrder') or 0,
            r.get('receivedOn') or '',
            0 if r.get('condition') == CONDITION_NEW else 1 if r.get('condition') == CONDITION_USED else 2,
            r.get('lotId') or 0,
        ))
        price['receipts'] = receipts
    return prices


def _qty_fields(row):
    conds = row.get('conditions') or set()
    condition = None
    if len(conds) == 1:
        condition = next(iter(conds))
    return {
        'qty': row['qty'],
        'qtyNew': row.get('qtyNew', 0),
        'qtyUsed': row.get('qtyUsed', 0),
        'amount': row.get('amount') or 0,
        'costPendingQty': row.get('costPendingQty', 0),
        'isShortage': bool(row.get('isShortage') or row['qty'] < 0),
        'customerProductId': row.get('customerProductId'),
        'condition': condition,
        'conditionMixed': len(conds) > 1,
        'conditions': sorted(conds),
    }


def group_lots_by_product(lots, size_orders=None):
    """One heading per 商品ID; rows are 顧客・加工仕様・サイズ・保管場所 with unit-price breakdown."""
    size_orders = size_orders or {}
    products = {}
    for lot in lots:
        pid = lot['productId']
        bucket = products.setdefault(pid, {
            'productId': pid,
            'code': lot['code'],
            'productName': lot['productName'],
            'color': lot['color'],
            'title': product_heading(lot),
            'qty': 0, 'qtyNew': 0, 'qtyUsed': 0,
            'amount': 0, 'costPendingQty': 0, 'conditions': set(),
            'rows': {},
        })
        _bump_condition_qty(bucket, lot)
        row_key = (lot['customerId'], lot['embroidery'], lot['size'], lot['locationId'])
        row = bucket['rows'].setdefault(row_key, {
            'customerId': lot['customerId'],
            'customerName': lot['customerName'],
            'customerProductId': lot.get('customerProductId'),
            'embroidery': lot['embroidery'],
            'embroideryLabel': lot['embroideryLabel'],
            'size': lot['size'],
            'locationId': lot['locationId'],
            'locationLabel': lot['locationLabel'],
            'qty': 0, 'qtyNew': 0, 'qtyUsed': 0,
            'amount': 0, 'costPendingQty': 0, 'conditions': set(),
            'prices': {},
        })
        _add_lot_price(row, lot)

    result = []
    for pid, bucket in products.items():
        order = size_orders.get(pid, {})
        rows = []
        for row in bucket['rows'].values():
            rows.append({
                'customerId': row['customerId'],
                'customerName': row['customerName'],
                'embroideryLabel': row['embroideryLabel'],
                'size': row['size'],
                'locationId': row['locationId'],
                'locationLabel': row['locationLabel'],
                **_qty_fields(row),
                'prices': _sorted_prices(row),
            })
        rows.sort(key=lambda r: (
            customer_sort_key(r['customerName']), r['customerName'],
            order.get(r['size'], 10_000),
            r['size'],
            r['embroideryLabel'],
            r['locationLabel'],
            r['locationId'],
        ))
        result.append({
            'title': bucket['title'],
            'productId': bucket['productId'],
            'code': bucket['code'],
            'productName': bucket['productName'],
            'color': bucket['color'],
            **_qty_fields(bucket),
            'rows': rows,
        })
    result.sort(key=lambda g: (g['code'], g['productId']))
    return result


def group_lots_by_customer(lots, size_orders=None):
    """One heading per 顧客ID; rows are 商品ID・加工仕様・サイズ・保管場所 with unit-price breakdown."""
    size_orders = size_orders or {}
    customers = {}
    for lot in lots:
        cid = lot['customerId']
        bucket = customers.setdefault(cid, {
            'customerId': cid,
            'customerName': lot['customerName'],
            'officeCode': lot.get('officeCode', ''),
            'title': lot['customerName'],
            'qty': 0, 'qtyNew': 0, 'qtyUsed': 0,
            'amount': 0, 'costPendingQty': 0, 'conditions': set(),
            'rows': {},
        })
        _bump_condition_qty(bucket, lot)
        row_key = (lot['productId'], lot['embroidery'], lot['size'], lot['locationId'])
        row = bucket['rows'].setdefault(row_key, {
            'productId': lot['productId'],
            'code': lot['code'],
            'productName': lot['productName'],
            'color': lot['color'],
            'customerProductId': lot.get('customerProductId'),
            'embroidery': lot['embroidery'],
            'embroideryLabel': lot['embroideryLabel'],
            'size': lot['size'],
            'locationId': lot['locationId'],
            'locationLabel': lot['locationLabel'],
            'qty': 0, 'qtyNew': 0, 'qtyUsed': 0,
            'amount': 0, 'costPendingQty': 0, 'conditions': set(),
            'prices': {},
        })
        _add_lot_price(row, lot)

    result = []
    for cid, bucket in customers.items():
        rows = []
        for row in bucket['rows'].values():
            rows.append({
                'productId': row['productId'],
                'code': row['code'],
                'productName': row['productName'],
                'color': row['color'],
                'embroideryLabel': row['embroideryLabel'],
                'size': row['size'],
                'locationId': row['locationId'],
                'locationLabel': row['locationLabel'],
                **_qty_fields(row),
                'prices': _sorted_prices(row),
            })
        rows.sort(key=lambda r: (
            r['code'],
            r['productId'],
            r['embroideryLabel'],
            size_orders.get(r['productId'], {}).get(r['size'], 10_000),
            r['size'],
            r['locationLabel'],
            r['locationId'],
        ))
        result.append({
            'title': bucket['title'],
            'customerId': bucket['customerId'],
            'customerName': bucket['customerName'],
            'officeCode': bucket.get('officeCode', ''),
            **_qty_fields(bucket),
            'rows': rows,
        })
    result.sort(key=lambda g: (customer_sort_key(g['customerName']), g['customerName'], g['customerId']))
    return result


def group_lots_by_location(lots, size_orders=None):
    """One heading per 拠点ID; rows are 棚・区画・顧客・商品・加工仕様・サイズ with unit-price breakdown."""
    size_orders = size_orders or {}
    sites = {}
    for lot in lots:
        sid = lot['siteId']
        bucket = sites.setdefault(sid, {
            'siteId': sid,
            'siteName': lot['siteName'],
            'title': lot['siteName'],
            'qty': 0, 'qtyNew': 0, 'qtyUsed': 0,
            'amount': 0, 'costPendingQty': 0, 'conditions': set(),
            'rows': {},
        })
        _bump_condition_qty(bucket, lot)
        row_key = (lot['locationId'], lot['customerId'], lot['productId'], lot['embroidery'], lot['size'])
        row = bucket['rows'].setdefault(row_key, {
            'locationId': lot['locationId'],
            'shelfLabel': lot['shelfLabel'],
            'customerId': lot['customerId'],
            'customerName': lot['customerName'],
            'productId': lot['productId'],
            'code': lot['code'],
            'productName': lot['productName'],
            'color': lot['color'],
            'customerProductId': lot.get('customerProductId'),
            'embroidery': lot['embroidery'],
            'embroideryLabel': lot['embroideryLabel'],
            'size': lot['size'],
            'qty': 0, 'qtyNew': 0, 'qtyUsed': 0,
            'amount': 0, 'costPendingQty': 0, 'conditions': set(),
            'prices': {},
        })
        _add_lot_price(row, lot)

    result = []
    for sid, bucket in sites.items():
        rows = []
        for row in bucket['rows'].values():
            rows.append({
                'locationId': row['locationId'],
                'shelfLabel': row['shelfLabel'],
                'customerId': row['customerId'],
                'customerName': row['customerName'],
                'productId': row['productId'],
                'code': row['code'],
                'productName': row['productName'],
                'color': row['color'],
                'embroideryLabel': row['embroideryLabel'],
                'size': row['size'],
                **_qty_fields(row),
                'prices': _sorted_prices(row),
            })
        rows.sort(key=lambda r: (
            0 if r['shelfLabel'] == SITE_STOCK_SHELF_LABEL else 1,
            r['shelfLabel'],
            r['locationId'],
            customer_sort_key(r['customerName']), r['customerName'],
            r['customerId'],
            r['code'],
            r['productId'],
            r['embroideryLabel'],
            size_orders.get(r['productId'], {}).get(r['size'], 10_000),
            r['size'],
        ))
        result.append({
            'title': bucket['title'],
            'siteId': bucket['siteId'],
            'siteName': bucket['siteName'],
            **_qty_fields(bucket),
            'rows': rows,
        })
    result.sort(key=lambda g: (g['siteName'], g['siteId']))
    return result


def group_lots(lots, group, size_orders=None):
    if group == 'lot':
        return []
    if group == 'product':
        return group_lots_by_product(lots, size_orders=size_orders)
    if group == 'customer':
        return group_lots_by_customer(lots, size_orders=size_orders)
    if group == 'location':
        return group_lots_by_location(lots, size_orders=size_orders)
    raise ValueError('集計の種類が不正です。')


def _lot_field(r, *names, default=None):
    if isinstance(r, dict):
        for name in names:
            if name in r:
                return r[name]
        return default
    keys = set(r.keys())
    for name in names:
        if name in keys:
            return r[name]
    return default


def issue_sequence(lots):
    """出荷の払出し順。入荷日不明を先にし、同日は登録順(id)。単価では並べない。"""
    def key(r):
        unknown = _lot_field(r, 'received_unknown', 'receivedUnknown', default=0)
        payout = _lot_field(r, 'payout_order', 'payoutOrder', default=0) or 0
        received = _lot_field(r, 'received_on', 'receivedOn', default='') or ''
        if unknown:
            return (0, payout, r['id'])
        return (1, received, r['id'])
    return sorted(lots, key=key)


def move_cost_pending(kind, lot_cost_pending=0, price_source=None):
    """履歴1件の金額が未確定か。不足出庫は登録単価を記録した場合のみ確定、原価未確定ロットへの入庫は未確定。"""
    if kind in ('outbound_shortage', 'stocktake_shortage'):
        return not price_source
    return kind == 'inbound' and bool(lot_cost_pending)


def registered_shortage_price(c, customer_product_id, size):
    """不足出庫時点の登録単価（特値→現単価）。なければ (0, None)。"""
    import stock_prices
    cp = c.execute('SELECT customer_id, product_id FROM customer_products WHERE id=?',
                   (customer_product_id,)).fetchone()
    if not cp:
        return 0, None
    hit = stock_prices.lookup(c, customer_id=cp['customer_id'], product_id=cp['product_id'], size_label=size)
    if hit.get('price') is None or hit.get('source') not in ('special', 'base'):
        return 0, None
    return int(hit['price']), hit['source']


def lot_history(c, lot_id):
    rows = []
    move_cols = {r['name'] for r in c.execute('PRAGMA table_info(moves)')}
    has_actor = 'actor_name' in move_cols
    lot = c.execute('SELECT COALESCE(cost_pending,0) AS p FROM lots WHERE id=?', (lot_id,)).fetchone()
    lot_pending = lot['p'] if lot else 0
    for r in c.execute('SELECT * FROM moves WHERE lot_id=? ORDER BY id', (lot_id,)):
        pending = move_cost_pending(r['kind'], lot_pending, r['price_source'] if 'price_source' in r.keys() else None)
        item = {
            'costPending': pending,
            'id': r['id'], 'kind': r['kind'], 'at': r['at'],
            'qtyDelta': r['qty_delta'],
            'qtyBefore': r['qty_before'], 'qtyAfter': r['qty_after'],
            'costBefore': r['cost_before'], 'costAfter': r['cost_after'],
            'unitCost': None if pending else r['unit_cost'],
            'note': r['note'],
            'actorName': r['actor_name'] if has_actor else '',
            'amountBefore': None if r['qty_before'] is None or r['cost_before'] is None else r['qty_before'] * r['cost_before'],
            'amountAfter': None if r['qty_after'] is None or r['cost_after'] is None else r['qty_after'] * r['cost_after'],
        }
        rows.append(item)
    return rows


def _actor_name(body):
    actor = body.get('_actor') if isinstance(body.get('_actor'), dict) else {}
    name = text(actor.get('name') or body.get('actorName'))
    return name[:80] if name else ''


def _require_write(body):
    if body.get('_canWrite') is False:
        raise ValueError('閲覧専用のため、数量の変更はできません。')


def _move_token_result(c, batch_id, already):
    batch = c.execute('SELECT * FROM move_batches WHERE id=?', (batch_id,)).fetchone()
    lines = []
    confirmed_amount = 0
    cost_pending_qty = 0
    for r in c.execute('''SELECT m.*, l.size_label, l.unit_cost AS lot_cost, l.qty_remaining, l.condition,
                                 COALESCE(l.cost_pending,0) AS cost_pending
                          FROM moves m JOIN lots l ON l.id=m.lot_id
                          WHERE m.move_batch_id=? ORDER BY m.id''', (batch_id,)):
        pending = r['kind'] in ('outbound_shortage', 'stocktake_shortage') or (
            r['kind'] == 'shortage_cover'
        )
        # shortage_cover は原価が確定する対応。outbound_shortage は未確定。
        shortage_pending = move_cost_pending(r['kind'], 0, r['price_source'])
        if r['kind'] == 'outbound_shortage' and shortage_pending:
            amount = None
            cost_pending_qty += abs(r['qty_delta'])
        elif r['kind'] == 'outbound_shortage':
            amount = abs(r['qty_delta']) * r['unit_cost']
            confirmed_amount += amount
        elif r['kind'] in ('shortage_cover', 'inbound', 'outbound', 'stocktake', 'stocktake_increase', 'stocktake_decrease'):
            if r['unit_cost'] is None:
                amount = None
            else:
                amount = abs(r['qty_delta']) * r['unit_cost']
                confirmed_amount += amount
        else:
            amount = abs(r['qty_delta']) * r['unit_cost'] if r['unit_cost'] is not None else None
            if amount is not None:
                confirmed_amount += amount
        lines.append({
            'lotId': r['lot_id'], 'kind': r['kind'], 'qty': abs(r['qty_delta']),
            'unitCost': r['unit_cost'],
            'amount': amount,
            'costPending': r['kind'] == 'outbound_shortage' and shortage_pending,
            'qtyBefore': r['qty_before'], 'qtyAfter': r['qty_after'],
            'size': r['size_label'],
            'condition': r['condition'],
            'conditionLabel': condition_label(r['condition']),
        })
    return {
        'already': already,
        'batchId': batch_id,
        'kind': batch['kind'],
        'actorName': batch['actor_name'],
        'note': batch['note'],
        'at': batch['created_at'],
        'lines': lines,
        'totalQty': sum(x['qty'] for x in lines),
        'totalAmount': confirmed_amount,
        'costPendingQty': cost_pending_qty,
    }


def resolve_customer_product(c, cp_id):
    if not isinstance(cp_id, int):
        raise ValueError('顧客と商品の取扱を選んでください。')
    cp = c.execute('''SELECT cp.*, p.active AS product_active, cu.active AS customer_active,
                             p.code, p.name AS product_name, p.color, cu.name AS customer_name
                      FROM customer_products cp
                      JOIN products p ON p.id=cp.product_id
                      JOIN customers cu ON cu.id=cp.customer_id
                      WHERE cp.id=?''', (cp_id,)).fetchone()
    if not cp or not cp['active'] or not cp['product_active'] or not cp['customer_active']:
        raise ValueError('使用中の顧客・商品・仕様を選んでください。')
    return cp


def resolve_active_location(c, location_id):
    loc = c.execute('SELECT * FROM locations WHERE id=?', (location_id,)).fetchone() if isinstance(location_id, int) else None
    if not loc or not loc['active']:
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    return loc


def resolve_size_label(c, product_id, size):
    if not isinstance(size, str) or size != size.strip() or not size.strip():
        raise ValueError('商品に登録したサイズだけを入力してください。')
    label = size.strip()
    row = c.execute('SELECT label FROM product_sizes WHERE product_id=? AND label=?', (product_id, label)).fetchone()
    if not row:
        raise ValueError('商品に登録したサイズだけを入力してください。新しいサイズは商品台帳で追加します。')
    return label


def lots_for_target(c, customer_product_id, size_label, location_id, condition=CONDITION_NEW, *, positive_only=True):
    """Lots for the same 顧客取扱・サイズ・場所・状態. positive_only=True → FIFO候補（残数>0・原価確定）。"""
    condition = parse_condition(condition, default=CONDITION_NEW)
    sql = '''SELECT * FROM lots
             WHERE customer_product_id=? AND size_label=? AND location_id=? AND condition=?'''
    params = [customer_product_id, size_label, location_id, condition]
    if positive_only:
        # 残数>0 なら原価未確定の棚卸増分も含める（単価は未確定のまま出庫可）
        sql += ' AND qty_remaining>0'
    sql += ' ORDER BY id'
    return list(c.execute(sql, params))


def shortage_lot_for_target(c, customer_product_id, size_label, location_id, condition=CONDITION_NEW):
    """原価未確定の不足ロット（同一取扱・サイズ・場所・状態で1本）。無ければ None。"""
    condition = parse_condition(condition, default=CONDITION_NEW)
    return c.execute(
        '''SELECT * FROM lots
           WHERE customer_product_id=? AND size_label=? AND location_id=? AND condition=?
             AND COALESCE(cost_pending,0)=1 AND qty_remaining<0
           ORDER BY id LIMIT 1''',
        (customer_product_id, size_label, location_id, condition),
    ).fetchone()


def target_fingerprint(lots, shortage_lot=None):
    parts = []
    for lot in sorted(lots, key=lambda r: r['id']):
        pending = 1 if _lot_field(lot, 'cost_pending', 'costPending', default=0) else 0
        parts.append(f"{lot['id']}:{lot['qty_remaining']}:{lot['unit_cost']}:{lot['condition']}:p{pending}")
    if shortage_lot is not None:
        parts.append(
            f"s{shortage_lot['id']}:{shortage_lot['qty_remaining']}:pending"
        )
    return '|'.join(parts)


def current_qty_for_target(c, customer_product_id, size_label, location_id, condition=CONDITION_NEW):
    condition = parse_condition(condition, default=CONDITION_NEW)
    row = c.execute(
        '''SELECT COALESCE(SUM(qty_remaining),0) AS qty
           FROM lots
           WHERE customer_product_id=? AND size_label=? AND location_id=? AND condition=?''',
        (customer_product_id, size_label, location_id, condition),
    ).fetchone()
    return int(row['qty'])


def lot_confirmed_amount(qty, unit_cost, cost_pending=0):
    """確定原価の金額のみ。原価未確定は 0 ではなく None（集計から除外）。"""
    if cost_pending:
        return None
    if unit_cost is None:
        return None
    return qty * unit_cost


def plan_outbound(c, body):
    """FIFO preview for the same 顧客・商品仕様・サイズ・保管場所・状態 only.

    不足分は他区分から自動補填しない。不足があってもプレビューは返す（登録時は確認必須）。
    """
    cp = resolve_customer_product(c, body.get('customerProductId'))
    size = resolve_size_label(c, cp['product_id'], body.get('size'))
    loc = resolve_active_location(c, body.get('locationId'))
    condition = parse_condition(body.get('condition'), default=CONDITION_NEW)
    qty = parse_qty(body.get('qty'), '出庫数量')
    lots = lots_for_target(c, cp['id'], size, loc['id'], condition)
    shortage = shortage_lot_for_target(c, cp['id'], size, loc['id'], condition)
    ordered = issue_sequence(lots)
    remaining = qty
    allocations = []
    for lot in ordered:
        if remaining <= 0:
            break
        take = min(remaining, lot['qty_remaining'])
        if take <= 0:
            continue
        pending = bool(_lot_field(lot, 'cost_pending', 'costPending', default=0))
        unit = None if pending else lot['unit_cost']
        amount = None if pending or unit is None else take * unit
        allocations.append({
            'lotId': lot['id'],
            'qty': take,
            'unitCost': unit,
            'amount': amount,
            'costPending': pending,
            'qtyBefore': lot['qty_remaining'],
            'qtyAfter': lot['qty_remaining'] - take,
            'receivedOn': lot['received_on'],
            'receivedUnknown': bool(lot['received_unknown']),
            'payoutOrder': lot['payout_order'],
            'condition': lot['condition'],
            'conditionLabel': condition_label(lot['condition']),
        })
        remaining -= take
    current = current_qty_for_target(c, cp['id'], size, loc['id'], condition)
    shortfall = remaining
    after_qty = current - qty
    confirmed_amount = sum(a['amount'] or 0 for a in allocations)
    return {
        'customerProductId': cp['id'],
        'customerId': cp['customer_id'],
        'customerName': cp['customer_name'],
        'productId': cp['product_id'],
        'code': cp['code'],
        'productName': cp['product_name'],
        'color': cp['color'],
        'embroidery': cp['embroidery'],
        'embroideryLabel': embroidery_label(cp['embroidery']),
        'size': size,
        'locationId': loc['id'],
        'locationLabel': location_label(c, loc),
        'condition': condition,
        'conditionLabel': condition_label(condition),
        'currentQty': current,
        'outboundQty': qty,
        'afterQty': after_qty,
        'shortfall': shortfall,
        'needsShortageConfirm': shortfall > 0,
        'shortageMessage': (
            f'登録上{shortfall}枚不足。このまま出庫しますか？（出庫後の数量は{after_qty}枚）'
            if shortfall > 0 else ''
        ),
        # ok: 指紋一致かつ（不足なし、または不足確認済み）で登録可能な状態
        'ok': shortfall == 0,
        'fingerprint': target_fingerprint(lots, shortage),
        'allocations': allocations,
        'totalAmount': confirmed_amount,
        'costPendingQty': shortfall,
        'confirmedAmount': confirmed_amount,
    }


def _inbound_cleaned_lines(c, product_id, body):
    """Validate multi-size inbound lines (or legacy single size/qty/unitCost). All-or-nothing."""
    raw_lines = body.get('lines')
    if isinstance(raw_lines, list):
        sizes = {r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (product_id,))}
        cleaned = []
        seen = set()
        for raw in raw_lines:
            if not isinstance(raw, dict):
                raise ValueError('数量の表が不正です。')
            size = raw.get('size')
            if not isinstance(size, str) or size.strip() not in sizes or size != size.strip():
                raise ValueError('商品に登録したサイズだけを入力してください。新しいサイズは商品台帳で追加します。')
            qty = parse_qty(raw.get('qty'), f'{size}の数量')
            cost = parse_money(raw.get('unitCost'), f'{size}の仕入単価')
            key = (size, cost)
            if key in seen:
                raise ValueError('同じサイズで同じ仕入単価の行が重複しています。数量を1行にまとめてください。単価が違う場合は行を分けたままにしてください。')
            seen.add(key)
            cleaned.append((size, qty, cost))
        if not cleaned:
            raise ValueError('数量のある行がありません。')
        return cleaned
    # Legacy single-size body (一覧から入庫 / 発注入荷)
    size = resolve_size_label(c, product_id, body.get('size'))
    qty = parse_qty(body.get('qty'), '入庫数量')
    cost = parse_money(body.get('unitCost'), '仕入単価')
    return [(size, qty, cost)]


def _ensure_shortage_lot(c, cp_id, size, location_id, condition, business_on, t):
    """同一対象の原価未確定不足ロットを取得、なければ作成（残数0で開始）。"""
    row = shortage_lot_for_target(c, cp_id, size, location_id, condition)
    if row:
        return row
    # 残数が0以下の cost_pending ロットがあれば再利用（棚卸の原価未確定増分は触らない）
    idle = c.execute(
        '''SELECT * FROM lots
           WHERE customer_product_id=? AND size_label=? AND location_id=? AND condition=?
             AND COALESCE(cost_pending,0)=1 AND qty_remaining<=0
           ORDER BY id LIMIT 1''',
        (cp_id, size, location_id, condition),
    ).fetchone()
    if idle:
        return idle
    c.execute(
        '''INSERT INTO lots(batch_id,customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
           basis_date,received_on,received_unknown,payout_order,condition,cost_pending,created_at,updated_at)
           VALUES(NULL,?,?,?,?,0,0,?,?,1,0,?,1,?,?)''',
        (cp_id, size, location_id, 0, business_on, None, condition, t, t),
    )
    lot_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return c.execute('SELECT * FROM lots WHERE id=?', (lot_id,)).fetchone()


def _apply_inbound_cover_shortage(c, *, cp_id, size, location_id, condition, qty, cost, received, batch_id, actor, note, t,
                                  cost_pending=False):
    """不足ロットを先に埋め、超過分だけ通常入庫ロットにする。過去出庫を二重差し引きしない。

    Returns (shortage_cover_qty, excess_lot_id_or_None, before_qty, after_qty).
    """
    before = current_qty_for_target(c, cp_id, size, location_id, condition)
    shortage = shortage_lot_for_target(c, cp_id, size, location_id, condition)
    cover = 0
    if shortage and shortage['qty_remaining'] < 0:
        cover = min(qty, -int(shortage['qty_remaining']))
        s_before = shortage['qty_remaining']
        s_after = s_before + cover
        c.execute(
            'UPDATE lots SET qty_remaining=?,updated_at=? WHERE id=? AND qty_remaining=?',
            (s_after, t, shortage['id'], s_before),
        )
        if c.execute('SELECT changes()').fetchone()[0] != 1:
            raise ValueError('同時に入出庫されたため、保存を中止しました。最新の数量を確認してください。')
        # 原価対応: 不足出庫に後から仕入単価を紐づける（確定金額として記録）
        c.execute(
            '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
               location_id,move_batch_id,actor_name,business_on,note,at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (shortage['id'], 'shortage_cover', cover, 0 if cost_pending else cost, s_before, s_after, None,
             None if cost_pending else cost,
             location_id, batch_id, actor, received,
             note or ('不足の補充（原価未確定）' if cost_pending else '不足の補充（原価対応）'), t),
        )
    excess = qty - cover
    excess_lot_id = None
    if excess > 0:
        c.execute(
            '''INSERT INTO lots(batch_id,customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
               basis_date,received_on,received_unknown,payout_order,condition,cost_pending,created_at,updated_at)
               VALUES(NULL,?,?,?,?,?,?,?,?,0,0,?,?,?,?)''',
            (cp_id, size, location_id, 0 if cost_pending else cost, excess, excess, received, received, condition,
             1 if cost_pending else 0, t, t),
        )
        excess_lot_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute(
            '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
               location_id,move_batch_id,actor_name,business_on,note,at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (excess_lot_id, 'inbound', excess, 0 if cost_pending else cost, 0, excess, None,
             None if cost_pending else cost, location_id, batch_id, actor, received,
             note or ('入庫（原価未確定）' if cost_pending else '入庫'), t),
        )
    after = before + qty
    return cover, excess_lot_id, before, after


def register_inbound(c, body, *, cost_pending=False):
    """Daily inbound: creates receipt lot(s). kind='inbound' (never opening).

    同一対象に不足（マイナス）があれば、先に不足を埋め（shortage_cover）、超過分だけ通常ロットにする。
    Accepts either legacy single size/qty/unitCost, or lines:[{size,qty,unitCost},...]
    like 初期在庫. All lines share one move_batch (one transaction + one client_token).
    """
    _require_write(body)
    token = _token(body.get('token'))
    existing = c.execute('SELECT id FROM move_batches WHERE client_token=?', (token,)).fetchone()
    if existing:
        return _move_token_result(c, existing['id'], True)
    cp = resolve_customer_product(c, body.get('customerProductId'))
    loc = resolve_active_location(c, body.get('locationId'))
    condition = parse_condition(body.get('condition'), default=CONDITION_NEW)
    cleaned = _inbound_cleaned_lines(c, cp['product_id'], body)
    received = parse_date(body.get('receivedOn'), '入荷日')
    note = optional(body.get('note'), '備考', 200)
    actor = _actor_name(body)
    single = len(cleaned) == 1
    before = None
    if single:
        before = current_qty_for_target(c, cp['id'], cleaned[0][0], loc['id'], condition)
    t = stamp()
    c.execute(
        'INSERT INTO move_batches(client_token,kind,actor_name,note,created_at) VALUES(?,?,?,?,?)',
        (token, 'inbound', actor, note, t),
    )
    batch_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    lot_id = None
    covered_total = 0
    for size, qty, cost in cleaned:
        cover, excess_lot_id, _b, _a = _apply_inbound_cover_shortage(
            c, cp_id=cp['id'], size=size, location_id=loc['id'], condition=condition,
            qty=qty, cost=cost, received=received, batch_id=batch_id, actor=actor, note=note, t=t,
            cost_pending=cost_pending,
        )
        covered_total += cover
        if excess_lot_id is not None:
            lot_id = excess_lot_id
        elif cover > 0:
            shortage = c.execute(
                '''SELECT id FROM lots WHERE customer_product_id=? AND size_label=? AND location_id=? AND condition=?
                   AND COALESCE(cost_pending,0)=1 ORDER BY id LIMIT 1''',
                (cp['id'], size, loc['id'], condition),
            ).fetchone()
            lot_id = shortage['id'] if shortage else lot_id
    result = _move_token_result(c, batch_id, False)
    result.update({
        'receivedOn': received,
        'locationId': loc['id'],
        'customerProductId': cp['id'],
        'condition': condition,
        'conditionLabel': condition_label(condition),
        'shortageCoveredQty': covered_total,
    })
    if single:
        size, qty, cost = cleaned[0]
        result.update({
            'lotId': lot_id,
            'currentQtyBefore': before,
            'currentQtyAfter': before + qty,
            'unitCost': None if cost_pending else cost,
            'size': size,
        })
    return result


def register_outbound(c, body):
    """Daily outbound with date FIFO. 不足時は confirmShortage でマイナス在庫（原価未確定）を許可。"""
    _require_write(body)
    token = _token(body.get('token'))
    existing = c.execute('SELECT id FROM move_batches WHERE client_token=?', (token,)).fetchone()
    if existing:
        return _move_token_result(c, existing['id'], True)
    plan = plan_outbound(c, body)
    confirm_shortage = body.get('confirmShortage') in (True, 1, '1')
    if plan['shortfall'] > 0 and not confirm_shortage:
        raise ValueError(plan['shortageMessage'] or f"登録上{plan['shortfall']}枚不足。このまま出庫しますか？")
    expected = text(body.get('fingerprint') or body.get('expectedFingerprint'))
    force = body.get('confirmChanged') in (True, 1, '1')
    if expected and expected != plan['fingerprint'] and not force:
        raise StockConflict({
            'error': '確認中に在庫の内訳が変わりました。内容を確認し直してください。',
            'needsReconfirm': True,
            'plan': plan,
        })
    note = optional(body.get('note'), '備考', 200)
    actor = _actor_name(body)
    t = stamp()
    business_on = parse_date(body.get('businessOn') or body.get('issuedOn') or t[:10], '出庫日')
    batch_kind = 'outbound_shortage' if plan['shortfall'] > 0 else 'outbound'
    c.execute(
        'INSERT INTO move_batches(client_token,kind,actor_name,note,created_at) VALUES(?,?,?,?,?)',
        (token, batch_kind, actor, note, t),
    )
    batch_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    for alloc in plan['allocations']:
        lot = c.execute('SELECT * FROM lots WHERE id=?', (alloc['lotId'],)).fetchone()
        if not lot or lot['qty_remaining'] < alloc['qty']:
            raise ValueError('確認中に在庫が減ったため、出庫を中止しました。最新の数量を確認してください。')
        before = lot['qty_remaining']
        after = before - alloc['qty']
        if after < 0:
            raise ValueError('確認中に在庫が減ったため、出庫を中止しました。最新の数量を確認してください。')
        c.execute(
            'UPDATE lots SET qty_remaining=?,updated_at=? WHERE id=? AND qty_remaining=?',
            (after, t, lot['id'], before),
        )
        if c.execute('SELECT changes()').fetchone()[0] != 1:
            raise ValueError('同時に出庫されたため、保存を中止しました。最新の数量を確認してください。')
        pending = bool(lot['cost_pending']) if 'cost_pending' in lot.keys() else False
        out_cost = 0 if pending else lot['unit_cost']
        c_before = None if pending else lot['unit_cost']
        c_after = None if pending else lot['unit_cost']
        c.execute(
            '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
               location_id,move_batch_id,actor_name,business_on,note,at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (lot['id'], 'outbound', -alloc['qty'], out_cost, before, after, c_before, c_after,
             lot['location_id'], batch_id, actor, business_on,
             note or ('出庫（原価未確定）' if pending else '出庫'), t),
        )
    if plan['shortfall'] > 0:
        shortage = _ensure_shortage_lot(
            c, plan['customerProductId'], plan['size'], plan['locationId'], plan['condition'], business_on, t,
        )
        s_before = shortage['qty_remaining']
        s_after = s_before - plan['shortfall']
        s_price, s_source = registered_shortage_price(c, plan['customerProductId'], plan['size'])
        c.execute(
            'UPDATE lots SET qty_remaining=?,updated_at=? WHERE id=? AND qty_remaining=?',
            (s_after, t, shortage['id'], s_before),
        )
        if c.execute('SELECT changes()').fetchone()[0] != 1:
            raise ValueError('同時に出庫されたため、保存を中止しました。最新の数量を確認してください。')
        c.execute(
            '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
               location_id,move_batch_id,actor_name,business_on,note,price_source,at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (shortage['id'], 'outbound_shortage', -plan['shortfall'], s_price, s_before, s_after, None, None,
             plan['locationId'], batch_id, actor, business_on,
             note or ('不足出庫' if s_source else '不足出庫（原価未確定）'), s_source, t),
        )
    result = _move_token_result(c, batch_id, False)
    result.update({
        'currentQtyBefore': plan['currentQty'],
        'currentQtyAfter': plan['afterQty'],
        'allocations': plan['allocations'],
        'fingerprint': plan['fingerprint'],
        'customerProductId': plan['customerProductId'],
        'size': plan['size'],
        'locationId': plan['locationId'],
        'condition': plan['condition'],
        'conditionLabel': plan['conditionLabel'],
        'shortfall': plan['shortfall'],
        'costPendingQty': plan['shortfall'],
        'confirmedAmount': plan['confirmedAmount'],
    })
    return result


def correct_inbound(c, body):
    """Correct an unconsumed daily inbound lot. Not for opening stock or consumed receipts."""
    _require_write(body)
    lot_id = body.get('lotId')
    if not isinstance(lot_id, int):
        raise ValueError('訂正する在庫を選んでください。')
    lot = c.execute('SELECT * FROM lots WHERE id=?', (lot_id,)).fetchone()
    if not lot:
        raise ValueError('在庫が見つかりません。')
    kinds = {r['kind'] for r in c.execute('SELECT DISTINCT kind FROM moves WHERE lot_id=?', (lot_id,))}
    if not kinds or not kinds <= {'inbound', 'inbound_correction'}:
        raise ValueError('出庫や初期在庫が入った入荷分は、この入庫の訂正では変えられません。初期在庫の訂正とは別です。')
    if lot['qty_remaining'] != lot['qty_received']:
        raise ValueError('すでに出庫された入荷分のため、数量・単価の訂正はできません。履歴を壊さないため中止しました。')
    qty = parse_qty(body.get('qty'), '訂正後の数量', allow_zero=True)
    cost = parse_money(body.get('unitCost'), '訂正後の仕入単価')
    reason = need(body.get('reason'), '訂正の理由', 200)
    if qty == lot['qty_remaining'] and cost == lot['unit_cost']:
        raise ValueError('数量も単価も変わっていません。')
    t = stamp()
    actor = _actor_name(body)
    before_qty = lot['qty_remaining']
    before_cost = lot['unit_cost']
    business_on = parse_date(body.get('businessOn') or t[:10], '訂正の業務日')
    c.execute(
        'UPDATE lots SET qty_received=?,qty_remaining=?,unit_cost=?,updated_at=? WHERE id=?',
        (qty, qty, cost, t, lot_id),
    )
    c.execute(
        '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
           location_id,actor_name,business_on,note,at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (lot_id, 'inbound_correction', qty - before_qty, cost, before_qty, qty, before_cost, cost,
         lot['location_id'], actor, business_on, reason, t),
    )
    return {
        'lotId': lot_id,
        'beforeQty': before_qty, 'afterQty': qty,
        'beforeCost': before_cost, 'afterCost': cost,
        'beforeAmount': before_qty * before_cost, 'afterAmount': qty * cost,
    }


def move_targets(c, args=None):
    """Aggregated targets for 入庫・出庫: 顧客・商品・加工・サイズ・場所・状態 (not split by unit cost).

    mode=in / out: 登録済み組み合わせを残数0・マイナスも含めて表示。
    Inactive masters excluded. Never auto-generates never-stocked combos.
    Amount excludes cost_pending (原価未確定) lots.
    """
    args = args or {}
    mode = (args.get('mode') or 'out').strip()
    if mode not in ('in', 'out'):
        raise ValueError('入庫または出庫の一覧を指定してください。')
    sql = '''SELECT cp.id AS customer_product_id, cp.customer_id, cp.product_id, cp.embroidery,
                    cu.name AS customer_name,
                    p.code, p.name AS product_name, p.color,
                    l.size_label, l.location_id, l.condition,
                    loc.name AS location_name, loc.parent_id,
                    COALESCE(SUM(l.qty_remaining), 0) AS qty,
                    COALESCE(SUM(CASE WHEN COALESCE(l.cost_pending,0)=0
                                      THEN l.qty_remaining * l.unit_cost ELSE 0 END), 0) AS amount,
                    COALESCE(SUM(CASE WHEN COALESCE(l.cost_pending,0)=1 AND l.qty_remaining<0
                                      THEN -l.qty_remaining ELSE 0 END), 0) AS cost_pending_qty
             FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             LEFT JOIN locations parent ON parent.id=loc.parent_id
             WHERE cu.active=1 AND p.active=1 AND cp.active=1 AND loc.active=1
               AND (parent.id IS NULL OR parent.active=1)
             GROUP BY cp.id, cp.customer_id, cp.product_id, cp.embroidery, cu.name,
                      p.code, p.name, p.color, l.size_label, l.location_id, l.condition,
                      loc.name, loc.parent_id'''
    stock_filter = (args.get('stockFilter') or '').strip()
    if stock_filter in ('shortage', 'negative', '不足', 'マイナス'):
        sql += ' HAVING COALESCE(SUM(l.qty_remaining), 0) < 0'
    rows = []
    for r in c.execute(sql):
        loc = {'name': r['location_name'], 'parent_id': r['parent_id']}
        rows.append({
            'customerId': r['customer_id'],
            'customerName': r['customer_name'],
            'productId': r['product_id'],
            'code': r['code'],
            'productName': r['product_name'],
            'color': r['color'],
            'customerProductId': r['customer_product_id'],
            'embroidery': r['embroidery'],
            'embroideryLabel': embroidery_label(r['embroidery']),
            'size': r['size_label'],
            'locationId': r['location_id'],
            'locationLabel': location_label(c, loc),
            'condition': r['condition'],
            'conditionLabel': condition_label(r['condition']),
            'qty': r['qty'],
            'amount': r['amount'],
            'costPendingQty': r['cost_pending_qty'],
            'isShortage': r['qty'] < 0,
        })
    q = text(args.get('q'))
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in ' '.join([
            r['customerName'], r['code'], r['productName'], r['color'],
            r['embroideryLabel'], r['size'], r['locationLabel'], r['conditionLabel'],
            str(r['productId']),
        ]).lower()]
    rows.sort(key=lambda r: (
        customer_sort_key(r['customerName']), r['customerName'], r['code'], r['productId'], r['embroideryLabel'], r['size'],
        r['locationLabel'], 0 if r['condition'] == CONDITION_NEW else 1, r['condition'],
    ))
    return {
        'mode': mode,
        'targets': rows,
        'totalQty': sum(r['qty'] for r in rows),
        'totalAmount': sum(r['amount'] for r in rows),
        'costPendingQty': sum(r['costPendingQty'] for r in rows),
    }


def register_stocktake(c, body):
    """棚卸調整: 実数量を入力し差分だけ増減。通常入出庫と区別する kind=stocktake*。"""
    _require_write(body)
    token = _token(body.get('token'))
    existing = c.execute('SELECT id FROM move_batches WHERE client_token=?', (token,)).fetchone()
    if existing:
        return _move_token_result(c, existing['id'], True)
    cp = resolve_customer_product(c, body.get('customerProductId'))
    size = resolve_size_label(c, cp['product_id'], body.get('size'))
    loc = resolve_active_location(c, body.get('locationId'))
    condition = parse_condition(body.get('condition'), default=CONDITION_NEW)
    actual = parse_qty(body.get('actualQty') if 'actualQty' in body else body.get('qty'), '実数量', allow_zero=True)
    before = current_qty_for_target(c, cp['id'], size, loc['id'], condition)
    delta = actual - before
    if delta == 0:
        raise ValueError('実数量が現在庫と同じです。調整の必要はありません。')
    note = optional(body.get('note') or body.get('reason'), '備考', 200)
    actor = _actor_name(body)
    t = stamp()
    business_on = parse_date(body.get('businessOn') or t[:10], '棚卸日')
    unit_cost_raw = body.get('unitCost')
    has_cost = unit_cost_raw is not None and not (isinstance(unit_cost_raw, str) and text(unit_cost_raw) == '')
    c.execute(
        'INSERT INTO move_batches(client_token,kind,actor_name,note,created_at) VALUES(?,?,?,?,?)',
        (token, 'stocktake', actor, note, t),
    )
    batch_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    if delta > 0:
        # 増分: 単価未入力なら原価未確定ロット（勝手に確定単価を付けない）
        if has_cost:
            cost = parse_money(unit_cost_raw, '仕入単価')
            c.execute(
                '''INSERT INTO lots(batch_id,customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
                   basis_date,received_on,received_unknown,payout_order,condition,cost_pending,created_at,updated_at)
                   VALUES(NULL,?,?,?,?,?,?,?,?,0,0,?,0,?,?)''',
                (cp['id'], size, loc['id'], cost, delta, delta, business_on, business_on, condition, t, t),
            )
            lot_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute(
                '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
                   location_id,move_batch_id,actor_name,business_on,note,at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (lot_id, 'stocktake', delta, cost, 0, delta, None, cost, loc['id'], batch_id, actor, business_on,
                 note or f'棚卸調整（{before}→{actual}）', t),
            )
        else:
            c.execute(
                '''INSERT INTO lots(batch_id,customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
                   basis_date,received_on,received_unknown,payout_order,condition,cost_pending,created_at,updated_at)
                   VALUES(NULL,?,?,?,?,?,?,?,?,1,0,?,1,?,?)''',
                (cp['id'], size, loc['id'], 0, delta, delta, business_on, None, condition, t, t),
            )
            lot_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute(
                '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
                   location_id,move_batch_id,actor_name,business_on,note,at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (lot_id, 'stocktake', delta, 0, 0, delta, None, None, loc['id'], batch_id, actor, business_on,
                 note or f'棚卸調整（{before}→{actual}・原価未確定）', t),
            )
    else:
        need = -delta
        lots = lots_for_target(c, cp['id'], size, loc['id'], condition)
        ordered = issue_sequence(lots)
        remaining = need
        for lot in ordered:
            if remaining <= 0:
                break
            take = min(remaining, lot['qty_remaining'])
            if take <= 0:
                continue
            l_before = lot['qty_remaining']
            l_after = l_before - take
            c.execute(
                'UPDATE lots SET qty_remaining=?,updated_at=? WHERE id=? AND qty_remaining=?',
                (l_after, t, lot['id'], l_before),
            )
            if c.execute('SELECT changes()').fetchone()[0] != 1:
                raise ValueError('同時に更新されたため、棚卸を中止しました。')
            c.execute(
                '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
                   location_id,move_batch_id,actor_name,business_on,note,at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (lot['id'], 'stocktake', -take, lot['unit_cost'], l_before, l_after, lot['unit_cost'], lot['unit_cost'],
                 loc['id'], batch_id, actor, business_on, note or f'棚卸調整（{before}→{actual}）', t),
            )
            remaining -= take
        if remaining > 0:
            shortage = _ensure_shortage_lot(c, cp['id'], size, loc['id'], condition, business_on, t)
            s_before = shortage['qty_remaining']
            s_after = s_before - remaining
            c.execute(
                'UPDATE lots SET qty_remaining=?,updated_at=? WHERE id=? AND qty_remaining=?',
                (s_after, t, shortage['id'], s_before),
            )
            if c.execute('SELECT changes()').fetchone()[0] != 1:
                raise ValueError('同時に更新されたため、棚卸を中止しました。')
            c.execute(
                '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
                   location_id,move_batch_id,actor_name,business_on,note,at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (shortage['id'], 'stocktake_shortage', -remaining, 0, s_before, s_after, None, None,
                 loc['id'], batch_id, actor, business_on, note or f'棚卸調整不足（{before}→{actual}）', t),
            )
    result = _move_token_result(c, batch_id, False)
    result.update({
        'currentQtyBefore': before,
        'currentQtyAfter': actual,
        'delta': delta,
        'actualQty': actual,
        'customerProductId': cp['id'],
        'size': size,
        'locationId': loc['id'],
        'condition': condition,
        'conditionLabel': condition_label(condition),
    })
    return result


def correct_history_move(c, body):
    """履歴詳細からの訂正。元記録は消さず、訂正前後・訂正者・訂正日時を残す。

    後続入出庫があるロットは数量直書きせず、取消（逆仕訳）で整合を取る。
    """
    _require_write(body)
    move_id = body.get('moveId')
    if not isinstance(move_id, int):
        raise ValueError('訂正する履歴を選んでください。')
    original = c.execute('SELECT * FROM moves WHERE id=?', (move_id,)).fetchone()
    if not original:
        raise ValueError('履歴が見つかりません。')
    if original['kind'] in (
        'opening_correction', 'inbound_correction', 'outbound_correction',
        'outbound_shortage_correction', 'stocktake_correction', 'move_void',
    ):
        raise ValueError('訂正記録そのものは、さらに訂正できません。元の記録を確認してください。')
    already = c.execute('SELECT id FROM moves WHERE corrects_move_id=?', (move_id,)).fetchone()
    if already:
        raise ValueError('この記録はすでに訂正済みです。')
    lot = c.execute('SELECT * FROM lots WHERE id=?', (original['lot_id'],)).fetchone()
    if not lot:
        raise ValueError('対象の在庫ロットが見つかりません。')
    later = c.execute(
        'SELECT id FROM moves WHERE lot_id=? AND id>? ORDER BY id LIMIT 1',
        (lot['id'], move_id),
    ).fetchone()
    reason = need(body.get('reason') or body.get('note'), '訂正の理由', 200)
    actor = _actor_name(body)
    t = stamp()
    business_on = parse_date(body.get('businessOn') or t[:10], '訂正の業務日')
    # 後続がある、または複数ロットにまたがる出庫バッチ: 取消（逆仕訳）のみ
    if later or original['kind'] in ('outbound', 'outbound_shortage', 'stocktake', 'stocktake_shortage', 'shortage_cover'):
        reverse_delta = -original['qty_delta']
        before = lot['qty_remaining']
        after = before + reverse_delta
        c.execute(
            'UPDATE lots SET qty_remaining=?,updated_at=? WHERE id=? AND qty_remaining=?',
            (after, t, lot['id'], before),
        )
        if c.execute('SELECT changes()').fetchone()[0] != 1:
            raise ValueError('同時に更新されたため、訂正を中止しました。')
        void_kind = 'move_void'
        c.execute(
            '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
               location_id,move_batch_id,actor_name,business_on,corrects_move_id,note,at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (lot['id'], void_kind, reverse_delta, original['unit_cost'], before, after,
             original['cost_after'], original['cost_before'], lot['location_id'], None, actor, business_on,
             move_id, reason, t),
        )
        return {
            'mode': 'void',
            'originalMoveId': move_id,
            'correctionMoveId': c.execute('SELECT last_insert_rowid()').fetchone()[0],
            'qtyBefore': before,
            'qtyAfter': after,
            'message': '後続の記録があるため、元記録を残したまま取消（逆仕訳）しました。必要なら改めて入出庫してください。',
        }
    # 未消費の入庫・初期在庫: 数量・単価の直訂正（従来どおり）と訂正行を残す
    if original['kind'] in ('inbound', 'opening'):
        qty = parse_qty(body.get('qty'), '訂正後の数量', allow_zero=True)
        cost = parse_money(body.get('unitCost'), '訂正後の仕入単価')
        if lot['qty_remaining'] != lot['qty_received']:
            raise ValueError('すでに出庫された入荷分のため、数量直書きはできません。取消で対応してください。')
        before_qty = lot['qty_remaining']
        before_cost = lot['unit_cost']
        if qty == before_qty and cost == before_cost:
            raise ValueError('数量も単価も変わっていません。')
        c.execute(
            'UPDATE lots SET qty_received=?,qty_remaining=?,unit_cost=?,updated_at=? WHERE id=?',
            (qty, qty, cost, t, lot['id']),
        )
        corr_kind = 'inbound_correction' if original['kind'] == 'inbound' else 'opening_correction'
        c.execute(
            '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
               location_id,actor_name,business_on,corrects_move_id,note,at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (lot['id'], corr_kind, qty - before_qty, cost, before_qty, qty, before_cost, cost,
             lot['location_id'], actor, business_on, move_id, reason, t),
        )
        return {
            'mode': 'rewrite',
            'originalMoveId': move_id,
            'correctionMoveId': c.execute('SELECT last_insert_rowid()').fetchone()[0],
            'beforeQty': before_qty, 'afterQty': qty,
            'beforeCost': before_cost, 'afterCost': cost,
        }
    raise ValueError('この操作の訂正には対応していません。')
