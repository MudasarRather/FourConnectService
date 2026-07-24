"""Probe the Command Ledger enhancement against the RUNNING backend (port 8000).

Exercises the decision endpoint's new structured `reason` field end to end:
201 with reason -> activity detail carries kind/decision/reason/note + actor stamp;
201 without reason (back-compat, reason=None); 422 guards (unknown kind, short
decision, oversize reason); grave kinds accepted; 409 seal after resolve.
Creates one disposable probe ticket and archives it at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_decision_ledger.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# WMI-hang guard (same as run_server.py) before app imports.
import platform
try:
    _ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
    _ur.__dict__["processor"] = "Intel"
    platform._uname_cache = _ur
    platform._Processor.get = staticmethod(lambda: "Intel")
except Exception:
    pass

from sqlalchemy import text

from app.database import SessionLocal
from app.utils.auth import create_access_token

BASE = "http://127.0.0.1:8000/api"
PASS = 0
FAIL = 0


def req(method, path, token, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except Exception:
            return e.code, None


def check(label, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label} {extra}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {extra}")


def mint(row) -> str:
    return create_access_token({"sub": str(row[0]), "tv": row[2] or 1})


db = SessionLocal()
su = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
print(f"superuser: {su[1] if su else None}")
if not su:
    print("No superuser found - abort"); sys.exit(1)
su_tok = mint(su)
su_id = str(su[0])
db.close()

made = []

print("-- setup: disposable incident --")
s, j = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": "probe: decision ledger reason field", "description": "probe - safe to ignore",
    "priority": "high", "ticket_type": "incident", "source": "internal",
})
check("create incident 201", s in (200, 201) and j and j.get("id"), f"(status {s})")
if not (j and j.get("id")):
    sys.exit(1)
tid = j["id"]
made.append(tid)

print("-- decision with structured reason --")
s, j = req("POST", f"/support-desk/tickets/{tid}/decision", su_tok, {
    "kind": "failover", "decision": "Probe: failing over to standby",
    "reason": "Primary degraded beyond recovery tolerance", "note": "probe context",
})
check("decision+reason 201", s == 201 and j and j.get("ok"), f"(status {s})")

s, j = req("GET", f"/support-desk/tickets/{tid}/activities", su_tok)
rows = [a for a in (j or []) if a.get("action") == "decision_logged"]
d = rows[-1]["detail"] if rows else {}
check("activity row present", s == 200 and len(rows) == 1, f"(status {s}, rows {len(rows)})")
check("detail.kind == failover", d.get("kind") == "failover")
check("detail.reason recorded", d.get("reason") == "Primary degraded beyond recovery tolerance")
check("detail.decision recorded", d.get("decision") == "Probe: failing over to standby")
check("detail.note recorded", d.get("note") == "probe context")
check("actor stamped", bool(rows and rows[-1].get("actor_name")),
      f"(actor {rows[-1].get('actor_name') if rows else '?'})")

print("-- back-compat: no reason key --")
s, j = req("POST", f"/support-desk/tickets/{tid}/decision", su_tok, {
    "kind": "mitigation", "decision": "Probe: no-reason entry",
})
check("decision sans reason 201", s == 201, f"(status {s})")
s, j = req("GET", f"/support-desk/tickets/{tid}/activities", su_tok)
rows = [a for a in (j or []) if a.get("action") == "decision_logged"]
d = rows[-1]["detail"] if rows else {}
check("reason is null when omitted", len(rows) == 2 and d.get("reason") is None)

print("-- validation guards --")
s, _ = req("POST", f"/support-desk/tickets/{tid}/decision", su_tok, {
    "kind": "made_up_kind", "decision": "Probe: bad kind",
})
check("unknown kind 422", s == 422, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{tid}/decision", su_tok, {
    "kind": "mitigation", "decision": "ab",
})
check("short decision 422", s == 422, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{tid}/decision", su_tok, {
    "kind": "mitigation", "decision": "Probe: oversize reason", "reason": "x" * 301,
})
check("oversize reason 422", s == 422, f"(status {s})")

print("-- terminal seal --")
s, _ = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok, {"assigned_agent_id": su_id})
check("assign owner 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{tid}/resolve", su_tok, {
    "resolution_code": "solved", "resolution_summary": "probe resolution - safe to ignore",
})
check("resolve 200", s == 200, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{tid}/decision", su_tok, {
    "kind": "stand_down", "decision": "Probe: should be sealed",
    "reason": "should never land",
})
check("decision on terminal 409", s == 409, f"(status {s})")

print("-- cleanup --")
for t in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{t}?reason=probe%20cleanup", su_tok)
    check(f"archive {t[:8]}", s in (204, 409, 404), f"(status {s})")

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
