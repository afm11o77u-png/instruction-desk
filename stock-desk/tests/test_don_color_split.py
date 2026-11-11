import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli
import product_ledger_paren as plp
import stock

class TestDonColorSplit(unittest.TestCase):
    def test_paren_purchase_note_classified(self):
        """「(購入)」が注記として商品名から外れ、商品名が「つなぎ服」に正規化されること。"""
        res1 = plp.classify_product_name('つなぎ服(購入)')
        self.assertEqual(res1['plannedName'], 'つなぎ服')
        self.assertEqual(res1['unclassified'], [])

        res2 = plp.split_name_and_color('つなぎ服 オレンジ(購入)')
        self.assertEqual(res2['plannedName'], 'つなぎ服')
        self.assertEqual(res2['color'], 'オレンジ')
        self.assertEqual(res2['unclassified'], [])

    def test_don_111_color_rules(self):
        """色番表の解決検証。"""
        cases = [
            ('ブルー', 'DON NO.111-1', 'DON NO.111-1', 'ブルー'),
            ('青', 'DON NO.111-1', 'DON NO.111-1', 'ブルー'),
            ('ネイビー', 'DON NO.111-2', 'DON NO.111-2', 'ネイビー'),
            ('ブラック', 'DON NO.111-5', 'DON NO.111-5', 'ブラック'),
            ('ライトグレー', 'DON NO.111-7', 'DON NO.111-7', 'ライトグレー'),
            ('レッド', 'DON NO.111-10', 'DON NO.111-10', 'レッド'),
            ('オレンジ', 'DON NO.111-33', 'DON NO.111-33', 'オレンジ'),
            ('グリーン', 'DON NO.117-4', 'DON NO.117-4', 'グリーン'),
            ('緑', 'DON NO.117-4', 'DON NO.117-4', 'グリーン'),
        ]
        for color_input, exp_code, exp_maker, exp_color in cases:
            rule = pli._resolve_don_111_color_rule(color_input)
            self.assertIsNotNone(rule, f"Rule not found for {color_input}")
            self.assertEqual(rule['code'], exp_code)
            self.assertEqual(rule['makerCode'], exp_maker)
            self.assertEqual(rule['color'], exp_color)

    def test_don_auto_color_split_in_preview_and_commit(self):
        """DON NO.111 のプレビューおよび確定登録で自動色別分割・品番・商品名・メモが正しく登録されること。"""
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            conn = stock.connect(path)
            stock.initialize(conn)
            pli.ensure_schema(conn)

            csv_text = (
                "顧客,仕入先,商品コード,商品名,サイズ\n"
                "Umiosu 札幌工場,フジ総合安全㈱,DON NO.111,つなぎ服 オレンジ(購入),L\n"
                "Umiosu 札幌工場,フジ総合安全㈱,DON NO.111,つなぎ服 グリーン(購入),L\n"
                "Umiosu 札幌工場,フジ総合安全㈱,DON NO.111,つなぎ服 ネイビー(購入),L\n"
                "Umiosu 札幌工場,フジ総合安全㈱,DON NO.111,つなぎ服 ブラック(購入),L\n"
                "Umiosu 札幌工場,フジ総合安全㈱,DON NO.111,つなぎ服 レッド(購入),L\n"
                "Umiosu 札幌工場,フジ総合安全㈱,DON NO.111,つなぎ服 ブルー,L\n"
            )
            body = {
                'importMode': 'products',
                'name': 'don_test.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview = pli.build_preview(conn, body)
            by_code = {g['code']: g for g in preview.get('skuGroups') or []}
            g = by_code.get('DON NO.111')
            self.assertIsNotNone(g)
            self.assertTrue(g.get('colorSplit'), "DON NO.111 should be automatically colorSplit")

            plans = (g.get('colorSplitPlans') or {}).get('plans') or []
            self.assertEqual(len(plans), 6)
            plans_by_color = {p['color']: p for p in plans}

            # ブルー (色番 1)
            p_blue = plans_by_color.get('ブルー')
            self.assertIsNotNone(p_blue)
            self.assertEqual(p_blue['internalCode'], 'DON NO.111-1')
            self.assertEqual(p_blue['productName'], 'つなぎ服')
            self.assertEqual(p_blue['notes'], '購入')

            # グリーン (DON NO.117 色番 4)
            p_green = plans_by_color.get('グリーン')
            self.assertIsNotNone(p_green)
            self.assertEqual(p_green['internalCode'], 'DON NO.117-4')
            self.assertEqual(p_green['makerCode'], 'DON NO.117-4')
            self.assertEqual(p_green['productName'], 'つなぎ服')
            self.assertEqual(p_green['notes'], '購入')

            # オレンジ (色番 33)
            p_orange = plans_by_color.get('オレンジ')
            self.assertIsNotNone(p_orange)
            self.assertEqual(p_orange['internalCode'], 'DON NO.111-33')
            self.assertEqual(p_orange['productName'], 'つなぎ服')
            self.assertEqual(p_orange['notes'], '購入')

            # レッド (色番 10)
            p_red = plans_by_color.get('レッド')
            self.assertIsNotNone(p_red)
            self.assertEqual(p_red['internalCode'], 'DON NO.111-10')
            self.assertEqual(p_red['productName'], 'つなぎ服')
            self.assertEqual(p_red['notes'], '購入')

            # 全行が確定可能（要確認0件）
            for row in preview['rows']:
                self.assertTrue(row.get('canCommit'), f"Row {row['rowNo']} should be ready to commit")
                self.assertNotEqual(row.get('status'), 'review', f"Row {row['rowNo']} should not be review")

            # コミット実行
            body['token'] = 'test-token-don-111'
            body['previewFingerprint'] = preview['previewFingerprint']
            body['previewAt'] = preview['previewAt']
            res = pli.commit_preview(conn, body)
            self.assertEqual(res.get('counts', {}).get('productsCreated'), 6)

            # DB保存結果の検証
            db_prods = {r['code']: dict(r) for r in conn.execute('SELECT code, maker_code, name, color FROM products').fetchall()}
            self.assertIn('DON NO.111-1', db_prods)
            self.assertEqual(db_prods['DON NO.111-1']['name'], 'つなぎ服')
            self.assertEqual(db_prods['DON NO.111-1']['color'], 'ブルー')

            self.assertIn('DON NO.117-4', db_prods)
            self.assertEqual(db_prods['DON NO.117-4']['maker_code'], 'DON NO.117-4')
            self.assertEqual(db_prods['DON NO.117-4']['name'], 'つなぎ服')
            self.assertEqual(db_prods['DON NO.117-4']['color'], 'グリーン')

        finally:
            conn.close()
            folder.cleanup()

    def test_hg2000n_super_color_split(self):
        """HG2000Nスーパーの基本品番抽出および色別分割（品番-色）の検証。"""
        import code_suffix_attributes as suffix
        # 単体分類テスト
        b = suffix.classify_suffix('HG2000Nスーパー　ブルー')
        self.assertTrue(b['apply'])
        self.assertEqual(b['baseCode'], 'HG2000Nスーパー')
        self.assertEqual(b['color'], 'ブルー')
        self.assertFalse(b['needsConfirm'])

        w = suffix.classify_suffix('HG2000Nスーパー　ホワイト')
        self.assertTrue(w['apply'])
        self.assertEqual(w['baseCode'], 'HG2000Nスーパー')
        self.assertEqual(w['color'], 'ホワイト')
        self.assertFalse(w['needsConfirm'])

        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            conn = stock.connect(path)
            stock.initialize(conn)
            pli.ensure_schema(conn)

            csv_text = (
                "顧客,仕入先,商品コード,商品名,サイズ\n"
                "㈱深川未来ファーム(販売),ミドリ安全北海道㈱,HG2000Nスーパー　ブルー,超耐滑長靴杯グリップ（販売）,23.5㎝\n"
                "㈱深川未来ファーム(販売),ミドリ安全北海道㈱,HG2000Nスーパー　ホワイト,超耐滑長靴杯グリップ（販売）,23.5㎝\n"
            )
            body = {
                'importMode': 'products',
                'name': 'hg_test.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview = pli.build_preview(conn, body)
            by_code = {g['code']: g for g in preview.get('skuGroups') or []}
            g = by_code.get('HG2000Nスーパー')
            self.assertIsNotNone(g)
            self.assertTrue(g.get('colorSplit'))

            plans = {p['color']: p for p in (g.get('colorSplitPlans') or {}).get('plans') or []}
            self.assertEqual(plans['ブルー']['internalCode'], 'HG2000Nスーパー-ブルー')
            self.assertEqual(plans['ブルー']['makerCode'], 'HG2000Nスーパー')
            self.assertEqual(plans['ホワイト']['internalCode'], 'HG2000Nスーパー-ホワイト')
            self.assertEqual(plans['ホワイト']['makerCode'], 'HG2000Nスーパー')

            # 要確認行が0件であること
            review_rows = [r for r in preview['rows'] if r.get('status') == 'review']
            self.assertEqual(len(review_rows), 0)

            # 確定コミット
            body['token'] = 'test-token-hg2000'
            body['previewFingerprint'] = preview['previewFingerprint']
            body['previewAt'] = preview['previewAt']
            res = pli.commit_preview(conn, body)
            self.assertEqual(res.get('counts', {}).get('productsCreated'), 2)

            db_prods = {r['code']: dict(r) for r in conn.execute('SELECT code, maker_code, name, color FROM products').fetchall()}
            self.assertIn('HG2000Nスーパー-ブルー', db_prods)
            self.assertEqual(db_prods['HG2000Nスーパー-ブルー']['maker_code'], 'HG2000Nスーパー')
            self.assertEqual(db_prods['HG2000Nスーパー-ブルー']['color'], 'ブルー')

            self.assertIn('HG2000Nスーパー-ホワイト', db_prods)
            self.assertEqual(db_prods['HG2000Nスーパー-ホワイト']['maker_code'], 'HG2000Nスーパー')
            self.assertEqual(db_prods['HG2000Nスーパー-ホワイト']['color'], 'ホワイト')
        finally:
            conn.close()
            folder.cleanup()

    def test_suffix_kk7122_and_mm120_and_km8397(self):
        """KK7122 (LL/XL), MM120 MM121へ変更, KM-8397 タイポ修正のテスト。"""
        import code_suffix_attributes as csa

        # 1. KK7122-16 (LL) with size 'XL' -> apply=True, baseCode='KK7122-16', note='LL', needsConfirm=False
        res_kk = csa.classify_suffix('KK7122-16 (LL)', product_name='女性用カーディガン', supplier='石屋商事㈱', size='XL')
        self.assertTrue(res_kk.get('apply'))
        self.assertEqual(res_kk.get('baseCode'), 'KK7122-16')
        self.assertEqual(res_kk.get('note'), 'LL')
        self.assertFalse(res_kk.get('needsConfirm'))

        # 2. MM120 MM121へ変更 -> apply=True, baseCode='MM120', note='MM121へ変更', needsConfirm=False
        res_mm = csa.classify_suffix('MM120 MM121へ変更', product_name='白衣', supplier='回立㈱')
        self.assertTrue(res_mm.get('apply'))
        self.assertEqual(res_mm.get('baseCode'), 'MM120')
        self.assertEqual(res_mm.get('note'), 'MM121へ変更')
        self.assertFalse(res_mm.get('needsConfirm'))

        # 3. KM-8397 typo correction -> baseCode='KM-8379'
        res_km = csa.classify_suffix('KM-8397', product_name='ジャケット', supplier='チトセ㈱')
        self.assertTrue(res_km.get('apply'))
        self.assertEqual(res_km.get('baseCode'), 'KM-8379')
        self.assertFalse(res_km.get('needsConfirm'))

    def test_km8379_and_km8399_auto_color_split(self):
        """KM-8397(->KM-8379) と KM-8399 が自動でブラック/ネイビーに色別分割登録されること。"""
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            conn = stock.connect(path)
            stock.initialize(conn)
            pli.ensure_schema(conn)

            csv_text = (
                "顧客,仕入先,商品コード,商品名,サイズ\n"
                "Umiosu 札幌工場,チトセ㈱,KM-8397,ジャケット ネイビー,M\n"
                "Umiosu 札幌工場,チトセ㈱,KM-8397,ジャケット ブラック,L\n"
                "Umiosu 札幌工場,チトセ㈱,KM-8399,テーパードパンツ 紺,M\n"
                "Umiosu 札幌工場,チトセ㈱,KM-8399,テーパードパンツ 黒,L\n"
            )
            body = {
                'importMode': 'products',
                'name': 'km_test.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview = pli.build_preview(conn, body)
            by_code = {g['code']: g for g in preview.get('skuGroups') or []}

            # KM-8397 は KM-8379 に自動置換されてグループ化
            self.assertIn('KM-8379', by_code)
            g_8379 = by_code['KM-8379']
            self.assertTrue(g_8379.get('colorSplit'))
            plans_8379 = {p['color']: p for p in (g_8379.get('colorSplitPlans') or {}).get('plans') or []}
            self.assertEqual(plans_8379['ネイビー']['internalCode'], 'KM-8379-ネイビー')
            self.assertEqual(plans_8379['ネイビー']['makerCode'], 'KM-8379')
            self.assertEqual(plans_8379['ネイビー']['productName'], 'ジャケット')
            self.assertEqual(plans_8379['ブラック']['internalCode'], 'KM-8379-ブラック')
            self.assertEqual(plans_8379['ブラック']['makerCode'], 'KM-8379')
            self.assertEqual(plans_8379['ブラック']['productName'], 'ジャケット')

            # KM-8399
            self.assertIn('KM-8399', by_code)
            g_8399 = by_code['KM-8399']
            self.assertTrue(g_8399.get('colorSplit'))
            plans_8399 = {p['color']: p for p in (g_8399.get('colorSplitPlans') or {}).get('plans') or []}
            self.assertEqual(plans_8399['ネイビー']['internalCode'], 'KM-8399-ネイビー')
            self.assertEqual(plans_8399['ネイビー']['makerCode'], 'KM-8399')
            self.assertEqual(plans_8399['ネイビー']['productName'], 'テーパードパンツ')
            self.assertEqual(plans_8399['ブラック']['internalCode'], 'KM-8399-ブラック')
            self.assertEqual(plans_8399['ブラック']['makerCode'], 'KM-8399')
            self.assertEqual(plans_8399['ブラック']['productName'], 'テーパードパンツ')

            # 要確認行が0件であること
            review_rows = [r for r in preview['rows'] if r.get('status') == 'review']
            self.assertEqual(len(review_rows), 0)

            # 確定コミット
            body['token'] = 'test-token-km'
            body['previewFingerprint'] = preview['previewFingerprint']
            body['previewAt'] = preview['previewAt']
            res = pli.commit_preview(conn, body)
            self.assertEqual(res.get('counts', {}).get('productsCreated'), 4)

            db_prods = {r['code']: dict(r) for r in conn.execute('SELECT code, maker_code, name, color FROM products').fetchall()}
            self.assertIn('KM-8379-ネイビー', db_prods)
            self.assertIn('KM-8379-ブラック', db_prods)
            self.assertIn('KM-8399-ネイビー', db_prods)
            self.assertIn('KM-8399-ブラック', db_prods)
        finally:
            conn.close()
            folder.cleanup()

    def test_mup1990_nvp141_ov9002_pk397(self):
        """MUP1990刺繍, NVP141(トムス), OV9002-6(ｵﾘｰﾌﾞ), PK397-53(マスカットグリーン) のテスト。"""
        import code_suffix_attributes as csa

        # 1. MUP1990刺繍 -> baseCode='MUP1990', needsConfirm=False
        res_mup = csa.classify_suffix('MUP1990刺繍', product_name='白衣', supplier='㈱シーズン大阪')
        self.assertTrue(res_mup.get('apply'))
        self.assertEqual(res_mup.get('baseCode'), 'MUP1990')
        self.assertFalse(res_mup.get('needsConfirm'))

        # 2. NVP141(トムス) -> baseCode='NVP141', note='トムス', needsConfirm=False
        res_nvp = csa.classify_suffix('NVP141(トムス)', product_name='ポロシャツ', supplier='㈱サーヴォ')
        self.assertTrue(res_nvp.get('apply'))
        self.assertEqual(res_nvp.get('baseCode'), 'NVP141')
        self.assertEqual(res_nvp.get('note'), 'トムス')
        self.assertFalse(res_nvp.get('needsConfirm'))

        # 3. OV9002-6(ｵﾘｰﾌﾞ) -> baseCode='OV9002-6', color='オリーブ', needsConfirm=False
        res_ov = csa.classify_suffix('OV9002-6(ｵﾘｰﾌﾞ)', product_name='キャスケット', supplier='住商モンブラン㈱')
        self.assertTrue(res_ov.get('apply'))
        self.assertEqual(res_ov.get('baseCode'), 'OV9002-6')
        self.assertEqual(res_ov.get('color'), 'オリーブ')
        self.assertFalse(res_ov.get('needsConfirm'))

        # 4. PK397-53 かつ 商品名 ポロシャツ(マスカットグリーン)
        res_name = plp.classify_product_name('ポロシャツ(マスカットグリーン)')
        self.assertEqual(res_name['plannedName'], 'ポロシャツ')
        self.assertEqual(res_name['color'], 'マスカットグリーン')
        self.assertEqual(res_name['processing'], '')
        self.assertFalse(res_name['unclassified'])

        # プレビュー全体で PK397-53 に加工サービス警告が出ず、正常に確定できること
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            conn = stock.connect(path)
            stock.initialize(conn)
            pli.ensure_schema(conn)

            csv_text = (
                "顧客,仕入先,商品コード,商品名,サイズ\n"
                "Umiosu 札幌工場,㈱札幌白衣,PK397-53,ポロシャツ(マスカットグリーン),M\n"
                "Umiosu 札幌工場,㈱シーズン大阪,MUP1990刺繍,白衣,L\n"
                "Umiosu 札幌工場,㈱サーヴォ,NVP141(トムス),ポロシャツ,LL\n"
                "Umiosu 札幌工場,住商モンブラン㈱,OV9002-6(ｵﾘｰﾌﾞ),キャスケット,F\n"
            )
            body = {
                'importMode': 'products',
                'name': 'uniform_test.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview = pli.build_preview(conn, body)
            review_rows = [r for r in preview['rows'] if r.get('status') == 'review']
            self.assertEqual(len(review_rows), 0, f"Review rows should be 0, but got: {review_rows}")

            # 4行とも commit 可能
            for r in preview['rows']:
                self.assertTrue(r.get('canCommit'), f"Row {r.get('rowNo')} cannot commit: {r.get('reasons')}")
                if r['csv']['code'] == 'PK397-53':
                    self.assertEqual(r['product'].get('officialColor'), 'マスカットグリーン')
                    self.assertEqual(r['product'].get('officialName'), 'ポロシャツ')
                elif r['csv']['code'] == 'MUP1990':
                    self.assertEqual(r['product'].get('officialName') or r['product'].get('name'), '白衣')
                elif r['csv']['code'] == 'NVP141':
                    self.assertEqual(r.get('suffixAttribute', {}).get('note') or r['csv'].get('notes'), 'トムス')
                elif r['csv']['code'] == 'OV9002-6':
                    self.assertEqual(r['product'].get('officialColor') or r.get('suffixAttribute', {}).get('color'), 'オリーブ')

            # 確定コミット
            body['token'] = 'test-token-uniform'
            body['previewFingerprint'] = preview['previewFingerprint']
            body['previewAt'] = preview['previewAt']
            res = pli.commit_preview(conn, body)
            self.assertEqual(res.get('counts', {}).get('productsCreated'), 4)

            db_prods = {r['code']: dict(r) for r in conn.execute('SELECT code, name, color FROM products').fetchall()}
            self.assertIn('PK397-53', db_prods)
            self.assertEqual(db_prods['PK397-53']['name'], 'ポロシャツ')
            self.assertEqual(db_prods['PK397-53']['color'], 'マスカットグリーン')

            self.assertIn('MUP1990', db_prods)
            self.assertIn('NVP141', db_prods)
            self.assertIn('OV9002-6', db_prods)
            self.assertEqual(db_prods['OV9002-6']['color'], 'オリーブ')
        finally:
            conn.close()
            folder.cleanup()

    def test_sic_parts_import_and_deduplication(self):
        """SIC-9415 M #19/#23/#26 が部品・資材として制服・部品フェーズ両方で取り込まれ、重複時は再作成しないこと。"""
        import code_suffix_attributes as csa

        for code in ('SIC-9415 M #19', 'SIC-9415 M #23', 'SIC-9415 M #26'):
            res = csa.classify_suffix(code, product_name='帽子用ライン 30m巻', supplier='㈱脇本商事')
            self.assertFalse(res.get('needsConfirm'))
            self.assertTrue(res.get('registerAsParts'))
            self.assertEqual(res.get('baseCode'), code)

        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            conn = stock.connect(path)
            stock.initialize(conn)
            pli.ensure_schema(conn)

            csv_text = (
                "顧客,仕入先,商品コード,商品名,サイズ\n"
                "Umiosu 札幌工場,㈱脇本商事,SIC-9415 M #19,帽子用ライン 30m巻,-\n"
                "Umiosu 札幌工場,㈱脇本商事,SIC-9415 M #23,帽子用ライン 30m巻,-\n"
                "Umiosu 札幌工場,㈱脇本商事,SIC-9415 M #26,帽子用ライン 30m巻,-\n"
            )
            # 1. 制服取り込み時（importMode='products', phaseなし / garments）で部品として取り込めること
            body_uniform = {
                'importMode': 'products',
                'name': 'sic_test.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview_u = pli.build_preview(conn, body_uniform)
            review_rows_u = [r for r in preview_u['rows'] if r.get('status') == 'review']
            self.assertEqual(len(review_rows_u), 0)
            self.assertEqual(len(preview_u['rows']), 3)
            for r in preview_u['rows']:
                self.assertEqual(r['product']['category'], stock.CATEGORY_PARTS)
                self.assertTrue(r.get('canCommit'))

            # 確定コミット
            body_uniform['token'] = 'sic-token-1'
            body_uniform['previewFingerprint'] = preview_u['previewFingerprint']
            body_uniform['previewAt'] = preview_u['previewAt']
            res_u = pli.commit_preview(conn, body_uniform)
            self.assertEqual(res_u.get('counts', {}).get('productsCreated'), 3)

            db_parts = {r['code']: dict(r) for r in conn.execute('SELECT code, name, category FROM products').fetchall()}
            self.assertIn('SIC-9415 M #19', db_parts)
            self.assertEqual(db_parts['SIC-9415 M #19']['category'], stock.CATEGORY_PARTS)
            self.assertIn('SIC-9415 M #23', db_parts)
            self.assertIn('SIC-9415 M #26', db_parts)

            # 2. 部品取り込み時（phase='parts'）でも漏れなく、かつ既存がある場合は重複新規登録しない（action='reuse'）
            body_parts = {
                'importMode': 'products',
                'productPhase': 'parts',
                'name': 'sic_test_parts.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview_p = pli.build_preview(conn, body_parts)
            self.assertEqual(len(preview_p['rows']), 3)
            for r in preview_p['rows']:
                # すでにDBにあるため reuse
                self.assertEqual(r['product']['action'], 'reuse')
                self.assertEqual(r['product']['category'], stock.CATEGORY_PARTS)
                self.assertIsNotNone(r['product']['id'])

            # 確定コミットしても新規登録は0件（重複登録されない）
            body_parts['token'] = 'sic-token-2'
            body_parts['previewFingerprint'] = preview_p['previewFingerprint']
            body_parts['previewAt'] = preview_p['previewAt']
            res_p = pli.commit_preview(conn, body_parts)
            self.assertEqual(res_p.get('counts', {}).get('productsCreated'), 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) c FROM products').fetchone()['c'], 3)
        finally:
            conn.close()
            folder.cleanup()

    def test_moss_green_and_t8091_color_split(self):
        """T-7501 C-62 ﾓｽｸﾞﾘｰﾝ と T-8091 (紺/黒) のテスト。"""
        import code_suffix_attributes as csa

        # 1. T-7501 C-62 ﾓｽｸﾞﾘｰﾝ -> apply=True, baseCode='T-7501 C-62', color='モスグリーン', needsConfirm=False
        res_t7501 = csa.classify_suffix('T-7501 C-62 ﾓｽｸﾞﾘｰﾝ', product_name='エプロン', supplier='チトセ㈱')
        self.assertTrue(res_t7501.get('apply'))
        self.assertEqual(res_t7501.get('baseCode'), 'T-7501 C-62')
        self.assertEqual(res_t7501.get('color'), 'モスグリーン')
        self.assertFalse(res_t7501.get('needsConfirm'))

        # 2. プレビューでの T-7501 と T-8091
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            conn = stock.connect(path)
            stock.initialize(conn)
            pli.ensure_schema(conn)

            csv_text = (
                "顧客,仕入先,商品コード,商品名,サイズ\n"
                "Umiosu 札幌工場,チトセ㈱,T-7501 C-62 ﾓｽｸﾞﾘｰﾝ,エプロン,F\n"
                "Umiosu 札幌工場,チトセ㈱,T-8091,胸当てエプロン 紺,F\n"
                "Umiosu 札幌工場,チトセ㈱,T-8091,胸当てエプロン 黒,F\n"
            )
            body = {
                'importMode': 'products',
                'name': 't_test.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview = pli.build_preview(conn, body)
            review_rows = [r for r in preview['rows'] if r.get('status') == 'review']
            self.assertEqual(len(review_rows), 0, f"Review rows should be 0, but got: {review_rows}")

            by_code = {g['code']: g for g in preview.get('skuGroups') or []}
            self.assertIn('T-8091', by_code)
            g_8091 = by_code['T-8091']
            self.assertTrue(g_8091.get('colorSplit'))
            plans_8091 = {p['color']: p for p in (g_8091.get('colorSplitPlans') or {}).get('plans') or []}
            self.assertEqual(plans_8091['ネイビー']['internalCode'], 'T-8091-ネイビー')
            self.assertEqual(plans_8091['ブラック']['internalCode'], 'T-8091-ブラック')

            # 確定コミット
            body['token'] = 't-token-1'
            body['previewFingerprint'] = preview['previewFingerprint']
            body['previewAt'] = preview['previewAt']
            res = pli.commit_preview(conn, body)
            self.assertEqual(res.get('counts', {}).get('productsCreated'), 3)

            db_prods = {r['code']: dict(r) for r in conn.execute('SELECT code, name, color FROM products').fetchall()}
            self.assertIn('T-7501 C-62', db_prods)
            self.assertEqual(db_prods['T-7501 C-62']['color'], 'モスグリーン')
            self.assertIn('T-8091-ネイビー', db_prods)
            self.assertIn('T-8091-ブラック', db_prods)
        finally:
            conn.close()
            folder.cleanup()

    def test_ves_and_yt_items(self):
        """VES367新, YT1912-3ライトベージュ, YT3923-1ネイビーSK 等のテスト。"""
        import code_suffix_attributes as csa

        ves = csa.classify_suffix('VES367新', product_name='パンツ(202306~)')
        self.assertTrue(ves['apply'])
        self.assertEqual(ves['baseCode'], 'VES367')
        self.assertEqual(ves['note'], '新')
        self.assertFalse(ves['needsConfirm'])

        yt_lb = csa.classify_suffix('YT1912-3ライトベージュ', product_name='ブラウス')
        self.assertTrue(yt_lb['apply'])
        self.assertEqual(yt_lb['baseCode'], 'YT1912-3')
        self.assertEqual(yt_lb['color'], 'ライトベージュ')
        self.assertFalse(yt_lb['needsConfirm'])

        yt_sk = csa.classify_suffix('YT3923-1ネイビーSK', product_name='スカート')
        self.assertTrue(yt_sk['apply'])
        self.assertEqual(yt_sk['baseCode'], 'YT3923-1')
        self.assertEqual(yt_sk['color'], 'ネイビー')
        self.assertEqual(yt_sk['note'], 'SK')
        self.assertFalse(yt_sk['needsConfirm'])

        yt_jk = csa.classify_suffix('YT4922-1ネイビーJK', product_name='ジャケット')
        self.assertTrue(yt_jk['apply'])
        self.assertEqual(yt_jk['baseCode'], 'YT4922-1')
        self.assertEqual(yt_jk['color'], 'ネイビー')
        self.assertEqual(yt_jk['note'], 'JK')
        self.assertFalse(yt_jk['needsConfirm'])

        yt_cd = csa.classify_suffix('YT9900-3グレーCD', product_name='カーディガン')
        self.assertTrue(yt_cd['apply'])
        self.assertEqual(yt_cd['baseCode'], 'YT9900-3')
        self.assertEqual(yt_cd['color'], 'グレー')
        self.assertEqual(yt_cd['note'], 'CD')
        self.assertFalse(yt_cd['needsConfirm'])

        yt_ns = csa.classify_suffix('YT9901-3グレーNS', product_name='カーディガン')
        self.assertTrue(yt_ns['apply'])
        self.assertEqual(yt_ns['baseCode'], 'YT9901-3')
        self.assertEqual(yt_ns['color'], 'グレー')
        self.assertEqual(yt_ns['note'], 'NS')
        self.assertFalse(yt_ns['needsConfirm'])

    def test_swapped_columns_and_pure_color(self):
        """アイスパック/IP5000, グリーン/ネクタイ, コート/6-521, コックコート/6-441 のテスト。"""
        import code_suffix_attributes as csa
        import product_ledger_import as pli

        # 1. グリーン -> 品番「不明」、色「グリーン」
        pure_green = csa.classify_suffix('グリーン')
        self.assertTrue(pure_green['apply'])
        self.assertEqual(pure_green['baseCode'], '不明')
        self.assertEqual(pure_green['color'], 'グリーン')
        self.assertFalse(pure_green['needsConfirm'])

        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            conn = stock.connect(path)
            stock.initialize(conn)
            pli.ensure_schema(conn)

            csv_text = """顧客,仕入先,商品コード,商品名,サイズ,初期在庫
テスト顧客,㈱サーヴォ,アイスパック,IP5000,-,1
テスト顧客,石屋商事㈱,グリーン,ネクタイ,-,0
テスト顧客,住商モンブラン㈱,コート,6-521,-,0
テスト顧客,住商モンブラン㈱,コックコート,6-441,-,0
テスト顧客,ナガイレーベン㈱,チュニック,LH6222,-,1
テスト顧客,住商モンブラン㈱,ハッピ,3-524,-,1
テスト顧客,㈱シーズン大阪,ピンク帽子,DBP6712,-,1
テスト顧客,弘進商事㈱,シェフメイト α3000(黒),コックシューズ,-,1
テスト顧客,弘進商事㈱,シェフメイトアルファ3000(黒),衛生靴,-,1
テスト顧客,信業通商会,ｽﾚﾝｶﾗｰﾀｵﾙ OK51000 1000匁,バスタオル(タオルケット),-,1
テスト顧客,㈱札幌白衣,ポロシャツ(レモンイエロー),PK397-63,-,1
テスト顧客,㈱シーズン大阪,黄色帽子,DBP6714,-,1
テスト顧客,住商モンブラン㈱,胸当てエプロン,5-718,-,1
テスト顧客,㈱サーヴォ,紫帽子,SZ510-L,-,1
"""
            body = {
                'importMode': 'products',
                'name': 't_test2.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview = pli.build_preview(conn, body)
            rows = {r['rowNo']: r for r in preview['rows']}

            # 行2: アイスパック/IP5000 -> 品番 IP5000, 品名 アイスパック, 区分 部品・資材
            r1 = rows[2]
            self.assertEqual(r1['csv']['code'], 'IP5000')
            self.assertEqual(r1['csv']['productName'], 'アイスパック')
            self.assertEqual(r1['product']['category'], stock.CATEGORY_PARTS)
            self.assertEqual(r1['parenClassification']['registerKind'], '部品・資材')
            self.assertFalse(r1.get('suffixAttribute', {}).get('needsConfirm'))
            # CSVの列対応警告がないこと
            reasons_1 = [pli.stock.text(x) for x in r1.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_1))

            # 行3: グリーン/ネクタイ -> 品番 不明, 色 グリーン, 品名 ネクタイ
            r2 = rows[3]
            self.assertEqual(r2['csv']['code'], '不明')
            self.assertEqual(r2['product']['officialColor'], 'グリーン')
            self.assertEqual(r2['csv']['productName'], 'ネクタイ')
            self.assertFalse(r2.get('suffixAttribute', {}).get('needsConfirm'))

            # 行4: コート/6-521 -> 品番 6-521, 品名 コート
            r3 = rows[4]
            self.assertEqual(r3['csv']['code'], '6-521')
            self.assertEqual(r3['csv']['productName'], 'コート')
            self.assertFalse(r3.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_3 = [pli.stock.text(x) for x in r3.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_3))

            # 行5: コックコート/6-441 -> 品番 6-441, 品名 コックコート
            r4 = rows[5]
            self.assertEqual(r4['csv']['code'], '6-441')
            self.assertEqual(r4['csv']['productName'], 'コックコート')
            self.assertFalse(r4.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_4 = [pli.stock.text(x) for x in r4.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_4))

            # 行6: チュニック/LH6222 -> 品番 LH6222, 品名 チュニック
            r5 = rows[6]
            self.assertEqual(r5['csv']['code'], 'LH6222')
            self.assertEqual(r5['csv']['productName'], 'チュニック')
            self.assertFalse(r5.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_5 = [pli.stock.text(x) for x in r5.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_5))

            # 行7: ハッピ/3-524 -> 品番 3-524, 品名 ハッピ
            r6 = rows[7]
            self.assertEqual(r6['csv']['code'], '3-524')
            self.assertEqual(r6['csv']['productName'], 'ハッピ')
            self.assertFalse(r6.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_6 = [pli.stock.text(x) for x in r6.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_6))

            # 行8: ピンク帽子/DBP6712 -> 品番 DBP6712, 品名 ピンク帽子
            r7 = rows[8]
            self.assertEqual(r7['csv']['code'], 'DBP6712')
            self.assertEqual(r7['csv']['productName'], 'ピンク帽子')
            self.assertFalse(r7.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_7 = [pli.stock.text(x) for x in r7.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_7))

            # 行9: シェフメイト α3000(黒) -> 品番 シェフメイト α3000, 色 ブラック
            r8 = rows[9]
            self.assertEqual(r8['csv']['code'], 'シェフメイト α3000')
            self.assertEqual(r8['product']['officialColor'], 'ブラック')
            self.assertFalse(r8.get('suffixAttribute', {}).get('needsConfirm'))

            # 行10: シェフメイトアルファ3000(黒) -> 品番 シェフメイトアルファ3000, 色 ブラック
            r9 = rows[10]
            self.assertEqual(r9['csv']['code'], 'シェフメイトアルファ3000')
            self.assertEqual(r9['product']['officialColor'], 'ブラック')
            self.assertFalse(r9.get('suffixAttribute', {}).get('needsConfirm'))

            # 行11: ｽﾚﾝｶﾗｰﾀｵﾙ OK51000 1000匁 -> 半角カタカナ全角化、警告なし
            r10 = rows[11]
            self.assertEqual(r10['csv']['code'], 'スレンカラータオル OK51000 1000匁')
            self.assertFalse(r10.get('suffixAttribute', {}).get('needsConfirm'))

            # 行12: ポロシャツ(レモンイエロー)/PK397-63 -> 品番 PK397-63, 色 レモンイエロー, 品名 ポロシャツ
            r11 = rows[12]
            self.assertEqual(r11['csv']['code'], 'PK397-63')
            self.assertEqual(r11['product']['officialColor'], 'レモンイエロー')
            self.assertEqual(r11['product']['officialName'], 'ポロシャツ')
            self.assertFalse(r11.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_11 = [pli.stock.text(x) for x in r11.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_11))

            # 行13: 黄色帽子/DBP6714 -> 品番 DBP6714, 品名 黄色帽子
            r12 = rows[13]
            self.assertEqual(r12['csv']['code'], 'DBP6714')
            self.assertEqual(r12['csv']['productName'], '黄色帽子')
            self.assertFalse(r12.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_12 = [pli.stock.text(x) for x in r12.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_12))

            # 行14: 胸当てエプロン/5-718 -> 品番 5-718, 品名 胸当てエプロン
            r13 = rows[14]
            self.assertEqual(r13['csv']['code'], '5-718')
            self.assertEqual(r13['csv']['productName'], '胸当てエプロン')
            self.assertFalse(r13.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_13 = [pli.stock.text(x) for x in r13.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_13))

            # 行15: 紫帽子/SZ510-L -> 品番 SZ510-L, 品名 紫帽子
            r14 = rows[15]
            self.assertEqual(r14['csv']['code'], 'SZ510-L')
            self.assertEqual(r14['csv']['productName'], '紫帽子')
            self.assertFalse(r14.get('suffixAttribute', {}).get('needsConfirm'))
            reasons_14 = [pli.stock.text(x) for x in r14.get('reasons') or []]
            self.assertFalse(any('CSVの列対応を確認してください' in x for x in reasons_14))
        finally:
            conn.close()
            folder.cleanup()

if __name__ == '__main__':
    unittest.main()
