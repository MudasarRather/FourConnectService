"""Probe the new Closed desk (Archive of Record) workflow against the RUNNING backend (8000).

Mints JWTs directly (superuser + a non-superuser support agent), then exercises the
whole closed lifecycle:
  * closure sources: manual (resolve close=true), auto-sweep (backdated resolved_at),
    merged (duplicate tombstone), withdrawn (requester cancel) — each lands in the right
    close_source bucket on the list filter AND in /closed/stats by_close_source;
  * closed_by_id/closed_by_name attribution (manual) vs NULL/System (auto-sweep);
  * follow-up: 201 from a closed ticket (child carries follow_up_of_id + enriched
    follow_up_of_number, requester preserved), 409 from an open ticket, 409 from a
    merged tombstone (points at the master); follow_up_of list filter finds the child;
  * KCS promote-article: 201 draft (status server-forced), idempotent second call
    returns the SAME article, 422 when there is no resolution summary (merged dup);
  * merge-chain: dup's masters contains the master; master's duplicates contains the dup;
  * CSAT verdict-of-record: first rating on a closed ticket OK; agent overwrite -> 409;
    superuser overwrite -> 200;
  * requester reopen from CLOSED -> 409 (agent-only); agent reopen -> 200 and the
    'reopened' activity carries from=closed (the exhume metric's source);
  * GET /me/tickets/closed/stats shape (12-month trend, buckets sum to closed_30d,
    p50<=p90, kb/follow-up counters, survival) + team seal (agent <= superuser);
  * closed_from / close_source filters on BOTH panels + CSV export with the new columns.
Creates disposable tickets and soft-deletes them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_closed_desk.py
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
TAG = "[PROBE-CLS]"


def req(method, path, token, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except Exception:
            return e.code, None


def req_raw(method, path, token):
    """Non-JSON fetch (CSV export)."""
    r = urllib.request.Request(f"{BASE}{path}", method=method)
    r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


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


def mk(token, subject, priority="high"):
    s, j = req("POST", "/support-desk/tickets/", token, {
        "subject": subject, "description": "probe - safe to ignore",
        "priority": priority, "ticket_type": "incident", "source": "internal",
    })
    return (j.get("id") if isinstance(j, dict) else None), s


def assign(token, tid, agent_id):
    return req("POST", f"/support-desk/tickets/{tid}/assign", token,
               {"assigned_agent_id": str(agent_id)})


def resolve(token, tid, summary, code="solved", close=False):
    return req("POST", f"/support-desk/tickets/{tid}/resolve", token, {
        "resolution_code": code, "resolution_summary": summary, "close": close})


def ids_of(j):
    return {r.get("id") for r in (j or {}).get("items", [])} if isinstance(j, dict) else set()


db = SessionLocal()
su = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
ag = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = FALSE AND is_support_agent = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
print(f"superuser: {su[1] if su else None}")
print(f"agent    : {ag[1] if ag else None}")
if not su:
    print("No superuser found - abort"); sys.exit(1)
su_tok = mint(su)
ag_tok = mint(ag) if ag else None

# Tombstone leftovers from an earlier crashed run so counts stay clean.
db.execute(text(f"UPDATE support_tickets SET is_deleted = TRUE WHERE subject LIKE '{TAG}%'"))
db.commit()

print("\n== 1. Closure sources: manual / auto-sweep / merged / withdrawn ==")
# manual close
t_man, s = mk(su_tok, f"{TAG} manual close")
check("create t_man", s in (200, 201) and t_man, f"({s})")
assign(su_tok, t_man, su[0])
s, j = resolve(su_tok, t_man, "Probe fix - sealed by hand.", close=True)
check("resolve&close t_man -> 200", s == 200, f"({s})")
check("closed_by_id stamped (manual)", isinstance(j, dict) and str(j.get("closed_by_id")) == str(su[0]))
check("closed_by_name enriched", isinstance(j, dict) and bool(j.get("closed_by_name")))
check("closed_at stamped", isinstance(j, dict) and bool(j.get("closed_at")))

# auto-sweep close
t_auto, s = mk(su_tok, f"{TAG} auto close")
check("create t_auto", s in (200, 201) and t_auto, f"({s})")
assign(su_tok, t_auto, su[0])
s, _ = resolve(su_tok, t_auto, "Probe fix - patched the config, KB-worthy.")
check("resolve t_auto -> 200", s == 200, f"({s})")
db.execute(text("UPDATE support_tickets SET resolved_at = NOW() - INTERVAL '4 days' WHERE id = :tid"),
           {"tid": t_auto})
db.commit()
s, _ = req("GET", "/support-desk/tickets/?scope=closed&limit=1", su_tok)   # sweep runs on the CLOSED desk too now
s, j = req("GET", f"/support-desk/tickets/{t_auto}", su_tok)
check("backdated t_auto auto-closed via scope=closed sweep",
      isinstance(j, dict) and j.get("status") == "closed",
      f"({j.get('status') if isinstance(j, dict) else s})")
check("auto-close leaves closed_by NULL (System)", isinstance(j, dict) and j.get("closed_by_id") is None)

# merged tombstone
t_master, s = mk(su_tok, f"{TAG} merge master")
t_dup, s = mk(su_tok, f"{TAG} merge duplicate")
check("create master+dup", bool(t_master and t_dup))
s, j = req("POST", f"/support-desk/tickets/{t_dup}/merge", su_tok, {"target_id": t_master})
check("merge dup -> 200", s == 200, f"({s})")
check("dup closed with merged_into_id", isinstance(j, dict) and j.get("status") == "closed"
      and str(j.get("merged_into_id")) == str(t_master))

# withdrawn (requester cancel) — raised via self-service so raised_by = superuser
s, j = req("POST", "/support-desk/me/tickets/", su_tok, {
    "subject": f"{TAG} withdrawn case", "description": "probe - will be withdrawn",
    "priority": "medium", "ticket_type": "incident", "source": "internal"})
t_wd = j.get("id") if isinstance(j, dict) else None
check("self-create t_wd", s in (200, 201) and t_wd, f"({s})")
s, j = req("POST", f"/support-desk/me/tickets/{t_wd}/withdraw", su_tok, {"reason": "probe - no longer needed"})
check("withdraw t_wd -> 200", s == 200, f"({s})")
check("withdraw = closed + code=cancelled", isinstance(j, dict) and j.get("status") == "closed"
      and j.get("resolution_code") == "cancelled")

print("\n== 2. close_source list filters (both panels) ==")
s, j = req("GET", "/support-desk/tickets/?scope=closed&close_source=manual&limit=100", su_tok)
f_man = ids_of(j)
check("close_source=manual has t_man, not t_auto/dup/wd",
      t_man in f_man and not ({t_auto, t_dup, t_wd} & f_man))
s, j = req("GET", "/support-desk/tickets/?scope=closed&close_source=auto_sweep&limit=100", su_tok)
f_auto = ids_of(j)
check("close_source=auto_sweep has t_auto only", t_auto in f_auto and not ({t_man, t_dup, t_wd} & f_auto))
s, j = req("GET", "/support-desk/tickets/?scope=closed&close_source=merged&limit=100", su_tok)
check("close_source=merged has dup", t_dup in ids_of(j))
s, j = req("GET", "/support-desk/tickets/?scope=closed&close_source=withdrawn&limit=100", su_tok)
check("close_source=withdrawn has t_wd", t_wd in ids_of(j))
s, j = req("GET", "/support-desk/tickets/?scope=closed&closed_from=2000-01-01T00:00:00&limit=100", su_tok)
check("closed_from accepts datetime", s == 200 and t_man in ids_of(j), f"({s})")
s, j = req("GET", "/support-desk/tickets/?scope=closed&closed_to=2000-01-01T00:00:00&limit=100", su_tok)
check("closed_to excludes fresh closures", s == 200 and t_man not in ids_of(j), f"({s})")
s, j = req("GET", "/support-desk/me/tickets/?scope=closed&close_source=manual&limit=100", su_tok)
check("self panel close_source mirrors", s == 200 and t_man in ids_of(j), f"({s})")
s, j = req("GET", "/support-desk/tickets/?scope=closed&sort_by=closed_at&sort_dir=desc&limit=10", su_tok)
check("sort_by=closed_at", s == 200, f"({s})")

print("\n== 3. Follow-up (Zendesk pattern) ==")
s, j = req("POST", f"/support-desk/tickets/{t_man}/follow-up", su_tok,
           {"description": "probe follow-up - the printer is acting up again"})
child = j.get("id") if isinstance(j, dict) else None
check("follow-up from closed -> 201", s == 201 and child, f"({s})")
check("child carries follow_up_of_id", isinstance(j, dict) and str(j.get("follow_up_of_id")) == str(t_man))
check("child follow_up_of_number enriched", isinstance(j, dict) and bool(j.get("follow_up_of_number")))
check("child subject defaulted", isinstance(j, dict) and str(j.get("subject", "")).startswith("Follow-up:"))
t_open, s = mk(su_tok, f"{TAG} open no-follow-up")
s, j = req("POST", f"/support-desk/tickets/{t_open}/follow-up", su_tok, {"description": "probe"})
check("follow-up from OPEN -> 409", s == 409, f"({s})")
s, j = req("POST", f"/support-desk/tickets/{t_dup}/follow-up", su_tok, {"description": "probe"})
check("follow-up from merged tombstone -> 409 (points at master)", s == 409, f"({s})")
s, j = req("GET", f"/support-desk/tickets/?follow_up_of={t_man}&limit=10", su_tok)
check("follow_up_of filter finds the child", child in ids_of(j))
# tombstone the child so it never skews other desks
if child:
    db.execute(text("UPDATE support_tickets SET subject = :s WHERE id = :tid"),
               {"s": f"{TAG} follow-up child", "tid": child})
    db.commit()

print("\n== 4. KCS promote-article ==")
s, j = req("POST", f"/support-desk/tickets/{t_auto}/promote-article", su_tok, {})
aid1 = j.get("id") if isinstance(j, dict) else None
check("promote closed t_auto -> 201 draft", s == 201 and aid1, f"({s})")
check("article status server-forced to draft", isinstance(j, dict) and j.get("status") == "draft")
s, j = req("POST", f"/support-desk/tickets/{t_auto}/promote-article", su_tok, {})
aid2 = j.get("id") if isinstance(j, dict) else None
check("second promote is idempotent (same article)", aid1 and aid1 == aid2, f"({aid1} vs {aid2})")
s, j = req("POST", f"/support-desk/tickets/{t_dup}/promote-article", su_tok, {})
check("promote without a resolution summary -> 422", s == 422, f"({s})")
s, j = req("POST", f"/support-desk/tickets/{t_open}/promote-article", su_tok, {})
check("promote an OPEN ticket -> 409", s == 409, f"({s})")

print("\n== 5. Merge-chain viewer ==")
s, j = req("GET", f"/support-desk/tickets/{t_dup}/merge-chain", su_tok)
masters = {m.get("id") for m in (j or {}).get("masters", [])} if isinstance(j, dict) else set()
check("dup's chain walks UP to the master", s == 200 and t_master in masters, f"({s})")
s, j = req("GET", f"/support-desk/tickets/{t_master}/merge-chain", su_tok)
dups = {m.get("id") for m in (j or {}).get("duplicates", [])} if isinstance(j, dict) else set()
check("master's chain lists the folded dup", s == 200 and t_dup in dups, f"({s})")

print("\n== 6. CSAT verdict-of-record guard ==")
s, j = req("POST", f"/support-desk/tickets/{t_man}/csat", su_tok, {"csat_score": 4, "csat_comment": "probe verdict"})
check("first rating on a closed ticket -> 200", s == 200, f"({s})")
if ag_tok:
    s, j = req("POST", f"/support-desk/tickets/{t_man}/csat", ag_tok, {"csat_score": 5})
    check("agent overwrite of sealed verdict -> 409", s == 409, f"({s})")
else:
    print("  [SKIP] no non-superuser agent for the overwrite test")
s, j = req("POST", f"/support-desk/tickets/{t_man}/csat", su_tok, {"csat_score": 5, "csat_comment": "probe corrected"})
check("superuser correction allowed -> 200", s == 200, f"({s})")

print("\n== 7. Reopen: requester 409, agent exhumes ==")
s, j = req("POST", f"/support-desk/me/tickets/{t_wd}/reopen", su_tok, {"reason": "probe"})
check("requester reopen from CLOSED -> 409", s == 409, f"({s})")
s, j = req("POST", f"/support-desk/tickets/{t_man}/reopen", su_tok,
           {"reason": "probe - the record needed reopening", "reason_code": "not_fixed"})
check("agent reopen from CLOSED -> 200", s == 200, f"({s})")
check("reopen cleared closed_at/by", isinstance(j, dict) and j.get("closed_at") is None
      and j.get("closed_by_id") is None)
check("CSAT survives the reopen (historic verdict)", isinstance(j, dict) and j.get("csat_score") == 5)
row = db.execute(text(
    "SELECT detail->>'from' FROM support_ticket_activities "
    "WHERE ticket_id = :tid AND action = 'reopened' ORDER BY created_at DESC LIMIT 1"),
    {"tid": t_man}).fetchone()
check("'reopened' activity stamps from=closed", bool(row and row[0] == "closed"), f"({row[0] if row else None})")

print("\n== 8. Sealed /closed/stats shape ==")
s, j = req("GET", "/support-desk/me/tickets/closed/stats", su_tok)
check("stats 200", s == 200, f"({s})")
j = j if isinstance(j, dict) else {}
expected = [
    "closed_today", "closed_7d", "closed_30d", "closed_total", "resolved_waiting",
    "by_close_source", "merged_total", "by_resolution_code", "by_root_cause", "by_priority",
    "uncoded_30d", "lifespan_avg_minutes", "lifespan_p50_minutes", "lifespan_p90_minutes",
    "lifespan_by_priority", "reopened_from_closed_30d", "closure_survival_pct_30d",
    "csat_avg", "csat_count", "csat_coverage_pct", "csat_low", "csat_dist",
    "kb_candidates_30d", "kb_promoted_total", "follow_ups_30d", "open_follow_ups",
    "trend", "leaderboard", "auto_closed_30d", "team_count", "team_names",
]
missing = [k for k in expected if k not in j]
check("all stats keys present", not missing, f"missing={missing}" if missing else "")
check("trend has 12 monthly buckets", len(j.get("trend") or []) == 12, f"({len(j.get('trend') or [])})")
srcs = j.get("by_close_source") or {}
check("by_close_source sums to closed_30d", sum(int(v or 0) for v in srcs.values()) == (j.get("closed_30d") or 0),
      f"({srcs} vs {j.get('closed_30d')})")
check("auto_sweep bucket >= 1 (t_auto)", int(srcs.get("auto_sweep") or 0) >= 1, f"({srcs.get('auto_sweep')})")
check("merged bucket >= 1 (t_dup)", int(srcs.get("merged") or 0) >= 1, f"({srcs.get('merged')})")
check("withdrawn bucket >= 1 (t_wd)", int(srcs.get("withdrawn") or 0) >= 1, f"({srcs.get('withdrawn')})")
p50, p90 = j.get("lifespan_p50_minutes"), j.get("lifespan_p90_minutes")
check("lifespan p50 <= p90", (p50 is None or p90 is None) or p50 <= p90, f"(p50={p50} p90={p90})")
check("kb_promoted_total >= 1 (t_auto's article)", (j.get("kb_promoted_total") or 0) >= 1,
      f"({j.get('kb_promoted_total')})")
check("follow_ups_30d >= 1 (the child)", (j.get("follow_ups_30d") or 0) >= 1, f"({j.get('follow_ups_30d')})")
check("reopened_from_closed_30d >= 1 (t_man exhumed)", (j.get("reopened_from_closed_30d") or 0) >= 1,
      f"({j.get('reopened_from_closed_30d')})")
check("closure_survival_pct within 0..100", j.get("closure_survival_pct_30d") is None
      or 0 <= j["closure_survival_pct_30d"] <= 100, f"({j.get('closure_survival_pct_30d')})")
dist_sum = sum(int(v or 0) for v in (j.get("csat_dist") or {}).values())
check("csat_dist sums to csat_count", dist_sum == (j.get("csat_count") or 0), f"({dist_sum} vs {j.get('csat_count')})")
check("leaderboard is a list", isinstance(j.get("leaderboard"), list))

print("\n== 9. Team seal (agent <= superuser) ==")
if ag_tok:
    s, aj = req("GET", "/support-desk/me/tickets/closed/stats", ag_tok)
    check("agent stats 200", s == 200, f"({s})")
    aj = aj if isinstance(aj, dict) else {}
    check("agent closed_total <= superuser", (aj.get("closed_total") or 0) <= (j.get("closed_total") or 0),
          f"({aj.get('closed_total')} <= {j.get('closed_total')})")
    s, lj = req("GET", "/support-desk/tickets/?scope=closed&limit=100", ag_tok)
    check("agent closed list 200 (sealed)", s == 200, f"({s})")
else:
    print("  [SKIP] no non-superuser agent available")

print("\n== 10. CSV export with the new columns ==")
s, body = req_raw("GET", "/support-desk/tickets/export?scope=closed&close_source=merged", su_tok)
check("export scope=closed 200", s == 200, f"({s})")
head = (body.splitlines() or [""])[0]
check("export has Close source/Closed by/CSAT columns",
      "Close source" in head and "Closed by" in head and "CSAT" in head)
check("export rows honor close_source=merged", "merged" in body)

# -- teardown --
db.execute(text(f"UPDATE support_tickets SET is_deleted = TRUE WHERE subject LIKE '{TAG}%'"))
db.execute(text("UPDATE support_kb_articles SET is_deleted = TRUE WHERE short_description LIKE 'Harvested from%' AND title LIKE '%[PROBE-CLS]%'"))
db.commit()
db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
