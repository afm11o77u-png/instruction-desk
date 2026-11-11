"""構想メモ: 主管理者だけが見られる読み取り専用ページ。"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

import auth

ROOT = Path(__file__).resolve().parents[1]


class _H(auth.AuthMixin):
    def __init__(self, user):
        self._u = user

    def session_user(self, c):
        return self._u


def U(role, **kw):
    u = {'id': 1, 'login': 'x@example.com', 'name': 'x', 'role': role, 'manage': 0, 'overview': 0,
         'active': 1, 'must_change': 0, 'version': 1, 'mark_kintai': 0}
    u.update(kw)
    return u


class OwnerMemosTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')

    def test_owner_can_read(self):
        code, rows = _H(U('owner')).auth_get('/api/memos', self.c)
        self.assertEqual(code, 200)
        self.assertIn('依頼WEB（顧客からの受注ページ）構成案', [r['title'] for r in rows])
        code, memo = _H(U('owner')).auth_get('/api/memos/' + rows[0]['id'], self.c)
        self.assertEqual(code, 200)
        self.assertIn('## 進め方', memo['markdown'])
        self.assertEqual(_H(U('owner')).auth_get('/api/memos/../auth', self.c)[0], 404)

    def test_procedure_memo_is_available_from_management(self):
        status, memo = _H(U('owner')).auth_get('/api/memos/04-mark-workflow', self.c)
        self.assertEqual(status, 200)
        for text in ('シフト準備', '15日締め', '月末締め', '10日 幹部会議', '前月16日〜当月15日', '入力者名を載せない'):
            self.assertIn(text, memo['markdown'])
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="procedure-open" hidden>手順メモ</button>', html)
        self.assertIn("only:'04-mark-workflow'", (ROOT / 'memos.js').read_text(encoding='utf-8'))

    def test_red_memo_tanaoroshi(self):
        code, rows = _H(U('owner')).auth_get('/api/memos', self.c)
        titles = [r['title'] for r in rows]
        self.assertIn('棚卸期間中の在庫の扱い（未対応・後で検討）', titles)  # 一覧の題名に [red] を出さない
        code, memo = _H(U('owner')).auth_get('/api/memos/02-tanaoroshi-kikan', self.c)
        self.assertEqual(code, 200)
        md = memo['markdown']
        self.assertIn('2026-11-05', md)
        self.assertIn('実棚 − スナップショット', md)
        for line in md.splitlines():
            if line.strip():
                self.assertIn('[red]', line)
        js = (ROOT / 'memos.js').read_text(encoding='utf-8')
        self.assertIn('memo-red', js)
        self.assertIn('.memo-red{color:', (ROOT / 'style.css').read_text(encoding='utf-8'))

    def test_non_owner_rejected(self):
        for path in ('/api/memos', '/api/memos/01-irai-web'):
            for u in (U('sales', manage=1, overview=1), U('floor'), U('floor2'), U('floor3')):
                self.assertEqual(_H(u).auth_get(path, self.c)[0], 403)
            self.assertEqual(_H(None).auth_get(path, self.c)[0], 401)

    def test_wiring(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        footer = index[index.index('id="sidebarfooter"'):index.index('</aside>')]
        self.assertIn('id="memo-open" hidden>構想メモ<', footer)
        self.assertLess(footer.index('id="memo-open"'), footer.index('id="olddata-open"'))
        self.assertIn('id="olddata-open" hidden>旧データの取り出し方<', footer)
        self.assertIn('src="/memos.js"', index)
        self.assertIn("'/memos.js':'memos.js'", (ROOT / 'server.py').read_text(encoding='utf-8'))
        acct = (ROOT / 'account.js').read_text(encoding='utf-8')
        self.assertIn("memoNav.hidden=role!=='owner'", acct)
        self.assertIn("oldDataNav.hidden=role!=='owner'", acct)
        self.assertIn('openOldDataMemo', acct)
        js = (ROOT / 'memos.js').read_text(encoding='utf-8')
        self.assertNotIn('method', js)
        self.assertIn('memo-copy', js)
        self.assertIn('03-kyudata-toridashi', js)

    def test_old_data_memo_has_no_invented_command(self):
        code, memo = _H(U('owner')).auth_get('/api/memos/03-kyudata-toridashi', self.c)
        self.assertEqual(code, 200)
        md = memo['markdown']
        self.assertIn('Copy response', md)
        self.assertIn('calc_stock_count', md)
        self.assertIn('旧在庫データ.json', md)
        self.assertIn("document.querySelectorAll('table')[1]", md)
        self.assertIn('2128', md)
        self.assertIn('保存したデータ行数', md)
        self.assertIn('旧顧客JSONの、どの通信を選ぶか', md)
        self.assertNotIn('成功を確認しました', md)
        self.assertNotIn('コマンドを載せていません', md)
        self.assertNotIn('http://', md)
        self.assertNotIn('https://', md)
        self.assertNotIn('fetch(', md)
        self.assertNotIn('curl ', md)
        self.assertNotIn('/api/', md)


if __name__ == '__main__':
    unittest.main()
