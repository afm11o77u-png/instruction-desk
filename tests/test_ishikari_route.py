import io
import json
import shutil
import sqlite3
import subprocess
import unittest
import zipfile
from pathlib import Path

import ishikari_route as r
import ishikari_shelves as shelves

ROOT = Path(__file__).resolve().parents[1]
CASES = ['コープフーズ 北海道サンプラス短期大学（要確認）', '㈱北のアトリエ（工場）', '株式会社 ニチレイ 札幌工場', 'ﾆﾁﾚｲ',
         '（株）DHC', '【14】', 'ＡＢＣ・食品', 'よつ葉乳業 札幌工場', '有限会社ユーハイムニチレイ店', '知らない会社']


class IshikariRouteTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:', isolation_level=None)
        data = shelves.read(self.c)['data']
        ids = {}
        for b in data['blocks']:
            ids.setdefault(b['text'].replace('\n', ''), b['id'])
        data['route'] = [ids['ユーハイムニチレイ'], ids['ニチレイ'], ids['よつ葉']]
        shelves.save(self.c, data, 0, '加藤')
        self.ids = ids

    def test_match_rank_alias_and_route_validation(self):
        info = r.info(self.c)
        m = lambda s: r.match(s, info)
        self.assertEqual((m('株式会社 ニチレイ 札幌工場')['name'], m('株式会社 ニチレイ 札幌工場')['rank']), ('ニチレイ', 2))
        self.assertEqual(m('ﾆﾁﾚｲ')['rank'], 2)
        self.assertEqual(m('よつ葉乳業 札幌工場')['rank'], 3)
        self.assertEqual(m('有限会社ユーハイムニチレイ店')['name'], 'ユーハイムニチレイ')  # 長い名称を優先
        self.assertEqual(m('知らない会社')['status'], 'unmatched')
        rank = r.ranker(self.c)
        rows = [dict(customer=x) for x in ['知らない会社', 'よつ葉乳業', 'A社', 'ユーハイムニチレイ店', 'ニチレイ']]
        self.assertEqual([x['customer'] for x in r.route_sorted(rows, rank)], ['ユーハイムニチレイ店', 'ニチレイ', 'よつ葉乳業', '知らない会社', 'A社'])
        r.save_alias(self.c, '知らない会社 本店', self.ids['ニチレイ'], '加藤')
        self.assertEqual(r.ranker(self.c)('知らない会社　本店'), 2)
        with self.assertRaises(ValueError):
            r.save_alias(self.c, 'X社', 'nope')
        data = shelves.read(self.c)['data']
        data['route'] = ['nope']
        with self.assertRaisesRegex(ValueError, 'ルート'):
            shelves.save(self.c, data, 1)

    def test_excel_uses_route_order(self):
        from picking_xlsx import workbook
        rows = [dict(stock='warehouse', customer=x, requester='', name='品', maker='', sku='', size='', condition='', quantity=1, note2='')
                for x in ['A社', 'よつ葉', 'ニチレイ札幌']]
        book = workbook(dict(number='PK-000001', date='2026-11-26', title='t', rows=rows), r.ranker(self.c))
        xml = zipfile.ZipFile(io.BytesIO(book)).read('xl/worksheets/sheet1.xml').decode()
        self.assertLess(xml.index('ニチレイ札幌'), xml.index('よつ葉'))
        self.assertLess(xml.index('よつ葉'), xml.index('A社'))

    def test_route_undo_wired(self):
        html = (ROOT / 'stock-desk/static/ishikari-2f.html').read_text(encoding='utf-8')
        js = (ROOT / 'stock-desk/static/ishikari-2f.js').read_text(encoding='utf-8')
        self.assertLess(html.index('id="route-clear"'), html.index('id="route-undo"'))
        self.assertIn('routeUndo.push(routeIds())', js)

    @unittest.skipUnless(shutil.which('node'), 'node がありません')
    def test_js_matches_python(self):
        info = r.info(self.c)
        script = ("const r=require(%s);const info=%s;console.log(JSON.stringify(%s.map(s=>[r.routeKey(s),r.routeMatch(s,info)])))"
                  % (json.dumps(str(ROOT / 'picking-route.js')), json.dumps(info, ensure_ascii=False), json.dumps(CASES, ensure_ascii=False)))
        out = json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True, encoding='utf-8').stdout)
        self.assertEqual(out, [[r.route_key(s), r.match(s, info)] for s in CASES])


if __name__ == '__main__':
    unittest.main()
