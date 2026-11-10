"""品番欄の追加文字を属性で分ける。

追加文字があるだけで統合・別商品・要確認にはしない。
色名や注記を外して同じ品番になるものは同じ商品へまとめてよい。
正式な数字ハイフン枝番、部品の品番、ミドリ安全の上下は崩さない。
"""
from __future__ import annotations

import re
import unicodedata

import product_ledger_paren as paren
import product_ledger_processing_service as proc
import stock

_NOTE_KIND = {
    'デリカ': 'usage',
    '農畜水産': 'usage',
    '駐車場警備': 'usage',
    '日用品': 'department',
    '男': 'gender',
    '女': 'gender',
    '男性': 'gender',
    '女性': 'gender',
    '定番': 'spec',
    '別注': 'spec',
    '無地': 'spec',
    '廃番': 'discontinued',
    '使用しない': 'discontinued',
    '不使用': 'discontinued',
    '現在不使用': 'discontinued',
}
_PART_WORDS = (
    'ボタン', '釦', 'ファスナー', 'ジッパー', 'チャック',
    'スナッパ', 'スナッパー', 'Ｄカン', 'Dカン', 'バックル',
    '生地', '芯地', '裏地', '名札', '肩パット', 'パッド', 'ゴム',
)


def is_part_product_name(name):
    t = stock.text(name)
    if not t:
        return False
    return any(w in t for w in _PART_WORDS)
_DIM_RE = re.compile(r'^(\d+(?:\.\d+)?)(mm|㎜|cm|㎝)$', re.I)
_SIZE_WORD = {'大', '小', 'LL', 'L', 'M', 'S', 'XL', '2L', '3L'}
_WRAP_RE = re.compile(r'^(.*?)(?:\s*)([（(【\[]([^】)）\]]*)[】)）\]])\s*$')
_CHANGE_RE = re.compile(r'→|無くなり次第|代替')


def _empty(**extra):
    base = {
        'apply': False,
        'kind': 'plain',
        'baseCode': '',
        'color': '',
        'processingName': '',
        'position': '',
        'note': '',
        'size': '',
        'registerAsParts': False,
        'needsConfirm': False,
        'reason': '',
    }
    base.update(extra)
    return base


def _part_right_name(right, product_name):
    """『品番用＋部品名』の右側だけ。空・括弧片・注記の切れ端は部品にしない。"""
    token = stock.text(right).strip('()（）[]【】 　')
    if len(token) < 2:
        return ''
    if token in _PART_WORDS:
        return token
    name = stock.text(product_name)
    base = re.split(r'[(（【\[]', name, 1)[0]
    base = re.sub(r'[\s\u3000]+', '', base)
    if base and base == token:
        return token
    if name and name == token:
        return token
    return ''


def _is_midori(supplier):
    return 'ミドリ安全' in stock.text(supplier)


def _formal_code(text):
    norm = unicodedata.normalize('NFKC', text or '')
    return bool(re.fullmatch(r'[#＃№0-9A-Za-z][0-9A-Za-z._\-－/／#＃№&+]*', norm))


def _is_color_code(text):
    t = stock.text(text).strip()
    if not t:
        return False
    # C/1, C-10, C-101, C/5, C10, C-1, C1
    if re.fullmatch(r'[Cc][\-_/／]?[0-9]+[A-Za-z]?', t):
        return True
    # Col.1, COL-2, Color 3, COL/1
    if re.fullmatch(r'(?:Col|COL|color|COLOR)[\.:_\-/／]?[0-9]+[A-Za-z]?', t):
        return True

    return False


def _color_of(text):
    hit = paren.classify_paren_segment(stock.text(text))
    if hit and hit.get('kind') == 'color' and paren._norm_token(text) == (hit.get('matched') or ''):
        return stock.text(hit.get('value') or text)
    # 品番末尾に色名が密着している（APK486-33ブラウン）
    raw = stock.text(text)
    for term in sorted(paren.COLOR_TERMS, key=len, reverse=True):
        if raw.endswith(term) and len(raw) > len(term):
            head = raw[:-len(term)]
            if _formal_code(head):
                return term
    for syn, canon in paren.COLOR_SYNONYMS.items():
        if raw.endswith(syn) and len(raw) > len(syn):
            head = raw[:-len(syn)]
            if _formal_code(head):
                return canon
    return ''


