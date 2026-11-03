"""初期在庫プレビュー: 判定フィルタ（表示のみ）の配線。"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'static' / 'product_ledger_csv.js'


class OpeningJudgeFilterTest(unittest.TestCase):
    def test_judge_filter_wired_display_only(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('id="plOpenJudgeFilter"', js)
        self.assertIn('st.openingJudgeFilter=', js)
        self.assertIn('plOpenUsageFilter', js)
        i = js.index('plOpenJudgeFilter').__index__()
        # 判定フィルタは選択状態・登録対象に触れない
        handler = js[js.index("judgeFilterEl.onchange"):js.index("document.querySelectorAll('[data-pl-open-qty]')")]
        self.assertNotIn('openingSelections', handler)
        self.assertNotIn('preview=null', handler)
        self.assertGreater(i, 0)

    def test_js_parses(self):
        try:
            r = subprocess.run(['node', '--check', str(JS)], capture_output=True, text=True)
        except FileNotFoundError:
            self.skipTest('node なし')
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
