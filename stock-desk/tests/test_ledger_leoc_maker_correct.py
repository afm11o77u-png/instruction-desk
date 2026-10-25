"""LEOC×ユニスタイル×別注の色名補正と、メーカー情報で補正（テストDBのみ）。"""
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
import product_ledger_scoped_color as scoped  # noqa: E402
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


class ScopedColorHelpersTest(unittest.TestCase):
    def test_pattern_requires_all_three_plus_name(self):
        self.assertTrue(scoped.matches_scoped_color_correction(
            customer='㈱ＬＥＯＣ',
            supplier='(有)ユニスタイル',
            code='別注',
            product_name='ブルーエプロン (胸当・ｻｯｸｽ)',
        ))
        # 商品名の半角カナ・空白ゆれは unify で吸収
        self.assertTrue(scoped.matches_scoped_color_correction(
            customer='㈱ＬＥＯＣ',
            supplier='(有)ユニスタイル',
            code='別注',
            product_name='ブルーエプロン（胸当・サックス）',
        ))
        # 顧客だけ違う
        self.assertFalse(scoped.matches_scoped_color_correction(
            customer='他顧客',
            supplier='(有)ユニスタイル',
            code='別注',
            product_name='ブルーエプロン（胸当・サックス）',
        ))
        # 仕入先だけ違う
        self.assertFalse(scoped.matches_scoped_color_correction(
            customer='㈱ＬＥＯＣ',
            supplier='他仕入',
            code='別注',
            product_name='ブルーエプロン（胸当・サックス）',
        ))
        # 品番が別注でない
        self.assertFalse(scoped.matches_scoped_color_correction(
            customer='㈱ＬＥＯＣ',
            supplier='(有)ユニスタイル',
            code='TC1595',
            product_name='ブルーエプロン（胸当・サックス）',
        ))
        # 3条件一致でも名パターンなし（ピンク）
        self.assertFalse(scoped.matches_scoped_color_correction(
            customer='㈱ＬＥＯＣ',
            supplier='(有)ユニスタイル',
            code='別注',
            product_name='ピンクエプロン (胸当・ﾋﾟﾝｸ)',
        ))
        # ブルーだけ / サックスだけ
        self.assertFalse(scoped.is_blue_sax_apron_name('ブルーエプロン（胸当）'))
        self.assertFalse(scoped.is_blue_sax_apron_name('エプロン（サックス）'))


