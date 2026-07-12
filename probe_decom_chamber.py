"""Probe — Rule delete / DECOMMISSION CHAMBER against the LIVE backend on :8000.

Coverage (self-cleaning):
  DELETE /automation-rules/{id}?reason=...  — 204; reason lands in the audit row
  DELETE without reason                      — still 204 (reason optional server-side)
  PATCH  is_active=false ("Park instead")    — round-trip
  config-ledger                              — the deletion's audit entry carries details.reason

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_decom_chamber.py
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
import urllib.parse
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


print("\n-- park instead (the reversible alternative) --")
st, r1 = call("POST", "/support-desk/automation-rules/", {
    "name": "DecomProbe Park", "conditions": [], "actions": [], "is_active": True})
check("create probe rule A", st == 201, f"status={st}")
st, parked = call("PATCH", f"/support-desk/automation-rules/{r1['id']}", {"is_active": False})
check("park (is_active=false) round-trip", st == 200 and parked.get("is_active") is False, f"status={st}")

print("\n-- decommission WITH a reason --")
reason = "Was misrouting traffic — probe verdict"
st, _ = call("DELETE", f"/support-desk/automation-rules/{r1['id']}?reason=" + urllib.parse.quote(reason))
check("delete with reason -> 204", st == 204, f"status={st}")
cur.execute("""SELECT details FROM audit_logs
               WHERE entity_type = 'support.rule' AND entity_id = %s
               ORDER BY created_at DESC LIMIT 1""", (r1["id"],))
row = cur.fetchone()
det = row[0] if row else None
if isinstance(det, str):
    det = json.loads(det)
check("audit row carries the verdict", det is not None and det.get("reason") == reason,
      f"details={det}")

print("\n-- decommission WITHOUT a reason (still legal server-side) --")
st, r2 = call("POST", "/support-desk/automation-rules/", {
    "name": "DecomProbe Bare", "conditions": [], "actions": []})
check("create probe rule B", st == 201, f"status={st}")
st, _ = call("DELETE", f"/support-desk/automation-rules/{r2['id']}")
check("bare delete -> 204", st == 204, f"status={st}")

print("\n-- version history survives --")
st, revs = call("GET", f"/support-desk/automation-rules/{r1['id']}/revisions")
check("revisions retrievable post-delete (created→parked→deleted)",
      st == 200 and len(revs or []) >= 3 and any(x.get("action") == "deleted" for x in revs),
      f"n={len(revs or [])}")

print("\n-- cleanup (probe rules are soft-deleted already; reap rows fully) --")
for rid in (r1["id"], r2["id"]):
    cur.execute("DELETE FROM support_rule_revisions WHERE rule_id = %s", (rid,))
    cur.execute("DELETE FROM support_automation_rules WHERE id = %s", (rid,))
cur.execute("""DELETE FROM audit_logs WHERE entity_type = 'support.rule'
               AND entity_id IN (%s, %s)""", (r1["id"], r2["id"]))
check("reaped", True)
conn.close()

print(f"\n{'ALL CHECKS PASSED' if not FAIL else f'{FAIL} CHECK(S) FAILED'}")
sys.exit(1 if FAIL else 0)
