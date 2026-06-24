"""Idempotent: add the `details` column to hr_exit_interviews.

create_all() never adds columns to an existing table, so this ALTERs in the
new ExitInterview.details (HR's appointment instructions shown to the employee:
meeting link / room / agenda). Safe to re-run.

Reads .env directly (cwd-safe). Run from anywhere:
    & "C:\\...\\python.exe" C:\\Projects\\FourConnectService\\add_exit_interview_details.py
"""
import os
import re
import sys

import psycopg2

TABLE = "hr_exit_interviews"
COLUMN = "details"


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

    conn = psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            (TABLE, COLUMN),
        )
        if cur.fetchone():
            print(f"[ok] {TABLE}.{COLUMN} already exists - nothing to do.")
            return 0
        cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {COLUMN} TEXT")
        print(f"[ok] added {TABLE}.{COLUMN}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
