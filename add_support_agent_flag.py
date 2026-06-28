"""Idempotent migration — add users.is_support_agent (Support Desk agent flag).

A regular employee with this flag set can work the support desk (tickets, ITIL,
dashboards) on the /user panel without being a full superuser. Safe + additive:
existing rows default to FALSE, so nobody gains access until explicitly flagged.

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_support_agent_flag.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import engine

CHECK_SQL = """
SELECT 1 FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'is_support_agent'
"""


def main() -> None:
    with engine.begin() as conn:
        exists = conn.execute(text(CHECK_SQL)).scalar()
        if exists:
            print("[migrate] users.is_support_agent already present — nothing to do.")
            return
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN is_support_agent BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        print("[migrate] added users.is_support_agent (default FALSE).")


if __name__ == "__main__":
    main()
