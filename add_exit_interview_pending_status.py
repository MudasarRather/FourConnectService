"""Ad-hoc migration -- add the PENDING state to the exit-interview status enum
and backfill the loophole rows.

Background
----------
Previously, accepting a separation auto-created the exit-interview row as
``SCHEDULED`` + ``mode='FORM'``. That made the self-service exit page show a
"Complete survey" button (and the admin tab show "Scheduled") even though HR had
never actually scheduled/invited the interview. The fix introduces a real
``PENDING`` ("awaiting HR scheduling") state.

This script:
  1. ``ALTER TYPE hr_exit_interview_status ADD VALUE IF NOT EXISTS 'PENDING'``
     (must run in autocommit -- Postgres forbids using a freshly-added enum value
     inside the same transaction that added it, and older PG forbids ADD VALUE in
     a txn block at all).
  2. Moves every *auto-created, never-actually-scheduled* interview to PENDING.
     "Never scheduled" = still SCHEDULED, with no scheduled_at / conducted_at /
     details / conducted_by, and no INTERVIEW_SCHEDULED audit row.

Run from the backend root so .env resolves (but we parse .env ourselves anyway):
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_exit_interview_pending_status.py
"""
from __future__ import annotations

import os
import re
import sys

import psycopg2


def _database_url() -> str:
    """Read DATABASE_URL straight from .env (don't trust get_settings() cwd resolution)."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    # Fallback (local dev only) -- matches config.py default.
    return "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


def _connect(db_url: str):
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url)
    if not m:
        raise SystemExit(f"Could not parse DATABASE_URL: {db_url!r}")
    user, pwd, host, port, name = m.groups()
    name = name.split("?")[0]
    return psycopg2.connect(host=host, port=port, dbname=name, user=user, password=pwd)


def main() -> None:
    db_url = _database_url()
    safe = re.sub(r":[^:@/]+@", ":****@", db_url)
    print(f"-> Connecting to {safe}")
    conn = _connect(db_url)

    # ── 1. add the enum value (autocommit) ──
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'hr_exit_interview_status' AND e.enumlabel = 'PENDING'"
        )
        if cur.fetchone():
            print("  - enum value 'PENDING' already present -- skipping ALTER TYPE")
        else:
            cur.execute("ALTER TYPE hr_exit_interview_status ADD VALUE IF NOT EXISTS 'PENDING'")
            print("  [ok] ALTER TYPE hr_exit_interview_status ADD VALUE 'PENDING'")

    # ── 2. backfill auto-created-but-never-scheduled interviews ──
    conn.autocommit = False
    with conn.cursor() as cur:
        # Be defensive -- confirm the table/columns exist before touching them.
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'hr_exit_interviews'"
        )
        cols = {r[0] for r in cur.fetchall()}
        required = {"status", "scheduled_at", "conducted_at", "details", "conducted_by_id"}
        if not required.issubset(cols):
            print(f"  ! hr_exit_interviews missing columns {required - cols} -- skipping backfill")
            conn.commit()
            conn.close()
            return

        cur.execute(
            """
            UPDATE hr_exit_interviews iv
               SET status = 'PENDING'
             WHERE iv.status = 'SCHEDULED'
               AND iv.scheduled_at IS NULL
               AND iv.conducted_at IS NULL
               AND iv.details IS NULL
               AND iv.conducted_by_id IS NULL
               AND NOT EXISTS (
                     SELECT 1 FROM hr_exit_audit_logs a
                      WHERE a.entity_id = iv.id
                        AND a.action = 'INTERVIEW_SCHEDULED'
                   )
            """
        )
        moved = cur.rowcount
        conn.commit()
        print(f"  [ok] backfilled {moved} never-scheduled interview(s) -> PENDING")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
