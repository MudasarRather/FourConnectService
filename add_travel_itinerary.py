"""Additive migration: add hr_travel_requests.trip_type + .itinerary (multi-city).

A trip can now be ONE_WAY, ROUND_TRIP (default, = today) or MULTI_CITY. For a
multi-city trip the per-leg list lives in ``itinerary`` (JSONB) and the single
from/to + departure/return columns are the DERIVED envelope. create_all() never
adds columns to existing tables, so this one-off ALTER aligns the live DB.

Idempotent: checks information_schema first, ADD COLUMN IF NOT EXISTS, backfills
existing rows to ROUND_TRIP. Reads DATABASE_URL straight from .env (cwd-safe).

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_travel_itinerary.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def db_url() -> str:
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db")


def has_col(cur, table, col) -> bool:
    cur.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = %s AND column_name = %s""",
        (table, col),
    )
    return cur.fetchone() is not None


def main() -> int:
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url())
    if not m:
        print("Could not parse DATABASE_URL")
        return 1
    user, pwd, host, port, name = m.groups()
    name = name.split("?")[0]
    print(f"Connecting to {host}:{port}/{name} as {user} ...")

    conn = psycopg2.connect(host=host, port=port, user=user, password=pwd, dbname=name)
    conn.autocommit = True
    cur = conn.cursor()

    if has_col(cur, "hr_travel_requests", "trip_type"):
        print("[OK] hr_travel_requests.trip_type already exists.")
    else:
        cur.execute("ALTER TABLE hr_travel_requests ADD COLUMN IF NOT EXISTS trip_type varchar(20)")
        cur.execute("UPDATE hr_travel_requests SET trip_type = 'ROUND_TRIP' WHERE trip_type IS NULL")
        cur.execute("ALTER TABLE hr_travel_requests ALTER COLUMN trip_type SET DEFAULT 'ROUND_TRIP'")
        cur.execute("ALTER TABLE hr_travel_requests ALTER COLUMN trip_type SET NOT NULL")
        print("[OK] Added hr_travel_requests.trip_type (varchar(20), default ROUND_TRIP).")

    if has_col(cur, "hr_travel_requests", "itinerary"):
        print("[OK] hr_travel_requests.itinerary already exists.")
    else:
        cur.execute("ALTER TABLE hr_travel_requests ADD COLUMN IF NOT EXISTS itinerary jsonb")
        print("[OK] Added hr_travel_requests.itinerary (jsonb, nullable).")

    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
