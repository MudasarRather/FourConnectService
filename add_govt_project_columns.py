"""Idempotent migration — adds the 16 new government-project columns to the
`projects` table without disturbing existing data.

Re-runs are safe: each ALTER is gated on a `information_schema.columns`
check that confirms the column isn't already present.

Run:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_govt_project_columns.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import engine

# (column_name, ddl_type) — exact types must match the SQLAlchemy model.
NEW_COLUMNS = [
    ("government_order_no",        "VARCHAR"),
    ("order_date",                 "TIMESTAMP"),
    ("issuing_authority",          "VARCHAR"),
    ("order_received_date",        "TIMESTAMP"),
    ("department",                 "VARCHAR"),
    ("category",                   "VARCHAR"),
    ("priority",                   "VARCHAR"),
    ("state",                      "VARCHAR"),
    ("district",                   "VARCHAR"),
    ("funding_type",               "VARCHAR"),
    ("project_head_name",          "VARCHAR"),
    ("project_head_designation",   "VARCHAR"),
    ("project_head_contact",       "VARCHAR"),
    ("nodal_officer",              "VARCHAR"),
    ("contractor",                 "VARCHAR"),
    ("lifecycle_status",           "VARCHAR"),
]

CHECK_SQL = """
SELECT 1 FROM information_schema.columns
WHERE table_name = 'projects' AND column_name = :col
"""

INDEX_SQL = "CREATE INDEX IF NOT EXISTS ix_projects_government_order_no ON projects (government_order_no)"


def main() -> None:
    added, skipped = 0, 0
    with engine.begin() as conn:
        for col, ddl in NEW_COLUMNS:
            exists = conn.execute(text(CHECK_SQL), {"col": col}).scalar()
            if exists:
                skipped += 1
                continue
            conn.execute(text(f'ALTER TABLE projects ADD COLUMN {col} {ddl}'))
            added += 1
            print(f"  + projects.{col} ({ddl})")
        # Lightweight default for lifecycle_status on existing rows so the UI
        # doesn't show NULL everywhere — backfill is *safe* (rows that already had
        # a value are untouched because of the WHERE clause).
        conn.execute(text(
            "UPDATE projects SET lifecycle_status = 'Order Received' "
            "WHERE lifecycle_status IS NULL"
        ))
        conn.execute(text(INDEX_SQL))
    print(f"[migrate] added {added} columns, skipped {skipped} (already present)")


if __name__ == "__main__":
    main()
