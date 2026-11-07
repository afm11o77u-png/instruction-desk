"""日別出勤簿: 保存パスの月シートを読取専用で返す。月次集計はしない。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import kintai

ROOT = Path(__file__).resolve().parents[1]


def _col_letters(n: int) -> str:
    s = ''
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx(sheets: dict[str, list[tuple[int, int, object]]], print_areas: dict[str, str] | None = None) -> bytes:
    """シート名 → [(row, col, value)] の最小 xlsx。値は数値か文字列。"""
    names = list(sheets)
    out = BytesIO()
    ss = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

    def cell(col, row, value):
        ref = f'{_col_letters(col)}{row}'
        if isinstance(value, tuple) and value and value[0] == 'formula':
            return f'<c r="{ref}"><f>{escape(str(value[1]))}</f></c>'
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        text = escape(str(value if value is not None else ''))
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + ''.join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(names) + 1)
            )
            + '</Types>',
        )
        z.writestr(
            '_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        z.writestr(
            'xl/_rels/workbook.xml.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + ''.join(
                f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(names) + 1)
            )
            + '</Relationships>',
        )
        tags = ''.join(
            f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, name in enumerate(names, 1)
        )
        defined = ''
        if print_areas:
            bits = []
            for name, formula in print_areas.items():
                if name not in names:
                    continue
                quoted = "'" + name.replace("'", "''") + "'"
                if formula.startswith('#'):
                    text = formula
                else:
                    text = ','.join(
                        part if '!' in part else f'{quoted}!{part}'
                        for part in (piece.strip() for piece in formula.split(','))
                        if part
                    )
                bits.append(
                    f'<definedName name="_xlnm.Print_Area" localSheetId="{names.index(name)}">{escape(text)}</definedName>'
                )
            if bits:
                defined = '<definedNames>' + ''.join(bits) + '</definedNames>'
        z.writestr(
            'xl/workbook.xml',
            f'<workbook xmlns="{ss}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{tags}</sheets>{defined}</workbook>',
        )
        for i, name in enumerate(names, 1):
            rows = {}
            for row, col, value in sheets[name]:
                rows.setdefault(row, []).append(cell(col, row, value))
            body = ''.join(f'<row r="{row}">{"".join(rows[row])}</row>' for row in sorted(rows))
            z.writestr(
                f'xl/worksheets/sheet{i}.xml',
                f'<worksheet xmlns="{ss}"><sheetData>{body}</sheetData></worksheet>',
            )
    return out.getvalue()


def _serial(day: date) -> int:
    return (day - date(1899, 12, 30)).days


class DailySheetReadTest(unittest.TestCase):
    def test_fixture_days_are_raw_cells_not_monthly_totals(self):
        blob = kintai.build_fixture_xlsx({
            (2026, 2): [
                {'day': 1, 'start': '9:00', 'leave': '', 'ordinary_minutes': 480, 'overtime_minutes': 30},
                {'day': 2, 'start': '', 'leave': '1.0有休', 'ordinary_minutes': 0, 'overtime_minutes': 0},
            ],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.FIXTURE_LAYOUT)
        self.assertEqual(view['sheetName'], '2026-02')
        by_date = {row['date']: row for row in view['rows']}
        self.assertEqual(by_date['2026/02/01']['start'], '9:00')
        self.assertEqual(by_date['2026/02/01']['leave'], '')
        self.assertEqual(by_date['2026/02/01']['ordinary'], '480')
        self.assertEqual(by_date['2026/02/01']['overtime'], '30')
        self.assertEqual(by_date['2026/02/02']['leave'], '1.0有休')
        self.assertNotIn('workDays', by_date['2026/02/01'])
        labels = [c['label'] for c in view['columns']]
        self.assertEqual(labels[:3], ['日', '始業', '休み'])

    def test_seishain_uses_calendar_date_and_month_sheet_cells(self):
        cal = '年間ｶﾚﾝﾀﾞｰ'
        blob = _xlsx({
            cal: [
                (3, 2, _serial(date(2026, 2, 1))),
                (4, 2, _serial(date(2026, 2, 15))),
                (5, 2, _serial(date(2026, 3, 1))),
            ],
            '26.2': [
                (4, 4, '休み'),
                (4, 5, '始業時間'),
                (5, 1, _serial(date(2025, 2, 1))),
                (5, 4, ''),
                (5, 5, 0.375),
                (6, 4, '1.0有休'),
                (6, 5, ''),
            ],
            '集計': [],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(view['sheetName'], '26.2')
        self.assertEqual([c['label'] for c in view['columns'][:3]], ['日付', '有休', '始業時間'])
        self.assertEqual(view['rows'][0]['date'], '2026/02/01')
        self.assertEqual(view['rows'][0]['start'], '9:00')
        self.assertNotIn('2025', view['rows'][0]['date'])
        self.assertEqual(view['rows'][1]['date'], '2026/02/15')
        self.assertEqual(view['rows'][1]['leave'], '1.0有休')
        self.assertEqual(view['rows'][1]['start'], '')
        self.assertNotIn('2026/03/01', [row['date'] for row in view['rows']])

    def test_monthdot_and_part_show_sheet_dates_without_converting_paid_hours(self):
        contract = _xlsx({
            '2026.2': [
                (5, 1, _serial(date(2026, 2, 1))),
                (5, 4, ''),
                (5, 5, '8:30'),
                (6, 1, _serial(date(2026, 2, 2))),
                (6, 4, '0.5有休'),
                (6, 5, ''),
            ],
        })
        view = kintai.daily_rows_from_workbook(contract, 2026, 2, kintai.MONTHDOT_2026_CONTRACT_LAYOUT)
        self.assertEqual(view['rows'][0]['date'], '2026/02/01')
        self.assertEqual(view['rows'][0]['start'], '8:30')
        self.assertEqual(view['rows'][1]['leave'], '0.5有休')
        self.assertEqual([c['key'] for c in view['columns']], [
            'date', 'leave', 'start', 'breakTime', 'end', 'calendarHours',
            'actual', 'earlyNight', 'dayOvertime', 'nightOvertime', 'total',
        ])
        # 見出しが無い時間列は、隣の数値を流用しない
        self.assertEqual(view['rows'][0]['calendarHours'], '（列なし）')
        self.assertEqual(view['totalsNote'], '（列なし）')
        self.assertEqual(view['totals'], [])
        self.assertNotIn('totalLabel', view['rows'][0])

        part = _xlsx({
            '2026.2': [
                (5, 1, _serial(date(2026, 2, 1))),
                (5, 4, ''),
                (5, 5, 0.5),
                (5, 8, 0.375),
            ],
        })
        part_view = kintai.daily_rows_from_workbook(part, 2026, 2, kintai.MONTHDOT_2026_PART_LAYOUT)
        row = part_view['rows'][0]
        self.assertEqual(row['start'], '9:00')
        self.assertNotIn('paidTime', [c['key'] for c in part_view['columns']])
        self.assertNotIn('0.5', row.values())

    def test_headed_columns_follow_excel_order_and_keep_blanks(self):
        """表示名は11個だけ。合計は実労働時間計の保存値で、実労働時間との足し算ではない。"""
        break_serial = 70 / (24 * 60)  # 1:10
        blob = _xlsx({
            '年間ｶﾚﾝﾀﾞｰ': [
                (3, 2, _serial(date(2026, 2, 2))),
            ],
            '26.2': [
                (3, 3, '曜日'),
                (3, 4, '休みヤス'),
                (3, 5, '始業時間（00：00）シギョウジカン'),
                (3, 6, '休憩時間（00：00）キュウケイジカン'),
                (3, 7, '終業時間（00：00）シュウギョウジカン'),
                (3, 8, '年間ｶﾚﾝﾀﾞｰ時間ネンカンカレンダー'),
                (3, 9, 'ｶﾚﾝﾀﾞ変換'),
                (3, 11, '年間'),
                (3, 23, '早朝深夜'),
                (3, 16, '深夜残業判定①'),
                (3, 22, '早朝勤務'),
                (3, 24, '実労働時間'),
                (3, 25, '早朝深夜勤務時間'),
                (3, 26, '日残業\n（深残除く）'),
                (3, 27, '深夜残業'),
                (3, 36, '休出早朝深夜勤務'),
                (3, 39, 'メモ'),
                (3, 40, '実労働時間計'),
                (5, 37, '実労働時間計'),
                (5, 38, 17.03),
                (6, 37, '予定時間計'),
                (6, 38, 0),
                (5, 3, '月'),
                (5, 4, ''),
                (5, 5, 9 / 24),
                (5, 6, break_serial),
                (5, 7, ''),
                (5, 8, 8),
                (5, 9, 8),
                (5, 11, 1),
                (5, 23, 3),
                (5, 16, 99),
                (5, 22, 0),
                (5, 24, 8.5),
                (5, 25, 0),
                (5, 26, ''),
                (5, 27, 0.25),
                (5, 30, 77),
                (5, 36, ('formula', 'Z5+AA5')),
                (5, 39, '遅番'),
                (5, 40, 9.1),
            ],
            '集計': [],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        labels = [c['label'] for c in view['columns']]
        self.assertEqual(labels, [
            '日付', '有休', '始業時間', '休憩時間', '終業時間', '年間',
            '実労働時間', '早朝深夜', '日残業', '深夜残業', '合計',
        ])
        self.assertNotIn('（00', ''.join(labels))
        by_key = {c['key']: c for c in view['columns']}
        self.assertEqual(by_key['calendarHours']['col'], 8)
        self.assertEqual(by_key['earlyNight']['col'], 25)
        self.assertEqual(by_key['total']['parts'][0]['col'], 37)
        self.assertEqual(by_key['total']['parts'][1]['col'], 38)
        self.assertEqual(by_key['actual']['col'], 24)
        self.assertEqual(by_key['dayOvertime']['sourceLabel'], '日残業\n（深残除く）')
        row = view['rows'][0]
        self.assertEqual(row['date'], '2026/02/02')
        self.assertEqual(row['leave'], '')
        self.assertEqual(row['start'], '9:00')
        self.assertEqual(row['breakTime'], '1:10')
        self.assertEqual(row['end'], '')
        self.assertNotEqual(row['end'], '0')
        self.assertEqual(row['calendarHours'], '8.00')
        self.assertEqual(row['actual'], '8.50')
        self.assertEqual(row['earlyNight'], '0.00')
        self.assertNotIn('1', row.values())
        self.assertNotIn('3', row.values())
        self.assertEqual(row['dayOvertime'], '')
        self.assertEqual(row['nightOvertime'], '0.25')
        self.assertNotIn('totalLabel', row)
        self.assertEqual(view['totals'][0]['label'], '実労働時間計')
        self.assertEqual(view['totals'][0]['value'], '17.03')
        self.assertNotEqual(view['totals'][0]['value'], row['actual'])
        self.assertNotIn('9.1', [item['value'] for item in view['totals']])
        self.assertNotIn('99', row.values())
        self.assertNotIn('77', row.values())
        self.assertNotIn('遅番', row.values())
        self.assertNotIn('月', row.values())

    def test_duplicate_total_header_is_not_guessed(self):
        blob = _xlsx({
            '年間ｶﾚﾝﾀﾞｰ': [(3, 2, _serial(date(2026, 2, 1)))],
            '26.2': [
                (3, 24, '実労働時間'),
                (5, 24, 8.5),
                (5, 37, '実労働時間計'),
                (6, 37, '予定時間計'),
                (5, 38, 9.1),
                (5, 50, '実労働時間計'),
                (6, 50, '予定時間計'),
                (5, 51, 3),
            ],
            '集計': [],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        row = view['rows'][0]
        self.assertEqual(row['actual'], '8.50')
        self.assertEqual(view['totalsNote'], '（列が複数）')
        self.assertEqual(view['totals'], [])
        self.assertNotIn('9.1', row.values())
        self.assertNotIn('3', row.values())

    def test_part_columns_use_their_own_positions_not_seishain(self):
        blob = _xlsx({
            '2026.2': [
                (3, 4, '休み'),
                (3, 5, '有休\n時間ユウキュウジカン'),
                (3, 6, '時給ジキュウ'),
                (3, 8, '始業時間（00：00）'),
                (3, 9, '休憩時間（00：00）'),
                (3, 10, '終業時間（00：00）'),
                (3, 24, '実労働時間'),
                (3, 25, '早朝深夜勤務時間'),
                (3, 26, '日残業\n（深残除く）'),
                (3, 27, '深夜残業'),
                (5, 1, _serial(date(2026, 2, 1))),
                (5, 4, ''),
                (5, 5, 0.5),
                (5, 6, 1105),
                (5, 8, 8.933333333333334 / 24),
                (5, 9, 70 / (24 * 60)),
                (5, 10, 16 / 24),
                (5, 24, 6.25),
                (5, 25, ''),
                (5, 26, 0.5),
                (5, 27, 0),
                (5, 70, '実労働時間計'),
                (5, 71, 7.5),
                (6, 70, '予定時間計'),
                (6, 71, 0),
                (5, 38, 99),
            ],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.MONTHDOT_2026_PART_LAYOUT)
        self.assertEqual([c['label'] for c in view['columns']], [
            '日付', '有休', '始業時間', '休憩時間', '終業時間', '年間',
            '実労働時間', '早朝深夜', '日残業', '深夜残業', '合計',
        ])
        by_key = {c['key']: c for c in view['columns']}
        self.assertEqual(by_key['start']['col'], 8)
        self.assertEqual(by_key['breakTime']['col'], 9)
        self.assertEqual(by_key['end']['col'], 10)
        self.assertEqual(by_key['total']['parts'][0]['col'], 70)
        self.assertEqual(by_key['total']['parts'][1]['col'], 71)
        self.assertEqual(by_key['calendarHours']['missing'], '（列なし）')
        row = view['rows'][0]
        self.assertEqual(row['start'], '8:56')
        self.assertEqual(row['breakTime'], '1:10')
        self.assertEqual(row['end'], '16:00')
        self.assertEqual(row['actual'], '6.25')
        self.assertEqual(row['dayOvertime'], '0.50')
        self.assertEqual(row['nightOvertime'], '0.00')
        self.assertNotIn('totalLabel', row)
        self.assertEqual(view['totals'][0]['label'], '実労働時間計')
        self.assertEqual(view['totals'][0]['value'], '7.50')
        self.assertNotIn('99', [item['value'] for item in view['totals']])
        self.assertEqual(row['calendarHours'], '（列なし）')
        self.assertNotIn('0.5有休', row.values())
        self.assertNotEqual(row['breakTime'], '1105')
        self.assertNotIn('1105', row.values())
        self.assertNotIn('paidTime', by_key)

    def test_side_totals_follow_excel_rows_without_weekly_math(self):
        """画像の AK/AL 相当。行のラベルと右の数値だけ。週の合計は作らない。"""
        labels = [
            (6, '実労働時間計', 17.03),
            (7, '予定時間計', 0),
            (8, '法定内労働計', 16),
            (9, '日残業計', ('formula', 'H9-H10')),
            (10, '週残業計', 0),
            (11, '深夜残業計', 0),
            (12, '早朝深夜勤務計', 0),
        ]
        sheet = [
            (3, 24, '実労働時間'),
            (6, 24, 8.62),
        ]
        cal = []
        for offset, row in enumerate(range(5, 13)):
            cal.append((3 + offset, 2, _serial(date(2026, 2, 1 + offset))))
        for row, label, value in labels:
            sheet.append((row, 37, label))
            sheet.append((row, 38, value))
        blob = _xlsx({'年間ｶﾚﾝﾀﾞｰ': cal, '26.2': sheet, '集計': []})
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertNotIn('totalLabel', view['rows'][0])
        self.assertEqual([(item['label'], item['value']) for item in view['totals'][:6]], [
            ('実労働時間計', '17.03'),
            ('予定時間計', '0.00'),
            ('法定内労働計', '16.00'),
            ('日残業計', '（計算結果なし）'),
            ('週残業計', '0.00'),
            ('深夜残業計', '0.00'),
        ])
        self.assertNotEqual(view['totals'][0]['value'], view['rows'][1]['actual'])

    def test_ruby_in_shared_strings_is_not_the_cell_value(self):
        ss = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        shared = (
            f'<sst xmlns="{ss}"><si><t>実労働時間計</t>'
            '<rPh sb="0" eb="7"><t>ジツロウドウジカンケイ</t></rPh>'
            '<phoneticPr fontId="1"/></si></sst>'
        )
        sheet = (
            f'<worksheet xmlns="{ss}"><sheetData>'
            '<row r="5"><c r="AK5" t="s"><v>0</v></c><c r="AL5"><v>45.15</v></c></row>'
            '<row r="6"><c r="AK6" t="s"><v>0</v></c><c r="AL6"><v>0</v></c></row>'
            '</sheetData></worksheet>'
        )
        out = BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr(
                '[Content_Types].xml',
                '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                '</Types>',
            )
            z.writestr('_rels/.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{rel}"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{rel}/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
            z.writestr('xl/workbook.xml', f'<workbook xmlns="{ss}" xmlns:r="{rel}"><sheets><sheet name="t" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/sharedStrings.xml', shared)
            z.writestr('xl/worksheets/sheet1.xml', sheet)
        grid = kintai.read_xlsx_grid(out.getvalue())['t']
        self.assertEqual(grid[(5, 37)], '実労働時間計')
        self.assertNotIn('ジツ', grid[(5, 37)])

    def test_hour_display_rounds_half_up_and_keeps_blanks(self):
        show = kintai._display_hours
        self.assertEqual(show(8.833333333333332), '8.83')
        self.assertEqual(show(0.8666666666666667), '0.87')
        self.assertEqual(show(1.1666666666666667), '1.17')
        self.assertEqual(show(1.005), '1.01')
        self.assertEqual(show(1.015), '1.02')
        self.assertEqual(show(-1.005), '-1.01')
        self.assertEqual(show(-1.225), '-1.23')
        self.assertEqual(show(0), '0.00')
        self.assertEqual(show(-0.0), '0.00')
        self.assertEqual(show(8), '8.00')
        self.assertEqual(show(''), '')
        self.assertEqual(show(None), '')
        self.assertEqual(show({'__missing_cache__': True}), '（計算結果なし）')
        self.assertEqual(kintai._display_clock(9 / 24), '9:00')
        self.assertEqual(kintai._display_total_label('実労働時間計ジツロウドウジカンケイ'), '実労働時間計')
        self.assertEqual(kintai._total_label_key('実ジツ労ロウ働ドウ時ジ間カン計ケイ'), '実労働時間計')
        self.assertEqual(kintai._heading_core('休みヤス'), '休み')
        self.assertEqual(kintai._heading_core('年間ｶﾚﾝﾀﾞｰ時間ネンカンカレンダー'), '年間ｶﾚﾝﾀﾞｰ時間')

    def test_totals_on_every_month_and_employment_despite_furigana(self):
        """月・雇用・列位置が違っても、同じ集計ラベルなら出す。無い月は計算しない。"""
        def side(col, value):
            return [
                (5, col, '実労働時間計ジツロウドウジカンケイ'),
                (5, col + 1, value),
                (6, col, '予定時間計ヨテイジカンケイ'),
                (6, col + 1, 0),
            ]

        seishain = {
            '年間ｶﾚﾝﾀﾞｰ': [
                (3, 2, _serial(date(2026, 2, 1))),
                (3, 8, _serial(date(2026, 3, 1))),
            ],
            '26.2': [
                (3, 4, '休み'),
                (3, 5, '始業時間（00：00）'),
                (3, 24, '実労働時間'),
                (5, 5, 9 / 24),
                (5, 24, 8.833333333333332),
                *side(37, 45.15),
                (5, 40, 999),
            ],
            '26.3': [
                (3, 4, '休み'),
                (3, 24, '実労働時間'),
                (5, 24, 1.005),
                (5, 55, '実ジツ労ロウ働ドウ時ジ間カン計ケイ'),
                (5, 56, 12.5),
                (6, 55, '予定ヨテイ時ジ間カン計ケイ'),
                (6, 56, 0),
            ],
            '集計': [],
        }
        feb = kintai.daily_rows_from_workbook(_xlsx(seishain), 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        mar = kintai.daily_rows_from_workbook(_xlsx(seishain), 2026, 3, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(feb['columns'][1]['label'], '有休')
        self.assertEqual(feb['columns'][1]['sourceLabel'], '休み')
        self.assertEqual(feb['rows'][0]['start'], '9:00')
        self.assertEqual(feb['rows'][0]['actual'], '8.83')
        self.assertEqual(feb['totals'][0]['label'], '実労働時間計')
        self.assertEqual(feb['totals'][0]['value'], '45.15')
        self.assertNotIn('999', [item['value'] for item in feb['totals']])
        self.assertEqual(mar['totals'][0]['label'], '実労働時間計')
        self.assertEqual(mar['totals'][0]['value'], '12.50')
        self.assertEqual(mar['rows'][0]['actual'], '1.01')
        self.assertEqual([c['col'] for c in mar['columns'][-1]['parts']], [55, 56])

        contract = _xlsx({
            '2026.2': [
                (5, 1, _serial(date(2026, 2, 1))),
                (5, 4, ''),
                (5, 5, '8:30'),
                (5, 38, 99),
            ],
            '2026.3': [
                (5, 1, _serial(date(2026, 3, 1))),
                (5, 4, '1.0有休'),
                (5, 12, '早朝深夜勤務計ソウチョウシンヤキンムケイ'),
                (5, 13, -1.005),
                (6, 12, '深夜残業計シンヤザンギョウケイ'),
                (6, 13, 0),
            ],
        })
        no_total = kintai.daily_rows_from_workbook(contract, 2026, 2, kintai.MONTHDOT_2026_CONTRACT_LAYOUT)
        has_total = kintai.daily_rows_from_workbook(contract, 2026, 3, kintai.MONTHDOT_2026_CONTRACT_LAYOUT)
        self.assertEqual(no_total['totalsNote'], '（列なし）')
        self.assertEqual(no_total['totals'], [])
        self.assertEqual(has_total['rows'][0]['leave'], '1.0有休')
        self.assertEqual(has_total['totals'][0]['label'], '早朝深夜勤務計')
        self.assertEqual(has_total['totals'][0]['value'], '-1.01')

        part = _xlsx({
            '2026.4': [
                (3, 4, '休み'),
                (3, 8, '始業時間（00：00）'),
                (5, 1, _serial(date(2026, 4, 1))),
                (5, 8, 8 / 24),
                (5, 90, '日残業計ヒザンギョウケイ'),
                (5, 91, 0.004),
                (6, 90, '週残業計シュウザンギョウケイ'),
                (6, 91, 2.005),
            ],
        })
        part_view = kintai.daily_rows_from_workbook(part, 2026, 4, kintai.MONTHDOT_2026_PART_LAYOUT)
        row = part_view['rows'][0]
        self.assertEqual(row['start'], '8:00')
        self.assertEqual(part_view['totals'][0]['label'], '日残業計')
        self.assertEqual(part_view['totals'][0]['value'], '0.00')
        self.assertEqual(part_view['totals'][1]['label'], '週残業計')
        self.assertEqual(part_view['totals'][1]['value'], '2.01')
        self.assertEqual([c['col'] for c in part_view['columns'][-1]['parts']], [90, 91])

    def test_print_area_picks_inside_pair_for_every_month(self):
        """26.2 は AK だけ。26.3〜27.1 は AK と印刷範囲外の AO がある。範囲内の組だけ使う。"""
        names = ['実労働時間計', '予定時間計', '法定内労働計', '日残業計', '週残業計', '深夜残業計', '早朝深夜勤務計']
        sheets = {'集計': []}
        areas = {}
        cal = []
        for (year, month), sheet_name in kintai.SEISHAIN_2026_MONTH_SHEETS.items():
            cal_col = kintai.SEISHAIN_2026_CAL_COLS[(year, month)]
            cal.append((3, cal_col, _serial(date(year, month, 1))))
            cells = [
                (3, 4, '休み'),
                (5, 37, names[0]),
                (5, 38, 45.15),
                (6, 37, names[1]),
                (6, 38, 0),
            ]
            if (year, month) != (2026, 2):
                for i, name in enumerate(names):
                    cells.append((5 + i, 41, name))
                    cells.append((5 + i, 42, 999))
            sheets[sheet_name] = cells
            areas[sheet_name] = '$A$1:$AL$50'
        sheets['年間ｶﾚﾝﾀﾞｰ'] = cal
        blob = _xlsx(sheets, areas)
        for (year, month), sheet_name in kintai.SEISHAIN_2026_MONTH_SHEETS.items():
            view = kintai.daily_rows_from_workbook(blob, year, month, kintai.SEISHAIN_2026_LAYOUT)
            total = view['columns'][-1]
            self.assertEqual([p['col'] for p in total['parts']], [37, 38], sheet_name)
            self.assertEqual(view['totals'][0]['label'], '実労働時間計')
            self.assertEqual(view['totals'][0]['value'], '45.15')
            self.assertNotIn('999', [item['value'] for item in view['totals']])

    def test_print_area_edges_do_not_guess(self):
        names = [(5, '実労働時間計', 11), (6, '予定時間計', 0)]

        def block(col, value):
            return [(row, col, label) for row, label, _ in names] + [
                (names[0][0], col + 1, value), (names[1][0], col + 1, 0),
            ]

        def book(area):
            return _xlsx({
                '年間ｶﾚﾝﾀﾞｰ': [(3, 2, _serial(date(2026, 2, 1)))],
                '26.2': [(3, 4, '休み'), *block(10, 3.5), *block(20, 8)],
                '集計': [],
            }, {'26.2': area} if area is not None else None)

        inside = kintai.daily_rows_from_workbook(book('$A$1:$K$50'), 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual([p['col'] for p in inside['columns'][-1]['parts']], [10, 11])
        self.assertEqual(inside['totals'][0]['value'], '3.50')

        both = kintai.daily_rows_from_workbook(book('$A$1:$Z$50'), 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(both['totalsNote'], '（列が複数）')

        none = kintai.daily_rows_from_workbook(book(None), 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(none['totalsNote'], '（列が複数）')

        broken = kintai.daily_rows_from_workbook(book('#REF!'), 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(broken['totalsNote'], '（列が複数）')

        value_outside = kintai.daily_rows_from_workbook(book('$A$1:$J$50'), 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(value_outside['totalsNote'], '（列なし）')
        self.assertEqual(value_outside['totals'], [])

        rows_outside = kintai.daily_rows_from_workbook(book('$A$1:$Z$4'), 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(rows_outside['totalsNote'], '（列なし）')

        other = _xlsx({
            '年間ｶﾚﾝﾀﾞｰ': [
                (3, 2, _serial(date(2026, 2, 1))),
                (3, 8, _serial(date(2026, 3, 1))),
            ],
            '26.2': [(3, 4, '休み'), *block(10, 4)],
            '26.3': [(3, 4, '休み'), *block(10, 1), *block(30, 2)],
            '集計': [],
        }, {'26.3': "'26.2'!$A$1:$Z$50"})
        march = kintai.daily_rows_from_workbook(other, 2026, 3, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(march['totalsNote'], '（列が複数）')

        two_ranges = kintai.daily_rows_from_workbook(
            book('$A$1:$K$50,$L$1:$Z$50'), 2026, 2, kintai.SEISHAIN_2026_LAYOUT,
        )
        self.assertEqual(two_ranges['totalsNote'], '（列が複数）')

    def test_closing_period_is_previous_16_through_15_every_month(self):
        self.assertEqual(kintai.closing_period(2026, 10), (date(2026, 9, 16), date(2026, 10, 15)))
        self.assertEqual(kintai.closing_period(2027, 1), (date(2026, 12, 16), date(2027, 1, 15)))
        self.assertEqual(kintai.closing_period(2024, 2), (date(2024, 1, 16), date(2024, 2, 15)))
        self.assertEqual(kintai.closing_period(2024, 3), (date(2024, 2, 16), date(2024, 3, 15)))
        self.assertEqual(kintai.closing_period(2026, 3), (date(2026, 2, 16), date(2026, 3, 15)))
        self.assertEqual((kintai.closing_period(2024, 3)[1] - kintai.closing_period(2024, 3)[0]).days + 1, 29)
        self.assertEqual((kintai.closing_period(2026, 3)[1] - kintai.closing_period(2026, 3)[0]).days + 1, 28)

    def test_daily_hides_days_outside_16_to_15_without_moving_totals(self):
        names = ['実労働時間計', '予定時間計']

        def seishain_october():
            cal_col = kintai.SEISHAIN_2026_CAL_COLS[(2026, 10)]
            dates = [date(2026, 9, 11), date(2026, 9, 16), date(2026, 10, 15), date(2026, 10, 16)]
            cal = [(3 + i, cal_col, _serial(day)) for i, day in enumerate(dates)]
            cells = [(3, 4, '休み')]
            # 9/11 は期間外。その行の合計を、9/16 の行へ詰めない
            cells += [(5, 37, names[0]), (5, 38, 1.03), (6, 37, names[1]), (6, 38, 0)]
            cells += [(7, 37, '法定内労働計'), (7, 38, 16), (8, 37, '日残業計'), (8, 38, 4.19)]
            return _xlsx({'年間ｶﾚﾝﾀﾞｰ': cal, '26.10': cells, '集計': []})

        view = kintai.daily_rows_from_workbook(seishain_october(), 2026, 10, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(view['periodStart'], '2026/09/16')
        self.assertEqual(view['periodEnd'], '2026/10/15')
        self.assertEqual([row['date'] for row in view['rows']], ['2026/09/16', '2026/10/15'])
        self.assertNotIn('totalLabel', view['rows'][0])
        self.assertEqual([(item['label'], item['value']) for item in view['totals']], [
            ('実労働時間計', '1.03'),
            ('予定時間計', '0.00'),
            ('法定内労働計', '16.00'),
            ('日残業計', '4.19'),
        ])

        def month_sheet(layout, year, month, days):
            sheet = layout['month_sheets'][(year, month)]
            cells = []
            for i, day in enumerate(days):
                cells.append((5 + i, 1, _serial(day)))
                cells.append((5 + i, layout['start_col'], 9 / 24))
            cells += [(5, 37, names[0]), (5, 38, 9), (6, 37, names[1]), (6, 38, 8)]
            return _xlsx({sheet: cells})

        contract = kintai.MONTHDOT_2026_CONTRACT_LAYOUT
        part = kintai.MONTHDOT_2026_PART_LAYOUT
        window = [date(2026, 12, 15), date(2026, 12, 16), date(2027, 1, 15), date(2027, 1, 16)]
        for layout in (contract, part):
            shown = kintai.daily_rows_from_workbook(month_sheet(layout, 2027, 1, window), 2027, 1, layout)
            self.assertEqual([row['date'] for row in shown['rows']], ['2026/12/16', '2027/01/15'])
            self.assertEqual([(item['label'], item['value']) for item in shown['totals']], [
                (names[0], '9.00'),
                (names[1], '8.00'),
            ])
            self.assertEqual(shown['rows'][0]['start'], '9:00')

        def leap_book(year, feb_days):
            sheet = 'm'
            cells = [(5 + i, 1, _serial(day)) for i, day in enumerate(feb_days)]
            cells += [(5, 4, ''), (6, 4, ''), (7, 37, '実労働時間計'), (7, 38, 2), (8, 37, '予定時間計'), (8, 38, 3)]
            layout = {
                **kintai.MONTHDOT_2026_CONTRACT_LAYOUT,
                'month_sheets': {(year, 3): sheet},
            }
            return _xlsx({sheet: cells}), layout

        leap_days = [date(2024, 2, 15), date(2024, 2, 16), date(2024, 2, 29), date(2024, 3, 15), date(2024, 3, 16)]
        plain_days = [date(2026, 2, 15), date(2026, 2, 16), date(2026, 2, 28), date(2026, 3, 15), date(2026, 3, 16)]
        leap_blob, leap_layout = leap_book(2024, leap_days)
        plain_blob, plain_layout = leap_book(2026, plain_days)
        leap_view = kintai.daily_rows_from_workbook(leap_blob, 2024, 3, leap_layout)
        plain_view = kintai.daily_rows_from_workbook(plain_blob, 2026, 3, plain_layout)
        self.assertEqual([row['date'] for row in leap_view['rows']], ['2024/02/16', '2024/02/29', '2024/03/15'])
        self.assertEqual([row['date'] for row in plain_view['rows']], ['2026/02/16', '2026/02/28', '2026/03/15'])
        self.assertNotIn('2024/02/29', [row['date'] for row in plain_view['rows']])

    def test_march_totals_keep_weekly_repeats_apart_from_the_date_window(self):
        """3月シート相当。42行の7名称は日付の16〜15とは別に、元の順で残す。印刷範囲外は使わない。"""
        names = [
            '実労働時間計', '予定時間計', '法定内労働計', '日残業計',
            '週残業計', '深夜残業計', '早朝深夜勤務計',
        ]
        cal_col = kintai.SEISHAIN_2026_CAL_COLS[(2026, 3)]
        first = date(2026, 2, 2)
        cal = []
        cells = [
            (3, 4, '休み'),
            (3, 5, '始業時間（00：00）'),
            (3, 24, '実労働時間'),
        ]
        for i in range(42):
            day = first + timedelta(days=i)
            cal.append((3 + i, cal_col, _serial(day)))
            row = 5 + i
            if i == 14:
                cells.append((row, 24, 8.5))
            label = names[i % 7]
            if i == 3:
                label = '日ヒ残ザン業ギョウ計ケイ'
            cells.append((row, 37, label))
            if i == 4:
                cells.append((row, 38, ('formula', 'AL4')))
            elif i == 11:
                pass
            elif i == 18:
                cells.append((row, 38, 1.005))
            else:
                cells.append((row, 38, i + 0.1))
            if i < 7:
                cells.append((row, 41, names[i]))
                cells.append((row, 42, 999))
        blob = _xlsx(
            {'年間ｶﾚﾝﾀﾞｰ': cal, '26.3': cells, '集計': []},
            {'26.3': '$A$1:$AL$50'},
        )
        view = kintai.daily_rows_from_workbook(blob, 2026, 3, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(view['periodStart'], '2026/02/16')
        self.assertEqual(view['periodEnd'], '2026/03/15')
        self.assertEqual(len(view['rows']), 28)
        self.assertEqual(view['rows'][0]['date'], '2026/02/16')
        self.assertEqual(view['rows'][-1]['date'], '2026/03/15')
        self.assertNotIn('2026/02/15', [row['date'] for row in view['rows']])
        self.assertNotIn('totalLabel', view['rows'][0])
        self.assertEqual([p['col'] for p in view['columns'][-1]['parts']], [37, 38])
        self.assertEqual(len(view['totals']), 42)
        self.assertEqual([item['label'] for item in view['totals']], names * 6)
        self.assertEqual(view['totals'][4]['value'], '（計算結果なし）')
        self.assertEqual(view['totals'][11]['value'], '')
        self.assertEqual(view['totals'][18]['value'], '1.01')
        self.assertNotIn('999', [item['value'] for item in view['totals']])
        self.assertNotEqual(view['totals'][14]['value'], view['rows'][0]['actual'])
        self.assertEqual(view['totalsNote'], '')

    def test_daily_table_scrolls_and_keeps_date_with_headers(self):
        js = (ROOT / 'kintai.js').read_text(encoding='utf-8')
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        paint = js.split('function paintDaily', 1)[1].split('async function ensurePeoplePack', 1)[0]
        self.assertIn('dayCols.map', paint)
        self.assertIn('kintai-daily-totals', paint)
        self.assertIn('data.totals', paint)
        self.assertIn("c.key !== 'total'", paint)
        self.assertNotIn('colspan=', paint)
        self.assertIn('scope="col"', paint)
        self.assertIn('scope="row"', paint)
        self.assertIn('title=', paint)
        self.assertNotIn("['date','leave','start']", paint)
        daily_css = css[css.find('#kintaidaily'):css.find('#kintaifileinput')]
        self.assertIn('#kintaidaily .kintai-tablewrap{max-width:100%', daily_css)
        self.assertIn('overflow:auto', daily_css)
        self.assertIn('.kintai-daily-sheet{width:max-content', daily_css)
        self.assertIn('font-size:14px', daily_css)
        self.assertIn('padding:4px 6px', daily_css)
        self.assertNotIn('font-size:12px', daily_css)
        self.assertNotIn('padding:1px 3px', daily_css)
        self.assertIn('white-space:nowrap', daily_css)
        self.assertIn('.kintai-daily-sticky{position:sticky;left:0', daily_css)
        self.assertIn('合計は日付の横ではなく', html)
        self.assertIn('週残業計', html)
        self.assertIn('（列なし）', html)
        self.assertNotIn('見出しがある列をExcelの並び', html)


class DailySheetApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        kintai.bind(self.data)
        self.db = self.data / 't.sqlite3'
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, version INTEGER)"
        )
        kintai.schema(self.c)
        self.person = self.c.execute('SELECT * FROM kintai_people WHERE active=1 ORDER BY id LIMIT 1').fetchone()
        fixtures = self.data / 'kintai-fixtures'
        fixtures.mkdir()
        self.book = fixtures / 'daily.xlsx'
        self.blob = kintai.build_fixture_xlsx({
            (2026, 2): [
                {'day': 3, 'start': '10:00', 'leave': '', 'ordinary_minutes': 450, 'overtime_minutes': 15},
            ],
        })
        self.book.write_bytes(self.blob)
        self.c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', ('daily.xlsx', self.person['id']))
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'fixture-v1',
            'peopleFiles': {},
            'peopleLayouts': {str(self.person['id']): 'fixture-v1'},
        }, ensure_ascii=False), encoding='utf-8')
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_reads_saved_path_without_writing_or_storing_days(self):
        before = self.book.read_bytes()
        mtime = self.book.stat().st_mtime
        monthly = self.c.execute('SELECT COUNT(*) FROM kintai_monthly').fetchone()[0]
        view = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 2)
        self.assertEqual(self.book.read_bytes(), before)
        self.assertEqual(self.book.stat().st_mtime, mtime)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM kintai_monthly').fetchone()[0], monthly)
        self.assertTrue(view['readOnly'])
        self.assertEqual(view['fileName'], 'daily.xlsx')
        hit = next(row for row in view['rows'] if row['date'] == '2026/02/03')
        self.assertEqual(hit['start'], '10:00')
        self.assertEqual(hit['ordinary'], '450')

    def test_permission_matches_attendance(self):
        class Denied:
            user = {'id': 2, 'role': 'sales', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/daily?personId=1&year=2026&month=2'

        class Allowed:
            user = {'id': 1, 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = f"/api/kintai/daily?personId={self.person['id']}&year=2026&month=2"

        self.assertEqual(kintai.handle_get(Denied(), self.c, '/api/kintai/daily')[0], 403)
        code, body = kintai.handle_get(Allowed(), self.c, '/api/kintai/daily')
        self.assertEqual(code, 200)
        self.assertEqual(body['sheetName'], '2026-02')
        self.assertEqual(body['personId'], self.person['id'])

    def test_saved_path_returns_sheet_columns_without_writing(self):
        blob = _xlsx({
            '年間ｶﾚﾝﾀﾞｰ': [(3, 2, _serial(date(2026, 2, 3)))],
            '26.2': [
                (3, 4, '休み'),
                (3, 5, '始業時間（00：00）'),
                (3, 6, '休憩時間（00：00）'),
                (3, 7, '終業時間（00：00）'),
                (3, 24, '実労働時間'),
                (3, 40, '実労働時間計'),
                (5, 5, 9 / 24),
                (5, 6, 70 / (24 * 60)),
                (5, 24, 8.5),
                (5, 37, '実労働時間計'),
                (5, 38, 17.03),
                (5, 40, 8.5),
                (6, 37, '予定時間計'),
                (6, 38, 0),
            ],
            '集計': [],
        })
        self.book.write_bytes(blob)
        before = self.book.read_bytes()
        mtime = self.book.stat().st_mtime
        monthly = self.c.execute('SELECT COUNT(*) FROM kintai_monthly').fetchone()[0]
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'shukkinbo-2026-seishain-v1',
            'peopleFiles': {},
            'peopleLayouts': {str(self.person['id']): 'shukkinbo-2026-seishain-v1'},
        }, ensure_ascii=False), encoding='utf-8')
        view = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 2)
        self.assertEqual(self.book.read_bytes(), before)
        self.assertEqual(self.book.stat().st_mtime, mtime)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM kintai_monthly').fetchone()[0], monthly)
        self.assertTrue(view['readOnly'])
        labels = [c['label'] for c in view['columns']]
        self.assertEqual(labels, [
            '日付', '有休', '始業時間', '休憩時間', '終業時間', '年間',
            '実労働時間', '早朝深夜', '日残業', '深夜残業', '合計',
        ])
        hit = view['rows'][0]
        self.assertEqual(hit['date'], '2026/02/03')
        self.assertEqual(hit['start'], '9:00')
        self.assertEqual(hit['breakTime'], '1:10')
        self.assertEqual(hit['end'], '')
        self.assertEqual(hit['actual'], '8.50')
        self.assertNotIn('totalLabel', hit)
        self.assertEqual(view['totals'][0]['label'], '実労働時間計')
        self.assertEqual(view['totals'][0]['value'], '17.03')
        self.assertNotEqual(view['totals'][0]['value'], hit['actual'])
        self.assertEqual(hit['calendarHours'], '（列なし）')
        code, body = kintai.handle_get(type('H', (), {
            'user': {'id': 1, 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0},
            'path': f"/api/kintai/daily?personId={self.person['id']}&year=2026&month=2",
        })(), self.c, '/api/kintai/daily')
        self.assertEqual(code, 200)
        self.assertEqual(body['totals'][0]['label'], '実労働時間計')
        self.assertEqual(body['totals'][0]['value'], '17.03')
        self.assertNotIn('totalLabel', body['rows'][0])


if __name__ == '__main__':
    unittest.main()
