import base64, io, json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import stock, stock_prices, server  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 't.db')
        self.c = stock.connect(self.db)
        stock.initialize(self.c)
        self.t = stock.now()
        self.loc = self.c.execute('SELECT id FROM locations LIMIT 1').fetchone()[0]
        self.ids = {}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _id(self, table, name):
        key = (table, name)
        if key not in self.ids:
            self.ids[key] = self.c.execute(
                f'INSERT INTO {table}(name,created_at,updated_at) VALUES(?,?,?)', (name, self.t, self.t)).lastrowid
        return self.ids[key]

    def product(self, code, supplier, name='品'):
        key = ('p', code, supplier)
        if key not in self.ids:
            self.ids[key] = self.c.execute(
                'INSERT INTO products(code,name,supplier_id,created_at,updated_at) VALUES(?,?,?,?,?)',
                (code, name, self._id('suppliers', supplier), self.t, self.t)).lastrowid
        return self.ids[key]

    def lot(self, customer, code, size, price, stock_qty=1, buy_qty=None, supplier='S1', name='品'):
        pid = self.product(code, supplier, name)
        cid = self._id('customers', customer)
        row = self.c.execute('SELECT id FROM customer_products WHERE customer_id=? AND product_id=?', (cid, pid)).fetchone()
        cp = row[0] if row else self.c.execute(
            'INSERT INTO customer_products(customer_id,product_id,created_at,updated_at) VALUES(?,?,?,?)',
            (cid, pid, self.t, self.t)).lastrowid
        self.c.execute(
            '''INSERT INTO lots(customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
               basis_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)''',
            (cp, size, self.loc, price, buy_qty if buy_qty is not None else stock_qty, stock_qty, '2026-01-01', self.t, self.t))
        return pid

    def st(self, customer, code, size, price, name='品'):
        return {'rowNo': 2, 'customer': customer, 'name': name, 'code': code, 'size': size, 'price': price, 'qty': 1}

    def base(self, pid, size, supplier='S1'):
        r = self.c.execute('SELECT price,status,reason FROM price_base WHERE product_id=? AND size_label=? AND supplier_id=?',
                           (pid, size, self._id('suppliers', supplier))).fetchone()
        return r and tuple(r)


class RulesTest(Base):
    def test_rule_b_stocktake_max_and_flags(self):
        pid = self.lot('A', 'X1', 'M', 1000)
        self.lot('B', 'X1', 'M', 1000)
        res = stock_prices.run_initial(self.c, {}, srows=[
            self.st('A', 'X1', 'M', 1200.0), self.st('A', 'X1', 'M', 0), self.st('B', 'X1', 'M', 1100)])
        self.assertEqual(self.base(pid, 'M'), (1200, 'review', stock_prices.REASON_STOCKTAKE_VS_DB))
        self.assertEqual(res['summary']['fromStocktake'], 1)

    def test_rule_b_same_as_db_ok_and_customer_spread(self):
        pid = self.lot('A', 'X1', 'M', 1000)
        self.lot('B', 'X1', 'M', 400)
        stock_prices.run_initial(self.c, {}, srows=[self.st('A', 'X1', 'M', 1000)])
        self.assertEqual(self.base(pid, 'M'), (1000, 'ok', ''))
        stock_prices.run_initial(self.c, {}, srows=[self.st('A', 'X1', 'M', 1000), self.st('B', 'X1', 'M', 500)])
        self.assertEqual(self.base(pid, 'M'), (1000, 'review', stock_prices.REASON_CUSTOMER_SPREAD))

    def test_rule_c_max_or_mode(self):
        pid = self.lot('A', 'X1', 'M', 1000, stock_qty=5)
        self.lot('A', 'X1', 'M', 900, stock_qty=50)
        self.lot('A', 'X1', 'M', 0, stock_qty=99)
        p2 = self.lot('A', '9910', '-', 70500, stock_qty=1)
        self.lot('A', '9910', '-', 360, stock_qty=2, buy_qty=10)
        self.lot('A', '9910', '-', 380, stock_qty=0, buy_qty=3)
        stock_prices.run_initial(self.c, {}, srows=[])
        self.assertEqual(self.base(pid, 'M'), (1000, 'ok', ''))
        self.assertEqual(self.base(p2, '-'), (360, 'review', stock_prices.REASON_MAX_VS_MODE))

    def test_customer_spread_across_split_products(self):
        # 同じ品番が顧客ごとに別商品として登録されていても顧客間2倍ルールで比較する
        p1 = self.lot('北の食品', 'DBP6713', 'L', 24, name='帽子（緑）')
        self.ids.pop(('p', 'DBP6713', 'S1'))
        p2 = self.lot('釧路丸水', 'DBP6713', 'L', 1150, name='頭巾')
        self.assertNotEqual(p1, p2)
        stock_prices.run_initial(self.c, {}, srows=[
            self.st('北の食品', 'DBP6713', 'L', 24, name='帽子（緑）'), self.st('釧路丸水', 'DBP6713', 'L', 1150, name='頭巾')])
        self.assertEqual(self.base(p1, 'L'), (24, 'review', stock_prices.REASON_CUSTOMER_SPREAD))
        self.assertEqual(self.base(p2, 'L'), (1150, 'review', stock_prices.REASON_CUSTOMER_SPREAD))

    def test_mode_weight_stock_then_purchase(self):
        lots = [{'price': 100, 'stockQty': 0, 'buyQty': 9}, {'price': 200, 'stockQty': 1, 'buyQty': 0},
                {'price': 300, 'stockQty': 1, 'buyQty': 5}]
        self.assertEqual(stock_prices._mode_price(lots), 300)

    def test_rule_d_unset_and_stocktake_zero_falls_back(self):
        pid = self.lot('A', 'X1', 'M', 0)
        p2 = self.lot('A', 'X2', 'M', 800)
        stock_prices.run_initial(self.c, {}, srows=[self.st('A', 'X1', 'M', 0), self.st('A', 'X2', 'M', 0)])
        self.assertEqual(self.base(pid, 'M'), (None, 'unset', ''))
        self.assertEqual(self.base(p2, 'M'), (800, 'ok', ''))

    def test_supplier_from_db_split_groups(self):
        p1 = self.lot('A', 'X1', 'M', 1000, supplier='S1')
        p2 = self.lot('B', 'X1', 'M', 700, supplier='S2')
        stock_prices.run_initial(self.c, {}, srows=[self.st('B', 'X1', 'M', 750)])
        self.assertEqual(self.base(p1, 'M', 'S1'), (1000, 'ok', ''))
        self.assertEqual(self.base(p2, 'M', 'S2')[0], 750)


