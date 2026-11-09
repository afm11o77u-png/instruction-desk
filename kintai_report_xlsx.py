"""個人向け勤怠通知。年度集計と選択月の日別をA4縦1枚に出力する。"""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

from kintai_bihin_xlsx import XLSX, content_disposition, _col

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def workbook(overview, daily):
    person = overview['rows'][0]
    rows, merges = [], []

    def row(values, style=0, height=14):
        n = len(rows) + 1
        cells = []
        for i, value in enumerate(values):
            ref = f'{_col(i)}{n}'
            if value is None or value == '':
                cells.append(f'<c r="{ref}" s="{style}"/>')
            elif isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        rows.append(f'<row r="{n}" ht="{height}" customHeight="1">{"".join(cells)}</row>')
        return n

    def line(text, style=2, height=18):
        n = row([text], style, height)
        merges.append(f'A{n}:I{n}')

    line('勤怠状況のお知らせ', 3, 26)
    line(f"{person['displayName']} 様（社員番号 {person['employeeCode']}）　{overview['fiscalLabel']}")
    line(f"月別：保存済み集計／最終取込 {person.get('lastImportAt') or '未取得'}", 2, 15)
    row(['月', '出勤日数', '休日数', '休日累計', '休日残数', '有休取得', '有休累計', '普通時間', '残業時間'], 1, 22)
    for m in person['months']:
        row([f"{m['year']}/{m['month']:02d}"] + [m.get(k) for k in (
            'workDays', 'holidayDays', 'holidayCumulative', 'holidayRemaining',
            'paidLeaveDays', 'paidLeaveCumulative', 'ordinaryHm', 'overtimeHm')])
    hold = next((m for m in person['months'] if m.get('paidLeaveReadBalance') is not None), {})
    balance = hold.get('paidLeaveReadBalance')
    line(f"有休現保有数：{balance if balance is not None else '未取得'} 日　取得日：{hold.get('paidLeaveAcquiredOn') or '未取得'}", 2)
    line(f"日別明細　{daily['year']}年{daily['month']}月分　{daily.get('periodStart', '')}～{daily.get('periodEnd', '')}", 2)
    # 元シートの勤務・休暇列を保持。合計や備考の長文は印刷明細から除く。
    columns = [c for c in daily['columns'] if c['key'] not in ('total', 'note', 'remarks', 'workDays', 'breakTime')]
    if len(columns) > 8:
        raise ValueError('日別の列数がA4通知の上限を超えています。レイアウトを確認してください。')
    columns += [{'key': 'workDays', 'label': '出勤日数'}]
    row([c['label'] for c in columns], 1, 21)
    if len(daily['rows']) > 31:
        raise ValueError('日別データが31行を超えています。重複日付などを確認してください。')
    for d in daily['rows']:
        row([d.get(c['key']) for c in columns], 0, 13)
    line('空欄は未取得・未確定。日別は元出勤簿の現在値と保存済み出勤日数（休憩欄は省略）。', 2, 15)
    line('月別は保存済み集計のため、日別変更後は反映・再取込により差が生じる場合があります。', 2, 15)
    last = len(rows)
    widths = [12, 9, 9, 9, 9, 9, 9, 11, 11]
    sheet = (f'<worksheet xmlns="{NS}"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
             '<sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>'
             '<cols>' + ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)) + '</cols>'
             f'<sheetData>{"".join(rows)}</sheetData><mergeCells>' + ''.join(f'<mergeCell ref="{m}"/>' for m in merges) + '</mergeCells>'
             '<pageMargins left="0.25" right="0.25" top="0.3" bottom="0.3" header="0.1" footer="0.1"/>'
             '<pageSetup paperSize="9" orientation="portrait" fitToWidth="1" fitToHeight="1"/></worksheet>')
    fonts = ''.join(f'<font>{"<b/>" if bold else ""}<sz val="{size}"/><name val="Yu Gothic"/></font>' for size, bold in [(8, False), (8, True), (9, False), (16, True)])
    styles = (f'<styleSheet xmlns="{NS}"><fonts count="4">{fonts}</fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE6EFEB"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="2"><border/><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="4">'
              + ''.join(f'<xf fontId="{i}" fillId="{2 if i == 1 else 0}" borderId="{1 if i < 2 else 0}" numFmtId="0" xfId="0" applyAlignment="1"><alignment vertical="center" horizontal="{ "center" if i < 2 else "left"}" wrapText="1"/></xf>' for i in range(4))
              + '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    # 共通の最小OOXMLパッケージを利用し、帳票のシート・書式だけ差し替える。
    from kintai_bihin_xlsx import workbook as base_workbook
    base = base_workbook({'year': daily['year'], 'month': daily['month'], 'rows': [], 'totals': {}})
    out = BytesIO()
    with ZipFile(BytesIO(base)) as src, ZipFile(out, 'w', ZIP_DEFLATED) as dest:
        for name in src.namelist():
            data = src.read(name)
            if name == 'xl/worksheets/sheet1.xml':
                data = sheet.encode('utf-8')
            elif name == 'xl/styles.xml':
                data = styles.encode('utf-8')
            elif name == 'xl/workbook.xml':
                data = (f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="勤怠通知" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">\'勤怠通知\'!$A$1:$I${last}</definedName></definedNames></workbook>').encode('utf-8')
            dest.writestr(name, data)
    return out.getvalue()


def export(c, fiscal_year, person_id, ym):
    import kintai
    if not person_id or not ym:
        raise ValueError('対象者と日別の対象月を選択してください。')
    if ym not in kintai.fiscal_months(fiscal_year, 2):
        raise ValueError('日別の対象月を選択年度の範囲内にしてください。')
    overview = kintai.list_overview(c, fiscal_year, person_id=person_id)
    daily = kintai.person_daily_sheet(c, person_id, *ym)
    name = f"勤怠通知_{overview['rows'][0]['displayName']}_{ym[0]}-{ym[1]:02d}.xlsx"
    return 200, workbook(overview, daily), XLSX, {'Content-Disposition': content_disposition(name)}
