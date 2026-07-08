"""Support Desk — add the `collaborators` column to support_tickets (live DB, idempotent).

Lets more than one person work a single ticket: beyond the single `assigned_agent_id`,
`collaborators` is a JSONB array of user ids who can see + work the ticket and have it
surface under their own "My Tickets". create_all never ALTERs existing tables.

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/add_ticket_collaborators_column.py
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


def main():
    url = database_url()
    print(f"[collaborators] connecting -> {re.sub(r':[^:@/]+@', ':***@', url)}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS collaborators JSONB NOT NULL DEFAULT '[]'::jsonb;")
    print("[collaborators] ensured column: support_tickets.collaborators")
    cur.close()
    conn.close()
    print("[collaborators] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[collaborators] FAILED: {e}")
        sys.exit(1)
