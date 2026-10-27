"""月別備品棚卸: 計算・空≠0・繰越・月隔離・同名別単価・シフト/出勤簿/在庫非影響。"""
from __future__ import annotations

import inspect
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import kintai
import kintai_bihin
import kintai_shift

ROOT = Path(__file__).resolve().parents[1]


class BihinMathTest(unittest.TestCase):
    def test_money_round_half_up(self):
        # 1.5 → 2, 2.5 → 3（half up）
        self.assertEqual(kintai_bihin.money_yen(Decimal('1'), Decimal('1.5')), 2)
        self.assertEqual(kintai_bihin.money_yen(Decimal('1'), Decimal('2.5')), 3)
        self.assertEqual(kintai_bihin.money_yen(Decimal('3'), Decimal('10.333')), 31)
        self.assertEqual(kintai_bihin.money_yen(Decimal('0.1'), Decimal('10')), 1)
        self.assertIsNone(kintai_bihin.money_yen(None, Decimal('100')))

    def test_usage_requires_all_three(self):
        self.assertIsNone(kintai_bihin.usage_qty(Decimal('10'), None, Decimal('5')))
        self.assertIsNone(kintai_bihin.usage_qty(None, Decimal('0'), Decimal('0')))
        self.assertIsNone(kintai_bihin.usage_qty(Decimal('1'), Decimal('1'), None))
        # 0 は入力済み
        self.assertEqual(
            kintai_bihin.usage_qty(Decimal('10'), Decimal('0'), Decimal('3')),
            Decimal('7'),
        )

    def test_empty_vs_zero_dec(self):
        self.assertIsNone(kintai_bihin._qty('', field='数量'))
        self.assertIsNone(kintai_bihin._qty(None, field='数量'))
        self.assertEqual(kintai_bihin._qty(0, field='数量'), Decimal('0'))
        self.assertEqual(kintai_bihin._qty('0', field='数量'), Decimal('0'))
        with self.assertRaises(ValueError):
            kintai_bihin._qty(-1, field='数量')


class BihinStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 't.sqlite3'
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, "
            "version INTEGER, mark_kintai INTEGER DEFAULT 0)"
        )
        kintai.bind(Path(self.tmp.name))
        kintai.schema(self.c)
        self.c.execute('DELETE FROM kintai_people')
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,1,0)',
            ('001', 'regular', '山田太郎', ''),
        )
        self.c.commit()
        self.user = {'id': 1, 'name': '主', 'login': 'o@ex.com', 'role': 'owner', 'mark_kintai': 1}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _add_item(self, name, price, category='消耗', unit='個', **extra):
        return kintai_bihin.save_item(self.c, self.user, {
            'category': category, 'name': name, 'unit': unit, 'unitPrice': price, **extra,
        })

    def test_empty_master_unsaved_month(self):
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertFalse(sheet['exists'])
        self.assertEqual(sheet['version'], 0)
        self.assertEqual(sheet['rows'], [])
        codes = {n['code'] for n in sheet['notices']}
        self.assertIn('empty_master', codes)

    def test_same_name_different_price_separate_ids(self):
        a = self._add_item('コピー用紙', 500)
        b = self._add_item('コピー用紙', 480)
        self.assertNotEqual(a['id'], b['id'])
        items = kintai_bihin.list_items(self.c)
        self.assertEqual(len(items), 2)
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(len(sheet['rows']), 2)
        names = [r['name'] for r in sheet['rows']]
        self.assertEqual(names, ['コピー用紙', 'コピー用紙'])
        prices = sorted(r['unitPrice'] for r in sheet['rows'])
        self.assertEqual(prices, [480, 500])

    def test_save_restore_and_math(self):
        it = self._add_item('ボールペン', 120, unit='本')
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        row = dict(sheet['rows'][0])
        row['openingQty'] = 10
        row['purchaseQty'] = 5
        row['closingQty'] = 7
        code, saved = kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 0, 'rows': [row],
        })
        self.assertEqual(code, 200)
        self.assertEqual(saved['version'], 1)
        r = saved['rows'][0]
        self.assertEqual(r['usageQty'], 8)  # 10+5-7
        self.assertEqual(r['openingAmount'], 1200)
        self.assertEqual(r['purchaseAmount'], 600)
        self.assertEqual(r['closingAmount'], 840)
        self.assertEqual(r['usageAmount'], 960)
        self.assertFalse(r['usageIncomplete'])
        again = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(again['version'], 1)
        self.assertEqual(again['rows'][0]['usageQty'], 8)
        self.assertEqual(again['rows'][0]['itemId'], it['id'])

    def test_incomplete_usage_when_qty_missing(self):
        self._add_item('ノート', 200)
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        row = dict(sheet['rows'][0])
        row['openingQty'] = 3
        row['purchaseQty'] = None  # 空
        row['closingQty'] = 1
        code, saved = kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 0, 'rows': [row],
        })
        self.assertEqual(code, 200)
        r = saved['rows'][0]
        self.assertTrue(r['usageIncomplete'])
        self.assertIsNone(r['usageQty'])
        self.assertIsNone(r['usageAmount'])
        # 0 は確定
        row2 = dict(r)
        row2['purchaseQty'] = 0
        code2, saved2 = kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 1, 'rows': [row2],
        })
        self.assertEqual(code2, 200)
        self.assertEqual(saved2['rows'][0]['usageQty'], 2)

    def test_reject_negative_input_and_negative_usage(self):
        self._add_item('テープ', 100)
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        row = dict(sheet['rows'][0])
        row['openingQty'] = -1
        with self.assertRaises(ValueError):
            kintai_bihin.save_sheet(self.c, self.user, {
                'year': 2026, 'month': 10, 'version': 0, 'rows': [row],
            })
        row['openingQty'] = 1
        row['purchaseQty'] = 0
        row['closingQty'] = 5  # 使用 = -4
        with self.assertRaises(ValueError) as ctx:
            kintai_bihin.save_sheet(self.c, self.user, {
                'year': 2026, 'month': 10, 'version': 0, 'rows': [row],
            })
        self.assertIn('負', str(ctx.exception))

    def test_carry_in_from_previous_month(self):
        it = self._add_item('ファイル', 80)
        sheet = kintai_bihin.get_sheet(self.c, 2026, 9)
        row = dict(sheet['rows'][0])
        row['openingQty'] = 2
        row['purchaseQty'] = 3
        row['closingQty'] = 4
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 9, 'version': 0, 'rows': [row],
        })
        nxt = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertFalse(nxt['exists'])
        self.assertEqual(nxt['rows'][0]['openingQty'], 4)
        self.assertIsNone(nxt['rows'][0]['purchaseQty'])
        self.assertIsNone(nxt['rows'][0]['closingQty'])
        self.assertEqual(nxt['rows'][0]['itemId'], it['id'])

    def test_no_previous_month_opening_manual(self):
        self._add_item('クリップ', 50)
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertIsNone(sheet['rows'][0]['openingQty'])
        codes = {n['code'] for n in sheet['notices']}
        self.assertIn('no_previous_month', codes)

    def test_saved_month_isolation_from_prev_edit(self):
        self._add_item('ペン', 100)
        # 9月保存
        s9 = kintai_bihin.get_sheet(self.c, 2026, 9)
        r9 = dict(s9['rows'][0])
        r9.update({'openingQty': 10, 'purchaseQty': 0, 'closingQty': 8})
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 9, 'version': 0, 'rows': [r9],
        })
        # 10月を繰越で開いて保存（月初=8）
        s10 = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(s10['rows'][0]['openingQty'], 8)
        r10 = dict(s10['rows'][0])
        r10.update({'purchaseQty': 1, 'closingQty': 5})
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 0, 'rows': [r10],
        })
        # 9月の月末を後から変更
        again9 = kintai_bihin.get_sheet(self.c, 2026, 9)
        r9b = dict(again9['rows'][0])
        r9b['closingQty'] = 3
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 9, 'version': again9['version'], 'rows': [r9b],
        })
        # 10月はスナップショットのまま（自動追従しない）
        again10 = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(again10['rows'][0]['openingQty'], 8)
        self.assertTrue(again10['carryMismatches'])
        self.assertEqual(again10['carryMismatches'][0]['previousClosingQty'], 3)
        self.assertEqual(again10['carryMismatches'][0]['openingQty'], 8)

    def test_master_rename_does_not_rewrite_saved_month(self):
        it = self._add_item('旧名称', 200)
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        row = dict(sheet['rows'][0])
        row.update({'openingQty': 1, 'purchaseQty': 0, 'closingQty': 1})
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 0, 'rows': [row],
        })
        kintai_bihin.save_item(self.c, self.user, {
            'id': it['id'], 'category': '消耗', 'name': '新名称', 'unit': '個', 'unitPrice': 999,
        })
        saved = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(saved['rows'][0]['name'], '旧名称')
        self.assertEqual(saved['rows'][0]['unitPrice'], 200)
        # 未作成月は新マスタ
        fresh = kintai_bihin.get_sheet(self.c, 2026, 11)
        self.assertFalse(fresh['exists'])
        self.assertEqual(fresh['rows'][0]['name'], '新名称')
        self.assertEqual(fresh['rows'][0]['unitPrice'], 999)

    def test_conflict_optimistic_lock(self):
        self._add_item('消しゴム', 60)
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        row = dict(sheet['rows'][0])
        row.update({'openingQty': 1, 'purchaseQty': 0, 'closingQty': 1})
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 0, 'rows': [row],
        })
        stale = dict(row)
        stale['closingQty'] = 0
        code, body = kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 0, 'rows': [stale],
        })
        self.assertEqual(code, 409)
        self.assertTrue(body.get('conflict'))
        cur = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual(cur['rows'][0]['closingQty'], 1)

    def test_amount_totals_not_qty(self):
        self._add_item('A', 100, unit='個')
        self._add_item('B', 200, unit='箱')
        sheet = kintai_bihin.get_sheet(self.c, 2026, 10)
        rows = []
        for r in sheet['rows']:
            q = dict(r)
            q.update({'openingQty': 2, 'purchaseQty': 0, 'closingQty': 1})
            rows.append(q)
        code, saved = kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 10, 'version': 0, 'rows': rows,
        })
        self.assertEqual(code, 200)
        self.assertEqual(saved['totals']['openingAmount'], 2 * 100 + 2 * 200)
        self.assertEqual(saved['totals']['usageAmount'], 1 * 100 + 1 * 200)
        self.assertNotIn('openingQty', saved['totals'])
        self.assertIn('単位が異なるため合計しません', saved['totals']['note'])

    def test_does_not_touch_shift_or_attendance(self):
        # シフトを先に保存
        shift = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': shift['people'],
            'cells': {'regular:001': {'1': {'kind': 'off', 'text': '×'}}},
        })
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES (1,2026,11,20,8,1,0,0,'','2026-01-01T00:00:00','取込済','')'''
        )
        self.c.commit()
        self._add_item('マーカー', 90)
        bihin = kintai_bihin.get_sheet(self.c, 2026, 11)
        row = dict(bihin['rows'][0])
        row.update({'openingQty': 1, 'purchaseQty': 0, 'closingQty': 1})
        kintai_bihin.save_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0, 'rows': [row],
        })
        # シフト・出勤簿そのまま
        again_shift = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual(again_shift['cells']['regular:001']['1']['kind'], 'off')
        monthly = self.c.execute(
            'SELECT work_days,status FROM kintai_monthly WHERE person_id=1 AND year=2026 AND month=11'
        ).fetchone()
        self.assertEqual(monthly['work_days'], 20)
        self.assertEqual(monthly['status'], '取込済')
        # シフト表 DDL を壊していない
        tables = {r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('kintai_shift_sheets', tables)
        self.assertIn('kintai_bihin_sheets', tables)
        self.assertIn('kintai_bihin_items', tables)

    def test_schema_additive_no_wipe_shift(self):
        src = inspect.getsource(kintai_bihin.schema)
        self.assertIn('CREATE TABLE IF NOT EXISTS kintai_bihin_items', src)
        self.assertIn('CREATE TABLE IF NOT EXISTS kintai_bihin_sheets', src)
        self.assertNotIn('DROP TABLE', src)
        self.assertNotIn('kintai_shift_sheets', src)
        # kintai.schema が bihin を呼ぶ
        ksrc = inspect.getsource(kintai.schema)
        self.assertIn('kintai_bihin.schema', ksrc)


class BihinUiContractTest(unittest.TestCase):
    def test_nav_and_panel(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('data-kintai-section="bihin"', index)
        self.assertIn('>備品棚卸<', index)
        self.assertIn('id="kintaibihin"', index)
        self.assertIn('id="bihingrid"', index)
        self.assertIn('id="bihinsave"', index)
        self.assertIn('id="bihinsavedialog"', index)
        self.assertIn('id="bihinmasterdialog"', index)
        self.assertIn('保存して移動', index)
        self.assertIn('破棄して移動', index)
        self.assertIn('kintai-bihin.js', index)
        # 既存ナビ維持
        self.assertIn('data-kintai-section="attendance"', index)
        self.assertIn('data-kintai-section="shift"', index)
        self.assertIn('id="kintaiattendance"', index)
        self.assertIn('id="kintaishift"', index)

    def test_scripts_and_routes(self):
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/kintai-bihin.js':'kintai-bihin.js'", server)
        self.assertIn("/api/kintai/bihin", server)
        self.assertIn('備品棚卸を保存できませんでした', server)
        place = (ROOT / 'placement.js').read_text(encoding='utf-8')
        self.assertIn("section==='bihin'", place)
        self.assertIn('confirmKintaiBihinLeave', place)
        self.assertIn('loadKintaiBihin', place)
        self.assertIn('kintaibihin', place)
        js = (ROOT / 'kintai-bihin.js').read_text(encoding='utf-8')
        self.assertIn('confirmKintaiBihinLeave', js)
        self.assertIn('未保存の変更があります', js)
        self.assertIn('beforeunload', js)
        self.assertIn('/api/kintai/bihin', js)
        self.assertIn('moneyYen', js)
        self.assertNotIn('scheduleSave', js)
        # 在庫デスク API を呼ばない
        self.assertNotIn('/inventory/', js)
        self.assertNotIn('/api/stock', js)
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('.bihin-input', css)
        self.assertIn('.bihin-calc', css)
        self.assertIn('.bihin-grid', css)

    def test_desk_nav_groups_mentions_bihin(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        aside = index[index.index('<aside>'):index.index('</aside>')]
        kintai = aside[aside.index('id="kintainav"'):]
        self.assertIn('data-kintai-section="bihin"', kintai)
        self.assertIn('>備品棚卸<', kintai)

    def test_no_stock_api_in_backend(self):
        src = (ROOT / 'kintai_bihin.py').read_text(encoding='utf-8')
        self.assertNotIn('stock-desk', src)
        self.assertNotIn('import stock', src)
        self.assertNotIn('/api/stock', src)
        # モジュール docstring で非連動を明記
        self.assertIn('入庫・出庫とは独立', src)


class BihinJsMathSmokeTest(unittest.TestCase):
    """フロントの四捨五入・空≠0ロジックを Node 無しでソース契約として確認。"""

    def test_js_exports_math_helpers(self):
        js = (ROOT / 'kintai-bihin.js').read_text(encoding='utf-8')
        self.assertIn('function moneyYen', js)
        self.assertIn('Math.floor(Math.abs(raw) + 0.5)', js)
        self.assertIn('function usageQty', js)
        self.assertIn("opening === ''", js)
        self.assertIn('_bihinMath', js)


if __name__ == '__main__':
    unittest.main()
