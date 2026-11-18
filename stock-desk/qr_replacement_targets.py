"""QRコード貼り替えが必要な商品の集約・詳細情報・CSV出力。"""
from __future__ import annotations

import csv
import io
import json
import sqlite3

import product_ledger_import as pli
import stock


def _collect_merged_qr_lots(c: sqlite3.Connection) -> list[dict]:
    """同じ在庫に合算され、旧QRが2つ以上紐づいているロット（または在庫ありで旧QR未紐づけ）を収集する。"""
    has_qr = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qr_stock_links'").fetchone()
    has_map = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_csv_row_maps'").fetchone()
    if not has_qr or not has_map:
        return []
    qr_by_key = {}
    for r in c.execute(
        'SELECT file_sha256, source_row_no, legacy_stock_id FROM qr_stock_links WHERE source_row_no IS NOT NULL'
    ):
        qr_by_key[(r['file_sha256'], r['source_row_no'])] = r['legacy_stock_id']
    lot_qrs: dict[int, list[str]] = {}
    for r in c.execute(
        'SELECT file_sha256, source_row_no, lot_ids_json FROM legacy_csv_row_maps WHERE source_row_no IS NOT NULL'
    ):
        lid_str = qr_by_key.get((r['file_sha256'], r['source_row_no']))
        if not lid_str:
            continue
        try:
            ids = json.loads(r['lot_ids_json'] or '[]')
        except (TypeError, ValueError):
            continue
        for lid in ids:
            lot_qrs.setdefault(int(lid), []).append(lid_str)
    out = []
    for row in c.execute(
        '''SELECT l.id AS lot_id, l.customer_product_id, l.size_label, l.condition,
                  l.qty_remaining, l.location_id,
                  cu.name AS customer_name, p.id AS product_id, p.code, p.name AS product_name, p.color,
                  cp.embroidery, loc.name AS loc_name, parent.name AS site_name
           FROM lots l
           JOIN customer_products cp ON cp.id = l.customer_product_id
           JOIN customers cu ON cu.id = cp.customer_id
           JOIN products p ON p.id = cp.product_id
           JOIN locations loc ON loc.id = l.location_id
           LEFT JOIN locations parent ON parent.id = loc.parent_id
           WHERE l.qty_remaining > 0
           ORDER BY cu.name, p.code, l.size_label, l.id'''
    ):
        qrs = list(dict.fromkeys(lot_qrs.get(int(row['lot_id']), [])))
        if len(qrs) == 1:
            continue
        s_name = row['site_name'] or row['loc_name'] or ''
        sh_name = row['loc_name'] if row['site_name'] else ''
        place = f"{s_name} / {sh_name}" if sh_name else s_name
        reason = '同じ在庫に合算（旧QR複数）' if len(qrs) >= 2 else '旧QR未対応'
        out.append({
            'lotId': int(row['lot_id']),
            'customerProductId': int(row['customer_product_id']),
            'productId': int(row['product_id']),
            'customerName': row['customer_name'] or '',
            'code': row['code'] or '',
            'name': row['product_name'] or '',
            'color': row['color'] or '',
            'embroideryLabel': stock.embroidery_label(row['embroidery']),
            'size': row['size_label'] or '',
            'condition': row['condition'] or '',
            'conditionLabel': stock.condition_label(row['condition']) if row['condition'] else '未設定',
            'qty': int(row['qty_remaining'] or 0),
            'locationId': int(row['location_id']),
            'place': place,
            'reason': reason,
            'legacyIds': qrs,
        })
    return out


