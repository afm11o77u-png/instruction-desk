"""3M9910 / 反射テープは取り込み対象外。保存済みカテゴリは変えない。合成データのみ。"""
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
import qr_link_import  # noqa: E402
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
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode('ascii')


def ledger(rows):
    return '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(rows)


def tape_row(name='反射テープ 10㎜', code='3M9910', qty=2):
    return {
        'id': 'tape-1', 'customer_id': 'c1', 'maker_id': 'm1', 'maker_name': '仕入A',
        'item_num': code, 'name': name, 'item_size': 'F', 'is_used': False,
        'stock_count': qty, 'calc_stock_count': qty, 'price': 100,
        'factory_name': '未確認', 'shelf_name': '',
    }


class ReflectiveTapeExcludeTest(unittest.TestCase):
    def test_name_and_code_must_both_match(self):
        self.assertTrue(qr_link_import.is_reflective_tape_row('3M9910', '反射テープ10㎜'))
        self.assertTrue(qr_link_import.is_reflective_tape_row('３Ｍ９９１０', '反射テープ 10mm'))
        self.assertTrue(qr_link_import.is_reflective_tape_row('3M9910', '＊反射テープ'))
        self.assertFalse(qr_link_import.is_reflective_tape_row('3M9910', '反射ベスト'))
        self.assertFalse(qr_link_import.is_reflective_tape_row('3M9910', '反射加工'))
        self.assertFalse(qr_link_import.is_reflective_tape_row('3M9910', '白衣'))
        self.assertFalse(qr_link_import.is_reflective_tape_row('TAPE-2', '反射テープ10㎜'))
        self.assertFalse(qr_link_import.is_reflective_tape_row('', '裾上げテープ'))
        self.assertTrue(qr_link_import._is_hem_tape_name('裾上げテープ'))

    def test_product_import_excludes_tape_and_keeps_uniform(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(c.close)
        text = ledger([
            '顧客A,仕入A,3M9910,反射テープ 10㎜,F',
            '顧客A,仕入A,P001,パンツ,M',
        ])
        body = {
            'importMode': 'products',
            'file': b64_text(text), 'name': 'ledger.csv',
            'registerOpeningStock': False,
            '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
        }
        preview = pli.build_preview(c, body)
        by_name = {r['csv']['productName']: r for r in preview['rows']}
        self.assertEqual(by_name['反射テープ 10㎜']['status'], pli.STATUS_EXCLUDED)
        self.assertIn('反射テープ', ' '.join(by_name['反射テープ 10㎜']['reasons']))
        self.assertIn('対象外', by_name['反射テープ 10㎜']['statusLabel'])
        self.assertNotEqual(by_name['パンツ']['status'], pli.STATUS_EXCLUDED)
        result = pli.commit_preview(c, {
            **body,
            'previewFingerprint': preview['previewFingerprint'],
            'previewAt': preview['previewAt'],
            'token': 'tape-exclude-1',
        }, db_path=str(path))
        c.commit()
        self.assertTrue(result['stockUnchanged'])
        names = [r['name'] for r in c.execute('SELECT name, category FROM products')]
        self.assertNotIn('反射テープ 10㎜', names)
        self.assertTrue(any('パンツ' in n for n in names))
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)

    def test_saved_uniform_category_is_not_rewritten(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(c.close)
        cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
        su = stock.save_supplier(c, {'name': '仕入A', 'active': True})
        stock.save_product(c, {
            'code': '3M9910', 'name': '反射テープ 10㎜', 'color': '', 'category': '制服',
            'supplierId': su['id'], 'active': True, 'sizes': ['F'],
            'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
        })
        c.commit()
        before = c.execute("SELECT category, display_no FROM products WHERE code='3M9910'").fetchone()
        body = {
            'importMode': 'opening_stock',
            'legacyStockFile': b64_json({'result': True, 'data': [tape_row()]}),
            'legacyStockName': 'old-stock.json',
            'legacyCustomerLinks': {},
            'basisDate': '2026-01-01',
            'defaultSiteName': '未確認',
            '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
        }
        preview = pli.build_preview(c, body)
        plan = preview['openingStock']['plans'][0]
        self.assertEqual(plan['status'], 'excluded')
        self.assertIn('反射テープ', ' '.join(plan['reasons']))
        self.assertEqual(plan.get('qrAction'), 'skip')
        opening = preview['openingStock']
        pli.commit_preview(c, {
            **body,
            'previewFingerprint': preview['previewFingerprint'],
            'previewAt': preview['previewAt'],
            'legacySourceFingerprint': opening.get('sourceFingerprint') or '',
            'token': 'tape-open-1',
        }, db_path=str(path))
        c.commit()
        after = c.execute("SELECT category, display_no FROM products WHERE code='3M9910'").fetchone()
        self.assertEqual(after['category'], '制服')
        self.assertEqual(after['display_no'], before['display_no'])
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)

    def test_other_code_with_same_name_is_not_excluded(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        self.addCleanup(c.close)
        text = ledger(['顧客A,仕入A,TAPE-2,反射テープ 10㎜,F'])
        preview = pli.build_preview(c, {
            'importMode': 'products',
            'file': b64_text(text), 'name': 'ledger.csv',
            '_canWrite': True, '_actor': {'name': 't', 'role': 'owner'},
        })
        self.assertNotEqual(preview['rows'][0]['status'], pli.STATUS_EXCLUDED)


if __name__ == '__main__':
    unittest.main()
