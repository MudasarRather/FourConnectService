"""Probe — Queue Config v2 (per-queue SLA, capacity/overflow, ledger, conditions)
against the LIVE backend on :8000 + the engine helpers directly (rolled back).

Coverage:
  HTTP  POST/PATCH/DELETE /api/support-desk/queues        — v2 fields round-trip,
        overflow self-ref 422, A->B->A cycle 422
  HTTP  POST/PATCH/DELETE /api/support-desk/automation-rules — audit + revisions
  HTTP  GET  /api/support-desk/automation-rules/{id}/revisions
  HTTP  GET  /api/support-desk/queues/config-ledger
  HTTP  POST /api/support-desk/automation-rules/simulate  — matches_keywords + gte
        impact conditions, queue SLA + overflow surfaced in the decision
  DB    apply_overflow / apply_queue_sla / business_hours condition (session
        rolled back — nothing persists)

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_queue_config_v2.py
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
print(f"superuser: {email}  tv={tv}")

# Migration sanity.
for col in ("sla_package_id", "capacity_limit", "overflow_queue_id"):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'support_queues' AND column_name = %s""", (col,))
    print(f"  col support_queues.{col}: {'OK' if cur.fetchone() else 'MISSING'}")
cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'support_rule_revisions'")
print(f"  table support_rule_revisions: {'OK' if cur.fetchone() else 'MISSING (create_all runs at boot)'}")
cur.execute("SELECT id, name FROM support_sla_packages WHERE is_deleted = FALSE ORDER BY is_default DESC LIMIT 1")
pkg_row = cur.fetchone()
conn.close()

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


print("\n── HTTP: queue v2 fields + overflow guards ──")
st, qa = call("POST", "/support-desk/queues/", {
    "name": "QC Probe A", "code": "QC_PROBE_A", "queue_priority": 51,
    "capacity_limit": 1})
check("create lane A (capacity 1)", st == 201, f"status={st}")
st, qb = call("POST", "/support-desk/queues/", {
    "name": "QC Probe B", "code": "QC_PROBE_B", "queue_priority": 52,
    **({"sla_package_id": str(pkg_row[0])} if pkg_row else {})})
check("create lane B (with SLA policy)" if pkg_row else "create lane B", st == 201, f"status={st}")
if st != 201 or qa is None or qb is None:
    sys.exit("probe lanes not created — aborting")
aid, bid = qa["id"], qb["id"]
check("v2 fields round-trip", qa.get("capacity_limit") == 1 and
      (not pkg_row or qb.get("sla_package_id") == str(pkg_row[0])))

st, r = call("PATCH", f"/support-desk/queues/{aid}", {"overflow_queue_id": aid})
check("overflow self-reference rejected 422", st == 422, f"status={st}")
st, r = call("PATCH", f"/support-desk/queues/{aid}", {"overflow_queue_id": bid})
check("overflow A→B accepted", st == 200, f"status={st}")
st, r = call("PATCH", f"/support-desk/queues/{bid}", {"overflow_queue_id": aid})
check("overflow cycle A→B→A rejected 422", st == 422, f"status={st}")

print("\n── HTTP: rule CRUD → revisions + ledger ──")
st, rule = call("POST", "/support-desk/automation-rules/", {
    "name": "QC Probe keyword rule", "match_type": "all", "order_index": 9999,
    "conditions": [{"field": "subject", "op": "matches_keywords", "value": "vpn outage, tunnel down"},
                   {"field": "impact", "op": "gte", "value": "high"}],
    "actions": [{"type": "route_queue", "value": "QC_PROBE_A"}],
    "stop_processing": True})
check("create keyword+impact rule", st == 201, f"status={st}")
rid = rule["id"] if rule else None
st, _ = call("PATCH", f"/support-desk/automation-rules/{rid}", {"description": "probe v2"})
check("update rule", st == 200, f"status={st}")
st, revs = call("GET", f"/support-desk/automation-rules/{rid}/revisions")
check("revisions endpoint (v2 after update)", st == 200 and revs and revs[0]["version"] == 2
      and revs[0]["action"] == "updated" and revs[-1]["action"] == "created",
      f"status={st} versions={[x['version'] for x in (revs or [])]}")

