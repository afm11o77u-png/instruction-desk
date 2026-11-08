"""了承は確定を止める事項がある間は押せない。表記差は選んだ既存を使い、一致する区分は古い提案文で止めない。"""
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
        "api.dropBlockedAcceptance(st);"
        "const items=api.buildUnifiedReviewItems(st);"
        "const html=api.unifiedListRowsHtml(st, items);"
        "process.stdout.write(JSON.stringify({html,state:st,items:items.map(i=>({label:i.targetLabel,"
        "open:(i.issues||[]).filter(x=>!x.resolved).map(x=>x.label)}))}));",
        encoding='utf-8',
    )
    data.write_text(json.dumps(st), encoding='utf-8')
    proc = subprocess.run(['node', str(harness), str(JS), str(data)], capture_output=True, text=True)
    folder.cleanup()
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


def base_state(rows, **extra):
    st = {
        'preview': {'rows': rows, 'skuGroups': extra.pop('skuGroups', [])},
        'rowClassifications': {},
        'selections': {},
        'skuDecisions': {},
        'selectedCodes': {},
        'expandedCodes': {},
        'expandedUnified': {},
        'sectionOpen': {'unified': True},
        'busy': False,
    }
    st.update(extra)
    return st


class MatchingCategoryIsNotABlockerTest(unittest.TestCase):
    def test_stale_process_text_does_not_disable_matching_parts(self):
        row = {
            'rowNo': 8, 'status': 'needs_review', 'canCommit': False,
            'reasons': ['既存カテゴリ「部品・資材」と取込提案「加工サービス」が異なります。'],
            'csv': {
                'customer': '顧客A', 'supplier': 'サーヴォ', 'code': '',
                'productName': 'DCP515用生地(袖/裾加工用)', 'size': '—',
            },
            'product': {
                'id': 103, 'action': 'review', 'blankSku': True, 'category': '部品・資材',
                'proposedCategory': '加工サービス', 'categoryConflict': True, 'candidates': [],
            },
            'supplier': {'action': 'reuse', 'candidates': []},
            'customer': {'action': 'reuse', 'candidates': []},
            'handle': {'candidates': []},
            'parenClassification': {
                'registerKind': '部品・資材', 'autoRegisterKind': '部品・資材',
                'plannedName': 'DCP515用生地',
            },
            'processingService': {'isPhysicalParts': True},
        }
        out = render(base_state([row]))
        html = out['html']
        self.assertIn('この内容で了承', html)
        self.assertNotIn('data-pl-accept-state="blocked"', html)
        self.assertIn('data-pl-accept-state="ready"', html)
        self.assertNotRegex(html, r'data-pl-review-accept="8"[^>]*disabled')
        self.assertTrue(all('加工サービス' not in lab for lab in out['items'][0]['open']))
        self.assertTrue(any('了承' in lab for lab in out['items'][0]['open']))

    def test_true_mismatch_disables_and_drops_stale_accepted(self):
        row = {
            'rowNo': 9, 'status': 'needs_review', 'canCommit': False,
            'reasons': ['既存カテゴリ「加工サービス」は変更しません。'],
            'csv': {
                'customer': '顧客A', 'supplier': 'K2', 'code': '要確認',
                'productName': '赤エプロン', 'size': 'L',
            },
            'product': {
                'id': 4, 'action': 'review', 'category': '加工サービス',
                'categoryConflict': True, 'candidates': [],
            },
            'supplier': {'action': 'reuse', 'candidates': []},
            'customer': {'action': 'reuse', 'candidates': []},
            'handle': {'candidates': []},
            'parenClassification': {'registerKind': '商品', 'plannedName': 'エプロン', 'color': 'レッド'},
        }
        st = base_state([row], rowClassifications={
            '9': {'reviewAccepted': True, 'reviewFingerprint': 'x', 'registerKind': '商品', 'userActed': True},
        })
        out = render(st)
        self.assertNotIn('了承済み', out['html'])
        self.assertIn('disabled', out['html'])
        self.assertIn('既存カテゴリ「加工サービス」は変更しません', out['html'])
        self.assertFalse(out['state']['rowClassifications']['9'].get('reviewAccepted'))


