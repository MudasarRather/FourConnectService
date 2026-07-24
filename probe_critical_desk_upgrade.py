"""Probe the Critical-desk upgrade (Phase B) against the RUNNING backend (port 8000).

Covers, end to end:
  1. Playbooks: library 200; apply 201 (rows match the library); re-apply 409;
     non-incident 422; resolved 409.
  2. Tasks: add 201; open->done stamps done_at/by; done->open no-note 422 / with-note
     200; open->skipped no-note 422; done->skipped 422; skipped->open free; foreign
     actor sealed 404; list progress_pct; lens rows carry task_total/task_done.
  3. Sev verb: promote owner-tier 200 + activity with note; short note 422;
     de-escalate non-lead 403 / lead 200; MI 409; terminal 409; already-at-target 422;
     non-incident 422.
  4. Stats: critical.* present; LOCKSTEP proof critical.sev2_unacked ==
     GET /incidents/?lens=critical&live=1&sev=2&flag=unacked total; de_escalations_30d
     >= 1; exposure flag counts <=> rows; playbook + responder_load populated.
  5. live=1 excludes terminal rows; omitted keeps them (back-compat).
  6. Deep link: propose as member -> non-superuser lead's notification action_url
     starts with /user/support/.
Creates disposable probe tickets and archives them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_critical_desk_upgrade.py
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
MARK = "probe-critdesk"
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

# Find a support team with a NON-SUPERUSER lead + an active non-lead member.
teams = db.execute(text(
    "SELECT id, name, lead_user_id, member_ids, member_roles FROM support_teams "
    "WHERE is_deleted = FALSE AND is_active = TRUE"
)).fetchall()
team_id = lead_uid = member_uid = None
team_pool = set()
for t in teams:
    roles = t[4] or {}
    lead = str(t[2]) if t[2] else next((u for u, r in roles.items() if r == "lead"), None)
    if not lead:
        continue
    lead_row = db.execute(text(
        "SELECT id, token_version, is_superuser FROM users WHERE id = :i AND is_active = TRUE"
    ), {"i": lead}).fetchone()
    if not lead_row or lead_row[2]:   # need a NON-superuser lead for the deep-link check
        continue
    for m in (t[3] or []):
        m = str(m)
        if m != lead and roles.get(m) != "lead" and m != su_id:
            act = db.execute(text(
                "SELECT id, token_version FROM users WHERE id = :i AND is_active = TRUE"
            ), {"i": m}).fetchone()
            if act:
                team_id, lead_uid, member_uid = str(t[0]), lead, m
                lead_tok = mint(lead_row[0], lead_row[1])
                member_tok = mint(act[0], act[1])
                team_pool = {str(x) for x in (t[3] or [])} | {lead}
                break
    if team_id:
        print(f"team: {t[1]}  lead: {lead_uid[:8]}  member: {member_uid[:8]}")
        break
if not team_id:
    print("No team with non-superuser lead + non-lead member found - abort"); sys.exit(1)

# Foreign agent: an active non-superuser support agent OUTSIDE the probe team (seal test).
foreign_tok = None
frows = db.execute(text(
    "SELECT id, token_version FROM users WHERE is_active = TRUE AND is_superuser = FALSE "
    "AND is_support_agent = TRUE LIMIT 50")).fetchall()
for fr in frows:
    if str(fr[0]) not in team_pool and str(fr[0]) != member_uid:
        foreign_tok = mint(fr[0], fr[1])
        print(f"foreign agent: {str(fr[0])[:8]}")
        break
if foreign_tok is None:
    print("note: no foreign agent available - seal check will be skipped")

made = []


def new_ticket(subject, ttype="incident", priority="high"):
    s, j = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": f"{MARK}: {subject}", "description": "probe - safe to ignore",
        "priority": priority, "ticket_type": ttype, "source": "internal",
    })
    if not (j and j.get("id")):
        print(f"  setup create failed ({s}) - abort"); sys.exit(1)
    made.append(j["id"])
    return j["id"]


def seat_on_team(tid, assignee=None):
    db.execute(text("UPDATE support_tickets SET team_id = :tm WHERE id = :i"),
               {"tm": team_id, "i": tid})
    db.commit()
    s, _ = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok,
               {"assigned_agent_id": assignee or member_uid})
    return s


print("-- T1: playbook library + apply + task lifecycle --")
t1 = new_ticket("playbook + tasks")
check("seat on team + assign member", seat_on_team(t1) == 200)

s, lib = req("GET", "/support-desk/incidents/playbooks", member_tok)
keys = {p.get("key") for p in (lib or [])}
check("playbook library 200 + 4 curated keys", s == 200 and
      {"sev1_bridge", "sev2_response", "security_exposure", "public_comms"} <= keys,
      f"(status {s}, keys {sorted(keys)})")
sev2_pb = next((p for p in (lib or []) if p.get("key") == "sev2_response"), {})
pb_count = int(sev2_pb.get("task_count") or 0)
check("library entries carry tasks", pb_count >= 3 and len(sev2_pb.get("tasks") or []) == pb_count)

s, j = req("POST", f"/support-desk/tickets/{t1}/tasks/apply-template", member_tok,
           {"template_key": "sev2_response"})
items = (j or {}).get("items") or []
check("apply playbook 201, rows match library", s == 201 and len(items) == pb_count,
      f"(status {s}, rows {len(items)})")
check("applied rows stamped + open", bool(items) and all(
    r.get("template_key") == "sev2_response" and r.get("status") == "open" for r in items))
s, _ = req("POST", f"/support-desk/tickets/{t1}/tasks/apply-template", member_tok,
           {"template_key": "sev2_response"})
check("re-apply same playbook 409", s == 409, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t1}/tasks/apply-template", member_tok,
           {"template_key": "nope_not_real"})
check("unknown playbook 422", s == 422, f"(status {s})")

t_sr = new_ticket("non-incident", ttype="service_request")
s, _ = req("POST", f"/support-desk/tickets/{t_sr}/tasks/apply-template", su_tok,
           {"template_key": "sev2_response"})
check("apply on non-incident 422", s == 422, f"(status {s})")

t_res = new_ticket("resolved incident")
s, _ = req("POST", f"/support-desk/tickets/{t_res}/assign", su_tok, {"assigned_agent_id": su_id})
s, _ = req("POST", f"/support-desk/tickets/{t_res}/resolve", su_tok, {
    "resolution_code": "solved", "resolution_summary": "probe resolution - safe to ignore"})
check("setup: resolve t_res 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t_res}/tasks/apply-template", su_tok,
           {"template_key": "sev2_response"})
check("apply on resolved 409", s == 409, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t_res}/tasks", su_tok, {"title": "should bounce"})
check("add task on resolved 409", s == 409, f"(status {s})")

print("-- T2: single-task transitions --")
s, task = req("POST", f"/support-desk/tickets/{t1}/tasks", member_tok,
              {"title": "probe ad-hoc task", "owner_id": member_uid})
tk = (task or {}).get("id")
check("add ad-hoc task 201", s == 201 and bool(tk), f"(status {s})")
s, j = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok, {"status": "done"})
check("open->done 200 + stamps", s == 200 and (j or {}).get("status") == "done"
      and (j or {}).get("done_at") and (j or {}).get("done_by_id"), f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok, {"status": "done"})
check("same-status 422", s == 422, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok, {"status": "skipped"})
check("done->skipped 422", s == 422, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok, {"status": "open"})
check("done->open without note 422", s == 422, f"(status {s})")
s, j = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok,
           {"status": "open", "status_note": "correction - closed the wrong item"})
check("done->open with note 200 + stamp cleared", s == 200 and (j or {}).get("status") == "open"
      and not (j or {}).get("done_at"), f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok, {"status": "skipped"})
check("open->skipped without note 422", s == 422, f"(status {s})")
s, j = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok,
           {"status": "skipped", "status_note": "not applicable on this incident"})
check("open->skipped with note 200", s == 200 and (j or {}).get("status") == "skipped",
      f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok, {"status": "done"})
check("skipped->done 422 (reopen first)", s == 422, f"(status {s})")
s, j = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok, {"status": "open"})
check("skipped->open free 200", s == 200 and (j or {}).get("status") == "open", f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{t1}/tasks/{tk}", member_tok, {"status": "done"})
check("re-complete 200", s == 200, f"(status {s})")

s, j = req("GET", f"/support-desk/tickets/{t1}/tasks", member_tok)
total = (j or {}).get("total") or 0
check("task list 200 + counts", s == 200 and total == pb_count + 1
      and (j or {}).get("done", 0) >= 1, f"(status {s}, total {total})")
check("progress_pct present", isinstance((j or {}).get("progress_pct"), (int, float)),
      f"(pct {(j or {}).get('progress_pct')})")

if foreign_tok:
    s, _ = req("GET", f"/support-desk/tickets/{t1}/tasks", foreign_tok)
    check("foreign agent sealed 404", s == 404, f"(status {s})")
else:
    print("  [SKIP] foreign agent sealed 404 (no candidate)")

s, j = req("GET", f"/support-desk/incidents/?lens=active&q={MARK}&limit=100", member_tok)
row = next((r for r in ((j or {}).get("items") or []) if r.get("id") == t1), None)
check("lens row carries task_total/task_done", bool(row)
      and row.get("task_total") == pb_count + 1 and row.get("task_done", 0) >= 1,
      f"(total {row.get('task_total') if row else None}, done {row.get('task_done') if row else None})")

print("-- T3: severity reclassification --")
t2 = new_ticket("sev verb")
check("seat on team + assign member", seat_on_team(t2) == 200)
s, _ = req("POST", f"/support-desk/tickets/{t2}/sev", member_tok,
           {"target_sev": 2, "note": "short"})
check("short note 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t2}/sev", member_tok,
           {"target_sev": 2, "note": "Cascading failures across checkout - raising to SEV2"})
check("owner-tier promote 200 -> critical", s == 200 and (j or {}).get("priority") == "critical"
      and (j or {}).get("sev") == 2, f"(status {s})")
s, acts = req("GET", f"/support-desk/tickets/{t2}/activities", su_tok)
sevacts = [a for a in (acts or []) if a.get("action") == "incident_sev_changed"]
d = sevacts[-1]["detail"] if sevacts else {}
check("activity incident_sev_changed carries note + from/to",
      d.get("to_sev") == 2 and d.get("to_priority") == "critical" and bool(d.get("note")))
s, _ = req("POST", f"/support-desk/tickets/{t2}/sev", member_tok,
           {"target_sev": 2, "note": "should already be critical at this point"})
check("promote when already critical 422", s == 422, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t2}/sev", member_tok,
           {"target_sev": 3, "note": "member trying to stand the desk down early"})
check("de-escalate as non-lead 403", s == 403, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t2}/sev", lead_tok,
           {"target_sev": 3, "note": "Blast radius contained to a single tenant"})
check("de-escalate as lead 200 -> high", s == 200 and (j or {}).get("priority") == "high",
      f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t2}/sev", lead_tok,
           {"target_sev": 3, "note": "second de-escalation should have nothing to do"})
check("de-escalate when not critical 422", s == 422, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t_res}/sev", su_tok,
           {"target_sev": 2, "note": "terminal tickets have settled severity"})
check("sev on resolved 409", s == 409, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t_sr}/sev", su_tok,
           {"target_sev": 2, "note": "service requests are not incidents at all"})
check("sev on non-incident 422", s == 422, f"(status {s})")
t_mi = new_ticket("mi sev guard")
s, _ = req("POST", f"/support-desk/tickets/{t_mi}/major-incident", su_tok,
           {"is_major_incident": True})
check("setup: declare MI 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t_mi}/sev", su_tok,
           {"target_sev": 3, "note": "MI severity moves belong to the MI verbs"})
check("sev on major incident 409", s == 409, f"(status {s})")

print("-- T4: stats critical block + lockstep --")
# put t2 back at SEV2 and stamp exposure so the critical lens has live probe rows
s, _ = req("POST", f"/support-desk/tickets/{t2}/sev", member_tok,
           {"target_sev": 2, "note": "Re-promoting for the stats lockstep exercise"})
check("re-promote 200", s == 200, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{t2}/incident-impact", member_tok,
           {"security_impact": True})
check("stamp security exposure 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t2}/tasks/apply-template", member_tok,
           {"template_key": "security_exposure"})
check("playbook on live SEV2 201", s == 201, f"(status {s})")

s, stats = req("GET", "/support-desk/incidents/stats", su_tok)
c = (stats or {}).get("critical") or {}
check("stats.critical present + keys", s == 200 and all(k in c for k in (
    "sev1_active", "sev2_active", "sev2_unacked", "sev2_update_overdue", "sev2_at_risk",
    "sev2_breached", "sev2_unowned", "ack_coverage_pct", "oldest_sev2_age_minutes",
    "exposure", "mi_proposed_30d", "mi_confirmed_30d", "mi_declined_30d",
    "de_escalations_30d", "playbook", "responder_load")), f"(status {s})")
check("sev2_active counts our live SEV2", int(c.get("sev2_active") or 0) >= 1)
check("de_escalations_30d incremented", int(c.get("de_escalations_30d") or 0) >= 1,
      f"(n {c.get('de_escalations_30d')})")
exp = c.get("exposure") or {}
check("exposure block populated", all(k in exp for k in (
    "by_business_impact", "compliance", "security", "public", "revenue_flagged", "unassessed")))
pb = c.get("playbook") or {}
check("playbook counts see the live SEV2 checklist",
      int(pb.get("tickets_with_tasks") or 0) >= 1 and int(pb.get("tasks_open") or 0) >= 1,
      f"(tickets {pb.get('tickets_with_tasks')}, open {pb.get('tasks_open')})")
rl = c.get("responder_load") or []
check("responder_load carries the assignee", any(
    r.get("user_id") == member_uid and int(r.get("sev2") or 0) >= 1 for r in rl),
      f"(rows {len(rl)})")

s, j = req("GET", "/support-desk/incidents/?lens=critical&live=1&sev=2&flag=unacked&limit=1", su_tok)
check("LOCKSTEP sev2_unacked == flagged rows total",
      s == 200 and int(c.get("sev2_unacked") or -1) == int((j or {}).get("total") or -2),
      f"(stats {c.get('sev2_unacked')}, rows {(j or {}).get('total')})")
s, j = req("GET", "/support-desk/incidents/?lens=critical&live=1&flag=exposure_security&limit=1", su_tok)
check("LOCKSTEP exposure.security == flagged rows total",
      s == 200 and int(exp.get("security") or -1) == int((j or {}).get("total") or -2),
      f"(stats {exp.get('security')}, rows {(j or {}).get('total')})")
s, j = req("GET", "/support-desk/incidents/?lens=critical&live=1&flag=unassessed&limit=1", su_tok)
check("LOCKSTEP exposure.unassessed == flagged rows total",
      s == 200 and int(exp.get("unassessed") or -1) == int((j or {}).get("total") or -2),
      f"(stats {exp.get('unassessed')}, rows {(j or {}).get('total')})")

print("-- T5: live param back-compat --")
t4 = new_ticket("terminal critical", priority="critical")
s, _ = req("POST", f"/support-desk/tickets/{t4}/assign", su_tok, {"assigned_agent_id": su_id})
s, _ = req("POST", f"/support-desk/tickets/{t4}/resolve", su_tok, {
    "resolution_code": "solved", "resolution_summary": "probe resolution - safe to ignore"})
check("setup: resolve critical 200", s == 200, f"(status {s})")
s, j = req("GET", f"/support-desk/incidents/?lens=critical&q={MARK}&limit=100", su_tok)
ids = {r.get("id") for r in ((j or {}).get("items") or [])}
check("omitted live keeps terminal row (back-compat)", s == 200 and t4 in ids, f"(status {s})")
s, j = req("GET", f"/support-desk/incidents/?lens=critical&live=1&q={MARK}&limit=100", su_tok)
ids = {r.get("id") for r in ((j or {}).get("items") or [])}
check("live=1 excludes terminal row", s == 200 and t4 not in ids and t2 in ids, f"(status {s})")

print("-- T6: MI-propose deep link is panel-aware --")
t5 = new_ticket("deep link proposal")
check("seat on team + assign member", seat_on_team(t5) == 200)
s, _ = req("POST", f"/support-desk/tickets/{t5}/mi-proposal", member_tok, {
    "note": "Error volume doubling every ten minutes - flagging for the major desk"})
check("propose 201", s == 201, f"(status {s})")
row = db.execute(text(
    "SELECT action_url FROM notifications WHERE user_id = :u AND type = 'SUPPORT_INCIDENT_MI_PROPOSED' "
    "ORDER BY created_at DESC LIMIT 1"), {"u": lead_uid}).fetchone()
check("non-superuser lead deep link starts /user/support/",
      bool(row) and str(row[0] or "").startswith("/user/support/incidents/major"),
      f"(url {row[0] if row else None})")
s, _ = req("POST", f"/support-desk/tickets/{t5}/mi-proposal/withdraw", member_tok)
check("withdraw proposal 200", s == 200, f"(status {s})")

print("-- cleanup --")
for t in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{t}?reason=probe%20cleanup", su_tok)
    check(f"archive {t[:8]}", s in (204, 409, 404), f"(status {s})")
db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
