"""出勤簿パス.xlsx 一括取込・雇用区分／退職者見出し・U+202A除去。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import kintai

SAMPLE = Path('/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/internal/kintai-shukkinbo-paths.xlsx')


@unittest.skipUnless(SAMPLE.is_file(), '出勤簿パス.xlsx がストアに無い')
class PathsWorkbookParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = SAMPLE.read_bytes()

    def test_strip_bidi_and_sections(self):
        entries = kintai.parse_paths_workbook(self.blob)
        self.assertGreaterEqual(len(entries), 15)
        # 加藤は正社員セクション
        kato = next(e for e in entries if e['nameKey'] == kintai.name_match_key('加藤 篤郎'))
        self.assertFalse(kato['retired'])
        self.assertEqual(kato['employment'], 'regular')
        self.assertEqual(kato['employeeCode'], '009')
        self.assertFalse(kato['path'].startswith('\u202a'))
        self.assertTrue(kato['path'].startswith('\\\\'))

        # 今城は契約社員セクション（seed旧regularでも取込で契約）
        ima = next(e for e in entries if '今城' in e['displayName'])
        self.assertEqual(ima['employment'], 'contract')
        self.assertFalse(ima['retired'])
        self.assertFalse(ima['path'].startswith('\u202a'))

        # 037 契約＝須永、037 パート＝南田（混同しない）
        sunaga = next(e for e in entries if '須永' in e['displayName'])
        minami = next(e for e in entries if '南田' in e['displayName'])
        self.assertEqual(sunaga['employeeCode'], '037')
        self.assertEqual(sunaga['employment'], 'contract')
        self.assertEqual(minami['employeeCode'], '037')
        self.assertEqual(minami['employment'], 'part')

        retirees = [e for e in entries if e['retired']]
        self.assertEqual(len(retirees), 4)
        names = {e['displayName'] for e in retirees}
        self.assertTrue(any('大槻' in n for n in names))
        self.assertTrue(any('遠藤' in n for n in names))

    def test_heading_helpers(self):
        self.assertEqual(kintai.parse_section_heading('正社員セイシャイン'), ('regular', False))
        self.assertEqual(kintai.parse_section_heading('契約社員ケイヤクシャイン'), ('contract', False))
        self.assertEqual(kintai.parse_section_heading('パート'), ('part', False))
        self.assertEqual(kintai.parse_section_heading('退職者タイショクシャ'), (None, True))
        self.assertEqual(kintai.parse_section_heading('加藤 篤郎'), (None, None))

    def test_clean_u202a(self):
        self.assertEqual(
            kintai.clean_source_path('\u202a\\\\server\\share\\a.xlsx'),
            '\\\\server\\share\\a.xlsx',
        )


@unittest.skipUnless(SAMPLE.is_file(), '出勤簿パス.xlsx がストアに無い')
class PathsImportDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        kintai.bind(self.data)
        self.db = self.data / 't.sqlite3'
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, version INTEGER)"
        )
        kintai.schema(self.c)
        self.c.commit()
        # 旧seed相当: 今城を regular のまま残して取込で契約へ更新できることを確認
        self.c.execute(
            "UPDATE kintai_people SET employment='regular' WHERE employee_code='031'"
        )
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_import_sets_paths_retirees_keeps_037_distinct(self):
        summary = kintai.import_paths_from_upload(self.c, '出勤簿パス.xlsx', SAMPLE.read_bytes())
        self.c.commit()
        self.assertGreaterEqual(summary['counts']['updated'], 10)
        self.assertGreaterEqual(summary['counts']['created'], 4)  # 退職者4
        # 保存コピー
        self.assertTrue((self.data / 'kintai-paths.xlsx').is_file())

        kato = self.c.execute(
            "SELECT * FROM kintai_people WHERE employee_code='009'"
        ).fetchone()
        self.assertIsNotNone(kato)
        self.assertEqual(kato['retired'], 0)
        self.assertTrue(kato['source_file'].startswith('\\\\'))
        self.assertNotIn('\u202a', kato['source_file'])

        ima = self.c.execute(
            "SELECT * FROM kintai_people WHERE display_name LIKE '%今城%'"
        ).fetchone()
        self.assertEqual(ima['employment'], 'contract')
        self.assertEqual(ima['retired'], 0)

        rows037 = list(self.c.execute(
            "SELECT employment, display_name, retired FROM kintai_people WHERE employee_code='037' ORDER BY employment"
        ))
        self.assertEqual(len(rows037), 2)
        self.assertEqual(rows037[0]['employment'], 'contract')
        self.assertIn('須永', rows037[0]['display_name'])
        self.assertEqual(rows037[1]['employment'], 'part')
        self.assertIn('南田', rows037[1]['display_name'])

        retired = list(self.c.execute(
            "SELECT display_name FROM kintai_people WHERE retired=1 ORDER BY sort_order, id"
        ))
        self.assertEqual(len(retired), 4)

        # 再読込でも加藤は消えず件数維持
        before = self.c.execute('SELECT COUNT(*) FROM kintai_people').fetchone()[0]
        summary2 = kintai.import_paths_from_saved(self.c)
        self.c.commit()
        after = self.c.execute('SELECT COUNT(*) FROM kintai_people').fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(summary2['counts']['created'], 0)

    def test_overview_separates_retired(self):
        kintai.import_paths_from_upload(self.c, 'paths.xlsx', SAMPLE.read_bytes())
        self.c.commit()
        data = kintai.list_overview(self.c, 2026, year_month=(2026, 2))
        retired_flags = [r['retired'] for r in data['rows']]
        # 在職が先、退職が後
        if any(retired_flags):
            first_retired = next(i for i, v in enumerate(retired_flags) if v)
            self.assertTrue(all(not v for v in retired_flags[:first_retired]))
        self.assertTrue(any(r['retired'] for r in data['rows']))
        self.assertTrue(any(not r['retired'] and '加藤' in r['displayName'] for r in data['rows']))


class PathsUiWireTest(unittest.TestCase):
    def test_html_has_paths_import_and_filter(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / 'index.html').read_text(encoding='utf-8')
        self.assertIn('kintaipathsimport', html)
        self.assertIn('kintaipathsreload', html)
        self.assertIn('kintaifilter', html)
        js = (root / 'kintai.js').read_text(encoding='utf-8')
        self.assertIn('importPathsFile', js)
        self.assertIn('退職者', js)
        gitignore = (root / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('data/kintai-paths.xlsx', gitignore)


if __name__ == '__main__':
    unittest.main()
