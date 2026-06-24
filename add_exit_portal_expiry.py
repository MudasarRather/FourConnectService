"""Additive migration: add hr_exit_cases.public_token_expires_at (timestamptz).

The former-employee document portal link is now valid only for a short window
(default 5 days) AFTER the documents are issued — set/refreshed at letter issue
and on HR 'Regenerate'. Past it the portal returns 410 Gone. create_all() never
adds columns to existing tables, so this one-off ALTER syncs the live DB.

Reads DATABASE_URL straight from .env (cwd-independent — see CLAUDE.md). Idempotent.

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_exit_portal_expiry.py
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
    cur.execute("ALTER TABLE hr_exit_cases ADD COLUMN IF NOT EXISTS public_token_expires_at timestamptz")
    print("[OK] hr_exit_cases.public_token_expires_at present.")
    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
