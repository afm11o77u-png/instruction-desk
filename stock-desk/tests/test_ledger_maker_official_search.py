"""メーカー公式／公式ショップ品番検索（fixtureのみ。本番DB・在庫更新なし）。

対象: Style-ist(サーヴォ) / ナガイレーベン / KAZEN / シーズン
共有経路: search_maker_product → _build_candidate_from_hit → adopt
"""
from __future__ import annotations

import json
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


STYLEIST_SJAU2007_HTML = '''<!DOCTYPE html><html><head>
<title>【ヒッコリー】ショップコート | サーヴォ公式</title>
</head><body>
<h1>【ヒッコリー】ショップコート</h1>
<!--関連商品-->
<a href="/item/OTHER_999.html">関連 SJAU-9999-Z9</a>
<table class="AE_item_head">
  <tr><th class="notranslate">品番</th>
    <td class="notranslate"><span>SJAU-2007-C6</span>
      <span class="AE_itemlist_hide_code"><span class="AE_item_code">SJAU_2007_C6</span></span>
    </td></tr>
  <!--<tr><th>商品名</th><td><span>商品名</span></td></tr>-->
</table>
<div>・ホワイト*ブラック、ホワイト*ブラウン、ホワイト*ネイビーの3色展開</div>
<table class="AE_item_free">
  <tr><th>素材</th><td>ヒッコリー</td></tr>
  <tr><th>カラー名</th><td>ホワイト*ブラウン</td></tr>
</table>
</body></html>'''

STYLEIST_SJAU1701_HTML = '''<!DOCTYPE html><html><head>
<title>【ヒッコリー】ショップコート | サーヴォ公式</title>
</head><body>
<h1>【ヒッコリー】ショップコート</h1>
<table>
  <tr><th>品番</th><td><span>SJAU-1701-A6</span>
    <span class="AE_item_code">SJAU_1701_A6</span></td></tr>
</table>
<table class="AE_item_free">
  <tr><th>カラー名</th><td>ベージュ白</td></tr>
</table>
</body></html>'''

STYLEIST_SEARCH_HTML = '''<!DOCTYPE html><html><head>
<title>「SJAU-2007-C6」の検索結果</title></head><body>
<a href="https://www.style-ist.jp/item/SJAU_2007_C6.html">SJAU-2007-C6</a>
<a href="/item/OTHER_NOISE.html">別商品</a>
</body></html>'''

NAGAI_RF5127_HTML = '''<!DOCTYPE html><html><head>
<title>RF-5127　/ 男子スクラブ｜NAGAILEBEN</title>
<link rel="canonical" href="https://www.nagaileben.net/i/RF-5127" />
</head><body>
<h1><span>RF-5127　/ 男子スクラブ</span></h1>
<ul class="sysImageList">
  <li><img alt="ネイビー＋ターコイズ" src="/img/rf5127-1.jpg"></li>
  <li><img alt="ネイビー＋ロイヤルブルー" src="/img/rf5127-2.jpg"></li>
  <li><img alt="Back style" src="/img/rf5127-3.jpg"></li>
</ul>
<div class="color_box_wrapper"><div class="color_box bg_navy_royalblue"></div>ネイビー＋ロイヤルブルー(94)</div>
<div class="color_box_wrapper"><div class="color_box bg_navy_turquoise"></div>ネイビー＋ターコイズ(95)</div>
<p>関連</p><a href="/i/RF-9999">RF-9999 別商品</a>
</body></html>'''

NAGAI_SEARCH_API_HTML = '''
<div class="sysItemList"><ul class="sysThumbnail">
<li data-item-id="RF-5127">
  <a href="https://www.nagaileben.net/i/RF-5127">
    <img alt="RF-5127　/ 男子スクラブ" src="/img/rf5127-1.jpg">
  </a>
</li>
</ul></div>
'''

