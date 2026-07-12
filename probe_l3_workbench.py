"""E2E probe — the L3 WORKBENCH (handoff dossier, KEDB, cascade solve) on the LIVE :8000.

Flow: temp L2 + L3 lanes (teamless — invisible to auto-assign) → probe ticket parked
on L2 + owned → tier-escalate L2→L3 with a technical diagnosis → tier-3 board carries
the new L3 stats keys + the ticket → handoff dossier surfaces the diagnosis, tier path
and esc-ACK state → escalation-ack flips the dossier → problem created with workaround/
owner → KEDB q-search + known_only filter → ticket linked to the problem (dossier
snapshot) → cascade resolve-linked resolves the ticket (idempotent re-run skips) →
guards (bad code 422, unknown linked ticket reported not raised) → CLEAN UP
(delete problem, ticket, both lanes). Never calls serve-next.

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_l3_workbench.py
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
import uuid as uuidlib
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
L3_STATS_KEYS = ["mi_active", "missing_rca", "problems_open", "known_errors", "fix_in_progress"]


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


MARK = uuidlib.uuid4().hex[:10]
cleanup = {"queues": [], "tickets": [], "problems": []}
try:
    # 1. Temp lanes on both tiers, teamless.
    c, q2 = call("POST", "/queues/", {"name": f"PROBE L3WB src {MARK}", "tier": 2, "queue_priority": 1})
    check("create temp L2 lane", c == 201, q2 if c != 201 else q2["id"])
    if c != 201:
        sys.exit("cannot continue without lanes")
    cleanup["queues"].append(q2["id"])
    c, q3 = call("POST", "/queues/", {"name": f"PROBE L3WB dst {MARK}", "tier": 3, "queue_priority": 1})
    check("create temp L3 lane", c == 201, q3 if c != 201 else q3["id"])
    if c != 201:
        sys.exit("cannot continue without lanes")
    cleanup["queues"].append(q3["id"])

    # 2. Probe ticket → park on the L2 lane + own it.
    c, t = call("POST", "/tickets/", {"subject": f"PROBE-L3WB pool exhaustion {MARK} [probe]",
                                      "description": "Automated L3 workbench probe — safe to ignore.",
                                      "ticket_type": "incident", "priority": "high",
                                      "source": "internal"})
    check("create probe ticket", c in (200, 201), t if c not in (200, 201) else t["ticket_number"])
    cleanup["tickets"].append(t["id"])
    c, t = call("POST", f"/tickets/{t['id']}/assign",
                {"queue_id": q2["id"], "assigned_agent_id": str(uid)})
    check("park on L2 + own it", c == 200 and str(t.get("queue_id")) == str(q2["id"]), c)

    # 3. The real L2→L3 handoff: tier-escalate with a technical diagnosis (422-gated).
    c, err = call("POST", f"/tickets/{t['id']}/tier-escalate", {"to_tier": 3, "queue_id": q3["id"]})
    check("L3 handoff without diagnosis → 422", c == 422, c)
    diag = f"Probe diagnosis {MARK}: pool exhausted under burst; retries tuned, leak persists."
    c, t = call("POST", f"/tickets/{t['id']}/tier-escalate",
                {"to_tier": 3, "queue_id": q3["id"], "diagnosis": diag, "reason_code": "complexity"})
    check("tier-escalate L2→L3", c == 200 and str(t.get("queue_id")) == str(q3["id"]),
          t if c != 200 else f"esc L{t.get('escalation_level')}")

    # 4. Tier-3 board: new stats keys + the ticket present.
    c, board = call("GET", f"/queues/tier/3/board?queue_id={q3['id']}")
    keys_ok = c == 200 and all(k in board["stats"] for k in L3_STATS_KEYS)
    check("tier-3 board carries L3 stats keys", keys_ok,
          [k for k in L3_STATS_KEYS if c == 200 and k not in board["stats"]] or "")
    if c == 200:
        row = next((x for x in board["items"] if str(x["id"]) == str(t["id"])), None)
        check("escalated ticket rides the L3 board", bool(row), "")

    # 5. Handoff dossier: escalation record + diagnosis + tier path, unacked.
    c, dz = call("GET", f"/tickets/{t['id']}/handoff-dossier")
    check("dossier 200 + escalated", c == 200 and dz.get("is_escalated") is True, dz if c != 200 else "")
    if c == 200:
        check("dossier carries the diagnosis", any(MARK in (d.get("body") or "") for d in dz.get("diagnoses", [])),
              f"{len(dz.get('diagnoses', []))} notes")
        check("dossier tier path recorded", any((p.get("tier") == 3 and p.get("direction") == "escalate")
                                                for p in dz.get("tier_path", [])), dz.get("tier_path"))
        check("dossier unacked with a due clock field", dz.get("acknowledged_at") is None, "")

    # 6. Acknowledge the escalation → dossier flips.
    c, _ = call("POST", f"/tickets/{t['id']}/escalation-ack", {"note": "probe ack"})
    check("escalation-ack", c == 200, c)
    c, dz = call("GET", f"/tickets/{t['id']}/handoff-dossier")
    check("dossier acknowledged", c == 200 and dz.get("acknowledged_at"), "")

    # 7. Problem with KEDB fields + owner.
    c, p = call("POST", "/problems/", {"title": f"PROBE problem {MARK}",
                                       "description": "probe problem record",
                                       "severity": "high",
                                       "workaround": f"Restart the pooler — workaround {MARK}",
                                       "owner_id": str(uid),
                                       "linked_ticket_ids": [str(t["id"])]})
    check("create problem w/ workaround+owner", c == 201 and p.get("workaround") and str(p.get("owner_id")) == str(uid),
          p if c != 201 else p["problem_number"])
    cleanup["problems"].append(p["id"])

    # 8. KEDB search: q hits the workaround text; known_only excludes until published.
    c, hits = call("GET", f"/problems/?q=workaround%20{MARK}")
    check("KEDB q-search finds the workaround", c == 200 and any(str(x["id"]) == str(p["id"]) for x in hits), c)
    c, hits = call("GET", "/problems/?known_only=true")
    in_kedb = c == 200 and any(str(x["id"]) == str(p["id"]) for x in hits)
    check("known_only excludes unpublished", not in_kedb, "")
    c, p = call("PATCH", f"/problems/{p['id']}", {"workaround_published": True, "status": "known_error"})
    check("publish workaround + known_error", c == 200 and p.get("workaround_published") is True, c)
    c, hits = call("GET", "/problems/?known_only=true")
    check("known_only now includes it", c == 200 and any(str(x["id"]) == str(p["id"]) for x in hits), c)
    c, board = call("GET", f"/queues/tier/3/board?queue_id={q3['id']}")
    check("stats: known_errors ≥ 1", c == 200 and board["stats"]["known_errors"] >= 1,
          c == 200 and board["stats"]["known_errors"])

    # 9. Link the ticket → dossier problem snapshot.
    c, t2 = call("PATCH", f"/tickets/{t['id']}", {"linked_problem_id": str(p["id"])})
    check("PATCH linked_problem_id", c == 200 and str(t2.get("linked_problem_id")) == str(p["id"]), c)
    c, dz = call("GET", f"/tickets/{t['id']}/handoff-dossier")
    check("dossier problem snapshot + linked_count",
          c == 200 and dz.get("problem") and dz["problem"]["linked_count"] == 1
          and dz["problem"].get("workaround"), dz.get("problem") if c == 200 else c)

    # 10. Cascade guards.
    c, _err = call("POST", f"/problems/{p['id']}/resolve-linked",
                   {"resolution_summary": "x", "resolution_code": "nonsense"})
    check("cascade bad resolution_code → 422", c == 422, c)

    # 11. Cascade solve: resolves the linked ticket + stamps the problem.
    c, cas = call("POST", f"/problems/{p['id']}/resolve-linked",
                  {"resolution_summary": f"Probe cascade resolution {MARK} — pooler patched.",
                   "resolution_code": "solved", "resolution_category": "software",
                   "root_cause": f"probe root cause {MARK}"})
    check("cascade resolve-linked", c == 200 and cas["resolved"] == 1 and cas["problem_status"] == "resolved",
          cas if c != 200 else f"resolved={cas['resolved']}")
    c, tk = call("GET", f"/tickets/{t['id']}")
    check("linked ticket now RESOLVED", c == 200 and tk.get("status") == "resolved", tk.get("status") if c == 200 else c)
    c, pk = call("GET", f"/problems/{p['id']}")
    check("problem root_cause stamped", c == 200 and MARK in (pk.get("root_cause") or ""), "")

    # 12. Idempotent re-run: everything already resolved → skipped, nothing raises.
    c, cas = call("POST", f"/problems/{p['id']}/resolve-linked",
                  {"resolution_summary": "probe cascade re-run", "resolution_code": "solved",
                   "mark_problem_resolved": False})
    check("cascade re-run skips resolved", c == 200 and cas["resolved"] == 0 and cas["skipped"] == 1,
          cas if c != 200 else cas["results"][0].get("reason"))

    # 13. Unknown linked ticket is REPORTED, not raised.
    c, p2 = call("POST", "/problems/", {"title": f"PROBE ghost-link {MARK}",
                                        "linked_ticket_ids": [str(uuidlib.uuid4())]})
    cleanup["problems"].append(p2["id"])
    c, cas = call("POST", f"/problems/{p2['id']}/resolve-linked",
                  {"resolution_summary": "probe ghost cascade", "resolution_code": "solved"})
    check("ghost ticket reported ok=False", c == 200 and cas["skipped"] == 1 and cas["results"][0]["ok"] is False,
          cas if c != 200 else cas["results"][0].get("reason"))

finally:
    for pid in cleanup["problems"]:
        c, _ = call("DELETE", f"/problems/{pid}")
        print(f"cleanup problem {pid}: {c}")
    for tid in cleanup["tickets"]:
        c, _ = call("DELETE", f"/tickets/{tid}")
        print(f"cleanup ticket {tid}: {c}")
    for qid in cleanup["queues"]:
        c, _ = call("DELETE", f"/queues/{qid}")
        print(f"cleanup queue {qid}: {c}")

print("\n" + ("ALL L3 WORKBENCH CHECKS PASSED ✔" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
