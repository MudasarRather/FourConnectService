"""One-off, idempotent migration for HR Settings — Phase B master enrichments.

create_all() can't add columns to existing tables, so this adds the new nullable
columns directly. All ADD COLUMN IF NOT EXISTS → safe to re-run, no row rewrite,
no FK constraints (kept plain to make the ALTER trivial; the ORM reads/writes the
columns either way).

  hr_departments.cost_center                 varchar(40)
  hr_designations.reporting_to_designation_id uuid
  hr_designations.approval_authority         jsonb
  hr_grades.eligibility                      jsonb
  hr_work_locations.code                     varchar(20)
  hr_work_locations.timezone                 varchar(40)  DEFAULT 'Asia/Kolkata'
  hr_work_locations.weekly_off_pattern       jsonb

Reads .env directly so it always hits the same DB the backend uses.

Run:
  & "<python>" C:\\Projects\\FourConnectService\\add_settings_master_fields.py
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


STATEMENTS = [
    "ALTER TABLE hr_departments    ADD COLUMN IF NOT EXISTS cost_center varchar(40)",
    "ALTER TABLE hr_designations   ADD COLUMN IF NOT EXISTS reporting_to_designation_id uuid",
    "ALTER TABLE hr_designations   ADD COLUMN IF NOT EXISTS approval_authority jsonb",
    "ALTER TABLE hr_grades         ADD COLUMN IF NOT EXISTS eligibility jsonb",
    "ALTER TABLE hr_work_locations ADD COLUMN IF NOT EXISTS code varchar(20)",
    "ALTER TABLE hr_work_locations ADD COLUMN IF NOT EXISTS timezone varchar(40) DEFAULT 'Asia/Kolkata'",
    "ALTER TABLE hr_work_locations ADD COLUMN IF NOT EXISTS weekly_off_pattern jsonb",
]


def main():
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", _db_url())
    if not m:
        print("Could not parse DATABASE_URL"); sys.exit(1)
    user, pwd, host, port, dbname = m.groups()
    print(f"DB: {host}:{port}/{dbname} (user={user})")
    conn = psycopg2.connect(dbname=dbname, user=user, password=pwd, host=host, port=port)
    conn.autocommit = True
    cur = conn.cursor()
    for sql in STATEMENTS:
        cur.execute(sql)
        print(f"  ok: {sql.split('ADD COLUMN IF NOT EXISTS')[1].strip()}")
    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
