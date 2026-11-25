"""顧客マスタ（旧システム）の「集配曜日フラグ」から、拠点の配送方法・集配曜日をまとめて登録する。

フラグは8桁：先頭の9＋月火水木金土日の7桁（1＝集配日）。例 91001000＝月・木。
- 曜日あり → 自社集配＋その曜日
- 曜日なし（90000000・0・空）で集配サイクルも空 → 福山便（曜日なし）
- 曜日なしで集配サイクルあり → 変更しない（要確認）
- それ以外の値 → 読み飛ばし
拠点は 営業所コード＝顧客コード、なければ 拠点名＝顧客名の店名部分（部署の取り込みと同じ照合）で探す。
新しい拠点は作らない。プレビューでは保存せず、確定時に同じファイルで照合し直して保存する。
"""
import base64
import csv
import io
import json

import department_excel as excel
import web_request_organizations as orgs

DAYS = ('月', '火', '水', '木', '金', '土', '日')
MAX_ROWS = 20000


def parse_flag(value):
    """('days', [...]) / ('none', []) / ('invalid', None)。"""
    text = excel.normalized(value)
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    if text in ('', '0'):
        return 'none', []
    if len(text) != 8 or not text.isdigit() or text[0] != '9' or any(ch not in '01' for ch in text[1:]):
        return 'invalid', None
    days = [d for d, ch in zip(DAYS, text[1:]) if ch == '1']
    return ('days', days) if days else ('none', [])


def outcome(flag, cycle):
    """1行の取り込み結果：('set', method, days) / ('review', reason) / ('skip', reason)。"""
    kind, days = parse_flag(flag)
    if kind == 'invalid':
        return ('skip', '集配曜日フラグの形式が違います')
    if kind == 'days':
        return ('set', '自社集配', days)
    if excel.normalized(cycle):
        return ('review', '曜日がないのに集配サイクルがあります')
    return ('set', '福山便', [])


def _decode_csv(blob):
    for enc in ('utf-8-sig', 'cp932'):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError()


def read_rows(raw, filename=''):
    """base64のxlsx/csvから [{'row','code','customer','flag','cycle'}] を返す。"""
    try:
        if not isinstance(raw, str) or len(raw) > 11_000_000:
            raise ValueError()
        blob = base64.b64decode(raw, validate=True)
        if len(blob) > 8_000_000:
            raise ValueError()
    except Exception as error:
        raise ValueError('ファイルを読み取れませんでした。形式・容量を確認してください。') from error
    required = ('顧客コード', '集配曜日フラグ')
    if blob[:2] == b'PK':
        try:
            table = _xlsx_table(blob, required)
        except ValueError:
            raise
        except Exception as error:
            raise ValueError('Excel（.xlsx）を読み取れませんでした。ファイルの形式・容量を確認してください。') from error
    else:
        if not str(filename).lower().endswith('.csv'):
            raise ValueError('Excel（.xlsx）またはCSVを選んでください。')
        try:
            lines = list(csv.reader(io.StringIO(_decode_csv(blob))))
        except Exception as error:
            raise ValueError('CSVを読み取れませんでした。文字コード（UTF-8・Shift_JIS）を確認してください。') from error
        if not lines:
            raise ValueError('CSVが空です。')
        header = [excel.normalized(h) for h in lines[0]]
        if not all(k in header for k in required):
            raise ValueError('「顧客コード」「集配曜日フラグ」の列があるファイルを選んでください。')
        idx = {h: i for i, h in reversed(list(enumerate(header)))}
        table = []
        for n, line in enumerate(lines[1:], start=2):
            get = lambda k: line[idx[k]] if k in idx and idx[k] < len(line) else ''
            if any(str(v).strip() for v in line):
                table.append((n, {k: get(k) for k in ('顧客コード', '顧客名', '集配曜日フラグ', '集配サイクル', '削除フラグ')}))
    if len(table) > MAX_ROWS:
        raise ValueError('取り込みは2万行以内のファイルで行ってください。')
    rows = []
    for n, cells in table:
        if excel.normalized(cells.get('削除フラグ')).casefold() in ('1', 'true', '削除'):
            continue
        rows.append(dict(row=n, code=excel.normalized(cells.get('顧客コード')), customer=excel.normalized(cells.get('顧客名')),
                         flag=excel.normalized(cells.get('集配曜日フラグ')), cycle=excel.normalized(cells.get('集配サイクル'))))
    return rows


