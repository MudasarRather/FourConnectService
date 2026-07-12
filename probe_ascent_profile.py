"""Probe — Escalation / ASCENT PROFILE workflow against the LIVE backend on :8000.

Coverage (self-cleaning):
  POST   /api/support-desk/automation-rules/   — time_based policy w/ threshold + escalate_tier
  PATCH  /api/support-desk/automation-rules/{id} — is_active flip (the manifest ARM switch)
  GET    /api/support-desk/automation-rules/   — last_run_at/run_count/time_threshold_mins present
  DELETE /api/support-desk/automation-rules/{id}

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_ascent_profile.py
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

m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", env.get("DATABASE_URL", ""))
if not m:
    sys.exit("DATABASE_URL not parseable")
user, pwd, host, port, dbname = m.groups()
conn = psycopg2.connect(user=user, password=pwd, host=host, port=port, dbname=dbname)
cur = conn.cursor()
cur.execute("""SELECT id, email, COALESCE(token_version, 0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
uid, email, tv = cur.fetchone()
conn.close()
print(f"superuser: {email}  tv={tv}")

token = jwt.encode({"sub": str(uid), "tv": int(tv),
                    "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
                   env.get("SECRET_KEY", ""), algorithm="HS256")
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


print("\n-- time-based burn: create / arm-switch / list fields / delete --")
st, r = call("POST", "/support-desk/automation-rules/", {
    "name": "AscentProbe Burn", "trigger": "time_based", "time_threshold_mins": 240,
    "match_type": "all",
    "conditions": [{"field": "priority", "op": "in", "value": ["critical", "urgent"]}],
    "actions": [{"type": "escalate_tier", "value": "2"}]})
check("create time_based policy", st == 201 and r.get("trigger") == "time_based", f"status={st}")
check("threshold round-trips", r.get("time_threshold_mins") == 240)

st, r2 = call("PATCH", f"/support-desk/automation-rules/{r['id']}", {"is_active": False})
check("ARM switch -> STANDBY (is_active false)", st == 200 and r2.get("is_active") is False, f"status={st}")
st, r3 = call("PATCH", f"/support-desk/automation-rules/{r['id']}", {"is_active": True})
check("ARM switch -> ARMED (is_active true)", st == 200 and r3.get("is_active") is True, f"status={st}")

st, listed = call("GET", "/support-desk/automation-rules/")
row = next((x for x in (listed or []) if str(x["id"]) == str(r["id"])), None)
check("listed with run_count + last_run_at fields", row is not None
      and "run_count" in row and "last_run_at" in row, f"status={st}")

st, _ = call("DELETE", f"/support-desk/automation-rules/{r['id']}")
check("delete probe policy", st in (200, 204), f"status={st}")

print(f"\n{'ALL CHECKS PASSED' if not FAIL else f'{FAIL} CHECK(S) FAILED'}")
sys.exit(1 if FAIL else 0)
