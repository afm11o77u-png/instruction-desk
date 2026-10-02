"""Dependency-free XLSX export; all user text is explicitly stored as text."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

HEADERS=['依頼者・確認先','顧客名','アイテム名','メーカー','品番','サイズ','新古','数量','備考②','実行者','確認','入力','確認']
KEYS=['requester','customer','name','maker','sku','size','condition','quantity','note2']
GROUPS=[('warehouse','石狩倉庫ピッキングリスト'),('factory','発寒支店ピッキングリスト'),('carry','管理担当持込リスト')]
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'

def cell(col,row,value,style=0):
    ref=f'{chr(65+col)}{row}'
    if type(value) is int: return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text=escape(str(value or '')).replace('\n','&#10;')
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'

def workbook(data):
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,4))+'</Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,4))+'<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        sheets=''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i,(_,name) in enumerate(GROUPS,1))
        defined=''.join(f'<definedName name="_xlnm.Print_Titles" localSheetId="{i}">\'{name}\'!$1:$3</definedName>' for i,(_,name) in enumerate(GROUPS))
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets><definedNames>{defined}</definedNames></workbook>')
        z.writestr('xl/styles.xml',f'''<styleSheet xmlns="{NS}"><fonts count="3"><font><sz val="9"/><name val="Yu Gothic"/></font><font><b/><sz val="9"/><name val="Yu Gothic"/><color rgb="FFFFFFFF"/></font><font><b/><sz val="14"/><name val="Yu Gothic"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF254E5C"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border/><border><left style="thin"><color rgb="FF555555"/></left><right style="thin"><color rgb="FF555555"/></right><top style="thin"><color rgb="FF555555"/></top><bottom style="thin"><color rgb="FF555555"/></bottom></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
        widths=[14,20,18,12,14,9,11,6,20,8,6,8,6]
        for i,(group,name) in enumerate(GROUPS,1):
            rows=sorted((r for r in data['rows'] if r['stock']==group),key=lambda r:(r['customer'],r['requester'],r['name']))
            sheetrows=f'<row r="1" ht="25" customHeight="1">{cell(0,1,name,2)}</row><row r="2" ht="22" customHeight="1">{cell(0,2,data["number"]+"　"+data["date"]+"　"+data["title"]+f"　{len(rows)}明細",3)}</row>'
            sheetrows+='<row r="3" ht="28" customHeight="1">'+''.join(cell(k,3,v,1) for k,v in enumerate(HEADERS))+'</row>'
            for rn,r in enumerate(rows,4):
                values=[];write=False
                for key in KEYS:
                    if key=='condition':
                        text=str(r.get(key) or '').strip()
                        if group=='warehouse' or text in ('','未指定'):
                            values.append('新　\n中　');write=True
                        else: values.append(text)
                    else: values.append(r.get(key,''))
                ht=' ht="36" customHeight="1"' if write else ''
                sheetrows+=f'<row r="{rn}"{ht}>'+''.join(cell(k,rn,v) for k,v in enumerate(values))+''.join(cell(k,rn,'') for k in range(9,13))+'</row>'
            cols=''.join(f'<col min="{j}" max="{j}" width="{w}" customWidth="1"/>' for j,w in enumerate(widths,1))
            z.writestr(f'xl/worksheets/sheet{i}.xml',f'<worksheet xmlns="{NS}"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><sheetViews><sheetView workbookViewId="0"><pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="32"/><cols>{cols}</cols><sheetData>{sheetrows}</sheetData><autoFilter ref="A3:M{max(3,len(rows)+3)}"/><mergeCells count="2"><mergeCell ref="A1:M1"/><mergeCell ref="A2:M2"/></mergeCells><printOptions horizontalCentered="1"/><pageMargins left="0.25" right="0.25" top="0.4" bottom="0.4" header="0.15" footer="0.15"/><pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/><headerFooter><oddFooter>&amp;C&amp;P / &amp;N</oddFooter></headerFooter></worksheet>')
    return out.getvalue()
