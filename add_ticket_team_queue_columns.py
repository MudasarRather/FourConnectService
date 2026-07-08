"""Phase-3 — add team_id / queue_id columns to support_tickets (live DB, idempotent).

The new SdTeam/SdQueue TABLES are auto-created by create_all on boot; only these
two COLUMNS on the existing support_tickets table need an explicit ALTER. Added as
bare UUID (no DDL-level FK) to avoid table-creation ordering issues — the ORM model
still declares the ForeignKey for relationship use.

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/add_ticket_team_queue_columns.py
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
    print(f"[phase3] connecting -> {re.sub(r':[^:@/]+@', ':***@', url)}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS team_id UUID;')
    cur.execute('ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS queue_id UUID;')
    cur.execute('CREATE INDEX IF NOT EXISTS ix_support_tickets_team ON support_tickets (team_id);')
    cur.execute('CREATE INDEX IF NOT EXISTS ix_support_tickets_queue ON support_tickets (queue_id);')
    print("[phase3] ensured columns: team_id, queue_id")
    cur.close()
    conn.close()
    print("[phase3] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[phase3] FAILED: {e}")
        sys.exit(1)
