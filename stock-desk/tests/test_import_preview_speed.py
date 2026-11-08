"""取り込み高速化の回帰。合成DBのみ。実データも測定用DBも使わない。

加工名の正規化キャッシュと品番マップ索引は、プレビューの状態・理由・候補と
確定後の商品・サイズ・取扱・ロット数量を変えない。確定前の再照合、DB指紋、
未決定の拒否、権限、同じ確定トークンの再利用はそのままである。
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as proc  # noqa: E402
import stock  # noqa: E402

NOW = '2026-01-01T00:00:00'
# 品番の追加文字を属性で分けたあとの合成プレビュー射影。
CONFLICT_PREVIEW_DIGEST = (
    '34aadd6a1f78fdf07a2996ef296243340b0448a0d32b54dbc48cc6ec5b86c210'
)

LEDGER_ROWS = [
    '顧客A,仕入先X,P001,パンツ（ネイビー）,L',
    '顧客A,仕入先Y,P001,パンツ（ホワイト）,L',
    '顧客B,仕入先X,P001,パンツ（ネイビー）,M',
    '顧客A,仕入先X,7-522,作業ズボン,M',
    '顧客A,仕入先X,7-522下,作業ズボン,LL',
    '顧客A,仕入先X,AZ-8618,白衣,S',
    '顧客A,仕入先X,AZ-8618-003,白衣,M',
    '顧客A,ミドリ安全㈱,CD1,白衣,S',
    '顧客A,ミドリ安全㈱,CD1上,白衣上,M',
    '顧客A,ミドリ安全㈱,CD1下,白衣下,L',
    '顧客A,仕入先X,EMB1,白衣（刺繍）,L',
    '顧客A,仕入先X,,生地,F',
    '顧客C,仕入先X,STOP1,停止商品,L',
    '顧客C,仕入先X,MERGE1,統合元,L',
    '顧客C,仕入先X,SIZE1,シャツ,LL',
    '顧客A,仕入先K2,PROC1,ポケット取付,F',
    '顧客A,仕入先X,USED1,作業着,L',
]
OPENING_ROWS = [
    '新品,仕入先X,顧客A,パンツ（ネイビー）,P001,L,1000,0,0,4,本社,棚1,0',
    '新品,仕入先Y,顧客A,パンツ（ホワイト）,P001,L,1000,0,0,0,本社,棚1,0',
    '新品,仕入先X,顧客A,パンツ（ネイビー）,P001,M,1000,0,0,1,本社,棚1,0',
    '中古,仕入先X,顧客A,作業着,USED1,L,500,0,0,2,本社,棚1,0',
    '中古,仕入先X,顧客A,作業着,USED1,L,500,0,0,1,本社,棚1,0',
    '新品,仕入先X,顧客A,シャツ,SIZE1,LL,800,0,0,3,本社,棚1,0',
]
SHAPE_DECISIONS = {
    'extend:7-522': {'action': 'merge_to_base'},
    'midori:CD1': {
        'action': 'choose',
        'targets': {'CD1': 'CD1', 'CD1上': 'CD1上', 'CD1下': 'CD1下'},
    },
}


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode('ascii')


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


def seed_conflict(conn):
    for name in ('仕入先X', '仕入先Y', 'ミドリ安全㈱', '仕入先K2'):
        conn.execute(
            '''INSERT INTO suppliers(name,address,phone,fax,contact,active,created_at,updated_at)
               VALUES(?,?,?,?,?,1,?,?)''',
            (name, '', '', '', '', NOW, NOW),
        )
    for name in ('顧客A', '顧客B', '顧客C'):
        conn.execute(
            '''INSERT INTO customers(name,kana,address,phone,contact,active,created_at,updated_at)
               VALUES(?,?,?,?,?,1,?,?)''',
            (name, '', '', '', '', NOW, NOW),
        )
    sup = {r['name']: r['id'] for r in conn.execute('SELECT id,name FROM suppliers')}
    cust = {r['name']: r['id'] for r in conn.execute('SELECT id,name FROM customers')}

    def add_product(code, name, supplier, active=1, merged=None, sizes=('L',)):
        conn.execute(
            '''INSERT INTO products(
                 code,maker_code,name,color,material,category,supplier_id,active,
                 merged_into_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (code, '', name, '', '', '制服', sup[supplier], active, merged, NOW, NOW),
        )
        pid = conn.execute('SELECT last_insert_rowid() id').fetchone()['id']
        for i, label in enumerate(sizes):
            conn.execute(
                'INSERT INTO product_sizes(product_id,label,sort_order) VALUES(?,?,?)',
                (pid, label, i),
            )
        return pid

    keep = add_product('KEEP1', '統合先', '仕入先X')
    add_product('MERGE1', '統合元', '仕入先X', active=0, merged=keep)
    add_product('STOP1', '停止商品', '仕入先X', active=0)
    add_product('SIZE1', 'シャツ', '仕入先X', sizes=('S',))
    conn.execute(
        '''INSERT INTO customer_products(
             customer_id,product_id,embroidery,is_standard,active,created_at,updated_at)
           VALUES(?,?,?,1,1,?,?)''',
        (cust['顧客C'], keep, '', NOW, NOW),
    )
    conn.execute(
        'INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,NULL,1,?,?)',
        ('本社', NOW, NOW),
    )
    site = conn.execute('SELECT last_insert_rowid() id').fetchone()['id']
    conn.execute(
        'INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,1,?,?)',
        ('棚1', site, NOW, NOW),
    )
    conn.commit()


def ledger_text():
    return '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(LEDGER_ROWS)


def opening_text():
    header = '新古,仕入先,顧客,商品名,商品コード,サイズ,仕入単価,購入数量,出荷数,在庫,拠点,棚,補正在庫数'
    return header + '\n' + '\n'.join(OPENING_ROWS)


def product_body(rows=None):
    text = ledger_text() if rows is None else (
        '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(rows)
    )
    return {
        'importMode': 'products',
        'name': 'ledger.csv',
        'file': b64(text),
        'auxName': 'opening.csv',
        'auxFile': b64(opening_text()),
        'registerOpeningStock': False,
        '_canWrite': True,
        '_actor': {'name': '計測'},
    }


def project(preview):
    rows = []
    for row in preview.get('rows') or []:
        prod = row.get('product') or {}
        handle = row.get('handle') or {}
        cands = []
        for cand in prod.get('candidates') or []:
            if isinstance(cand, dict):
                cands.append({
                    'id': cand.get('id'),
                    'code': cand.get('code'),
                    'name': cand.get('name'),
                    'active': cand.get('active'),
                })
        rows.append({
            'rowNo': row.get('rowNo'),
            'status': row.get('status'),
            'label': row.get('statusLabel'),
            'reasons': row.get('reasons'),
            'csv': {
                k: (row.get('csv') or {}).get(k)
                for k in ('customer', 'supplier', 'code', 'productName', 'size')
            },
            'product': {
                k: prod.get(k)
                for k in ('id', 'action', 'code', 'name', 'officialName', 'category', 'size', 'sizeAction')
            },
            'candidates': cands,
            'handle': {k: handle.get(k) for k in ('id', 'action', 'embroidery')},
        })
    groups = []
    for group in preview.get('skuGroups') or []:
        groups.append({
            'code': group.get('code'),
            'status': group.get('status'),
            'needs': group.get('needsDecision'),
            'decision': group.get('decision'),
            'auto': group.get('auxStockPickAuto'),
            'reason': group.get('auxStockPickReason'),
            'name': group.get('plannedOfficialName'),
            'color': group.get('plannedColor') or group.get('plannedRegisteredColor'),
            'supplier': group.get('plannedPrimarySupplier'),
        })
    shapes = []
    for group in preview.get('codeShapeGroups') or []:
        shapes.append({
            'id': group.get('id'),
            'members': [m.get('code') for m in group.get('members') or []],
            'sizes': group.get('sizeUnion'),
        })
    opening = []
    for plan in ((preview.get('openingStock') or {}).get('plans') or []):
        target = plan.get('target') or {}
        opening.append({
            'rowNo': plan.get('rowNo'),
            'status': plan.get('status'),
            'qty': plan.get('qty'),
            'reasons': plan.get('reasons'),
            'pid': target.get('productId'),
            'hid': target.get('handleId'),
            'size': target.get('size'),
            'cond': plan.get('condition'),
            'merged': plan.get('mergedInto'),
        })
    return {
        'counts': preview.get('countsRaw'),
        'rows': rows,
        'groups': groups,
        'shapes': shapes,
        'opening': opening,
        'openCounts': (preview.get('openingStock') or {}).get('counts'),
    }


def digest(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def master_snapshot(conn):
    products = [
        tuple(r) for r in conn.execute(
            '''SELECT code,name,category,active,merged_into_id
               FROM products ORDER BY code,id'''
        )
    ]
    sizes = [
        tuple(r) for r in conn.execute(
            '''SELECT p.code, ps.label
               FROM product_sizes ps JOIN products p ON p.id=ps.product_id
               ORDER BY p.code, ps.sort_order, ps.label'''
        )
    ]
    handles = [
        tuple(r) for r in conn.execute(
            '''SELECT cu.name, p.code, cp.embroidery, cp.active
               FROM customer_products cp
               JOIN customers cu ON cu.id=cp.customer_id
               JOIN products p ON p.id=cp.product_id
               ORDER BY cu.name, p.code, cp.embroidery, cp.id'''
        )
    ]
    lots = [
        tuple(r) for r in conn.execute(
            '''SELECT p.code, l.size_label, l.condition, l.qty_received, l.qty_remaining, l.unit_cost
               FROM lots l
               JOIN customer_products cp ON cp.id=l.customer_product_id
               JOIN products p ON p.id=cp.product_id
               ORDER BY p.code, l.size_label, l.condition, l.id'''
        )
    ]
    return {'products': products, 'sizes': sizes, 'handles': handles, 'lots': lots}


class ServiceNameCacheTest(unittest.TestCase):
    def tearDown(self):
        proc.clear_service_name_cache()

    def test_cached_key_matches_body_including_edges(self):
        samples = [
            '', '  ', None, 'ポケット取り付け', 'ポケット取付け', '＊刺繍',
            'エプロン　カン止', '反射テープ 10㎜', 'スナッパー', 'スナッパ',
            '白衣（刺繍）', 'DCP515用生地（袖／裾加工用）', '７−５２２下',
            'A' * 600,
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(
                    proc.normalize_service_key(sample),
                    proc._normalize_service_key_body(sample),
                )
                self.assertEqual(
                    proc.canonicalize_attach_verbs(sample),
                    proc._canonicalize_attach_verbs_body(sample),
                )
        proc._norm_cache.clear()
        for i in range(proc._NORM_CACHE_MAX + 10):
            proc.normalize_service_key(f'cache-fill-{i}')
        self.assertLessEqual(len(proc._norm_cache), proc._NORM_CACHE_MAX)
        self.assertEqual(
            proc.normalize_service_key('ポケット縫い付け'),
            proc._normalize_service_key_body('ポケット縫い付け'),
        )
        with self.assertRaises(AttributeError):
            proc.normalize_service_key(1)
        with self.assertRaises(AttributeError):
            proc._normalize_service_key_body(1)

    def test_compiled_alias_map_matches_fresh_comprehension(self):
        fresh = {
            proc._normalize_service_key_body(key): display
            for key, display in proc._SERVICE_NAME_ALIASES.items()
        }
        self.assertEqual(dict(proc._alias_map()), fresh)
        fresh_values = [
            (proc._normalize_service_key_body(display), display)
            for display in proc._SERVICE_NAME_ALIASES.values()
        ]
        self.assertEqual(list(proc._ensure_service_index()['value_keys']), fresh_values)
        fresh_terms = [
            (term, proc._normalize_service_key_body(term))
            for term in proc._SERVICE_NAME_CANDIDATES
        ]
        self.assertEqual(list(proc._ensure_service_index()['candidates']), fresh_terms)

    def test_replacing_alias_table_object_rebuilds(self):
        original = proc._SERVICE_NAME_ALIASES
        try:
            proc._SERVICE_NAME_ALIASES = {'テスト別名': 'テスト正規'}
            self.assertEqual(
                proc._alias_map()[proc.normalize_service_key('テスト別名')],
                'テスト正規',
            )
            proc._SERVICE_NAME_ALIASES['その場'] = '書き換えた正規'
            self.assertNotIn(
                proc._normalize_service_key_body('その場'),
                proc._alias_map(),
            )
            proc.clear_service_name_cache()
            self.assertEqual(
                proc._alias_map()[proc.normalize_service_key('その場')],
                '書き換えた正規',
            )
        finally:
            proc._SERVICE_NAME_ALIASES = original
            proc.clear_service_name_cache()


class ConflictPreviewParityTest(unittest.TestCase):
    def tearDown(self):
        proc.clear_service_name_cache()

    def _preview(self):
        folder, _path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        seed_conflict(conn)
        return conn, pli.build_preview(conn, product_body())

    def test_preview_matches_precached_main_digest(self):
        _conn, preview = self._preview()
        self.assertEqual(preview.get('skuDecisionPending'), 0)
        shape_ids = [g['id'] for g in preview['codeShapeGroups']]
        self.assertEqual(shape_ids, ['midori:CD1'])
        self.assertNotIn('extend:AZ-8618', shape_ids)
        picked = [
            g for g in preview['skuGroups']
            if g.get('code') == 'P001' and g.get('auxStockPickAuto')
        ]
        self.assertEqual(len(picked), 1)
        self.assertIn('在庫', picked[0].get('auxStockPickReason') or '')
        self.assertEqual(digest(project(preview)), CONFLICT_PREVIEW_DIGEST)

    def test_uncached_normalize_matches_cached_preview_and_commit(self):
        original_norm = proc.normalize_service_key
        original_verb = proc.canonicalize_attach_verbs

        def uncached_preview_and_commit():
            proc.normalize_service_key = proc._normalize_service_key_body
            proc.canonicalize_attach_verbs = proc._canonicalize_attach_verbs_body
            proc.clear_service_name_cache()
            try:
                return self._commit_pair()
            finally:
                proc.normalize_service_key = original_norm
                proc.canonicalize_attach_verbs = original_verb
                proc.clear_service_name_cache()

        slow_preview, slow_master, slow_open = uncached_preview_and_commit()
        fast_preview, fast_master, fast_open = self._commit_pair()
        self.assertEqual(digest(project(slow_preview)), digest(project(fast_preview)))
        self.assertEqual(digest(project(slow_open)), digest(project(fast_open)))
        self.assertEqual(slow_master, fast_master)

    def _commit_pair(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        seed_conflict(conn)
        # 停止中・統合元への取扱作成は確定で拒否されるため、確定比較の台帳からは外す。
        # プレビュー一致の試験側には残してある。
        commitable = [
            row for row in LEDGER_ROWS
            if ',STOP1,' not in f',{row},' and ',MERGE1,' not in f',{row},'
        ]
        body = product_body(commitable)
        preview = pli.build_preview(conn, body)
        with self.assertRaises(ValueError) as caught:
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'speed-undecided',
            }, db_path=str(path))
        self.assertIn('未決定', str(caught.exception))
        conn.rollback()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) n FROM products WHERE code='7-522'").fetchone()['n'],
            0,
        )
        result = pli.commit_preview(conn, {
            **body,
            'previewFingerprint': preview['previewFingerprint'],
            'previewAt': preview['previewAt'],
            'token': 'speed-products',
            'codeShapeDecisions': SHAPE_DECISIONS,
        }, db_path=str(path))
        conn.commit()
        self.assertTrue(result.get('stockUnchanged'))
        again = pli.commit_preview(conn, {
            **body,
            'previewFingerprint': preview['previewFingerprint'],
            'previewAt': preview['previewAt'],
            'token': 'speed-products',
            'codeShapeDecisions': SHAPE_DECISIONS,
        }, db_path=str(path))
        self.assertTrue(again.get('already'))
        codes = {r['code'] for r in conn.execute('SELECT code FROM products')}
        self.assertIn('7-522', codes)
        self.assertNotIn('7-522下', codes)
        self.assertIn('AZ-8618', codes)
        self.assertIn('AZ-8618-003', codes)
        self.assertTrue({'CD1', 'CD1上', 'CD1下'} <= codes)
        sizes = {
            r['label'] for r in conn.execute(
                '''SELECT label FROM product_sizes ps
                   JOIN products p ON p.id=ps.product_id WHERE p.code='SIZE1' '''
            )
        }
        self.assertEqual(sizes, {'S', 'LL'})
        obody = {
            'importMode': 'opening_stock',
            'auxName': 'opening.csv',
            'auxFile': b64(opening_text()),
            'basisDate': '2026-01-01',
            'defaultSiteName': '未確認',
            '_canWrite': True,
            '_actor': {'name': '計測'},
        }
        opening = pli.build_preview(conn, obody)
        pli.commit_preview(conn, {
            **obody,
            'previewFingerprint': opening['previewFingerprint'],
            'previewAt': opening['previewAt'],
            'token': 'speed-opening',
        }, db_path=str(path))
        conn.commit()
        used = conn.execute(
            '''SELECT COALESCE(SUM(l.qty_remaining),0) q
               FROM lots l
               JOIN customer_products cp ON cp.id=l.customer_product_id
               JOIN products p ON p.id=cp.product_id
               WHERE p.code='USED1' '''
        ).fetchone()['q']
        self.assertEqual(used, 3)
        master = master_snapshot(conn)
        again_open = pli.build_preview(conn, obody)
        self.assertEqual(master_snapshot(conn), master)
        return preview, master, again_open

    def test_stopped_product_is_still_rejected(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        seed_conflict(conn)
        body = product_body()
        preview = pli.build_preview(conn, body)
        with self.assertRaises(ValueError) as stopped:
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'speed-stopped',
                'codeShapeDecisions': SHAPE_DECISIONS,
            }, db_path=str(path))
        self.assertIn('使用停止', str(stopped.exception))

    def test_stale_fingerprint_and_viewer_are_rejected(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        seed_conflict(conn)
        body = product_body()
        preview = pli.build_preview(conn, body)
        conn.execute(
            '''INSERT INTO customers(name,kana,address,phone,contact,active,created_at,updated_at)
               VALUES(?,?,?,?,?,1,?,?)''',
            ('別顧客', '', '', '', '', NOW, NOW),
        )
        with self.assertRaises(stock.StockConflict):
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'speed-stale',
                'codeShapeDecisions': SHAPE_DECISIONS,
            }, db_path=str(path))
        denied = dict(body)
        denied['_canWrite'] = False
        with self.assertRaises(ValueError) as caught:
            pli.commit_preview(conn, {
                **denied,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'speed-viewer',
                'codeShapeDecisions': SHAPE_DECISIONS,
            }, db_path=str(path))
        self.assertIn('閲覧専用', str(caught.exception))


class OldSchemaIndexTest(unittest.TestCase):
    def _old_file(self):
        folder = tempfile.TemporaryDirectory()
        path = Path(folder.name) / 'old.sqlite3'
        raw = sqlite3.connect(path)
        raw.execute(
            '''CREATE TABLE product_ledger_row_maps (
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
            )'''
        )
        raw.execute(
            '''INSERT INTO product_ledger_row_maps(
                 product_map_key,row_map_key,csv_product_code,csv_product_name,created_at,updated_at)
               VALUES('k','r','OLD1','旧商品',?,?)''',
            (NOW, NOW),
        )
        raw.commit()
        raw.close()
        return folder, path

    def test_ensure_schema_adds_columns_and_code_index(self):
        folder, path = self._old_file()
        self.addCleanup(folder.cleanup)
        conn = stock.connect(path)
        self.addCleanup(conn.close)
        pli.ensure_schema(conn)
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(product_ledger_row_maps)')}
        self.assertIn('csv_color', cols)
        self.assertIn('spec_json', cols)
        indexes = {
            r['name'] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='product_ledger_row_maps'"
            )
        }
        self.assertIn('product_ledger_row_maps_code', indexes)
        self.assertEqual(
            conn.execute('SELECT csv_product_code FROM product_ledger_row_maps').fetchone()['csv_product_code'],
            'OLD1',
        )
        creates = {'n': 0}

        def trace(sql):
            if sql.lstrip().upper().startswith('CREATE'):
                creates['n'] += 1

        conn.set_trace_callback(trace)
        pli.ensure_schema(conn)
        conn.set_trace_callback(None)
        self.assertEqual(creates['n'], 0)
        conn.close()
        again = stock.connect(path)
        self.addCleanup(again.close)
        pli.ensure_schema(again)
        self.assertEqual(
            again.execute('SELECT COUNT(*) n FROM product_ledger_row_maps').fetchone()['n'],
            1,
        )
        plan = ' '.join(
            (r['detail'] if isinstance(r, sqlite3.Row) else r[3])
            for r in again.execute(
                '''EXPLAIN QUERY PLAN
                   SELECT * FROM product_ledger_row_maps
                   WHERE csv_product_code=? AND product_id IS NOT NULL
                   ORDER BY id DESC''',
                ('OLD1',),
            )
        )
        self.assertIn('product_ledger_row_maps_code', plan)

    def test_initialize_adds_code_index_on_existing_table(self):
        folder, path = self._old_file()
        self.addCleanup(folder.cleanup)
        conn = stock.connect(path)
        self.addCleanup(conn.close)
        stock.initialize(conn)
        indexes = {
            r['name'] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='product_ledger_row_maps'"
            )
        }
        self.assertIn('product_ledger_row_maps_code', indexes)
        self.assertEqual(
            conn.execute('SELECT csv_product_name FROM product_ledger_row_maps').fetchone()['csv_product_name'],
            '旧商品',
        )


if __name__ == '__main__':
    unittest.main()