def _strip_color_tail(text, color):
    raw = stock.text(text)
    if raw.endswith(color):
        return raw[:-len(color)]
    for syn, canon in paren.COLOR_SYNONYMS.items():
        if canon == color and raw.endswith(syn):
            return raw[:-len(syn)]
    return raw


def _embroidery_suffix_name(text):
    """品番の追加文字が刺繍類なら、取扱に残す原文名。刺繍なしは空。"""
    raw = stock.text(text)
    if not proc.is_embroidery_class_name(raw):
        return ''
    word = _processing_word(raw)
    return word or raw


def _processing_word(text):
    raw = stock.text(text)
    hit = paren.classify_paren_segment(raw) or {}
    if hit.get('kind') != 'processing':
        return ''
    matched = stock.text(hit.get('matched') or '')
    if matched and paren._norm_token(raw) == matched:
        return stock.text(hit.get('value') or raw)
    unified = proc.unify_processing_service_name(raw)
    if unified and unified == raw:
        return raw
    return ''


def _note_parts(inner):
    parts = [stock.text(p) for p in re.split(r'[・･、,，]+', stock.text(inner)) if stock.text(p)]
    kinds = []
    for part in parts:
        if part in _NOTE_KIND:
            kinds.append(_NOTE_KIND[part])
            continue
        if part.startswith('旧') and len(part) > 1:
            kinds.append('old_code')
            continue
        if 'ロッド' in part or re.fullmatch(r'\d+枚', part):
            kinds.append('quantity')
            continue
        return None
    return kinds


def _dimension(text):
    raw = stock.text(text)
    if raw in _SIZE_WORD or raw in ('大', '小'):
        return raw
    matched = _DIM_RE.fullmatch(raw.replace(' ', ''))
    if matched:
        return matched.group(1) + matched.group(2)
    return ''


def _size_conflict(dimension, size):
    current = stock.canonical_size_label(size)
    if current in ('', '-', '—'):
        return False
    return stock.fold_size_ascii(current) != stock.fold_size_ascii(dimension)


