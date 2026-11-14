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
    # Full-width to half-width alphanumeric/kana normalization if needed
    return s.lower()

def extract_branch_keywords(name: str) -> list:
    """Extract branch or location keywords like '帯広', '発寒', '北10条' from customer name."""
    if not name:
        return []
    keywords = []
    # 1. Split by spaces, full-width spaces, underscores, dots
    parts = [p.strip() for p in re.split(r"[\s\u3000・_]+", name) if p.strip()]
    if len(parts) >= 2:
        for p in parts[1:]:
            cleaned = re.sub(r"[㈱㈲（）\(\)株式会社有限会社]", "", p)
            if cleaned:
                keywords.append(normalize_name(cleaned))
                
    # 2. Extract words ending in 店, 支店, 営業所, 工場, 病院, 館 etc.
    m = re.findall(r"([^\s\u3000・_]{2,}(?:店|支店|営業所|工場|病院|館|センター|クリニック))", name)
    for w in m:
        nw = normalize_name(w)
        if nw and nw not in keywords:
            keywords.append(nw)
            # also root without 店
            root = re.sub(r"(店|支店|営業所|工場|病院|館|センター|クリニック)$", "", nw)
            if len(root) >= 2 and root not in keywords:
                keywords.append(root)
    return keywords

def parse_cycle_flag(flag_str: str) -> str:
    """Parse 8-digit flag like 91001000 into Japanese weekdays."""
    s = str(flag_str or "").strip()
    if not s or len(s) < 8 or not s.startswith("9"):
        return ""
    # Indices: 1: 月, 2: 火, 3: 水, 4: 木, 5: 金, 6: 土, 7: 日
    days_map = [
        (1, "月"),
        (2, "火"),
        (3, "水"),
        (4, "木"),
        (5, "金"),
        (6, "土"),
        (7, "日"),
    ]
    matched = [name for idx, name in days_map if idx < len(s) and s[idx] == "1"]
    if matched:
        return "・".join(matched)
    return ""

def get_shipment_info(customer_name: str, customer_code: str = "") -> dict:
    """Lookup shipment info from SQLite shipments table with strict store/branch safety."""
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
        
        # 3. Store/branch safety check
        target_branches = extract_branch_keywords(customer_name)
        
        # If target has a specific branch (e.g. 帯広, 発寒店), we MUST only match candidates that have that branch!
        # Do NOT fall back to random stores or headquarters!
        if target_branches:
            for r_name, r_w, r_t, r_c, r_m in all_shipments:
                r_norm = normalize_name(r_name)
                # Check if all branch keywords match or main branch matches
                if any(b in r_norm for b in target_branches):
                    # Also check that company name part matches
                    company_prefix = normalize_name(customer_name.split()[0] if customer_name.split() else customer_name)
                    clean_co = re.sub(r"(店|支店|営業所|工場|病院|館|センター|クリニック).*$", "", company_prefix)
                    if clean_co and clean_co in r_norm:
                        conn.close()
                        return {"weekday": r_w or "", "timeBand": r_t or "", "car": r_c or "", "method": r_m or ""}
            # Target has specific branch but no match found with that branch -> STOP (do NOT match other stores!)
            conn.close()
            return {}

        # 4. If target has NO specific branch, only match candidate that also has NO specific branch
        for r_name, r_w, r_t, r_c, r_m in all_shipments:
            r_branches = extract_branch_keywords(r_name)
            if not r_branches:
                r_norm = normalize_name(r_name)
                if r_norm and (norm_target == r_norm or (len(norm_target) >= 4 and norm_target in r_norm) or (len(r_norm) >= 4 and r_norm in norm_target)):
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
        cycle_flag = str(data.get("cycleFlag") or "").strip()
        
        # 1. Parse weekday from Access M_顧客マスタ flag (e.g. 91001000 -> "月・木")
        flag_weekday = parse_cycle_flag(cycle_flag)
        
        # 2. Check if Access explicitly marked no delivery (flag 90000000 and no cycle)
        # 90000000 means all weekday bits are 0
        is_explicit_no_delivery = (cycle_flag.startswith("90000000") or cycle_flag == "90000000") and not cycle_car
        
        ship_info = {}
        if not is_explicit_no_delivery:
            ship_info = get_shipment_info(customer_name, customer_code)
        
        weekday = ship_info.get("weekday") or flag_weekday or ""
        time_band = ship_info.get("timeBand") or ""
        car = ship_info.get("car") or cycle_car or ""
        method = ship_info.get("method") or ""
        
        # 3. Label accurately if no delivery is configured
        if is_explicit_no_delivery and not weekday and not car:
            weekday = "集配なし"
            car = "送り便 / 対象外"
            method = "送り便"
        elif not weekday and not car:
            weekday = "未設定"
            car = "未設定"
            
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
            "isNoDelivery": bool(is_explicit_no_delivery or weekday == "集配なし" or "送り便" in car or "対象外" in car),
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

SYNC_PS1_PATH = Path(__file__).parent / "sync_customers.ps1"

