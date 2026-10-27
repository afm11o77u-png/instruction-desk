"""シフト表先頭の齊藤遼太: 出勤簿マスタ共有・先頭固定・パス非推測。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import kintai
import kintai_shift


class SaitoShiftTopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        kintai.bind(self.data)
        self.c = sqlite3.connect(self.data / 't.sqlite3')
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, "
            "version INTEGER, mark_kintai INTEGER DEFAULT 0)"
        )
        kintai.schema(self.c)
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_seed_includes_saito_first(self):
        rows = list(self.c.execute(
            'SELECT display_name, employee_code, employment, source_file, sort_order, retired '
            'FROM kintai_people WHERE active=1 ORDER BY sort_order, id'
        ))
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]['display_name'], '齊藤 遼太')
        self.assertEqual(rows[0]['sort_order'], 0)
        self.assertEqual(rows[0]['source_file'], '')
        self.assertEqual(rows[0]['retired'], 0)
        # パスを推測していないこと
        self.assertFalse(rows[0]['source_file'].startswith('\\\\'))
        self.assertEqual(rows[1]['display_name'], '加藤 篤郎')

    def test_ensure_adds_to_existing_db_and_pins_top(self):
        self.c.execute('DELETE FROM kintai_people')
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,1,0)',
            ('009', 'regular', '加藤 篤郎', ''),
        )
        self.c.commit()
        kintai._ensure_shift_top_people(self.c)
        self.c.commit()
        rows = list(self.c.execute(
            'SELECT display_name, sort_order, source_file FROM kintai_people WHERE active=1 '
            'ORDER BY sort_order, id'
        ))
        self.assertEqual(rows[0]['display_name'], '齊藤 遼太')
        self.assertEqual(rows[0]['sort_order'], 0)
        self.assertEqual(rows[0]['source_file'], '')

    def test_ensure_re_pins_without_clearing_path(self):
        kintai._ensure_shift_top_people(self.c)
        row = self.c.execute(
            "SELECT id FROM kintai_people WHERE display_name='齊藤 遼太'"
        ).fetchone()
        self.c.execute(
            'UPDATE kintai_people SET sort_order=99, source_file=? WHERE id=?',
            (r'\\share\manual.xlsx', row['id']),
        )
        self.c.commit()
        kintai._ensure_shift_top_people(self.c)
        again = self.c.execute(
            "SELECT sort_order, source_file FROM kintai_people WHERE id=?",
            (row['id'],),
        ).fetchone()
        self.assertEqual(again['sort_order'], 0)
        self.assertEqual(again['source_file'], r'\\share\manual.xlsx')

    def test_shift_roster_lists_saito_first(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertTrue(sheet['people'])
        self.assertEqual(sheet['people'][0]['displayName'], '齊藤 遼太')
        self.assertEqual(sheet['people'][0]['source'], 'roster')
        self.assertEqual(sheet['people'][0]['sortOrder'], 0)

    def test_default_people_has_single_saito_entry(self):
        names = [p['display_name'] for p in kintai.DEFAULT_PEOPLE]
        self.assertEqual(names.count('齊藤 遼太'), 1)
        self.assertEqual(kintai.DEFAULT_PEOPLE[0]['display_name'], '齊藤 遼太')
        self.assertEqual(kintai.DEFAULT_PEOPLE[0]['sort_order'], 0)
        self.assertEqual(kintai.DEFAULT_PEOPLE[0].get('file_hint'), '')


if __name__ == '__main__':
    unittest.main()
