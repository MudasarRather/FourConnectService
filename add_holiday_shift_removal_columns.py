"""Idempotent migration — add removal-audit columns to hr_holiday_shift_assignments.

The Holiday Roster "stand-down" modal captures WHY an employee was removed from a
holiday shift. We persist that on the (soft-deleted) row so the removal is
auditable instead of vanishing.

Adds (all nullable, safe to backfill as NULL):
    removal_reason     TEXT
    removal_category   VARCHAR(64)
    removed_at         TIMESTAMPTZ
    removed_by_id      UUID  (FK users.id)

Reads DATABASE_URL straight from .env (NOT get_settings()) so it always targets the
same DB the backend uses regardless of cwd — see CLAUDE.md env note. Idempotent:
re-running skips columns that already exist.

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_holiday_shift_removal_columns.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = "hr_holiday_shift_assignments"

# (column, DDL type clause)
COLUMNS = [
    ("removal_reason", "TEXT"),
    ("removal_category", "VARCHAR(64)"),
    ("removed_at", "TIMESTAMP WITH TIME ZONE"),
    ("removed_by_id", "UUID"),
]


def _database_url() -> str:
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            txt = fh.read()
        m = re.search(r"^\s*DATABASE_URL\s*=\s*(.+)\s*$", txt, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    # Fallback to the hardcoded local default (config.py) — only hit when .env is absent.
    return "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


def main() -> int:
    url = _database_url()
    safe = re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)
    print(f"[migrate] target: {safe}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (TABLE,)
    )
    if not cur.fetchone():
        print(f"[migrate] table {TABLE!r} does not exist — nothing to do.")
        return 0

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (TABLE,),
    )
    existing = {r[0] for r in cur.fetchall()}

    added = 0
    for col, ddl in COLUMNS:
        if col in existing:
            print(f"[migrate] {col}: already present — skip")
            continue
        cur.execute(f'ALTER TABLE {TABLE} ADD COLUMN {col} {ddl}')
        print(f"[migrate] {col}: ADDED ({ddl})")
        added += 1

    # FK for removed_by_id → users.id (best-effort; skip if it already exists).
    if "removed_by_id" not in existing:
        cur.execute(
            """SELECT 1 FROM information_schema.table_constraints
               WHERE table_name = %s AND constraint_name = %s""",
            (TABLE, "fk_holiday_shift_removed_by"),
        )
        if not cur.fetchone():
            try:
                cur.execute(
                    f'ALTER TABLE {TABLE} ADD CONSTRAINT fk_holiday_shift_removed_by '
                    f'FOREIGN KEY (removed_by_id) REFERENCES users(id)'
                )
                print("[migrate] fk_holiday_shift_removed_by: ADDED")
            except Exception as exc:  # noqa: BLE001
                print(f"[migrate] FK skipped (non-fatal): {exc}")

    cur.close()
    conn.close()
    print(f"[migrate] done — {added} column(s) added.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
