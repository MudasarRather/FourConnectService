"""Ad-hoc migration: add hr_performance_pips.employee_ack_at (TIMESTAMPTZ).

`Base.metadata.create_all()` only creates new TABLES, never new columns on an
existing table — so a new column needs an explicit ALTER. Idempotent
(ADD COLUMN IF NOT EXISTS), safe to re-run.

Backs the employee self-service PIP acknowledgement (POST /hr/me/performance/pips/{id}/acknowledge).

Reads DATABASE_URL straight from .env (relative-cwd resolution of get_settings()
is unreliable for ad-hoc scripts — see CLAUDE.md), falling back to the local dev DB.

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_pip_employee_ack.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
FALLBACK = "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


def database_url() -> str:
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return FALLBACK


def main() -> int:
    url = database_url()
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", url)
    if not m:
        print(f"[FAIL] Could not parse DATABASE_URL: {url!r}")
        return 1
    user, pwd, host, port, db = m.groups()
    db = db.split("?")[0]
    print(f"[..] Connecting to {host}:{port}/{db} as {user}")
    conn = psycopg2.connect(host=host, port=port, user=user, password=pwd, dbname=db)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE hr_performance_pips "
                "ADD COLUMN IF NOT EXISTS employee_ack_at TIMESTAMPTZ"
            )
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'hr_performance_pips' AND column_name = 'employee_ack_at'"
            )
            row = cur.fetchone()
        if row:
            print(f"[OK] hr_performance_pips.employee_ack_at present ({row[1]})")
            return 0
        print("[FAIL] Column not found after ALTER")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
