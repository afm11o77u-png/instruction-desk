import io
import sqlite3
import unittest

import openpyxl

import ishikari_shelves as m


class IshikariShelvesTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:', isolation_level=None)

    def test_seed_save_conflict_history_and_xlsx(self):
        state = m.read(self.c)
        self.assertEqual((state['version'], len(state['data']['blocks']), len(state['data']['items'])), (0, 726, 122))
        data = state['data']
        nichirei = next(b for b in data['blocks'] if b['text'] == 'ニチレイ')
        nichirei['text'] = 'ニチレイ新'
        row = next(i for i in data['items'] if i['name'] == 'ニチレイ')
        row.update(name='ニチレイ新', shelf='99')
        saved = m.save(self.c, data, 0, '加藤')
        self.assertEqual(saved['version'], 1)
        with self.assertRaises(m.ConflictError):
            m.save(self.c, data, 0, '別の人')
        self.assertEqual([r[0] for r in self.c.execute('SELECT version FROM ishikari_shelf_history ORDER BY version')], [0, 1])
        book = openpyxl.load_workbook(io.BytesIO(m.build_xlsx(m.read(self.c)['data'])))
        self.assertEqual(book.worksheets[0].cell(nichirei['r'], nichirei['c']).value, 'ニチレイ新')
        rows = [[c.value for c in r] for r in book.worksheets[1].iter_rows(min_row=6)]
        self.assertEqual(len(rows), 122)
        self.assertIn(['ニチレイ新', 99], [r[:2] for r in rows])

    def test_rejects_overlap_and_bad_values(self):
        data = m.read(self.c)['data']
        a, b = data['blocks'][3], data['blocks'][4]
        b.update(r=a['r'], c=a['c'])
        with self.assertRaisesRegex(ValueError, '重なって'):
            m.save(self.c, data, 0)
        data = m.read(self.c)['data']
        data['blocks'][0]['bg'] = 'red;x'
        with self.assertRaises(ValueError):
            m.save(self.c, data, 0)
        data = m.read(self.c)['data']
        data['items'][0]['name'] = ' '
        with self.assertRaises(ValueError):
            m.save(self.c, data, 0)
        self.assertEqual(m.read(self.c)['version'], 0)

    def test_diagram_fits_text_without_changing_saved_size(self):
        from pathlib import Path
        js = (Path(m.ROOT) / 'stock-desk' / 'static' / 'ishikari-2f.js').read_text(encoding='utf-8')
        self.assertIn('function fitCell(d)', js)
        self.assertIn('fitAll();', js)
        self.assertNotIn('b.size=lo', js)  # 表示だけ小さくし、保存する大きさは変えない

    def test_directory_table_is_sized_to_content(self):
        from pathlib import Path
        page = (Path(m.ROOT) / 'stock-desk' / 'static' / 'ishikari-2f.html').read_text(encoding='utf-8')
        self.assertIn('table{border-collapse:collapse;width:auto}', page)
        self.assertNotIn('width:100%;min-width:660px', page)



class IshikariLinkWiringTest(unittest.TestCase):
    def test_map_list_link_wired(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parents[1] / 'stock-desk/static/ishikari-2f.js').read_text(encoding='utf-8')
        for needle in ('afterPlace(d.b)', 'addItemForBlock(b)', 'itemMismatch(it)', 'addBlockForItem(it)', "map.closest('.map-scroll')"):
            self.assertIn(needle, js)

if __name__ == '__main__':
    unittest.main()
