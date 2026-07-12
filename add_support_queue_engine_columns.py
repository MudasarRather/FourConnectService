"""Idempotent migration — Queue Engine columns (ServiceNow AWA / Zendesk routing).

Adds:
  * support_queues            — tier, skill_ids, serve_order, queue_priority,
                                max_agent_load, is_default, business_hours
  * support_automation_rules  — trigger, stop_processing, time_threshold_mins

New TABLES (support_skills, support_agent_status, support_ticket_skips) are
auto-created by Base.metadata.create_all() at backend startup — this script only
patches existing tables (create_all never alters).

Re-runs are safe: each ALTER is gated on an information_schema check.

Run FROM the backend root so .env resolves to the live DB:
    cd C:\\Projects\\FourConnectService
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_support_queue_engine_columns.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import engine

PATCHES = {
    "support_queues": [
        ("tier",            "INTEGER"),
        ("skill_ids",       "JSONB NOT NULL DEFAULT '[]'::jsonb"),
        ("serve_order",     "VARCHAR(20) NOT NULL DEFAULT 'priority_age'"),
        ("queue_priority",  "INTEGER NOT NULL DEFAULT 50"),
        ("max_agent_load",  "INTEGER"),
        ("is_default",      "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("business_hours",  "JSONB"),
    ],
    "support_automation_rules": [
        ("trigger",             "VARCHAR(16) NOT NULL DEFAULT 'on_create'"),
        ("stop_processing",     "BOOLEAN NOT NULL DEFAULT TRUE"),
        ("time_threshold_mins", "INTEGER"),
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
                # "trigger" is a reserved-ish keyword — always quote the identifier.
                conn.execute(text(f'ALTER TABLE {tbl} ADD COLUMN "{col}" {ddl}'))
                added += 1
                print(f"  + {tbl}.{col} ({ddl})")
        # Helpful partial index for the tier boards.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_support_queues_tier ON support_queues (tier) WHERE tier IS NOT NULL"
        ))
    print(f"[migrate] added {added} columns, skipped {skipped} (already present)")


if __name__ == "__main__":
    main()
