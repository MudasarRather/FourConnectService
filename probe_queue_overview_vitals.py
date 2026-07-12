"""Probe — Queues Overview "Vitals Bay" telemetry (additive analytics block)
against the LIVE backend on :8000.

Coverage:
  HTTP GET /api/support-desk/queues/overview?days=7
       — new keys present: flow_interval, deltas, aging, sla_split, burn,
         utilization, breach_horizon, reopens_range
       — per-card additions: aging, burn_rate_hr, drain_eta_mins, crew_capacity,
         load_pct, reopens_range
       — fleet aging == sum of card agings
       — breach_horizon items are unbreached, due_in_seconds >= 0, sorted ascending
  HTTP GET ?days=1&flow_interval=hour   — hourly flow honoured (<= 24 buckets)
  HTTP GET ?days=7&flow_interval=hour   — falls back to 'day' (only days<=2 hourly)
  HTTP GET as a TEAM-SEALED agent       — 200, queues subset of superuser's set
                                          (skipped when no non-superuser agent exists)

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_queue_overview_vitals.py
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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import urllib.error
import urllib.request
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
su_id, su_email, su_tv = row
print(f"superuser: {su_email}  tv={su_tv}")

# A team-sealed agent (non-superuser support agent who is on at least one team).
cur.execute("""
    SELECT u.id, u.email, COALESCE(u.token_version, 0)
    FROM users u
    WHERE u.is_superuser = FALSE AND u.is_active = TRUE
      AND COALESCE(u.is_support_agent, FALSE) = TRUE
      AND EXISTS (SELECT 1 FROM support_teams t
                  WHERE t.is_deleted = FALSE
                    AND (t.member_ids)::jsonb ? u.id::text)
    LIMIT 1""")
agent_row = cur.fetchone()
conn.close()

secret = env.get("SECRET_KEY", "your-secret-key-here-change-this-in-production")


def token_for(uid, tv):
    return jwt.encode(
        {"sub": str(uid), "tv": int(tv),
         "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
        secret, algorithm="HS256")


BASE = "http://127.0.0.1:8000/api"
FAIL = 0


def call(path, tok):
    req = urllib.request.Request(BASE + path, method="GET",
                                 headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
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


su_tok = token_for(su_id, su_tv)

print("\n── GET /queues/overview?days=7 (superuser) ──")
st, ov = call("/support-desk/queues/overview?days=7", su_tok)
check("status 200", st == 200, f"status={st}")
if st != 200 or ov is None:
    sys.exit("overview not reachable — is the backend on :8000 serving the new code?")

for key in ("flow_interval", "deltas", "aging", "sla_split", "burn",
            "utilization", "breach_horizon", "reopens_range"):
    check(f"key '{key}' present", key in ov)
check("flow_interval == 'day'", ov.get("flow_interval") == "day")
check("deltas has inflow/outflow/reopens/sla_*",
      all(k in ov.get("deltas", {}) for k in
          ("inflow", "outflow", "reopens", "sla_resolution", "sla_response")))
check("aging has all 5 buckets",
      all(k in ov.get("aging", {}) for k in ("lt_1h", "h1_4", "h4_24", "d1_3", "gt_3d")))
check("sla_split has response/resolution/by_priority",
      all(k in ov.get("sla_split", {}) for k in ("response", "resolution", "by_priority")))
check("burn has burn_rate_hr + drain_eta_mins",
      all(k in ov.get("burn", {}) for k in ("burn_rate_hr", "drain_eta_mins")))
check("utilization has load_pct + top_agents",
      all(k in ov.get("utilization", {}) for k in
          ("load_pct", "crew_capacity", "open_capped", "top_agents")))

cards = ov.get("queues", [])
if cards:
    c0 = cards[0]
    for key in ("aging", "burn_rate_hr", "drain_eta_mins", "crew_capacity",
                "load_pct", "reopens_range"):
        check(f"card key '{key}' present", key in c0)
    fleet_sum = {k: sum(int((c.get("aging") or {}).get(k, 0)) for c in cards)
                 for k in ("lt_1h", "h1_4", "h4_24", "d1_3", "gt_3d")}
    check("fleet aging == sum(card aging)", fleet_sum == ov.get("aging"),
          f"fleet={ov.get('aging')} sum={fleet_sum}")
    open_total = sum(int(c.get("open") or 0) for c in cards)
    aging_total = sum(fleet_sum.values())
    check("aging buckets cover all open work", aging_total == open_total,
          f"aging={aging_total} open={open_total}")
else:
    print("  [SKIP] no visible queues — card-level checks skipped")

bh = ov.get("breach_horizon", [])
check("breach_horizon is a list (<=8)", isinstance(bh, list) and len(bh) <= 8, f"n={len(bh)}")
if bh:
    secs = [x.get("due_in_seconds") for x in bh]
    check("breach_horizon sorted ascending", secs == sorted(secs))
    check("breach_horizon fields",
          all(all(k in x for k in ("id", "ticket_number", "subject", "priority",
                                   "queue_id", "queue_name", "kind", "due_at",
                                   "due_in_seconds")) for x in bh))
    check("due_in_seconds >= 0", all(int(x.get("due_in_seconds") or 0) >= 0 for x in bh))
    check("kind is response|resolution",
          all(x.get("kind") in ("response", "resolution") for x in bh))

print("\n── flow_interval behaviour ──")
st, hov = call("/support-desk/queues/overview?days=1&flow_interval=hour", su_tok)
check("days=1&flow_interval=hour → 200", st == 200, f"status={st}")
if st == 200 and hov:
    check("effective flow_interval == 'hour'", hov.get("flow_interval") == "hour")
    hcards = hov.get("queues", [])
    if hcards:
        n = len(hcards[0].get("flow") or [])
        check("hourly buckets <= 24 for days=1", 0 < n <= 24, f"buckets={n}")
st, dov = call("/support-desk/queues/overview?days=7&flow_interval=hour", su_tok)
check("days=7&flow_interval=hour falls back to 'day'",
      st == 200 and dov.get("flow_interval") == "day", f"status={st}")
st, _bad = call("/support-desk/queues/overview?days=7&flow_interval=week", su_tok)
check("flow_interval=week rejected 422", st == 422, f"status={st}")

print("\n── team seal (agent) ──")
if agent_row:
    ag_id, ag_email, ag_tv = agent_row
    ag_tok = token_for(ag_id, ag_tv)
    st, aov = call("/support-desk/queues/overview?days=7", ag_tok)
    check(f"agent {ag_email} gets 200", st == 200, f"status={st}")
    if st == 200 and aov is not None:
        su_ids = {c["id"] for c in cards}
        ag_ids = {c["id"] for c in aov.get("queues", [])}
        check("agent queue set ⊆ superuser queue set", ag_ids.issubset(su_ids),
              f"agent={len(ag_ids)} su={len(su_ids)}")
        check("agent gets the vitals keys too",
              all(k in aov for k in ("deltas", "aging", "breach_horizon")))
else:
    print("  [SKIP] no non-superuser team-member support agent found")

print(f"\n{'ALL CHECKS PASSED' if FAIL == 0 else f'{FAIL} CHECK(S) FAILED'}")
sys.exit(1 if FAIL else 0)
