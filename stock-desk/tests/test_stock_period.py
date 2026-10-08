"""§7 scenarios for 期間入出荷集計 (temp DB / fixtures only — never touch production stock)."""
from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import stock
import stock_period


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def seed_masters(c):
    cu = stock.save_customer(c, {'name': '期間テスト病院', 'active': True})
    su = stock.save_supplier(c, {'name': '期間テスト仕入先', 'active': True})
    pr = stock.save_product(c, {
        'code': 'PER-1', 'name': '期間スクラブ', 'color': '紺',
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '期間倉庫', 'active': True})
    handle = pr['links'][0]['id']
    return cu, pr, loc, handle


def _out(c, handle, loc_id, qty, token, business_on, **extra):
    plan = stock.plan_outbound(c, {
        'customerProductId': handle, 'size': 'M', 'locationId': loc_id, 'qty': qty,
    })
    body = {
        'token': token, 'customerProductId': handle, 'size': 'M',
        'locationId': loc_id, 'qty': qty, 'fingerprint': plan['fingerprint'],
        'businessOn': business_on,
    }
    body.update(extra)
    return stock.register_outbound(c, body)


class StockPeriodTest(unittest.TestCase):
    def test_boundary_dates_inclusive_and_identity(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'per-pre-01', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 10, 'unitCost': 1000, 'receivedOn': '2026-09-15',
            })
            stock.register_inbound(c, {
                'token': 'per-start', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 1100, 'receivedOn': '2026-10-01',
            })
            _out(c, handle, loc['id'], 3, 'per-end-out', '2026-10-31')
            stock.register_inbound(c, {
                'token': 'per-after', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 1200, 'receivedOn': '2026-11-01',
            })
            c.commit()
            data = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            self.assertEqual(len(data['rows']), 1)
            row = data['rows'][0]
            self.assertTrue(row['computable'])
            self.assertEqual(row['openingQty'], 10)
            self.assertEqual(row['inboundQty'], 4)
            self.assertEqual(row['outboundQty'], 3)
            self.assertEqual(row['correctionQty'], 0)
            self.assertEqual(row['closingQty'], 11)
            self.assertTrue(row['identityOk'])
            self.assertEqual(row['openingQty'] + row['inboundQty'] - row['outboundQty'] + row['correctionQty'], row['closingQty'])
            self.assertEqual(row['inboundAmount'], 4 * 1100)
            # Outbound amount uses lot unit costs at issue time (FIFO: oldest 1000)
            self.assertEqual(row['outboundAmount'], 3 * 1000)
            self.assertEqual(data['totals']['inboundQty'], 4)
            self.assertEqual(data['totals']['outboundQty'], 3)
            self.assertEqual(data['totals']['inboundAmount'], 4 * 1100)
            self.assertEqual(data['totals']['outboundAmount'], 3 * 1000)
        finally:
            c.close()
            folder.cleanup()

    def test_opening_stock_not_counted_as_period_inbound(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'per-open-pre', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-09-01', 'receivedUnknown': False, 'receivedOn': '2026-08-01',
                'lines': [{'size': 'M', 'qty': 7, 'unitCost': 800}],
            })
            stock.register_inbound(c, {
                'token': 'per-in-01', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 900, 'receivedOn': '2026-10-10',
            })
            c.commit()
            data = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            row = data['rows'][0]
            self.assertEqual(row['openingQty'], 7)
            self.assertEqual(row['inboundQty'], 2)
            self.assertEqual(row['closingQty'], 9)
            # Opening inside period → 訂正増減, not 入庫
            stock.register_opening(c, {
                'token': 'per-open-in', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-10-05', 'receivedUnknown': False, 'receivedOn': '2026-10-05',
                'lines': [{'size': 'L', 'qty': 3, 'unitCost': 700}],
            })
            c.commit()
            data2 = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31', 'size': 'L'})
            row_l = data2['rows'][0]
            self.assertEqual(row_l['openingQty'], 0)
            self.assertEqual(row_l['inboundQty'], 0)
            self.assertEqual(row_l['correctionQty'], 3)
            self.assertEqual(row_l['closingQty'], 3)
        finally:
            c.close()
            folder.cleanup()

    def test_multi_price_fifo_outbound_amounts(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'fifo-lot-a', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 2000, 'receivedOn': '2026-09-01',
            })
            stock.register_inbound(c, {
                'token': 'fifo-lot-b', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 500, 'receivedOn': '2026-09-15',
            })
            _out(c, handle, loc['id'], 7, 'fifo-out', '2026-10-05')
            c.commit()
            data = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            row = data['rows'][0]
            self.assertEqual(row['openingQty'], 10)
            self.assertEqual(row['outboundQty'], 7)
            self.assertEqual(row['outboundAmount'], 5 * 2000 + 2 * 500)
            costs = sorted(p['unitCost'] for p in row['priceBreakdown'])
            self.assertEqual(costs, [500, 2000])
            # Current master changes must not rewrite past outbound amounts (FIFO lot costs)
            cu_id = c.execute('SELECT customer_id FROM customer_products WHERE id=?', (handle,)).fetchone()[0]
            pr_id = c.execute('SELECT product_id FROM customer_products WHERE id=?', (handle,)).fetchone()[0]
            su_id = c.execute('SELECT supplier_id FROM products WHERE id=?', (pr_id,)).fetchone()[0]
            stock.save_product(c, {
                'id': pr_id, 'code': 'PER-1', 'name': '改名', 'color': '紺',
                'supplierId': su_id, 'active': True, 'sizes': ['M', 'L'],
                'links': [{'customerId': cu_id, 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            again = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            self.assertEqual(again['rows'][0]['outboundAmount'], 5 * 2000 + 2 * 500)
        finally:
            c.close()
            folder.cleanup()

    def test_correction_cancel_backdated_no_double_count(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            # Pre-period stock so a period-start outbound is valid by business date
            stock.register_inbound(c, {
                'token': 'corr-pre', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 1000, 'receivedOn': '2026-09-20',
            })
            stock.register_inbound(c, {
                'token': 'corr-in-01', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 10, 'unitCost': 1000, 'receivedOn': '2026-10-05',
            })
            lot_id = c.execute(
                'SELECT id FROM lots WHERE customer_product_id=? AND received_on=? ORDER BY id DESC',
                (handle, '2026-10-05'),
            ).fetchone()['id']
            stock.correct_inbound(c, {
                'lotId': lot_id, 'qty': 8, 'unitCost': 1000, 'reason': '数え直し',
                'businessOn': '2026-10-06',
            })
            # Backdated outbound relative to registration — business date = period start
            _out(c, handle, loc['id'], 2, 'corr-out', '2026-10-01')
            c.commit()
            data = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            row = data['rows'][0]
            self.assertEqual(row['openingQty'], 5)
            self.assertEqual(row['inboundQty'], 10)
            self.assertEqual(row['outboundQty'], 2)
            self.assertEqual(row['correctionQty'], -2)
            self.assertEqual(row['closingQty'], 11)
            self.assertTrue(row['identityOk'])
            kinds = [h['kind'] for h in row['history']]
            self.assertEqual(kinds.count('inbound'), 1)
            self.assertEqual(kinds.count('inbound_correction'), 1)
            self.assertEqual(kinds.count('outbound'), 1)
        finally:
            c.close()
            folder.cleanup()

    def test_missing_business_date_shows_unavailable_not_zero(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'miss-in-01', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            c.commit()
            # Simulate legacy outbound without business_on (do not invent from at)
            lot_id = c.execute('SELECT id FROM lots WHERE customer_product_id=?', (handle,)).fetchone()['id']
            c.execute(
                '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
                   location_id,actor_name,business_on,note,at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (lot_id, 'outbound', -1, 1000, 5, 4, 1000, 1000, loc['id'], 'tester', None, '旧出庫', '2026-10-08T12:00:00'),
            )
            c.execute('UPDATE lots SET qty_remaining=4 WHERE id=?', (lot_id,))
            c.commit()
            data = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            row = data['rows'][0]
            self.assertFalse(row['computable'])
            self.assertIsNone(row['openingQty'])
            self.assertIsNone(row['inboundQty'])
            self.assertIsNone(row['outboundQty'])
            self.assertIn('出庫', row['unavailableReason'])
            self.assertEqual(data['totals']['unavailableRows'], 1)
            self.assertEqual(data['totals']['inboundQty'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_insufficient_history_unavailable(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            # Orphan outbound before any inbound/opening → running balance negative
            stock.register_inbound(c, {
                'token': 'seed-for-lot', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 1000, 'receivedOn': '2026-10-20',
            })
            lot_id = c.execute('SELECT id FROM lots WHERE customer_product_id=?', (handle,)).fetchone()['id']
            c.execute(
                '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,
                   location_id,actor_name,business_on,note,at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (lot_id, 'outbound', -5, 1000, 0, 0, 1000, 1000, loc['id'], 'tester', '2026-10-01', '欠損履歴', '2026-10-01T09:00:00'),
            )
            c.commit()
            data = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            row = data['rows'][0]
            self.assertFalse(row['computable'])
            self.assertIn('履歴が足りない', row['unavailableReason'])
        finally:
            c.close()
            folder.cleanup()

    def test_filter_totals_and_start_after_end_error(self):
        folder, path, c = newdb()
        try:
            cu, pr, loc, handle = seed_masters(c)
            cu2 = stock.save_customer(c, {'name': '別病院', 'active': True})
            stock.save_product(c, {
                'id': pr['id'], 'code': pr['code'], 'name': pr['name'], 'color': pr['color'],
                'supplierId': pr['supplierId'], 'active': True, 'sizes': pr['sizes'],
                'links': [
                    {'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                    {'customerId': cu2['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                ],
            })
            links = {r['customer_id']: r['id'] for r in c.execute(
                'SELECT id,customer_id FROM customer_products WHERE product_id=?', (pr['id'],)
            )}
            stock.register_inbound(c, {
                'token': 'filter-in-1', 'customerProductId': links[cu['id']], 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1000, 'receivedOn': '2026-10-02',
            })
            stock.register_inbound(c, {
                'token': 'filter-in-2', 'customerProductId': links[cu2['id']], 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 1000, 'receivedOn': '2026-10-03',
            })
            c.commit()
            all_rows = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            self.assertEqual(all_rows['totals']['inboundQty'], 8)
            filtered = stock_period.summarize_period(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'customerId': str(cu['id']),
            })
            self.assertEqual(filtered['totals']['inboundQty'], 3)
            self.assertEqual(len(filtered['rows']), 1)
            with self.assertRaises(ValueError) as ctx:
                stock_period.summarize_period(c, {'from': '2026-10-31', 'to': '2026-10-01'})
            self.assertIn('開始日', str(ctx.exception))
        finally:
            c.close()
            folder.cleanup()

    def test_export_matches_screen_totals(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'export-in1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 1500, 'receivedOn': '2026-10-01',
            })
            _out(c, handle, loc['id'], 1, 'export-out1', '2026-10-15')
            c.commit()
            args = {'from': '2026-10-01', 'to': '2026-10-31'}
            data = stock_period.summarize_period(c, args)
            csv_bytes = stock_period.export_csv(c, args)
            text = csv_bytes.decode('utf-8-sig')
            reader = list(csv.reader(io.StringIO(text)))
            # Totals row includes inbound/outbound qty and amounts
            totals_line = next(r for r in reader if r and r[0] == '合計 入庫数')
            self.assertEqual(int(totals_line[1]), data['totals']['inboundQty'])
            self.assertEqual(int(totals_line[3]), data['totals']['outboundQty'])
            self.assertEqual(int(totals_line[5]), data['totals']['correctionQty'])
            self.assertEqual(int(totals_line[7]), data['totals']['inboundAmount'])
            self.assertEqual(int(totals_line[9]), data['totals']['outboundAmount'])
            xlsx = stock_period.export_xlsx(c, args)
            self.assertTrue(xlsx[:2] == b'PK')
            with zipfile.ZipFile(io.BytesIO(xlsx)) as zf:
                self.assertTrue(any(n.endswith('.xml') for n in zf.namelist()))
        finally:
            c.close()
            folder.cleanup()

    def test_history_popup_payload_and_readonly_api(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'history-in', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 6, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            _out(c, handle, loc['id'], 2, 'hist-out', '2026-10-10')
            c.commit()
            hist = stock_period.period_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31',
                'customerProductId': str(handle), 'size': 'M', 'locationId': str(loc['id']),
            })
            self.assertEqual(len(hist['events']), 2)
            self.assertEqual(hist['events'][0]['kind'], 'inbound')
            self.assertEqual(hist['events'][1]['kind'], 'outbound')
            self.assertEqual(hist['events'][1]['businessOn'], '2026-10-10')
            # GET period APIs work under can_write=False; do not mutate qty
            before = stock.current_qty_for_target(c, handle, 'M', loc['id'])
            code, body = server.api_get(path, 'live', '/api/period-summary', {
                'from': ['2026-10-01'], 'to': ['2026-10-31'],
            }, can_write=False)
            self.assertEqual(code, 200)
            self.assertEqual(body['totals']['outboundQty'], 2)
            after = stock.current_qty_for_target(c, handle, 'M', loc['id'])
            self.assertEqual(before, after)
            # Period endpoints are not write paths
            self.assertNotIn('/api/period-summary', server.WRITE_PATHS)
        finally:
            c.close()
            folder.cleanup()

    def test_uses_business_date_not_registration_stamp(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'business-in', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 1000, 'receivedOn': '2026-09-20',
            })
            # Register "today" but business date in September → outside October period inbound
            _out(c, handle, loc['id'], 1, 'business-out', '2026-09-25')
            c.commit()
            # Force registration timestamp far from business date
            c.execute("UPDATE moves SET at='2026-10-15T12:00:00' WHERE kind='outbound'")
            c.commit()
            data = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            row = data['rows'][0]
            self.assertEqual(row['openingQty'], 3)
            self.assertEqual(row['inboundQty'], 0)
            self.assertEqual(row['outboundQty'], 0)
            self.assertEqual(row['closingQty'], 3)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
