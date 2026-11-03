import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import product_ledger_position as position_sku


class PositionUsedMarkTest(unittest.TestCase):
    def reg(self, name, code='NPC75082'):
        return position_sku.resolve_garment_position(code=code, product_name=name)['registeredCode']

    def test_used_mark_and_condition_suffix_ignored(self):
        for name in ('白下', '＊白下', '*白下', '＊ 白下', '白下（新品）', '＊白下（中古）', '白下(中古)'):
            self.assertEqual(self.reg(name), 'NPC75082下', name)
        self.assertEqual(self.reg('＊青上'), 'NPC75082上')

    def test_other_rules_unchanged(self):
        self.assertEqual(self.reg('パンツ'), 'NPC75082')
        self.assertEqual(self.reg('＊パンツ'), 'NPC75082')
        self.assertEqual(self.reg('裾上げ'), 'NPC75082')
        self.assertEqual(self.reg('＊上下セット'), 'NPC75082')


if __name__ == '__main__':
    unittest.main()
