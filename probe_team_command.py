"""Probe the Team Command hardening against the RUNNING backend (port 8000).

Covers: dup name/code 400s (code-dup on PATCH used to 500), assignment_method 422,
member dedupe + invalid-member 422, lead agent-grant, roster notifications, the
member-removal 409 + reassign_strategy directive (+ member-impact preflight parity),
the on_hold deactivate/delete guards, escalated-in delete guard, queue/template
detachment on delete, team audit rows, GET /teams/overview (shape + reconciliation
with /teams/{id}/stats + agent seal), and POST /teams/{id}/rebalance.

Creates a disposable team + tickets and cleans up at the end (restores the borrowed
users' is_support_agent flags). ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_team_command.py
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

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
STAMP = os.urandom(3).hex().upper()
T0 = datetime.now(timezone.utc)


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
helpers = db.execute(text(
    "SELECT id, email, token_version, is_support_agent FROM users "
    "WHERE is_superuser = FALSE AND is_active = TRUE ORDER BY created_at LIMIT 2"
)).fetchall()
print(f"superuser: {su[1] if su else None}")
if not su or len(helpers) < 2:
    print("Need a superuser + two active users - abort")
    sys.exit(1)
su_tok = mint(su)
X, Y = helpers[0], helpers[1]         # X = member we will remove; Y = lead who stays
x_id, y_id = str(X[0]), str(Y[0])
x_agent0, y_agent0 = bool(X[3]), bool(Y[3])
print(f"member X : {X[1]} (agent={x_agent0})")
print(f"lead   Y : {Y[1]} (agent={y_agent0})")

team_a = team_b = q_id = tpl_id = t1 = t2 = None

# ───────────────────────── create + validation guards ─────────────────────────
print("-- create + validation guards --")
name_a = f"[PROBE] Team Command {STAMP}"
s, j = req("POST", "/support-desk/teams/", su_tok, {
    "name": name_a, "code": f"PRB{STAMP}", "color": "#e8b04b",
    "member_ids": [x_id, x_id, y_id],           # dup X on purpose
    "lead_user_id": y_id, "assignment_method": "round_robin",
})
team_a = j.get("id") if isinstance(j, dict) else None
check("create team A", s == 201 and team_a, f"(status {s})")
if not team_a:
    sys.exit(1)
check("member_ids deduped", isinstance(j, dict) and j.get("member_ids") == [x_id, y_id],
      f"(got {j.get('member_ids') if isinstance(j, dict) else None})")

s, _ = req("POST", "/support-desk/teams/", su_tok, {"name": name_a.upper(), "code": f"PRB2{STAMP}"})
check("dup name (case-insens) -> 400", s == 400, f"(status {s})")
s, _ = req("POST", "/support-desk/teams/", su_tok,
           {"name": f"[PROBE] chaos {STAMP}", "assignment_method": "chaos"})
check("bad assignment_method -> 422", s == 422, f"(status {s})")
s, _ = req("POST", "/support-desk/teams/", su_tok,
           {"name": f"[PROBE] ghost {STAMP}", "member_ids": ["00000000-0000-0000-0000-00000000dead"]})
check("unknown member -> 422", s == 422, f"(status {s})")

s, j = req("POST", "/support-desk/teams/", su_tok,
           {"name": f"[PROBE] Sibling {STAMP}", "code": f"PRS{STAMP}"})
team_b = j.get("id") if isinstance(j, dict) else None
check("create team B", s == 201 and team_b, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/teams/{team_b}", su_tok, {"code": f"PRB{STAMP}"})
check("dup code on PATCH -> 400 (was 500)", s == 400, f"(status {s})")

row = db.execute(text("SELECT is_support_agent FROM users WHERE id = :i"), {"i": y_id}).fetchone()
check("lead granted is_support_agent", bool(row and row[0]))
n = db.execute(text(
    "SELECT count(*) FROM notifications WHERE user_id = :u AND type = 'SUPPORT_TEAM_MEMBER_ADDED' AND title LIKE :like"
), {"u": x_id, "like": f"%{STAMP}%"}).scalar()
check("member-added notification queued", (n or 0) >= 1, f"(rows {n})")
n = db.execute(text(
    "SELECT count(*) FROM notifications WHERE user_id = :u AND type = 'SUPPORT_TEAM_LEAD_ASSIGNED' AND title LIKE :like"
), {"u": y_id, "like": f"%{STAMP}%"}).scalar()
check("lead-assigned notification queued", (n or 0) >= 1, f"(rows {n})")

# ───────────────────────── member-removal guard + directive ─────────────────────────
print("-- member-removal guard + reassignment directive --")
s, j = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": f"[PROBE] team command orphan check {STAMP}", "description": "probe - safe to ignore",
    "priority": "high", "ticket_type": "incident", "source": "internal",
})
t1 = j.get("id") if isinstance(j, dict) else None
check("create probe ticket T1", s in (200, 201) and t1, f"(status {s})")
db.execute(text(
    "UPDATE support_tickets SET team_id = :tm, assigned_agent_id = :a, status = 'open', "
    "is_escalated = FALSE, escalated_to_team_id = NULL WHERE id = :i"
), {"tm": team_a, "a": x_id, "i": t1})
db.commit()

s, j = req("GET", f"/support-desk/teams/{team_a}/member-impact?remove={x_id}", su_tok)
check("member-impact preflight", s == 200 and isinstance(j, dict) and j.get("total_open", 0) >= 1
      and any(m.get("user_id") == x_id for m in j.get("members", [])), f"(status {s}, total {j and j.get('total_open')})")

s, j = req("PATCH", f"/support-desk/teams/{team_a}", su_tok, {"member_ids": [y_id]})
det = (j or {}).get("detail") if isinstance(j, dict) else None
check("remove loaded member w/o strategy -> 409",
      s == 409 and isinstance(det, dict) and det.get("error") == "members_have_open_assignments",
      f"(status {s}, error {det and det.get('error')})")
check("409 payload lists the member", isinstance(det, dict)
      and any(m.get("user_id") == x_id and m.get("open_count", 0) >= 1 for m in det.get("members", [])))

s, j = req("PATCH", f"/support-desk/teams/{team_a}", su_tok,
           {"member_ids": [y_id], "reassign_strategy": "auto"})
check("retry with reassign_strategy=auto -> 200", s == 200, f"(status {s})")
row = db.execute(text("SELECT assigned_agent_id FROM support_tickets WHERE id = :i"), {"i": t1}).fetchone()
check("T1 moved to remaining roster", row and str(row[0]) == y_id, f"(now {row and row[0]})")
n = db.execute(text(
    "SELECT count(*) FROM support_ticket_activities WHERE ticket_id = :i AND action = 'assigned' "
    "AND detail::text LIKE '%team_member_removed%'"
), {"i": t1}).scalar()
check("reassignment activity written", (n or 0) >= 1, f"(rows {n})")
n = db.execute(text(
    "SELECT count(*) FROM notifications WHERE user_id = :u AND type = 'SUPPORT_TEAM_MEMBER_REMOVED' AND title LIKE :like"
), {"u": x_id, "like": f"%{STAMP}%"}).scalar()
check("member-removed notification queued", (n or 0) >= 1, f"(rows {n})")
n = db.execute(text(
    "SELECT count(*) FROM audit_logs WHERE action = 'support.team.updated' AND entity_id = :e"
), {"e": team_a}).scalar()
check("team update audited", (n or 0) >= 1, f"(rows {n})")

# ───────────────────────── on_hold + escalated-in guards ─────────────────────────
print("-- deactivate / delete guards (on_hold + escalated-in) --")
db.execute(text("UPDATE support_tickets SET status = 'on_hold' WHERE id = :i"), {"i": t1})
db.commit()
s, j = req("DELETE", f"/support-desk/teams/{team_a}", su_tok)
det = (j or {}).get("detail") if isinstance(j, dict) else None
check("delete with on_hold ticket -> 409 (old loophole)",
      s == 409 and isinstance(det, dict) and det.get("on_hold", 0) >= 1,
      f"(status {s}, on_hold {det and det.get('on_hold')})")
s, j = req("PATCH", f"/support-desk/teams/{team_a}", su_tok, {"is_active": False})
det = (j or {}).get("detail") if isinstance(j, dict) else None
check("deactivate with active ticket -> 409 (old loophole)",
      s == 409 and isinstance(det, dict) and det.get("error") == "team_has_active_tickets",
      f"(status {s})")

# ───────────────────────── overview + stats reconciliation + rebalance ─────────────────────────
print("-- overview / stats / rebalance --")
s, j = req("GET", "/support-desk/teams/overview", su_tok)
card = next((c for c in (j or {}).get("teams", []) if c.get("id") == team_a), None) if s == 200 else None
check("GET /teams/overview", s == 200 and isinstance(j, dict) and "totals" in j, f"(status {s})")
check("overview has team A card", bool(card))
check("overview counts on_hold as active", bool(card) and card.get("open", 0) >= 1 and card.get("on_hold", 0) >= 1,
      f"(open {card and card.get('open')}, on_hold {card and card.get('on_hold')})")
check("overview card carries flow[7] + roster fields", bool(card) and len(card.get("flow", [])) == 7
      and card.get("agent_count", 0) >= 1)

s, j = req("GET", f"/support-desk/teams/{team_a}/stats", su_tok)
check("GET /teams/{id}/stats (delegate)", s == 200 and isinstance(j, dict) and "roster" in j, f"(status {s})")
if card and s == 200:
    check("overview.open == stats.queue (reconciles)", card.get("open") == j.get("queue"),
          f"(card {card.get('open')} vs stats {j.get('queue')})")

s, j = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": f"[PROBE] rebalance target {STAMP}", "description": "probe - safe to ignore",
    "priority": "medium", "ticket_type": "service_request", "source": "internal",
})
t2 = j.get("id") if isinstance(j, dict) else None
db.execute(text(
    "UPDATE support_tickets SET team_id = :tm, assigned_agent_id = NULL, status = 'open', "
    "is_escalated = FALSE, escalated_to_team_id = NULL WHERE id = :i"), {"tm": team_a, "i": t2})
db.commit()
s, j = req("POST", f"/support-desk/teams/{team_a}/rebalance", su_tok, {"max_tickets": 10})
check("POST /teams/{id}/rebalance", s == 200 and isinstance(j, dict) and j.get("assigned", 0) >= 1,
      f"(status {s}, assigned {j and j.get('assigned')})")
row = db.execute(text("SELECT assigned_agent_id FROM support_tickets WHERE id = :i"), {"i": t2}).fetchone()
check("rebalance assigned T2 to the roster", row and str(row[0]) == y_id, f"(now {row and row[0]})")

# agent seal: Y (non-superuser) sees only teams they are on
y_tok = mint((Y[0], Y[1], Y[2]))
s, j = req("GET", "/support-desk/teams/overview", y_tok)
ids = [c.get("id") for c in (j or {}).get("teams", [])] if s == 200 else []
mine = {str(r[0]) for r in db.execute(text(
    "SELECT id FROM support_teams WHERE is_deleted = FALSE AND is_active = TRUE AND "
    "(lead_user_id = :u OR member_ids::text LIKE :like)"), {"u": y_id, "like": f"%{y_id}%"}).fetchall()}
check("agent overview sealed to own teams", s == 200 and team_a in ids and all(i in mine for i in ids),
      f"(status {s}, teams {len(ids)})")

# escalated-in guard: a foreign active ticket escalated INTO team A blocks delete
db.execute(text(
    "UPDATE support_tickets SET team_id = NULL, status = 'open', escalated_to_team_id = :tm WHERE id = :i"
), {"tm": team_a, "i": t2})
db.execute(text("UPDATE support_tickets SET status = 'closed', closed_at = NOW() WHERE id = :i"), {"i": t1})
db.commit()
s, j = req("DELETE", f"/support-desk/teams/{team_a}", su_tok)
det = (j or {}).get("detail") if isinstance(j, dict) else None
check("delete with live escalation-in -> 409", s == 409 and isinstance(det, dict)
      and det.get("escalated_in", 0) >= 1, f"(status {s}, esc_in {det and det.get('escalated_in')})")

# ───────────────────────── delete detaches queues/templates + audits ─────────────────────────
print("-- delete detaches routing refs + audits --")
s, j = req("POST", "/support-desk/queues/", su_tok,
           {"name": f"[PROBE] Queue {STAMP}", "code": f"PRQ{STAMP}", "team_id": team_a})
q_id = j.get("id") if isinstance(j, dict) else None
s, j = req("POST", "/support-desk/ticket-templates/", su_tok,
           {"name": f"[PROBE] Template {STAMP}", "team_id": team_a})
tpl_id = j.get("id") if isinstance(j, dict) else None
check("probe queue + template created", bool(q_id and tpl_id))

db.execute(text("UPDATE support_tickets SET status = 'closed', closed_at = NOW(), escalated_to_team_id = NULL "
                "WHERE id = :i"), {"i": t2})
db.commit()
s, _ = req("DELETE", f"/support-desk/teams/{team_a}", su_tok)
check("delete team A (work terminal) -> 204", s == 204, f"(status {s})")
row = db.execute(text("SELECT team_id FROM support_queues WHERE id = :i"), {"i": q_id}).fetchone()
check("queue.team_id detached", row and row[0] is None, f"(now {row and row[0]})")
row = db.execute(text("SELECT team_id FROM support_ticket_templates WHERE id = :i"), {"i": tpl_id}).fetchone()
check("template.team_id detached", row and row[0] is None, f"(now {row and row[0]})")
n = db.execute(text(
    "SELECT count(*) FROM audit_logs WHERE action = 'support.team.deleted' AND entity_id = :e"), {"e": team_a}).scalar()
check("team delete audited", (n or 0) >= 1, f"(rows {n})")

# ───────────────────────── cleanup ─────────────────────────
print("-- cleanup --")
try:
    req("DELETE", f"/support-desk/teams/{team_b}", su_tok)
    for tid in (t1, t2):
        if tid:
            db.execute(text("UPDATE support_tickets SET is_deleted = TRUE, archived_at = NOW() WHERE id = :i"), {"i": tid})
    if q_id:
        db.execute(text("UPDATE support_queues SET is_deleted = TRUE WHERE id = :i"), {"i": q_id})
    if tpl_id:
        db.execute(text("UPDATE support_ticket_templates SET is_deleted = TRUE WHERE id = :i"), {"i": tpl_id})
    # restore borrowed users' agent flags + drop probe notifications
    db.execute(text("UPDATE users SET is_support_agent = :f WHERE id = :i"), {"f": x_agent0, "i": x_id})
    db.execute(text("UPDATE users SET is_support_agent = :f WHERE id = :i"), {"f": y_agent0, "i": y_id})
    db.execute(text("DELETE FROM notifications WHERE title LIKE '%[PROBE]%' OR title LIKE :like"),
               {"like": f"%{STAMP}%"})
    db.commit()
    print("  cleaned up (tickets archived, team B removed, flags + notifications restored)")
except Exception as exc:
    print(f"  cleanup issue: {exc}")

print(f"\n===== {PASS} passed, {FAIL} failed =====")
sys.exit(1 if FAIL else 0)
