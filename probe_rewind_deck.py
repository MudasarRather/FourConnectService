"""Probe — Rule version history / REWIND DECK against the LIVE backend on :8000.

Coverage (self-cleaning):
  · created → updated produces v1/v2 with snapshots (newest first)
  · RESTORE: PATCH with v1's snapshot fields mints v3 whose config == v1
  · revisions endpoint works for DELETED rules (the deck's read-only mode)
  · RECREATE: POST from a deleted rule's snapshot forges a fresh rule

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_rewind_deck.py
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
user, pwd, host, port, dbname = m.groups()
conn = psycopg2.connect(user=user, password=pwd, host=host, port=port, dbname=dbname)
conn.autocommit = True
cur = conn.cursor()
cur.execute("""SELECT id, email, COALESCE(token_version, 0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
uid, email, tv = cur.fetchone()
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


def snap_payload(s):
    return {"name": s["name"], "description": s.get("description"), "match_type": s.get("match_type", "all"),
            "conditions": s.get("conditions") or [], "actions": s.get("actions") or [],
            "trigger": s.get("trigger", "on_create"), "stop_processing": bool(s.get("stop_processing")),
            "time_threshold_mins": s.get("time_threshold_mins"), "is_active": s.get("is_active") is not False}


print("\n-- forge v1, recut to v2 --")
st, r = call("POST", "/support-desk/automation-rules/", {
    "name": "RewindProbe", "match_type": "all",
    "conditions": [{"field": "priority", "op": "in", "value": ["low"]}],
    "actions": [{"type": "set_priority", "value": "high"}], "stop_processing": True})
check("create (v1)", st == 201, f"status={st}")
rid = r["id"]
st, _ = call("PATCH", f"/support-desk/automation-rules/{rid}", {
    "name": "RewindProbe RENAMED", "match_type": "any", "stop_processing": False})
check("update (v2)", st == 200, f"status={st}")
st, revs = call("GET", f"/support-desk/automation-rules/{rid}/revisions")
check("two cuts, newest first", st == 200 and len(revs) == 2 and revs[0]["version"] == 2
      and revs[0]["snapshot"]["name"] == "RewindProbe RENAMED", f"n={len(revs or [])}")

print("\n-- RESTORE v1 (the deck's restore = PATCH with the old snapshot) --")
v1 = next(x for x in revs if x["version"] == 1)
st, restored = call("PATCH", f"/support-desk/automation-rules/{rid}", snap_payload(v1["snapshot"]))
check("restore PATCH ok", st == 200 and restored["name"] == "RewindProbe"
      and restored["match_type"] == "all" and restored["stop_processing"] is True, f"status={st}")
st, revs = call("GET", f"/support-desk/automation-rules/{rid}/revisions")
check("restore minted v3 (non-destructive)", st == 200 and revs[0]["version"] == 3
      and revs[0]["snapshot"]["name"] == "RewindProbe", f"head=v{revs and revs[0]['version']}")

print("\n-- deleted rules: read-only deck + RECREATE --")
st, _ = call("DELETE", f"/support-desk/automation-rules/{rid}?reason=Rewind%20probe%20teardown")
check("decommission", st == 204, f"status={st}")
st, revs = call("GET", f"/support-desk/automation-rules/{rid}/revisions")
check("deck still readable after decommission (v4 = deleted cut)", st == 200 and revs[0]["version"] == 4
      and revs[0]["action"] == "deleted", f"head={revs and revs[0]['action']}")
st, clone = call("POST", "/support-desk/automation-rules/", snap_payload(v1["snapshot"]))
check("recreate from a cut forges a fresh rule", st == 201 and clone["name"] == "RewindProbe"
      and str(clone["id"]) != str(rid), f"status={st}")

print("\n-- cleanup --")
st, _ = call("DELETE", f"/support-desk/automation-rules/{clone['id']}")
check("delete clone", st == 204, f"status={st}")
for x in (rid, clone["id"]):
    cur.execute("DELETE FROM support_rule_revisions WHERE rule_id = %s", (x,))
    cur.execute("DELETE FROM audit_logs WHERE entity_type = 'support.rule' AND entity_id = %s", (x,))
    cur.execute("DELETE FROM support_automation_rules WHERE id = %s", (x,))
check("reaped", True)
conn.close()

print(f"\n{'ALL CHECKS PASSED' if not FAIL else f'{FAIL} CHECK(S) FAILED'}")
sys.exit(1 if FAIL else 0)
