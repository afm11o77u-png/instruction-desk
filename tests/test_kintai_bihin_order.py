"""備品棚卸の発注メール準備: 検証・入数換算・購入加算・冪等・取消・テンプレ非上書き。"""
from __future__ import annotations

import inspect
import re
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest import mock
from zipfile import ZipFile

import kintai
import kintai_bihin
import kintai_bihin_order
import kintai_shift

ROOT = Path(__file__).resolve().parents[1]


class BihinOrderHelpersTest(unittest.TestCase):
    def test_inventory_from_order_uses_packing(self):
        self.assertEqual(
            kintai_bihin_order.inventory_qty_from_order(Decimal('2'), Decimal('10')),
            Decimal('20'),
        )

    def test_packing_required_no_guess(self):
        with self.assertRaises(ValueError) as ctx:
            kintai_bihin_order._parse_packing('', required=True)
        self.assertIn('入数', str(ctx.exception))
        with self.assertRaises(ValueError):
            kintai_bihin_order._parse_packing(0, required=True)

    def test_fixture_template_resolves(self):
        path, src = kintai_bihin_order.resolve_template_path()
        self.assertTrue(path.is_file())
        self.assertIn(src, ('repo-fixture', 'desktop', 'env:BIHIN_ORDER_TEMPLATE'))
        self.assertGreater(path.stat().st_size, 10000)
        kintai_bihin_order.validate_order_template(path)

    def test_normalize_newlines_literal_and_real(self):
        self.assertEqual(kintai_bihin_order._normalize_newlines('a\\n\\nb'), 'a\n\nb')
        self.assertEqual(kintai_bihin_order._normalize_newlines('a\n\nb'), 'a\n\nb')
        self.assertEqual(kintai_bihin_order._normalize_newlines('a\\nb\nc'), 'a\nb\nc')

    def test_compose_uses_html_br_not_literal_backslash_n(self):
        opened = []

        def fake_popen(args, **kwargs):
            opened.append(args)
            return mock.Mock()

        kintai_bihin_order.open_compose(
            to='vendor@example.com',
            subject='件名',
            body='行1\n\n行2',
            attachment=None,
            popen=fake_popen,
            tb_path='/usr/bin/true',
        )
        compose = opened[0][2]
        self.assertIn("format='1'", compose)
        self.assertIn('行1<br><br>行2', compose)
        self.assertNotIn('\\\\n', compose)
        # two-char backslash-n must not appear as body text
        self.assertNotRegex(compose, r"body='[^']*\\n")

    def test_fill_does_not_overwrite_template_and_preserves_parts(self):
        path, _ = kintai_bihin_order.resolve_template_path()
        before = path.read_bytes()
        blob = kintai_bihin_order.fill_order_workbook(
            path,
            order_date='2026-10-29',
            vendor_name='テスト商事',
            lines=[{
                'orderProductName': 'コピー用紙A4',
                'orderQty': 3,
                'orderUnit': '箱',
                'note': '至急',
            }],
        )
        after = path.read_bytes()
        self.assertEqual(before, after)
        self.assertNotEqual(blob, before)
        self.assertGreater(len(blob), 10000)
        with ZipFile(BytesIO(blob)) as z:
            names = z.namelist()
            self.assertTrue(any('drawings/' in n for n in names))
            self.assertTrue(any('printerSettings/' in n for n in names))
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertIn('mergeCell', sheet)
            self.assertIn('pageSetup', sheet)
            self.assertIn('SUM(G19:G31)', sheet)
            self.assertNotIn('TODAY()', sheet)
            self.assertIn('コピー用紙A4', sheet)
            self.assertRegex(sheet, r'<c r="D19"[^>]*>\s*<v>3</v>')
            self.assertRegex(sheet, r'<c r="H3"[^>]*t="inlineStr"')
            calc = z.read('xl/calcChain.xml').decode('utf-8')
            self.assertNotIn('H3', calc)
            self.assertIn('G32', calc)
            rels = z.read('xl/worksheets/_rels/sheet1.xml.rels').decode('utf-8')
            self.assertIn('drawing', rels)
            self.assertIn('printerSettings', rels)

    def test_fill_rejects_too_many_lines_without_fallback(self):
        path, _ = kintai_bihin_order.resolve_template_path()
        lines = [
            {'orderProductName': f'品{i}', 'orderQty': 1, 'orderUnit': '箱'}
            for i in range(kintai_bihin_order.ORDER_SHEET_MAX_LINES + 1)
        ]
        with self.assertRaises(ValueError) as ctx:
            kintai_bihin_order.fill_order_workbook(
                path, order_date='2026-10-29', lines=lines,
            )
        self.assertIn('上限', str(ctx.exception))


class BihinOrderStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 't.sqlite3'
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, "
            "version INTEGER, mark_kintai INTEGER DEFAULT 0)"
        )
        kintai.bind(Path(self.tmp.name))
        kintai.schema(self.c)
        self.c.execute('DELETE FROM kintai_people')
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,1,0)',
            ('001', 'regular', '山田太郎', ''),
        )
        self.c.commit()
        self.user = {'id': 1, 'name': '主', 'login': 'o@ex.com', 'role': 'owner', 'mark_kintai': 1}
        # isolate mail-out into tmp
        self._old_data = None

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _vendor(self, name='文具店', email='vendor@example.com', mode='excel', **extra):
        return kintai_bihin_order.save_vendor(self.c, self.user, {
            'name': name, 'email': email, 'mode': mode, **extra,
        })

    def _item(self, name='コピー用紙', price=500, unit='枚', **order):
        return kintai_bihin.save_item(self.c, self.user, {
            'category': '消耗', 'name': name, 'unit': unit, 'unitPrice': price, **order,
        })

    def test_validate_missing_vendor_email_qty(self):
        it = self._item('ペン', 100, orderUnit='本', packingQty=1)
        with self.assertRaises(ValueError) as ctx:
            kintai_bihin_order.prepare_orders(
                self.c, self.user,
                {'prepKey': 'k1', 'lines': [{'itemId': it['id'], 'orderQty': 2}]},
                open_mail=False, skip_mail=True,
            )
        self.assertIn('仕入先', str(ctx.exception))

        v = self._vendor(email='')
        it2 = self._item('ノート', 200, vendorId=v['id'], orderUnit='冊', packingQty=1)
        with self.assertRaises(ValueError) as ctx2:
            kintai_bihin_order.prepare_orders(
                self.c, self.user,
                {'prepKey': 'k2', 'lines': [{'itemId': it2['id'], 'orderQty': 1}]},
                open_mail=False, skip_mail=True,
            )
        self.assertIn('メール', str(ctx2.exception))

        v2 = self._vendor(name='別店', email='ok@example.com')
        it3 = self._item('消しゴム', 50, vendorId=v2['id'], orderUnit='個', packingQty=1)
        with self.assertRaises(ValueError):
            kintai_bihin_order.prepare_orders(
                self.c, self.user,
                {'prepKey': 'k3', 'lines': [{'itemId': it3['id'], 'orderQty': 0}]},
                open_mail=False, skip_mail=True,
            )
        with self.assertRaises(ValueError):
            kintai_bihin_order.prepare_orders(
                self.c, self.user,
                {'prepKey': 'k4', 'lines': [{'itemId': it3['id'], 'orderQty': -1}]},
                open_mail=False, skip_mail=True,
            )

    def test_fail_prep_does_not_add_purchase(self):
        v = self._vendor()
        it = self._item('テープ', 80, vendorId=v['id'], orderProductName='養生テープ', orderUnit='巻', packingQty=1)
        # force mail open failure
        with self.assertRaises(ValueError):
            kintai_bihin_order.prepare_orders(
                self.c, self.user,
                {'prepKey': 'fail1', 'orderDate': '2026-10-15',
                 'lines': [{'itemId': it['id'], 'orderQty': 4}]},
                open_mail=True, skip_mail=False,
                popen=mock.Mock(side_effect=OSError('no tb')),
                tb_path='/nonexistent/thunderbird',
            )
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        # unsaved or purchase still empty
        if sheet['rows']:
            self.assertTrue(
                sheet['rows'][0]['purchaseQty'] in (None, 0)
                or sheet['rows'][0].get('purchaseQty') is None
            )
        hist = kintai_bihin_order.list_order_history(self.c)
        self.assertEqual(hist, [])

    def test_success_adds_purchase_with_packing_not_overwrite(self):
        v = self._vendor(mode='body')
        it = self._item(
            'ラベル', 120, unit='枚',
            vendorId=v['id'], orderProductName='HIQラベル', orderUnit='箱', packingQty=100,
        )
        # pre-save month with existing purchase 5
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        row = dict(sheet['rows'][0])
        row.update({'openingQty': 1, 'purchaseQty': 5, 'closingQty': 2})
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 0, 'rows': [row],
        })
        result = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'ok1', 'orderDate': '2026-10-20',
             'lines': [{'itemId': it['id'], 'orderQty': 2}]},
            open_mail=True, skip_mail=True,
        )
        self.assertFalse(result['idempotent'])
        self.assertEqual(len(result['orders']), 1)
        self.assertIn('送信', result['message'])  # 送信はご自身で…の文言
        self.assertNotIn('送信済み', result['message'])
        again = kintai_bihin.get_sheet(self.c, 2026, 10)
        # 5 + 2*100 = 205
        self.assertEqual(again['rows'][0]['purchaseQty'], 205)
        # body has clear name/qty/unit and real newlines (not literal \n)
        body = result['orders'][0]['body']
        self.assertIn('HIQラベル', body)
        self.assertIn('2', body)
        self.assertIn('箱', body)
        self.assertIn('\n', body)
        self.assertNotIn('\\n', body)

    def test_vendor_save_normalizes_literal_newlines(self):
        v = self._vendor(
            bodyTemplate='{vendor} 様\\n\\n明細:\\n{lines}\\n',
            subjectTemplate='発注\\n{date}',
        )
        self.assertEqual(v['bodyTemplate'], '{vendor} 様\n\n明細:\n{lines}')
        self.assertEqual(v['subjectTemplate'], '発注\n{date}')

    def test_idempotent_prep_key(self):
        v = self._vendor()
        it = self._item(
            'ファイル', 90, vendorId=v['id'], orderUnit='冊', packingQty=1,
            orderProductName='クリアファイル',
        )
        r1 = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'same', 'orderDate': '2026-10-01',
             'lines': [{'itemId': it['id'], 'orderQty': 3}]},
            open_mail=True, skip_mail=True,
        )
        r2 = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'same', 'orderDate': '2026-10-01',
             'lines': [{'itemId': it['id'], 'orderQty': 3}]},
            open_mail=True, skip_mail=True,
        )
        self.assertTrue(r2['idempotent'])
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(sheet['rows'][0]['purchaseQty'], 3)

    def test_regenerate_mail_does_not_readd_purchase(self):
        v = self._vendor(mode='excel')
        it = self._item(
            '再生テスト', 90, vendorId=v['id'], orderUnit='箱', packingQty=2,
            orderProductName='再生ラベル',
        )
        first = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'regen1', 'orderDate': '2026-10-01',
             'lines': [{'itemId': it['id'], 'orderQty': 2}]},
            open_mail=True, skip_mail=True,
        )
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 4)
        opened = []

        def fake_popen(args, **kwargs):
            opened.append(args)
            return mock.Mock()

        again = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'regen1', 'regenerateMail': True,
             'orderDate': '2026-10-01',
             'lines': [{'itemId': it['id'], 'orderQty': 2}]},
            open_mail=True, skip_mail=False,
            popen=fake_popen, tb_path='/usr/bin/true',
        )
        self.assertTrue(again['idempotent'])
        self.assertTrue(again.get('regenerated'))
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 4)
        self.assertEqual(len(opened), 1)
        compose = opened[0][2]
        self.assertIn('attachment=', compose)
        self.assertIn("format='1'", compose)
        att = again['orders'][0]['attachmentPath']
        self.assertTrue(att and Path(att).is_file())
        with ZipFile(att) as z:
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertIn('再生ラベル', sheet)

    def test_cancel_reverses_only_that_order(self):
        v = self._vendor()
        it = self._item(
            'マーカー', 70, vendorId=v['id'], orderUnit='本', packingQty=1,
            orderProductName='油性マーカー',
        )
        a = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'a', 'orderDate': '2026-10-05',
             'lines': [{'itemId': it['id'], 'orderQty': 2}]},
            open_mail=True, skip_mail=True,
        )
        b = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'b', 'orderDate': '2026-10-05',
             'lines': [{'itemId': it['id'], 'orderQty': 5}]},
            open_mail=True, skip_mail=True,
        )
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 7)
        oid = a['orders'][0]['id']
        kintai_bihin_order.cancel_order(self.c, self.user, {'id': oid})
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 5)
        hist = kintai_bihin_order.history_flat(self.c)
        cancelled = [h for h in hist if h['orderId'] == oid]
        self.assertTrue(cancelled and cancelled[0]['cancelled'])
        active = [h for h in hist if h['orderId'] == b['orders'][0]['id']]
        self.assertTrue(active and not active[0]['cancelled'])

    def test_order_date_month_not_displayed_past_month(self):
        """購入は発注日の月へ。表示中の別月シートは触らない。"""
        v = self._vendor()
        it = self._item(
            'クリップ', 40, vendorId=v['id'], orderUnit='箱', packingQty=50,
            orderProductName='ゼムクリップ',
        )
        # 9月を先に保存
        s9 = kintai_bihin.get_sheet(self.c, 2026, 9)
        r9 = dict(s9['rows'][0])
        r9.update({'openingQty': 1, 'purchaseQty': 1, 'closingQty': 1})
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 9, 'version': 0, 'rows': [r9],
        })
        kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'oct', 'orderDate': '2026-10-12',
             'lines': [{'itemId': it['id'], 'orderQty': 1}]},
            open_mail=True, skip_mail=True,
        )
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 9)['rows'][0]['purchaseQty'], 1)
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 50)

    def test_does_not_break_shift_or_bihin_save(self):
        shift = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': shift['people'],
            'cells': {'regular:001': {'1': {'kind': 'off', 'text': '×'}}},
        })
        v = self._vendor()
        it = self._item('糊', 60, vendorId=v['id'], orderUnit='本', packingQty=1, orderProductName='スティック糊')
        kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 's', 'orderDate': '2026-11-02',
             'lines': [{'itemId': it['id'], 'orderQty': 1}]},
            open_mail=True, skip_mail=True,
        )
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual(again['cells']['regular:001']['1']['kind'], 'off')
        # 通常保存も可
        sheet = kintai_bihin.get_sheet(self.c, 2026, 11)
        row = dict(sheet['rows'][0])
        row['closingQty'] = 0
        code, saved = kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': sheet['version'], 'rows': [row],
        })
        self.assertEqual(code, 200)
        self.assertEqual(saved['rows'][0]['closingQty'], 0)

    def test_group_same_vendor(self):
        v = self._vendor(mode='excel')
        a = self._item('A紙', 10, vendorId=v['id'], orderUnit='冊', packingQty=1, orderProductName='A')
        b = self._item('B紙', 20, vendorId=v['id'], orderUnit='冊', packingQty=1, orderProductName='B')
        opened = []

        def fake_popen(args, **kwargs):
            opened.append(args)
            return mock.Mock()

        result = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'g', 'orderDate': '2026-10-08',
             'lines': [
                 {'itemId': a['id'], 'orderQty': 1},
                 {'itemId': b['id'], 'orderQty': 2},
             ]},
            open_mail=True, skip_mail=False,
            popen=fake_popen, tb_path='/usr/bin/true',
        )
        self.assertEqual(len(result['orders']), 1)
        self.assertEqual(len(result['orders'][0]['lines']), 2)
        self.assertEqual(len(opened), 1)
        compose = ' '.join(opened[0])
        self.assertIn('attachment=', compose)
        self.assertIn("format='1'", compose)
        self.assertIn('<br>', compose)

    def test_soft_deleted_item_hidden_from_order_keeps_history(self):
        v = self._vendor()
        it = self._item(
            '付箋', 30, vendorId=v['id'], orderUnit='冊', packingQty=1,
            orderProductName='ポストイット',
        )
        prepared = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'soft-del', 'orderDate': '2026-10-15',
             'lines': [{'itemId': it['id'], 'orderQty': 4}]},
            open_mail=True, skip_mail=True,
        )
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 4)
        kintai_bihin.soft_delete_item(self.c, self.user, {'id': it['id']})
        # 発注候補からは拒否
        with self.assertRaises(ValueError) as ctx:
            kintai_bihin_order.prepare_orders(
                self.c, self.user,
                {'prepKey': 'soft-del-2', 'orderDate': '2026-10-16',
                 'lines': [{'itemId': it['id'], 'orderQty': 1}]},
                open_mail=True, skip_mail=True,
            )
        self.assertIn('品目', str(ctx.exception))
        # 履歴は残る・購入加算も残る
        hist = kintai_bihin_order.history_flat(self.c)
        self.assertTrue(any(h['orderId'] == prepared['orders'][0]['id'] for h in hist))
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 4)
        # 設定バンドルの items には inactive 含む（UI がフィルタ）
        bundle = kintai_bihin_order.settings_bundle(self.c)
        found = [x for x in bundle['items'] if x['id'] == it['id']]
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]['active'])


