"""宛名記憶の段数パターン保存と、旧形式ペイロード互換。"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import label_sets


def _connect(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _user(role='floor'):
    return {'id': 1, 'name': '現場太郎', 'login': 'floor@example.co.jp', 'role': role, 'must_change': 0}


def _handler(user=None):
    u = user or _user()
    return SimpleNamespace(session_user=lambda c: u)


class LabelSetsPatternTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='label-sets-')
        self.db = Path(self._tmp.name) / 'instructions.sqlite3'
        self.c = _connect(self.db)
        self.c.execute(
            'CREATE TABLE audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor_id INTEGER, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL)'
        )
        label_sets.initialize(self.c)
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self._tmp.cleanup()

    def _slot(self, **overrides):
        base = {
            'customer': 'サザエ食品',
            'management': 'M-1',
            'department': '事務所',
            'employee': '波平',
            'request': '消耗・破損交換',
        }
        base.update(overrides)
        return base

    def test_save_and_list_keep_three_line_pattern(self):
        status, saved = label_sets.handle(
            _handler(),
            self.c,
            '/api/label-sets/save',
            {'name': '交換3段', 'pattern': '3', 'slots': [self._slot(), {}, {}, {}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved['payload']['pattern'], '3')
        self.assertEqual(saved['payload']['slots'][0]['management'], 'M-1')
        self.c.commit()

        status, rows = label_sets.handle(_handler(), self.c, '/api/label-sets', None)
        self.assertEqual(status, 200)
        item = next(r for r in rows if r['name'] == '交換3段')
        self.assertEqual(item['payload']['pattern'], '3')
        self.assertEqual(item['payload']['slots'][0]['department'], '事務所')

    def test_legacy_array_payload_is_four_line(self):
        self.c.execute(
            'INSERT INTO label_sets(user_id,name,version,payload) VALUES(1,?,?,?)',
            (
                '旧形式',
                1,
                json.dumps([self._slot(customer='旧顧客'), None, None, None], ensure_ascii=False),
            ),
        )
        self.c.commit()
        status, rows = label_sets.handle(_handler(), self.c, '/api/label-sets', None)
        self.assertEqual(status, 200)
        item = next(r for r in rows if r['name'] == '旧形式')
        self.assertEqual(item['payload']['pattern'], '4')
        self.assertEqual(item['payload']['slots'][0]['customer'], '旧顧客')

    def test_invalid_pattern_falls_back_to_four(self):
        status, saved = label_sets.handle(
            _handler(),
            self.c,
            '/api/label-sets/save',
            {'name': '四段', 'pattern': '9', 'slots': [self._slot(employee='マスオ'), {}, {}, {}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved['payload']['pattern'], '4')

    def test_save_keeps_request_hide_sentinel(self):
        status, saved = label_sets.handle(
            _handler(),
            self.c,
            '/api/label-sets/save',
            {
                'name': '非表示混在',
                'pattern': '4',
                'slots': [
                    self._slot(request='表示なし'),
                    self._slot(customer='他社', employee='花子', request='新規'),
                    {},
                    {},
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved['payload']['slots'][0]['request'], '表示なし')
        self.assertEqual(saved['payload']['slots'][0]['customer'], 'サザエ食品')
        self.assertEqual(saved['payload']['slots'][1]['request'], '新規')


class LabelDeskAssetsTest(unittest.TestCase):
    def test_sheet_js_has_three_line_and_notice_hooks(self):
        src = (ROOT / 'sheet.js').read_text(encoding='utf-8')
        for needle in (
            'currentLabelPattern',
            'taglines3',
            'deliveryNoticePage',
            'normalizeLabelPayload',
            'formatNoticeDate',
            'queueNoticePrint',
            "queuePrint(list,true,true,true,false,currentLabelPattern())",
            'pattern:currentLabelPattern()',
        ):
            self.assertIn(needle, src)

    def test_index_has_pattern_and_notice_panels(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        for needle in (
            'labelmodeaddress',
            'labelmodenotice',
            'name="labelpattern"',
            'labelnoticepanel',
            'id="noticeto"',
            'id="noticedate"',
            'id="noticeplace"',
            'id="noticeprint"',
            'class="labelcol-mid"',
            'value="サザエ食品"',
            'value="宮の森事務所"',
            '表示なし',
        ):
            self.assertIn(needle, html)
        self.assertNotIn('labelrequestshowbox', html)
        self.assertNotIn('name="labelrequestshow"', html)

    def test_sheet_js_hides_request_row_per_slot(self):
        src = (ROOT / 'sheet.js').read_text(encoding='utf-8')
        self.assertNotIn('currentLabelShowRequest', src)
        self.assertIn("tag.classList.add('taghidejob')", src)
        self.assertIn('if(withJob)tag.append(body,job);else tag.append(body)', src)
        self.assertIn('labelPrintShowsRequest(order.request)', src)
        self.assertIn('queuePrint(list,true,true,true,false,currentLabelPattern())', src)
        self.assertIn('<option>表示なし</option>', src)
        # 段数切替で消すのは管理番号・部署のみ。
        self.assertIn('data-key="management"],[data-key="department"]', src)
        clear_request = "[data-key=\"request\"]').forEach(el=>{el.value=''})"
        self.assertNotIn(clear_request, src)

    def test_three_line_hides_mid_columns_and_notice_defaults(self):
        js = (ROOT / 'sheet.js').read_text(encoding='utf-8')
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('applyLabelPatternUi', js)
        self.assertIn("classList.toggle('labelpattern3'", js)
        self.assertIn('data-key="management"', js)
        self.assertIn("NOTICE_DEFAULT_TO='サザエ食品'", js)
        self.assertIn("NOTICE_DEFAULT_PLACE='宮の森事務所'", js)
        self.assertIn('#labeldesk #labeladdresspanel.labelpattern3 .labelcol-mid{display:none}', css)

    def test_weekday_samples_match_expected_japanese(self):
        # Mirror sheet.js formatNoticeDate (JS getDay: Sun=0) without a browser.
        import re
        from datetime import date

        weekdays = ['日', '月', '火', '水', '木', '金', '土']

        def format_js_style(iso: str) -> str:
            m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', iso or '')
            if not m:
                return ''
            d = date(int(m[1]), int(m[2]), int(m[3]))
            js_day = (d.weekday() + 1) % 7
            return f'{d.month}月{d.day}日（{weekdays[js_day]}）'

        self.assertEqual(format_js_style('2026-10-12'), '10月12日（月）')
        self.assertEqual(format_js_style('2026-10-10'), '10月10日（土）')
        self.assertEqual(format_js_style('2026-01-01'), '1月1日（木）')

    def test_label_print_css_is_centered_a4_landscape(self):
        import re

        js = (ROOT / 'sheet.js').read_text(encoding='utf-8')
        sheet_css = (ROOT / 'sheet.css').read_text(encoding='utf-8')
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        m = re.search(r'const labelPrintCss=`([^`]*)`', js)
        self.assertIsNotNone(m)
        label_css = m.group(1)
        self.assertIn('@page{size:A4 landscape;margin:0}', label_css)
        self.assertIn('.labelpage{width:297mm;height:210mm;', label_css)
        self.assertIn('padding:5mm 8mm', label_css)
        self.assertIn('left:8mm;right:8mm', label_css)
        self.assertNotIn('padding:5mm 28mm', label_css)
        self.assertNotIn('left:28mm;right:28mm', label_css)
        self.assertIn('grid-template-columns:minmax(0,1fr) minmax(0,1fr)', label_css)
        self.assertIn('grid-template-rows:minmax(0,1fr) minmax(0,1fr)', label_css)
        self.assertNotIn('margin:18mm 12mm 6mm 12mm', label_css)
        self.assertNotIn('width:273mm', label_css)
        self.assertIn('<title></title>', js)
        self.assertIn('@page labels{size:A4 landscape;margin:0}', sheet_css)
        self.assertIn('@page{size:A4 portrait;margin:12mm}', sheet_css)
        self.assertIn('id="labelprinttip"', html)
        self.assertIn('ヘッダーとフッター', html)
        self.assertIn('ヘッダーとフッター', js)


if __name__ == '__main__':
    unittest.main()
