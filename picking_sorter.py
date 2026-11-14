r"""
指示書フォルダ自動仕分け・保存・移動モジュール (picking_sorter.py)

1. 入力元: \\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\【処理中・・・】
2. 出力①: 石狩倉庫ブック (ピッキングリスト)
   - 保存先: \\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\ピッキングリスト\{YYYYMMDD}\
   - ファイル名: {YYYYMMDD}{連番:02d}.xlsx (例: 2026111401.xlsx)
   - シート構成:
     * 「石狩倉庫ピッキングリスト」: 在庫場所 '0'
     * 「工場内 aリスト」: 在庫場所 'a'
     * 「持ち込み bリスト」: 在庫場所 'b'
     * 「Sheet2」: 集約全データ
3. 出力②: 報告用リスト
   - 保存先: \\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\報告用リスト\
   - ファイル名: 報告用{YYMMDD}.xlsx (例: 報告用261114.xlsx)
   - シート構成: 「持ち込み」「工場」「倉庫」
     * 「持ち込み」シート: 在庫場所 'b' のデータを追記
     * 「工場」シート: 在庫場所 'a' のデータを追記
4. 移動: \\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\処理済み\{YYYY}.{M}月\{YYYYMMDD}\
"""

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

DEFAULT_BASE_DIR = r"\\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】"

def get_base_dir() -> Path:
    env_path = os.environ.get("INSTRUCTION_FOLDER_BASE")
    if env_path:
        return Path(env_path)
    return Path(DEFAULT_BASE_DIR)

def get_processing_dir() -> Path:
    return get_base_dir() / "【処理中・・・】"

def get_ishikari_base_dir() -> Path:
    return get_base_dir() / "ピッキングリスト"

def get_report_base_dir() -> Path:
    return get_base_dir() / "報告用リスト"

def get_processed_base_dir() -> Path:
    return get_base_dir() / "処理済み"

def normalize_stock(val) -> str:
    """在庫場所の値を正規化 ('0'=石狩倉庫, 'a'=工場内, 'b'=持ち込み)"""
    if val is None:
        return "0"
    s = str(val).strip().lower()
    if s in ("0", "0.0", "", "none"):
        return "0"
    if s in ("a", "ａ"):
        return "a"
    if s in ("b", "ｂ"):
        return "b"
    return s

def clean_cell_text(val, default="") -> str:
    if val is None:
        return default
    s = str(val).strip()
    if s == "0" or s == "0.0":
        return default
    return s

def get_pending_files() -> list[Path]:
    """【処理中・・・】内の対象Excelファイル一覧を取得（一時ファイル ~$* は除外）"""
    p_dir = get_processing_dir()
    if not p_dir.exists():
        return []
    files = []
    for f in p_dir.iterdir():
        if f.is_file() and not f.name.startswith("~$") and f.suffix.lower() in (".xlsx", ".xlsm", ".xlsb", ".xls"):
            files.append(f)
    return sorted(files, key=lambda x: x.name)

def preview_sort() -> dict:
    """プレビュー情報（対象件数とファイル名）を返す"""
    p_dir = get_processing_dir()
    if not p_dir.exists():
        return {
            "exists": False,
            "dir": str(p_dir),
            "count": 0,
            "files": []
        }
    files = get_pending_files()
    return {
        "exists": True,
        "dir": str(p_dir),
        "count": len(files),
        "files": [f.name for f in files]
    }

