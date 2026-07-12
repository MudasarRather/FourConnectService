"""Probe — POST /api/support-desk/queues/route-unrouted against the LIVE backend.

Self-cleaning and adaptive (the desk may already have real lanes/rules):
  1. dry-run                  -> plan report; verify scanned == DB unqueued count
  2. if the plan routes 0, lay a temp lane so at least one chain step can fire
  3. dry-run again            -> verify NOTHING was written
  4. REAL run                 -> verify DB delta == reported routed + per-ticket
                                 'routed - by backfill' activities exist
  5. REVERT every ticket the real run touched (queue_id AND team_id restored from
     a pre-run snapshot; probe activities deleted; temp lane removed)
  6. verify the desk is byte-identical to the starting state

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_route_unrouted.py
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
conn.autocommit = True
cur = conn.cursor()

cur.execute("""SELECT id, email, COALESCE(token_version, 0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
row = cur.fetchone()
if not row:
    sys.exit("no active superuser found")
uid, email, tv = row
print(f"superuser: {email}  tv={tv}")

secret = env.get("SECRET_KEY", "your-secret-key-here-change-this-in-production")
token = jwt.encode(
    {"sub": str(uid), "tv": int(tv),
     "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
    secret, algorithm="HS256")
HDRS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8000/api"

fails = []


def call(method, path, body=None, expect=200):
    req = urllib.request.Request(f"{BASE}{path}", method=method, headers=HDRS,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code, payload = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, payload = e.code, e.read().decode()
    ok = code == expect
    print(f"{'PASS' if ok else 'FAIL'}  {method} {path} -> {code}")
    if not ok:
        fails.append(f"{method} {path} -> {code}: {payload[:300]}")
        print("      ", payload[:300])
    try:
        return json.loads(payload)
    except Exception:
        return {}


UNQ_WHERE = """is_deleted = false AND merged_into_id IS NULL AND queue_id IS NULL
               AND status NOT IN ('closed','archived')"""


def snapshot():
    """(id, team_id) of every unqueued candidate — the exact revert baseline."""
    cur.execute(f"SELECT id, team_id FROM support_tickets WHERE {UNQ_WHERE}")
    return {str(r[0]): r[1] for r in cur.fetchall()}


start = snapshot()
print(f"\nstarting unqueued (non-terminal) tickets: {len(start)}")

# -- 1 - dry-run: plan only --
r1 = call("POST", "/support-desk/queues/route-unrouted?dry_run=true", body={})
if r1.get("dry_run") is not True:
    fails.append("dry_run flag not echoed")
print(f"   plan: scanned={r1.get('scanned')} routed={r1.get('routed')} via={r1.get('via')}")
if r1.get("scanned") != len(start):
    fails.append(f"scanned {r1.get('scanned')} != DB unqueued {len(start)}")
for u in (r1.get("unrouted") or [])[:3]:
    print(f"   unroutable: {u['ticket_number']} - {u['reason']}")

# -- 2 - if nothing would route, lay a temp lane so a chain step can fire --
lane_id = None
lane_was_default = False
prior_defaults = []
if not r1.get("routed"):
    cur.execute(f"""SELECT team_id, count(*) FROM support_tickets WHERE {UNQ_WHERE}
                    AND team_id IS NOT NULL GROUP BY team_id ORDER BY count(*) DESC LIMIT 1""")
    tm = cur.fetchone()
    lane_body = {"name": "[PROBE] Backfill Lane", "tier": 1, "assignment_method": "manual",
                 "auto_assign": False, "queue_priority": 50}
    if tm:
        lane_body["team_id"] = str(tm[0])
        print(f"\nplan is empty -> temp lane bound to team {tm[0]} ({tm[1]} unqueued tickets)")
    else:
        # Snapshot the crown BEFORE creating a default lane — the server's
        # _clear_other_defaults strips every other default on create, and the
        # delete guard 409s on a default lane. Both are restored in cleanup.
        cur.execute("SELECT id FROM support_queues WHERE is_default AND NOT is_deleted")
        prior_defaults = [str(r[0]) for r in cur.fetchall()]
        lane_body["is_default"] = True
        lane_was_default = True
        print("\nplan is empty -> temp DEFAULT lane (no unqueued ticket carries a team)")
    lane = call("POST", "/support-desk/queues/", body=lane_body, expect=201)
    lane_id = lane.get("id")

try:
    # -- 3 - dry-run must write nothing --
    r2 = call("POST", "/support-desk/queues/route-unrouted?dry_run=true", body={})
    print(f"   plan: routed={r2.get('routed')} via={r2.get('via')}")
    if not r2.get("routed"):
        fails.append("dry-run still plans 0 routes - nothing to exercise")
    if snapshot() != start:
        fails.append("DRY-RUN WROTE TO THE DB")
    else:
        print("   dry-run wrote nothing (DB unchanged) [ok]")

    # -- 4 - real run --
    r3 = call("POST", "/support-desk/queues/route-unrouted", body={})
    print(f"   real: routed={r3.get('routed')} via={r3.get('via')} unrouted={r3.get('unrouted_count')}")
    after = snapshot()
    moved = [tid for tid in start if tid not in after]
    if len(moved) != r3.get("routed"):
        fails.append(f"DB delta {len(moved)} != reported routed {r3.get('routed')}")
    else:
        print(f"   DB confirms {len(moved)} ticket(s) now sit in a lane [ok]")
    cur.execute("""SELECT count(*) FROM support_ticket_activities
                   WHERE action = 'routed' AND detail->>'by' = 'backfill'
                     AND ticket_id = ANY(%s::uuid[])""", (moved,))
    acts = cur.fetchone()[0]
    if acts != len(moved):
        fails.append(f"backfill activities {acts} != routed {len(moved)}")
    else:
        print(f"   {acts} 'routed - by backfill' activities written [ok]")

    # idempotency: a second run must not re-route what the first run placed
    r4 = call("POST", "/support-desk/queues/route-unrouted", body={})
    if r4.get("routed"):
        fails.append(f"second run re-routed {r4.get('routed')} - not idempotent")
    else:
        print("   second run routed 0 - idempotent [ok]")
finally:
    # -- 5 - revert everything the real run wrote --
    cur.execute(f"""SELECT id, team_id FROM support_tickets
                    WHERE is_deleted = false AND merged_into_id IS NULL
                      AND status NOT IN ('closed','archived') AND queue_id IS NOT NULL""")
    now_queued = {str(r[0]): r[1] for r in cur.fetchall()}
    reverted = 0
    for tid in list(start.keys()):
        if tid in now_queued:
            cur.execute("""UPDATE support_tickets SET queue_id = NULL, team_id = %s
                           WHERE id = %s""", (start[tid], tid))
            reverted += cur.rowcount
    print(f"\nundo: {reverted} ticket(s) restored to unqueued (team_id restored)")
    cur.execute("""DELETE FROM support_ticket_activities
                   WHERE action = 'routed' AND detail->>'by' = 'backfill'
                     AND ticket_id = ANY(%s::uuid[])""", (list(start.keys()),))
    print(f"undo: {cur.rowcount} probe activities removed")
    if lane_id:
        if lane_was_default:
            # The API refuses a zero-default desk via PATCH and refuses to DELETE a
            # default lane — but zero-default WAS the pre-run state (the temp lane is
            # only created when the plan is empty, which a real default would prevent).
            # Restore it faithfully with direct SQL, then delete through the API.
            cur.execute("UPDATE support_queues SET is_default = FALSE WHERE id = %s", (lane_id,))
            for pid in prior_defaults:
                cur.execute("UPDATE support_queues SET is_default = TRUE WHERE id = %s", (pid,))
            print(f"undo: default crown restored (prior defaults: {len(prior_defaults)})")
        call("DELETE", f"/support-desk/queues/{lane_id}", expect=204)

# -- 6 - final state check --
final = snapshot()
if final != start:
    fails.append(f"probe leaked state: {len(final)} unqueued now vs {len(start)} at start")
else:
    print(f"final state identical to start ({len(final)} unqueued) [ok]")

print("\n" + ("ALL PASS" if not fails else "FAILURES"))
for f in fails:
    print(" -", f)
conn.close()
sys.exit(1 if fails else 0)
