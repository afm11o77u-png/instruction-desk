"""納品希望日: lead 2 days, 裾上げ days, and 自社集配 weekdays."""
import unittest
import web_request_delivery as d


class DesiredDateRulesTest(unittest.TestCase):
    TODAY = '2026-11-26'  # Thursday

    def test_earliest_is_two_calendar_days_later_plus_hem(self):
        self.assertEqual(d.earliest_date(self.TODAY, 0), '2026-11-28')
        self.assertEqual(d.earliest_date(self.TODAY, 5), '2026-12-03')
        items = [
            dict(hem_processing=False, processing_days=9),
            dict(hem_processing=True, processing_days=3),
            dict(hem_processing=True, processing_days=7),
        ]
        self.assertEqual(d.processing_days(items), 7)
        self.assertEqual(d.processing_days([]), 0)
        self.assertEqual(d.earliest_date(self.TODAY, d.processing_days(items)), '2026-12-05')

    def test_jisya_weekdays_and_hint(self):
        earliest = d.earliest_date(self.TODAY, 0)  # Sat 11/28
        nxt = d.next_allowed(earliest, '自社集配', ['月', '木'])
        self.assertEqual(nxt, '2026-11-30')  # Monday
        self.assertFalse(d.is_allowed('2026-11-28', earliest, '自社集配', ['月', '木']))
        self.assertTrue(d.is_allowed('2026-11-30', earliest, '自社集配', ['月', '木']))
        self.assertTrue(d.is_allowed('2026-12-03', earliest, '自社集配', ['月', '木']))
        text = d.hint_text(earliest, nxt, 0, '自社集配', ['月', '木'])
        self.assertIn('次回の集配日は11月30日（月）です', text)
        self.assertIn('最短は11月28日（土）です', text)

    def test_courier_or_unset_allows_any_day_from_earliest(self):
        earliest = d.earliest_date(self.TODAY, 0)
        for method in ('', '福山便', '佐川便', 'ヤマト便'):
            self.assertTrue(d.is_allowed(earliest, earliest, method, []))
            self.assertTrue(d.is_allowed('2026-11-29', earliest, method, []))  # Sunday ok
            self.assertFalse(d.is_allowed('2026-11-27', earliest, method, []))
        # 自社集配 without weekdays behaves the same
        self.assertTrue(d.is_allowed('2026-11-29', earliest, '自社集配', []))

    def test_inherit_delivery_from_parent_when_site_unset(self):
        nodes = [
            dict(id='co', parent_id=None, kind='company', delivery_method='ヤマト便'),
            dict(id='area', parent_id='co', kind='region', delivery_method='自社集配', collection_days=['火', '金']),
            dict(id='shop', parent_id='area', kind='site', delivery_method='', collection_days=[]),
            dict(id='own', parent_id='area', kind='site', delivery_method='佐川便', collection_days=['月']),
        ]
        self.assertEqual(d.resolve_delivery(nodes, 'shop'), ('自社集配', ['火', '金']))
        self.assertEqual(d.resolve_delivery(nodes, 'own'), ('佐川便', []))
        nxt = d.next_allowed(d.earliest_date(self.TODAY, 0), *d.resolve_delivery(nodes, 'shop'))
        self.assertEqual(nxt, '2026-12-01')  # Tuesday

    def test_validate_rejects_too_early_and_wrong_weekday(self):
        nodes = [dict(id='shop', parent_id=None, kind='site', delivery_method='自社集配', collection_days=['月', '木'])]
        items = [dict(hem_processing=True, processing_days=2)]
        # earliest = 11/26 + 2 + 2 = 11/30 Mon, which is a collection day
        d.validate_desired_date('2026-11-30', self.TODAY, items, nodes, 'shop')
        with self.assertRaises(ValueError) as ctx:
            d.validate_desired_date('2026-11-28', self.TODAY, items, nodes, 'shop')
        self.assertIn('11月30日', str(ctx.exception))
        with self.assertRaises(ValueError):
            d.validate_desired_date('2026-12-01', self.TODAY, items, nodes, 'shop')  # Tuesday


if __name__ == '__main__':
    unittest.main()
