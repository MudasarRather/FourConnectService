"""Additive migration: add hr_exit_cases.public_token + personal_email.

Powers the Former-Employee Document Portal — a public, no-auth link by which a
leaver whose ERP login was revoked during clearance can still download their
relieving / experience letters. `public_token` is an unguessable
secrets.token_urlsafe(32) minted at case acceptance; `personal_email` is where HR
pushes the portal link. create_all() never adds columns to existing tables, so
this one-off ALTER brings the live DB in line with the model.

Reads DATABASE_URL straight from .env (cwd-independent — see CLAUDE.md) and is
idempotent: ADD COLUMN IF NOT EXISTS + a guarded unique index.

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_exit_portal_fields.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def db_url() -> str:
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db")


def main() -> int:
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url())
    if not m:
        print("Could not parse DATABASE_URL")
        return 1
    user, pwd, host, port, name = m.groups()
    name = name.split("?")[0]
    print(f"Connecting to {host}:{port}/{name} as {user} …")

    conn = psycopg2.connect(host=host, port=port, user=user, password=pwd, dbname=name)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("ALTER TABLE hr_exit_cases ADD COLUMN IF NOT EXISTS public_token varchar(64)")
    cur.execute("ALTER TABLE hr_exit_cases ADD COLUMN IF NOT EXISTS personal_email varchar(255)")
    # Unique index (also covers the lookup the public portal does on every request).
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_hr_exit_public_token "
                "ON hr_exit_cases (public_token) WHERE public_token IS NOT NULL")
    print("[OK] hr_exit_cases.public_token + personal_email present; unique index ensured.")

    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
