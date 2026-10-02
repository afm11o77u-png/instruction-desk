"""架空の試用データ。本番用の空データベースには入れない。"""
import stock


def seed(c):
    aoba = stock.save_customer(c, {'name': 'あおば記念病院', 'kana': 'アオバキネンビョウイン', 'address': '札幌市西区架空1-1', 'phone': '011-000-0001', 'contact': '佐藤', 'active': True})
    kashiwa = stock.save_customer(c, {'name': 'かしわ食品 本店', 'kana': 'カシワショクホンテン', 'address': '札幌市北区架空2-2', 'phone': '011-000-0002', 'contact': '高橋', 'active': True})
    hokko = stock.save_supplier(c, {'name': '北光繊維', 'address': '石狩市架空3-3', 'phone': '0133-00-0001', 'contact': '伊藤', 'active': True})
    maruzen = stock.save_supplier(c, {'name': '丸善被服', 'address': '小樽市架空4-4', 'phone': '0134-00-0002', 'contact': '渡辺', 'active': True})
    first = stock.save_product(c, {
        'code': 'SCR-100', 'name': '白スクラブ', 'color': '白', 'supplierId': hokko['id'], 'active': True,
        'sizes': ['M', 'L', 'LL', '2L'],
        'links': [
            {'customerId': aoba['id'], 'embroidery': '', 'isStandard': True, 'active': True},
            {'customerId': aoba['id'], 'embroidery': '左胸ネーム', 'isStandard': False, 'active': True},
            {'customerId': kashiwa['id'], 'embroidery': '背中社名', 'isStandard': True, 'active': True},
        ],
    })
    second = stock.save_product(c, {
        'code': 'SCR-100', 'name': '白スクラブ', 'color': '白', 'supplierId': maruzen['id'], 'active': True,
        'sizes': ['M'],
        'links': [{'customerId': aoba['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    site1 = stock.save_location(c, {'name': '石狩倉庫①', 'active': True})
    site2 = stock.save_location(c, {'name': '石狩倉庫②', 'active': True})
    shelf = stock.save_location(c, {'name': '1-A', 'parentId': site1['id'], 'active': True})
    plain = next(x['id'] for x in first['links'] if x['customerId'] == aoba['id'] and x['embroidery'] == '')
    named = next(x['id'] for x in first['links'] if x['embroidery'] == '左胸ネーム')
    other = next(x['id'] for x in first['links'] if x['customerId'] == kashiwa['id'])
    other_maker = second['links'][0]['id']
    stock.register_opening(c, {
        'token': 'sample-opening-price-split', 'customerProductId': plain, 'locationId': site1['id'],
        'basisDate': '2026-09-01', 'receivedUnknown': True, 'payoutOrder': 1,
        'lines': [{'size': 'M', 'qty': 10, 'unitCost': 1000}, {'size': 'M', 'qty': 5, 'unitCost': 1200}, {'size': 'L', 'qty': 4, 'unitCost': 1100}],
    })
    stock.register_opening(c, {
        'token': 'sample-opening-embroidery', 'customerProductId': named, 'locationId': site1['id'],
        'basisDate': '2026-09-01', 'receivedUnknown': False, 'receivedOn': '2026-08-01',
        'lines': [{'size': 'M', 'qty': 2, 'unitCost': 1300}],
    })
    stock.register_opening(c, {
        'token': 'sample-opening-other-customer', 'customerProductId': other, 'locationId': site2['id'],
        'basisDate': '2026-09-01', 'receivedUnknown': False, 'receivedOn': '2026-07-15',
        'lines': [{'size': 'M', 'qty': 3, 'unitCost': 1000}],
    })
    stock.register_opening(c, {
        'token': 'sample-opening-other-supplier', 'customerProductId': other_maker, 'locationId': site1['id'],
        'basisDate': '2026-09-01', 'receivedUnknown': False, 'receivedOn': '2026-06-01',
        'lines': [{'size': 'M', 'qty': 1, 'unitCost': 900}],
    })
    stock.register_opening(c, {
        'token': 'sample-opening-shelf-ll', 'customerProductId': plain, 'locationId': shelf['id'],
        'basisDate': '2026-09-01', 'receivedUnknown': False, 'receivedOn': '2026-03-01',
        'lines': [{'size': 'LL', 'qty': 1, 'unitCost': 1500}],
    })
