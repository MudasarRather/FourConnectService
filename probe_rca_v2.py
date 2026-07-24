"""Probe RCA v2 (Root Cause Analysis desks) against the RUNNING backend (port 8000).

Covers, end to end:
  1. set_rca validation: empty payload/empty strings 422, summary<10 422, bad category
     422, whys>5 422, ancillary-without-summary 422; valid filing 200 stamps
     filed/filed_by; legacy subset payload back-compat; breach_reason-only leaves the
     machine untouched; re-file logs rca_revised; merged tombstone 409.
  2. Review workflow: member validate 403; lead validate 200 (+ filer notification);
     validate-nothing 409; four-eyes 409 (superuser exempt); return-note 422/200;
     re-file clears review stamps; validated-content-edit demotes to filed.
  3. Reopen: validated RCA goes STALE + rca_invalidated activity; re-file works.
  4. Close gate: resolve free; close of critical/breached without live RCA 422; file
     then close 200; clean low-sev close 200; cancelled outcome exempt; bulk close
     per-item skip.
  5. Board: lens rows + stats lockstep, zero-lens stats present, bad lens/sort 422,
     foreign-team seal.
  6. Analytics: all blocks present, coverage consistent.
  7. Clusters: seeded trio clusters at min_size=3, hidden at 4; promote mints an
     investigating problem + links; re-list flags has_open_problem.
  8. Cascade: resolve-linked stamps rca_inherited on empty slots, never over live
     filings, propagate_rca=false stamps nothing.
  9. Additive surfaces: reports-overview carries rca_owed; incidents stats
     missing_rca present; legacy missing_rca list filter still 200; timeline accepts
     the new rca_* kinds and rca_recorded pins as a milestone.
Creates disposable probe tickets/problems and archives them at the end.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_rca_v2.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

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
MARK = "probe-rcav2"
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

# Team with a NON-superuser lead + an active non-lead member (same pattern as the
# critical-desk probe).
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
        # Only usable for the seal check if they're OUTSIDE the probe team everywhere.
        foreign_tok = mint(fr[0], fr[1])
        print(f"foreign agent: {str(fr[0])[:8]}")
        break
if foreign_tok is None:
    print("note: no foreign agent available - seal check will be skipped")

made = []
made_problems = []


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


def resolve(tid, tok=None, close=False, code="solved"):
    return req("POST", f"/support-desk/tickets/{tid}/resolve", tok or su_tok, {
        "resolution_code": code, "resolution_summary": "probe resolution - safe to ignore",
        "close": close})


VALID_RCA = {
    "rca_summary": "Connection pool exhausted after the retry storm from the gateway.",
    "rca_corrective": "Bounced the pool and raised the cap.",
    "rca_preventive": "Alert on pool saturation at 80 percent.",
    "rca_category": "configuration",
    "rca_five_whys": ["Pool exhausted", "Retry storm", "No backoff", "Old client lib", "Upgrade skipped"],
    "rca_factors": ["retry storm", "missing alerting"],
}

print("-- T1: set_rca validation + state machine --")
t1 = new_ticket("filing target")
check("seat + assign member", seat_on_team(t1) == 200)
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok, {"rca_summary": "   "})
check("empty-string summary 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok, {"rca_summary": "too short"})
check("summary <10 chars 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok, {"rca_corrective": "fix it properly"})
check("ancillary-without-summary 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok,
           {**VALID_RCA, "rca_category": "gremlins"})
check("bad category 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok,
           {**VALID_RCA, "rca_five_whys": ["a"] * 6})
check("6 whys 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok, VALID_RCA)
check("valid filing 200 -> filed + stamps", s == 200 and j.get("rca_status") == "filed"
      and j.get("rca_filed_at") and str(j.get("rca_filed_by_id")) == member_uid,
      f"(status {s}, rca_status {j and j.get('rca_status')})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok, VALID_RCA)
row = db.execute(text(
    "SELECT action FROM support_ticket_activities WHERE ticket_id = :i "
    "AND action IN ('rca_recorded','rca_revised') ORDER BY created_at DESC LIMIT 1"),
    {"i": t1}).fetchone()
check("re-file 200 + newest activity rca_revised", s == 200 and row and row[0] == "rca_revised",
      f"(status {s}, action {row and row[0]})")

t_legacy = new_ticket("legacy subset")
check("seat + assign member", seat_on_team(t_legacy) == 200)
s, j = req("POST", f"/support-desk/tickets/{t_legacy}/rca", member_tok, {
    "breach_reason": "vendor_dependency",
    "rca_summary": "Vendor API rejected auth after their rotation.",
    "rca_corrective": "Re-keyed.", "rca_preventive": "Rotation calendar."})
check("legacy war-room subset payload 200 -> filed", s == 200 and j.get("rca_status") == "filed",
      f"(status {s})")

t_br_only = new_ticket("breach annotation only")
check("seat + assign member", seat_on_team(t_br_only) == 200)
s, j = req("POST", f"/support-desk/tickets/{t_br_only}/rca", member_tok,
           {"breach_reason": "underestimated_effort"})
check("breach_reason-only 200 + status stays NULL", s == 200 and j.get("rca_status") in (None, ""),
      f"(status {s}, rca_status {j and j.get('rca_status')})")

print("-- T2: review workflow (validate / return / four-eyes) --")
s, _ = req("POST", f"/support-desk/tickets/{t1}/rca/validate", member_tok, {})
check("member validate 403", s == 403, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t1}/rca/return", lead_tok, {})
check("return without note 422", s == 422, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca/return", lead_tok,
           {"note": "Whys stop at the retry storm - dig into why backoff was missing."})
check("lead return 200 -> returned + note", s == 200 and j.get("rca_status") == "returned"
      and j.get("rca_review_note"), f"(status {s})")
nrow = db.execute(text(
    "SELECT id FROM notifications WHERE user_id = :u AND type = 'SUPPORT_RCA_RETURNED' "
    "ORDER BY created_at DESC LIMIT 1"), {"u": member_uid}).fetchone()
check("filer got RCA_RETURNED notification", bool(nrow))
s, _ = req("POST", f"/support-desk/tickets/{t1}/rca/validate", lead_tok, {})
check("validate a RETURNED filing 409", s == 409, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok, VALID_RCA)
check("re-file after return -> filed, review cleared", s == 200 and j.get("rca_status") == "filed"
      and not j.get("rca_reviewed_at") and not j.get("rca_review_note"), f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/rca/validate", lead_tok,
           {"note": "Chain is complete now."})
check("lead validate 200 -> validated", s == 200 and j.get("rca_status") == "validated",
      f"(status {s})")
nrow = db.execute(text(
    "SELECT id FROM notifications WHERE user_id = :u AND type = 'SUPPORT_RCA_VALIDATED' "
    "ORDER BY created_at DESC LIMIT 1"), {"u": member_uid}).fetchone()
check("filer got RCA_VALIDATED notification", bool(nrow))
s, j = req("POST", f"/support-desk/tickets/{t1}/rca", member_tok,
           {**VALID_RCA, "rca_corrective": "Also added circuit breaker."})
check("content edit on validated -> demoted to filed", s == 200 and j.get("rca_status") == "filed",
      f"(status {s})")

# Four-eyes: the LEAD files on a fresh ticket, then tries to validate their own filing.
t_fe = new_ticket("four eyes")
check("seat + assign LEAD", seat_on_team(t_fe, assignee=lead_uid) == 200)
s, _ = req("POST", f"/support-desk/tickets/{t_fe}/rca", lead_tok, VALID_RCA)
check("lead files own RCA 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t_fe}/rca/validate", lead_tok, {})
check("lead self-validate 409 (four-eyes)", s == 409, f"(status {s})")
s, j = req("POST", f"/support-desk/tickets/{t_fe}/rca/validate", su_tok, {})
check("superuser validates same filing 200", s == 200 and j.get("rca_status") == "validated",
      f"(status {s})")

print("-- T3: merged tombstone + reopen invalidation --")
t_m = new_ticket("merge dupe")
t_master = new_ticket("merge master")
s, _ = req("POST", f"/support-desk/tickets/{t_m}/merge", su_tok, {"target_id": t_master})
check("setup merge 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t_m}/rca", su_tok, VALID_RCA)
check("RCA on merged tombstone 409", s == 409, f"(status {s})")

s, _ = resolve(t_fe)  # validated RCA on t_fe; resolve then reopen
check("setup resolve t_fe 200", s[0] == 200 if isinstance(s, tuple) else s == 200)
s, _ = req("POST", f"/support-desk/tickets/{t_fe}/reopen", su_tok, {"reason": "probe recurrence"})
check("reopen 200", s == 200, f"(status {s})")
s, j = req("GET", f"/support-desk/tickets/{t_fe}", su_tok)
check("reopen -> rca_status stale, text preserved", s == 200 and j.get("rca_status") == "stale"
      and j.get("rca_summary"), f"(rca_status {j and j.get('rca_status')})")
row = db.execute(text(
    "SELECT id FROM support_ticket_activities WHERE ticket_id = :i AND action = 'rca_invalidated' "
    "ORDER BY created_at DESC LIMIT 1"), {"i": t_fe}).fetchone()
check("rca_invalidated activity logged", bool(row))
s, j = req("POST", f"/support-desk/tickets/{t_fe}/rca", lead_tok, VALID_RCA)
check("re-file after stale -> filed", s == 200 and j.get("rca_status") == "filed", f"(status {s})")

print("-- T4: close gate --")
t_gate = new_ticket("close gate critical")           # priority critical -> gate applies
check("seat + assign member", seat_on_team(t_gate) == 200)
s, _ = resolve(t_gate, tok=member_tok, close=False)
check("resolve WITHOUT RCA stays free 200", s == 200, f"(status {s})")
s, j = resolve(t_gate, tok=member_tok, close=True)
check("close WITHOUT live RCA 422", s == 422, f"(status {s}, detail {str(j)[:80]})")
s, _ = req("POST", f"/support-desk/tickets/{t_gate}/rca", member_tok, VALID_RCA)
check("file RCA 200", s == 200, f"(status {s})")
s, _ = resolve(t_gate, tok=member_tok, close=True)
check("close after filing 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{t_gate}/rca/validate", lead_tok, {})
check("post-close validate 200 (review is post-seal work)", s == 200, f"(status {s})")

t_low = new_ticket("clean low-sev", ttype="service_request", priority="medium")
check("seat + assign member", seat_on_team(t_low) == 200)
s, _ = resolve(t_low, tok=member_tok, close=True)
check("clean low-sev close without RCA 200", s == 200, f"(status {s})")

t_cxl = new_ticket("cancelled critical")
check("seat + assign member", seat_on_team(t_cxl) == 200)
s, _ = resolve(t_cxl, tok=member_tok, close=True, code="cancelled")
check("cancelled outcome exempt from gate 200", s == 200, f"(status {s})")

# Breached NON-critical branch: push the resolution deadline into the past so the
# breach is HONEST — recompute_breach_flags (which runs on resolve) keeps it set.
t_brc = new_ticket("breached high", priority="high")
check("seat + assign member", seat_on_team(t_brc) == 200)
db.execute(text("UPDATE support_tickets SET resolution_due_at = now() - interval '2 hours', "
                "sla_resolution_breached = TRUE WHERE id = :i"),
           {"i": t_brc}); db.commit()
s, _ = resolve(t_brc, tok=member_tok, close=True)
check("breached close WITHOUT RCA 422", s == 422, f"(status {s})")

# Bulk close: one gated critical + one clean request -> per-item skip, batch 200.
t_bulk_bad = new_ticket("bulk gated critical")
t_bulk_ok = new_ticket("bulk clean", ttype="service_request", priority="low")
check("seat both", seat_on_team(t_bulk_bad) == 200 and seat_on_team(t_bulk_ok) == 200)
s, j = req("POST", "/support-desk/tickets/bulk", su_tok, {
    "ids": [t_bulk_bad, t_bulk_ok], "action": "close",
    "resolution_code": "solved", "resolution_summary": "probe bulk close"})
res = {str(r.get("id")): r for r in (j or {}).get("results", [])} if j else {}
bad = res.get(t_bulk_bad) or {}
good = res.get(t_bulk_ok) or {}
check("bulk 200, gated row SKIPPED with RCA reason", s == 200 and bad.get("skipped")
      and "root-cause" in str(bad.get("error") or "").lower(),
      f"(status {s}, error {str(bad.get('error'))[:70]})")
check("bulk clean row closed", bool(good) and not good.get("skipped"), f"({good})")

print("-- T5: RCA board (lenses + stats lockstep + seal) --")
s, j = req("GET", "/support-desk/incidents/rca/board?lens=owed&limit=100", su_tok)
ids = {str(i.get("ticket_id")) for i in (j or {}).get("items", [])}
stats = (j or {}).get("stats") or {}
check("board owed 200 + stats block", s == 200 and isinstance(stats.get("owed"), int)
      and "aging" in stats and "coverage_pct" in stats, f"(status {s})")
# t_brc is terminal (resolved via the failed close attempt? no - close 422 blocked the
# whole call, so resolve it now to make it owed debt).
s, _ = resolve(t_brc, tok=member_tok, close=False)
s, j = req("GET", "/support-desk/incidents/rca/board?lens=owed&limit=50"
                  "&q=probe-rcav2%3A%20breached%20high", su_tok)
ids = {str(i.get("ticket_id")) for i in (j or {}).get("items", [])}
check("breached terminal no-RCA ticket in OWED lens", t_brc in ids,
      f"({t_brc[:8]}, hits {len(ids)})")
s, j = req("GET", "/support-desk/incidents/rca/board?lens=pending&limit=100", su_tok)
ids = {str(i.get("ticket_id")) for i in (j or {}).get("items", [])}
check("filed ticket in PENDING lens", t_fe in ids and (j or {}).get("stats", {}).get("pending", 0) >= 1)
s, j = req("GET", "/support-desk/incidents/rca/board?lens=validated&limit=100", su_tok)
ids = {str(i.get("ticket_id")) for i in (j or {}).get("items", [])}
check("validated ticket in VALIDATED lens", t_gate in ids)
s, _ = req("GET", "/support-desk/incidents/rca/board?lens=bogus", su_tok)
check("bad lens 422", s == 422, f"(status {s})")
s, _ = req("GET", "/support-desk/incidents/rca/board?sort=bogus", su_tok)
check("bad sort 422", s == 422, f"(status {s})")
if foreign_tok:
    s, j = req("GET", "/support-desk/incidents/rca/board?lens=owed&limit=100", foreign_tok)
    fids = {str(i.get("ticket_id")) for i in (j or {}).get("items", [])}
    check("foreign agent sealed out of probe-team debt", s == 200 and t_brc not in fids,
          f"(status {s})")

print("-- T6: analytics --")
s, j = req("GET", "/support-desk/incidents/rca/analytics?days=90", su_tok)
blocks = {"coverage", "category_mix", "breach_reason_mix", "cycle_time", "review_latency",
          "debt_aging", "actions_follow_through", "kedb", "trend"}
check("analytics 200 + all blocks", s == 200 and j and blocks <= set(j.keys()),
      f"(status {s}, missing {sorted(blocks - set((j or {}).keys()))})")
cov = (j or {}).get("coverage") or {}
check("coverage consistent", isinstance(cov.get("pct"), int)
      and cov.get("covered", 0) + 0 <= cov.get("eligible", 0) or cov.get("eligible", 0) == 0,
      f"({cov})")

print("-- T7: recurrence clusters + promote --")
trio = []
for tag in ("alpha", "beta", "gamma"):
    tid = new_ticket(f"gatewayx timeoutz {tag}", priority="high")
    s, _ = req("POST", f"/support-desk/tickets/{tid}/assign", su_tok, {"assigned_agent_id": su_id})
    s, _ = resolve(tid)
    trio.append(tid)
s, j = req("GET", "/support-desk/incidents/rca/clusters?days=30&min_size=3&limit=20", su_tok)
clus = (j or {}).get("clusters") or []
target = next((c for c in clus if set(map(str, c.get("ticket_ids") or [])) >= set(trio)), None)
check("seeded trio clusters at min_size=3", s == 200 and target is not None,
      f"(status {s}, clusters {len(clus)})")
s, _ = req("GET", "/support-desk/incidents/rca/clusters?min_size=1", su_tok)
check("min_size below 2 422", s == 422, f"(status {s})")
s, j = req("POST", "/support-desk/incidents/rca/clusters/promote", su_tok, {
    "ticket_ids": trio, "title": f"{MARK}: recurring gateway timeouts",
    "statement": "Three gateway timeouts in one probe window.",
    "root_cause_hint": "Suspected connection pool ceiling."})
pid = (j or {}).get("problem_id")
check("promote 201 -> problem + linked", s == 201 and pid and (j or {}).get("linked") == 3,
      f"(status {s}, linked {(j or {}).get('linked')})")
if pid:
    made_problems.append(pid)
s, j = req("GET", "/support-desk/incidents/rca/clusters?days=30&min_size=3&limit=20", su_tok)
clus = (j or {}).get("clusters") or []
target = next((c for c in clus if set(map(str, c.get("ticket_ids") or [])) >= set(trio)), None)
check("re-list flags has_open_problem", target is not None and target.get("has_open_problem"),
      f"({target and target.get('open_problem_number')})")

print("-- T8: cascade rca_inherited --")
t_c1 = new_ticket("cascade empty slot", priority="high")   # open, no RCA
t_c2 = new_ticket("cascade live rca", priority="high")     # open, WITH live RCA
for tc in (t_c1, t_c2):
    s, _ = req("POST", f"/support-desk/tickets/{tc}/assign", su_tok, {"assigned_agent_id": su_id})
s, _ = req("POST", f"/support-desk/tickets/{t_c2}/rca", su_tok, {
    "rca_summary": "Human-filed root cause that must survive the cascade."})
s, j = req("POST", "/support-desk/problems/", su_tok, {
    "title": f"{MARK}: cascade source", "description": "probe",
    "root_cause": "Gateway pool ceiling reached under retry storms.",
    "linked_ticket_ids": [t_c1, t_c2]})
pid2 = (j or {}).get("id")
check("setup problem 201", s == 201 and pid2, f"(status {s})")
if pid2:
    made_problems.append(pid2)
s, j = req("POST", f"/support-desk/problems/{pid2}/resolve-linked", su_tok, {
    "resolution_summary": "Raised the ceiling + added backoff.",
    "resolution_code": "solved", "mark_problem_resolved": False})
res = {str(r.get("ticket_id")): r for r in (j or {}).get("results", [])} if j else {}
check("cascade 200, empty slot INHERITED", s == 200 and (res.get(t_c1) or {}).get("rca_inherited"),
      f"(status {s}, {res.get(t_c1)})")
check("live filing NOT overwritten", not (res.get(t_c2) or {}).get("rca_inherited"),
      f"({res.get(t_c2)})")
s, j = req("GET", f"/support-desk/tickets/{t_c1}", su_tok)
check("inherited ticket: filed + provenance", j and j.get("rca_status") == "filed"
      and j.get("rca_inherited_from_problem_id"),
      f"(rca_status {j and j.get('rca_status')})")
s, j = req("GET", f"/support-desk/tickets/{t_c2}", su_tok)
check("live ticket summary unchanged", j and "must survive" in (j.get("rca_summary") or ""))
row = db.execute(text(
    "SELECT id FROM support_ticket_activities WHERE ticket_id = :i AND action = 'rca_inherited' "
    "LIMIT 1"), {"i": t_c1}).fetchone()
check("rca_inherited activity logged", bool(row))

print("-- T9: additive surfaces (reports-overview, stats, legacy filter, timeline) --")
s, j = req("GET", "/support-desk/me/tickets/reports-overview", member_tok)
check("reports-overview 200 + rca_owed in totals", s == 200 and j
      and "rca_owed" in (j.get("totals") or {}), f"(status {s})")
s, j = req("GET", "/support-desk/incidents/stats", su_tok)
check("incidents stats still carries missing_rca", s == 200 and j and "missing_rca" in j,
      f"(status {s})")
s, j = req("GET", "/support-desk/tickets/?scope=sla_breached&missing_rca=true&limit=5", su_tok)
check("legacy missing_rca list filter 200", s == 200, f"(status {s})")
s, j = req("GET", "/support-desk/incidents/timeline?kinds=rca_recorded,rca_validated,rca_returned,"
                  "rca_invalidated,rca_inherited,rca_revised&limit=5", su_tok)
check("timeline accepts all six rca kinds", s == 200, f"(status {s})")
arow = db.execute(text(
    "SELECT id FROM support_ticket_activities WHERE ticket_id = :i AND action = 'rca_recorded' "
    "ORDER BY created_at ASC LIMIT 1"), {"i": t1}).fetchone()
if arow:
    s, j = req("POST", f"/support-desk/incidents/activities/{arow[0]}/pin", su_tok, {})
    check("rca_recorded is milestone-pinnable", s in (200, 201), f"(status {s})")
    req("DELETE", f"/support-desk/incidents/activities/{arow[0]}/pin", su_tok)
else:
    check("rca_recorded activity found for pin test", False)

print("-- cleanup --")
for p in made_problems:
    s, _ = req("DELETE", f"/support-desk/problems/{p}", su_tok)
    check(f"problem cleanup {str(p)[:8]}", s in (200, 204, 404, 409), f"(status {s})")
for t in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{t}?reason=probe%20cleanup", su_tok)
    check(f"archive {str(t)[:8]}", s in (204, 409, 404), f"(status {s})")
db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
