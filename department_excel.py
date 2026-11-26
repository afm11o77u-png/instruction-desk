"""Read department candidates from the supplied customer-master XLSX layout."""
import base64
import io
import posixpath
import re
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value or '')).split())


def preview(raw,existing,source="customer",skip_retired=False):
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
    sheet,table,header=sheets[0];known={normalized(d['name']).casefold() for d in existing};groups={};unresolved=[];deleted=0;retired=0;count=0;records=[]
    for row,cells in table[1:]:
        count+=1
        if count>20000:raise ValueError('取り込みは2万行以内のExcelで行ってください。')
        if normalized(cells.get(header.get('削除フラグ',''),'')).casefold() in ('1','true','削除'):
            deleted+=1;continue
        if skip_retired and '退社フラグ' in header and normalized(cells.get(header['退社フラグ'],'')) not in ('','0'):
            retired+=1;continue  # 退社(1)・異動(2)の行は使わない
        code=normalized(cells.get(header['顧客コード'],''));customer=normalized(cells.get(header.get('顧客名',''),''))
        direct=header.get('部署名') or header.get('部署');site_name=customer.split(' ',1)[0]
        if direct:name=normalized(cells.get(direct,''))
        else:
            name=customer.split(' ',1)[1] if ' ' in customer else ''
            head,_,last=customer.rpartition(' ')
            if head and ' ' in name and last in SITE_DEPARTMENT_SUFFIXES:name,site_name=last,head  # 「シティ あばしり店 管理」→ 店「シティ あばしり店」・部署「管理」
        records.append(dict(row=row,code=code,customer=customer,name=name,site_name=site_name))
        if not code or (source=="customer" and not customer) or not name or len(name)>80:
            unresolved.append(dict(row=row,code=code,customer=customer,name='',selected=False));continue
        key=name.casefold()
        if key not in groups:groups[key]=dict(name=name,rows=[],examples=[],existing=key in known,selected=key not in known)
        item=groups[key];item['rows'].append(row)
        if len(item['examples'])<3:item['examples'].append(dict(code=code,customer=customer))
    if not count:raise ValueError('取り込むデータ行がありません。')
    return dict(sheet=sheet,source_rows=count,deleted=deleted,retired=retired,candidates=list(groups.values()),unresolved=unresolved,records=records)

# The customer master puts these department labels after the location name.
SITE_DEPARTMENT_SUFFIXES = {
    'OS', 'グロサリー', 'デイリー', 'ベーカリー', 'ベーカリー2', '惣菜',
    '管理', '管理SP', '精肉', '貸出', '青果', '鮮魚', '衣料', '官製品',
}


def company_name_key(value):
    import site_name_key
    return site_name_key.company_key(value)


def _strip_company(name, company):
    import site_name_key
    ck = site_name_key.company_key(company)
    nm = normalized(name)
    for i in range(1, len(nm)):
        if site_name_key._plain(nm[:i]) == ck:
            return nm[i:].strip() or nm
    return nm


def preview_sites(raw, company, sites=(), extra_terms=()):
    """会社の拠点候補。sites はその会社に登録済みの拠点（name）。登録済みと同じ拠点は existing、似た拠点は similar に入れる。
    extra_terms：会社名で始まらなくても、顧客名にこの文字を含む行も候補にする（例：北雄ラッキーの「シティ」）。"""
    import site_name_key
    master = preview(raw, [])
    company_key = company_name_key(company)
    if not company_key:
        raise ValueError('会社名を確認してください。')
    sites = [dict(name=n['name'], id=n.get('id')) for n in sites]
    extra_keys = [k for k in (site_name_key._plain(t) for t in extra_terms) if k]
    candidates = {}
    matched = 0
    for record in master['records']:
        customer = record['customer']
        name, _, suffix = customer.rpartition(' ')
        if suffix not in SITE_DEPARTMENT_SUFFIXES:
            name = customer
        keys = site_name_key.site_keys(name, company)
        plain_customer = site_name_key._plain(customer)
        own = (plain_customer.startswith(company_key) or any(k in plain_customer for k in extra_keys)) and site_name_key._plain(name) != company_key
        node, how, similar = site_name_key.find(name, company, sites, lambda n: company) if sites and keys else (None, '', [])
        if not own and not node:
            continue  # 他社の行（登録済みの拠点と同じ名前のときだけ表示する）
        matched += 1
        key = keys[0] if keys else site_name_key._plain(name)
        if node:
            key = 'node:' + str(node['id'] or node['name'])
        candidate = candidates.setdefault(key, dict(name=_strip_company(name, company), count=0, examples=[],
                                                     existing=node['name'] if node else '', similar=[] if node else similar[:3]))
        candidate['count'] += 1
        if customer not in candidate['examples'] and len(candidate['examples']) < 3:
            candidate['examples'].append(customer)
    for c in candidates.values():
        c['checked'] = not c['existing'] and not c['similar']
    if len(candidates) > 500:
        raise ValueError('候補が500件を超えています。対象の会社の行に絞ってください。')
    return dict(candidates=list(candidates.values()), matched=matched,
                source_rows=master['source_rows'], deleted=master['deleted'])
