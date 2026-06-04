"""Idempotent live-DB migration for the late-mark accumulation policy.

1. Adds `hr_attendance.late_condoned` (bool) — admin waiver of a late mark so it
   no longer counts toward the monthly accumulation penalty. `create_all()` does
   NOT add columns to existing tables, so this must be applied directly.
2. Adds the value 'LATE_PENALTY' to the native enum `hr_leave_ledger_kind` so
   late-accumulation LWP debits are distinguishable from no-show debits.
3. Seeds two SystemSettings (only if absent) that make the rule configurable:
     late_marks_per_penalty = 3     (every 3rd late mark in a month …)
     late_penalty_days       = 0.5  (… deducts this many days, to LWP)

Reads DATABASE_URL straight from .env (see CLAUDE.md cwd-resolution trap).
Run from the backend root; safe to re-run.
"""
import os
import re
import psycopg2

DEFAULTS = {
    "late_marks_per_penalty": "3",
    "late_penalty_days": "0.5",
}


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
        raise SystemExit("Could not parse DATABASE_URL")
    user, pwd, host, port, db = m.groups()
    return psycopg2.connect(dbname=db.split("?")[0], user=user, password=pwd, host=host, port=port)


def main() -> None:
    conn = _connect(_database_url())
    try:
        # 1. add column (idempotent)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_attendance' AND column_name = 'late_condoned'
            """)
            if cur.fetchone():
                print("- hr_attendance.late_condoned already exists")
            else:
                cur.execute("ALTER TABLE hr_attendance ADD COLUMN late_condoned boolean NOT NULL DEFAULT false")
                print("+ added hr_attendance.late_condoned")

            # 2. add enum value (idempotent)
            cur.execute("""
                SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = 'hr_leave_ledger_kind' AND e.enumlabel = 'LATE_PENALTY'
            """)
            if cur.fetchone():
                print("- enum hr_leave_ledger_kind.'LATE_PENALTY' already present")
            else:
                cur.execute("ALTER TYPE hr_leave_ledger_kind ADD VALUE 'LATE_PENALTY'")
                print("+ added enum hr_leave_ledger_kind.'LATE_PENALTY'")

        # 3. seed settings (only if absent)
        conn.autocommit = False
        with conn.cursor() as cur:
            for key, val in DEFAULTS.items():
                cur.execute("SELECT 1 FROM system_settings WHERE key = %s", (key,))
                if cur.fetchone():
                    print(f"- setting {key} already set")
                else:
                    cur.execute(
                        "INSERT INTO system_settings (key, value, description) VALUES (%s, %s, %s)",
                        (key, val, "Late-mark accumulation policy (auto-seeded)"),
                    )
                    print(f"+ seeded setting {key} = {val}")
            conn.commit()
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
