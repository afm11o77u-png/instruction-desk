"""了承済みでも残る案内と、既存カテゴリ衝突の案内を分ける。"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402

JS = ROOT / 'static' / 'product_ledger_csv.js'


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


def render(st):
    folder = tempfile.TemporaryDirectory()
    harness = Path(folder.name) / 'h.js'
    data = Path(folder.name) / 'c.json'
    harness.write_text(
        "const fs=require('fs');const vm=require('vm');"
        "const sandbox={window:{},document:{getElementById(){return null},querySelector(){return null},"
        "querySelectorAll(){return []}},console,CSS:{escape:s=>String(s)},esc:s=>String(s==null?'':s)};"
        "vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);"
        "const api=sandbox.window.ProductLedgerCsv.__test;"
        "const st=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));"
        "const items=api.buildUnifiedReviewItems(st);"
        "const html=api.unifiedListRowsHtml(st, items);"
        "process.stdout.write(JSON.stringify({html, items:items.map(i=>({id:i.id,label:i.targetLabel,kind:i.kind,"
        "open:(i.issues||[]).filter(x=>!x.resolved).map(x=>x.label+'|'+(x.fieldKey||''))}))}));",
        encoding='utf-8',
    )
    data.write_text(json.dumps(st), encoding='utf-8')
    proc = subprocess.run(['node', str(harness), str(JS), str(data)], capture_output=True, text=True)
    folder.cleanup()
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


class AcceptedPartStillListedTest(unittest.TestCase):
    def test_accepted_blank_part_does_not_ask_for_a_code(self):
        row = {
            'rowNo': 2, 'status': 'needs_review', 'canCommit': False,
            'reasons': [],
            'csv': {
                'customer': '顧客A', 'supplier': 'サーヴォ', 'code': '',
                'productName': 'DCP515用生地(裾・裾加工用)', 'size': '—',
            },
            'product': {
                'action': 'review', 'blankSku': True, 'category': '部品・資材',
                'candidates': [], 'reasons': [], 'categoryConflict': False,
            },
            'supplier': {'action': 'reuse', 'candidates': [], 'reasons': []},
            'customer': {'action': 'reuse', 'candidates': []},
            'handle': {'candidates': []},
            'parenClassification': {
                'registerKind': '部品・資材', 'autoRegisterKind': '部品・資材',
                'plannedName': 'DCP515用生地', 'color': '',
            },
            'processingService': {'isPhysicalParts': True},
        }
        fp = '部品・資材\u0001\u0001DCP515用生地\u0001\u0001サーヴォ'
        st = {
            'preview': {'rows': [row], 'skuGroups': []},
            'rowClassifications': {
                '2': {
                    'reviewAccepted': True, 'reviewFingerprint': fp, 'userActed': True,
                    'plannedName': 'DCP515用生地', 'userEdited': True,
                },
            },
            'selections': {}, 'skuDecisions': {}, 'selectedCodes': {},
            'expandedCodes': {}, 'expandedUnified': {}, 'sectionOpen': {'unified': True},
            'busy': False,
        }
        out = render(st)
        self.assertNotIn('→ 品番', out['html'])
        self.assertNotIn('区分の確認|sku', ' '.join(' '.join(i['open']) for i in out['items']))
        self.assertEqual(out['html'].count('この内容で了承済みです'), 1)
        applied = pli._apply_review_acceptance(json.loads(json.dumps(row)), st['rowClassifications']['2'])
        self.assertTrue(applied['canCommit'], applied.get('reasons'))
        self.assertEqual(applied['product']['category'], '部品・資材')


class CategoryConflictGuideTest(unittest.TestCase):
    def test_conflict_guidance_is_once_and_not_a_repreview(self):
        folder, _path, conn = newdb()
        try:
            customer = stock.save_customer(conn, {'name': '顧客A', 'active': True})
            supplier = stock.save_supplier(conn, {'name': 'K2スタッフ工房', 'active': True})
            for name in ('別注エプロン 赤', 'チェック柄ベスト(ムーリベッキ)'):
                saved = stock.save_product(conn, {
                    'code': '要確認', 'name': name, 'category': '加工サービス',
                    'supplierId': supplier['id'], 'sizes': ['L'], 'links': [], 'active': True,
                    '_allowHistoricalDuplicateCode': True,
                })
                conn.execute(
                    '''INSERT INTO customer_products(
                         customer_id,product_id,embroidery,is_standard,active,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)''',
                    (customer['id'], saved['id'], '', 1, 1, stock.stamp(), stock.stamp()),
                )
            conn.commit()
            lines = [
                '顧客A,K2スタッフ工房,,別注エプロン 赤,L',
                '顧客A,K2スタッフ工房,,別注エプロン 赤,M',
                '顧客A,K2スタッフ工房,,チェック柄ベスト(ムーリベッキ),L',
                '顧客A,K2スタッフ工房,,チェック柄ベスト(ムーリベッキ),M',
            ]
            text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
            body = {
                'importMode': 'products', 'name': 'c.csv',
                'file': base64.b64encode(text.encode()).decode(),
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview = pli.build_preview(conn, body)
            ov = {}
            for row in preview['rows']:
                ov[str(row['rowNo'])] = {
                    'reviewAccepted': True,
                    'reviewFingerprint': pli._review_fingerprint(row),
                    'userActed': True,
                    'registerKind': '商品',
                    'registerKindUser': True,
                }
            body['rowClassifications'] = ov
            again = pli.build_preview(conn, body)
            for row in again['rows']:
                self.assertFalse(row['canCommit'])
                self.assertTrue(row['product'].get('categoryConflict') or any(
                    '既存カテゴリ' in str(reason) for reason in row.get('reasons') or []
                ))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) n FROM products WHERE category='加工サービス'").fetchone()['n'],
                2,
            )
            st = {
                'preview': again,
                'rowClassifications': ov,
                'selections': {}, 'skuDecisions': {}, 'selectedCodes': {},
                'expandedCodes': {},
                'expandedUnified': {item_id: True for item_id in ('x',)},
                'sectionOpen': {'unified': True},
                'busy': False,
            }
            # 詳細を開いた状態でも案内は対象ごとに1回
            items_stub = {}
            out = render(st)
            self.assertNotIn('再照合」で登録予定へ反映', out['html'])
            self.assertNotIn('→ 品番', out['html'])
            self.assertIn('既存カテゴリ「加工サービス」は変更しません', out['html'])
            self.assertIn('この内容で了承', out['html'])
            self.assertIn('既存カテゴリ', out['html'])
            self.assertFalse(items_stub)
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
