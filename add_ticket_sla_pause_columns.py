"""Idempotent migration: add SLA stop-the-clock columns to support_tickets.

  sla_paused_since TIMESTAMPTZ NULL   -- clock frozen since (NULL = running)
  sla_paused_ms    BIGINT NOT NULL DEFAULT 0  -- total paused time (display/reporting)

Reads DATABASE_URL from .env directly (cwd-independent) so it hits the SAME DB the
backend uses. ASCII-only prints (the Windows cp1252 console crashes on unicode BEFORE
the SQL runs). Safe to re-run — uses ADD COLUMN IF NOT EXISTS.

    python add_ticket_sla_pause_columns.py
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
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS sla_paused_since TIMESTAMPTZ",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS sla_paused_ms BIGINT NOT NULL DEFAULT 0",
    ]
    for s in stmts:
        cur.execute(s)
        print("[OK] " + s)
    # verify
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name='support_tickets' AND column_name IN ('sla_paused_since','sla_paused_ms')
                   ORDER BY column_name""")
    have = [r[0] for r in cur.fetchall()]
    print("[DONE] support_tickets now has: %s" % ", ".join(have))
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
