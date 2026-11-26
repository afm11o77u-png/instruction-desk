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
     * 「工場」シート: 報告用の追記対象外
4. 移動: \\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\処理済み\{YYYY}.{M}月\{YYYYMMDD}\
"""

import json
import os
import posixpath
import re
import shutil
import unicodedata
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from xml.dom import minidom
from xml.etree import ElementTree
from zipfile import ZipFile
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter, range_boundaries
import excel_import

DEFAULT_BASE_DIR = r"\\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】"
_REPORT_SAVE_LOCK = Lock()
_SORT_LOCK = Lock()
_REPORT_KEYS = ("requester", "customer", "name", "maker", "sku", "size", "condition", "quantity", "note", "note2")
_SHEET_XML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOCUMENT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

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

def get_next_ishikari_filename(now: datetime | None = None) -> str:
    """次の石狩倉庫用連番ファイル名を計算して返す"""
    if now is None:
        now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    target_dir = get_ishikari_base_dir() / date_str
    if not target_dir.exists():
        return f"{date_str}01.xlsx"

    max_seq = 0
    pattern = re.compile(rf"^{date_str}(\d{{2}})\.xlsx$", re.IGNORECASE)
    for f in target_dir.iterdir():
        if f.is_file():
            m = pattern.match(f.name)
            if m:
                seq = int(m.group(1))
                if seq > max_seq:
                    max_seq = seq
    return f"{date_str}{max_seq + 1:02d}.xlsx"

def get_default_report_filename(now: datetime | None = None) -> str:
    """既定の報告用リストファイル名を返す"""
    if now is None:
        now = datetime.now()
    return f"報告用{now.strftime('%y%m%d')}.xlsx"

def _instruction_customer_names(wb) -> tuple[str, str]:
    """Return the B2 customer and its display name for the standard header."""
    if "指示書" not in wb.sheetnames:
        return "", ""
    label, customer, adjacent = next(
        wb["指示書"].iter_rows(min_row=2, max_row=2, max_col=3),
        (None,) * 3,
    )
    if label is None or clean_cell_text(label.value) != "顧客名":
        return "", ""
    name = clean_cell_text(customer.value)
    if not name or not isinstance(adjacent.value, str) or adjacent.data_type == "e":
        return name, name
    extra = clean_cell_text(adjacent.value)
    if not extra or extra.isdecimal() or extra == name:
        return name, name
    company_prefixes = ("株式会社", "有限会社", "合同会社", "合資会社", "合名会社", "㈱", "㈲", "(株)", "（株）")
    separator = "" if name in company_prefixes or extra in company_prefixes else " "
    return name, f"{name}{separator}{extra}"


def _extract_address_from_workbook(file_path: Path) -> dict | None:
    address = _extract_address_fields(file_path)
    if not address or not address["customer"]:
        return address

    try:
        wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=True)
        try:
            name, display_name = _instruction_customer_names(wb)
            if name == address["customer"]:
                address["customer"] = display_name
        finally:
            wb.close()
    except Exception:
        # 店名を読み取れない場合も、既に取得した宛名は使用する。
        pass
    return address

def _extract_address_fields(file_path: Path) -> dict | None:
    """指示書Excelから宛名データ（顧客名、管理番号、部署名、従業員名、受付内容）を柔軟に抽出"""
    standard = None
    # 1. excel_import エンジンによる標準解析
    try:
        blob = file_path.read_bytes()
        payload, _ = excel_import.parse_instruction(blob)
        customer = str(payload.get("customer") or "").strip()
        management = str(payload.get("management") or "").strip()
        department = str(payload.get("department") or "").strip()
        employee = str(payload.get("employee") or "").strip()
        request_val = str(payload.get("request") or "").strip()

        if customer or employee:
            standard = {
                "source_file": file_path.name,
                "customer": customer,
                "management": management,
                "department": department,
                "employee": employee,
                "request": request_val
            }
            if employee:
                return standard
    except Exception:
        pass

    # 2. フォールバック: openpyxl で全シートから項目名を柔軟探索
    try:
        wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=True)
        addr = {
            "source_file": file_path.name,
            "customer": "",
            "management": "",
            "department": "",
            "employee": "",
            "request": ""
        }
        # 指示書シートまたは先頭シートを対象
        target_sheets = [s for s in wb.sheetnames if "指示" in s]
        if not target_sheets:
            target_sheets = [wb.sheetnames[0]]

        for sname in target_sheets:
            ws = wb[sname]
            for row in ws.iter_rows(values_only=True, max_row=40):
                row_vals = [str(c or "").strip() for c in row]
                for idx, val in enumerate(row_vals):
                    v = val.replace(" ", "").replace("　", "")
                    # 右隣のセルを取得するヘルパー
                    next_val = ""
                    for next_idx in range(idx + 1, min(idx + 4, len(row_vals))):
                        if row_vals[next_idx]:
                            next_val = row_vals[next_idx]
                            break
                    if not next_val:
                        continue

                    if not addr["customer"] and ("顧客名" in v or "お客様名" in v or v == "顧客"):
                        addr["customer"] = next_val
                    elif not addr["management"] and ("管理番号" in v or "管理No" in v):
                        addr["management"] = next_val
                    elif not addr["department"] and ("部署" in v or "店舗" in v or "ネーム部署" in v):
                        addr["department"] = next_val
                    elif not addr["employee"] and (v in ("氏名", "従業員") or v.startswith(("従業員名", "氏名", "社員名", "お名前"))):
                        addr["employee"] = next_val
                    elif not addr["request"] and ("受付内容" in v or "依頼内容" in v or "区分" in v):
                        addr["request"] = next_val

        wb.close()
        if standard is not None:
            standard["employee"] = addr["employee"]
            return standard
        if addr["customer"] or addr["employee"]:
            return addr
    except Exception:
        pass

    if standard is not None:
        return standard

    # 3. 最後のフォールバック: ピッキングリストの抽出行データから補完
    try:
        rows = _extract_rows_from_workbook(file_path)
        if rows:
            first = rows[0]
            return {
                "source_file": file_path.name,
                "customer": first.get("customer", ""),
                "management": "",
                "department": "",
                "employee": "",
                "request": ""
            }
    except Exception:
        pass

    return None

def _default_sort_date(now: datetime) -> datetime:
    """Use the next calendar day for sorting defaults from 16:00 JST."""
    return now + timedelta(days=1) if now.hour >= 16 else now


def preview_sort() -> dict:
    """プレビュー情報（対象件数、ファイル名、保存予定ファイル名、抽出宛名一覧）を返す"""
    p_dir = get_processing_dir()
    now = _default_sort_date(datetime.now(timezone(timedelta(hours=9))))
    next_ishikari = get_next_ishikari_filename(now)
    def_report = get_default_report_filename(now)

    journal_path = get_base_dir() / ".instruction-desk-pending-sort.json"
    if journal_path.exists():
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            journal = None
        if journal is not None:
            result = journal["result"]
            return {
                "resume_move": True,
                "next_ishikari_file": result["ishikari_file"],
                "default_report_file": result["report_file"],
                "default_processed_date": result["processed_date"],
                "files": [entry["source"] for entry in journal["moves"]],
            }

    if not p_dir.exists():
        return {
            "exists": False,
            "dir": str(p_dir),
            "count": 0,
            "files": [],
            "next_ishikari_file": next_ishikari,
            "default_report_file": def_report,
            "default_processed_date": now.date().isoformat(),
            "addresses": []
        }
    files = get_pending_files()
    addresses = []
    for f in files:
        addr = _extract_address_from_workbook(f)
        if addr:
            addresses.append(addr)

    return {
        "exists": True,
        "dir": str(p_dir),
        "count": len(files),
        "files": [f.name for f in files],
        "next_ishikari_file": next_ishikari,
        "default_report_file": def_report,
        "default_processed_date": now.date().isoformat(),
        "addresses": addresses
    }

def _extract_rows_from_workbook(file_path: Path) -> list[dict]:
    """ピッキングリストから行を抽出し、対応する指示書の備考を優先する。"""
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

    instruction_rows = ()
    if (
        header_row_idx == 1
        and target_sheet.title == "ピッキングリスト"
        and "指示書" in wb.sheetnames
        and tuple(header_col_map.get(key) for key in ("name", "sku", "size", "quantity", "note", "note2"))
        == (2, 4, 5, 7, 8, 9)
    ):
        details = wb["指示書"].iter_rows(
            min_row=10, max_row=22, max_col=8, values_only=True
        )
        if tuple(clean_cell_text(value) for value in next(details, ())) == (
            "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考"
        ):
            instruction_rows = tuple(details)

    if header_row_idx is None:
        header_col_map = {
            "requester": 0, "customer": 1, "name": 2, "maker": 3,
            "sku": 4, "size": 5, "condition": 6, "quantity": 7,
            "note": 8, "note2": 9, "stock": 10
        }
        header_row_idx = 1

    instruction_customer, display_customer = _instruction_customer_names(wb)
    for detail_index, row in enumerate(
        target_sheet.iter_rows(min_row=header_row_idx + 1, values_only=True)
    ):
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
        if cond_raw in ("0", "0.0", "", "未指定", "新古", "新品・中古", "新　　　　古"):
            condition_val = "新\n古"
        elif cond_raw in ("新", "新品"):
            condition_val = "新"
        elif cond_raw in ("古", "中古"):
            condition_val = "古"
        else:
            condition_val = cond_raw

        note, note2 = get_val("note"), get_val("note2")
        if detail_index < len(instruction_rows):
            # ピッキングリスト2行目と指示書11行目が対応する。除外行があっても行位置は詰めない。
            detail = instruction_rows[detail_index]
            if all(
                clean_cell_text(detail[col]) == clean_cell_text(get_val(key))
                for col, key in ((0, "name"), (2, "sku"), (3, "size"), (5, "quantity"))
            ):
                note, note2 = detail[6:8]

        customer = clean_cell_text(get_val("customer"))
        if customer and customer == instruction_customer:
            customer = display_customer
        rows.append({
            "source_file": file_path.name,
            "requester": clean_cell_text(get_val("requester")),
            "customer": customer,
            "name": name_val,
            "maker": clean_cell_text(get_val("maker")),
            "sku": clean_cell_text(get_val("sku")),
            "size": clean_cell_text(get_val("size")),
            "condition": condition_val,
            "quantity": qty_num,
            "note": clean_cell_text(note),
            "note2": clean_cell_text(note2),
            "stock": stock_val,
            "_source_fields": tuple(get_val(key) for key in (
                "requester", "customer", "name", "maker", "sku", "size", "condition"
            )) + (note, note2),
        })

    wb.close()
    return rows

def _apply_sheet_styling(ws, rows_data: list[dict], title: str, now: datetime, file_name: str, *, include_warehouse_column: bool = True):
    """ピッキング／作業シート用の共通フォーマット適用"""
    headers = ["依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考_1", "倉庫から", "実行者", "確認", "入力", "確認"]
    if not include_warehouse_column:
        headers.remove("倉庫から")
    for col_idx, header in enumerate(headers, start=1):
        ws.cell(row=1, column=col_idx, value=header)

    ws.sheet_format.defaultRowHeight = 31.5
    ws.sheet_format.customHeight = True
    font_header = Font(name="Yu Gothic", size=11, bold=True)
    font_executor_header = Font(name="Yu Gothic", size=9, bold=True)
    font_data = Font(name="Yu Gothic", size=11, bold=True)
    font_cond = Font(name="Yu Gothic", size=9.5, bold=True)
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=False, shrink_to_fit=True)
    align_left = Alignment(horizontal="left", vertical="center", wrap_text=False, shrink_to_fit=True)
    align_cond = Alignment(horizontal="left", vertical="center", wrap_text=False, shrink_to_fit=True)
    align_stacked = Alignment(horizontal="left", vertical="center", wrap_text=True)
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=False, shrink_to_fit=True)
    border_thin = Border(
        left=Side(style='thin', color='888888'),
        right=Side(style='thin', color='888888'),
        top=Side(style='thin', color='888888'),
        bottom=Side(style='thin', color='888888')
    )
    fill_header = PatternFill(start_color="EAEAEA", end_color="EAEAEA", fill_type="solid")

    ws.row_dimensions[1].height = 25
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = font_executor_header if header == "実行者" else font_header
        cell.alignment = align_header
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
        ]
        row_vals.extend([""] * (len(headers) - len(row_vals)))
        # 表示用シートは1行に統一。集約シートの元データはそのまま保持する。
        row_vals = [re.sub(r"[\r\n]+", " ", v) if isinstance(v, str) else v for v in row_vals]
        stacked = isinstance(row_vals[6], str) and re.fullmatch(r"新\s*古", row_vals[6])
        if stacked:
            row_vals[6] = "新\n古"
        ws.append(row_vals)
        ws.row_dimensions[r_idx].height = 32
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=r_idx, column=col_idx)
            cell.border = border_thin
            if col_idx == 7: # 新古: 左寄せ・1行表示
                cell.font = font_cond
                cell.alignment = align_stacked if stacked else align_cond
            elif col_idx in (1, 6, 8) or col_idx > 11:
                cell.font = font_data
                cell.alignment = align_center
            else:
                cell.font = font_data
                cell.alignment = align_left

    # Excel displays an XLSX width of 6.5 as 5.88 with this workbook's default font.
    col_widths = [8.0, 25.0, 15.0, 10.0, 14.0, 7.0, 5.5, 6.0, 16.0, 12.0, 9.0, 6.5, 6.5, 6.5, 6.5]
    if not include_warehouse_column:
        del col_widths[10]
    for col_idx, width in enumerate(col_widths, start=1):
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
    ws.oddHeader.left.text = '&"-,太字"&11 &D　&T'
    ws.oddHeader.center.text = f'&"-,太字"&14 {title}'
    ws.oddHeader.right.text = '&"-,太字"&11 &P / &N'
    ws.print_title_rows = '1:1'

_ISHIKARI_IDENTITY_FIELDS = (
    "requester", "customer", "name", "maker", "sku", "size", "condition", "note", "note2",
)
_KATAKANA_TO_HIRAGANA = str.maketrans({i: i - 0x60 for i in range(0x30A1, 0x30F7)})


def _ishikari_customer_order(row, readings):
    name = unicodedata.normalize("NFKC", row["customer"]).strip().casefold()
    name = re.sub(r"^(?:株式会社|有限会社|\(株\)|\(有\))\s*", "", name)
    if re.match(r"[a-z]", name):
        return 0, name, row["customer"], row["name"]

    reading = unicodedata.normalize("NFKC", readings.get(row["customer"]) or name)
    reading = re.sub(r"^(?:カブシキガイシャ|ユウゲンガイシャ|\(カブ\)|\(ユウ\))\s*", "", reading.strip())
    reading = reading.translate(_KATAKANA_TO_HIRAGANA)
    if re.match(r"[ぁ-ゖ]", reading):
        # Group voiced sounds and small kana with their corresponding base kana.
        base = "".join(c for c in unicodedata.normalize("NFD", reading)
                       if not unicodedata.combining(c))
        base = base.translate(str.maketrans("ぁぃぅぇぉっゃゅょゎ", "あいうえおつやゆよわ"))
        return 1, base, reading, row["customer"], row["name"]
    return 2, name, row["customer"], row["name"]


def _merge_ishikari_rows(rows, readings):
    merged = {}
    for row in rows:
        output_fields = tuple(row[field] for field in _ISHIKARI_IDENTITY_FIELDS)
        source_fields = row.get("_source_fields", output_fields)
        identity = (output_fields, tuple((type(value), value) for value in source_fields))
        if identity in merged:
            merged[identity]["quantity"] += row["quantity"]
        else:
            merged[identity] = row.copy()
    return sorted(merged.values(), key=lambda row: _ishikari_customer_order(row, readings))


def build_ishikari_workbook(now: datetime, rows: list[dict], file_name: str, *, customer_readings=None):
    """Create the warehouse workbook without touching the filesystem."""
    ishikari_rows = [r for r in rows if r["stock"] == "0"]
    factory_rows  = [r for r in rows if r["stock"] == "a"]
    carry_rows    = [r for r in rows if r["stock"] == "b"]

    if customer_readings is None:
        ishikari_rows.sort(key=lambda x: (x["customer"], x["name"]))
    else:
        ishikari_rows = _merge_ishikari_rows(ishikari_rows, customer_readings)
    factory_rows.sort(key=lambda x: (x["customer"], x["name"]))
    carry_rows.sort(key=lambda x: (x["customer"], x["name"]))

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
    _apply_sheet_styling(ws_carry, carry_rows, ws_carry.title, now, file_name)

    # 3. 工場内 aリスト
    ws_factory = wb.create_sheet(title="工場内 aリスト")
    _apply_sheet_styling(ws_factory, factory_rows, ws_factory.title, now, file_name)

    # 4. 石狩倉庫ピッキングリスト
    ws_ishikari = wb.create_sheet(title="石狩倉庫ピッキングリスト")
    _apply_sheet_styling(ws_ishikari, ishikari_rows, ws_ishikari.title, now, file_name)

    # 開いた時に「石狩倉庫ピッキングリスト」が最初に画面に出るようアクティブ設定
    wb.active = ws_ishikari
    for s in wb.worksheets:
        s.sheet_view.tabSelected = (s == ws_ishikari)

    return wb, {
        "ishikari": len(ishikari_rows),
        "factory": len(factory_rows),
        "carry": len(carry_rows)
    }

def preview_ishikari_format() -> bytes:
    rows = [
        {
            "source_file": "見本.xlsx", "requester": "見本", "customer": "サンプル顧客",
            "name": name, "maker": "サンプル", "sku": "SAMPLE-01", "size": "M",
            "condition": condition, "quantity": 1, "note": "印刷範囲内の備考",
            "note2": "印刷範囲外の備考", "stock": stock,
        }
        for stock in ("0", "a", "b")
        for name, condition in (
            ("白衣（新古）", "新\n古"),
            ("白衣（新）", "新"),
            ("白衣（古）", "古"),
        )
    ]
    wb, _ = build_ishikari_workbook(datetime.now(), rows, "ピッキングリスト書式見本.xlsx")
    try:
        for ws in wb.worksheets[1:]:
            ws.merge_cells(start_row=5, start_column=1, end_row=5, end_column=ws.max_column)
            ws["A5"] = "※ 元の指示書が「新・新品」なら新のみ、「古・中古」なら古のみ。「新古・空欄・未指定」は新と古を縦2段で表示します。"
            ws["A5"].font = Font(name="Yu Gothic", size=10)
            ws["A5"].alignment = Alignment(vertical="center", wrap_text=True)
            for row_number in range(2, 6):
                ws.row_dimensions[row_number].height = ws.sheet_format.defaultRowHeight
        with BytesIO() as out:
            wb.save(out)
            return out.getvalue()
    finally:
        wb.close()


def scan_ishikari_workbook(day: str, rows: list[dict]) -> bytes:
    """Export legacy barcode rows using the automatic sorter workbook format."""
    now = datetime.fromisoformat(day)
    stock_codes = {"warehouse": "0", "factory": "a", "carry": "b"}
    fields = ("stock", "customer", "name", "maker", "sku", "size", "condition", "note2")
    merged = {}
    for row in rows:
        key = tuple(row[field] for field in fields)
        if key in merged:
            merged[key]["quantity"] += row["quantity"]
        else:
            merged[key] = row.copy()

    normalized = []
    for row in merged.values():
        condition = str(row.get("condition") or "").strip()
        if row["stock"] == "warehouse" or condition in ("", "未指定", "新古", "新品・中古"):
            condition = "新\n古"
        elif condition in ("新", "新品"):
            condition = "新"
        elif condition in ("古", "中古"):
            condition = "古"
        normalized.append({
            **row,
            "source_file": "",
            "condition": condition,
            "note": row.get("note", ""),
            "stock": stock_codes[row["stock"]],
        })

    wb, _ = build_ishikari_workbook(now, normalized, f"{day}_ピッキングリスト.xlsx")
    try:
        # The previous barcode exporter stored all user text literally.
        # Preserve that behavior instead of letting openpyxl interpret formulas or errors.
        for ws in wb.worksheets:
            for cells in ws.iter_rows():
                for cell in cells:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
        with BytesIO() as out:
            wb.save(out)
            return out.getvalue()
    finally:
        wb.close()


def _save_ishikari_workbook(now: datetime, rows: list[dict], custom_filename: str | None = None, *, customer_readings=None) -> tuple[Path | None, dict]:
    r"""石狩倉庫用連番ブック (ピッキングリスト\YYYYMMDD\YYYYMMDDNN.xlsx) を作成"""
    if not rows:
        return None, {"ishikari": 0, "factory": 0, "carry": 0}

    if custom_filename and custom_filename.strip():
        file_name = custom_filename.strip()
        if not file_name.lower().endswith(".xlsx"):
            file_name += ".xlsx"
    else:
        file_name = get_next_ishikari_filename(now)
    # Use the date in an ordinary numbered filename even if the dialog was
    # opened before the cutoff or the user changed the suggested date.
    folder_day = now.date()
    named_day = re.fullmatch(r"(\d{8})\d{2,}\.xlsx", file_name, re.IGNORECASE)
    if named_day:
        try:
            folder_day = datetime.strptime(named_day[1], "%Y%m%d").date()
        except ValueError:
            pass
    target_dir = get_ishikari_base_dir() / folder_day.strftime("%Y%m%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / file_name

    wb, counts = build_ishikari_workbook(now, rows, file_name, customer_readings=customer_readings or {})
    try:
        wb.save(str(out_path))
    finally:
        wb.close()
    return out_path, counts


def _append_rows_to_report_sheet(ws, rows_data: list[dict], title: str, now: datetime, file_name: str):
    """報告用リストの指定シートにデータを追記"""
    ws.sheet_format.defaultRowHeight = 31.5
    ws.sheet_format.customHeight = True
    headers = ["依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考_1", "実行者", "確認", "入力", "確認"]
    
    font_header = Font(name="Yu Gothic", size=11, bold=True)
    font_data = Font(name="Yu Gothic", size=11, bold=True)
    font_cond = Font(name="Yu Gothic", size=9.5, bold=True)
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    align_cond = Alignment(horizontal="left", vertical="center", wrap_text=True)
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=False)
    border_thin = Border(
        left=Side(style='thin', color='888888'),
        right=Side(style='thin', color='888888'),
        top=Side(style='thin', color='888888'),
        bottom=Side(style='thin', color='888888')
    )
    fill_header = PatternFill(start_color="EAEAEA", end_color="EAEAEA", fill_type="solid")

    # ヘッダーが未設定なら設定
    last_data_row = next(
        (row for row in range(ws.max_row, 0, -1)
         if any(cell.value not in (None, "") for cell in ws[row])),
        0,
    )
    if not last_data_row:
        ws.row_dimensions[1].height = 25
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = font_header
            cell.alignment = align_header
            cell.border = border_thin
            cell.fill = fill_header
        last_data_row = 1

    start_row = last_data_row + 1
    for r_idx, r in enumerate(rows_data, start=start_row):
        row_vals = [r[key] for key in _REPORT_KEYS] + [""] * 4
        ws.row_dimensions[r_idx].height = 32
        for col_idx, value in enumerate(row_vals, start=1):
            cell = ws.cell(row=r_idx, column=col_idx)
            cell.value = value
            cell.border = border_thin
            if col_idx == 7: # 新古: 左端インデントで縦並び、はみ出さない
                cell.font = font_cond
                cell.alignment = align_cond
            elif col_idx in (1, 6, 8, 11, 12, 13, 14):
                cell.font = font_data
                cell.alignment = align_center
            else:
                cell.font = font_data
                cell.alignment = align_left

    col_widths = {1:9.0, 2:32.0, 3:20.0, 4:11.0, 5:14.0, 6:7.0, 7:5.5, 8:6.0, 9:15.0, 10:8.0, 11:8.0, 12:6.5, 13:6.5, 14:6.5}
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
    ws.oddHeader.left.text = '&"-,太字"&11 &D　&T'
    ws.oddHeader.center.text = f'&"-,太字"&14 {title}'
    ws.oddHeader.right.text = '&"-,太字"&11 &P / &N'
    ws.print_title_rows = '1:1'

def _report_has_external_data(path: Path) -> bool:
    """Identify Excel connections that a workbook round trip cannot retain."""
    with ZipFile(path) as archive:
        for part in archive.namelist():
            name = part.lower()
            if name == "xl/connections.xml" or name.startswith("xl/querytables/"):
                return True
            if name.startswith("xl/tables/") and name.endswith(".xml"):
                if ElementTree.fromstring(archive.read(part)).get("tableType") == "queryTable":
                    return True
    return False


def _append_to_external_report(source: Path, destination: Path, rows: list[dict]):
    """Patch only the report sheet in the original Excel package."""
    with ZipFile(source) as original:
        book = ElementTree.fromstring(original.read("xl/workbook.xml"))
        sheet = book.find(f".//{{{_SHEET_XML}}}sheet[@name='持ち込み']")
        if sheet is None:
            raise ValueError("外部データ接続付きの報告書に「持ち込み」シートがありません。")
        rel_id = sheet.get(f"{{{_DOCUMENT_REL}}}id")
        rels = ElementTree.fromstring(original.read("xl/_rels/workbook.xml.rels"))
        target = next(rel.attrib["Target"] for rel in rels if rel.get("Id") == rel_id)
        part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))

        doc = minidom.parseString(original.read(part))
        root = doc.documentElement
        data = root.getElementsByTagNameNS(_SHEET_XML, "sheetData")[0]
        existing_rows = {int(row.getAttribute("r")): row for row in data.getElementsByTagNameNS(_SHEET_XML, "row")}
        last_data_row = max(
            (index for index, row in existing_rows.items() if any(
                row.getElementsByTagNameNS(_SHEET_XML, tag) for tag in ("v", "is", "f")
            )),
            default=0,
        )
        if not last_data_row:
            raise ValueError("外部データ接続付きの報告書の「持ち込み」シートに見出しがありません。")
        first_row, end_row = last_data_row + 1, last_data_row + len(rows)
        if end_row > 1_048_576:
            raise ValueError("報告用リストがExcelの行数上限を超えます。")
        for tag in ("mergeCell", "autoFilter"):
            for item in root.getElementsByTagNameNS(_SHEET_XML, tag):
                left, top, _, bottom = range_boundaries(item.getAttribute("ref"))
                if left <= 14 and top <= end_row and bottom >= first_row:
                    raise ValueError("報告書の追記先が結合セルまたはフィルター範囲と重なっています。")
        if root.getElementsByTagNameNS(_SHEET_XML, "tablePart"):
            raise ValueError("外部データ接続付きの報告書では「持ち込み」シートに直接設定されたテーブルに追記できません。")

        def element(tag):
            return doc.createElementNS(_SHEET_XML, f"{root.prefix}:{tag}" if root.prefix else tag)

        formats = root.getElementsByTagNameNS(_SHEET_XML, "sheetFormatPr")
        if formats:
            sheet_format = formats[0]
        else:
            sheet_format = element("sheetFormatPr")
            columns = root.getElementsByTagNameNS(_SHEET_XML, "cols")
            root.insertBefore(sheet_format, columns[0] if columns else data)
        sheet_format.setAttribute("defaultRowHeight", "31.5")
        sheet_format.setAttribute("customHeight", "1")

        styles = {
            re.sub(r"\d+$", "", cell.getAttribute("r")): cell.getAttribute("s")
            for cell in existing_rows[last_data_row].getElementsByTagNameNS(_SHEET_XML, "c")
        } if last_data_row > 1 else {}
        following_rows = iter(sorted(i for i in existing_rows if i >= first_row))
        following = next(following_rows, None)
        for index, values in enumerate(rows, start=first_row):
            row = existing_rows.get(index)
            if row is None:
                row = element("row")
                row.setAttribute("r", str(index))
                data.insertBefore(row, existing_rows[following] if following is not None else None)
            else:
                following = next(following_rows, None)

            existing_cells = {
                cell.getAttribute("r"): cell for cell in row.getElementsByTagNameNS(_SHEET_XML, "c")
            }
            for col in range(1, 15):
                letter = get_column_letter(col)
                ref = f"{letter}{index}"
                cell = existing_cells.pop(ref, None)
                if cell is None:
                    cell = element("c")
                    cell.setAttribute("r", ref)
                    if styles.get(letter):
                        cell.setAttribute("s", styles[letter])
                else:
                    row.removeChild(cell)
                    if cell.hasAttribute("t"):
                        cell.removeAttribute("t")
                value = values[_REPORT_KEYS[col - 1]] if col <= len(_REPORT_KEYS) else ""
                if value != "":
                    if col == 8:
                        child = element("v")
                        child.appendChild(doc.createTextNode(str(value)))
                    else:
                        cell.setAttribute("t", "inlineStr")
                        child = element("is")
                        text = element("t")
                        text.setAttribute("xml:space", "preserve")
                        text.appendChild(doc.createTextNode(str(value)))
                        child.appendChild(text)
                    cell.appendChild(child)
                row.insertBefore(cell, next(iter(existing_cells.values()), None))

        dimensions = root.getElementsByTagNameNS(_SHEET_XML, "dimension")
        if dimensions:
            _, top, right, bottom = range_boundaries(dimensions[0].getAttribute("ref"))
            dimensions[0].setAttribute("ref", f"A{min(top, first_row)}:{get_column_letter(max(14, right))}{max(bottom, end_row)}")
        patched = doc.toxml(encoding="utf-8")
        ElementTree.fromstring(patched)
        with ZipFile(destination, "w") as output:
            for entry in original.infolist():
                output.writestr(entry, patched if entry.filename == part else original.read(entry))


def _save_report_workbook(now: datetime, rows: list[dict], custom_filename: str | None = None) -> tuple[Path | None, dict]:
    with _REPORT_SAVE_LOCK:
        return _save_report_workbook_locked(now, rows, custom_filename)


def _save_report_workbook_locked(now: datetime, rows: list[dict], custom_filename: str | None) -> tuple[Path | None, dict]:
    r"""報告用リスト (報告用リスト\報告用YYMMDD.xlsx) の「持ち込み」シートに追記保存"""
    carry_rows = [r for r in rows if r["stock"] == "b"]

    if not carry_rows:
        return None, {"carry": 0, "factory": 0}

    carry_rows.sort(key=lambda x: (x["customer"], x["name"]))

    report_dir = get_report_base_dir()
    report_dir.mkdir(parents=True, exist_ok=True)

    if custom_filename and custom_filename.strip():
        file_name = custom_filename.strip()
        if not file_name.lower().endswith(".xlsx"):
            file_name += ".xlsx"
    else:
        file_name = get_default_report_filename(now)
    out_path = report_dir / file_name

    if out_path.exists() and _report_has_external_data(out_path):
        temp_path = None
        before = out_path.stat()
        try:
            with NamedTemporaryFile(dir=report_dir, suffix=".xlsx", delete=False) as temp:
                temp_path = Path(temp.name)
            _append_to_external_report(out_path, temp_path, carry_rows)
            after = out_path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise OSError("報告用リストが編集中に更新されました。再度仕分けを実行してください。")
            os.replace(temp_path, out_path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return out_path, {"carry": len(carry_rows), "factory": 0}

    if out_path.exists():
        wb = openpyxl.load_workbook(str(out_path))
    else:
        wb = openpyxl.Workbook()
        ws_carry = wb.active
        ws_carry.title = "持ち込み"
        wb.create_sheet(title="倉庫")

    # 持ち込みシート
    if "持ち込み" not in wb.sheetnames:
        ws_carry = wb.create_sheet(title="持ち込み", index=0)
    else:
        ws_carry = wb["持ち込み"]
    _append_rows_to_report_sheet(ws_carry, carry_rows, "持ち込みリスト", now, out_path.name)

    if "工場" not in wb.sheetnames:
        wb.create_sheet(title="工場", index=wb.index(ws_carry) + 1)
    ws_factory = wb["工場"]
    if not any(value is not None for row in ws_factory.values for value in row):
        _apply_sheet_styling(ws_factory, [], "工場", now, out_path.name,
                             include_warehouse_column=False)

    # 倉庫シートがなければ枠だけ作成
    if "倉庫" not in wb.sheetnames:
        ws_ware = wb.create_sheet(title="倉庫")
        headers = ["依頼者", "顧客名", "アイテム名", "メーカー", "品番", "サイズ", "新古", "数量", "備考", "備考_1", "実行者", "確認", "入力", "確認"]
        ws_ware.append(headers)

    # 開いた時に「持ち込み」シートが最初に画面に出るようアクティブ設定
    if "持ち込み" in wb.sheetnames:
        target_ws = wb["持ち込み"]
        wb.active = target_ws
        for s in wb.worksheets:
            s.sheet_view.tabSelected = (s == target_ws)

    temp_path = None
    try:
        with NamedTemporaryFile(dir=report_dir, suffix=".xlsx", delete=False) as temp:
            temp_path = Path(temp.name)
        wb.save(temp_path)
        os.replace(temp_path, out_path)
    finally:
        wb.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return out_path, {
        "carry": len(carry_rows),
        "factory": 0
    }

def _processed_destination(day: date) -> Path:
    month_folder_name = f"{day.year}.{day.month}月"
    day_folder_name = day.isoformat().replace("-", "")
    return get_processed_base_dir() / month_folder_name / day_folder_name


def _file_stamp(path: Path) -> list[int]:
    info = path.stat()
    return [info.st_size, info.st_mtime_ns]


def _planned_moves(now, files, destination):
    reserved = set()
    moves = []
    for source in files:
        name = source.name
        suffix_number = 0
        while name.casefold() in reserved or (destination / name).exists():
            suffix_number += 1
            serial = f"_{suffix_number}" if suffix_number > 1 else ""
            name = f"{source.stem}_{now:%H%M%S}{serial}{source.suffix}"
        reserved.add(name.casefold())
        moves.append({"source": source.name, "destination": name, "stamp": _file_stamp(source)})
    return moves


def _save_move_journal(path, journal):
    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            json.dump(journal, temp, ensure_ascii=False)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _finish_saved_sort(path, journal, addresses=None):
    result = journal["result"]
    destination = _processed_destination(date.fromisoformat(result["processed_date"]))
    archived = []
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for entry in journal["moves"]:
            if any(Path(entry[key]).name != entry[key] or entry[key] in ("", ".", "..") for key in ("source", "destination")):
                raise ValueError("移動記録のファイル名が不正です。")
            source = get_processing_dir() / entry["source"]
            target = destination / entry["destination"]
            if source.exists():
                if _file_stamp(source) != entry["stamp"] or target.exists():
                    raise OSError(f"移動元または移動先が変更されています（{source.name}）。")
                source.rename(target)
            elif not target.is_file() or _file_stamp(target) != entry["stamp"]:
                raise OSError(f"元の指示書が移動先に見つかりません（{source.name}）。")
            archived.append((entry["source"], target))
    except OSError as exc:
        raise OSError(f"Excelは保存済みですが、指示書の移動を完了できませんでした。同じ画面から再実行すると移動を再開します。{exc}") from exc
    resumed = addresses is None
    if resumed:
        addresses = []
        for original_name, target in archived:
            address = _extract_address_from_workbook(target)
            if address:
                addresses.append({**address, "source_file": original_name})
    path.unlink()
    result["moved_count"] = len(archived)
    result["addresses"] = addresses
    result["resumed_move"] = resumed
    if resumed:
        result["message"] = "前回保存した指示書の移動を完了しました。新しい指示書は取込画面を開き直して仕分けしてください。"
    return result

def execute_sort(custom_ishikari_file: str | None = None, custom_report_file: str | None = None, *, processed_date: str | None = None, customer_readings=None) -> dict:
    """仕分け実行メインロジック"""
    with _SORT_LOCK:
        return _execute_sort(custom_ishikari_file, custom_report_file, processed_date, customer_readings)


def _execute_sort(custom_ishikari_file, custom_report_file, processed_date, customer_readings=None):
    journal_path = get_base_dir() / ".instruction-desk-pending-sort.json"
    if journal_path.exists():
        return _finish_saved_sort(journal_path, json.loads(journal_path.read_text(encoding="utf-8")))

    now = _default_sort_date(datetime.now(timezone(timedelta(hours=9))))
    move_date = now.date()
    if processed_date not in (None, ""):
        try:
            move_date = date.fromisoformat(processed_date)
            if move_date.isoformat() != processed_date:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("処理済みフォルダの日付を確認してください。") from None
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
    addresses = []
    dest_dir = _processed_destination(move_date)
    moves = _planned_moves(now, pending_files, dest_dir)

    for f in pending_files:
        rows = _extract_rows_from_workbook(f)
        all_rows.extend(rows)
        addr = _extract_address_from_workbook(f)
        if addr:
            addresses.append(addr)
    if any(_file_stamp(f) != move["stamp"] for f, move in zip(pending_files, moves)):
        raise OSError("読み込み中に指示書が更新されました。保存を終えてから再実行してください。")

    # 1. 石狩倉庫ブック (ピッキングリスト\YYYYMMDD\YYYYMMDDNN.xlsx)
    ishikari_path, ishikari_counts = _save_ishikari_workbook(
        now, all_rows, custom_filename=custom_ishikari_file, customer_readings=customer_readings
    )

    # 2. 報告用リスト (報告用リスト\報告用YYMMDD.xlsx) の「持ち込み」シートへ追記
    report_path, report_counts = _save_report_workbook(now, all_rows, custom_filename=custom_report_file)

    result = {
        "success": True,
        "message": "指示書の仕分けと保存が完了しました。",
        "ishikari_file": ishikari_path.name if ishikari_path else None,
        "ishikari_path": str(ishikari_path) if ishikari_path else None,
        "ishikari_counts": ishikari_counts,
        "report_file": report_path.name if report_path else None,
        "report_path": str(report_path) if report_path else None,
        "report_counts": report_counts,
        "processed_dir": str(dest_dir),
        "processed_date": move_date.isoformat(),
    }
    journal = {"moves": moves, "result": result}
    _save_move_journal(journal_path, journal)
    return _finish_saved_sort(journal_path, journal, addresses)
