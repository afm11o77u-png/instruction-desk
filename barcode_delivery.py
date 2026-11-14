"""Barcode delivery lookup service using db3.mdb and instructions.sqlite3"""
import os
import re
import json
import sqlite3
import subprocess
from pathlib import Path

MDB_PATH = Path(r"C:\Users\zfm11\Desktop\db3.mdb")
DB_PATH = Path("data/instructions.sqlite3")
PS1_PATH = Path(__file__).parent / "barcode_delivery.ps1"

# In-memory cache for fast lookup
_CACHE = {}

def normalize_name(name: str) -> str:
    """Normalize customer name for fuzzy matching."""
    if not name:
        return ""
    # Remove whitespace, full-width whitespace, brackets, company symbols
    s = re.sub(r"[\s\u3000㈱㈲（）\(\)株式会社有限会社]", "", str(name))
    return s.lower()

def get_shipment_info(customer_name: str, customer_code: str = "") -> dict:
    """Lookup shipment info from SQLite shipments table."""
    if not DB_PATH.exists() or not customer_name:
        return {}
    
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        cur = conn.cursor()
        
        # 1. Exact match on customer name
        cur.execute("SELECT weekday, time_band, car, method FROM shipments WHERE customer = ? LIMIT 1", (customer_name,))
        row = cur.fetchone()
        if row and (row[0] or row[1] or row[2]):
            conn.close()
            return {"weekday": row[0] or "", "timeBand": row[1] or "", "car": row[2] or "", "method": row[3] or ""}
        
        # 2. Normalized match across all shipments
        norm_target = normalize_name(customer_name)
        cur.execute("SELECT customer, weekday, time_band, car, method FROM shipments")
        all_shipments = cur.fetchall()
        for r_name, r_w, r_t, r_c, r_m in all_shipments:
            if normalize_name(r_name) == norm_target:
                conn.close()
                return {"weekday": r_w or "", "timeBand": r_t or "", "car": r_c or "", "method": r_m or ""}
            
        # 3. Substring match
        if len(norm_target) >= 3:
            for r_name, r_w, r_t, r_c, r_m in all_shipments:
                r_norm = normalize_name(r_name)
                if r_norm and (norm_target in r_norm or r_norm in norm_target):
                    conn.close()
                    return {"weekday": r_w or "", "timeBand": r_t or "", "car": r_c or "", "method": r_m or ""}

        # 4. Token-based match (e.g. "光塩東館調理 玉置" -> check each chunk)
        tokens = [p for p in re.split(r"[\s\u3000・_]+", customer_name) if len(p) >= 2]
        for tok in tokens:
            t_norm = normalize_name(tok)
            if len(t_norm) >= 2:
                for r_name, r_w, r_t, r_c, r_m in all_shipments:
                    r_norm = normalize_name(r_name)
                    if t_norm in r_norm or (len(r_norm) >= 3 and r_norm in t_norm):
                        conn.close()
                        return {"weekday": r_w or "", "timeBand": r_t or "", "car": r_c or "", "method": r_m or ""}

        # 5. Prefix match on first non-department chunk
        first_chunk = tokens[0] if tokens else customer_name
        clean_tok = re.sub(r"[調理販売食堂工場店舗売店厨房本部支店男女]+$", "", normalize_name(first_chunk))
        for pfx_len in (4, 3, 2):
            if len(clean_tok) >= pfx_len:
                pfx = clean_tok[:pfx_len]
                for r_name, r_w, r_t, r_c, r_m in all_shipments:
                    if pfx in normalize_name(r_name):
                        conn.close()
                        return {"weekday": r_w or "", "timeBand": r_t or "", "car": r_c or "", "method": r_m or ""}
                    
        conn.close()
    except Exception:
        pass
        
    return {}

def lookup_barcode(code: str) -> dict:
    """Lookup MasterCode in db3.mdb and combine with shipments info."""
    code = str(code or "").strip()
    if not code:
        return {"ok": False, "found": False, "message": "バーコード番号が空です。"}
    
    if code in _CACHE:
        return _CACHE[code]
    
    if not MDB_PATH.exists():
        return {
            "ok": False,
            "found": False,
            "message": f"Accessデータベースファイルが見つかりません。({MDB_PATH})"
        }
    
    clean_code = re.sub(r"[^0-9a-zA-Z_\-\/]", "", code)
    if not clean_code:
        return {"ok": False, "found": False, "message": "バーコード番号の形式が正しくありません。"}

    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(PS1_PATH.resolve()),
            "-Code", clean_code,
            "-MdbPath", str(MDB_PATH.resolve())
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        out = (p.stdout or "").strip()
        if not out:
            err = (p.stderr or "").strip()
            return {"ok": False, "found": False, "message": f"検索処理でエラーが発生しました。({err or '応答なし'})"}
            
        data = json.loads(out)
        if not data.get("found"):
            err = data.get("error")
            msg = f"バーコード番号【{code}】に該当するデータは見つかりませんでした。"
            if err:
                msg += f" (DB情報: {err})"
            return {"ok": True, "found": False, "masterCode": code, "message": msg}
            
        # Found in db3.mdb! Combine with shipments table
        customer_name = str(data.get("customer") or "").strip()
        customer_code = str(data.get("customerCode") or "").strip()
        cycle_car = str(data.get("cycle") or "").strip()
        
        ship_info = get_shipment_info(customer_name, customer_code)
        
        weekday = ship_info.get("weekday") or ""
        time_band = ship_info.get("timeBand") or ""
        car = ship_info.get("car") or ""
        method = ship_info.get("method") or ""
        
        # If shipments table didn't have car/cycle, fallback to M_顧客マスタ
        if not car and cycle_car:
            car = cycle_car
            
        result = {
            "ok": True,
            "found": True,
            "masterCode": str(data.get("masterCode") or code).strip(),
            "customer": customer_name,
            "customerCode": customer_code,
            "weekday": weekday,
            "car": car,
            "timeBand": time_band,
            "method": method,
            "itemName": str(data.get("itemName") or "").strip(),
            "size": str(data.get("size") or "").strip(),
            "department": str(data.get("department") or "").strip(),
            "person": str(data.get("person") or "").strip()
        }
        
        # Save to cache
        _CACHE[code] = result
        return result
        
    except Exception as e:
        return {"ok": False, "found": False, "message": f"照会エラー: {e}"}
