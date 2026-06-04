"""Phase-4 migration for the Leave & Absence module.

Adds configurable approval-chain support.

Idempotent. Steps:
  1. ALTER TABLE hr_leave_policies — add approval_chain JSONB NULL.
  2. ALTER TABLE hr_leave_requests — add approval_steps JSONB NOT NULL DEFAULT
     '[]'::jsonb and current_step INTEGER NOT NULL DEFAULT 0.
  3. Backfill approval_steps for legacy in-flight requests so the new code
     paths can read uniformly. PENDING_MANAGER/PENDING_HR/APPROVED rows get a
     synthesized two-stage [MANAGER, HR] chain reconstructed from the existing
     manager_*/hr_* columns. Terminal-rejected/cancelled/withdrawn rows are
     left untouched (their approval_steps stays empty — the router treats that
     as "use legacy columns").

Usage (any cwd — reads .env directly from backend root):
    python C:\\Projects\\FourConnectService\\add_leave_phase4.py

Re-run is safe.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras


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


def main():
    creds = parse_pg_url(read_db_url())
    print(f"Connecting to {creds['host']}:{creds['port']}/{creds['dbname']} ...")
    conn = psycopg2.connect(**creds, connect_timeout=10)
    psycopg2.extras.register_uuid()
    conn.autocommit = True
    cur = conn.cursor()

    print("\n[1/3] Adding hr_leave_policies.approval_chain ...")
    cur.execute("""
        ALTER TABLE hr_leave_policies
        ADD COLUMN IF NOT EXISTS approval_chain JSONB NULL;
    """)
    print("  ok")

    print("\n[2/3] Adding hr_leave_requests.approval_steps + current_step ...")
    cur.execute("""
        ALTER TABLE hr_leave_requests
        ADD COLUMN IF NOT EXISTS approval_steps JSONB NOT NULL DEFAULT '[]'::jsonb;
    """)
    cur.execute("""
        ALTER TABLE hr_leave_requests
        ADD COLUMN IF NOT EXISTS current_step INTEGER NOT NULL DEFAULT 0;
    """)
    print("  ok")

    print("\n[3/3] Backfilling approval_steps for legacy in-flight requests ...")
    # Pull every row that is in a non-terminal state OR is APPROVED — these
    # are the rows the new code paths might still want to read. We don't touch
    # rejected/cancelled/withdrawn rows; they're effectively frozen.
    cur.execute("""
        SELECT id, employee_id, status,
               manager_id, manager_decision, manager_decided_at, manager_notes,
               hr_id, hr_decision, hr_decided_at, hr_notes,
               is_admin_override,
               approval_steps, current_step
        FROM hr_leave_requests
        WHERE is_deleted = FALSE
          AND status IN ('DRAFT','PENDING_MANAGER','PENDING_HR','APPROVED');
    """)
    rows = cur.fetchall()
    backfilled = 0
    skipped = 0
    for r in rows:
        (lid, _emp_id, status,
         mgr_id, mgr_dec, mgr_at, mgr_notes,
         hr_id_, hr_dec, hr_at, hr_notes,
         is_override,
         existing_steps, existing_cur) = r
        if existing_steps and len(existing_steps) > 0:
            skipped += 1
            continue

        # Synthesize a two-stage chain from the legacy columns.
        mgr_step = {
            "step": 0,
            "approver_type": "MANAGER",
            "approver_user_id": str(mgr_id) if mgr_id else None,
            "label": "Reporting Manager",
            "decision": mgr_dec,
            "decided_by_id": str(mgr_id) if mgr_id else None,
            "decided_at": mgr_at.isoformat() if mgr_at else None,
            "notes": mgr_notes,
        }
        hr_step = {
            "step": 1,
            "approver_type": "HR",
            "approver_user_id": str(hr_id_) if hr_id_ else None,
            "label": "HR",
            "decision": hr_dec,
            "decided_by_id": str(hr_id_) if hr_id_ else None,
            "decided_at": hr_at.isoformat() if hr_at else None,
            "notes": hr_notes,
        }
        steps = [mgr_step, hr_step]

        if status == "APPROVED" or is_override:
            cur_idx = 2  # both done
        elif status == "PENDING_HR":
            cur_idx = 1
        elif status == "PENDING_MANAGER":
            cur_idx = 0
        else:  # DRAFT
            cur_idx = 0

        cur.execute(
            "UPDATE hr_leave_requests SET approval_steps = %s::jsonb, current_step = %s WHERE id = %s;",
            (json.dumps(steps), cur_idx, lid),
        )
        backfilled += 1

    print(f"  backfilled {backfilled} row(s), skipped {skipped} already-set row(s)")

    print("\nDone. Restart the backend to pick up the new ORM columns.")


if __name__ == "__main__":
    main()
