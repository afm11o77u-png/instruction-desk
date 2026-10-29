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

    def test_format_order_content_block(self):
        single = kintai_bihin_order._format_order_content_block([{
            'orderProductName': 'CP2610HMES-TA／白ラベル',
            'orderQty': 2,
            'orderUnit': '箱',
        }])
        self.assertEqual(single, 'CP2610HMES-TA／白ラベル\n数量：2 箱')
        multi = kintai_bihin_order._format_order_content_block([
            {'orderProductName': 'A品', 'orderQty': 1, 'orderUnit': '箱', 'orderSpec': '規格X'},
            {'orderProductName': 'B品', 'orderQty': Decimal('2.0'), 'orderUnit': '巻'},
        ])
        self.assertEqual(multi, 'A品\n規格X\n数量：1 箱\n\nB品\n数量：2 巻')
        # 金額フィールドがあっても本文には出さない
        with_amount = kintai_bihin_order._format_order_content_block([{
            'orderProductName': 'C', 'orderQty': 3, 'orderUnit': '個',
            'amount': 999, 'unitPrice': 100,
        }])
        self.assertNotIn('999', with_amount)
        self.assertNotIn('100', with_amount)
        self.assertNotIn('円', with_amount)
        self.assertEqual(with_amount, 'C\n数量：3 個')
        # 注文画面数量のまま（入数換算しない）
        self.assertIn('数量：2 箱', single)
        self.assertNotIn('数量：20', single)

    def test_qty_unit_not_concatenated(self):
        """数字のみ単位は本文から除外。「1」+「1」→「数量：1」（「1 1」「11」にしない）。"""
        # 再現: 単位が数字「1」のとき qty=1 で「数量：11」→#277で「1 1」→本修正で「1」
        bad_unit = kintai_bihin_order._format_order_content_block([{
            'orderProductName': 'CP2610HMES-TA／白ラベル',
            'orderQty': 1,
            'orderUnit': '1',
        }])
        self.assertEqual(bad_unit, 'CP2610HMES-TA／白ラベル\n数量：1')
        self.assertNotIn('数量：11', bad_unit)
        self.assertNotIn('数量：1 1', bad_unit)
        # 全角数字のみも除外
        zenkaku = kintai_bihin_order._format_qty_with_unit(2, '２')
        self.assertEqual(zenkaku, '2')
        # 単位が「1ロット」は本物の単位として空白区切り（除外しない）
        lot = kintai_bihin_order._format_order_content_block([{
            'orderProductName': 'ロット品',
            'orderQty': 1,
            'orderUnit': '1ロット',
        }])
        self.assertEqual(lot, 'ロット品\n数量：1 1ロット')
        self.assertNotIn('数量：11ロット', lot)
        # 単位空なら数量のみ（二重にしない）
        empty = kintai_bihin_order._format_qty_with_unit(1, '')
        self.assertEqual(empty, '1')
        self.assertEqual(kintai_bihin_order._format_qty_with_unit(1, '箱'), '1 箱')
        # 数字のみ単位の検知（自動書き換えはしない）
        self.assertTrue(kintai_bihin_order.is_digits_only_unit('1'))
        self.assertTrue(kintai_bihin_order.is_digits_only_unit('２'))
        self.assertFalse(kintai_bihin_order.is_digits_only_unit('1ロット'))
        self.assertFalse(kintai_bihin_order.is_digits_only_unit('箱'))
        self.assertEqual(kintai_bihin_order._display_order_unit('1'), '')
        self.assertEqual(kintai_bihin_order._display_order_unit('箱'), '箱')
        # qty 1 / 2 / 10 — 本物の単位は空白付き、数字のみ単位は数量のみ
        for q in (1, 2, 10):
            text = kintai_bihin_order._format_order_content_block([{
                'orderProductName': 'X', 'orderQty': q, 'orderUnit': '箱',
            }])
            self.assertEqual(text, f'X\n数量：{q} 箱')
            self.assertNotIn(f'数量：{q}箱', text)  # 空白なし連結は不可
            digits = kintai_bihin_order._format_order_content_block([{
                'orderProductName': 'Y', 'orderQty': q, 'orderUnit': '1',
            }])
            self.assertEqual(digits, f'Y\n数量：{q}')
            self.assertNotIn(f'数量：{q} 1', digits)

    def test_fill_writes_qty_number_only_into_mapped_yellow_cells(self):
        """品名照合で黄欄 D のみ数値転記。単位は結合せず、品名セルは書き換えない。"""
        path, _ = kintai_bihin_order.resolve_template_path()
        before = path.read_bytes()
        lines = [
            {'orderProductName': 'コンビテックス 白', 'orderQty': 1, 'orderUnit': '1'},
            {'orderProductName': 'HIQラベル(カラー)赤', 'orderQty': 2, 'orderUnit': '箱'},
            {'orderProductName': '裾上げテープ', 'orderQty': 10, 'orderUnit': '1ロット'},
        ]
        blob = kintai_bihin_order.fill_order_workbook(
            path, order_date='2026-10-29', vendor_name='株式会社シーティープランニング',
            lines=lines,
        )
        self.assertEqual(path.read_bytes(), before)
        with ZipFile(BytesIO(blob)) as z:
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertRegex(sheet, r'<c r="D19"[^>]*>\s*<v>1</v>')
            self.assertRegex(sheet, r'<c r="D20"[^>]*>\s*<v>2</v>')
            self.assertRegex(sheet, r'<c r="D23"[^>]*>\s*<v>10</v>')
            # 数量と単位を連結した「11」等は書かない
            self.assertNotRegex(sheet, r'<c r="D19"[^>]*>\s*<v>11</v>')
            # テンプレ品名は sharedStrings のまま（シート XML へインラインしない）
            sst = z.read('xl/sharedStrings.xml').decode('utf-8')
            self.assertIn('コンビテックス', sst)
            self.assertRegex(sheet, r'<c r="B19"[^>]*t="s"')
            # 未発注の見本数量 D21/D22 はクリア
            d21 = re.search(r'<c r="D21"[^>]*(?:/>|>(.*?)</c>)', sheet, re.DOTALL)
            self.assertIsNotNone(d21)
            self.assertNotIn('<v>1</v>', d21.group(0))
            # 数式は残す
            self.assertIn('SUM(G19:G31)', sheet)

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
            vendor_name='株式会社シーティープランニング',
            lines=[{
                'orderProductName': 'カッター刃(上刃)',
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
            # 品名は sharedStrings のまま（B24 を書き換えない）
            sst = z.read('xl/sharedStrings.xml').decode('utf-8')
            self.assertIn('カッター刃(上刃)', sst)
            self.assertRegex(sheet, r'<c r="B24"[^>]*t="s"')
            self.assertRegex(sheet, r'<c r="D24"[^>]*>\s*<v>3</v>')
            self.assertRegex(sheet, r'<c r="H3"[^>]*t="inlineStr"')
            # F/G 見本単価はブランケットクリアしない（数式行は維持）
            calc = z.read('xl/calcChain.xml').decode('utf-8')
            self.assertNotIn('H3', calc)
            self.assertIn('G32', calc)
            rels = z.read('xl/worksheets/_rels/sheet1.xml.rels').decode('utf-8')
            self.assertIn('drawing', rels)
            self.assertIn('printerSettings', rels)

    def test_fill_rejects_unmapped_without_guess(self):
        path, _ = kintai_bihin_order.resolve_template_path()
        with self.assertRaises(ValueError) as ctx:
            kintai_bihin_order.fill_order_workbook(
                path,
                order_date='2026-10-29',
                lines=[
                    {'orderProductName': '裾上げテープ', 'orderQty': 1, 'orderUnit': '箱'},
                    {'orderProductName': '未知の備品XYZ', 'orderQty': 2, 'orderUnit': '箱',
                     'itemId': 99},
                ],
            )
        msg = str(ctx.exception)
        self.assertIn('見つからない', msg)
        self.assertIn('未知の備品XYZ', msg)
        self.assertIn('itemId=99', msg)

    def test_fill_item_id_and_cell_override(self):
        path, _ = kintai_bihin_order.resolve_template_path()
        # 名前が曖昧でも orderQtyCell / itemId マップで確定
        kintai_bihin_order.EMPIRE_CT_ITEM_ID_TO_QTY_CELL[9001] = 'D26'
        try:
            blob = kintai_bihin_order.fill_order_workbook(
                path,
                order_date='2026-10-29',
                lines=[
                    {'itemId': 9001, 'orderProductName': 'バネ部品（別名）', 'orderQty': 4},
                    {'orderProductName': '別名ラベル', 'orderQty': 7, 'orderQtyCell': 'D21'},
                ],
            )
        finally:
            kintai_bihin_order.EMPIRE_CT_ITEM_ID_TO_QTY_CELL.pop(9001, None)
        with ZipFile(BytesIO(blob)) as z:
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertRegex(sheet, r'<c r="D26"[^>]*>\s*<v>4</v>')
            self.assertRegex(sheet, r'<c r="D21"[^>]*>\s*<v>7</v>')

    def test_fill_multi_color_hiq(self):
        path, _ = kintai_bihin_order.resolve_template_path()
        blob = kintai_bihin_order.fill_order_workbook(
            path,
            order_date='2026-10-29',
            lines=[
                {'orderProductName': 'HIQラベル', 'orderSpec': '赤', 'orderQty': 1},
                {'orderProductName': 'HIQラベル 青 幅50mm', 'orderQty': 2},
                {'orderProductName': 'HIQラベル(カラー)緑', 'orderQty': 3},
            ],
        )
        with ZipFile(BytesIO(blob)) as z:
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertRegex(sheet, r'<c r="D20"[^>]*>\s*<v>1</v>')
            self.assertRegex(sheet, r'<c r="D21"[^>]*>\s*<v>2</v>')
            self.assertRegex(sheet, r'<c r="D22"[^>]*>\s*<v>3</v>')


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

    def _vendor(self, name='文具店', email='vendor@example.com', mode='body', **extra):
        # 既定は本文モード（他仕入先の本文フローを壊さない）。Excel は CT 向けに明示。
        if mode == 'body' and 'bodyTemplate' not in extra:
            extra = {
                **extra,
                'bodyTemplate': '{vendor} 様\n\n{注文内容}\n',
            }
        return kintai_bihin_order.save_vendor(self.c, self.user, {
            'name': name, 'email': email, 'mode': mode, **extra,
        })

    def _ct_vendor(self, email='ct-test@example.com', **extra):
        """シーティープランニング相当（実ベンダー宛ではない example.com）。"""
        return self._vendor(
            name='株式会社シーティープランニング',
            email=email,
            mode='excel',
            bodyTemplate=extra.pop('bodyTemplate', '明細は添付の注文書をご確認ください。'),
            **extra,
        )

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
        # body has {注文内容} expanded: 発注品名 / 数量：注文画面qty 単位（入数換算なし）
        body = result['orders'][0]['body']
        self.assertIn('HIQラベル\n数量：2 箱', body)
        self.assertNotIn('数量：200', body)  # packing 100 × 2 は本文に出さない
        self.assertIn('\n', body)
        self.assertNotIn('\\n', body)
        self.assertNotIn('{注文内容}', body)

    def test_vendor_save_normalizes_literal_newlines(self):
        v = self._vendor(
            bodyTemplate='{vendor} 様\\n\\n明細:\\n{注文内容}\\n',
            subjectTemplate='発注\\n{date}',
        )
        self.assertEqual(v['bodyTemplate'], '{vendor} 様\n\n明細:\n{注文内容}')
        self.assertEqual(v['subjectTemplate'], '発注\n{date}')

    def test_body_mode_requires_order_placeholder_no_mail_no_purchase(self):
        v = self._vendor(
            mode='body',
            bodyTemplate='{vendor} 様\n\nよろしく。\n',  # {注文内容} なし
        )
        it = self._item(
            '欠落プレース', 10, vendorId=v['id'], orderUnit='箱', packingQty=5,
            orderProductName='欠落品',
        )
        with self.assertRaises(ValueError) as ctx:
            kintai_bihin_order.prepare_orders(
                self.c, self.user,
                {'prepKey': 'no-ph', 'orderDate': '2026-10-20',
                 'lines': [{'itemId': it['id'], 'orderQty': 1}]},
                open_mail=True, skip_mail=True,
            )
        self.assertIn('{注文内容}', str(ctx.exception))
        self.assertEqual(kintai_bihin_order.list_order_history(self.c), [])
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        if sheet['rows']:
            pq = sheet['rows'][0].get('purchaseQty')
            self.assertTrue(pq in (None, 0))

    def test_excel_mode_without_placeholder_keeps_attach_flow(self):
        v = self._ct_vendor(bodyTemplate='添付をご確認ください。')
        it = self._item(
            '添付のみ', 10, vendorId=v['id'], orderUnit='箱', packingQty=2,
            orderProductName='裾上げテープ',
        )
        opened = []

        def fake_popen(args, **kwargs):
            opened.append(args)
            return mock.Mock()

        result = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'excel-no-ph', 'orderDate': '2026-10-21',
             'lines': [{'itemId': it['id'], 'orderQty': 3}]},
            open_mail=True, skip_mail=False,
            popen=fake_popen, tb_path='/usr/bin/true',
        )
        self.assertEqual(len(result['orders']), 1)
        body = result['orders'][0]['body']
        self.assertEqual(body, '添付をご確認ください。')
        self.assertNotIn('数量：', body)
        compose = opened[0][2]
        self.assertIn('attachment=', compose)
        att = result['orders'][0]['attachmentPath']
        self.assertTrue(att and Path(att).is_file())
        with ZipFile(att) as z:
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertRegex(sheet, r'<c r="D23"[^>]*>\s*<v>3</v>')
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 6)

    def test_excel_mode_with_placeholder_replaces(self):
        v = self._ct_vendor(bodyTemplate='前置き\n\n{注文内容}\n\n後書き')
        it = self._item(
            '添付＋本文', 10, vendorId=v['id'], orderUnit='巻', packingQty=1,
            orderProductName='コンビテックス 白', orderSpec='幅50mm',
        )
        result = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'excel-ph', 'orderDate': '2026-10-22',
             'lines': [{'itemId': it['id'], 'orderQty': 4}]},
            open_mail=True, skip_mail=True,
        )
        body = result['orders'][0]['body']
        self.assertTrue(body.startswith('前置き\n\n'))
        self.assertTrue(body.rstrip().endswith('後書き'))
        self.assertIn('コンビテックス 白\n幅50mm\n数量：4 巻', body)
        self.assertNotIn('{注文内容}', body)
        self.assertTrue(result['orders'][0]['attachmentPath'])
        with ZipFile(result['orders'][0]['attachmentPath']) as z:
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertRegex(sheet, r'<c r="D19"[^>]*>\s*<v>4</v>')

    def test_multi_vendor_separate_compose_bodies(self):
        va = self._vendor(
            name='店A', email='a@example.com', mode='body',
            bodyTemplate='A宛\n{注文内容}\n以上',
        )
        vb = self._vendor(
            name='店B', email='b@example.com', mode='body',
            bodyTemplate='B宛\n{注文内容}\n以上',
        )
        ia = self._item(
            'A商品', 10, vendorId=va['id'], orderUnit='箱', packingQty=1,
            orderProductName='A品名',
        )
        ib = self._item(
            'B商品', 20, vendorId=vb['id'], orderUnit='袋', packingQty=1,
            orderProductName='B品名',
        )
        opened = []

        def fake_popen(args, **kwargs):
            opened.append(args)
            return mock.Mock()

        result = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'mv', 'orderDate': '2026-10-23',
             'lines': [
                 {'itemId': ia['id'], 'orderQty': 1},
                 {'itemId': ib['id'], 'orderQty': 2},
             ]},
            open_mail=True, skip_mail=False,
            popen=fake_popen, tb_path='/usr/bin/true',
        )
        self.assertEqual(len(result['orders']), 2)
        self.assertEqual(len(opened), 2)
        bodies = {o['vendorEmail']: o['body'] for o in result['orders']}
        self.assertIn('A品名\n数量：1 箱', bodies['a@example.com'])
        self.assertNotIn('B品名', bodies['a@example.com'])
        self.assertIn('B品名\n数量：2 袋', bodies['b@example.com'])
        self.assertNotIn('A品名', bodies['b@example.com'])
        # 既存テンプレを勝手に書き換えない
        again = kintai_bihin_order.list_vendors(self.c)
        by_id = {v['id']: v for v in again}
        self.assertEqual(by_id[va['id']]['bodyTemplate'], 'A宛\n{注文内容}\n以上')
        self.assertEqual(by_id[vb['id']]['bodyTemplate'], 'B宛\n{注文内容}\n以上')

    def test_multi_items_same_vendor_blank_line(self):
        v = self._vendor(
            mode='body',
            bodyTemplate='注文:\n{注文内容}\n',
        )
        a = self._item(
            '複数1', 10, vendorId=v['id'], orderUnit='箱', packingQty=1,
            orderProductName='品1',
        )
        b = self._item(
            '複数2', 10, vendorId=v['id'], orderUnit='本', packingQty=1,
            orderProductName='品2',
        )
        result = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'multi-same', 'orderDate': '2026-10-24',
             'lines': [
                 {'itemId': a['id'], 'orderQty': 1},
                 {'itemId': b['id'], 'orderQty': 3},
             ]},
            open_mail=True, skip_mail=True,
        )
        self.assertEqual(len(result['orders']), 1)
        body = result['orders'][0]['body']
        self.assertIn('品1\n数量：1 箱\n\n品2\n数量：3 本', body)

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

    def test_digits_only_unit_flag_and_records_not_concatenated(self):
        """単位「1」でも orderQty/orderUnit は分離保存。本文は「数量：1」。購入は入数換算。自動書き換えなし。"""
        v = self._vendor(mode='body')
        it = self._item(
            '白ラベル', 120, unit='枚',
            vendorId=v['id'],
            orderProductName='CP2610HMES-TA／白ラベル',
            orderUnit='1',
            packingQty=100,
        )
        self.assertEqual(it['orderUnit'], '1')
        self.assertTrue(it.get('orderUnitNeedsReview'))
        # 再保存しても単位「1」を推測書き換えしない
        again = kintai_bihin.save_item(self.c, self.user, {
            'id': it['id'],
            'category': it['category'],
            'name': it['name'],
            'unit': it['unit'],
            'unitPrice': it['unitPrice'],
            'vendorId': v['id'],
            'orderProductName': it['orderProductName'],
            'orderUnit': '1',
            'packingQty': 100,
            'active': True,
        })
        self.assertEqual(again['orderUnit'], '1')
        self.assertTrue(again.get('orderUnitNeedsReview'))

        result = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'digits-unit', 'orderDate': '2026-10-20',
             'lines': [{'itemId': it['id'], 'orderQty': 1}]},
            open_mail=True, skip_mail=True,
        )
        body = result['orders'][0]['body']
        self.assertIn('数量：1', body)
        self.assertNotIn('数量：1 1', body)
        self.assertNotIn('数量：11', body)
        # 履歴レコードは qty/unit 分離（表示バグであって保存値の連結ではない）
        flat = kintai_bihin_order.history_flat(self.c)
        row = next(h for h in flat if h['orderId'] == result['orders'][0]['id'])
        self.assertEqual(row['orderQty'], 1)
        self.assertEqual(row['orderUnit'], '1')
        self.assertEqual(row['inventoryQty'], 100)  # 1 * packing 100
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(sheet['rows'][0]['purchaseQty'], 100)
        # regenerate で二重加算しない
        kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'digits-unit', 'regenerateMail': True,
             'orderDate': '2026-10-20',
             'lines': [{'itemId': it['id'], 'orderQty': 1}]},
            open_mail=True, skip_mail=True,
        )
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'][0]['purchaseQty'], 100)
        hints = kintai_bihin_order.settings_bundle(self.c)['hints']
        self.assertIn('箱・巻・個', hints['orderUnit'])

    def test_regenerate_mail_does_not_readd_purchase(self):
        v = self._ct_vendor()
        it = self._item(
            '再生テスト', 90, vendorId=v['id'], orderUnit='箱', packingQty=2,
            orderProductName='HIQラベル(カラー)赤',
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
            sst = z.read('xl/sharedStrings.xml').decode('utf-8')
            # テンプレ品名は残し、数量のみ D20
            self.assertIn('HIQ', sst)
            self.assertRegex(sheet, r'<c r="D20"[^>]*>\s*<v>2</v>')

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
        v = self._ct_vendor()
        a = self._item(
            'A紙', 10, vendorId=v['id'], orderUnit='箱', packingQty=1,
            orderProductName='HIQラベル(カラー)赤',
        )
        b = self._item(
            'B紙', 20, vendorId=v['id'], orderUnit='箱', packingQty=1,
            orderProductName='HIQラベル(カラー)青',
        )
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
        self.assertIn("to='ct-test@example.com'", compose)
        with ZipFile(result['orders'][0]['attachmentPath']) as z:
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertRegex(sheet, r'<c r="D20"[^>]*>\s*<v>1</v>')
            self.assertRegex(sheet, r'<c r="D21"[^>]*>\s*<v>2</v>')
            # 未発注の見本数量（緑）はクリア
            d22 = re.search(r'<c r="D22"[^>]*(?:/>|>(.*?)</c>)', sheet, re.DOTALL)
            self.assertIsNotNone(d22)
            self.assertNotIn('<v>1</v>', d22.group(0))

    def test_ct_planning_unmapped_blocks_purchase(self):
        v = self._ct_vendor()
        it = self._item(
            '未知', 10, vendorId=v['id'], orderUnit='箱', packingQty=1,
            orderProductName='ウェブにだけある品',
        )
        with self.assertRaises(ValueError) as ctx:
            kintai_bihin_order.prepare_orders(
                self.c, self.user,
                {'prepKey': 'unmap', 'orderDate': '2026-10-09',
                 'lines': [{'itemId': it['id'], 'orderQty': 1}]},
                open_mail=True, skip_mail=True,
            )
        self.assertIn('ウェブにだけある品', str(ctx.exception))
        self.assertEqual(kintai_bihin_order.list_order_history(self.c), [])
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        if sheet['rows']:
            self.assertTrue(sheet['rows'][0].get('purchaseQty') in (None, 0))

    def test_body_vendor_unchanged_when_ct_excel_exists(self):
        """他仕入先の本文モードは Excel マップの影響を受けない。"""
        body_v = self._vendor(name='別仕入先', mode='body')
        ct = self._ct_vendor()
        body_it = self._item(
            '本文品', 10, vendorId=body_v['id'], orderUnit='箱', packingQty=1,
            orderProductName='本文だけの品',
        )
        ct_it = self._item(
            'CT品', 10, vendorId=ct['id'], orderUnit='箱', packingQty=1,
            orderProductName='カッター刃のバネ',
        )
        opened = []

        def fake_popen(args, **kwargs):
            opened.append(args)
            return mock.Mock()

        result = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'mixed-modes', 'orderDate': '2026-10-11',
             'lines': [
                 {'itemId': body_it['id'], 'orderQty': 1},
                 {'itemId': ct_it['id'], 'orderQty': 5},
             ]},
            open_mail=True, skip_mail=False,
            popen=fake_popen, tb_path='/usr/bin/true',
        )
        self.assertEqual(len(result['orders']), 2)
        by_mode = {o['mode']: o for o in result['orders']}
        self.assertEqual(by_mode['body']['body'].count('本文だけの品'), 1)
        self.assertFalse(by_mode['body']['attachmentPath'])
        self.assertTrue(by_mode['excel']['attachmentPath'])
        with ZipFile(by_mode['excel']['attachmentPath']) as z:
            sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertRegex(sheet, r'<c r="D26"[^>]*>\s*<v>5</v>')
        self.assertEqual(len(opened), 2)

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

    def test_order_after_sheet_row_delete_restores_without_double_count(self):
        """月シート行削除≠発注取消。再発注は行を復元し今回分だけ加算（過去分は二重にしない）。"""
        v = self._vendor()
        it = self._item(
            'ラベル再登', 50, vendorId=v['id'], orderUnit='箱', packingQty=10,
            orderProductName='再登ラベル',
        )
        first = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'row-del-1', 'orderDate': '2026-10-05',
             'lines': [{'itemId': it['id'], 'orderQty': 2}]},
            open_mail=True, skip_mail=True,
        )
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(sheet['rows'][0]['purchaseQty'], 20)  # 2*10
        # 表示中月の行だけ全削除して保存（マスタ・履歴は触らない）
        code, emptied = kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': sheet['version'], 'rows': [],
        })
        self.assertEqual(code, 200)
        self.assertEqual(emptied['rows'], [])
        hist_before = kintai_bihin_order.history_flat(self.c)
        self.assertTrue(any(h['orderId'] == first['orders'][0]['id'] for h in hist_before))
        self.assertFalse(any(
            h.get('cancelled') for h in hist_before if h['orderId'] == first['orders'][0]['id']
        ))
        # 同月で再発注 → 行復元＋今回分のみ（過去20は再加算しない）
        second = kintai_bihin_order.prepare_orders(
            self.c, self.user,
            {'prepKey': 'row-del-2', 'orderDate': '2026-10-20',
             'lines': [{'itemId': it['id'], 'orderQty': 1}]},
            open_mail=True, skip_mail=True,
        )
        again = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(len(again['rows']), 1)
        self.assertEqual(again['rows'][0]['itemId'], it['id'])
        self.assertEqual(again['rows'][0]['purchaseQty'], 10)  # 1*10 only
        hist = kintai_bihin_order.history_flat(self.c)
        ids = {h['orderId'] for h in hist}
        self.assertIn(first['orders'][0]['id'], ids)
        self.assertIn(second['orders'][0]['id'], ids)
        # マスタは有効のまま
        items = kintai_bihin.list_items(self.c)
        self.assertTrue(any(x['id'] == it['id'] and x['active'] for x in items))


