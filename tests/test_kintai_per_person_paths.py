"""雇用区分ごとの年度ルート＋相対パス。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import kintai


class PerPersonRelativePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.year = self.base / '出勤簿   2026年度版'
        self.reg = self.year / '①　正社員（営業手当無し）001～050'
        self.con = self.year / '③　契約社員（営業手当無し）001～100'
        self.part = self.year / '⑤　パート　001～300'
        for d in (self.reg, self.con, self.part):
            d.mkdir(parents=True)
        (self.reg / '009  正社員（営業手当無し）出勤簿2026.2～.xlsx').write_bytes(b'PK\x03\x04x')
        (self.con / '036　契約社員（営業手当無し）出勤簿2026.2～.xlsx').write_bytes(b'PK\x03\x04x')
        (self.part / '010　パート用　出勤簿2026.2～.xlsx').write_bytes(b'PK\x03\x04x')
        kintai.bind(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_join_year_root_and_relative(self):
        settings = {
            'shareRoot': str(self.year),
            'peopleFiles': {
                '1': kintai.EXAMPLE_PATHS_BY_EMPLOYMENT['regular']['relative'],
            },
            'layoutId': 'fixture-v1',
            'peopleLayouts': {},
        }
        person = {'id': 1, 'employee_code': '009', 'source_file': ''}
        resolved = kintai.resolve_person_file_existing(person, settings)
        self.assertTrue(resolved['ok'], resolved)
        self.assertTrue(resolved['path'].name.startswith('009'))

    def test_typed_filename_still_matches_in_subfolder(self):
        settings = {
            'shareRoot': str(self.year),
            'peopleFiles': {
                '1': r'①　正社員（営業手当無し）001～050\009 正社員(営業手当無し)出勤簿2026.2〜.xlsx',
            },
            'layoutId': 'fixture-v1',
            'peopleLayouts': {},
        }
        person = {'id': 1, 'employee_code': '009', 'source_file': ''}
        resolved = kintai.resolve_person_file_existing(person, settings)
        self.assertTrue(resolved['ok'], resolved)

    def test_contract_and_part_examples_resolve(self):
        for kind in ('contract', 'part'):
            ex = kintai.EXAMPLE_PATHS_BY_EMPLOYMENT[kind]
            settings = {
                'shareRoot': str(self.year),
                'peopleFiles': {'9': ex['relative']},
                'layoutId': 'fixture-v1',
                'peopleLayouts': {},
            }
            person = {'id': 9, 'employee_code': ex['employeeCode'], 'source_file': ''}
            resolved = kintai.resolve_person_file_existing(person, settings)
            self.assertTrue(resolved['ok'], (kind, resolved))

    def test_layout_id_for_person_override(self):
        settings = {
            'shareRoot': '',
            'layoutId': 'shukkinbo-2026-seishain-v1',
            'peopleFiles': {},
            'peopleLayouts': {'3': 'fixture-v1'},
        }
        self.assertEqual(
            kintai.layout_id_for_person({'id': 3, 'employment': 'part'}, settings),
            'fixture-v1',
        )
        self.assertEqual(
            kintai.layout_id_for_person({'id': 1, 'employment': 'regular'}, settings),
            'shukkinbo-2026-seishain-v1',
        )
        self.assertEqual(
            kintai.layout_id_for_person({'id': 9, 'employment': 'contract'}, settings),
            'shukkinbo-2026-monthdot-v1',
        )

    def test_list_files_subdir(self):
        (self.base / 'kintai-local.json').write_text(
            json.dumps({'shareRoot': str(self.year), 'layoutId': 'fixture-v1'}, ensure_ascii=False),
            encoding='utf-8',
        )
        user = {'role': 'owner', 'manage': 1}
        result = kintai.list_share_files_for_user(
            user, prefix='009', subdir='①　正社員（営業手当無し）001～050'
        )
        self.assertGreaterEqual(result['count'], 1)
        self.assertTrue(any('009' in f['relative'] for f in result['files']))
        self.assertTrue(any('①' in s['name'] or '正社員' in s['name'] for s in result['subfolders']))

    def test_full_path_without_share_root(self):
        target = self.reg / '009  正社員（営業手当無し）出勤簿2026.2～.xlsx'
        settings = {
            'shareRoot': '',
            'peopleFiles': {'1': str(target)},
            'layoutId': 'fixture-v1',
            'peopleLayouts': {},
        }
        person = {'id': 1, 'employee_code': '009', 'source_file': ''}
        resolved = kintai.resolve_person_file_existing(person, settings)
        self.assertTrue(resolved['ok'], resolved)
        self.assertEqual(resolved['path'], target)

    def test_examples_expose_full_path(self):
        for kind in ('regular', 'contract', 'part'):
            ex = kintai.EXAMPLE_PATHS_BY_EMPLOYMENT[kind]
            self.assertIn('fullPath', ex)
            self.assertTrue(kintai.is_absolute_source_path(ex['fullPath']))


if __name__ == '__main__':
    unittest.main()
