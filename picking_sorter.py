r"""
指示書フォルダ自動仕分け・保存・移動モジュール (picking_sorter.py)

1. 入力元: \\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\【処理中・・・】
2. 出力①: 石狩倉庫 (在庫場所 '0')
   - 保存先: \\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\ピッキングリスト\{YYYYMMDD}\
   - ファイル名: {YYYYMMDD}{連番:02d}.xlsx (例: 2026111401.xlsx)
3. 出力②: 持ち込み (在庫場所 'b')
   - 保存先: \\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\報告用リスト\
   - ファイル名: 報告用{YYMMDD}.xlsx (例: 報告用261114.xlsx)
   - シート構成: 「持ち込み」「工場」「倉庫」の3シート。「持ち込み」シートに追記。
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

# 既定の基準フォルダパス（環境変数 INSTRUCTION_FOLDER_BASE で上書き可能）
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
        # data_only=True で数式の結果を取得
        wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=True)
    except Exception as e:
        # 読めないファイルはスキップ
        return []

    target_sheet = None
    for name in wb.sheetnames:
        if "ピッキングリスト" in name:
            target_sheet = wb[name]
            break
    
    if target_sheet is None:
        wb.close()
        return []

    # ヘッダーを探す
    header_col_map = {}
    header_row_idx = None

    for r_idx, row in enumerate(target_sheet.iter_rows(values_only=True), start=1):
        if r_idx > 10: # ヘッダーは10行目までにあるはず
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
        # デフォルト列順（VBA基準: 依頼者(1), 顧客名(2), アイテム名(3), メーカー(4), 品番(5), サイズ(6), 新古(7), 数量(8), 備考(9), 備考_1(10), 在庫場所(11) ※0-indexed）
        header_col_map = {
            "requester": 0, "customer": 1, "name": 2, "maker": 3,
            "sku": 4, "size": 5, "condition": 6, "quantity": 7,
            "note": 8, "note2": 9, "stock": 10
        }
        header_row_idx = 1

    # データ行を読み込み
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

        # アイテム名が "0" または空、数量が 0 または空なら除外
        if not name_val or name_val == "0":
            continue
        
        try:
            qty_num = int(float(str(qty_val or 0)))
        except (ValueError, TypeError):
            qty_num = 0
        if qty_num <= 0:
            continue

        stock_val = str(get_val("stock") or "").strip()
        condition_val = str(get_val("condition") or "").strip()
        if condition_val in ("0", "新古", "新品・中古"):
            condition_val = "新　　　　古"
        elif condition_val in ("", "未指定"):
            condition_val = "新　　　　古"

        rows.append({
            "source_file": file_path.name,
            "requester": str(get_val("requester") or "").strip(),
            "customer": str(get_val("customer") or "").strip(),
            "name": name_val,
            "maker": str(get_val("maker") or "").strip(),
            "sku": str(get_val("sku") or "").strip(),
            "size": str(get_val("size") or "").strip(),
            "condition": condition_val,
            "quantity": qty_num,
            "note": str(get_val("note") or "").strip(),
            "note2": str(get_val("note2") or "").strip(),
            "stock": stock_val,
        })

    wb.close()
    return rows

def _save_ishikari_list(now: datetime, rows: list[dict]) -> tuple[Path | None, int]:
    """石狩倉庫 (在庫場所 '0') 用のピッキングリストを連番保存"""
    ishikari_rows = [r for r in rows if r["stock"] == "0"]
    if not ishikari_rows:
        return None, 0

    # 顧客名昇順、アイテム名昇順でソート
    ishikari_rows.sort(key=lambda x: (x["customer"], x["name"]))

    date_str = now.strftime("%Y%m%d")
    target_dir = get_ishikari_base_dir() / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    # 既存の連番を検索
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

    # ブック作成
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "石狩倉庫ピッキングリスト"

    # ヘッダー
    headers = ["依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考2", "実行者", "確認", "入力", "確認"]
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

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = font_header
        cell.alignment = align_center
        cell.border = border_thin
        cell.fill = fill_header

    ws.row_dimensions[1].height = 25

    for r_idx, r in enumerate(ishikari_rows, start=2):
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
            "", "", "", "" # チェック欄
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

    # 列幅の調整
    col_widths = {
        1: 12, # 依頼者
        2: 24, # 顧客名
        3: 24, # アイテム名
        4: 14, # メーカー
        5: 14, # 品番
        6: 10, # サイズ
        7: 12, # 新古
        8: 8,  # 数量
        9: 20, # 備考
        10: 16,# 備考2
        11: 6, # 実行者
        12: 6, # 確認
        13: 6, # 入力
        14: 6  # 確認
    }
    for col_idx, width in col_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # 印刷設定（A4横、1ページ幅に収める）
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.oddHeader.left.text = f'&"-,Bold"&14{now.strftime("%Y/%m/%d %H:%M")}'
    ws.oddHeader.center.text = f'&"-,Bold"&14石狩倉庫ピッキングリスト ({file_name})'
    ws.print_title_rows = '1:1'

    wb.save(str(out_path))
    wb.close()
    return out_path, len(ishikari_rows)

def _save_carry_list(now: datetime, rows: list[dict]) -> tuple[Path | None, int]:
    """持ち込み (在庫場所 'b') 用の報告用リスト（持ち込み/工場/倉庫の3シート）に追記保存"""
    carry_rows = [r for r in rows if r["stock"].lower() == "b"]
    if not carry_rows:
        return None, 0

    carry_rows.sort(key=lambda x: (x["customer"], x["name"]))

    report_dir = get_report_base_dir()
    report_dir.mkdir(parents=True, exist_ok=True)

    # ファイル名: 報告用YYMMDD.xlsx (例: 報告用261114.xlsx)
    date_str = now.strftime("%y%m%d")
    file_name = f"報告用{date_str}.xlsx"
    out_path = report_dir / file_name

    headers = ["依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考2", "実行者", "確認", "入力", "確認"]

    font_data = Font(name="Yu Gothic", size=11, bold=True)
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    border_thin = Border(
        left=Side(style='thin', color='888888'),
        right=Side(style='thin', color='888888'),
        top=Side(style='thin', color='888888'),
        bottom=Side(style='thin', color='888888')
    )

    if out_path.exists():
        wb = openpyxl.load_workbook(str(out_path))
    else:
        wb = openpyxl.Workbook()
        # 3シート作成: 持ち込み、工場、倉庫
        default_sheet = wb.active
        default_sheet.title = "持ち込み"
        wb.create_sheet(title="工場")
        wb.create_sheet(title="倉庫")

    if "持ち込み" not in wb.sheetnames:
        ws = wb.create_sheet(title="持ち込み", index=0)
    else:
        ws = wb["持ち込み"]

    # ヘッダーが未設定なら設定
    if ws.max_row == 1 and ws.cell(row=1, column=1).value is None:
        ws.append(headers)
        font_header = Font(name="Yu Gothic", size=11, bold=True)
        fill_header = PatternFill(start_color="EAEAEA", end_color="EAEAEA", fill_type="solid")
        ws.row_dimensions[1].height = 25
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = font_header
            cell.alignment = align_center
            cell.border = border_thin
            cell.fill = fill_header

    start_row = ws.max_row + 1
    for r_idx, r in enumerate(carry_rows, start=start_row):
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
            "", "", "", "" # チェック欄
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

    # 列幅の設定
    col_widths = {1:12, 2:24, 3:24, 4:14, 5:14, 6:10, 7:12, 8:8, 9:20, 10:16, 11:6, 12:6, 13:6, 14:6}
    for col_idx, width in col_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # 印刷設定
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.oddHeader.left.text = f'&"-,Bold"&14{now.strftime("%Y/%m/%d %H:%M")}'
    ws.oddHeader.center.text = f'&"-,Bold"&14持ち込みリスト ({file_name})'
    ws.print_title_rows = '1:1'

    wb.save(str(out_path))
    wb.close()
    return out_path, len(carry_rows)

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
            # 同名衝突時はタイムスタンプを付与
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
            "ishikari_count": 0,
            "carry_file": None,
            "carry_count": 0,
            "moved_count": 0,
        }

    all_rows = []
    processed_files = []

    for f in pending_files:
        rows = _extract_rows_from_workbook(f)
        all_rows.extend(rows)
        processed_files.append(f)

    # 1. 石狩倉庫
    ishikari_path, ishikari_count = _save_ishikari_list(now, all_rows)

    # 2. 持ち込み (報告用リスト)
    carry_path, carry_count = _save_carry_list(now, all_rows)

    # 3. 処理済みフォルダへの移動
    dest_dir, moved_count = _move_processed_files(now, processed_files)

    return {
        "success": True,
        "message": "指示書の仕分けと保存が完了しました。",
        "ishikari_file": ishikari_path.name if ishikari_path else None,
        "ishikari_path": str(ishikari_path) if ishikari_path else None,
        "ishikari_count": ishikari_count,
        "carry_file": carry_path.name if carry_path else None,
        "carry_path": str(carry_path) if carry_path else None,
        "carry_count": carry_count,
        "moved_count": moved_count,
        "processed_dir": str(dest_dir)
    }
