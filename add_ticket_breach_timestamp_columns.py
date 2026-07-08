"""Idempotent migration: add SLA breach-detection timestamps to support_tickets.

  sla_response_breached_at   TIMESTAMPTZ NULL  -- when the response target was missed
  sla_resolution_breached_at TIMESTAMPTZ NULL  -- when the resolution target was missed

Both power the Breached desk (breach aging, sort-by-overage, "breached Xh ago").
Backfills existing breached rows with their due instant (honest aging; also prevents
the first breach sweep from spamming activities/notifications for ancient breaches).

Reads DATABASE_URL from .env directly (cwd-independent) so it hits the SAME DB the
backend uses. ASCII-only prints (the Windows cp1252 console crashes on unicode BEFORE
the SQL runs). Safe to re-run — uses ADD COLUMN IF NOT EXISTS.

    python add_ticket_breach_timestamp_columns.py
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
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS sla_response_breached_at TIMESTAMPTZ",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS sla_resolution_breached_at TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_sla_response_breached_at ON support_tickets (sla_response_breached_at)",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_sla_resolution_breached_at ON support_tickets (sla_resolution_breached_at)",
    ]
    for s in stmts:
        cur.execute(s)
        print("[OK] " + s)
    # Backfill: stamp already-breached rows with their due instant so aging is honest
    # and the first sweep run doesn't treat them as freshly detected.
    cur.execute("""UPDATE support_tickets
                   SET sla_response_breached_at = response_due_at
                   WHERE sla_response_breached = TRUE
                     AND sla_response_breached_at IS NULL
                     AND response_due_at IS NOT NULL""")
    print("[OK] backfilled sla_response_breached_at on %d rows" % cur.rowcount)
    cur.execute("""UPDATE support_tickets
                   SET sla_resolution_breached_at = resolution_due_at
                   WHERE sla_resolution_breached = TRUE
                     AND sla_resolution_breached_at IS NULL
                     AND resolution_due_at IS NOT NULL""")
    print("[OK] backfilled sla_resolution_breached_at on %d rows" % cur.rowcount)
    # verify
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name='support_tickets'
                     AND column_name IN ('sla_response_breached_at','sla_resolution_breached_at')
                   ORDER BY column_name""")
    have = [r[0] for r in cur.fetchall()]
    print("[DONE] support_tickets now has: %s" % ", ".join(have))
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
