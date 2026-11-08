"""品番なし部品の了承が、再照合と本登録まで届くこと。"""
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
LINES = [
    '顧客A,KAZEN WLD(株),,エプロン用ボタン,-',
    '顧客A,KAZEN WLD(株),,青リボン,-',
    '顧客A,KAZEN WLD(株),,長白衣ボタン,-',
]


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


def preview(conn, lines, **extra):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products', 'name': 'parts.csv',
        'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
        '_canWrite': True, '_actor': {'name': '確認'},
        **extra,
    }
    return pli.build_preview(conn, body), body


def choice_key(row):
    csv = row['csv']
    return '\u0001'.join([
        str(row['rowNo']), csv.get('customer') or '', csv.get('supplier') or '',
        csv.get('code') or '', csv.get('productName') or '', csv.get('size') or '',
    ])


def kind_override(row, kind, *, accept=False, fingerprint=''):
    data = {
        'registerKind': kind,
        'registerKindUser': True,
        'choiceKey': choice_key(row),
        'userEdited': True,
    }
    if accept:
        data.update({
            'reviewAccepted': True,
            'reviewFingerprint': fingerprint,
            'userActed': True,
            'confirmed': True,
            'acceptProposal': True,
        })
    return {str(row['rowNo']): data}


class BlankPartsRegisterTest(unittest.TestCase):
    def test_three_parts_accept_repreview_commit_reimport(self):
        folder, path, conn = newdb()
        try:
            first, _ = preview(conn, LINES)
            by_name = {row['csv']['productName']: row for row in first['rows']}
            for name in ('エプロン用ボタン', '青リボン', '長白衣ボタン'):
                row = by_name[name]
                self.assertEqual(row['product']['category'], '部品・資材', name)
                self.assertTrue(row['product'].get('blankSku'), name)
                self.assertEqual(row['csv']['code'], '', name)
                self.assertTrue(row['canCommit'], (name, row.get('reasons')))
            button = by_name['エプロン用ボタン']
            fp = pli._review_fingerprint(button)
            self.assertTrue(fp.startswith('部品・資材'))
            switched, _ = preview(conn, LINES, rowClassifications=kind_override(button, '加工サービス'))
            held = next(row for row in switched['rows'] if row['csv']['productName'] == 'エプロン用ボタン')
            self.assertFalse(held['canCommit'])
            self.assertEqual(held['status'], 'needs_review')
            accepted, body = preview(
                conn, LINES,
                rowClassifications=kind_override(button, '部品・資材', accept=True, fingerprint=fp),
            )
            again = next(row for row in accepted['rows'] if row['csv']['productName'] == 'エプロン用ボタン')
            self.assertTrue(again['canCommit'], again.get('reasons'))
            self.assertEqual(again['product']['category'], '部品・資材')
            self.assertFalse(any(
                (plan.get('serviceName') or '') == 'エプロン用ボタン'
                for plan in (accepted.get('processingServicePlans') or [])
            ))
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': accepted['previewFingerprint'],
                'previewAt': accepted['previewAt'],
                'token': 'parts-confirm-1',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            saved = {
                row['name']: row
                for row in conn.execute('SELECT id, code, name, category, color FROM products')
            }
            self.assertEqual(saved['エプロン用ボタン']['category'], '部品・資材')
            self.assertEqual(saved['エプロン用ボタン']['code'], '')
            self.assertEqual(saved['リボン']['category'], '部品・資材')
            self.assertEqual(saved['リボン']['color'], 'ブルー')
            self.assertEqual(saved['長白衣ボタン']['category'], '部品・資材')
            self.assertEqual(conn.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
            reimport, _ = preview(conn, LINES)
            reused = {row['csv']['productName']: row['product']['id'] for row in reimport['rows']}
            self.assertEqual(reused['エプロン用ボタン'], saved['エプロン用ボタン']['id'])
            self.assertEqual(reused['青リボン'], saved['リボン']['id'])
            self.assertEqual(reused['長白衣ボタン'], saved['長白衣ボタン']['id'])
            self.assertEqual(conn.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], 3)
        finally:
            conn.close()
            folder.cleanup()


