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


if __name__ == '__main__':
    unittest.main()
