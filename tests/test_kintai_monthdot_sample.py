"""契約・パート2026出勤簿サンプル（Git外）の実レイアウト読取。"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai

STORE = Path('/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/internal')

CONTRACT_CANDIDATES = [
    Path(os.environ['KINTAI_SAMPLE_CONTRACT_XLSX']) if os.environ.get('KINTAI_SAMPLE_CONTRACT_XLSX') else None,
    STORE / 'kintai-sample-031-imaki-keiyaku-2026.xlsx',
]
PART_CANDIDATES = [
    Path(os.environ['KINTAI_SAMPLE_PART_XLSX']) if os.environ.get('KINTAI_SAMPLE_PART_XLSX') else None,
    STORE / 'kintai-sample-010-kikuchi-part-2026.xlsx',
]


def _first_file(candidates: list[Path | None]) -> Path | None:
    for p in candidates:
        if p and p.is_file():
            return p
    return None


@unittest.skipUnless(_first_file(CONTRACT_CANDIDATES), '契約出勤簿サンプルxlsxが無い（Git外パス）')
class ContractMonthdotSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = _first_file(CONTRACT_CANDIDATES)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.sample = Path(cls.tmp.name) / 'contract.xlsx'
        shutil.copy2(src, cls.sample)
        cls.blob = cls.sample.read_bytes()
        cls.layout = kintai.MONTHDOT_2026_CONTRACT_LAYOUT
        cls.months = kintai.fiscal_months(2026, 2)
        cls.as_of = date(2027, 1, 31)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_parse_key_months(self):
        parsed = kintai.parse_person_workbook(
            self.blob, self.months, self.layout, as_of=self.as_of
        )
        self.assertEqual(set(parsed.keys()), set(self.months))

        may = parsed[(2026, 5)]
        self.assertEqual(may['work_days'], 2.0)
        self.assertEqual(may['paid_leave_days'], 0.0)
        self.assertEqual(may['holiday_days'], 40.0)
        self.assertEqual(may['ordinary_minutes'], 900)
        self.assertEqual(may['overtime_minutes'], 0)
        self.assertEqual(may['total_minutes'], 900)

        aug = parsed[(2026, 8)]
        self.assertEqual(aug['work_days'], 22.0)
        self.assertEqual(aug['holiday_days'], 20.0)
        self.assertEqual(aug['ordinary_minutes'], 8610)
        self.assertEqual(aug['overtime_minutes'], 264)
        self.assertEqual(aug['total_minutes'], 8874)

        feb = parsed[(2026, 2)]
        self.assertEqual(feb['work_days'], 0.0)
        self.assertIsNone(feb['ordinary_minutes'])
        self.assertIsNone(feb['overtime_minutes'])
        self.assertEqual(feb['holiday_days'], 42.0)

    def test_start_col_is_e_not_h(self):
        self.assertEqual(self.layout['start_col'], 5)
        self.assertEqual(self.layout['leave_col'], 4)

    def test_source_file_not_written(self):
        before = self.sample.read_bytes()
        mtime = self.sample.stat().st_mtime
        kintai.parse_person_workbook(self.blob, self.months, self.layout, as_of=self.as_of)
        self.assertEqual(self.sample.read_bytes(), before)
        self.assertEqual(self.sample.stat().st_mtime, mtime)


@unittest.skipUnless(_first_file(PART_CANDIDATES), 'パート出勤簿サンプルxlsxが無い（Git外パス）')
class PartMonthdotSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = _first_file(PART_CANDIDATES)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.sample = Path(cls.tmp.name) / 'part.xlsx'
        shutil.copy2(src, cls.sample)
        cls.blob = cls.sample.read_bytes()
        cls.layout = kintai.MONTHDOT_2026_PART_LAYOUT
        cls.months = kintai.fiscal_months(2026, 2)
        cls.as_of = date(2027, 1, 31)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_parse_key_months(self):
        parsed = kintai.parse_person_workbook(
            self.blob, self.months, self.layout, as_of=self.as_of
        )
        self.assertEqual(set(parsed.keys()), set(self.months))

        feb = parsed[(2026, 2)]
        self.assertEqual(feb['work_days'], 18.0)
        self.assertEqual(feb['holiday_days'], 24.0)
        self.assertEqual(feb['paid_leave_days'], 0.0)
        self.assertEqual(feb['ordinary_minutes'], 6434)
        self.assertEqual(feb['overtime_minutes'], 0)

        june = parsed[(2026, 6)]
        self.assertEqual(june['work_days'], 13.0)
        self.assertEqual(june['paid_leave_days'], 4.0)
        self.assertEqual(june['holiday_days'], 25.0)
        self.assertEqual(june['ordinary_minutes'], 4674)

        nov = parsed[(2026, 11)]
        self.assertEqual(nov['work_days'], 0.0)
        self.assertEqual(nov['paid_leave_days'], 1.0)
        self.assertEqual(nov['holiday_days'], 41.0)
        self.assertIsNone(nov['ordinary_minutes'])
        self.assertIsNone(nov['overtime_minutes'])

    def test_start_col_is_h_not_e(self):
        """パート月シート E は有休時間。始業は H。誤ると有休月の休日が崩れる。"""
        self.assertEqual(self.layout['start_col'], 8)
        self.assertEqual(self.layout['leave_col'], 4)
        wrong = dict(self.layout)
        wrong['start_col'] = 5
        parsed_wrong = kintai.parse_person_workbook(
            self.blob, [(2026, 6)], wrong, as_of=self.as_of
        )
        parsed_ok = kintai.parse_person_workbook(
            self.blob, [(2026, 6)], self.layout, as_of=self.as_of
        )
        self.assertNotEqual(
            parsed_wrong[(2026, 6)]['holiday_days'],
            parsed_ok[(2026, 6)]['holiday_days'],
        )

    def test_source_file_not_written(self):
        before = self.sample.read_bytes()
        mtime = self.sample.stat().st_mtime
        kintai.parse_person_workbook(self.blob, self.months, self.layout, as_of=self.as_of)
        self.assertEqual(self.sample.read_bytes(), before)
        self.assertEqual(self.sample.stat().st_mtime, mtime)


class MonthdotLayoutRegistryTest(unittest.TestCase):
    def test_registered_and_employment_defaults(self):
        self.assertEqual(
            kintai.LAYOUTS['shukkinbo-2026-monthdot-v1']['kind'], 'summary-monthdot'
        )
        self.assertEqual(
            kintai.LAYOUTS['shukkinbo-2026-monthdot-part-v1']['kind'], 'summary-monthdot'
        )
        self.assertEqual(
            kintai.layout_id_for_person({'id': 1, 'employment': 'contract'}, {}),
            'shukkinbo-2026-monthdot-v1',
        )
        self.assertEqual(
            kintai.layout_id_for_person({'id': 2, 'employment': 'part'}, {}),
            'shukkinbo-2026-monthdot-part-v1',
        )


if __name__ == '__main__':
    unittest.main()