class BihinOrderUiContractTest(unittest.TestCase):
    def test_ui_hooks(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="bihinorder"', index)
        self.assertIn('id="bihinordersettings"', index)
        self.assertIn('id="bihinorderhistory"', index)
        self.assertIn('id="bihinorderdialog"', index)
        self.assertIn('Excel添付', index)
        self.assertIn('本文に記載', index)
        js = (ROOT / 'kintai-bihin.js').read_text(encoding='utf-8')
        self.assertIn('/api/kintai/bihin/order/prepare', js)
        self.assertIn('/api/kintai/bihin/order/cancel', js)
        self.assertIn('prepKey', js)
        self.assertNotIn('送信済み', js)
        self.assertNotIn('mailto:', js)
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('/api/kintai/bihin/order/prepare', server)
        src = inspect.getsource(kintai_bihin.schema)
        self.assertIn('kintai_bihin_order.schema', src)
        # シフト非破壊
        self.assertNotIn('DROP TABLE', inspect.getsource(kintai_bihin_order.schema))
        fixture = ROOT / 'assets' / 'bihin-order' / '注文書_エンパイア発寒支店.xlsx'
        self.assertTrue(fixture.is_file())
        self.assertGreater(fixture.stat().st_size, 10000)
        kintai_bihin_order.validate_order_template(fixture)


if __name__ == '__main__':
    unittest.main()
