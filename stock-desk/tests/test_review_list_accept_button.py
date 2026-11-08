"""確認一覧の各対象に了承ボタンがあり、既存と同じ区分は登録でき、違う区分は通さない。"""
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


def preview(conn, lines, **extra):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products', 'name': 'p.csv',
        'file': base64.b64encode(text.encode()).decode(),
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
        "const detail=items.map(i=>api.unifiedRowDetailHtml(st,i)).join('');"
        "process.stdout.write(JSON.stringify({html:html+detail,count:items.length,"
        "labels:items.map(i=>i.targetLabel)}));",
        encoding='utf-8',
    )
    data.write_text(json.dumps(st), encoding='utf-8')
    proc = subprocess.run(['node', str(harness), str(JS), str(data)], capture_output=True, text=True)
    folder.cleanup()
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


class FabricMatchExistingPartsTest(unittest.TestCase):
    def test_select_parts_reuses_existing_without_rewriting(self):
        folder, path, conn = newdb()
        try:
            supplier = stock.save_supplier(conn, {'name': 'サーヴォ', 'active': True})
            saved = stock.save_product(conn, {
                'code': '', 'name': 'DCP515用生地(袖/裾加工用)', 'category': '部品・資材',
                'supplierId': supplier['id'], 'sizes': ['—'], 'links': [], 'active': True,
            })
            conn.commit()
            lines = ['顧客A,サーヴォ,,DCP515用生地(袖/裾加工用),-']
            first, _ = preview(conn, lines)
            row = first['rows'][0]
            self.assertTrue(row['product'].get('categoryConflict'))
            self.assertFalse(row['canCommit'])
            self.assertIn('加工サービス', ' '.join(row.get('reasons') or []))
            key = choice_key(row)
            ready, body = preview(conn, lines, rowClassifications={
                str(row['rowNo']): {
                    'registerKind': '部品・資材', 'registerKindUser': True,
                    'choiceKey': key, 'userEdited': True, 'userActed': True,
                    'reviewAccepted': True, 'reviewFingerprint': pli._review_fingerprint(row),
                },
            })
            done = ready['rows'][0]
            self.assertTrue(done['canCommit'], done.get('reasons'))
            self.assertFalse(done['product'].get('categoryConflict'))
            self.assertEqual(done['product']['id'], saved['id'])
            self.assertEqual(done['product']['category'], '部品・資材')
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': ready['previewFingerprint'],
                'previewAt': ready['previewAt'],
                'token': 'fabric-keep-parts',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            self.assertEqual(
                conn.execute('SELECT category FROM products WHERE id=?', (saved['id'],)).fetchone()['category'],
                '部品・資材',
            )
            self.assertEqual(conn.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], 1)
            blocked, _ = preview(conn, lines, rowClassifications={
                str(row['rowNo']): {
                    'registerKind': '商品', 'registerKindUser': True, 'choiceKey': key,
                    'reviewAccepted': True, 'userActed': True,
                },
            })
            self.assertFalse(blocked['rows'][0]['canCommit'])
            self.assertTrue(blocked['rows'][0]['product'].get('categoryConflict'))
            self.assertEqual(
                conn.execute('SELECT category FROM products WHERE id=?', (saved['id'],)).fetchone()['category'],
                '部品・資材',
            )
        finally:
            conn.close()
            folder.cleanup()


class EveryReviewItemHasAcceptButtonTest(unittest.TestCase):
    def test_conflict_stopped_candidates_and_sku_all_have_the_button(self):
        rows = [
            {
                'rowNo': 2, 'status': 'needs_review', 'reasons': [
                    '既存カテゴリ「部品・資材」と取込提案「加工サービス」が異なります。',
                ],
                'csv': {'customer': '顧客A', 'supplier': 'サーヴォ', 'code': '', 'productName': 'DCP515用生地(袖/裾加工用)', 'size': '—'},
                'product': {'id': 103, 'action': 'review', 'blankSku': True, 'category': '部品・資材', 'proposedCategory': '加工サービス', 'categoryConflict': True, 'candidates': [], 'reasons': []},
                'supplier': {'action': 'reuse', 'candidates': []},
                'customer': {'action': 'reuse', 'candidates': []},
                'handle': {'candidates': []},
                'parenClassification': {'registerKind': '部品・資材', 'autoRegisterKind': '部品・資材', 'plannedName': 'DCP515用生地', 'color': ''},
                'processingService': {'isPhysicalParts': True},
            },
            {
                'rowNo': 3, 'status': 'needs_review', 'reasons': ['停止中です。'],
                'csv': {'customer': '顧客A', 'supplier': 'K2', 'code': '別注', 'productName': '赤エプロン', 'size': 'L'},
                'product': {'action': 'review', 'category': '加工サービス', 'categoryConflict': False, 'candidates': [], 'reasons': ['停止中です。']},
                'supplier': {'action': 'reuse', 'candidates': []},
                'customer': {'action': 'reuse', 'candidates': []},
                'handle': {'candidates': []},
                'parenClassification': {'registerKind': '商品', 'plannedName': 'エプロン', 'color': 'レッド'},
                'processingService': {},
            },
            {
                'rowNo': 4, 'status': 'needs_review', 'reasons': ['候補から選んでください。'],
                'csv': {'customer': '顧客A', 'supplier': 'A', 'code': '', 'productName': '長白衣ボタン', 'size': '—'},
                'product': {'action': 'review', 'blankSku': True, 'category': '部品・資材', 'candidates': [{'id': 1, 'name': '長白衣ボタン'}, {'id': 2, 'name': '長白衣ボタン'}], 'reasons': []},
                'supplier': {'action': 'reuse', 'candidates': []},
                'customer': {'action': 'reuse', 'candidates': []},
                'handle': {'candidates': []},
                'parenClassification': {'registerKind': '部品・資材', 'plannedName': '長白衣ボタン'},
                'processingService': {},
            },
        ]
        st = {
            'preview': {
                'rows': rows,
                'skuGroups': [{
                    'code': 'OV1', 'codeDisplay': 'OV1', 'needsDecision': True, 'status': 'needs_decision',
                    'reviewTier': 'individual', 'rowNos': [9], 'variants': [], 'reviewReasons': [],
                }],
            },
            'rowClassifications': {},
            'selections': {},
            'skuDecisions': {},
            'selectedCodes': {},
            'expandedCodes': {},
            'expandedUnified': {},
            'sectionOpen': {'unified': True},
            'busy': False,
        }
        out = render(st)
        self.assertGreaterEqual(out['html'].count('この内容で了承'), 4)
        self.assertIn('取込提案「加工サービス」', out['html'])
        self.assertIn('停止中のため、了承では登録できません', out['html'])
        self.assertIn('商品の候補が複数あります', out['html'])


if __name__ == '__main__':
    unittest.main()
