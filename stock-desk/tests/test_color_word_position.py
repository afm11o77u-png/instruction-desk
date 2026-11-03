"""色語の位置（先頭／末尾）だけが違う商品名を同一商品にそろえる。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import legacy_code_assign as a  # noqa: E402


def rows(*names, customer='よつ葉乳業㈱', code='別注'):
    return [(i, {'顧客': customer, '商品コード': code, '商品名': n}) for i, n in enumerate(names)]


class ColorPositionTest(unittest.TestCase):
    def names(self, rs):
        return [r['商品名'] for _, r in rs]

    def test_prefix_color_aligned_to_suffix_form(self):
        rs = rows('清浄帽子　緑', '準清浄帽子　緑', '＊緑清浄帽子', '＊緑準清浄帽子', '緑清浄帽子')
        self.assertEqual(a.align_color_word_position(rs), 3)
        self.assertEqual(self.names(rs), ['清浄帽子　緑', '準清浄帽子　緑', '＊清浄帽子　緑',
                                          '＊準清浄帽子　緑', '清浄帽子　緑'])
        self.assertEqual(rs[2][1]['元商品名'], '＊緑清浄帽子')

    def test_rest_must_match_exactly(self):
        # 清浄帽子 と 準清浄帽子 は別商品のまま
        rs = rows('準清浄帽子　緑', '緑清浄帽子', '白衣', '緑ベスト')
        self.assertEqual(a.align_color_word_position(rs), 0)
        self.assertEqual(self.names(rs), ['準清浄帽子　緑', '緑清浄帽子', '白衣', '緑ベスト'])

    def test_other_color_or_other_customer_code_not_merged(self):
        rs = rows('清浄帽子　白', '緑清浄帽子') + rows('清浄帽子　緑', customer='他社') \
            + rows('清浄帽子　緑', code='X1')
        self.assertEqual(a.align_color_word_position(rs), 0)

    def test_assign_rows_ledger_strip_used(self):
        rs = rows('清浄帽子　緑', '＊緑清浄帽子')
        counts = a.assign_rows(rs, strip_used=True)
        self.assertEqual(counts.get('color_position'), 1)
        self.assertEqual(self.names(rs), ['清浄帽子　緑', '清浄帽子　緑'])
        self.assertEqual(rs[1][1]['元商品名'], '＊緑清浄帽子')


if __name__ == '__main__':
    unittest.main()
