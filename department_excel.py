"""Read department candidates from the supplied customer-master XLSX layout."""
import base64
import io
import posixpath
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value or '')).split())


def preview(raw,existing,source="customer"):
    if source not in ("customer","contract"):raise ValueError("取り込みの書式を選んでください。")
    required=("顧客コード","部署名") if source=="contract" else ("顧客コード","顧客名")
    label="契約" if source=="contract" else "顧客"
    try:
        if not isinstance(raw,str) or len(raw)>11_000_000:raise ValueError()
        blob=base64.b64decode(raw,validate=True)
        if len(blob)>8_000_000:raise ValueError()
        with ZipFile(io.BytesIO(blob)) as book:
            if len(book.infolist())>5000 or sum(x.file_size for x in book.infolist())>40_000_000:raise ValueError()
            workbook=ET.fromstring(book.read('xl/workbook.xml'));rels=ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
            targets={r.get('Id'):r.get('Target') for r in rels}
            shared=[]
            if 'xl/sharedStrings.xml' in book.namelist():
                shared=[''.join(t.text or '' for t in x.iter(NS+'t')) for x in ET.fromstring(book.read('xl/sharedStrings.xml'))]
            sheets=[]
            for sheet in workbook.findall(NS+'sheets/'+NS+'sheet'):
                target=targets.get(sheet.get(REL+'id'),'')
                path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                if not path.startswith('xl/'):raise ValueError()
                table=[]
                for row in ET.fromstring(book.read(path)).findall(NS+'sheetData/'+NS+'row'):
                    cells={}
                    for cell in row.findall(NS+'c'):
                        column=''.join(c for c in cell.get('r','') if c.isalpha());kind=cell.get('t');value=cell.findtext(NS+'v','')
                        if kind=='s':value=shared[int(value)]
                        elif kind=='inlineStr':value=''.join(t.text or '' for t in cell.iter(NS+'t'))
                        if cell.find(NS+'f') is not None:value='' # Never use formulas as names/codes.
                        cells[column]=value
                    if any(str(v).strip() for v in cells.values()):table.append((int(row.get('r','0')),cells))
                if table:
                    header={normalized(v):col for col,v in table[0][1].items()}
                    if all(key in header for key in required):sheets.append((sheet.get('name'),table,header))
    except Exception as error:
        raise ValueError(label+'マスタのExcel（.xlsx）を読み取れませんでした。ファイルの形式・容量を確認してください。') from error
    if len(sheets)!=1:raise ValueError('「'+'」「'.join(required)+'」のあるシートを1つ含むExcelを選んでください。')
    sheet,table,header=sheets[0];known={normalized(d['name']).casefold() for d in existing};groups={};unresolved=[];deleted=0;count=0
    for row,cells in table[1:]:
        count+=1
        if count>20000:raise ValueError('取り込みは2万行以内のExcelで行ってください。')
        if normalized(cells.get(header.get('削除フラグ',''),'')).casefold() in ('1','true','削除'):
            deleted+=1;continue
        code=normalized(cells.get(header['顧客コード'],''));customer=normalized(cells.get(header.get('顧客名',''),''))
        direct=header.get('部署名') or header.get('部署');name=normalized(cells.get(direct,'')) if direct else (customer.split(' ',1)[1] if ' ' in customer else '')
        if not code or (source=="customer" and not customer) or not name or len(name)>80:
            unresolved.append(dict(row=row,code=code,customer=customer,name='',selected=False));continue
        key=name.casefold()
        if key not in groups:groups[key]=dict(name=name,rows=[],examples=[],existing=key in known,selected=key not in known)
        item=groups[key];item['rows'].append(row)
        if len(item['examples'])<3:item['examples'].append(dict(code=code,customer=customer))
    if not count:raise ValueError('取り込むデータ行がありません。')
    return dict(sheet=sheet,source_rows=count,deleted=deleted,candidates=list(groups.values()),unresolved=unresolved)
