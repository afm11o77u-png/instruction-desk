"""備品棚卸の発注メール準備（Thunderbird 起動・Excel 添付／本文記載）。

実送信はしない。ユーザーがメールクライアントで確認してから送信する。
購入数量への加算は「準備成功後」のみ。失敗時は加算しない。
prepKey で二重クリック／再試行を冪等化する。
注文書テンプレートは読み取り専用コピーのみ（原本は上書きしない）。
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import subprocess
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, BadZipFile

import kintai_bihin

# --- modes ---
MODE_EXCEL = 'excel'
MODE_BODY = 'body'
MODE_LABELS = {
    MODE_EXCEL: 'Excel添付',
    MODE_BODY: '本文に記載',
}

DEFAULT_SUBJECT = '【備品発注】{date}／{vendor}'
DEFAULT_BODY_EXCEL = (
    '{vendor} ご担当者様\n\n'
    'お疲れ様です。備品の発注をお願いいたします。\n'
    '明細は添付の注文書をご確認ください。\n\n'
    'よろしくお願いいたします。\n'
)
DEFAULT_BODY_INLINE = (
    '{vendor} ご担当者様\n\n'
    'お疲れ様です。下記のとおり備品の発注をお願いいたします。\n\n'
    '{lines}\n\n'
    'よろしくお願いいたします。\n'
)

# Desktop path on the office PC (read-only). Cloud / other PCs use repo fixture when missing.
DESKTOP_TEMPLATE = Path(r'C:\Users\zfm11\Desktop\注文書_エンパイア発寒支店.xlsx')
REPO_FIXTURE_TEMPLATE = Path(__file__).resolve().parent / 'assets' / 'bihin-order' / '注文書_エンパイア発寒支店.xlsx'

# Empire CT Planning order form layout (注文書_エンパイア発寒支店.xlsx).
# Fill only these cells; never invent a simple replacement sheet.
ORDER_SHEET_NAME = '注文書'
ORDER_SHEET_DATE_CELL = 'H3'
ORDER_SHEET_HEADER_MARKERS = ('品', '名')  # B18 header contains 品……名
ORDER_SHEET_DATA_START_ROW = 19
ORDER_SHEET_DATA_END_ROW = 31
ORDER_SHEET_MAX_LINES = ORDER_SHEET_DATA_END_ROW - ORDER_SHEET_DATA_START_ROW + 1
ORDER_SHEET_NAME_COL = 'B'
ORDER_SHEET_QTY_COL = 'D'
ORDER_SHEET_UNIT_COL = 'E'
ORDER_SHEET_NOTE_COL = 'H'
# Sample unit-price / amount columns — clear when rewriting lines so stale sample
# numbers do not remain; formulas G32:G34 are left untouched.
ORDER_SHEET_CLEAR_COLS = ('F', 'G')

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
# Match a single <c r="X99" .../> or <c r="X99" ...>...</c> element.
_CELL_XML_RE = re.compile(
    r'<c\s+r="([A-Z]+\d+)"([^>]*?)(?:/>|>(.*?)</c>)',
    re.DOTALL,
)


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=' ')


def _actor_name(user) -> str:
    if not user:
        return ''
    try:
        return str(user['name'] or user['login'] or '')[:80]
    except (KeyError, IndexError, TypeError):
        return ''


def _mail_out_dir() -> Path:
    root = Path(os.environ.get('INSTRUCTION_DATA', str(Path(__file__).resolve().parent / 'data')))
    d = root / 'bihin-order-out'
    d.mkdir(parents=True, exist_ok=True)
    return d


def schema(c):
    """発注用テーブル。既存 bihin / shift は触らない。"""
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_bihin_vendors (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            subject_template TEXT NOT NULL DEFAULT '',
            body_template TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'excel',
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )'''
    )
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_bihin_orders (
            id INTEGER PRIMARY KEY,
            prep_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            order_date TEXT NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            vendor_id INTEGER,
            vendor_name TEXT NOT NULL DEFAULT '',
            vendor_email TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'excel',
            cancelled INTEGER NOT NULL DEFAULT 0,
            cancelled_at TEXT,
            purchase_applied INTEGER NOT NULL DEFAULT 0,
            mail_opened INTEGER NOT NULL DEFAULT 0,
            attachment_path TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            lines_json TEXT NOT NULL DEFAULT '[]'
        )'''
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_kintai_bihin_orders_prep ON kintai_bihin_orders(prep_key)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_kintai_bihin_orders_created ON kintai_bihin_orders(created_at)'
    )
    # 品目マスタに発注設定列を追加（なければ）
    cols = {r[1] for r in c.execute('PRAGMA table_info(kintai_bihin_items)')}
    alters = [
        ('vendor_id', 'INTEGER'),
        ('order_product_name', "TEXT NOT NULL DEFAULT ''"),
        ('order_unit', "TEXT NOT NULL DEFAULT ''"),
        ('packing_qty', "TEXT NOT NULL DEFAULT ''"),
    ]
    for name, decl in alters:
        if name not in cols:
            c.execute(f'ALTER TABLE kintai_bihin_items ADD COLUMN {name} {decl}')


def resolve_template_path() -> tuple[Path, str]:
    """Return (path, source_note). Never returns a path that should be overwritten."""
    env = (os.environ.get('BIHIN_ORDER_TEMPLATE') or '').strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p, 'env:BIHIN_ORDER_TEMPLATE'
    if DESKTOP_TEMPLATE.is_file():
        return DESKTOP_TEMPLATE, 'desktop'
    if REPO_FIXTURE_TEMPLATE.is_file():
        return REPO_FIXTURE_TEMPLATE, 'repo-fixture'
    raise ValueError(
        '注文書テンプレートが見つかりません。'
        f' 想定パス: {DESKTOP_TEMPLATE} ／ リポジトリ: {REPO_FIXTURE_TEMPLATE}'
    )


def _mode_norm(raw) -> str:
    s = str(raw or '').strip()
    if s in (MODE_EXCEL, 'Excel添付', 'excel添付'):
        return MODE_EXCEL
    if s in (MODE_BODY, '本文に記載', 'body', 'inline'):
        return MODE_BODY
    if s == '':
        return MODE_EXCEL
    raise ValueError('発注モードは「Excel添付」または「本文に記載」を選んでください。')


def _vendor_public(r) -> dict:
    mode = _mode_norm(r['mode'] if r['mode'] not in (None, '') else MODE_EXCEL)
    return {
        'id': int(r['id']),
        'name': r['name'] or '',
        'email': r['email'] or '',
        'subjectTemplate': r['subject_template'] or '',
        'bodyTemplate': r['body_template'] or '',
        'mode': mode,
        'modeLabel': MODE_LABELS[mode],
        'active': bool(r['active']),
        'sortOrder': int(r['sort_order'] or 0),
        'createdAt': r['created_at'] or None,
        'updatedAt': r['updated_at'] or None,
    }


def list_vendors(c, *, include_inactive: bool = False) -> list[dict]:
    if include_inactive:
        rows = c.execute(
            'SELECT * FROM kintai_bihin_vendors ORDER BY sort_order, id'
        ).fetchall()
    else:
        rows = c.execute(
            'SELECT * FROM kintai_bihin_vendors WHERE active=1 ORDER BY sort_order, id'
        ).fetchall()
    return [_vendor_public(r) for r in rows]


def save_vendor(c, user, payload: dict) -> dict:
    payload = payload or {}
    vendor_id = payload.get('id')
    name = str(payload.get('name') or '').strip()[:120]
    email = str(payload.get('email') or '').strip()[:120]
    # Persist real newlines (convert typed "\n" escapes so compose does not show literals).
    subject = _normalize_newlines(str(payload.get('subjectTemplate') or '')).strip()[:500]
    body = _normalize_newlines(str(payload.get('bodyTemplate') or '')).strip()[:4000]
    mode = _mode_norm(payload.get('mode'))
    active = 1 if payload.get('active', True) else 0
    try:
        sort_order = int(payload.get('sortOrder', 0))
    except (TypeError, ValueError):
        sort_order = 0
    if not name:
        raise ValueError('仕入先名を入力してください。')
    if email and not EMAIL_RE.fullmatch(email):
        raise ValueError('仕入先のメールアドレスの形式が不正です。')
    now = _now()
    if vendor_id in (None, ''):
        cur = c.execute(
            '''INSERT INTO kintai_bihin_vendors(
                name, email, subject_template, body_template, mode,
                active, sort_order, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)''',
            (name, email, subject, body, mode, active, sort_order, now, now),
        )
        vid = int(cur.lastrowid)
    else:
        try:
            vid = int(vendor_id)
        except (TypeError, ValueError) as e:
            raise ValueError('仕入先 ID を確認してください。') from e
        row = c.execute('SELECT id FROM kintai_bihin_vendors WHERE id=?', (vid,)).fetchone()
        if not row:
            raise ValueError('仕入先が見つかりません。')
        c.execute(
            '''UPDATE kintai_bihin_vendors
               SET name=?, email=?, subject_template=?, body_template=?, mode=?,
                   active=?, sort_order=?, updated_at=?
               WHERE id=?''',
            (name, email, subject, body, mode, active, sort_order, now, vid),
        )
    row = c.execute('SELECT * FROM kintai_bihin_vendors WHERE id=?', (vid,)).fetchone()
    return _vendor_public(row)


def _parse_packing(raw, *, required: bool) -> Decimal | None:
    if raw is None or raw == '':
        if required:
            raise ValueError('入数（発注単位→棚卸単位の換算）を設定してください。推測では換算しません。')
        return None
    try:
        d = Decimal(str(raw).strip().replace(',', ''))
    except (InvalidOperation, ValueError) as e:
        raise ValueError('入数の形式が不正です。') from e
    if d.is_nan() or d.is_infinite() or d <= 0:
        raise ValueError('入数は正の数を設定してください。推測では換算しません。')
    return d


def inventory_qty_from_order(order_qty: Decimal, packing: Decimal) -> Decimal:
    """発注数量×入数＝棚卸購入数量。入数が無ければ呼び出さない。"""
    return order_qty * packing


def _item_order_fields(r) -> dict:
    packing_raw = r['packing_qty'] if 'packing_qty' in r.keys() else ''
    packing = None
    if packing_raw not in (None, ''):
        try:
            packing = kintai_bihin._dec_to_json(_parse_packing(packing_raw, required=True))
        except ValueError:
            packing = packing_raw
    vendor_id = r['vendor_id'] if 'vendor_id' in r.keys() else None
    if vendor_id in (None, ''):
        vendor_id = None
    else:
        try:
            vendor_id = int(vendor_id)
        except (TypeError, ValueError):
            vendor_id = None
    return {
        'vendorId': vendor_id,
        'orderProductName': (r['order_product_name'] if 'order_product_name' in r.keys() else '') or '',
        'orderUnit': (r['order_unit'] if 'order_unit' in r.keys() else '') or '',
        'packingQty': packing,
    }


# Patch item public/save via helpers used from kintai_bihin after schema migrate
def enrich_item_public(base: dict, row) -> dict:
    base.update(_item_order_fields(row))
    return base


def apply_item_order_fields(c, item_id: int, payload: dict):
    vendor_id = payload.get('vendorId')
    if vendor_id in (None, ''):
        vendor_id = None
    else:
        try:
            vendor_id = int(vendor_id)
        except (TypeError, ValueError) as e:
            raise ValueError('仕入先を確認してください。') from e
        if vendor_id is not None:
            v = c.execute('SELECT id FROM kintai_bihin_vendors WHERE id=?', (vendor_id,)).fetchone()
            if not v:
                raise ValueError('仕入先が見つかりません。')
    order_name = str(payload.get('orderProductName') or '').strip()[:120]
    order_unit = str(payload.get('orderUnit') or '').strip()[:40]
    packing_raw = payload.get('packingQty')
    packing_s = ''
    if packing_raw not in (None, ''):
        packing_s = format(_parse_packing(packing_raw, required=True), 'f')
    c.execute(
        '''UPDATE kintai_bihin_items
           SET vendor_id=?, order_product_name=?, order_unit=?, packing_qty=?
           WHERE id=?''',
        (vendor_id, order_name, order_unit, packing_s, item_id),
    )


def _render_template(tpl: str, mapping: dict) -> str:
    out = tpl
    for key, val in mapping.items():
        out = out.replace('{' + key + '}', str(val))
    return out


def _format_lines_block(lines: list[dict]) -> str:
    parts = []
    for ln in lines:
        name = ln.get('orderProductName') or ln.get('itemName') or ''
        qty = ln.get('orderQty')
        unit = ln.get('orderUnit') or ''
        parts.append(f'・{name}　{qty}{unit}')
    return '\n'.join(parts)


def _normalize_newlines(text: str) -> str:
    """Turn typed/literal \\n escapes into real newlines; keep real newlines.

    Thunderbird was showing literal \\n\\n because templates/compose escaped
    newlines as two-character sequences. Save and compose both normalize here.
    """
    if text is None:
        return ''
    s = str(text).replace('\r\n', '\n').replace('\r', '\n')
    # Convert literal backslash-n / backslash-r sequences (not real CRs).
    s = s.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\r', '\n')
    return s


def _xml_escape_text(value: str) -> str:
    return (
        str(value)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('\n', '&#10;')
    )


def _cell_attrs_style(attrs: str) -> str:
    """Keep only the style index from existing cell attrs (preserve borders/fonts)."""
    m = re.search(r'\ss="(\d+)"', attrs or '')
    return f' s="{m.group(1)}"' if m else ''


def _build_cell_xml(ref: str, attrs: str, value) -> str:
    style = _cell_attrs_style(attrs)
    if value is None or value == '':
        return f'<c r="{ref}"{style}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}"{style} t="inlineStr"><is><t>{_xml_escape_text(str(value))}</t></is></c>'
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, Decimal):
            num = format(value, 'f')
            if '.' in num:
                num = num.rstrip('0').rstrip('.')
        else:
            num = str(value)
        return f'<c r="{ref}"{style}><v>{num}</v></c>'
    text = _xml_escape_text(str(value))
    return (
        f'<c r="{ref}"{style} t="inlineStr">'
        f'<is><t xml:space="preserve">{text}</t></is></c>'
    )


def _set_sheet_cells(sheet_xml: str, updates: dict) -> str:
    """Replace existing <c r=".."> nodes; refuse missing mapped refs."""
    missing = [ref for ref in updates if f'r="{ref}"' not in sheet_xml]
    if missing:
        raise ValueError(
            '注文書テンプレートに必要なセルがありません（'
            + ', '.join(missing)
            + '）。レイアウトが想定と異なるため、簡易表への置き換えはしません。'
            ' 原本レイアウトを合わせるか BIHIN_ORDER_TEMPLATE で互換コピーを指定してください。'
        )

    def repl(match: re.Match) -> str:
        ref = match.group(1)
        if ref not in updates:
            return match.group(0)
        return _build_cell_xml(ref, match.group(2) or '', updates[ref])

    return _CELL_XML_RE.sub(repl, sheet_xml)


def _strip_calc_chain_ref(calc_xml: str, ref: str) -> str:
    """Drop a formula cell from calcChain after replacing it with a value."""
    return re.sub(rf'<c\s+r="{re.escape(ref)}"[^/]*/>', '', calc_xml)


def _shared_string_plain(si_xml: str) -> str:
    """Extract display text from a sharedStrings <si>, ignoring phonetic <rPh>."""
    # Remove phonetic runs first so their <t> do not concatenate into the label.
    cleaned = re.sub(r'<rPh\b[^>]*>.*?</rPh>', '', si_xml, flags=re.DOTALL)
    parts = re.findall(r'<t[^>]*>(.*?)</t>', cleaned, flags=re.DOTALL)
    if not parts:
        return ''
    text = ''.join(parts)
    return (
        text.replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&amp;', '&')
        .replace('&#10;', '\n')
    )


def _load_shared_strings(sst_xml: str) -> list[str]:
    return [_shared_string_plain(si) for si in re.findall(r'<si\b[^>]*>.*?</si>', sst_xml, flags=re.DOTALL)]


def _cell_display_value(cell_xml: str, shared: list[str]):
    if 't="s"' in cell_xml or "t='s'" in cell_xml:
        m = re.search(r'<v>(\d+)</v>', cell_xml)
        if not m:
            return ''
        idx = int(m.group(1))
        return shared[idx] if 0 <= idx < len(shared) else ''
    if 't="inlineStr"' in cell_xml:
        parts = re.findall(r'<t[^>]*>(.*?)</t>', cell_xml, flags=re.DOTALL)
        return ''.join(parts)
    m = re.search(r'<f[^>]*>(.*?)</f>', cell_xml, flags=re.DOTALL)
    if m:
        return f'={m.group(1)}'
    m = re.search(r'<v>(.*?)</v>', cell_xml, flags=re.DOTALL)
    return m.group(1) if m else ''


def _find_cell_xml(sheet_xml: str, ref: str) -> str | None:
    m = re.search(
        rf'<c\s+r="{re.escape(ref)}"[^>]*?(?:/>|>.*?</c>)',
        sheet_xml,
        flags=re.DOTALL,
    )
    return m.group(0) if m else None


def validate_order_template(template_path: Path) -> None:
    """Ensure template matches Empire order layout. Raises ValueError with reason."""
    try:
        raw = template_path.read_bytes()
    except OSError as e:
        raise ValueError(f'注文書テンプレートを読めません: {e}') from e
    try:
        zin = ZipFile(BytesIO(raw))
    except BadZipFile as e:
        raise ValueError('注文書テンプレートが壊れているか、Excel形式ではありません。') from e
    try:
        names = zin.namelist()
        sheet_name = next(
            (n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')),
            None,
        )
        if not sheet_name:
            raise ValueError('注文書テンプレートにワークシートがありません。簡易表への置き換えはしません。')
        sheet_xml = zin.read(sheet_name).decode('utf-8')
        sst = zin.read('xl/sharedStrings.xml').decode('utf-8') if 'xl/sharedStrings.xml' in names else ''
        shared = _load_shared_strings(sst) if sst else []
        # Workbook sheet title
        if 'xl/workbook.xml' in names:
            wb = zin.read('xl/workbook.xml').decode('utf-8')
            if f'name="{ORDER_SHEET_NAME}"' not in wb and f"name='{ORDER_SHEET_NAME}'" not in wb:
                raise ValueError(
                    f'注文書テンプレートのシート名が「{ORDER_SHEET_NAME}」ではありません。'
                    ' 想定レイアウトと異なるため記入できません（簡易表へは落とさない）。'
                )
        required = [ORDER_SHEET_DATE_CELL]
        for row in range(ORDER_SHEET_DATA_START_ROW, ORDER_SHEET_DATA_END_ROW + 1):
            required.extend([
                f'{ORDER_SHEET_NAME_COL}{row}',
                f'{ORDER_SHEET_QTY_COL}{row}',
                f'{ORDER_SHEET_UNIT_COL}{row}',
                f'{ORDER_SHEET_NOTE_COL}{row}',
            ])
        missing = [ref for ref in required if _find_cell_xml(sheet_xml, ref) is None]
        if missing:
            raise ValueError(
                '注文書テンプレートに必要なセルがありません（'
                + ', '.join(missing[:8])
                + ('…' if len(missing) > 8 else '')
                + '）。レイアウト不一致のため簡易表への置き換えはしません。'
            )
        header = _find_cell_xml(sheet_xml, 'B18')
        if not header:
            raise ValueError(
                '注文書テンプレートに明細見出し（B18）がありません。'
                ' 想定レイアウトと異なるため記入できません。'
            )
        label = _cell_display_value(header, shared)
        if not all(mark in label for mark in ORDER_SHEET_HEADER_MARKERS):
            raise ValueError(
                f'注文書テンプレートの B18 が見出し「品名」ではありません（取得値: {label!r}）。'
                ' レイアウト不一致のため簡易表への置き換えはしません。'
            )
    finally:
        zin.close()


def fill_order_workbook(
    template_path: Path,
    *,
    order_date: str,
    vendor_name: str = '',
    lines: list[dict],
) -> bytes:
    """Copy template zip and fill only date/item/qty cells. Never writes template_path.

    vendor_name is accepted for API compatibility but not written: the Empire form
    letterhead already names the supplier (CT Planning). Silent layout fallback is
    forbidden — validate_order_template must pass.
    """
    del vendor_name  # letterhead / 仕入先欄はテンプレ固定。上書きしない。
    validate_order_template(template_path)
    if len(lines) > ORDER_SHEET_MAX_LINES:
        raise ValueError(
            f'注文明細がテンプレートの行数上限（{ORDER_SHEET_MAX_LINES}行、'
            f'{ORDER_SHEET_DATA_START_ROW}〜{ORDER_SHEET_DATA_END_ROW}行目）を超えています。'
            ' 分割して発注するか、テンプレートの明細行を増やした互換コピーを'
            ' BIHIN_ORDER_TEMPLATE で指定してください。'
        )

    try:
        raw = template_path.read_bytes()
    except OSError as e:
        raise ValueError(f'注文書テンプレートを読めません: {e}') from e
    try:
        zin = ZipFile(BytesIO(raw))
    except BadZipFile as e:
        raise ValueError('注文書テンプレートが壊れているか、Excel形式ではありません。') from e

    try:
        names = list(zin.namelist())
        sheet_name = next(
            n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')
        )
        sheet_xml = zin.read(sheet_name).decode('utf-8')

        # Prefer a readable Japanese date for 注文日 (H3).
        try:
            day = date.fromisoformat(str(order_date)[:10])
            date_label = f'{day.year}/{day.month:02d}/{day.day:02d}'
        except ValueError:
            date_label = str(order_date)

        updates: dict = {ORDER_SHEET_DATE_CELL: date_label}
        for i in range(ORDER_SHEET_MAX_LINES):
            row = ORDER_SHEET_DATA_START_ROW + i
            if i < len(lines):
                ln = lines[i]
                name = ln.get('orderProductName') or ln.get('itemName') or ''
                qty = ln.get('orderQty')
                unit = ln.get('orderUnit') or ''
                note = ln.get('note') or ''
                updates[f'{ORDER_SHEET_NAME_COL}{row}'] = name
                updates[f'{ORDER_SHEET_QTY_COL}{row}'] = qty
                updates[f'{ORDER_SHEET_UNIT_COL}{row}'] = unit
                updates[f'{ORDER_SHEET_NOTE_COL}{row}'] = note
            else:
                updates[f'{ORDER_SHEET_NAME_COL}{row}'] = ''
                updates[f'{ORDER_SHEET_QTY_COL}{row}'] = ''
                updates[f'{ORDER_SHEET_UNIT_COL}{row}'] = ''
                updates[f'{ORDER_SHEET_NOTE_COL}{row}'] = ''
            # Always clear sample unit-price / amount on line rows.
            for col in ORDER_SHEET_CLEAR_COLS:
                updates[f'{col}{row}'] = ''

        new_sheet = _set_sheet_cells(sheet_xml, updates)

        out = BytesIO()
        with ZipFile(out, 'w', ZIP_DEFLATED) as zout:
            for n in names:
                data = zin.read(n)
                if n == sheet_name:
                    zout.writestr(n, new_sheet.encode('utf-8'))
                elif n == 'xl/calcChain.xml':
                    # H3 was =TODAY(); now a static value — drop from calcChain to
                    # avoid Excel "repaired" prompts.
                    calc = data.decode('utf-8')
                    calc = _strip_calc_chain_ref(calc, ORDER_SHEET_DATE_CELL)
                    zout.writestr(n, calc.encode('utf-8'))
                else:
                    # Preserve drawings, printerSettings, styles, sharedStrings, etc.
                    zout.writestr(n, data)
        return out.getvalue()
    finally:
        zin.close()


def thunderbird_path() -> str:
    try:
        import winreg  # type: ignore
    except ImportError as e:
        raise ValueError('この環境では Thunderbird 連携（Windows）を使えません。') from e
    keys = (
        (winreg.HKEY_CURRENT_USER, r'Software\Clients\Mail\Mozilla Thunderbird\shell\open\command'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Clients\Mail\Mozilla Thunderbird\shell\open\command'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Clients\Mail\Mozilla Thunderbird\shell\open\command'),
    )
    for root, name in keys:
        try:
            with winreg.OpenKey(root, name) as key:
                command = winreg.QueryValue(key, '')
        except OSError:
            continue
        match = re.match(r'"([^"]+)"', command) or re.match(r'(\S+)', command)
        if match and Path(match.group(1)).is_file():
            return match.group(1)
    fallback = Path(r'C:\Program Files\Mozilla Thunderbird\thunderbird.exe')
    if fallback.is_file():
        return str(fallback)
    raise ValueError('このパソコンのThunderbirdが見つかりません。')


def open_compose(*, to: str, subject: str, body: str, attachment: str | None = None, popen=subprocess.Popen, tb_path=None):
    """Open Thunderbird compose. Does not send. Keeps to/subject/body/attachment + identity signature.

    Newlines: do NOT pass literal \\n (Thunderbird shows them as text). Use HTML
    body with <br> and format=1 so real line breaks appear; the account signature
    is still appended by Thunderbird's compose window.
    """
    subject_one = _normalize_newlines(subject).replace('\n', ' ').strip()
    body_norm = _normalize_newlines(body)
    # HTML body: escape, then real newlines → <br> (not the two-char sequence \\n).
    body_html = html.escape(body_norm, quote=True).replace('\n', '<br>')
    if any("'" in part for part in (to, subject_one, body_html, attachment or '')):
        raise ValueError('メールの内容に単引用符は使えません。テンプレートを見直してください。')
    parts = [
        f"to='{to}'",
        f"subject='{subject_one}'",
        f"body='{body_html}'",
        "format='1'",
    ]
    if attachment:
        parts.append(f"attachment='{attachment}'")
    compose = ','.join(parts)
    exe = tb_path or thunderbird_path()
    try:
        popen([exe, '-compose', compose])
    except OSError as e:
        raise ValueError('Thunderbirdを開けませんでした。') from e


def _parse_order_date(raw) -> date:
    if raw in (None, ''):
        return date.today()
    s = str(raw).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError as e:
        raise ValueError('発注日を確認してください（YYYY-MM-DD）。') from e


def _validate_and_build_groups(c, lines_in: list) -> list[dict]:
    if not isinstance(lines_in, list) or not lines_in:
        raise ValueError('発注する品目と数量を1行以上入力してください。')
    groups: dict[int, dict] = {}
    for i, raw in enumerate(lines_in):
        if not isinstance(raw, dict):
            raise ValueError(f'発注明細の形式が不正です（{i + 1}件目）。')
        try:
            item_id = int(raw.get('itemId'))
        except (TypeError, ValueError) as e:
            raise ValueError(f'{i + 1}件目の品目を確認してください。') from e
        order_qty = kintai_bihin._qty(raw.get('orderQty'), field=f'{i + 1}件目の発注数量')
        if order_qty is None or order_qty <= 0:
            raise ValueError(f'{i + 1}件目の発注数量は正の数を入力してください。')

        item = c.execute('SELECT * FROM kintai_bihin_items WHERE id=?', (item_id,)).fetchone()
        if not item or not item['active']:
            raise ValueError(f'{i + 1}件目の品目が見つかりません。')
        vendor_id = item['vendor_id'] if 'vendor_id' in item.keys() else None
        if vendor_id in (None, ''):
            raise ValueError(f'「{item["name"]}」に仕入先が設定されていません。発注設定で仕入先を割り当ててください。')
        vendor_id = int(vendor_id)
        vendor = c.execute(
            'SELECT * FROM kintai_bihin_vendors WHERE id=? AND active=1', (vendor_id,)
        ).fetchone()
        if not vendor:
            raise ValueError(f'「{item["name"]}」の仕入先が見つかりません。')
        email = (vendor['email'] or '').strip()
        if not email or not EMAIL_RE.fullmatch(email):
            raise ValueError(
                f'仕入先「{vendor["name"]}」のメールアドレスが未設定または不正です。発注の前に設定してください。'
            )

        packing = _parse_packing(
            item['packing_qty'] if 'packing_qty' in item.keys() else '',
            required=True,
        )
        assert packing is not None
        inv_qty = inventory_qty_from_order(order_qty, packing)
        order_name = (item['order_product_name'] if 'order_product_name' in item.keys() else '') or ''
        order_name = order_name.strip() or (item['name'] or '')
        order_unit = (item['order_unit'] if 'order_unit' in item.keys() else '') or ''
        order_unit = order_unit.strip()
        if not order_unit:
            raise ValueError(f'「{item["name"]}」の発注単位が未設定です。発注設定で単位を入力してください。')

        line = {
            'itemId': item_id,
            'itemName': item['name'] or '',
            'inventoryUnit': item['unit'] or '',
            'orderProductName': order_name,
            'orderUnit': order_unit,
            'orderQty': kintai_bihin._dec_to_json(order_qty),
            'packingQty': kintai_bihin._dec_to_json(packing),
            'inventoryQty': kintai_bihin._dec_to_json(inv_qty),
            'note': str(raw.get('note') or '')[:120],
        }
        g = groups.get(vendor_id)
        if not g:
            mode = _mode_norm(vendor['mode'])
            g = {
                'vendorId': vendor_id,
                'vendorName': vendor['name'] or '',
                'vendorEmail': email,
                'mode': mode,
                'subjectTemplate': vendor['subject_template'] or '',
                'bodyTemplate': vendor['body_template'] or '',
                'lines': [],
            }
            groups[vendor_id] = g
        g['lines'].append(line)
    return list(groups.values())


def _add_purchase_for_lines(c, user, year: int, month: int, lines: list[dict]) -> dict:
    """Add inventory qty to purchaseQty for each line. Does not overwrite; adds to existing."""
    sheet = kintai_bihin.get_sheet(c, year, month)
    rows = [dict(r) for r in sheet['rows']]
    by_item = {r.get('itemId'): r for r in rows if r.get('itemId') is not None}
    for ln in lines:
        iid = ln['itemId']
        add = kintai_bihin._qty(ln['inventoryQty'], field='購入加算')
        assert add is not None
        row = by_item.get(iid)
        if not row:
            # sheet may be unsaved without this item if inactive — append from master
            item = c.execute('SELECT * FROM kintai_bihin_items WHERE id=?', (iid,)).fetchone()
            if not item:
                raise ValueError('品目が見つかりません。')
            price = kintai_bihin._price(item['unit_price'] or '0', field='単価')
            row = {
                'rowKey': kintai_bihin._new_row_key(),
                'itemId': iid,
                'category': item['category'] or '',
                'name': item['name'] or '',
                'unit': item['unit'] or '',
                'unitPrice': kintai_bihin._dec_to_json(price),
                'sortOrder': len(rows),
                'openingQty': None,
                'purchaseQty': None,
                'closingQty': None,
            }
            rows.append(row)
            by_item[iid] = row
        cur = kintai_bihin._qty(row.get('purchaseQty'), field='購入数量')
        base = cur if cur is not None else Decimal('0')
        row['purchaseQty'] = kintai_bihin._dec_to_json(base + add)
    code, saved = kintai_bihin.save_sheet(c, user, {
        'year': year,
        'month': month,
        'version': sheet['version'],
        'rows': rows,
    })
    if code == 409:
        # one retry with fresh version
        sheet2 = kintai_bihin.get_sheet(c, year, month)
        rows2 = [dict(r) for r in sheet2['rows']]
        by2 = {r.get('itemId'): r for r in rows2 if r.get('itemId') is not None}
        for ln in lines:
            iid = ln['itemId']
            add = kintai_bihin._qty(ln['inventoryQty'], field='購入加算')
            assert add is not None
            row = by2.get(iid)
            if not row:
                raise ValueError('棚卸月の行が見つかりません。品目マスタと月シートを確認してください。')
            cur = kintai_bihin._qty(row.get('purchaseQty'), field='購入数量')
            base = cur if cur is not None else Decimal('0')
            row['purchaseQty'] = kintai_bihin._dec_to_json(base + add)
        code, saved = kintai_bihin.save_sheet(c, user, {
            'year': year,
            'month': month,
            'version': sheet2['version'],
            'rows': rows2,
        })
    if code != 200:
        raise ValueError(saved.get('error') or '当月購入への加算に失敗しました。発注準備は中止しました。')
    return saved


def _subtract_purchase_for_lines(c, user, year: int, month: int, lines: list[dict]) -> dict:
    sheet = kintai_bihin.get_sheet(c, year, month)
    if not sheet['exists']:
        return sheet
    rows = [dict(r) for r in sheet['rows']]
    by_item = {r.get('itemId'): r for r in rows if r.get('itemId') is not None}
    for ln in lines:
        iid = ln['itemId']
        sub = kintai_bihin._qty(ln['inventoryQty'], field='購入取消')
        assert sub is not None
        row = by_item.get(iid)
        if not row:
            continue
        cur = kintai_bihin._qty(row.get('purchaseQty'), field='購入数量')
        if cur is None:
            continue
        nxt = cur - sub
        if nxt < 0:
            nxt = Decimal('0')
        row['purchaseQty'] = kintai_bihin._dec_to_json(nxt)
    code, saved = kintai_bihin.save_sheet(c, user, {
        'year': year,
        'month': month,
        'version': sheet['version'],
        'rows': rows,
    })
    if code == 409:
        sheet2 = kintai_bihin.get_sheet(c, year, month)
        rows2 = [dict(r) for r in sheet2['rows']]
        by2 = {r.get('itemId'): r for r in rows2 if r.get('itemId') is not None}
        for ln in lines:
            iid = ln['itemId']
            sub = kintai_bihin._qty(ln['inventoryQty'], field='購入取消')
            assert sub is not None
            row = by2.get(iid)
            if not row:
                continue
            cur = kintai_bihin._qty(row.get('purchaseQty'), field='購入数量')
            if cur is None:
                continue
            nxt = cur - sub
            if nxt < 0:
                nxt = Decimal('0')
            row['purchaseQty'] = kintai_bihin._dec_to_json(nxt)
        code, saved = kintai_bihin.save_sheet(c, user, {
            'year': year, 'month': month, 'version': sheet2['version'], 'rows': rows2,
        })
    if code != 200:
        raise ValueError(saved.get('error') or '当月購入の取消に失敗しました。')
    return saved


def _order_public(row, lines=None) -> dict:
    mode = _mode_norm(row['mode'])
    if lines is None:
        try:
            lines = json.loads(row['lines_json'] or '[]')
        except json.JSONDecodeError:
            lines = []
    return {
        'id': int(row['id']),
        'prepKey': row['prep_key'],
        'createdAt': row['created_at'],
        'orderDate': row['order_date'],
        'year': int(row['year']),
        'month': int(row['month']),
        'vendorId': row['vendor_id'],
        'vendorName': row['vendor_name'] or '',
        'vendorEmail': row['vendor_email'] or '',
        'mode': mode,
        'modeLabel': MODE_LABELS[mode],
        'cancelled': bool(row['cancelled']),
        'cancelledAt': row['cancelled_at'],
        'purchaseApplied': bool(row['purchase_applied']),
        'mailOpened': bool(row['mail_opened']),
        # 送信済みとは言わない（ユーザー確認送信）
        'statusLabel': '取消済み' if row['cancelled'] else ('メール作成済み（未送信のまま）' if row['mail_opened'] else '準備中'),
        'attachmentPath': row['attachment_path'] or '',
        'subject': row['subject'] or '',
        'body': row['body'] or '',
        'createdBy': row['created_by'] or '',
        'lines': lines,
    }


def list_order_history(c, *, limit: int = 100) -> list[dict]:
    try:
        lim = max(1, min(500, int(limit)))
    except (TypeError, ValueError):
        lim = 100
    rows = c.execute(
        'SELECT * FROM kintai_bihin_orders ORDER BY id DESC LIMIT ?', (lim,)
    ).fetchall()
    return [_order_public(r) for r in rows]


def history_flat(c, *, limit: int = 200) -> list[dict]:
    """Flat history rows: datetime, vendor, item, qty, month, mode, cancelled."""
    orders = list_order_history(c, limit=limit)
    flat = []
    for o in orders:
        for ln in o.get('lines') or []:
            flat.append({
                'orderId': o['id'],
                'datetime': o['createdAt'],
                'vendor': o['vendorName'],
                'item': ln.get('orderProductName') or ln.get('itemName') or '',
                'orderQty': ln.get('orderQty'),
                'orderUnit': ln.get('orderUnit') or '',
                'inventoryQty': ln.get('inventoryQty'),
                'inventoryUnit': ln.get('inventoryUnit') or '',
                'year': o['year'],
                'month': o['month'],
                'mode': o['mode'],
                'modeLabel': o['modeLabel'],
                'cancelled': o['cancelled'],
                # 明示的に送信済みフラグは出さない
            })
    return flat


def _orders_for_prep(c, prep_key: str) -> list:
    return c.execute(
        'SELECT * FROM kintai_bihin_orders WHERE prep_key=? ORDER BY id', (prep_key,)
    ).fetchall()


def prepare_orders(
    c,
    user,
    payload: dict,
    *,
    open_mail: bool = True,
    popen=subprocess.Popen,
    tb_path=None,
    skip_mail: bool = False,
) -> dict:
    """Validate → open mail → on success add purchase + history.

    skip_mail: tests / environments without Thunderbird — still records as mailOpened
    only when open_mail is False intentionally after successful "compose" mock.
    """
    payload = payload or {}
    prep_key = str(payload.get('prepKey') or '').strip() or secrets.token_hex(16)
    if len(prep_key) > 80:
        raise ValueError('prepKey が長すぎます。')

    existing = _orders_for_prep(c, prep_key)
    if existing:
        # Idempotent / regenerate: never re-add purchase for an existing prepKey.
        regenerate = bool(payload.get('regenerateMail') or payload.get('regenerate'))
        template_source = None
        if regenerate and (open_mail or skip_mail):
            mail_dir = _mail_out_dir()
            for row in existing:
                if row['cancelled']:
                    continue
                try:
                    lines = json.loads(row['lines_json'] or '[]')
                except json.JSONDecodeError:
                    lines = []
                attachment_uri = None
                attachment_path = row['attachment_path'] or ''
                if _mode_norm(row['mode']) == MODE_EXCEL:
                    template_path, template_source = resolve_template_path()
                    blob = fill_order_workbook(
                        template_path,
                        order_date=row['order_date'],
                        vendor_name=row['vendor_name'] or '',
                        lines=lines,
                    )
                    fname = (
                        f"bihin-order-{prep_key[:12]}-v{row['vendor_id']}-"
                        f"{row['order_date']}-regen.xlsx"
                    )
                    path = mail_dir / fname
                    path.write_bytes(blob)
                    attachment_path = str(path)
                    attachment_uri = path.as_uri()
                    if template_path.resolve() == path.resolve():
                        raise ValueError('注文書テンプレートを上書きしようとしました。中止します。')
                    c.execute(
                        'UPDATE kintai_bihin_orders SET attachment_path=? WHERE id=?',
                        (attachment_path, int(row['id'])),
                    )
                elif attachment_path:
                    try:
                        attachment_uri = Path(attachment_path).as_uri()
                    except OSError:
                        attachment_uri = None
                if open_mail and not skip_mail:
                    open_compose(
                        to=row['vendor_email'] or '',
                        subject=row['subject'] or '',
                        body=row['body'] or '',
                        attachment=attachment_uri,
                        popen=popen,
                        tb_path=tb_path,
                    )
            fresh = _orders_for_prep(c, prep_key)
            return {
                'prepKey': prep_key,
                'idempotent': True,
                'regenerated': True,
                'orders': [_order_public(r) for r in fresh],
                'message': (
                    '既存の発注のメール／添付を再作成しました'
                    '（当月購入は二重加算していません）。'
                ),
                'templateSource': template_source,
            }
        return {
            'prepKey': prep_key,
            'idempotent': True,
            'regenerated': False,
            'orders': [_order_public(r) for r in existing],
            'message': '同じ発注準備は既に完了しています（二重加算しません）。',
            'templateSource': None,
        }

    order_day = _parse_order_date(payload.get('orderDate'))
    year, month = order_day.year, order_day.month
    groups = _validate_and_build_groups(c, payload.get('lines') or [])

    template_path = None
    template_source = None
    if any(g['mode'] == MODE_EXCEL for g in groups):
        template_path, template_source = resolve_template_path()

    # Build compose payloads first (no DB / no purchase yet)
    prepared = []
    mail_dir = _mail_out_dir()
    # cleanup old attachments (>7d)
    cutoff = datetime.now().timestamp() - 7 * 86400
    for old in mail_dir.glob('bihin-order-*.xlsx'):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass

    date_label = f'{order_day.year}年{order_day.month}月{order_day.day}日'
    for g in groups:
        mapping = {
            'vendor': g['vendorName'],
            'date': date_label,
            'lines': _format_lines_block(g['lines']),
            'year': year,
            'month': month,
        }
        subject_tpl = _normalize_newlines(g['subjectTemplate'] or DEFAULT_SUBJECT)
        if g['mode'] == MODE_BODY:
            body_tpl = _normalize_newlines(g['bodyTemplate'] or DEFAULT_BODY_INLINE)
        else:
            body_tpl = _normalize_newlines(g['bodyTemplate'] or DEFAULT_BODY_EXCEL)
        subject = _normalize_newlines(
            _render_template(subject_tpl, mapping)
        ).strip() or DEFAULT_SUBJECT.format(**mapping)
        body = _normalize_newlines(_render_template(body_tpl, mapping))
        # Body mode must clearly show name/qty/unit
        saved_body_tpl = _normalize_newlines(g['bodyTemplate'] or '')
        if g['mode'] == MODE_BODY and '{lines}' not in (saved_body_tpl or DEFAULT_BODY_INLINE):
            if _format_lines_block(g['lines']) not in body:
                body = body.rstrip() + '\n\n' + _format_lines_block(g['lines']) + '\n'

        attachment_uri = None
        attachment_path = ''
        if g['mode'] == MODE_EXCEL:
            assert template_path is not None
            blob = fill_order_workbook(
                template_path,
                order_date=order_day.isoformat(),
                vendor_name=g['vendorName'],
                lines=g['lines'],
            )
            fname = f"bihin-order-{prep_key[:12]}-v{g['vendorId']}-{order_day.isoformat()}.xlsx"
            path = mail_dir / fname
            path.write_bytes(blob)
            attachment_path = str(path)
            attachment_uri = path.as_uri()
            # Ensure template untouched
            if template_path.resolve() == path.resolve():
                raise ValueError('注文書テンプレートを上書きしようとしました。中止します。')

        prepared.append({
            'group': g,
            'subject': subject,
            'body': body,
            'attachmentPath': attachment_path,
            'attachmentUri': attachment_uri,
        })

    # Open mail clients BEFORE purchase apply
    if open_mail and not skip_mail:
        for p in prepared:
            open_compose(
                to=p['group']['vendorEmail'],
                subject=p['subject'],
                body=p['body'],
                attachment=p['attachmentUri'],
                popen=popen,
                tb_path=tb_path,
            )
    elif open_mail and skip_mail:
        # Test / Linux: treat as compose opened without spawning Thunderbird
        pass

    # Success path: apply purchase then insert history
    now = _now()
    actor = _actor_name(user)
    orders_out = []
    for p in prepared:
        g = p['group']
        try:
            _add_purchase_for_lines(c, user, year, month, g['lines'])
        except Exception:
            # Purchase failed after mail opened — do not write history claiming applied.
            # Leave any opened compose for the user; no DB order rows for this prep_key yet.
            raise
        cur = c.execute(
            '''INSERT INTO kintai_bihin_orders(
                prep_key, created_at, order_date, year, month,
                vendor_id, vendor_name, vendor_email, mode,
                cancelled, cancelled_at, purchase_applied, mail_opened,
                attachment_path, subject, body, created_by, lines_json
            ) VALUES (?,?,?,?,?,?,?,?,?,0,NULL,1,?,?,?,?,?,?)''',
            (
                prep_key, now, order_day.isoformat(), year, month,
                g['vendorId'], g['vendorName'], g['vendorEmail'], g['mode'],
                1 if (open_mail or skip_mail) else 0,
                p['attachmentPath'], p['subject'], p['body'], actor,
                json.dumps(g['lines'], ensure_ascii=False),
            ),
        )
        oid = int(cur.lastrowid)
        row = c.execute('SELECT * FROM kintai_bihin_orders WHERE id=?', (oid,)).fetchone()
        orders_out.append(_order_public(row, g['lines']))

    return {
        'prepKey': prep_key,
        'idempotent': False,
        'orders': orders_out,
        'message': (
            'メール作成画面を開きました。内容を確認し、送信はご自身で行ってください'
            '（自動送信しません）。当月購入へ加算しました。'
            if open_mail else
            '発注準備を記録し、当月購入へ加算しました。'
        ),
        'templateSource': template_source,
        'year': year,
        'month': month,
    }


def cancel_order(c, user, payload: dict) -> dict:
    payload = payload or {}
    try:
        oid = int(payload.get('id'))
    except (TypeError, ValueError) as e:
        raise ValueError('発注履歴 ID を確認してください。') from e
    row = c.execute('SELECT * FROM kintai_bihin_orders WHERE id=?', (oid,)).fetchone()
    if not row:
        raise ValueError('発注履歴が見つかりません。')
    if row['cancelled']:
        return {'order': _order_public(row), 'already': True, 'message': '既に取消済みです。'}
    lines = json.loads(row['lines_json'] or '[]')
    if row['purchase_applied']:
        _subtract_purchase_for_lines(c, user, int(row['year']), int(row['month']), lines)
    now = _now()
    c.execute(
        '''UPDATE kintai_bihin_orders
           SET cancelled=1, cancelled_at=?, purchase_applied=0
           WHERE id=? AND cancelled=0''',
        (now, oid),
    )
    fresh = c.execute('SELECT * FROM kintai_bihin_orders WHERE id=?', (oid,)).fetchone()
    return {
        'order': _order_public(fresh),
        'already': False,
        'message': '発注を取消し、この発注分の当月購入加算のみ戻しました。',
    }


def settings_bundle(c) -> dict:
    tpl_note = None
    tpl_path = None
    try:
        p, src = resolve_template_path()
        tpl_path = str(p)
        tpl_note = src
    except ValueError as e:
        tpl_note = f'missing: {e}'
    return {
        'vendors': list_vendors(c, include_inactive=True),
        'items': kintai_bihin.list_items(c, include_inactive=True),
        'modes': [
            {'id': MODE_EXCEL, 'label': MODE_LABELS[MODE_EXCEL]},
            {'id': MODE_BODY, 'label': MODE_LABELS[MODE_BODY]},
        ],
        'templatePath': tpl_path,
        'templateSource': tpl_note,
        'desktopTemplatePath': str(DESKTOP_TEMPLATE),
        'fixtureTemplatePath': str(REPO_FIXTURE_TEMPLATE),
        'defaultSubject': DEFAULT_SUBJECT,
        'hints': {
            'packing': '入数＝発注1単位あたりの棚卸数量。未設定のままでは換算・発注できません。',
            'mail': 'Thunderbird で作成画面を開くだけです。送信済みにはしません。',
            'purchase': '発注日の年月の当月購入へ加算します（表示中の過去月には入れません）。',
        },
    }
