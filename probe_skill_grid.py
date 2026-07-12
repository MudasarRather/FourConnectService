"""Probe — Skills / CERTIFICATION GRID workflow against the LIVE backend on :8000.

Coverage (self-cleaning — every probe row is deleted at the end):
  GET    /api/support-desk/skills/                  — list + enrichment (agents, queue_count)
  POST   /api/support-desk/skills/                  — create; duplicate code -> 400
  PATCH  /api/support-desk/skills/{id}              — roster flip (the grid's cell toggle),
                                                      is_active pause, code clash -> 409 (NEW guard)
  DELETE /api/support-desk/skills/{id}              — 409 while a lane demands it, 204 after unwiring

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_skill_grid.py
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
cur.execute("""SELECT id FROM users WHERE is_active = TRUE LIMIT 2""")
agent_ids = [str(r[0]) for r in cur.fetchall()]
conn.close()
print(f"superuser: {email}  tv={tv}  probe agents: {len(agent_ids)}")

secret = env.get("SECRET_KEY", "your-secret-key-here-change-this-in-production")
token = jwt.encode(
    {"sub": str(uid), "tv": int(tv),
     "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
    secret, algorithm="HS256")
HDRS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8000/api"

FAIL = 0


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=HDRS,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


def check(label, ok, extra=""):
    global FAIL
    print(f"  [{'OK' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


print("\n── create + duplicate-code guards ──")
st, sa = call("POST", "/support-desk/skills/", {"name": "GridProbe Alpha", "code": "GRID_PRB_A",
                                                "color": "#f2b64d", "agent_ids": []})
check("create skill A", st == 201, f"status={st}")
st, sb = call("POST", "/support-desk/skills/", {"name": "GridProbe Beta", "code": "GRID_PRB_B"})
check("create skill B", st == 201, f"status={st}")
st, _ = call("POST", "/support-desk/skills/", {"name": "GridProbe Clash", "code": "GRID_PRB_A"})
check("create with taken code -> 400", st == 400, f"status={st}")
st, _ = call("PATCH", f"/support-desk/skills/{sb['id']}", {"code": "GRID_PRB_A"})
check("PATCH code onto taken code -> 409 (new guard)", st == 409, f"status={st}")

print("\n── the grid's cell toggle — roster PATCH round-trip ──")
st, s1 = call("PATCH", f"/support-desk/skills/{sa['id']}", {"agent_ids": agent_ids[:1]})
check("certify one agent", st == 200 and len(s1.get("agent_ids", [])) == 1, f"status={st}")
check("enrichment carries agents[{id,name}]", bool(s1.get("agents")) and "name" in s1["agents"][0])
st, s2 = call("PATCH", f"/support-desk/skills/{sa['id']}", {"agent_ids": []})
check("revoke back to zero", st == 200 and s2.get("agent_ids") == [], f"status={st}")

print("\n── pause (is_active) round-trip ──")
st, s3 = call("PATCH", f"/support-desk/skills/{sa['id']}", {"is_active": False})
check("pause skill", st == 200 and s3.get("is_active") is False, f"status={st}")
st, listed = call("GET", "/support-desk/skills/?include_inactive=true")
check("paused skill still listed with include_inactive", st == 200 and
      any(str(x["id"]) == str(sa["id"]) and x["is_active"] is False for x in (listed or [])))
st, listed2 = call("GET", "/support-desk/skills/")
check("paused skill hidden from default list", st == 200 and
      not any(str(x["id"]) == str(sa["id"]) for x in (listed2 or [])))
st, _ = call("PATCH", f"/support-desk/skills/{sa['id']}", {"is_active": True})
check("reactivate", st == 200, f"status={st}")

print("\n── delete guard — demanded skills can't be deleted ──")
st, qz = call("POST", "/support-desk/queues/", {"name": "GridProbe Lane", "code": "GRID_PRB_Q",
                                                "skill_ids": [str(sa["id"])]})
check("create lane demanding skill A", st == 201, f"status={st}")
st, enr = call("GET", "/support-desk/skills/?include_inactive=true")
row_a = next((x for x in (enr or []) if str(x["id"]) == str(sa["id"])), {})
check("queue_count enrichment reflects the demand", row_a.get("queue_count") == 1,
      f"queue_count={row_a.get('queue_count')}")
st, body = call("DELETE", f"/support-desk/skills/{sa['id']}")
check("delete demanded skill -> 409", st == 409, f"status={st}")
st, _ = call("PATCH", f"/support-desk/queues/{qz['id']}", {"skill_ids": []})
check("unwire the lane", st == 200, f"status={st}")

print("\n── cleanup ──")
st, _ = call("DELETE", f"/support-desk/queues/{qz['id']}")
check("delete probe lane", st in (200, 204), f"status={st}")
for sid, label in ((sa["id"], "A"), (sb["id"], "B")):
    st, _ = call("DELETE", f"/support-desk/skills/{sid}")
    check(f"delete probe skill {label}", st == 204, f"status={st}")

print(f"\n{'ALL CHECKS PASSED' if not FAIL else f'{FAIL} CHECK(S) FAILED'}")
sys.exit(1 if FAIL else 0)
