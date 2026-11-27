"""出荷情報一覧：既存の出荷情報と同じ処理で、保存先だけ別（空から始まり、互いに書き込まない）。"""
import sqlite3
import unittest
import shipping


class H:
    def __init__(self, path): self.path = path; self.user = dict(id=1, name='主管理者', login='o', role='owner', must_change=0)


class ShipmentListTest(unittest.TestCase):
    def test_separate_store_starts_empty(self):
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; shipping.schema(c)
        old, new = H('/api/shipments'), H('/api/shipment-list')
        shipping.create(old, c, dict(customer='既存顧客'))
        self.assertEqual(shipping.search(H('/api/shipment-list?customer='), c)[1]['total'], 0)  # 空から始まる
        row = shipping.create(new, c, dict(customer='新しい顧客', weekday='火・金'))[1]
        self.assertEqual(row['number'], 900001)  # 既存のExcelの登録番号と重ならない
        self.assertEqual([x['customer'] for x in shipping.search(H('/api/shipments?customer='), c)[1]['items']], ['既存顧客'])
        self.assertEqual([x['customer'] for x in shipping.search(H('/api/shipment-list?customer='), c)[1]['items']], ['新しい顧客'])
        self.assertEqual(shipping.customers(new, c)[1]['customers'], ['新しい顧客'])
        # 一覧から書き出したExcelを既存の出荷情報へ取り込んでも、番号が見つからず止まる（上書きしない）
        book = shipping.export_book(new, c)[1]
        status, result = shipping.import_book(H('/api/shipments/import'), c, book)
        self.assertEqual(status, 400); self.assertIn('見つかりません', result['error'])
        self.assertEqual(shipping.import_book(H('/api/shipment-list/import'), c, book)[1]['unchanged'], 1)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM shipments').fetchone()[0], 1)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM shipment_list_history').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