class ScopedColorPreviewCommitTest(unittest.TestCase):
    def test_scope_excludes_other_parties_and_other_betshu(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '㈱ＬＥＯＣ,(有)ユニスタイル,別注,ブルーエプロン (胸当・ｻｯｸｽ),S',
                '㈱ＬＥＯＣ,(有)ユニスタイル,別注,ピンクエプロン (胸当・ﾋﾟﾝｸ),S',
                '他顧客,(有)ユニスタイル,別注,ブルーエプロン (胸当・ｻｯｸｽ),S',
                '㈱ＬＥＯＣ,他仕入,別注,ブルーエプロン (胸当・ｻｯｸｽ),S',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'scope.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_no = {r['rowNo']: r for r in preview['rows']}
            self.assertTrue(
                (by_no[2].get('parenClassification') or {}).get('scopedColorCorrection')
            )
            self.assertFalse(
                (by_no[3].get('parenClassification') or {}).get('scopedColorCorrection')
            )
            self.assertFalse(
                (by_no[4].get('parenClassification') or {}).get('scopedColorCorrection')
            )
            self.assertFalse(
                (by_no[5].get('parenClassification') or {}).get('scopedColorCorrection')
            )
            # スコープ外のブルー／サックスは従来どおり要確認のまま（勝手にマージしない）
            self.assertEqual(by_no[4]['status'], 'needs_review')
            self.assertEqual(by_no[5]['status'], 'needs_review')
        finally:
            c.close()
            folder.cleanup()

    def test_leoc_blue_sax_sizes_correct_and_commit(self):
        folder, path, c = newdb()
        try:
            text = ledger_csv([
                '㈱ＬＥＯＣ,(有)ユニスタイル,別注,ブルーエプロン (胸当・ｻｯｸｽ),S',
                '㈱ＬＥＯＣ,(有)ユニスタイル,別注,ブルーエプロン (胸当・ｻｯｸｽ),M',
                '㈱ＬＥＯＣ,(有)ユニスタイル,別注,ピンクエプロン (胸当・ﾋﾟﾝｸ),S',
            ])
            before = pli.stock_snapshot(c)
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'leoc.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_no = {r['rowNo']: r for r in preview['rows']}

            for n in (2, 3):
                r = by_no[n]
                pc = r.get('parenClassification') or {}
                self.assertTrue(pc.get('scopedColorCorrection'), pc)
                self.assertEqual(pc.get('plannedName'), 'エプロン')
                self.assertEqual(pc.get('color'), 'ブルー・サックス')
                self.assertEqual(pc.get('supplement'), '胸当')
                self.assertFalse(pc.get('needsConfirm'), pc)
                self.assertIn(
                    scoped.SCOPED_REASON,
                    pc.get('changeSummary') or '',
                )
                self.assertEqual(
                    (r.get('product') or {}).get('officialName'), 'エプロン',
                )
                self.assertEqual(
                    (r.get('product') or {}).get('officialColor'), 'ブルー・サックス',
                )
                # 原文は保持（初期在庫照合用）
                self.assertEqual(
                    (r.get('csv') or {}).get('productName'),
                    'ブルーエプロン (胸当・ｻｯｸｽ)',
                )
                self.assertNotEqual(r.get('status'), 'needs_review', r.get('reasons'))

            # 同一別注のサイズ差は同じ照合グループ
            self.assertEqual(by_no[2]['matchGroupKey'], by_no[3]['matchGroupKey'])

            pink = by_no[4]
            ppc = pink.get('parenClassification') or {}
            self.assertFalse(ppc.get('scopedColorCorrection'))
            self.assertNotEqual(ppc.get('color'), 'ブルー・サックス')

            result = pli.commit_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'leoc.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-leoc-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result.get('stockUnchanged', True) or result.get('stockUnchanged') is not False)
            self.assertEqual(pli.stock_snapshot(c), before)

            # ブルーサックスは1商品（サイズまとめ）。ピンクは別商品。
            prods = list(c.execute(
                "SELECT id,name,color FROM products WHERE code='別注' AND merged_into_id IS NULL ORDER BY id"
            ))
            blue = [p for p in prods if 'ブルー・サックス' in (p['color'] or '')]
            self.assertEqual(len(blue), 1, prods)
            self.assertEqual(blue[0]['name'], 'エプロン（胸当）')
            self.assertEqual(blue[0]['color'], 'ブルー・サックス')
            # 原文別名
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (blue[0]['id'],),
                )
            }
            self.assertTrue(
                any('ブルーエプロン' in a for a in aliases),
                aliases,
            )
            sizes = {
                r['label'] for r in c.execute(
                    'SELECT label FROM product_sizes WHERE product_id=?',
                    (blue[0]['id'],),
                )
            }
            self.assertEqual(sizes, {'S', 'M'})
        finally:
            c.close()
            folder.cleanup()


class MakerSkuEligibilityTest(unittest.TestCase):
    def test_valid_and_hidden_cases(self):
        self.assertTrue(msl.is_valid_maker_sku('VEH30B'))
        self.assertTrue(msl.is_valid_maker_sku('7-881'))
        self.assertFalse(msl.is_valid_maker_sku(''))
        self.assertFalse(msl.is_valid_maker_sku('別注'))
        self.assertFalse(msl.is_valid_maker_sku('品番なし'))
        self.assertFalse(msl.is_valid_maker_sku('品番なし（要確認）'))
        self.assertFalse(msl.is_valid_maker_sku('不明'))

    def test_row_meta_color_split_keeps_maker(self):
        row = {
            'csv': {'code': 'ABC-100-赤', 'makerCode': 'ABC-100', 'productName': 'ポロ'},
            'product': {'code': 'ABC-100-赤', 'makerCode': 'ABC-100', 'colorSplit': True},
        }
        meta = msl.row_maker_correction_meta(row)
        self.assertTrue(meta['eligible'])
        self.assertEqual(meta['makerSku'], 'ABC-100')

    def test_row_meta_hides_custom_order(self):
        row = {
            'csv': {'code': '別注', 'productName': 'エプロン'},
            'product': {'code': '別注'},
        }
        meta = msl.row_maker_correction_meta(row)
        self.assertFalse(meta['eligible'])
        self.assertEqual(meta['makerSku'], '')

    def test_normal_code_used_as_maker_when_empty_maker_code(self):
        row = {
            'csv': {'code': 'VEH30B', 'productName': 'シャツ'},
            'product': {'code': 'VEH30B'},
        }
        meta = msl.row_maker_correction_meta(row)
        self.assertTrue(meta['eligible'])
        self.assertEqual(meta['makerSku'], 'VEH30B')