class MatchTest(Base):
    def test_normalization_and_candidates(self):
        a = self.lot('㈱ＡＢ', '105', 'M', 100)
        b = self.lot('C', '6991-099', 'L', 100)
        z = self.lot('C', '00051', 'F', 100)
        d = self.lot('C', '2023-01', 'F', 100)
        m = self.lot('㈱道新総合印刷', '9008-1', 'F', 100)
        res = stock_prices.run_initial(self.c, {}, srows=[
            self.st('㈱AB ', 105.0, 'Ｍ', 111), self.st('C', '6991-099(2601へ変更)', 'L', 112),
            self.st('C', 51, 'F', 113), self.st('C', 44927, 'F', 114),
            self.st('㈱道新総合印刷', 2596145, 'F', 115), self.st('C', 'NONE', 'F', 1), self.st('C', '', 'F', 1)])
        self.assertEqual([self.base(p, s)[0] for p, s in ((a, 'M'), (b, 'L'), (z, 'F'), (d, 'F'), (m, 'F'))],
                         [111, 112, 113, 114, 115])
        self.assertEqual(res['summary']['matched'], 5)
        self.assertEqual(res['summary']['ignored'], 2)
        self.assertEqual({r['reason'] for r in res['ignoredRows']}, {'一致なし', '顧客か品番が空'})

    def test_helpers(self):
        self.assertEqual(stock_prices.norm(105.0), '105')
        self.assertEqual(stock_prices.serial_to_ym('45292'), '2024-01')
        self.assertEqual(stock_prices.strip_paren_memo('6991-099（2601へ変更）'), '6991-099')

    def test_code_map_editable(self):
        p = self.lot('Z', 'REAL', 'F', 100)
        stock_prices.save_code_map(self.c, {'customerName': '', 'stockCode': 'OLD', 'realCode': 'REAL'})
        stock_prices.run_initial(self.c, {}, srows=[self.st('Z', 'OLD', 'F', 150)])
        self.assertEqual(self.base(p, 'F')[0], 150)
        maps = stock_prices.code_maps(self.c)
        self.assertIn(('㈱道新総合印刷', '2596145', '9008-1'), {(m['customerName'], m['stockCode'], m['realCode']) for m in maps})
        mid = [m['id'] for m in maps if m['stockCode'] == 'OLD'][0]
        stock_prices.save_code_map(self.c, {'id': mid, 'delete': True})
        self.assertNotIn('OLD', [m['stockCode'] for m in stock_prices.code_maps(self.c)])

    def test_xlsx_reader(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['コード', '顧客', '商品名', '商品コード', 'サイズ', '仕入単価', '在庫数'])
        ws.append([1.0, 'A', 'n', 105.0, 'M', 700.0, 3])
        wb.create_sheet('other').append(['x'])
        buf = io.BytesIO()
        wb.save(buf)
        rows = stock_prices.read_stocktake_xlsx(buf.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(stock_prices.norm(rows[0]['code']), '105')
        p = self.lot('A', '105', 'M', 500)
        stock_prices.run_initial(self.c, {'file': base64.b64encode(buf.getvalue()).decode(), 'name': 't.xlsx'})
        self.assertEqual(self.base(p, 'M')[0], 700)


class OverwriteAndEditTest(Base):
    def test_rerun_overwrites_and_keeps_qty(self):
        p = self.lot('A', 'X1', 'M', 1000, stock_qty=7)
        stock_prices.run_initial(self.c, {}, srows=[])
        bid = self.c.execute('SELECT id FROM price_base').fetchone()[0]
        stock_prices.save_base(self.c, {'id': bid, 'price': '1500'})
        self.assertEqual(self.base(p, 'M'), (1500, 'ok', ''))
        stock_prices.run_initial(self.c, {}, srows=[])
        self.assertEqual(self.base(p, 'M'), (1000, 'ok', ''))
        self.assertEqual(self.c.execute('SELECT SUM(qty_remaining) FROM lots').fetchone()[0], 7)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM price_base').fetchone()[0], 1)

    def test_edit_clears_review_and_lists(self):
        p = self.lot('A', 'X1', 'M', 1000)
        self.lot('A', 'X2', 'M', 0)
        stock_prices.run_initial(self.c, {}, srows=[self.st('A', 'X1', 'M', 1200)])
        items = stock_prices.review_list(self.c)['items']
        self.assertEqual(sorted(i['statusLabel'] for i in items), ['未設定', '要確認'])
        rid = [i['id'] for i in items if i['statusLabel'] == '要確認'][0]
        stock_prices.save_base(self.c, {'id': rid, 'price': 1100})
        self.assertEqual(self.base(p, 'M'), (1100, 'ok', ''))
        self.assertEqual(len(stock_prices.review_list(self.c)['items']), 1)

    def test_special_lookup_no_auto(self):
        p = self.lot('A', 'X1', 'M', 1000)
        self.lot('B', 'X1', 'M', 500)
        stock_prices.run_initial(self.c, {}, srows=[])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM price_special').fetchone()[0], 0)
        a, b, s1 = self._id('customers', 'A'), self._id('customers', 'B'), self._id('suppliers', 'S1')
        look = lambda cid, size='M': stock_prices.lookup(self.c, customer_id=cid, product_id=p, size_label=size)
        self.assertEqual((look(b)['price'], look(b)['source']), (1000, 'base'))
        stock_prices.save_special(self.c, {'customerId': b, 'supplierId': s1, 'productId': p, 'price': '800'})
        self.assertEqual((look(b)['price'], look(b)['source']), (800, 'special'))
        stock_prices.save_special(self.c, {'customerId': b, 'supplierId': s1, 'productId': p, 'size': 'M', 'price': 850})
        self.assertEqual(look(b)['price'], 850)
        self.assertEqual(look(a)['price'], 1000)
        sp = stock_prices.product_prices(self.c, p)['specials']
        self.assertEqual(len(sp), 2)
        stock_prices.save_special(self.c, {'id': sp[0]['id'], 'price': 900, 'size': sp[0]['size']})
        for s in sp:
            stock_prices.save_special(self.c, {'id': s['id'], 'delete': True})
        self.assertEqual(look(b)['price'], 1000)


class MigrationAndApiTest(unittest.TestCase):
    def test_existing_db_migrates_without_reregister(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'old.db')
            c = sqlite3.connect(db)
            c.executescript(stock.SCHEMA)
            c.execute("INSERT INTO settings VALUES('x','1')")
            c.commit()
            c.close()
            server.prepare(db, 'real')
            c = stock.connect(db)
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'price_base', 'price_special', 'price_code_map'} <= names)
            self.assertFalse(stock_prices.needs_migration(c))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM price_code_map').fetchone()[0], 1)
            c.close()

    def test_api_routes(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 's.db')
            server.prepare(db, 'sample')
            for p in ('/api/prices/initial', '/api/prices/base', '/api/prices/special', '/api/prices/code-map'):
                self.assertIn(p, server.WRITE_PATHS)
            st, body = server.api_post(db, 'sample', '/api/prices/initial', {})[:2]
            self.assertEqual(st, 200, body)
            st, body = server.api_get(db, 'sample', '/api/prices/review', {})[:2]
            self.assertEqual(st, 200)
            st, body = server.api_get(db, 'sample', '/api/prices/code-map', {})[:2]
            self.assertEqual(len(body['maps']), 1)
            with stock.connect(db) as c:
                row = c.execute('''SELECT cp.customer_id,cp.product_id,l.size_label FROM lots l
                                   JOIN customer_products cp ON cp.id=l.customer_product_id LIMIT 1''').fetchone()
            if row:
                st, body = server.api_get(db, 'sample', '/api/prices/lookup', {
                    'customerId': [str(row[0])], 'productId': [str(row[1])], 'size': [row[2]]})[:2]
                self.assertEqual(st, 200)
                self.assertIn('price', body)


class UiWiringTest(unittest.TestCase):
    def test_ui_hooks(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'current_price.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('current_price.js', html)
        self.assertIn('id="priceInitOpen"', app)
        self.assertIn('id="priceRulesOpen"', app)
        self.assertIn('currentPriceBox', app)
        for s in ('/api/prices/initial', '/api/prices/review', '/api/prices/special', '/api/prices/code-map', '取り込みルール'):
            self.assertIn(s, js)


if __name__ == '__main__':
    unittest.main()