KAZEN_KZN715_HTML = '''<!DOCTYPE html><html><head>
<title>スクラブジャケット半袖（男女兼用）（ネイビー／SS）｜KAZEN公式</title>
</head><body>
<h1>スクラブジャケット半袖（男女兼用）</h1>
<p>（ネイビー／SS）</p>
<dl class="code"><dt>商品コード</dt><dd>KZN715-28 SS</dd></dl>
<div>カラーピッカーUIは参考にしない</div>
<a href="/item_detail.php?@uid=rel">関連 KZN715-99</a>
</body></html>'''

KAZEN_SEARCH_HIT_HTML = '''<!DOCTYPE html><html><head><title>商品リスト</title></head><body>
<a href="item_detail.php?@uid=kzn715navy">KZN715 スクラブジャケット半袖</a>
</body></html>'''

SEASON_FT5313_JSON = {
    'id': 1,
    'title': '男性用パンツ【ブルー】［制電］［抗菌］［厚手］FT5313 食品工場白衣 工場白衣 食品作業着 食品工場ユニフォーム',
    'handle': 'ft5313',
    'tags': ['制電', '厚手', '抗菌', '男性用'],
    'variants': [
        {'id': 1, 'title': 'S', 'sku': 'FT5313S', 'option1': 'S'},
        {'id': 2, 'title': 'M', 'sku': 'FT5313M', 'option1': 'M'},
        {'id': 3, 'title': 'L', 'sku': 'FT5313L', 'option1': 'L'},
    ],
}


class HelperOfficialSitesTest(unittest.TestCase):
    def test_supplier_brand_hints(self):
        self.assertEqual(msl.manufacturer_hint_from_supplier('サーヴォ北海道'), 'サーヴォ')
        self.assertEqual(msl.manufacturer_hint_from_supplier('ナガイレーベン販売'), 'ナガイレーベン')
        self.assertEqual(msl.manufacturer_hint_from_supplier('シーズン静岡'), 'シーズン')
        self.assertEqual(
            msl.resolve_manufacturer(manufacturer='', supplier='雑貨問屋'),
            '',
        )

    def test_unknown_maker_skips_catalog_fanout(self):
        with mock.patch.object(msl, '_midori_ec_candidates') as midori, \
                mock.patch.object(msl, '_kazen_ec_candidates') as kazen:
            hits = msl._catalog_searches_for(manufacturer='', query='ABC-1', maker_sku='ABC-1')
        self.assertEqual(hits, [])
        midori.assert_not_called()
        kazen.assert_not_called()

    def test_season_base_sku_no_blanket_letter_strip(self):
        self.assertEqual(msl._season_base_sku('FT5313S'), 'FT5313')
        self.assertEqual(msl._season_base_sku('FT5313LL'), 'FT5313')
        # 末尾英字の無条件削除はしない（サイズ接尾以外）
        self.assertEqual(msl._season_base_sku('ABC12X'), 'ABC12X')

    def test_styleist_color_and_table(self):
        info = msl.extract_product_info_fields(STYLEIST_SJAU2007_HTML)
        self.assertTrue(msl.skus_equal(info['sku'], 'SJAU-2007-C6'))
        self.assertEqual(info['color'], 'ホワイト×ブラウン')
        # コメント内の「商品名」プレースホルダは採用しない
        self.assertNotEqual(info.get('name'), '商品名')

    def test_nagaileben_colorways(self):
        ways = msl.extract_nagaileben_colorways(NAGAI_RF5127_HTML)
        colors = {w['color'] for w in ways}
        self.assertTrue(any('ロイヤルブルー' in c for c in colors))
        self.assertTrue(any('ターコイズ' in c for c in colors))
        codes = {w.get('colorCode') for w in ways}
        self.assertIn('94', codes)
        self.assertIn('95', codes)

    def test_season_title_cleanup(self):
        parsed = msl.parse_maker_listing_title(
            SEASON_FT5313_JSON['title'], 'FT5313', sku_verified=True,
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['name'], '男性用パンツ')
        self.assertEqual(parsed['color'], 'ブルー')
        self.assertIn('制電', parsed.get('features') or [])
        self.assertNotIn('食品工場白衣', parsed['name'])

    def test_kazen_code_with_browse_size(self):
        info = msl.extract_product_info_fields(KAZEN_KZN715_HTML)
        self.assertTrue(msl.skus_equal(info['sku'], 'KZN715-28'))
        self.assertEqual(info.get('size'), 'SS')
        parsed = msl.parse_maker_listing_title(
            'スクラブジャケット半袖（男女兼用）（ネイビー／SS）',
            'KZN715-28',
            sku_verified=True,
        )
        self.assertEqual(parsed['name'], 'スクラブジャケット半袖（男女兼用）')
        self.assertEqual(parsed['color'], 'ネイビー')
        self.assertEqual(parsed['size'], 'SS')