def _extract_rows_from_workbook(file_path: Path) -> list[dict]:
    """指示書Excelの『ピッキングリスト』シートから行データを抽出"""
    rows = []
    try:
        wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=True)
    except Exception:
        return []

    target_sheet = None
    for name in wb.sheetnames:
        if "ピッキングリスト" in name:
            target_sheet = wb[name]
            break
    
    if target_sheet is None:
        wb.close()
        return []

    header_col_map = {}
    header_row_idx = None

    for r_idx, row in enumerate(target_sheet.iter_rows(values_only=True), start=1):
        if r_idx > 10:
            break
        row_strs = [str(c or "").strip() for c in row]
        if any("アイテム名" in c or "品名" in c or "商品名" in c for c in row_strs) and any("数量" in c or "枚数" in c for c in row_strs):
            header_row_idx = r_idx
            for c_idx, val in enumerate(row_strs):
                v = val.replace(" ", "").replace("　", "")
                if "依頼者" in v or "担当" in v: header_col_map["requester"] = c_idx
                elif "顧客名" in v or "お客様" in v: header_col_map["customer"] = c_idx
                elif "アイテム名" in v or "商品名" in v or "品名" in v: header_col_map["name"] = c_idx
                elif "メーカー" in v: header_col_map["maker"] = c_idx
                elif "品番" in v: header_col_map["sku"] = c_idx
                elif "サイズ" in v: header_col_map["size"] = c_idx
                elif "新古" in v: header_col_map["condition"] = c_idx
                elif "数量" in v or "枚数" in v: header_col_map["quantity"] = c_idx
                elif "在庫場所" in v: header_col_map["stock"] = c_idx
                elif "備考" in v:
                    if "note" not in header_col_map: header_col_map["note"] = c_idx
                    else: header_col_map["note2"] = c_idx
            break

    if header_row_idx is None:
        header_col_map = {
            "requester": 0, "customer": 1, "name": 2, "maker": 3,
            "sku": 4, "size": 5, "condition": 6, "quantity": 7,
            "note": 8, "note2": 9, "stock": 10
        }
        header_row_idx = 1

    for row in target_sheet.iter_rows(min_row=header_row_idx + 1, values_only=True):
        if not row:
            continue
        
        def get_val(key):
            idx = header_col_map.get(key)
            if idx is not None and idx < len(row):
                return row[idx]
            return None

        name_val = str(get_val("name") or "").strip()
        qty_val = get_val("quantity")

        if not name_val or name_val == "0":
            continue
        
        try:
            qty_num = int(float(str(qty_val or 0)))
        except (ValueError, TypeError):
            qty_num = 0
        if qty_num <= 0:
            continue

        stock_val = normalize_stock(get_val("stock"))
        
        cond_raw = str(get_val("condition") or "").strip()
        if cond_raw in ("0", "0.0", "", "未指定", "新古", "新品・中古"):
            condition_val = "新　　　　古"
        elif cond_raw in ("新", "新品"):
            condition_val = "新"
        elif cond_raw in ("古", "中古"):
            condition_val = "古"
        else:
            condition_val = cond_raw

        rows.append({
            "source_file": file_path.name,
            "requester": clean_cell_text(get_val("requester")),
            "customer": clean_cell_text(get_val("customer")),
            "name": name_val,
            "maker": clean_cell_text(get_val("maker")),
            "sku": clean_cell_text(get_val("sku")),
            "size": clean_cell_text(get_val("size")),
            "condition": condition_val,
            "quantity": qty_num,
            "note": clean_cell_text(get_val("note")),
            "note2": clean_cell_text(get_val("note2")),
            "stock": stock_val,
        })

    wb.close()
    return rows

def _apply_sheet_styling(ws, rows_data: list[dict], title: str, now: datetime, file_name: str):
    """ピッキング／作業シート用の共通フォーマット適用"""
    headers = ["依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考_1", "実行者", "確認", "入力", "確認"]
    ws.append(headers)

    font_header = Font(name="Yu Gothic", size=11, bold=True)
    font_data = Font(name="Yu Gothic", size=11, bold=True)
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    border_thin = Border(
        left=Side(style='thin', color='888888'),
        right=Side(style='thin', color='888888'),
        top=Side(style='thin', color='888888'),
        bottom=Side(style='thin', color='888888')
    )
    fill_header = PatternFill(start_color="EAEAEA", end_color="EAEAEA", fill_type="solid")

    ws.row_dimensions[1].height = 25
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = font_header
        cell.alignment = align_center
        cell.border = border_thin
        cell.fill = fill_header

    for r_idx, r in enumerate(rows_data, start=2):
        row_vals = [
            r["requester"],
            r["customer"],
            r["name"],
            r["maker"],
            r["sku"],
            r["size"],
            r["condition"],
            r["quantity"],
            r["note"],
            r["note2"],
            "", "", "", ""
        ]
        ws.append(row_vals)
        ws.row_dimensions[r_idx].height = 30
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=r_idx, column=col_idx)
            cell.font = font_data
            cell.border = border_thin
            if col_idx in (1, 6, 7, 8, 11, 12, 13, 14):
                cell.alignment = align_center
            else:
                cell.alignment = align_left

    col_widths = {1:12, 2:24, 3:24, 4:14, 5:14, 6:10, 7:12, 8:8, 9:20, 10:16, 11:6, 12:6, 13:6, 14:6}
    for col_idx, width in col_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = 0.0
    ws.page_margins.right = 0.0
    ws.page_margins.top = 0.984
    ws.page_margins.bottom = 0.0
    ws.page_margins.header = 0.315
    ws.page_margins.footer = 0.315
    ws.oddHeader.left.text = '&"-,Bold"&12 &D　&T'
    ws.oddHeader.center.text = '&"-,Bold"&12 &A'
    ws.oddHeader.right.text = None
    ws.print_title_rows = '1:1'