class BihinOrderUiContractTest(unittest.TestCase):
    def test_ui_hooks(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="bihinorder"', index)
        self.assertIn('id="bihinordersettings"', index)
        self.assertIn('id="bihinorderhistory"', index)
        self.assertIn('id="bihinorderdialog"', index)
        self.assertIn('Excel添付', index)
        self.assertIn('本文に記載', index)
        self.assertIn('{注文内容}', index)
        self.assertIn('id="bihinvendorinsertorder"', index)
        self.assertIn('注文内容を挿入', index)
        self.assertIn('id="bihinitemorderspec"', index)
        self.assertIn('規格・補足', index)
        self.assertIn('id="bihinitemorderqtycell"', index)
        self.assertIn('注文書数量セル', index)
        self.assertIn('empireCtCellMap', inspect.getsource(kintai_bihin_order.settings_bundle))
        self.assertIn('bihinitemorderunit', index)
        self.assertIn('箱・巻・個などの単位を入力', index)
        self.assertIn('id="bihinitemorderunithint"', index)
        js = (ROOT / 'kintai-bihin.js').read_text(encoding='utf-8')
        self.assertIn('/api/kintai/bihin/order/prepare', js)
        self.assertIn('/api/kintai/bihin/order/cancel', js)
        self.assertIn('prepKey', js)
        self.assertIn('insertOrderContentPlaceholder', js)
        self.assertIn('{注文内容}', js)
        self.assertIn('orderSpec', js)
        self.assertIn('orderQtyCell', js)
        self.assertIn('formatOrderQtyUnit', js)
        self.assertIn('displayOrderUnit', js)
        self.assertIn('isDigitsOnlyOrderUnit', js)
        self.assertIn('orderUnitNeedsReview', js)
        self.assertNotIn('送信済み', js)
        self.assertNotIn('mailto:', js)
        self.assertIn('ORDER_CONTENT_PLACEHOLDER', inspect.getsource(kintai_bihin_order))
        self.assertIn('{注文内容}', kintai_bihin_order.DEFAULT_BODY_INLINE)
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
