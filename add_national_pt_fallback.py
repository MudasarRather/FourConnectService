"""Idempotent backfill: insert a NATIONAL Professional-Tax fallback row (no PT)
for every fiscal year that already has PT config but no national PT_SLABS row.

Why: PT is a STATE levy. The payroll engine now resolves PT per employee from
their work-location state and falls back to the national PT_SLABS (= no PT) for
states with no configured slab. Seeds.py adds this national row for FRESH fiscal
years, but existing years were seeded before this change (the seeder early-returns
once a year's config exists), so they need this one-off backfill.

Safe to re-run: skips any fiscal year that already has a national PT_SLABS row.
Reads DATABASE_URL straight from .env (do NOT trust get_settings() cwd — see
CLAUDE.md). Run from the backend root:

    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_national_pt_fallback.py
"""
import os
import re
import uuid
from datetime import date

import psycopg2
import psycopg2.extras

_PT_NATIONAL = [{"upto": None, "amount": 0}]


def _database_url() -> str:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    with open(env_path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"\s*DATABASE_URL\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1)
    raise RuntimeError("DATABASE_URL not found in .env")


def _fy_start_year(fy: str) -> int:
    return int(fy.split("-")[0])


def main() -> None:
    conn = psycopg2.connect(_database_url())
    psycopg2.extras.register_uuid()
    cur = conn.cursor()

    # Fiscal years that already have ANY PT_SLABS row (state or national).
    cur.execute("SELECT DISTINCT fiscal_year FROM hr_statutory_config WHERE key = 'PT_SLABS'")
    fys = sorted(r[0] for r in cur.fetchall())
    if not fys:
        print("No PT_SLABS rows found — nothing to backfill.")
        return

    inserted = 0
    for fy in fys:
        cur.execute(
            "SELECT 1 FROM hr_statutory_config "
            "WHERE fiscal_year = %s AND key = 'PT_SLABS' AND state_code IS NULL",
            (fy,),
        )
        if cur.fetchone():
            print(f"  {fy}: national PT_SLABS already present — skip")
            continue
        cur.execute(
            "INSERT INTO hr_statutory_config "
            "(id, fiscal_year, state_code, key, value_json, effective_from, is_active, description) "
            "VALUES (%s, %s, NULL, 'PT_SLABS', %s, %s, true, %s)",
            (
                uuid.uuid4(),
                fy,
                psycopg2.extras.Json(_PT_NATIONAL),
                date(_fy_start_year(fy), 4, 1),
                "National fallback — no PT (state rows override)",
            ),
        )
        inserted += 1
        print(f"  {fy}: inserted national PT_SLABS (no PT)")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nDone. Inserted {inserted} national PT_SLABS row(s).")


if __name__ == "__main__":
    main()
