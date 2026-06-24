"""Migration: Exit Management module tables + PG enum types + guards.

``Base.metadata.create_all()`` (run on startup) creates the 7 new tables and most
PG enum types automatically. This script handles what create_all() can't:

  1. Idempotently CREATE every Exit enum type (so a partial earlier run / a
     create_all() race never leaves a half-built type) — guarded with DO $$..$$.
  2. ADD VALUE 'RELIEVING_LETTER' to the existing hr_doc_category enum (PG has no
     IF NOT EXISTS for CREATE TYPE, but ADD VALUE IF NOT EXISTS works on PG 12+).
  3. The partial-unique index uq_hr_exit_open_case — one OPEN case per employee.
  4. Verify the 7 tables exist (safe to run after the app has booted once).

Idempotent throughout. Reads DATABASE_URL straight from .env (cwd-safe).

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_exit_management_tables.py
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


def has_table(cur, table) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,))
    return cur.fetchone() is not None


# enum_name -> list of values
ENUMS = {
    "hr_exit_resignation_type": [
        "VOLUNTARY", "RETIREMENT", "CONTRACT_COMPLETION", "PROBATION_EXIT",
        "MUTUAL_SEPARATION", "TERMINATION", "TRANSFER",
    ],
    "hr_exit_reason_category": [
        "BETTER_OPPORTUNITY", "COMPENSATION", "RELOCATION", "HIGHER_STUDIES",
        "HEALTH", "PERSONAL", "WORK_ENVIRONMENT", "CAREER_GROWTH", "RETIREMENT",
        "PERFORMANCE", "MISCONDUCT", "REDUNDANCY", "CONTRACT_END", "OTHER",
    ],
    "hr_exit_case_status": [
        "DRAFT", "SUBMITTED", "MANAGER_REVIEW", "ACCEPTED", "NOTICE_PERIOD",
        "CLEARANCE", "SETTLEMENT", "COMPLETED", "WITHDRAWN", "REJECTED", "CANCELLED",
    ],
    "hr_exit_clearance_dept": ["MANAGER", "IT", "FINANCE", "HR", "ADMIN", "SECURITY", "PROJECT"],
    "hr_exit_clearance_status": ["PENDING", "IN_PROGRESS", "CLEARED", "BLOCKED", "NA"],
    "hr_exit_settlement_status": ["DRAFT", "VERIFIED", "APPROVED", "PAID", "CLOSED", "REVERSED"],
    "hr_exit_interview_status": ["SCHEDULED", "IN_PROGRESS", "COMPLETED", "SKIPPED", "CANCELLED"],
    "hr_exit_doc_status": ["NOT_GENERATED", "GENERATED", "ISSUED", "REVOKED"],
    "hr_exit_audit_action": [
        "CREATED", "UPDATED", "SUBMITTED", "MANAGER_DECISION", "ACCEPTED", "REJECTED",
        "WITHDRAWN", "CANCELLED", "NOTICE_STARTED", "NOTICE_WAIVED", "NOTICE_ADJUSTED",
        "CLEARANCE_SEEDED", "CLEARANCE_ITEM_UPDATED", "CLEARANCE_REOPENED",
        "CLEARANCE_COMPLETED", "INTERVIEW_SCHEDULED", "INTERVIEW_COMPLETED",
        "ASSET_RETURN_FLAGGED", "SETTLEMENT_DRAFTED", "SETTLEMENT_RECALCULATED",
        "SETTLEMENT_VERIFIED", "SETTLEMENT_APPROVED", "SETTLEMENT_PAID",
        "SETTLEMENT_REVERSED", "SETTLEMENT_CLOSED", "LETTER_GENERATED", "LETTER_ISSUED",
        "LETTER_REVOKED", "EXITED", "ARCHIVED", "POLICY_CREATED", "POLICY_UPDATED",
        "POLICY_DELETED",
    ],
}

EXIT_TABLES = [
    "hr_exit_policies", "hr_exit_cases", "hr_exit_clearance_items",
    "hr_exit_interviews", "hr_exit_settlements", "hr_exit_documents",
    "hr_exit_audit_logs",
]


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

    # 1. Idempotently create every Exit enum type.
    for enum_name, values in ENUMS.items():
        vals = ", ".join("'%s'" % v for v in values)
        cur.execute(
            f"""DO $$ BEGIN
                    CREATE TYPE {enum_name} AS ENUM ({vals});
                EXCEPTION WHEN duplicate_object THEN null;
                END $$;"""
        )
    print(f"[OK] Ensured {len(ENUMS)} Exit enum types.")

    # 2. Add RELIEVING_LETTER to the existing hr_doc_category enum (experience already there).
    try:
        cur.execute("ALTER TYPE hr_doc_category ADD VALUE IF NOT EXISTS 'RELIEVING_LETTER'")
        print("[OK] hr_doc_category has RELIEVING_LETTER.")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] could not add RELIEVING_LETTER to hr_doc_category: {exc}")

    # 3. Partial-unique index: one OPEN *and not-deleted* case per employee.
    #    The is_deleted=false clause is essential — without it a soft-deleted case
    #    that is still in an open status keeps occupying the employee's slot and a
    #    new case raises a raw UniqueViolation. Drop any stale (pre-is_deleted)
    #    index def first so re-runs self-heal.
    if has_table(cur, "hr_exit_cases"):
        cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_hr_exit_open_case'")
        row = cur.fetchone()
        if row and "is_deleted" not in row[0]:
            cur.execute("DROP INDEX IF EXISTS uq_hr_exit_open_case")
            print("[OK] dropped stale uq_hr_exit_open_case (predicate lacked is_deleted).")
        cur.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_hr_exit_open_case
                   ON hr_exit_cases (employee_id)
                   WHERE is_deleted = false
                     AND status IN ('DRAFT','SUBMITTED','MANAGER_REVIEW','ACCEPTED',
                                    'NOTICE_PERIOD','CLEARANCE','SETTLEMENT')"""
        )
        print("[OK] uq_hr_exit_open_case partial-unique index ensured.")
    else:
        cur.execute("SELECT 1")
        print("[INFO] hr_exit_cases not present yet — start the backend once "
              "(create_all) then re-run for the partial-unique index.")

    # 4. Verify tables.
    present = [t for t in EXIT_TABLES if has_table(cur, t)]
    missing = [t for t in EXIT_TABLES if t not in present]
    print(f"[INFO] Exit tables present: {len(present)}/{len(EXIT_TABLES)}")
    if missing:
        print(f"[INFO] Not yet created (create_all on next boot): {missing}")

    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