class ConfirmDoesNotBypassBlocksTest(unittest.TestCase):
    def test_duplicate_blank_part_needs_a_choice(self):
        folder, _path, conn = newdb()
        try:
            supplier = stock.save_supplier(conn, {'name': 'KAZEN WLD(株)', 'active': True})
            for _ in range(2):
                stock.save_product(conn, {
                    'code': '', 'name': '長白衣ボタン', 'category': '部品・資材',
                    'supplierId': supplier['id'], 'sizes': ['—'], 'links': [], 'active': True,
                    '_allowHistoricalDuplicateCode': True,
                })
            conn.commit()
            first, _ = preview(conn, ['顧客A,KAZEN WLD(株),,長白衣ボタン,-'])
            row = first['rows'][0]
            self.assertEqual(row['status'], 'needs_review')
            self.assertFalse(row['canCommit'])
            self.assertTrue(any('複数' in str(reason) for reason in row.get('reasons') or []))
            fp = pli._review_fingerprint(row)
            stayed, _ = preview(
                conn, ['顧客A,KAZEN WLD(株),,長白衣ボタン,-'],
                rowClassifications=kind_override(row, '部品・資材', accept=True, fingerprint=fp),
            )
            self.assertFalse(stayed['rows'][0]['canCommit'])
            picked = stayed['rows'][0]['product']['candidates'][0]['id']
            chosen, _ = preview(
                conn, ['顧客A,KAZEN WLD(株),,長白衣ボタン,-'],
                selections={str(row['rowNo']): {'productId': picked}},
            )
            self.assertTrue(chosen['rows'][0]['canCommit'], chosen['rows'][0].get('reasons'))
            self.assertEqual(chosen['rows'][0]['product']['id'], picked)
        finally:
            conn.close()
            folder.cleanup()

    def test_existing_category_conflict_stays_blocked(self):
        folder, _path, conn = newdb()
        try:
            customer = stock.save_customer(conn, {'name': '顧客A', 'active': True})
            supplier = stock.save_supplier(conn, {'name': 'K2スタッフ工房', 'active': True})
            saved = stock.save_product(conn, {
                'code': '要確認', 'name': '別注エプロン 赤', 'category': '加工サービス',
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
            first, _ = preview(conn, ['顧客A,K2スタッフ工房,,別注エプロン 赤,L'])
            row = first['rows'][0]
            self.assertTrue(row['product'].get('categoryConflict') or any(
                '既存カテゴリ' in str(reason) for reason in row.get('reasons') or []
            ))
            self.assertFalse(row['canCommit'])
            fp = pli._review_fingerprint(row)
            stayed, _ = preview(
                conn, ['顧客A,K2スタッフ工房,,別注エプロン 赤,L'],
                rowClassifications=kind_override(row, '商品', accept=True, fingerprint=fp),
            )
            self.assertFalse(stayed['rows'][0]['canCommit'])
            self.assertEqual(
                conn.execute('SELECT category FROM products WHERE id=?', (saved['id'],)).fetchone()['category'],
                '加工サービス',
            )
        finally:
            conn.close()
            folder.cleanup()


class PartsConfirmUiTest(unittest.TestCase):
    def test_blank_part_issue_points_at_accept_not_a_code_field(self):
        folder = tempfile.TemporaryDirectory()
        try:
            row = {
                'rowNo': 2,
                'status': 'needs_review',
                'canCommit': False,
                'reasons': ['登録区分を変更しました。登録予定を確認し「この内容で了承」してから再照合してください。'],
                'csv': {
                    'customer': '顧客A', 'supplier': 'KAZEN WLD(株)', 'code': '',
                    'productName': 'エプロン用ボタン', 'size': '—',
                },
                'product': {
                    'action': 'create', 'blankSku': True, 'category': '部品・資材',
                    'candidates': [], 'reasons': [],
                },
                'supplier': {'action': 'create', 'candidates': [], 'reasons': []},
                'customer': {'action': 'create', 'candidates': []},
                'handle': {'candidates': []},
                'parenClassification': {
                    'registerKind': '部品・資材', 'autoRegisterKind': '加工サービス',
                    'plannedName': 'エプロン用ボタン', 'color': '',
                },
                'processingService': {},
            }
            st = {
                'preview': {'rows': [row], 'skuGroups': []},
                'rowClassifications': {},
                'selections': {},
                'skuDecisions': {},
                'selectedCodes': {},
                'expandedCodes': {},
                'expandedUnified': {},
                'sectionOpen': {'unified': True},
                'busy': False,
            }
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
                "process.stdout.write(JSON.stringify({labels:items.map(i=>i.targetLabel),"
                "issues:items.map(i=>(i.issues||[]).filter(x=>!x.resolved).map(x=>x.label+'|'+(x.fieldKey||''))),html}));",
                encoding='utf-8',
            )
            data.write_text(json.dumps(st), encoding='utf-8')
            proc = subprocess.run(['node', str(harness), str(JS), str(data)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)
            self.assertIn('エプロン用ボタン', out['labels'][0])
            joined = ' '.join(' '.join(row) for row in out['issues'])
            self.assertIn('登録予定の了承|kind', joined)
            self.assertNotIn('|sku', joined)
            self.assertIn('この内容で了承', out['html'])
            self.assertIn('品番なし（入力不要）', out['html'])
            self.assertIn('登録区分を変更しました', out['html'])
        finally:
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
