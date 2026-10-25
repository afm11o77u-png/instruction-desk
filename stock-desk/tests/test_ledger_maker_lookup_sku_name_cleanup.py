"""メーカー補正: 検索品番の用途注記分離・商品名クリーンアップ（テストDBのみ。本番登録なし）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import maker_sku_bulk_lookup as bulk  # noqa: E402
import maker_sku_lookup as msl  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


class SplitMakerSkuAndNoteTest(unittest.TestCase):
    def test_usage_note_parking_guard(self):
        got = msl.split_maker_sku_and_note('AZ8960-019（駐車場警備）')
        self.assertEqual(got['searchSku'], 'AZ8960-019')
        self.assertEqual(got['usageNote'], '駐車場警備')
        self.assertEqual(got['sourceSku'], 'AZ8960-019（駐車場警備）')
        self.assertTrue(got['noteStripped'])

    def test_does_not_strip_all_parens(self):
        # 色番・型番枝っぽい括弧は残す
        keep_code = msl.split_maker_sku_and_note('APK499（10）')
        self.assertEqual(keep_code['searchSku'], 'APK499（10）')
        self.assertEqual(keep_code['usageNote'], '')

        # 色名括弧も品番側に残す（不明扱いでなく色として保持）
        keep_color = msl.split_maker_sku_and_note('VEH30B（ネイビー）')
        self.assertEqual(keep_color['searchSku'], 'VEH30B（ネイビー）')
        self.assertEqual(keep_color['usageNote'], '')

    def test_upper_lower_still_via_normalize(self):
        norm = msl.normalize_search_maker_sku('VEH30B下')
        self.assertEqual(norm['baseSku'], 'VEH30B')
        self.assertEqual(norm['position'], '下')
        self.assertEqual(norm['usageNote'], '')

    def test_effective_sku_uses_search_side_only(self):
        row = {
            'csv': {
                'sourceCode': 'AZ8960-019（駐車場警備）',
                'code': 'AZ8960-019（駐車場警備）',
                'supplier': 'アイトス',
            },
            'product': {},
        }
        self.assertEqual(msl.effective_maker_sku_from_row(row), 'AZ8960-019')
        meta = msl.row_maker_correction_meta(row)
        self.assertEqual(meta['makerSku'], 'AZ8960-019')
        self.assertEqual(meta['usageNote'], '駐車場警備')
        self.assertEqual(meta['sourceSku'], 'AZ8960-019（駐車場警備）')

    def test_note_split_does_not_rewrite_source_alone(self):
        # planned_registered_sku の基礎は検索側だが、source 原文は呼び出し側が保持
        planned = msl.planned_registered_sku(
            maker_sku='AZ8960-019',
            source_code='AZ8960-019（駐車場警備）',
        )
        self.assertEqual(planned, 'AZ8960-019')
        split = msl.split_maker_sku_and_note('AZ8960-019（駐車場警備）')
        self.assertEqual(split['sourceSku'], 'AZ8960-019（駐車場警備）')


class BaseSkuNotColorwayMatchTest(unittest.TestCase):
    def test_stem_page_is_not_full_sku_match(self):
        html = '''<!DOCTYPE html><html><head><title>AZ-8960 ジャケット｜アイトス</title></head>
        <body><h1>AZ-8960</h1>
        <div>商品コード</div><div>AZ-8960</div>
        </body></html>'''
        info = msl.page_sku_match(html, 'AZ-8960 ジャケット', 'AZ8960-019')
        self.assertEqual(info['match'], 'none')

    def test_full_colorway_matches(self):
        html = '''<!DOCTYPE html><html><head><title>AZ8960-019｜アイトス</title></head>
        <body><h1>AZ8960-019 ジャケット</h1>
        <div>商品コード</div><div>AZ8960-019</div>
        </body></html>'''
        info = msl.page_sku_match(html, 'AZ8960-019｜アイトス', 'AZ8960-019')
        self.assertEqual(info['match'], 'structured')


class CleanMakerProductNameTest(unittest.TestCase):
    def test_remove_brand_and_color_code_list(self):
        got = msl.clean_maker_product_name(
            'KAZEN(カゼン) 21 22 24… スクラブジャケット半袖(男女兼用)',
            maker_sku='21',
        )
        self.assertEqual(got['cleanedName'], 'スクラブジャケット半袖（男女兼用）')
        self.assertTrue(got['removedDigitEnum'])

    def test_remove_order_unit_keep_sleeve(self):
        got = msl.clean_maker_product_name(
            '男女兼用 ショップコート 七分袖 1枚(ご注文単位1枚)',
        )
        self.assertEqual(got['cleanedName'], '男女兼用 ショップコート 七分袖')
        self.assertTrue(got['removedOrderUnit'])
        self.assertIn('七分袖', got['cleanedName'])

    def test_does_not_strip_all_numbers(self):
        # 単独の型番風数字は一覧とみなして消さない
        got = msl.clean_maker_product_name('シリーズ21 スクラブ半袖')
        self.assertIn('21', got['cleanedName'])
        self.assertFalse(got['removedDigitEnum'])

    def test_parse_listing_applies_cleanup(self):
        parsed = msl.parse_maker_listing_title(
            '男女兼用 ショップコート 七分袖 1枚(ご注文単位1枚)',
            'X-1',
            sku_verified=True,
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['name'], '男女兼用 ショップコート 七分袖')
        self.assertEqual(parsed['cleanedName'], '男女兼用 ショップコート 七分袖')
        self.assertIn('ご注文単位', parsed['originalTitle'] or parsed['title'])


class CandidatePayloadAndAdoptTest(unittest.TestCase):
    def test_candidate_exposes_original_and_cleaned(self):
        html = '''<!DOCTYPE html><html><head>
        <title>男女兼用 ショップコート 七分袖 1枚(ご注文単位1枚)｜店</title>
        </head><body>
        <h1>男女兼用 ショップコート 七分袖 1枚(ご注文単位1枚)</h1>
        <div>商品コード</div><div>SC-100</div>
        </body></html>'''
        hit = {
            'title': '男女兼用 ショップコート 七分袖 1枚(ご注文単位1枚)｜店',
            'url': 'https://example.com/item/sc-100',
            'source': 'test',
            'preferred': True,
            'pageHtml': html,
        }
        with mock.patch.object(msl, '_http_get', return_value=('https://example.com/item/sc-100', html)):
            built = msl._build_candidate_from_hit(
                hit,
                maker_sku='SC-100',
                manufacturer='テスト',
                hints={},
                current_name='旧名',
                current_color='',
            )
        cand = built['candidate']
        self.assertIsNotNone(cand)
        self.assertEqual(cand['name'], '男女兼用 ショップコート 七分袖')
        self.assertEqual(cand['cleanedName'], '男女兼用 ショップコート 七分袖')
        self.assertIn('ご注文単位', cand['originalTitle'])
        self.assertEqual(cand['makerSku'], 'SC-100')
        self.assertTrue(cand['sourceUrl'])

    def test_dedupe_keeps_color_variants_separate(self):
        # 色が違えば名が同じでも別候補（キーに色を含む）
        a = {'name': 'コート', 'color': 'ネイビー', 'position': '', 'plannedRegisteredSku': 'X', 'supplement': '', 'size': ''}
        b = {'name': 'コート', 'color': 'ブラック', 'position': '', 'plannedRegisteredSku': 'X', 'supplement': '', 'size': ''}
        key_a = (
            msl.maker_sku_key(a['name']),
            __import__('product_ledger_paren', fromlist=['color_match_key']).color_match_key(a['color']),
        )
        key_b = (
            msl.maker_sku_key(b['name']),
            __import__('product_ledger_paren', fromlist=['color_match_key']).color_match_key(b['color']),
        )
        self.assertNotEqual(key_a, key_b)

    def test_adopt_stores_source_sku_and_note(self):
        folder, path, c = newdb()
        try:
            result = msl.adopt_maker_correction(
                c,
                manufacturer='アイトス',
                maker_sku='AZ8960-019（駐車場警備）',
                name='ジャケット 1枚(ご注文単位1枚)',
                color='ネイビー',
                source_sku='AZ8960-019（駐車場警備）',
                usage_note='駐車場警備',
                original_title='ジャケット 1枚(ご注文単位1枚)',
            )
            self.assertEqual(result['makerSku'], 'AZ8960-019')
            self.assertEqual(result['searchMakerSku'], 'AZ8960-019')
            self.assertEqual(result['sourceSku'], 'AZ8960-019（駐車場警備）')
            self.assertEqual(result['usageNote'], '駐車場警備')
            self.assertEqual(result['name'], 'ジャケット')
            saved = msl.lookup_saved_correction(
                c, manufacturer='アイトス', maker_sku='AZ8960-019',
            )
            self.assertIsNotNone(saved)
            extra = json.loads(saved['adopted_json'])
            self.assertEqual(extra.get('usageNote'), '駐車場警備')
            self.assertEqual(extra.get('sourceSku'), 'AZ8960-019（駐車場警備）')
        finally:
            c.close()
            folder.cleanup()


class SearchUsesNoteStrippedSkuTest(unittest.TestCase):
    def test_search_strips_note_and_keeps_meta(self):
        folder, path, c = newdb()
        try:
            with mock.patch.object(msl, '_catalog_searches_for', return_value=[]):
                with mock.patch.object(msl, '_ddg_candidates', return_value=[]):
                    result = msl.search_maker_product(
                        c,
                        manufacturer='アイトス',
                        maker_sku='AZ8960-019（駐車場警備）',
                        current_name='旧',
                        current_sku='AZ8960-019（駐車場警備）',
                    )
            self.assertEqual(result['makerSku'], 'AZ8960-019')
            self.assertEqual(result['searchMakerSku'], 'AZ8960-019')
            self.assertEqual(result['sourceSku'], 'AZ8960-019（駐車場警備）')
            self.assertEqual(result['usageNote'], '駐車場警備')
            # 注記分離だけでは現在の登録予定を書き換えない
            self.assertEqual(result['plannedRegisteredSku'], 'AZ8960-019（駐車場警備）')
        finally:
            c.close()
            folder.cleanup()

    def test_bulk_dedupe_uses_search_sku(self):
        k1 = bulk.bulk_lookup_dedupe_key(
            manufacturer='アイトス', maker_sku='AZ8960-019（駐車場警備）',
        )
        k2 = bulk.bulk_lookup_dedupe_key(
            manufacturer='アイトス', maker_sku='AZ8960-019',
        )
        self.assertEqual(k1, k2)


class RowMapProvenanceTest(unittest.TestCase):
    def test_upsert_row_map_keeps_source_and_note(self):
        folder, path, c = newdb()
        try:
            pli.ensure_schema(c)
            row = {
                'rowNo': 1,
                'csv': {
                    'customer': 'テスト顧客',
                    'supplier': 'アイトス',
                    'sourceCode': 'AZ8960-019（駐車場警備）',
                    'code': 'AZ8960-019',
                    'productName': '旧ジャケット',
                    'size': 'M',
                },
                'product': {'code': 'AZ8960-019'},
                'makerCorrection': {
                    'makerSku': 'AZ8960-019',
                    'searchMakerSku': 'AZ8960-019',
                    'sourceSku': 'AZ8960-019（駐車場警備）',
                    'usageNote': '駐車場警備',
                },
                'specProvenance': {
                    'originalName': '旧ジャケット',
                    'sourceCode': 'AZ8960-019（駐車場警備）',
                    'searchMakerSku': 'AZ8960-019',
                    'usageNote': '駐車場警備',
                    'registeredCode': 'AZ8960-019',
                },
                'parenClassification': {},
            }
            # customer/product はダミーID
            pli.upsert_row_map(
                c,
                run_id='test-run',
                file_sha256='abc',
                file_name='t.csv',
                row=row,
                customer_id=1,
                product_id=1,
                customer_product_id=1,
            )
            mapped = c.execute(
                'SELECT csv_product_code, spec_json FROM product_ledger_row_maps ORDER BY id DESC LIMIT 1'
            ).fetchone()
            self.assertIsNotNone(mapped)
            self.assertEqual(mapped['csv_product_code'], 'AZ8960-019（駐車場警備）')
            spec = json.loads(mapped['spec_json'])
            self.assertEqual(spec.get('sourceCode'), 'AZ8960-019（駐車場警備）')
            self.assertEqual(spec.get('usageNote'), '駐車場警備')
            self.assertEqual(spec.get('searchMakerSku'), 'AZ8960-019')
            self.assertEqual(spec.get('registeredCode'), 'AZ8960-019')
        finally:
            c.close()
            folder.cleanup()


class UiFieldPresenceTest(unittest.TestCase):
    def test_panel_html_mentions_original_and_cleaned(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('元掲載名', js)
        self.assertIn('整理後商品名', js)
        self.assertIn('originalTitle', js)
        self.assertIn('cleanedName', js)
        self.assertIn('usageNote', js)


if __name__ == '__main__':
    unittest.main()