def classify_suffix(code, *, product_name='', supplier='', size=''):
    """品番欄の追加文字。apply のとき登録品番は baseCode。原文は呼び出し側が残す。"""
    raw = stock.text(code)
    if not raw:
        return _empty()
    is_used = raw.startswith(('*', '＊'))
    clean_raw = raw.lstrip('*＊ ')
    if is_used and clean_raw:
        sub = classify_suffix(clean_raw, product_name=product_name, supplier=supplier, size=size)
        if not sub.get('needsConfirm'):
            sub['apply'] = True
            sub['note'] = '中古' if not sub.get('note') else f"{sub['note']}・中古"
            return sub
    if raw.startswith(('別注', '特注')):
        sub_code = raw[2:].strip(' -_－')
        if sub_code and _formal_code(sub_code):
            sub = classify_suffix(sub_code, product_name=product_name, supplier=supplier, size=size)
            if not sub.get('needsConfirm'):
                sub['apply'] = True
                sub['note'] = '別注' if not sub.get('note') else f"{sub['note']}・別注"
                return sub
    discon_m = re.match(r'^(?:[■【\[（(]?\s*(?:廃番|不使用|使用しない)\s*[■】\]）)]?)\s*(.+)$', raw)
    if discon_m:
        clean_code = discon_m.group(1).strip(' -_－')
        if clean_code and _formal_code(clean_code):
            sub = classify_suffix(clean_code, product_name=product_name, supplier=supplier, size=size)
            if not sub.get('needsConfirm'):
                sub['apply'] = True
                sub['note'] = '廃番' if not sub.get('note') else f"{sub['note']}・廃番"
                return sub
    if raw in ('要確認', '別注') or (raw.startswith('別注(') and raw.endswith(')')):
        return _empty(kind='plain', baseCode=raw)
    name = stock.text(product_name)
    if _is_midori(supplier) and ('上' in raw or '下' in raw):
        return _empty(kind='midori_position', baseCode=raw)
    wrapped = _WRAP_RE.match(raw)
    inner = ''
    body = raw
    if wrapped:
        body = stock.text(wrapped.group(1))
        inner = stock.text(wrapped.group(3))
    if _CHANGE_RE.search(raw):
        return _empty(
            kind='mixed', needsConfirm=True, baseCode=raw,
            reason=f'品番「{raw}」は変更先や代替のメモがあり、自動では品番を決めません。',
        )
    note = ''
    if inner:
        if paren.is_embroidery_price_condition(inner):
            # 刺繍代込みは価格条件。品番文字列はそのまま別商品。刺繍サービスは足さない。
            return _empty(
                apply=False, kind='price_condition', needsConfirm=False, baseCode=raw,
                note=inner,
            )
        # 括弧内が色（0803(エメラルドグリーン), 6-951(青) 等）
        inner_color = ''
        hit = paren.classify_paren_segment(inner) or {}
        if hit.get('kind') == 'color':
            inner_color = stock.text(hit.get('value') or inner)
        elif _is_color_code(inner):
            inner_color = inner
        if inner_color and _formal_code(body):
            return _empty(apply=True, kind='color', baseCode=body, color=inner_color, note=note)

        # 品番の（LEOC刺繍）は未知の注記ではない。取扱名は固有のまま残す。
        emb = _embroidery_suffix_name(inner)
        if emb and _formal_code(body):
            return _empty(apply=True, kind='processing', baseCode=body, processingName=emb)
        kinds = _note_parts(inner)
        if kinds is None:
            dim = _dimension(inner)
            if dim:
                if is_part_product_name(product_name):
                    return _empty(apply=False, kind='parts', baseCode=raw, registerAsParts=True, size=dim, note=inner)
                if _size_conflict(dim, size):
                    return _empty(
                        kind='dimension', needsConfirm=True, baseCode=raw,
                        reason=f'品番「{raw}」の寸法とサイズ欄「{stock.text(size)}」が違います。どちらをサイズにするか確認してください。',
                    )
                return _empty(apply=True, kind='dimension', baseCode=body, size=dim, note=inner)
            return _empty(
                kind='unknown', needsConfirm=True, baseCode=raw,
                reason=f'品番「{raw}」の追加文字「{inner}」の意味を確定できません。',
            )
        note = inner
    color = _color_of(body)
    base = _strip_color_tail(body, color) if color else body
    if color and not _formal_code(base):
        return _empty(
            kind='unknown', needsConfirm=True, baseCode=raw,
            reason=f'品番「{raw}」から色を外した品番を確定できません。',
        )
    space = re.match(r'^(\S+)\s+(.+)$', base)
    if space:
        head, tail = space.group(1), stock.text(space.group(2))
        word = _processing_word(tail)
        tail_color = ''
        hit = paren.classify_paren_segment(tail) or {}
        if hit.get('kind') == 'color' and stock.text(hit.get('original') or '') == tail:
            tail_color = stock.text(hit.get('value') or tail)
        if (tail_color or _is_color_code(tail)) and _formal_code(head):
            return _empty(apply=True, kind='color', baseCode=head, color=tail_color or tail, note=note)
        if paren.is_embroidery_price_condition(tail):
            return _empty(
                apply=False, kind='price_condition', needsConfirm=False, baseCode=raw,
                note=tail,
            )
        if tail in ('生地', '芯地', '裏地'):
            # 行全体が部品。衣服の基本品番へ切らない。
            return _empty(
                apply=False, kind='parts', baseCode=raw, registerAsParts=True, note=tail,
            )
        if tail in _NOTE_KIND and _formal_code(head):
            return _empty(apply=True, kind='note', baseCode=head, note=tail, color=color)
        if not word:
            word = _embroidery_suffix_name(tail)
        if word and _formal_code(head):
            return _empty(
                apply=True, kind='processing', baseCode=head,
                processingName=word, color=color, note=note,
            )
        if _is_color_code(tail) and _formal_code(head):
            return _empty(apply=True, kind='color', baseCode=head, color=tail, note=note)
        dim = _dimension(tail)
        if dim and _formal_code(head):
            if is_part_product_name(product_name):
                return _empty(apply=False, kind='parts', baseCode=raw, registerAsParts=True, size=dim, note=note)
            if '/' in tail or '／' in tail:
                return _empty(
                    kind='dimension', needsConfirm=True, baseCode=raw,
                    reason=f'品番「{raw}」に複数の寸法があるため、自動では分けません。',
                )
            if _size_conflict(dim, size):
                return _empty(
                    kind='dimension', needsConfirm=True, baseCode=raw,
                    reason=f'品番「{raw}」の寸法とサイズ欄が違います。確認してください。',
                )
            return _empty(apply=True, kind='dimension', baseCode=head, size=dim, color=color, note=note)
        if _formal_code(head) and _formal_code(tail):
            norm_raw = unicodedata.normalize('NFKC', raw)
            return _empty(
                apply=True,
                kind='plain',
                baseCode=norm_raw,
                needsConfirm=False,
            )
        return _empty(
            kind='unknown', needsConfirm=True, baseCode=raw,
            reason=f'品番「{raw}」の追加文字「{tail}」の意味を確定できません。',
        )
    if len(base) > 1 and base[-1] in ('大', '小') and _formal_code(base[:-1]):
        if _size_conflict(base[-1], size):
            return _empty(
                kind='dimension', needsConfirm=True, baseCode=raw,
                reason=f'品番「{raw}」の寸法とサイズ欄が違います。確認してください。',
            )
        return _empty(apply=True, kind='dimension', baseCode=base[:-1], size=base[-1], color=color, note=note)
    if base.endswith(('上', '下')) and len(base) > 1 and _formal_code(base[:-1]):
        if _is_midori(supplier):
            return _empty(kind='midori_position', baseCode=raw)
        return _empty(
            apply=True, kind='position', baseCode=base[:-1],
            position=base[-1], color=color, note=note,
        )
    if color and base != body:
        return _empty(apply=True, kind='color', baseCode=base, color=color, note=note)
    if note and base != raw:
        return _empty(apply=True, kind='note', baseCode=base, note=note, color=color)
    if '用' in raw and not note:
        left, right = raw.split('用', 1)
        right_name = _part_right_name(right, name)
        if right_name:
            if re.search(r'\d', left) and _formal_code(left):
                return _empty(
                    apply=False, kind='parts', baseCode=raw, registerAsParts=True,
                    note=right_name,
                )
            return _empty(
                kind='unknown', needsConfirm=True, baseCode=raw,
                reason=f'品番「{raw}」は部品のように見えますが、基本品番を確定できません。',
            )
    if _formal_code(raw):
        norm = unicodedata.normalize('NFKC', raw)
        return _empty(
            apply=bool(norm != raw),
            kind='branch' if re.search(r'[-_－][0-9A-Za-z]+$', norm) else 'plain',
            baseCode=norm,
        )
    return _empty(
        kind='unknown', needsConfirm=True, baseCode=raw,
        reason=f'品番「{raw}」の追加文字を属性に分けられません。',
    )


