"""Add proof-request columns to hr_leave_requests.

Adds:
  * proof_requested          BOOLEAN NOT NULL DEFAULT FALSE
  * proof_requested_at       TIMESTAMP WITH TIME ZONE
  * proof_requested_by_id    UUID REFERENCES users(id)
  * proof_request_note       TEXT
  * proof_submitted_at       TIMESTAMP WITH TIME ZONE

Uses ADD COLUMN IF NOT EXISTS so it's safe to re-run.

The companion table `hr_leave_proof_attachments` is created automatically by
SQLAlchemy's `Base.metadata.create_all()` on the next backend boot, so this
script only handles the in-place ALTERs.

Usage (any cwd — reads .env directly from backend root):
    python C:\\Projects\\FourConnectService\\add_proof_columns.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras


BACKEND_ROOT = Path(__file__).parent
ENV_PATH = BACKEND_ROOT / ".env"

# Fallback for environments where .env is missing — matches the hardcoded
# config.py default.
FALLBACK_DB_URL = "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


def read_db_url() -> str:
    if not ENV_PATH.exists():
        print(f"WARNING: .env not found at {ENV_PATH}; using local fallback")
        return FALLBACK_DB_URL
    txt = ENV_PATH.read_text(encoding="utf-8")
    m = re.search(r"^\s*DATABASE_URL\s*=\s*(.+?)\s*$", txt, re.MULTILINE)
    if not m:
        print("WARNING: DATABASE_URL not set in .env; using local fallback")
        return FALLBACK_DB_URL
    return m.group(1).strip()


def normalize_pg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgres+psycopg2://"):
        return "postgresql://" + url[len("postgres+psycopg2://"):]
    return url


# (column_name, definition) — definition omits the column name, only carries
# the type + constraints so we can SELECT against information_schema first.
COLUMNS = [
    ("proof_requested",        "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("proof_requested_at",     "TIMESTAMP WITH TIME ZONE"),
    ("proof_requested_by_id",  "UUID REFERENCES users(id)"),
    ("proof_request_note",     "TEXT"),
    ("proof_submitted_at",     "TIMESTAMP WITH TIME ZONE"),
]


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def main() -> int:
    db_url = normalize_pg_url(read_db_url())
    # Hide the password when echoing.
    safe = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", db_url)
    print(f"Connecting to {safe} ...")
    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
    except Exception as e:
        print(f"ERROR: could not connect: {type(e).__name__}: {e}")
        return 1

    psycopg2.extras.register_uuid()
    conn.autocommit = False
    cur = conn.cursor()

    table = "hr_leave_requests"
    print(f"\nApplying ALTER TABLE on {table}\n")
    for col, ddl in COLUMNS:
        if column_exists(cur, table, col):
            print(f"  skipped (already present): {col}")
            continue
        sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl};"
        try:
            cur.execute(sql)
            print(f"  OK column {col} added")
        except Exception as e:
            print(f"  FAILED on {col}: {type(e).__name__}: {e}")
            conn.rollback()
            cur.close()
            conn.close()
            return 2

    conn.commit()
    cur.close()
    conn.close()
    print("\nDone. The companion table hr_leave_proof_attachments will be")
    print("created by SQLAlchemy create_all() on the next backend boot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