class StyleistLookupTest(unittest.TestCase):
    def test_sjau2007_official_to_adopt(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'SJAU-2007-C6',
                'url': 'https://www.style-ist.jp/item/SJAU_2007_C6.html',
                'source': 'styleist_ec',
                'preferred': True,
                'manufacturer': 'サーヴォ',
                'pageHtml': STYLEIST_SJAU2007_HTML,
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]):
                res = msl.search_maker_product(
                    c,
                    manufacturer='サーヴォ',
                    maker_sku='SJAU-2007-C6',
                    current_name='ショップコート',
                    current_color='',
                    source_name='ショップコート',
                    supplier='サーヴォ',
                )
            self.assertEqual(res['status'], 'ok')
            cand = res['candidates'][0]
            self.assertIn('ショップコート', cand['name'])
            self.assertEqual(cand['color'], 'ホワイト×ブラウン')
            self.assertTrue(msl.skus_equal(cand['makerSku'], 'SJAU-2007-C6'))
            adopted = msl.adopt_maker_correction(
                c,
                manufacturer='サーヴォ',
                maker_sku='SJAU-2007-C6',
                name=cand['name'],
                color=cand['color'],
                source_url=cand['sourceUrl'],
                current_color='',
            )
            self.assertTrue(adopted.get('ok') or adopted.get('adopted') or adopted.get('name'))
            saved = msl.lookup_saved_correction(
                c, manufacturer='サーヴォ', maker_sku='SJAU-2007-C6',
            )
            self.assertIsNotNone(saved)
            self.assertEqual(saved['adopted_color'], 'ホワイト×ブラウン')
            self.assertIn('style-ist.jp', saved.get('source_url') or '')
        finally:
            c.close()
            folder.cleanup()

    def test_sjau1701_single_color(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'SJAU-1701-A6',
                'url': 'https://www.style-ist.jp/item/SJAU_1701_A6.html',
                'source': 'styleist_ec',
                'preferred': True,
                'manufacturer': 'サーヴォ',
                'pageHtml': STYLEIST_SJAU1701_HTML,
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]):
                res = msl.search_maker_product(
                    c, manufacturer='サーヴォ', maker_sku='SJAU-1701-A6',
                )
            cand = res['candidates'][0]
            self.assertEqual(cand['color'], 'ベージュ白')
        finally:
            c.close()
            folder.cleanup()

    def test_styleist_search_collector(self):
        def fake_get(url, timeout=10):
            if 'item_list' in url:
                return url, STYLEIST_SEARCH_HTML
            return url, STYLEIST_SJAU2007_HTML

        with mock.patch.object(msl, '_http_get', side_effect=fake_get):
            hits = msl._styleist_ec_candidates('SJAU-2007-C6', maker_sku='SJAU-2007-C6')
        urls = [h['url'] for h in hits]
        self.assertTrue(any('SJAU_2007_C6' in u for u in urls))
        self.assertFalse(any('OTHER_NOISE' in u for u in urls))


