"""Phase-2 migration for the Leave & Absence module.

Idempotent. Steps:
  1. ALTER TYPE hr_attendance_log_action ADD VALUE — new COMP_OFF_* and
     ENCASHMENT_* members.
  2. ALTER TYPE hr_leave_ledger_kind ADD VALUE — COMP_OFF_EARNED, COMP_OFF_USED,
     COMP_OFF_EXPIRED.
  3. ALTER TABLE hr_leave_balance_history — add is_auto_generated, earned_on,
     expires_on, related_encashment_id (all nullable / safe defaults).
  4. Table hr_leave_encashments auto-creates via Base.metadata.create_all on
     next backend startup — script does NOT create it.
  5. Seed system_settings keys: comp_off_expiry_days=90, leave_encash_counter=0.

Usage (any cwd — reads .env directly from backend root):
    python C:\\Projects\\FourConnectService\\add_leave_phase2.py

Re-run is safe.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg2


BACKEND_ROOT = Path(__file__).parent
ENV_PATH = BACKEND_ROOT / ".env"


def read_db_url() -> str:
    if not ENV_PATH.exists():
        sys.exit(f".env not found at {ENV_PATH}")
    txt = ENV_PATH.read_text(encoding="utf-8")
    m = re.search(r"^\s*DATABASE_URL\s*=\s*(.+?)\s*$", txt, re.MULTILINE)
    if not m:
        sys.exit("DATABASE_URL not set in .env")
    return m.group(1).strip()


def parse_pg_url(url: str) -> dict:
    m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", url)
    if not m:
        sys.exit(f"Could not parse DATABASE_URL: {url}")
    return dict(user=m.group(1), password=m.group(2),
                host=m.group(3), port=int(m.group(4)), dbname=m.group(5))


LOG_ACTION_VALUES = [
    "COMP_OFF_EARNED", "COMP_OFF_GRANTED", "COMP_OFF_USED", "COMP_OFF_EXPIRED",
    "ENCASHMENT_REQUESTED", "ENCASHMENT_APPROVED", "ENCASHMENT_REJECTED",
    "ENCASHMENT_PAID", "ENCASHMENT_CANCELLED",
]

LEDGER_KIND_VALUES = [
    "COMP_OFF_EARNED", "COMP_OFF_USED", "COMP_OFF_EXPIRED",
]

NEW_HISTORY_COLUMNS = [
    ("is_auto_generated",     "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("earned_on",             "DATE NULL"),
    ("expires_on",            "DATE NULL"),
    ("related_encashment_id", "UUID NULL"),
]

NEW_SETTINGS = [
    ("comp_off_expiry_days", "90", "Days before an auto-credited COMP_OFF expires"),
    ("leave_encash_counter", "0",  "Counter for LeaveEncashment.reference_no (EN-YY-NNNNNN)"),
]


def main():
    creds = parse_pg_url(read_db_url())
    print(f"Connecting to {creds['host']}:{creds['port']}/{creds['dbname']} ...")
    conn = psycopg2.connect(**creds, connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()

    print("\n[1/5] Adding hr_attendance_log_action enum values ...")
    added = 0
    for v in LOG_ACTION_VALUES:
        try:
            cur.execute(f"ALTER TYPE hr_attendance_log_action ADD VALUE IF NOT EXISTS '{v}'")
            added += 1
        except psycopg2.errors.DuplicateObject:
            pass
    print(f"   processed {added} value(s) (idempotent)")

    print("\n[2/5] Adding hr_leave_ledger_kind enum values ...")
    added = 0
    for v in LEDGER_KIND_VALUES:
        try:
            cur.execute(f"ALTER TYPE hr_leave_ledger_kind ADD VALUE IF NOT EXISTS '{v}'")
            added += 1
        except psycopg2.errors.DuplicateObject:
            pass
    print(f"   processed {added} value(s) (idempotent)")

    print("\n[3/5] Adding columns to hr_leave_balance_history ...")
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'hr_leave_balance_history'
    """)
    existing_cols = {row[0] for row in cur.fetchall()}
    if not existing_cols:
        print("   hr_leave_balance_history table not yet created — boot the backend once, then re-run")
    else:
        added = 0
        for col, ddl in NEW_HISTORY_COLUMNS:
            if col in existing_cols:
                continue
            cur.execute(f"ALTER TABLE hr_leave_balance_history ADD COLUMN {col} {ddl}")
            added += 1
        print(f"   added {added} column(s)")
        # Add index on earned_on if absent
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS ix_hr_ledger_earned_on ON hr_leave_balance_history (earned_on)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_hr_ledger_expires_on ON hr_leave_balance_history (expires_on)")
        except Exception:
            pass

    print("\n[4/5] hr_leave_encashments table ...")
    cur.execute("""
        SELECT 1 FROM information_schema.tables WHERE table_name = 'hr_leave_encashments'
    """)
    if cur.fetchone():
        print("   table already present — created by Base.metadata.create_all")
    else:
        print("   table NOT yet created — restart the backend so SQLAlchemy creates it, then re-run this script")

    print("\n[5/5] Seeding system_settings ...")
    seeded = 0
    for key, value, desc in NEW_SETTINGS:
        cur.execute("SELECT value FROM system_settings WHERE key = %s", (key,))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO system_settings (key, value, description, updated_at) VALUES (%s, %s, %s, NOW())",
            (key, value, desc),
        )
        seeded += 1
    print(f"   inserted {seeded} setting(s)")

    cur.close(); conn.close()
    print("\nDone. Phase-2 leave migration complete.")


if __name__ == "__main__":
    main()
