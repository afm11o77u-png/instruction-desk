"""メーカー補正: ユニフィス表抽出・MON管理番号・URL指定（テストDBのみ。本番操作なし）。"""
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


UNIFICE_TABLE_HTML = '''<!DOCTYPE html><html><head>
<title>9-970 兼用キャスケット(ワイン)[年間物] 住商モンブラン</title>
<meta property="og:title" content="兼用キャスケット(ワイン) MONTBLANC (住商モンブラン)の9-970【ユニフォームのユニフィス】" />
</head><body>
<TABLE border="1">
<TR><TH>ブランド名</TH></TR>
<TR><td>MONTBLANC (住商モンブラン)</td></TR>
</TABLE>
<TABLE border="1">
<TR><TH>品番</TH><TH>品名</TH></TR>
<TR><TD>9-970</TD><TD>兼用キャスケット(ワイン)</TD></TR>
</TABLE>
<p>関連商品</p>
<a href="/UNIFICE/search.php?code=MON9-980">9-980 別キャスケット(黒)</a>
</body></html>'''

UNIFICE_JSONLD_HTML = '''<!DOCTYPE html><html><head>
<title>住商モンブランの9-970 兼用キャスケット(ワイン)</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
 "name":"9-970 兼用キャスケット(ワイン)",
 "sku":"9-970","mpn":"MON9-970",
 "brand":{"@type":"Brand","name":"MONTBLANC (住商モンブラン)"},
 "color":""}
</script>
</head><body>
<h1>9-970　兼用キャスケット(ワイン)</h1>
</body></html>'''

RELATED_ONLY_HTML = '''<!DOCTYPE html><html><head>
<title>別商品帽子</title></head><body>
<h1>別の帽子</h1>
<div>商品コード</div><div>ZZ-1</div>
<a href="/x">関連 9-970 兼用キャスケット</a>
<p>この商品を見た人は 9-970 も見ています</p>
</body></html>'''


class UnificeExtractTest(unittest.TestCase):
    def test_product_table_sku_and_name_not_label(self):
        info = msl.extract_product_info_fields(UNIFICE_TABLE_HTML)
        self.assertEqual(info['sku'], '9-970')
        self.assertEqual(info['name'], '兼用キャスケット(ワイン)')
        self.assertIn('モンブラン', info['brand'])
        vals = msl.extract_structured_sku_values(UNIFICE_TABLE_HTML)
        self.assertIn('9-970', vals)
        self.assertNotIn('品名', vals)

    def test_jsonld_sku_and_site_mpn_confirmation(self):
        info = msl.extract_product_info_fields(UNIFICE_JSONLD_HTML)
        self.assertEqual(info['sku'], '9-970')
        self.assertEqual(info['mpn'], 'MON9-970')
        conf = msl.site_management_confirms_maker_sku(
            maker_sku='9-970',
            page_sku='9-970',
            page_mpn='MON9-970',
        )
        self.assertTrue(conf['confirmed'])
        self.assertEqual(conf['siteCode'], 'MON9-970')
        # メーカー品番なしに MON を削って一致させない
        conf2 = msl.site_management_confirms_maker_sku(
            maker_sku='9-970',
            page_sku='',
            page_mpn='MON9-970',
        )
        self.assertFalse(conf2['confirmed'])

    def test_build_candidate_name_color_from_table(self):
        hit = {
            'title': '兼用キャスケット(ワイン) MONTBLANC (住商モンブラン)の9-970【ユニフォームのユニフィス】',
            'url': 'https://www.unifice.com/UNIFICE/search.php?code=MON9-970',
            'source': 'unifice',
            'preferred': True,
            'manufacturer': '住商モンブラン',
            'pageHtml': UNIFICE_TABLE_HTML,
        }
        built = msl._build_candidate_from_hit(
            hit,
            maker_sku='9-970',
            manufacturer='住商モンブラン',
            hints={},
            current_name='キャスケット',
            current_color='',
            current_sku='9-970',
        )
        self.assertIsNone(built.get('rejection'))
        cand = built['candidate']
        self.assertEqual(cand['name'], '兼用キャスケット')
        self.assertEqual(cand['color'], 'ワイン')
        self.assertEqual(cand['makerSku'], '9-970')
        self.assertEqual(cand.get('siteCode'), '')  # 表HTMLに mpn 無し
        self.assertNotIn('ユニフィス', cand['name'])
        self.assertNotIn('MONTBLANC', cand['name'])

    def test_jsonld_candidate_keeps_site_code(self):
        hit = {
            'title': '兼用キャスケット(ワイン)',
            'url': 'https://www.unifice.com/U2/search.php?action=syousai&code=MON9-970',
            'source': 'unifice',
            'preferred': True,
            'manufacturer': '住商モンブラン',
            'pageHtml': UNIFICE_JSONLD_HTML,
        }
        built = msl._build_candidate_from_hit(
            hit,
            maker_sku='9-970',
            manufacturer='住商モンブラン',
            hints={},
            current_name='旧',
            current_color='',
            current_sku='9-970',
        )
        self.assertIsNone(built.get('rejection'))
        cand = built['candidate']
        self.assertEqual(cand['name'], '兼用キャスケット')
        self.assertEqual(cand['color'], 'ワイン')
        self.assertEqual(cand.get('siteCode'), 'MON9-970')

    def test_related_link_only_is_sku_mismatch(self):
        hit = {
            'title': '別商品',
            'url': 'https://example.com/other',
            'source': 'sales_page',
            'pageHtml': RELATED_ONLY_HTML,
        }
        built = msl._build_candidate_from_hit(
            hit,
            maker_sku='9-970',
            manufacturer='住商モンブラン',
            hints={},
            current_name='x',
            current_color='',
            current_sku='9-970',
        )
        self.assertEqual(built.get('rejection'), 'sku_mismatch')

    def test_do_not_strip_mon_from_arbitrary_sku(self):
        # MONABC をメーカー品番 ABC と機械同一視しない
        self.assertFalse(
            msl.site_management_confirms_maker_sku(
                maker_sku='ABC',
                page_sku='',
                page_mpn='MONABC',
            )['confirmed']
        )