class NagailebenLookupTest(unittest.TestCase):
    def test_colorway_choice_when_ambiguous(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'RF-5127 / 男子スクラブ',
                'url': 'https://www.nagaileben.net/i/RF-5127',
                'source': 'nagaileben_ec',
                'preferred': True,
                'manufacturer': 'ナガイレーベン',
                'pageHtml': NAGAI_RF5127_HTML,
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]):
                res = msl.search_maker_product(
                    c,
                    manufacturer='ナガイレーベン',
                    maker_sku='RF-5127',
                    current_color='',
                    source_name='男子スクラブ',
                )
            self.assertEqual(res['status'], 'colorway_choice')
            self.assertGreaterEqual(len(res['candidates']), 2)
            names = {c['name'] for c in res['candidates']}
            self.assertTrue(any('男子スクラブ' in n for n in names))
            colors = {c['color'] for c in res['candidates']}
            self.assertTrue(any('ロイヤルブルー' in x for x in colors))
            self.assertTrue(any('ターコイズ' in x for x in colors))
        finally:
            c.close()
            folder.cleanup()

    def test_colorway_matched_from_csv_color(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'RF-5127 / 男子スクラブ',
                'url': 'https://www.nagaileben.net/i/RF-5127',
                'source': 'nagaileben_ec',
                'preferred': True,
                'manufacturer': 'ナガイレーベン',
                'pageHtml': NAGAI_RF5127_HTML,
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]):
                res = msl.search_maker_product(
                    c,
                    manufacturer='ナガイレーベン',
                    maker_sku='RF-5127',
                    current_color='ネイビー×ロイヤルブルー',
                )
            self.assertEqual(res['status'], 'ok')
            self.assertEqual(len(res['candidates']), 1)
            self.assertIn('ロイヤルブルー', res['candidates'][0]['color'])
        finally:
            c.close()
            folder.cleanup()

    def test_search_api_extracts_product_link(self):
        with mock.patch.object(msl, '_nagaileben_search_api_html', return_value=NAGAI_SEARCH_API_HTML), \
                mock.patch.object(msl, '_http_get', side_effect=AssertionError('should not GET list')):
            hits = msl._nagaileben_ec_candidates('RF-5127', maker_sku='RF-5127')
        self.assertTrue(any(h['url'].endswith('/i/RF-5127') for h in hits))


class KazenOfficialImproveTest(unittest.TestCase):
    def test_kzn715_heading_and_code_size(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'スクラブジャケット半袖（男女兼用）（ネイビー／SS）',
                'url': 'https://kazen-ec.com/item_detail.php?@uid=kzn715navy',
                'source': 'kazen_ec',
                'preferred': True,
                'manufacturer': 'KAZEN',
                'pageHtml': KAZEN_KZN715_HTML,
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]):
                res = msl.search_maker_product(
                    c,
                    manufacturer='KAZEN',
                    maker_sku='KZN715-28',
                    current_name='ジャケット',
                    current_color='',
                    # 旧CSVサイズは別管理。SSは表示のみ
                    current_sku='KZN715-28',
                )
            self.assertEqual(res['status'], 'ok')
            cand = res['candidates'][0]
            self.assertEqual(cand['name'], 'スクラブジャケット半袖（男女兼用）')
            self.assertEqual(cand['color'], 'ネイビー')
            self.assertEqual(cand.get('size'), 'SS')
            self.assertEqual(cand['plannedRegisteredSku'], 'KZN715-28')
            self.assertIn('サイズ', res.get('note') or '')
        finally:
            c.close()
            folder.cleanup()

    def test_official_no_hit_status(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[]):
                res = msl.search_maker_product(
                    c, manufacturer='KAZEN', maker_sku='KZN715-28',
                )
            self.assertEqual(res['status'], 'official_no_hit')
            self.assertEqual(res['statusLabel'], '公式サイトに該当なし')
            self.assertNotEqual(res['statusLabel'], '候補なし')
        finally:
            c.close()
            folder.cleanup()


