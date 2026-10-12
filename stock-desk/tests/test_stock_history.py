"""在庫履歴ページ API / UI ソース検証（temp DB のみ。本番在庫は触らない）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import stock
import stock_history

JST = ZoneInfo('Asia/Tokyo')
ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def seed_masters(c, *, customer='履歴テスト病院', code='HIST-1'):
    cu = stock.save_customer(c, {'name': customer, 'active': True})
    su = stock.save_supplier(c, {'name': '履歴テスト仕入先', 'active': True})
    pr = stock.save_product(c, {
        'code': code, 'name': '履歴スクラブ', 'color': '白',
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '履歴倉庫', 'active': True})
    handle = pr['links'][0]['id']
    return cu, pr, loc, handle


def _set_move_at(c, move_id, at):
    c.execute('UPDATE moves SET at=? WHERE id=?', (at, move_id))


def _last_move_id(c):
    return c.execute('SELECT id FROM moves ORDER BY id DESC LIMIT 1').fetchone()['id']


class StockHistoryApiTest(unittest.TestCase):
    def test_recorded_at_bounds_inclusive_end_jst(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'hist-day-before', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 1000, 'receivedOn': '2026-09-30',
            })
            mid_before = _last_move_id(c)
            _set_move_at(c, mid_before, '2026-09-30T23:59:59')

            stock.register_inbound(c, {
                'token': 'hist-start', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1100, 'receivedOn': '2026-10-01',
            })
            mid_start = _last_move_id(c)
            _set_move_at(c, mid_start, '2026-10-01T00:00:00')

            stock.register_inbound(c, {
                'token': 'hist-end', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 1200, 'receivedOn': '2026-10-31',
            })
            mid_end = _last_move_id(c)
            _set_move_at(c, mid_end, '2026-10-31T23:59:59')

            stock.register_inbound(c, {
                'token': 'hist-after', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 1300, 'receivedOn': '2026-11-01',
            })
            mid_after = _last_move_id(c)
            _set_move_at(c, mid_after, '2026-11-01T00:00:00')
            c.commit()

            data = stock_history.list_history(c, {'from': '2026-10-01', 'to': '2026-10-31', 'pageSize': 50})
            ids = {e['id'] for e in data['events']}
            self.assertIn(mid_start, ids)
            self.assertIn(mid_end, ids)
            self.assertNotIn(mid_before, ids)
            self.assertNotIn(mid_after, ids)
            self.assertEqual(data['periodBasis'], 'recordedAt')
            self.assertIn('記録日時', data['periodBasisLabel'])
            self.assertEqual(data['sort'], 'recordedAtDesc')
            # newest first
            ats = [e['at'] for e in data['events']]
            self.assertEqual(ats, sorted(ats, reverse=True))
        finally:
            c.close()
            folder.cleanup()

    def test_filters_kind_condition_customer_product_location(self):
        folder, path, c = newdb()
        try:
            cu, pr, loc, handle = seed_masters(c)
            loc2 = stock.save_location(c, {'siteName': '別倉庫', 'active': True})
            stock.register_inbound(c, {
                'token': 'filt-in-new', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 1000, 'receivedOn': '2026-10-02',
                'condition': 'new',
            })
            stock.register_inbound(c, {
                'token': 'filt-in-used', 'customerProductId': handle, 'size': 'M',
                'locationId': loc2['id'], 'qty': 2, 'unitCost': 800, 'receivedOn': '2026-10-03',
                'condition': 'used',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 1, 'condition': 'new',
            })
            stock.register_outbound(c, {
                'token': 'filt-out', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'fingerprint': plan['fingerprint'],
                'businessOn': '2026-10-04', 'condition': 'new',
            })
            c.commit()
            # Force all into October window
            for r in c.execute("SELECT id FROM moves"):
                _set_move_at(c, r['id'], '2026-10-10T12:00:00')
            c.commit()

            only_out = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'kind': 'outbound',
            })
            self.assertTrue(only_out['events'])
            self.assertTrue(all(e['kind'] == 'outbound' for e in only_out['events']))

            used = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'condition': 'used',
            })
            self.assertTrue(used['events'])
            self.assertTrue(all(e['condition'] == 'used' for e in used['events']))

            by_loc = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'locationId': str(loc2['id']),
            })
            self.assertTrue(by_loc['events'])
            self.assertTrue(all(e['locationId'] == loc2['id'] for e in by_loc['events']))

            by_cu = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'customerId': str(cu['id']),
            })
            self.assertGreaterEqual(by_cu['total'], 3)
            by_pr = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'productId': str(pr['id']),
            })
            self.assertEqual(by_pr['total'], by_cu['total'])
        finally:
            c.close()
            folder.cleanup()

    def test_qty_and_unit_cost_corrections_and_lot_scope(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'hist-corr-in', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 10, 'unitCost': 1000, 'receivedOn': '2026-10-05',
            })
            lot = c.execute("SELECT id FROM lots WHERE size_label='M'").fetchone()['id']
            # quantity correction
            stock.correct_inbound(c, {
                'lotId': lot, 'qty': 8, 'unitCost': 1000, 'reason': '数え直し',
                'businessOn': '2026-10-06',
            })
            # unit-cost-only correction (qty unchanged)
            stock.correct_inbound(c, {
                'lotId': lot, 'qty': 8, 'unitCost': 900, 'reason': '単価誤り',
                'businessOn': '2026-10-07',
            })
            for r in c.execute('SELECT id FROM moves'):
                _set_move_at(c, r['id'], '2026-10-10T10:00:00')
            c.commit()

            data = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'kind': 'correction',
            })
            kinds = {e['kind'] for e in data['events']}
            self.assertIn('inbound_correction', kinds)
            cost_only = [e for e in data['events'] if e['qtyDelta'] == 0 and e['costBefore'] != e['costAfter']]
            self.assertTrue(cost_only)
            qty_corr = [e for e in data['events'] if e['qtyDelta'] == -2]
            self.assertTrue(qty_corr)
            self.assertEqual(qty_corr[0]['qtyBefore'], 10)
            self.assertEqual(qty_corr[0]['qtyAfter'], 8)
            self.assertIn('対象ロット', data['qtyScopeNote'])
        finally:
            c.close()
            folder.cleanup()

    def test_multi_lot_outbound_batch_peers(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'hist-ml-a', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            stock.register_inbound(c, {
                'token': 'hist-ml-b', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 1200, 'receivedOn': '2026-10-02',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 5,
            })
            self.assertGreaterEqual(len(plan['allocations']), 2)
            out = stock.register_outbound(c, {
                'token': 'hist-ml-out', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'fingerprint': plan['fingerprint'],
                'businessOn': '2026-10-08',
            })
            for r in c.execute('SELECT id FROM moves'):
                _set_move_at(c, r['id'], '2026-10-08T15:00:00')
            c.commit()

            data = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'kind': 'outbound',
            })
            self.assertEqual(len(data['events']), 2)
            for e in data['events']:
                self.assertEqual(e['moveBatchId'], out['batchId'])
                self.assertEqual(e['batchLineCount'], 2)
                self.assertEqual(len(e['batchPeers']), 2)
                self.assertTrue(any(p['isSelf'] for p in e['batchPeers']))
        finally:
            c.close()
            folder.cleanup()

    def test_zero_stock_and_inactive_masters_still_listed(self):
        folder, path, c = newdb()
        try:
            cu, pr, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'hist-zero-in', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 2,
            })
            stock.register_outbound(c, {
                'token': 'hist-zero-out', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'fingerprint': plan['fingerprint'],
                'businessOn': '2026-10-02',
            })
            stock.save_customer(c, {'id': cu['id'], 'name': cu['name'], 'active': False})
            stock.save_product(c, {
                'id': pr['id'], 'code': pr['code'], 'name': pr['name'], 'color': pr['color'],
                'supplierId': pr['supplierId'], 'active': False, 'sizes': pr['sizes'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': False}],
            })
            for r in c.execute('SELECT id FROM moves'):
                _set_move_at(c, r['id'], '2026-10-05T09:00:00')
            c.commit()
            lot_qty = c.execute('SELECT qty_remaining FROM lots').fetchone()['qty_remaining']
            self.assertEqual(lot_qty, 0)

            data = stock_history.list_history(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            self.assertGreaterEqual(data['total'], 2)
            self.assertTrue(any(e['lotQtyRemaining'] == 0 for e in data['events']))
            self.assertTrue(any(e['customerActive'] is False for e in data['events']))
            self.assertTrue(any(e['productActive'] is False for e in data['events']))
            self.assertEqual(data['namesNote'].count('現在'), 1)
        finally:
            c.close()
            folder.cleanup()

    def test_pagination_searches_full_set(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            for i in range(7):
                stock.register_inbound(c, {
                    'token': f'hist-page-{i}', 'customerProductId': handle, 'size': 'M',
                    'locationId': loc['id'], 'qty': 1, 'unitCost': 1000 + i,
                    'receivedOn': f'2026-10-{i+1:02d}',
                })
                _set_move_at(c, _last_move_id(c), f'2026-10-{i+1:02d}T12:00:00')
            c.commit()

            page1 = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'page': 1, 'pageSize': 3,
            })
            page2 = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'page': 2, 'pageSize': 3,
            })
            page3 = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'page': 3, 'pageSize': 3,
            })
            self.assertEqual(page1['total'], 7)
            self.assertEqual(page1['totalPages'], 3)
            self.assertEqual(len(page1['events']), 3)
            self.assertEqual(len(page2['events']), 3)
            self.assertEqual(len(page3['events']), 1)
            ids = [e['id'] for e in page1['events'] + page2['events'] + page3['events']]
            self.assertEqual(len(ids), len(set(ids)))
            # Filter by kind still searches whole DB, not just loaded page
            inbound = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'kind': 'inbound', 'pageSize': 2,
            })
            self.assertEqual(inbound['total'], 7)
        finally:
            c.close()
            folder.cleanup()

    def test_unrecorded_actor_and_business_day_not_invented(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'hist-unrec-in', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            mid = _last_move_id(c)
            c.execute("UPDATE moves SET actor_name='', business_on=NULL, at='2026-10-01T10:00:00' WHERE id=?", (mid,))
            c.commit()
            data = stock_history.list_history(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            row = next(e for e in data['events'] if e['id'] == mid)
            self.assertEqual(row['actorNameLabel'], '未記録')
            # inbound without business_on falls back to lot received_on via resolve_business_on
            # Force both empty for outbound-like missing business day:
            c.execute("UPDATE moves SET kind='outbound', business_on=NULL WHERE id=?", (mid,))
            c.execute('UPDATE lots SET received_on=NULL, received_unknown=1 WHERE id=(SELECT lot_id FROM moves WHERE id=?)', (mid,))
            c.commit()
            data2 = stock_history.list_history(c, {'from': '2026-10-01', 'to': '2026-10-31', 'kind': 'outbound'})
            row2 = next(e for e in data2['events'] if e['id'] == mid)
            self.assertIsNone(row2['businessOn'])
            self.assertEqual(row2['businessOnLabel'], '未記録')
        finally:
            c.close()
            folder.cleanup()

    def test_readonly_http_and_no_mutation(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'hist-ro-http', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            _set_move_at(c, _last_move_id(c), '2026-10-01T11:00:00')
            c.commit()
            before_qty = c.execute('SELECT SUM(qty_remaining) AS n FROM lots').fetchone()['n']
            before_moves = c.execute('SELECT COUNT(*) AS n FROM moves').fetchone()['n']
            st, body = server.api_get(
                str(path), 'live', '/api/stock-history',
                {'from': ['2026-10-01'], 'to': ['2026-10-31']},
                can_write=False,
            )
            self.assertEqual(st, 200)
            self.assertGreaterEqual(body['total'], 1)
            after_qty = c.execute('SELECT SUM(qty_remaining) AS n FROM lots').fetchone()['n']
            after_moves = c.execute('SELECT COUNT(*) AS n FROM moves').fetchone()['n']
            self.assertEqual(before_qty, after_qty)
            self.assertEqual(before_moves, after_moves)
        finally:
            c.close()
            folder.cleanup()

    def test_default_period_is_current_jst_month(self):
        today = date.today()  # list_history uses JST; CI hosts are typically UTC≈same calendar or we compare fields
        folder, path, c = newdb()
        try:
            data = stock_history.list_history(c, {})
            start = date.fromisoformat(data['from'])
            end = date.fromisoformat(data['to'])
            jst_today = stock_history._jst_today()
            self.assertEqual(start, date(jst_today.year, jst_today.month, 1))
            self.assertEqual(end, jst_today)
            self.assertLessEqual(end, jst_today + timedelta(days=1))
            self.assertEqual(today.year, jst_today.year)  # sanity for local CI
        finally:
            c.close()
            folder.cleanup()


class StockHistoryUiSourceTest(unittest.TestCase):
    def test_history_section_and_no_row_history_button(self):
        self.assertIn("'history'", APP_JS[APP_JS.index('STOCK_SECTIONS='):APP_JS.index('STOCK_SECTIONS=') + 120])
        self.assertIn("history:'stock-history'", APP_JS)
        self.assertIn('function historyPage()', APP_JS)
        self.assertIn('/api/stock-history', APP_JS)
        self.assertIn('記録日時（日本時間）', APP_JS)
        self.assertIn('条件を解除', APP_JS)
        self.assertIn('対象ロット', APP_JS)
        # Per-row 履歴 button removed from stock list; 開く / 訂正 remain
        lot_table = APP_JS[APP_JS.index("f.group==='lot'"):APP_JS.index("if(f.group==='product')")]
        self.assertNotIn('data-history', lot_table)
        self.assertNotIn('>履歴</button>', lot_table)
        self.assertIn('data-correct', lot_table)
        self.assertIn("stockOpenBtn(l.id,'開く')", lot_table)
        self.assertIn('stockopen', lot_table)

    def test_history_filters_are_two_top_packed_rows(self):
        """Dates + month shortcuts on row 1; criteria + actions on row 2 (no lonely wrap)."""
        import re
        m = re.search(r'function historyPage\([^)]*\)\{', APP_JS)
        self.assertIsNotNone(m)
        nxt = re.search(r'\nfunction ', APP_JS[m.end():])
        body = APP_JS[m.start():m.end() + nxt.start() if nxt else len(APP_JS)]
        self.assertIn('class="bar history-filters"', body)
        self.assertNotIn('period-filters history-filters', body)
        self.assertIn('history-filter-dates', body)
        self.assertIn('history-filter-criteria', body)
        self.assertEqual(body.count('history-filter-row'), 2)
        for fid in ('hFrom', 'hTo', 'hThisMonth', 'hPrevMonth', 'hKind', 'hLocation', 'hRun', 'hClear'):
            self.assertIn(f'id="{fid}"', body)
        self.assertIn("inputId:'hCustomerQ'", body)
        self.assertIn("inputId:'hProductQ'", body)
        self.assertIn("conditionSelectHtml('hCondition'", body)
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'\.history-filters\{[^}]*flex-direction:\s*column')
        self.assertRegex(css, r'\.history-filter-row\{[^}]*align-items:\s*flex-start')
        self.assertIn('.history-filters .search-picker-hint:empty{display:none}', css)

    def test_compact_list_columns_and_detail_popup(self):
        page = APP_JS[APP_JS.index('function historyPage()'):APP_JS.index('async function loadMoveTargets()')]
        # Compact columns only (業務日・保管場所・記録者などは詳細へ)
        self.assertIn('<th>記録日時</th><th>操作</th><th>顧客</th><th>商品</th><th>仕様</th><th>増減数</th><th>詳細</th>', page)
        self.assertNotIn('<th>業務日</th>', page)
        self.assertNotIn('<th>加工仕様</th>', page)
        self.assertNotIn('<th>保管場所</th>', page)
        self.assertNotIn('<th>記録者</th>', page)
        self.assertNotIn('<th>担当者</th>', page)
        self.assertNotIn('<th>備考</th>', page)
        self.assertNotIn('<th>ロット数量（前→後）</th>', page)
        self.assertIn('historyShortAt(e.at)', page)
        self.assertIn('historySpecText(e)', page)
        self.assertIn('historyDeltaText(e)', page)
        self.assertIn('>開く</button>', page)
        self.assertIn('history-detail-dialog', page)
        self.assertIn('historyDetailHtml(e)', page)
        # Product code / name stacked
        self.assertIn('hist-product', page)
        self.assertIn('esc(e.code)', page)
        self.assertIn('esc(e.productName)', page)

    def test_history_display_formatters(self):
        self.assertIn('function historyShortAt(at)', APP_JS)
        self.assertIn('function historyFullAt(at)', APP_JS)
        self.assertIn('function historyDeltaText(e)', APP_JS)
        self.assertIn('function historySpecText(e)', APP_JS)
        delta = APP_JS[APP_JS.index('function historyDeltaText(e)'):APP_JS.index('function historySpecText(e)')]
        self.assertIn("return '0枚'", delta)
        self.assertIn("return '未記録'", delta)
        self.assertIn("'＋'+num(d)", delta)
        self.assertIn("'−'+num(Math.abs(d))", delta)
        short = APP_JS[APP_JS.index('function historyShortAt(at)'):APP_JS.index('function historyFullAt(at)')]
        self.assertIn('Number(m[2])}/${Number(m[3])} ${m[4]}:${m[5]}', short)
        detail = APP_JS[APP_JS.index('function historyDetailHtml(e)'):APP_JS.index('function historyPage()')]
        for label in (
            '記録日時', '業務日', '加工仕様', '保管場所',
            '対象ロットの数量（変更前→変更後）',
            '単価（変更前→変更後）', '金額（変更前→変更後）',
            '記録者', '備考', '対象ロットID', '同一出庫のロット内訳',
        ):
            self.assertIn(label, detail)
        self.assertIn('<dt>記録者</dt>', detail)
        self.assertNotIn('<dt>担当者</dt>', detail)
        self.assertIn("actorNameLabel||'未記録'", detail)
        self.assertIn('historyFullAt(e.at)', detail)
        # 期間内訳も同じ操作者表示は「記録者」。顧客・仕入先の連絡担当者ラベルは触らない。
        period = APP_JS[APP_JS.index('function showPeriodHistory('):APP_JS.index('function movePage()')]
        self.assertIn('<th>記録者</th>', period)
        self.assertNotIn('<th>担当者</th>', period)
        self.assertIn("actorName||'未記録'", period)
        self.assertIn("field('contact','担当者'", APP_JS)

    def test_history_table_css_allows_wrap_not_shrink(self):
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('.history-table{', css)
        self.assertIn('word-break:break-word', css)
        self.assertIn('dialog.history-detail-dialog', css)
        self.assertIn('overflow:auto', css[css.index('dialog.history-detail-dialog'):])

    def test_server_exposes_stock_history_route(self):
        server_py = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("/api/stock-history", server_py)
        self.assertIn('stock_history.list_history', server_py)


class StockHistoryDisplayLogicTest(unittest.TestCase):
    """Mirror key UI formatting rules so 0枚 vs 未記録 cannot silently drift."""

    @staticmethod
    def short_at(at):
        import re
        m = re.match(r'^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})', str(at or ''))
        if not m:
            return str(at) if at else '未記録'
        return f'{int(m.group(2))}/{int(m.group(3))} {m.group(4)}:{m.group(5)}'

    @staticmethod
    def delta_text(qty_delta):
        if qty_delta is None or qty_delta == '':
            return '未記録'
        try:
            d = float(qty_delta)
        except (TypeError, ValueError):
            return '未記録'
        if d != d:  # NaN
            return '未記録'
        if d == 0:
            return '0枚'
        if d > 0:
            return '＋' + f'{d:g}'
        return '−' + f'{abs(d):g}'

    def test_short_datetime_omits_year(self):
        self.assertEqual(self.short_at('2026-10-13T14:30:00'), '10/13 14:30')
        self.assertEqual(self.short_at('2026-01-05 09:05:11'), '1/5 09:05')
        self.assertEqual(self.short_at(''), '未記録')

    def test_delta_zero_vs_unrecorded(self):
        self.assertEqual(self.delta_text(0), '0枚')
        self.assertEqual(self.delta_text(10), '＋10')
        self.assertEqual(self.delta_text(-5), '−5')
        self.assertEqual(self.delta_text(None), '未記録')
        self.assertEqual(self.delta_text(''), '未記録')

    def test_api_cost_only_correction_has_zero_delta_for_ui(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'hist-ui-cost', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 8, 'unitCost': 1000, 'receivedOn': '2026-10-05',
            })
            lot = c.execute("SELECT id FROM lots WHERE size_label='M'").fetchone()['id']
            stock.correct_inbound(c, {
                'lotId': lot, 'qty': 8, 'unitCost': 900, 'reason': '単価のみ',
                'businessOn': '2026-10-07',
            })
            for r in c.execute('SELECT id FROM moves'):
                _set_move_at(c, r['id'], '2026-10-10T14:30:00')
            c.commit()
            data = stock_history.list_history(c, {
                'from': '2026-10-01', 'to': '2026-10-31', 'kind': 'correction',
            })
            cost_only = [e for e in data['events'] if e['qtyDelta'] == 0]
            self.assertTrue(cost_only)
            e = cost_only[0]
            self.assertEqual(self.delta_text(e['qtyDelta']), '0枚')
            self.assertEqual(self.short_at(e['at']), '10/10 14:30')
            self.assertIn(e['at'][:4], e['at'])  # full year still on API payload for detail
            self.assertIsNotNone(e['businessOn'])
            self.assertTrue(e['actorNameLabel'])
            self.assertIn('対象ロット', data['qtyScopeNote'])
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
