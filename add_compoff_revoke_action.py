"""Idempotent live migration — add COMP_OFF_REVOKED to the Postgres
``hr_attendance_log_action`` enum (admin comp-off grant deletion). Mirrors
add_leave_policy_log_actions.py — autocommit, reads .env directly."""
import re, psycopg2

NEW_VALUES = ["COMP_OFF_REVOKED"]

env = open(".env").read()
m = re.search(r"DATABASE_URL=postgresql://(.*?):(.*?)@(.*?):(\d+)/(\S+)", env)
if not m:
    raise SystemExit("Could not parse DATABASE_URL from .env")
u, p, h, port, db = m.groups()
conn = psycopg2.connect(dbname=db, user=u, password=p, host=h, port=port)
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
            "WHERE t.typname='hr_attendance_log_action'")
existing = {r[0] for r in cur.fetchall()}
for v in NEW_VALUES:
    if v in existing:
        print(f"  skip (present): {v}")
    else:
        cur.execute(f"ALTER TYPE hr_attendance_log_action ADD VALUE IF NOT EXISTS '{v}'")
        print(f"  added: {v}")
cur.close(); conn.close(); print("Done.")