print("\n── HTTP: simulate — keywords + impact rank + overflow/SLA surfaced ──")
st, sim = call("POST", "/support-desk/automation-rules/simulate", {
    "subject": "Site-to-site VPN outage in Pune", "impact": "critical", "priority": "high"})
check("simulate matches keyword rule", st == 200 and sim and
      any(mm.get("rule_id") == rid for mm in sim.get("matched", [])),
      f"status={st} via={sim.get('decision', {}).get('via') if sim else None}")
check("simulate routed to probe lane", bool(sim) and sim["decision"].get("queue_name") in ("QC Probe A", "QC Probe B"))
st, sim2 = call("POST", "/support-desk/automation-rules/simulate", {
    "subject": "VPN outage again", "impact": "low"})
check("impact gte gate blocks low-impact", st == 200 and
      not any(mm.get("rule_id") == rid for mm in (sim2 or {}).get("matched", [])))

st, ledger = call("GET", "/support-desk/queues/config-ledger?limit=20")
check("config-ledger lists recent config ops", st == 200 and ledger and ledger["total"] >= 1 and
      any(i["action"].startswith("support.rule.") for i in ledger["items"]),
      f"status={st} total={(ledger or {}).get('total')}")

print("\n── DB engine: overflow hop + queue SLA + business-hours (rolled back) ──")
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import app.main  # noqa: F401 — load the FULL model registry (partial imports leave relationships unresolvable)
from app.database import SessionLocal
from app.models.support_desk.workspace import SdQueue
from app.models.support_desk.ticket import SdTicket
from app.utils.support_desk.assignment import apply_overflow, apply_queue_sla
from app.utils.support_desk.rules import _business_hours_state
from app.utils.support_desk import sla as sla_util

db = SessionLocal()
try:
    qA = db.query(SdQueue).filter(SdQueue.code == "QC_PROBE_A").first()
    qB = db.query(SdQueue).filter(SdQueue.code == "QC_PROBE_B").first()
    t = SdTicket(ticket_number="QCPROBE1", subject="filler", status="open", priority="medium",
                 queue_id=qA.id)
    db.add(t)
    db.flush()
    final, hopped = apply_overflow(db, qA)   # A holds 1 open, capacity 1 → hop to B
    check("apply_overflow hops full lane A→B", hopped and str(final.id) == str(qB.id))
    empty_final, empty_hop = apply_overflow(db, qB)   # B unlimited → stay
    check("apply_overflow leaves uncapped lane", not empty_hop)

    if pkg_row:
        t2 = SdTicket(ticket_number="QCPROBE2", subject="sla probe", status="open",
                      priority="high", queue_id=qB.id, created_at=sla_util.now_utc())
        db.add(t2)
        db.flush()
        apply_queue_sla(db, t2, qB)
        check("apply_queue_sla stamps lane package", str(t2.sla_package_id) == str(pkg_row[0]),
              f"deadlines={'set' if (t2.response_due_at or t2.resolution_due_at) else 'none (package matrix empty?)'}")
    state = _business_hours_state(db, t, sla_util.now_utc())
    check("business-hours state resolves", state in ("in_hours", "out_of_hours"), state)
finally:
    db.rollback()
    db.close()

print("\n── cleanup ──")
st, _ = call("DELETE", f"/support-desk/automation-rules/{rid}")
check("delete probe rule", st == 204, f"status={st}")
st, revs = call("GET", f"/support-desk/automation-rules/{rid}/revisions")
check("revisions survive rule delete (tombstone history)", st == 200 and revs and revs[0]["action"] == "deleted")
# A points at B — clear the overflow, then delete both lanes.
call("PATCH", f"/support-desk/queues/{aid}", {"overflow_queue_id": None})
st1, _ = call("DELETE", f"/support-desk/queues/{aid}")
st2, _ = call("DELETE", f"/support-desk/queues/{bid}")
check("delete probe lanes", st1 == 204 and st2 == 204, f"A={st1} B={st2}")

print(f"\n{'ALL CHECKS PASSED' if FAIL == 0 else f'{FAIL} CHECK(S) FAILED'}")
sys.exit(1 if FAIL else 0)