def _insert_dup_products(c, code='VEH30B'):
    """通常品番の重複を SQL 直挿しで作る（save_product は1品番=1商品を拒否するため）。"""
    t = stock.stamp()
    stock.save_supplier(c, {'name': '仕入A'})
    stock.save_supplier(c, {'name': '仕入B'})
    s1 = c.execute("SELECT id FROM suppliers WHERE name='仕入A'").fetchone()['id']
    s2 = c.execute("SELECT id FROM suppliers WHERE name='仕入B'").fetchone()['id']
    c.execute(
        'INSERT INTO products(code,maker_code,name,color,material,category,supplier_id,active,created_at,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?)',
        (code, '', '旧名A', '赤', '', '', s1, 1, t, t),
    )
    c.execute(
        'INSERT INTO products(code,maker_code,name,color,material,category,supplier_id,active,created_at,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?)',
        (code, '', '旧名B', '青', '', '', s2, 1, t, t),
    )
    c.commit()


class MakerLookupFlowTest(unittest.TestCase):
    def test_preview_exposes_button_flag_only_on_needs_review_eligible(self):
        folder, path, c = newdb()
        try:
            _insert_dup_products(c, 'VEH30B')
            text = ledger_csv([
                '顧客A,仕入A,VEH30B,ポロシャツ,M',
                '顧客A,仕入A,別注,青帽子,F',
            ])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'mk.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_no = {r['rowNo']: r for r in preview['rows']}
            veh = by_no[2]
            self.assertEqual(veh['status'], 'needs_review')
            self.assertTrue((veh.get('makerCorrection') or {}).get('eligible'))
            self.assertEqual((veh.get('makerCorrection') or {}).get('makerSku'), 'VEH30B')

            bet = by_no[3]
            self.assertFalse((bet.get('makerCorrection') or {}).get('eligible'))
        finally:
            c.close()
            folder.cleanup()

    def test_search_statuses_and_adopt_persists_without_overwrite_on_research(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            # ネットワーク無しでも保存済み経路を検証
            saved, changed = msl.save_adopted_correction(
                c,
                manufacturer='テストメーカー',
                maker_sku='VEH30B',
                adopted_name='公式ポロ',
                adopted_color='ネイビー',
                source_url='https://example.com/veh30b',
                overwrite_manual=True,
            )
            self.assertTrue(changed)
            self.assertEqual(saved['adopted_name'], '公式ポロ')

            with mock.patch.object(msl, '_ddg_candidates', side_effect=OSError('net down')), \
                    mock.patch.object(msl, '_catalog_searches_for', side_effect=OSError('net down')), \
                    mock.patch.object(msl, '_midori_ec_candidates', side_effect=OSError('net down')):
                res = msl.search_maker_product(
                    c,
                    manufacturer='テストメーカー',
                    maker_sku='VEH30B',
                    current_name='旧',
                    current_color='',
                    supplier='仕入A',
                )
            self.assertEqual(res['status'], 'fetch_failed')
            self.assertEqual(res['statusLabel'], '検索結果取得不可')
            self.assertTrue(res.get('referenceUrl'))
            self.assertEqual(res['supplierIgnored'], '仕入A')
            # 保存済みは残る
            self.assertEqual((res.get('saved') or {}).get('name'), '公式ポロ')

            # 再検索で手動確定を上書きしない
            saved2, changed2 = msl.save_adopted_correction(
                c,
                manufacturer='テストメーカー',
                maker_sku='VEH30B',
                adopted_name='勝手な上書き',
                adopted_color='赤',
                source_url='https://example.com/other',
                overwrite_manual=False,
            )
            self.assertFalse(changed2)
            self.assertEqual(saved2['adopted_name'], '公式ポロ')

            # 採用 API 相当は上書き可
            adopted = msl.adopt_maker_correction(
                c,
                manufacturer='テストメーカー',
                maker_sku='VEH30B',
                name='採用後ポロ',
                color='白',
                source_url='https://example.com/new',
            )
            self.assertEqual(adopted['name'], '採用後ポロ')
            row = msl.lookup_saved_correction(
                c, manufacturer='テストメーカー', maker_sku='VEH30B',
            )
            self.assertEqual(row['adopted_name'], '採用後ポロ')
            self.assertEqual(row['adopted_color'], '白')

            # 候補なし
            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=[]), \
                    mock.patch.object(msl, '_midori_ec_candidates', return_value=[]):
                none = msl.search_maker_product(
                    c, manufacturer='', maker_sku='ZZZ999NOMATCH',
                )
            self.assertEqual(none['status'], 'no_match')

            # 複数候補（掲載名に品番＋色を含める）
            fake_hits = [
                {
                    'title': '公式シャツ ZZ1 白',
                    'url': 'https://servo.example/zz1w',
                    'preferred': True, 'source': 'midori_ec', 'manufacturer': 'テスト',
                },
                {
                    'title': '公式シャツ ZZ1 黒',
                    'url': 'https://shop.example/zz1k',
                    'preferred': False, 'source': 'sales_page', 'manufacturer': 'テスト',
                },
            ]

            def fake_get(url, timeout=10):
                if 'zz1w' in url:
                    return url, '<html><title>公式シャツ ZZ1 白</title><body>ZZ1 白</body></html>'
                if 'zz1k' in url:
                    return url, '<html><title>公式シャツ ZZ1 黒</title><body>ZZ1 黒</body></html>'
                return url, '<html></html>'

            with mock.patch.object(msl, '_ddg_candidates', return_value=[]), \
                    mock.patch.object(msl, '_catalog_searches_for', return_value=fake_hits), \
                    mock.patch.object(msl, '_http_get', side_effect=fake_get):
                multi = msl.search_maker_product(
                    c, manufacturer='テスト', maker_sku='ZZ1',
                    current_name='仮', current_color='',
                )
            self.assertEqual(multi['status'], 'multiple')
            self.assertGreaterEqual(len(multi['candidates']), 2)
        finally:
            c.close()
            folder.cleanup()

    def test_adopt_updates_preview_only_via_row_classification(self):
        folder, path, c = newdb()
        try:
            _insert_dup_products(c, 'VEH30B')
            text = ledger_csv(['顧客A,仕入A,VEH30B,ポロシャツ,M'])
            preview = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'mk2.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row = preview['rows'][0]
            before_name = (row.get('parenClassification') or {}).get('plannedName')
            # 採用前は予定がメーカー採用名ではない
            self.assertNotEqual(before_name, '公式ポロ採用')

            preview2 = pli.build_preview(c, {
                'importMode': 'products',
                'file': b64(text), 'name': 'mk2.csv',
                'rowClassifications': {
                    '2': {
                        'plannedName': '公式ポロ採用',
                        'color': 'ネイビー',
                        'userEdited': True,
                        'confirmed': True,
                        'acceptProposal': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row2 = preview2['rows'][0]
            pc2 = row2.get('parenClassification') or {}
            self.assertEqual(pc2.get('plannedName'), '公式ポロ採用')
            self.assertEqual(pc2.get('color'), 'ネイビー')
            # まだ DB の商品名は旧名のまま（プレビューのみ）
            names = {
                r['name'] for r in c.execute(
                    "SELECT name FROM products WHERE code='VEH30B'"
                )
            }
            self.assertNotIn('公式ポロ採用', names)
        finally:
            c.close()
            folder.cleanup()


class MakerUiSourceTest(unittest.TestCase):
    def test_button_and_endpoints_wired(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        scoped_py = (ROOT / 'product_ledger_scoped_color.py').read_text(encoding='utf-8')
        self.assertIn('メーカー情報で補正', js)
        self.assertIn('data-pl-maker-correct', js)
        self.assertIn('data-pl-maker-adopt', js)
        self.assertIn('/api/product-ledger-csv/maker-lookup', js)
        self.assertIn('/api/product-ledger-csv/maker-lookup/adopt', js)
        self.assertIn('登録予定の補足', js)
        self.assertIn('data-pl-paren-supp', js)
        self.assertIn('指定した顧客・仕入先・別注商品の色名補正を適用', scoped_py)
        self.assertIn("'/api/product-ledger-csv/maker-lookup'", server)
        self.assertIn("'/api/product-ledger-csv/maker-lookup/adopt'", server)


if __name__ == '__main__':
    unittest.main()
