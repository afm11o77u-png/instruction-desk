"""メーカー情報で補正: アイトス基礎品番＋色番照合（fixtureのみ。本番検索・登録なし）。"""
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


AITOZ_60520_HTML = '''<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>アイトス 作業服・ユニフォーム</title>
</head><body>
<div><span id="lblMenuItemCode">AZ-60520   </span></div>
<h4><span id="goodsnumber" class="bullet">AZ-60520   </span>
<span id="goodsname">ワークパンツ（ノータック）（男女兼用）</span></h4>
<div class="specbox" id="itemDetailColor">
<span>カラーバリエーション</span>
<table>
<tr><th>003シルバーグレー</th></tr>
<tr><th>014チャコール</th></tr>
<tr><th>018ディープネイビー</th></tr>
<tr><th>025カーキ</th></tr>
<tr><th>086アイアンブルー</th></tr>
</table>
</div>
<a href="../item/detail.aspx?hinban=60501">関連 カーゴパンツ</a>
<a href="../item/detail.aspx?hinban=60521">関連 別商品 019イエロー</a>
<p>関連商品カラー 019 は別SKU</p>
</body></html>'''

AITOZ_8960_HTML = '''<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>アイトス 作業服</title>
</head><body>
<span id="lblMenuItemCode">AZ-8960   </span>
<span id="goodsnumber">AZ-8960   </span>
<span id="goodsname">高視認性防水防寒コート（男女兼用）</span>
<div id="itemDetailColor">
<span>カラーバリエーション</span>
<table>
<tr><th>019イエロー×ネイビー</th></tr>
<tr><th>063オレンジ×ネイビー</th></tr>
</table>
</div>
<nav>人気 018ディープネイビー は別商品</nav>
</body></html>'''

AITOZ_RELATED_COLOR_ONLY_HTML = '''<!DOCTYPE html><html><head>
<title>別商品</title></head><body>
<span id="goodsnumber">AZ-99999</span>
<span id="goodsname">別のパンツ</span>
<div id="itemDetailColor">
<table><tr><th>003グレー</th></tr></table>
</div>
<a href="other">関連 AZ-60520 018ディープネイビー</a>
<p>この商品を見た人は 018 も見ています</p>
</body></html>'''


class AitozSkuSplitTest(unittest.TestCase):
    def test_split_hyphen_and_compact(self):
        a = msl.split_maker_sku_parts('AZ-60520-018', manufacturer='アイトス')
        self.assertEqual(a['rule'], 'aitoz')
        self.assertEqual(a['base'], 'AZ-60520')
        self.assertEqual(a['colorCode'], '018')
        self.assertEqual(a['hinban'], '60520')

        b = msl.split_maker_sku_parts('AZ8960-019', manufacturer='アイトス㈱')
        self.assertEqual(b['base'], 'AZ-8960')
        self.assertEqual(b['colorCode'], '019')

    def test_leading_zero_preserved(self):
        p = msl.split_maker_sku_parts('AZ-60520-018', manufacturer='アイトス')
        self.assertEqual(p['colorCode'], '018')
        self.assertTrue(msl.color_codes_equal('018', p['colorCode']))
        self.assertFalse(msl.color_codes_equal('018', '18'))

    def test_az_hyphen_normalization(self):
        self.assertTrue(msl.aitoz_bases_equal('AZ8960', 'AZ-8960'))
        self.assertTrue(msl.aitoz_bases_equal('AZ-60520', 'AZ60520'))
        self.assertFalse(msl.aitoz_bases_equal('AZ-60520', 'AZ-60521'))

    def test_no_unconditional_split_other_makers(self):
        kazen = msl.split_maker_sku_parts('APK499-10', manufacturer='KAZEN')
        self.assertEqual(kazen['rule'], '')
        self.assertEqual(kazen['base'], 'APK499-10')
        self.assertEqual(kazen['colorCode'], '')

        midori = msl.split_maker_sku_parts('VEH30B下', manufacturer='ミドリ安全')
        self.assertEqual(midori['colorCode'], '')
        self.assertEqual(midori['base'], 'VEH30B下')


