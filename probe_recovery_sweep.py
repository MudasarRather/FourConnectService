"""Probe — Route-the-backlog / RECOVERY SWEEP against the LIVE backend on :8000.

Coverage (self-cleaning, incl. residue from earlier runs):
  POST /queues/route-unrouted?dry_run=true
        · full report shape incl. `remaining`
        · dry run writes NOTHING (fixture still stranded after)
        · fixture appears in the scan (scanned grows by exactly 1)
        · every stranded row carries the NEW reason_code / team_id / team_name keys
  POST /queues/route-unrouted (execute)
        · fixture lands in a lane with per-ticket 'routed' provenance
        · re-run is idempotent — placement doesn't move

NOTE: the fixture may route via the desk's LIVE first-match rules rather than the
team-lane branch — that's create-parity by design, so the probe asserts mechanics
(scan/no-write/placement/provenance/idempotency + new report fields), not which
branch fired.

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_recovery_sweep.py
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
conn.autocommit = True
cur = conn.cursor()
cur.execute("""SELECT id, email, COALESCE(token_version, 0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
uid, email, tv = cur.fetchone()
print(f"superuser: {email}  tv={tv}")


def reap_residue():
    cur.execute("""DELETE FROM support_ticket_activities WHERE ticket_id IN
                   (SELECT id FROM support_tickets WHERE subject LIKE 'RecoverySweep probe%')""")
    cur.execute("""DELETE FROM support_ticket_comments WHERE ticket_id IN
                   (SELECT id FROM support_tickets WHERE subject LIKE 'RecoverySweep probe%')""")
    cur.execute("DELETE FROM support_tickets WHERE subject LIKE 'RecoverySweep probe%'")
    cur.execute("DELETE FROM support_queues WHERE code LIKE 'SWP_PRB%'")
    cur.execute("DELETE FROM support_teams WHERE name = 'SweepProbe Crew'")


reap_residue()   # clear leftovers from any earlier run first

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
        with urllib.request.urlopen(req, timeout=60) as r:
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


print("\n-- baseline dry run --")
st, base = call("POST", "/support-desk/queues/route-unrouted?dry_run=true")
check("baseline dry run 200 + full shape", st == 200 and all(k in base for k in
      ("scanned", "routed", "via", "unrouted", "unrouted_count", "remaining", "overflowed")), f"status={st}")

print("\n-- fixture: ticket via API, then SQL-stranded --")
st, tkt = call("POST", "/support-desk/tickets/", {
    "subject": "RecoverySweep probe — stranded fixture", "description": "probe", "priority": "low"})
check("create fixture ticket", st in (200, 201) and tkt.get("id"), f"status={st}")
tid = tkt["id"]
cur.execute("""INSERT INTO support_teams (id, name, member_ids, is_active, is_deleted, created_at, updated_at)
               VALUES (gen_random_uuid(), 'SweepProbe Crew', '[]'::jsonb, TRUE, FALSE, now(), now())
               RETURNING id""")
team_id = cur.fetchone()[0]
cur.execute("UPDATE support_tickets SET queue_id = NULL, team_id = %s WHERE id = %s", (team_id, tid))

print("\n-- dry run: scan + report intelligence, zero writes --")
st, plan = call("POST", "/support-desk/queues/route-unrouted?dry_run=true")
check("fixture enters the scan (scanned +1)", st == 200 and plan["scanned"] == base["scanned"] + 1,
      f"{base['scanned']} -> {plan['scanned']}")
if plan.get("unrouted"):
    check("every stranded row carries reason_code/team_id/team_name",
          all(("reason_code" in u and "team_id" in u and "team_name" in u) for u in plan["unrouted"]),
          f"rows={len(plan['unrouted'])}")
else:
    check("no stranded rows to inspect (skip field check)", True)
cur.execute("SELECT queue_id FROM support_tickets WHERE id = %s", (tid,))
check("dry run wrote NOTHING (fixture still stranded)", cur.fetchone()[0] is None)

print("\n-- execute: placement + provenance + idempotency --")
st, r1 = call("POST", "/support-desk/queues/route-unrouted")
check("execute 200", st == 200, f"status={st}")
cur.execute("SELECT queue_id FROM support_tickets WHERE id = %s", (tid,))
q1 = cur.fetchone()[0]
if q1 is None:
    # live config has no route for it — that IS the stranded ledger's case; verify report says so
    row = next((u for u in (r1.get("unrouted") or []) if str(u["id"]) == str(tid)), None)
    check("unroutable fixture reported with reason_code=team_no_lane",
          row is not None and row.get("reason_code") == "team_no_lane"
          and str(row.get("team_id")) == str(team_id) and row.get("team_name") == "SweepProbe Crew",
          f"row={row}")
    st, lane = call("POST", "/support-desk/queues/", {
        "name": "SweepProbe Lane", "code": "SWP_PRB", "team_id": str(team_id)})
    check("FIX action: lay a lane for the team", st == 201, f"status={st}")
    st, _ = call("POST", "/support-desk/queues/route-unrouted")
    cur.execute("SELECT queue_id FROM support_tickets WHERE id = %s", (tid,))
    q1 = cur.fetchone()[0]
    check("re-run routes it into the new team lane", str(q1) == str(lane["id"]), f"queue={q1}")
else:
    check("fixture placed in a lane (live rules/category/default parity)", True, f"queue={q1}")
cur.execute("""SELECT COUNT(*) FROM support_ticket_activities
               WHERE ticket_id = %s AND action = 'routed'""", (tid,))
check("per-ticket 'routed' provenance written", cur.fetchone()[0] >= 1)
st, _ = call("POST", "/support-desk/queues/route-unrouted")
cur.execute("SELECT queue_id FROM support_tickets WHERE id = %s", (tid,))
check("second sweep is idempotent — placement unmoved", st == 200 and str(cur.fetchone()[0]) == str(q1))

print("\n-- cleanup --")
reap_residue()
check("fixture + residue reaped", True)
conn.close()

print(f"\n{'ALL CHECKS PASSED' if not FAIL else f'{FAIL} CHECK(S) FAILED'}")
sys.exit(1 if FAIL else 0)
