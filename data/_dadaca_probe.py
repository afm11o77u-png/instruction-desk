# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path
p = Path(r'C:\instruction-desk\data\instructions.sqlite3')
c = sqlite3.connect(p)
c.row_factory = sqlite3.Row
rows = c.execute("SELECT id, source_name FROM name_customers WHERE source_name LIKE '%DADACA%' OR source_name LIKE '%ダダカ%' OR source_name LIKE '%千歳%' OR source_name LIKE '%夕張%'").fetchall()
out = []
out.append('customers ' + str(len(rows)))
for r in rows:
    out.append(f"C {r['id']} {r['source_name']}")
    forms = c.execute('SELECT id, format_name FROM name_formats WHERE customer_id=? AND retired=0', (r['id'],)).fetchall()
    for f in forms:
        rev = c.execute('SELECT * FROM name_reviews WHERE format_id=?', (f['id'],)).fetchone()
        out.append(f"  F {f['id']} {f['format_name']} review={dict(rev) if rev else None}")
# also users
users = c.execute('SELECT id, name, role, active FROM users').fetchall()
out.append('USERS')
for u in users:
    out.append(f"{u['id']} {u['name']} {u['role']} {u['active']}")
Path(r'C:\instruction-desk\data\_dadaca_probe.txt').write_text('\n'.join(out), encoding='utf-8')
