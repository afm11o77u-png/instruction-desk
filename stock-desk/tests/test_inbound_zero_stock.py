"""Zero-stock combos stay on 入庫一覧 for daily restock (temp DB only)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def seed_masters(c):
    cu = stock.save_customer(c, {'name': 'ゼロ在庫病院', 'active': True})
    su = stock.save_supplier(c, {'name': 'ゼロ在庫仕入先', 'active': True})
    pr = stock.save_product(c, {
        'code': 'Z-1', 'name': 'ゼロスクラブ', 'color': '白',
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': 'ゼロ倉庫', 'active': True})
    handle = pr['links'][0]['id']
    return cu, pr, loc, handle


class InboundZeroStockTest(unittest.TestCase):
    def test_inbound_list_keeps_zero_stock_combo_default(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'zero-list-in1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 3,
            })
            stock.register_outbound(c, {
                'token': 'zero-list-out1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'fingerprint': plan['fingerprint'],
                'businessOn': '2026-10-02',
            })
            c.commit()
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 0)

            inbound = stock.move_targets(c, {'mode': 'in'})
            self.assertEqual(len(inbound['targets']), 1)
            row = inbound['targets'][0]
            self.assertEqual(row['qty'], 0)
            self.assertEqual(row['amount'], 0)
            self.assertEqual(row['customerProductId'], handle)
            self.assertEqual(row['size'], 'M')
            self.assertEqual(row['locationId'], loc['id'])

            # No「在庫0も表示」flag required — default inbound list includes zeros
            self.assertEqual(inbound['mode'], 'in')

            outbound = stock.move_targets(c, {'mode': 'out'})
            self.assertEqual(outbound['targets'], [])
            # Default / missing mode stays outbound-safe (no zero rows)
            self.assertEqual(stock.move_targets(c, {})['targets'], [])
        finally:
            c.close()
            folder.cleanup()

    def test_no_duplicate_rows_for_multi_price_receipts(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'dup-price-a', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 1000, 'receivedOn': '2026-09-01',
            })
            stock.register_inbound(c, {
                'token': 'dup-price-b', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 1200, 'receivedOn': '2026-09-15',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 4,
            })
            stock.register_outbound(c, {
                'token': 'dup-price-out', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'fingerprint': plan['fingerprint'],
                'businessOn': '2026-10-01',
            })
            c.commit()
            targets = stock.move_targets(c, {'mode': 'in'})
            self.assertEqual(len(targets['targets']), 1)
            self.assertEqual(targets['targets'][0]['qty'], 0)
            # Never-stocked size L is not auto-generated
            self.assertFalse(any(t['size'] == 'L' for t in targets['targets']))
        finally:
            c.close()
            folder.cleanup()

    def test_restock_at_zero_new_lot_and_history(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            first = stock.register_inbound(c, {
                'token': 'restock-first', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 800, 'receivedOn': '2026-09-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 5,
            })
            stock.register_outbound(c, {
                'token': 'restock-drain', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'fingerprint': plan['fingerprint'],
                'businessOn': '2026-09-20',
            })
            c.commit()
            old_lot = first['lotId']
            old_remaining = c.execute('SELECT qty_remaining FROM lots WHERE id=?', (old_lot,)).fetchone()['qty_remaining']
            self.assertEqual(old_remaining, 0)

            # Select zero-stock target → restock (same combo, new receipt lot)
            target = stock.move_targets(c, {'mode': 'in'})['targets'][0]
            self.assertEqual(target['qty'], 0)
            second = stock.register_inbound(c, {
                'token': 'restock-again', 'customerProductId': target['customerProductId'],
                'size': target['size'], 'locationId': target['locationId'],
                'qty': 4, 'unitCost': 950, 'receivedOn': '2026-10-08',
            })
            c.commit()
            self.assertNotEqual(second['lotId'], old_lot)
            # Past zeroed receipt not overwritten
            self.assertEqual(
                c.execute('SELECT qty_remaining,qty_received,unit_cost FROM lots WHERE id=?', (old_lot,)).fetchone()['qty_remaining'],
                0,
            )
            self.assertEqual(
                c.execute('SELECT qty_remaining,unit_cost FROM lots WHERE id=?', (second['lotId'],)).fetchone()['unit_cost'],
                950,
            )
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 4)

            after = stock.move_targets(c, {'mode': 'in'})['targets'][0]
            self.assertEqual(after['qty'], 4)
            self.assertEqual(after['amount'], 4 * 950)

            # Current stock / amount on list and product view reflect restock only
            view = stock.stock_query(c, {'group': 'product'})
            row = view['groups'][0]['rows'][0]
            self.assertEqual(row['qty'], 4)
            self.assertEqual(row['amount'], 4 * 950)

            hist = stock.lot_history(c, second['lotId'])
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[0]['kind'], 'inbound')
            self.assertEqual(hist[0]['qtyDelta'], 4)
            self.assertEqual(hist[0]['unitCost'], 950)
            hist_old = stock.lot_history(c, old_lot)
            kinds = [e['kind'] for e in hist_old]
            self.assertIn('inbound', kinds)
            self.assertIn('outbound', kinds)
        finally:
            c.close()
            folder.cleanup()

    def test_search_includes_zero_stock_and_inactive_excluded(self):
        folder, path, c = newdb()
        try:
            cu, pr, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'search-zero-in', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 1,
            })
            stock.register_outbound(c, {
                'token': 'search-zero-out', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'fingerprint': plan['fingerprint'],
                'businessOn': '2026-10-02',
            })
            c.commit()
            found = stock.move_targets(c, {'mode': 'in', 'q': 'ゼロスクラブ'})
            self.assertEqual(len(found['targets']), 1)
            self.assertEqual(found['targets'][0]['qty'], 0)

            # Deactivate handling → drop from inbound list
            stock.save_product(c, {
                'id': pr['id'], 'code': pr['code'], 'name': pr['name'], 'color': pr['color'],
                'supplierId': pr['supplierId'], 'active': True, 'sizes': pr['sizes'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': False}],
            })
            c.commit()
            self.assertEqual(stock.move_targets(c, {'mode': 'in'})['targets'], [])
        finally:
            c.close()
            folder.cleanup()

    def test_opening_then_zero_still_listed_for_inbound(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'open-then-zero', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-08-01', 'receivedUnknown': False, 'receivedOn': '2026-07-01',
                'lines': [{'size': 'L', 'qty': 2, 'unitCost': 700}],
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'L', 'locationId': loc['id'], 'qty': 2,
            })
            stock.register_outbound(c, {
                'token': 'open-then-out', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 2, 'fingerprint': plan['fingerprint'],
                'businessOn': '2026-08-15',
            })
            c.commit()
            inbound = stock.move_targets(c, {'mode': 'in'})
            self.assertEqual(len(inbound['targets']), 1)
            self.assertEqual(inbound['targets'][0]['size'], 'L')
            self.assertEqual(inbound['targets'][0]['qty'], 0)
            outbound = stock.move_targets(c, {'mode': 'out'})
            self.assertEqual(outbound['targets'], [])
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
