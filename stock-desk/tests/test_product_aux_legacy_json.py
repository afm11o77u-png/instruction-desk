"""商品登録の判定補助を、初期在庫CSVと旧在庫JSONで同じ規則にする。合成データのみ。"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def b64_text(text):
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def b64_json(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode('utf-8')).decode('ascii')


def ledger(rows):
    return '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(rows)


def opening_csv(rows):
    header = (
        '新古,仕入先,顧客,商品名,商品コード,サイズ,仕入単価,'
        '購入数量,購入金額,出荷数,出荷金額,在庫,在庫金額,営業所コード,拠点,棚,補正在庫数'
    )
    return header + '\n' + '\n'.join(rows)


def opening_line(supplier, customer, name, code, size, qty):
    return f'新品,{supplier},{customer},{name},{code},{size},100,9,900,8,800,{qty},0,1,本社,A1,0'


CUST = '㈱合田観光商事　ひまわり'
NAME = 'ベーシックカラーベスト　046.蛍光グリーン'
LEDGER = ledger([
    f'{CUST},㈱ユニーク,00008,{NAME},F',
    f'{CUST},㈱ユニーク,00008,{NAME},XL',
    f'{CUST},㈱ユニーク,00008,{NAME},2XL',
    f'{CUST},アイトス㈱,00008,駐車場ベスト（緑),F',
    f'{CUST},アイトス㈱,00008,駐車場ベスト（緑),2L',
])
OPENING = opening_csv([
    opening_line('㈱ユニーク', CUST, NAME, '00008', 'F', '0'),
    opening_line('㈱ユニーク', CUST, NAME, '00008', 'XL', '0'),
    opening_line('㈱ユニーク', CUST, NAME, '00008', '2XL', '2'),
])


def stock_row(sid, size, qty, supplier='㈱ユニーク', name=NAME, code='00008', customer_id='c1'):
    return {
        'id': sid, 'customer_id': customer_id, 'maker_id': 'm1', 'maker_name': supplier,
        'item_id': 'item', 'item_num': code, 'name': name, 'item_size': size,
        'is_used': False, 'stock_count': qty, 'calc_stock_count': qty, 'price': 100,
        'factory_name': '本社', 'shelf_name': 'A1',
    }


def customers():
    return {'result': True, 'data': [{'id': 'c1', 'name': CUST, 'phone': '使わない'}]}


def decision(preview, code='00008'):
    group = next(g for g in preview['skuGroups'] if g['code'] == code)
    aux = group.get('auxPrimary') or {}
    plan = group.get('productPlan') or {}
    roles = tuple(sorted(
        (j.get('supplier'), j.get('plannedRole'), j.get('auxStatus'), j.get('auxStockTotal'))
        for j in (aux.get('supplierJudgments') or [])
    ))
    return {
        'needsDecision': group.get('needsDecision'),
        'supplier': aux.get('autoPrimarySupplier') or group.get('plannedPrimarySupplier'),
        'name': plan.get('plannedRegisteredName') or plan.get('productName'),
        'color': plan.get('plannedRegisteredColor') or plan.get('color'),
        'auto': bool(group.get('auxStockPickAuto')),
        'roles': roles,
    }


class ProductAuxJsonTest(unittest.TestCase):
    def test_json_matches_csv_judgment_and_writes_no_stock(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(c.close)
        csv_body = {
            'importMode': 'products',
            'file': b64_text(LEDGER), 'name': 'ledger.csv',
            'auxFile': b64_text(OPENING), 'auxName': 'opening.csv',
            'registerOpeningStock': False,
            '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
        }
        json_body = {
            'importMode': 'products',
            'file': b64_text(LEDGER), 'name': 'ledger.csv',
            'legacyStockFile': b64_json({'result': True, 'data': [
                stock_row('s1', 'F', 0),
                stock_row('s2', 'XL', 0),
                stock_row('s3', '2XL', 2),
            ]}),
            'legacyStockName': 'old-stock.json',
            'legacyCustomerFile': b64_json(customers()),
            'legacyCustomerName': 'old-customers.json',
            'registerOpeningStock': False,
            '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
        }
        csv_preview = pli.build_preview(c, csv_body)
        json_preview = pli.build_preview(c, json_body)
        self.assertEqual(decision(csv_preview), decision(json_preview))
        self.assertTrue(json_preview['auxOpeningStock']['fromLegacyJson'])
        self.assertTrue(json_preview['auxOpeningStock']['judgmentOnly'])
        self.assertIn('同じ在庫を初期在庫CSVでは登録しません', json_preview['auxOpeningStock']['note'])
        self.assertEqual(pli.stock_snapshot(c)['lots'], 0)
        result = pli.commit_preview(c, {
            **json_body,
            'previewFingerprint': json_preview['previewFingerprint'],
            'previewAt': json_preview['previewAt'],
            'token': 'aux-json-1',
        }, db_path=str(path))
        c.commit()
        self.assertTrue(result['stockUnchanged'])
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
        prod = c.execute("SELECT name, color, supplier_id FROM products WHERE code='00008'").fetchone()
        self.assertEqual(prod['name'], 'ベーシックカラーベスト')
        self.assertEqual(prod['color'], '蛍光グリーン')
        self.assertEqual(
            c.execute('SELECT name FROM suppliers WHERE id=?', (prod['supplier_id'],)).fetchone()['name'],
            '㈱ユニーク',
        )

    def test_customer_name_filter_needs_customer_json(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(c.close)
        other = opening_csv([
            opening_line('アイトス㈱', '別人', '駐車場ベスト（緑)', '00008', 'F', '5'),
        ])
        base = {
            'importMode': 'products',
            'file': b64_text(LEDGER), 'name': 'ledger.csv',
            '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
        }
        csv_preview = pli.build_preview(c, {**base, 'auxFile': b64_text(other)})
        named = pli.build_preview(c, {
            **base,
            'legacyStockFile': b64_json({'result': True, 'data': [
                stock_row('s9', 'F', 5, supplier='アイトス㈱', name='駐車場ベスト（緑)', customer_id='c9'),
            ]}),
            'legacyCustomerFile': b64_json({'result': True, 'data': [{'id': 'c9', 'name': '別人'}]}),
        })
        loose = pli.build_preview(c, {
            **base,
            'legacyStockFile': b64_json({'result': True, 'data': [
                stock_row('s9', 'F', 5, supplier='アイトス㈱', name='駐車場ベスト（緑)', customer_id='c9'),
            ]}),
        })
        self.assertEqual(decision(csv_preview)['supplier'], decision(named)['supplier'])
        self.assertNotEqual(decision(named)['supplier'], decision(loose)['supplier'])
        self.assertEqual(decision(loose)['supplier'], 'アイトス㈱')

    def test_upper_lower_code_matches_like_csv(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(c.close)
        text = ledger([
            '顧客A,仕入A,VEH30B 下,作業ズボン,M',
            '顧客A,仕入B,VEH30B 下,別物ズボン,M',
        ])
        csv_body = {
            'importMode': 'products',
            'file': b64_text(text),
            'auxFile': b64_text(opening_csv([
                opening_line('仕入A', '顧客A', '作業ズボン', 'VEH30B下', 'M', '4'),
            ])),
            '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
        }
        json_body = {
            'importMode': 'products',
            'file': b64_text(text),
            'legacyStockFile': b64_json({'result': True, 'data': [
                stock_row('u1', 'M', 4, supplier='仕入A', name='作業ズボン', code='VEH30B下', customer_id='a'),
            ]}),
            'legacyCustomerFile': b64_json({'result': True, 'data': [{'id': 'a', 'name': '顧客A'}]}),
            '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
        }
        self.assertEqual(
            decision(pli.build_preview(c, csv_body), 'VEH30B下'),
            decision(pli.build_preview(c, json_body), 'VEH30B下'),
        )

    def test_csv_and_json_together_are_rejected(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(c.close)
        with self.assertRaises(ValueError) as caught:
            pli.build_preview(c, {
                'importMode': 'products',
                'file': b64_text(LEDGER),
                'auxFile': b64_text(OPENING),
                'legacyStockFile': b64_json({'result': True, 'data': [stock_row('s1', 'F', 1)]}),
                '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
            })
        self.assertIn('同時には使えません', str(caught.exception))
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)


if __name__ == '__main__':
    unittest.main()
