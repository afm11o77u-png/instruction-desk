"""旧在庫JSONの初期在庫と旧QR同時登録。合成データのみ。実ファイルは使わない。"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import opening_legacy_json as olj  # noqa: E402
import opening_stock_wipe as wipe  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import qr_link_import  # noqa: E402
import stock  # noqa: E402
import stock_qr  # noqa: E402

OWNER = {'_canWrite': True, '_actor': {'name': '確認', 'role': 'owner'}}


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


def b64(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode('ascii')


def row(**kwargs):
    base = {
        'id': 'stock-1',
        'customer_id': 'cust-old-1',
        'maker_id': 'maker-old-1',
        'item_id': 'item-1',
        'item_num': 'P001',
        'name': 'パンツ',
        'item_size': 'L',
        'maker_name': '仕入先X',
        'is_used': False,
        'stock_count': 4,
        'calc_stock_count': 4,
        'price': 1000,
        'factory_name': '本社',
        'shelf_name': '棚1',
        'factory_id': 'fac-1',
        'shelf_id': 'shelf-1',
    }
    base.update(kwargs)
    return base


def doc(rows):
    return {'result': True, 'data': rows}


def register_product(conn, path, lines, decisions=None):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products',
        'name': 'ledger.csv',
        'file': base64.b64encode(text.encode()).decode('ascii'),
        'registerOpeningStock': False,
        '_canWrite': True,
        '_actor': {'name': '確認'},
    }
    preview = pli.build_preview(conn, body)
    pli.commit_preview(conn, {
        **body,
        'previewFingerprint': preview['previewFingerprint'],
        'previewAt': preview['previewAt'],
        'token': 'prod-' + str(abs(hash(text)) % 10**8),
        'codeShapeDecisions': decisions or {},
    }, db_path=str(path))
    conn.commit()
    return preview


def customer_id(conn, name='顧客A'):
    return conn.execute('SELECT id FROM customers WHERE name=?', (name,)).fetchone()['id']


def opening_body(rows, conn, links=None, token='open-json-1'):
    if links is None:
        links = {'cust-old-1': customer_id(conn)}
    return {
        'importMode': 'opening_stock',
        'legacyStockFile': b64(doc(rows)),
        'legacyStockName': 'old-stock.json',
        'legacyCustomerLinks': links,
        'basisDate': '2026-01-01',
        'defaultSiteName': '未確認',
        '_canWrite': True,
        '_actor': {'name': '確認', 'role': 'owner'},
        'token': token,
    }


def commit_json(conn, path, rows, links=None, token='open-json-1'):
    body = opening_body(rows, conn, links, token)
    preview = pli.build_preview(conn, body)
    result = pli.commit_preview(conn, {
        **body,
        'previewFingerprint': preview['previewFingerprint'],
        'previewAt': preview['previewAt'],
    }, db_path=str(path))
    conn.commit()
    return preview, result


class OpeningLegacyJsonTest(unittest.TestCase):
    def test_registers_stock_and_old_qr_together(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, ['顧客A,仕入先X,P001,パンツ,L'])
        preview, result = commit_json(conn, path, [row()])
        self.assertEqual(result['counts']['openingCreated'], 1)
        self.assertEqual(result['counts']['legacyQrInserted'], 1)
        plan = preview['openingStock']['plans'][0]
        self.assertIn('stock_count と calc_stock_count が同じ値', plan['reasons'][0])
        link = conn.execute('SELECT * FROM qr_stock_links').fetchone()
        self.assertEqual(link['legacy_stock_id'], 'stock-1')
        self.assertEqual(link['legacy_customer_id'], 'cust-old-1')
        self.assertEqual(link['legacy_maker_id'], 'maker-old-1')
        self.assertEqual(link['qr_raw'], olj.qr_text('stock-1', 'cust-old-1', 'maker-old-1'))
        product = conn.execute('SELECT id FROM products WHERE code=?', ('P001',)).fetchone()
        self.assertEqual(link['product_id'], product['id'])
        self.assertNotEqual(link['legacy_stock_id'], str(product['id']))
        before = conn.execute('SELECT qty_remaining FROM lots').fetchone()['qty_remaining']
        found = qr_link_import.lookup_qr(conn, link['qr_raw'])
        self.assertTrue(found['found'])
        self.assertEqual(found['lotCount'], 1)
        self.assertIsNone(found['pickedLotId'])
        self.assertEqual(conn.execute('SELECT qty_remaining FROM lots').fetchone()['qty_remaining'], before)
        again, _ = commit_json(conn, path, [row()], token='open-json-2')
        self.assertEqual(again['openingStock']['plans'][0]['status'], 'unchanged')
        self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], 4)
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)

    def test_upper_lower_keeps_source_id_on_unified_product(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, [
            '顧客A,仕入先X,7-522,作業ズボン,M',
            '顧客A,仕入先X,7-522下,作業ズボン,LL',
        ], {'extend:7-522': {'action': 'merge_to_base'}})
        self.assertEqual(
            [r['code'] for r in conn.execute('SELECT code FROM products ORDER BY code')],
            ['7-522'],
        )
        preview, _result = commit_json(conn, path, [row(
            id='stock-lower', item_num='7-522下', name='作業ズボン', item_size='LL', stock_count=2, calc_stock_count=2,
        )])
        self.assertEqual(preview['openingStock']['plans'][0]['status'], 'create')
        link = conn.execute('SELECT * FROM qr_stock_links').fetchone()
        product = conn.execute("SELECT id FROM products WHERE code='7-522'").fetchone()
        self.assertEqual(link['legacy_stock_id'], 'stock-lower')
        self.assertEqual(link['product_id'], product['id'])
        self.assertEqual(link['item_num'], '7-522下')
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], 1)

    def test_duplicate_old_id_does_not_double_qty(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, ['顧客A,仕入先X,P001,パンツ,L'])
        preview, _result = commit_json(conn, path, [
            row(stock_count=3, calc_stock_count=3),
            row(stock_count=9, calc_stock_count=9),
        ])
        statuses = [p['status'] for p in preview['openingStock']['plans']]
        self.assertIn('needs_review', statuses)
        self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], 3)
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)

    def test_changed_reimport_does_not_move_link(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, [
            '顧客A,仕入先X,P001,パンツ,L',
            '顧客A,仕入先X,P002,シャツ,M',
        ])
        commit_json(conn, path, [row()])
        preview, result = commit_json(conn, path, [row(item_num='P002', name='シャツ', item_size='M')], token='open-json-3')
        self.assertTrue(preview['openingStock']['plans'][0]['legacyConflict'])
        self.assertEqual(result['counts'].get('openingCreated', 0), 0)
        link = conn.execute('SELECT item_num, product_id FROM qr_stock_links').fetchone()
        self.assertEqual(link['item_num'], 'P001')
        self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], 4)

    def test_existing_qr_conflict_and_parts_skip_and_qty_rules(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, [
            '顧客A,仕入先X,P001,パンツ,L',
            '顧客A,仕入先X,PART1,生地,F',
        ])
        conn.execute("UPDATE products SET category=? WHERE code='PART1'", (stock.CATEGORY_PARTS,))
        other = conn.execute("SELECT id FROM products WHERE code='P002'").fetchone()
        # P002 does not exist; point the prior link at P001's product but a different size so the target differs.
        product = conn.execute("SELECT id FROM products WHERE code='P001'").fetchone()['id']
        handle = conn.execute('SELECT id, customer_id FROM customer_products').fetchone()
        qr_link_import.ensure_schema(conn)
        conn.execute(
            '''INSERT INTO qr_stock_links(
                 legacy_stock_id, legacy_customer_id, legacy_maker_id, customer_name,
                 qr_raw, customer_id, product_id, customer_product_id, size_label, condition,
                 match_kind, created_at, updated_at)
               VALUES('stock-conflict','cust-old-1','maker-old-1','顧客A','{}',?,?,?,'M','new','exact','t','t')''',
            (handle['customer_id'], product, handle['id']),
        )
        conn.commit()
        preview, result = commit_json(conn, path, [
            row(id='stock-conflict', stock_count=1, calc_stock_count=1),
            row(id='stock-part', item_num='PART1', name='生地', item_size='F', stock_count=2, calc_stock_count=2),
            row(id='stock-empty', stock_count=None, calc_stock_count=None),
            row(id='stock-neg', stock_count=-2, calc_stock_count=-2),
            row(id='stock-diff', stock_count=1, calc_stock_count=2),
            row(id='stock-null', is_used=None),
        ], token='open-json-mix')
        by_id = {(p.get('legacy') or {}).get('stockId'): p for p in preview['openingStock']['plans']}
        self.assertTrue(by_id['stock-conflict']['legacyConflict'])
        self.assertTrue(by_id['stock-part'].get('qrOutOfScope'))
        self.assertTrue(any(olj.QTY_EMPTY in ' '.join(by_id['stock-empty']['reasons']) for _ in [0]))
        self.assertIn(olj.QTY_NEGATIVE, ' '.join(by_id['stock-neg']['reasons']))
        self.assertIn(olj.QTY_MISMATCH, ' '.join(by_id['stock-diff']['reasons']))
        self.assertEqual(by_id['stock-null']['status'], 'needs_review')
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) n FROM qr_stock_links WHERE legacy_stock_id='stock-part'"
        ).fetchone()['n'], 0)
        self.assertEqual(conn.execute(
            "SELECT size_label FROM qr_stock_links WHERE legacy_stock_id='stock-conflict'"
        ).fetchone()['size_label'], 'M')
        self.assertEqual(result['counts'].get('legacyQrInserted', 0), 0)
        self.assertIsNone(other)

    def test_customer_must_be_explicit_and_failure_rolls_back(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, ['顧客A,仕入先X,P001,パンツ,L'])
        body = opening_body([row(), row(id='stock-2', stock_count=1, calc_stock_count=1)], conn, links={})
        preview = pli.build_preview(conn, body)
        with self.assertRaises(ValueError) as caught:
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'no-customer',
            }, db_path=str(path))
        self.assertIn('旧顧客ID', str(caught.exception))
        conn.rollback()
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
        # 旧ID文字列が customers.id と一致しても、明示が無ければ結び付けない。
        lone = conn.execute('SELECT id FROM customers').fetchone()['id']
        body_same = opening_body([row(customer_id=str(lone))], conn, links={})
        preview_same = pli.build_preview(conn, body_same)
        with self.assertRaises(ValueError):
            pli.commit_preview(conn, {
                **body_same,
                'previewFingerprint': preview_same['previewFingerprint'],
                'previewAt': preview_same['previewAt'],
                'token': 'id-equal',
            }, db_path=str(path))
        conn.rollback()
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
        good = opening_body([row(), row(id='stock-2')], conn)
        preview_good = pli.build_preview(conn, good)
        original = olj.save_links

        def fail_links(*_args, **_kwargs):
            raise ValueError('リンク保存に失敗しました。')

        olj.save_links = fail_links
        try:
            with self.assertRaises(ValueError):
                pli.commit_preview(conn, {
                    **good,
                    'previewFingerprint': preview_good['previewFingerprint'],
                    'previewAt': preview_good['previewAt'],
                    'token': 'rollback',
                }, db_path=str(path))
        finally:
            olj.save_links = original
        conn.rollback()
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)

    def test_viewer_and_stale_fingerprint_are_rejected(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, ['顧客A,仕入先X,P001,パンツ,L'])
        body = opening_body([row()], conn)
        preview = pli.build_preview(conn, body)
        denied = dict(body)
        denied['_canWrite'] = False
        with self.assertRaises(ValueError) as caught:
            pli.commit_preview(conn, {
                **denied,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
            }, db_path=str(path))
        self.assertIn('閲覧専用', str(caught.exception))
        conn.execute(
            '''INSERT INTO customers(name,kana,address,phone,contact,active,created_at,updated_at)
               VALUES('別顧客','','','','',1,'t','t')'''
        )
        with self.assertRaises(stock.StockConflict):
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'stale-json',
            }, db_path=str(path))

    def test_wipe_then_reimport_and_new_qr_unchanged(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, ['顧客A,仕入先X,P001,パンツ,L'])
        _preview, result = commit_json(conn, path, [row()])
        link_id = conn.execute('SELECT id FROM qr_stock_links').fetchone()['id']
        issued = stock_qr.show(conn, {
            '_canWrite': True,
            '_actor': {'name': '確認', 'role': 'owner'},
            'customerProductId': conn.execute('SELECT id FROM customer_products').fetchone()['id'],
            'size': 'L',
            'condition': 'new',
        })
        conn.commit()
        before_new = stock_qr.lookup(conn, issued['publicId'])
        qty_before = conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q']
        self.assertFalse(before_new['qtyChanged'])
        self.assertGreater(before_new['lotCount'], 0)
        self.assertIsNone(before_new['pickedLotId'])
        wipe.execute_wipe(conn, {**OWNER, 'confirmText': '初期在庫を全削除'}, db_path=str(path))
        conn.commit()
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
        self.assertEqual(conn.execute('SELECT id FROM qr_stock_links').fetchone()['id'], link_id)
        found = qr_link_import.lookup_qr(conn, olj.qr_text('stock-1', 'cust-old-1', 'maker-old-1'))
        self.assertEqual(found['lotCount'], 0)
        self.assertEqual(found['lots'], [])
        commit_json(conn, path, [row()], token='after-wipe')
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)
        self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], qty_before)
        found_after = qr_link_import.lookup_qr(conn, olj.qr_text('stock-1', 'cust-old-1', 'maker-old-1'))
        self.assertEqual(found_after['lotCount'], 1)
        self.assertIsNone(found_after['pickedLotId'])
        new_after = stock_qr.lookup(conn, issued['publicId'])
        self.assertFalse(new_after['qtyChanged'])
        self.assertEqual(
            conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'],
            qty_before,
        )

    def test_two_old_rows_sum_and_keep_separate_links(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, ['顧客A,仕入先X,P001,パンツ,L'])
        commit_json(conn, path, [
            row(id='stock-a', stock_count=2, calc_stock_count=2),
            row(id='stock-b', stock_count=3, calc_stock_count=3),
        ])
        self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], 5)
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 2)
        for stock_id in ('stock-a', 'stock-b'):
            found = qr_link_import.lookup_qr(conn, olj.qr_text(stock_id, 'cust-old-1', 'maker-old-1'))
            self.assertEqual(found['lotCount'], 1)
            self.assertIsNone(found['pickedLotId'])
            self.assertEqual(found['lots'][0]['qtyRemaining'], 5)

    def test_stopped_product_is_not_registered(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, ['顧客A,仕入先X,P001,パンツ,L'])
        conn.execute("UPDATE products SET active=0 WHERE code='P001'")
        conn.commit()
        preview, result = commit_json(conn, path, [row()], token='stopped-product-1')
        self.assertEqual(preview['openingStock']['plans'][0]['status'], 'needs_review')
        self.assertEqual(result['counts'].get('openingCreated', 0), 0)
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)

    def test_csv_opening_still_registers_without_qr_link(self):
        folder, path, conn = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(conn.close)
        register_product(conn, path, ['顧客A,仕入先X,P001,パンツ,L'])
        opening = '新古,仕入先,顧客,商品名,商品コード,サイズ,仕入単価,在庫,拠点,棚\n新品,仕入先X,顧客A,パンツ,P001,L,1000,2,本社,棚1'
        body = {
            'importMode': 'opening_stock',
            'auxFile': base64.b64encode(opening.encode()).decode('ascii'),
            'auxName': 'opening.csv',
            'basisDate': '2026-01-01',
            'defaultSiteName': '未確認',
            '_canWrite': True,
            '_actor': {'name': '確認'},
            'token': 'csv-only',
        }
        preview = pli.build_preview(conn, body)
        pli.commit_preview(conn, {
            **body,
            'previewFingerprint': preview['previewFingerprint'],
            'previewAt': preview['previewAt'],
        }, db_path=str(path))
        conn.commit()
        self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], 2)
        self.assertEqual(conn.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
        both = dict(body)
        both['legacyStockFile'] = b64(doc([row()]))
        with self.assertRaises(ValueError) as caught:
            pli.build_preview(conn, both)
        self.assertIn('同時', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
