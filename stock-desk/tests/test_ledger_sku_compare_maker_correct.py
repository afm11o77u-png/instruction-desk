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
                'needsDecision': True,
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
                    'needsDecision': True,
                    'colorDiffPossible': True,
                    'rowNos': [2],
                    'nameCandidates': ['スクラブ ブルー', 'スクラブ ワイン'],
                },
                [],
            )
            self.assertFalse(meta['showButton'], code)
            self.assertEqual(meta['makerSku'], '')

    def test_button_when_name_diff_without_color_diff(self):
        """色競合がなくても確認待ち＋有効品番なら表示（商品名の言い回し差）。"""
        meta = msl.sku_group_maker_correction_meta(
            {
                'code': '9-1354',
                'needsDecision': True,
                'colorDiffPossible': False,
                'nameWordingDiff': True,
                'rowNos': [2, 3],
                'nameCandidates': ['帽子', 'ハンティングキャップ'],
            },
            [],
        )
        self.assertTrue(meta['showButton'])
        self.assertTrue(meta['bulkEligible'])
        self.assertEqual(meta['makerSku'], '9-1354')
        self.assertEqual(meta['sourceNameHint'], '帽子')

    def test_button_when_kind_diff_without_color_diff(self):
        meta = msl.sku_group_maker_correction_meta(
            {
                'code': 'AR1667-1',
                'needsDecision': True,
                'colorDiffPossible': False,
                'kindDiffPossible': True,
                'rowNos': [2, 3],
                'nameCandidates': ['オーバーブラウス', '桜台 ジャケット'],
            },
            [],
        )
        self.assertTrue(meta['showButton'])
        self.assertEqual(meta['makerSku'], 'AR1667-1')

    def test_hidden_when_not_needs_decision(self):
        meta = msl.sku_group_maker_correction_meta(
            {
                'code': '72-1226',
                'needsDecision': False,
                'colorDiffPossible': False,
                'rowNos': [2, 3],
                'nameCandidates': ['スクラブ', '＊スクラブ'],
            },
            [],
        )
        self.assertFalse(meta['showButton'])
        self.assertTrue(meta['bulkEligible'])

    def test_hidden_after_maker_adopted(self):
        meta = msl.sku_group_maker_correction_meta(
            {
                'code': 'APK600-C5',
                'needsDecision': True,
                'makerCorrectionAdopted': True,
                'colorDiffPossible': False,
                'rowNos': [2],
                'nameCandidates': ['ズボン', 'ゴムパンツ（超大きい人用）'],
            },
            [],
        )
        self.assertFalse(meta['showButton'])
        self.assertTrue(meta['bulkEligible'])

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
                'needsDecision': True,
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
            self.assertTrue(g.get('needsDecision'))
            mk = g.get('makerCorrection') or {}
            self.assertTrue(mk.get('showButton'), mk)
            self.assertEqual(mk.get('makerSku'), '72-1226')
            self.assertEqual(mk.get('sourceNameHint'), 'スクラブ')
        finally:
            c.close()
            folder.cleanup()

    def test_preview_button_on_name_wording_without_color(self):
        """色競合なし・商品名言い回し差の確認待ちでもボタン表示。"""
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,仕入A,9-1354,帽子,F',
                '顧客B,仕入A,9-1354,ハンティングキャップ,F',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'hat.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(g for g in preview['skuGroups'] if g['code'] == '9-1354')
            self.assertTrue(g.get('needsDecision'), g.get('reviewReasons'))
            self.assertFalse(g.get('colorDiffPossible'), g.get('reviewReasons'))
            self.assertTrue(g.get('nameWordingDiff') or any(
                (r.get('code') or '') == 'name_wording'
                for r in (g.get('reviewReasons') or [])
            ), g.get('reviewReasons'))
            mk = g.get('makerCorrection') or {}
            self.assertTrue(mk.get('showButton'), mk)
            self.assertTrue(mk.get('bulkEligible'), mk)
            self.assertEqual(mk.get('makerSku'), '9-1354')
        finally:
            c.close()
            folder.cleanup()

    def test_preview_button_on_kind_or_name_examples(self):
        """対象例: APK600-C5 / AR1667-1 も有効品番なら表示。"""
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,仕入A,APK600-C5,ズボン,3L',
                '顧客A,仕入A,APK600-C5,ゴムパンツ（超大きい人用）,5L',
                '顧客B,仕入B,AR1667-1,オーバーブラウス,M',
                '顧客B,仕入B,AR1667-1,桜台 ジャケット,L',
                '顧客C,仕入C,別注,特注帽子,F',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'examples.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            groups = {g['code']: g for g in preview.get('skuGroups') or []}
            for code in ('APK600-C5', 'AR1667-1'):
                g = groups[code]
                self.assertTrue(g.get('needsDecision'), (code, g.get('reviewReasons')))
                mk = g.get('makerCorrection') or {}
                self.assertTrue(mk.get('showButton'), (code, mk))
                self.assertEqual(mk.get('makerSku'), code)
            # 別注は非表示
            bet = groups.get('別注') or {}
            if bet:
                self.assertFalse((bet.get('makerCorrection') or {}).get('showButton'))
            # 行単位: 要確認＋有効品番のみ
            by_no = {r['rowNo']: r for r in preview['rows']}
            for rn in (2, 3, 4, 5):
                row = by_no[rn]
                if row.get('status') == 'needs_review':
                    mk = row.get('makerCorrection') or {}
                    self.assertTrue(mk.get('eligible'), (rn, mk))
                    self.assertTrue(mk.get('makerSku'))
            bet_rows = [r for r in preview['rows'] if (r.get('csv') or {}).get('code') == '別注']
            self.assertTrue(bet_rows)
            for r in bet_rows:
                self.assertFalse((r.get('makerCorrection') or {}).get('eligible'))
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
            # 自動まとめなら確認待ちでないためボタン非表示。有効品番なら一括対象は残る
            if g.get('needsDecision'):
                self.assertTrue(mk.get('showButton'), mk)
            else:
                self.assertFalse(mk.get('showButton'), mk)
            self.assertTrue(mk.get('bulkEligible'), mk)
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
            self.assertTrue(g2.get('makerCorrectionAdopted'))
            mk2 = g2.get('makerCorrection') or {}
            self.assertFalse(mk2.get('showButton'), mk2)
            reason_codes = {
                (r.get('code') or '') for r in (g2.get('reviewReasons') or [])
            }
            self.assertIn('maker_adopt', reason_codes)
            self.assertNotIn('color', reason_codes)
            self.assertNotIn('name_wording', reason_codes)
            self.assertNotIn('kind', reason_codes)
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
            self.assertEqual(fail['statusLabel'], '検索結果取得不可')

            bad = msl.search_maker_product(c, manufacturer='', maker_sku='別注')
            self.assertEqual(bad['status'], 'invalid_sku')
        finally:
            c.close()
            folder.cleanup()


