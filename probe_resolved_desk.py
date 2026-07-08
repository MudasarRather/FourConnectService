"""Probe the new Resolved desk (Closeout) workflow against the RUNNING backend (8000).

Mints JWTs directly (superuser + a non-superuser support agent), then exercises the
whole resolved lifecycle:
  * resolution-notes drop-gate: resolve without a summary -> 422 (single + bulk + self),
    with a summary -> 200; 'no_response' accepted on the admin route (enum-drift fix);
  * bare set-status into resolved/closed -> 422 (must go through /resolve);
  * attribution: resolved_by_id/closed_by_id stamped + resolved_by_name enriched;
    auto_close_at = resolved_at + 3d on the enriched row;
  * reopen clears resolved_by/closed_by + resolved_at, re-arms a FRESH resolution due
    (not instantly breached);
  * list refinements on BOTH panels: scope=resolved strict shelf, include_closed widens,
    resolution_code / csat=rated|unrated|low / resolved_from / pending_close /
    sort_by=resolved_at|csat_score|ttr;
  * CSAT set via /csat -> reflected in the csat=low filter + stats;
  * GET /me/tickets/resolved/stats shape (trend=14 buckets, p50<=p90, csat_dist sums,
    code mix contains the probe's code, leaderboard) + team seal (agent <= superuser);
  * auto-close: backdate resolved_at 4d -> opening scope=resolved seals it to closed.
Creates disposable tickets and soft-deletes them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_resolved_desk.py
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
TAG = "[PROBE-RES]"


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

print("\n== 1. Resolution-notes drop-gate + enum fix ==")
t1, s = mk(su_tok, f"{TAG} drop-gate ticket")
check("create t1", s in (200, 201) and t1, f"({s})")
s, _ = assign(su_tok, t1, su[0])
check("assign owner to t1", s == 200, f"({s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/resolve", su_tok, {"resolution_code": "solved"})
check("resolve WITHOUT summary -> 422", s == 422, f"({s})")
s, j = req("POST", f"/support-desk/tickets/{t1}/resolve", su_tok,
           {"resolution_code": "solved", "resolution_summary": "  "})
check("resolve with whitespace summary -> 422", s == 422, f"({s})")
s, j = req("POST", "/support-desk/tickets/bulk", su_tok,
           {"action": "resolve", "ids": [t1], "resolution_code": "solved"})
check("bulk resolve WITHOUT summary -> 422", s == 422, f"({s})")
s, j = req("POST", f"/support-desk/me/tickets/{t1}/resolve", su_tok, {"resolution_code": "solved"})
check("self resolve WITHOUT summary -> 422", s == 422, f"({s})")
s, j = resolve(su_tok, t1, "Probe fix - patched the config.", code="no_response")
check("resolve with code=no_response accepted (enum fix)", s == 200, f"({s})")

print("\n== 2. Bare set-status into terminal -> 422 ==")
t2, s = mk(su_tok, f"{TAG} set-status guard")
check("create t2", s in (200, 201) and t2, f"({s})")
s, _ = assign(su_tok, t2, su[0])
s, j = req("POST", f"/support-desk/tickets/{t2}/status", su_tok, {"status": "resolved"})
check("set-status resolved -> 422", s == 422, f"({s})")
s, j = req("POST", f"/support-desk/tickets/{t2}/status", su_tok, {"status": "closed"})
check("set-status closed -> 422", s == 422, f"({s})")

print("\n== 3. Attribution + enrichment ==")
s, j = resolve(su_tok, t2, "Probe fix - replaced the cable.")
check("resolve t2 with summary -> 200", s == 200, f"({s})")
check("resolved_by_id stamped", isinstance(j, dict) and str(j.get("resolved_by_id")) == str(su[0]))
check("resolved_by_name enriched", isinstance(j, dict) and bool(j.get("resolved_by_name")))
check("auto_close_at present on resolved", isinstance(j, dict) and bool(j.get("auto_close_at")))
if isinstance(j, dict) and j.get("auto_close_at") and j.get("resolved_at"):
    from datetime import datetime
    ra = datetime.fromisoformat(j["resolved_at"].replace("Z", "+00:00"))
    ac = datetime.fromisoformat(j["auto_close_at"].replace("Z", "+00:00"))
    check("auto_close_at = resolved_at + 3d", abs((ac - ra).total_seconds() - 3 * 86400) < 5)
else:
    check("auto_close_at = resolved_at + 3d", False, "(missing fields)")

t3, s = mk(su_tok, f"{TAG} close attribution")
s, _ = assign(su_tok, t3, su[0])
s, j = resolve(su_tok, t3, "Probe fix - closed straight away.", close=True)
check("resolve&close t3 -> 200", s == 200, f"({s})")
check("closed_by_id stamped", isinstance(j, dict) and str(j.get("closed_by_id")) == str(su[0]))

print("\n== 4. Reopen clears attribution + fresh SLA ==")
s, j = req("POST", f"/support-desk/tickets/{t2}/reopen", su_tok,
           {"reason": "probe - fix did not hold", "reason_code": "not_fixed"})
check("reopen t2 -> 200", s == 200, f"({s})")
check("resolved_by cleared on reopen", isinstance(j, dict) and j.get("resolved_by_id") is None)
check("resolved_at cleared on reopen", isinstance(j, dict) and j.get("resolved_at") is None)
check("fresh resolution clock (not breached)", isinstance(j, dict) and not j.get("sla_resolution_breached"))
s, j = resolve(su_tok, t2, "Probe fix v2 - held this time.")
check("re-resolve t2 -> 200", s == 200, f"({s})")

print("\n== 5. CSAT + list refinements (both panels) ==")
s, j = req("POST", f"/support-desk/tickets/{t2}/csat", su_tok, {"csat_score": 2, "csat_comment": "probe low rating"})
check("set CSAT=2 on t2", s == 200, f"({s})")

s, j = req("GET", "/support-desk/tickets/?scope=resolved&status=resolved&limit=100", su_tok)
shelf = ids_of(j)
check("admin shelf contains t1+t2", t1 in shelf and t2 in shelf)
check("admin shelf excludes closed t3", t3 not in shelf)
s, j = req("GET", "/support-desk/tickets/?scope=resolved&include_closed=1&limit=100", su_tok)
widened = ids_of(j)
check("include_closed widens to t3", t3 in widened)
s, j = req("GET", "/support-desk/tickets/?scope=resolved&resolution_code=no_response&limit=100", su_tok)
check("filter resolution_code=no_response -> t1 only", t1 in ids_of(j) and t2 not in ids_of(j))
s, j = req("GET", "/support-desk/tickets/?scope=resolved&csat=low&limit=100", su_tok)
check("filter csat=low -> t2", t2 in ids_of(j) and t1 not in ids_of(j))
s, j = req("GET", "/support-desk/tickets/?scope=resolved&csat=unrated&limit=100", su_tok)
check("filter csat=unrated -> t1", t1 in ids_of(j) and t2 not in ids_of(j))
s, j = req("GET", "/support-desk/tickets/?scope=resolved&pending_close=1&limit=100", su_tok)
check("pending_close pins the shelf", t1 in ids_of(j) and t3 not in ids_of(j))
s, j = req("GET", "/support-desk/tickets/?scope=resolved&resolved_from=2000-01-01T00:00:00&limit=100", su_tok)
check("resolved_from accepts datetime", s == 200 and t2 in ids_of(j), f"({s})")
for key in ("resolved_at", "csat_score", "ttr"):
    s, j = req("GET", f"/support-desk/tickets/?scope=resolved&sort_by={key}&sort_dir=desc&limit=10", su_tok)
    check(f"admin sort_by={key}", s == 200, f"({s})")
for key in ("resolved_at", "csat_score", "ttr"):
    s, j = req("GET", f"/support-desk/me/tickets/?scope=resolved&sort_by={key}&limit=10", su_tok)
    check(f"self sort_by={key}", s == 200, f"({s})")
s, j = req("GET", "/support-desk/me/tickets/?scope=resolved&status=resolved&csat=low&limit=100", su_tok)
check("self panel csat=low mirrors", s == 200 and t2 in ids_of(j), f"({s})")

print("\n== 6. Sealed /resolved/stats shape ==")
s, j = req("GET", "/support-desk/me/tickets/resolved/stats", su_tok)
check("stats 200", s == 200, f"({s})")
j = j if isinstance(j, dict) else {}
expected = [
    "resolved_now", "pending_close", "due_close_24h", "overdue_close", "unrated_shelf",
    "closed_total", "resolved_today", "resolved_7d", "resolved_30d", "trend",
    "mttr_avg_minutes", "mttr_p50_minutes", "mttr_p90_minutes", "mttr_by_priority",
    "avg_time_spent_minutes", "fcr_30d", "fcr_30d_pct", "reopens_30d", "survived_30d",
    "reopen_rate_30d", "sla_met_30d", "sla_met_pct_30d", "csat_avg", "csat_count",
    "csat_coverage_pct", "csat_low", "csat_dist", "by_resolution_code", "by_root_cause",
    "by_priority", "leaderboard", "squad", "team_count", "team_names",
]
missing = [k for k in expected if k not in j]
check("all stats keys present", not missing, f"missing={missing}" if missing else "")
check("resolved_today >= 2 (probe resolves)", (j.get("resolved_today") or 0) >= 2, f"({j.get('resolved_today')})")
check("trend has 14 buckets", len(j.get("trend") or []) == 14, f"({len(j.get('trend') or [])})")
p50, p90 = j.get("mttr_p50_minutes"), j.get("mttr_p90_minutes")
check("p50 <= p90", (p50 is None or p90 is None) or p50 <= p90, f"(p50={p50} p90={p90})")
dist_sum = sum(int(v or 0) for v in (j.get("csat_dist") or {}).values())
check("csat_dist sums to csat_count", dist_sum == (j.get("csat_count") or 0), f"({dist_sum} vs {j.get('csat_count')})")
check("by_resolution_code has probe code", "no_response" in (j.get("by_resolution_code") or {}))
check("leaderboard has entries", len(j.get("leaderboard") or []) >= 1)
check("csat_low >= 1 (probe rating)", (j.get("csat_low") or 0) >= 1, f"({j.get('csat_low')})")

print("\n== 7. Team seal (agent <= superuser) ==")
if ag_tok:
    s, aj = req("GET", "/support-desk/me/tickets/resolved/stats", ag_tok)
    check("agent stats 200", s == 200, f"({s})")
    aj = aj if isinstance(aj, dict) else {}
    check("agent resolved_30d <= superuser", (aj.get("resolved_30d") or 0) <= (j.get("resolved_30d") or 0),
          f"({aj.get('resolved_30d')} <= {j.get('resolved_30d')})")
    s, lj = req("GET", "/support-desk/tickets/?scope=resolved&include_closed=1&limit=100", ag_tok)
    agent_ids = ids_of(lj)
    su_ids = {t1, t2, t3}
    leaked = agent_ids & su_ids
    # Probe tickets are raised by the superuser with no team routing — an unrelated agent
    # must not see them unless the triage-pool taxonomy routes incidents to their team.
    print(f"  [INFO] agent sees {len(leaked)} of the probe tickets (triage-pool routing may allow some)")
else:
    print("  [SKIP] no non-superuser agent available")

print("\n== 8. Auto-close sweep on scope open ==")
db.execute(text("UPDATE support_tickets SET resolved_at = NOW() - INTERVAL '4 days' WHERE id = :tid"),
           {"tid": t1})
db.commit()
s, j = req("GET", "/support-desk/tickets/?scope=resolved&limit=1", su_tok)   # triggers the sweep
s, j = req("GET", f"/support-desk/tickets/{t1}", su_tok)
check("backdated t1 auto-closed by the sweep", isinstance(j, dict) and j.get("status") == "closed",
      f"({j.get('status') if isinstance(j, dict) else s})")
check("auto-close leaves closed_by NULL (system)", isinstance(j, dict) and j.get("closed_by_id") is None)

# ── teardown ──
db.execute(text(f"UPDATE support_tickets SET is_deleted = TRUE WHERE subject LIKE '{TAG}%'"))
db.commit()
db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
