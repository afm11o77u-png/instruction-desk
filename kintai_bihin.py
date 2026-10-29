"""月別備品棚卸（勤怠管理内）。在庫デスクの入庫・出庫とは独立。

マスタ（科目・品名・単位・単価）と月シートを分離する。
保存済み月は行構造・単価・品名のスナップショットを保持し、マスタ変更で書き換えない。
未作成月のみ、前月の保存済み月末数量を月初に引き継ぐ（空≠0）。
金額は数量×単価を四捨五入（round half up）して円にする。
同時編集は version による楽観ロックで上書きを防ぐ。
"""
from __future__ import annotations

import json
import secrets
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from datetime import datetime

MONEY_QUANT = Decimal('1')  # 円（整数）
QTY_MAX = Decimal('999999999.999')
PRICE_MAX = Decimal('999999999.999')


def schema(c):
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_bihin_items (
            id INTEGER PRIMARY KEY,
            category TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            unit_price TEXT NOT NULL DEFAULT '0',
            sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )'''
    )
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_bihin_sheets (
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            rows_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (year, month)
        )'''
    )
    # 発注（仕入先・履歴・品目の発注列）。シフト／出勤簿は触らない。
    import kintai_bihin_order
    kintai_bihin_order.schema(c)


def _parse_year_month(year, month) -> tuple[int, int]:
    try:
        y = int(year)
        m = int(month)
    except (TypeError, ValueError) as e:
        raise ValueError('年月を確認してください。') from e
    if y < 2000 or y > 2100 or not 1 <= m <= 12:
        raise ValueError('年月を確認してください。')
    return y, m


def _prev_year_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _dec(value, *, allow_empty=True, field='値') -> Decimal | None:
    """空は None。0 は Decimal('0')。負は拒否。"""
    if value is None or value == '':
        if allow_empty:
            return None
        raise ValueError(f'{field}を入力してください。')
    if isinstance(value, bool):
        raise ValueError(f'{field}の形式が不正です。')
    try:
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, int):
            d = Decimal(value)
        elif isinstance(value, float):
            # 浮動小数の桁揺れを避けるため文字列経由
            d = Decimal(str(value))
        else:
            s = str(value).strip().replace(',', '')
            if s == '':
                if allow_empty:
                    return None
                raise ValueError(f'{field}を入力してください。')
            d = Decimal(s)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f'{field}の形式が不正です。') from e
    if d.is_nan() or d.is_infinite():
        raise ValueError(f'{field}の形式が不正です。')
    if d < 0:
        raise ValueError(f'{field}に負の値は使えません。')
    return d


def _qty(value, field='数量') -> Decimal | None:
    d = _dec(value, field=field)
    if d is not None and d > QTY_MAX:
        raise ValueError(f'{field}が大きすぎます。')
    return d


def _price(value, field='単価') -> Decimal:
    d = _dec(value, allow_empty=False, field=field)
    assert d is not None
    if d > PRICE_MAX:
        raise ValueError(f'{field}が大きすぎます。')
    return d


def money_yen(qty: Decimal | None, unit_price: Decimal) -> int | None:
    """数量×単価を四捨五入（round half up）して円（整数）。数量が空なら None。"""
    if qty is None:
        return None
    return int((qty * unit_price).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))


def usage_qty(opening: Decimal | None, purchase: Decimal | None, closing: Decimal | None) -> Decimal | None:
    """月初・購入・月末がすべて入力済みのときだけ使用数量を確定。空≠0。"""
    if opening is None or purchase is None or closing is None:
        return None
    return opening + purchase - closing


def _dec_to_json(d: Decimal | None):
    if d is None:
        return None
    # 整数っぽいものは int、それ以外は正規化文字列→float ではなく文字列で往復精度を保つ
    # API では number として扱いやすいよう、有限小数は float 化（保存は TEXT で Decimal 文字列）
    s = format(d.normalize(), 'f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    if s in ('', '-'):
        s = '0'
    if '.' not in s:
        return int(s)
    return float(s)


def _new_row_key() -> str:
    return f'row:{secrets.token_hex(8)}'


def _actor_name(user) -> str:
    if not user:
        return ''
    try:
        return str(user['name'] or user['login'] or '')[:80]
    except (KeyError, IndexError, TypeError):
        return ''


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=' ')