class AitozPageMatchTest(unittest.TestCase):
    def test_base_and_color_match_60520(self):
        info = msl.page_sku_match(
            AITOZ_60520_HTML, 'アイトス', 'AZ-60520-018',
            manufacturer='アイトス',
            page_url='https://azweb.aitoz.co.jp/item/detail.aspx?hinban=60520',
        )
        self.assertEqual(info['match'], 'base_color')
        self.assertEqual(info['colorCode'], '018')
        self.assertEqual(info['colorName'], 'ディープネイビー')
        self.assertEqual(info['pageName'], 'ワークパンツ（ノータック）（男女兼用）')
        self.assertEqual(info['evidenceLabel'], '基礎品番一致＋色番018一致')
        self.assertTrue(msl.aitoz_bases_equal(info['matchedValue'], 'AZ-60520'))

    def test_compact_sku_matches_hyphen_page(self):
        info = msl.page_sku_match(
            AITOZ_8960_HTML, 'アイトス', 'AZ8960-019',
            manufacturer='アイトス',
            page_url='https://azweb.aitoz.co.jp/item/detail.aspx?itemcode=AZ-8960',
        )
        self.assertEqual(info['match'], 'base_color')
        self.assertEqual(info['colorCode'], '019')
        self.assertIn('イエロー', info['colorName'])
        self.assertIn('ネイビー', info['colorName'])
        self.assertEqual(info['pageName'], '高視認性防水防寒コート（男女兼用）')

    def test_missing_color_is_base_only_not_guessed(self):
        info = msl.page_sku_match(
            AITOZ_60520_HTML, 'アイトス', 'AZ-60520-999',
            manufacturer='アイトス',
            page_url='https://azweb.aitoz.co.jp/item/detail.aspx?hinban=60520',
        )
        self.assertEqual(info['match'], 'base_only')
        self.assertEqual(info['evidenceLabel'], '基礎品番一致・色番未確認')
        self.assertEqual(info['colorName'], '')

    def test_related_link_color_not_enough(self):
        info = msl.page_sku_match(
            AITOZ_RELATED_COLOR_ONLY_HTML, '別商品', 'AZ-60520-018',
            manufacturer='アイトス',
            page_url='https://azweb.aitoz.co.jp/item/detail.aspx?hinban=99999',
        )
        self.assertEqual(info['match'], 'none')

    def test_full_sku_still_works_for_kazen(self):
        html = (
            '<html><head><title>APK499-10 白</title></head>'
            '<body><div>商品コード</div><div>APK499-10</div></body></html>'
        )
        info = msl.page_sku_match(html, 'APK499-10 白', 'APK499-10', manufacturer='KAZEN')
        self.assertEqual(info['match'], 'structured')


