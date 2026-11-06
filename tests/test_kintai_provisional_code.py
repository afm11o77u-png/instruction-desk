"""仮社員番号（pending / pending-）を、読取元パスと出勤簿取込成功時に実番号へ更新する。"""
from __future__ import annotations

import calendar
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai

ROOT = Path(__file__).resolve().parents[1]
SAITO_PATH = (
    r'\\Desktop-iu7d38a\e\札幌発寒支店共有\札幌発寒支店'
    r'\① 正社員（営業手当無し）001～050'
    r'\018 正社員（営業手当無し）出勤簿2026.2〜.xlsx'
)
USER = {
    'id': 1, 'name': '主', 'login': 'o@ex.com', 'role': 'owner',
    'manage': 1, 'overview': 1, 'mark_kintai': 1,
}


def _fixture_bytes() -> bytes:
    data = {}
    for y, m in kintai.fiscal_months(2026, 2):
        last = calendar.monthrange(y, m)[1]
        data[(y, m)] = [
            {
                'day': d,
                'start': '9:00' if d == 1 else '',
                'leave': '',
                'ordinary_minutes': 480 if d == 1 else 0,
                'overtime_minutes': 0,
            }
            for d in range(1, last + 1)
        ]
    return kintai.build_fixture_xlsx(data)


class _Db(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        kintai.bind(self.data)
        self.c = sqlite3.connect(self.data / 't.sqlite3')
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, version INTEGER)"
        )
        kintai.schema(self.c)
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def person(self, name: str):
        return self.c.execute(
            'SELECT * FROM kintai_people WHERE display_name=?', (name,)
        ).fetchone()

    def code_of(self, name: str) -> str:
        return self.person(name)['employee_code']


class ProvisionalCodeSaveTest(_Db):
    def test_infer_reads_leading_digits_of_filename(self):
        self.assertEqual(kintai.infer_code_from_path(SAITO_PATH), '018')
        self.assertEqual(kintai.infer_code_from_path(r'C:\work\009 出勤簿.xlsx'), '009')
        self.assertEqual(kintai.infer_code_from_path(r'\\share\正社員出勤簿.xlsx'), '')

    def test_save_path_promotes_pending_to_018(self):
        saito = self.person('齊藤 遼太')
        self.assertEqual(saito['employee_code'], 'pending')
        self.assertEqual(saito['employment'], 'regular')
        saved = kintai.update_person_file(self.c, saito['id'], '  ' + SAITO_PATH + '  ')
        self.c.commit()
        row = self.person('齊藤 遼太')
        self.assertEqual(row['employee_code'], '018')
        self.assertEqual(row['employment'], 'regular')
        self.assertEqual(row['source_file'], SAITO_PATH)
        self.assertEqual(saved['employeeCode'], '018')
        self.assertEqual(saved['personId'], saito['id'])
        self.assertEqual(saved['sourceFile'], SAITO_PATH)
        # 018パートの廣瀬がいても、018正社員への更新は衝突しない
        hirose = self.person('廣瀬 愛')
        self.assertEqual(hirose['employee_code'], '018')
        self.assertEqual(hirose['employment'], 'part')
        both = list(self.c.execute(
            "SELECT employment FROM kintai_people WHERE employee_code='018' ORDER BY employment"
        ))
        self.assertEqual([r['employment'] for r in both], ['part', 'regular'])

    def test_save_path_promotes_pending_dash_code(self):
        saito = self.person('齊藤 遼太')
        self.c.execute(
            "UPDATE kintai_people SET employee_code=? WHERE id=?",
            ('pending-saitoryota', saito['id']),
        )
        self.c.commit()
        saved = kintai.update_person_file(self.c, saito['id'], SAITO_PATH)
        self.assertEqual(saved['employeeCode'], '018')
        self.assertEqual(self.code_of('齊藤 遼太'), '018')

    def test_save_path_does_not_overwrite_real_code(self):
        kato = self.person('加藤 篤郎')
        self.assertEqual(kato['employee_code'], '009')
        saved = kintai.update_person_file(self.c, kato['id'], SAITO_PATH)
        self.c.commit()
        row = self.person('加藤 篤郎')
        self.assertEqual(row['employee_code'], '009')
        self.assertEqual(row['source_file'], SAITO_PATH)
        self.assertEqual(saved['employeeCode'], '009')
        self.assertEqual(self.code_of('齊藤 遼太'), 'pending')

    def test_save_path_keeps_pending_when_same_employment_code_exists(self):
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,80,0)',
            ('018', 'regular', '別人 太郎', ''),
        )
        self.c.commit()
        saito = self.person('齊藤 遼太')
        saved = kintai.update_person_file(self.c, saito['id'], SAITO_PATH)
        self.c.commit()
        row = self.person('齊藤 遼太')
        self.assertEqual(row['employee_code'], 'pending')
        self.assertEqual(row['source_file'], SAITO_PATH)
        self.assertEqual(saved['employeeCode'], 'pending')
        other = self.c.execute(
            "SELECT display_name FROM kintai_people WHERE employee_code='018' AND employment='regular'"
        ).fetchone()
        self.assertEqual(other['display_name'], '別人 太郎')

    def test_save_path_without_code_keeps_pending(self):
        saito = self.person('齊藤 遼太')
        saved = kintai.update_person_file(self.c, saito['id'], r'\\share\正社員出勤簿.xlsx')
        self.assertEqual(saved['employeeCode'], 'pending')
        self.assertEqual(self.code_of('齊藤 遼太'), 'pending')

    def test_api_returns_updated_employee_code(self):
        saito = self.person('齊藤 遼太')

        class H:
            user = USER
            path = '/api/kintai/person-file'

        status, body = kintai.handle_post(H(), self.c, '/api/kintai/person-file', {
            'personId': saito['id'],
            'sourceFile': SAITO_PATH,
        })
        self.c.commit()
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])
        self.assertEqual(body['employeeCode'], '018')
        self.assertEqual(body['personId'], saito['id'])
        overview = kintai.list_overview(self.c, 2026, year_month=(2026, 2))
        shown = next(p for p in overview['people'] if p['id'] == saito['id'])
        self.assertEqual(shown['employeeCode'], '018')
        self.assertEqual(shown['sourceFile'], SAITO_PATH)


