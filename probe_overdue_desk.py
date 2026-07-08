"""Probe the new Overdue desk (Gravity Well) workflow against the RUNNING backend (8000).

Mints JWTs directly (superuser + a non-superuser support agent), then exercises:
scope=overdue with the new overdue_kind refinement (default resolution-only semantics
unchanged; response = past response target with no first reply; any = either clock;
paused tickets excluded in every variant), GET /me/tickets/overdue/stats (shape,
kind split, lateness ladder, frozen_excluded, team seal: agent <= superuser), the
command-center + /me mirrors, and POST /tickets/{id}/nudge-owner (unowned -> 409,
happy path writes ONE owner_nudge activity, 24h throttle -> 409, self-nudge -> 409,
terminal -> 409). Creates three disposable tickets and soft-deletes them at the end.
ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_overdue_desk.py
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


def mk(token, subject):
    s, j = req("POST", "/support-desk/tickets/", token, {
        "subject": subject, "description": "probe - safe to ignore",
        "priority": "high", "ticket_type": "incident", "source": "internal",
    })
    return (j.get("id") if isinstance(j, dict) else None), s


def ids_of(j):
    return {r.get("id") for r in (j or {}).get("items", [])} if isinstance(j, dict) else set()


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

# Tombstone any leftovers from an earlier crashed run so counts stay clean.
db.execute(text("UPDATE support_tickets SET is_deleted = TRUE WHERE subject LIKE '[PROBE-OVD]%'"))
db.commit()

print("-- setup: three probe tickets (both-overdue / response-only / frozen) --")
tA, sA = mk(su_tok, "[PROBE-OVD] both clocks missed")
tB, sB = mk(su_tok, "[PROBE-OVD] response clock only")
tC, sC = mk(su_tok, "[PROBE-OVD] paused past-due (frozen)")
check("create 3 probe tickets", all([tA, tB, tC]), f"({sA},{sB},{sC})")
if not all([tA, tB, tC]):
    sys.exit(1)

# Admin-created tickets may not stamp raised_by — make the superuser the requester so
# the /me involvement mirror (raised_by OR assigned OR collaborator) can see them.
db.execute(text("UPDATE support_tickets SET raised_by_user_id = :u WHERE id IN (:a, :b, :c)"),
           {"u": str(su[0]), "a": tA, "b": tB, "c": tC})

# A: open, unpaused, BOTH clocks past, no first reply, unowned.
db.execute(text(
    "UPDATE support_tickets SET status='open', sla_paused_since=NULL, assigned_agent_id=NULL, "
    "response_due_at = NOW() - INTERVAL '4 hours', resolution_due_at = NOW() - INTERVAL '3 hours', "
    "first_responded_at = NULL, resolved_at = NULL WHERE id = :i"), {"i": tA})
# B: open, unpaused, response target missed, resolution still ahead.
db.execute(text(
    "UPDATE support_tickets SET status='open', sla_paused_since=NULL, "
    "response_due_at = NOW() - INTERVAL '2 hours', resolution_due_at = NOW() + INTERVAL '5 hours', "
    "first_responded_at = NULL, resolved_at = NULL WHERE id = :i"), {"i": tB})
# C: past-due on paper but the clock is FROZEN (pending customer pause).
db.execute(text(
    "UPDATE support_tickets SET status='pending_customer', sla_paused_since = NOW() - INTERVAL '1 hour', "
    "resolution_due_at = NOW() - INTERVAL '2 hours', resolved_at = NULL WHERE id = :i"), {"i": tC})
db.commit()

print("-- scope=overdue: legacy default (resolution clock only, paused excluded) --")
s, j = req("GET", "/support-desk/tickets/?scope=overdue&limit=100&q=%5BPROBE-OVD%5D", su_tok)
got = ids_of(j)
check("default includes resolution-overdue A", s == 200 and tA in got, f"(status {s})")
check("default EXCLUDES response-only B", tB not in got)
check("default EXCLUDES frozen C", tC not in got)

print("-- overdue_kind=any widens to the response clock --")
s, j = req("GET", "/support-desk/tickets/?scope=overdue&overdue_kind=any&limit=100&q=%5BPROBE-OVD%5D", su_tok)
got = ids_of(j)
check("any includes A", s == 200 and tA in got, f"(status {s})")
check("any includes response-only B", tB in got)
check("any still EXCLUDES frozen C", tC not in got)
s, j = req("GET", "/support-desk/tickets/?scope=overdue&overdue_kind=response&limit=100&q=%5BPROBE-OVD%5D", su_tok)
got = ids_of(j)
check("response includes B (no first reply)", s == 200 and tB in got, f"(status {s})")
s, j = req("GET", "/support-desk/tickets/?scope=overdue&overdue_kind=resolution&limit=100&q=%5BPROBE-OVD%5D", su_tok)
got = ids_of(j)
check("resolution == default (A yes, B no)", s == 200 and tA in got and tB not in got, f"(status {s})")

print("-- panel mirrors: /me list + command-center honour overdue_kind --")
s, j = req("GET", "/support-desk/me/tickets/?scope=overdue&overdue_kind=any&limit=100&q=%5BPROBE-OVD%5D", su_tok)
got = ids_of(j)
check("/me scope=overdue&any returns A+B (raised by me)", s == 200 and tA in got and tB in got, f"(status {s})")
s, j = req("GET", "/support-desk/me/tickets/command-center?scope=overdue&overdue_kind=any&limit=150&q=%5BPROBE-OVD%5D", su_tok)
got = ids_of(j)
check("command-center scope=overdue&any returns A+B", s == 200 and tA in got and tB in got, f"(status {s})")

print("-- GET /me/tickets/overdue/stats: shape + numbers --")
s, st = req("GET", "/support-desk/me/tickets/overdue/stats", su_tok)
check("stats 200 + dict", s == 200 and isinstance(st, dict), f"(status {s})")
if isinstance(st, dict):
    for f in ("total", "swept_now", "resolution_overdue", "response_overdue", "both_overdue",
              "unassigned", "not_escalated", "critical", "frozen_excluded",
              "by_priority", "by_status", "by_late", "total_late_minutes",
              "at_risk", "imminent", "recovered_today", "squad", "team_names", "oldest"):
        check(f"stats field {f}", f in st)
    check("total >= 2 (A + B)", st.get("total", 0) >= 2, f"({st.get('total')})")
    check("response_overdue >= 2 (A + B)", st.get("response_overdue", 0) >= 2, f"({st.get('response_overdue')})")
    check("resolution_overdue >= 1 (A)", st.get("resolution_overdue", 0) >= 1, f"({st.get('resolution_overdue')})")
    check("both_overdue >= 1 (A)", st.get("both_overdue", 0) >= 1, f"({st.get('both_overdue')})")
    check("frozen_excluded >= 1 (C)", st.get("frozen_excluded", 0) >= 1, f"({st.get('frozen_excluded')})")
    check("unassigned >= 1 (A unowned)", st.get("unassigned", 0) >= 1, f"({st.get('unassigned')})")
    check("total_late_minutes > 0", st.get("total_late_minutes", 0) > 0, f"({st.get('total_late_minutes')}m)")
    check("lateness ladder non-empty", bool(st.get("by_late")), f"({st.get('by_late')})")
    check("oldest carries a ticket ref", isinstance(st.get("oldest"), dict) and st["oldest"].get("late_minutes", 0) > 0,
          f"({(st.get('oldest') or {}).get('ticket_number')})")

print("-- team seal: agent numbers are a subset of the superuser's --")
if ag_tok:
    s, ast = req("GET", "/support-desk/me/tickets/overdue/stats", ag_tok)
    check("agent stats 200", s == 200, f"(status {s})")
    if isinstance(ast, dict) and isinstance(st, dict):
        check("agent total <= superuser total", ast.get("total", 0) <= st.get("total", 0),
              f"({ast.get('total')} <= {st.get('total')})")
    s, aj = req("GET", "/support-desk/tickets/?scope=overdue&overdue_kind=any&limit=100", ag_tok)
    a_total = (aj or {}).get("total", 0) if isinstance(aj, dict) else 0
    s2, sj = req("GET", "/support-desk/tickets/?scope=overdue&overdue_kind=any&limit=100", su_tok)
    s_total = (sj or {}).get("total", 0) if isinstance(sj, dict) else 0
    check("agent overdue list <= superuser's", s == 200 and s2 == 200 and a_total <= s_total,
          f"({a_total} <= {s_total})")
else:
    print("  [SKIP] no non-superuser agent found")

print("-- nudge-owner: guards + happy path + 24h throttle --")
s, j = req("POST", f"/support-desk/tickets/{tA}/nudge-owner", su_tok, {"message": "probe nudge"})
check("unowned ticket -> 409", s == 409, f"(status {s})")
if ag:
    s, j = req("POST", f"/support-desk/tickets/{tA}/assign", su_tok, {"assigned_agent_id": str(ag[0])})
    check("assign A to agent", s == 200, f"(status {s})")
    s, j = req("POST", f"/support-desk/tickets/{tA}/nudge-owner", su_tok, {"message": "probe nudge"})
    check("nudge assigned ticket -> 200", s == 200, f"(status {s})")
    n = db.execute(text(
        "SELECT COUNT(*) FROM support_ticket_activities WHERE ticket_id = :i AND action = 'owner_nudge'"),
        {"i": tA}).scalar()
    check("ONE owner_nudge activity written", n == 1, f"(count {n})")
    s, j = req("POST", f"/support-desk/tickets/{tA}/nudge-owner", su_tok, {})
    check("second nudge inside 24h -> 409", s == 409, f"(status {s})")
    s, j = req("POST", f"/support-desk/tickets/{tA}/nudge-owner", ag_tok, {})
    check("self-nudge (owner calls) -> 409 or 404-out-of-scope", s in (404, 409), f"(status {s})")
    db.execute(text("UPDATE support_tickets SET status='resolved', resolved_at=NOW() WHERE id = :i"), {"i": tA})
    db.commit()
    db.execute(text("DELETE FROM support_ticket_activities WHERE ticket_id=:i AND action='owner_nudge'"), {"i": tA})
    db.commit()
    s, j = req("POST", f"/support-desk/tickets/{tA}/nudge-owner", su_tok, {})
    check("terminal ticket -> 409", s == 409, f"(status {s})")
else:
    print("  [SKIP] no agent to assign - throttle/self tests skipped")

print("-- cleanup: soft-delete the probes --")
db.execute(text("UPDATE support_tickets SET is_deleted = TRUE WHERE id IN (:a, :b, :c)"),
           {"a": tA, "b": tB, "c": tC})
db.commit()
db.close()
check("probes tombstoned", True)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
