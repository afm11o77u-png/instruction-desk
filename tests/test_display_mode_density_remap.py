"""Density remap: key migration once + mode list / defaults."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import display_modes

ROOT = Path(__file__).resolve().parents[1]
DM_JS = (ROOT / 'display-mode.js').read_text(encoding='utf-8')
STOCK_DM_JS = (ROOT / 'stock-desk' / 'static' / 'display-mode.js').read_text(encoding='utf-8')
DM_CSS = (ROOT / 'display-mode.css').read_text(encoding='utf-8')
STOCK_DM_CSS = (ROOT / 'stock-desk' / 'static' / 'display-mode.css').read_text(encoding='utf-8')


class FakeHandler:
    def __init__(self, user):
        self._user = user

    def session_user(self, c):
        return self._user


def _connect():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    display_modes.initialize(c)
    c.execute(
        'CREATE TABLE users (id INTEGER PRIMARY KEY, must_change INTEGER NOT NULL DEFAULT 0)'
    )
    c.execute('INSERT INTO users(id, must_change) VALUES(1, 0)')
    c.commit()
    return c


class DensityRemapMapTest(unittest.TestCase):
    def test_legacy_key_map(self):
        self.assertEqual(display_modes.remap_legacy_mode('standard'), 'cozy')
        self.assertEqual(display_modes.remap_legacy_mode('compact'), 'standard')
        self.assertEqual(display_modes.remap_legacy_mode('cozy'), 'cozy')
        self.assertEqual(display_modes.remap_legacy_mode('grid'), 'grid')

    def test_mode_set_and_default(self):
        self.assertEqual(display_modes.MODES, ('cozy', 'standard', 'compact', 'grid'))
        self.assertEqual(display_modes.DEFAULT, 'standard')
        self.assertEqual(display_modes.default_for('orders'), 'grid')
        self.assertEqual(display_modes.default_for('shipinfo'), 'standard')
        self.assertEqual(display_modes.default_for('stock-list'), 'standard')
        self.assertEqual(display_modes.default_for('stock-opening'), 'standard')
        self.assertEqual(display_modes.default_for('stock-other'), 'standard')

    def test_js_mode_labels_and_order(self):
        for src in (DM_JS, STOCK_DM_JS):
            self.assertIn("id: 'cozy', label: 'ゆったり'", src)
            self.assertIn("id: 'standard', label: '標準'", src)
            self.assertIn("id: 'compact', label: 'コンパクト'", src)
            self.assertIn("id: 'grid', label: 'グリッド'", src)
            cozy = src.index("id: 'cozy', label: 'ゆったり'")
            standard = src.index("id: 'standard', label: '標準'")
            compact = src.index("id: 'compact', label: 'コンパクト'")
            grid = src.index("id: 'grid', label: 'グリッド'")
            self.assertLess(cozy, standard)
            self.assertLess(standard, compact)
            self.assertLess(compact, grid)
            self.assertIn("standard: 'cozy'", src)
            self.assertIn("compact: 'standard'", src)
            self.assertIn("'stock-list': 'standard'", src)
            self.assertIn("'stock-opening': 'standard'", src)

    def test_css_density_roles(self):
        for css in (DM_CSS, STOCK_DM_CSS):
            # ゆったり = former 標準 padding
            self.assertIn('.list-stage.dm-cozy table td{padding:10px 12px', css)
            # 標準 = former コンパクト padding
            self.assertIn('.list-stage.dm-standard table td,', css)
            self.assertIn('padding:4px 8px;font-size:.875rem', css)
            # New denser コンパクト
            self.assertIn('コンパクト: new denser list', css)
            self.assertIn('.list-stage.dm-compact{font-size:12px', css)
            self.assertIn('padding:3px 6px', css)
            self.assertIn('.list-stage.dm-compact .money', css)
            # Former oversized cozy look must be gone
            self.assertNotIn('font-size:1.12rem', css)


class DensityRemapPersistTest(unittest.TestCase):
    def test_get_migrates_once_and_preserves_new_choice(self):
        c = _connect()
        # Pre-remap saved prefs (density_v defaults to 0)
        c.execute(
            'INSERT INTO user_display_modes(user_id, screen_id, mode) VALUES(?,?,?)',
            (1, 'shipinfo', 'standard'),
        )
        c.execute(
            'INSERT INTO user_display_modes(user_id, screen_id, mode) VALUES(?,?,?)',
            (1, 'stock-list', 'compact'),
        )
        c.execute(
            'INSERT INTO user_display_modes(user_id, screen_id, mode) VALUES(?,?,?)',
            (1, 'orders', 'cozy'),
        )
        c.execute(
            'INSERT INTO user_display_modes(user_id, screen_id, mode) VALUES(?,?,?)',
            (1, 'floor', 'grid'),
        )
        c.commit()

        status, body = display_modes.handle_get(FakeHandler({'id': 1, 'must_change': 0}), c)
        self.assertEqual(status, 200)
        self.assertEqual(body['modes']['shipinfo'], 'cozy')       # old 標準 → ゆったり
        self.assertEqual(body['modes']['stock-list'], 'standard')  # old コンパクト → 標準
        self.assertEqual(body['modes']['orders'], 'cozy')          # old ゆったり → ゆったり
        self.assertEqual(body['modes']['floor'], 'grid')
        self.assertEqual(body['densityRemapVersion'], 1)

        rows = {
            r['screen_id']: (r['mode'], r['density_v'])
            for r in c.execute(
                'SELECT screen_id, mode, density_v FROM user_display_modes WHERE user_id=1'
            )
        }
        self.assertEqual(rows['shipinfo'], ('cozy', 1))
        self.assertEqual(rows['stock-list'], ('standard', 1))
        self.assertEqual(rows['orders'], ('cozy', 1))
        self.assertEqual(rows['floor'], ('grid', 1))

        # User picks new 標準 after migration — must not be remapped on reload
        status, body = display_modes.handle_post(
            FakeHandler({'id': 1, 'must_change': 0}),
            c,
            {'screenId': 'shipinfo', 'mode': 'standard'},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body['mode'], 'standard')

        status, body = display_modes.handle_get(FakeHandler({'id': 1, 'must_change': 0}), c)
        self.assertEqual(status, 200)
        self.assertEqual(body['modes']['shipinfo'], 'standard')
        row = c.execute(
            "SELECT mode, density_v FROM user_display_modes WHERE user_id=1 AND screen_id='shipinfo'"
        ).fetchone()
        self.assertEqual(row['mode'], 'standard')
        self.assertEqual(row['density_v'], 1)

    def test_post_writes_density_v(self):
        c = _connect()
        status, body = display_modes.handle_post(
            FakeHandler({'id': 1, 'must_change': 0}),
            c,
            {'screenId': 'drafts', 'mode': 'compact'},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body['mode'], 'compact')
        row = c.execute(
            "SELECT mode, density_v FROM user_display_modes WHERE user_id=1 AND screen_id='drafts'"
        ).fetchone()
        self.assertEqual(tuple(row), ('compact', 1))

    def test_initialize_adds_density_v_on_legacy_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 't.sqlite3'
            c = sqlite3.connect(path)
            c.execute(
                'CREATE TABLE user_display_modes ('
                'user_id INTEGER NOT NULL, screen_id TEXT NOT NULL, mode TEXT NOT NULL, '
                'PRIMARY KEY (user_id, screen_id))'
            )
            c.execute(
                "INSERT INTO user_display_modes VALUES (1, 'shipinfo', 'compact')"
            )
            c.commit()
            c.close()

            c = sqlite3.connect(path)
            c.row_factory = sqlite3.Row
            display_modes.initialize(c)
            cols = {r[1] for r in c.execute('PRAGMA table_info(user_display_modes)')}
            self.assertIn('density_v', cols)
            display_modes.migrate_user_modes(c, 1)
            row = c.execute(
                "SELECT mode, density_v FROM user_display_modes WHERE screen_id='shipinfo'"
            ).fetchone()
            self.assertEqual(row['mode'], 'standard')
            self.assertEqual(row['density_v'], 1)
            c.close()


if __name__ == '__main__':
    unittest.main()
