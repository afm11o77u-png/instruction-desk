"""個人向け勤怠通知。年度の月別集計をA4横1枚に出力する。"""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

from kintai_bihin_xlsx import XLSX, content_disposition, _col

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def workbook(overview):
    person = overview['rows'][0]
    rows, merges = [], []

    def row(values, style=0, height=32):
        n = len(rows) + 1
        cells = []
        for i, value in enumerate(values):
            cell_style = style[i] if isinstance(style, list) else style
            ref = f'{_col(i)}{n}'
            if value is None or value == '':
                cells.append(f'<c r="{ref}" s="{cell_style}"/>')
            elif isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}" s="{cell_style}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" s="{cell_style}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        rows.append(f'<row r="{n}" ht="{height}" customHeight="1">{"".join(cells)}</row>')
        return n

    def line(text, style=2, height=26):
        n = row([text], style, height)
        merges.append(f'A{n}:L{n}')

    line('勤怠状況のお知らせ', 3, 38)
    line(f"{person['displayName']} 様（社員番号 {person['employeeCode']}）　{overview['fiscalLabel']}")
    line(f"月別：保存済み集計／最終取込 {person.get('lastImportAt') or '未取得'}", 2, 15)
    row(['月', '出勤日数', '休日数', '累計休日数', '年間休日残数', '有給取得日数', '有給累計取得', '有給残数', '有給休暇起算日', '普通勤務時間', '残業時間', '勤務時間計'], 1, 40)
    for m in person['months']:
        values = [f"{m['year']}/{m['month']:02d}"] + [m.get(k) for k in (
            'workDays', 'holidayDays', 'holidayCumulative', 'holidayRemaining',
            'paidLeaveDays', 'paidLeaveCumulative')]
        values += [m.get('paidLeaveRemaining') if m.get('paidLeaveShowsRead') else None,
                   m.get('paidLeaveBaseDate'), m.get('ordinaryHm'), m.get('overtimeHm'), m.get('totalHm')]
        row(values, [5] + [4 if v is not None and v != '' else 0 for v in values[1:]])
    hold = next((m for m in person['months'] if m.get('paidLeaveReadBalance') is not None), {})
    balance = hold.get('paidLeaveReadBalance')
    line(f"有休現保有数：{balance if balance is not None else '未取得'} 日　取得日：{hold.get('paidLeaveAcquiredOn') or '未取得'}", 2)
    line('空欄は未取得・未確定です。Web出勤簿と同じ保存済み集計を表示しています。', 2, 22)
    last = len(rows)
    widths = [12, 10, 9, 11, 12, 12, 12, 10, 17, 14, 12, 14]
    sheet = (f'<worksheet xmlns="{NS}"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
             '<sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>'
             '<cols>' + ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)) + '</cols>'
             f'<sheetData>{"".join(rows)}</sheetData><mergeCells>' + ''.join(f'<mergeCell ref="{m}"/>' for m in merges) + '</mergeCells>'
             '<pageMargins left="0.25" right="0.25" top="0.3" bottom="0.3" header="0.1" footer="0.1"/>'
             '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="1"/></worksheet>')
    # Webの.kintai-sheetと同じ紺見出し、淡黄の数値、青灰の罫線。
    fonts = ''.join(f'<font>{"<b/>" if bold else ""}{"<u/>" if underline else ""}<color rgb="{color}"/><sz val="{size}"/><name val="BIZ UDPGothic"/></font>'
                    for size, bold, color, underline in [(13, False, 'FF000000', False), (11, True, 'FFFFFFFF', False),
                        (12, False, 'FF000000', False), (22, True, 'FF000000', False), (13, True, 'FF1F4E79', True)])
    border = '<border>' + ''.join(f'<{side} style="thin"><color rgb="FFB7C3D0"/></{side}>' for side in ('left', 'right', 'top', 'bottom')) + '</border>'
    formats = [(0, 0, 1, 'right'), (1, 2, 1, 'center'), (2, 0, 0, 'left'),
               (3, 0, 0, 'left'), (0, 3, 1, 'right'), (4, 0, 1, 'left')]
    styles = (f'<styleSheet xmlns="{NS}"><fonts count="5">{fonts}</fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              + ''.join(f'<fill><patternFill patternType="solid"><fgColor rgb="{color}"/><bgColor indexed="64"/></patternFill></fill>' for color in ('FF1F4E79', 'FFFFF2CC'))
              + f'</fills><borders count="2"><border/>{border}</borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="6">'
              + ''.join(f'<xf fontId="{font}" fillId="{fill}" borderId="{border_id}" numFmtId="0" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" horizontal="{align}" wrapText="1"/></xf>' for font, fill, border_id, align in formats)
              + '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    # 共通の最小OOXMLパッケージを利用し、帳票のシート・書式だけ差し替える。
    from kintai_bihin_xlsx import workbook as base_workbook
    base = base_workbook({'year': overview['fiscalStartYear'], 'month': 2, 'rows': [], 'totals': {}})
    out = BytesIO()
    with ZipFile(BytesIO(base)) as src, ZipFile(out, 'w', ZIP_DEFLATED) as dest:
        for name in src.namelist():
            data = src.read(name)
            if name == 'xl/worksheets/sheet1.xml':
                data = sheet.encode('utf-8')
            elif name == 'xl/styles.xml':
                data = styles.encode('utf-8')
            elif name == 'xl/workbook.xml':
                data = (f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="勤怠通知" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">\'勤怠通知\'!$A$1:$L${last}</definedName></definedNames></workbook>').encode('utf-8')
            dest.writestr(name, data)
    return out.getvalue()


def export(c, fiscal_year, person_id):
    import kintai
    if not person_id:
        raise ValueError('対象者を選択してください。')
    if not 2000 <= fiscal_year <= 2100:
        raise ValueError('年度を確認してください。')
    overview = kintai.list_overview(c, fiscal_year, person_id=person_id)
    name = f"勤怠通知_{overview['rows'][0]['displayName']}_{fiscal_year}年度.xlsx"
    return 200, workbook(overview), XLSX, {'Content-Disposition': content_disposition(name)}
