import os, sqlite3, tempfile, unittest
from pathlib import Path
from unittest import mock
import openpyxl
import picking_merge_queue as mq
import picking_sorter as ps

ROW = dict(requester='マーク', customer='テスト顧客', name='白衣', maker='M', sku='S1', size='L', condition='新品', quantity=2, note='', note2='')


class MergeQueueSortTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / '【処理中・・・】').mkdir()
        self.env = mock.patch.dict(os.environ, {'INSTRUCTION_FOLDER_BASE': str(base)})
        self.env.start()
        self.c = sqlite3.connect(':memory:'); self.c.row_factory = sqlite3.Row
        mq.add(self.c, None, '朝', '2026-11-27', [dict(ROW, stock='warehouse'), dict(ROW, stock='carry')])
        mq.add(self.c, None, '昼', '2026-11-27', [dict(ROW, stock='factory')])
        self.base = base

    def tearDown(self):
        self.env.stop(); self.c.close(); self.tmp.cleanup()

    def run_sort(self):
        return ps.execute_sort(barcode_loader=lambda: mq.pending(self.c), barcode_marker=lambda ids, f: mq.mark_merged(self.c, ids, f))

    def test_merge_without_instruction_files_then_no_double_merge(self):
        r = self.run_sort()
        self.assertTrue(r['success'])
        self.assertEqual((r['barcode_rows'], r['barcode_lists']), (3, 2))
        self.assertEqual(r['ishikari_counts'], {'ishikari': 1, 'factory': 1, 'carry': 1})
        self.assertEqual(r['report_counts']['carry'], 1)
        wb = openpyxl.load_workbook(r['ishikari_path'])
        self.assertEqual(wb['Sheet2'].max_row, 4)
        self.assertEqual(mq.listing(self.c), [])
        self.assertEqual({x['merged_file'] for x in self.c.execute('SELECT merged_file FROM picking_merge_queue')}, {r['ishikari_file']})
        self.assertFalse(self.run_sort()['success'])  # 統合済みは二度と入らない

    def test_failure_keeps_pending(self):
        with mock.patch.object(ps, '_save_ishikari_workbook', side_effect=OSError('locked')):
            with self.assertRaises(OSError): self.run_sort()
        self.assertEqual(len(mq.listing(self.c)), 2)


if __name__ == '__main__':
    unittest.main()
