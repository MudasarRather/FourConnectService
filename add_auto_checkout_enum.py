"""One-shot: add AUTO_CHECKOUT to the hr_attendance_log_action Postgres enum.

create_all() doesn't ALTER an existing enum type. Safe to re-run — uses IF NOT
EXISTS. Reads .env directly (don't depend on cwd-relative settings load).
"""
import re, sys
from pathlib import Path
import psycopg2

env_path = Path(__file__).parent / ".env"
db_url = None
if env_path.exists():
    txt = env_path.read_text(encoding="utf-8")
    m = re.search(r"^DATABASE_URL\s*=\s*(.+)$", txt, re.MULTILINE)
    if m:
        db_url = m.group(1).strip()
if not db_url:
    print("Could not find DATABASE_URL in .env"); sys.exit(1)

m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url)
if not m:
    print(f"Bad DATABASE_URL: {db_url}"); sys.exit(1)
user, pwd, host, port, dbname = m.groups()

conn = psycopg2.connect(host=host, port=int(port), user=user, password=pwd, dbname=dbname)
conn.autocommit = True
cur = conn.cursor()
cur.execute("ALTER TYPE hr_attendance_log_action ADD VALUE IF NOT EXISTS 'AUTO_CHECKOUT'")
print("AUTO_CHECKOUT added to hr_attendance_log_action enum (or already present).")
cur.close()
conn.close()
