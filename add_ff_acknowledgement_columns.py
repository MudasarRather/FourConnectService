"""Additive migration: F&F acknowledgement columns on hr_exit_settlements.

The HR clearance gate "Full & Final acknowledged" now records the acknowledgement
on the authoritative settlement record instead of a bare clearance checkbox:

    ff_statement_shared_at   timestamptz   when the F&F statement was shared
    ff_acknowledged_at       timestamptz   when the employee acknowledged amounts
    ff_acknowledged_by_id    uuid          HR actor who recorded the ack
    payout_confirmed_at      timestamptz   when the payout schedule was confirmed
    ff_ack_snapshot          jsonb         reproducible checklist + actor

create_all() never adds columns to existing tables, so this one-off ALTER brings
the live DB in line with the model. Reads DATABASE_URL straight from .env
(cwd-independent — see CLAUDE.md) and is idempotent (ADD COLUMN IF NOT EXISTS).

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_ff_acknowledgement_columns.py
"""
import os
import re

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def db_url() -> str:
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db")


COLUMNS = [
    ("ff_statement_shared_at", "timestamptz"),
    ("ff_acknowledged_at", "timestamptz"),
    ("ff_acknowledged_by_id", "uuid"),
    ("payout_confirmed_at", "timestamptz"),
    ("ff_ack_snapshot", "jsonb"),
]
TABLE = "hr_exit_settlements"


def main() -> int:
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url())
    if not m:
        print("Could not parse DATABASE_URL")
        return 1
    user, pwd, host, port, name = m.groups()
    name = name.split("?")[0]
    print(f"Connecting to {host}:{port}/{name} as {user} ...")

    conn = psycopg2.connect(host=host, port=port, user=user, password=pwd, dbname=name)
    conn.autocommit = True
    cur = conn.cursor()

    for col, ddl in COLUMNS:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            (TABLE, col),
        )
        if cur.fetchone():
            print(f"[skip] {TABLE}.{col} already exists.")
            continue
        cur.execute(f'ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "{col}" {ddl}')
        print(f"[OK]   Added {TABLE}.{col} ({ddl}).")

    # FK for the actor column (best-effort — ignore if it already exists).
    try:
        cur.execute(
            """SELECT 1 FROM information_schema.table_constraints
               WHERE table_name = %s AND constraint_name = 'hr_exit_settlements_ff_ack_by_fkey'""",
            (TABLE,),
        )
        if not cur.fetchone():
            cur.execute(
                f'ALTER TABLE {TABLE} ADD CONSTRAINT hr_exit_settlements_ff_ack_by_fkey '
                f'FOREIGN KEY (ff_acknowledged_by_id) REFERENCES users(id)'
            )
            print(f"[OK]   Added FK {TABLE}.ff_acknowledged_by_id -> users.id.")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] FK creation skipped: {e}")

    cur.close()
    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
