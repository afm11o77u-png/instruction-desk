"""初期在庫の全削除。合成DBだけ。実データは消さない。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import opening_stock_wipe as wipe  # noqa: E402
import qr_link_import  # noqa: E402
import stock  # noqa: E402


def newdb():
    import tempfile
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def seed_opening(c):
    cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
    su = stock.save_supplier(c, {'name': '仕入A', 'active': True})
    pr = stock.save_product(c, {
        'code': 'AZ-1', 'name': '白衣', 'color': '', 'category': '制服',
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
    stock.register_opening(c, {
        '_canWrite': True, '_actor': {'name': 'seed', 'role': 'owner'},
        'token': 'open-wipe-seed-1',
        'customerProductId': pr['links'][0]['id'],
        'locationId': loc['id'], 'condition': 'new',
        'basisDate': '2026-01-01', 'receivedUnknown': True, 'payoutOrder': 1,
        'allowZero': True,
        'lines': [{'size': 'M', 'qty': 2, 'unitCost': 100}],
    })
    c.execute(
        '''INSERT INTO lots(batch_id,customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
           basis_date,received_unknown,payout_order,condition,created_at,updated_at)
           VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (pr['links'][0]['id'], 'L', loc['id'], 50, 1, 1, '2026-01-02', 0, 0, 'new', 't', 't'),
    )
    qr_link_import.ensure_schema(c)
    c.execute(
        '''INSERT INTO qr_stock_links(
             legacy_stock_id, legacy_customer_id, legacy_maker_id, customer_id, product_id,
             customer_product_id, size_label, condition, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)''',
        ('old-1', 'c', 'm', cu['id'], pr['id'], pr['links'][0]['id'], 'M', 'new', 't', 't'),
    )
    c.commit()
    return pr


OWNER = {'_canWrite': True, '_actor': {'name': '主管理者', 'role': 'owner'}, 'confirmText': wipe.CONFIRM_TEXT}


class OpeningStockWipeTest(unittest.TestCase):
    def test_refuses_without_owner_or_confirm_and_deletes_only_opening(self):
        folder, path, c = newdb()
        try:
            seed_opening(c)
            with self.assertRaises(PermissionError):
                wipe.preview_wipe(c, {'_actor': {'role': 'staff'}})
            with self.assertRaises(ValueError):
                wipe.execute_wipe(c, {'_canWrite': True, '_actor': {'role': 'owner'}, 'confirmText': 'ちがう'})
            preview = wipe.preview_wipe(c, OWNER)
            self.assertTrue(preview['canWipe'])
            self.assertEqual(preview['counts']['openingLots'], 1)
            self.assertIn('旧QRの対応', preview['kept'])
            result = wipe.execute_wipe(c, OWNER, db_path=str(path))
            c.commit()
            self.assertTrue(result['ok'])
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM lots WHERE batch_id IS NOT NULL').fetchone()['n'], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM lots WHERE batch_id IS NULL').fetchone()['n'], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)
            self.assertEqual(c.execute('SELECT qty_remaining FROM lots').fetchone()['qty_remaining'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_blocks_when_opening_has_later_history_and_rolls_back_nothing(self):
        folder, path, c = newdb()
        try:
            seed_opening(c)
            lot = c.execute('SELECT id, qty_remaining FROM lots WHERE batch_id IS NOT NULL').fetchone()
            c.execute(
                '''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,actor_name,note,at)
                   VALUES(?,?,?,?,?,?,?,?,?)''',
                (lot['id'], 'outbound', -1, 100, 2, 1, 't', '', '2026-02-01T00:00:00'),
            )
            c.execute('UPDATE lots SET qty_remaining=? WHERE id=?', (1, lot['id']))
            c.commit()
            before = c.execute('SELECT COUNT(*) n FROM lots').fetchone()['n']
            with self.assertRaises(wipe.OpeningWipeBlocked):
                wipe.execute_wipe(c, OWNER, db_path=str(path))
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], before)
            self.assertEqual(c.execute('SELECT qty_remaining FROM lots WHERE id=?', (lot['id'],)).fetchone()['qty_remaining'], 1)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