def _save_ishikari_workbook(now: datetime, rows: list[dict]) -> tuple[Path | None, dict]:
    r"""石狩倉庫用連番ブック (ピッキングリスト\YYYYMMDD\YYYYMMDDNN.xlsx) を作成"""
    if not rows:
        return None, {"ishikari": 0, "factory": 0, "carry": 0}

    ishikari_rows = [r for r in rows if r["stock"] == "0"]
    factory_rows  = [r for r in rows if r["stock"] == "a"]
    carry_rows    = [r for r in rows if r["stock"] == "b"]

    ishikari_rows.sort(key=lambda x: (x["customer"], x["name"]))
    factory_rows.sort(key=lambda x: (x["customer"], x["name"]))
    carry_rows.sort(key=lambda x: (x["customer"], x["name"]))

    date_str = now.strftime("%Y%m%d")
    target_dir = get_ishikari_base_dir() / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    max_seq = 0
    pattern = re.compile(rf"^{date_str}(\d{{2}})\.xlsx$", re.IGNORECASE)
    for f in target_dir.iterdir():
        if f.is_file():
            m = pattern.match(f.name)
            if m:
                seq = int(m.group(1))
                if seq > max_seq:
                    max_seq = seq

    next_seq = max_seq + 1
    file_name = f"{date_str}{next_seq:02d}.xlsx"
    out_path = target_dir / file_name

    wb = openpyxl.Workbook()

    # 1. Sheet2 (全件集約シート)
    ws_all = wb.active
    ws_all.title = "Sheet2"
    all_headers = ["Source.Name", "依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考_1", "在庫場所"]
    ws_all.append(all_headers)
    for r in sorted(rows, key=lambda x: (x["customer"], x["name"])):
        ws_all.append([
            r["source_file"], r["requester"], r["customer"], r["name"],
            r["maker"], r["sku"], r["size"], r["condition"], r["quantity"],
            r["note"], r["note2"], r["stock"]
        ])

    # 2. 持ち込み bリスト
    ws_carry = wb.create_sheet(title="持ち込み bリスト")
    _apply_sheet_styling(ws_carry, carry_rows, "持ち込み bリスト", now, file_name)

    # 3. 工場内 aリスト
    ws_factory = wb.create_sheet(title="工場内 aリスト")
    _apply_sheet_styling(ws_factory, factory_rows, "工場内 aリスト", now, file_name)

    # 4. 石狩倉庫ピッキングリスト
    ws_ishikari = wb.create_sheet(title="石狩倉庫ピッキングリスト")
    _apply_sheet_styling(ws_ishikari, ishikari_rows, "石狩倉庫ピッキングリスト", now, file_name)

    wb.save(str(out_path))
    wb.close()

    return out_path, {
        "ishikari": len(ishikari_rows),
        "factory": len(factory_rows),
        "carry": len(carry_rows)
    }

def _append_rows_to_report_sheet(ws, rows_data: list[dict], title: str, now: datetime, file_name: str):
    """報告用リストの指定シートにデータを追記"""
    headers = ["依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考_1", "実行者", "確認", "入力", "確認"]
    
    font_header = Font(name="Yu Gothic", size=11, bold=True)
    font_data = Font(name="Yu Gothic", size=11, bold=True)
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    border_thin = Border(
        left=Side(style='thin', color='888888'),
        right=Side(style='thin', color='888888'),
        top=Side(style='thin', color='888888'),
        bottom=Side(style='thin', color='888888')
    )
    fill_header = PatternFill(start_color="EAEAEA", end_color="EAEAEA", fill_type="solid")

    # ヘッダーが未設定なら設定
    if ws.max_row == 1 and ws.cell(row=1, column=1).value is None:
        ws.append(headers)
        ws.row_dimensions[1].height = 25
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = font_header
            cell.alignment = align_center
            cell.border = border_thin
            cell.fill = fill_header
    elif ws.max_row < 1:
        ws.append(headers)

    start_row = ws.max_row + 1
    for r_idx, r in enumerate(rows_data, start=start_row):
        row_vals = [
            r["requester"],
            r["customer"],
            r["name"],
            r["maker"],
            r["sku"],
            r["size"],
            r["condition"],
            r["quantity"],
            r["note"],
            r["note2"],
            "", "", "", ""
        ]
        ws.append(row_vals)
        ws.row_dimensions[r_idx].height = 30
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=r_idx, column=col_idx)
            cell.font = font_data
            cell.border = border_thin
            if col_idx in (1, 6, 7, 8, 11, 12, 13, 14):
                cell.alignment = align_center
            else:
                cell.alignment = align_left

    col_widths = {1:12, 2:24, 3:24, 4:14, 5:14, 6:10, 7:12, 8:8, 9:20, 10:16, 11:6, 12:6, 13:6, 14:6}
    for col_idx, width in col_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = 0.0
    ws.page_margins.right = 0.0
    ws.page_margins.top = 0.984
    ws.page_margins.bottom = 0.0
    ws.page_margins.header = 0.315
    ws.page_margins.footer = 0.315
    ws.oddHeader.left.text = '&"-,Bold"&12 &D　&T'
    ws.oddHeader.center.text = '&"-,Bold"&12 &A'
    ws.oddHeader.right.text = None
    ws.print_title_rows = '1:1'

