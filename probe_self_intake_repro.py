"""Reproduce: /user/support/tickets/new 'success but ticket missing' + team gate.

For a PLAIN EMPLOYEE and a SUPPORT AGENT (both non-superuser):
  1. POST /support-desk/me/tickets/ (minimal incident payload)
  2. verify the row exists in the DB
  3. verify it appears in GET /support-desk/me/tickets/ (the My Tickets list)
  4. report the team-gate context: which teams handle 'incident', is the caller a
     member, agent flag — so we can see WHY the gate allowed/blocked.
Cleans up every created ticket. ASCII-only.
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
STAMP = os.urandom(3).hex().upper()
created = []


def req(method, path, token, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except Exception:
            return e.code, None


db = SessionLocal()

# Who handles 'incident'?
teams = db.execute(text(
    "SELECT id, name, request_types, category_ids, member_ids, is_active, is_deleted FROM support_teams "
    "WHERE is_deleted = FALSE AND is_active = TRUE"
)).fetchall()
handling = [t for t in teams if 'incident' in (t[2] or [])]
print(f"active teams: {len(teams)}; teams whose request_types include 'incident': {len(handling)}")
for t in handling:
    print(f"  - {t[1]} members={len(t[4] or [])}")

member_ids = set()
for t in handling:
    for m in (t[4] or []):
        member_ids.add(str(m))

emp = db.execute(text(
    "SELECT id, email, token_version, is_support_agent FROM users "
    "WHERE is_superuser = FALSE AND is_active = TRUE AND is_support_agent = FALSE "
    "ORDER BY created_at LIMIT 5"
)).fetchall()
agent = db.execute(text(
    "SELECT id, email, token_version, is_support_agent FROM users "
    "WHERE is_superuser = FALSE AND is_active = TRUE AND is_support_agent = TRUE "
    "ORDER BY created_at LIMIT 5"
)).fetchall()

# prefer an employee who is NOT on any handling team (to test the gate)
emp_out = next((u for u in emp if str(u[0]) not in member_ids), emp[0] if emp else None)
agent_u = agent[0] if agent else None
print(f"plain employee (not on handling team): {emp_out[1] if emp_out else None}")
print(f"support agent: {agent_u[1] if agent_u else None}")


def run_case(label, row):
    if not row:
        print(f"-- {label}: no such user available --")
        return
    tok = create_access_token({"sub": str(row[0]), "tv": row[2] or 1})
    on_team = str(row[0]) in member_ids
    print(f"-- {label} ({row[1]}) agent={bool(row[3])} on_handling_team={on_team} --")
    s, j = req("POST", "/support-desk/me/tickets/", tok, {
        "subject": f"[REPRO] {label} {STAMP}", "description": "repro probe ticket",
        "ticket_type": "incident", "priority": "medium",
    })
    print(f"  POST create -> {s} {j.get('detail') if isinstance(j, dict) and s >= 400 else ''}")
    if s == 201 and isinstance(j, dict):
        tid = j.get("id")
        created.append(tid)
        print(f"  created id={tid} number={j.get('ticket_number')} status={j.get('status')} team_id={j.get('team_id')}")
        row_db = db.execute(text("SELECT id, status, is_deleted, raised_by_user_id FROM support_tickets WHERE id = :i"), {"i": tid}).fetchone()
        print(f"  DB row exists: {bool(row_db)} (status={row_db[1] if row_db else None} is_deleted={row_db[2] if row_db else None})")
        s2, lst = req("GET", "/support-desk/me/tickets/?page=1&limit=50", tok)
        items = (lst or {}).get("items") if isinstance(lst, dict) else (lst or [])
        found = any(str(x.get("id")) == str(tid) for x in (items or []))
        print(f"  GET /me/tickets list -> {s2}; contains new ticket: {found} (items={len(items or [])})")
        if handling and not bool(row[3]) and not on_team:
            print("  *** GATE LEAK: non-agent NOT on a handling team was allowed to create 'incident' ***")
    elif s == 403:
        print("  gate blocked (403) — expected when handling teams exist and caller is neither agent nor member")


run_case("EMPLOYEE", emp_out)
run_case("AGENT", agent_u)

# cleanup
for tid in created:
    try:
        db.execute(text("DELETE FROM support_ticket_activities WHERE ticket_id = :i"), {"i": tid})
        db.execute(text("DELETE FROM support_tickets WHERE id = :i"), {"i": tid})
    except Exception as e:
        print(f"cleanup issue {tid}: {e}")
db.commit()
db.close()
print("cleaned up.")
