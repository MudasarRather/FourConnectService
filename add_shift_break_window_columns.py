"""Idempotent patch: add break_windows + late-punch + break-overrun columns
to the live hr_shifts table.

Run once after deploying the model change. Safe to re-run.

Usage (from any cwd — reads .env from the backend root directly):
    python C:\\Projects\\FourConnectService\\add_shift_break_window_columns.py
"""
import os
import re
import sys
import psycopg2

BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BACKEND_ROOT, ".env")


def read_db_url():
    """Read DATABASE_URL straight out of .env so cwd doesn't matter."""
    if not os.path.exists(ENV_PATH):
        sys.exit(f".env not found at {ENV_PATH}")
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*DATABASE_URL\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1)
    sys.exit("DATABASE_URL not set in .env")


def parse_pg_url(url):
    m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", url)
    if not m:
        sys.exit(f"Could not parse DATABASE_URL: {url}")
    return dict(user=m.group(1), password=m.group(2),
                host=m.group(3), port=int(m.group(4)), dbname=m.group(5))


COLUMNS = [
    ("break_windows", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("late_punch_requires_approval", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("late_self_punch_threshold_minutes", "INTEGER NOT NULL DEFAULT 15"),
    ("break_overrun_alert_minutes", "INTEGER NOT NULL DEFAULT 10"),
]


def main():
    creds = parse_pg_url(read_db_url())
    print(f"Connecting to {creds['host']}:{creds['port']}/{creds['dbname']} ...")
    conn = psycopg2.connect(**creds, connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'hr_shifts'"
    )
    existing = {row[0] for row in cur.fetchall()}
    if not existing:
        sys.exit("hr_shifts table does not exist — run the app once first to auto-create it.")

    added = []
    skipped = []
    for col, ddl in COLUMNS:
        if col in existing:
            skipped.append(col)
            continue
        sql = f'ALTER TABLE hr_shifts ADD COLUMN {col} {ddl}'
        print(f"  + {col}")
        cur.execute(sql)
        added.append(col)

    cur.close()
    conn.close()

    print()
    print(f"Done. Added {len(added)} column(s): {added}")
    print(f"Skipped {len(skipped)} (already present): {skipped}")


if __name__ == "__main__":
    main()
