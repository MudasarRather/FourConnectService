"""Idempotent: add 'DELETED' to the native Postgres enum hr_exit_audit_action.

The ExitCase soft-delete endpoint now records a DELETED audit row, but the
audit `action` column is a native PG enum — create_all() never adds new enum
values, so the value must be added with ALTER TYPE. Safe to re-run.

Reads .env directly (see CLAUDE.md: pydantic-settings resolves .env relative to
cwd, so off-cwd invocation silently hits the local fallback DB).

    & "C:\\...\\python.exe" C:\\Projects\\FourConnectService\\add_exit_audit_deleted_enum.py
"""
import os
import re
import sys

import psycopg2

ENUM_NAME = "hr_exit_audit_action"
NEW_VALUE = "DELETED"


def _database_url() -> str:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"
    )


def main() -> int:
    url = _database_url()
    m = re.search(r"postgresql(?:\+psycopg2)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", url)
    if not m:
        print(f"!! could not parse DATABASE_URL: {url}")
        return 1
    user, password, host, port, dbname = m.groups()
    dbname = dbname.split("?")[0]
    print(f"Connecting to {host}:{port}/{dbname} as {user} …")

    conn = psycopg2.connect(
        dbname=dbname, user=user, password=password, host=host, port=port
    )
    conn.autocommit = True  # ALTER TYPE ... ADD VALUE cannot run inside a tx block
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = %s AND e.enumlabel = %s
            """,
            (ENUM_NAME, NEW_VALUE),
        )
        if cur.fetchone():
            print(f"[ok] '{NEW_VALUE}' already present on {ENUM_NAME} - nothing to do.")
            return 0
        cur.execute(f"ALTER TYPE {ENUM_NAME} ADD VALUE IF NOT EXISTS '{NEW_VALUE}'")
        print(f"[ok] added '{NEW_VALUE}' to {ENUM_NAME}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
