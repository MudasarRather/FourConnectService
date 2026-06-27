"""Idempotent seed for HR Settings master tables (Phase B).

Seeds the ``is_system`` rows whose ``code`` mirrors the live enum values, so the
configurable masters resolve existing Employee / ExitCase rows out of the box.
Run AFTER the backend has booted once (so create_all() has made the tables).

Safe to re-run — every INSERT is guarded by NOT EXISTS. Reads .env directly.

Run:
  & "<python>" C:\\Projects\\FourConnectService\\seed_settings_masters.py
"""
import os
import re
import sys
import uuid

import platform
_ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
_ur.__dict__["processor"] = "Intel"
platform._uname_cache = _ur
platform._Processor.get = staticmethod(lambda: "Intel")

import psycopg2

_DEFAULT_DB = "postgresql://postgres:acer2gb@127.0.0.1:5432/fourreck_db"

EMPLOYMENT_TYPES = [
    ("FULL_TIME", "Full Time"), ("CONTRACT", "Contract"), ("CONSULTANT", "Consultant"),
    ("INTERN", "Intern"), ("PART_TIME", "Part Time"),
]
EMPLOYEE_CATEGORIES = [
    ("PERMANENT", "Permanent"), ("PROBATIONARY", "Probationary"),
    ("CONTRACT", "Contract"), ("TRAINEE", "Trainee"),
]
# (code, label, category, is_voluntary)
SEPARATION_REASONS = [
    ("VOLUNTARY", "Voluntary Resignation", "RESIGNATION_TYPE", True),
    ("RETIREMENT", "Retirement", "RESIGNATION_TYPE", True),
    ("CONTRACT_COMPLETION", "Contract Completion", "RESIGNATION_TYPE", None),
    ("PROBATION_EXIT", "Probation Exit", "RESIGNATION_TYPE", None),
    ("MUTUAL_SEPARATION", "Mutual Separation", "RESIGNATION_TYPE", None),
    ("TERMINATION", "Termination", "RESIGNATION_TYPE", False),
    ("TRANSFER", "Transfer", "RESIGNATION_TYPE", None),
    ("BETTER_OPPORTUNITY", "Better Opportunity", "EXIT_REASON", None),
    ("COMPENSATION", "Compensation", "EXIT_REASON", None),
    ("RELOCATION", "Relocation", "EXIT_REASON", None),
    ("HIGHER_STUDIES", "Higher Studies", "EXIT_REASON", None),
    ("HEALTH", "Health", "EXIT_REASON", None),
    ("PERSONAL", "Personal", "EXIT_REASON", None),
    ("WORK_ENVIRONMENT", "Work Environment", "EXIT_REASON", None),
    ("CAREER_GROWTH", "Career Growth", "EXIT_REASON", None),
    ("RETIREMENT", "Retirement", "EXIT_REASON", None),
    ("PERFORMANCE", "Performance", "EXIT_REASON", None),
    ("MISCONDUCT", "Misconduct", "EXIT_REASON", None),
    ("REDUNDANCY", "Redundancy", "EXIT_REASON", None),
    ("CONTRACT_END", "Contract End", "EXIT_REASON", None),
    ("OTHER", "Other", "EXIT_REASON", None),
]


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

    def seed_simple(table, rows):
        n = 0
        for i, (code, label) in enumerate(rows):
            cur.execute(
                f"""INSERT INTO {table} (id, code, label, is_system, sort_order, is_active, is_deleted)
                    SELECT %s, %s, %s, true, %s, true, false
                    WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE code = %s)""",
                (str(uuid.uuid4()), code, label, i, code),
            )
            n += cur.rowcount
        print(f"  {table}: +{n} new (of {len(rows)})")

    seed_simple("hr_employment_type_master", EMPLOYMENT_TYPES)
    seed_simple("hr_employee_category_master", EMPLOYEE_CATEGORIES)

    n = 0
    for i, (code, label, category, vol) in enumerate(SEPARATION_REASONS):
        cur.execute(
            """INSERT INTO hr_separation_reason_master
                 (id, code, label, category, is_voluntary, is_system, sort_order, is_active, is_deleted)
               SELECT %s, %s, %s, %s, %s, true, %s, true, false
               WHERE NOT EXISTS (
                 SELECT 1 FROM hr_separation_reason_master WHERE code = %s AND category = %s)""",
            (str(uuid.uuid4()), code, label, category, vol, i, code, category),
        )
        n += cur.rowcount
    print(f"  hr_separation_reason_master: +{n} new (of {len(SEPARATION_REASONS)})")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
