"""ログイン利用者は sqlite3.Row（.get がない）。権限チェックで .get を使うと接続が切れる。"""
import re
import sqlite3
import unittest
from pathlib import Path

import kintai

ROOT = Path(__file__).resolve().parents[1]
BAD = re.compile(r"\buser\.get\('(manage|overview|mark_kintai|role)'\)")


def row(**kw):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    cols = ','.join(f'? AS {k}' for k in kw)
    return c.execute(f'SELECT {cols}', tuple(kw.values())).fetchone()


class UserFlagRowAccessTest(unittest.TestCase):
    def test_no_get_on_login_user_flags(self):
        for name in ('server.py', 'kintai.py'):
            hits = BAD.findall((ROOT / name).read_text(encoding='utf-8'))
            self.assertEqual(hits, [], name)

    def test_list_share_files_accepts_row_manager(self):
        u = row(role='sales', manage=1, overview=1, mark_kintai=0)
        try:
            kintai.list_share_files_for_user(u)
        except AttributeError as e:
            self.fail(f'Row で落ちる: {e}')
        except Exception:
            pass  # 共有フォルダ未設定などはここでは対象外
        self.assertEqual(kintai.list_share_files_for_user(row(role='sales', manage=0))[1], 403)


if __name__ == '__main__':
    unittest.main()