def list_items(c, *, include_inactive: bool = False) -> list[dict]:
    if include_inactive:
        rows = c.execute(
            'SELECT * FROM kintai_bihin_items ORDER BY sort_order, id'
        ).fetchall()
    else:
        rows = c.execute(
            'SELECT * FROM kintai_bihin_items WHERE active=1 ORDER BY sort_order, id'
        ).fetchall()
    return [_item_public(r) for r in rows]


def _item_public(r) -> dict:
    price = _price(r['unit_price'] if r['unit_price'] not in (None, '') else '0', field='単価')
    base = {
        'id': int(r['id']),
        'category': r['category'] or '',
        'name': r['name'] or '',
        'unit': r['unit'] or '',
        'unitPrice': _dec_to_json(price),
        'sortOrder': int(r['sort_order'] or 0),
        'active': bool(r['active']),
        'createdAt': r['created_at'] or None,
        'updatedAt': r['updated_at'] or None,
    }
    import kintai_bihin_order
    return kintai_bihin_order.enrich_item_public(base, r)


def save_item(c, user, payload: dict) -> dict:
    """マスタ品目の新規／更新。保存済み月シートは触らない。同名別単価は別行（別 ID）。"""
    payload = payload or {}
    item_id = payload.get('id')
    category = str(payload.get('category') or '').strip()[:80]
    name = str(payload.get('name') or '').strip()[:120]
    unit = str(payload.get('unit') or '').strip()[:40]
    if not name:
        raise ValueError('品名を入力してください。')
    price = _price(payload.get('unitPrice'), field='単価')
    active = 1 if payload.get('active', True) else 0
    try:
        sort_order = int(payload.get('sortOrder', 0))
    except (TypeError, ValueError):
        sort_order = 0
    now = _now()
    price_s = format(price, 'f')

    if item_id is None or item_id == '':
        cur = c.execute(
            '''INSERT INTO kintai_bihin_items(
                category, name, unit, unit_price, sort_order, active, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)''',
            (category, name, unit, price_s, sort_order, active, now, now),
        )
        new_id = int(cur.lastrowid)
        # 発注設定（仕入先・発注品名・発注単位・入数）
        import kintai_bihin_order
        kintai_bihin_order.apply_item_order_fields(c, new_id, payload)
        row = c.execute('SELECT * FROM kintai_bihin_items WHERE id=?', (new_id,)).fetchone()
        return _item_public(row)

    try:
        iid = int(item_id)
    except (TypeError, ValueError) as e:
        raise ValueError('品目 ID を確認してください。') from e
    existing = c.execute('SELECT id FROM kintai_bihin_items WHERE id=?', (iid,)).fetchone()
    if not existing:
        raise ValueError('品目が見つかりません。')
    c.execute(
        '''UPDATE kintai_bihin_items
           SET category=?, name=?, unit=?, unit_price=?, sort_order=?, active=?, updated_at=?
           WHERE id=?''',
        (category, name, unit, price_s, sort_order, active, now, iid),
    )
    if any(k in payload for k in ('vendorId', 'orderProductName', 'orderUnit', 'packingQty')):
        import kintai_bihin_order
        kintai_bihin_order.apply_item_order_fields(c, iid, payload)
    row = c.execute('SELECT * FROM kintai_bihin_items WHERE id=?', (iid,)).fetchone()
    return _item_public(row)


def soft_delete_item(c, user, payload: dict) -> dict:
    """品目を ID で論理削除（active=0）。保存済み月・発注履歴は書き換えない。

    品名では消さない（同名別単価の取り違え防止）。未作成月・発注候補から外れる。
    """
    _ = user  # 権限は API 層。save_item とシグネチャを揃える
    payload = payload or {}
    try:
        iid = int(payload.get('id'))
    except (TypeError, ValueError) as e:
        raise ValueError('品目 ID を確認してください。') from e
    existing = c.execute('SELECT * FROM kintai_bihin_items WHERE id=?', (iid,)).fetchone()
    if not existing:
        raise ValueError('品目が見つかりません。')
    if not existing['active']:
        return _item_public(existing)
    now = _now()
    c.execute(
        'UPDATE kintai_bihin_items SET active=0, updated_at=? WHERE id=?',
        (now, iid),
    )
    row = c.execute('SELECT * FROM kintai_bihin_items WHERE id=?', (iid,)).fetchone()
    return _item_public(row)


