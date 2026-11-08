"""商品取り込み時の品番の形の確認。

通常ルールの対象は、ミドリ安全の制服を除く商品。非ミドリ安全の制服は落とさない。
既存の品番のうしろに英数字以外の文字が付いた候補だけを、品番のみへまとめる確認にする。
自動ではまとめない。AZ-8618-003 のような英数字の枝番は剥がさない。
短い品番がファイルにも既存マスタにも無いときは、品番を推測せず確認対象にする。

ミドリ安全（仕入先名にこの文字列を含む）の制服:
上または下を含む品番は、末尾かどうかにかかわらずすべて確認対象にする。
末尾の上・下で基本品番が切れるものだけ、同じ基本品番の上下なしも同じ確認に入れる。
途中に上または下がある正式品番は、自動ではまとめず、その品番のまま登録する確認にする。
上と下を同じ商品へ自動では潰さない。

確定した対応は code_shape_links に残す。初期在庫と旧QRを同時に取り込む処理は未実装。
"""
from __future__ import annotations

import re
import unicodedata

import stock
import stock_product_merge

MIDORI_MARK = 'ミドリ安全'
JOINT_IMPORT_NOTE = (
    '初期在庫と旧QRの一体取り込みは今回未実装です。'
    '確定した旧品番から登録先品番への対応だけを残します。'
)
_FORMAL_EXTRA = re.compile(r'(?:[-_－][0-9A-Za-z]+|[0-9A-Za-z]+)\Z')
_VERTICAL_TAIL = re.compile(r'[\s\-－_]*([上下])\Z')


