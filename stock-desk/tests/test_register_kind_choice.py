"""詳細で制服・部品・加工を選び、了承した区分だけ登録する。"""
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
        'importMode': 'products', 'name': 'kind.csv',
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


def override(row, kind, *, accept=False, fingerprint=''):
    data = {
        'registerKind': kind,
        'registerKindUser': True,
        'choiceKey': choice_key(row),
        'userEdited': True,
    }
    if accept:
        data['reviewAccepted'] = True
        data['reviewFingerprint'] = fingerprint
        data['userActed'] = True
    return {str(row['rowNo']): data}


def names(conn):
    return {
        (row['code'], row['name'], row['category'])
        for row in conn.execute('SELECT code, name, category FROM products')
    }


class CodedK2UniformChoiceTest(unittest.TestCase):
    LINES = ['顧客A,K2スタッフ工房,G-100,チェック柄ベスト,M']

    def test_processing_candidate_becomes_uniform_only(self):
        folder, path, conn = newdb()
        try:
            first, _ = preview(conn, self.LINES)
            row = first['rows'][0]
            self.assertTrue((row.get('processingService') or {}).get('k2GarmentCandidate'))
            self.assertEqual((row.get('product') or {}).get('category'), '加工サービス')
            held, _ = preview(conn, self.LINES, rowClassifications=override(row, '商品'))
            chosen = held['rows'][0]
            self.assertEqual(chosen['product']['category'], '制服')
            self.assertEqual(chosen['csv']['code'], 'G-100')
            self.assertNotIn('チェック柄', chosen['csv']['code'])
            self.assertFalse(chosen['canCommit'])
            self.assertEqual(chosen['status'], 'needs_review')
            plans = held.get('processingServicePlans') or []
            self.assertFalse(any('チェック柄ベスト' in (p.get('serviceName') or '') for p in plans))
            fp = pli._review_fingerprint(chosen)
            self.assertEqual(fp.split('\u0001')[0], '商品')
            ready, body = preview(
                conn, self.LINES,
                rowClassifications=override(row, '商品', accept=True, fingerprint=fp),
            )
            done = ready['rows'][0]
            self.assertTrue(done['canCommit'], done.get('reasons'))
            self.assertEqual(done['product']['category'], '制服')
            self.assertFalse(ready.get('processingServicePlans'))
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': ready['previewFingerprint'],
                'previewAt': ready['previewAt'],
                'token': 'kind-uniform-1',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            saved = list(conn.execute('SELECT code, name, category, supplier_id FROM products'))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]['code'], 'G-100')
            self.assertEqual(saved[0]['category'], '制服')
            self.assertNotIn('別注', saved[0]['code'])
            k2 = conn.execute("SELECT id FROM suppliers WHERE name='K2スタッフ工房'").fetchone()
            self.assertEqual(saved[0]['supplier_id'], k2['id'])
            self.assertEqual(conn.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
            again, _ = preview(conn, self.LINES)
            reused = again['rows'][0]
            self.assertEqual(reused['product']['id'], conn.execute('SELECT id FROM products').fetchone()['id'])
            self.assertEqual(reused['product']['category'], '制服')
            self.assertFalse(again.get('processingServicePlans'))
            self.assertEqual(len(list(conn.execute('SELECT id FROM products'))), 1)
        finally:
            conn.close()
            folder.cleanup()


class KindDirectionTest(unittest.TestCase):
    def test_parts_to_uniform_and_uniform_to_parts_or_process(self):
        folder, path, conn = newdb()
        try:
            lines = [
                '顧客A,K2スタッフ工房,,エプロン用ボタン,-',
                '顧客A,住商,AS-1,シャツ,L',
                '顧客A,住商,AS-2,ジャケット,L',
            ]
            first, _ = preview(conn, lines)
            by_name = {row['csv']['productName']: row for row in first['rows']}
            button = by_name['エプロン用ボタン']
            shirt = by_name['シャツ']
            jacket = by_name['ジャケット']
            self.assertEqual(button['product']['category'], '部品・資材')
            specs = {
                str(button['rowNo']): override(button, '商品')[str(button['rowNo'])],
                str(shirt['rowNo']): override(shirt, '部品・資材')[str(shirt['rowNo'])],
                str(jacket['rowNo']): override(jacket, '加工サービス')[str(jacket['rowNo'])],
            }
            held, _ = preview(conn, lines, rowClassifications=specs)
            held_by = {row['csv']['productName']: row for row in held['rows']}
            self.assertEqual(held_by['エプロン用ボタン']['product']['category'], '制服')
            self.assertEqual(held_by['シャツ']['product']['category'], '部品・資材')
            self.assertEqual(held_by['シャツ']['csv']['code'], 'AS-1')
            self.assertEqual(held_by['ジャケット']['product']['category'], '加工サービス')
            fps = {name: pli._review_fingerprint(row) for name, row in held_by.items()}
            accepted = {
                str(button['rowNo']): override(button, '商品', accept=True, fingerprint=fps['エプロン用ボタン'])[str(button['rowNo'])],
                str(shirt['rowNo']): override(shirt, '部品・資材', accept=True, fingerprint=fps['シャツ'])[str(shirt['rowNo'])],
                str(jacket['rowNo']): override(jacket, '加工サービス', accept=True, fingerprint=fps['ジャケット'])[str(jacket['rowNo'])],
            }
            ready, body = preview(conn, lines, rowClassifications=accepted)
            self.assertTrue(all(row['canCommit'] for row in ready['rows']), [row.get('reasons') for row in ready['rows']])
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': ready['previewFingerprint'],
                'previewAt': ready['previewAt'],
                'token': 'kind-directions',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            got = names(conn)
            self.assertIn(('', 'エプロン用ボタン', '制服'), got)
            self.assertNotIn(('', 'エプロン用ボタン', '部品・資材'), got)
            self.assertTrue(any(code == 'AS-1' and cat == '部品・資材' for code, _name, cat in got))
            self.assertFalse(any(code == 'AS-1' and cat == '制服' for code, _name, cat in got))
            self.assertFalse(any(code == 'AS-2' and cat == '制服' for code, _name, cat in got))
            self.assertTrue(any(cat == '加工サービス' for _c, _n, cat in got))
            self.assertEqual(conn.execute('SELECT COUNT(*) n FROM lots').fetchone()['n'], 0)
        finally:
            conn.close()
            folder.cleanup()

    def test_garment_paren_processing_stays(self):
        folder, path, conn = newdb()
        try:
            lines = ['顧客A,住商モンブラン,6-615,コックコート(裾上げ),L']
            first, body = preview(conn, lines)
            row = first['rows'][0]
            self.assertNotEqual(row['product'].get('category'), '加工サービス')
            self.assertIn('裾上げ', str(row.get('handle') or {}) + str(row.get('parenClassification') or {}))
            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': first['previewFingerprint'],
                'previewAt': first['previewAt'],
                'token': 'kind-hem',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            cats = {row['name']: row['category'] for row in conn.execute('SELECT name, category FROM products')}
            self.assertEqual(cats.get('コックコート'), '制服')
            self.assertEqual(cats.get('裾上げ'), '加工サービス')
            garment = conn.execute("SELECT id, code FROM products WHERE code='6-615'").fetchone()
            self.assertEqual(garment['code'], '6-615')
        finally:
            conn.close()
            folder.cleanup()


class ChoiceSafetyTest(unittest.TestCase):
    def test_wrong_row_key_and_stale_accept_do_not_apply(self):
        folder, _path, conn = newdb()
        try:
            lines = [
                '顧客A,K2スタッフ工房,G-100,チェック柄ベスト,M',
                '顧客A,K2スタッフ工房,G-200,エプロン,M',
            ]
            first, _ = preview(conn, lines)
            vest = next(row for row in first['rows'] if row['csv']['productName'] == 'チェック柄ベスト')
            apron = next(row for row in first['rows'] if row['csv']['productName'] == 'エプロン')
            bad = override(vest, '商品')
            bad[str(vest['rowNo'])]['choiceKey'] = choice_key(apron)
            stayed, _ = preview(conn, lines, rowClassifications=bad)
            vest2 = next(row for row in stayed['rows'] if row['csv']['code'] == 'G-100')
            self.assertNotEqual(vest2['product'].get('category'), '制服')
            held, _ = preview(conn, [lines[0]], rowClassifications=override(vest, '商品'))
            fp_uniform = pli._review_fingerprint(held['rows'][0])
            changed = override(vest, '部品・資材', accept=True, fingerprint=fp_uniform)
            rejected, _ = preview(conn, [lines[0]], rowClassifications=changed)
            self.assertFalse(rejected['rows'][0]['canCommit'])
            self.assertEqual(rejected['rows'][0]['status'], 'needs_review')
        finally:
            conn.close()
            folder.cleanup()

    def test_existing_category_is_not_rewritten(self):
        folder, _path, conn = newdb()
        try:
            supplier = stock.save_supplier(conn, {'name': '住商', 'active': True})
            stock.save_product(conn, {
                'code': 'AS-9', 'name': 'シャツ', 'category': '制服',
                'supplierId': supplier['id'], 'sizes': ['L'], 'links': [], 'active': True,
            })
            conn.commit()
            before = conn.execute("SELECT category FROM products WHERE code='AS-9'").fetchone()['category']
            lines = ['顧客A,住商,AS-9,シャツ,L']
            first, _ = preview(conn, lines)
            row = first['rows'][0]
            held, _ = preview(conn, lines, rowClassifications=override(row, '部品・資材', accept=True, fingerprint='x'))
            self.assertFalse(held['rows'][0]['canCommit'])
            self.assertTrue((held['rows'][0].get('product') or {}).get('categoryConflict'))
            self.assertEqual(
                conn.execute("SELECT category FROM products WHERE code='AS-9'").fetchone()['category'],
                before,
            )
        finally:
            conn.close()
            folder.cleanup()


class ChoiceUiTest(unittest.TestCase):
    def test_detail_explains_the_choice(self):
        folder, _path, conn = newdb()
        try:
            first, _ = preview(conn, ['顧客A,K2スタッフ工房,G-100,チェック柄ベスト,M'])
            row = first['rows'][0]
            st = {
                'preview': first,
                'rowClassifications': {
                    str(row['rowNo']): {'registerKind': '商品', 'registerKindUser': True},
                },
                'busy': False,
            }
            harness = Path(folder.name) / 'h.js'
            data = Path(folder.name) / 'c.json'
            harness.write_text(
                "const fs=require('fs');const vm=require('vm');"
                "const sandbox={window:{},document:{getElementById(){return null},querySelector(){return null},querySelectorAll(){return []}},console,CSS:{escape:s=>String(s)},esc:s=>String(s==null?'':s)};"
                "vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);"
                "const api=sandbox.window.ProductLedgerCsv.__test;"
                "const st=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));"
                "const row=st.preview.rows[0];"
                "const html=api.parenCell(st,row);"
                "process.stdout.write(JSON.stringify({html, fp:api.reviewFingerprint(st,row), key:api.rowChoiceKey(row)}));",
                encoding='utf-8',
            )
            data.write_text(json.dumps(st), encoding='utf-8')
            proc = subprocess.run(['node', str(harness), str(JS), str(data)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)
            self.assertIn('制服', out['html'])
            self.assertIn('この内容で了承', out['html'])
            self.assertIn('選んだ区分だけ登録', out['html'])
            self.assertIn('G-100', out['html'])
            self.assertTrue(out['fp'].startswith('商品\u0001G-100'))
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