class AitozLookupSearchTest(unittest.TestCase):
    def test_single_lookup_60520(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'AZ-60520 アイトス',
                'url': 'https://azweb.aitoz.co.jp/item/detail.aspx?hinban=60520',
                'source': 'aitoz_direct',
                'preferred': True,
                'manufacturer': 'アイトス',
            }

            def fake_get(url, timeout=10):
                if '60520' in url:
                    return url, AITOZ_60520_HTML
                return url, '<html></html>'

            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]), \
                    mock.patch.object(msl, '_http_get', side_effect=fake_get):
                res = msl.search_maker_product(
                    c,
                    manufacturer='アイトス',
                    maker_sku='AZ-60520-018',
                    current_name='パンツ',
                    current_color='',
                    current_sku='AZ-60520-018',
                    source_name='ワークパンツ',
                    supplier='アイトス㈱',
                )
            self.assertEqual(res['status'], 'ok')
            cand = res['candidates'][0]
            self.assertEqual(cand['name'], 'ワークパンツ（ノータック）（男女兼用）')
            self.assertEqual(cand['color'], 'ディープネイビー')
            self.assertEqual(cand['makerColorCode'], '018')
            self.assertEqual(cand['plannedRegisteredSku'], 'AZ-60520-018')
            self.assertEqual(cand['skuMatch'], 'base_color')
            self.assertEqual(cand['skuMatchEvidence'], '基礎品番一致＋色番018一致')
        finally:
            c.close()
            folder.cleanup()

    def test_single_lookup_8960_hyphen_absorb(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'AZ-8960 アイトス',
                'url': 'https://azweb.aitoz.co.jp/item/detail.aspx?itemcode=AZ-8960',
                'source': 'aitoz_direct',
                'preferred': True,
                'manufacturer': 'アイトス',
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]), \
                    mock.patch.object(
                        msl, '_http_get',
                        return_value=(hit['url'], AITOZ_8960_HTML),
                    ):
                res = msl.search_maker_product(
                    c,
                    manufacturer='アイトス',
                    maker_sku='AZ8960-019',
                    current_sku='AZ8960-019',
                    supplier='アイトス',
                )
            self.assertEqual(res['status'], 'ok')
            cand = res['candidates'][0]
            self.assertEqual(cand['name'], '高視認性防水防寒コート（男女兼用）')
            self.assertIn('イエロー', cand['color'])
            self.assertEqual(cand['makerColorCode'], '019')
            self.assertEqual(cand['plannedRegisteredSku'], 'AZ8960-019')
            self.assertEqual(cand['skuMatchEvidence'], '基礎品番一致＋色番019一致')
        finally:
            c.close()
            folder.cleanup()

    def test_url_hit_uses_same_match_path(self):
        """URL指定相当: 同一 _build_candidate_from_hit 経路。"""
        hit = {
            'title': 'AZ-60520',
            'url': 'https://azweb.aitoz.co.jp/item/detail.aspx?hinban=60520',
            'source': 'url',
            'preferred': True,
            'manufacturer': 'アイトス',
        }
        with mock.patch.object(
            msl, '_http_get',
            return_value=(hit['url'], AITOZ_60520_HTML),
        ):
            built = msl._build_candidate_from_hit(
                hit,
                maker_sku='AZ-60520-018',
                manufacturer='アイトス',
                hints={'positions': [], 'colors': []},
                current_name='旧名',
                current_color='',
                current_sku='AZ-60520-018',
            )
        self.assertIsNone(built.get('rejection'))
        cand = built['candidate']
        self.assertEqual(cand['skuMatch'], 'base_color')
        self.assertEqual(cand['color'], 'ディープネイビー')
        self.assertEqual(cand['plannedRegisteredSku'], 'AZ-60520-018')

    def test_base_only_keeps_color_unconfirmed(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            hit = {
                'title': 'AZ-60520',
                'url': 'https://azweb.aitoz.co.jp/item/detail.aspx?hinban=60520',
                'source': 'aitoz_direct',
                'preferred': True,
                'manufacturer': 'アイトス',
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[hit]), \
                    mock.patch.object(
                        msl, '_http_get',
                        return_value=(hit['url'], AITOZ_60520_HTML),
                    ):
                res = msl.search_maker_product(
                    c,
                    manufacturer='アイトス',
                    maker_sku='AZ-60520-999',
                    current_sku='AZ-60520-999',
                )
            self.assertEqual(res['status'], 'ok')
            cand = res['candidates'][0]
            self.assertEqual(cand['skuMatch'], 'base_only')
            self.assertEqual(cand['skuMatchEvidence'], '基礎品番一致・色番未確認')
            self.assertEqual(cand['color'], '')
            self.assertIn('color', cand.get('missingFields') or [])
            # 別色（018）を推測しない
            self.assertNotEqual(cand['color'], 'ディープネイビー')
        finally:
            c.close()
            folder.cleanup()

    def test_adopt_keeps_full_sku_and_color_code(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            adopted = msl.adopt_maker_correction(
                c,
                manufacturer='アイトス',
                maker_sku='AZ-60520-018',
                name='ワークパンツ（ノータック）（男女兼用）',
                color='ディープネイビー',
                planned_registered_sku='AZ-60520-018',
                source_url='https://azweb.aitoz.co.jp/item/detail.aspx?hinban=60520',
                maker_color_code='018',
                base_sku_override='AZ-60520',
            )
            self.assertEqual(adopted['plannedRegisteredSku'], 'AZ-60520-018')
            self.assertEqual(adopted['makerColorCode'], '018')
            self.assertEqual(adopted['baseSku'], 'AZ-60520')
            # 色番違いを同一キーへ統合しない
            other = msl.adopt_maker_correction(
                c,
                manufacturer='アイトス',
                maker_sku='AZ-60520-025',
                name='ワークパンツ（ノータック）（男女兼用）',
                color='カーキ',
                planned_registered_sku='AZ-60520-025',
                maker_color_code='025',
                base_sku_override='AZ-60520',
            )
            self.assertEqual(other['plannedRegisteredSku'], 'AZ-60520-025')
            saved018 = msl.lookup_saved_correction(
                c, manufacturer='アイトス', maker_sku='AZ-60520-018',
            )
            saved025 = msl.lookup_saved_correction(
                c, manufacturer='アイトス', maker_sku='AZ-60520-025',
            )
            self.assertIsNotNone(saved018)
            self.assertIsNotNone(saved025)
            self.assertEqual(saved018['adopted_color'], 'ディープネイビー')
            self.assertEqual(saved025['adopted_color'], 'カーキ')
        finally:
            c.close()
            folder.cleanup()

    def test_search_stages_include_base_sku(self):
        stages = msl.build_search_query_stages(
            manufacturer='アイトス',
            maker_sku='AZ-60520-018',
            hints={'positions': [], 'colors': []},
        )
        blob = ' '.join(stages)
        self.assertIn('AZ-60520-018', blob)
        self.assertIn('AZ-60520', blob)
        self.assertIn('aitoz', blob.lower())

    def test_direct_candidates_from_sku(self):
        hits = msl._aitoz_direct_candidates('AZ8960-019', manufacturer='アイトス')
        self.assertTrue(hits)
        urls = ' '.join(h['url'] for h in hits)
        self.assertIn('hinban=8960', urls)
        self.assertIn('itemcode=', urls)


class AitozUiEvidenceTest(unittest.TestCase):
    def test_panel_shows_evidence_and_color_code(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('skuMatchEvidence', js)
        self.assertIn('makerColorCode', js)
        py = (ROOT / 'maker_sku_lookup.py').read_text(encoding='utf-8')
        self.assertIn('基礎品番一致', py)


if __name__ == '__main__':
    unittest.main()