class SkuCompareMakerAdoptKindTest(unittest.TestCase):
    def test_adopt_clears_kind_and_keeps_other_reasons(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,仕入A,AR1667-1,オーバーブラウス,M',
                '顧客A,仕入A,AR1667-1,桜台 ジャケット,L',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'kind.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g0 = next(g for g in preview['skuGroups'] if g['code'] == 'AR1667-1')
            self.assertTrue(g0.get('needsDecision'))
            self.assertTrue((g0.get('makerCorrection') or {}).get('showButton'))
            row_nos = [str(n) for n in (g0.get('rowNos') or [])]
            classifications = {
                rn: {
                    'plannedName': 'オーバーブラウス',
                    'color': '',
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
                'file': b64(text), 'name': 'kind.csv',
                'rowClassifications': classifications,
                'skuDecisions': {
                    'AR1667-1': {
                        'decision': 'merge',
                        'officialName': 'オーバーブラウス',
                        'color': '',
                        'makerAdopted': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g2 = next(g for g in preview2['skuGroups'] if g['code'] == 'AR1667-1')
            codes = {(r.get('code') or '') for r in (g2.get('reviewReasons') or [])}
            self.assertIn('maker_adopt', codes)
            self.assertNotIn('kind', codes)
            self.assertNotIn('name_wording', codes)
            self.assertFalse(g2.get('kindDiffPossible'))
            self.assertFalse((g2.get('makerCorrection') or {}).get('showButton'))
            # 原文CSVは維持（個別情報・照合用）
            by_no = {r['rowNo']: r for r in preview2['rows']}
            names = {(by_no[int(rn)].get('csv') or {}).get('productName') for rn in row_nos}
            self.assertTrue(names & {'オーバーブラウス', '桜台 ジャケット'})
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) AS n FROM products WHERE code='AR1667-1'"
                ).fetchone()['n'],
                0,
            )
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
        # 品番直下の結果パネル
        self.assertIn('skuMakerLookupRowHtml', js)
        self.assertIn('scrollMakerLookupIntoView', js)
        self.assertIn('確認待ちではありません', js)
        self.assertNotIn('色競合がありません', js)


if __name__ == '__main__':
    unittest.main()
