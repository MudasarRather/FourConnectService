"""One-off, idempotent migration for the Rehire workflow.

create_all() can neither add columns to an existing table nor add a value to an
existing Postgres enum, so this does both directly:

1. ALTER TYPE hr_employee_change_type ADD VALUE 'REHIRED'  (the new history type)
2. hr_employees.original_joining_date  DATE          (first-ever join, service history)
3. hr_employees.rehire_count           INT NOT NULL DEFAULT 0

Safe to re-run (uses IF NOT EXISTS). Reads .env directly so it always hits the
same DB the backend uses, regardless of cwd.

Run:
  & "<python>" C:\\Projects\\FourConnectService\\add_rehire_support.py
"""
import os
import re
import sys

import platform
_ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
_ur.__dict__["processor"] = "Intel"
platform._uname_cache = _ur
platform._Processor.get = staticmethod(lambda: "Intel")

import psycopg2

_DEFAULT_DB = "postgresql://postgres:acer2gb@127.0.0.1:5432/fourreck_db"


def _db_url() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path, "r", encoding="utf-8"):
            s = line.strip()
            if s.startswith("DATABASE_URL") and "=" in s:
                return s.partition("=")[2].strip().strip('"').strip("'")
    return _DEFAULT_DB


def main():
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", _db_url())
    if not m:
        print("Could not parse DATABASE_URL"); sys.exit(1)
    user, pwd, host, port, dbname = m.groups()
    print(f"DB: {host}:{port}/{dbname} (user={user})")
    conn = psycopg2.connect(dbname=dbname, user=user, password=pwd, host=host, port=port)
    conn.autocommit = True  # ALTER TYPE ADD VALUE can't run inside a txn block
    cur = conn.cursor()

    # 1) new enum value
    cur.execute("ALTER TYPE hr_employee_change_type ADD VALUE IF NOT EXISTS 'REHIRED'")
    print("  enum hr_employee_change_type: +REHIRED (idempotent)")

    # 2 + 3) new columns
    cur.execute("ALTER TABLE hr_employees ADD COLUMN IF NOT EXISTS original_joining_date date")
    cur.execute("ALTER TABLE hr_employees ADD COLUMN IF NOT EXISTS rehire_count integer NOT NULL DEFAULT 0")
    print("  hr_employees: +original_joining_date, +rehire_count (idempotent)")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
