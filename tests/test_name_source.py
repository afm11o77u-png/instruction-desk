"""ネーム情報（ソース）：Excel取り込み・書き出し・差分・反映（合成データ）。"""
import base64, io, json, sqlite3, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
import names, name_source


def _db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE orders (id INTEGER PRIMARY KEY, payload TEXT NOT NULL DEFAULT "{}")')
    c.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, role TEXT, active INTEGER DEFAULT 1, must_change INTEGER DEFAULT 0)')
    names.schema(c)
    name_source.schema(c)
    return c


def _h(role='owner'):
    return SimpleNamespace(user={'id': 1, 'name': 'テスト', 'role': role, 'active': 1, 'must_change': 0}, path='/api/name-source')


def _workbook():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws['B2'] = '見出し'
    heads = ['顧客名', '貼付け部位', '大きさ', '色', 'フォント', 'フォントサイズ', '太字', '列', '名前', '定型', '段サイズ（上下幅/左右/上下）', '備考', '個人的メモ', '担当']
    for i, h in enumerate(heads):
        ws.cell(4, 3 + i, h)
    rows = [
        ['テスト食品', '白衣：左袖25cm', '大ネーム', '白', 'HG丸ゴシック', '大汎用', '〇', 1, 'フルネーム', None, None, None, None, '担当A'],
        [None, '白下：左前\u3000帽子：右耳上', '小ネーム', '男性：青\u3000女性：赤', 'MSPゴシック', 36, '〇', 1, '苗字のみ', None, None, None, None, None],
        [None, '部署名：製造、物流', None, None, None, None, None, None, None, None, None, None, None, None],
        ['見本商店', '帽子：後頭部', '小ネーム', '緑', 'HG丸ゴシック', '小汎用', None, 1, 'フルネーム', None, None, None, None, None],
    ]
    for r, values in enumerate(rows, start=5):
        for i, v in enumerate(values):
            ws.cell(r, 3 + i, v)
    ws.merge_cells('C5:C7')
    ws.merge_cells('P5:P7')
    ws.merge_cells('D7:O7')
    out = io.BytesIO()
    wb.save(out)
    return base64.b64encode(out.getvalue()).decode()


def _web(c):
    fields = dict(reading='', heading='', note='', pending='', caption='', departments=[], unknown=False, blank=False, reasons=[])
    cid = names._apply_customer(c, dict(fields, customer='テスト食品'), None)
    big = names._write_format(c, dict(fields, customer='テスト食品', format='大ネーム（フルネーム）', placement='白衣：左袖25cm',
                                      groups=[], colors=[], fixed='白', width=80, height=50, small=False), cid)
    small = names._write_format(c, dict(fields, customer='テスト食品', format='小ネーム（苗字）', placement='白下：左前／帽子：左耳上',
                                        groups=['男性', '女性'], colors=['青', '赤'], fixed='', width=50, height=24, small=True), cid)
    c.execute("UPDATE name_formats SET family='MS PGothic', maximum_font=36 WHERE id=?", (small,))
    oid = names._apply_customer(c, dict(fields, customer='ほか商事'), None)
    other = names._write_format(c, dict(fields, customer='ほか商事', format='大ネーム', placement='白衣：左胸',
                                        groups=[], colors=[], fixed='青', width=80, height=50, small=False), oid)
    return big, small, other


class NameSourceTest(unittest.TestCase):
    def test_import_export_diff_apply(self):
        c = _db()
        big, small, other = _web(c)
        code, res = name_source.import_excel(_h(), c, {'data': _workbook(), 'confirm': True})
        self.assertEqual(code, 200)
        self.assertEqual(res['imported']['rows'], 3)
        self.assertEqual(res['imported']['notes'], 1)
        self.assertEqual(res['autoLinked'], 2)

        # 書き出し→読み直しで同じ内容（結合・補足行・ネームID）
        parsed = name_source.parse_workbook(name_source.build_workbook(c))
        stored = [dict(r) for r in c.execute('SELECT * FROM name_source_rows ORDER BY position')]
        self.assertEqual([p['kind'] for p in parsed['rows']], [r['kind'] for r in stored])
        for p, r in zip(parsed['rows'], stored):
            for k in name_source.TEXT_KEYS:
                self.assertEqual(p.get(k, ''), r[k], k)
            self.assertEqual(p['name_id'], name_source._id_text(r['format_id']))

        items, counts = name_source.compute(c)
        self.assertEqual(counts, {'changed': 1, 'source_only': 1, 'web_only': 1, 'same': 1})
        changed = next(i for i in items if i['type'] == 'changed')
        self.assertEqual([d['field'] for d in changed['diffs']], ['placement'])

        # 一般ユーザーは反映できない
        code, _ = name_source.apply(_h('sales'), c, {'choices': {changed['key']: 'to_web'}})
        self.assertEqual(code, 403)

        src_only = next(i for i in items if i['type'] == 'source_only')
        web_only = next(i for i in items if i['type'] == 'web_only')
        code, res = name_source.apply(_h(), c, {'choices': {changed['key']: 'to_web', src_only['key']: 'to_web', web_only['key']: 'to_source'}})
        self.assertEqual(code, 200)
        self.assertEqual(res['done'], {'to_web': 1, 'to_source': 0, 'created_web': 1, 'created_source': 1})
        self.assertEqual(c.execute('SELECT placement FROM name_formats WHERE id=?', (small,)).fetchone()[0], '白下：左前／帽子：右耳上')
        _, counts = name_source.compute(c)
        self.assertEqual(counts, {'changed': 0, 'source_only': 0, 'web_only': 0, 'same': 4})
        self.assertEqual(c.execute("SELECT COUNT(*) FROM name_source_history WHERE action IN ('ソース→WEB','WEB→ソース')").fetchone()[0], 3)

    def test_link_conflict_and_owner_only_edit(self):
        c = _db()
        big, small, _ = _web(c)
        name_source.import_excel(_h(), c, {'data': _workbook(), 'confirm': True})
        rows = c.execute("SELECT id, format_id FROM name_source_rows WHERE kind='row' ORDER BY position").fetchall()
        code, res = name_source.save_cell(_h(), c, {'id': rows[2]['id'], 'field': 'name_id', 'value': name_source._id_text(big)})
        self.assertEqual(code, 400)
        code, _ = name_source.save_cell(_h('sales'), c, {'id': rows[0]['id'], 'field': 'memo', 'value': 'x'})
        self.assertEqual(code, 403)

    def test_non_owner_cannot_view(self):
        """主管理者以外は一覧・書き出し・差分も見られない。"""
        c = _db()
        _web(c)
        name_source.import_excel(_h(), c, {'data': _workbook(), 'confirm': True})
        for role in ('sales', 'floor', 'floor2', 'floor3', 'warehouse'):
            code, body = name_source.list_rows(_h(role), c)
            self.assertEqual(code, 403, role)
            self.assertNotIn('rows', body if isinstance(body, dict) else {})
            result = name_source.export_excel(_h(role), c)
            self.assertEqual(result[0], 403, role)
            code, body = name_source.diff(_h(role), c, {})
            self.assertEqual(code, 403, role)
            self.assertNotIn('items', body if isinstance(body, dict) else {})
        code, body = name_source.list_rows(_h(), c)
        self.assertEqual(code, 200)
        self.assertGreater(len(body['rows']), 0)
        code, body = name_source.diff(_h(), c, {})
        self.assertEqual(code, 200)
        self.assertIn('counts', body)



if __name__ == '__main__':
    unittest.main()
