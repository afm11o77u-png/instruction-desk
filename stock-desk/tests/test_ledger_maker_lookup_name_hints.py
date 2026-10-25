"""メーカー情報で補正: 旧商品名ヒント検索（テストDBのみ。本番登録なし）。"""
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


class NameHintHelpersTest(unittest.TestCase):
    def test_extract_hints_shirashita_blue(self):
        h = msl.extract_name_search_hints('白下 ブルー')
        self.assertEqual(h['positions'], ['下'])
        self.assertEqual(h['colors'], ['ブルー'])
        # 「白」は検索色にしない
        self.assertNotIn('白', h['colors'])

    def test_stages_include_position_and_color(self):
        h = msl.extract_name_search_hints('白下 ブルー')
        stages = msl.build_search_query_stages(
            manufacturer='ミドリ安全', maker_sku='VEH30B', hints=h,
        )
        self.assertIn('ミドリ安全 VEH30B', stages)
        self.assertIn('VEH30B', stages)
        self.assertIn('ミドリ安全 VEH30B 下', stages)
        self.assertIn('ミドリ安全 VEH30B 下 ブルー', stages)
        # 保存SKUの密着クエリは試すが、maker_sku 自体は書き換えない
        self.assertIn('VEH30B下', stages)
        self.assertEqual(msl.maker_sku_key('VEH30B'), 'VEH30B')

    def test_supplier_brand_hint_not_unconditional(self):
        self.assertEqual(
            msl.manufacturer_hint_from_supplier('ミドリ安全北海道㈱'),
            'ミドリ安全',
        )
        self.assertEqual(msl.manufacturer_hint_from_supplier('別会社㈱'), '')
        self.assertEqual(
            msl.resolve_manufacturer(manufacturer='', supplier='ミドリ安全北海道㈱'),
            'ミドリ安全',
        )
        self.assertEqual(
            msl.resolve_manufacturer(manufacturer='明示メーカー', supplier='ミドリ安全北海道㈱'),
            '明示メーカー',
        )

    def test_parse_title_and_reject_partial_sku(self):
        ok = msl.parse_maker_listing_title(
            '男女共用　スラックス　ＶＥＨ３０Ｂ　下　ブルー', 'VEH30B',
        )
        self.assertEqual(ok['name'], '男女共用 スラックス')
        self.assertEqual(ok['color'], 'ブルー')
        self.assertEqual(ok['position'], '下')
        self.assertEqual(ok['plannedRegisteredSku'], 'VEH30B下')
        self.assertEqual(ok.get('supplement') or '', '')
        # 型番一部一致は同一視しない
        self.assertIsNone(msl.parse_maker_listing_title(
            '男女共用　スラックス　ＶＥＨ３０Ｇ　下　グリーン', 'VEH30B',
        ))
        # 上は下ヒントで弾く
        up = msl.parse_maker_listing_title(
            '男女共用　スラックス　ＶＥＨ３０Ｂ　上　ブルー', 'VEH30B',
        )
        self.assertFalse(msl._candidate_passes_hints(up, {'positions': ['下'], 'colors': ['ブルー']}))

    def test_fullwidth_sku_match(self):
        self.assertTrue(msl.skus_equal('VEH30B', 'ＶＥＨ３０Ｂ'))
        self.assertTrue(msl._page_mentions_sku('品番ＶＥＨ３０Ｂです', 'VEH30B'))
        self.assertFalse(msl.skus_equal('VEH30B', 'VEH30'))


