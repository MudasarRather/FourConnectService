"""Additive migration: add hr_exit_clearance_items.submission (JSONB, nullable).

The MANAGER & PROJECT clearance lanes now follow an employee-submits →
reporting-manager-approves flow. The departing employee fills a work/knowledge/
client handover (notes, successor, a checklist over the playbook steps, optional
attachments) which is stored in this JSONB column; the manager then signs the
lane off. create_all() never adds columns to existing tables, so this one-off
ALTER brings the live DB in line with the model.

Reads DATABASE_URL straight from .env (cwd-independent — see CLAUDE.md) and is
idempotent: it checks information_schema first and ADD COLUMN IF NOT EXISTS.

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_exit_clearance_submission.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def db_url() -> str:
    # Parse .env directly rather than trusting get_settings() cwd resolution.
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

    cur.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = 'hr_exit_clearance_items' AND column_name = 'submission'"""
    )
    if cur.fetchone():
        print("[OK] hr_exit_clearance_items.submission already exists — nothing to do.")
        cur.close(); conn.close()
        return 0

    cur.execute("ALTER TABLE hr_exit_clearance_items ADD COLUMN IF NOT EXISTS submission jsonb")
    print("[OK] Added column hr_exit_clearance_items.submission (jsonb, nullable).")

    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
