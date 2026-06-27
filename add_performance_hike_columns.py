"""Ad-hoc migration: add merit/hike outcome columns to hr_performance_reviews.

`Base.metadata.create_all()` only creates new TABLES, never new columns on an
existing table — so the Appraisal→Payroll hike fields need an explicit ALTER.
Idempotent (ADD COLUMN IF NOT EXISTS), safe to re-run.

Reads DATABASE_URL straight from .env (relative-cwd resolution of get_settings()
is unreliable for ad-hoc scripts — see CLAUDE.md), falling back to the local dev DB.

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_performance_hike_columns.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
FALLBACK = "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"

TABLE = "hr_performance_reviews"

# column_name -> SQL type
COLUMNS = {
    "merit_policy_id": "UUID",
    "hike_effective_from": "DATE",
    "final_rating_band": "VARCHAR(40)",
    "recommended_hike_pct": "NUMERIC(5,2)",
    "recommendation_note": "TEXT",
    "recommended_by_id": "UUID",
    "recommended_at": "TIMESTAMPTZ",
    "approved_hike_pct": "NUMERIC(5,2)",
    "approved_by_id": "UUID",
    "approved_at": "TIMESTAMPTZ",
    "hike_status": "VARCHAR(16) NOT NULL DEFAULT 'NONE'",
    "comp_revision_id": "UUID",
    "prev_annual_ctc": "NUMERIC(14,2)",
    "new_annual_ctc": "NUMERIC(14,2)",
}


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
            for col, sqltype in COLUMNS.items():
                cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {col} {sqltype}")
                print(f"[OK] {TABLE}.{col} ensured")
            # Indexes mirroring the model (index=True on merit_policy_id, hike_status)
            cur.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_merit_policy_id ON {TABLE} (merit_policy_id)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_hike_status ON {TABLE} (hike_status)")
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = ANY(%s)",
                (TABLE, list(COLUMNS.keys())),
            )
            present = {r[0] for r in cur.fetchall()}
        missing = set(COLUMNS) - present
        if missing:
            print(f"[FAIL] Columns missing after ALTER: {sorted(missing)}")
            return 1
        print(f"[DONE] All {len(COLUMNS)} hike columns present on {TABLE}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