class MakerLookupVeh30bTest(unittest.TestCase):
    def test_veh30b_shirashita_blue_live_or_mocked(self):
        folder, path, c = newdb()
        try:
            # ネットワークが使える環境では実ページ、ダメならモック
            try:
                live = msl.search_maker_product(
                    c,
                    manufacturer='',
                    maker_sku='VEH30B',
                    source_name='白下 ブルー',
                    current_name='白下 ブルー',
                    supplier='ミドリ安全北海道㈱',
                )
                use_live = live.get('status') == 'ok' and live.get('candidates')
            except Exception:
                use_live = False
                live = None

            if use_live:
                cand = live['candidates'][0]
            else:
                fake_hits = [{
                    'title': '男女共用　スラックス　ＶＥＨ３０Ｂ　下　ブルー',
                    'url': 'https://ec.midori-anzen.com/shop/g/g31131914/',
                    'source': 'midori_ec',
                    'preferred': True,
                    'manufacturer': 'ミドリ安全',
                }]
                page = (
                    '<html><head><title>男女共用 スラックス ＶＥＨ３０Ｂ 下 ブルー</title></head>'
                    '<body>ＶＥＨ３０Ｂ 下 ブルー</body></html>'
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
                        supplier='ミドリ安全北海道㈱',
                    )
                self.assertEqual(live['status'], 'ok')
                cand = live['candidates'][0]

            self.assertEqual(cand['name'], '男女共用 スラックス')
            self.assertEqual(cand['color'], 'ブルー')
            self.assertEqual(cand['position'], '下')
            self.assertEqual(cand['plannedRegisteredSku'], 'VEH30B下')
            self.assertEqual(cand.get('supplement') or '', '')
            self.assertEqual(live.get('manufacturer'), 'ミドリ安全')
            # 検索用メーカー品番は基礎のまま。登録予定は末尾付き
            self.assertEqual(live.get('makerSku'), 'VEH30B')
            self.assertEqual(live.get('plannedRegisteredSku') or cand['plannedRegisteredSku'], 'VEH30B下')
        finally:
            c.close()
            folder.cleanup()

    def test_does_not_adopt_upper_or_other_sku(self):
        folder, path, c = newdb()
        try:
            fake_hits = [
                {
                    'title': '男女共用　スラックス　ＶＥＨ３０Ｂ　上　ブルー',
                    'url': 'https://ec.midori-anzen.com/shop/g/gUP/',
                    'source': 'midori_ec', 'preferred': True, 'manufacturer': 'ミドリ安全',
                },
                {
                    'title': '男女共用　スラックス　ＶＥＨ３０Ｇ　下　グリーン',
                    'url': 'https://ec.midori-anzen.com/shop/g/gG/',
                    'source': 'midori_ec', 'preferred': True, 'manufacturer': 'ミドリ安全',
                },
            ]

            def fake_get(url, timeout=10):
                if 'gUP' in url:
                    return url, '<title>男女共用 スラックス ＶＥＨ３０Ｂ 上 ブルー</title>ＶＥＨ３０Ｂ'
                if 'gG' in url:
                    return url, '<title>男女共用 スラックス ＶＥＨ３０Ｇ 下 グリーン</title>ＶＥＨ３０Ｇ'
                return url, '<html></html>'

            with mock.patch.object(msl, '_midori_ec_candidates', return_value=fake_hits), \
                    mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_http_get', side_effect=fake_get):
                res = msl.search_maker_product(
                    c,
                    manufacturer='ミドリ安全',
                    maker_sku='VEH30B',
                    source_name='白下 ブルー',
                    supplier='ミドリ安全北海道㈱',
                )
            # 上・別品番ページは品番不一致（またはヒント不一致）で除外
            self.assertIn(res['status'], ('no_match', 'sku_mismatch', 'hint_reject'))
            self.assertEqual(res['candidates'], [])
        finally:
            c.close()
            folder.cleanup()

    def test_adopt_clears_white_blue_color_conflict_in_preview(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '顧客A,ミドリ安全北海道㈱,VEH30B,白下 ブルー,M',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'veh.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row = preview['rows'][0]
            pc = row.get('parenClassification') or {}
            # 「白下」は色＋上下の密着として色切り出ししない → 色はブルーのみ
            self.assertFalse(pc.get('needsConfirm'), pc)
            self.assertEqual(pc.get('plannedName'), '白下')
            self.assertEqual(pc.get('color'), 'ブルー')
            reasons = ' '.join(pc.get('confirmReasons') or row.get('reasons') or [])
            self.assertNotIn('白', reasons)

            # メーカー採用相当の手編集（上下は品番末尾、補足へは載せない）
            preview2 = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'veh.csv',
                'rowClassifications': {
                    str(row['rowNo']): {
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
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row2 = preview2['rows'][0]
            pc2 = row2.get('parenClassification') or {}
            self.assertEqual(pc2.get('plannedName'), '男女共用 スラックス')
            self.assertEqual(pc2.get('color'), 'ブルー')
            self.assertNotEqual(pc2.get('supplement'), '下')
            self.assertEqual((row2.get('csv') or {}).get('code'), 'VEH30B下')
            self.assertFalse(pc2.get('needsConfirm'), pc2)
            # サイズ等は残る
            self.assertEqual((row2.get('csv') or {}).get('size'), 'M')
            self.assertEqual((row2.get('csv') or {}).get('productName'), '白下 ブルー')
            # メーカー採用後も白を色競合として残さない
            reasons2 = ' '.join(pc2.get('confirmReasons') or [])
            self.assertNotIn('白', reasons2)
        finally:
            c.close()
            folder.cleanup()

    def test_saved_not_overwritten_on_research(self):
        folder, path, c = newdb()
        try:
            msl.adopt_maker_correction(
                c,
                manufacturer='ミドリ安全',
                maker_sku='VEH30B',
                name='男女共用 スラックス',
                color='ブルー',
                position='下',
                planned_registered_sku='VEH30B下',
                source_url='https://ec.midori-anzen.com/shop/g/g31131914/',
            )
            saved, changed = msl.save_adopted_correction(
                c,
                manufacturer='ミドリ安全',
                maker_sku='VEH30B下',
                adopted_name='勝手な上書き',
                adopted_color='赤',
                overwrite_manual=False,
            )
            self.assertFalse(changed)
            self.assertEqual(saved['adopted_name'], '男女共用 スラックス')
        finally:
            c.close()
            folder.cleanup()


class MakerLookupUiSourceTest(unittest.TestCase):
    def test_source_name_and_supplement_wired(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('sourceName', js)
        self.assertIn('currentSupplement', js)
        self.assertIn('plannedRegisteredSku', js)
        self.assertIn('source_name', server)
        self.assertIn('supplement=', server)
        self.assertIn('planned_registered_sku', server)


if __name__ == '__main__':
    unittest.main()
