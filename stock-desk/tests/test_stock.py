import json, os, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock, server, sample_data

ok = 0
ng = 0

def check(name, cond):
    global ok, ng
    if cond:
        ok += 1
        print('OK', name)
    else:
        ng += 1
        print('NG', name)

def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c

def test_rules():
    folder, path, c = newdb()
    try:
        sample_data.seed(c)
        c.commit()
        aoba = c.execute("SELECT id FROM customers WHERE name='あおば記念病院'").fetchone()['id']
        product = c.execute("SELECT id FROM products WHERE supplier_id=(SELECT id FROM suppliers WHERE name='北光繊維')").fetchone()['id']
        other = c.execute("SELECT id FROM products WHERE supplier_id=(SELECT id FROM suppliers WHERE name='丸善被服')").fetchone()['id']
        check('同じ品番でも商品IDは別', product != other)
        sizes = [r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=? ORDER BY sort_order', (product,))]
        check('LLと2Lを別サイズのまま持つ', sizes == ['M', 'L', 'LL', '2L'])
        view = stock.stock_query(c, {'customerId': str(aoba), 'productId': str(product), 'size': 'M', 'spec': 'none'})
        check('1000円×10と1200円×5で15着', view['totalQty'] == 15)
        check('金額は16000円', view['totalAmount'] == 16000)
        product_view = stock.stock_query(c, {'customerId': str(aoba), 'productId': str(product), 'size': 'M', 'spec': 'none', 'group': 'product'})
        check('商品別の合計が15着16000円', product_view['groups'][0]['qty'] == 15 and product_view['groups'][0]['amount'] == 16000)
        check('単価別内訳がある', sorted(p['unitCost'] for p in product_view['groups'][0]['parts']) == [1000, 1200])
        kashiwa = c.execute("SELECT id FROM customers WHERE name='かしわ食品 本店'").fetchone()['id']
        mixed = stock.stock_query(c, {'customerId': str(aoba), 'size': 'M', 'spec': 'none'})
        other_customer = stock.stock_query(c, {'customerId': str(kashiwa)})
        check('別顧客の在庫が混ざらない', other_customer['totalQty'] == 3 and all(l['customerId'] == aoba for l in mixed['lots']))
        embroidered = stock.stock_query(c, {'customerId': str(aoba), 'productId': str(product), 'spec': '左胸ネーム'})
        check('刺繍ありは別在庫', embroidered['totalQty'] == 2 and embroidered['lots'][0]['embroideryLabel'] == '左胸ネーム')
        shelf = stock.stock_query(c, {'customerId': str(aoba), 'productId': str(product), 'size': 'LL'})
        check('保管場所とサイズで分かれる', shelf['lots'][0]['locationLabel'] == '石狩倉庫①／1-A' and shelf['totalQty'] == 1)
        unknown = c.execute("SELECT received_on,received_unknown FROM lots WHERE received_unknown=1 LIMIT 1").fetchone()
        check('入荷日不明は架空日付にしない', unknown['received_on'] is None and unknown['received_unknown'] == 1)
        batch = c.execute("SELECT COUNT(*) AS n FROM lots WHERE batch_id=(SELECT id FROM opening_batches WHERE client_token='sample-opening-price-split')").fetchone()['n']
        check('複数サイズを1回で登録', batch == 3)
        before = c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n']
        again = stock.register_opening(c, {'token': 'sample-opening-price-split'})
        c.commit()
        after = c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n']
        check('同じ登録の再送は二重計上しない', again['already'] is True and before == after)
        try:
            stock.register_opening(c, {'token': 'blank-cost', 'customerProductId': product_view['lots'][0] and c.execute('SELECT customer_product_id FROM lots LIMIT 1').fetchone()[0],
                'locationId': 1, 'basisDate': '2026-09-01', 'receivedUnknown': True, 'payoutOrder': 2,
                'lines': [{'size': 'M', 'qty': 1, 'unitCost': ''}]})
            check('空欄単価を拒否', False)
        except ValueError as e:
            check('空欄単価を拒否', '0円' in str(e))
        c.rollback()
        lot = next(l for l in stock.stock_query(c, {'customerId': str(aoba), 'productId': str(product), 'size': 'M', 'spec': 'none'})['lots'] if l['unitCost'] == 1000)
        fixed = stock.correct_opening(c, {'lotId': lot['id'], 'qty': 8, 'unitCost': 1000, 'reason': '数え直し'})
        c.commit()
        check('訂正の前後が残る', fixed['beforeQty'] == 10 and fixed['afterQty'] == 8 and fixed['beforeAmount'] == 10000 and fixed['afterAmount'] == 8000)
        after_fix = stock.stock_query(c, {'customerId': str(aoba), 'productId': str(product), 'size': 'M', 'spec': 'none'})
        check('訂正後も数量と金額が一致', after_fix['totalQty'] == 13 and after_fix['totalAmount'] == 14000)
        row = c.execute('SELECT qty_received,qty_remaining,unit_cost FROM lots WHERE id=?', (lot['id'],)).fetchone()
        check('残数量と入荷数量がずれない', row['qty_received'] == row['qty_remaining'] == 8 and row['qty_remaining'] * row['unit_cost'] == 8000)
        stock.save_customer(c, {'id': aoba, 'name': 'あおば記念病院 新館', 'active': True})
        c.commit()
        renamed = stock.stock_query(c, {'customerId': str(aoba)})
        check('名称変更後も同じ顧客IDで在庫が残る', renamed['lots'] and all(l['customerId'] == aoba and l['customerName'] == 'あおば記念病院 新館' for l in renamed['lots']))
        c.close()
        c2 = stock.connect(path)
        check('保存後に開き直しても残る', c2.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'] == before)
        c2.close()
        order = stock.issue_sequence([
            {'id': 2, 'received_unknown': 1, 'payout_order': 2, 'received_on': None, 'unit_cost': 100},
            {'id': 3, 'received_unknown': 0, 'payout_order': 0, 'received_on': '2026-09-01', 'unit_cost': 1},
            {'id': 1, 'received_unknown': 1, 'payout_order': 1, 'received_on': None, 'unit_cost': 9000},
        ])
        check('入荷日不明を先にし、安さでは並べない', [r['id'] for r in order] == [1, 2, 3])
        names = {r['name'] for r in c2.execute('SELECT name FROM sqlite_master WHERE type="table"')} if False else None
    finally:
        try:
            c.close()
        except Exception:
            pass
        folder.cleanup()

def test_restart_and_http():
    folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    path = Path(folder.name) / 'stock.sqlite3'
    server.prepare(path, 'live')
    httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(path, 'live'))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    import urllib.request
    def post(path_url, body):
        req = urllib.request.Request(f'http://127.0.0.1:{port}{path_url}', data=json.dumps(body).encode(), method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('X-Stock-Request', '1')
        req.add_header('Origin', 'http://127.0.0.1')
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())
    status, customer = post('/api/customers', {'name': 'テスト顧客', 'active': True})
    check('顧客を保存', status == 200 and customer['id'])
    status, supplier = post('/api/suppliers', {'name': 'テスト仕入先', 'active': True})
    status, product = post('/api/products', {'code': 'SCR-100', 'name': '白スクラブ', 'color': '白', 'supplierId': supplier['id'], 'active': True, 'sizes': ['M', 'L', 'LL', '2L'], 'links': [{'customerId': customer['id'], 'embroidery': '', 'isStandard': True, 'active': True}]})
    check('複数サイズをまとめて保存', status == 200 and product['sizes'] == ['M', 'L', 'LL', '2L'])
    status, place = post('/api/locations', {'siteName': '石狩倉庫①', 'active': True})
    handle = product['links'][0]['id']
    body = {'token': 'http-once', 'customerProductId': handle, 'locationId': place['id'], 'basisDate': '2026-09-01', 'receivedUnknown': True, 'payoutOrder': 1, 'lines': [{'size': 'M', 'qty': 10, 'unitCost': 1000}, {'size': 'M', 'qty': 5, 'unitCost': 1200}, {'size': 'L', 'qty': 1, 'unitCost': 1100}]}
    status, first = post('/api/opening', body)
    status2, second = post('/api/opening', body)
    check('HTTPの二重送信を1回にする', status == 200 and first['already'] is False and second['already'] is True and first['totalQty'] == second['totalQty'] == 16)
    blank = dict(body, token='http-blank', lines=[{'size': 'M', 'qty': 1, 'unitCost': ''}])
    status, err = post('/api/opening', blank)
    check('HTTPでも空欄単価は0円にしない', status == 400 and '0円' in err['error'])
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/stock?customerId={customer["id"]}&size=M&spec=none') as r:
        view = json.loads(r.read().decode())
    check('HTTPの在庫が15着16000円', view['totalQty'] == 15 and view['totalAmount'] == 16000)
    httpd.shutdown()
    httpd.server_close()
    c = stock.connect(path)
    check('サーバー停止後もデータが残る', c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'] == 3)
    c.close()
    folder.cleanup()

def test_excel():
    import base64
    import excel_book
    folder, path, c = newdb()
    try:
        book = excel_book.workbook([
            ('顧客', ['顧客名', 'カナ', '住所', '電話番号', '担当者'], [['青葉のまち', 'アオバノマチ', '札幌市', '011-891-7700', '河口']]),
            ('仕入先', ['仕入先名', '住所', '電話番号', '担当者'], [['北光繊維', '', '', '']]),
            ('商品', ['品番', '商品名', '色', '仕入先名', 'サイズ'], [['SCR-1', 'スクラブ', '白', '北光繊維', 'M、L、LL、2L']]),
            ('取扱', ['品番', '商品名', '色', '顧客名', '加工仕様', '標準'], [['SCR-1', 'スクラブ', '白', '青葉のまち', '', '標準']]),
            ('保管場所', ['拠点名', '棚名'], [['石狩倉庫①', 'A-1']]),
            ('初期在庫', ['顧客名', '品番', '商品名', '色', '加工仕様', '拠点名', '棚名', '在庫基準日', '入荷日', '入荷日不明', '払出し順', 'サイズ', '数量', '仕入単価'],
             [['青葉のまち', 'SCR-1', 'スクラブ', '白', '', '石狩倉庫①', 'A-1', '2026-09-01', '', 'する', '1', 'LL', '3', '1000'],
              ['青葉のまち', 'SCR-1', 'スクラブ', '白', '', '石狩倉庫①', 'A-1', '2026-09-01', '', 'する', '1', '2L', '2', '1200']]),
        ])
        body = {'file': base64.b64encode(book).decode()}
        first = excel_book.apply(c, body)
        c.commit()
        check('ExcelでLLと2Lを別サイズにする', sorted(r['label'] for r in c.execute("SELECT label FROM product_sizes WHERE label IN ('LL','2L')")) == ['2L', 'LL'])
        check('Excelの初期在庫が5着', c.execute('SELECT SUM(qty_remaining) FROM lots').fetchone()[0] == 5)
        second = excel_book.apply(c, body)
        c.commit()
        check('同じExcelの初期在庫は増えない', '同じ内容1' in second['summary'] and c.execute('SELECT SUM(qty_remaining) FROM lots').fetchone()[0] == 5)
        check('同じ顧客は更新になる', '顧客 新規0・更新1' in second['summary'])
    finally:
        c.close()
        folder.cleanup()
    folder, path, c = newdb()
    try:
        book = excel_book.workbook([
            ('顧客', ['顧客名', 'カナ', '住所', '電話番号', '担当者'], [['残さない顧客', '', '', '', '']]),
            ('仕入先', ['仕入先名', '住所', '電話番号', '担当者'], [['残さない仕入先', '', '', '']]),
            ('商品', ['品番', '商品名', '色', '仕入先名', 'サイズ'], [['X', 'なし', '', '残さない仕入先', 'M']]),
            ('取扱', ['品番', '商品名', '色', '顧客名', '加工仕様', '標準'], [['X', 'なし', '', '残さない顧客', '', '']]),
            ('保管場所', ['拠点名', '棚名'], [['ない倉庫', '']]),
            ('初期在庫', ['顧客名', '品番', '商品名', '色', '加工仕様', '拠点名', '棚名', '在庫基準日', '入荷日', '入荷日不明', '払出し順', 'サイズ', '数量', '仕入単価'],
             [['残さない顧客', 'X', 'なし', '', '', 'ない倉庫', '', '2026-09-01', '2026-09-01', '', '', 'M', '1', '']]),
        ])
        try:
            excel_book.apply(c, {'file': base64.b64encode(book).decode()})
            c.commit()
            failed = False
        except ValueError as e:
            c.rollback()
            failed = '0円' in str(e)
        check('空欄の仕入単価でExcel全体を取り消す', failed and c.execute('SELECT COUNT(*) FROM customers').fetchone()[0] == 0)
    finally:
        c.close()
        folder.cleanup()

def test_locations():
    folder, path, c = newdb()
    try:
        zero = stock.save_location(c, {'siteName': '石狩倉庫', 'active': True})
        c.commit()
        check('拠点ゼロから拠点だけ登録', zero['parentId'] is None and zero['name'] == '石狩倉庫' and zero['label'] == '石狩倉庫')

        both = stock.save_location(c, {'siteName': '本社倉庫', 'shelfName': '棚1', 'active': True})
        c.commit()
        sites = list(c.execute('SELECT id,name,parent_id FROM locations WHERE name IN (\'本社倉庫\',\'棚1\') ORDER BY parent_id IS NOT NULL, id'))
        check('拠点と棚を一度に登録', both['parentId'] == sites[0]['id'] and both['label'] == '本社倉庫／棚1' and len(sites) == 2)

        under = stock.save_location(c, {'siteId': zero['id'], 'shelfName': '1F', 'active': True})
        c.commit()
        check('既存拠点の下に棚を追加', under['parentId'] == zero['id'] and under['label'] == '石狩倉庫／1F')

        wait = stock.save_location(c, {'siteId': zero['id'], 'shelfName': '償却待ち', 'active': True})
        c.commit()
        check('償却待ちを場所として登録できる', wait['name'] == '償却待ち' and wait['label'] == '石狩倉庫／償却待ち')

        other = stock.save_location(c, {'siteName': '別倉庫', 'shelfName': '棚1', 'active': True})
        c.commit()
        check('別拠点なら同じ棚名を許す', other['label'] == '別倉庫／棚1' and other['parentId'] != both['parentId'])

        try:
            stock.save_location(c, {'siteName': '石狩倉庫', 'active': True})
            check('同じ拠点名は重複しない', False)
        except ValueError as e:
            check('同じ拠点名は重複しない', '同じ拠点名' in str(e))
        c.rollback()

        try:
            stock.save_location(c, {'siteId': zero['id'], 'shelfName': '1F', 'active': True})
            check('同じ拠点の同じ棚名は重複しない', False)
        except ValueError as e:
            check('同じ拠点の同じ棚名は重複しない', '同じ棚・区画名' in str(e))
        c.rollback()

        try:
            stock.save_location(c, {'siteName': '石狩倉庫', 'shelfName': '新規棚', 'active': True})
            check('新規拠点入力で既存拠点名は拒否', False)
        except ValueError as e:
            check('新規拠点入力で既存拠点名は拒否', '一覧からその拠点を選んで' in str(e))
        c.rollback()

        before = c.execute('SELECT COUNT(*) AS n FROM locations').fetchone()['n']
        original_insert = stock._insert_shelf
        def boom(*args, **kwargs):
            raise ValueError('棚の保存に失敗しました。')
        stock._insert_shelf = boom
        try:
            c.execute('BEGIN')
            stock.save_location(c, {'siteName': '失敗倉庫', 'shelfName': '棚1', 'active': True})
            check('拠点＋棚の途中失敗を検出', False)
        except ValueError as e:
            c.rollback()
            after = c.execute('SELECT COUNT(*) AS n FROM locations').fetchone()['n']
            orphan = c.execute("SELECT id FROM locations WHERE name='失敗倉庫'").fetchone()
            check('拠点＋棚の途中失敗を検出', '棚の保存に失敗' in str(e))
            check('失敗時に拠点だけ残らない', after == before and orphan is None)
        finally:
            stock._insert_shelf = original_insert

        # 空白のトリムと、類似名の自動統合はしない
        padded = stock.save_location(c, {'siteName': '  空白倉庫  ', 'shelfName': '  棚A  ', 'active': True})
        c.commit()
        check('前後空白をトリムする', padded['label'] == '空白倉庫／棚A')
        try:
            stock.save_location(c, {'siteName': '空白倉庫', 'active': True})
            check('トリム後の同名は重複', False)
        except ValueError as e:
            check('トリム後の同名は重複', '同じ拠点名' in str(e))
        c.rollback()
        similar = stock.save_location(c, {'siteName': '空白倉庫本館', 'active': True})
        c.commit()
        check('似た名前はマージしない', similar['name'] == '空白倉庫本館')

        # 修正：拠点名と棚名を分けて扱い、親は変えない
        renamed_site = stock.save_location(c, {'id': zero['id'], 'siteName': '石狩倉庫改', 'active': True})
        c.commit()
        shelf_again = stock.location_row(c, under['id'])
        check('拠点名の修正', renamed_site['name'] == '石狩倉庫改' and shelf_again['label'] == '石狩倉庫改／1F')
        renamed_shelf = stock.save_location(c, {'id': under['id'], 'shelfName': '2F', 'active': True})
        c.commit()
        check('棚名の修正は親を変えない', renamed_shelf['parentId'] == zero['id'] and renamed_shelf['label'] == '石狩倉庫改／2F')

        # 既存在庫リンクが壊れないこと（サンプル相当の紐付け）
        cust = stock.save_customer(c, {'name': 'リンク確認', 'active': True})
        prod = stock.save_product(c, {'code': 'L-1', 'name': 'リンク商品', 'color': '', 'supplierId': None, 'active': True,
                                     'sizes': ['M'], 'links': [{'customerId': cust['id'], 'embroidery': '', 'isStandard': True, 'active': True}]})
        opening = stock.register_opening(c, {
            'token': 'loc-link', 'customerProductId': prod['links'][0]['id'], 'locationId': renamed_shelf['id'],
            'basisDate': '2026-09-01', 'receivedUnknown': True, 'payoutOrder': 1,
            'lines': [{'size': 'M', 'qty': 2, 'unitCost': 500}],
        })
        c.commit()
        view = stock.stock_query(c, {'locationId': str(renamed_shelf['id'])})
        check('在庫と場所のリンクが残る', opening['totalQty'] == 2 and view['lots'][0]['locationId'] == renamed_shelf['id']
              and view['lots'][0]['locationLabel'] == '石狩倉庫改／2F')
    finally:
        try:
            c.close()
        except Exception:
            pass
        folder.cleanup()

if __name__ == '__main__':
    test_rules()
    test_locations()
    test_excel()
    test_restart_and_http()
    print(f'合計 OK {ok} NG {ng}')
    sys.exit(1 if ng else 0)
