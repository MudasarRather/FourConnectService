"""E2E probe — the full Queue Engine workflow against the LIVE backend on :8000.

Flow: create L1+L2 test queues (no team → no auto-assign, no stray notifications)
→ create a routing rule targeting the L1 queue → simulate (dry) → create a ticket
matching the rule → assert it routed to the L1 queue → tier board shows it →
serve-next claims it → skip (with reason) un-assigns it → serve-next again →
tier-escalate to L2 (with diagnosis) → assert queue moved + escalation record →
tier-descend back to L1 → overview reflects the lanes → CLEAN UP (archive ticket,
delete rule + queues). Prints PASS/FAIL per step.

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_queue_engine_e2e.py
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
    sys.stdout.reconfigure(encoding="utf-8")   # Windows cp1252 console chokes on arrows
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
cur.execute("SELECT id, COALESCE(token_version,0) FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1")
uid, tv = cur.fetchone()
conn.close()
token = jwt.encode({"sub": str(uid), "tv": int(tv),
                    "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
                   env.get("SECRET_KEY", ""), algorithm="HS256")
HDRS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8000/api/support-desk"

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


cleanup = {"queues": [], "rules": [], "tickets": []}
try:
    # 1. Lay two test lanes (no team ⇒ no auto-assign, nobody pinged).
    c, q1 = call("POST", "/queues/", {"name": "PROBE L1 Yard", "tier": 1,
                                      "queue_priority": 99, "serve_order": "priority_age"})
    check("create L1 queue", c == 201, q1 if c != 201 else q1["id"])
    if c != 201:
        sys.exit("cannot continue without the L1 queue")
    cleanup["queues"].append(q1["id"])
    c, q2 = call("POST", "/queues/", {"name": "PROBE L2 Yard", "tier": 2})
    check("create L2 queue", c == 201)
    cleanup["queues"].append(q2["id"])

    # 2. A routing rule: subject contains 'PROBE-SWITCHYARD' → route to the L1 lane + tag.
    #    order_index 0 — the chain is FIRST-MATCH and live desks carry user catch-alls
    #    (e.g. "incident → L1", stop-on-match) that would shadow a rule parked at 999.
    #    The subject condition is probe-unique, so real traffic never matches this rule.
    c, rule = call("POST", "/automation-rules/", {
        "name": "PROBE switchyard rule", "match_type": "all",
        "conditions": [{"field": "subject", "op": "contains", "value": "PROBE-SWITCHYARD"}],
        "actions": [{"type": "route_queue", "value": q1["id"]},
                    {"type": "add_tags", "value": ["probe-routed"]}],
        "order_index": 0, "stop_processing": True})
    check("create routing rule", c == 201)
    cleanup["rules"].append(rule["id"])

    # 3. Dry-run simulate — must pick the rule + the L1 queue, write nothing.
    c, simr = call("POST", "/automation-rules/simulate", {"subject": "PROBE-SWITCHYARD printer down",
                                                          "ticket_type": "incident", "priority": "high"})
    hit = c == 200 and any(mm["rule_id"] == rule["id"] for mm in simr["matched"]) \
        and simr["decision"].get("queue_id") == q1["id"]
    check("simulate picks rule → L1 lane", hit, simr if not hit else "")

    # 4. Create a real ticket matching the rule.
    c, t = call("POST", "/tickets/", {"subject": "PROBE-SWITCHYARD printer down [e2e probe]",
                                      "description": "Automated queue-engine probe — safe to ignore.",
                                      "ticket_type": "incident", "priority": "high", "source": "internal"})
    check("create matching ticket", c in (200, 201), t if c not in (200, 201) else t["ticket_number"])
    cleanup["tickets"].append(t["id"])
    check("rule routed ticket → L1 queue", str(t.get("queue_id")) == str(q1["id"]),
          f"queue_id={t.get('queue_id')}")
    check("rule tagged ticket", "probe-routed" in (t.get("tags") or []), t.get("tags"))

    # 5. Tier board shows it.
    c, board = call("GET", f"/queues/tier/1/board?queue_id={q1['id']}")
    on_board = c == 200 and any(str(x["id"]) == str(t["id"]) for x in board["items"])
    check("L1 board lists the ticket", on_board)

    # 6. Serve-next claims it (assigns to me, open → in_progress).
    c, served = call("POST", f"/queues/tier/1/serve-next?queue_id={q1['id']}")
    got = c == 200 and served.get("ticket") and str(served["ticket"]["id"]) == str(t["id"])
    check("serve-next claims the probe ticket", got,
          "" if got else served)
    if got:
        check("serve-next set status in_progress", served["ticket"]["status"] == "in_progress",
              served["ticket"]["status"])

    # 7. Skip with a reason — un-assigns, returns to pool, out of MY rotation today.
    c, skipped = call("POST", f"/tickets/{t['id']}/skip", {"reason_code": "need_info", "note": "e2e probe skip"})
    check("skip (reason-coded) un-assigns", c == 200 and skipped.get("assigned_agent_id") is None,
          skipped if c != 200 else "")
    c, again = call("POST", f"/queues/tier/1/serve-next?queue_id={q1['id']}")
    excl = c == 200 and (again.get("ticket") is None or str(again["ticket"]["id"]) != str(t["id"]))
    check("serve-next excludes my skipped ticket today", excl, again if not excl else "")

    # 8. Tier-escalate L1 → L2 (needs nothing extra at L2; check the lane move + record).
    #    Explicit queue_id — live desks have REAL L2 lanes that find_tier_queue would
    #    rank above the probe's (queue_priority order); the probe must own its target.
    c, esc = call("POST", f"/tickets/{t['id']}/tier-escalate",
                  {"to_tier": 2, "reason_code": "complexity", "reason": "probe escalation",
                   "queue_id": q2["id"]})
    ok = c == 200 and str(esc.get("queue_id")) == str(q2["id"]) and esc.get("is_escalated")
    check("tier-escalate moves lane + writes escalation", ok, esc if not ok else f"level={esc.get('escalation_level')}")

    # 8b. L3 without diagnosis must 422.
    c, err = call("POST", f"/tickets/{t['id']}/tier-escalate", {"to_tier": 3})
    check("L3 handoff without diagnosis → 422", c == 422, c)

    # 9. Tier-descend back to L1 (reason-coded; explicit lane for the same reason as #8).
    c, desc = call("POST", f"/tickets/{t['id']}/tier-descend",
                   {"to_tier": 1, "reason_code": "misrouted", "reason": "probe send-back",
                    "queue_id": q1["id"]})
    check("tier-descend returns to L1", c == 200 and str(desc.get("queue_id")) == str(q1["id"]),
          desc if c != 200 else "")

    # 10. Overview reflects the lanes + the tier flow edge.
    c, ov = call("GET", "/queues/overview?days=7")
    lanes = {str(x["id"]) for x in ov["queues"]}
    check("overview lists both probe lanes", c == 200 and str(q1["id"]) in lanes and str(q2["id"]) in lanes)
    flow_edge = any(e["from_tier"] == 1 and e["to_tier"] == 2 and e["count"] >= 1 for e in ov.get("tier_flow", []))
    check("overview tier flow shows the L1→L2 move", flow_edge, ov.get("tier_flow"))

    # 11. Queue drawer stats endpoint.
    c, qs = call("GET", f"/queues/{q1['id']}/stats")
    check("queue stats drill", c == 200 and qs["card"]["tier"] == 1)

    # 12. Guard: default-queue + reassign-directive delete guards.
    c, _ = call("DELETE", f"/queues/{q1['id']}")
    check("delete guarded while holding an active ticket (409)", c == 409, c)

finally:
    # ── cleanup: archive ticket → delete rule → delete queues ──
    for tid in cleanup["tickets"]:
        c, _ = call("DELETE", f"/tickets/{tid}")
        print(f"cleanup ticket {tid}: {c}")
    for rid in cleanup["rules"]:
        c, _ = call("DELETE", f"/automation-rules/{rid}")
        print(f"cleanup rule {rid}: {c}")
    for qid in cleanup["queues"]:
        c, _ = call("DELETE", f"/queues/{qid}")
        print(f"cleanup queue {qid}: {c}")

print("\n" + ("ALL E2E CHECKS PASSED ✔" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
