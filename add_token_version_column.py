"""Idempotent migration: add users.token_version.

Bumping this integer invalidates a user's already-issued JWTs (the token carries
a `tv` claim that get_current_user compares against this column), so changing an
employee's email or ERP password logs their live session out within the auth
heartbeat window — WITHOUT permanently deactivating the account (they sign back
in with the new credentials).

Reads DATABASE_URL straight from .env next to this script (NOT get_settings —
cwd-relative resolution would silently hit the localhost fallback DB; see
CLAUDE.md "the live DB is REMOTE"). Safe to re-run: ADD COLUMN IF NOT EXISTS.

    & "C:\\...\\python.exe" C:\\Projects\\FourConnectService\\add_token_version_column.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def _database_url() -> str:
    env_path = os.path.join(HERE, ".env")
    if not os.path.exists(env_path):
        sys.exit(f".env not found at {env_path}")
    with open(env_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^\s*DATABASE_URL\s*=\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        sys.exit("DATABASE_URL not present in .env")
    url = m.group(1).strip().strip('"').strip("'")
    # psycopg2 accepts postgresql:// but not the SQLAlchemy +psycopg2 suffix.
    url = url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgres+psycopg2://", "postgresql://"
    )
    return url


def main() -> None:
    url = _database_url()
    # host (without creds) for a safe, non-secret confirmation line
    host = re.sub(r"^.*@", "", url).split("/")[0]
    print(f"Connecting to {host} ...")
    conn = psycopg2.connect(url)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 1;"
        )
        # Confirm it landed.
        cur.execute(
            "SELECT data_type, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'token_version';"
        )
        row = cur.fetchone()
        cur.close()
        if row:
            print(f"OK — users.token_version present (type={row[0]}, default={row[1]}).")
        else:
            sys.exit("FAILED — column not found after ALTER.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
