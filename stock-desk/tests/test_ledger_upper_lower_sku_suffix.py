"""上下区分を品番末尾として登録する（テストDBのみ。本番登録・在庫変更なし）。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_bulk_import as bulk  # noqa: E402
import maker_sku_lookup as msl  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import product_ledger_position as pos  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def b64(text: str, encoding='utf-8-sig') -> str:
    return base64.b64encode(text.encode(encoding)).decode('ascii')


def ledger_csv(rows):
    header = '顧客,仕入先,商品コード,商品名,サイズ'
    return '\n'.join([header] + rows)


class PositionHelperTest(unittest.TestCase):
    def test_space_and_glued_sku_suffix(self):
        self.assertEqual(pos.strip_sku_position_suffix('VEH30B 下'), ('VEH30B', '下'))
        self.assertEqual(pos.strip_sku_position_suffix('VEH30B　下'), ('VEH30B', '下'))
        self.assertEqual(pos.strip_sku_position_suffix('VEH30B下'), ('VEH30B', '下'))
        self.assertEqual(pos.attach_sku_position_suffix('VEH30B', '下'), 'VEH30B下')
        self.assertEqual(pos.attach_sku_position_suffix('VEH30B下', '下'), 'VEH30B下')
        self.assertEqual(pos.attach_sku_position_suffix('VEHS3229N', '上'), 'VEHS3229N上')

    def test_no_double_append(self):
        self.assertEqual(
            pos.resolve_garment_position(code='VEH30B下', product_name='下')['registeredCode'],
            'VEH30B下',
        )

    def test_false_positives_not_extracted(self):
        self.assertEqual(pos.extract_garment_position_from_text('スラックス 裾上げ'), '')
        self.assertEqual(pos.extract_garment_position_from_text('上下セット'), '')
        self.assertEqual(pos.extract_garment_position_from_text('上衣 ブルー'), '')
        self.assertEqual(pos.extract_garment_position_from_text('股下72'), '')

    def test_conflict_needs_confirm(self):
        info = pos.resolve_garment_position(code='VEH30B 上', product_name='白下 ブルー')
        self.assertTrue(info['conflict'])
        self.assertEqual(info['position'], '')
        self.assertEqual(info['registeredCode'], 'VEH30B')


class ImportSkuSuffixTest(unittest.TestCase):
    def test_spaced_code_registers_glued_sku(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,ミドリ安全北海道㈱,VEH30B 下,男女共用 スラックス,M',
                '顧客A,ミドリ安全北海道㈱,VEHS3229N 上,ジャケット,L',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'pos.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            codes = {(r.get('csv') or {}).get('code') for r in preview['rows']}
            self.assertIn('VEH30B下', codes)
            self.assertIn('VEHS3229N上', codes)
            row_lower = next(r for r in preview['rows'] if (r.get('csv') or {}).get('code') == 'VEH30B下')
            self.assertEqual((row_lower.get('csv') or {}).get('sourceCode'), 'VEH30B 下')
            self.assertEqual((row_lower.get('csv') or {}).get('position'), '下')
            supp = ((row_lower.get('parenClassification') or {}).get('supplement') or '')
            self.assertNotEqual(supp, '下')
        finally:
            c.close()
            folder.cleanup()

    def test_name_position_splits_upper_lower_products(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,ミドリ安全北海道㈱,VEH30B,白下 ブルー,M',
                '顧客A,ミドリ安全北海道㈱,VEH30B,白上 ブルー,M',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'split.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            codes = sorted({(r.get('csv') or {}).get('code') for r in preview['rows']})
            self.assertEqual(codes, ['VEH30B上', 'VEH30B下'])
            group_codes = sorted(g['code'] for g in (preview.get('skuGroups') or []))
            self.assertEqual(group_codes, ['VEH30B上', 'VEH30B下'])
        finally:
            c.close()
            folder.cleanup()

    def test_hem_raise_not_appended_to_sku(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,仕入先X,ABC123,スラックス 裾上げ,M',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'hem.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row = preview['rows'][0]
            self.assertEqual((row.get('csv') or {}).get('code'), 'ABC123')
            self.assertFalse((row.get('csv') or {}).get('position'))
        finally:
            c.close()
            folder.cleanup()


class MakerAdoptSkuSuffixTest(unittest.TestCase):
    def test_parse_title_uses_position_not_supplement(self):
        ok = msl.parse_maker_listing_title(
            '男女共用　スラックス　ＶＥＨ３０Ｂ　下　ブルー', 'VEH30B',
        )
        self.assertEqual(ok['name'], '男女共用 スラックス')
        self.assertEqual(ok['color'], 'ブルー')
        self.assertEqual(ok['position'], '下')
        self.assertEqual(ok['plannedRegisteredSku'], 'VEH30B下')
        self.assertEqual(ok.get('supplement') or '', '')

    def test_adopt_saves_distinct_upper_lower(self):
        folder, path, c = newdb()
        try:
            lower = msl.adopt_maker_correction(
                c,
                manufacturer='ミドリ安全',
                maker_sku='VEH30B',
                name='男女共用 スラックス',
                color='ブルー',
                position='下',
                planned_registered_sku='VEH30B下',
                source_url='https://example.com/lower',
            )
            upper = msl.adopt_maker_correction(
                c,
                manufacturer='ミドリ安全',
                maker_sku='VEH30B',
                name='男女共用 ブルゾン',
                color='ブルー',
                position='上',
                planned_registered_sku='VEH30B上',
                source_url='https://example.com/upper',
            )
            self.assertEqual(lower['plannedRegisteredSku'], 'VEH30B下')
            self.assertEqual(upper['plannedRegisteredSku'], 'VEH30B上')
            # 基礎品番だけで下の補正を上へ適用しない
            saved_lower, _ = msl._lookup_saved_for_position(
                c, manufacturer='ミドリ安全', base_sku='VEH30B', position='下',
            )
            saved_upper, _ = msl._lookup_saved_for_position(
                c, manufacturer='ミドリ安全', base_sku='VEH30B', position='上',
            )
            self.assertEqual(saved_lower['adopted_name'], '男女共用 スラックス')
            self.assertEqual(saved_upper['adopted_name'], '男女共用 ブルゾン')
            none_base, _ = msl._lookup_saved_for_position(
                c, manufacturer='ミドリ安全', base_sku='VEH30B', position='',
            )
            self.assertIsNone(none_base)
        finally:
            c.close()
            folder.cleanup()

    def test_maker_adopt_preview_keeps_sku_suffix(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,ミドリ安全北海道㈱,VEH30B,白下 ブルー,M',
            ])
            preview0 = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'veh.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row_no = str(preview0['rows'][0]['rowNo'])
            patch = {
                'plannedName': '男女共用 スラックス',
                'color': 'ブルー',
                'supplement': '',
                'position': '下',
                'plannedRegisteredSku': 'VEH30B下',
                'unclassified': [],
                'userEdited': True,
                'confirmed': True,
                'acceptProposal': True,
                'makerAdopted': True,
            }
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'veh.csv',
                'rowClassifications': {row_no: patch},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row = preview['rows'][0]
            self.assertEqual((row.get('csv') or {}).get('code'), 'VEH30B下')
            self.assertEqual((row.get('parenClassification') or {}).get('plannedName'), '男女共用 スラックス')
            self.assertEqual((row.get('parenClassification') or {}).get('color'), 'ブルー')
            self.assertNotEqual((row.get('parenClassification') or {}).get('supplement'), '下')
            # 再照合でも補正保持
            preview2 = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'veh.csv',
                'rowClassifications': {row_no: patch},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual((preview2['rows'][0].get('csv') or {}).get('code'), 'VEH30B下')
            self.assertEqual(
                (preview2['rows'][0].get('parenClassification') or {}).get('plannedName'),
                '男女共用 スラックス',
            )
        finally:
            c.close()
            folder.cleanup()

    def test_search_candidate_exposes_planned_sku(self):
        folder, path, c = newdb()
        try:
            fake_hits = [{
                'title': '男女共用　スラックス　ＶＥＨ３０Ｂ　下　ブルー',
                'url': 'https://ec.midori-anzen.com/shop/g/g31131914/',
                'source': 'midori_ec',
                'preferred': True,
                'manufacturer': 'ミドリ安全',
            }]
            page = (
                '<html><head><title>男女共用 スラックス ＶＥＨ３０Ｂ 下 ブルー</title></head>'
                '<body>商品コード ＶＥＨ３０Ｂ</body></html>'
            )
            with mock.patch.object(msl, '_midori_ec_candidates', return_value=fake_hits), \
                    mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_http_get', return_value=('https://ec.midori-anzen.com/shop/g/g31131914/', page)):
                live = msl.search_maker_product(
                    c,
                    manufacturer='',
                    maker_sku='VEH30B',
                    source_name='白下 ブルー',
                    current_name='白下 ブルー',
                    current_sku='VEH30B',
                    supplier='ミドリ安全北海道㈱',
                )
            self.assertEqual(live['status'], 'ok')
            cand = live['candidates'][0]
            self.assertEqual(cand['plannedRegisteredSku'], 'VEH30B下')
            self.assertEqual(cand['position'], '下')
            self.assertNotEqual(cand.get('supplement'), '下')
            self.assertTrue(cand['diff'].get('skuChanged'))
        finally:
            c.close()
            folder.cleanup()


class OpeningMatchSkuSuffixTest(unittest.TestCase):
    def test_opening_matches_old_notation_to_suffixed_product(self):
        folder, path, c = newdb()
        try:
            # 商品を直接登録
            su = stock.save_supplier(c, {'name': 'ミドリ安全北海道㈱', 'active': True})
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            lower = stock.save_product(c, {
                'code': 'VEH30B下', 'name': '男女共用 スラックス', 'color': 'ブルー',
                'supplierId': su['id'], 'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            upper = stock.save_product(c, {
                'code': 'VEH30B上', 'name': '男女共用 ブルゾン', 'color': 'ブルー',
                'supplierId': su['id'], 'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            lower_id = lower['id']
            upper_id = upper['id']
            cp_l = lower['links'][0]['id']
            cp_u = upper['links'][0]['id']
            cid = cu['id']
            # マップ: 原文品番 VEH30B + 白下／白上
            for product_id, cpid, name, position, registered in (
                (lower_id, cp_l, '白下 ブルー', '下', 'VEH30B下'),
                (upper_id, cp_u, '白上 ブルー', '上', 'VEH30B上'),
            ):
                row = {
                    'rowNo': 1,
                    'csv': {
                        'customer': '顧客A',
                        'supplier': 'ミドリ安全北海道㈱',
                        'code': registered,
                        'sourceCode': 'VEH30B',
                        'baseCode': 'VEH30B',
                        'position': position,
                        'registeredCode': registered,
                        'productName': name,
                        'size': 'M',
                    },
                    'parenClassification': {'color': 'ブルー', 'position': position, 'supplement': ''},
                    'product': {'codeOverride': registered, 'position': position},
                    'specProvenance': {
                        'color': 'ブルー', 'position': position,
                        'baseCode': 'VEH30B', 'registeredCode': registered,
                        'sourceCode': 'VEH30B', 'plannedName': '男女共用',
                    },
                }
                pli.upsert_row_map(
                    c, run_id=1, file_sha256='x', file_name='t.csv',
                    row=row, customer_id=cid, product_id=product_id, customer_product_id=cpid,
                )
            # 旧表記の初期在庫行（原文名・品番の表記差から登録予定品番へ）
            for aux_code, aux_name, expect_pid in (
                ('VEH30B', '白下 ブルー', lower_id),
                ('VEH30B 下', '白下 ブルー', lower_id),
                ('VEH30B下', '白下 ブルー', lower_id),
                ('VEH30B', '白上 ブルー', upper_id),
            ):
                res = bulk.lookup_ledger_destinations_for_aux(
                    c,
                    customer='顧客A',
                    code=aux_code,
                    product_name=aux_name,
                    size='M',
                    supplier='ミドリ安全北海道㈱',
                )
                self.assertEqual(res['status'], 'ok', (aux_code, aux_name, res))
                self.assertEqual(res['matches'][0]['productId'], expect_pid, (aux_code, aux_name))
            # 上下未特定の基礎品番だけでは自動振り分けしない
            ambiguous = bulk.lookup_ledger_destinations_for_aux(
                c,
                customer='顧客A',
                code='VEH30B',
                product_name='スラックス',  # 上下が取れない
                size='M',
                supplier='ミドリ安全北海道㈱',
            )
            # 名コア不一致で missing、または ambiguous。どちらでも自動決定しない
            self.assertIn(ambiguous['status'], ('missing', 'ambiguous'))
        finally:
            c.close()
            folder.cleanup()


class UiWiringTest(unittest.TestCase):
    def test_ui_shows_planned_registered_sku(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('plannedRegisteredSku', js)
        self.assertIn('登録予定品番', js)
        self.assertIn('skuChanged', js)
        self.assertIn('position', js)
        self.assertIn('planned_registered_sku', server)
        self.assertIn('currentSku', server)


if __name__ == '__main__':
    unittest.main()
