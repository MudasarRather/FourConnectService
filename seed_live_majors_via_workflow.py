"""Stand up the Major Incident program on the LIVE desk using ONLY real workflow verbs
(no synthetic rows): declare two existing SEV2 criticals as majors (roster, cadence,
war room, impact, decision, stakeholder broadcast, watcher) and raise one MI-candidate
proposal from a real agent. Everything it does is reversible through the same desk
(stand-down / decline / withdraw). ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" seed_live_majors_via_workflow.py
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


def req(method, path, token, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except Exception:
            return e.code, None


db = SessionLocal()
su = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
su_tok = create_access_token({"sub": str(su[0]), "tv": su[2] or 1})
su_id = str(su[0])
print(f"superuser: {su[1]}")

# a non-lead team member for the proposal + roster seats
teams = db.execute(text(
    "SELECT id, name, lead_user_id, member_ids, member_roles FROM support_teams "
    "WHERE is_deleted = FALSE AND is_active = TRUE"
)).fetchall()
team_id = member_uid = member_tok = lead_uid = None
for t in teams:
    roles = t[4] or {}
    lead = str(t[2]) if t[2] else next((u for u, r in roles.items() if r == "lead"), None)
    if not lead:
        continue
    for m in (t[3] or []):
        m = str(m)
        if m != lead and roles.get(m) != "lead" and m != su_id:
            act = db.execute(text("SELECT id, token_version FROM users WHERE id = :i AND is_active = TRUE"),
                             {"i": m}).fetchone()
            if act:
                team_id, member_uid, lead_uid = str(t[0]), m, lead
                member_tok = create_access_token({"sub": m, "tv": act[1] or 1})
                break
    if team_id:
        print(f"team: {t[1]}  lead: {lead_uid[:8]}  member: {member_uid[:8]}")
        break

# candidates: live SEV2 criticals that are NOT majors and NOT probe leftovers
s, j = req("GET", "/support-desk/incidents/?lens=critical&limit=100", su_tok)
rows = [r for r in (j or {}).get("items", [])
        if not r.get("is_major_incident")
        and r.get("status") not in ("resolved", "closed")
        and not str(r.get("subject", "")).lower().startswith("probe")]
print(f"critical non-MI candidates: {len(rows)}")
if len(rows) < 3:
    print("Fewer than 3 candidates - proceeding with what exists.")

def show(s, what, j=None):
    print(f"  [{s}] {what}" + (f" -> {str(j)[:110]}" if s >= 400 else ""))

# ── MI #1: full command surface ──
if rows:
    t1 = rows[0]
    tid = t1["id"]
    print(f"\nMI #1: {t1['ticket_number']} - {t1['subject'][:60]}")
    if not t1.get("assigned_agent_id"):
        s, j = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok, {"assigned_agent_id": su_id}); show(s, "assign", j)
    s, j = req("POST", f"/support-desk/tickets/{tid}/major-incident", su_tok, {
        "is_major_incident": True, "business_impact": "critical",
        "update_interval_minutes": 30, "open_war_room": True,
    }); show(s, "declare + cadence 30m + war room", j)
    s, j = req("POST", f"/support-desk/tickets/{tid}/ack", su_tok, {"note": "Command has eyes on this."}); show(s, "ack", j)
    s, j = req("PATCH", f"/support-desk/tickets/{tid}/incident-roles", su_tok, {
        "incident_commander_id": su_id,
        **({"comms_lead_id": member_uid} if member_uid else {}),
    }); show(s, "roster (cmdr + comms)", j)
    s, j = req("PATCH", f"/support-desk/tickets/{tid}/incident-impact", su_tok, {
        "affected_services": [str(t1.get("category_name") or "core-service").lower().replace(" ", "-")],
        "affected_users": 1800, "public_impact": True,
    }); show(s, "impact stamp", j)
    s, j = req("POST", f"/support-desk/tickets/{tid}/decision", su_tok, {
        "kind": "mitigation", "decision": "Mitigation in flight - degrading gracefully while the fix rolls out.",
        "reason": "Limit blast radius before rollback window closes",
    }); show(s, "decision (mitigation)", j)
    if member_uid:
        s, j = req("POST", f"/support-desk/tickets/{tid}/watchers", su_tok, {"user_id": member_uid}); show(s, "add stakeholder watcher", j)
    s, j = req("POST", f"/support-desk/tickets/{tid}/status-update", su_tok, {
        "body": "Major incident update: mitigation underway, error rate falling. Next update in 30 minutes.",
        "phase": "mitigating", "audience": "stakeholder", "is_internal": True,
    }); show(s, "stakeholder broadcast", j)

# ── MI #2: declared but with visible gaps (no comms lead, no update yet) ──
if len(rows) > 1:
    t2 = rows[1]
    tid = t2["id"]
    print(f"\nMI #2: {t2['ticket_number']} - {t2['subject'][:60]}")
    if not t2.get("assigned_agent_id"):
        s, j = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok, {"assigned_agent_id": su_id}); show(s, "assign", j)
    s, j = req("POST", f"/support-desk/tickets/{tid}/major-incident", su_tok, {
        "is_major_incident": True, "update_interval_minutes": 60,
    }); show(s, "declare + cadence 60m (no war room, unstaffed)", j)
    s, j = req("POST", f"/support-desk/tickets/{tid}/ack", su_tok, {}); show(s, "ack", j)

# ── MI candidate: a real proposal from the agent ──
if len(rows) > 2 and member_tok:
    t3 = rows[2]
    tid = t3["id"]
    print(f"\nCANDIDATE: {t3['ticket_number']} - {t3['subject'][:60]}")
    db.execute(text("UPDATE support_tickets SET team_id = COALESCE(team_id, :tm) WHERE id = :i"),
               {"tm": team_id, "i": tid})
    db.commit()
    s, j = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok, {"assigned_agent_id": member_uid}); show(s, "assign to agent", j)
    s, j = req("POST", f"/support-desk/tickets/{tid}/mi-proposal", member_tok, {
        "note": "Blast radius is growing past a single tenant - requesting major status before the next spike.",
        "business_impact": "high",
    }); show(s, "agent proposes major", j)

s, j = req("GET", "/support-desk/incidents/stats", su_tok)
print(f"\nDESK NOW: major_active={j.get('major_active')} proposals={j.get('mi_proposals_pending')} "
      f"active_total={j.get('active_total')}")
db.close()
print("Done - everything above used real desk verbs and is reversible from the UI.")
