"""Fix: the partial-unique index uq_hr_exit_open_case must EXCLUDE soft-deleted rows.

The original predicate was:
    WHERE status IN ('DRAFT','SUBMITTED',...,'SETTLEMENT')
which ignores is_deleted. Once a case is soft-deleted (is_deleted=true) while
still in an open status (e.g. a deleted DRAFT), it keeps occupying the employee's
"one open case" slot forever, so creating a new case raises a raw UniqueViolation
500 — even though the app-level guard (open_case_for_employee, which filters
is_deleted=false) reports no open case. App and DB disagreed.

This recreates the index with `is_deleted = false` added to the predicate so it
matches the application's intended semantics. Idempotent: skips if already fixed.

Reads .env directly (cwd-safe). Run from anywhere:
    & "C:\\...\\python.exe" C:\\Projects\\FourConnectService\\fix_exit_open_case_index.py
"""
import os
import re
import sys

import psycopg2

INDEX_NAME = "uq_hr_exit_open_case"
NEW_DDL = """
    CREATE UNIQUE INDEX uq_hr_exit_open_case
        ON hr_exit_cases (employee_id)
        WHERE is_deleted = false
          AND status IN ('DRAFT','SUBMITTED','MANAGER_REVIEW','ACCEPTED',
                         'NOTICE_PERIOD','CLEARANCE','SETTLEMENT')
"""


def _database_url() -> str:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"
    )


def main() -> int:
    url = _database_url()
    m = re.search(r"postgresql(?:\+psycopg2)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", url)
    if not m:
        print(f"!! could not parse DATABASE_URL: {url}")
        return 1
    user, password, host, port, dbname = m.groups()
    dbname = dbname.split("?")[0]
    print(f"Connecting to {host}:{port}/{dbname} as {user} ...")

    conn = psycopg2.connect(
        dbname=dbname, user=user, password=password, host=host, port=port
    )
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s", (INDEX_NAME,))
        row = cur.fetchone()
        current = row[0] if row else None
        if current and "is_deleted" in current:
            print(f"[ok] {INDEX_NAME} already excludes soft-deleted rows - nothing to do.")
            return 0

        if current:
            print(f"[..] current def lacks is_deleted: {current}")
        cur.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
        cur.execute(NEW_DDL)
        print(f"[ok] recreated {INDEX_NAME} with is_deleted = false in the predicate.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
