"""Idempotent migration — adds `default_pay_level` (VARCHAR(20)) to `hr_grades`.

A grade's default pay level pre-fills an employee's pay_level when the grade is
selected (promote / add-employee / profile edit). Re-runs are safe: the ALTER is
gated on an information_schema check.

Connects via psycopg2 directly (parsing .env) to avoid importing SQLAlchemy —
which on this box can hang at import via platform.uname() -> WMI. It also reads
.env itself so it always targets the SAME (remote) DB the backend uses,
regardless of cwd.

Run:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_grade_pay_level.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")


def _database_url() -> str:
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("DATABASE_URL", "")


def main() -> None:
    url = _database_url()
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", url)
    if not m:
        print(f"Could not parse DATABASE_URL: {url!r}")
        sys.exit(1)
    user, pwd, host, port, name = m.groups()
    name = name.split("?")[0]
    print(f"Connecting to {host}:{port}/{name} as {user} ...")

    conn = psycopg2.connect(dbname=name, user=user, password=pwd, host=host, port=port)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'hr_grades' AND column_name = 'default_pay_level'
        """
    )
    if cur.fetchone():
        print("  = hr_grades.default_pay_level already exists — nothing to do")
    else:
        cur.execute("ALTER TABLE hr_grades ADD COLUMN default_pay_level VARCHAR(20)")
        print("  + hr_grades.default_pay_level (VARCHAR(20)) added")
    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
