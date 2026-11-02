"""部品・加工サービス: サイズ・色・取扱を台帳取込で作らない（テストDBのみ）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import legacy_csv_bulk_import as bulk  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as proc_svc  # noqa: E402
import stock  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb  # noqa: E402

ROWS = ['よつ葉乳業,株サーヴォ,,刺繍加工,',
        'よつ葉乳業,丸和,,刺繍加工,L',
        'よつ葉乳業,株サーヴォ,DCP600,作業服上(刺繍),L',
        'よつ葉乳業,株サーヴォ,DCP600,作業服上(刺繍),M',
        'よつ葉乳業,YKK,SN10,スナッパー(赤),10mm',
        'よつ葉乳業,YKK,SN10,スナッパー(白),12mm',
        'よつ葉乳業,YKK,,ボタン,',
        'よつ葉乳業,YKK,,ボタン,L']


class Base(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def run_phase(self, phase, tok):
        b = {'importMode': 'products', 'name': 'n.csv', 'file': b64(ledger_csv(ROWS)),
             '_canWrite': True, '_actor': {'name': 't'}}
        if phase:
            b['productPhase'] = phase
        # SN10 スナッパー(赤)/(白) はメーカー部品。色違いは色ごとに分ける
        d = {'SN10': {'decision': 'color_split'}}
        b['skuDecisions'] = d
        b['appliedSkuDecisions'] = d
        p = pli.build_preview(self.c, b)
        if p['rows']:
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(self.c, {**b, 'commitToken': tok,
                                            'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        return p

    def products(self):
        out = {}
        for r in self.c.execute('SELECT id,name,color,category FROM products'):
            sizes = [x[0] for x in self.c.execute('SELECT label FROM product_sizes WHERE product_id=?', (r[0],))]
            hc = self.c.execute('SELECT COUNT(*) FROM customer_products WHERE product_id=?', (r[0],)).fetchone()[0]
            out[r[1]] = (r[2], r[3], sizes, hc)
        return out


class ImportTest(Base):
    def check(self, prev_rows):
        for r in prev_rows:
            pr = r.get('product') or {}
            if pr.get('sizeHidden'):
                self.assertNotEqual(pr.get('sizeAction'), 'add')
                self.assertNotEqual(r['status'], pli.STATUS_SIZE_ADD)
                self.assertNotEqual((r.get('handle') or {}).get('action'), 'create')
            self.assertFalse([x for x in r.get('reasons') or [] if 'サイズが空' in x])
        got = self.products()
        self.assertEqual(got['刺繍加工'], ('', stock.CATEGORY_PROCESS_SERVICE, ['—'], 0))
        self.assertNotIn('スナッパー加工', got)
        snap = [v for k, v in got.items() if k.startswith('スナッパー')]
        self.assertTrue(snap)
        self.assertTrue(all(v[1] == stock.CATEGORY_PARTS for v in snap), snap)
        self.assertEqual(got['ボタン'], ('', stock.CATEGORY_PARTS, ['F'], 0))
        self.assertEqual(got['作業服上'][2], ['L', 'M'])
        self.assertEqual(got['作業服上'][3], 1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM customer_product_processes').fetchone()[0], 1)

    def test_phases(self):
        p1 = self.run_phase('parts', 'tok-cs-1')
        p2 = self.run_phase('goods', 'tok-cs-2')
        self.check(p1['rows'] + p2['rows'])

    def test_bulk(self):
        p = self.run_phase('', 'tok-cs-3')
        self.check(p['rows'])


class OpeningSizeTest(Base):
    def test_parts_stock_uses_default_size(self):
        pid = stock.save_product(self.c, {'code': '', 'name': 'ボタン', 'category': stock.CATEGORY_PARTS,
                                          'sizes': ['F'], 'links': []})['id']
        gid = stock.save_product(self.c, {'code': 'X1', 'name': '服', 'sizes': ['L'], 'links': []})['id']
        self.assertEqual(bulk._codeless_stock_size(self.c, pid, '10mm'), 'F')
        self.assertEqual(bulk._codeless_stock_size(self.c, gid, 'M'), 'M')


class RepairTest(Base):
    def test_cleanup(self):
        pid = stock.save_product(self.c, {'code': '', 'name': 'スナッパー加工', 'color': 'ホワイト',
                                          'category': stock.CATEGORY_PROCESS_SERVICE,
                                          'sizes': ['10mm', '12mm', '—'], 'links': []})['id']
        out = proc_svc.apply_service_size_color_cleanup(self.c, dry_run=True)
        self.assertEqual(out['planned'][0]['removeSizes'], ['10mm', '12mm'])
        self.assertEqual(self.products()['スナッパー加工'][2], ['10mm', '12mm', '—'])  # 既定は書込なし
        proc_svc.apply_service_size_color_cleanup(self.c, dry_run=False)
        self.assertEqual(self.products()['スナッパー加工'][:3], ('', stock.CATEGORY_PROCESS_SERVICE, ['—']))
        self.assertEqual(proc_svc.apply_service_size_color_cleanup(self.c)['planned'], [])
        self.assertTrue(pid)

    def test_keeps_size_in_use(self):
        cust = stock.save_customer(self.c, {'name': '顧客A'})['id']
        pid = stock.save_product(self.c, {'code': '', 'name': '刺繍加工',
                                          'category': stock.CATEGORY_PROCESS_SERVICE,
                                          'sizes': ['L', 'M'], 'links': []})['id']
        loc = stock.save_location(self.c, {'name': '倉庫'})['id']
        cp = stock.ensure_customer_product(self.c, {'customerId': cust, 'productId': pid, 'embroidery': ''})
        cp_id = cp['id'] if isinstance(cp, dict) else cp
        t = stock.stamp()
        self.c.execute('''INSERT INTO lots(customer_product_id,size_label,location_id,unit_cost,qty_received,
                          qty_remaining,basis_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                       (cp_id, 'L', loc, 0, 1, 1, '2026-01-01', t, t))
        proc_svc.apply_service_size_color_cleanup(self.c, dry_run=False)
        self.assertEqual(self.products()['刺繍加工'][2], ['L'])


if __name__ == '__main__':
    unittest.main()


class MakerPartsNotServiceTest(unittest.TestCase):
    def test_classify(self):
        import product_ledger_processing_service as ps
        r = ps.classify_processing_service_row(supplier='YKK', product_name='スナッパー(赤)', code='SN10')
        self.assertFalse(r['isProcessingService'])
        self.assertTrue(r['isPhysicalParts'])
        for name in ('スナッパー加工', '刺繍'):
            self.assertFalse(ps.classify_processing_service_row(supplier='YKK', product_name=name, code='')['isPhysicalParts'])
        self.assertFalse(ps.classify_processing_service_row(
            supplier='株サーヴォ', product_name='作業服上(刺繍)', code='DCP600')['isPhysicalParts'])


class GarmentNotMakerPartsTest(unittest.TestCase):
    """回帰: 「七分袖」→「七分袖加工」と補われ、品番ありの衣服が部品になっていた"""

    def test_garment_with_code_not_parts(self):
        import product_ledger_processing_service as ps
        cases = [
            ('住商モンブラン㈱', '長白衣七分袖（女性）', '71-139'),
            ('㈱マジェステックストレード', 'スタッフ・女性七分袖シャツ(マイスター)', '400-L-S7-m'),
            ('ＫＡＺＥＮ　ＷＬＤ㈱', 'レディスシャツ七分袖(ﾎﾜｲﾄ)', '627-10'),
            ('ＫＡＺＥＮ　ＷＬＤ㈱', 'コックシャツ七分袖(ﾎﾜｲﾄ)', 'APK210-10'),
            ('ＫＡＺＥＮ　ＷＬＤ㈱', 'コックシャツ七分袖(ｸﾘｰﾑ) (不使用)', 'APK210-24 (不使用)'),
        ]
        for sup, name, code in cases:
            r = ps.classify_processing_service_row(supplier=sup, product_name=name, code=code)
            self.assertFalse(r['isPhysicalParts'], name)
            self.assertFalse(r.get('makerParts'), name)
        r = ps.classify_processing_service_row(supplier='YKK', product_name='スナッパー(赤)', code='SN10')
        self.assertTrue(r['isPhysicalParts'])


class WorkSuffixNotPartsTest(unittest.TestCase):
    """回帰: 裏地補修・ファスナーガード交換が「裏地」「ファスナー」で部品・資材になっていた"""

    def test_work_names_not_physical(self):
        import product_ledger_processing_service as ps
        for name in ('裏地補修', 'ファスナーガード交換', 'ﾌｧｽﾅｰ交換', 'ボタンホール作製',
                     'スカート裏地補修', 'エプロンボタン付け', '裾マジックテープ取付'):
            self.assertFalse(ps.is_physical_parts_name(name), name)
            r = ps.classify_processing_service_row(supplier='Ｋ２スタッフ工房', product_name=name, code='')
            self.assertFalse(r['isPhysicalParts'], name)
        for name in ('裏地補修', 'ファスナーガード交換', 'ﾌｧｽﾅｰ交換', 'ボタンホール作製', '片布作製', '胸囲幅直し'):
            r = ps.classify_processing_service_row(supplier='Ｋ２スタッフ工房', product_name=name, code='')
            self.assertTrue(r['isProcessingService'], name)
            self.assertFalse(r.get('k2AutoExclude'), name)
        for name in ('ボタン　18mm グリーン', 'DCP515用生地(袖/裾加工用)', '裏地', 'ファスナー'):
            self.assertTrue(ps.is_physical_parts_name(name), name)


class GarmentBodyNotProcessingTest(unittest.TestCase):
    """品番のある制服は名前に加工語があっても加工サービスにしない（#332）。"""

    def _c(self, sup, name, code, paren=''):
        import product_ledger_processing_service as ps
        return ps.classify_processing_service_row(supplier=sup, product_name=name, code=code, paren_processing=paren)

    def test_garment_with_code_not_processing(self):
        for code, name in [('AS6800', 'コックコート七分袖'), ('34207-81', '七分袖シャツ'),
                           ('AZ8710-008', '反射ベスト'), ('AZ66301-008', 'リフレクターキャップ'),
                           ('FP6700U-16', '裾上げパンツ'), ('RW0009-1', 'コックコート（七分袖）')]:
            r = self._c('アイトス', name, code)
            self.assertFalse(r.get('isProcessingService'), (code, name, r.get('serviceName')))


