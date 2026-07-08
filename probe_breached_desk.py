"""Probe the new Breached desk workflow against the RUNNING backend (port 8000).

Mints JWTs directly (superuser + a non-superuser support agent), then exercises:
the breach-flag sweep (idle past-due ticket gets flipped + stamped + timeline activity,
idempotent on the second pass), GET /me/tickets/breached/stats (shape + team seal:
agent numbers are a subset of the superuser's), scope=sla_breached list (swept ticket
appears with sla_*_breached_at in the payload), breach_kind / missing_rca / active_only
list params, sort by sla_resolution_breached_at, the RCA roundtrip (POST /rca clears the
missing_rca lens), and scope=due_soon (the at-risk rail). Creates one disposable ticket
and archives it at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_breached_desk.py
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
        with urllib.request.urlopen(r, timeout=45) as resp:
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
ag = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = FALSE AND is_support_agent = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
print(f"superuser: {su[1] if su else None}")
print(f"agent    : {ag[1] if ag else None}")
if not su:
    print("No superuser found - abort"); sys.exit(1)
su_tok = mint(su)
ag_tok = mint(ag) if ag else None

print("-- setup: idle past-due ticket (flags stale-False) --")
s, j = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": "[PROBE] breached desk check", "description": "probe - safe to ignore",
    "priority": "high", "ticket_type": "incident", "source": "internal",
})
tid = j.get("id") if isinstance(j, dict) else None
check("create probe ticket", s in (200, 201) and tid, f"(status {s}, id {tid})")
if not tid:
    sys.exit(1)

# Simulate the loophole: deadlines silently passed, flags never refreshed (no write since).
db.execute(text(
    "UPDATE support_tickets SET status='open', sla_paused_since=NULL, "
    "response_due_at = NOW() - INTERVAL '4 hours', resolution_due_at = NOW() - INTERVAL '3 hours', "
    "first_responded_at = NULL, resolved_at = NULL, "
    "sla_response_breached = FALSE, sla_resolution_breached = FALSE, "
    "sla_response_breached_at = NULL, sla_resolution_breached_at = NULL, "
    "breach_reason = NULL, rca_summary = NULL WHERE id = :i"), {"i": tid})
db.commit()

print("-- sweep on list-load: scope=sla_breached flips the stale flags --")
s, j = req("GET", "/support-desk/tickets/?scope=sla_breached&limit=100&q=%5BPROBE%5D", su_tok)
rows = (j or {}).get("items", []) if isinstance(j, dict) else []
mine = next((r for r in rows if r.get("id") == tid), None)
check("swept ticket surfaces in scope=sla_breached", s == 200 and mine is not None, f"(status {s})")
if mine:
    check("both flags flipped", mine.get("sla_response_breached") and mine.get("sla_resolution_breached"))
    check("breach stamps in payload", bool(mine.get("sla_response_breached_at")) and bool(mine.get("sla_resolution_breached_at")),
          f"(reso_at {mine.get('sla_resolution_breached_at')})")

acts = db.execute(text(
    "SELECT COUNT(*) FROM support_ticket_activities WHERE ticket_id = :i AND action = 'sla_breached'"), {"i": tid}).scalar()
check("sla_breached timeline activity written once", acts == 1, f"(count {acts})")

print("-- stats endpoint: shape + sweep idempotence --")
s, st = req("GET", "/support-desk/me/tickets/breached/stats", su_tok)
check("GET /me/tickets/breached/stats", s == 200 and isinstance(st, dict), f"(status {s})")
if isinstance(st, dict):
    for f in ("active_breached", "by_kind", "by_priority", "by_age", "unassigned_breached",
              "not_escalated", "total_debt_minutes", "at_risk", "imminent", "repaired_today",
              "missing_rca", "rca_coverage", "squad", "team_names"):
        check(f"stats field {f}", f in st)
    check("active_breached counts the probe", st.get("active_breached", 0) >= 1, f"({st.get('active_breached')})")
    check("by_kind.both >= 1", st.get("by_kind", {}).get("both", 0) >= 1, f"({st.get('by_kind')})")
    check("total_debt_minutes > 0", st.get("total_debt_minutes", 0) > 0, f"({st.get('total_debt_minutes')}m)")
    check("missing_rca counts the probe", st.get("missing_rca", 0) >= 1, f"({st.get('missing_rca')})")
acts2 = db.execute(text(
    "SELECT COUNT(*) FROM support_ticket_activities WHERE ticket_id = :i AND action = 'sla_breached'"), {"i": tid}).scalar()
check("sweep idempotent (still one activity after stats call)", acts2 == 1, f"(count {acts2})")

print("-- team seal: agent stats/list are a subset of the superuser's --")
if ag_tok:
    s, ast = req("GET", "/support-desk/me/tickets/breached/stats", ag_tok)
    check("agent stats 200", s == 200, f"(status {s})")
    if isinstance(ast, dict) and isinstance(st, dict):
        check("agent active_breached <= superuser's",
              ast.get("active_breached", 0) <= st.get("active_breached", 0),
              f"({ast.get('active_breached')} <= {st.get('active_breached')})")
    s, aj = req("GET", "/support-desk/tickets/?scope=sla_breached&limit=100", ag_tok)
    a_total = (aj or {}).get("total", 0) if isinstance(aj, dict) else 0
    s2, sj = req("GET", "/support-desk/tickets/?scope=sla_breached&limit=100", su_tok)
    s_total = (sj or {}).get("total", 0) if isinstance(sj, dict) else 0
    check("agent breached list <= superuser's", s == 200 and s2 == 200 and a_total <= s_total,
          f"({a_total} <= {s_total})")
else:
    print("  [SKIP] no non-superuser agent found")

print("-- list refinements --")
s, j = req("GET", "/support-desk/tickets/?scope=sla_breached&breach_kind=both&limit=100&q=%5BPROBE%5D", su_tok)
ok = s == 200 and any(r.get("id") == tid for r in (j or {}).get("items", []))
check("breach_kind=both returns probe", ok, f"(status {s})")
s, j = req("GET", "/support-desk/tickets/?scope=sla_breached&breach_kind=response&limit=100&q=%5BPROBE%5D", su_tok)
ok = s == 200 and not any(r.get("id") == tid for r in (j or {}).get("items", []))
check("breach_kind=response excludes both-kind probe", ok, f"(status {s})")
s, j = req("GET", "/support-desk/tickets/?scope=sla_breached&missing_rca=true&limit=100&q=%5BPROBE%5D", su_tok)
ok = s == 200 and any(r.get("id") == tid for r in (j or {}).get("items", []))
check("missing_rca=true returns probe", ok, f"(status {s})")
s, j = req("GET", "/support-desk/tickets/?scope=sla_breached&active_only=true&limit=100&q=%5BPROBE%5D", su_tok)
ok = s == 200 and any(r.get("id") == tid for r in (j or {}).get("items", []))
check("active_only=true keeps open probe", ok, f"(status {s})")
s, j = req("GET", "/support-desk/tickets/?scope=sla_breached&sort_by=sla_resolution_breached_at&sort_dir=asc&limit=5", su_tok)
check("sort by sla_resolution_breached_at", s == 200, f"(status {s})")

print("-- RCA roundtrip --")
s, _ = req("POST", f"/support-desk/tickets/{tid}/rca", su_tok, {
    "breach_reason": "understaffed_shift",
    "rca_summary": "Probe RCA: queue unattended over the weekend.",
    "rca_corrective": "Reassigned coverage.", "rca_preventive": "On-call rota updated.",
})
check("POST /rca", s == 200, f"(status {s})")
s, j = req("GET", "/support-desk/tickets/?scope=sla_breached&missing_rca=true&limit=100&q=%5BPROBE%5D", su_tok)
ok = s == 200 and not any(r.get("id") == tid for r in (j or {}).get("items", []))
check("missing_rca=true no longer returns probe", ok, f"(status {s})")
s, st2 = req("GET", "/support-desk/me/tickets/breached/stats", su_tok)
if isinstance(st, dict) and isinstance(st2, dict):
    check("missing_rca decreased after RCA", st2.get("missing_rca", 99) < st.get("missing_rca", 0),
          f"({st.get('missing_rca')} -> {st2.get('missing_rca')})")

print("-- due_soon (at-risk rail) --")
db.execute(text(
    "UPDATE support_tickets SET resolution_due_at = NOW() + INTERVAL '90 minutes', "
    "sla_resolution_breached = FALSE, sla_resolution_breached_at = NULL WHERE id = :i"), {"i": tid})
db.commit()
s, j = req("GET", "/support-desk/tickets/?scope=due_soon&limit=100&q=%5BPROBE%5D", su_tok)
ok = s == 200 and any(r.get("id") == tid for r in (j or {}).get("items", []))
check("scope=due_soon returns probe (due in 90m)", ok, f"(status {s})")
s, st3 = req("GET", "/support-desk/me/tickets/breached/stats", su_tok)
check("stats at_risk >= 1", isinstance(st3, dict) and st3.get("at_risk", 0) >= 1,
      f"({(st3 or {}).get('at_risk')})")

print("-- cleanup --")
s, _ = req("DELETE", f"/support-desk/tickets/{tid}", su_tok)
check("archive probe ticket", s in (200, 204), f"(status {s})")

db.close()
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