def _save_report_workbook(now: datetime, rows: list[dict]) -> tuple[Path | None, dict]:
    r"""報告用リスト (報告用リスト\報告用YYMMDD.xlsx) の「持ち込み」および「工場」シートに追記保存"""
    carry_rows   = [r for r in rows if r["stock"] == "b"]
    factory_rows = [r for r in rows if r["stock"] == "a"]

    if not carry_rows and not factory_rows:
        return None, {"carry": 0, "factory": 0}

    carry_rows.sort(key=lambda x: (x["customer"], x["name"]))
    factory_rows.sort(key=lambda x: (x["customer"], x["name"]))

    report_dir = get_report_base_dir()
    report_dir.mkdir(parents=True, exist_ok=True)

    date_str = now.strftime("%y%m%d")
    file_name = f"報告用{date_str}.xlsx"
    out_path = report_dir / file_name

    if out_path.exists():
        wb = openpyxl.load_workbook(str(out_path))
    else:
        wb = openpyxl.Workbook()
        ws_carry = wb.active
        ws_carry.title = "持ち込み"
        wb.create_sheet(title="工場")
        wb.create_sheet(title="倉庫")

    # 持ち込みシート
    if "持ち込み" not in wb.sheetnames:
        ws_carry = wb.create_sheet(title="持ち込み", index=0)
    else:
        ws_carry = wb["持ち込み"]
    if carry_rows:
        _append_rows_to_report_sheet(ws_carry, carry_rows, "持ち込みリスト", now, file_name)

    # 工場シート
    if "工場" not in wb.sheetnames:
        ws_factory = wb.create_sheet(title="工場", index=1)
    else:
        ws_factory = wb["工場"]
    if factory_rows:
        _append_rows_to_report_sheet(ws_factory, factory_rows, "工場リスト", now, file_name)

    # 倉庫シートがなければ枠だけ作成
    if "倉庫" not in wb.sheetnames:
        ws_ware = wb.create_sheet(title="倉庫")
        headers = ["依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考_1", "実行者", "確認", "入力", "確認"]
        ws_ware.append(headers)

    wb.save(str(out_path))
    wb.close()

    return out_path, {
        "carry": len(carry_rows),
        "factory": len(factory_rows)
    }

def _move_processed_files(now: datetime, files: list[Path]) -> tuple[Path, int]:
    r"""処理済みフォルダ (処理済み\{YYYY}.{M}月\{YYYYMMDD}\) へファイルを移動"""
    month_folder_name = f"{now.year}.{now.month}月"
    day_folder_name = now.strftime("%Y%m%d")
    dest_dir = get_processed_base_dir() / month_folder_name / day_folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    moved_count = 0
    for f in files:
        if not f.exists():
            continue
        dest_file = dest_dir / f.name
        if dest_file.exists():
            ts = now.strftime("%H%M%S")
            dest_file = dest_dir / f"{f.stem}_{ts}{f.suffix}"
        shutil.move(str(f), str(dest_file))
        moved_count += 1

    return dest_dir, moved_count

def execute_sort() -> dict:
    """仕分け実行メインロジック"""
    now = datetime.now()
    pending_files = get_pending_files()
    if not pending_files:
        return {
            "success": False,
            "message": "【処理中・・・】に対象ファイルがありません。",
            "ishikari_file": None,
            "ishikari_counts": {"ishikari": 0, "factory": 0, "carry": 0},
            "report_file": None,
            "report_counts": {"carry": 0, "factory": 0},
            "moved_count": 0,
        }

    all_rows = []
    processed_files = []

    for f in pending_files:
        rows = _extract_rows_from_workbook(f)
        all_rows.extend(rows)
        processed_files.append(f)

    # 1. 石狩倉庫ブック (ピッキングリスト\YYYYMMDD\YYYYMMDDNN.xlsx)
    ishikari_path, ishikari_counts = _save_ishikari_workbook(now, all_rows)

    # 2. 報告用リスト (報告用リスト\報告用YYMMDD.xlsx) の「持ち込み」「工場」シートへ追記
    report_path, report_counts = _save_report_workbook(now, all_rows)

    # 3. 処理済みフォルダへの移動
    dest_dir, moved_count = _move_processed_files(now, processed_files)

    return {
        "success": True,
        "message": "指示書の仕分けと保存が完了しました。",
        "ishikari_file": ishikari_path.name if ishikari_path else None,
        "ishikari_path": str(ishikari_path) if ishikari_path else None,
        "ishikari_counts": ishikari_counts,
        "report_file": report_path.name if report_path else None,
        "report_path": str(report_path) if report_path else None,
        "report_counts": report_counts,
        "moved_count": moved_count,
        "processed_dir": str(dest_dir)
    }
