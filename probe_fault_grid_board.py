"""Probe the Fault Grid board enhancements against the RUNNING backend (port 8000).

Exercises: server paging (page/limit echo + disjoint pages + back-compat), the posture
`flag` filters vs /incidents/stats counts (shared flag_condition — may NOT drift), sort_by
whitelist, the hardened incident-impact guards (422 non-incident, 409 merged), parent/child
linking (link / self / two-level / non-incident / children list / unlink / rollup fields),
the reports-overview major_incidents rollup, and the parent-resolve child-note hook.
Creates disposable probe tickets and archives them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_fault_grid_board.py
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
        with urllib.request.urlopen(r, timeout=30) as resp:
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
ag = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = FALSE AND is_support_agent = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
print(f"superuser: {su[1] if su else None}")
print(f"agent    : {ag[1] if ag else None}")
if not su:
    print("No superuser found - abort"); sys.exit(1)
su_tok = mint(su)
su_id = str(su[0])
ag_tok = mint(ag) if ag else None
db.close()

made = []   # ticket ids to archive at the end


def create(subject, ttype="incident", priority="high"):
    s, j = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": subject, "description": "probe - safe to ignore",
        "priority": priority, "ticket_type": ttype, "source": "internal",
    })
    tid = j.get("id") if isinstance(j, dict) else None
    if tid:
        made.append(tid)
    return s, tid


print("-- 1. list back-compat + paging --")
s, j = req("GET", "/support-desk/incidents/", su_tok)
check("no-param list 200 + legacy shape", s == 200 and isinstance(j.get("items"), list)
      and j.get("page") == 1, f"(status {s}, total {j.get('total') if j else '?'})")
s, j = req("GET", "/support-desk/incidents/?lens=all&page=1&limit=1", su_tok)
tot = j.get("total") if j else 0
p1 = [it["id"] for it in (j.get("items") or [])] if j else []
check("page echo", s == 200 and j.get("page") == 1 and j.get("limit") == 1, f"(status {s})")
if tot and tot > 1:
    s, j2 = req("GET", "/support-desk/incidents/?lens=all&page=2&limit=1", su_tok)
    p2 = [it["id"] for it in (j2.get("items") or [])] if j2 else []
    check("page1/page2 disjoint, same total", s == 200 and j2.get("total") == tot
          and p1 and p2 and not set(p1) & set(p2), f"(status {s}, tot {tot})")
else:
    print("  [SKIP] fewer than 2 incidents on the desk; disjoint-page check skipped")

print("-- 2. flag filters vs stats (shared predicates) --")
s, st = req("GET", "/support-desk/incidents/stats", su_tok)
check("stats 200 + unowned present", s == 200 and st is not None and "unowned" in st,
      f"(status {s}, unowned {st.get('unowned') if st else '?'})")
if st:
    by_sev = st.get("by_sev") or {}
    check("sum(by_sev) == active_total", sum(by_sev.values()) == st.get("active_total"),
          f"({by_sev} vs {st.get('active_total')})")
    pairs = [("unacked", st.get("unacked")), ("at_risk", (st.get("sla") or {}).get("at_risk")),
             ("breached", (st.get("sla") or {}).get("breached")), ("unowned", st.get("unowned")),
             ("cmdr_unstaffed", st.get("roles_unassigned")),
             ("update_overdue", st.get("update_overdue"))]
    for flag, want in pairs:
        s, j = req("GET", f"/support-desk/incidents/?flag={flag}&limit=1", su_tok)
        check(f"flag={flag} count == stats", s == 200 and j is not None and j.get("total") == want,
              f"(list {j.get('total') if j else '?'} vs stats {want})")
s, _ = req("GET", "/support-desk/incidents/?flag=bogus", su_tok)
check("bad flag -> 422", s == 422, f"(status {s})")
s, _ = req("GET", "/support-desk/incidents/?sort_by=bogus", su_tok)
check("bad sort_by -> 422", s == 422, f"(status {s})")

print("-- 3. sort ordering --")
s, j = req("GET", "/support-desk/incidents/?lens=all&sort_by=created_at&sort_dir=asc&limit=20", su_tok)
if s == 200 and j and len(j.get("items") or []) >= 2:
    dates = [it["created_at"] for it in j["items"]]
    check("created_at asc ordering", dates == sorted(dates), f"({len(dates)} rows)")
else:
    check("sort_by created_at asc 200", s == 200, f"(status {s})")

print("-- 4. impact guards --")
s, inc_id = create("[PROBE] fault-grid impact guard")
check("create probe incident", s in (200, 201) and inc_id, f"(status {s})")
if inc_id:
    s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-impact", su_tok,
               {"affected_services": ["probe-svc"], "affected_users": 7})
    check("impact happy 200", s == 200, f"(status {s})")
s, q_id = create("[PROBE] fault-grid non-incident", ttype="service_request", priority="low")
if q_id:
    s, j = req("PATCH", f"/support-desk/tickets/{q_id}/incident-impact", su_tok,
               {"affected_users": 1})
    check("impact on non-incident -> 422", s == 422, f"(status {s})")
s, dup_id = create("[PROBE] fault-grid merged dup")
if dup_id and inc_id:
    s, _ = req("POST", f"/support-desk/tickets/{dup_id}/merge", su_tok, {"target_id": inc_id})
    check("merge dup into master", s == 200, f"(status {s})")
    s, _ = req("PATCH", f"/support-desk/tickets/{dup_id}/incident-impact", su_tok,
               {"affected_users": 2})
    check("impact on merged -> 409", s == 409, f"(status {s})")

print("-- 5. parent/child linking --")
s, parent_id = create("[PROBE] fault-grid master incident")
s, child_id = create("[PROBE] fault-grid child incident")
s, grand_id = create("[PROBE] fault-grid grandchild incident")
if parent_id and child_id:
    s, j = req("PATCH", f"/support-desk/tickets/{child_id}/incident-parent", su_tok,
               {"parent_id": parent_id})
    check("link child -> 200 + number", s == 200 and j and j.get("parent_incident_number"),
          f"(status {s}, master {j.get('parent_incident_number') if j else '?'})")
    s, _ = req("PATCH", f"/support-desk/tickets/{child_id}/incident-parent", su_tok,
               {"parent_id": child_id})
    check("self-link -> 422", s == 422, f"(status {s})")
    if grand_id:
        s, _ = req("PATCH", f"/support-desk/tickets/{grand_id}/incident-parent", su_tok,
                   {"parent_id": child_id})
        check("two-level link -> 422", s == 422, f"(status {s})")
    if q_id:
        s, _ = req("PATCH", f"/support-desk/tickets/{child_id}/incident-parent", su_tok,
                   {"parent_id": q_id})
        check("non-incident parent -> 422", s == 422, f"(status {s})")
    s, j = req("GET", f"/support-desk/incidents/{parent_id}/children", su_tok)
    kid_ids = [it["id"] for it in (j.get("items") or [])] if j else []
    check("children list carries the child", s == 200 and child_id in kid_ids,
          f"(status {s}, kids {len(kid_ids)})")
    s, j = req("GET", "/support-desk/incidents/?lens=active&limit=100", su_tok)
    rows = {it["id"]: it for it in (j.get("items") or [])} if j else {}
    prow, crow = rows.get(parent_id), rows.get(child_id)
    check("list rollup fields", bool(prow and prow.get("child_count") == 1
          and crow and crow.get("parent_incident_number")),
          f"(parent child_count {prow.get('child_count') if prow else '?'}, "
          f"child master {crow.get('parent_incident_number') if crow else '?'})")

    # parent-resolve -> child gets the parent_incident_resolved note
    # (resolve requires an owner; superuser may not be an assignable agent, so prefer
    # the probe agent when one exists)
    owner = str(ag[0]) if ag else su_id
    s, aj = req("POST", f"/support-desk/tickets/{parent_id}/assign", su_tok,
                {"assigned_agent_id": owner})
    check("assign master owner", s in (200, 201) and (aj or {}).get("assigned_agent_id"),
          f"(status {s})")
    s, _ = req("POST", f"/support-desk/tickets/{parent_id}/resolve", su_tok,
               {"resolution_code": "solved", "resolution_summary": "probe: master resolved - verifying child hook"})
    check("resolve master", s == 200, f"(status {s})")
    s, j = req("GET", "/support-desk/incidents/timeline?kinds=parent_incident_resolved&limit=50", su_tok)
    hits = [e for d in (j.get("days") or []) for e in (d.get("events") or [])
            if e.get("ticket_id") == child_id] if j else []
    check("child noted on master resolve", s == 200 and len(hits) >= 1, f"(status {s}, notes {len(hits)})")

    s, j = req("PATCH", f"/support-desk/tickets/{child_id}/incident-parent", su_tok, {"clear": True})
    check("unlink -> 200", s == 200 and j and j.get("parent_incident_id") is None, f"(status {s})")
    s, _ = req("PATCH", f"/support-desk/tickets/{child_id}/incident-parent", su_tok, {"clear": True})
    check("re-clear -> 422 (nothing to clear)", s == 422, f"(status {s})")

print("-- 6. reports-overview rollup --")
s, j = req("GET", "/support-desk/me/tickets/reports-overview", su_tok)
tot_keys = set((j.get("totals") or {}).keys()) if j else set()
check("totals.major_incidents present", s == 200 and "major_incidents" in tot_keys,
      f"(status {s}, keys {sorted(tot_keys)})")

print("-- 7. agent seal still holds --")
if ag_tok:
    s, j = req("GET", "/support-desk/incidents/?flag=breached&limit=5", ag_tok)
    check("agent flag list 200 (sealed)", s == 200 and j is not None, f"(status {s}, total {j.get('total') if j else '?'})")
    s, j = req("GET", "/support-desk/incidents/stats", ag_tok)
    check("agent stats 200 (sealed)", s == 200 and j is not None and "unowned" in j, f"(status {s})")
else:
    print("  [SKIP] no non-superuser support agent; seal probes skipped")

print("-- cleanup --")
for tid in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{tid}?reason=probe%20cleanup", su_tok)
    check(f"archive {tid[:8]}", s in (204, 409, 404), f"(status {s})")

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
