"""新品／中古 (lots.condition) — opening, in/out, list, FIFO, Excel, period, corrections."""
import base64
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import excel_book
import stock
import stock_period


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def seed_masters(c):
    cu = stock.save_customer(c, {'name': '状態テスト病院', 'active': True})
    su = stock.save_supplier(c, {'name': '状態テスト仕入先', 'active': True})
    pr = stock.save_product(c, {
        'code': 'SCR-COND', 'name': '状態スクラブ', 'color': '紺',
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '状態テスト倉庫', 'active': True})
    handle = pr['links'][0]['id']
    return cu, pr, loc, handle


class StockConditionTest(unittest.TestCase):
    def test_opening_new_only_no_auto_used_zero_lot(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'open-new-only', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-09-01', 'receivedOn': '2026-09-01',
                'condition': 'new',
                'lines': [{'size': 'M', 'qty': 20, 'unitCost': 1000}],
            })
            c.commit()
            lots = list(c.execute('SELECT condition, qty_remaining FROM lots'))
            self.assertEqual(len(lots), 1)
            self.assertEqual(lots[0]['condition'], 'new')
            self.assertEqual(lots[0]['qty_remaining'], 20)
            view = stock.stock_query(c, {'group': 'customer'})
            row = view['groups'][0]['rows'][0]
            self.assertEqual(row['qtyNew'], 20)
            self.assertEqual(row['qtyUsed'], 0)
            self.assertEqual(row['qty'], 20)
            self.assertEqual(view['totalQtyNew'], 20)
            self.assertEqual(view['totalQtyUsed'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_same_handling_can_add_used_separately(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'open-new-a', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-09-01', 'receivedOn': '2026-09-01',
                'condition': '新品',
                'lines': [{'size': 'M', 'qty': 10, 'unitCost': 1000}],
            })
            stock.register_opening(c, {
                'token': 'open-used-a', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-09-01', 'receivedOn': '2026-09-01',
                'condition': '中古',
                'lines': [{'size': 'M', 'qty': 3, 'unitCost': 400}],
            })
            c.commit()
            view = stock.stock_query(c, {'group': 'product'})
            row = view['groups'][0]['rows'][0]
            self.assertEqual(row['qtyNew'], 10)
            self.assertEqual(row['qtyUsed'], 3)
            self.assertEqual(row['qty'], 13)
            self.assertEqual(row['amount'], 10 * 1000 + 3 * 400)
            by_cond = stock.stock_query(c, {'condition': 'used', 'group': 'lot'})
            self.assertEqual(by_cond['totalQty'], 3)
            self.assertEqual(by_cond['lots'][0]['condition'], 'used')
        finally:
            c.close()
            folder.cleanup()

    def test_outbound_does_not_cross_condition(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'in-new-cond1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 1000, 'receivedOn': '2026-08-01',
                'condition': 'new',
            })
            stock.register_inbound(c, {
                'token': 'in-used-cond1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 300, 'receivedOn': '2026-07-01',
                'condition': 'used',
            })
            plan_new = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'],
                'qty': 6, 'condition': 'new',
            })
            self.assertFalse(plan_new['ok'])
            self.assertEqual(plan_new['shortfall'], 1)
            self.assertEqual(plan_new['currentQty'], 5)
            with self.assertRaises(ValueError):
                stock.register_outbound(c, {
                    'token': 'out-cross-cond1', 'customerProductId': handle, 'size': 'M',
                    'locationId': loc['id'], 'qty': 6, 'condition': 'new',
                    'fingerprint': plan_new['fingerprint'],
                })
            ok = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'],
                'qty': 5, 'condition': 'new',
            })
            stock.register_outbound(c, {
                'token': 'out-new-only1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'condition': 'new',
                'fingerprint': ok['fingerprint'],
            })
            c.commit()
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id'], 'new'), 0)
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id'], 'used'), 5)
        finally:
            c.close()
            folder.cleanup()

    def test_fifo_and_amount_per_condition(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'used-old', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 200, 'receivedOn': '2026-06-01',
                'condition': 'used',
            })
            stock.register_inbound(c, {
                'token': 'used-new', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 500, 'receivedOn': '2026-08-01',
                'condition': 'used',
            })
            stock.register_inbound(c, {
                'token': 'new-cheap', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 10, 'unitCost': 50, 'receivedOn': '2026-05-01',
                'condition': 'new',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'],
                'qty': 5, 'condition': 'used',
            })
            self.assertTrue(plan['ok'])
            self.assertEqual([a['unitCost'] for a in plan['allocations']], [200, 500])
            self.assertEqual([a['qty'] for a in plan['allocations']], [4, 1])
            out = stock.register_outbound(c, {
                'token': 'out-used-fifo', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'condition': 'used',
                'fingerprint': plan['fingerprint'],
            })
            c.commit()
            self.assertEqual(out['totalAmount'], 4 * 200 + 500)
            remain = stock.stock_query(c, {})
            self.assertEqual(remain['totalQtyNew'], 10)
            self.assertEqual(remain['totalQtyUsed'], 3)
            self.assertEqual(remain['totalAmount'], 10 * 50 + 3 * 500)
        finally:
            c.close()
            folder.cleanup()

    def test_first_used_inbound_without_used_lot(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'only-new', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 900, 'receivedOn': '2026-10-01',
                'condition': 'new',
            })
            targets_in = stock.move_targets(c, {'mode': 'in'})
            conds = {t['condition'] for t in targets_in['targets']}
            self.assertEqual(conds, {'new'})
            first_used = stock.register_inbound(c, {
                'token': 'first-used', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 350, 'receivedOn': '2026-10-02',
                'condition': 'used',
            })
            c.commit()
            self.assertEqual(first_used['condition'], 'used')
            self.assertEqual(first_used['currentQtyBefore'], 0)
            self.assertEqual(first_used['currentQtyAfter'], 1)
            targets_out = stock.move_targets(c, {'mode': 'out'})
            self.assertEqual(sorted(t['condition'] for t in targets_out['targets']), ['new', 'used'])
        finally:
            c.close()
            folder.cleanup()

    def test_correction_keeps_condition(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'corr-open-cond1', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-09-01', 'receivedOn': '2026-09-01',
                'condition': 'used',
                'lines': [{'size': 'M', 'qty': 8, 'unitCost': 600}],
            })
            lot = c.execute("SELECT id, condition FROM lots WHERE condition='used'").fetchone()
            stock.correct_opening(c, {
                'lotId': lot['id'], 'qty': 6, 'unitCost': 550, 'reason': '数え直し',
            })
            c.commit()
            fresh = c.execute('SELECT qty_remaining, unit_cost, condition FROM lots WHERE id=?', (lot['id'],)).fetchone()
            self.assertEqual(fresh['condition'], 'used')
            self.assertEqual(fresh['qty_remaining'], 6)
            self.assertEqual(fresh['unit_cost'], 550)
            inbound = stock.register_inbound(c, {
                'token': 'corr-in-cond1', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 700, 'receivedOn': '2026-10-01',
                'condition': 'new',
            })
            stock.correct_inbound(c, {
                'lotId': inbound['lotId'], 'qty': 3, 'unitCost': 700, 'reason': '入庫誤り',
                '_canWrite': True,
            })
            c.commit()
            lot2 = c.execute('SELECT condition, qty_remaining FROM lots WHERE id=?', (inbound['lotId'],)).fetchone()
            self.assertEqual(lot2['condition'], 'new')
            self.assertEqual(lot2['qty_remaining'], 3)
        finally:
            c.close()
            folder.cleanup()

    def test_period_summary_separates_condition(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'per-new-cond1', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-08-01', 'receivedOn': '2026-08-01',
                'condition': 'new',
                'lines': [{'size': 'M', 'qty': 10, 'unitCost': 1000}],
            })
            stock.register_opening(c, {
                'token': 'per-used-cond1', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-08-01', 'receivedOn': '2026-08-01',
                'condition': 'used',
                'lines': [{'size': 'M', 'qty': 4, 'unitCost': 400}],
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'],
                'qty': 2, 'condition': 'used',
            })
            stock.register_outbound(c, {
                'token': 'per-out-cond1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'condition': 'used',
                'businessOn': '2026-09-15', 'fingerprint': plan['fingerprint'],
            })
            c.commit()
            summary = stock_period.summarize_period(c, {
                'from': '2026-09-01', 'to': '2026-09-30', 'group': 'detail',
            })
            rows = {(r['condition'], r['outboundQty']): r for r in summary['rows'] if r['computable']}
            self.assertIn(('used', 2), rows)
            self.assertEqual(rows[('used', 2)]['closingQty'], 2)
            used_only = stock_period.summarize_period(c, {
                'from': '2026-09-01', 'to': '2026-09-30', 'condition': 'used',
            })
            self.assertTrue(all(r['condition'] == 'used' for r in used_only['rows']))
            self.assertEqual(used_only['totals']['outboundQty'], 2)
        finally:
            c.close()
            folder.cleanup()

    def test_excel_condition_column_and_legacy_default(self):
        folder, path, c = newdb()
        try:
            book = excel_book.workbook([
                ('顧客', ['顧客名', 'カナ', '住所', '電話番号', '担当者'], [['状態顧客', '', '', '', '']]),
                ('仕入先', ['仕入先名', '住所', '電話番号', '担当者'], [['状態仕入', '', '', '']]),
                ('商品', ['品番', '商品名', '色', '仕入先名', 'サイズ'], [['C-1', 'コート', '黒', '状態仕入', 'M']]),
                ('取扱', ['品番', '商品名', '色', '顧客名', '加工仕様', '標準'], [['C-1', 'コート', '黒', '状態顧客', '', '標準']]),
                ('保管場所', ['拠点名', '棚名'], [['状態倉庫', '']]),
                ('初期在庫', [
                    '顧客名', '品番', '商品名', '色', '加工仕様', '拠点名', '棚名',
                    '在庫基準日', '入荷日', '入荷日不明', '払出し順', '状態', 'サイズ', '数量', '仕入単価',
                ], [
                    ['状態顧客', 'C-1', 'コート', '黒', '', '状態倉庫', '', '2026-09-01', '2026-09-01', '', '', '中古', 'M', '2', '500'],
                    ['状態顧客', 'C-1', 'コート', '黒', '', '状態倉庫', '', '2026-09-01', '2026-09-01', '', '', '新品', 'M', '5', '1200'],
                ]),
            ])
            result = excel_book.apply(c, {'file': base64.b64encode(book).decode()})
            c.commit()
            self.assertEqual(result['counts']['初期在庫']['created'], 2)
            qty = {
                r['condition']: r['qty']
                for r in c.execute('SELECT condition, SUM(qty_remaining) AS qty FROM lots GROUP BY condition')
            }
            self.assertEqual(qty['used'], 2)
            self.assertEqual(qty['new'], 5)
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            legacy = excel_book.workbook([
                ('顧客', ['顧客名', 'カナ', '住所', '電話番号', '担当者'], [['旧顧客', '', '', '', '']]),
                ('仕入先', ['仕入先名', '住所', '電話番号', '担当者'], [['旧仕入', '', '', '']]),
                ('商品', ['品番', '商品名', '色', '仕入先名', 'サイズ'], [['L-1', '旧品', '', '旧仕入', 'M']]),
                ('取扱', ['品番', '商品名', '色', '顧客名', '加工仕様', '標準'], [['L-1', '旧品', '', '旧顧客', '', '']]),
                ('保管場所', ['拠点名', '棚名'], [['旧倉庫', '']]),
                ('初期在庫', [
                    '顧客名', '品番', '商品名', '色', '加工仕様', '拠点名', '棚名',
                    '在庫基準日', '入荷日', '入荷日不明', '払出し順', 'サイズ', '数量', '仕入単価',
                ], [
                    ['旧顧客', 'L-1', '旧品', '', '', '旧倉庫', '', '2026-09-01', '2026-09-01', '', '', 'M', '3', '800'],
                ]),
            ])
            result = excel_book.apply(c, {'file': base64.b64encode(legacy).decode()})
            c.commit()
            self.assertIn(excel_book.LEGACY_CONDITION_NOTE, result['summary'])
            lot = c.execute('SELECT condition, qty_remaining FROM lots').fetchone()
            self.assertEqual(lot['condition'], 'new')
            self.assertEqual(lot['qty_remaining'], 3)
            again = excel_book.apply(c, {'file': base64.b64encode(legacy).decode()})
            c.commit()
            self.assertEqual(again['counts']['初期在庫']['already'], 1)
            self.assertEqual(c.execute('SELECT SUM(qty_remaining) FROM lots').fetchone()[0], 3)
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            bad = excel_book.workbook([
                ('顧客', ['顧客名', 'カナ', '住所', '電話番号', '担当者'], [['不正顧客', '', '', '', '']]),
                ('仕入先', ['仕入先名', '住所', '電話番号', '担当者'], [['不正仕入', '', '', '']]),
                ('商品', ['品番', '商品名', '色', '仕入先名', 'サイズ'], [['B-1', '不正', '', '不正仕入', 'M']]),
                ('取扱', ['品番', '商品名', '色', '顧客名', '加工仕様', '標準'], [['B-1', '不正', '', '不正顧客', '', '']]),
                ('保管場所', ['拠点名', '棚名'], [['不正倉庫', '']]),
                ('初期在庫', [
                    '顧客名', '品番', '商品名', '色', '加工仕様', '拠点名', '棚名',
                    '在庫基準日', '入荷日', '入荷日不明', '払出し順', '状態', 'サイズ', '数量', '仕入単価',
                ], [
                    ['不正顧客', 'B-1', '不正', '', '', '不正倉庫', '', '2026-09-01', '2026-09-01', '', '', '再生', 'M', '1', '100'],
                ]),
            ])
            with self.assertRaises(ValueError) as ctx:
                excel_book.apply(c, {'file': base64.b64encode(bad).decode()})
            self.assertIn('新品', str(ctx.exception))
            c.rollback()
            self.assertEqual(c.execute('SELECT COUNT(*) FROM lots').fetchone()[0], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_unset_condition_not_bulk_rewritten(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'unset-seed', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-09-01', 'receivedOn': '2026-09-01',
                'condition': 'new',
                'lines': [{'size': 'M', 'qty': 1, 'unitCost': 100}],
            })
            c.execute("UPDATE lots SET condition=''")
            c.commit()
            stock.migrate_schema(c)
            c.commit()
            value = c.execute('SELECT condition FROM lots').fetchone()['condition']
            self.assertEqual(value, '')
            self.assertEqual(stock.condition_label(value), '未設定')
            view = stock.stock_query(c, {'group': 'lot'})
            self.assertEqual(view['lots'][0]['conditionLabel'], '未設定')
            self.assertEqual(view['totalQtyNew'], 0)
            self.assertEqual(view['totalQtyUsed'], 0)
            self.assertEqual(view['totalQty'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_default_opening_condition_is_new(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'default-new', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-09-01', 'receivedOn': '2026-09-01',
                'lines': [{'size': 'M', 'qty': 1, 'unitCost': 100}],
            })
            c.commit()
            self.assertEqual(c.execute('SELECT condition FROM lots').fetchone()['condition'], 'new')
            with self.assertRaises(ValueError):
                stock.parse_condition('再生品')
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
