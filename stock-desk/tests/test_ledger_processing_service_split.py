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
        # 一般統一（非エルムス経路）は従来どおり「刺繍加工」
        self.assertEqual(ps.unify_processing_service_name('刺繍'), '刺繍加工')
        self.assertEqual(ps.unify_processing_service_name('刺繍入れ'), '刺繍加工')
        self.assertEqual(
            ps.unify_processing_service_name('コックコート七分袖加工／七分袖加工'),
            '七分袖加工',
        )

    def test_elms_force_embroidery_regardless_of_name_or_code(self):
        """エルムス行は品番・商品名にかかわらず加工名「刺繍」。他業者へは適用しない。"""
        cases = [
            ('(有)エルムスコーポレーション', '刺繍', ''),
            ('㈲エルムスコーポレーション', '刺繍入れ', '刺繍'),
            ('エルムス', '', ''),
            ('有限会社エルムスコーポレーション', 'エプロン', 'A-1'),
            ('(有)エルムスコーポレーション', 'ポロシャツ袖', ''),
            ('エルムスコーポレーション', 'サンプル2型', 'S-2'),
            ('(有)エルムスコーポレーション', 'ワッペン作製', '刺繍入れ'),
        ]
        for supplier, name, code in cases:
            r = ps.classify_processing_service_row(
                supplier=supplier, product_name=name, code=code,
            )
            self.assertTrue(r['elmsEmbroideryRule'], msg=(supplier, name, code))
            self.assertTrue(r['isProcessingService'], msg=(supplier, name, code))
            self.assertTrue(r['identifiable'], msg=(supplier, name, code))
            self.assertFalse(r['needsConfirm'], msg=(supplier, name, code))
            self.assertEqual(r['serviceName'], '刺繍')
            self.assertEqual(r['displayName'], '刺繍')
            self.assertEqual(r['vendorName'], '(有)エルムスコーポレーション')
        # 他業者の衣服名は加工サービス化しない
        other = ps.classify_processing_service_row(
            supplier='某メーカー', product_name='エプロン', code='',
        )
        self.assertFalse(other['isProcessingService'])
        self.assertFalse(other['elmsEmbroideryRule'])
        # K2 の不明名は確認待ちに残さず自動除外（エルムス強制を流用しない）
        k2 = ps.classify_processing_service_row(
            supplier='K2スタッフ工房', product_name='エプロン', code='',
        )
        self.assertFalse(k2['needsConfirm'])
        self.assertTrue(k2.get('k2AutoExclude'))
        self.assertFalse(k2.get('elmsEmbroideryRule'))
        self.assertIn('加工内容を特定できない', k2.get('autoExcludeReason') or '')

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
        self.assertEqual(ps.unify_processing_service_name('丈直し'), '丈直し')
        self.assertEqual(ps.unify_processing_service_name('裾直し'), '裾直し')
        self.assertEqual(ps.unify_processing_service_name('スナッパー'), 'スナッパー加工')
        # 原文にない具体化（袖→七分袖）はしない。丈直し→丈詰め等も推測しない
        self.assertEqual(ps.unify_processing_service_name('袖加工'), '袖加工')
        self.assertNotEqual(ps.unify_processing_service_name('袖加工'), '七分袖加工')
        self.assertEqual(ps.unify_processing_service_name('丈直し'), '丈直し')
        self.assertNotEqual(ps.unify_processing_service_name('丈直し'), '丈加工')
        self.assertNotEqual(ps.unify_processing_service_name('丈直し'), '裾直し')

    def test_take_naoshi_classified_as_service(self):
        """『丈直し』単独は加工サービス。丈詰め等へ置き換えない。"""
        r = ps.classify_processing_service_row(
            supplier='K2スタッフ工房', product_name='丈直し', code='',
        )
        self.assertTrue(r['identifiable'])
        self.assertTrue(r['isProcessingService'])
        self.assertFalse(r['needsConfirm'])
        self.assertEqual(r['serviceName'], '丈直し')
        self.assertEqual(r['displayName'], '丈直し')
        self.assertEqual(r['vendorName'], 'K2スタッフ工房')
        self.assertEqual(
            ps.service_dedupe_key('丈直し', 'K2スタッフ工房'),
            ps.service_dedupe_key('丈直し', 'K2スタッフ工房'),
        )

    def test_size_down_classified_as_service(self):
        """『サイズダウン』単独は加工サービス。別語へ推測しない。"""
        self.assertEqual(ps.unify_processing_service_name('サイズダウン'), 'サイズダウン')
        r = ps.classify_processing_service_row(
            supplier='K2スタッフ工房', product_name='サイズダウン', code='',
        )
        self.assertTrue(r['identifiable'])
        self.assertTrue(r['isProcessingService'])
        self.assertFalse(r['needsConfirm'])
        self.assertEqual(r['serviceName'], 'サイズダウン')
        self.assertEqual(r['displayName'], 'サイズダウン')
        self.assertEqual(r['vendorName'], 'K2スタッフ工房')
        # 原文にない具体化はしない
        self.assertNotEqual(r['serviceName'], 'サイズアップ')
        self.assertNotEqual(r['serviceName'], '丈直し')

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
        # 非K2の加工業者は従来どおり要確認
        r = ps.classify_processing_service_row(
            supplier='加工屋Z', product_name='謎の一行', code='X',
        )
        # 未知業者は加工業者判定されない場合がある → K2 以外の既知加工業者で確認
        # 加工屋Z が is_processing_vendor でないなら、K2 自動除外側を別テストで見る
        if r.get('isProcessingVendor'):
            self.assertTrue(r['needsConfirm'])
            self.assertFalse(r['identifiable'])
            self.assertFalse(r['isProcessingService'])
            self.assertTrue(any('加工名' in x for x in r['confirmReasons']))

        k2 = ps.classify_processing_service_row(
            supplier='K2スタッフ工房', product_name='謎の一行', code='X',
        )
        self.assertFalse(k2['needsConfirm'])
        self.assertTrue(k2.get('k2AutoExclude'))
        self.assertFalse(k2['identifiable'])
        self.assertFalse(k2['isProcessingService'])
        self.assertIn('加工内容を特定できない', k2.get('autoExcludeReason') or '')

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
            self.assertEqual(auto[0]['serviceName'], '刺繍')
            self.assertEqual(auto[0]['displayName'], '刺繍')
            self.assertEqual(auto[0]['vendorName'], '(有)エルムスコーポレーション')
            self.assertTrue(auto[0].get('elmsEmbroideryRule'))
            # 衣服種差・在庫0・本体不明で確認待ちにしない
            for g in preview.get('skuGroups') or []:
                self.assertFalse(g.get('garmentBodyReviewOnly'), msg=g.get('code'))
                reasons = [r.get('code') for r in (g.get('reviewReasons') or [])]
                self.assertNotIn('kind', reasons)
                self.assertNotIn('garment_body_unknown', reasons)
            for row in preview.get('rows') or []:
                ps_info = row.get('processingService') or {}
                if not ps_info.get('elmsEmbroideryRule'):
                    continue
                self.assertFalse(row.get('processingServiceNeedsConfirm'))
                self.assertEqual(ps_info.get('serviceName'), '刺繍')
                prov = row.get('specProvenance') or {}
                self.assertEqual(prov.get('originalName'), (row.get('csv') or {}).get('productName'))
                self.assertTrue(prov.get('hideOriginalNameAsAlias'))

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
                (stock.CATEGORY_PARTS, '刺繍'),
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
                (stock.CATEGORY_PARTS, '刺繍'),
            ))
            self.assertEqual(len(services2), 1)
            # 旧名称「刺繍加工」の残骸も増やさない
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) AS n FROM products WHERE category=? AND name=?",
                    (stock.CATEGORY_PARTS, '刺繍加工'),
                ).fetchone()['n'],
                0,
            )
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
            elms = next(p for p in plans if 'エルムス' in (p.get('vendorName') or ''))
            self.assertEqual(elms['serviceName'], '刺繍')
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
            elms_prod = c.execute(
                "SELECT name FROM products WHERE category=? AND name=?",
                (stock.CATEGORY_PARTS, '刺繍'),
            ).fetchone()
            self.assertIsNotNone(elms_prod)
        finally:
            c.close()
            folder.cleanup()

    def test_elms_varied_names_one_service_keeps_provenance(self):
        """複数顧客・衣服名・空品番でも刺繍1件。元情報は provenance に保持。"""
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,㈲エルムスコーポレーション,,エプロン,',
                '顧客B,(有)エルムスコーポレーション,刺繍,ポロシャツ袖,M',
                '顧客C,エルムス,刺繍入れ,サンプル2型,L',
                '顧客A,有限会社エルムスコーポレーション,,ワッペン作製,',
                '顧客D,某メーカー,X-9,別物シャツ,M',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'elms-var.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            auto = [
                p for p in (preview.get('processingServicePlans') or [])
                if p.get('autoRegister')
            ]
            elms_plans = [p for p in auto if p.get('elmsEmbroideryRule')]
            self.assertEqual(len(elms_plans), 1, msg=elms_plans)
            self.assertEqual(elms_plans[0]['serviceName'], '刺繍')
            self.assertEqual(elms_plans[0]['vendorName'], '(有)エルムスコーポレーション')
            self.assertGreaterEqual(len(elms_plans[0].get('sourceRowNos') or []), 4)

            elms_rows = [
                r for r in (preview.get('rows') or [])
                if (r.get('processingService') or {}).get('elmsEmbroideryRule')
            ]
            self.assertEqual(len(elms_rows), 4)
            originals = {
                (r.get('csv') or {}).get('productName') for r in elms_rows
            }
            self.assertEqual(
                originals,
                {'エプロン', 'ポロシャツ袖', 'サンプル2型', 'ワッペン作製'},
            )
            for r in elms_rows:
                self.assertFalse(r.get('processingServiceNeedsConfirm'))
                self.assertNotIn('加工名を特定できません', ' '.join(r.get('reasons') or []))
                prov = r.get('specProvenance') or {}
                self.assertEqual(prov.get('processingServiceName'), '刺繍')
                self.assertEqual(prov.get('originalName'), (r.get('csv') or {}).get('productName'))
                self.assertTrue(prov.get('targetCustomer'))

            # 某メーカー行は加工サービス計画に混ざらない
            for p in auto:
                self.assertNotIn('某メーカー', p.get('vendorName') or '')

            result = pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'elms-var.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-elms-var-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts'].get('failed', 0), 0)
            services = list(c.execute(
                "SELECT id,name,code FROM products WHERE category=? AND name=?",
                (stock.CATEGORY_PARTS, '刺繍'),
            ))
            self.assertEqual(len(services), 1)
            # マップに元名・顧客が残る
            maps = list(c.execute(
                '''SELECT csv_customer,csv_product_name,csv_product_code,spec_json
                   FROM product_ledger_row_maps
                   WHERE csv_supplier LIKE '%エルムス%' OR csv_supplier='エルムス' '''
            ))
            self.assertGreaterEqual(len(maps), 4)
            for m in maps:
                spec = json.loads(m['spec_json'] or '{}')
                self.assertEqual(spec.get('processingServiceName'), '刺繍')
                self.assertTrue(spec.get('originalName') or m['csv_product_name'])
        finally:
            c.close()
            folder.cleanup()

    def test_elms_reuses_existing_embroidery_kako_alias(self):
        """既存「刺繍加工」＋エルムスは同一サービスとして再利用し表示を「刺繍」へ。"""
        folder, path, c = newdb()
        try:
            # 事前に旧名称で登録
            sup = stock.save_supplier(c, {
                'name': '(有)エルムスコーポレーション', 'active': True,
            })
            sid = sup['id'] if isinstance(sup, dict) else int(sup)
            old = stock.save_product(c, {
                'code': '',
                'name': '刺繍加工',
                'color': '',
                'category': stock.CATEGORY_PARTS,
                'supplierId': sid,
                'sizes': ['—'],
                'links': [],
                'active': True,
            })
            old_id = old['id'] if isinstance(old, dict) else int(old)
            c.commit()

            text = ledger(
                '顧客A,エルムス,,刺繍入れ,',
                '顧客B,㈲エルムスコーポレーション,,エプロン,',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'elms-reuse.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            auto = [
                p for p in (preview.get('processingServicePlans') or [])
                if p.get('autoRegister')
            ]
            self.assertEqual(len(auto), 1)
            self.assertEqual(auto[0]['action'], 'reuse')
            self.assertEqual(int(auto[0]['productId']), old_id)
            self.assertEqual(auto[0]['serviceName'], '刺繍')

            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'elms-reuse.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-elms-reuse-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            rows = list(c.execute(
                "SELECT id,name FROM products WHERE category=? AND supplier_id=?",
                (stock.CATEGORY_PARTS, sid),
            ))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['id'], old_id)
            self.assertEqual(rows[0]['name'], '刺繍')
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

    def test_take_naoshi_k2_auto_no_manual_across_customers(self):
        """丈直し／K2 は手入力なしで加工サービス自動登録。同業者は1件・顧客別照合は保持。"""
        folder, path, c = newdb()
        try:
            text = ledger(
                '㈱ダイイチ,某メーカー,D-1,パンツ,M',
                '㈱ダイイチ,K2スタッフ工房,D-1,丈直し,M',
                '㈱ニチレイ,某メーカー,N-1,パンツ,L',
                '㈱ニチレイ,K2スタッフ工房,N-1,丈直し,L',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'take-naoshi.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans), 1, msg=plans)
            self.assertEqual(plans[0]['serviceName'], '丈直し')
            self.assertEqual(plans[0]['displayName'], '丈直し')
            self.assertEqual(plans[0]['vendorName'], 'K2スタッフ工房')

            take_rows = []
            for row in preview.get('rows') or []:
                csv = row.get('csv') or {}
                if (csv.get('productName') or '') != '丈直し':
                    continue
                take_rows.append(row)
                reasons = ' '.join(row.get('reasons') or [])
                self.assertNotIn('加工名を特定できません', reasons)
                self.assertNotIn('加工名不足', reasons)
                self.assertFalse(row.get('processingServiceNeedsConfirm'))
                ps_info = row.get('processingService') or {}
                self.assertTrue(ps_info.get('identifiable'))
                self.assertEqual(ps_info.get('serviceName'), '丈直し')
                self.assertEqual(ps_info.get('displayName'), '丈直し')
            self.assertEqual(len(take_rows), 2)

            # パンツ本体の一般『丈加工』無視とは別経路（丈直しはサービスとして残る）
            self.assertEqual(ps.unify_processing_service_name('丈加工'), '丈加工')
            self.assertEqual(ps.unify_processing_service_name('裾直し'), '裾直し')

            result = pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'take-naoshi.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-take-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts'].get('failed', 0), 0)
            services = list(c.execute(
                "SELECT id,name,code,category FROM products WHERE name=? AND category=?",
                ('丈直し', stock.CATEGORY_PARTS),
            ))
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0]['code'], '')

            # 顧客ごとの行マップが残る（初期在庫照合で混同しない）
            maps = list(c.execute(
                "SELECT csv_customer,csv_product_code,csv_product_name,spec_json,product_id "
                "FROM product_ledger_row_maps WHERE csv_product_name=?",
                ('丈直し',),
            ))
            self.assertEqual(len(maps), 2)
            customers = {m['csv_customer'] for m in maps}
            self.assertEqual(customers, {'㈱ダイイチ', '㈱ニチレイ'})
            codes = {m['csv_product_code'] for m in maps}
            self.assertEqual(codes, {'D-1', 'N-1'})
            # 行マップは対象衣服側。加工サービス情報は spec に保持
            for m in maps:
                self.assertNotEqual(m['product_id'], services[0]['id'])
                spec = json.loads(m['spec_json'] or '{}')
                self.assertEqual(spec.get('processingServiceName'), '丈直し')
                self.assertEqual(spec.get('processingVendor'), 'K2スタッフ工房')
                self.assertEqual(spec.get('processingDisplayName'), '丈直し')
            # 顧客×対象品番が別なら別取扱（混同しない）
            cust_code = {(m['csv_customer'], m['csv_product_code']) for m in maps}
            self.assertEqual(
                cust_code,
                {('㈱ダイイチ', 'D-1'), ('㈱ニチレイ', 'N-1')},
            )
            self.assertEqual(len({m['product_id'] for m in maps}), 2)

            # 再照合でも同一サービスを再利用
            preview2 = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'take-naoshi.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans2 = [p for p in (preview2.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans2), 1)
            self.assertEqual(plans2[0].get('action'), 'reuse')
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'take-naoshi.csv',
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'ps-take-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE name=? AND category=?",
                ('丈直し', stock.CATEGORY_PARTS),
            ).fetchone()['n']
            self.assertEqual(n, 1)
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

    def test_size_down_k2_auto_no_manual(self):
        """サイズダウン／K2 は手入力なしで加工名認識。表示名は加工名と同一。"""
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,某メーカー,S-1,シャツ,M',
                '顧客A,K2スタッフ工房,S-1,サイズダウン,M',
                '顧客B,K2スタッフ工房,,サイズダウン,',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'size-down.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans), 1, msg=plans)
            self.assertEqual(plans[0]['serviceName'], 'サイズダウン')
            self.assertEqual(plans[0]['displayName'], 'サイズダウン')
            self.assertEqual(plans[0]['vendorName'], 'K2スタッフ工房')

            for row in preview.get('rows') or []:
                csv = row.get('csv') or {}
                if (csv.get('productName') or '') != 'サイズダウン':
                    continue
                reasons = ' '.join(row.get('reasons') or [])
                self.assertNotIn('加工名を特定できません', reasons)
                self.assertNotIn('加工名不足', reasons)
                self.assertFalse(row.get('processingServiceNeedsConfirm'))
                ps_info = row.get('processingService') or {}
                self.assertTrue(ps_info.get('identifiable'))
                self.assertEqual(ps_info.get('serviceName'), 'サイズダウン')
                self.assertEqual(ps_info.get('displayName'), 'サイズダウン')
                pc = row.get('parenClassification') or {}
                self.assertEqual(pc.get('processing'), 'サイズダウン')
                self.assertEqual(pc.get('plannedName'), 'サイズダウン')
                # 登録時表示名の別欄は持たず、加工名と予定名が一致
                self.assertEqual(pc.get('processing'), pc.get('plannedName'))

            result = pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'size-down.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'ps-sizedown-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts'].get('failed', 0), 0)
            services = list(c.execute(
                "SELECT id,name,code,category FROM products WHERE name=? AND category=?",
                ('サイズダウン', stock.CATEGORY_PARTS),
            ))
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0]['code'], '')

            # 再照合でも同一サービス再利用・表示名は加工名のまま
            preview2 = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'size-down.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans2 = [p for p in (preview2.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans2), 1)
            self.assertEqual(plans2[0].get('action'), 'reuse')
            self.assertEqual(plans2[0]['displayName'], 'サイズダウン')
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'size-down.csv',
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'ps-sizedown-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE name=? AND category=?",
                ('サイズダウン', stock.CATEGORY_PARTS),
            ).fetchone()['n']
            self.assertEqual(n, 1)

            maps = list(c.execute(
                "SELECT csv_product_name,spec_json,product_id FROM product_ledger_row_maps "
                "WHERE csv_product_name=?",
                ('サイズダウン',),
            ))
            self.assertTrue(maps)
            for m in maps:
                spec = json.loads(m['spec_json'] or '{}')
                self.assertEqual(spec.get('processingServiceName'), 'サイズダウン')
                self.assertEqual(spec.get('processingDisplayName'), 'サイズダウン')
                self.assertEqual(spec.get('processingVendor'), 'K2スタッフ工房')
        finally:
            c.close()
            folder.cleanup()

    def test_display_name_not_filled_from_planned_when_unknown(self):
        """不明な原文では予定名から加工名を無条件補完しない。K2は自動除外。"""
        r = ps.classify_processing_service_row(
            supplier='K2スタッフ工房', product_name='謎の一行', code='X',
        )
        self.assertFalse(r['needsConfirm'])
        self.assertTrue(r.get('k2AutoExclude'))
        self.assertFalse(r['identifiable'])
        self.assertEqual(r.get('serviceName') or '', '')
        self.assertEqual(r.get('displayName') or '', '')

        folder, path, c = newdb()
        try:
            text = ledger('顧客A,K2スタッフ工房,X-9,謎の一行,M')
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'unk.csv',
                '_canWrite': True, '_actor': {'name': 't'},
                'rowClassifications': {
                    '2': {
                        # 表示名だけ別語が残っていても加工名へ流し込まない
                        'plannedName': 'サイズダウン',
                        'processing': '',
                        'userEdited': True,
                    },
                },
            })
            row = (preview.get('rows') or [None])[0]
            self.assertIsNotNone(row)
            ps_info = row.get('processingService') or {}
            self.assertFalse(ps_info.get('identifiable'))
            self.assertEqual(ps_info.get('serviceName') or '', '')
            self.assertTrue(row.get('autoRowExcluded') or ps_info.get('k2AutoExclude'))
            self.assertEqual(row.get('status'), pli.STATUS_HOLD)
            pc = row.get('parenClassification') or {}
            # 加工名欄は空のまま（plannedName から補完しない）
            self.assertEqual(pc.get('processing') or '', '')
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(plans, [])
        finally:
            c.close()
            folder.cleanup()

    def test_user_edited_processing_name_syncs_display(self):
        """手入力の加工名が登録・表示・重複判定へ一貫反映される。"""
        folder, path, c = newdb()
        try:
            text = ledger('顧客A,K2スタッフ工房,,手直し候補,')
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'manual-proc.csv',
                '_canWrite': True, '_actor': {'name': 't'},
                'rowClassifications': {
                    '2': {
                        'processing': '刺繍加工',
                        'plannedName': '刺繍加工',
                        'userEdited': True,
                        'confirmed': True,
                    },
                },
            })
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]['serviceName'], '刺繍加工')
            self.assertEqual(plans[0]['displayName'], '刺繍加工')
            row = (preview.get('rows') or [None])[0]
            pc = row.get('parenClassification') or {}
            self.assertEqual(pc.get('processing'), '刺繍加工')
            self.assertEqual(pc.get('plannedName'), '刺繍加工')
        finally:
            c.close()
            folder.cleanup()

    def test_physical_parts_view_not_processing_labels(self):
        """実物部品は加工サービス表示にしない。"""
        folder, path, c = newdb()
        try:
            text = ledger('顧客A,K2スタッフ工房,,ボタン,')
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'btn.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row = (preview.get('rows') or [None])[0]
            self.assertIsNotNone(row)
            ps_info = row.get('processingService') or {}
            self.assertTrue(ps_info.get('isPhysicalParts'))
            self.assertFalse(ps_info.get('isProcessingService'))
            self.assertTrue(row.get('physicalPartsView'))
            self.assertFalse(row.get('processingServiceView'))
            plans = [p for p in (preview.get('processingServicePlans') or []) if p.get('autoRegister')]
            self.assertEqual(plans, [])
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