class NotationChoiceTest(unittest.TestCase):
    def test_supplier_and_customer_variants_commit_the_chosen_id(self):
        folder, path, conn = newdb()
        try:
            supplier = stock.save_supplier(conn, {'name': 'KAZEN WLD株式会社', 'active': True})
            stopped = stock.save_supplier(conn, {'name': '長白衣ボタン㈱', 'active': False})
            customer = stock.save_customer(conn, {'name': '石屋商事 事務所株式会社', 'active': True})
            conn.commit()
            lines = [
                '顧客A,KAZEN WLD㈱,,エプロン用ボタン,-',
                '石屋商事 事務所㈱,サーヴォ,,青リボン,-',
            ]
            # 青リボンの仕入先は新規でよい。顧客だけ表記差。
            stock.save_supplier(conn, {'name': 'サーヴォ', 'active': True})
            conn.commit()
            first, body = preview(conn, lines)
            button = next(r for r in first['rows'] if 'ボタン' in r['csv']['productName'])
            ribbon = next(r for r in first['rows'] if 'リボン' in r['csv']['productName'])
            self.assertEqual(button['supplier']['action'], 'review')
            self.assertEqual(button['supplier']['candidates'][0]['id'], supplier['id'])
            self.assertFalse(button['canCommit'])
            self.assertEqual(ribbon['customer']['action'], 'review')
            self.assertEqual(ribbon['customer']['candidates'][0]['id'], customer['id'])
            self.assertFalse(ribbon['canCommit'])
            ignored, _ = preview(conn, lines, rowClassifications={
                str(button['rowNo']): {
                    'reviewAccepted': True,
                    'reviewFingerprint': pli._review_fingerprint(button),
                    'userActed': True,
                },
            })
            still = next(r for r in ignored['rows'] if r['rowNo'] == button['rowNo'])
            self.assertFalse(still['canCommit'])
            self.assertTrue(still.get('acceptanceIgnored'))
            chosen, body = preview(conn, lines, selections={
                str(button['rowNo']): {'supplierId': supplier['id']},
                str(ribbon['rowNo']): {'customerId': customer['id']},
            }, rowClassifications={
                str(button['rowNo']): {
                    'reviewAccepted': True,
                    'reviewFingerprint': pli._review_fingerprint(button),
                    'registerKind': '部品・資材', 'registerKindUser': True,
                    'choiceKey': choice_key(button), 'userActed': True,
                },
                str(ribbon['rowNo']): {
                    'reviewAccepted': True,
                    'reviewFingerprint': pli._review_fingerprint(ribbon),
                    'registerKind': '部品・資材', 'registerKindUser': True,
                    'choiceKey': choice_key(ribbon), 'userActed': True,
                },
            })
            button2 = next(r for r in chosen['rows'] if r['rowNo'] == button['rowNo'])
            ribbon2 = next(r for r in chosen['rows'] if r['rowNo'] == ribbon['rowNo'])
            self.assertTrue(button2['canCommit'], button2.get('reasons'))
            self.assertEqual(button2['supplier']['id'], supplier['id'])
            self.assertTrue(ribbon2['canCommit'], ribbon2.get('reasons'))
            self.assertEqual(ribbon2['customer']['id'], customer['id'])
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': chosen['previewFingerprint'],
                'previewAt': chosen['previewAt'],
                'token': 'notation-choice-1',
                'selections': {
                    str(button['rowNo']): {'supplierId': supplier['id']},
                    str(ribbon['rowNo']): {'customerId': customer['id']},
                },
                'rowClassifications': body['rowClassifications'],
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            names = [r['name'] for r in conn.execute('SELECT name FROM suppliers ORDER BY id')]
            self.assertIn('KAZEN WLD株式会社', names)
            self.assertNotIn('KAZEN WLD㈱', names)
            customers = [r['name'] for r in conn.execute('SELECT name FROM customers ORDER BY id')]
            self.assertIn('石屋商事 事務所株式会社', customers)
            self.assertNotIn('石屋商事 事務所㈱', customers)
            blocked, _ = preview(conn, ['顧客A,長白衣ボタン株式会社,,長白衣ボタン,-'], selections={
                '2': {'supplierId': stopped['id']},
            })
            row = blocked['rows'][0]
            self.assertEqual(row['supplier']['action'], 'review')
            self.assertFalse(row['canCommit'])
            self.assertTrue(any('停止中' in str(x) for x in row.get('reasons') or []))
        finally:
            conn.close()
            folder.cleanup()


class NotationUiTest(unittest.TestCase):
    def test_unselected_candidate_disables_accept_and_is_not_auto(self):
        row = {
            'rowNo': 12, 'status': 'needs_review', 'canCommit': False,
            'reasons': ['12行目：仕入先「KAZEN WLD㈱」は㈱／株式会社などの表記差の候補があります。'],
            'csv': {
                'customer': '顧客A', 'supplier': 'KAZEN WLD㈱', 'code': '',
                'productName': 'エプロン用ボタン', 'size': '—',
            },
            'product': {'action': 'create', 'blankSku': True, 'category': '部品・資材', 'candidates': []},
            'supplier': {
                'action': 'review',
                'candidates': [{'id': 7, 'name': 'KAZEN WLD株式会社', 'active': True}],
            },
            'customer': {'action': 'create', 'candidates': []},
            'handle': {'candidates': []},
            'parenClassification': {'registerKind': '部品・資材', 'plannedName': 'エプロン用ボタン'},
            'processingService': {'isPhysicalParts': True},
        }
        ribbon = {
            'rowNo': 13, 'status': 'needs_review', 'canCommit': False,
            'reasons': ['13行目：顧客「石屋商事 事務所㈱」は㈱／株式会社などの表記差の候補があります。'],
            'csv': {
                'customer': '石屋商事 事務所㈱', 'supplier': 'サーヴォ', 'code': '',
                'productName': '青リボン', 'size': '—',
            },
            'product': {'action': 'create', 'blankSku': True, 'category': '部品・資材', 'candidates': []},
            'supplier': {'action': 'reuse', 'candidates': []},
            'customer': {
                'action': 'review',
                'candidates': [{'id': 3, 'name': '石屋商事 事務所株式会社', 'active': True}],
            },
            'handle': {'candidates': []},
            'parenClassification': {'registerKind': '部品・資材', 'plannedName': 'リボン', 'color': 'ブルー'},
        }
        st = base_state([row, ribbon], rowClassifications={
            '12': {'reviewAccepted': True, 'reviewFingerprint': 'leftover', 'userActed': True},
            '13': {'reviewAccepted': True, 'reviewFingerprint': 'leftover', 'userActed': True},
        })
        out = render(st)
        html = out['html']
        self.assertNotIn('了承済み', html)
        self.assertIn('仕入先の候補', html)
        self.assertIn('顧客の候補', html)
        self.assertIn('KAZEN WLD株式会社', html)
        self.assertIn('石屋商事 事務所株式会社', html)
        self.assertEqual(html.count('disabled'), html.count('data-pl-review-accept'))
        self.assertIn('選択が必要', html)
        self.assertIn('表記の候補から選んでください', html)
        self.assertFalse(out['state']['rowClassifications']['12'].get('reviewAccepted'))
        self.assertFalse(out['state']['rowClassifications']['13'].get('reviewAccepted'))


if __name__ == '__main__':
    unittest.main()