class UrlLookupTest(unittest.TestCase):
    def test_reject_non_public_urls(self):
        for bad in (
            'file:///tmp/x.html',
            'http://localhost/x',
            'http://127.0.0.1/x',
            'http://192.168.1.1/x',
            'http://10.0.0.2/x',
            'ftp://example.com/x',
            '',
        ):
            v = msl.validate_public_http_url(bad)
            self.assertFalse(v['ok'], bad)

        ok = msl.validate_public_http_url(
            'https://www.unifice.com/UNIFICE/search.php?code=MON9-970'
        )
        self.assertTrue(ok['ok'])

    def test_from_url_success_and_mismatch_statuses(self):
        folder, path, c = newdb()
        try:
            with mock.patch.object(msl, '_http_get', return_value=(
                'https://www.unifice.com/UNIFICE/search.php?code=MON9-970',
                UNIFICE_TABLE_HTML,
            )):
                res = msl.lookup_maker_product_from_url(
                    c,
                    url='https://www.unifice.com/UNIFICE/search.php?code=MON9-970',
                    manufacturer='住商モンブラン',
                    maker_sku='9-970',
                    current_name='旧キャスケット',
                    current_color='',
                )
            self.assertEqual(res['status'], 'ok')
            self.assertTrue(res.get('fromUrl'))
            cand = res['candidates'][0]
            self.assertEqual(cand['name'], '兼用キャスケット')
            self.assertEqual(cand['color'], 'ワイン')

            with mock.patch.object(msl, '_http_get', return_value=(
                'https://example.com/other', RELATED_ONLY_HTML,
            )):
                bad = msl.lookup_maker_product_from_url(
                    c,
                    url='https://example.com/other',
                    manufacturer='住商モンブラン',
                    maker_sku='9-970',
                )
            self.assertEqual(bad['status'], 'sku_mismatch')
            self.assertFalse(bad.get('candidates'))

            invalid = msl.lookup_maker_product_from_url(
                c,
                url='http://127.0.0.1/secret',
                manufacturer='住商モンブラン',
                maker_sku='9-970',
            )
            self.assertEqual(invalid['status'], 'invalid_url')
        finally:
            c.close()
            folder.cleanup()

    def test_search_prefers_unifice_catalog_without_ddg(self):
        folder, path, c = newdb()
        try:
            unifice_hit = {
                'title': '兼用キャスケット(ワイン)',
                'url': 'https://www.unifice.com/UNIFICE/search.php?code=9-970',
                'source': 'unifice',
                'preferred': True,
                'manufacturer': '住商モンブラン',
                'pageHtml': UNIFICE_TABLE_HTML,
            }
            with mock.patch.object(msl, '_unifice_ec_candidates', return_value=[unifice_hit]), \
                    mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_midori_ec_candidates', return_value=[]), \
                    mock.patch.object(msl, '_kazen_ec_candidates', return_value=[]):
                res = msl.search_maker_product(
                    c,
                    manufacturer='住商モンブラン',
                    maker_sku='9-970',
                    current_name='キャスケット',
                    current_color='ワイン',
                    supplier='住商モンブラン',
                )
            self.assertIn(res['status'], ('ok', 'multiple'))
            self.assertTrue(res['candidates'])
            top = res['candidates'][0]
            self.assertEqual(top['name'], '兼用キャスケット')
            self.assertEqual(top['color'], 'ワイン')
            # DDG が空でも no_match に潰さない
            self.assertNotEqual(res['status'], 'no_match')
        finally:
            c.close()
            folder.cleanup()

    def test_statuses_not_collapsed_to_no_match(self):
        folder, path, c = newdb()
        try:
            with mock.patch.object(msl, '_ddg_candidates', side_effect=OSError('down')), \
                    mock.patch.object(msl, '_catalog_searches_for', side_effect=OSError('down')):
                failed = msl.search_maker_product(
                    c, manufacturer='住商モンブラン', maker_sku='9-970',
                )
            self.assertEqual(failed['status'], 'fetch_failed')

            hit = {
                'title': '別',
                'url': 'https://example.com/x',
                'source': 'sales_page',
                'preferred': False,
            }
            with mock.patch.object(msl, '_ddg_candidates', return_value=[hit]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[]), \
                    mock.patch.object(msl, '_http_get', return_value=(
                        'https://example.com/x', RELATED_ONLY_HTML,
                    )):
                mismatch = msl.search_maker_product(
                    c, manufacturer='住商モンブラン', maker_sku='9-970',
                )
            self.assertEqual(mismatch['status'], 'sku_mismatch')
        finally:
            c.close()
            folder.cleanup()


class UiAndApiWiringTest(unittest.TestCase):
    def test_ui_has_url_fetch_controls(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('このURLから商品情報を取得', js)
        self.assertIn('/api/product-ledger-csv/maker-lookup/from-url', js)
        self.assertIn('makerLookupNeedsUrlAssist', js)
        self.assertIn('plMakerUrlFetch', js)

    def test_server_has_from_url_route(self):
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/api/product-ledger-csv/maker-lookup/from-url'", server)
        self.assertIn('lookup_maker_product_from_url', server)
        limits = (ROOT / 'upload_limits.py').read_text(encoding='utf-8')
        self.assertIn('/api/product-ledger-csv/maker-lookup/from-url', limits)


if __name__ == '__main__':
    unittest.main()
