"""Probe — Queue Engine endpoints against the LIVE backend on :8000.

Mints a superuser JWT (SECRET_KEY from .env, `tv` = the user's token_version —
required since token-version session invalidation) and exercises:
  GET  /api/support-desk/queues/overview
  GET  /api/support-desk/skills
  GET  /api/support-desk/agent-status
  GET  /api/support-desk/queues/tier/1/board
  GET  /api/support-desk/tickets/skip-report
  POST /api/support-desk/automation-rules/simulate

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_queue_engine.py
"""
import platform
try:
    _ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
    _ur.__dict__["processor"] = "Intel"
    platform._uname_cache = _ur
except Exception:
    pass

import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from jose import jwt

psycopg2.extras.register_uuid()

# .env parsed directly (never trust get_settings() cwd resolution).
env = {}
with open(".env", encoding="utf-8") as f:
    for line in f:
        m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

db_url = env.get("DATABASE_URL", "")
m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url)
if not m:
    sys.exit("DATABASE_URL not parseable from .env")
user, pwd, host, port, dbname = m.groups()
conn = psycopg2.connect(user=user, password=pwd, host=host, port=port, dbname=dbname)
cur = conn.cursor()
cur.execute("""SELECT id, email, COALESCE(token_version, 0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
row = cur.fetchone()
if not row:
    sys.exit("no active superuser found")
uid, email, tv = row
print(f"superuser: {email}  tv={tv}")

# Sanity: new tables auto-created at boot?
for tbl in ("support_skills", "support_agent_status", "support_ticket_skips"):
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (tbl,))
    print(f"  table {tbl}: {'OK' if cur.fetchone() else 'MISSING'}")
conn.close()

secret = env.get("SECRET_KEY", "your-secret-key-here-change-this-in-production")
token = jwt.encode(
    {"sub": str(uid), "tv": int(tv),
     "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
    secret, algorithm="HS256")
HDRS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8000/api"


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=HDRS,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


checks = [
    ("GET", "/support-desk/queues/overview", None),
    ("GET", "/support-desk/skills", None),
    ("GET", "/support-desk/agent-status", None),
    ("GET", "/support-desk/queues/tier/1/board", None),
    ("GET", "/support-desk/tickets/skip-report", None),
    ("POST", "/support-desk/automation-rules/simulate",
     {"subject": "probe: printer down", "ticket_type": "incident", "priority": "high"}),
]
ok = True
for method, path, body in checks:
    code, data = call(method, path, body)
    good = 200 <= code < 300
    ok = ok and good
    summary = ""
    if good and isinstance(data, dict):
        keys = list(data.keys())[:6]
        summary = f"keys={keys}"
        if path.endswith("/overview"):
            summary += f" queues={data.get('queue_count')} totals={data.get('totals', {}).get('open')} open"
        if path.endswith("/board"):
            summary += f" total={data.get('total')} stats={list((data.get('stats') or {}).keys())[:5]}"
        if path.endswith("/simulate"):
            summary += f" matched={len(data.get('matched') or [])} decision={data.get('decision')}"
    elif good and isinstance(data, list):
        summary = f"list[{len(data)}]"
    print(f"{'PASS' if good else 'FAIL'} {code} {method} {path}  {summary if good else data}")

print("ALL PASS" if ok else "SOME FAILED")
