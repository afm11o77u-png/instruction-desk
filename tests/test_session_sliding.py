"""セッションの操作中延長（メモリ DB のみ）。"""
import sqlite3, sys, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth  # noqa: E402


class H(auth.AuthMixin):
    def __init__(self, cookie=''):
        self.headers = {'Cookie': cookie}
        self.cookie = None


def db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, x TEXT)')
    auth.schema(c)
    c.execute("INSERT INTO users(login,name,role,password,active) VALUES('a','A','owner','x',1)")
    return c


class SlidingSessionTest(unittest.TestCase):
    def login(self, c):
        h = H()
        h.new_session(c, 1)
        return h.cookie[0].split(';')[0]

    def test_idle_timeout_value(self):
        self.assertEqual(auth.SESSION_IDLE_SECONDS, 12 * 3600)
        self.assertIn('Max-Age=43200', auth.session_cookie('t'))

    def test_activity_extends_session(self):
        c = db()
        ck = self.login(c)
        t0 = time.time()
        with mock.patch.object(auth.time, 'time', return_value=t0 + 11 * 3600):
            h = H(ck)
            self.assertIsNotNone(h.session_user(c))
            self.assertIn('desk_session=', h.cookie)
        exp = c.execute('SELECT expires FROM sessions').fetchone()[0]
        self.assertGreater(exp, t0 + 22 * 3600)
        # 延長後は元の期限を過ぎても有効
        with mock.patch.object(auth.time, 'time', return_value=t0 + 13 * 3600):
            self.assertIsNotNone(H(ck).session_user(c))

    def test_idle_expires(self):
        c = db()
        ck = self.login(c)
        with mock.patch.object(auth.time, 'time', return_value=time.time() + 12 * 3600 + 5):
            self.assertIsNone(H(ck).session_user(c))

    def test_no_write_on_every_request(self):
        c = db()
        ck = self.login(c)
        h = H(ck)
        self.assertIsNotNone(h.session_user(c))
        self.assertIsNone(h.cookie)


class InventoryPageRedirectTest(unittest.TestCase):
    def test_page_redirects_not_json(self):
        src = (Path(__file__).resolve().parents[1] / 'server.py').read_text(encoding='utf-8')
        self.assertIn("{'Location': '/'}", src)


if __name__ == '__main__':
    unittest.main()
