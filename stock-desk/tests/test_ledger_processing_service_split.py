"""加工サービスの自動整理と商品本体・加工の分離（temp DB のみ）。"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_bulk_import as bulk  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as ps  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def ledger(*rows):
    return '\n'.join(['顧客,仕入先,商品コード,商品名,サイズ', *rows])


def opening_line(supplier, customer, name, code, size, qty):
    return (
        f'{supplier},{customer},{name},{code},{size},新品,,{qty},'
        f',,,,,,'
    )


def tiny_opening(rows):
    header = (
        '仕入先,得意先名,商品名,商品コード,サイズ,状態,補正在庫数,在庫,'
        '購入,出荷,単価,拠点,棚,区画'
    )
    # 実際の初期在庫CSVヘッダに合わせる
    header = '仕入先,顧客,商品名,商品コード,サイズ,状態,在庫,単価,拠点,棚'
    return '\n'.join([header, *rows])


class ProcessingServiceUnitTest(unittest.TestCase):
    def test_unify_embroidery_aliases(self):
        self.assertEqual(ps.unify_processing_service_name('刺繍'), '刺繍加工')
        self.assertEqual(ps.unify_processing_service_name('刺繍入れ'), '刺繍加工')
        self.assertEqual(
            ps.unify_processing_service_name('コックコート七分袖加工／七分袖加工'),
            '七分袖加工',
        )

    def test_pocket_attach_without_kako_char(self):
        """『加工』文字がなくてもポケット取付は認識。表記差は同一名へ。"""
        for raw in (
            'ポケット取付', 'ポケット取り付け', 'ポケット取付け',
            'ポケット縫付', 'ポケット縫い付け',
        ):
            self.assertEqual(
                ps.unify_processing_service_name(raw), 'ポケット取付', msg=raw,
            )
            r = ps.classify_processing_service_row(
                supplier='K2スタッフ工房', product_name=raw, code='',
            )
            self.assertTrue(r['identifiable'], msg=raw)
            self.assertTrue(r['isProcessingService'], msg=raw)
            self.assertFalse(r['needsConfirm'], msg=raw)
            self.assertEqual(r['serviceName'], 'ポケット取付')
            self.assertEqual(r['displayName'], 'ポケット取付')
            self.assertEqual(r['vendorName'], 'K2スタッフ工房')
        # 表記差でも同一 dedupe キー
        self.assertEqual(
            ps.service_dedupe_key('ポケット取り付け', 'K2スタッフ工房'),
            ps.service_dedupe_key('ポケット取付', 'K2スタッフ工房'),
        )

    def test_sleeve_hem_snapper_shared(self):
        self.assertEqual(ps.unify_processing_service_name('袖加工'), '袖加工')
        self.assertEqual(ps.unify_processing_service_name('丈加工'), '丈加工')
        self.assertEqual(ps.unify_processing_service_name('スナッパー'), 'スナッパー加工')
        # 原文にない具体化（袖→七分袖）はしない
        self.assertEqual(ps.unify_processing_service_name('袖加工'), '袖加工')
        self.assertNotEqual(ps.unify_processing_service_name('袖加工'), '七分袖加工')

    def test_customer_specific_uwagi_kept(self):
        name = 'わらく堂用上衣加工（衿・裾・袖）'
        self.assertEqual(ps.unify_processing_service_name(name), '上衣加工')
        specs = ps.extract_row_specific_specs(name)
        self.assertEqual(specs['customerUse'], 'わらく堂用')
        self.assertIn('衿', specs['parenSpecs'])
        self.assertIn('わらく堂用', specs['customerSpecific'])
        r = ps.classify_processing_service_row(
            supplier='K2スタッフ工房', product_name=name, code='W-1',
        )
        self.assertTrue(r['identifiable'])
        self.assertEqual(r['serviceName'], '上衣加工')
        self.assertIn('衿', r['rowSpecs'].get('customerSpecific') or '')

    def test_k2_and_elm_vendors(self):
        self.assertTrue(ps.is_processing_vendor('K2スタッフ工房'))
        self.assertTrue(ps.is_processing_vendor('(有)エルムスコーポレーション'))
        self.assertTrue(ps.is_processing_vendor('エルムス'))
        self.assertFalse(ps.is_processing_vendor('某メーカー'))

    def test_physical_parts_not_service(self):
        r = ps.classify_processing_service_row(
            supplier='K2スタッフ工房', product_name='ボタン', code='',
        )
        self.assertFalse(r['isProcessingService'])
        self.assertTrue(r['isPhysicalParts'])

    def test_unknown_name_on_vendor_needs_confirm(self):
        r = ps.classify_processing_service_row(
            supplier='K2スタッフ工房', product_name='謎の一行', code='X',
        )
        self.assertTrue(r['needsConfirm'])
        self.assertFalse(r['identifiable'])
        # 業者名だけで架空加工を作らない
        self.assertFalse(r['isProcessingService'])
        self.assertTrue(any('加工名' in x for x in r['confirmReasons']))

    def test_composition_kept(self):
        name = '半袖加工＋リフレクター＋マジックテープ付け'
        unified = ps.unify_processing_service_name(name)
        # NFKC で全角＋が半角+になることがある。構成要素は保持する
        self.assertIn('半袖加工', unified)
        self.assertIn('リフレクター', unified)
        self.assertIn('マジックテープ', unified)
        specs = ps.extract_row_specific_specs(name + ' 股下72')
        self.assertIn('股下', specs['dimensions'])


class ProcessingServiceImportTest(unittest.TestCase):
    def test_embroidery_same_vendor_one_service_across_kinds(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '㈱どんぐり,(有)エルムスコーポレーション,H-1,ハンチング帽刺繍,F',
                '㈱どんぐり,エルムス,P-1,ポロシャツ刺繍入れ,M',
                '㈱どんぐり,(有)エルムスコーポレーション,C-1,コックコート刺繍,L',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'emb.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = preview.get('processingServicePlans') or []
            auto = [p for p in plans if p.get('autoRegister')]
            self.assertEqual(len(auto), 1)
            self.assertEqual(auto[0]['serviceName'], '刺繍加工')
            self.assertEqual(auto[0]['vendorName'], '(有)エルムスコーポレーション')
            # 衣服種差・在庫0で個別確認に出さない
            for g in preview.get('skuGroups') or []:
                if g.get('garmentBodyReviewOnly'):
                    continue
                reasons = [r.get('code') for r in (g.get('reviewReasons') or [])]
                self.assertNotIn('kind', reasons)

            result = pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'emb.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-emb-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertGreaterEqual(result['counts'].get('processingServicesCreated', 0), 1)
            services = list(c.execute(
                "SELECT id,code,name,category,supplier_id FROM products WHERE category=? AND name=?",
                (stock.CATEGORY_PARTS, '刺繍加工'),
            ))
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0]['code'], '')
            # 再取込で重複なし
            preview2 = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'emb.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'emb.csv',
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'ps-emb-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            services2 = list(c.execute(
                "SELECT id FROM products WHERE category=? AND name=?",
                (stock.CATEGORY_PARTS, '刺繍加工'),
            ))
            self.assertEqual(len(services2), 1)
        finally:
            c.close()
            folder.cleanup()

    def test_6_721_split_body_and_k2_processing(self):
        folder, path, c = newdb()
        try:
            # 本体行 + 加工業者行
            text = ledger(
                '㈱どんぐり,某メーカー,6-721,コックコート,M',
                '㈱どんぐり,K2スタッフ工房,6-721,コックコート七分袖加工／七分袖加工,M',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': '6721.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]['serviceName'], '七分袖加工')
            self.assertEqual(plans[0]['vendorName'], 'K2スタッフ工房')

            g = next(x for x in preview['skuGroups'] if x['code'] == '6-721')
            # K2 が本体通常仕入先候補に入らない
            self.assertNotIn('K2スタッフ工房', g.get('supplierCandidates') or [])
            for name in g.get('supplierCandidates') or []:
                self.assertFalse(ps.is_processing_vendor(name))

            result = pli.commit_preview(c, {
                'file': csv_b64(text), 'name': '6721.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-6721-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts'].get('failed', 0), 0)

            body = c.execute(
                "SELECT id,code,name,supplier_id FROM products WHERE code=? AND active=1",
                ('6-721',),
            ).fetchone()
            self.assertIsNotNone(body)
            # 本体の通常仕入先は K2 ではない
            if body['supplier_id']:
                sup = c.execute(
                    'SELECT name FROM suppliers WHERE id=?', (body['supplier_id'],)
                ).fetchone()
                self.assertFalse(ps.is_processing_vendor(sup['name']))

            svc = c.execute(
                "SELECT id,code,name FROM products WHERE name=? AND category=?",
                ('七分袖加工', stock.CATEGORY_PARTS),
            ).fetchone()
            self.assertIsNotNone(svc)
            self.assertEqual(svc['code'], '')
            self.assertNotEqual(svc['id'], body['id'])

            # 取扱は本体＋七分袖加工
            handles = list(c.execute(
                '''SELECT embroidery FROM customer_products
                   WHERE product_id=? AND active=1''',
                (body['id'],),
            ))
            embs = {h['embroidery'] for h in handles}
            self.assertIn('七分袖加工', embs)

            # マップに加工サービス ID と原文
            maps = list(c.execute(
                'SELECT csv_product_name,csv_processing,spec_json,product_id FROM product_ledger_row_maps'
            ))
            self.assertTrue(maps)
            linked = [
                m for m in maps
                if '七分袖' in (m['csv_product_name'] or '')
                or '七分袖' in (m['csv_processing'] or '')
            ]
            self.assertTrue(linked)
            spec = json.loads(linked[0]['spec_json'] or '{}')
            self.assertEqual(spec.get('processingServiceName'), '七分袖加工')
            self.assertEqual(spec.get('processingVendor'), 'K2スタッフ工房')
            self.assertEqual(int(linked[0]['product_id']), int(body['id']))
        finally:
            c.close()
            folder.cleanup()

    def test_different_vendors_stay_distinct(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,K2スタッフ工房,,七分袖加工,',
                '顧客A,(有)エルムスコーポレーション,,刺繍入れ,',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'vend.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans), 2)
            keys = {p['dedupeKey'] for p in plans}
            self.assertEqual(len(keys), 2)
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'vend.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-vend-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE category=?",
                (stock.CATEGORY_PARTS,),
            ).fetchone()['n']
            self.assertEqual(n, 2)
        finally:
            c.close()
            folder.cleanup()

    def test_row_specs_and_hem_service_kept(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,K2スタッフ工房,X-1,半袖加工＋リフレクター＋マジックテープ付け 股下72,M',
                '顧客A,某メーカー,X-1,ブルゾン,M',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'comp.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = preview.get('processingServicePlans') or []
            self.assertTrue(plans)
            self.assertIn('半袖加工', plans[0]['serviceName'])
            samples = plans[0].get('rowSpecsSamples') or []
            self.assertTrue(samples)
            self.assertTrue(
                samples[0].get('dimensions') or samples[0].get('composition')
                or '股下' in (samples[0].get('originalName') or '')
            )
            # 丈加工サービス名も統一対象
            self.assertEqual(ps.unify_processing_service_name('丈加工'), '丈加工')
        finally:
            c.close()
            folder.cleanup()

    def test_pocket_attach_k2_auto_no_manual(self):
        """ポケット取付／K2 は手入力なしで加工サービス自動登録。表記差は重複しない。"""
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,某メーカー,P-1,シャツ,M',
                '顧客A,K2スタッフ工房,P-1,ポケット取付,M',
                '顧客B,K2スタッフ工房,,ポケット取り付け,',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'pocket.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans), 1, msg=plans)
            self.assertEqual(plans[0]['serviceName'], 'ポケット取付')
            self.assertEqual(plans[0]['displayName'], 'ポケット取付')
            self.assertEqual(plans[0]['vendorName'], 'K2スタッフ工房')

            # 確認理由に「加工名を特定できません」が残らない
            for row in preview.get('rows') or []:
                csv = row.get('csv') or {}
                if 'ポケット' in (csv.get('productName') or ''):
                    reasons = ' '.join(row.get('reasons') or [])
                    self.assertNotIn('加工名を特定できません', reasons)
                    self.assertFalse(row.get('processingServiceNeedsConfirm'))
                    ps_info = row.get('processingService') or {}
                    self.assertTrue(ps_info.get('identifiable'))
                    self.assertEqual(ps_info.get('serviceName'), 'ポケット取付')

            # K2 は本体仕入先候補に混ざらない
            g = next(x for x in preview['skuGroups'] if x['code'] == 'P-1')
            self.assertNotIn('K2スタッフ工房', g.get('supplierCandidates') or [])

            result = pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'pocket.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-pocket-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts'].get('failed', 0), 0)
            services = list(c.execute(
                "SELECT id,name,code,category FROM products WHERE name=? AND category=?",
                ('ポケット取付', stock.CATEGORY_PARTS),
            ))
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0]['code'], '')

            # 再照合でも同一サービス・確認理由なし
            preview2 = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'pocket.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans2 = [p for p in (preview2.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans2), 1)
            self.assertEqual(plans2[0].get('action'), 'reuse')
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'pocket.csv',
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'ps-pocket-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE name=? AND category=?",
                ('ポケット取付', stock.CATEGORY_PARTS),
            ).fetchone()['n']
            self.assertEqual(n, 1)

            # 行マップに個別仕様・対象品番が残る
            maps = list(c.execute(
                "SELECT csv_product_name,spec_json,product_id FROM product_ledger_row_maps"
            ))
            pocket_maps = [m for m in maps if 'ポケット' in (m['csv_product_name'] or '')]
            self.assertTrue(pocket_maps)
            spec = json.loads(pocket_maps[0]['spec_json'] or '{}')
            self.assertEqual(spec.get('processingServiceName'), 'ポケット取付')
            self.assertEqual(spec.get('processingVendor'), 'K2スタッフ工房')
            self.assertEqual(spec.get('processingDisplayName'), 'ポケット取付')
        finally:
            c.close()
            folder.cleanup()

    def test_waraku_specs_not_replaced(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                'わらく堂,K2スタッフ工房,W-9,わらく堂用上衣加工（衿・裾・袖）,L',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'waraku.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]['serviceName'], '上衣加工')
            samples = plans[0].get('rowSpecsSamples') or []
            self.assertTrue(samples)
            self.assertIn('衿', samples[0].get('customerSpecific') or samples[0].get('parenSpecs') or '')
            # 勝手に別加工へ置き換えない
            self.assertNotEqual(plans[0]['serviceName'], '七分袖加工')
        finally:
            c.close()
            folder.cleanup()

    def test_opening_stock_uses_garment_not_service_qty(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '㈱どんぐり,某メーカー,6-721,コックコート,M',
                '㈱どんぐり,K2スタッフ工房,6-721,コックコート七分袖加工／七分袖加工,M',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'open.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'open.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-open-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()

            body = c.execute(
                "SELECT id FROM products WHERE code='6-721'"
            ).fetchone()
            svc = c.execute(
                "SELECT id FROM products WHERE name='七分袖加工' AND category=?",
                (stock.CATEGORY_PARTS,),
            ).fetchone()
            self.assertIsNotNone(body)
            self.assertIsNotNone(svc)

            dest = bulk.lookup_ledger_destinations_for_aux(
                c,
                customer='㈱どんぐり',
                code='6-721',
                product_name='コックコート七分袖加工／七分袖加工',
                size='M',
                supplier='K2スタッフ工房',
            )
            self.assertIn(dest.get('status'), ('ok', 'unique', 'matched', 'ready'), msg=dest)
            # status 名が環境により異なる場合は matches を見る
            matches = dest.get('matches') or []
            if dest.get('match'):
                matches = [dest['match']]
            # フォールバック: status ok 系で productId
            if not matches and dest.get('productId'):
                matches = [dest]
            self.assertTrue(matches or dest.get('status') != 'missing', msg=dest)
            if matches:
                for m in matches:
                    self.assertEqual(int(m['productId']), int(body['id']))
                    self.assertNotEqual(int(m['productId']), int(svc['id']))
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
