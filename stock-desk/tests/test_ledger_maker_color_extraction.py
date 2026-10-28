"""メーカー補正: 商品情報表の色抽出・同一品番補完・空色上書き防止（fixtureのみ。本番DBなし）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import maker_sku_lookup as msl  # noqa: E402
import product_ledger_paren as paren  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


# uniform1 相当: 縦型 th/td の商品番号・色
UNIFORM1_72_1226_HTML = '''<!DOCTYPE html><html><head>
<title>ユニフォームのオンラインショップです。</title>
</head><body>
<h1>72-1226 ジャケット(男女兼用・半袖)</h1>
<table>
<tr><th>商品番号</th><td>72-1226</td></tr>
<tr><th>メーカー希望<br>小売価格</th><td>8,600円</td></tr>
</table>
<table>
<tr><th>商品分類</th><td>半袖ジャケット（ブルゾン・ジャンパー）</td></tr>
<tr><th>色</th><td>ワイン／ネイビー</td></tr>
</table>
<p>Q. 色とカラー（グリーンorブルー）の物を探しています。</p>
</body></html>'''

# unifice 相当: 品名のみ（色なし）
UNIFICE_72_1226_HTML = '''<!DOCTYPE html><html><head>
<title>兼用半袖ジャケット MONTBLANC (住商モンブラン)の72-1226【ユニフォームのユニフィス】</title>
<meta property="og:title" content="兼用半袖ジャケット MONTBLANC (住商モンブラン)の72-1226" />
</head><body>
<TABLE border="1">
<TR><TH>ブランド名</TH></TR>
<TR><td>MONTBLANC (住商モンブラン)</td></TR>
</TABLE>
<TABLE border="1">
<TR><TH>品番</TH><TH>品名</TH></TR>
<TR><TD>72-1226</TD><TD>兼用半袖ジャケット</TD></TR>
</TABLE>
<p>関連商品</p>
<a href="/UNIFICE/search.php?code=MON72-999">72-999 別ジャケット(黒)</a>
</body></html>'''

# 単色（括弧内）
UNIFICE_SINGLE_COLOR_HTML = '''<!DOCTYPE html><html><head>
<title>9-970 兼用キャスケット(ワイン)</title>
</head><body>
<TABLE border="1">
<TR><TH>品番</TH><TH>品名</TH></TR>
<TR><TD>9-970</TD><TD>兼用キャスケット(ワイン)</TD></TR>
</TABLE>
</body></html>'''

# 横並び表に色列
TABLE_COLOR_COLUMN_HTML = '''<!DOCTYPE html><html><body>
<table>
<tr><th>品番</th><th>品名</th><th>カラー</th></tr>
<tr><td>AB-100</td><td>スクラブ</td><td>ネイビー</td></tr>
<tr><td>AB-200</td><td>別商品</td><td>レッド</td></tr>
</table>
</body></html>'''

# 色が取得できないページ（品名のみ）
NO_COLOR_HTML = '''<!DOCTYPE html><html><body>
<h1>ZZ-1 テストジャケット</h1>
<table>
<tr><th>品番</th><th>品名</th></tr>
<tr><td>ZZ-1</td><td>テストジャケット</td></tr>
</table>
</body></html>'''


class NormalizePageColorTest(unittest.TestCase):
    def test_colorway_slash_becomes_times(self):
        self.assertEqual(
            msl.normalize_maker_page_color('ワイン／ネイビー'),
            'ワイン×ネイビー',
        )
        self.assertEqual(
            paren.normalize_color_value('ワイン／ネイビー'),
            'ワイン×ネイビー',
        )

    def test_rejects_prose_and_or_lists(self):
        self.assertEqual(msl.normalize_maker_page_color('グリーンorブルー'), '')
        self.assertEqual(
            msl.normalize_maker_page_color('色とカラー（グリーンorブルー）の物を探しています。'),
            '',
        )


class ExtractProductInfoColorTest(unittest.TestCase):
    def test_uniform1_vertical_color_and_sku(self):
        info = msl.extract_product_info_fields(UNIFORM1_72_1226_HTML)
        self.assertEqual(info['sku'], '72-1226')
        self.assertEqual(info['color'], 'ワイン×ネイビー')
        self.assertNotIn('グリーン', info['color'])

    def test_unifice_name_without_color(self):
        info = msl.extract_product_info_fields(UNIFICE_72_1226_HTML)
        self.assertEqual(info['sku'], '72-1226')
        self.assertEqual(info['name'], '兼用半袖ジャケット')
        self.assertEqual(info['color'], '')

    def test_horizontal_color_column_first_row_only(self):
        info = msl.extract_product_info_fields(TABLE_COLOR_COLUMN_HTML)
        self.assertEqual(info['sku'], 'AB-100')
        self.assertEqual(info['name'], 'スクラブ')
        self.assertEqual(info['color'], 'ネイビー')
        # 2行目の別商品色を混ぜない
        self.assertNotEqual(info['color'], 'レッド')


class BuildCandidateColorTest(unittest.TestCase):
    def test_72_1226_uniform1_candidate(self):
        hit = {
            'title': '72-1226 ジャケット(男女兼用・半袖)',
            'url': 'https://www.uniform1.com/guide/item/ME/MO1/72-1226',
            'source': 'url_specified',
            'preferred': True,
            'manufacturer': '住商モンブラン',
            'pageHtml': UNIFORM1_72_1226_HTML,
        }
        built = msl._build_candidate_from_hit(
            hit,
            maker_sku='72-1226',
            manufacturer='住商モンブラン',
            hints={},
            current_name='旧名',
            current_color='',
            current_sku='72-1226',
        )
        self.assertIsNone(built.get('rejection'))
        cand = built['candidate']
        self.assertEqual(cand['color'], 'ワイン×ネイビー')
        self.assertFalse(cand.get('colorUnavailable'))
        self.assertIn('color', cand.get('fieldSources') or {})
        self.assertEqual(cand['fieldSources']['color']['value'], 'ワイン×ネイビー')
        self.assertEqual(cand['makerSku'], '72-1226')

    def test_72_1226_unifice_name_only_marks_color_unavailable(self):
        hit = {
            'title': '兼用半袖ジャケット',
            'url': 'https://www.unifice.com/UNIFICE/search.php?code=72-1226',
            'source': 'unifice',
            'preferred': True,
            'manufacturer': '住商モンブラン',
            'pageHtml': UNIFICE_72_1226_HTML,
        }
        built = msl._build_candidate_from_hit(
            hit,
            maker_sku='72-1226',
            manufacturer='住商モンブラン',
            hints={},
            current_name='旧',
            current_color='',
            current_sku='72-1226',
        )
        self.assertIsNone(built.get('rejection'))
        cand = built['candidate']
        self.assertEqual(cand['name'], '兼用半袖ジャケット')
        self.assertEqual(cand['color'], '')
        self.assertTrue(cand.get('colorUnavailable'))
        self.assertIn('color', cand.get('missingFields') or [])

    def test_single_color_from_name_paren(self):
        hit = {
            'title': '兼用キャスケット(ワイン)',
            'url': 'https://www.unifice.com/UNIFICE/search.php?code=9-970',
            'source': 'unifice',
            'pageHtml': UNIFICE_SINGLE_COLOR_HTML,
            'manufacturer': '住商モンブラン',
        }
        built = msl._build_candidate_from_hit(
            hit,
            maker_sku='9-970',
            manufacturer='住商モンブラン',
            hints={},
            current_name='',
            current_color='',
            current_sku='9-970',
        )
        cand = built['candidate']
        self.assertEqual(cand['name'], '兼用キャスケット')
        self.assertEqual(cand['color'], 'ワイン')

    def test_complement_same_sku_color_not_other_product(self):
        name_only = {
            'name': '兼用半袖ジャケット',
            'color': '',
            'position': '',
            'plannedRegisteredSku': '72-1226',
            'makerSku': '72-1226',
            'supplement': '',
            'size': '',
            'sourceUrl': 'https://www.unifice.com/UNIFICE/search.php?code=72-1226',
            'sourceUrls': ['https://www.unifice.com/UNIFICE/search.php?code=72-1226'],
            'missingFields': ['color'],
            'colorUnavailable': True,
            'preferred': True,
            'fieldSources': {
                'name': {
                    'value': '兼用半袖ジャケット',
                    'sourceUrl': 'https://www.unifice.com/UNIFICE/search.php?code=72-1226',
                    'sourceLabel': '商品情報表',
                },
                'color': {
                    'value': '', 'sourceUrl': '', 'sourceLabel': '色未取得', 'unavailable': True,
                },
            },
            'diff': {'currentColor': ''},
        }
        with_color = {
            'name': 'ジャケット（男女兼用・半袖）',
            'color': 'ワイン×ネイビー',
            'position': '',
            'plannedRegisteredSku': '72-1226',
            'makerSku': '72-1226',
            'supplement': '',
            'size': '',
            'sourceUrl': 'https://www.uniform1.com/guide/item/ME/MO1/72-1226',
            'sourceUrls': ['https://www.uniform1.com/guide/item/ME/MO1/72-1226'],
            'missingFields': [],
            'colorUnavailable': False,
            'preferred': False,
            'fieldSources': {
                'color': {
                    'value': 'ワイン×ネイビー',
                    'sourceUrl': 'https://www.uniform1.com/guide/item/ME/MO1/72-1226',
                    'sourceLabel': '商品情報表',
                },
            },
            'diff': {'currentColor': ''},
        }
        other_sku = {
            'name': '別ジャケット',
            'color': 'ブラック',
            'position': '',
            'plannedRegisteredSku': '72-999',
            'makerSku': '72-999',
            'supplement': '',
            'size': '',
            'sourceUrl': 'https://example.com/other',
            'sourceUrls': ['https://example.com/other'],
            'missingFields': [],
            'colorUnavailable': False,
            'preferred': False,
            'fieldSources': {
                'color': {
                    'value': 'ブラック',
                    'sourceUrl': 'https://example.com/other',
                    'sourceLabel': '商品情報表',
                },
            },
            'diff': {'currentColor': ''},
        }
        merged = msl.complement_color_from_same_product_pages(
            [name_only, with_color, other_sku],
        )
        by_sku = {
            c['plannedRegisteredSku']: c
            for c in merged
            if c.get('name') == '兼用半袖ジャケット' or c.get('plannedRegisteredSku') == '72-999'
        }
        # 名あり候補へ同一品番の色が載る
        jacket = next(c for c in merged if c.get('name') == '兼用半袖ジャケット')
        self.assertEqual(jacket['color'], 'ワイン×ネイビー')
        self.assertFalse(jacket.get('colorUnavailable'))
        self.assertTrue(
            (jacket.get('fieldSources') or {}).get('color', {}).get('complemented')
            or jacket['color'] == 'ワイン×ネイビー'
        )
        # 別品番の色は混ぜない
        other = next(c for c in merged if c.get('plannedRegisteredSku') == '72-999')
        self.assertEqual(other['color'], 'ブラック')


class AdoptBlankColorGuardTest(unittest.TestCase):
    def test_blank_color_does_not_wipe_existing(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            first = msl.adopt_maker_correction(
                c,
                manufacturer='住商モンブラン',
                maker_sku='72-1226',
                name='兼用半袖ジャケット',
                color='ワイン×ネイビー',
                source_url='https://www.uniform1.com/guide/item/ME/MO1/72-1226',
            )
            self.assertEqual(first['color'], 'ワイン×ネイビー')
            second = msl.adopt_maker_correction(
                c,
                manufacturer='住商モンブラン',
                maker_sku='72-1226',
                name='兼用半袖ジャケット',
                color='',  # 空では上書きしない
                source_url='https://www.unifice.com/UNIFICE/search.php?code=72-1226',
                current_color='手動ワイン',
            )
            self.assertEqual(second['color'], 'ワイン×ネイビー')
            # current_color フォールバック（未保存時）
            third = msl.adopt_maker_correction(
                c,
                manufacturer='住商モンブラン',
                maker_sku='ZZ-KEEP',
                name='テスト',
                color='',
                current_color='既存ブルー',
            )
            self.assertEqual(third['color'], '既存ブルー')
        finally:
            c.close()
            folder.cleanup()


class LookupFromUrlAndAdoptRematchTest(unittest.TestCase):
    def test_72_1226_url_then_adopt_survives(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)

            def fake_http_get(url, timeout=25):
                u = str(url)
                if 'uniform1.com' in u:
                    return u, UNIFORM1_72_1226_HTML
                if 'unifice.com' in u:
                    return u, UNIFICE_72_1226_HTML
                raise urllib_error_for_test(u)

            with mock.patch.object(msl, '_http_get', side_effect=fake_http_get):
                with mock.patch.object(msl, '_catalog_searches_for', return_value=[{
                    'title': '兼用半袖ジャケット',
                    'url': 'https://www.unifice.com/UNIFICE/search.php?code=72-1226',
                    'source': 'unifice',
                    'preferred': True,
                    'manufacturer': '住商モンブラン',
                    'pageHtml': UNIFICE_72_1226_HTML,
                }]):
                    result = msl.lookup_maker_product_from_url(
                        c,
                        url='https://www.uniform1.com/guide/item/ME/MO1/72-1226',
                        manufacturer='住商モンブラン',
                        maker_sku='72-1226',
                        current_name='旧ジャケット',
                        current_color='',
                        current_sku='72-1226',
                    )
            self.assertIn(result['status'], ('ok', 'multiple'))
            cands = result['candidates']
            self.assertTrue(cands)
            # 期待: 名＋配色1色＋品番
            best = next(
                (
                    x for x in cands
                    if x.get('color') == 'ワイン×ネイビー'
                ),
                cands[0],
            )
            self.assertEqual(best['color'], 'ワイン×ネイビー')
            # 商品名は uniform1 見出し or カタログ補完名のいずれか（同一品番）
            self.assertTrue(best.get('name'))
            self.assertEqual(best.get('plannedRegisteredSku') or best.get('makerSku'), '72-1226')

            adopted = msl.adopt_maker_correction(
                c,
                manufacturer='住商モンブラン',
                maker_sku='72-1226',
                name='兼用半袖ジャケット',
                color='ワイン×ネイビー',
                planned_registered_sku='72-1226',
                source_url=best.get('sourceUrl') or '',
            )
            self.assertEqual(adopted['color'], 'ワイン×ネイビー')
            self.assertEqual(adopted['name'], '兼用半袖ジャケット')

            # rematch: 保存済みが候補先頭に残る
            with mock.patch.object(msl, '_catalog_searches_for', return_value=[]):
                with mock.patch.object(msl, '_ddg_candidates', return_value=[]):
                    again = msl.search_maker_product(
                        c,
                        manufacturer='住商モンブラン',
                        maker_sku='72-1226',
                        current_name='旧',
                        current_color='',
                        current_sku='72-1226',
                    )
            saved = again.get('saved') or {}
            front = (again.get('candidates') or [{}])[0]
            self.assertEqual(saved.get('color') or front.get('color'), 'ワイン×ネイビー')
            self.assertEqual(saved.get('name') or front.get('name'), '兼用半袖ジャケット')
        finally:
            c.close()
            folder.cleanup()

    def test_color_unavailable_status_note(self):
        folder, path, c = newdb()
        try:
            status, label, note = msl._lookup_status_for_candidates([{
                'name': 'テストジャケット',
                'color': '',
                'colorUnavailable': True,
            }])
            self.assertEqual(status, 'ok')
            self.assertIn('色未取得', label)
            self.assertIn('色補正完了ではありません', note)

            status2, label2, _ = msl._lookup_status_for_candidates([{
                'name': 'テストジャケット',
                'color': 'ネイビー',
            }])
            self.assertEqual(status2, 'ok')
            self.assertEqual(label2, '補正候補取得できた')
        finally:
            c.close()
            folder.cleanup()

    def test_no_color_page_candidate(self):
        hit = {
            'title': 'ZZ-1 テストジャケット',
            'url': 'https://example.com/zz-1',
            'source': 'sales_page',
            'pageHtml': NO_COLOR_HTML,
        }
        built = msl._build_candidate_from_hit(
            hit,
            maker_sku='ZZ-1',
            manufacturer='',
            hints={},
            current_name='',
            current_color='手動赤',
            current_sku='ZZ-1',
        )
        cand = built['candidate']
        self.assertEqual(cand['name'], 'テストジャケット')
        self.assertEqual(cand['color'], '')
        self.assertTrue(cand.get('colorUnavailable'))
        # 空色は colorChanged にしない（既存を消す差分に見せない）
        self.assertFalse(cand['diff'].get('colorChanged'))


def urllib_error_for_test(url: str):
    raise OSError(f'unexpected fetch: {url}')


class UiContractTest(unittest.TestCase):
    def test_panel_shows_field_sources_and_color_unavailable(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('fieldSources', js)
        self.assertIn('色未取得', js)
        self.assertIn('色補正未完了', js)
        self.assertIn('空上書きなし', js)
        self.assertIn('currentColor', js)


if __name__ == '__main__':
    unittest.main()