class SeasonLookupTest(unittest.TestCase):
    def test_ft5313_from_product_json(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': SEASON_FT5313_JSON['title'],
                'url': 'https://shop.season-shizuoka.jp/products/ft5313',
                'source': 'season_shop',
                'preferred': True,
                'manufacturer': 'シーズン',
                'handle': 'ft5313',
                'pageJson': SEASON_FT5313_JSON,
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]):
                res = msl.search_maker_product(
                    c,
                    manufacturer='シーズン',
                    maker_sku='FT5313',
                    current_name='パンツ',
                    current_color='',
                )
            self.assertEqual(res['status'], 'ok')
            cand = res['candidates'][0]
            self.assertEqual(cand['name'], '男性用パンツ')
            self.assertEqual(cand['color'], 'ブルー')
            feats = cand.get('features') or []
            self.assertTrue(any(f in feats for f in ('制電', '抗菌', '厚手')))
            self.assertNotIn('食品工場白衣', cand['name'])
        finally:
            c.close()
            folder.cleanup()

    def test_size_variant_sku_maps_to_base_without_blanket_strip(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': SEASON_FT5313_JSON['title'],
                'url': 'https://shop.season-shizuoka.jp/products/ft5313',
                'source': 'season_shop',
                'preferred': True,
                'manufacturer': 'シーズン',
                'handle': 'ft5313',
                'pageJson': SEASON_FT5313_JSON,
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]):
                res = msl.search_maker_product(
                    c, manufacturer='シーズン', maker_sku='FT5313S',
                )
            self.assertEqual(res['status'], 'ok')
            # 登録予定は呼び出し側の品番を維持（サイズSKUの機械削除で台帳を壊さない）
            self.assertTrue(res['candidates'])
        finally:
            c.close()
            folder.cleanup()


class AdoptedUrlPrefetchTest(unittest.TestCase):
    def test_saved_url_is_refetched_and_reverified(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            msl.save_adopted_correction(
                c,
                manufacturer='サーヴォ',
                maker_sku='SJAU-2007-C6',
                adopted_name='旧名',
                adopted_color='ホワイト×ブラウン',
                source_url='https://www.style-ist.jp/item/SJAU_2007_C6.html',
            )
            calls = []

            def fake_get(url, timeout=10):
                calls.append(url)
                if 'SJAU_2007_C6' in url:
                    return url, STYLEIST_SJAU2007_HTML
                return url, '<html></html>'

            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[]), \
                    mock.patch.object(msl, '_http_get', side_effect=fake_get):
                res = msl.search_maker_product(
                    c, manufacturer='サーヴォ', maker_sku='SJAU-2007-C6',
                )
            self.assertTrue(any('SJAU_2007_C6' in u for u in calls))
            # 再照合できた live 候補、または保存候補
            self.assertTrue(res['candidates'])
            live = [c for c in res['candidates'] if not c.get('fromSaved')]
            if live:
                self.assertEqual(live[0]['color'], 'ホワイト×ブラウン')
                self.assertIn('ショップコート', live[0]['name'])
        finally:
            c.close()
            folder.cleanup()


class UrlPathSharesExtractTest(unittest.TestCase):
    def test_from_url_uses_same_extract(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            with mock.patch.object(
                msl, '_http_get',
                return_value=(
                    'https://www.style-ist.jp/item/SJAU_2007_C6.html',
                    STYLEIST_SJAU2007_HTML,
                ),
            ):
                res = msl.lookup_maker_product_from_url(
                    c,
                    url='https://www.style-ist.jp/item/SJAU_2007_C6.html',
                    manufacturer='サーヴォ',
                    maker_sku='SJAU-2007-C6',
                )
            self.assertIn(res['status'], ('ok', 'multiple'))
            cand = res['candidates'][0]
            self.assertEqual(cand['color'], 'ホワイト×ブラウン')
        finally:
            c.close()
            folder.cleanup()


class UiOfficialStatusTest(unittest.TestCase):
    def test_status_labels_and_scroll_preserve(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('official_no_hit', js)
        self.assertIn('colorway_choice', js)
        self.assertIn('公式サイトに該当なし', js)
        self.assertIn('配色の選択が必要', js)
        self.assertIn('preserveScroll', js)
        self.assertIn('scrollMakerLookupIntoView', js)
        self.assertIn('captureLedgerScroll', js)


if __name__ == '__main__':
    unittest.main()
