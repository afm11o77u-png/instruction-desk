"""バーコードでピッキングリスト作成の「統合待ち」。

バーコード画面の明細（石狩倉庫・発寒支店・管理担当持込）を統合待ちとして保存し、
指示書ピッキング自動仕分けの「仕分けを実行する」で指示書と一緒に1冊へまとめる。
統合した分は統合済み（保存したブック名つき）にして、二度と入らないようにする。
"""
import json
from datetime import datetime

PENDING, MERGED, CANCELLED = '統合待ち', '統合済み', '取消'


def ensure(c):
    c.execute('''CREATE TABLE IF NOT EXISTS picking_merge_queue(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, day TEXT NOT NULL, rows_json TEXT NOT NULL,
      row_count INTEGER NOT NULL, status TEXT NOT NULL DEFAULT '統合待ち', created_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '',
      merged_at TEXT, merged_file TEXT, cancelled_at TEXT, cancelled_by TEXT)''')


def _now():
    return datetime.now().isoformat(sep=' ', timespec='seconds')


def _actor(u):
    try:
        return str(u['name'] or u['login'] or '')[:80] if u else ''
    except (KeyError, IndexError, TypeError):
        return ''


def add(c, u, title, day, rows):
    """rows は picking.validate_rows(final=True) 済みのもの。"""
    ensure(c)
    cur = c.execute('INSERT INTO picking_merge_queue(title,day,rows_json,row_count,status,created_at,created_by) VALUES(?,?,?,?,?,?,?)',
                    (title, day, json.dumps(rows, ensure_ascii=False), len(rows), PENDING, _now(), _actor(u)))
    return cur.lastrowid


def listing(c):
    ensure(c)
    return [dict(id=r['id'], title=r['title'], day=r['day'], rows=r['row_count'], createdAt=r['created_at'], createdBy=r['created_by'])
            for r in c.execute('SELECT * FROM picking_merge_queue WHERE status=? ORDER BY id', (PENDING,))]


def cancel(c, u, batch_id):
    ensure(c)
    cur = c.execute('UPDATE picking_merge_queue SET status=?,cancelled_at=?,cancelled_by=? WHERE id=? AND status=?',
                    (CANCELLED, _now(), _actor(u), batch_id, PENDING))
    if cur.rowcount != 1:
        raise ValueError('統合待ちのリストが見つかりません（統合済みか取消済みです）。')


def pending(c):
    """仕分け用: (ids, [(title, rows), ...])。"""
    ensure(c)
    ids, batches = [], []
    for r in c.execute('SELECT id,title,rows_json FROM picking_merge_queue WHERE status=? ORDER BY id', (PENDING,)):
        ids.append(r['id'])
        batches.append((r['title'], json.loads(r['rows_json'])))
    return ids, batches


def mark_merged(c, ids, file_name):
    ensure(c)
    now = _now()
    for i in ids or []:
        c.execute('UPDATE picking_merge_queue SET status=?,merged_at=?,merged_file=? WHERE id=? AND status!=?',
                  (MERGED, now, file_name or '', int(i), MERGED))
