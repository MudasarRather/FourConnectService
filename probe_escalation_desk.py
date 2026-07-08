"""Probe the new escalation-desk workflow against the RUNNING backend (port 8000).

Mints JWTs directly (superuser + a non-superuser support agent), then exercises the
structured escalate (type/reason-code/response clock), escalation-ack (eMTTA, 409 rules,
re-arm on re-escalate), reasoned de-escalate (422 empty / clears live state at L0),
escalation-history (dwell), functional team routing, GET /me/tickets/escalated/stats
(shape + team seal), the SLA-breach auto-escalation sweep (fires once) and the
response-overdue sweep (day-throttled), plus the new bulk actions. Creates disposable
tickets and archives them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_escalation_desk.py
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


# Raw SQL — importing a single ORM model trips full mapper configuration.
db = SessionLocal()
su = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
ag = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = FALSE AND is_support_agent = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
team = db.execute(text(
    "SELECT id, name FROM support_teams WHERE is_deleted = FALSE LIMIT 1"
)).fetchone()
print(f"superuser: {su[1] if su else None}")
print(f"agent    : {ag[1] if ag else None}")
print(f"team     : {team[1] if team else None}")
if not su:
    print("No superuser found - abort"); sys.exit(1)
su_tok = mint(su)
ag_tok = mint(ag) if ag else None
su_id = str(su[0])

print("-- structured escalate lifecycle --")
s, j = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": "[PROBE] escalation lifecycle check", "description": "probe - safe to ignore",
    "priority": "high", "ticket_type": "incident", "source": "internal",
})
tid = j.get("id") if isinstance(j, dict) else None
check("create probe ticket", s in (200, 201) and tid, f"(status {s}, id {tid})")

if tid:
    # Ticket creation auto-routes (route_and_assign may hand it an owner + team) — strip
    # that so the unowned guard + level counting start from a clean L0/no-owner state.
    db.execute(text(
        "UPDATE support_tickets SET assigned_agent_id = NULL, team_id = NULL, status = 'open', "
        "is_escalated = FALSE, escalation_level = 0 WHERE id = :i"), {"i": tid})
    db.commit()
    # Guard: cannot escalate without an owner.
    s, _ = req("POST", f"/support-desk/tickets/{tid}/escalate", su_tok, {"reason": "probe"})
    check("escalate unowned -> 409", s == 409, f"(status {s})")
    s, j = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok, {"assigned_agent_id": su_id})
    check("assign owner", s == 200, f"(status {s})")

    # 422 on bad taxonomy.
    s, _ = req("POST", f"/support-desk/tickets/{tid}/escalate", su_tok, {"reason": "x", "reason_code": "bogus"})
    check("bad reason_code -> 422", s == 422, f"(status {s})")

    # Structured escalate L1 with a 60m ack clock.
    s, j = req("POST", f"/support-desk/tickets/{tid}/escalate", su_tok, {
        "reason": "Needs senior eyes", "reason_code": "complexity",
        "escalation_type": "hierarchical", "response_minutes": 60})
    check("escalate L1 structured", s == 200 and j.get("is_escalated") and j.get("escalation_level") == 1
          and j.get("escalation_reason_code") == "complexity" and j.get("escalation_type") == "hierarchical"
          and j.get("escalation_response_due_at") and j.get("escalated_by_name")
          and j.get("status") == "escalated",
          f"(status {s}, L{j.get('escalation_level') if j else '?'}, due {j.get('escalation_response_due_at') if j else '?'})")
    due1 = j.get("escalation_response_due_at") if j else None

    # Esc-ack: 200 then 409; due date kept (historical), overdue computed off unacked.
    s, j = req("POST", f"/support-desk/tickets/{tid}/escalation-ack", su_tok, {"note": "tier2 on it"})
    check("escalation-ack 200 + stamps", s == 200 and j.get("escalation_acknowledged_at")
          and j.get("escalation_acknowledged_by_name") and j.get("escalation_acked") is True
          and j.get("escalation_response_due_at") == due1,
          f"(status {s}, by {j.get('escalation_acknowledged_by_name') if j else '?'})")
    s, _ = req("POST", f"/support-desk/tickets/{tid}/escalation-ack", su_tok, {})
    check("escalation-ack repeat -> 409", s == 409, f"(status {s})")

    # Re-escalate L2: ack cleared, clock re-armed.
    s, j = req("POST", f"/support-desk/tickets/{tid}/escalate", su_tok, {
        "reason": "Still burning", "reason_code": "sla_risk"})
    check("re-escalate L2 clears ack + re-arms clock",
          s == 200 and j.get("escalation_level") == 2 and j.get("escalation_acknowledged_at") is None
          and j.get("escalation_response_due_at") and j.get("escalation_response_due_at") != due1
          and j.get("escalation_acked") is False,
          f"(status {s}, L{j.get('escalation_level') if j else '?'})")

    # Functional escalate L3 routed to a real team.
    if team:
        s, j = req("POST", f"/support-desk/tickets/{tid}/escalate", su_tok, {
            "reason": "Route to specialists", "reason_code": "expertise", "team_id": str(team[0])})
        check("functional escalate + team routing",
              s == 200 and j.get("escalation_level") == 3 and j.get("escalation_type") == "functional"
              and j.get("escalated_to_team_id") == str(team[0]) and j.get("escalated_to_team_name")
              and j.get("team_id") == str(team[0]),
              f"(status {s}, to {j.get('escalated_to_team_name') if j else '?'})")
    else:
        print("  [SKIP] no team on the desk; functional-routing probe skipped")

    # De-escalate: empty 422, reasoned 200; at L0 live state clears, history kept.
    s, _ = req("POST", f"/support-desk/tickets/{tid}/de-escalate", su_tok, {})
    check("de-escalate empty -> 422", s == 422, f"(status {s})")
    lvl = 3 if team else 2
    for i in range(lvl):
        s, j = req("POST", f"/support-desk/tickets/{tid}/de-escalate", su_tok, {"reason": f"stabilized step {i+1}"})
    check("de-escalate to L0 clears live state",
          s == 200 and j.get("escalation_level") == 0 and j.get("is_escalated") is False
          and j.get("escalation_acknowledged_at") is None and j.get("escalation_response_due_at") is None
          and j.get("escalated_to_team_id") is None and j.get("escalation_type") is None
          and j.get("escalated_at") and j.get("escalation_reason_code")
          and j.get("status") == "in_progress",
          f"(status {s}, status {j.get('status') if j else '?'})")
    s, _ = req("POST", f"/support-desk/tickets/{tid}/de-escalate", su_tok, {"reason": "again"})
    check("de-escalate at L0 -> 409", s == 409, f"(status {s})")

    # History: every rung with dwell + structured detail.
    s, j = req("GET", f"/support-desk/tickets/{tid}/escalation-history", su_tok)
    evs = j if isinstance(j, list) else []
    esc_evs = [e for e in evs if e.get("action") == "escalated"]
    de_evs = [e for e in evs if e.get("action") == "de_escalated"]
    check("escalation-history derives all rungs",
          s == 200 and len(esc_evs) == lvl and len(de_evs) == lvl
          and esc_evs[0].get("reason_code") == "complexity" and esc_evs[0].get("level") == 1
          and all("dwell_ms" in e for e in evs[:-1])
          and all(e.get("reason") for e in de_evs),
          f"(status {s}, {len(esc_evs)} up / {len(de_evs)} down)")

print("-- stats + lenses --")
s, j = req("GET", "/support-desk/me/tickets/escalated/stats", su_tok)
need = {"active_escalations", "by_level", "by_type", "by_reason_code", "unacked",
        "esc_response_overdue", "breaching_sla", "no_owner", "auto_escalated_count",
        "sla_breach_candidates", "avg_dwell_minutes", "emtta_minutes", "de_escalated_today",
        "resolved_today", "ack_coverage", "squad", "team_names"}
check("escalated/stats shape", s == 200 and j is not None and need.issubset(j.keys()),
      f"(status {s}, active {j.get('active_escalations') if j else '?'}, de-esc today {j.get('de_escalated_today') if j else '?'})")
check("de_escalated_today counts the probe", s == 200 and j and j.get("de_escalated_today", 0) >= 1,
      f"({j.get('de_escalated_today') if j else '?'})")
su_active = j.get("active_escalations") if j else 0

print("-- auto-escalation sweep (fires once) --")
s, j = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": "[PROBE] breach auto-escalation check", "description": "probe - safe to ignore",
    "priority": "medium", "ticket_type": "incident", "source": "internal",
})
tid2 = j.get("id") if isinstance(j, dict) else None
check("create breach fixture", s in (200, 201) and tid2, f"(status {s})")
if tid2:
    s, _ = req("POST", f"/support-desk/tickets/{tid2}/assign", su_tok, {"assigned_agent_id": su_id})
    # Force a resolution breach + in_progress via raw SQL (the sweep only lifts active work).
    db.execute(text(
        "UPDATE support_tickets SET sla_resolution_breached = TRUE, status = 'in_progress' WHERE id = :i"
    ), {"i": tid2})
    db.commit()
    s, _ = req("GET", "/support-desk/tickets/?scope=escalated&limit=1", su_tok)   # list-load hook fires
    s, j = req("GET", f"/support-desk/tickets/{tid2}", su_tok)
    check("sweep auto-escalated once",
          s == 200 and j.get("is_escalated") and j.get("escalation_level") == 1
          and j.get("auto_escalated_at") and j.get("escalation_reason_code") == "sla_breach"
          and j.get("auto_escalated") is True,
          f"(status {s}, L{j.get('escalation_level') if j else '?'}, code {j.get('escalation_reason_code') if j else '?'})")
    # De-escalate it, hit the list again: the once-only stamp must prevent a re-lift.
    s, _ = req("POST", f"/support-desk/tickets/{tid2}/de-escalate", su_tok, {"reason": "probe reset"})
    s, _ = req("GET", "/support-desk/tickets/?scope=escalated&limit=1", su_tok)
    s, j = req("GET", f"/support-desk/tickets/{tid2}", su_tok)
    check("once-only stamp holds after de-escalate",
          s == 200 and j.get("is_escalated") is False and j.get("escalation_level") == 0,
          f"(status {s}, L{j.get('escalation_level') if j else '?'})")

print("-- response-overdue sweep (day-throttled) --")
if tid2:
    # Re-escalate and backdate the ack clock into the past.
    s, _ = req("POST", f"/support-desk/tickets/{tid2}/escalate", su_tok, {"reason": "probe overdue", "reason_code": "other"})
    db.execute(text(
        "UPDATE support_tickets SET escalation_response_due_at = NOW() - INTERVAL '3 hours' WHERE id = :i"
    ), {"i": tid2})
    db.commit()
    req("GET", "/support-desk/tickets/?scope=escalated&limit=1", su_tok)
    req("GET", "/support-desk/tickets/?scope=escalated&limit=1", su_tok)   # second pass same day
    n = db.execute(text(
        "SELECT COUNT(*) FROM support_ticket_activities WHERE ticket_id = :i AND action = 'escalation_response_overdue'"
    ), {"i": tid2}).scalar()
    check("overdue nudge exactly once/day", n == 1, f"(activities {n})")
    s, j = req("GET", f"/support-desk/tickets/{tid2}", su_tok)
    check("enrichment flags esc_response_overdue",
          s == 200 and j.get("esc_response_overdue") is True and (j.get("esc_response_due_ms") or 0) < 0,
          f"(due_ms {j.get('esc_response_due_ms') if j else '?'})")

print("-- bulk actions --")
if tid2:
    s, j = req("POST", "/support-desk/tickets/bulk", su_tok, {"ids": [tid2], "action": "escalation_ack"})
    r0 = (j.get("results") or [{}])[0] if j else {}
    check("bulk escalation_ack applies", s == 200 and r0.get("ok") and not r0.get("skipped"), f"(status {s})")
    s, j = req("POST", "/support-desk/tickets/bulk", su_tok, {"ids": [tid2], "action": "escalation_ack"})
    r0 = (j.get("results") or [{}])[0] if j else {}
    check("bulk escalation_ack skip", s == 200 and r0.get("skipped"), f"({r0.get('error')})")
    s, _ = req("POST", "/support-desk/tickets/bulk", su_tok, {"ids": [tid2], "action": "de_escalate"})
    check("bulk de_escalate without reason -> 422", s == 422, f"(status {s})")
    s, j = req("POST", "/support-desk/tickets/bulk", su_tok, {"ids": [tid2], "action": "de_escalate", "reason": "probe bulk down"})
    r0 = (j.get("results") or [{}])[0] if j else {}
    check("bulk de_escalate applies", s == 200 and r0.get("ok") and not r0.get("skipped"), f"(status {s})")

print("-- terminal guard --")
if tid:
    s, _ = req("POST", f"/support-desk/tickets/{tid}/resolve", su_tok, {"resolution_code": "solved", "resolution_summary": "probe"})
    s, _ = req("POST", f"/support-desk/tickets/{tid}/escalation-ack", su_tok, {})
    check("esc-ack on terminal -> 409", s == 409, f"(status {s})")
    s, _ = req("POST", f"/support-desk/tickets/{tid}/escalate", su_tok, {"reason": "no"})
    check("escalate terminal -> 409", s == 409, f"(status {s})")

print("-- agent team seal --")
if ag_tok:
    s, j = req("GET", "/support-desk/tickets/?scope=escalated&limit=100", ag_tok)
    ag_total = j.get("total") if j else None
    check("agent escalated list 200 (sealed)", s == 200, f"(total {ag_total})")
    s, j = req("GET", "/support-desk/me/tickets/escalated/stats", ag_tok)
    check("agent escalated/stats 200 (sealed)", s == 200 and j is not None
          and (j.get("active_escalations") or 0) <= max(su_active, 100),
          f"(active {j.get('active_escalations') if j else '?'} vs desk {su_active})")
else:
    print("  [SKIP] no non-superuser support agent flagged; seal probes skipped")

# Cleanup: archive the probe tickets.
for t_ in (tid, tid2):
    if t_:
        s, _ = req("DELETE", f"/support-desk/tickets/{t_}?reason=probe%20cleanup", su_tok)
        check("archive probe ticket", s == 204, f"(status {s})")

db.close()
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
