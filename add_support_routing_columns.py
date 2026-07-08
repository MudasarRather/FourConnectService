"""Phase 1-2 (assignment) — add routing/auto-assign columns to support_queues.

Adds the columns the auto-assignment engine needs (the SdQueue table already
exists, so create_all won't alter it). Idempotent; reads .env directly; ASCII-only
prints (the Windows cp1252 console crashes on unicode BEFORE the SQL runs).

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/add_support_routing_columns.py
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
    print(f"[routing] connecting -> {re.sub(r':[^:@/]+@', ':***@', url)}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    # assignment_method: manual | round_robin | load_balanced
    cur.execute("ALTER TABLE support_queues ADD COLUMN IF NOT EXISTS assignment_method VARCHAR(20) DEFAULT 'round_robin';")
    # categories this queue routes (JSONB array of category uuids as strings)
    cur.execute("ALTER TABLE support_queues ADD COLUMN IF NOT EXISTS category_ids JSONB DEFAULT '[]'::jsonb;")
    # round-robin cursor: the user who got the last auto-assigned ticket from this queue
    cur.execute("ALTER TABLE support_queues ADD COLUMN IF NOT EXISTS rr_last_user_id UUID;")
    print("[routing] ensured columns on support_queues: assignment_method, category_ids, rr_last_user_id")
    cur.close()
    conn.close()
    print("[routing] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[routing] FAILED: {e}")
        sys.exit(1)
