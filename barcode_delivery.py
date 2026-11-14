"""Barcode delivery lookup service using db3.mdb and instructions.sqlite3"""
import os
import re
import json
import sqlite3
import subprocess
from pathlib import Path

MDB_PATH = Path(r"C:\Users\zfm11\Desktop\db3.mdb")
DB_PATH = Path("data/instructions.sqlite3")

# In-memory cache for fast lookup
_CACHE = {}

def normalize_name(name: str) -> str:
    """Normalize customer name for fuzzy matching."""
    if not name:
        return ""
    # Remove whitespace, full-width whitespace, brackets, company symbols
    s = re.sub(r"[\s\u3000㈱㈲（）\(\)株式会社有限会社]", "", name)
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
        for r_name, r_w, r_t, r_c, r_m in cur.fetchall():
            if normalize_name(r_name) == norm_target:
                conn.close()
                return {"weekday": r_w or "", "timeBand": r_t or "", "car": r_c or "", "method": r_m or ""}
            
        # 3. Substring match
        if len(norm_target) >= 3:
            cur.execute("SELECT customer, weekday, time_band, car, method FROM shipments")
            for r_name, r_w, r_t, r_c, r_m in cur.fetchall():
                r_norm = normalize_name(r_name)
                if r_norm and (norm_target in r_norm or r_norm in norm_target):
                    conn.close()
                    return {"weekday": r_w or "", "timeBand": r_t or "", "car": r_c or "", "method": r_m or ""}
                    
        conn.close()
    except Exception as e:
        pass
        
    return {}

def lookup_barcode(code: str) -> dict:
    """Lookup MasterCode in db3.mdb and combine with shipments info."""
    code = str(code).strip()
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
    
    # PowerShell script to query db3.mdb
    # Note: Using parameters or escaped string to avoid SQL injection
    clean_code = re.sub(r"[^0-9a-zA-Z_\-\/]", "", code)
    if not clean_code:
        return {"ok": False, "found": False, "message": "バーコード番号の形式が正しくありません。"}
        
    ps_script = f"""
$connStr = "Provider=Microsoft.ACE.OLEDB.12.0;Data Source={MDB_PATH};Persist Security Info=False;"
$conn = New-Object System.Data.OleDb.OleDbConnection($connStr)
try {{
    $conn.Open()
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = @"
SELECT TOP 1
    s.MasterCode,
    c.顧客名,
    c.顧客コード,
    c.集配サイクル,
    c.集配曜日フラグ,
    p.商品名,
    p.ラベル表示用_商品名,
    s.サイズ,
    k.部署名,
    k.個人名
FROM ((([M_識別コードマスタ] s
LEFT JOIN [M_顧客マスタ] c ON s.顧客コード = c.顧客コード)
LEFT JOIN [M_商品マスタ] p ON s.商品コード = p.商品コード)
LEFT JOIN [M_契約マスタ] k ON s.KID = k.KID)
WHERE s.MasterCode = '{clean_code}'
"@
    $dt = New-Object System.Data.DataTable
    $adapter = New-Object System.Data.OleDb.OleDbDataAdapter($cmd)
    [void]$adapter.Fill($dt)
    if ($dt.Rows.Count -gt 0) {{
        $r = $dt.Rows[0]
        $res = @{{
            found = $true
            masterCode = [string]$r["MasterCode"]
            customer = [string]$r["顧客名"]
            customerCode = [string]$r["顧客コード"]
            cycle = [string]$r["集配サイクル"]
            cycleFlag = [string]$r["集配曜日フラグ"]
            itemName = if ($r["ラベル表示用_商品名"]) {{ [string]$r["ラベル表示用_商品名"] }} else {{ [string]$r["商品名"] }}
            size = [string]$r["サイズ"]
            department = [string]$r["部署名"]
            person = [string]$r["個人名"]
        }}
        $res | ConvertTo-Json -Compress
    }} else {{
        @{{ found = $false }} | ConvertTo-Json -Compress
    }}
}} catch {{
    @{{ found = $false; error = $_.Exception.Message }} | ConvertTo-Json -Compress
}} finally {{
    $conn.Close()
}}
"""

    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10
        )
        out = p.stdout.strip()
        if not out:
            return {"ok": False, "found": False, "message": "検索処理から応答がありませんでした。"}
            
        data = json.loads(out)
        if not data.get("found"):
            err = data.get("error")
            msg = f"バーコード番号【{code}】に該当するデータは見つかりませんでした。"
            if err:
                msg += f" (DB情報: {err})"
            return {"ok": True, "found": False, "masterCode": code, "message": msg}
            
        # Found in db3.mdb! Combine with shipments table
        customer_name = data.get("customer", "").strip()
        customer_code = data.get("customerCode", "").strip()
        ship_info = get_shipment_info(customer_name, customer_code)
        
        weekday = ship_info.get("weekday") or ""
        time_band = ship_info.get("timeBand") or ""
        car = ship_info.get("car") or ""
        method = ship_info.get("method") or ""
        
        # If shipments table didn't have car/cycle, fallback to M_顧客マスタ
        if not car and data.get("cycle"):
            car = data.get("cycle").strip()
            
        result = {
            "ok": True,
            "found": True,
            "masterCode": data.get("masterCode") or code,
            "customer": customer_name,
            "customerCode": customer_code,
            "weekday": weekday,
            "car": car,
            "timeBand": time_band,
            "method": method,
            "itemName": data.get("itemName") or "",
            "size": data.get("size") or "",
            "department": data.get("department") or "",
            "person": data.get("person") or ""
        }
        
        # Save to cache
        _CACHE[code] = result
        return result
        
    except Exception as e:
        return {"ok": False, "found": False, "message": f"照会エラー: {e}"}
