"""月シートの実績は、日別の集計とは別にキャッシュのまま表示する。"""
import unittest
import kintai


class DailyActualsTests(unittest.TestCase):
    def grid(self):
        labels = [
            '普通残業時間（×1.25）', '深夜残業時間（×1.50）',
            '60時間超時間（×1.50）', '休日出勤勤務時間（×1.35）',
            '休日出勤残業時間（×1.35）', '早朝・深夜勤務時間（×0.25）',
            '休日出勤深夜残業時間（×1.60）', '休日出勤深夜勤務時間（×0.25）',
        ]
        grid = {(47, 1): '実績'}
        for i, label in enumerate(labels):
            row, col, value_col = (47 + i % 4, 2, 8) if i < 4 else (47 + i % 4, 22, 38)
            grid[row, col] = label
            grid[row, value_col] = 0 if i else 4.8166666667
        return grid

    def test_eight_cached_values_in_excel_column_order(self):
        rows = kintai._sheet_actual_rows(self.grid(), {'day_last_row': 46})
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]['value'], '4.82')
        self.assertEqual(rows[4]['label'], '休日出勤残業時間（×1.35）')
        self.assertTrue(all(r['value'] == '0.00' for r in rows[1:]))

    def test_blank_and_missing_formula_cache_are_not_zero(self):
        grid = self.grid()
        del grid[47, 8]
        grid[48, 8] = {'__missing_cache__': True, 'formula': 'SUM(X5:X46)'}
        rows = kintai._sheet_actual_rows(grid, {'day_last_row': 46})
        self.assertEqual(rows[0]['value'], '')
        self.assertEqual(rows[1]['value'], '（計算結果なし）')

    def test_no_actual_block_does_not_guess(self):
        grid = self.grid()
        del grid[47, 1]
        self.assertEqual(kintai._sheet_actual_rows(grid, {'day_last_row': 46}), [])


if __name__ == '__main__':
    unittest.main()
