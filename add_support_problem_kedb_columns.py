"""Idempotent migration — Known-Error DB columns on support_problems (L3 workbench).

Adds:
  * support_problems — workaround (the published interim fix ServiceNow calls a
                       Known Error workaround), workaround_published (visible to
                       lower tiers as "known error — apply this"), owner_id (the
                       L3 engineer accountable for the problem record).

Re-runs are safe: each ALTER is gated on an information_schema check.

Run FROM the backend root so .env resolves to the live DB:
    cd C:\\Projects\\FourConnectService
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_support_problem_kedb_columns.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import engine

PATCHES = {
    "support_problems": [
        ("workaround",           "TEXT"),
        ("workaround_published", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("owner_id",             "UUID REFERENCES users(id)"),
    ],
}

CHECK_SQL = """
SELECT 1 FROM information_schema.columns
WHERE table_name = :tbl AND column_name = :col
"""


def main() -> None:
    added, skipped = 0, 0
    with engine.begin() as conn:
        for tbl, cols in PATCHES.items():
            for col, ddl in cols:
                if conn.execute(text(CHECK_SQL), {"tbl": tbl, "col": col}).scalar():
                    skipped += 1
                    continue
                conn.execute(text(f'ALTER TABLE {tbl} ADD COLUMN "{col}" {ddl}'))
                added += 1
                print(f"  + {tbl}.{col} ({ddl})")
        # The KEDB lookup filters on status + published flag.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_support_problems_status ON support_problems (status)"
        ))
    print(f"[migrate] added {added} columns, skipped {skipped} (already present)")


if __name__ == "__main__":
    main()
