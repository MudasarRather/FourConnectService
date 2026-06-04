"""Idempotent migration for the Leave & Absence module.

Steps (each guarded against re-run):
  1. ALTER TYPE hr_attendance_log_action ADD VALUE — for each new LEAVE_* member.
  2. ALTER TABLE hr_attendance ADD COLUMN IF NOT EXISTS leave_request_id UUID.
  3. Seed hr_leave_policies with 10 default rows (one per LeaveType) if missing.
  4. Seed system_settings rows: fiscal_year_start, leave_encashment_formula,
     leave_ref_counter.

Tables themselves (hr_leave_requests, hr_leave_balances, hr_leave_balance_history,
hr_leave_policies) auto-create via SQLAlchemy `Base.metadata.create_all` on the
next backend startup — this script does NOT create them.

Usage (any cwd — reads .env directly from the backend root):
    python C:\\Projects\\FourConnectService\\add_leave_module_tables.py
"""
from __future__ import annotations

import os
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


# ─── 1. Enum values to add to hr_attendance_log_action ──────────────────────
NEW_ENUM_VALUES = [
    "LEAVE_REQUESTED",
    "LEAVE_MANAGER_APPROVED",
    "LEAVE_MANAGER_REJECTED",
    "LEAVE_HR_APPROVED",
    "LEAVE_HR_REJECTED",
    "LEAVE_CANCELLED",
    "LEAVE_WITHDRAWN",
    "LEAVE_ADMIN_OVERRIDE",
    "LEAVE_BALANCE_ACCRUED",
    "LEAVE_BALANCE_CARRY_FORWARD",
    "LEAVE_BALANCE_ADJUSTED",
]


# ─── 3. Default leave policies (sensible India-default quotas) ──────────────
# (leave_type, annual_quota, monthly_accrual, max_carry_forward,
#  encashment_allowed, requires_attachment, count_holidays_weekoffs,
#  max_consecutive_days, requires_notice_days, advance_book_days, label, color_hex)
DEFAULT_POLICIES = [
    ("CASUAL",      12,  1.0, 0,    False, False, True,  5,    1, 90,  "Casual Leave",       "#facc15"),
    ("SICK",        10,  0,   0,    False, False, True,  None, 0, 30,  "Sick Leave",         "#ef4444"),
    ("EARNED",      18,  1.5, 30,   True,  False, True,  15,   3, 180, "Earned Leave",       "#38bdf8"),
    ("MATERNITY",   180, 0,   0,    False, True,  True,  None, 30, 365,"Maternity Leave",    "#f472b6"),
    ("PATERNITY",   10,  0,   0,    False, False, True,  10,   7, 180, "Paternity Leave",    "#818cf8"),
    ("BEREAVEMENT", 5,   0,   0,    False, False, True,  5,    0, 30,  "Bereavement Leave",  "#a78bfa"),
    ("COMP_OFF",    0,   0,   0,    False, False, True,  None, 1, 180, "Compensatory Off",   "#fb923c"),
    ("LWP",         0,   0,   0,    False, False, False, None, 0, 90,  "Leave Without Pay",  "#f97316"),
    ("STUDY",       5,   0,   0,    False, True,  True,  10,   30,180, "Study Leave",        "#14b8a6"),
    ("SPECIAL",     0,   0,   0,    False, False, True,  None, 0, 180, "Special Leave",      "#c084fc"),
]


# ─── 4. System settings keys to seed (if missing) ───────────────────────────
DEFAULT_SETTINGS = [
    ("fiscal_year_start", "04-01", "Leave fiscal-year boundary in MM-DD; India default 04-01"),
    ("leave_encashment_formula", "basic_salary * days_encashed / 30",
        "Default encashment formula — admin-editable (Phase 2 wires this to payroll)"),
    ("leave_ref_counter", "0", "Monotonic counter for LeaveRequest.reference_no"),
]


def main():
    creds = parse_pg_url(read_db_url())
    print(f"Connecting to {creds['host']}:{creds['port']}/{creds['dbname']} ...")
    conn = psycopg2.connect(**creds, connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()

    # ── Step 1: enum values
    print("\n[1/4] Adding enum values to hr_attendance_log_action ...")
    added_enum = 0
    for val in NEW_ENUM_VALUES:
        try:
            cur.execute(
                f"ALTER TYPE hr_attendance_log_action ADD VALUE IF NOT EXISTS '{val}'"
            )
            added_enum += 1
        except psycopg2.errors.DuplicateObject:
            pass
    print(f"   processed {added_enum} value(s) (idempotent)")

    # ── Step 2: leave_request_id column on hr_attendance
    print("\n[2/4] Adding hr_attendance.leave_request_id (if missing) ...")
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'hr_attendance' AND column_name = 'leave_request_id'
    """)
    if cur.fetchone():
        print("   already present — skipped")
    else:
        # FK target table may not exist yet (will be created by SQLAlchemy on
        # startup). Add the column nullable first; FK will be added by a
        # later run after `Base.metadata.create_all` runs.
        cur.execute(
            "ALTER TABLE hr_attendance ADD COLUMN leave_request_id UUID NULL"
        )
        print("   added column hr_attendance.leave_request_id")

    # ── Step 3: seed default policies
    print("\n[3/4] Seeding default LeavePolicy rows ...")
    # Check table exists; if not, the SQLAlchemy create_all hasn't run yet.
    cur.execute("""
        SELECT 1 FROM information_schema.tables WHERE table_name = 'hr_leave_policies'
    """)
    if not cur.fetchone():
        print("   hr_leave_policies table not yet created — boot the backend once, then re-run this script")
    else:
        seeded = 0
        for (lt, quota, accrual, cf_max, encash, attach, count_hw, max_cons,
             notice, advance, label, color) in DEFAULT_POLICIES:
            cur.execute(
                "SELECT id FROM hr_leave_policies WHERE leave_type = %s::hr_leave_type",
                (lt,),
            )
            if cur.fetchone():
                continue
            cur.execute(
                """
                INSERT INTO hr_leave_policies (
                    id, leave_type, annual_quota, monthly_accrual, max_carry_forward,
                    encashment_allowed, requires_attachment, count_holidays_weekoffs,
                    max_consecutive_days, requires_notice_days, advance_book_days,
                    label, color_hex, is_active, is_deleted, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), %s::hr_leave_type, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, TRUE, FALSE, NOW(), NOW()
                )
                """,
                (lt, quota, accrual, cf_max, encash, attach, count_hw,
                 max_cons, notice, advance, label, color),
            )
            seeded += 1
        print(f"   inserted {seeded} new policy row(s)")

    # ── Step 4: seed system_settings keys
    print("\n[4/4] Seeding system_settings ...")
    setting_seeded = 0
    for key, value, desc in DEFAULT_SETTINGS:
        cur.execute("SELECT value FROM system_settings WHERE key = %s", (key,))
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO system_settings (key, value, description, updated_at)
            VALUES (%s, %s, %s, NOW())
            """,
            (key, value, desc),
        )
        setting_seeded += 1
    print(f"   inserted {setting_seeded} new setting(s)")

    cur.close()
    conn.close()
    print("\nDone. Leave module migration complete.")
    print("If hr_leave_policies / hr_leave_requests / hr_leave_balances / hr_leave_balance_history")
    print("did NOT exist yet, restart the backend now so SQLAlchemy creates them,")
    print("then re-run this script to finish seeding policies.")


if __name__ == "__main__":
    main()
