"""正社員2026出勤簿サンプル（Git外）の実レイアウト読取。"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai

SAMPLE_CANDIDATES = [
    Path(os.environ['KINTAI_SAMPLE_XLSX']) if os.environ.get('KINTAI_SAMPLE_XLSX') else None,
    Path('/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/internal/kintai-sample-009-kato-2026.xlsx'),
    Path('/tmp/kintai-sample-009-kato-2026.xlsx'),
]


def _sample_path() -> Path | None:
    for p in SAMPLE_CANDIDATES:
        if p and p.is_file():
            return p
    return None


@unittest.skipUnless(_sample_path(), '加藤出勤簿サンプルxlsxが無い（Git外パス）')
class SeishainSampleParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = _sample_path()
        cls.tmp = tempfile.TemporaryDirectory()
        # ワークスペース／Git に置かず /tmp へコピーして読む
        cls.sample = Path(cls.tmp.name) / 'sample.xlsx'
        shutil.copy2(src, cls.sample)
        cls.blob = cls.sample.read_bytes()
        cls.layout = kintai.SEISHAIN_2026_LAYOUT
        cls.months = kintai.fiscal_months(2026, 2)
        cls.as_of = date(2027, 1, 31)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_hours_to_minutes_always_hours(self):
        self.assertEqual(kintai.hours_to_minutes(165.55, 't'), 9933)
        self.assertEqual(kintai.hours_to_minutes(11.16666666666665, 't'), 670)
        self.assertEqual(kintai.hours_to_minutes(0, 't'), 0)

    def test_parse_all_fiscal_months(self):
        parsed = kintai.parse_person_workbook(
            self.blob, self.months, self.layout, as_of=self.as_of
        )
        self.assertEqual(set(parsed.keys()), set(self.months))
        feb = parsed[(2026, 2)]
        self.assertEqual(feb['work_days'], 21)
        self.assertEqual(feb['paid_leave_days'], 0)
        self.assertEqual(feb['holiday_days'], 21)
        self.assertEqual(feb['ordinary_minutes'], 9933)
        self.assertEqual(feb['overtime_minutes'], 670)
        self.assertEqual(feb['total_minutes'], 10603)

        may = parsed[(2026, 5)]
        self.assertEqual(may['work_days'], 19.5)
        self.assertEqual(may['paid_leave_days'], 0.5)
        self.assertEqual(may['holiday_days'], 22)
        self.assertEqual(may['ordinary_minutes'], 9253)
        # 管理表 K = 行13+14+18（総労働−法定内ではない）
        self.assertEqual(may['overtime_minutes'], 545)

        aug = parsed[(2026, 8)]
        self.assertEqual(aug['work_days'], 19)
        self.assertEqual(aug['paid_leave_days'], 3)
        self.assertEqual(aug['holiday_days'], 20)

        nov = parsed[(2026, 11)]
        self.assertEqual(nov['work_days'], 0)
        self.assertEqual(nov['paid_leave_days'], 1)
        self.assertEqual(nov['holiday_days'], 41)
        # 管理表: 行5=0 の月は J/K 空白
        self.assertIsNone(nov['ordinary_minutes'])
        self.assertIsNone(nov['overtime_minutes'])
        self.assertIsNone(nov['total_minutes'])

    def test_layout_overtime_rows_match_kanrihyo(self):
        parts = self.layout.get('row_overtime_hours_parts')
        self.assertEqual(tuple(parts), (13, 14, 18))
        self.assertNotIn('row_total_hours', self.layout)

    def test_source_file_not_written(self):
        before = self.sample.read_bytes()
        mtime = self.sample.stat().st_mtime
        kintai.parse_person_workbook(self.blob, self.months, self.layout, as_of=self.as_of)
        self.assertEqual(self.sample.read_bytes(), before)
        self.assertEqual(self.sample.stat().st_mtime, mtime)

    def test_layout_registered(self):
        self.assertIn('shukkinbo-2026-seishain-v1', kintai.LAYOUTS)
        self.assertEqual(kintai.get_layout('shukkinbo-2026-seishain-v1')['kind'], 'summary-shukkinbo')


if __name__ == '__main__':
    unittest.main()
