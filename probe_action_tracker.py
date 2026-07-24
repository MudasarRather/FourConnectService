"""Probe the PIR action-item tracker against the RUNNING backend (port 8000).

Covers: the /incidents/actions rollup (counts, overdue-first ordering, filters),
the status-only PATCH carve-out on approved reports (draft 409, bad kind 422,
index 404, open->done 200 with audit stamps, same-status 422, named-owner
self-close off-roster), and stats.actions_overdue. Creates one disposable
MI + PIR and archives the ticket at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_action_tracker.py
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta

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

teams = db.execute(text(
    "SELECT id, name, lead_user_id, member_ids, member_roles FROM support_teams "
    "WHERE is_deleted = FALSE AND is_active = TRUE"
)).fetchall()
team_id = member_uid = member_tok = None
for t in teams:
    roles = t[4] or {}
    lead = str(t[2]) if t[2] else next((u for u, r in roles.items() if r == "lead"), None)
    for m in (t[3] or []):
        m = str(m)
        if m != lead and roles.get(m) != "lead" and m != su_id:
            act = db.execute(text(
                "SELECT id, token_version FROM users WHERE id = :i AND is_active = TRUE"
            ), {"i": m}).fetchone()
            if act:
                team_id, member_uid = str(t[0]), m
                member_tok = mint(act[0], act[1])
                break
    if team_id:
        print(f"team: {t[1]}  member(owner): {member_uid[:8]}")
        break
if not team_id:
    print("No team with a non-lead member found - abort"); sys.exit(1)

made = []
# Two days back, not one: the backend computes "today" in UTC while this probe runs on
# local time (IST) — a 1-day delta straddles midnight and reads as "today" server-side.
yesterday = (date.today() - timedelta(days=2)).isoformat()
tomorrow = (date.today() + timedelta(days=2)).isoformat()

print("-- setup: MI -> resolve -> PIR with action registers --")
s, j = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": "probe: action tracker", "description": "probe - safe to ignore",
    "priority": "high", "ticket_type": "incident", "source": "internal",
})
tid = (j or {}).get("id")
check("create incident", s in (200, 201) and tid, f"(status {s})")
if not tid:
    sys.exit(1)
made.append(tid)
db.execute(text("UPDATE support_tickets SET team_id = :tm WHERE id = :i"), {"tm": team_id, "i": tid})
db.commit()
s, _ = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok, {"assigned_agent_id": su_id})
check("assign 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{tid}/major-incident", su_tok, {"is_major_incident": True})
check("declare MI 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{tid}/resolve", su_tok, {
    "resolution_code": "solved", "resolution_summary": "probe resolution - safe to ignore",
})
check("resolve 200", s == 200, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{tid}/pir", su_tok, {"title": "probe action tracker PIR"})
pid = (j or {}).get("id")
check("PIR created", s in (200, 201) and pid, f"(status {s})")
if not pid:
    sys.exit(1)
s, _ = req("PATCH", f"/support-desk/incidents/pirs/{pid}", su_tok, {
    "executive_summary": "Probe summary - safe to ignore.",
    "root_cause": "Probe root cause.",
    "corrective_actions": [
        {"action": "Probe corrective A - overdue burn", "owner_id": member_uid,
         "owner_name": "probe member", "target_date": yesterday, "status": "open"},
        {"action": "Probe corrective B - future", "owner_id": su_id,
         "owner_name": "probe admin", "target_date": tomorrow, "status": "open"},
    ],
    "preventive_actions": [{"action": "Probe preventive C - no date", "status": "open"}],
})
check("PIR sections saved", s == 200, f"(status {s})")

print("-- draft seal: status PATCH refused before approval --")
s, _ = req("PATCH", f"/support-desk/incidents/pirs/{pid}/actions/corrective/0", su_tok,
           {"status": "done"})
check("draft PIR action patch 409", s == 409, f"(status {s})")

s, _ = req("POST", f"/support-desk/incidents/pirs/{pid}/submit", su_tok, {})
check("submit 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/incidents/pirs/{pid}/approve", su_tok, {})
check("approve 200", s == 200, f"(status {s})")

print("-- rollup board --")
s, j = req("GET", "/support-desk/incidents/actions?limit=100", su_tok)
items = (j or {}).get("items") or []
mine = [r for r in items if r.get("pir_id") == pid]
check("rollup 200 + rows", s == 200 and len(mine) == 3, f"(status {s}, mine {len(mine)})")
check("counts.overdue >= 1", ((j or {}).get("counts") or {}).get("overdue", 0) >= 1,
      f"(overdue {((j or {}).get('counts') or {}).get('overdue')})")
check("overdue burns first", bool(items) and items[0].get("overdue") is True)
over = next((r for r in mine if r.get("overdue")), None)
check("overdue row is corrective A", bool(over) and "corrective A" in over.get("action", ""))
s, j = req("GET", "/support-desk/incidents/actions?kind=preventive&limit=100", su_tok)
kinds = {r.get("kind") for r in ((j or {}).get("items") or [])}
check("kind filter", s == 200 and kinds <= {"preventive"}, f"(kinds {kinds})")
s, j = req("GET", f"/support-desk/incidents/actions?owner_id={member_uid}&limit=100", su_tok)
owners = {r.get("owner_id") for r in ((j or {}).get("items") or [])}
check("owner filter", s == 200 and owners <= {member_uid}, f"(owners {len(owners)})")

s, j = req("GET", "/support-desk/incidents/stats", su_tok)
check("stats actions_overdue >= 1", s == 200 and (j or {}).get("actions_overdue", 0) >= 1,
      f"(overdue {(j or {}).get('actions_overdue')})")

print("-- status PATCH gates --")
s, _ = req("PATCH", f"/support-desk/incidents/pirs/{pid}/actions/bogus/0", su_tok, {"status": "done"})
check("bad kind 422", s == 422, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/incidents/pirs/{pid}/actions/corrective/99", su_tok, {"status": "done"})
check("bad index 404", s == 404, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/incidents/pirs/{pid}/actions/corrective/1", su_tok, {"status": "open"})
check("same status 422", s == 422, f"(status {s})")

print("-- named-owner self-close (off-roster member) --")
s, j = req("PATCH", f"/support-desk/incidents/pirs/{pid}/actions/corrective/0", member_tok,
           {"status": "done", "note": "probe: shipped the circuit breaker"})
check("owner self-close 200", s == 200 and (j or {}).get("status") == "done", f"(status {s})")
check("audit stamps present", bool((j or {}).get("status_changed_at")
                                   and (j or {}).get("status_changed_by")))
check("note recorded", (j or {}).get("status_note") == "probe: shipped the circuit breaker")
check("no longer overdue", (j or {}).get("overdue") is False)

s, j = req("PATCH", f"/support-desk/incidents/pirs/{pid}/actions/corrective/0", su_tok,
           {"status": "done"})
check("re-done same status 422", s == 422, f"(status {s})")

s, j = req("GET", f"/support-desk/incidents/pirs/{pid}", su_tok)
acts = ((j or {}).get("corrective_actions") or [])
check("PIR document reflects status", bool(acts) and acts[0].get("status") == "done"
      and (j or {}).get("status") == "approved")

print("-- cleanup --")
for t in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{t}?reason=probe%20cleanup", su_tok)
    check(f"archive {t[:8]}", s in (204, 409, 404), f"(status {s})")
db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