def ensure_candidate_schema(c):
    c.execute(
        '''CREATE TABLE IF NOT EXISTS process_service_candidates (
          service_name TEXT NOT NULL,
          source_code TEXT NOT NULL,
          base_code TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(service_name, source_code)
        )'''
    )


def collect_processing_candidates(classified):
    found = []
    seen = set()
    for row in classified or []:
        attr = row.get('suffixAttribute') or {}
        name = stock.text(attr.get('processingName') or '')
        if not name:
            continue
        source = stock.text((row.get('csv') or {}).get('sourceCode') or '')
        base = stock.text((row.get('csv') or {}).get('code') or '')
        key = (name, source)
        if key in seen:
            continue
        seen.add(key)
        service_name = name
        customer_name = ''
        if proc.is_embroidery_class_name(name):
            # LEOC刺繍などは刺繍する文字を取扱に残し、共通サービスは「刺繍」だけ増やす。
            service_name = proc.ELMS_EMBROIDERY_SERVICE_NAME
            if name != service_name:
                customer_name = name
        found.append({
            'serviceName': service_name,
            'customerProcessingName': customer_name,
            'sourceCode': source,
            'baseCode': base,
        })
    return found


def _service_exists(c, name):
    if proc.find_existing_processing_service_any_vendor(c, name):
        return True
    unified = proc.unify_processing_service_name(name)
    return bool(unified and unified != name and proc.find_existing_processing_service_any_vendor(c, unified))