def _xlsx_table(blob, required):
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    import posixpath
    NS, REL = excel.NS, excel.REL
    found = []
    with ZipFile(io.BytesIO(blob)) as book:
        if len(book.infolist()) > 5000 or sum(x.file_size for x in book.infolist()) > 40_000_000:
            raise ValueError('Excelの容量が大きすぎます。')
        workbook = ET.fromstring(book.read('xl/workbook.xml'))
        rels = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        targets = {r.get('Id'): r.get('Target') for r in rels}
        shared = []
        if 'xl/sharedStrings.xml' in book.namelist():
            shared = [''.join(t.text or '' for t in x.iter(NS + 't')) for x in ET.fromstring(book.read('xl/sharedStrings.xml'))]
        for sheet in workbook.findall(NS + 'sheets/' + NS + 'sheet'):
            target = targets.get(sheet.get(REL + 'id'), '')
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            if not path.startswith('xl/'):
                raise ValueError('Excelの形式を確認してください。')
            table = []
            for row in ET.fromstring(book.read(path)).findall(NS + 'sheetData/' + NS + 'row'):
                cells = {}
                for cell in row.findall(NS + 'c'):
                    column = ''.join(ch for ch in cell.get('r', '') if ch.isalpha())
                    kind = cell.get('t')
                    value = cell.findtext(NS + 'v', '')
                    if kind == 's':
                        value = shared[int(value)]
                    elif kind == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.iter(NS + 't'))
                    if cell.find(NS + 'f') is not None:
                        value = ''
                    cells[column] = value
                if any(str(v).strip() for v in cells.values()):
                    table.append((int(row.get('r', '0')), cells))
            if table:
                header = {}
                for col, v in table[0][1].items():
                    header.setdefault(excel.normalized(v), col)
                if all(k in header for k in required):
                    found.append([(n, {k: cells.get(header[k], '') for k in ('顧客コード', '顧客名', '集配曜日フラグ', '集配サイクル', '削除フラグ') if k in header}) for n, cells in table[1:]])
    if len(found) != 1:
        raise ValueError('「顧客コード」「集配曜日フラグ」の列があるシートを1つ含むExcelを選んでください。')
    return found[0]


def _key(value):
    return excel.normalized(value).casefold()


def match_node(row, sites):
    """(node, how) / (None, reason)。営業所コード＝顧客コード → 拠点名＝顧客名の店名部分。"""
    code = _key(row['code'])
    hits = [n for n in sites if code and _key(n.get('office_code')) == code]
    if len(hits) == 1:
        return hits[0], 'コード一致'
    if len(hits) > 1:
        return None, '同じ営業所コードの拠点が複数あります'
    site = _key(row['customer'].split(' ', 1)[0]) if row['customer'] else ''
    hits = [n for n in sites if site and _key(n['name']) == site]
    if len(hits) == 1:
        return hits[0], '拠点名一致'
    if len(hits) > 1:
        return None, '同じ名前の拠点が複数あります'
    return None, '一致する拠点がありません'


def describe(method, days):
    method = method or '未設定'
    return method + ('（' + '・'.join(days) + '）' if method == '自社集配' and days else '')


