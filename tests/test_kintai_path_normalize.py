"""ファイル名・共有パスの正規化照合と取込エラー詳細。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai

ROOT = Path(__file__).resolve().parents[1]
REAL_NAME = '009  正社員（営業手当無し）出勤簿2026.2～.xlsx'
TYPED_NAME = '009 正社員(営業手当無し)出勤簿2026.2〜.xlsx'


class NormalizeFilenameTest(unittest.TestCase):
    def test_example_kato_file_matches_real_chars(self):
        rel = kintai.EXAMPLE_PATHS_BY_EMPLOYMENT['regular']['relative']
        self.assertTrue(rel.endswith(REAL_NAME))
        self.assertIn('①', rel)
        fname = rel.rsplit('\\', 1)[-1]
        self.assertEqual(fname, REAL_NAME)
        self.assertEqual(fname[3:5], '  ')  # 009のあと半角スペース2
        self.assertIn('（', fname)
        self.assertIn('～', fname)
        self.assertTrue(kintai.EXAMPLE_YEAR_SHARE_ROOT.endswith('出勤簿   2026年度版'))
        self.assertNotIn('①', kintai.EXAMPLE_YEAR_SHARE_ROOT)

    def test_normalize_equates_space_paren_tilde(self):
        self.assertEqual(
            kintai.normalize_filename_key(REAL_NAME),
            kintai.normalize_filename_key(TYPED_NAME),
        )

    def test_js_has_howto_and_import_result(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('kintaihow', html)
        self.assertIn('kintaiimportresult', html)
        self.assertIn('kintailistfiles', html)
        js = (ROOT / 'kintai.js').read_text(encoding='utf-8')
        self.assertIn('paintImportResult', js)
        self.assertIn('shareInputUnsaved', js)
        self.assertIn('未保存', js)


class FuzzyResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / '出勤簿   2026年度版'
        self.root.mkdir(parents=True)
        # 実ファイル名（全角）
        (self.root / REAL_NAME).write_bytes(kintai.build_fixture_xlsx({
            (2026, 2): [{'day': 1, 'start': '9:00', 'leave': '', 'ordinary_minutes': 480, 'overtime_minutes': 0}],
            **{
                ym: [{'day': 1, 'start': '', 'leave': '', 'ordinary_minutes': 0, 'overtime_minutes': 0}]
                for ym in kintai.fiscal_months(2026, 2) if ym != (2026, 2)
            },
        }))
        kintai.bind(Path(self.tmp.name))
        # 入力はスペース少なめの親 + 半角括弧ファイル名
        typed_parent = Path(self.tmp.name) / '出勤簿  2026年度版'
        self.settings = {
            'shareRoot': str(typed_parent),
            'layoutId': 'fixture-v1',
            'peopleFiles': {'1': TYPED_NAME},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_dir_and_file_normalize_resolve(self):
        person = {'id': 1, 'employee_code': '009', 'source_file': ''}
        resolved = kintai.resolve_person_file_existing(person, self.settings)
        self.assertTrue(resolved['ok'], resolved)
        self.assertEqual(resolved['path'].name, REAL_NAME)

    def test_not_found_shows_full_path_and_candidates(self):
        settings = {
            'shareRoot': str(self.root),
            'layoutId': 'fixture-v1',
            'peopleFiles': {'1': '031  何か.xlsx'},
        }
        person = {'id': 1, 'employee_code': '031', 'source_file': ''}
        resolved = kintai.resolve_person_file_existing(person, settings)
        self.assertFalse(resolved['ok'])
        self.assertEqual(resolved['kind'], 'not_found')
        self.assertIn('探索パス', resolved['message'])
        self.assertTrue(any('009' in c for c in resolved['candidates']) or '候補' in resolved['message'])


class ImportErrorDetailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        kintai.bind(self.data)
        self.db = self.data / 't.sqlite3'
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, version INTEGER, mark_kintai INTEGER DEFAULT 0)"
        )
        kintai.schema(self.c)
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_import_reports_unset_and_not_found(self):
        # 1人だけファイル設定（存在しない）
        p = self.c.execute("SELECT * FROM kintai_people WHERE employee_code='009'").fetchone()
        share = self.data / 'share'
        share.mkdir()
        (self.data / 'kintai-local.json').write_text(
            json.dumps({
                'shareRoot': str(share),
                'layoutId': 'fixture-v1',
                'peopleFiles': {str(p['id']): '009  no-such.xlsx'},
            }, ensure_ascii=False),
            encoding='utf-8',
        )
        user = {'id': 1, 'name': '主', 'login': 'o@ex.com', 'role': 'owner', 'mark_kintai': 1, 'manage': 1}
        summary = kintai.import_all(self.c, user, 2026, as_of=date(2027, 1, 31))
        self.c.commit()
        by_id = {x['personId']: x for x in summary['people']}
        kato = by_id[p['id']]
        self.assertEqual(kato['status'], 'エラー')
        self.assertEqual(kato['errorKind'], 'not_found')
        self.assertIn('探索パス', kato['error'])
        unset = [x for x in summary['people'] if x['status'] == '未取得']
        self.assertGreaterEqual(len(unset), 1)
        self.assertEqual(summary['counts']['error'] + summary['counts']['missing'] + summary['counts']['ok'], len(summary['people']))


if __name__ == '__main__':
    unittest.main()
