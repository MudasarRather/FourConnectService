"""One-off, idempotent migration — fixed-term contract end date.

create_all() can't add a column to an existing table, so this adds:
    hr_employees.contract_end_date  DATE  (nullable)

It drives the CONTRACT_EXPIRY notification scan (app/utils/hr/notification_scans.py).
Safe to re-run (uses IF NOT EXISTS). Reads .env directly so it always hits the
same DB the backend uses, regardless of cwd.

Run:
  & "<python>" C:\\Projects\\FourConnectService\\add_contract_end_date.py
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
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE hr_employees ADD COLUMN IF NOT EXISTS contract_end_date date")
    print("  hr_employees: +contract_end_date (idempotent)")
    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