def restore_item(c, user, payload: dict) -> dict:
    """論理削除した品目を ID で復元（active=1）。過去月の行は書き換えず、重複行も作らない。"""
    _ = user
    payload = payload or {}
    try:
        iid = int(payload.get('id'))
    except (TypeError, ValueError) as e:
        raise ValueError('品目 ID を確認してください。') from e
    existing = c.execute('SELECT * FROM kintai_bihin_items WHERE id=?', (iid,)).fetchone()
    if not existing:
        raise ValueError('品目が見つかりません。')
    if existing['active']:
        return _item_public(existing)
    now = _now()
    c.execute(
        'UPDATE kintai_bihin_items SET active=1, updated_at=? WHERE id=?',
        (now, iid),
    )
    row = c.execute('SELECT * FROM kintai_bihin_items WHERE id=?', (iid,)).fetchone()
    return _item_public(row)


def _load_sheet_row(c, year: int, month: int):
    return c.execute(
        'SELECT * FROM kintai_bihin_sheets WHERE year=? AND month=?',
        (year, month),
    ).fetchone()


def _parse_rows_json(raw) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _enrich_row(raw: dict) -> dict:
    """スナップショット行に計算列を付与。空の数量は確定使用を出さない。"""
    category = str(raw.get('category') or '')[:80]
    name = str(raw.get('name') or '')[:120]
    unit = str(raw.get('unit') or '')[:40]
    row_key = str(raw.get('rowKey') or raw.get('key') or '').strip() or _new_row_key()
    item_id = raw.get('itemId')
    if item_id is not None and item_id != '':
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            item_id = None
    else:
        item_id = None
    try:
        sort_order = int(raw.get('sortOrder', 0))
    except (TypeError, ValueError):
        sort_order = 0

    price = _price(raw.get('unitPrice', 0), field='単価')
    opening = _qty(raw.get('openingQty'), field='月初数量')
    purchase = _qty(raw.get('purchaseQty'), field='購入数量')
    closing = _qty(raw.get('closingQty'), field='月末数量')
    used = usage_qty(opening, purchase, closing)
    negative_usage = used is not None and used < 0

    return {
        'rowKey': row_key,
        'itemId': item_id,
        'category': category,
        'name': name,
        'unit': unit,
        'unitPrice': _dec_to_json(price),
        'sortOrder': sort_order,
        'openingQty': _dec_to_json(opening),
        'purchaseQty': _dec_to_json(purchase),
        'closingQty': _dec_to_json(closing),
        'openingAmount': money_yen(opening, price),
        'purchaseAmount': money_yen(purchase, price),
        'closingAmount': money_yen(closing, price),
        'usageQty': _dec_to_json(used) if used is not None else None,
        'usageAmount': money_yen(used, price) if used is not None else None,
        'usageIncomplete': used is None,
        'negativeUsage': negative_usage,
    }


