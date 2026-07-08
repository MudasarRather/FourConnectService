"""Support Desk — add request_types to categories + subcategory_id to tickets (live DB).

Enables the ServiceNow/Zendesk request_type → category → subcategory cascade:
  • support_categories.request_types — which request types a top-level category applies to
  • support_tickets.subcategory_id   — the chosen leaf subcategory
create_all never ALTERs existing tables. Idempotent — safe to re-run.

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/add_support_taxonomy_columns.py
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


DDL = [
    "ALTER TABLE support_categories ADD COLUMN IF NOT EXISTS request_types JSONB NOT NULL DEFAULT '[]'::jsonb;",
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS subcategory_id UUID;",
    "CREATE INDEX IF NOT EXISTS ix_support_tickets_subcategory ON support_tickets (subcategory_id);",
]


def main():
    url = database_url()
    print(f"[taxonomy] connecting -> {re.sub(r':[^:@/]+@', ':***@', url)}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    for stmt in DDL:
        cur.execute(stmt)
    print("[taxonomy] ensured: support_categories.request_types, support_tickets.subcategory_id")
    cur.close()
    conn.close()
    print("[taxonomy] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[taxonomy] FAILED: {e}")
        sys.exit(1)
