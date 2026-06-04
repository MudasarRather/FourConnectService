"""Live migration — encashment manager-endorsement gate.

1. Adds PENDING_MANAGER to the hr_leave_encashment_status PG enum.
2. Adds manager_id / manager_decision / manager_decided_at / manager_notes
   columns to hr_leave_encashments (create_all() won't ALTER existing tables).

Idempotent; reads DATABASE_URL from .env directly (autocommit for ALTER TYPE).
"""
import re, psycopg2

env = open(".env").read()
m = re.search(r"DATABASE_URL=postgresql://(.*?):(.*?)@(.*?):(\d+)/(\S+)", env)
u, p, h, port, db = m.groups()
conn = psycopg2.connect(dbname=db, user=u, password=p, host=h, port=port)
conn.autocommit = True
cur = conn.cursor()

# 1. enum value
cur.execute("SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
            "WHERE t.typname='hr_leave_encashment_status'")
have = {r[0] for r in cur.fetchall()}
if "PENDING_MANAGER" in have:
    print("  enum PENDING_MANAGER already present")
else:
    cur.execute("ALTER TYPE hr_leave_encashment_status ADD VALUE IF NOT EXISTS 'PENDING_MANAGER'")
    print("  enum PENDING_MANAGER added")

# 2. columns
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='hr_leave_encashments'")
cols = {r[0] for r in cur.fetchall()}
addsql = {
    "manager_id": "ALTER TABLE hr_leave_encashments ADD COLUMN manager_id uuid REFERENCES users(id)",
    "manager_decision": "ALTER TABLE hr_leave_encashments ADD COLUMN manager_decision varchar(20)",
    "manager_decided_at": "ALTER TABLE hr_leave_encashments ADD COLUMN manager_decided_at timestamptz",
    "manager_notes": "ALTER TABLE hr_leave_encashments ADD COLUMN manager_notes text",
}
for name, sql in addsql.items():
    if name in cols:
        print(f"  column {name} already present")
    else:
        cur.execute(sql)
        print(f"  column {name} added")
cur.close(); conn.close(); print("Done.")