def _already_satisfied(c, name):
    """刺繍類はエルム側にあるときだけ登録済み。別業者の既存は消さず、候補も残す。"""
    if proc.is_embroidery_class_name(name):
        found = proc.elms_supplier_matches(c)
        if found['status'] != 'unique':
            return False
        canon = proc.ELMS_EMBROIDERY_SERVICE_NAME
        return bool(proc.find_existing_processing_service(c, canon, found['name']))
    return _service_exists(c, name)


def remember_candidates(c, found):
    """未登録の加工名だけ残す。登録済みサービスと顧客取扱は消さない。"""
    ensure_candidate_schema(c)
    when = stock.stamp()
    kept = []
    for item in found or []:
        name = stock.text(item.get('serviceName') or '')
        source = stock.text(item.get('sourceCode') or '')
        base = stock.text(item.get('baseCode') or '')
        if not name or not source:
            continue
        if _already_satisfied(c, name):
            c.execute(
                'DELETE FROM process_service_candidates WHERE service_name=?',
                (name,),
            )
            continue
        c.execute(
            '''INSERT INTO process_service_candidates(service_name, source_code, base_code, created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(service_name, source_code) DO UPDATE SET base_code=excluded.base_code''',
            (name, source, base, when),
        )
        kept.append({'serviceName': name, 'sourceCode': source, 'baseCode': base})
    return kept


def pending_candidates(c):
    ensure_candidate_schema(c)
    rows = []
    for row in c.execute(
        'SELECT service_name, source_code, base_code FROM process_service_candidates ORDER BY service_name, source_code'
    ):
        if _already_satisfied(c, row['service_name']):
            c.execute(
                'DELETE FROM process_service_candidates WHERE service_name=?',
                (row['service_name'],),
            )
            continue
        rows.append({
            'serviceName': row['service_name'],
            'sourceCode': row['source_code'],
            'baseCode': row['base_code'],
        })
    return rows


def register_pending_with_k2(c, classified):
    """未登録候補のうち、刺繍類はエルム、それ以外は K2スタッフ工房。IDは推測しない。"""
    remember_candidates(c, collect_processing_candidates(classified))
    names = []
    for row in pending_candidates(c):
        if row['serviceName'] not in names:
            names.append(row['serviceName'])
    if not names:
        return {'registered': [], 'confirm': [], 'skipped': '候補なし'}
    embroidery = [name for name in names if proc.is_embroidery_class_name(name)]
    others = [name for name in names if name not in embroidery]
    result = {'registered': [], 'confirm': [], 'skipped': ''}
    if others:
        result['k2'] = _register_names_with_vendor(
            c, others, _k2_vendor(c),
        )
        result['registered'].extend(result['k2'].get('registered') or [])
        if result['k2'].get('skipped'):
            result['skipped'] = result['k2']['skipped']
    if embroidery:
        found = proc.elms_supplier_matches(c)
        plans = [proc.embroidery_vendor_plan(c, name) for name in embroidery]
        if found['status'] != 'unique':
            result['confirm'].extend(plans)
            result['skipped'] = plans[0]['message']
        else:
            created = _register_names_with_vendor(c, embroidery, {
                'supplierId': found['id'],
                'vendor': found['name'],
                'elms': True,
            })
            result['registered'].extend(created.get('registered') or [])
            result['elms'] = created
    return result


