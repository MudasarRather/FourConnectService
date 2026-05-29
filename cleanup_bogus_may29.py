"""Clean up Umran's bogus May 29 punches (3-second IN/OUT created before the
early-clock-in policy was in place). Deletes the May 29 attendance row and
its punches so the policy gate has a clean slate to test against.
"""
import os, sys, re
from pathlib import Path
os.chdir(r"C:/Projects/FourConnectService"); sys.path.insert(0, r"C:/Projects/FourConnectService")
import psycopg2
from psycopg2.extras import register_uuid

env_path = Path(r"C:/Projects/FourConnectService/.env")
txt = env_path.read_text(encoding="utf-8")
m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", re.search(r"^DATABASE_URL\s*=\s*(.+)$", txt, re.MULTILINE).group(1).strip())
user, pwd, host, port, dbname = m.groups()

conn = psycopg2.connect(host=host, port=int(port), user=user, password=pwd, dbname=dbname)
register_uuid()
conn.autocommit = True
cur = conn.cursor()

# Look up Umran's emp id.
cur.execute("""
    SELECT e.id FROM hr_employees e
    JOIN users u ON u.id = e.user_id
    WHERE LOWER(u.email) LIKE '%umran%' AND e.is_deleted = false
""")
emp_id = cur.fetchone()[0]

# Delete May 29 punches (both IN at 1:09:05 and OUT at 1:09:08).
cur.execute("""
    DELETE FROM hr_attendance_punches
    WHERE employee_id = %s
      AND punch_time >= '2026-05-28 18:30:00+00:00'  -- May 29 00:00 IST
      AND punch_time <  '2026-05-29 18:30:00+00:00'  -- May 30 00:00 IST
    RETURNING id
""", (emp_id,))
deleted_punches = cur.fetchall()
print(f"Deleted {len(deleted_punches)} punches from May 29 IST.")

# Delete the May 29 attendance row so it gets recomputed cleanly on next access.
cur.execute("DELETE FROM hr_attendance WHERE employee_id = %s AND date = '2026-05-29' RETURNING id", (emp_id,))
deleted_atts = cur.fetchall()
print(f"Deleted {len(deleted_atts)} attendance row(s) for May 29.")

cur.close(); conn.close()
print("Done.")
