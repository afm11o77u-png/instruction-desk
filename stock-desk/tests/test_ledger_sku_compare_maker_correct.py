"""品番単位比較一覧の「メーカー情報で補正」（テストDBのみ。本番登録・在庫変更なし）。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import maker_sku_lookup as msl  # noqa: E402
import product_ledger_import as pli  # noqa: E402
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


class NeutralSourceNameTest(unittest.TestCase):
    def test_strip_conflicting_colors_for_search(self):
        self.assertEqual(msl.strip_color_tokens_from_name('スクラブ ブルー'), 'スクラブ')
        self.assertEqual(msl.strip_color_tokens_from_name('スクラブ ワイン'), 'スクラブ')
        self.assertEqual(
            msl.neutral_search_source_name(['スクラブ ブルー', 'スクラブ ワイン']),
            'スクラブ',
        )


class SkuGroupMakerMetaTest(unittest.TestCase):
    def test_button_when_color_diff_and_valid_sku(self):
        meta = msl.sku_group_maker_correction_meta(
            {
                'code': '72-1226',
                'colorDiffPossible': True,
                'rowNos': [2, 3],
                'nameCandidates': ['スクラブ ブルー', 'スクラブ ワイン'],
            },
            [],
        )
        self.assertTrue(meta['showButton'])
        self.assertEqual(meta['makerSku'], '72-1226')
        self.assertEqual(meta['sourceNameHint'], 'スクラブ')

    def test_hidden_for_custom_order_and_blank(self):
        for code in ('別注', '品番なし', ''):
            meta = msl.sku_group_maker_correction_meta(
                {
                    'code': code,
                    'colorDiffPossible': True,
                    'rowNos': [2],
                    'nameCandidates': ['スクラブ ブルー', 'スクラブ ワイン'],
                },
                [],
            )
            self.assertFalse(meta['showButton'], code)
            self.assertEqual(meta['makerSku'], '')

    def test_hidden_when_no_color_diff(self):
        meta = msl.sku_group_maker_correction_meta(
            {
                'code': '72-1226',
                'colorDiffPossible': False,
                'rowNos': [2, 3],
                'nameCandidates': ['スクラブ', '＊スクラブ'],
            },
            [],
        )
        self.assertFalse(meta['showButton'])

    def test_uses_maker_code_when_internal_sku(self):
        rows = [{
            'rowNo': 2,
            'csv': {
                'code': '72-1226-社内',
                'makerCode': '72-1226',
                'productName': 'スクラブ ブルー',
                'supplier': 'ミドリ安全北海道㈱',
            },
            'product': {
                'code': '72-1226-社内',
                'makerCode': '72-1226',
                'colorSplit': True,
            },
            'makerCorrection': {
                'eligible': True,
                'makerSku': '72-1226',
                'manufacturer': 'ミドリ安全',
            },
        }]
        meta = msl.sku_group_maker_correction_meta(
            {
                'code': '72-1226-社内',
                'colorDiffPossible': True,
                'rowNos': [2],
                'nameCandidates': ['スクラブ ブルー', 'スクラブ ワイン'],
            },
            rows,
        )
        self.assertTrue(meta['showButton'])
        self.assertEqual(meta['makerSku'], '72-1226')
        self.assertEqual(meta['manufacturer'], 'ミドリ安全')


class SkuCompareMakerPreviewTest(unittest.TestCase):
    def test_preview_exposes_group_button_on_color_conflict(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,ミドリ安全北海道㈱,72-1226,スクラブ ブルー,M',
                '顧客A,ミドリ安全北海道㈱,72-1226,スクラブ ワイン,L',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'scrub.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            groups = {g['code']: g for g in preview.get('skuGroups') or []}
            g = groups['72-1226']
            self.assertTrue(g.get('colorDiffPossible'), g.get('reviewReasons'))
            mk = g.get('makerCorrection') or {}
            self.assertTrue(mk.get('showButton'), mk)
            self.assertEqual(mk.get('makerSku'), '72-1226')
            self.assertEqual(mk.get('sourceNameHint'), 'スクラブ')
        finally:
            c.close()
            folder.cleanup()

    def test_normalized_same_color_not_conflict(self):
        folder, path, c = newdb()
        try:
            # 半角カナ／全角の同一色は競合にしない
            text = ledger_csv([
                '顧客A,仕入A,72-1226,スクラブ ﾌﾞﾙｰ,M',
                '顧客A,仕入A,72-1226,スクラブ ブルー,L',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'same-color.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            groups = {g['code']: g for g in preview.get('skuGroups') or []}
            g = groups.get('72-1226') or {}
            self.assertFalse(g.get('colorDiffPossible'), g.get('reviewReasons'))
            mk = g.get('makerCorrection') or {}
            self.assertFalse(mk.get('showButton'), mk)
        finally:
            c.close()
            folder.cleanup()

    def test_adopt_merges_to_one_product_preview_only(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,ミドリ安全北海道㈱,72-1226,スクラブ ブルー,M',
                '顧客B,ミドリ安全北海道㈱,72-1226,スクラブ ワイン,L',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'scrub2.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g0 = next(g for g in preview['skuGroups'] if g['code'] == '72-1226')
            self.assertTrue(g0.get('colorDiffPossible'))
            row_nos = [str(n) for n in (g0.get('rowNos') or [])]
            self.assertGreaterEqual(len(row_nos), 2)

            # メーカー採用相当（ワイン確定）→ 全行へ分類＋まとめ決定
            classifications = {
                rn: {
                    'plannedName': 'スクラブ',
                    'color': 'ワイン',
                    'unclassified': [],
                    'userEdited': True,
                    'confirmed': True,
                    'acceptProposal': True,
                    'makerAdopted': True,
                }
                for rn in row_nos
            }
            preview2 = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'scrub2.csv',
                'rowClassifications': classifications,
                'skuDecisions': {
                    '72-1226': {
                        'decision': 'merge',
                        'officialName': 'スクラブ',
                        'color': 'ワイン',
                        'makerAdopted': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g2 = next(g for g in preview2['skuGroups'] if g['code'] == '72-1226')
            self.assertFalse(g2.get('colorDiffPossible'), g2.get('reviewReasons'))
            mk2 = g2.get('makerCorrection') or {}
            self.assertFalse(mk2.get('showButton'), mk2)
            reason_codes = {
                (r.get('code') or '') for r in (g2.get('reviewReasons') or [])
            }
            self.assertIn('maker_adopt', reason_codes)
            self.assertNotIn('color', reason_codes)
            # 各行の予定は採用内容。原文CSVは維持
            by_no = {r['rowNo']: r for r in preview2['rows']}
            for rn in row_nos:
                row = by_no[int(rn)]
                pc = row.get('parenClassification') or {}
                self.assertEqual(pc.get('plannedName'), 'スクラブ')
                self.assertEqual(pc.get('color'), 'ワイン')
                self.assertEqual((row.get('csv') or {}).get('productName') in (
                    'スクラブ ブルー', 'スクラブ ワイン',
                ), True)
            # DB 未登録（プレビューのみ）
            self.assertEqual(
                c.execute("SELECT COUNT(*) AS n FROM products WHERE code='72-1226'").fetchone()['n'],
                0,
            )
            # 決定は merge（色分割にしない）
            self.assertEqual((g2.get('decision') or {}).get('decision'), 'merge')
        finally:
            c.close()
            folder.cleanup()

    def test_color_split_decision_not_auto_overwritten_without_flag(self):
        """利用者が色分割を選んでいるとき、makerAdopted 無しでは色分割のまま。"""
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,仕入A,72-1226,スクラブ ブルー,M',
                '顧客A,仕入A,72-1226,スクラブ ワイン,L',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'split.csv',
                'skuDecisions': {'72-1226': {'decision': 'color_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(g for g in preview['skuGroups'] if g['code'] == '72-1226')
            self.assertTrue(g.get('colorSplit') or (g.get('decision') or {}).get('decision') == 'color_split')
            # 色競合は残る（メーカー採用していない）
            self.assertTrue(g.get('colorDiffPossible'))
        finally:
            c.close()
            folder.cleanup()

    def test_search_multiple_colors_not_auto_picked(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            fake_hits = [
                {
                    'title': 'スクラブ 72-1226 ブルー',
                    'url': 'https://ec.midori-anzen.com/shop/g/gblue/',
                    'preferred': True, 'source': 'midori_ec', 'manufacturer': 'ミドリ安全',
                },
                {
                    'title': 'スクラブ 72-1226 ワイン',
                    'url': 'https://ec.midori-anzen.com/shop/g/gwine/',
                    'preferred': True, 'source': 'midori_ec', 'manufacturer': 'ミドリ安全',
                },
            ]

            def fake_get(url, timeout=10):
                if 'gblue' in url:
                    return url, '<html><title>スクラブ 72-1226 ブルー</title><body>72-1226 ブルー</body></html>'
                if 'gwine' in url:
                    return url, '<html><title>スクラブ 72-1226 ワイン</title><body>72-1226 ワイン</body></html>'
                return url, '<html></html>'

            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=fake_hits), \
                    mock.patch.object(msl, '_http_get', side_effect=fake_get):
                # 色ヒント無し（グループ検索）→ 複数候補のまま
                res = msl.search_maker_product(
                    c,
                    manufacturer='ミドリ安全',
                    maker_sku='72-1226',
                    current_name='スクラブ',
                    current_color='',
                    source_name='スクラブ',
                    supplier='ミドリ安全北海道㈱',
                )
            self.assertEqual(res['status'], 'multiple')
            colors = {(c.get('color') or '') for c in res['candidates']}
            self.assertIn('ブルー', colors)
            self.assertIn('ワイン', colors)
        finally:
            c.close()
            folder.cleanup()

    def test_no_match_and_fetch_failed_statuses(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[]), \
                    mock.patch.object(msl, '_midori_ec_candidates', return_value=[]):
                none = msl.search_maker_product(
                    c, manufacturer='ミドリ安全', maker_sku='72-1226',
                    source_name='スクラブ',
                )
            self.assertEqual(none['status'], 'no_match')

            with mock.patch.object(msl, '_ddg_candidates', side_effect=OSError('down')), \
                    mock.patch.object(msl, '_catalog_searches_for', side_effect=OSError('down')), \
                    mock.patch.object(msl, '_midori_ec_candidates', side_effect=OSError('down')):
                fail = msl.search_maker_product(
                    c, manufacturer='ミドリ安全', maker_sku='72-1226',
                )
            self.assertEqual(fail['status'], 'fetch_failed')

            bad = msl.search_maker_product(c, manufacturer='', maker_sku='別注')
            self.assertEqual(bad['status'], 'invalid_sku')
        finally:
            c.close()
            folder.cleanup()


class SkuCompareMakerUiSourceTest(unittest.TestCase):
    def test_sku_compare_button_wired(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('data-pl-sku-maker-correct', js)
        self.assertIn("scope:'sku'", js)
        self.assertIn('色別に分けて登録', js)
        # 色分割からの切替確認
        self.assertIn('同じ商品としてまとめる', js)
        self.assertIn('makerAdopted:true', js)
        # 既存の行単位ボタンも維持
        self.assertIn('data-pl-maker-correct', js)


if __name__ == '__main__':
    unittest.main()