def _k2_vendor(c):
    matches = [
        row for row in c.execute('SELECT id, name, active FROM suppliers')
        if proc.is_k2_staff_workshop(row['name']) and row['active']
    ]
    if len(matches) > 1:
        return {'ambiguous': True, 'message': 'K2スタッフ工房に当たる仕入先が複数あります。推測では選びません。'}
    if len(matches) == 1:
        return {'supplierId': matches[0]['id'], 'vendor': proc.K2_VENDOR_DISPLAY}
    saved = stock.save_supplier(c, {'name': proc.K2_VENDOR_DISPLAY, 'active': True})
    return {'supplierId': saved['id'], 'vendor': proc.K2_VENDOR_DISPLAY}


def _register_names_with_vendor(c, names, vendor_info):
    if vendor_info.get('ambiguous'):
        return {'registered': [], 'skipped': vendor_info.get('message') or ''}
    import product_ledger_import as pli
    registered = []
    vendor = vendor_info['vendor']
    for name in names:
        service_name = name
        if vendor_info.get('elms') and proc.is_embroidery_class_name(name):
            service_name = proc.ELMS_EMBROIDERY_SERVICE_NAME
        if vendor_info.get('elms') and _already_satisfied(c, service_name):
            existing = proc.find_existing_processing_service(c, service_name, vendor)
            if existing:
                c.execute('DELETE FROM process_service_candidates WHERE service_name=?', (name,))
                registered.append({
                    'serviceName': service_name, 'productId': existing['id'],
                    'created': False, 'supplierId': vendor_info['supplierId'], 'reused': True,
                })
                continue
        pid, created = pli._ensure_processing_service_product(c, {
            'serviceName': service_name,
            'vendorName': vendor,
            'elmsEmbroideryRule': bool(
                vendor_info.get('elms') and service_name == proc.ELMS_EMBROIDERY_SERVICE_NAME
            ),
            'targetGarmentCodes': [],
        })
        c.execute('DELETE FROM process_service_candidates WHERE service_name=?', (name,))
        registered.append({
            'serviceName': service_name, 'productId': pid, 'created': created,
            'supplierId': vendor_info['supplierId'],
        })
    return {'registered': registered, 'vendor': vendor, 'skipped': ''}


def annotate_candidate_vendors(c, found):
    """プレビュー用。刺繍類はエルム、それ以外はK2。決まらない刺繍は確認。"""
    out = []
    for item in found or []:
        row = dict(item)
        name = stock.text(row.get('serviceName') or '')
        if proc.is_embroidery_class_name(name):
            plan = proc.embroidery_vendor_plan(c, name)
            row['embroidery'] = True
            row['vendorName'] = plan.get('vendorName') or ''
            row['vendorStatus'] = plan.get('status')
            row['vendorMessage'] = plan.get('message')
            row['needsConfirm'] = plan.get('needsConfirm')
            row['existingVendorKept'] = plan.get('existingKept')
            customer = stock.text(row.get('customerProcessingName') or '')
            if customer and customer != name:
                row['vendorMessage'] = (
                    f'加工内容「{customer}」は取扱に残します（刺繍する文字を消して「刺繍」だけにはしません）。'
                    f'共通の加工サービスは「刺繍」、加工先はエルムです。'
                    + (row.get('vendorMessage') or '')
                )
        else:
            row['embroidery'] = False
            row['vendorName'] = proc.K2_VENDOR_DISPLAY
            row['vendorStatus'] = 'k2'
            row['vendorMessage'] = '刺繍以外の未登録加工はK2スタッフ工房です。'
            row['needsConfirm'] = False
        out.append(row)
    return out
