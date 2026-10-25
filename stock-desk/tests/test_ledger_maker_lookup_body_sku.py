"""メーカー情報で補正: 本文商品コード照合・色/サイズ分解（テストDBのみ。本番登録なし）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import maker_sku_lookup as msl  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


KAZEN_WHITE_HTML = '''<!DOCTYPE html><html><head>
<title>APK499胸当てエプロン(無地）（ホワイト／フリー）｜KAZEN公式オンラインストア</title>
</head><body>
<h1>APK499胸当てエプロン(無地）</h1>
<p>(ホワイト／フリー)</p>
<div><span>商品コード</span></div>
<div>APK499-10</div>
<p>関連商品</p>
<a href="/item_detail.php?@uid=other">APK499-5 ブラック</a>
<nav>人気品番 APK499-21</nav>
</body></html>'''

KAZEN_RELATED_ONLY_HTML = '''<!DOCTYPE html><html><head>
<title>別のエプロン商品｜KAZEN公式オンラインストア</title>
</head><body>
<h1>別商品エプロン</h1>
<div><span>商品コード</span></div>
<div>ZZZ999-1</div>
<a href="/item_detail.php?@uid=rel">関連 APK499-10 ホワイト</a>
<p>この商品を見た人は APK499-10 も見ています</p>
</body></html>'''


class BodySkuMatchHelpersTest(unittest.TestCase):
    def test_parse_heading_without_full_sku_when_verified(self):
        parsed = msl.parse_maker_listing_title(
            'APK499胸当てエプロン(無地）（ホワイト／フリー）',
            'APK499-10',
            sku_verified=True,
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['name'], '胸当てエプロン（無地）')
        self.assertEqual(parsed['color'], 'ホワイト')
        self.assertEqual(parsed['size'], 'フリー')
        self.assertFalse(parsed.get('skuInTitle'))

    def test_reject_heading_without_full_sku_when_not_verified(self):
        self.assertIsNone(msl.parse_maker_listing_title(
            'APK499胸当てエプロン(無地）（ホワイト／フリー）',
            'APK499-10',
        ))

    def test_color_size_not_confused_with_colorway(self):
        color_size = msl.parse_maker_listing_title(
            'APK499胸当てエプロン(無地)（ホワイト／フリー）',
            'APK499-10',
            sku_verified=True,
        )
        self.assertEqual(color_size['color'], 'ホワイト')
        self.assertEqual(color_size['size'], 'フリー')

        colorway = msl.parse_maker_listing_title(
            'APK100 スクラブ（黒／ワイン）',
            'APK100-1',
            sku_verified=True,
        )
        self.assertIn('×', colorway['color'])
        self.assertEqual(colorway['size'], '')
        self.assertIn('ブラック', colorway['color'])
        self.assertIn('ワイン', colorway['color'])

    def test_halfwidth_kana_and_no_space_parens(self):
        parsed = msl.parse_maker_listing_title(
            'APK499胸当てｴﾌﾟﾛﾝ(無地)(ﾎﾜｲﾄ/ﾌﾘｰ)',
            'APK499-10',
            sku_verified=True,
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['color'], 'ホワイト')
        self.assertEqual(parsed['size'], 'フリー')
        self.assertIn('エプロン', parsed['name'])
        self.assertIn('無地', parsed['name'])

    def test_structured_sku_match_and_reject_related_only(self):
        title = 'APK499胸当てエプロン(無地）（ホワイト／フリー）｜KAZEN公式オンラインストア'
        info = msl.page_sku_match(KAZEN_WHITE_HTML, title, 'APK499-10')
        self.assertEqual(info['match'], 'structured')
        self.assertTrue(msl.skus_equal(info['matchedValue'], 'APK499-10'))

        bad = msl.page_sku_match(
            KAZEN_RELATED_ONLY_HTML, '別のエプロン商品｜KAZEN', 'APK499-10',
        )
        self.assertEqual(bad['match'], 'none')

    def test_kazen_supplier_brand_hint(self):
        self.assertEqual(msl.manufacturer_hint_from_supplier('カゼン北海道'), 'KAZEN')
        self.assertEqual(
            msl.resolve_manufacturer(manufacturer='', supplier='（株）KAZEN販売'),
            'KAZEN',
        )


class KazenLookupSearchTest(unittest.TestCase):
    def test_body_code_match_yields_candidate(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'APK499胸当てエプロン(無地）（ホワイト／フリー）｜KAZEN',
                'url': 'https://kazen-ec.com/item_detail.php?@uid=white',
                'source': 'kazen_ec',
                'preferred': True,
                'manufacturer': 'KAZEN',
            }

            def fake_get(url, timeout=10):
                if 'uid=white' in url:
                    return url, KAZEN_WHITE_HTML
                return url, '<html></html>'

            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]), \
                    mock.patch.object(msl, '_http_get', side_effect=fake_get):
                res = msl.search_maker_product(
                    c,
                    manufacturer='KAZEN',
                    maker_sku='APK499-10',
                    current_name='エプロン',
                    current_color='',
                    source_name='エプロン',
                    supplier='カゼン',
                )
            self.assertEqual(res['status'], 'ok')
            self.assertEqual(res['statusLabel'], '補正候補取得できた')
            cand = res['candidates'][0]
            self.assertEqual(cand['name'], '胸当てエプロン（無地）')
            self.assertEqual(cand['color'], 'ホワイト')
            self.assertEqual(cand['size'], 'フリー')
            self.assertEqual(cand.get('skuMatch'), 'structured')
            # CSVサイズ上書き用ではない（採用APIは name/color/supplement）
            self.assertIn('サイズ', res.get('note') or '')
        finally:
            c.close()
            folder.cleanup()

    def test_related_link_sku_not_adopted(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': '別のエプロン商品',
                'url': 'https://kazen-ec.com/item_detail.php?@uid=other',
                'source': 'kazen_ec',
                'preferred': True,
                'manufacturer': 'KAZEN',
            }

            def fake_get(url, timeout=10):
                return url, KAZEN_RELATED_ONLY_HTML

            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]), \
                    mock.patch.object(msl, '_http_get', side_effect=fake_get):
                res = msl.search_maker_product(
                    c,
                    manufacturer='KAZEN',
                    maker_sku='APK499-10',
                    source_name='エプロン',
                )
            self.assertEqual(res['status'], 'sku_mismatch')
            self.assertEqual(res['statusLabel'], '品番不一致')
            self.assertEqual(res['candidates'], [])
        finally:
            c.close()
            folder.cleanup()

    def test_parse_incomplete_status_exposes_url_and_missing(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            html = (
                '<html><head><title>APK499-10</title></head>'
                '<body><div>商品コード</div><div>APK499-10</div></body></html>'
            )
            hit = {
                'title': 'APK499-10',
                'url': 'https://kazen-ec.com/item_detail.php?@uid=bare',
                'source': 'kazen_ec', 'preferred': True, 'manufacturer': 'KAZEN',
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]), \
                    mock.patch.object(msl, '_http_get', return_value=(hit['url'], html)):
                res = msl.search_maker_product(
                    c, manufacturer='KAZEN', maker_sku='APK499-10',
                )
            self.assertEqual(res['status'], 'parse_incomplete')
            self.assertEqual(res['statusLabel'], '品番一致したが項目抽出不可')
            self.assertTrue(res.get('matchedPages'))
            page = res['matchedPages'][0]
            self.assertEqual(page.get('sourceUrl'), hit['url'])
            self.assertIn('name', page.get('missingFields') or [])
        finally:
            c.close()
            folder.cleanup()

    def test_status_labels_distinguished(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            with mock.patch.object(msl, '_ddg_candidates', side_effect=OSError('down')), \
                    mock.patch.object(msl, '_catalog_searches_for', side_effect=OSError('down')):
                fail = msl.search_maker_product(
                    c, manufacturer='KAZEN', maker_sku='APK499-10',
                )
            self.assertEqual(fail['status'], 'fetch_failed')
            self.assertEqual(fail['statusLabel'], '検索結果取得不可')

            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[]):
                none = msl.search_maker_product(
                    c, manufacturer='KAZEN', maker_sku='APK499-10',
                )
            self.assertEqual(none['status'], 'no_match')
        finally:
            c.close()
            folder.cleanup()


class MakerLookupUiBodySkuTest(unittest.TestCase):
    def test_inline_panel_and_status_copy(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('skuMakerLookupRowHtml', js)
        self.assertIn('scrollMakerLookupIntoView', js)
        self.assertIn('data-sku-maker-lookup', js)
        self.assertIn('検索中…', js)
        self.assertIn('matchedPages', js)
        self.assertIn('不足項目', js)
        self.assertIn('検索結果取得不可', js)


if __name__ == '__main__':
    unittest.main()
