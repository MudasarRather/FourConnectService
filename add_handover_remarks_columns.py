"""Ad-hoc schema patch: add `system_vendor` + `client_remarks` to project_handovers.

`Base.metadata.create_all()` creates NEW tables (e.g. handover_deliverables) but does
NOT add columns to existing tables — so these two columns on the pre-existing
`project_handovers` table must be added manually. Idempotent (ADD COLUMN IF NOT EXISTS).

Reads DATABASE_URL straight from .env (do NOT trust get_settings() cwd resolution — see
CLAUDE.md). Run from the backend root:
    & "<python>" add_handover_remarks_columns.py
"""
import re
from pathlib import Path

import psycopg2

ENV = Path(__file__).resolve().parent / ".env"


def db_url() -> str:
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    m = re.search(r'^DATABASE_URL\s*=\s*(.+)$', text, re.MULTILINE)
    if not m:
        return "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"
    return m.group(1).strip().strip('"').strip("'")


def main():
    url = db_url()
    m = re.search(r'postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)', url)
    user, pw, host, port, dbname = m.groups()
    conn = psycopg2.connect(dbname=dbname, user=user, password=pw, host=host, port=port)
    conn.autocommit = True
    cur = conn.cursor()
    stmts = [
        "ALTER TABLE project_handovers ADD COLUMN IF NOT EXISTS system_vendor VARCHAR",
        "ALTER TABLE project_handovers ADD COLUMN IF NOT EXISTS client_remarks TEXT",
    ]
    for s in stmts:
        cur.execute(s)
        print("OK:", s)
    # Verify
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='project_handovers' AND column_name IN ('system_vendor','client_remarks')"
    )
    print("present:", sorted(r[0] for r in cur.fetchall()))
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