def get_targets_detail(c: sqlite3.Connection) -> dict:
    """貼り替え対象56件と、各分割商品の現在庫・保管場所情報を集約する。"""
    raw_targets = pli._collect_all_qr_replacement_targets(c)
    
    detailed_targets = []
    all_split_product_ids = set()
    all_split_codes = set()
    total_replacement_lots = 0

    for t in raw_targets:
        orig_code = t.get('originalCode') or ''
        split_type = t.get('splitTypeLabel') or t.get('splitType') or '分割'
        items = []

        for item in t.get('splitItems') or []:
            code = item.get('code') or ''
            name = item.get('name') or ''
            color = item.get('color') or ''

            p_row = c.execute(
                "SELECT id, code, name, color, category, display_no FROM products WHERE code = ?",
                (code,),
            ).fetchone()

            p_id = p_row['id'] if p_row else None
            p_display_no = p_row['display_no'] if p_row else ''
            p_name = p_row['name'] if p_row else name
            p_color = p_row['color'] if p_row else color

            if p_id:
                all_split_product_ids.add(p_id)
            if code:
                all_split_codes.add(code)

            # 在庫数と保管場所
            locations_info = []
            total_qty = 0
            if p_id:
                lots = c.execute("""
                    SELECT l.size_label, l.qty_remaining, loc.name AS loc_name, parent.name AS site_name
                    FROM lots l
                    JOIN customer_products cp ON cp.id = l.customer_product_id
                    JOIN locations loc ON loc.id = l.location_id
                    LEFT JOIN locations parent ON parent.id = loc.parent_id
                    WHERE cp.product_id = ? AND l.qty_remaining > 0
                    ORDER BY parent.name, loc.name, l.size_label
                """, (p_id,)).fetchall()

                for lot in lots:
                    qty = int(lot['qty_remaining'] or 0)
                    total_qty += qty
                    total_replacement_lots += 1
                    s_name = lot['site_name'] or lot['loc_name'] or ''
                    sh_name = lot['loc_name'] if lot['site_name'] else ''
                    display_place = f"{s_name} / {sh_name}" if sh_name else s_name
                    locations_info.append({
                        'siteName': s_name,
                        'shelfName': sh_name,
                        'size': lot['size_label'],
                        'qty': qty,
                        'display': f"{display_place} ({lot['size_label']}: {qty}着)",
                    })

            items.append({
                'code': code,
                'name': p_name,
                'color': p_color,
                'productId': p_id,
                'displayNo': p_display_no,
                'totalQty': total_qty,
                'locations': locations_info,
            })

        detailed_targets.append({
            'originalCode': orig_code,
            'splitType': split_type,
            'items': items,
        })

    merged_lots = _collect_merged_qr_lots(c)
    return {
        'totalCount': len(detailed_targets),
        'targets': detailed_targets,
        'splitProductIds': sorted(list(all_split_product_ids)),
        'splitCodes': sorted(list(all_split_codes)),
        'mergedLotCount': len(merged_lots),
        'mergedLots': merged_lots,
    }


def export_csv_bytes(c: sqlite3.Connection) -> bytes:
    """現場作業用の貼り替えリストCSV（Excelで文字化けしないUTF-8 with BOM）。"""
    detail = get_targets_detail(c)
    targets = detail.get('targets') or []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        '元品番',
        '分割理由',
        '新商品コード',
        '商品名',
        '色',
        '現在庫数',
        '保管場所（拠点 / 棚 / サイズ / 数量）',
        '貼り替え確認（レ）',
    ])

    for t in targets:
        orig = t.get('originalCode') or ''
        stype = t.get('splitType') or ''
        for item in t.get('items') or []:
            locs_str = '、'.join(l['display'] for l in item.get('locations') or [])
            if not locs_str:
                locs_str = '在庫なし'
            writer.writerow([
                orig,
                stype,
                item.get('code') or '',
                item.get('name') or '',
                item.get('color') or '',
                item.get('totalQty') or 0,
                locs_str,
                '',  # チェック用空欄
            ])

    for m in detail.get('mergedLots') or []:
        writer.writerow([
            m.get('customerName') or '',
            m.get('reason') or '',
            m.get('code') or '',
            f"{m.get('name') or ''} ({m.get('size') or ''} / {m.get('conditionLabel') or ''})",
            m.get('color') or '',
            m.get('qty') or 0,
            f"{m.get('place') or ''} ({m.get('size') or ''}: {m.get('qty') or 0}着)",
            '',
        ])

    return '\ufeff'.join(['', output.getvalue()]).encode('utf-8')
