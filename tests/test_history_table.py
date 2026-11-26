"""依頼履歴：店舗切替・状態絞り込み・1件1行の表。社内受付の確認は従来の records(true)。"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HistoryTableTest(unittest.TestCase):
    def test_history_table_and_staff_unchanged(self):
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        self.assertIn("function renderRecords(){renderHistory();$('#staff-list').innerHTML=records(true)}", js)
        self.assertIn("const HISTORY_STATUSES=['受付待ち','受付済み','差し戻し'];", js)
        self.assertIn("(single?'':'<option value=\"\">すべての店舗</option>')", js)  # 1店舗だけの利用者は切替なし
        self.assertIn("status:b.dataset.accept?'受付済み':'差し戻し'", js)  # 受付する／差し戻す（RW-52でサーバー保存）
        self.assertNotIn("createdAt:new Date().toISOString()", js)  # 依頼日はサーバーが記録（RW-52）


if __name__ == '__main__':
    unittest.main()
