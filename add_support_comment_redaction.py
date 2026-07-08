"""Idempotent migration — adds comment-redaction columns to
`support_ticket_comments` (Zendesk/ServiceNow-style redaction).

Re-runs are safe: each ALTER is gated on an information_schema check.

Run FROM the backend root so .env resolves to the live DB:
    cd C:\\Projects\\FourConnectService
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_support_comment_redaction.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import engine

NEW_COLUMNS = [
    ("is_redacted",     "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("redacted_by_id",  "UUID"),
    ("redacted_at",     "TIMESTAMPTZ"),
    ("redacted_reason", "VARCHAR(300)"),
]

CHECK_SQL = """
SELECT 1 FROM information_schema.columns
WHERE table_name = 'support_ticket_comments' AND column_name = :col
"""


def main() -> None:
    added, skipped = 0, 0
    with engine.begin() as conn:
        for col, ddl in NEW_COLUMNS:
            if conn.execute(text(CHECK_SQL), {"col": col}).scalar():
                skipped += 1
                continue
            conn.execute(text(f'ALTER TABLE support_ticket_comments ADD COLUMN {col} {ddl}'))
            added += 1
            print(f"  + support_ticket_comments.{col} ({ddl})")
    print(f"[migrate] added {added} columns, skipped {skipped} (already present)")


if __name__ == "__main__":
    main()
