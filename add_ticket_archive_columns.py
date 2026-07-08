"""Idempotent migration: add archive-provenance columns to support_tickets.

  archived_at         TIMESTAMPTZ NULL  -- when the record entered deep storage
  archived_by_id      UUID NULL         -- who archived it (NULL = System / retention sweep)
  archive_reason_code VARCHAR(40) NULL  -- coded taxonomy (spam|duplicate|...|auto_retention)
  legal_hold          BOOLEAN NOT NULL DEFAULT FALSE

All four power the Archived "Deep Storage" desk (stats, retention rail, purge guards).
Backfill for rows already soft-deleted (is_deleted = TRUE):
  1. latest activity row action='archived' -> archived_at + archived_by_id
  2. fallback: archived_at = updated_at (rows tombstoned by raw SQL / probes)
  3. archive_reason_code stays NULL for legacy rows -> the desk renders them "uncoded"

Reads DATABASE_URL from .env directly (cwd-independent) so it hits the SAME DB the
backend uses. ASCII-only prints (the Windows cp1252 console crashes on unicode BEFORE
the SQL runs). Safe to re-run -- uses ADD COLUMN IF NOT EXISTS + NULL-guarded updates.

    python add_ticket_archive_columns.py
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
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS archived_by_id UUID REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS archive_reason_code VARCHAR(40)",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN NOT NULL DEFAULT FALSE",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_archived_at ON support_tickets (archived_at)",
    ]
    for s in stmts:
        cur.execute(s)
        print("[OK] " + s)

    # 1) archived_at + archived_by_id from the newest 'archived' activity
    cur.execute("""
        UPDATE support_tickets t
        SET archived_at = a.created_at,
            archived_by_id = a.actor_user_id
        FROM (
            SELECT DISTINCT ON (ticket_id) ticket_id, actor_user_id, created_at
            FROM support_ticket_activities
            WHERE action = 'archived'
            ORDER BY ticket_id, created_at DESC
        ) a
        WHERE a.ticket_id = t.id
          AND t.is_deleted = TRUE
          AND t.archived_at IS NULL
    """)
    print("[OK] backfilled archived_at/by from activities on %d rows" % cur.rowcount)

    # 2) fallback for tombstoned rows with no activity trail (raw-SQL / probe leftovers)
    cur.execute("""
        UPDATE support_tickets
        SET archived_at = updated_at
        WHERE is_deleted = TRUE
          AND archived_at IS NULL
    """)
    print("[OK] fallback archived_at = updated_at on %d rows" % cur.rowcount)

    # verify
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name='support_tickets'
                     AND column_name IN ('archived_at','archived_by_id','archive_reason_code','legal_hold')
                   ORDER BY column_name""")
    have = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT count(*) FROM support_tickets WHERE is_deleted = TRUE")
    n_arc = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM support_tickets WHERE is_deleted = TRUE AND archived_at IS NULL")
    n_gap = cur.fetchone()[0]
    print("[DONE] support_tickets now has: %s" % ", ".join(have))
    print("[DONE] archived rows: %d (missing archived_at: %d)" % (n_arc, n_gap))
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
