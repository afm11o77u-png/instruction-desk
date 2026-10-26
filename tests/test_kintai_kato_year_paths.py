"""年度表示（2026年2月〜2027年1月）と長いUNCパス設定・実レイアウト未確認時の挙動。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai

ROOT = Path(__file__).resolve().parents[1]


class FiscalYearUiTest(unittest.TestCase):
    def test_label_includes_end_month(self):
        self.assertEqual(kintai.fiscal_label(2026), '2026年2月〜2027年1月')

    def test_preferred_year_october_2026(self):
        self.assertEqual(kintai.preferred_fiscal_start_year(date(2026, 10, 26)), 2026)

    def test_preferred_year_january_is_previous(self):
        self.assertEqual(kintai.preferred_fiscal_start_year(date(2027, 1, 15)), 2026)

    def test_choices_always_include_2026(self):
        self.assertIn(2026, kintai.fiscal_year_choices(2027))
        self.assertIn(2026, kintai.fiscal_year_choices(2025))

    def test_js_year_label_not_open_ended_only(self):
        js = (ROOT / 'kintai.js').read_text(encoding='utf-8')
        self.assertIn('fiscalYearOptions', js)
        self.assertNotIn('月〜</option>', js)  # 終端なしラベルを組み立てない
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('年度（2月〜翌1月）', html)
        self.assertIn('kintaifilefill', html)
        self.assertIn('kintaiprobe', html)


class LongPathSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        kintai.bind(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_accepts_kato_unc_lengths(self):
        root = kintai.EXAMPLE_KATO_SHARE_ROOT
        relative = kintai.EXAMPLE_KATO_RELATIVE
        full = kintai.EXAMPLE_KATO_FILE
        self.assertTrue(full.startswith('\\\\'))
        self.assertLessEqual(len(full), kintai.PATH_MAX)
        saved = kintai.save_local_settings({'shareRoot': root, 'peopleFiles': {'1': relative}}, True)
        self.assertEqual(saved['shareRoot'], root)
        self.assertEqual(saved['peopleFiles']['1'], relative)
        # フルパス（主運用）も可・ルート不要
        saved2 = kintai.save_local_settings({'shareRoot': '', 'peopleFiles': {'1': full}}, True)
        self.assertEqual(saved2['peopleFiles']['1'], full)
        person = {'id': 1, 'source_file': ''}
        path = kintai.resolve_person_file(person, kintai.load_local_settings())
        self.assertIsNotNone(path)
        self.assertTrue(kintai.is_absolute_source_path(full))

    def test_fixture_mismatch_mentions_sample(self):
        # 実ファイル風にシート名が違う xlsx
        blob = kintai.build_fixture_xlsx({
            (2026, 2): [{'day': 1, 'start': '9:00', 'leave': '', 'ordinary_minutes': 480, 'overtime_minutes': 0}],
        })
        # シート名を変えるため別ブックを最小構成で… fixture は 2026-02 なので、期待月をずらす
        with self.assertRaises(ValueError) as ctx:
            kintai.parse_person_workbook(
                blob,
                kintai.fiscal_months(2026, 2),  # 12か月要求 → 一部欠け
                kintai.FIXTURE_LAYOUT,
                as_of=date(2027, 1, 31),
            )
        self.assertIn('一部の月シートがありません', str(ctx.exception))

    def test_no_sheets_message_asks_for_sample(self):
        # 空に近い：別年月だけ
        blob = kintai.build_fixture_xlsx({
            (2099, 1): [{'day': 1, 'start': '', 'leave': '', 'ordinary_minutes': 0, 'overtime_minutes': 0}],
        })
        with self.assertRaises(ValueError) as ctx:
            kintai.parse_person_workbook(blob, [(2026, 2)], kintai.FIXTURE_LAYOUT, as_of=date(2026, 2, 28))
        msg = str(ctx.exception)
        self.assertIn('サンプル', msg)

    def test_probe_lists_sheets_without_aggregating(self):
        blob = kintai.build_fixture_xlsx({
            (2026, 2): [{'day': 1, 'start': '9:00', 'leave': '', 'ordinary_minutes': 480, 'overtime_minutes': 0}],
            (2026, 3): [{'day': 1, 'start': '', 'leave': '1.0有休', 'ordinary_minutes': 0, 'overtime_minutes': 0}],
        })
        probed = kintai.probe_workbook_bytes(blob)
        names = {s['name'] for s in probed['sheets']}
        self.assertEqual(names, {'2026-02', '2026-03'})
        self.assertIn('未確認', probed['note'])


class OverviewYmTest(unittest.TestCase):
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

    def test_ym_picks_january_2027_not_ambiguous(self):
        data = kintai.list_overview(self.c, 2026, year_month=(2027, 1))
        self.assertEqual(data['fiscalLabel'], '2026年2月〜2027年1月')
        self.assertTrue(any(m['key'] == '2027-01' for m in data['months']))
        self.assertEqual(data['rows'][0]['month']['year'], 2027)
        self.assertEqual(data['rows'][0]['month']['month'], 1)
        self.assertIn(2026, [o['startYear'] for o in data['fiscalYearOptions']])


if __name__ == '__main__':
    unittest.main()
