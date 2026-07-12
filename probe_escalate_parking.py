"""Probe — queue↔ticket coherence fixes (2026-07-12).

Fix 4: functional POST /tickets/{id}/escalate with a team target must RE-PARK the
       lane — queue_id moves to the receiving team's own lane (or clears when the
       team has none) so the ticket never lingers on the old team's tier board.
Fix 5: DELETE /queues/{id}?reassign_to= must stamp the target lane's team_id on the
       moved tickets (no queue/team divergence).

Fully deterministic (temp teams + temp lanes, no dependence on live rules — the
probe tickets use ticket_type 'training', which no rule or team request_types
matches). Self-cleaning: tickets → lanes → teams. Run FROM the backend root.
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
import uuid
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
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
conn = psycopg2.connect(user=m.group(1), password=m.group(2), host=m.group(3), port=m.group(4), dbname=m.group(5))
conn.autocommit = True
cur = conn.cursor()
cur.execute("""SELECT id, email, COALESCE(token_version,0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
su_id, su_email, su_tv = cur.fetchone()
print(f"superuser: {su_email}")

token = jwt.encode({"sub": str(su_id), "tv": int(su_tv),
                    "exp": datetime.now(timezone.utc) + timedelta(minutes=20)},
                   env.get("SECRET_KEY", ""), algorithm="HS256")
HDRS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8000/api/support-desk"
STAMP = uuid.uuid4().hex[:6].upper()
fails = []


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=HDRS,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        fails.append(name)


clean = {"tickets": [], "queues": [], "teams": []}
try:
    # ── setup: team T (with its own lane L), team T2 (lane-less) ──
    c, tT = call("POST", "/teams/", {"name": f"[PROBE] ParkCrew {STAMP}", "code": f"PKA{STAMP}"})
    check("create team T", c == 201, c)
    clean["teams"].append(tT["id"])
    c, tT2 = call("POST", "/teams/", {"name": f"[PROBE] NoLaneCrew {STAMP}", "code": f"PKB{STAMP}"})
    check("create team T2 (lane-less)", c == 201, c)
    clean["teams"].append(tT2["id"])
    c, laneL = call("POST", "/queues/", {"name": f"[PROBE] Park Lane {STAMP}", "tier": 2,
                                         "team_id": tT["id"], "auto_assign": False,
                                         "assignment_method": "manual", "queue_priority": 60})
    check("create lane L (team T)", c == 201, c)
    clean["queues"].append(laneL["id"])

    # ── Fix 4a: escalate to a team WITH a lane → ticket parks in that lane ──
    c, tkA = call("POST", "/tickets/", {"subject": f"PARKING PROBE A {STAMP} — safe to ignore",
                                        "description": "probe_escalate_parking.py",
                                        "ticket_type": "training", "priority": "low", "source": "internal"})
    check("ticket A created (unrouted type)", c == 201 and not tkA.get("queue_id"),
          f"queue={tkA.get('queue_id')}")
    clean["tickets"].append(tkA["id"])
    c, _ = call("POST", f"/tickets/{tkA['id']}/assign", {"assigned_agent_id": str(su_id)})
    check("ticket A assigned (escalate precondition)", c == 200, c)
    c, escA = call("POST", f"/tickets/{tkA['id']}/escalate",
                   {"team_id": tT["id"], "reason": "probe parking check", "reason_code": "complexity"})
    okA = c == 200 and str(escA.get("team_id")) == str(tT["id"]) \
        and str(escA.get("queue_id")) == str(laneL["id"])
    check("escalate → team T moves BOTH team_id AND queue_id (parks in lane L)", okA,
          f"team={escA.get('team_id') if c == 200 else escA} queue={escA.get('queue_id') if c == 200 else ''}")
    cur.execute("""SELECT count(*) FROM support_ticket_activities
                   WHERE ticket_id = %s AND action = 'routed' AND detail->>'by' = 'escalation'""",
                (tkA["id"],))
    check("'routed · by escalation' activity written", cur.fetchone()[0] == 1)

    # ── Fix 4b: escalate to a LANE-LESS team → queue_id clears (old lane released) ──
    c, tkB = call("POST", "/tickets/", {"subject": f"PARKING PROBE B {STAMP} — safe to ignore",
                                        "description": "probe_escalate_parking.py",
                                        "ticket_type": "training", "priority": "low", "source": "internal",
                                        "queue_id": laneL["id"]})   # superuser pin — starts ON a lane
    check("ticket B created pinned to lane L", c == 201 and str(tkB.get("queue_id")) == str(laneL["id"]),
          f"queue={tkB.get('queue_id')}")
    clean["tickets"].append(tkB["id"])
    c, _ = call("POST", f"/tickets/{tkB['id']}/assign", {"assigned_agent_id": str(su_id)})
    check("ticket B assigned", c == 200, c)
    c, escB = call("POST", f"/tickets/{tkB['id']}/escalate",
                   {"team_id": tT2["id"], "reason": "probe no-lane check", "reason_code": "complexity"})
    okB = c == 200 and str(escB.get("team_id")) == str(tT2["id"]) and not escB.get("queue_id")
    check("escalate → lane-less team T2 CLEARS queue_id (not stranded on old lane)", okB,
          f"team={escB.get('team_id') if c == 200 else escB} queue={escB.get('queue_id') if c == 200 else ''}")

    # ── Fix 5: queue delete with reassign_to stamps the target lane's team ──
    c, laneX = call("POST", "/queues/", {"name": f"[PROBE] Doomed Lane {STAMP}", "tier": 1,
                                         "team_id": tT["id"], "auto_assign": False,
                                         "assignment_method": "manual"})
    check("create lane X (team T)", c == 201, c)
    clean["queues"].append(laneX["id"])
    c, laneY = call("POST", "/queues/", {"name": f"[PROBE] Inherit Lane {STAMP}", "tier": 1,
                                         "team_id": tT2["id"], "auto_assign": False,
                                         "assignment_method": "manual"})
    check("create lane Y (team T2)", c == 201, c)
    clean["queues"].append(laneY["id"])
    c, tkC = call("POST", "/tickets/", {"subject": f"PARKING PROBE C {STAMP} — safe to ignore",
                                        "description": "probe_escalate_parking.py",
                                        "ticket_type": "training", "priority": "low", "source": "internal",
                                        "queue_id": laneX["id"]})
    check("ticket C created in lane X", c == 201 and str(tkC.get("queue_id")) == str(laneX["id"]),
          f"queue={tkC.get('queue_id')}")
    clean["tickets"].append(tkC["id"])
    c, _ = call("DELETE", f"/queues/{laneX['id']}?reassign_to={laneY['id']}")
    check("delete lane X reassigning to Y", c == 204, c)
    if c == 204:
        clean["queues"].remove(laneX["id"])
    c, tkC2 = call("GET", f"/tickets/{tkC['id']}")
    okC = c == 200 and str(tkC2.get("queue_id")) == str(laneY["id"]) \
        and str(tkC2.get("team_id")) == str(tT2["id"])
    check("moved ticket carries lane Y AND team T2 (no queue/team divergence)", okC,
          f"queue={tkC2.get('queue_id') if c == 200 else tkC2} team={tkC2.get('team_id') if c == 200 else ''}")
    # write_audit namespaces: action='support.queue.tickets_reassigned', details = JSON text.
    cur.execute("""SELECT details FROM audit_logs
                   WHERE action = 'support.queue.tickets_reassigned' AND entity_id = %s
                   ORDER BY created_at DESC LIMIT 1""", (uuid.UUID(laneX["id"]),))
    row = cur.fetchone()
    det = json.loads(row[0]) if row and row[0] else {}
    check("reassign audit records to_team", det.get("to_team") == str(tT2["id"]),
          det if row else "no audit row")
finally:
    for tid in clean["tickets"]:
        c, _ = call("DELETE", f"/tickets/{tid}")
        print(f"cleanup ticket {tid}: {c}")
    for qid in clean["queues"]:
        c, _ = call("DELETE", f"/queues/{qid}")
        print(f"cleanup queue {qid}: {c}")
    for tmid in clean["teams"]:
        c, _ = call("DELETE", f"/teams/{tmid}")
        print(f"cleanup team {tmid}: {c}")
    conn.close()

print("\n" + ("ALL PARKING CHECKS PASSED ✔" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
