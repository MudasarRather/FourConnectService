"""Probe PIR v2 (Post-Incident desks) against the RUNNING backend (port 8000).

Covers, end to end:
  1. Debt (owed) lens: resolved SEV2 incident appears in /incidents/pirs/board?lens=owed
     (server-side single truth), leaves it once a PIR is drafted; board 422s on bad
     lens/sort; legacy /incidents/pirs list keeps its shape.
  2. Document v2: PATCH carries contributing_factors / went_well / went_wrong /
     participants / review-meeting fields; caps enforced; revisions trail appends;
     action items receive stable aids; refresh_metrics freezes a draft snapshot;
     explicit-null clears the meeting; pir_meeting_set activity lands.
  3. Submit: drop-gate 422; metrics_snapshot frozen at submit; submitted_by stamped;
     idempotent create 409; non-incident 422.
  4. Sign-off: member approve 403 · foreign agent 404 (seal) · lead approve 200 ·
     FOUR-EYES 409 on own submission (superuser exempt) · reject needs note 422 ·
     reject clears submit stamps + stamps role='lead' in approvals · publish is
     superuser-only (lead 403) and stamps the distribution receipt.
  5. Follow-through: action PATCH 409 while draft; after publish — patch by stable
     aid (wrong positional index self-heals), in_progress accepted + counted,
     same-status 422, stale aid 404, positional back-compat.
  6. Calendar: review meeting surfaces as kind=pir_review on /me/tickets/calendar
     (+ ICS label) inside the team seal.
  7. Additive surfaces: /incidents/stats pir.owed + pir.actions_open;
     reports-overview totals carry pir_owed; PDF export 200/503 (GTK-dependent).
Creates disposable probe tickets and archives them at the end.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_pir_v2.py
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")
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
MARK = "probe-pirv2"
PASS = 0
FAIL = 0


def req(method, path, token, body=None, raw=False):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            payload = resp.read()
            if raw:
                return resp.status, payload, resp.headers.get("Content-Type")
            return resp.status, json.loads(payload.decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            body_ = e.read()
            if raw:
                return e.code, body_, e.headers.get("Content-Type")
            return e.code, json.loads(body_.decode() or "null")
        except Exception:
            return (e.code, None, None) if raw else (e.code, None)


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
team_id = lead_uid = member_uid = None
team_pool = set()
lead_tok = member_tok = None
for t in teams:
    roles = t[4] or {}
    lead = str(t[2]) if t[2] else next((u for u, r in roles.items() if r == "lead"), None)
    if not lead:
        continue
    lead_row = db.execute(text(
        "SELECT id, token_version, is_superuser FROM users WHERE id = :i AND is_active = TRUE"
    ), {"i": lead}).fetchone()
    if not lead_row or lead_row[2]:
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

foreign_tok = None
frows = db.execute(text(
    "SELECT id, token_version FROM users WHERE is_active = TRUE AND is_superuser = FALSE "
    "AND is_support_agent = TRUE LIMIT 80")).fetchall()
for fr in frows:
    if str(fr[0]) not in team_pool and str(fr[0]) != member_uid:
        foreign_tok = mint(fr[0], fr[1])
        print(f"foreign agent: {str(fr[0])[:8]}")
        break
if foreign_tok is None:
    print("note: no foreign agent available - seal check will be skipped")

made = []


def new_ticket(subject, ttype="incident", priority="critical"):
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


def resolve(tid, tok=None):
    return req("POST", f"/support-desk/tickets/{tid}/resolve", tok or su_tok, {
        "resolution_code": "solved", "resolution_summary": "probe resolution - safe to ignore",
        "close": False})


TODAY = datetime.now(timezone.utc)
YDAY = (TODAY - timedelta(days=1)).date().isoformat()
TMRW = (TODAY + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

# ═════════ T1 — debt lens (server-side owed) ═════════
print("-- T1: owed lens single truth --")
tA = new_ticket("alpha outage")
check("seat + assign member", seat_on_team(tA) == 200)
s, _ = resolve(tA)
check("resolve tA", s == 200, f"(status {s})")
s, j = req("GET", f"/support-desk/incidents/pirs/board?lens=owed&q={MARK}", su_tok)
in_owed = s == 200 and any(str(r.get("ticket_id")) == str(tA) for r in (j or {}).get("items", []))
check("resolved SEV2 shows in owed lens", in_owed, f"(status {s})")
check("owed rows are kind='owed'", all(r.get("kind") == "owed" for r in (j or {}).get("items", []) if str(r.get("ticket_id")) == str(tA)))
check("board stats lockstep keys", j is not None and all(
    k in (j.get("stats") or {}) for k in ("owed", "draft", "in_review", "approved",
                                          "published", "actions_open", "actions_overdue",
                                          "coverage_pct", "published_30d")))
s, _j = req("GET", "/support-desk/incidents/pirs/board?lens=bogus", su_tok)
check("bad lens 422", s == 422, f"(status {s})")
s, _j = req("GET", "/support-desk/incidents/pirs/board?lens=owed&sort=bogus", su_tok)
check("bad sort 422", s == 422, f"(status {s})")

# non-incident refuses a PIR
tQ = new_ticket("plain request", ttype="service_request", priority="low")
s, j = req("POST", f"/support-desk/tickets/{tQ}/pir", su_tok, {})
check("non-incident PIR 422", s == 422, f"(status {s})")

# ═════════ T2 — document v2 fields ═════════
print("-- T2: PIR v2 document fields --")
s, pir = req("POST", f"/support-desk/tickets/{tA}/pir", member_tok, {"title": f"{MARK} review A"})
check("member drafts PIR (owner-tier)", s == 201 and pir and pir.get("id"), f"(status {s})")
pA = pir["id"]
s, _j = req("POST", f"/support-desk/tickets/{tA}/pir", member_tok, {})
check("idempotent create 409", s == 409, f"(status {s})")
s, j = req("GET", f"/support-desk/incidents/pirs/board?lens=owed&q={MARK}", su_tok)
check("drafting removes tA from owed", s == 200 and not any(
    str(r.get("ticket_id")) == str(tA) for r in (j or {}).get("items", [])))
s, j = req("GET", f"/support-desk/incidents/pirs/board?lens=drafting&q={MARK}", su_tok)
check("tA's PIR shows in drafting lens", s == 200 and any(
    str(r.get("pir_id")) == str(pA) for r in (j or {}).get("items", [])))

patch1 = {
    "executive_summary": "Alpha service degraded for 47 minutes after a config push.",
    "root_cause": "Bad config pushed without canary; health-check gap masked the fault.",
    "root_cause_category": "configuration",
    "contributing_factors": ["no canary", "health-check gap", "  ", "alert fatigue"] + [f"f{i}" for i in range(12)],
    "went_well": ["Paging worked end to end", "Rollback under 5 minutes"],
    "went_wrong": ["Dashboards lagged by 4 minutes"],
    "participants": [{"name": "Probe Lead", "role": "commander"},
                     {"name": "Probe Member", "role": "scribe"}],
    "review_meeting_at": TMRW.isoformat(),
    "review_meeting_notes": "Agenda: timeline walk, actions, owner sign-up.",
    "corrective_actions": [
        {"action": "Add canary stage to config pipeline", "target_date": YDAY, "status": "open"},
        {"action": "Close the health-check gap", "target_date": YDAY, "status": "open"},
    ],
    "preventive_actions": [{"action": "Quarterly config-push game day", "status": "open"}],
    "refresh_metrics": True,
}
s, j = req("PATCH", f"/support-desk/incidents/pirs/{pA}", member_tok, patch1)
check("v2 PATCH 200", s == 200, f"(status {s})")
check("factors capped at 10 + blanks dropped", j and len(j.get("contributing_factors") or []) == 10)
check("retro registers echo", j and j.get("went_well") and j.get("went_wrong"))
check("participants echo", j and len(j.get("participants") or []) == 2)
check("meeting scheduled", j and (j.get("review_meeting_at") or "").startswith(TMRW.date().isoformat()))
aids = [a.get("aid") for a in (j or {}).get("corrective_actions", [])]
check("stable aids assigned", all(aids) and len(set(aids)) == len(aids), f"(aids {aids})")
check("draft metrics freeze via refresh_metrics", bool((j or {}).get("metrics_snapshot")))
check("revision trail appended", len((j or {}).get("revisions") or []) >= 1
      and "fields" in ((j or {}).get("revisions") or [{}])[-1])

row = db.execute(text(
    "SELECT id FROM support_ticket_activities WHERE ticket_id = :i AND action = 'pir_meeting_set' LIMIT 1"
), {"i": tA}).fetchone()
check("pir_meeting_set activity logged", bool(row))

# explicit-null clears the meeting, then restore it for the calendar test
s, j = req("PATCH", f"/support-desk/incidents/pirs/{pA}", member_tok,
           {"review_meeting_at": None})
check("explicit null clears meeting", s == 200 and not (j or {}).get("review_meeting_at"),
      f"(status {s})")
s, j = req("PATCH", f"/support-desk/incidents/pirs/{pA}", member_tok,
           {"review_meeting_at": TMRW.isoformat()})
check("meeting restored", s == 200 and bool((j or {}).get("review_meeting_at")))

# ═════════ T3 — submit gate + metrics freeze ═════════
print("-- T3: submit --")
tB = new_ticket("beta outage")
seat_on_team(tB)
resolve(tB)
s, pirB = req("POST", f"/support-desk/tickets/{tB}/pir", lead_tok, {"title": f"{MARK} review B"})
check("lead drafts PIR B", s == 201, f"(status {s})")
pB = pirB["id"]
s, j = req("POST", f"/support-desk/incidents/pirs/{pB}/submit", lead_tok)
check("submit drop-gate 422", s == 422 and "missing" in str((j or {}).get("detail", "")).lower(),
      f"(status {s})")
req("PATCH", f"/support-desk/incidents/pirs/{pB}", lead_tok, {
    "executive_summary": "Beta store returned stale reads for 20 minutes.",
    "root_cause": "Replica lag after failover was not surfaced to the router.",
    "corrective_actions": [{"action": "Surface replica lag to the router", "status": "open"}],
})
s, j = req("POST", f"/support-desk/incidents/pirs/{pB}/submit", lead_tok)
check("lead submit 200", s == 200 and (j or {}).get("status") == "in_review", f"(status {s})")
check("metrics frozen at submit", bool((j or {}).get("metrics_snapshot"))
      and "mttr_minutes" in ((j or {}).get("metrics_snapshot") or {}))
check("submitted_by stamped", str((j or {}).get("submitted_by_id")) == lead_uid)

s, j = req("POST", f"/support-desk/incidents/pirs/{pA}/submit", member_tok)
check("member submit A 200", s == 200 and (j or {}).get("status") == "in_review", f"(status {s})")

# ═════════ T4 — sign-off gates ═════════
print("-- T4: sign-off (lead ∪ admin, four-eyes) --")
s, _j = req("POST", f"/support-desk/incidents/pirs/{pA}/approve", member_tok, {})
check("member approve 403", s == 403, f"(status {s})")
if foreign_tok:
    s, _j = req("GET", f"/support-desk/incidents/pirs/{pA}", foreign_tok)
    check("foreign agent sealed out (404)", s == 404, f"(status {s})")
    s, _j = req("POST", f"/support-desk/incidents/pirs/{pA}/approve", foreign_tok, {})
    check("foreign approve 404", s == 404, f"(status {s})")
s, _j = req("POST", f"/support-desk/incidents/pirs/{pB}/approve", lead_tok, {})
check("FOUR-EYES: lead can't approve own submission (409)", s == 409, f"(status {s})")
s, j = req("POST", f"/support-desk/incidents/pirs/{pB}/approve", su_tok, {"note": "clean record"})
check("superuser approves lead's filing", s == 200 and (j or {}).get("status") == "approved",
      f"(status {s})")

s, _j = req("POST", f"/support-desk/incidents/pirs/{pA}/reject", lead_tok, {"note": "   "})
check("reject needs note 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/incidents/pirs/{pA}/reject", lead_tok,
           {"note": "Impact numbers missing - quantify the blast radius."})
check("lead reject 200 → draft", s == 200 and (j or {}).get("status") == "draft", f"(status {s})")
check("submit stamps cleared", not (j or {}).get("submitted_at"))
appr = ((j or {}).get("approvals") or [])
check("approvals trail carries role=lead", any(a.get("role") == "lead"
                                               and a.get("decision") == "rejected" for a in appr))
s, j = req("POST", f"/support-desk/incidents/pirs/{pA}/submit", member_tok)
check("resubmit A", s == 200, f"(status {s})")
s, j = req("POST", f"/support-desk/incidents/pirs/{pA}/approve", lead_tok, {"note": "good now"})
check("lead approves member's filing (four-eyes ok)", s == 200
      and (j or {}).get("status") == "approved", f"(status {s})")
s, _j = req("POST", f"/support-desk/incidents/pirs/{pA}/approve", lead_tok, {})
check("double approve 409", s == 409, f"(status {s})")

s, _j = req("POST", f"/support-desk/incidents/pirs/{pA}/publish", lead_tok)
check("publish is superuser-only (lead 403)", s == 403, f"(status {s})")
s, j = req("POST", f"/support-desk/incidents/pirs/{pA}/publish", su_tok)
check("superuser publish 200", s == 200 and (j or {}).get("status") == "published", f"(status {s})")
dist = (j or {}).get("distribution") or {}
check("distribution receipt stamped", "recipients" in dist and "watchers" in dist, f"({dist})")
s, _j = req("PATCH", f"/support-desk/incidents/pirs/{pA}", member_tok,
            {"executive_summary": "tamper"})
check("published document sealed (PATCH 409)", s == 409, f"(status {s})")

# ═════════ T5 — follow-through (stable aid + in_progress) ═════════
print("-- T5: action tracker --")
s, j = req("GET", f"/support-desk/incidents/pirs/{pA}", su_tok)
reg = (j or {}).get("corrective_actions") or []
check("register kept aids through lifecycle", len(reg) >= 2 and all(a.get("aid") for a in reg))
aid2 = reg[1].get("aid")
# wrong positional index + correct aid → self-heals to the aid's row
s, j = req("PATCH", f"/support-desk/incidents/pirs/{pA}/actions/corrective/0", su_tok,
           {"status": "in_progress", "aid": aid2, "note": "started work"})
check("aid addressing overrides stale index", s == 200 and (j or {}).get("aid") == aid2
      and (j or {}).get("index") == 1, f"(status {s})")
check("in_progress accepted", (j or {}).get("status") == "in_progress")
s, _j = req("PATCH", f"/support-desk/incidents/pirs/{pA}/actions/corrective/1", su_tok,
            {"status": "in_progress", "aid": aid2})
check("same-status 422", s == 422, f"(status {s})")
s, _j = req("PATCH", f"/support-desk/incidents/pirs/{pA}/actions/corrective/0", su_tok,
            {"status": "done", "aid": "deadbeef"})
check("stale aid 404", s == 404, f"(status {s})")
s, j = req("PATCH", f"/support-desk/incidents/pirs/{pA}/actions/corrective/0", su_tok,
           {"status": "done"})
check("positional back-compat (no aid)", s == 200 and (j or {}).get("index") == 0, f"(status {s})")
s, j = req("GET", f"/support-desk/incidents/actions?q={MARK}", su_tok)
rows = (j or {}).get("items") or []
check("actions board carries aid", s == 200 and rows and all("aid" in r for r in rows))
check("counts expose in_progress", "in_progress" in ((j or {}).get("counts") or {}))
s, j = req("GET", f"/support-desk/incidents/actions?q={MARK}&status=in_progress", su_tok)
check("in_progress filter exact", s == 200 and all(r.get("status") == "in_progress"
                                                   for r in (j or {}).get("items") or []))
s, j = req("GET", f"/support-desk/incidents/actions?q={MARK}&status=open", su_tok)
check("open filter = working set (includes in_progress)", s == 200 and any(
    r.get("status") == "in_progress" for r in (j or {}).get("items") or []))
s, j = req("GET", f"/support-desk/incidents/pirs/board?lens=actions_due&q={MARK}", su_tok)
check("actions_due lens 200", s == 200, f"(status {s})")

# draft-era PATCH still routes through the editor (409)
s, _j = req("PATCH", f"/support-desk/incidents/pirs/{pB}/actions/corrective/0", su_tok,
            {"status": "done"})
check("approved-not-published action patch allowed", s == 200, f"(status {s})")
tC = new_ticket("gamma outage")
seat_on_team(tC)
resolve(tC)
s, pirC = req("POST", f"/support-desk/tickets/{tC}/pir", member_tok, {})
pC = pirC["id"]
req("PATCH", f"/support-desk/incidents/pirs/{pC}", member_tok, {
    "corrective_actions": [{"action": "Probe draft action row", "status": "open"}]})
s, _j = req("PATCH", f"/support-desk/incidents/pirs/{pC}/actions/corrective/0", su_tok,
            {"status": "done"})
check("draft action patch 409 (edit via editor)", s == 409, f"(status {s})")

# ═════════ T6 — calendar (pir_review kind) ═════════
print("-- T6: Chrono Desk calendar --")
frm = (TODAY - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
to = (TODAY + timedelta(days=6)).strftime("%Y-%m-%dT00:00:00Z")
s, j = req("GET", f"/support-desk/me/tickets/calendar?from={frm}&to={to}&kinds=pir_review",
           member_tok)
evs = [e for e in ((j or {}).get("events") or []) if e.get("kind") == "pir_review"]
check("pir_review events on the sealed calendar", s == 200 and any(
    str(e.get("ticket_id")) == str(tA) for e in evs), f"(status {s}, n={len(evs)})")
check("event note = report number", any((e.get("note") or "").startswith("PIR") for e in evs))
s, body, ctype = req("GET",
                     f"/support-desk/me/tickets/calendar/export.ics?from={frm}&to={to}&kinds=pir_review",
                     member_tok, raw=True)
check("ICS export carries PIR review meeting", s == 200 and b"PIR review meeting" in (body or b""),
      f"(status {s})")

# ═════════ T7 — additive surfaces ═════════
print("-- T7: stats / reports-overview / PDF / legacy --")
s, j = req("GET", "/support-desk/incidents/stats", su_tok)
pirblock = ((j or {}).get("pir") or {})
check("stats pir.owed present", s == 200 and "owed" in pirblock, f"(status {s})")
check("stats pir.actions_open present", "actions_open" in pirblock)
check("stats pir legacy keys intact", all(k in pirblock for k in
                                          ("draft", "in_review", "approved", "published", "missing")))
s, j = req("GET", "/support-desk/me/tickets/reports-overview", member_tok)
check("reports-overview totals carry pir_owed", s == 200 and "pir_owed" in ((j or {}).get("totals") or {}),
      f"(status {s})")
s, j = req("GET", f"/support-desk/incidents/pirs?q={MARK}", su_tok)
check("legacy list shape intact", s == 200 and "total" in (j or {}) and "items" in (j or {}),
      f"(status {s})")
s, body, ctype = req("GET", f"/support-desk/incidents/pirs/{pC}/export.pdf", member_tok, raw=True)
if s == 503:
    check("PDF export (GTK missing → clear 503)", True, "(503 accepted)")
else:
    check("draft PDF export 200 + pdf content-type", s == 200 and "pdf" in (ctype or ""),
          f"(status {s}, {ctype})")
s, j = req("GET", f"/support-desk/incidents/pirs/board?lens=in_review&q={MARK}", su_tok)
check("board in_review lens 200", s == 200, f"(status {s})")

print("-- cleanup --")
for t in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{t}?reason=probe%20cleanup", su_tok)
    check(f"archive {str(t)[:8]}", s in (204, 409, 404), f"(status {s})")
db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
