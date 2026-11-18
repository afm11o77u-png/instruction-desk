"""旧取り込み履歴（legacy_csv_row_maps / product_ledger_row_maps）と既存の加工サービス紐づけから、
「加工未整理」のままになっている制服の取扱加工を一括で補完する。
"""
from __future__ import annotations

import re
import unicodedata

import stock


def _norm(value):
    return re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


# 加工名ではない誤検知ワード（Aラインスカート、未加工品など）
_IGNORE_RE = re.compile(r'未加工|Aライン|ラインスカート', re.IGNORECASE)

# 抽出ルール: (検索正規表現, 表示用加工名, 紐づける加工サービス候補名タプル)
_PROCESS_RULES = (
    (re.compile(r'(?:LEOC|ﾚｵｯｸ)刺繍', re.IGNORECASE), 'LEOC刺繍', ('刺繍',)),
    (re.compile(r'DHC刺繍', re.IGNORECASE), 'DHC刺繍', ('刺繍',)),
    (re.compile(r'ｻﾝｹﾞﾂ刺繍|サンゲツ刺繍', re.IGNORECASE), 'サンゲツ刺繍', ('刺繍',)),
    (re.compile(r'刺繍|ｼｼｭｳ'), '刺繍', ('刺繍',)),
    (re.compile(r'七分袖加工'), '七分袖加工', ('七分袖加工',)),
    (re.compile(r'半袖加工'), '半袖加工', ('半袖加工',)),
    (re.compile(r'内ポケット加工'), '内ポケット加工', ('内ポケット加工',)),
    (re.compile(r'後ポケット加工'), '後ポケット加工', ('後ポケット加工',)),
    (re.compile(r'白衣ポケット加工'), '白衣ポケット加工', ('白衣ポケット加工(住商モンブラン)', 'ポケット加工')),
    (re.compile(r'ポケット・ペン差し加工'), 'ポケット・ペン差し加工', ('ポケット・ペン差し加工',)),
    (re.compile(r'ポケット加工|ポケット付|キーポケット付'), 'ポケット加工', ('ポケット加工',)),
    (re.compile(r'スナッパー加工|ｽﾅｯﾊﾟｰ加工'), 'スナッパー加工', ('スナッパー加工',)),
    (re.compile(r'ファスナー加工'), 'ファスナー加工', ('ファスナー加工', 'ファスナー裏加工')),
    (re.compile(r'マジックテープ加工'), 'マジックテープ加工', ('マジックテープ加工',)),
    (re.compile(r'パンツ丈加工|丈加工'), '丈加工', ('丈加工', '丈直し')),
    (re.compile(r'裾上げ'), '裾上げ', ('裾上げ',)),
    (re.compile(r'体毛落下防止加工'), '体毛落下防止加工', ('体毛落下防止加工',)),
    (re.compile(r'(?:ライン入|ライン付|黄3ライン)'), 'ライン加工', ('ライン加工',)),
    (re.compile(r'加工あり'), '加工あり', ()),
)


def _active_process_map(c):
    """加工サービス名(正規化) -> {'id', 'name'}"""
    out = {}
    for r in c.execute(
        "SELECT id, name FROM products WHERE category=? AND active=1 AND merged_into_id IS NULL ORDER BY id",
        (stock.CATEGORY_PROCESS_SERVICE,),
    ):
        key = _norm(r['name'])
        if key and key not in out:
            out[key] = {'id': int(r['id']), 'name': r['name']}
    return out


def _detect_from_texts(texts, proc_map):
    labels = []
    proc_ids = []
    proc_names = []
    for raw in texts:
        cleaned = _IGNORE_RE.sub('', unicodedata.normalize('NFKC', str(raw or '')))
        if not cleaned:
            continue
        for pat, label, svc_cands in _PROCESS_RULES:
            if not pat.search(cleaned):
                continue
            if label not in labels:
                labels.append(label)
            for cand in svc_cands:
                hit = proc_map.get(_norm(cand))
                if hit and hit['id'] not in proc_ids:
                    proc_ids.append(hit['id'])
                    proc_names.append(hit['name'])
                    break
            if '刺繍' in label or 'ポケット' in label:
                break
    return '・'.join(labels), proc_ids, proc_names


