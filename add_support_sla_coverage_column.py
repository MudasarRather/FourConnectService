"""Add the SLA coverage-calendar column to support_sla_packages.

Base.metadata.create_all() never ALTERs existing tables, so live DBs need this
one-shot script (same pattern as add_support_queue_engine_columns.py). Idempotent —
safe to re-run. Reads .env directly (pydantic-settings resolves .env relative to cwd,
which silently points off-cwd invocations at the wrong DB).

    python C:\\Projects\\FourConnectService\\add_support_sla_coverage_column.py
"""
import re
import sys

import psycopg2
import psycopg2.extras

sys.stdout.reconfigure(encoding="utf-8")
psycopg2.extras.register_uuid()

ENV_PATH = r"C:\Projects\FourConnectService\.env"

def main() -> None:
    env = open(ENV_PATH, encoding="utf-8").read()
    m = re.search(r"DATABASE_URL=postgresql://(.*?):(.*?)@(.*?):(\d+)/(\S+)", env)
    if not m:
        print("DATABASE_URL not found in .env — aborting (refusing to guess a fallback DB).")
        sys.exit(1)
    user, pw, host, port, dbname = m.groups()
    print(f"target DB: {host}:{port}/{dbname}")

    conn = psycopg2.connect(user=user, password=pw, host=host, port=port, dbname=dbname)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'support_sla_packages' AND column_name = 'coverage'""")
    if cur.fetchone():
        print("support_sla_packages.coverage already exists — nothing to do.")
    else:
        cur.execute("""ALTER TABLE support_sla_packages
                       ADD COLUMN coverage JSONB NOT NULL DEFAULT '{}'::jsonb""")
        print("added support_sla_packages.coverage (JSONB, default {} = 24x7 — no behaviour change).")

    conn.close()
    print("done.")

if __name__ == "__main__":
    main()
