import sqlite3
import unittest

import auth


def row(role, manage=0, overview=0):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    return c.execute('SELECT ? AS role, ? AS manage, ? AS overview', (role, manage, overview)).fetchone()


class StockFlagsRowTest(unittest.TestCase):
    """ログイン中の利用者は sqlite3.Row。管理者・閲覧の印で在庫管理を開けること。"""

    def test_manage_flag_opens_and_writes(self):
        u = row('sales', manage=1)
        self.assertTrue(auth.can_open_stock(u))
        self.assertTrue(auth.stock_can_write(u))
        self.assertFalse(auth.stock_shelves_only(u))

    def test_overview_flag_opens_read_only(self):
        u = row('floor', overview=1)
        self.assertTrue(auth.can_open_stock(u))
        self.assertFalse(auth.stock_can_write(u))

    def test_plain_roles_and_floor3(self):
        self.assertFalse(auth.can_open_stock(row('floor')))
        self.assertTrue(auth.stock_shelves_only(row('floor3')))
        self.assertFalse(auth.stock_shelves_only(row('floor3', overview=1)))
        self.assertTrue(auth.can_open_stock(row('floor3', overview=1)))


if __name__ == '__main__':
    unittest.main()
