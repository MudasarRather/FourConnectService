"""Probe the new Critical war-room endpoints against the RUNNING backend (port 8000).

Mints JWTs directly (superuser + a non-superuser support agent), then exercises:
list ?scope=critical&include_major=1, /me/tickets/critical/stats, /ack (200 then 409),
/status-update (cadence re-arm), /presence, bulk ack + the bulk team-scope guard.
Creates ONE test ticket and archives (soft-deletes) it at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_critical_warroom.py
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


# Raw SQL — importing a single ORM model trips full mapper configuration (cross-model
# relationship strings like 'AssetCategory' need every module imported). Not worth it here.
db = SessionLocal()
su = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
ag = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = FALSE AND is_support_agent = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
print(f"superuser: {su[1] if su else None}")
print(f"agent    : {ag[1] if ag else None}")
if not su:
    print("No superuser found - abort"); sys.exit(1)
su_tok = mint(su)
ag_tok = mint(ag) if ag else None
db.close()

print("-- superuser probes --")
s, j = req("GET", "/support-desk/tickets/?scope=critical&include_major=1&limit=5", su_tok)
check("list scope=critical include_major", s == 200 and isinstance(j.get("items"), list), f"(status {s}, total {j.get('total') if j else '?'})")
su_total = j.get("total") if j else 0

s, j = req("GET", "/support-desk/me/tickets/critical/stats", su_tok)
need = {"active_critical", "major_incidents", "unacked", "mtta_minutes", "ack_coverage", "by_business_impact", "squad", "missing_rca"}
check("critical/stats shape", s == 200 and j is not None and need.issubset(j.keys()),
      f"(status {s}, active {j.get('active_critical') if j else '?'}, unacked {j.get('unacked') if j else '?'})")

# Create a disposable critical ticket to exercise the lifecycle.
s, j = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": "[PROBE] war-room lifecycle check", "description": "probe - safe to ignore",
    "priority": "critical", "ticket_type": "incident", "source": "internal",
})
tid = j.get("id") if isinstance(j, dict) else None
check("create probe ticket", s in (200, 201) and tid, f"(status {s}, id {tid})")

if tid:
    s, j = req("POST", f"/support-desk/tickets/{tid}/ack", su_tok, {"note": "probe ack"})
    check("ack 200 + stamps", s == 200 and j.get("acknowledged_at") and j.get("acknowledged_by_name"),
          f"(status {s}, by {j.get('acknowledged_by_name') if j else '?'})")
    s, _ = req("POST", f"/support-desk/tickets/{tid}/ack", su_tok, {})
    check("ack repeat -> 409", s == 409, f"(status {s})")

    s, j = req("POST", f"/support-desk/tickets/{tid}/major-incident", su_tok,
               {"is_major_incident": True, "business_impact": "high", "affected_users": 42,
                "update_interval_minutes": 30})
    check("major-incident + cadence armed", s == 200 and j.get("is_major_incident") and j.get("next_update_due_at"),
          f"(status {s}, next_due {j.get('next_update_due_at') if j else '?'})")
    first_due = j.get("next_update_due_at") if j else None

    s, j = req("POST", f"/support-desk/tickets/{tid}/status-update", su_tok,
               {"body": "probe: investigating", "is_internal": True, "interval_minutes": 60})
    check("status-update re-arms", s == 200 and j.get("next_update_due_at") and j.get("next_update_due_at") != first_due
          and j.get("update_interval_minutes") == 60, f"(status {s})")

    s, j = req("POST", f"/support-desk/tickets/{tid}/status-update", su_tok,
               {"body": "probe: standing down", "is_internal": True, "stop_cadence": True})
    check("status-update stop_cadence", s == 200 and j.get("next_update_due_at") is None, f"(status {s})")

    s, j = req("POST", f"/support-desk/tickets/{tid}/presence", su_tok)
    me_in = any(v.get("is_me") for v in (j.get("viewers") or [])) if j else False
    check("presence heartbeat", s == 200 and me_in, f"(status {s}, viewers {len(j.get('viewers') or []) if j else 0})")

    # include_major visibility: demote priority but keep MI flag -> still on the board.
    s, j = req("PATCH", f"/support-desk/tickets/{tid}", su_tok, {"priority": "high"})
    check("demote priority", s == 200 and j.get("priority") == "high", f"(status {s})")
    s, j = req("GET", "/support-desk/tickets/?scope=critical&include_major=1&limit=100", su_tok)
    ids = [it.get("id") for it in (j.get("items") or [])] if j else []
    check("MI at non-critical priority still visible", s == 200 and tid in ids, f"(status {s})")
    s, j = req("GET", "/support-desk/tickets/?scope=critical&limit=100", su_tok)
    ids = [it.get("id") for it in (j.get("items") or [])] if j else []
    check("pure scope stays pure", s == 200 and tid not in ids, f"(status {s})")

    # bulk ack (already acked -> skipped with reason)
    s, j = req("POST", "/support-desk/tickets/bulk", su_tok, {"ids": [tid], "action": "ack"})
    r0 = (j.get("results") or [{}])[0] if j else {}
    check("bulk ack skip reason", s == 200 and r0.get("skipped") and "cknowledged" in (r0.get("error") or ""),
          f"(status {s}, {r0.get('error')})")

print("-- agent probes --")
if ag_tok:
    s, j = req("GET", "/support-desk/tickets/?scope=critical&include_major=1&limit=5", ag_tok)
    ag_total = j.get("total") if j else None
    check("agent list team-sealed 200", s == 200, f"(status {s}, total {ag_total} vs desk {su_total})")
    s, j = req("GET", "/support-desk/me/tickets/critical/stats", ag_tok)
    check("agent critical/stats 200", s == 200 and j is not None, f"(status {s}, active {j.get('active_critical') if j else '?'})")
    if tid:
        s, j = req("POST", "/support-desk/tickets/bulk", ag_tok, {"ids": [tid], "action": "ack"})
        r0 = (j.get("results") or [{}])[0] if j else {}
        in_scope_err = (r0.get("error") or "")
        # The probe ticket routes to no team -> if the agent's teams don't handle its
        # taxonomy it MUST be rejected as out of scope; if they do, a skip is also fine.
        check("bulk team-guard responds", s == 200 and (r0.get("ok") is False or r0.get("skipped")),
              f"(status {s}, {in_scope_err!r})")
else:
    print("  [SKIP] no non-superuser support agent flagged; team-seal probes skipped")

# Cleanup: archive the probe ticket.
if tid:
    s, _ = req("DELETE", f"/support-desk/tickets/{tid}?reason=probe%20cleanup", su_tok)
    check("archive probe ticket", s == 204, f"(status {s})")

print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
