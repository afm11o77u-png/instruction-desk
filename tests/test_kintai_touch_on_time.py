import csv
import unittest
from io import StringIO
from types import SimpleNamespace

import kintai
import kintai_touch_on_time as tot


class TouchOnTimeTest(unittest.TestCase):
    def payload(self):
        return {'year': 2026, 'month': 11, 'people': [
            {'key': 'regular:1', 'displayName': '加藤 篤郎'},
            {'key': 'part:2', 'displayName': '田中 明美'},
            {'key': 'regular:3', 'displayName': '齊藤 遼太'},
        ], 'cells': {'regular:1': {
            '1': {'kind': 'off'}, '2': {'kind': 'off'},
            '3': {'kind': 'unknown'}, '4': {'kind': 'time', 'text': '15:00まで'},
        }}}

    def test_rules_and_leading_zero(self):
        _, blob, mime, _ = tot.export_view(self.payload())
        rows = list(csv.reader(StringIO(blob.decode('cp932'))))[1:]
        kato = {r[1]: r for r in rows if r[0] == '00001804'}
        self.assertEqual(kato['20261101'][3:], ['', '', '', '2'])
        self.assertNotIn('20261102', kato)
        self.assertNotIn('20261103', kato)
        self.assertEqual(kato['20261104'][3:], ['09:00', '70', '15:00', '1'])
        self.assertEqual(kato['20261105'][3:], ['09:00', '70', '17:40', '1'])
        self.assertTrue(any(r[0] == '31002124' and r[4] == '0' for r in rows))
        self.assertEqual({r[0] for r in rows}, {'00001804', '31002124'})
        self.assertIn('Shift_JIS', mime)

    def test_preview_exclusions_duplicate_and_invalid_time(self):
        p = self.payload()
        summary = tot.preview(p)[1]
        self.assertEqual(summary['excluded'], ['齊藤 遼太'])
        self.assertEqual(summary['members'][0]['unknown'], 1)
        p['people'].append({'key': 'regular:4', 'displayName': '加藤篤郎'})
        with self.assertRaisesRegex(ValueError, '重複'):
            tot.build(p)
        p = self.payload()
        p['cells']['regular:1']['4']['text'] = '未定'
        with self.assertRaises(ValueError):
            tot.build(p)

    def test_task_mark(self):
        p = self.payload()
        import kintai_shift
        p['cells']['regular:1']['5'] = {'kind': 'task', 'text': next(iter(kintai_shift.TASK_TEXTS))}
        rows = tot.build(p)[1]
        self.assertIn(['00001804', '20261105', '', '09:00', '70', '17:40', '1'], rows)

    def test_api_existing_permissions_and_read_only(self):
        class NoDatabase:
            def execute(self, *args):
                raise AssertionError('Export must not access the database')
        owner = SimpleNamespace(user={'role': 'owner'})
        self.assertEqual(kintai.handle_post(owner, NoDatabase(), '/api/kintai/shift/touch-on-time/preview', self.payload())[0], 200)
        self.assertEqual(kintai.handle_post(owner, NoDatabase(), '/api/kintai/shift/touch-on-time/csv', self.payload())[0], 200)
        user = SimpleNamespace(user={'role': 'user', 'manage': 0, 'overview': 0, 'mark_kintai': 0})
        self.assertEqual(kintai.handle_post(user, NoDatabase(), '/api/kintai/shift/touch-on-time/csv', self.payload())[0], 403)


if __name__ == '__main__':
    unittest.main()
