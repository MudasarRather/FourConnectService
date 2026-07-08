"""Idempotent migration: add resolution-attribution columns to support_tickets.

  resolved_by_id UUID NULL  -- who recorded the fix (NULL = system auto-resolve)
  closed_by_id   UUID NULL  -- who closed the ticket (NULL = auto-close sweep)

Both power the Resolved desk (resolver leaderboard, resolved_by filter). Backfill:
  1. latest activity row  action='resolved'                     -> resolved_by_id
  2. latest activity row  action='status_changed' detail.to='closed' -> closed_by_id
  3. fallback: assigned_agent_id where resolved_at is set and resolved_by_id still NULL
Backfill only touches rows currently RESOLVED/CLOSED (a reopen clears the live
resolution record, so re-attributing reopened tickets would be dishonest).

Reads DATABASE_URL from .env directly (cwd-independent) so it hits the SAME DB the
backend uses. ASCII-only prints (the Windows cp1252 console crashes on unicode BEFORE
the SQL runs). Safe to re-run -- uses ADD COLUMN IF NOT EXISTS + NULL-guarded updates.

    python add_ticket_resolution_actor_columns.py
"""
import os
import re
import psycopg2


def _database_url() -> str:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    url = None
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*DATABASE_URL\s*=\s*(.+)\s*$", line)
                if m:
                    url = m.group(1).strip().strip('"').strip("'")
                    break
    return url or "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


def main():
    url = _database_url()
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/([^?]+)", url)
    if not m:
        print("[FAIL] could not parse DATABASE_URL")
        return
    user, pw, host, port, dbname = m.groups()
    print("[..] connecting to %s:%s/%s" % (host, port, dbname))
    conn = psycopg2.connect(dbname=dbname, user=user, password=pw, host=host, port=int(port))
    conn.autocommit = True
    cur = conn.cursor()
    stmts = [
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS resolved_by_id UUID REFERENCES users(id)",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS closed_by_id UUID REFERENCES users(id)",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_resolved_by_id ON support_tickets (resolved_by_id)",
    ]
    for s in stmts:
        cur.execute(s)
        print("[OK] " + s)

    # 1) resolved_by_id from the latest actor-stamped 'resolved' activity
    cur.execute("""
        UPDATE support_tickets t
        SET resolved_by_id = a.actor_user_id
        FROM (
            SELECT DISTINCT ON (ticket_id) ticket_id, actor_user_id
            FROM support_ticket_activities
            WHERE action = 'resolved' AND actor_user_id IS NOT NULL
            ORDER BY ticket_id, created_at DESC
        ) a
        WHERE a.ticket_id = t.id
          AND t.resolved_by_id IS NULL
          AND t.status IN ('resolved', 'closed')
          AND t.resolved_at IS NOT NULL
    """)
    print("[OK] backfilled resolved_by_id from activities on %d rows" % cur.rowcount)

    # 2) closed_by_id from the latest actor-stamped close transition
    cur.execute("""
        UPDATE support_tickets t
        SET closed_by_id = a.actor_user_id
        FROM (
            SELECT DISTINCT ON (ticket_id) ticket_id, actor_user_id
            FROM support_ticket_activities
            WHERE action = 'status_changed'
              AND detail->>'to' = 'closed'
              AND actor_user_id IS NOT NULL
            ORDER BY ticket_id, created_at DESC
        ) a
        WHERE a.ticket_id = t.id
          AND t.closed_by_id IS NULL
          AND t.status = 'closed'
    """)
    print("[OK] backfilled closed_by_id from activities on %d rows" % cur.rowcount)

    # 3) fallback: attribute to the assigned agent (what the stats derived before)
    cur.execute("""
        UPDATE support_tickets
        SET resolved_by_id = assigned_agent_id
        WHERE resolved_by_id IS NULL
          AND assigned_agent_id IS NOT NULL
          AND resolved_at IS NOT NULL
          AND status IN ('resolved', 'closed')
    """)
    print("[OK] fallback resolved_by_id = assigned_agent_id on %d rows" % cur.rowcount)

    # verify
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name='support_tickets'
                     AND column_name IN ('resolved_by_id','closed_by_id')
                   ORDER BY column_name""")
    have = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT count(*) FROM support_tickets WHERE resolved_by_id IS NOT NULL")
    n = cur.fetchone()[0]
    print("[DONE] support_tickets now has: %s (resolved_by_id set on %d rows)" % (", ".join(have), n))
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
