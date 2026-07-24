"""Probe the Major-Incident command upgrade against the RUNNING backend (port 8000).

Covers, end to end:
  • MI-candidate proposal workflow: non-lead direct declare 403, propose 201,
    short-note 422, double-propose 409, non-lead confirm 403, mi_proposed flag lens,
    lead confirm (cadence armed + war-room auto-link), confirm-again 409,
    decline note gate (422/200), superuser direct declare consumes a pending proposal.
  • Stakeholder comms hub: owner-tier add-watcher (idempotent, 400 bogus user),
    audience=stakeholder broadcast stamps the comms log + fans to watchers,
    self-remove watcher.
  • Phase clocks: /phases ordered track + durations + MTTA/MTTR; 422 non-incident.
  • Executive sitrep: JSON blocks + PDF (GTK-tolerant 200/503).
Creates disposable probe tickets and archives them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_mi_command_upgrade.py
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
        with urllib.request.urlopen(r, timeout=40) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "pdf" in ctype:
                return resp.status, raw
            return resp.status, json.loads(raw.decode() or "null")
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


def mint(uid, tv) -> str:
    return create_access_token({"sub": str(uid), "tv": tv or 1})


db = SessionLocal()
su = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
if not su:
    print("No superuser found - abort"); sys.exit(1)
su_tok = mint(su[0], su[2])
su_id = str(su[0])
print(f"superuser: {su[1]}")

# Find a support team with a lead + an active NON-lead member (the proposal actor).
teams = db.execute(text(
    "SELECT id, name, lead_user_id, member_ids, member_roles FROM support_teams "
    "WHERE is_deleted = FALSE AND is_active = TRUE"
)).fetchall()
team_id = lead_uid = member_uid = None
for t in teams:
    roles = t[4] or {}
    lead = str(t[2]) if t[2] else next((u for u, r in roles.items() if r == "lead"), None)
    if not lead:
        continue
    for m in (t[3] or []):
        m = str(m)
        if m != lead and roles.get(m) != "lead" and m != su_id:
            act = db.execute(text(
                "SELECT id, token_version FROM users WHERE id = :i AND is_active = TRUE"
            ), {"i": m}).fetchone()
            lead_row = db.execute(text(
                "SELECT id, token_version FROM users WHERE id = :i AND is_active = TRUE"
            ), {"i": lead}).fetchone()
            if act and lead_row:
                team_id, lead_uid, member_uid = str(t[0]), lead, m
                lead_tok = mint(lead_row[0], lead_row[1])
                member_tok = mint(act[0], act[1])
                break
    if team_id:
        print(f"team: {t[1]}  lead: {lead_uid[:8]}  member: {member_uid[:8]}")
        break
if not team_id:
    print("No team with lead + non-lead member found - abort"); sys.exit(1)

made = []


def new_incident(subject, ttype="incident"):
    s, j = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": subject, "description": "probe - safe to ignore",
        "priority": "high", "ticket_type": ttype, "source": "internal",
    })
    if not (j and j.get("id")):
        print(f"  setup create failed ({s}) - abort"); sys.exit(1)
    made.append(j["id"])
    return j["id"]


def seat_on_team(tid):
    db.execute(text("UPDATE support_tickets SET team_id = :tm WHERE id = :i"),
               {"tm": team_id, "i": tid})
    db.commit()
    s, _ = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok,
               {"assigned_agent_id": member_uid})
    return s


print("-- T1: proposal happy path + gates --")
t1 = new_incident("probe: MI proposal happy path")
check("seat on team + assign member", seat_on_team(t1) == 200)
s, j = req("POST", f"/support-desk/tickets/{t1}/major-incident", member_tok,
           {"is_major_incident": True})
check("non-lead direct declare 403", s == 403, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t1}/mi-proposal", member_tok, {"note": "short"})
check("short note 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/mi-proposal", member_tok, {
    "note": "Error rate x40 and climbing across two regions - this needs the major desk",
    "business_impact": "high", "affected_users": 1200,
})
check("propose 201", s == 201 and j and j.get("ok"), f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/mi-proposal", member_tok, {
    "note": "second proposal that should bounce off the pending one",
})
check("double propose 409", s == 409, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t1}/mi-proposal/confirm", member_tok, {})
check("non-lead confirm 403", s == 403, f"(status {s})")

s, j = req("GET", "/support-desk/incidents/?lens=active&flag=mi_proposed&limit=100", member_tok)
rows = (j or {}).get("items") or []
mine = [r for r in rows if r.get("id") == t1]
check("mi_proposed lens carries T1", s == 200 and len(mine) == 1, f"(status {s}, rows {len(rows)})")
check("row carries proposer + note", bool(mine and mine[0].get("mi_proposed_by_name")
                                          and mine[0].get("mi_proposal_note")))
s, j = req("GET", "/support-desk/incidents/stats", member_tok)
check("stats mi_proposals_pending >= 1", s == 200 and (j or {}).get("mi_proposals_pending", 0) >= 1,
      f"(pending {(j or {}).get('mi_proposals_pending')})")

s, j = req("POST", f"/support-desk/tickets/{t1}/mi-proposal/confirm", lead_tok, {
    "update_interval_minutes": 30, "open_war_room": True, "note": "confirmed - stand up the room",
})
check("lead confirm 200", s == 200 and j and j.get("is_major_incident"), f"(status {s})")
check("cadence armed 30m", (j or {}).get("update_interval_minutes") == 30)
check("war room auto-linked", str((j or {}).get("war_room_url") or "").startswith("/user/support/queues/l2"))
s, j = req("GET", "/support-desk/incidents/?lens=major&limit=100", su_tok)
row = next((r for r in ((j or {}).get("items") or []) if r.get("id") == t1), None)
check("now on major lens, stamps cleared", bool(row) and row.get("is_major_incident")
      and not row.get("mi_proposed_at"), f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t1}/mi-proposal/confirm", lead_tok, {})
check("confirm again 409", s == 409, f"(status {s})")

print("-- T2: decline path + direct declare consumes proposal --")
t2 = new_incident("probe: MI proposal decline path")
check("seat on team + assign member", seat_on_team(t2) == 200)
s, _ = req("POST", f"/support-desk/tickets/{t2}/mi-proposal", member_tok, {
    "note": "Suspected regional outage building - flagging for the major desk early",
})
check("propose 201", s == 201, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t2}/mi-proposal/decline", lead_tok, {})
check("decline without note 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t2}/mi-proposal/decline", lead_tok, {
    "note": "Single-tenant blast radius - keep it on the normal track",
})
check("decline with note 200", s == 200 and (j or {}).get("declined"), f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t2}/mi-proposal", member_tok, {
    "note": "Re-proposing: the blast radius just grew to three more tenants",
})
check("re-propose after decline 201", s == 201, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t2}/major-incident", su_tok,
           {"is_major_incident": True})
check("superuser direct declare 200", s == 200 and (j or {}).get("is_major_incident"), f"(status {s})")
s, j = req("GET", "/support-desk/incidents/?lens=major&limit=100", su_tok)
row = next((r for r in ((j or {}).get("items") or []) if r.get("id") == t2), None)
check("direct declare consumed proposal", bool(row) and not row.get("mi_proposed_at"))

print("-- T3: comms hub + phases + sitrep --")
t3 = new_incident("probe: phases and sitrep")
s, _ = req("POST", f"/support-desk/tickets/{t3}/assign", su_tok, {"assigned_agent_id": su_id})
check("assign self 200", s == 200, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t3}/watchers", su_tok, {"user_id": member_uid})
check("add stakeholder 200", s == 200 and (j or {}).get("watching"), f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t3}/watchers", su_tok, {"user_id": member_uid})
check("add again idempotent", s == 200 and (j or {}).get("total") == 1, f"(total {(j or {}).get('total')})")
s, _ = req("POST", f"/support-desk/tickets/{t3}/watchers", su_tok,
           {"user_id": "00000000-0000-0000-0000-000000000001"})
check("bogus stakeholder 400", s == 400, f"(status {s})")

s, _ = req("POST", f"/support-desk/tickets/{t3}/ack", su_tok, {})
check("ack 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t3}/decision", su_tok, {
    "kind": "mitigation", "decision": "Probe: rolling the fix out region by region",
})
check("mitigation decision 201", s == 201, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t3}/status-update", su_tok, {
    "body": "Probe broadcast: mitigation in flight, next update in 30 minutes.",
    "phase": "mitigating", "audience": "stakeholder", "interval_minutes": 30,
})
check("stakeholder broadcast 200", s == 200, f"(status {s})")
s, j = req("GET", f"/support-desk/tickets/{t3}/activities", su_tok)
ups = [a for a in (j or []) if a.get("action") == "status_update"]
d = ups[-1]["detail"] if ups else {}
check("comms log carries audience", d.get("audience") == "stakeholder")
check("broadcast reached watchers", int(d.get("broadcast_watchers") or 0) >= 1,
      f"(watchers {d.get('broadcast_watchers')})")

s, _ = req("POST", f"/support-desk/tickets/{t3}/resolve", su_tok, {
    "resolution_code": "solved", "resolution_summary": "probe resolution - safe to ignore",
})
check("resolve 200", s == 200, f"(status {s})")

s, j = req("GET", f"/support-desk/incidents/{t3}/phases", su_tok)
keys = [p.get("key") for p in ((j or {}).get("phases") or [])]
ats = {p.get("key"): p.get("at") for p in ((j or {}).get("phases") or [])}
check("phases 200 + full track", s == 200 and keys == [
    "started", "detected", "declared", "acknowledged", "first_mitigation", "resolved", "closed"],
      f"(status {s})")
check("detected/acked/mitigation/resolved lit",
      all(ats.get(k) for k in ("detected", "acknowledged", "first_mitigation", "resolved")))
check("durations + mttr present", bool(((j or {}).get("durations_minutes") or {}).get("total") is not None
                                       and (j or {}).get("mttr_minutes") is not None))

s, j = req("GET", f"/support-desk/incidents/{t3}/sitrep", su_tok)
check("sitrep 200", s == 200 and (j or {}).get("ticket_number"), f"(status {s})")
check("sitrep blocks present", all(k in (j or {}) for k in
      ("phases", "roster", "impact", "cadence", "decisions", "sla", "children", "pir")))
check("sitrep decisions counted", ((j or {}).get("decisions") or {}).get("count", 0) >= 1)
check("sitrep watchers_total >= 1", (j or {}).get("watchers_total", 0) >= 1)
s, raw = req("GET", f"/support-desk/incidents/{t3}/sitrep.pdf", su_tok)
check("sitrep.pdf 200 %PDF or 503 GTK", (s == 200 and bytes(raw or b"")[:4] == b"%PDF") or s == 503,
      f"(status {s})")

t4 = new_incident("probe: non-incident phases 422", ttype="service_request")
s, _ = req("GET", f"/support-desk/incidents/{t4}/phases", su_tok)
check("phases on non-incident 422", s == 422, f"(status {s})")

s, j = req("DELETE", f"/support-desk/tickets/{t3}/watchers/{member_uid}", member_tok)
check("stakeholder self-remove 200", s == 200 and not (j or {}).get("watching"), f"(status {s})")

s, j = req("GET", "/support-desk/incidents/stats", su_tok)
check("stats new keys present", s == 200 and "phase_minutes_30d" in (j or {})
      and "actions_overdue" in (j or {}) and "mttd_minutes_30d" in (j or {}))

print("-- cleanup --")
for t in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{t}?reason=probe%20cleanup", su_tok)
    check(f"archive {t[:8]}", s in (204, 409, 404), f"(status {s})")
db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
