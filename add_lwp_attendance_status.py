"""One-time, idempotent live-DB migration for the LWP no-show workflow.

1. Adds the value 'LWP' to the native Postgres enum `hr_attendance_status`
   (SQLAlchemy `Enum(..., name="hr_attendance_status")`). `create_all()` never
   alters an existing enum, so this must be applied directly.
2. Ensures the LWP LeavePolicy has a usable annual_quota so a no-show can be
   covered (otherwise every no-show falls straight to ABSENT). Only sets a
   starter quota when the current quota is 0 — never overwrites an admin value.

Reads DATABASE_URL straight from .env (NOT via get_settings(), whose cwd-relative
resolution silently falls back to the local DB — see CLAUDE.md). Mirrors the
regex parse used by the login handler.

Run from the backend root:
    & "<python>" add_lwp_attendance_status.py
Safe to re-run.
"""
import os
import re
import sys

import psycopg2

STARTER_LWP_QUOTA = "12.00"  # days/FY — admin-adjustable in Leave Policies


def _database_url() -> str:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DATABASE_URL not found in .env")


def _connect(url: str):
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", url)
    if not m:
        raise SystemExit(f"Could not parse DATABASE_URL: {url[:30]}...")
    user, pwd, host, port, db = m.groups()
    db = db.split("?")[0]
    return psycopg2.connect(dbname=db, user=user, password=pwd, host=host, port=port)


def main() -> None:
    url = _database_url()
    print(f"Connecting to {re.sub(r':.*@', ':****@', url)}")
    conn = _connect(url)
    try:
        # ── 1. Add the enum value (must be autocommit — ADD VALUE cannot run
        #       inside a transaction block on older Postgres). Guarded so it is
        #       a no-op when the value already exists.
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = 'hr_attendance_status' AND e.enumlabel = 'LWP'
                """
            )
            if cur.fetchone():
                print("• enum value hr_attendance_status.'LWP' already present — skipping")
            else:
                cur.execute("ALTER TYPE hr_attendance_status ADD VALUE 'LWP'")
                print("✓ added enum value hr_attendance_status.'LWP'")

        # ── 2. Ensure a usable LWP quota (only when currently 0). ──────────
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, annual_quota, is_active
                FROM hr_leave_policies
                WHERE leave_type = 'LWP' AND is_deleted = false
                ORDER BY created_at NULLS LAST
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row is None:
                print("• no LWP LeavePolicy row found — create one in Leave Policies "
                      f"and set its annual quota (suggested {STARTER_LWP_QUOTA}). "
                      "Until then, every no-show resolves to ABSENT.")
            else:
                pid, quota, is_active = row
                if quota and float(quota) > 0:
                    print(f"• LWP policy already has annual_quota={quota} — leaving as-is")
                else:
                    cur.execute(
                        "UPDATE hr_leave_policies SET annual_quota = %s, is_active = true "
                        "WHERE id = %s",
                        (STARTER_LWP_QUOTA, pid),
                    )
                    print(f"✓ set LWP annual_quota = {STARTER_LWP_QUOTA} (was {quota}); "
                          "adjust anytime in Leave Policies")
            conn.commit()
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
