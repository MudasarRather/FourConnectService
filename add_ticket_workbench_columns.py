"""Workbench Support-Desk ticket columns — idempotent ALTER TABLE on the LIVE DB.

Adds the fields that power the "My Tickets" agent workbench (ITIL impact/urgency,
sub-status, the resolve workflow, time tracking, merge, last-viewed). Mirrors
add_ticket_phase2_columns.py: reads DATABASE_URL straight from .env (cwd-independent),
`ADD COLUMN IF NOT EXISTS` so it's safe to re-run. Keep all prints ASCII (the Windows
cp1252 console crashes on unicode BEFORE the SQL runs).

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/add_ticket_workbench_columns.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def database_url() -> str:
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*DATABASE_URL\s*=\s*(.+)\s*$", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    return "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


COLUMNS = [
    ("sub_status", "VARCHAR(40)"),               # finer state within a status (e.g. awaiting_approval)
    ("impact", "VARCHAR(20)"),                   # low|medium|high|critical (ITIL)
    ("urgency", "VARCHAR(20)"),                  # low|medium|high|critical (ITIL)
    ("resolution_code", "VARCHAR(40)"),          # solved|workaround|no_fault_found|duplicate|...
    ("resolution_summary", "TEXT"),
    ("resolution_category", "VARCHAR(40)"),      # hardware|software|network|user_error|vendor|configuration|other
    ("time_spent_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ("merged_into_id", "UUID"),                  # bare UUID (ORM declares the FK) to dodge create-order
    ("last_viewed_at", "TIMESTAMPTZ"),
]


def main():
    url = database_url()
    safe = re.sub(r":[^:@/]+@", ":***@", url)
    print(f"[workbench] connecting -> {safe}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    added = []
    for name, ddl in COLUMNS:
        cur.execute(f'ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS {name} {ddl};')
        added.append(name)
    # resolved_at already exists + is used for "resolved today"; index it for the aggregate.
    cur.execute('CREATE INDEX IF NOT EXISTS ix_support_tickets_resolved_at ON support_tickets (resolved_at);')
    print(f"[workbench] ensured {len(added)} columns: {', '.join(added)}")
    cur.close()
    conn.close()
    print("[workbench] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[workbench] FAILED: {e}")
        sys.exit(1)
