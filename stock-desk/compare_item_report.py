"""旧システム item_report CSV と実在庫DBの商品を、読み取り専用で突き合わせる。

商品・取扱・初期在庫の登録／更新／削除は一切行わない。
sqlite は URI mode=ro で開き、stock.save_* / initialize / apply 等の書き込みAPIは呼ばない。
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
# Nested stock-desk lives under instruction-desk; real DB is sibling data/stock.sqlite3
REPO_ROOT = SCRIPT_ROOT.parent

# Reuse stdlib xlsx writer already used by stock-desk (no openpyxl).
import excel_book  # noqa: E402


REQUIRED_CSV_COLUMNS = ('新古', '仕入先', '顧客', '商品名', '商品コード', 'サイズ', '仕入単価')


def normalize_text(value: str | None) -> str:
    """空白整理と全角半角の揃え。ハイフン／枝番／色番号は削除しない。"""
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = text.replace('\u3000', ' ')
    text = re.sub(r'[ \t\r\n]+', ' ', text).strip()
    return text


def normalize_code(value: str | None) -> str:
    return normalize_text(value)


def normalize_supplier(value: str | None) -> str:
    """仕入先名のざっくり照合用。法人格の表記ゆれを弱く揃える。"""
    text = normalize_text(value)
    for src, dst in (
        ('株式会社', ''),
        ('有限会社', ''),
        ('合同会社', ''),
        ('(株)', ''),
        ('(有)', ''),
        ('㈱', ''),
        ('㈲', ''),
    ):
        text = text.replace(src, dst)
    return normalize_text(text)


def name_core(value: str | None) -> str:
    """商品名比較用。中古印の＊/* と括弧内の刺繍・加工は残したまま比較する。"""
    return normalize_text(value)


def resolve_stock_db_path(
    explicit: str | Path | None = None,
    *,
    allow_standalone: bool = False,
) -> Path:
    """指示書デスクが実際に開く在庫DBパスを解決する。

    本番相当（instruction-desk server.py と同じ）:
      DATA = Path(os.environ.get('INSTRUCTION_DATA', str(REPO_ROOT / 'data')))
      STOCK_DB = DATA / 'stock.sqlite3'

    取り違え防止:
      stock-desk/data/live/stock.sqlite3 … 単体の空台帳
      stock-desk/data/sample/stock.sqlite3 … サンプル
    これらは明示指定 + allow_standalone のときだけ使う。
    """
    if explicit:
        path = Path(explicit).expanduser().resolve()
        _reject_standalone_unless_allowed(path, allow_standalone)
        return path

    env_db = os.environ.get('STOCK_DB') or os.environ.get('COMPARE_STOCK_DB')
    if env_db:
        path = Path(env_db).expanduser().resolve()
        _reject_standalone_unless_allowed(path, allow_standalone)
        return path

    data_dir = Path(os.environ.get('INSTRUCTION_DATA', str(REPO_ROOT / 'data'))).expanduser()
    path = (data_dir / 'stock.sqlite3').resolve()
    _reject_standalone_unless_allowed(path, allow_standalone)
    return path


def _reject_standalone_unless_allowed(path: Path, allow_standalone: bool) -> None:
    if allow_standalone:
        return
    parts = {p.lower() for p in path.parts}
    under_stock_data = 'stock-desk' in parts and 'data' in parts
    if under_stock_data and ('live' in parts or 'sample' in parts):
        raise ValueError(
            '指定されたDBは stock-desk の live/sample 台帳です。'
            '指示書デスクの実在庫は instruction-desk\\data\\stock.sqlite3 です。'
            '意図的に live/sample を使うときは --allow-standalone を付けてください。'
        )


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """sqlite を読み取り専用で開く。ファイルが無いときは明確に失敗する。"""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'在庫DBが見つかりません: {path}')
    uri = path.as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    # Extra guard: reject writes even if a caller forgets mode=ro.
    conn.execute('PRAGMA query_only=ON')
    return conn


def load_web_products(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        '''
        SELECT p.id, p.code, p.name, p.color, p.active,
               p.supplier_id, COALESCE(s.name, '') AS supplier_name
        FROM products p
        LEFT JOIN suppliers s ON s.id = p.supplier_id
        ORDER BY p.id
        '''
    ).fetchall()
    sizes_by_pid: dict[int, list[str]] = defaultdict(list)
    for r in conn.execute(
        'SELECT product_id, label FROM product_sizes ORDER BY product_id, sort_order, id'
    ):
        sizes_by_pid[r['product_id']].append(r['label'])
    products = []
    for r in rows:
        products.append({
            'id': r['id'],
            'code': r['code'] or '',
            'name': r['name'] or '',
            'color': r['color'] or '',
            'active': bool(r['active']),
            'supplier_id': r['supplier_id'],
            'supplier_name': r['supplier_name'] or '',
            'sizes': sizes_by_pid.get(r['id'], []),
            'code_key': normalize_code(r['code']),
            'name_key': name_core(r['name']),
            'color_key': normalize_text(r['color']),
            'supplier_key': normalize_supplier(r['supplier_name']),
        })
    return products


def read_csv_rows(csv_path: Path) -> list[dict]:
    path = Path(csv_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'CSVが見つかりません: {path}')
    raw = path.read_bytes()
    text = None
    for enc in ('cp932', 'utf-8-sig', 'utf-8'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError('CSVの文字コードを判別できませんでした（cp932 / utf-8）。')

    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise ValueError('CSVの見出し行がありません。')
    missing = [c for c in REQUIRED_CSV_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise ValueError(f'CSVに必要な列がありません: {", ".join(missing)}')

    rows = []
    for index, row in enumerate(reader, start=2):  # header = 1
        code = row.get('商品コード') or ''
        rows.append({
            'csv_row': index,
            'condition': row.get('新古') or '',
            'supplier': row.get('仕入先') or '',
            'customer': row.get('顧客') or '',
            'name': row.get('商品名') or '',
            'code': code,
            'size': row.get('サイズ') or '',
            'unit_cost': row.get('仕入単価') or '',
            'qty_purchase': row.get('購入数量') or '',
            'amount_purchase': row.get('購入金額') or '',
            'qty_ship': row.get('出荷数') or '',
            'amount_ship': row.get('出荷金額') or '',
            'qty_stock': row.get('在庫') or '',
            'amount_stock': row.get('在庫金額') or '',
            'office_code': row.get('営業所コード') or '',
            'site': row.get('拠点') or '',
            'shelf': row.get('棚') or '',
            'qty_adjust': row.get('補正在庫数') or '',
            'code_key': normalize_code(code),
            'name_key': name_core(row.get('商品名')),
            'supplier_key': normalize_supplier(row.get('仕入先')),
        })
    return rows


def _supplier_match(csv_row: dict, product: dict) -> bool:
    if not csv_row['supplier_key'] or not product['supplier_key']:
        return False
    return csv_row['supplier_key'] == product['supplier_key']


def _name_match(csv_row: dict, product: dict) -> bool:
    if not csv_row['name_key'] or not product['name_key']:
        return False
    return csv_row['name_key'] == product['name_key']


def _color_in_name_or_field(csv_row: dict, product: dict) -> bool:
    """色専用列がCSVに無いため、WEB色が空／CSV名に含まれる／一致、で緩く見る。"""
    color = product['color_key']
    if not color:
        return True
    if color == csv_row['name_key']:
        return True
    return color in csv_row['name_key']


def pick_candidates(csv_row: dict, by_code: dict[str, list[dict]]) -> tuple[str, list[dict], str]:
    """1 CSV行（商品コード単位の代表）に対する突合結果。

    Returns: (bucket, candidates, note)
      bucket: matched | review | missing | blank
    """
    if not csv_row['code_key']:
        return 'blank', [], '品番（商品コード）が空欄'

    candidates = list(by_code.get(csv_row['code_key'], []))
    if not candidates:
        return 'missing', [], 'WEBに同じ品番なし'

    if len(candidates) == 1:
        only = candidates[0]
        notes = []
        if csv_row['supplier_key'] and only['supplier_key'] and not _supplier_match(csv_row, only):
            notes.append('仕入先が食い違う')
        if csv_row['name_key'] and only['name_key'] and not _name_match(csv_row, only):
            # 表記違いだけでは除外しない（要確認にもしない）。矛盾メモのみ。
            notes.append('商品名の表記が異なる（除外しない）')
        if only['color_key'] and not _color_in_name_or_field(csv_row, only):
            notes.append('色の表記が異なる（除外しない）')
        if any(n.startswith('仕入先') for n in notes):
            return 'review', candidates, '／'.join(notes)
        return 'matched', candidates, '／'.join(notes) if notes else '品番一致（候補1件）'

    # 同品番が複数 → 仕入先 → 商品名・色
    narrowed = [p for p in candidates if _supplier_match(csv_row, p)]
    step = '仕入先'
    if len(narrowed) == 1:
        return 'matched', narrowed, f'同品番複数→{step}で確定'
    if len(narrowed) == 0:
        narrowed = candidates
        step = '仕入先不一致のまま'
    else:
        candidates = narrowed

    by_name = [p for p in candidates if _name_match(csv_row, p)]
    if len(by_name) == 1:
        return 'matched', by_name, f'同品番複数→{step}→商品名で確定'
    if len(by_name) > 1:
        by_color = [p for p in by_name if _color_in_name_or_field(csv_row, p)]
        if len(by_color) == 1:
            return 'matched', by_color, f'同品番複数→{step}→商品名・色で確定'
        pool = by_color or by_name
        return 'review', pool, f'同品番複数で確定できず（{step}／商品名で絞っても{len(pool)}件）'

    by_color = [p for p in candidates if _color_in_name_or_field(csv_row, p)]
    if len(by_color) == 1:
        return 'matched', by_color, f'同品番複数→{step}→色で確定'
    pool = by_color or candidates
    return 'review', pool, f'同品番複数で確定できず（候補{len(pool)}件）'


def compare_rows(csv_rows: list[dict], products: list[dict]) -> dict:
    by_code: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        if p['code_key']:
            by_code[p['code_key']].append(p)

    # 商品コード単位で判定し、サイズ行へ結果を展開（数量・単価は合算しない）
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in csv_rows:
        key = (
            row['code_key'],
            row['supplier_key'],
            row['name_key'],
            normalize_text(row['condition']),
            normalize_text(row['customer']),
        )
        groups[key].append(row)

    matched_rows: list[dict] = []
    review_rows: list[dict] = []
    missing_rows: list[dict] = []
    blank_rows: list[dict] = []
    group_stats = {'matched': 0, 'review': 0, 'missing': 0, 'blank': 0}

    for key, members in groups.items():
        # グループ代表（先頭行）で突合。サイズ違いは同一判定を共有。
        bucket, candidates, note = pick_candidates(members[0], by_code)
        group_stats[bucket] += 1
        size_labels = [m['size'] for m in members]
        csv_row_nos = [str(m['csv_row']) for m in members]
        for member in members:
            detail = {
                **member,
                'bucket': bucket,
                'note': note,
                'group_csv_rows': ','.join(csv_row_nos),
                'group_sizes': ','.join(size_labels),
                'candidates': candidates,
            }
            if bucket == 'matched':
                matched_rows.append(detail)
            elif bucket == 'review':
                review_rows.append(detail)
            elif bucket == 'missing':
                missing_rows.append(detail)
            else:
                blank_rows.append(detail)

    def sort_key(r):
        return (r['code_key'], r['supplier_key'], r['name_key'], r['csv_row'])

    matched_rows.sort(key=sort_key)
    review_rows.sort(key=sort_key)
    missing_rows.sort(key=sort_key)
    blank_rows.sort(key=sort_key)

    return {
        'matched': matched_rows,
        'review': review_rows,
        'missing': missing_rows,
        'blank': blank_rows,
        'group_stats': group_stats,
        'csv_row_count': len(csv_rows),
        'web_product_count': len(products),
        'web_code_count': len(by_code),
    }


def _candidate_cells(candidates: list[dict], limit: int = 8) -> tuple[str, str, str, str, str]:
    shown = candidates[:limit]
    ids = ','.join(str(p['id']) for p in shown)
    codes = ' ／ '.join(p['code'] for p in shown)
    names = ' ／ '.join(
        f"{p['name']}" + (f"（{p['color']}）" if p['color'] else '')
        for p in shown
    )
    suppliers = ' ／ '.join(p['supplier_name'] or '（仕入先なし）' for p in shown)
    sizes = ' ／ '.join(','.join(p['sizes']) if p['sizes'] else '（サイズなし）' for p in shown)
    if len(candidates) > limit:
        ids += f' …他{len(candidates) - limit}件'
    return ids, codes, names, suppliers, sizes


def _detail_headers() -> list[str]:
    return [
        '判定', 'メモ', 'CSV行番号', 'グループ内CSV行番号', 'グループ内サイズ',
        '新古', '仕入先', '顧客', '商品名', '商品コード', 'サイズ', '仕入単価',
        '購入数量', '購入金額', '出荷数', '出荷金額', '在庫', '在庫金額',
        '営業所コード', '拠点', '棚', '補正在庫数',
        'WEB商品ID', 'WEB品番', 'WEB商品名（色）', 'WEB仕入先', 'WEBサイズ',
    ]


def _detail_row(item: dict) -> list[str]:
    ids, codes, names, suppliers, sizes = _candidate_cells(item.get('candidates') or [])
    return [
        {'matched': '対応候補', 'review': '要確認', 'missing': '該当なし', 'blank': '品番空欄'}.get(item['bucket'], item['bucket']),
        item.get('note') or '',
        str(item['csv_row']),
        item.get('group_csv_rows') or str(item['csv_row']),
        item.get('group_sizes') or item.get('size') or '',
        item.get('condition') or '',
        item.get('supplier') or '',
        item.get('customer') or '',
        item.get('name') or '',
        item.get('code') or '',
        item.get('size') or '',
        item.get('unit_cost') or '',
        item.get('qty_purchase') or '',
        item.get('amount_purchase') or '',
        item.get('qty_ship') or '',
        item.get('amount_ship') or '',
        item.get('qty_stock') or '',
        item.get('amount_stock') or '',
        item.get('office_code') or '',
        item.get('site') or '',
        item.get('shelf') or '',
        item.get('qty_adjust') or '',
        ids,
        codes,
        names,
        suppliers,
        sizes,
    ]


def build_workbook_bytes(
    *,
    db_path: Path,
    csv_path: Path,
    result: dict,
    generated_at: datetime | None = None,
) -> bytes:
    generated_at = generated_at or datetime.now()
    stats = result['group_stats']
    cover_headers = ['項目', '内容']
    cover_rows = [
        ['生成日時', generated_at.strftime('%Y-%m-%d %H:%M:%S')],
        ['接続先DB（絶対パス）', str(Path(db_path).resolve())],
        ['CSV（絶対パス）', str(Path(csv_path).resolve())],
        ['読み取り専用', 'はい（sqlite mode=ro / PRAGMA query_only=ON。商品・取扱・初期在庫へ書き込みなし）'],
        ['CSV行数（ヘッダ除く）', str(result['csv_row_count'])],
        ['WEB商品件数', str(result['web_product_count'])],
        ['WEB品番種類数', str(result['web_code_count'])],
        ['対応候補（商品グループ）', str(stats['matched'])],
        ['要確認（商品グループ）', str(stats['review'])],
        ['該当なし（商品グループ）', str(stats['missing'])],
        ['品番空欄（商品グループ）', str(stats['blank'])],
        ['対応候補（CSV行）', str(len(result['matched']))],
        ['要確認（CSV行）', str(len(result['review']))],
        ['該当なし（CSV行）', str(len(result['missing']))],
        ['品番空欄（CSV行）', str(len(result['blank']))],
        ['注意', 'サイズ違いは同じ判定グループにまとめ、CSV行番号と数量・単価はそのまま保持（合算しない）。新商品は登録しない。'],
    ]
    headers = _detail_headers()
    sheets = [
        ('集計', cover_headers, cover_rows),
        ('対応候補', headers, [_detail_row(r) for r in result['matched']]),
        ('要確認', headers, [_detail_row(r) for r in result['review']]),
        ('該当なし', headers, [_detail_row(r) for r in result['missing']]),
        ('品番空欄', headers, [_detail_row(r) for r in result['blank']]),
    ]
    return excel_book.workbook(sheets)


def default_output_path(*, desktop: bool = False) -> Path:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    name = f'item_report_compare_{stamp}.xlsx'
    if desktop:
        desk = Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or '.') / 'Desktop'
        return (desk / name).resolve()
    out_dir = SCRIPT_ROOT / 'data' / 'compare'
    out_dir.mkdir(parents=True, exist_ok=True)
    return (out_dir / name).resolve()


def run_compare(
    csv_path: Path,
    db_path: Path,
    output_path: Path,
) -> dict:
    print(f'接続先DB: {db_path.resolve()}', flush=True)
    print(f'CSV: {Path(csv_path).resolve()}', flush=True)
    csv_rows = read_csv_rows(csv_path)
    with open_readonly(db_path) as conn:
        products = load_web_products(conn)
        # Ensure we never leave a write transaction (readonly should already forbid).
        result = compare_rows(csv_rows, products)
    blob = build_workbook_bytes(db_path=db_path, csv_path=csv_path, result=result)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)
    print(f'出力Excel: {output_path.resolve()}', flush=True)
    stats = result['group_stats']
    print(
        '集計（商品グループ）: '
        f"対応候補={stats['matched']} 要確認={stats['review']} "
        f"該当なし={stats['missing']} 品番空欄={stats['blank']}",
        flush=True,
    )
    return {'result': result, 'output': output_path.resolve(), 'db': db_path.resolve()}


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='旧システムCSVと実在庫DBの商品を読み取り専用で比較し、確認用Excelを出力します。',
    )
    p.add_argument('csv', nargs='?', help='item_report CSV のパス')
    p.add_argument('--db', help='在庫DB（stock.sqlite3）のパス。省略時は指示書デスクと同じ解決')
    p.add_argument('--out', help='出力xlsxパス')
    p.add_argument('--desktop', action='store_true', help='出力先をデスクトップにする')
    p.add_argument(
        '--allow-standalone',
        action='store_true',
        help='stock-desk の live/sample 台帳を意図的に使うときだけ指定',
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    csv_path = args.csv
    if not csv_path:
        print('使い方: compare_item_report.py <item_report.csv> [--db path] [--out path|--desktop]', flush=True)
        return 2
    try:
        db_path = resolve_stock_db_path(args.db, allow_standalone=args.allow_standalone)
        out = Path(args.out).expanduser() if args.out else default_output_path(desktop=args.desktop)
        run_compare(Path(csv_path), db_path, out)
    except (FileNotFoundError, ValueError, sqlite3.Error) as e:
        print(f'エラー: {e}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
