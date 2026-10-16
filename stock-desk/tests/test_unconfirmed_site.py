"""仮拠点「未確認」の起動時確保・初期在庫選択・Excel取込（棚空）。"""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import excel_book
import stock


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


class UnconfirmedSiteTest(unittest.TestCase):
    def test_initialize_ensures_unconfirmed_site_without_shelves(self):
        folder, path, c = newdb()
        try:
            rows = list(c.execute(
                'SELECT id,name,parent_id,active FROM locations WHERE name=? ORDER BY parent_id IS NOT NULL, id',
                (stock.UNCONFIRMED_SITE_NAME,),
            ))
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]['parent_id'])
            self.assertEqual(rows[0]['active'], 1)
            children = c.execute(
                'SELECT COUNT(*) AS n FROM locations WHERE parent_id=?',
                (rows[0]['id'],),
            ).fetchone()['n']
            self.assertEqual(children, 0)
        finally:
            c.close()
            folder.cleanup()

    def test_ensure_is_idempotent_no_duplicate(self):
        folder, path, c = newdb()
        try:
            first = stock.ensure_unconfirmed_site(c)
            second = stock.ensure_unconfirmed_site(c)
            stock.initialize(c)
            third = stock.ensure_unconfirmed_site(c)
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(second['id'], third['id'])
            self.assertEqual(first['name'], stock.UNCONFIRMED_SITE_NAME)
            self.assertIsNone(first['parentId'])
            n = c.execute(
                'SELECT COUNT(*) AS n FROM locations WHERE name=? AND parent_id IS NULL',
                (stock.UNCONFIRMED_SITE_NAME,),
            ).fetchone()['n']
            self.assertEqual(n, 1)
            # UNIQUE index also rejects a second insert of the same root name
            with self.assertRaises(Exception):
                c.execute(
                    'INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
                    (stock.UNCONFIRMED_SITE_NAME, None, 1, stock.stamp(), stock.stamp()),
                )
            c.rollback()
        finally:
            c.close()
            folder.cleanup()

    def test_existing_site_is_reused_not_recreated(self):
        folder = tempfile.TemporaryDirectory()
        path = Path(folder.name) / 'stock.sqlite3'
        c = stock.connect(path)
        try:
            c.executescript(stock.SCHEMA)
            t = stock.stamp()
            c.execute(
                'INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
                (stock.UNCONFIRMED_SITE_NAME, None, 1, t, t),
            )
            existing_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.commit()
            got = stock.ensure_unconfirmed_site(c)
            self.assertEqual(got['id'], existing_id)
            n = c.execute(
                'SELECT COUNT(*) AS n FROM locations WHERE name=? AND parent_id IS NULL',
                (stock.UNCONFIRMED_SITE_NAME,),
            ).fetchone()['n']
            self.assertEqual(n, 1)
        finally:
            c.close()
            folder.cleanup()

    def test_opening_picker_can_select_unconfirmed_site(self):
        folder, path, c = newdb()
        try:
            boot = stock.bootstrap(c)
            sites = [loc for loc in boot['locations'] if loc['parentId'] is None and loc['active']]
            unconfirmed = next(s for s in sites if s['name'] == stock.UNCONFIRMED_SITE_NAME)
            options, require_shelf = stock.opening_picker_shelves(boot['locations'], unconfirmed['id'])
            self.assertEqual(options, [])
            self.assertFalse(require_shelf)
            location_id = stock.resolve_opening_location(unconfirmed['id'], '', boot['locations'])
            self.assertEqual(location_id, unconfirmed['id'])

            cu = stock.save_customer(c, {'name': '未確認選択病院', 'active': True})
            su = stock.save_supplier(c, {'name': '未確認選択仕入', 'active': True})
            pr = stock.save_product(c, {
                'code': 'UNC-1', 'name': 'スクラブ', 'color': '白',
                'supplierId': su['id'], 'sizes': ['M'], 'active': True, 'links': [],
            })
            handle = stock.ensure_customer_product(c, {
                'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '', 'isStandard': True,
            })
            opening = stock.register_opening(c, {
                'token': 'unconfirmed-opening-select',
                'customerProductId': handle['id'],
                'locationId': unconfirmed['id'],
                'basisDate': '2026-10-01',
                'receivedUnknown': True,
                'payoutOrder': 1,
                'lines': [{'size': 'M', 'qty': 2, 'unitCost': 1000}],
            })
            self.assertFalse(opening.get('already'))
            lot = c.execute(
                'SELECT location_id, qty_remaining FROM lots WHERE customer_product_id=?',
                (handle['id'],),
            ).fetchone()
            self.assertEqual(lot['location_id'], unconfirmed['id'])
            self.assertEqual(lot['qty_remaining'], 2)
        finally:
            c.close()
            folder.cleanup()

    def test_excel_import_unconfirmed_site_empty_shelf(self):
        folder, path, c = newdb()
        try:
            book = excel_book.workbook([
                ('顧客', ['顧客名', 'カナ', '住所', '電話番号', '担当者'],
                 [['未確認Excel病院', '', '', '', '']]),
                ('仕入先', ['仕入先名', '住所', '電話番号', '担当者'],
                 [['未確認Excel仕入', '', '', '']]),
                ('商品', ['品番', '商品名', '色', '仕入先名', 'サイズ'],
                 [['UNC-X', 'スクラブ', '白', '未確認Excel仕入', 'M']]),
                ('取扱', ['品番', '商品名', '色', '顧客名', '加工仕様', '標準'],
                 [['UNC-X', 'スクラブ', '白', '未確認Excel病院', '', '標準']]),
                ('保管場所', ['拠点名', '棚名'],
                 [[stock.UNCONFIRMED_SITE_NAME, '']]),
                ('初期在庫', [
                    '顧客名', '品番', '商品名', '色', '加工仕様', '拠点名', '棚名',
                    '在庫基準日', '入荷日', '入荷日不明', '払出し順', 'サイズ', '数量', '仕入単価',
                ], [[
                    '未確認Excel病院', 'UNC-X', 'スクラブ', '白', '',
                    stock.UNCONFIRMED_SITE_NAME, '',
                    '2026-10-01', '', 'する', '1', 'M', '3', '1100',
                ]]),
            ])
            result = excel_book.apply(c, {'file': base64.b64encode(book).decode()})
            c.commit()
            site = c.execute(
                'SELECT id FROM locations WHERE name=? AND parent_id IS NULL',
                (stock.UNCONFIRMED_SITE_NAME,),
            ).fetchone()
            self.assertIsNotNone(site)
            shelves = c.execute(
                'SELECT COUNT(*) AS n FROM locations WHERE parent_id=?',
                (site['id'],),
            ).fetchone()['n']
            self.assertEqual(shelves, 0)
            n_sites = c.execute(
                'SELECT COUNT(*) AS n FROM locations WHERE name=? AND parent_id IS NULL',
                (stock.UNCONFIRMED_SITE_NAME,),
            ).fetchone()['n']
            self.assertEqual(n_sites, 1)
            lot = c.execute(
                'SELECT location_id, qty_remaining FROM lots'
            ).fetchone()
            self.assertEqual(lot['location_id'], site['id'])
            self.assertEqual(lot['qty_remaining'], 3)
            self.assertIn('初期在庫', result['summary'])
        finally:
            c.close()
            folder.cleanup()

    def test_blank_site_is_not_auto_converted(self):
        """拠点必須の仕様維持: 空の拠点名を「未確認」へ変換しない。"""
        folder, path, c = newdb()
        try:
            with self.assertRaises(ValueError) as cm:
                stock.save_location(c, {'siteName': '', 'active': True})
            self.assertIn('拠点名', str(cm.exception))
            # Excel 保管場所でも拠点名必須
            book = excel_book.workbook([
                ('保管場所', ['拠点名', '棚名'], [['', '']]),
            ])
            with self.assertRaises(ValueError):
                excel_book.apply(c, {'file': base64.b64encode(book).decode()})
            c.rollback()
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
