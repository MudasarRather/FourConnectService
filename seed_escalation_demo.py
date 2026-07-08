"""Seed 5 escalation fixtures for the Thermal-Updraft desk headless verification.

Variety: tiers L1/L2/L3, acked / unacked / response-overdue, functional team routing,
auto-escalated (via the real sweep), long + short dwell. 4 land on the probe agent's
team (visible on the agent panel), 1 on another team / raw desk (seal proof: admin > agent).
Prints seeded ids to seed_escalation_demo.json for the cleanup pass.

Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" seed_escalation_demo.py
Cleanup:
    & "...python.exe" seed_escalation_demo.py --cleanup
"""
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

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
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_escalation_demo.json")


def req(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


db = SessionLocal()
su = db.execute(text("SELECT id, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1")).fetchone()
tok = create_access_token({"sub": str(su[0]), "tv": su[1] or 1})
su_id = str(su[0])
ag = db.execute(text("SELECT id FROM users WHERE is_superuser = FALSE AND is_support_agent = TRUE AND is_active = TRUE LIMIT 1")).fetchone()
# The agent's first team (member or lead) — fixtures routed here show on the agent panel.
ag_team = None
if ag:
    ag_team = db.execute(text(
        "SELECT id, name FROM support_teams WHERE is_deleted = FALSE AND "
        "(lead_user_id = :a OR member_ids @> to_jsonb(ARRAY[CAST(:a AS TEXT)])) LIMIT 1"), {"a": str(ag[0])}).fetchone()
other_team = db.execute(text(
    "SELECT id, name FROM support_teams WHERE is_deleted = FALSE AND id != COALESCE(:t, '00000000-0000-0000-0000-000000000000'::uuid) LIMIT 1"),
    {"t": str(ag_team[0]) if ag_team else None}).fetchone()
print(f"agent team: {ag_team[1] if ag_team else None} · other team: {other_team[1] if other_team else None}")

if "--cleanup" in sys.argv:
    try:
        ids = json.load(open(STATE))["ids"]
    except Exception:
        print("no seed state found"); sys.exit(0)
    for i in ids:
        s, _ = req("DELETE", f"/support-desk/tickets/{i}?reason=demo%20seed%20cleanup", tok)
        print(f"  archived {i} -> {s}")
    os.remove(STATE)
    db.close(); print("cleanup done"); sys.exit(0)

FIX = [
    dict(subject="Payment gateway intermittently returns 502", priority="critical",
         lifts=[dict(reason="Revenue-impacting — needs Tier 2 immediately", reason_code="sla_risk", escalation_type="hierarchical"),
                dict(reason="No stable mitigation after first lift", reason_code="complexity", escalation_type="hierarchical")],
         ack=False, overdue=True, dwell_h=5, team="agent"),
    dict(subject="VIP org locked out of the client portal", priority="high",
         lifts=[dict(reason="Key account executive visibility", reason_code="vip", escalation_type="hierarchical")],
         ack=True, overdue=False, dwell_h=2, team="agent"),
    dict(subject="Ledger export produces corrupt XLSX", priority="high",
         lifts=[dict(reason="Needs the data-platform specialists", reason_code="expertise", escalation_type="functional", team="agent")],
         ack=False, overdue=False, dwell_h=0.6, team="agent"),
    dict(subject="Recurring SSO loop on mobile after patch", priority="medium",
         lifts=[dict(reason="Third reopen this month", reason_code="repeat_incident", escalation_type="hierarchical"),
                dict(reason="Engineering review required", reason_code="complexity", escalation_type="hierarchical"),
                dict(reason="Leadership visibility requested", reason_code="customer_request", escalation_type="hierarchical")],
         ack=True, overdue=False, dwell_h=30, team="agent"),
    dict(subject="Duplicate invoices issued to two orgs", priority="high",
         lifts=[dict(reason="Cross-team billing impact", reason_code="complexity", escalation_type="hierarchical")],
         ack=False, overdue=False, dwell_h=1.2, team="other"),
]

ids = []
for fx in FIX:
    s, j = req("POST", "/support-desk/tickets/", tok, {
        "subject": f"[DEMO] {fx['subject']}", "description": "Thermal-Updraft verification seed — safe to archive.",
        "priority": fx["priority"], "ticket_type": "incident", "source": "internal",
    })
    tid = j.get("id"); ids.append(tid)
    req("POST", f"/support-desk/tickets/{tid}/assign", tok, {"assigned_agent_id": su_id})
    for li, lift in enumerate(fx["lifts"]):
        body = {k: v for k, v in lift.items() if k != "team"}
        if lift.get("team") == "agent" and ag_team:
            body["team_id"] = str(ag_team[0])
        s, j = req("POST", f"/support-desk/tickets/{tid}/escalate", tok, body)
        if s != 200:
            print(f"  !! escalate failed {s}: {j}")
    if fx["ack"]:
        req("POST", f"/support-desk/tickets/{tid}/escalation-ack", tok, {"note": "On it — receiving tier"})
    # dwell + overdue + team routing touch-ups (raw SQL — display-only backdating)
    db.execute(text("UPDATE support_tickets SET escalated_at = NOW() - (:h || ' hours')::interval WHERE id = :i"),
               {"h": fx["dwell_h"], "i": tid})
    if fx["overdue"]:
        db.execute(text("UPDATE support_tickets SET escalation_response_due_at = NOW() - INTERVAL '90 minutes' WHERE id = :i"), {"i": tid})
    team_row = ag_team if fx["team"] == "agent" else other_team
    if team_row:
        db.execute(text("UPDATE support_tickets SET team_id = :t WHERE id = :i"), {"t": str(team_row[0]), "i": tid})
    db.commit()
    print(f"  seeded {tid} · L{len(fx['lifts'])} · {'acked' if fx['ack'] else 'unacked'}{' · OVERDUE' if fx['overdue'] else ''}")

# one auto-escalation via the REAL sweep: breach an owned in_progress ticket, then list-load
s, j = req("POST", "/support-desk/tickets/", tok, {
    "subject": "[DEMO] Report scheduler silently skipping runs", "priority": "medium",
    "description": "Thermal-Updraft verification seed — safe to archive.", "ticket_type": "incident", "source": "internal",
})
tid = j.get("id"); ids.append(tid)
req("POST", f"/support-desk/tickets/{tid}/assign", tok, {"assigned_agent_id": su_id})
db.execute(text("UPDATE support_tickets SET sla_resolution_breached = TRUE, status = 'in_progress', team_id = :t WHERE id = :i"),
           {"t": str(ag_team[0]) if ag_team else None, "i": tid})
db.commit()
req("GET", "/support-desk/tickets/?scope=escalated&limit=1", tok)   # sweep fires
print(f"  seeded {tid} · AUTO (sweep)")

json.dump({"ids": ids}, open(STATE, "w"))
db.close()
print(f"done — {len(ids)} fixtures. State: {STATE}")
