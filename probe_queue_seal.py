"""Seal probe — Queue Engine viewed by a NON-superuser support agent.

Asserts: overview only shows the agent's teams' queues; config mutations
(queue create, rule create/reorder, skill create) are 403; the working
surfaces (tier board, skills list, agent-status, my status) stay usable;
GET /queues/ hides foreign lanes' live open counts; an explicit foreign
queue_id on ticket create is DROPPED (auto-routing decides); /teams/people
is a search (2+ chars, capped 25) for agents — never a directory dump.

Run FROM the backend root.
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
cur = conn.cursor()
cur.execute("""SELECT u.id, u.email, COALESCE(u.token_version,0),
                      EXISTS (SELECT 1 FROM hr_employees e
                              WHERE e.reporting_manager_id = u.id AND e.is_deleted = FALSE) AS is_mgr
               FROM users u
               WHERE u.is_support_agent = TRUE AND u.is_superuser = FALSE AND u.is_active = TRUE
               ORDER BY is_mgr ASC LIMIT 1""")
row = cur.fetchone()
if not row:
    sys.exit("no non-superuser agent found — seal probe skipped")
uid, email, tv, agent_is_mgr = row
# The agent's team ids (member or lead) for the seal assertion.
cur.execute("SELECT id, member_ids, lead_user_id FROM support_teams WHERE is_deleted = FALSE AND is_active = TRUE")
my_teams = set()
for tid, members, lead in cur.fetchall():
    ids = {str(x) for x in (members or [])}
    if str(uid) in ids or (lead and str(lead) == str(uid)):
        my_teams.add(str(tid))

# A superuser for the comparison view + ticket cleanup (the agent can't DELETE).
cur.execute("""SELECT id, COALESCE(token_version,0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
su_row = cur.fetchone()

# For the foreign-pin create check: a request type the agent's teams handle (the
# create self-gate requires it) + a foreign lane that NO legitimate routing path
# could pick for that ticket (over-excluded on purpose: rule targets, overflow
# targets, default lane, lanes of every team that handles the chosen type).
my_types, foreign_q, foreign_q_name = [], None, None
try:
    cur.execute("SELECT id, request_types FROM support_teams WHERE is_deleted = FALSE AND is_active = TRUE")
    team_types = {str(r[0]): [str(x) for x in (r[1] or [])] for r in cur.fetchall()}
    my_types = sorted({t for _tid, ts in team_types.items() if _tid in my_teams for t in ts})
    rule_targets = set()
    try:
        cur.execute("SELECT actions FROM support_automation_rules")
        for (actions,) in cur.fetchall():
            for a in (actions or []):
                if isinstance(a, dict) and a.get("type") in ("route_queue", "assign_queue"):
                    rule_targets.add(str(a.get("value")))
    except Exception:
        conn.rollback()
    cur.execute("""SELECT id, team_id, is_default, overflow_queue_id, name FROM support_queues
                   WHERE is_deleted = FALSE AND is_active = TRUE""")
    qrows = cur.fetchall()
    overflow_targets = {str(r[3]) for r in qrows if r[3]}
    handling = {_tid for _tid, ts in team_types.items() if my_types and my_types[0] in ts}
    for qid2, qteam, isdef, _of, qname in qrows:
        if not qteam or str(qteam) in my_teams or isdef:
            continue
        if str(qid2) in rule_targets or str(qid2) in overflow_targets or str(qteam) in handling:
            continue
        foreign_q, foreign_q_name = str(qid2), qname
        break
except Exception:
    conn.rollback()
conn.close()
print(f"agent: {email} · teams: {len(my_teams)}")

token = jwt.encode({"sub": str(uid), "tv": int(tv),
                    "exp": datetime.now(timezone.utc) + timedelta(minutes=15)},
                   env.get("SECRET_KEY", ""), algorithm="HS256")
HDRS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
ADMIN_HDRS = None
if su_row:
    su_token = jwt.encode({"sub": str(su_row[0]), "tv": int(su_row[1]),
                           "exp": datetime.now(timezone.utc) + timedelta(minutes=15)},
                          env.get("SECRET_KEY", ""), algorithm="HS256")
    ADMIN_HDRS = {"Authorization": f"Bearer {su_token}", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8000/api/support-desk"
fails = []


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method, headers=headers or HDRS,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        fails.append(name)


c, ov = call("GET", "/queues/overview")
sealed = c == 200 and all((str(q.get("team_id")) in my_teams) for q in ov.get("queues", []))
check("overview sealed to my teams' queues", sealed,
      f"{len(ov.get('queues', []))} queues visible" if c == 200 else ov)

c, board = call("GET", "/queues/tier/1/board")
check("tier board readable (sealed)", c == 200)

c, _ = call("POST", "/queues/", {"name": "SEAL PROBE"})
check("queue create blocked for agent (403)", c == 403, c)
c, _ = call("POST", "/automation-rules/", {"name": "SEAL PROBE", "conditions": [], "actions": []})
check("rule create blocked for agent (403)", c == 403, c)
c, _ = call("PATCH", "/automation-rules/reorder", {"order": [{"id": "00000000-0000-0000-0000-000000000000", "order_index": 1}]})
check("rule reorder blocked for agent (403)", c == 403, c)
c, _ = call("POST", "/skills/", {"name": "SEAL PROBE"})
check("skill create blocked for agent (403)", c == 403, c)

c, sk = call("GET", "/skills/")
check("skills list readable for agent", c == 200)
c, st = call("GET", "/agent-status")
check("agent-status roster readable (sealed)", c == 200)
c, me = call("PUT", "/me/status", {"status": "online"})
check("agent can set own status", c == 200)

# ── queue LIST seal: rows stay desk-wide (escalation targets) but foreign lanes
#    must read open_ticket_count = 0 for a non-superuser ──
c, qlist = call("GET", "/queues/")
ok = c == 200 and isinstance(qlist, list)
foreign_zero = ok and all(int(q.get("open_ticket_count") or 0) == 0
                          for q in qlist if str(q.get("team_id")) not in my_teams)
check("queue list: foreign lanes read open_ticket_count=0", foreign_zero,
      f"{len(qlist)} lanes" if ok else (c, qlist))
if ok and ADMIN_HDRS:
    c2, sulist = call("GET", "/queues/", headers=ADMIN_HDRS)
    nonvac = c2 == 200 and any(int(q.get("open_ticket_count") or 0) > 0
                               and str(q.get("team_id")) not in my_teams for q in sulist)
    print(f"      (non-vacuous: admin view shows {'≥1' if nonvac else 'no'} foreign lane with open work)")

# ── /teams/people: a SEARCH for agents, never a directory dump ──
c, _ = call("GET", "/teams/people")
check("people directory dump blocked for agent (422)", c == 422, c)
c, ppl = call("GET", "/teams/people?q=an&limit=500")
check("people search allowed for agent, capped at 25",
      c == 200 and isinstance(ppl, list) and len(ppl) <= 25,
      f"{len(ppl) if isinstance(ppl, list) else ppl} rows (asked 500)")

# ── ticket create: an explicit FOREIGN queue_id must be dropped (routing decides) ──
# No natural candidate (every foreign lane is a rule/overflow target on this DB)?
# Lay a TEMP team-less lane as admin — a plain agent may not pin that either, and
# no routing path can legitimately land the probe ticket in it. Self-cleaning.
temp_lane = None
if not foreign_q and my_types and ADMIN_HDRS and not agent_is_mgr:
    c, tq = call("POST", "/queues/", {"name": "SEAL PROBE FOREIGN LANE", "tier": 3,
                                      "queue_priority": 1, "auto_assign": False}, headers=ADMIN_HDRS)
    if c == 201 and isinstance(tq, dict):
        temp_lane = tq["id"]
        foreign_q, foreign_q_name = tq["id"], tq["name"]
if agent_is_mgr:
    print("SKIP  foreign-pin create check (probe agent is a reporting manager — pinning is allowed for them)")
elif foreign_q and my_types:
    c, t = call("POST", "/tickets/", {
        "subject": "SEAL-PROBE foreign lane pin — safe to ignore",
        "description": "Automated seal probe (probe_queue_seal.py).",
        "ticket_type": my_types[0], "priority": "low", "source": "internal",
        "queue_id": foreign_q})
    if c == 201 and isinstance(t, dict):
        pinned = str(t.get("queue_id") or "") == foreign_q
        check("create: foreign queue_id pin dropped (auto-routing decided)", not pinned,
              f"pinned '{foreign_q_name}'" if pinned else f"landed queue={t.get('queue_id')}")
        if ADMIN_HDRS:
            cd, _ = call("DELETE", f"/tickets/{t['id']}", headers=ADMIN_HDRS)
            print(f"      cleanup probe ticket: {cd}")
    else:
        check("create with foreign queue_id (needs 201 to assert)", False, (c, t))
else:
    print(f"SKIP  foreign-pin create check (foreign lane: {foreign_q_name or '—'} · handled types: {len(my_types)})")
if temp_lane and ADMIN_HDRS:
    cd, _ = call("DELETE", f"/queues/{temp_lane}", headers=ADMIN_HDRS)
    print(f"      cleanup temp lane: {cd}")

print("\n" + ("SEAL CHECKS PASSED ✔" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
