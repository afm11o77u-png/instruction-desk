"""発注書の印刷記録（temp DB only）."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_orders
from test_purchase_orders import newdb, seed, po_body

W = {'_canWrite': True, '_actor': {'name': '担当太郎'}}


class PoPrintRecordTest(unittest.TestCase):
    def setUp(self):
        self.folder, _, self.c = newdb()
        self.fx = seed(self.c)
        self.po = stock_orders.save_purchase_order(self.c, po_body(self.fx, token='po-print-rec-01'))

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def row(self):
        return [r for r in stock_orders.list_purchase_orders(self.c, {})['orders'] if r['id'] == self.po['id']][0]

    def test_unprinted_then_printed(self):
        self.assertIsNone(self.row()['printedAt'])
        a = stock_orders.mark_printed(self.c, {'id': self.po['id'], **W})
        b = stock_orders.mark_printed(self.c, {'id': self.po['id'], **W})
        self.assertTrue(b['printedAt'])
        self.assertEqual(b['printCount'], 2)
        self.assertGreaterEqual(b['printedAt'], a['printedAt'])
        self.assertTrue(self.row()['printedAt'])
        self.assertFalse(self.row()['needsReprint'])

    def test_edit_after_print_needs_reprint(self):
        stock_orders.save_purchase_order(self.c, po_body(self.fx, id=self.po['id'], token='po-print-edit-01', remarks='変更前'))
        self.assertFalse(self.row()['needsReprint'])  # 未印刷の編集はフラグなし
        stock_orders.mark_printed(self.c, {'id': self.po['id'], **W})
        stock_orders.save_purchase_order(self.c, po_body(self.fx, id=self.po['id'], token='po-print-edit-02', remarks='変更後'))
        self.assertTrue(self.row()['needsReprint'])
        stock_orders.mark_printed(self.c, {'id': self.po['id'], **W})
        self.assertFalse(self.row()['needsReprint'])

    def test_requires_write_and_migration_idempotent(self):
        with self.assertRaises(Exception):
            stock_orders.mark_printed(self.c, {'id': self.po['id'], '_canWrite': False})
        stock_orders.migrate_purchase_orders(self.c)
        stock_orders.migrate_purchase_orders(self.c)
        self.assertFalse(stock_orders.needs_purchase_order_migration(self.c))


if __name__ == '__main__':
    unittest.main()
