"""顧客名が空の加工サービスは加工だけ登録し、顧客と取扱は作らない。"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402

JS = ROOT / 'static' / 'product_ledger_csv.js'


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


def preview(conn, lines, **extra):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products', 'name': 'p.csv',
        'file': base64.b64encode(text.encode()).decode(),
        '_canWrite': True, '_actor': {'name': '確認'},
        **extra,
    }
    return pli.build_preview(conn, body), body


def counts(conn):
    def n(sql):
        return conn.execute(sql).fetchone()[0]
    qr = 0
    tables = {r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'stock_qr_codes' in tables:
        qr = n('SELECT COUNT(*) FROM stock_qr_codes')
    return {
        'products': n('SELECT COUNT(*) FROM products'),
        'customers': n('SELECT COUNT(*) FROM customers'),
        'handles': n('SELECT COUNT(*) FROM customer_products'),
        'lots': n('SELECT COUNT(*) FROM lots'),
        'qr': qr,
    }


def render_note(preview_obj):
    folder = tempfile.TemporaryDirectory()
    harness = Path(folder.name) / 'h.js'
    data = Path(folder.name) / 'c.json'
    harness.write_text(
        "const fs=require('fs');const vm=require('vm');"
        "const sandbox={window:{},document:{getElementById(){return null},querySelector(){return null},"
        "querySelectorAll(){return []}},console,CSS:{escape:s=>String(s)},esc:s=>String(s==null?'':s)};"
        "vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);"
        "const api=sandbox.window.ProductLedgerCsv.__test;"
        "const st=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));"
        "const html=api.processingServiceAutoHtml(st);"
        "const items=api.buildUnifiedReviewItems(st);"
        "const list=api.unifiedListRowsHtml(st, items);"
        "process.stdout.write(JSON.stringify({html, list, labels:items.map(i=>i.targetLabel)}));",
        encoding='utf-8',
    )
    data.write_text(json.dumps({
        'preview': preview_obj,
        'rowClassifications': {},
        'selections': {},
        'skuDecisions': {},
        'selectedCodes': {},
        'expandedCodes': {},
        'expandedUnified': {},
        'sectionOpen': {'unified': True},
        'busy': False,
    }), encoding='utf-8')
    proc = subprocess.run(['node', str(harness), str(JS), str(data)], capture_output=True, text=True)
    folder.cleanup()
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


class ServiceOnlyEmptyCustomerTest(unittest.TestCase):
    LINES = [
        ',K2スタッフ工房,,キャップサイズダウン,-',
        ',K2スタッフ工房,,スナッパー加工,-',
    ]

    def test_accept_repreview_commit_adds_only_the_service(self):
        folder, path, conn = newdb()
        try:
            stock.save_supplier(conn, {'name': 'K2スタッフ工房', 'active': True})
            conn.commit()
            before = counts(conn)
            first, body = preview(conn, self.LINES)
            for row in first['rows']:
                self.assertTrue(row.get('registerServiceWithoutCustomer'), row.get('reasons'))
                self.assertTrue(row['canCommit'], row.get('reasons'))
                self.assertNotEqual(row['status'], 'needs_review')
                self.assertEqual((row.get('customer') or {}).get('action'), 'omit')
                self.assertTrue((row.get('handle') or {}).get('skipForProcessingService'))
            note = render_note(first)
            self.assertIn('加工サービスだけ登録し、顧客と取扱は作りません', note['html'])
            self.assertNotIn('顧客名を入力してください', note['html'] + note['list'])
            # 了承して再照合しても登録条件は同じ（顧客空では止めない）。
            accepted, body = preview(conn, self.LINES, rowClassifications={
                str(row['rowNo']): {
                    'reviewAccepted': True,
                    'reviewFingerprint': pli._review_fingerprint(row),
                    'userActed': True,
                    'registerKind': '加工サービス',
                    'registerKindUser': True,
                }
                for row in first['rows']
            })
            for row in accepted['rows']:
                self.assertTrue(row['canCommit'], row.get('reasons'))
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': accepted['previewFingerprint'],
                'previewAt': accepted['previewAt'],
                'token': 'service-only-1',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            after = counts(conn)
            self.assertEqual(after['products'], before['products'] + 2)
            self.assertEqual(after['customers'], before['customers'])
            self.assertEqual(after['handles'], before['handles'])
            self.assertEqual(after['lots'], before['lots'])
            self.assertEqual(after['qr'], before['qr'])
            names = sorted(r['name'] for r in conn.execute(
                "SELECT name FROM products WHERE category='加工サービス'"
            ))
            self.assertEqual(names, ['サイズダウン', 'スナッパー加工'])
            again, body2 = preview(conn, self.LINES)
            ids = {r['product']['id'] for r in again['rows']}
            self.assertEqual(len(ids), 2)
            for row in again['rows']:
                self.assertEqual(row['product']['action'], 'reuse')
                self.assertTrue(row['canCommit'])
            pli.commit_preview(conn, {
                **body2,
                'previewFingerprint': again['previewFingerprint'],
                'previewAt': again['previewAt'],
                'token': 'service-only-2',
            }, db_path=str(path))
            conn.commit()
            self.assertEqual(counts(conn)['products'], after['products'])
        finally:
            conn.close()
            folder.cleanup()


class CustomerPresentAndGuardsTest(unittest.TestCase):
    def test_customer_present_keeps_handle_and_blocks_stay(self):
        folder, path, conn = newdb()
        try:
            stock.save_supplier(conn, {'name': 'K2スタッフ工房', 'active': True})
            stock.save_customer(conn, {'name': '石屋商事事務所', 'active': True})
            conn.commit()
            lines = [
                '顧客A,K2スタッフ工房,,サイズダウン,-',
                '石屋商事 事務所,K2スタッフ工房,,丈詰め,-',
                ',K2スタッフ工房,,長白衣ボタン,-',
            ]
            first, body = preview(conn, lines)
            by_name = {r['csv']['productName']: r for r in first['rows']}
            sized = by_name['サイズダウン']
            self.assertFalse(sized.get('registerServiceWithoutCustomer'))
            self.assertEqual(sized['customer']['action'], 'create')
            variant = by_name['丈詰め']
            self.assertEqual(variant['customer']['action'], 'review')
            self.assertFalse(variant['canCommit'])
            self.assertTrue(variant['customer']['candidates'])
            parts = by_name['長白衣ボタン']
            self.assertFalse(parts.get('registerServiceWithoutCustomer'))
            self.assertFalse(parts['canCommit'])
            self.assertIn('顧客が空', ' '.join(parts.get('reasons') or []))
            ready, body = preview(conn, ['顧客A,K2スタッフ工房,,サイズダウン,-'])
            row = ready['rows'][0]
            self.assertTrue(row['canCommit'], row.get('reasons'))
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': ready['previewFingerprint'],
                'previewAt': ready['previewAt'],
                'token': 'with-customer-1',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM customers WHERE name='顧客A'"
            ).fetchone()[0], 1)
            # 品番なしの加工そのものは従来どおり取扱を付けない。顧客レコードは作る。
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM customer_products').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM lots').fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT category FROM products WHERE name='サイズダウン'").fetchone()[0],
                '加工サービス',
            )
        finally:
            conn.close()
            folder.cleanup()

    def test_guards_and_garment_hem_stay(self):
        folder, path, conn = newdb()
        try:
            k2 = stock.save_supplier(conn, {'name': 'K2スタッフ工房', 'active': True})
            stock.save_product(conn, {
                'code': '', 'name': 'サイズダウン', 'category': '部品・資材',
                'supplierId': k2['id'], 'sizes': ['—'], 'links': [], 'active': True,
            })
            stock.save_supplier(conn, {'name': 'K2スタッフ工房株式会社', 'active': True})
            stopped = stock.save_supplier(conn, {'name': '停止工房', 'active': False})
            conn.commit()
            conflict, _ = preview(conn, [',K2スタッフ工房,,サイズダウン,-'])
            row = conflict['rows'][0]
            self.assertTrue(row['product'].get('categoryConflict'))
            self.assertFalse(row['canCommit'])
            self.assertFalse(row.get('registerServiceWithoutCustomer'))
            self.assertEqual(
                conn.execute('SELECT category FROM products WHERE id=?', (k2 and row['product']['id'],)).fetchone()['category'],
                '部品・資材',
            )
            variant, _ = preview(conn, [',K2スタッフ工房㈱,,丈詰め,-'])
            self.assertEqual(variant['rows'][0]['supplier']['action'], 'review')
            self.assertFalse(variant['rows'][0]['canCommit'])
            self.assertFalse(variant['rows'][0].get('registerServiceWithoutCustomer'))
            held, _ = preview(conn, [',停止工房,,スナッパー加工,-'])
            self.assertIn('停止中', ' '.join(held['rows'][0].get('reasons') or []))
            self.assertFalse(held['rows'][0]['canCommit'])
            hem, body = preview(conn, ['顧客A,住商モンブラン,6-615,コックコート(裾上げ),L'])
            coat = hem['rows'][0]
            self.assertFalse(coat.get('registerServiceWithoutCustomer'))
            self.assertNotEqual((coat.get('handle') or {}).get('action'), 'excluded')
            self.assertIn('裾上げ', str(coat.get('handle') or {}))
            self.assertTrue(coat['canCommit'], coat.get('reasons'))
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': hem['previewFingerprint'],
                'previewAt': hem['previewAt'],
                'token': 'hem-keep-1',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            garment = conn.execute(
                "SELECT id, category FROM products WHERE code='6-615'"
            ).fetchone()
            self.assertIsNotNone(garment)
            self.assertEqual(garment['category'], '制服')
            handles = list(conn.execute(
                'SELECT embroidery FROM customer_products WHERE product_id=?',
                (garment['id'],),
            ))
            self.assertTrue(any('裾上げ' in (h['embroidery'] or '') for h in handles))
            self.assertEqual(stopped['active'], False)
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