def preview(c):
    """加工未整理の制服取扱から、反映候補を抽出する（DBは変更しない）。"""
    proc_map = _active_process_map(c)

    # 既存の (customer_id, product_id, spec_key) 重複チェック用
    existing_specs = {}
    for r in c.execute('SELECT id, customer_id, product_id, embroidery FROM customer_products'):
        existing_specs.setdefault((int(r['customer_id']), int(r['product_id'])), {})[
            stock.spec_key(r['embroidery'])
        ] = int(r['id'])

    # 1. すでに customer_product_processes があるのに embroidery が「加工未整理」のもの
    linked_procs = {}
    for r in c.execute(
        '''SELECT cpp.customer_product_id, pp.id AS proc_id, pp.name AS proc_name
           FROM customer_product_processes cpp
           JOIN products pp ON pp.id = cpp.process_product_id
           ORDER BY cpp.customer_product_id, cpp.sort_order, pp.id'''
    ):
        linked_procs.setdefault(int(r['customer_product_id']), []).append(
            (int(r['proc_id']), r['proc_name'])
        )

    # 2. 旧在庫・商品台帳の取り込み履歴（元の品番・商品名）
    legacy_texts = {}
    for tbl in ('legacy_csv_row_maps', 'product_ledger_row_maps'):
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone():
            continue
        for r in c.execute(
            f'''SELECT DISTINCT customer_product_id, csv_product_code, csv_product_name
                FROM {tbl} WHERE customer_product_id IS NOT NULL'''
        ):
            cpid = int(r['customer_product_id'])
            bucket = legacy_texts.setdefault(cpid, [])
            for val in (r['csv_product_code'], r['csv_product_name']):
                if val and val not in bucket:
                    bucket.append(val)

    items = []
    skipped = []
    handles = c.execute(
        '''SELECT cp.id, cp.customer_id, cp.product_id, cp.embroidery,
                  cu.name AS customer_name, p.code, p.name AS product_name, p.category
           FROM customer_products cp
           JOIN customers cu ON cu.id = cp.customer_id
           JOIN products p ON p.id = cp.product_id
           WHERE cp.embroidery = ? AND cp.active = 1
             AND p.active = 1 AND p.merged_into_id IS NULL
             AND (p.category = ? OR p.category = '')
           ORDER BY cu.name, p.code, p.name, cp.id''',
        (stock.PENDING_EMBROIDERY, stock.CATEGORY_UNIFORM),
    ).fetchall()

    for h in handles:
        cpid = int(h['id'])
        procs = linked_procs.get(cpid, [])
        if procs:
            new_label = '・'.join(dict.fromkeys(name for _pid, name in procs))
            add_proc_ids = []
            proc_names = [name for _pid, name in procs]
            source = '加工サービス紐づけ済み'
        else:
            texts = legacy_texts.get(cpid, [])
            if not texts:
                continue
            new_label, add_proc_ids, proc_names = _detect_from_texts(texts, proc_map)
            if not new_label:
                continue
            source = '旧データ名から検出'

        peer_map = existing_specs.get((int(h['customer_id']), int(h['product_id'])), {})
        conflict_id = peer_map.get(stock.spec_key(new_label))
        if conflict_id and conflict_id != cpid:
            skipped.append({
                'cpId': cpid,
                'customerName': h['customer_name'],
                'code': h['code'] or '',
                'productName': h['product_name'] or '',
                'proposedLabel': new_label,
                'reason': '同じ顧客・商品に同じ加工の取扱が既にあります',
            })
            continue

        items.append({
            'cpId': cpid,
            'customerName': h['customer_name'],
            'code': h['code'] or '',
            'productName': h['product_name'] or '',
            'proposedLabel': new_label,
            'addProcessIds': add_proc_ids,
            'processNames': proc_names,
            'source': source,
        })

    return {
        'items': items,
        'count': len(items),
        'skipped': skipped,
        'skippedCount': len(skipped),
    }


def commit(c, body=None):
    """プレビュー候補を一括で取扱（embroidery・handling_kind・customer_product_processes）へ反映する。"""
    body = body if isinstance(body, dict) else {}
    selected = body.get('cpIds')
    selected_set = {int(x) for x in selected} if isinstance(selected, list) else None
    plan = preview(c)
    t = stock.now()
    updated = 0
    procs_added = 0
    for item in plan['items']:
        cpid = item['cpId']
        if selected_set is not None and cpid not in selected_set:
            continue
        label = item['proposedLabel']
        hk = stock.handling_kind_of(label)
        c.execute(
            'UPDATE customer_products SET embroidery=?, handling_kind=?, updated_at=? WHERE id=?',
            (label, hk, t, cpid),
        )
        if item['addProcessIds']:
            cur_max = c.execute(
                'SELECT COALESCE(MAX(sort_order), -1) FROM customer_product_processes WHERE customer_product_id=?',
                (cpid,),
            ).fetchone()[0]
            for idx, pid in enumerate(item['addProcessIds'], start=int(cur_max) + 1):
                cur = c.execute(
                    '''INSERT OR IGNORE INTO customer_product_processes(customer_product_id, process_product_id, sort_order)
                       VALUES(?,?,?)''',
                    (cpid, pid, idx),
                )
                procs_added += cur.rowcount
        updated += 1
    return {
        'ok': True,
        'updated': updated,
        'processesAdded': procs_added,
        'skippedCount': plan['skippedCount'],
    }