def ensure_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS code_shape_links (
      id INTEGER PRIMARY KEY,
      source_code TEXT NOT NULL,
      target_code TEXT NOT NULL,
      supplier_scope TEXT NOT NULL,
      group_kind TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(source_code, supplier_scope)
    )''')


def is_midori_supplier(name):
    text = unicodedata.normalize('NFKC', stock.text(name))
    return MIDORI_MARK in text


def _uniformish(category):
    cat = stock.text(category)
    return cat not in (stock.CATEGORY_PARTS, stock.CATEGORY_PROCESS_SERVICE)


def _norm_code(code):
    return unicodedata.normalize('NFKC', stock.text(code))


def vertical_split(code):
    """末尾の上または下だけを外す。途中の上下や、外した残りが空なら確定しない。"""
    text = _norm_code(code)
    matched = _VERTICAL_TAIL.search(text)
    if not matched:
        return '', ''
    base = text[:matched.start()].strip(' -_－')
    if not base or '上' in base or '下' in base:
        return '', ''
    return base, matched.group(1)


def _has_nonformal_char(extra):
    return any(not (ch.isascii() and (ch.isalnum() or ch in '-_－')) for ch in extra)


def _known_codes(present):
    return {_norm_code(code) for code in present if _norm_code(code)}


def extension_of(code, known):
    """known にある最長の品番＋非公式の追加文字なら (base, extra)。無ければ ('', '')。"""
    text = _norm_code(code)
    if not text:
        return '', ''
    best = ''
    for base in known:
        if base and text.startswith(base) and len(text) > len(base) and len(base) > len(best):
            best = base
    if not best:
        return '', ''
    extra = text[len(best):]
    if _FORMAL_EXTRA.fullmatch(extra) or not _has_nonformal_char(extra):
        return '', ''
    return best, extra


def _row_info(row):
    csv = row.get('csv') or {}
    product = row.get('product') or {}
    code = _norm_code(csv.get('sourceCode') or csv.get('code') or product.get('sourceCode') or product.get('code') or '')
    if not stock_product_merge.is_normal_product_code(code):
        return None
    if row.get('status') in ('excluded',):
        return None
    supplier = stock.text(csv.get('supplier') or product.get('primarySupplierName') or '')
    category = stock.text(product.get('category') or product.get('proposedCategory') or '')
    color = stock.text(csv.get('color') or product.get('color') or '')
    name = stock.text(csv.get('productName') or product.get('name') or '')
    size = stock.text(csv.get('size') or product.get('size') or '')
    return {
        'rowNo': row.get('rowNo'),
        'code': code,
        'supplier': supplier,
        'category': category,
        'color': color,
        'name': name,
        'size': size,
        'midoriUniform': is_midori_supplier(supplier) and _uniformish(category),
    }


def _existing_products(c):
    ensure_schema(c)
    products = []
    rows = c.execute(
        '''SELECT p.id, p.code, p.name, p.color, p.category, p.active,
                  s.name AS supplier_name
           FROM products p
           LEFT JOIN suppliers s ON s.id = p.supplier_id
           WHERE p.merged_into_id IS NULL'''
    ).fetchall()
    sizes = {}
    if rows:
        for size_row in c.execute('SELECT product_id, label FROM product_sizes ORDER BY sort_order, id'):
            sizes.setdefault(size_row['product_id'], []).append(size_row['label'])
    for row in rows:
        products.append({
            'id': row['id'],
            'code': _norm_code(row['code']),
            'name': row['name'] or '',
            'color': row['color'] or '',
            'category': row['category'] or '',
            'supplier': row['supplier_name'] or '',
            'active': bool(row['active']),
            'sizes': list(sizes.get(row['id']) or []),
        })
    return products


def _saved_links(c):
    ensure_schema(c)
    found = {}
    for row in c.execute('SELECT source_code, target_code, supplier_scope, group_kind FROM code_shape_links'):
        found[(row['source_code'], row['supplier_scope'])] = {
            'targetCode': row['target_code'],
            'groupKind': row['group_kind'],
        }
    return found


def _member(code, role, infos, existing):
    names, suppliers, colors, sizes, row_nos = [], [], [], [], []
    for info in infos:
        if info['name'] and info['name'] not in names:
            names.append(info['name'])
        if info['supplier'] and info['supplier'] not in suppliers:
            suppliers.append(info['supplier'])
        if info['color'] and info['color'] not in colors:
            colors.append(info['color'])
        if info['size'] and info['size'] not in sizes:
            sizes.append(info['size'])
        if info['rowNo'] is not None:
            row_nos.append(info['rowNo'])
    prior = [item for item in existing if item['code'] == code]
    for item in prior:
        for label in item['sizes']:
            if label not in sizes:
                sizes.append(label)
    return {
        'code': code,
        'role': role,
        'names': names,
        'suppliers': suppliers,
        'colors': colors,
        'sizes': sizes,
        'rowNos': row_nos,
        'existing': [
            {
                'id': item['id'],
                'name': item['name'],
                'color': item['color'],
                'category': item['category'],
                'supplier': item['supplier'],
                'sizes': item['sizes'],
                'active': item['active'],
            }
            for item in prior
        ],
    }


def _union_sizes(members):
    labels = []
    for member in members:
        for label in member['sizes']:
            if label not in labels:
                labels.append(label)
    return labels


def build_groups(c, classified):
    """確認群。決定は含めない。自動では登録先を変えない。"""
    ensure_schema(c)
    infos = []
    for row in classified or []:
        info = _row_info(row)
        if info:
            infos.append(info)
    existing = _existing_products(c)
    saved = _saved_links(c)
    by_code = {}
    kept = []
    for info in infos:
        import code_suffix_attributes as suffix_attr
        attr = suffix_attr.classify_suffix(
            info['code'], product_name=info['name'], supplier=info['supplier'], size=info['size'],
        )
        if attr.get('kind') == 'midori_position' or (info['midoriUniform'] and ('上' in info['code'] or '下' in info['code'])):
            kept.append(info)
            continue
        if attr.get('apply') or attr.get('registerAsParts') or attr.get('needsConfirm'):
            continue
        kept.append(info)
    infos = kept
    for info in infos:
        by_code.setdefault(info['code'], []).append(info)
    present = set(by_code) | {item['code'] for item in existing if item['code']}

    used = set()
    groups = []

    midori_buckets = {}
    for code, rows in by_code.items():
        if not any(row['midoriUniform'] for row in rows):
            continue
        base, token = vertical_split(code)
        if token:
            midori_buckets.setdefault(base, {'tokens': set(), 'codes': set()})
            midori_buckets[base]['tokens'].add(token)
            midori_buckets[base]['codes'].add(code)
            used.add(code)
        elif ('上' in code or '下' in code):
            # 末尾以外の上・下。正式品番として残し、自動ではまとめない。
            groups.append(_undetermined_group(
                code, rows, existing, saved, 'midori', embedded_mark=True,
            ))
            used.add(code)
    for base, bucket in midori_buckets.items():
        plain_rows = [
            row for row in by_code.get(base, [])
            if row['midoriUniform']
        ]
        if plain_rows or any(item['code'] == base and is_midori_supplier(item['supplier']) and _uniformish(item['category']) for item in existing):
            bucket['codes'].add(base)
            used.add(base)
        members = []
        for code in sorted(bucket['codes']):
            role = 'plain' if code == base else ('upper' if vertical_split(code)[1] == '上' else 'lower')
            members.append(_member(code, role, by_code.get(code, []), existing))
        if not members:
            continue
        groups.append({
            'id': 'midori:' + base,
            'kind': 'midori_vertical',
            'baseCode': base,
            'supplierScope': 'midori',
            'note': (
                'ミドリ安全の制服です。上または下を含む品番を確認します。'
                '末尾の上・下で基本品番が分かるときは、上下なしの品番も同じ確認に入れます。'
                '上と下を同じ商品へ自動ではまとめません。登録先を選んでください。'
            ),
            'previousLinks': [
                {'sourceCode': code, **saved[(code, 'midori')]}
                for code in sorted(bucket['codes'])
                if (code, 'midori') in saved
            ],
            'members': members,
            'sizeUnion': _union_sizes(members),
            'jointImport': JOINT_IMPORT_NOTE,
        })

    known_for_extend = {code for code in present if code not in used or code in by_code}
    extension_buckets = {}
    for code, rows in by_code.items():
        if code in used:
            continue
        # 通常ルールはミドリ安全の制服だけを除く。非ミドリ安全の制服はここで確認する。
        if any(row['midoriUniform'] for row in rows):
            continue
        base, extra = extension_of(code, known_for_extend - {code})
        if base:
            extension_buckets.setdefault(base, []).append(code)
            used.add(code)
        elif _has_nonformal_char(code):
            groups.append(_undetermined_group(code, rows, existing, saved, 'other'))
            used.add(code)
    for base, extras in extension_buckets.items():
        codes = [base] + sorted(extras)
        if base in by_code or any(item['code'] == base for item in existing):
            pass
        members = []
        if base in by_code or any(item['code'] == base for item in existing):
            members.append(_member(base, 'base', by_code.get(base, []), existing))
        for code in sorted(extras):
            members.append(_member(code, 'extension', by_code.get(code, []), existing))
        groups.append({
            'id': 'extend:' + base,
            'kind': 'code_extension',
            'baseCode': base,
            'supplierScope': 'other',
            'note': (
                f'品番「{base}」に文字が追加された候補です。'
                '品番のみへまとめるかは確認が必要です。自動ではまとめません。'
                '英数字だけの枝番は対象にしません。'
            ),
            'previousLinks': [
                {'sourceCode': code, **saved[(code, 'other')]}
                for code in codes
                if (code, 'other') in saved
            ],
            'members': members,
            'sizeUnion': _union_sizes(members),
            'jointImport': JOINT_IMPORT_NOTE,
        })
    groups.sort(key=lambda group: group['id'])
    return groups


def _undetermined_group(code, rows, existing, saved, scope, embedded_mark=False):
    member = _member(code, 'undetermined', rows, existing)
    if embedded_mark:
        note = (
            f'品番「{code}」は途中に上または下があります。'
            '正式品番として自動ではまとめず、この品番のまま登録する確認です。'
            '英数字の枝番は切り落としません。'
        )
    else:
        note = (
            f'品番「{code}」は追加文字があるように見えますが、元の品番を確定できません。'
            '推測で短くしません。この品番のまま登録するか、今回の登録から外してください。'
        )
    return {
        'id': f'unknown:{scope}:{code}',
        'kind': 'undetermined',
        'baseCode': '',
        'supplierScope': scope,
        'note': note,
        'previousLinks': [saved[(code, scope)]] if (code, scope) in saved else [],
        'members': [member],
        'sizeUnion': list(member['sizes']),
        'jointImport': JOINT_IMPORT_NOTE,
    }


def _decision_map(raw):
    if not isinstance(raw, dict):
        return {}
    return raw


def assess(groups, decisions):
    """各群が決定済みか。未決定や範囲外の登録先は resolved=False。"""
    raw = _decision_map(decisions)
    assessed = []
    for group in groups:
        decision = raw.get(group['id']) if isinstance(raw.get(group['id']), dict) else {}
        action = stock.text(decision.get('action'))
        targets = decision.get('targets') if isinstance(decision.get('targets'), dict) else {}
        member_codes = [member['code'] for member in group['members']]
        resolved = False
        normalized = {'action': action, 'targets': {}}
        error = ''
        if group['kind'] == 'code_extension':
            if action == 'keep_separate':
                resolved = True
            elif action == 'merge_to_base' and group.get('baseCode') in member_codes:
                resolved = True
                for code in member_codes:
                    normalized['targets'][code] = group['baseCode']
            elif action:
                error = 'この確認では、品番のみへまとめるか、別々のままにするかを選んでください。'
        elif group['kind'] == 'undetermined':
            if action == 'keep_separate':
                resolved = True
            elif action:
                error = '元の品番が確定しないため、この品番のまま登録する以外は選べません。'
        elif group['kind'] == 'midori_vertical':
            if action == 'choose':
                ok = True
                chosen = {}
                for code in member_codes:
                    target = _norm_code(targets.get(code))
                    if target not in member_codes:
                        ok = False
                        error = f'品番「{code}」の登録先は、この確認に出ている品番から選んでください。'
                        break
                    chosen[code] = target
                resolved = ok
                normalized['targets'] = chosen
            elif action:
                error = 'ミドリ安全の上下は、品番ごとに登録先を選んでください。'
        assessed.append({
            **group,
            'resolved': resolved,
            'decision': normalized if resolved else None,
            'decisionError': error,
        })
    return assessed


def require_resolved(groups, decisions):
    assessed = assess(groups, decisions)
    pending = [group for group in assessed if not group['resolved']]
    if pending:
        sample = '、'.join(group['id'] for group in pending[:5])
        raise ValueError(
            f'品番の形の確認が {len(pending)} 件未決定です（{sample}）。'
            '自動では登録先を決めません。確認を選んでから登録してください。'
        )
    return assessed


def row_overrides(groups, decisions, classified):
    """決定済みのとき、行番号 → 登録する品番。別々のままは上書きしない。"""
    assessed = require_resolved(groups, decisions)
    by_source = {}
    for group in assessed:
        decision = group['decision'] or {}
        if group['kind'] == 'code_extension' and decision.get('action') == 'merge_to_base':
            for code, target in (decision.get('targets') or {}).items():
                if code != target:
                    by_source[(group['supplierScope'], code)] = target
        elif group['kind'] == 'midori_vertical':
            for code, target in (decision.get('targets') or {}).items():
                if code != target:
                    by_source[(group['supplierScope'], code)] = target
    overrides = {}
    for row in classified or []:
        info = _row_info(row)
        if not info or info['rowNo'] is None:
            continue
        scope = 'midori' if info['midoriUniform'] else 'other'
        target = by_source.get((scope, info['code']))
        if target:
            overrides[info['rowNo']] = target
    return overrides


def save_links(c, groups, decisions):
    """確定した対応を残す。初期在庫・旧QRの同時取り込みはここからは呼ばない。"""
    ensure_schema(c)
    assessed = require_resolved(groups, decisions)
    when = stock.stamp()
    saved = 0
    for group in assessed:
        decision = group['decision'] or {}
        targets = decision.get('targets') or {}
        if group['kind'] == 'code_extension' and decision.get('action') != 'merge_to_base':
            targets = {member['code']: member['code'] for member in group['members']}
        if group['kind'] == 'undetermined':
            targets = {member['code']: member['code'] for member in group['members']}
        for source, target in targets.items():
            c.execute(
                '''INSERT INTO code_shape_links(source_code, target_code, supplier_scope, group_kind, created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(source_code, supplier_scope) DO UPDATE SET
                     target_code=excluded.target_code,
                     group_kind=excluded.group_kind,
                     created_at=excluded.created_at''',
                (source, target, group['supplierScope'], group['kind'], when),
            )
            saved += 1
    return saved
