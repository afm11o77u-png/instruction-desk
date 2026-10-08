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
        check('商品別は商品ID見出し1件', len(product_view['groups']) == 1 and product_view['groups'][0]['productId'] == product)
        check('商品別見出しに顧客・サイズを混ぜない',
              product_view['groups'][0]['title'] == 'SCR-100　白スクラブ　白'
              and 'あおば' not in product_view['groups'][0]['title']
              and '/ M' not in product_view['groups'][0]['title'])
        m_row = product_view['groups'][0]['rows'][0]
        check('同一内訳の複数単価を集約', m_row['qty'] == 15 and m_row['amount'] == 16000 and len(product_view['groups'][0]['rows']) == 1)
        check('単価別内訳がある', sorted(p['unitCost'] for p in m_row['prices']) == [1000, 1200])
        check('在庫金額はロット単価×残数の合計', m_row['amount'] == sum(p['unitCost'] * p['qty'] for p in m_row['prices']) == 16000)
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
        opening_list = stock.opening_entries(c)
        lot_n = c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n']
        check('初期在庫一覧は既存ロットから作る', len(opening_list['entries']) == lot_n and lot_n > 0)
        check('初期在庫一覧に登録時項目がある', all(
            {'qty', 'unitCost', 'customerName', 'code', 'embroideryLabel', 'size', 'locationLabel', 'basisDate'} <= set(e)
            for e in opening_list['entries']
        ))
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

        # 複数棚を1回の保存で登録
        multi = stock.save_location(c, {
            'siteName': '多棚倉庫',
            'shelves': [{'name': '棚A'}, {'name': '棚B'}, {'name': ''}, {'name': '  棚C  '}],
            'active': True,
        })
        c.commit()
        multi_names = [s['name'] for s in multi['shelves']]
        check('複数棚を一度に登録', multi['parentId'] is None and multi['name'] == '多棚倉庫'
              and multi_names == ['棚A', '棚B', '棚C'] and multi['label'] == '多棚倉庫')
        check('空の棚行は無視する', len(multi['shelves']) == 3)

        # 既存拠点の下へ棚を追加（既存＋新規をまとめて）
        added = stock.save_location(c, {
            'siteId': multi['id'],
            'shelves': [
                {'id': multi['shelves'][0]['id'], 'name': '棚A'},
                {'id': multi['shelves'][1]['id'], 'name': '棚B'},
                {'id': multi['shelves'][2]['id'], 'name': '棚C'},
                {'name': '棚D'},
            ],
            'active': True,
        })
        c.commit()
        check('既存拠点へ棚を追加', [s['name'] for s in added['shelves']] == ['棚A', '棚B', '棚C', '棚D'])

        # 改名はID維持
        shelf_a_id = added['shelves'][0]['id']
        renamed_batch = stock.save_location(c, {
            'id': multi['id'],
            'siteName': '多棚倉庫',
            'shelves': [
                {'id': shelf_a_id, 'name': '棚A改'},
                {'id': added['shelves'][1]['id'], 'name': '棚B'},
                {'id': added['shelves'][2]['id'], 'name': '棚C'},
                {'id': added['shelves'][3]['id'], 'name': '棚D'},
            ],
            'active': True,
        })
        c.commit()
        check('複数保存での改名はID維持', renamed_batch['shelves'][0]['id'] == shelf_a_id
              and renamed_batch['shelves'][0]['name'] == '棚A改'
              and renamed_batch['shelves'][0]['label'] == '多棚倉庫／棚A改')

        # 空棚は削除できる／在庫がある棚は削除できない
        empty_id = renamed_batch['shelves'][1]['id']
        stocked_id = renamed_batch['shelves'][0]['id']
        cust2 = stock.save_customer(c, {'name': '多棚リンク', 'active': True})
        prod2 = stock.save_product(c, {'code': 'M-1', 'name': '多棚商品', 'color': '', 'supplierId': None, 'active': True,
                                      'sizes': ['S'], 'links': [{'customerId': cust2['id'], 'embroidery': '', 'isStandard': True, 'active': True}]})
        stock.register_opening(c, {
            'token': 'multi-shelf-stock', 'customerProductId': prod2['links'][0]['id'], 'locationId': stocked_id,
            'basisDate': '2026-09-01', 'receivedUnknown': True, 'payoutOrder': 1,
            'lines': [{'size': 'S', 'qty': 1, 'unitCost': 100}],
        })
        c.commit()
        check('在庫あり棚はhasStock', stock.location_row(c, stocked_id)['hasStock'] is True)
        check('空棚はhasStockなし', stock.location_row(c, empty_id)['hasStock'] is False)

        try:
            stock.save_location(c, {
                'id': multi['id'],
                'siteName': '多棚倉庫',
                'shelves': [
                    {'id': stocked_id, 'remove': True},
                    {'id': empty_id, 'name': '棚B'},
                    {'id': renamed_batch['shelves'][2]['id'], 'name': '棚C'},
                    {'id': renamed_batch['shelves'][3]['id'], 'name': '棚D'},
                ],
                'active': True,
            })
            check('在庫あり棚の削除は拒否', False)
        except ValueError as e:
            check('在庫あり棚の削除は拒否', '在庫' in str(e) and '削除できません' in str(e))
        c.rollback()

        cleared = stock.save_location(c, {
            'id': multi['id'],
            'siteName': '多棚倉庫',
            'shelves': [
                {'id': stocked_id, 'name': '棚A改'},
                {'id': empty_id, 'remove': True},
                {'id': renamed_batch['shelves'][2]['id'], 'name': '棚C'},
                {'id': renamed_batch['shelves'][3]['id'], 'name': '棚D'},
            ],
            'active': True,
        })
        c.commit()
        check('空棚は削除できる', empty_id not in [s['id'] for s in cleared['shelves']]
              and [s['name'] for s in cleared['shelves']] == ['棚A改', '棚C', '棚D'])
        check('在庫棚のIDは削除後も残る', stocked_id in [s['id'] for s in cleared['shelves']])

        # --- 拠点ごとの一覧表示（UX用ヘルパー、データ構造は維持） ---
        boot = stock.bootstrap(c)
        grouped = stock.group_locations_by_site(boot['locations'], include_stopped=False)
        check('一覧は拠点ごとに1行', all(r['parentId'] is None for r in grouped) and len(grouped) >= 1)
        multi_row = next(r for r in grouped if r['name'] == '多棚倉庫')
        check('棚は同じ行にまとめる', multi_row['shelfSummary'].startswith('棚A改') and '棚C' in multi_row['shelfSummary'])
        check('開く対象は拠点ID', multi_row['id'] == multi['id'])

        wait_site = stock.save_location(c, {'siteName': '償却待ち', 'active': True})
        c.commit()
        boot = stock.bootstrap(c)
        wait_row = next(r for r in stock.group_locations_by_site(boot['locations']) if r['name'] == '償却待ち')
        check('償却待ちは通常の拠点として棚なし', wait_row['shelfSummary'] == '棚なし' and wait_row['id'] == wait_site['id'])

        # 多数棚は「ほか○件」
        many = stock.save_location(c, {
            'siteName': '多数棚倉庫',
            'shelves': [{'name': f'棚{i}'} for i in range(1, 6)],
            'active': True,
        })
        c.commit()
        many_row = next(
            r for r in stock.group_locations_by_site(stock.bootstrap(c)['locations'])
            if r['id'] == many['id']
        )
        check('多数棚はほか○件で省略', many_row['shelfSummary'] == '棚1、棚2、棚3、ほか2件'
              and many_row['shelfExtraCount'] == 2)

        # 棚名検索は親拠点を返す
        hit = stock.group_locations_by_site(stock.bootstrap(c)['locations'], q='棚A改')
        check('棚名検索は親拠点行を返す', len(hit) == 1 and hit[0]['name'] == '多棚倉庫'
              and any(s['name'] == '棚A改' for s in hit[0]['shelves']))
        hit_wait = stock.group_locations_by_site(stock.bootstrap(c)['locations'], q='償却待ち')
        check('償却待ち名の検索も拠点を返す', any(r['name'] == '償却待ち' for r in hit_wait))

        # 停止も識別できる
        stock.save_location(c, {
            'id': multi['id'],
            'siteName': '多棚倉庫',
            'shelves': [
                {'id': stocked_id, 'name': '棚A改'},
                {'id': cleared['shelves'][1]['id'], 'name': '棚C'},
                {'id': cleared['shelves'][2]['id'], 'name': '棚D', 'remove': False},
            ],
            'active': False,
        })
        # mark one shelf stopped via direct update (site stop above affects site active)
        c.execute('UPDATE locations SET active=0 WHERE id=?', (cleared['shelves'][1]['id'],))
        c.commit()
        hidden = stock.group_locations_by_site(stock.bootstrap(c)['locations'], include_stopped=False)
        check('停止拠点は通常一覧から隠す', all(r['name'] != '多棚倉庫' for r in hidden))
        shown = stock.group_locations_by_site(stock.bootstrap(c)['locations'], include_stopped=True)
        stopped_row = next(r for r in shown if r['name'] == '多棚倉庫')
        check('停止も表示で拠点・棚を識別', stopped_row['active'] is False
              and '棚C（停止）' in stopped_row['shelfSummary'])

        # 再開してピッカー検証へ
        stock.save_location(c, {'id': multi['id'], 'siteName': '多棚倉庫', 'shelves': [
            {'id': stocked_id, 'name': '棚A改'},
            {'id': cleared['shelves'][1]['id'], 'name': '棚C'},
            {'id': cleared['shelves'][2]['id'], 'name': '棚D'},
        ], 'active': True})
        c.execute('UPDATE locations SET active=1 WHERE id=?', (cleared['shelves'][1]['id'],))
        c.commit()

        # --- 拠点→棚ピッカー ---
        locs = stock.bootstrap(c)['locations']
        site_only_opts, need = stock.opening_picker_shelves(locs, wait_site['id'])
        check('棚なし拠点は棚選択不要', site_only_opts == [] and need is False)
        check('棚なしは拠点IDが場所', stock.resolve_opening_location(wait_site['id'], None, locs) == wait_site['id'])

        opts, need_shelf = stock.opening_picker_shelves(locs, multi['id'])
        check('棚あり拠点は棚必須', need_shelf is True and all(not o.get('siteOnly') for o in opts))
        try:
            stock.resolve_opening_location(multi['id'], None, locs)
            check('棚ありで棚未選択は拒否', False)
        except ValueError as e:
            check('棚ありで棚未選択は拒否', '棚・区画を選んでください' in str(e))
        resolved = stock.resolve_opening_location(multi['id'], stocked_id, locs)
        check('拠点→棚で棚IDを解決', resolved == stocked_id)

        # 拠点直下の既存在庫は「棚指定なし」として残す
        site_stock = stock.save_location(c, {'siteName': '拠点直下倉庫', 'active': True})
        c.commit()
        cust3 = stock.save_customer(c, {'name': '直下確認', 'active': True})
        prod3 = stock.save_product(c, {
            'code': 'S-1', 'name': '直下商品', 'color': '', 'supplierId': None, 'active': True,
            'sizes': ['M'],
            'links': [{'customerId': cust3['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
        })
        opening_site = stock.register_opening(c, {
            'token': 'site-level-stock', 'customerProductId': prod3['links'][0]['id'],
            'locationId': site_stock['id'],
            'basisDate': '2026-09-01', 'receivedUnknown': True, 'payoutOrder': 1,
            'lines': [{'size': 'M', 'qty': 3, 'unitCost': 200}],
        })
        c.commit()
        # あとから棚を追加しても、既存の拠点直下参照は残る
        with_shelf = stock.save_location(c, {
            'id': site_stock['id'],
            'siteName': '拠点直下倉庫',
            'shelves': [{'name': '後付け棚'}],
            'active': True,
        })
        c.commit()
        locs2 = stock.bootstrap(c)['locations']
        preserve_opts, preserve_need = stock.opening_picker_shelves(
            locs2, site_stock['id'], current_location_id=site_stock['id']
        )
        check('棚追加後も棚指定なしを出せる', preserve_need is True
              and preserve_opts[0]['name'] == stock.SITE_ONLY_SHELF_LABEL
              and preserve_opts[0]['id'] == site_stock['id'])
        kept = stock.resolve_opening_location(site_stock['id'], site_stock['id'], locs2)
        check('棚指定なしは拠点IDのまま', kept == site_stock['id'])
        view_site = stock.stock_query(c, {'locationId': str(site_stock['id'])})
        check('拠点直下在庫のリンクは壊れない', opening_site['totalQty'] == 3
              and view_site['lots'][0]['locationId'] == site_stock['id']
              and view_site['lots'][0]['locationLabel'] == '拠点直下倉庫'
              and with_shelf['shelves'][0]['name'] == '後付け棚')

        # 複数棚の「開く」＝拠点IDで一括編集できること（#28経路）
        reopened = stock.save_location(c, {
            'id': multi['id'],
            'siteName': '多棚倉庫',
            'shelves': [
                {'id': stocked_id, 'name': '棚A改'},
                {'id': cleared['shelves'][1]['id'], 'name': '棚C'},
                {'id': cleared['shelves'][2]['id'], 'name': '棚D'},
                {'name': '開く追加'},
            ],
            'active': True,
        })
        c.commit()
        check('開く経由の複数棚編集', [s['name'] for s in reopened['shelves']] == ['棚A改', '棚C', '棚D', '開く追加'])
        check('開く後も在庫リンク維持', stock.location_row(c, stocked_id)['hasStock'] is True
              and stock.stock_query(c, {'locationId': str(stocked_id)})['lots'][0]['locationId'] == stocked_id)

        # 新規登録は既存拠点を upsert しない（棚なし含む）
        bare = stock.save_location(c, {'siteName': '棚なし倉庫', 'shelves': [], 'active': True})
        c.commit()
        check('棚なしで拠点だけ登録', bare['parentId'] is None and bare['name'] == '棚なし倉庫'
              and bare['shelves'] == [])
        bare_id = bare['id']
        before_name = c.execute('SELECT name,updated_at FROM locations WHERE id=?', (bare_id,)).fetchone()
        try:
            stock.save_location(c, {'siteName': '棚なし倉庫', 'shelves': [{'name': '誤追加'}], 'active': True})
            check('新規で既存拠点名は拒否（ID不変）', False)
        except ValueError as e:
            c.rollback()
            after = c.execute('SELECT id,name,updated_at FROM locations WHERE id=?', (bare_id,)).fetchone()
            orphan = c.execute("SELECT id FROM locations WHERE parent_id=? AND name='誤追加'", (bare_id,)).fetchone()
            check('新規で既存拠点名は拒否（ID不変）', '同じ拠点名' in str(e)
                  and after['id'] == bare_id
                  and after['name'] == before_name['name']
                  and after['updated_at'] == before_name['updated_at']
                  and orphan is None)
        # 使用停止・再開はID維持
        stopped = stock.save_location(c, {'id': bare_id, 'siteName': '棚なし倉庫', 'shelves': [], 'active': False})
        c.commit()
        resumed = stock.save_location(c, {'id': bare_id, 'siteName': '棚なし倉庫', 'shelves': [], 'active': True})
        c.commit()
        check('使用停止・再開でID維持', stopped['id'] == bare_id and resumed['id'] == bare_id
              and stopped['active'] is False and resumed['active'] is True)
    finally:
        try:
            c.close()
        except Exception:
            pass
        folder.cleanup()

def test_product_grouped_stock():
    """商品別: 商品ID単位の見出し＋顧客/仕様/サイズ/場所の内訳。顧客別・場所別は変えない。"""
    folder, path, c = newdb()
    try:
        sample_data.seed(c)
        c.commit()
        aoba = c.execute("SELECT id FROM customers WHERE name='あおば記念病院'").fetchone()['id']
        products = list(c.execute("SELECT id,code,supplier_id FROM products WHERE code='SCR-100' ORDER BY id"))
        check('同じ品番で商品IDが2つ', len(products) == 2 and products[0]['id'] != products[1]['id'])

        # フィルタ前後で totalQty / totalAmount はロット合計と一致（表示集計の変更で壊れないこと）
        filters = [
            {},
            {'customerId': str(aoba)},
            {'productId': str(products[0]['id'])},
            {'size': 'M', 'spec': 'none'},
            {'customerId': str(aoba), 'productId': str(products[0]['id'])},
        ]
        for f in filters:
            base = stock.stock_query(c, dict(f, group='lot'))
            product_view = stock.stock_query(c, dict(f, group='product'))
            lot_qty = sum(l['qty'] for l in base['lots'])
            lot_amount = sum(l['amount'] for l in base['lots'])
            group_qty = sum(g['qty'] for g in product_view['groups'])
            group_amount = sum(g['amount'] for g in product_view['groups'])
            row_qty = sum(r['qty'] for g in product_view['groups'] for r in g['rows'])
            row_amount = sum(r['amount'] for g in product_view['groups'] for r in g['rows'])
            price_amount = sum(
                p['unitCost'] * p['qty']
                for g in product_view['groups'] for r in g['rows'] for p in r['prices']
            )
            label = f"filter={f or 'all'}"
            check(f'商品別合計がロットと一致 {label}',
                  product_view['totalQty'] == base['totalQty'] == lot_qty == group_qty == row_qty
                  and product_view['totalAmount'] == base['totalAmount'] == lot_amount == group_amount == row_amount == price_amount)

        # 商品IDごとに1見出し。同じ品番でもマージしない
        all_product = stock.stock_query(c, {'group': 'product'})
        ids = [g['productId'] for g in all_product['groups']]
        check('商品別は商品IDごとに1見出し', len(ids) == len(set(ids)) and set(ids) == {p['id'] for p in products})
        check('同じ品番でも商品IDは分けたまま', all(g['code'] == 'SCR-100' for g in all_product['groups']))

        # 北光繊維側: 顧客・サイズ・場所が内訳行。見出しに顧客名やサイズを連結しない
        hokko = next(g for g in all_product['groups'] if g['productId'] == products[0]['id'])
        check('見出しは品番・商品名・色のみ', hokko['title'] == 'SCR-100　白スクラブ　白')
        check('見出しに顧客・加工仕様・サイズを連結しない',
              all(token not in hokko['title'] for token in ('あおば', 'かしわ', '左胸', '／', ' M', ' LL')))
        row_keys = [(r['customerName'], r['embroideryLabel'], r['size'], r['locationLabel']) for r in hokko['rows']]
        check('内訳は顧客・仕様・サイズ・場所の組み合わせ', len(row_keys) == len(set(row_keys)) and len(row_keys) >= 3)

        # 顧客名順、同一顧客内は登録サイズ順（M → L → LL → 2L）
        aoba_sizes = [r['size'] for r in hokko['rows'] if r['customerName'] == 'あおば記念病院' and r['embroideryLabel'] == '刺繍なし']
        check('同一顧客のサイズは登録順', aoba_sizes == ['M', 'L', 'LL'])
        check('内訳は顧客名順', [r['customerName'] for r in hokko['rows']] == sorted(
            [r['customerName'] for r in hokko['rows']]
        ))

        # Mサイズ・刺繍なし: 1000×10 + 1200×5 が1行に集約、単価内訳は残す
        m_plain = next(
            r for r in hokko['rows']
            if r['customerName'] == 'あおば記念病院' and r['size'] == 'M' and r['embroideryLabel'] == '刺繍なし'
        )
        check('複数単価ロットを数量・金額集約', m_plain['qty'] == 15 and m_plain['amount'] == 16000)
        check('単価の内訳を保持', [(p['unitCost'], p['qty']) for p in m_plain['prices']] == [(1000, 10), (1200, 5)])
        check('最新単価だけで金額を置き換えない', m_plain['amount'] != 1200 * 15 and m_plain['amount'] == 1000 * 10 + 1200 * 5)

        # 顧客別・場所別の parts 構造は維持
        customer_view = stock.stock_query(c, {'group': 'customer', 'customerId': str(aoba)})
        location_view = stock.stock_query(c, {'group': 'location'})
        check('顧客別は従来のparts構造', customer_view['groups'] and 'parts' in customer_view['groups'][0] and 'rows' not in customer_view['groups'][0])
        check('場所別は従来のparts構造', location_view['groups'] and 'parts' in location_view['groups'][0] and 'rows' not in location_view['groups'][0])
        check('顧客別・場所別の合計もロット一致',
              customer_view['totalQty'] == sum(g['qty'] for g in customer_view['groups'])
              and location_view['totalAmount'] == sum(g['amount'] for g in location_view['groups']))

        # UI: 商品別レンダラがあり、旧の長い見出し連結を商品別で使っていない
        app_js = Path(__file__).resolve().parents[1] / 'static' / 'app.js'
        text = app_js.read_text(encoding='utf-8')
        check('商品別リスト描画がある', 'renderProductStockList' in text and 'product-stock-table' in text)
        check('単価の内訳を展開できる', '単価の内訳' in text and 'price-breakdown' in text)
        check('商品別はrowsを使う', 'g.rows' in text and "f.group==='product'" in text)
    finally:
        try:
            c.close()
        except Exception:
            pass
        folder.cleanup()


if __name__ == '__main__':
    test_rules()
    test_product_grouped_stock()
    test_locations()
    test_excel()
    test_restart_and_http()
    print(f'合計 OK {ok} NG {ng}')
    sys.exit(1 if ng else 0)