def plan(rows, org):
    """取り込み計画。保存はしない。"""
    nodes = org.get('nodes', [])
    byid = {n['id']: n for n in nodes}
    sites = [n for n in nodes if n.get('kind') == 'site']

    def path(n):
        names = []
        while n:
            names.append(n['name'])
            n = byid.get(n.get('parent_id'))
        return ' / '.join(reversed(names))

    skipped, review, unmatched = [], [], []
    targets = {}
    held = set()
    for row in rows:
        base = dict(row=row['row'], code=row['code'], customer=row['customer'], flag=row['flag'], cycle=row['cycle'])
        result = outcome(row['flag'], row['cycle'])
        if result[0] == 'skip':
            skipped.append(dict(base, reason=result[1]))
            continue
        node, how = match_node(row, sites)
        if result[0] == 'review':
            review.append(dict(base, reason=result[1], site=path(node) if node else ''))
            if node:
                held.add(node['id'])
            continue
        if not node:
            unmatched.append(dict(base, reason=how))
            continue
        targets.setdefault(node['id'], []).append((base, how, result[1], result[2]))
    changes, unchanged = [], 0
    for nid, items in targets.items():
        node = byid[nid]
        wanted = {(m, tuple(d)) for _, _, m, d in items}
        codes = [b['code'] for b, *_ in items]
        if len(wanted) > 1 or nid in held:
            reason = '同じ拠点で集配曜日が食い違っています' if len(wanted) > 1 else '同じ拠点に要確認の行があるため変更しません'
            for b, *_ in items:
                review.append(dict(b, reason=reason, site=path(node)))
            continue
        method, days = next(iter(wanted))
        before_method = node.get('delivery_method', '') or ''
        before_days = [d for d in DAYS if d in (node.get('collection_days') or [])] if before_method == '自社集配' else []
        entry = dict(node_id=nid, site=path(node), codes=codes, customers=sorted({b['customer'] for b, *_ in items}),
                     match=items[0][1], before=describe(before_method, before_days), after=describe(method, list(days)),
                     delivery_method=method, collection_days=list(days))
        if before_method == method and before_days == list(days):
            unchanged += 1
            continue
        changes.append(entry)
    changes.sort(key=lambda x: x['site'])
    counts = dict(rows=len(rows), matched_sites=len(changes) + unchanged, changes=len(changes), unchanged=unchanged,
                  unmatched=len(unmatched), review=len(review), skipped=len(skipped))
    return dict(changes=changes, unmatched=unmatched, review=review, skipped=skipped, counts=counts,
                organization_version=org.get('version'))


def apply_changes(org, changes):
    payload = json.loads(json.dumps({k: v for k, v in org.items() if k != 'version'}, ensure_ascii=False))
    byid = {n['id']: n for n in payload['nodes']}
    for ch in changes:
        node = byid[ch['node_id']]
        node['delivery_method'] = ch['delivery_method']
        node['collection_days'] = list(ch['collection_days'])
    return payload


def handle(c, d, data, user):
    """action: collection_days_import_preview / collection_days_import"""
    if not orgs.admin(user) or user is None:
        raise PermissionError('集配曜日の取り込みは主管理者・管理者が行ってください。')
    rows = read_rows(d.get('file'), d.get('filename', ''))
    org = orgs.read(c)
    result = plan(rows, org)
    if d.get('action') == 'collection_days_import_preview':
        return result
    if d.get('confirm') is not True:
        raise ValueError('プレビューを確認してから取り込んでください。')
    if type(d.get('organization_version')) is not int or d['organization_version'] != org['version']:
        raise ValueError('組織構成が更新されています。ファイルを選び直してプレビューからやり直してください。')
    expected = d.get('expected_changes')
    if expected != len(result['changes']):
        raise ValueError('プレビューと内容が変わりました。ファイルを選び直してください。')
    if not result['changes']:
        return dict(result, saved=0)
    payload = apply_changes(org, result['changes'])
    orgs.save(c, payload, org['version'], {x['id'] for x in data['customers']},
              {x['id'] for x in data.get('organizationStaff', data['staff'])}, user, backup=True)
    return dict(result, saved=len(result['changes']))