def _normalize_save_rows(raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('棚卸行の形式が不正です。')
    out = []
    seen_keys = set()
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ValueError(f'棚卸行の形式が不正です（{i + 1}件目）。')
        enriched = _enrich_row({
            **r,
            'sortOrder': r.get('sortOrder', i),
            'rowKey': r.get('rowKey') or r.get('key') or _new_row_key(),
        })
        if enriched['negativeUsage']:
            raise ValueError(
                f'「{enriched["name"] or "（無題）"}」の使用数量が負になります。'
                '月初・購入・月末の数量を確認してください。'
            )
        if enriched['rowKey'] in seen_keys:
            enriched['rowKey'] = _new_row_key()
        seen_keys.add(enriched['rowKey'])
        # 永続化は入力スナップショットのみ（計算列は再計算）
        out.append({
            'rowKey': enriched['rowKey'],
            'itemId': enriched['itemId'],
            'category': enriched['category'],
            'name': enriched['name'],
            'unit': enriched['unit'],
            'unitPrice': enriched['unitPrice'],
            'sortOrder': i,
            'openingQty': enriched['openingQty'],
            'purchaseQty': enriched['purchaseQty'],
            'closingQty': enriched['closingQty'],
        })
    return out


def _closing_by_item_id(c, year: int, month: int) -> dict[int, Decimal | None]:
    row = _load_sheet_row(c, year, month)
    if not row:
        return {}
    out: dict[int, Decimal | None] = {}
    for r in _parse_rows_json(row['rows_json']):
        if not isinstance(r, dict):
            continue
        iid = r.get('itemId')
        if iid is None or iid == '':
            continue
        try:
            iid = int(iid)
        except (TypeError, ValueError):
            continue
        try:
            out[iid] = _qty(r.get('closingQty'), field='月末数量')
        except ValueError:
            out[iid] = None
    return out


def _compose_unsaved(c, year: int, month: int) -> tuple[list[dict], list[dict]]:
    """未作成月: アクティブマスタから行を組み立て、前月月末を月初へ。"""
    items = list_items(c, include_inactive=False)
    py, pm = _prev_year_month(year, month)
    prev_closing = _closing_by_item_id(c, py, pm) if py >= 2000 else {}
    prev_exists = bool(_load_sheet_row(c, py, pm)) if py >= 2000 else False

    rows = []
    for i, it in enumerate(items):
        opening = None
        if prev_exists and it['id'] in prev_closing:
            opening = prev_closing[it['id']]
        rows.append(_enrich_row({
            'rowKey': _new_row_key(),
            'itemId': it['id'],
            'category': it['category'],
            'name': it['name'],
            'unit': it['unit'],
            'unitPrice': it['unitPrice'],
            'sortOrder': i,
            'openingQty': _dec_to_json(opening),
            'purchaseQty': None,
            'closingQty': None,
        }))
    notices = []
    if not items:
        notices.append({
            'code': 'empty_master',
            'message': '品目マスタが空です。品目を登録してから棚卸を入力できます。',
        })
    elif not prev_exists:
        notices.append({
            'code': 'no_previous_month',
            'message': '前月の保存がありません。月初数量は手入力してください（空は0ではありません）。',
        })
    return rows, notices


def _carry_mismatches(c, year: int, month: int, saved_rows: list[dict]) -> list[dict]:
    """保存済み月を開いたとき、前月末と当月初の食い違いを通知用に返す。自動修正しない。"""
    py, pm = _prev_year_month(year, month)
    if py < 2000:
        return []
    prev = _load_sheet_row(c, py, pm)
    if not prev:
        return []
    prev_closing = _closing_by_item_id(c, py, pm)
    mismatches = []
    for r in saved_rows:
        iid = r.get('itemId')
        if iid is None:
            continue
        if iid not in prev_closing:
            continue
        expected = prev_closing[iid]
        actual = None
        try:
            actual = _qty(r.get('openingQty'), field='月初数量')
        except ValueError:
            actual = None
        # 両方空は一致扱い。片方が空でも不一致。
        if expected is None and actual is None:
            continue
        if expected is None or actual is None or expected != actual:
            mismatches.append({
                'itemId': iid,
                'rowKey': r.get('rowKey'),
                'name': r.get('name') or '',
                'unit': r.get('unit') or '',
                'previousClosingQty': _dec_to_json(expected),
                'openingQty': _dec_to_json(actual),
                'previousYear': py,
                'previousMonth': pm,
            })
    return mismatches


def _totals(rows: list[dict]) -> dict:
    """金額のみ合計。数量は単位が異なるため合計しない。"""
    def sum_amount(key):
        total = 0
        any_val = False
        for r in rows:
            v = r.get(key)
            if v is None:
                continue
            any_val = True
            total += int(v)
        return total if any_val else None

    return {
        'openingAmount': sum_amount('openingAmount'),
        'purchaseAmount': sum_amount('purchaseAmount'),
        'closingAmount': sum_amount('closingAmount'),
        'usageAmount': sum_amount('usageAmount'),
        # 数量合計は出さない（単位混在）
        'note': '金額のみ合計。数量は単位が異なるため合計しません。',
        'rounding': '金額＝数量×単価を四捨五入（round half up）して円（整数）',
    }


def get_sheet(c, year, month) -> dict:
    y, m = _parse_year_month(year, month)
    db_row = _load_sheet_row(c, y, m)
    notices: list[dict] = []
    mismatches: list[dict] = []

    if db_row:
        raw_rows = _parse_rows_json(db_row['rows_json'])
        rows = [_enrich_row({**r, 'sortOrder': i}) for i, r in enumerate(raw_rows) if isinstance(r, dict)]
        mismatches = _carry_mismatches(c, y, m, rows)
        if mismatches:
            notices.append({
                'code': 'carry_mismatch',
                'message': (
                    f'前月（{mismatches[0]["previousYear"]}年{mismatches[0]["previousMonth"]}月）の月末数量と、'
                    '当月の月初数量が一致しない行があります。自動では直しません。内容を確認して修正してください。'
                ),
                'count': len(mismatches),
            })
        version = int(db_row['version'])
        updated_at = db_row['updated_at']
        updated_by = db_row['updated_by'] or ''
        exists = True
    else:
        rows, notices = _compose_unsaved(c, y, m)
        version = 0
        updated_at = None
        updated_by = ''
        exists = False

    return {
        'year': y,
        'month': m,
        'version': version,
        'updatedAt': updated_at,
        'updatedBy': updated_by,
        'exists': exists,
        'rows': rows,
        'totals': _totals(rows),
        'notices': notices,
        'carryMismatches': mismatches,
        'items': list_items(c, include_inactive=False),
        'rounding': '金額＝数量×単価を四捨五入（round half up）して円（整数）。空の数量は0とみなさない。',
    }


def save_sheet(c, user, payload: dict) -> tuple[int, dict]:
    """月シートを上書き保存。version 不一致は 409。マスタ・出勤簿・シフト・在庫は触らない。"""
    y, m = _parse_year_month(payload.get('year'), payload.get('month'))
    try:
        client_ver = int(payload.get('version'))
    except (TypeError, ValueError) as e:
        raise ValueError('version を確認してください。') from e
    if client_ver < 0:
        raise ValueError('version を確認してください。')

    rows = _normalize_save_rows(payload.get('rows'))
    now = _now()
    actor = _actor_name(user)

    db_row = _load_sheet_row(c, y, m)
    current_ver = int(db_row['version']) if db_row else 0
    if client_ver != current_ver:
        sheet = get_sheet(c, y, m)
        return 409, {
            'error': 'ほかの画面で先に保存されています。内容を確認してから再度保存してください。',
            'conflict': True,
            'sheet': sheet,
        }

    new_ver = current_ver + 1
    rows_json = json.dumps(rows, ensure_ascii=False)
    if db_row:
        cur = c.execute(
            '''UPDATE kintai_bihin_sheets
               SET version=?, updated_at=?, updated_by=?, rows_json=?
               WHERE year=? AND month=? AND version=?''',
            (new_ver, now, actor, rows_json, y, m, current_ver),
        )
        if cur.rowcount != 1:
            sheet = get_sheet(c, y, m)
            return 409, {
                'error': 'ほかの画面で先に保存されています。内容を確認してから再度保存してください。',
                'conflict': True,
                'sheet': sheet,
            }
    else:
        try:
            c.execute(
                '''INSERT INTO kintai_bihin_sheets(
                    year, month, version, updated_at, updated_by, rows_json
                ) VALUES (?,?,?,?,?,?)''',
                (y, m, new_ver, now, actor, rows_json),
            )
        except Exception:
            sheet = get_sheet(c, y, m)
            return 409, {
                'error': 'ほかの画面で先に保存されています。内容を確認してから再度保存してください。',
                'conflict': True,
                'sheet': sheet,
            }
    return 200, get_sheet(c, y, m)
