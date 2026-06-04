"""Schema patch: add `source` + `source_ref` columns to `hr_holidays` and
backfill provenance for rows that came from the India bulk-import endpoint.

Idempotent — re-running is safe. Run with the project venv:
    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" `
      C:/Projects/FourConnectService/add_holiday_source.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras


HERE = Path(__file__).resolve().parent
ENV = (HERE / ".env").read_text()
m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", ENV)
if not m:
    print("FAIL: could not parse DATABASE_URL from .env")
    sys.exit(1)
user, pw, host, port, db = m.groups()
db = db.strip()

print(f"Target: host={host} port={port} db={db} user={user}")
conn = psycopg2.connect(host=host, port=port, dbname=db, user=user, password=pw)
psycopg2.extras.register_uuid()
conn.autocommit = False
cur = conn.cursor()


def column_exists(table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, column),
    )
    return cur.fetchone() is not None


# ────────────────────────────────────────────────────────────────────────
# 1) Add the columns if missing
# ────────────────────────────────────────────────────────────────────────
if not column_exists("hr_holidays", "source"):
    print("ALTER TABLE hr_holidays ADD COLUMN source")
    cur.execute(
        "ALTER TABLE hr_holidays ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'manual'"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS ix_hr_holidays_source ON hr_holidays(source)")
else:
    print("hr_holidays.source already present — skipping ALTER")

if not column_exists("hr_holidays", "source_ref"):
    print("ALTER TABLE hr_holidays ADD COLUMN source_ref")
    cur.execute("ALTER TABLE hr_holidays ADD COLUMN source_ref VARCHAR(120) NULL")
else:
    print("hr_holidays.source_ref already present — skipping ALTER")

conn.commit()

# ────────────────────────────────────────────────────────────────────────
# 2) Backfill provenance for rows that almost-certainly came from the
#    India bulk importer.
#
#    Strategy: an admin would not manually type 28 Indian national
#    holidays into the UI. Any row whose holiday_type='NATIONAL' AND
#    name matches a known _INDIA_HOLIDAYS entry is flagged source='import:in'
#    + source_ref='IN:<year>'. Everything else stays 'manual'.
# ────────────────────────────────────────────────────────────────────────
INDIA_HOLIDAY_NAMES = {
    "republic day", "holi", "good friday", "eid al-fitr", "buddha purnima",
    "eid al-adha", "muharram", "independence day", "raksha bandhan",
    "janmashtami", "milad-un-nabi", "gandhi jayanti", "dussehra", "diwali",
    "govardhan puja", "bhai dooj", "guru nanak jayanti", "christmas",
    "new year", "republic day eve", "labour day", "mahavir jayanti",
    "easter", "ramadan", "navratri", "ganesh chaturthi", "onam", "pongal",
    "makar sankranti", "vasant panchami", "mahashivratri",
}

cur.execute(
    """
    SELECT id, name, date, created_at
    FROM hr_holidays
    WHERE holiday_type = 'NATIONAL' AND source = 'manual'
    """
)
candidates = cur.fetchall()
print(f"\nNATIONAL rows still tagged 'manual': {len(candidates)}")

updated = 0
for hid, name, hdate, _created_at in candidates:
    norm = (name or "").strip().lower()
    if any(known in norm for known in INDIA_HOLIDAY_NAMES):
        year = hdate.year if hasattr(hdate, "year") else 0
        cur.execute(
            "UPDATE hr_holidays SET source=%s, source_ref=%s WHERE id=%s",
            ("import:in", f"IN:{year}", hid),
        )
        updated += 1

print(f"Backfilled {updated} NATIONAL row(s) as source='import:in'.")

conn.commit()

# ────────────────────────────────────────────────────────────────────────
# 3) Show the resulting Aug 25, 2026 row(s) so the admin can verify
# ────────────────────────────────────────────────────────────────────────
cur.execute(
    """
    SELECT id, name, date, holiday_type, is_active, is_deleted, source, source_ref, created_at
    FROM hr_holidays
    WHERE date = %s
    """,
    ("2026-08-25",),
)
print("\nAug 25, 2026 row(s):")
for row in cur.fetchall():
    print(" ", row)

cur.close()
conn.close()
print("\nDone.")