class K2StaffWorkshopTest(unittest.TestCase):
    def _c(self, name, code=''):
        import product_ledger_processing_service as ps
        return ps.classify_processing_service_row(supplier='Ｋ２スタッフ工房', product_name=name, code=code)

    def test_repairs_are_processing(self):
        for n in ('修理', 'サイズダウン・アップ', '丈直し・反射板調整'):
            r = self._c(n)
            self.assertTrue(r.get('isProcessingService'), n)
            self.assertFalse(r.get('k2AutoExclude'), n)
            self.assertFalse(r.get('k2GarmentCandidate'), n)

    def test_garment_like_defaults_processing_with_choice(self):
        for n in ('衣装係ベスト', 'エプロン　ピンク', '赤エプロン', 'ブルゾン　紺×緑'):
            r = self._c(n, '要確認')
            self.assertTrue(r.get('isProcessingService'), n)
            self.assertTrue(r.get('k2GarmentCandidate'), n)

    def test_choice_product_makes_uniform(self):
        import product_ledger_import as pli, stock
        ps = self._c('衣装係ベスト', '要確認')
        rows = [{'rowNo': 5, 'processingService': ps, 'product': {'category': '加工サービス'}}]
        pli.apply_k2_garment_choice(rows, {'5': {'registerKind': '商品'}})
        self.assertTrue(rows[0]['k2GarmentAsProduct'])
        self.assertEqual(rows[0]['product']['category'], stock.CATEGORY_UNIFORM)
        self.assertFalse(rows[0]['processingService']['isProcessingService'])
        rows2 = [{'rowNo': 5, 'processingService': ps, 'product': {}}]
        pli.apply_k2_garment_choice(rows2, {})
        self.assertFalse(rows2[0].get('k2GarmentAsProduct'))
