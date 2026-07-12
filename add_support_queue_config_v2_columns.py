"""Idempotent migration — Queue Config v2 columns (ServiceNow/Zendesk parity).

Adds to ``support_queues``:
  * sla_package_id     — per-queue SLA policy (precedence: org > queue > default)
  * capacity_limit     — open-ticket cap; NULL = unlimited
  * overflow_queue_id  — spill target when at capacity (one hop, no chains)

The NEW table ``support_rule_revisions`` (rule config versioning) is auto-created
by ``Base.metadata.create_all()`` at backend startup — this script only patches
the existing table (create_all never alters).

Re-runs are safe: each ALTER is gated on an information_schema check.

Run FROM the backend root so .env resolves to the live DB:
    cd C:\\Projects\\FourConnectService
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_support_queue_config_v2_columns.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import engine

PATCHES = {
    "support_queues": [
        ("sla_package_id",    "UUID REFERENCES support_sla_packages(id)"),
        ("capacity_limit",    "INTEGER"),
        ("overflow_queue_id", "UUID"),
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
    print(f"[migrate] added {added} columns, skipped {skipped} (already present)")


if __name__ == "__main__":
    main()
