"""Probe the new Reopened desk (Mobius Loop) workflow against the RUNNING backend (8000).

Mints JWTs directly (superuser + a non-superuser support agent), then exercises the
whole reopen lifecycle:
  * agent reopen with reason_code -> metadata stamped (source/by/at/latency/prev_*),
    fresh re-resolution SLA (due > now, NOT breached - the "instantly overdue" loophole),
    409 on non-terminal;
  * requester self-reopen (source=requester; 409 on closed);
  * customer-reply auto-reopen: reply to RESOLVED inside the 3-day window reopens
    (source=portal), reply to an open ticket does NOT, reply past the window does NOT;
  * exactly ONE 'reopened' activity per cycle;
  * list filters (scope=reopened / reopen_source / chronic / reopened_from +
    sort_by=last_reopened_at) on BOTH panels;
  * GET /me/tickets/reopened/stats shape + team seal (agent totals <= superuser).
Creates disposable tickets and soft-deletes them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_reopened_desk.py
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
        with urllib.request.urlopen(r, timeout=45) as resp:
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


def mk(token, subject):
    s, j = req("POST", "/support-desk/tickets/", token, {
        "subject": subject, "description": "probe - safe to ignore",
        "priority": "high", "ticket_type": "incident", "source": "internal",
    })
    return (j.get("id") if isinstance(j, dict) else None), s


def resolve(token, tid, summary):
    return req("POST", f"/support-desk/tickets/{tid}/resolve", token, {
        "resolution_code": "solved", "resolution_summary": summary})


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
db.execute(text("UPDATE support_tickets SET is_deleted = TRUE WHERE subject LIKE '[PROBE-ROP]%'"))
db.commit()

print("-- setup: probe tickets --")
tA, sA = mk(su_tok, "[PROBE-ROP] agent+self reopen cycles")
tB, sB = mk(su_tok, "[PROBE-ROP] portal auto-reopen")
tC, sC = mk(su_tok, "[PROBE-ROP] open control (no reopen on reply)")
tD, sD = mk(su_tok, "[PROBE-ROP] stale resolved (past window)")
check("create 4 probe tickets", all([tA, tB, tC, tD]), f"({sA},{sB},{sC},{sD})")
if not all([tA, tB, tC, tD]):
    sys.exit(1)
# Make the superuser the requester so self routes (_own) + reply hooks see them.
db.execute(text("UPDATE support_tickets SET raised_by_user_id = :u WHERE id IN (:a,:b,:c,:d)"),
           {"u": str(su[0]), "a": tA, "b": tB, "c": tC, "d": tD})
db.commit()

print("-- 1. agent reopen: metadata + fresh SLA (the loophole test) --")
s, _ = resolve(su_tok, tA, "first fix - will bounce")
check("resolve A", s == 200, f"({s})")
# age the resolve + make the ORIGINAL deadline already past, so the old behavior
# (stale resolution_due_at) would flag the reopened ticket breached instantly.
db.execute(text("UPDATE support_tickets SET resolved_at = NOW() - INTERVAL '90 minutes', "
                "resolution_due_at = NOW() - INTERVAL '30 minutes' WHERE id = :i"), {"i": tA})
db.commit()
s, j = req("POST", f"/support-desk/tickets/{tA}/reopen", su_tok,
           {"reason": "customer says VPN still drops", "reason_code": "not_fixed"})
check("agent reopen 200", s == 200, f"({s})")
if s == 200:
    check("reopened_count == 1", j.get("reopened_count") == 1)
    check("reopen_source == agent", j.get("reopen_source") == "agent")
    check("reason_code stamped", j.get("reopen_reason_code") == "not_fixed")
    check("last_reopened_at stamped", bool(j.get("last_reopened_at")))
    check("last_reopened_by stamped", str(j.get("last_reopened_by_id")) == str(su[0]))
    check("latency recorded (~90m)", (j.get("reopen_latency_ms") or 0) > 60 * 60 * 1000)
    check("prev fix snapshot kept", j.get("prev_resolution_code") == "solved"
          and "first fix" in (j.get("prev_resolution_summary") or ""))
    check("live resolution cleared", j.get("resolution_code") is None and j.get("resolved_at") is None)
    check("status back to in_progress", j.get("status") == "in_progress")
    row = db.execute(text("SELECT resolution_due_at > NOW(), sla_resolution_breached "
                          "FROM support_tickets WHERE id = :i"), {"i": tA}).fetchone()
    check("LOOPHOLE FIXED: fresh re-resolution due > now", bool(row[0]))
    check("LOOPHOLE FIXED: not re-flagged breached", row[1] is False)
s, _ = req("POST", f"/support-desk/tickets/{tA}/reopen", su_tok, {"reason": "again"})
check("reopen non-terminal -> 409", s == 409, f"({s})")

print("-- 2. requester self-reopen (source=requester) + closed guard --")
s, _ = resolve(su_tok, tA, "second fix - will bounce again")
check("re-resolve A", s == 200, f"({s})")
s, j = req("POST", f"/support-desk/me/tickets/{tA}/reopen", su_tok,
           {"reason": "still broken after the second fix", "reason_code": "recurred"})
check("self reopen 200", s == 200, f"({s})")
if s == 200:
    check("reopened_count == 2 (chronic)", j.get("reopened_count") == 2)
    check("reopen_source == requester", j.get("reopen_source") == "requester")
    check("prev snapshot rolled", "second fix" in (j.get("prev_resolution_summary") or ""))
db.execute(text("UPDATE support_tickets SET status='closed', closed_at=NOW(), resolved_at=NOW() "
                "WHERE id = :i"), {"i": tA})
db.commit()
s, _ = req("POST", f"/support-desk/me/tickets/{tA}/reopen", su_tok, {"reason": "let me back in"})
check("self reopen CLOSED -> 409", s == 409, f"({s})")
db.execute(text("UPDATE support_tickets SET status='in_progress', closed_at=NULL, resolved_at=NULL "
                "WHERE id = :i"), {"i": tA})
db.commit()

print("-- 3. customer-reply auto-reopen (portal loophole) --")
s, _ = resolve(su_tok, tB, "fixed the export")
check("resolve B", s == 200, f"({s})")
s, _ = req("POST", f"/support-desk/me/tickets/{tB}/comments", su_tok,
           {"body": "It broke again this morning - same truncation."})
check("requester reply on RESOLVED -> 201", s == 201, f"({s})")
row = db.execute(text("SELECT status, reopened_count, reopen_source FROM support_tickets "
                      "WHERE id = :i"), {"i": tB}).fetchone()
check("B auto-reopened to a work state", row[0] in ("open", "in_progress"), f"({row[0]})")
check("B reopened_count == 1", row[1] == 1)
check("B reopen_source == portal", row[2] == "portal")
s, _ = req("POST", f"/support-desk/me/tickets/{tC}/comments", su_tok, {"body": "just adding info"})
row = db.execute(text("SELECT reopened_count FROM support_tickets WHERE id = :i"), {"i": tC}).fetchone()
check("reply on OPEN ticket does NOT reopen", s == 201 and row[0] == 0, f"({s},{row[0]})")
s, _ = resolve(su_tok, tD, "old fix")
db.execute(text("UPDATE support_tickets SET resolved_at = NOW() - INTERVAL '5 days' WHERE id = :i"),
           {"i": tD})
db.commit()
s, _ = req("POST", f"/support-desk/me/tickets/{tD}/comments", su_tok, {"body": "too late reply"})
row = db.execute(text("SELECT status, reopened_count FROM support_tickets WHERE id = :i"), {"i": tD}).fetchone()
check("reply PAST 3d window does NOT reopen", s == 201 and row[0] == "resolved" and row[1] == 0,
      f"({s},{row[0]},{row[1]})")

print("-- 4. exactly one 'reopened' activity per cycle --")
nA = db.execute(text("SELECT COUNT(*) FROM support_ticket_activities WHERE ticket_id = :i "
                     "AND action = 'reopened'"), {"i": tA}).fetchone()[0]
nB = db.execute(text("SELECT COUNT(*) FROM support_ticket_activities WHERE ticket_id = :i "
                     "AND action = 'reopened'"), {"i": tB}).fetchone()[0]
check("A: 2 cycles -> 2 activities", nA == 2, f"({nA})")
check("B: 1 cycle -> 1 activity", nB == 1, f"({nB})")

print("-- 5. list filters + sorts (both panels) --")
s, j = req("GET", "/support-desk/tickets/?scope=reopened&limit=100", su_tok)
got = ids_of(j)
check("admin scope=reopened has A+B", s == 200 and tA in got and tB in got, f"({s})")
check("...and not C (never reopened)", tC not in got)
s, j = req("GET", "/support-desk/tickets/?scope=reopened&reopen_source=portal&limit=100", su_tok)
got = ids_of(j)
check("filter reopen_source=portal -> B only", tB in got and tA not in got)
s, j = req("GET", "/support-desk/tickets/?scope=reopened&chronic=true&limit=100", su_tok)
got = ids_of(j)
check("filter chronic -> A (2x) not B (1x)", tA in got and tB not in got)
s, j = req("GET", "/support-desk/tickets/?scope=reopened&reopened_from=2000-01-01T00:00:00Z&limit=100", su_tok)
check("filter reopened_from works", s == 200 and tA in ids_of(j), f"({s})")
s, j = req("GET", "/support-desk/tickets/?scope=reopened&sort_by=last_reopened_at&sort_dir=desc&limit=100", su_tok)
check("admin sort_by=last_reopened_at 200", s == 200, f"({s})")
s, j = req("GET", "/support-desk/me/tickets/?scope=reopened&sort_by=reopened_count&sort_dir=desc&limit=100", su_tok)
got = ids_of(j)
check("self mirror: scope=reopened + sort", s == 200 and tA in got and tB in got, f"({s})")
s, j = req("GET", "/support-desk/me/tickets/?scope=reopened&chronic=true&limit=100", su_tok)
check("self mirror: chronic filter", s == 200 and tA in ids_of(j) and tB not in ids_of(j), f"({s})")

print("-- 6. reopened/stats shape + team seal --")
s, j = req("GET", "/support-desk/me/tickets/reopened/stats", su_tok)
check("stats 200", s == 200, f"({s})")
if s == 200:
    keys = ["total_reopened", "active_reopened", "re_resolved", "chronic", "chronic_open",
            "by_source", "by_reason", "by_priority", "by_status", "reopens_30d",
            "resolved_30d", "reopen_rate_30d", "re_breached", "due_soon_reopened",
            "unassigned_reopened", "critical_reopened", "max_reopens", "squad",
            "team_count", "team_names"]
    missing = [k for k in keys if k not in j]
    check("stats shape complete", not missing, f"missing={missing}" if missing else "")
    check("total >= 2 (A+B)", (j.get("total_reopened") or 0) >= 2, f"({j.get('total_reopened')})")
    check("chronic >= 1 (A)", (j.get("chronic") or 0) >= 1, f"({j.get('chronic')})")
    check("by_source has portal + requester",
          "portal" in (j.get("by_source") or {}) and "requester" in (j.get("by_source") or {}),
          f"({j.get('by_source')})")
    check("by_reason has coded verdicts",
          any(k in (j.get("by_reason") or {}) for k in ("not_fixed", "recurred")),
          f"({j.get('by_reason')})")
    check("max_reopens >= 2", (j.get("max_reopens") or 0) >= 2)
    check("avg_time_to_reopen recorded", j.get("avg_time_to_reopen_minutes") is not None)
    su_total = j.get("total_reopened") or 0
    if ag_tok:
        s2, j2 = req("GET", "/support-desk/me/tickets/reopened/stats", ag_tok)
        check("agent stats 200 (team-sealed)", s2 == 200, f"({s2})")
        if s2 == 200:
            check("TEAM SEAL: agent total <= superuser total",
                  (j2.get("total_reopened") or 0) <= su_total,
                  f"(agent={j2.get('total_reopened')}, su={su_total})")
    else:
        print("  [SKIP] no non-superuser agent available for seal check")

print("-- 7. agent panel list seal --")
if ag_tok:
    s, j = req("GET", "/support-desk/tickets/?scope=reopened&limit=100", ag_tok)
    check("agent list 200 (sealed, never whole desk)", s == 200, f"({s})")
    s, j = req("GET", "/support-desk/me/tickets/reopened/stats", ag_tok)
    check("agent stats reachable", s == 200, f"({s})")
else:
    print("  [SKIP] no non-superuser agent available")

print("-- cleanup --")
db.execute(text("UPDATE support_tickets SET is_deleted = TRUE WHERE subject LIKE '[PROBE-ROP]%'"))
db.commit()
db.close()
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