def init_customer_cache_table():
    """Ensure access_customers table exists in SQLite database."""
    if not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS access_customers (
                code TEXT PRIMARY KEY,
                name TEXT,
                cycle TEXT,
                flag TEXT,
                weekday TEXT,
                is_no_delivery INTEGER,
                updated_at TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ac_name ON access_customers(name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ac_cycle ON access_customers(cycle)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ac_is_no_deliv ON access_customers(is_no_delivery)")
        conn.commit()
        conn.close()
    except Exception:
        pass

def sync_customers_from_mdb() -> dict:
    """Read all customers from db3.mdb and cache into SQLite access_customers."""
    if not MDB_PATH.exists():
        return {"ok": False, "message": f"Accessデータベースが見つかりません: {MDB_PATH}"}
    if not SYNC_PS1_PATH.exists():
        return {"ok": False, "message": f"同期スクリプトが見つかりません: {SYNC_PS1_PATH}"}

    init_customer_cache_table()

    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(SYNC_PS1_PATH.resolve()),
            "-MdbPath", str(MDB_PATH.resolve())
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = (p.stdout or "").strip()
        if not out:
            return {"ok": False, "message": f"Access同期処理で応答がありませんでした。({p.stderr or ''})"}

        data = json.loads(out)
        if not isinstance(data, list):
            return {"ok": False, "message": "同期データ形式が不正です。"}

        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        cur = conn.cursor()
        
        # Batch upsert
        rows = []
        for r in data:
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            name = str(r.get("name") or "").strip()
            cycle = str(r.get("cycle") or "").strip()
            flag = str(r.get("flag") or "").strip()
            
            wday = parse_cycle_flag(flag)
            
            # Determine delivery type
            # 90000000 with empty cycle is definitely no delivery
            is_no_deliv = 1 if ((flag == "90000000" or flag == "0" or not flag) and (not cycle or cycle == "0" or cycle == "なし")) else 0
            
            rows.append((code, name, cycle, flag, wday, is_no_deliv, now_str))

        cur.execute("DELETE FROM access_customers")
        cur.executemany("""
            INSERT OR REPLACE INTO access_customers (code, name, cycle, flag, weekday, is_no_delivery, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)
        
        conn.commit()
        conn.close()
        
        return {"ok": True, "count": len(rows), "updatedAt": now_str}
    except Exception as e:
        return {"ok": False, "message": f"同期失敗: {e}"}

def get_delivery_customers_list(q: str = "", delivery_type: str = "all", car: str = "", weekday: str = "", limit: int = 200, offset: int = 0) -> dict:
    """Fetch cached customers list with filters."""
    init_customer_cache_table()
    
    if not DB_PATH.exists():
        return {"ok": True, "total": 0, "filtered": 0, "customers": [], "cars": [], "weekdays": []}
        
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        cur = conn.cursor()
        
        # Check count of cached customers
        cur.execute("SELECT COUNT(*) FROM access_customers")
        total_in_db = cur.fetchone()[0]
        
        # If empty, attempt auto-sync once if MDB is reachable
        if total_in_db == 0 and MDB_PATH.exists():
            sync_res = sync_customers_from_mdb()
            if sync_res.get("ok"):
                cur.execute("SELECT COUNT(*) FROM access_customers")
                total_in_db = cur.fetchone()[0]

        # Get unique cars and weekdays for filter dropdowns
        cur.execute("SELECT DISTINCT cycle FROM access_customers WHERE cycle != '' AND cycle != '0' ORDER BY cycle")
        cars = [r[0] for r in cur.fetchall() if r[0]]
        
        cur.execute("SELECT DISTINCT weekday FROM access_customers WHERE weekday != '' ORDER BY weekday")
        weekdays = [r[0] for r in cur.fetchall() if r[0]]

        # Build filter query
        where_clauses = []
        params = []
        
        # Delivery type filter: 'regular' (定期集配), 'delivery_only' (送り便/集配なし), 'all' (すべて)
        if delivery_type == "regular":
            where_clauses.append("is_no_delivery = 0")
        elif delivery_type == "delivery_only":
            where_clauses.append("is_no_delivery = 1")
            
        if car:
            where_clauses.append("cycle = ?")
            params.append(car)
            
        if weekday:
            where_clauses.append("weekday = ?")
            params.append(weekday)
            
        if q:
            # Multi-word search
            words = [w.strip() for w in q.split() if w.strip()]
            for w in words:
                where_clauses.append("(name LIKE ? OR code LIKE ?)")
                params.extend([f"%{w}%", f"%{w}%"])
                
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        
        # Filtered count
        cur.execute(f"SELECT COUNT(*) FROM access_customers {where_sql}", params)
        filtered_count = cur.fetchone()[0]
        
        # Fetch records
        query = f"""
            SELECT code, name, cycle, flag, weekday, is_no_delivery, updated_at
            FROM access_customers
            {where_sql}
            ORDER BY is_no_delivery ASC, code ASC
            LIMIT ? OFFSET ?
        """
        cur.execute(query, params + [max(1, min(limit, 20000)), max(0, offset)])
        
        records = []
        for r_code, r_name, r_cycle, r_flag, r_wday, r_is_no_deliv, r_upd in cur.fetchall():
            display_car = r_cycle if (r_cycle and r_cycle != "0") else ("送り便 / 対象外" if r_is_no_deliv else "未設定")
            display_wday = r_wday if r_wday else ("集配なし" if r_is_no_deliv else "未設定")
            records.append({
                "code": r_code,
                "name": r_name,
                "cycle": display_car,
                "weekday": display_wday,
                "flag": r_flag,
                "isNoDelivery": bool(r_is_no_deliv)
            })
            
        conn.close()
        return {
            "ok": True,
            "total": total_in_db,
            "filtered": filtered_count,
            "cars": cars,
            "weekdays": weekdays,
            "customers": records
        }
    except Exception as e:
        return {"ok": False, "message": f"一覧取得エラー: {e}", "customers": []}
