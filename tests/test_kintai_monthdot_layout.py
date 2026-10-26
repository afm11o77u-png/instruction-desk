"""契約・パート出勤簿（月シート 2026.2 形式）の検出・自動レイアウト。"""
from __future__ import annotations

import unittest
from datetime import date
from io import BytesIO
import zipfile

import kintai


def _minimal_xlsx(sheet_names: list[str]) -> bytes:
    """空に近い xlsx（シート名だけ）。セル意味の検証には使わない。"""
    from xml.sax.saxutils import escape

    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '''<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
''' + ''.join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheet_names) + 1)
        ) + '</Types>')
        z.writestr('_rels/.rels', '''<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        sheets_xml = ''.join(
            f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, n in enumerate(sheet_names, 1)
        )
        z.writestr('xl/workbook.xml', f'''<?xml version="1.0"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>{sheets_xml}</sheets></workbook>''')
        rels = ''.join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheet_names) + 1)
        )
        z.writestr('xl/_rels/workbook.xml.rels', f'''<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>''')
        for i in range(1, len(sheet_names) + 1):
            z.writestr(
                f'xl/worksheets/sheet{i}.xml',
                '''<?xml version="1.0"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>日付</t></is></c>
<c r="B1" t="inlineStr"><is><t>見出し仮</t></is></c></row>
</sheetData></worksheet>''',
            )
    return buf.getvalue()


class MonthdotDetectTest(unittest.TestCase):
    def test_detect_monthdot_vs_seishain(self):
        self.assertEqual(
            kintai.detect_sheet_pattern([f'2026.{m}' for m in range(2, 13)] + ['2027.1', '集計']),
            'monthdot-yyyy-m',
        )
        self.assertEqual(
            kintai.detect_sheet_pattern(['集計', '年間ｶﾚﾝﾀﾞｰ', '26.2', '26.3', '27.1']),
            'seishain-2026',
        )

    def test_layout_by_employment(self):
        self.assertEqual(
            kintai.layout_id_for_person({'id': 1, 'employment': 'regular'}, {}),
            'shukkinbo-2026-seishain-v1',
        )
        self.assertEqual(
            kintai.layout_id_for_person({'id': 2, 'employment': 'contract'}, {}),
            'shukkinbo-2026-monthdot-v1',
        )
        self.assertEqual(
            kintai.layout_id_for_person({'id': 3, 'employment': 'part'}, {}),
            'shukkinbo-2026-monthdot-part-v1',
        )
        # 個別上書きが優先
        self.assertEqual(
            kintai.layout_id_for_person(
                {'id': 2, 'employment': 'contract'},
                {'peopleLayouts': {'2': 'fixture-v1'}},
            ),
            'fixture-v1',
        )

    def test_monthdot_minimal_xlsx_missing_summary_fails_clearly(self):
        names = [f'2026.{m}' for m in range(2, 13)] + ['2027.1']
        blob = _minimal_xlsx(names)
        layout = kintai.MONTHDOT_2026_CONTRACT_LAYOUT
        with self.assertRaises(ValueError) as ctx:
            kintai.parse_person_workbook(
                blob, kintai.fiscal_months(2026, 2), layout, as_of=date(2027, 1, 31)
            )
        msg = str(ctx.exception)
        self.assertIn('集計', msg)

    def test_seishain_on_monthdot_file_explains(self):
        names = [f'2026.{m}' for m in range(2, 13)] + ['2027.1', '集計']
        blob = _minimal_xlsx(names)
        with self.assertRaises(ValueError) as ctx:
            kintai.parse_person_workbook(
                blob,
                kintai.fiscal_months(2026, 2),
                kintai.SEISHAIN_2026_LAYOUT,
                as_of=date(2027, 1, 31),
            )
        msg = str(ctx.exception)
        self.assertIn('契約・パート', msg)
        self.assertIn('2026.2', msg)

    def test_probe_suggests_monthdot_v1(self):
        names = [f'2026.{m}' for m in range(2, 13)] + ['2027.1', '集計']
        probed = kintai.probe_workbook_bytes(_minimal_xlsx(names))
        self.assertEqual(probed['sheetPattern'], 'monthdot-yyyy-m')
        self.assertEqual(probed['suggestedLayoutId'], 'shukkinbo-2026-monthdot-v1')
        self.assertFalse(probed['sampleNeeded'])
        self.assertTrue(any(s.get('preview') for s in probed['sheets']))

    def test_pending_alias_still_registered(self):
        self.assertIn('shukkinbo-2026-monthdot-pending', kintai.LAYOUTS)
        self.assertEqual(
            kintai.LAYOUTS['shukkinbo-2026-monthdot-pending']['kind'],
            'summary-monthdot',
        )

    def test_error_format_puts_reason_first(self):
        text = kintai._format_import_error(
            'パース失敗: x',
            path='\\\\server\\very\\long\\path\\file.xlsx',
            layout_id='shukkinbo-2026-monthdot-v1',
            employment='contract',
            parse_hint=True,
        )
        self.assertTrue(text.startswith('パース失敗'))
        self.assertIn('レイアウト:', text)
        self.assertIn('パス:', text)
        self.assertLess(text.index('パース失敗'), text.index('パス:'))


if __name__ == '__main__':
    unittest.main()