class ProvisionalCodeImportTest(_Db):
    def _place(self, filename: str) -> str:
        folder = self.data / 'kintai-fixtures'
        folder.mkdir(exist_ok=True)
        (folder / filename).write_bytes(_fixture_bytes())
        return filename

    def _settings(self, person_ids=()):
        # 正社員の既定は実レイアウト。検証xlsxは対象者ごとに fixture-v1 を指定する。
        layouts = {str(pid): 'fixture-v1' for pid in person_ids}
        (self.data / 'kintai-local.json').write_text(
            json.dumps(
                {
                    'shareRoot': '',
                    'layoutId': 'fixture-v1',
                    'peopleFiles': {},
                    'peopleLayouts': layouts,
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

    def _import(self, person_ids):
        self._settings(person_ids)
        return kintai.import_all(self.c, USER, 2026, as_of=date(2027, 1, 31))

    def test_import_success_promotes_already_saved_pending(self):
        """パスだけ保存済みで pending のままの人を、取込成功時に救う。"""
        name = '018 正社員（営業手当無し）出勤簿2026.2〜.xlsx'
        self._place(name)
        saito = self.person('齊藤 遼太')
        self.c.execute(
            'UPDATE kintai_people SET source_file=? WHERE id=?', (name, saito['id'])
        )
        self.c.commit()
        summary = self._import([saito['id']])
        self.c.commit()
        item = next(p for p in summary['people'] if p['personId'] == saito['id'])
        self.assertEqual(item['status'], '成功')
        self.assertEqual(item['employeeCode'], '018')
        self.assertEqual(self.code_of('齊藤 遼太'), '018')
        self.assertEqual(self.person('齊藤 遼太')['employment'], 'regular')
        self.assertEqual(self.code_of('廣瀬 愛'), '018')
        self.assertEqual(self.person('廣瀬 愛')['employment'], 'part')
        self.assertEqual(self.code_of('加藤 篤郎'), '009')

    def test_import_promotes_pending_dash_code(self):
        name = '018 正社員出勤簿.xlsx'
        self._place(name)
        saito = self.person('齊藤 遼太')
        self.c.execute(
            'UPDATE kintai_people SET employee_code=?, source_file=? WHERE id=?',
            ('pending-saitoryota', name, saito['id']),
        )
        self.c.commit()
        summary = self._import([saito['id']])
        item = next(p for p in summary['people'] if p['personId'] == saito['id'])
        self.assertEqual(item['status'], '成功')
        self.assertEqual(item['employeeCode'], '018')
        self.assertEqual(self.code_of('齊藤 遼太'), '018')

    def test_import_does_not_overwrite_real_code(self):
        name = '018 正社員（営業手当無し）出勤簿2026.2〜.xlsx'
        self._place(name)
        kato = self.person('加藤 篤郎')
        self.c.execute(
            'UPDATE kintai_people SET source_file=? WHERE id=?', (name, kato['id'])
        )
        self.c.commit()
        summary = self._import([kato['id']])
        item = next(p for p in summary['people'] if p['personId'] == kato['id'])
        self.assertEqual(item['status'], '成功')
        self.assertEqual(item['employeeCode'], '009')
        self.assertEqual(self.code_of('加藤 篤郎'), '009')
        self.assertEqual(self.code_of('齊藤 遼太'), 'pending')

    def test_import_keeps_pending_on_same_employment_collision(self):
        name = '018 正社員出勤簿.xlsx'
        self._place(name)
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,80,0)',
            ('018', 'regular', '別人 太郎', ''),
        )
        saito = self.person('齊藤 遼太')
        self.c.execute(
            'UPDATE kintai_people SET source_file=? WHERE id=?', (name, saito['id'])
        )
        self.c.commit()
        summary = self._import([saito['id']])
        self.c.commit()
        item = next(p for p in summary['people'] if p['personId'] == saito['id'])
        self.assertEqual(item['status'], '成功')
        self.assertEqual(item['employeeCode'], 'pending')
        self.assertEqual(self.code_of('齊藤 遼太'), 'pending')
        other = self.c.execute(
            "SELECT display_name FROM kintai_people WHERE employee_code='018' AND employment='regular'"
        ).fetchone()
        self.assertEqual(other['display_name'], '別人 太郎')

    def test_import_failure_does_not_promote(self):
        folder = self.data / 'kintai-fixtures'
        folder.mkdir()
        name = '018 壊れた出勤簿.xlsx'
        (folder / name).write_bytes(b'not-xlsx')
        saito = self.person('齊藤 遼太')
        self.c.execute(
            'UPDATE kintai_people SET source_file=? WHERE id=?', (name, saito['id'])
        )
        self.c.commit()
        summary = self._import([saito['id']])
        item = next(p for p in summary['people'] if p['personId'] == saito['id'])
        self.assertEqual(item['status'], 'エラー')
        self.assertEqual(item['employeeCode'], 'pending')
        self.assertEqual(self.code_of('齊藤 遼太'), 'pending')

    def test_api_import_returns_promoted_code(self):
        name = '018 正社員出勤簿.xlsx'
        self._place(name)
        saito = self.person('齊藤 遼太')
        self.c.execute(
            'UPDATE kintai_people SET source_file=? WHERE id=?', (name, saito['id'])
        )
        self.c.commit()
        self._settings([saito['id']])

        class H:
            user = USER
            path = '/api/kintai/import'

        status, body = kintai.handle_post(H(), self.c, '/api/kintai/import', {'year': 2026})
        self.c.commit()
        self.assertEqual(status, 200)
        item = next(p for p in body['people'] if p['personId'] == saito['id'])
        self.assertEqual(item['status'], '成功')
        self.assertEqual(item['employeeCode'], '018')
        self.assertEqual(self.code_of('齊藤 遼太'), '018')


class PathsImportStillPromotesTest(_Db):
    def test_bulk_paths_import_still_updates_pending_and_leaves_unlisted(self):
        saito = self.person('齊藤 遼太')
        summary = kintai.apply_paths_import(self.c, [{
            'displayName': '齊藤 遼太',
            'nameKey': kintai.name_match_key('齊藤 遼太'),
            'path': SAITO_PATH,
            'retired': False,
            'employeeCode': '018',
            'employment': 'regular',
            'row': 2,
        }])
        self.c.commit()
        hit = next(p for p in summary['people'] if p.get('personId') == saito['id'])
        self.assertEqual(hit['status'], '更新')
        self.assertEqual(hit['employeeCode'], '018')
        self.assertIn('018', hit['detail'])
        self.assertEqual(self.code_of('齊藤 遼太'), '018')
        self.assertEqual(self.person('廣瀬 愛')['employee_code'], '018')
        self.assertEqual(self.person('廣瀬 愛')['employment'], 'part')
        kato = self.person('加藤 篤郎')
        self.assertEqual(kato['employee_code'], '009')
        self.assertEqual(kato['source_file'], '')
        kept = next(p for p in summary['keptUnlisted'] if p['displayName'] == '加藤 篤郎')
        self.assertEqual(kept['employeeCode'], '009')


class ProvisionalCodeUiWireTest(unittest.TestCase):
    def test_screen_applies_employee_code_from_response(self):
        js = (ROOT / 'kintai.js').read_text(encoding='utf-8')
        self.assertIn('function applyPersonCode(', js)
        self.assertIn('saved.employeeCode', js)
        self.assertIn('applyPersonCode(p.personId, p.employeeCode)', js)
        self.assertIn('applyPersonCode(personId, saved.employeeCode)', js)


if __name__ == '__main__':
    unittest.main()
