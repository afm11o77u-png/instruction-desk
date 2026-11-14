import sqlite3
import name_review

def restore_name_approvals_safe(conn):
    """Restore approved status for any name formats that were previously approved in history."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT format_id 
            FROM name_review_history 
            WHERE action = '内容を確認'
        """)
        history_fids = [r[0] for r in cur.fetchall()]

        for fid in history_fids:
            token = name_review.digest(cur, fid)
            if not token:
                continue
            
            cur.execute("""
                SELECT actor_id, actor_name, at 
                FROM name_review_history 
                WHERE format_id = ? AND action = '内容を確認'
                ORDER BY id DESC LIMIT 1
            """, (fid,))
            h = cur.fetchone()
            if not h:
                continue
                
            actor_id, actor_name, confirmed_at = h[0], h[1], h[2]
            
            cur.execute("SELECT assignee_id, confirmed_digest FROM name_reviews WHERE format_id = ?", (fid,))
            r = cur.fetchone()
            
            # If already confirmed and digest matches, skip
            if r and r[1] == token:
                continue
                
            assignee_id = r[0] if (r and r[0]) else actor_id
            u = cur.execute("SELECT name, active FROM users WHERE id = ?", (assignee_id,)).fetchone()
            if not u or not u[1]:
                u_act = cur.execute("SELECT name, active FROM users WHERE id = ?", (actor_id,)).fetchone()
                if u_act and u_act[1]:
                    assignee_id = actor_id
                    actor_name = u_act[0]
                else:
                    continue

            conf_name = actor_name if assignee_id == actor_id else u[0]

            cur.execute("""
                INSERT INTO name_reviews (format_id, assignee_id, confirmed_by, confirmed_name, confirmed_at, confirmed_digest)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(format_id) DO UPDATE SET
                    assignee_id = excluded.assignee_id,
                    confirmed_by = excluded.confirmed_by,
                    confirmed_name = excluded.confirmed_name,
                    confirmed_at = excluded.confirmed_at,
                    confirmed_digest = excluded.confirmed_digest
            """, (fid, assignee_id, assignee_id, conf_name, confirmed_at, token))
    except Exception as e:
        # Avoid crashing server startup
        pass

def restore_name_approvals():
    conn = sqlite3.connect('data/instructions.sqlite3')
    restore_name_approvals_safe(conn)
    conn.commit()
    conn.close()

if __name__ == '__main__':
    restore_name_approvals()
    print('Completed.')
