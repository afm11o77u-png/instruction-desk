import tempfile
import unittest
from pathlib import Path
from unittest import mock

import picking_sorter as ps


class PickingSortResumeTest(unittest.TestCase):
    def test_failed_move_resumes_without_saving_excel_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            processing = base / "processing"
            processed = base / "processed"
            processing.mkdir()
            for name in ("a.xlsx", "b.xlsx"):
                (processing / name).write_bytes(name.encode())
            saves = mock.Mock(return_value=(base / "out.xlsx", {"ishikari": 1, "factory": 0, "carry": 0}))
            report = mock.Mock(return_value=(base / "report.xlsx", {"carry": 0, "factory": 0}))
            real_rename = Path.rename
            calls = {"n": 0}

            def flaky_rename(self, target):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError("locked")
                return real_rename(self, target)

            with mock.patch.object(ps, "get_base_dir", return_value=base), \
                 mock.patch.object(ps, "get_processing_dir", return_value=processing), \
                 mock.patch.object(ps, "get_processed_base_dir", return_value=processed), \
                 mock.patch.object(ps, "get_pending_files", side_effect=lambda: sorted(processing.glob("*.xlsx"))), \
                 mock.patch.object(ps, "_extract_rows_from_workbook", return_value=[]), \
                 mock.patch.object(ps, "_extract_address_from_workbook", return_value=None), \
                 mock.patch.object(ps, "_save_ishikari_workbook", saves), \
                 mock.patch.object(ps, "_save_report_workbook", report):
                with mock.patch.object(Path, "rename", flaky_rename):
                    with self.assertRaises(OSError):
                        ps.execute_sort(processed_date="2026-11-26")
                self.assertTrue((base / ".instruction-desk-pending-sort.json").exists())
                preview = ps.preview_sort()
                self.assertTrue(preview["resume_move"])
                result = ps.execute_sort()
            self.assertTrue(result["resumed_move"])
            self.assertEqual(result["moved_count"], 2)
            self.assertEqual(saves.call_count, 1)
            self.assertEqual(report.call_count, 1)
            self.assertFalse((base / ".instruction-desk-pending-sort.json").exists())
            self.assertEqual(sorted(p.name for p in (processed / "2026.11月" / "20261126").iterdir()), ["a.xlsx", "b.xlsx"])
            self.assertEqual(list(processing.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
