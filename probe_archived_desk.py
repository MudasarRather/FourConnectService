"""Probe the new Archived desk (Deep Storage) workflow against the RUNNING backend (8000).

Mints JWTs directly (superuser + a non-superuser support agent), then exercises the
whole archive lifecycle:
  * archive with a coded reason (DELETE ?reason_code=) stamps archived_at/by/reason;
    422 on a bogus code and on the sweep-only 'auto_retention';
  * archived records stay READABLE (GET detail + activities) but 404 on mutation;
  * list scope=archived + archive_reason_code / legal_hold filters + archived_at sort;
  * restore clears the stamps, snapshots the prior values on the 'restored' activity,
    409s when not archived, 409s for a non-superuser while legal-held;
  * legal hold: any agent places, only a superuser releases (403), held records are
    exempt from purge;
  * purge: 403 non-superuser, 409 not-eligible, 409 legal-held, 409 merge-master;
    backdated record purges 204 with children gone + an audit tombstone;
  * auto-archive sweep: a CLOSED record backdated past SUPPORT_CLOSED_AUTOARCHIVE_DAYS
    lands on the shelf with reason auto_retention + System actor, and closed_stats'
    widened lifetime base still counts it;
  * GET /me/tickets/archived/stats shape (12-month trend, cohorts, governance counters,
    retention policy echo) + team seal (agent <= superuser);
  * bulk restore mixed batch + bulk legal_hold place/release guards;
  * CSV export with the new archive columns.
Creates disposable tickets and tombstones them (uncounted test_ticket) at the end.
ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_archived_desk.py
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
TAG = "[PROBE-ARC]"


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

print("\n== 1. Archive with a coded reason (provenance stamps) ==")
t1, s = mk(su_tok, f"{TAG} archive round-trip")
check("create t1 201/200", s in (200, 201) and t1, f"({s})")
s, _ = req("DELETE", f"/support-desk/tickets/{t1}?reason=probe%20dupe&reason_code=duplicate", su_tok)
check("archive t1 204", s == 204, f"({s})")
s, j = req("GET", f"/support-desk/tickets/{t1}", su_tok)
check("archived detail READABLE 200", s == 200, f"({s})")
j = j if isinstance(j, dict) else {}
check("is_deleted true", j.get("is_deleted") is True)
check("archived_at stamped", bool(j.get("archived_at")))
check("archived_by_id = superuser", str(j.get("archived_by_id")) == str(su[0]), f"({j.get('archived_by_id')})")
check("archive_reason_code = duplicate", j.get("archive_reason_code") == "duplicate")
check("purge_eligible False (young)", j.get("purge_eligible") is False, f"({j.get('purge_eligible')})")
s, acts = req("GET", f"/support-desk/tickets/{t1}/activities", su_tok)
check("archived activities READABLE 200", s == 200, f"({s})")
arc_act = [a for a in (acts or []) if a.get("action") == "archived"]
check("'archived' activity carries reason_code", arc_act and arc_act[-1].get("detail", {}).get("reason_code") == "duplicate")
s, _ = req("PATCH", f"/support-desk/tickets/{t1}", su_tok, {"priority": "low"})
check("mutation on archived row 404", s == 404, f"({s})")

print("\n== 2. Bad reason codes ==")
t2, _ = mk(su_tok, f"{TAG} bad codes")
s, _ = req("DELETE", f"/support-desk/tickets/{t2}?reason_code=bogus", su_tok)
check("unknown reason_code 422", s == 422, f"({s})")
s, _ = req("DELETE", f"/support-desk/tickets/{t2}?reason_code=auto_retention", su_tok)
check("manual auto_retention 422", s == 422, f"({s})")
s, _ = req("DELETE", f"/support-desk/tickets/{t2}?reason_code=spam", su_tok)
check("archive t2 spam 204", s == 204, f"({s})")

print("\n== 3. Archived list + filters ==")
s, j = req("GET", "/support-desk/tickets/?scope=archived&limit=100&sort_by=archived_at&sort_dir=desc", su_tok)
check("scope=archived 200", s == 200, f"({s})")
check("t1+t2 on the shelf", {t1, t2} <= ids_of(j))
s, j = req("GET", "/support-desk/tickets/?scope=archived&archive_reason_code=duplicate&limit=100", su_tok)
check("reason filter finds t1 only", t1 in ids_of(j) and t2 not in ids_of(j))
s, j = req("GET", "/support-desk/tickets/?scope=archived&legal_hold=true&limit=100", su_tok)
check("legal_hold=true excludes fresh rows", t1 not in ids_of(j) and t2 not in ids_of(j))

print("\n== 4. Restore (stamps cleared, prior snapshotted) ==")
s, j = req("POST", f"/support-desk/tickets/{t1}/restore", su_tok, {"note": "probe restore"})
check("restore t1 200", s == 200, f"({s})")
j = j if isinstance(j, dict) else {}
check("is_deleted cleared", j.get("is_deleted") is False)
check("archived_at cleared", j.get("archived_at") is None)
check("archive_reason_code cleared", j.get("archive_reason_code") is None)
s, acts = req("GET", f"/support-desk/tickets/{t1}/activities", su_tok)
res_act = [a for a in (acts or []) if a.get("action") == "restored"]
check("'restored' activity snapshots prior reason", res_act
      and (res_act[-1].get("detail", {}).get("prior") or {}).get("archive_reason_code") == "duplicate")
s, _ = req("POST", f"/support-desk/tickets/{t1}/restore", su_tok, None)
check("restore again 409 (not archived)", s == 409, f"({s})")

print("\n== 5. Legal hold (place=agent, release=superuser) ==")
s, j = req("POST", f"/support-desk/tickets/{t2}/legal-hold", su_tok, {"hold": True, "note": "litigation"})
check("place hold 200", s == 200 and isinstance(j, dict) and j.get("legal_hold") is True, f"({s})")
check("held record: purge_eligible_at suspended", (j or {}).get("purge_eligible_at") is None)
if ag_tok:
    s, _ = req("POST", f"/support-desk/tickets/{t2}/legal-hold", ag_tok, {"hold": False})
    check("agent release 403", s == 403, f"({s})")
    s, _ = req("POST", f"/support-desk/tickets/{t2}/restore", ag_tok, None)
    check("agent restore of held record 409", s == 409, f"({s})")
else:
    print("  [SKIP] no non-superuser agent for the 403/409 checks")
s, j = req("POST", f"/support-desk/tickets/{t2}/legal-hold", su_tok, {"hold": False})
check("superuser release 200", s == 200 and (j or {}).get("legal_hold") is False, f"({s})")

print("\n== 6. Purge guards + destruction ==")
t3, _ = mk(su_tok, f"{TAG} purge target")
req("DELETE", f"/support-desk/tickets/{t3}?reason_code=test_ticket", su_tok)
if ag_tok:
    s, _ = req("DELETE", f"/support-desk/tickets/{t3}/purge", ag_tok)
    check("agent purge 403", s == 403, f"({s})")
s, _ = req("DELETE", f"/support-desk/tickets/{t3}/purge", su_tok)
check("young record purge 409 (retention running)", s == 409, f"({s})")
db.execute(text("UPDATE support_tickets SET archived_at = NOW() - INTERVAL '200 days' WHERE id = :i"), {"i": t3})
db.commit()
s, _ = req("DELETE", f"/support-desk/tickets/{t3}/purge?reason=probe%20end%20of%20life", su_tok)
check("eligible purge 204", s == 204, f"({s})")
s, _ = req("GET", f"/support-desk/tickets/{t3}", su_tok)
check("purged record gone 404", s == 404, f"({s})")
n_orphan = db.execute(text("SELECT count(*) FROM support_ticket_activities WHERE ticket_id = :i"), {"i": t3}).scalar()
check("activities cascaded away", (n_orphan or 0) == 0, f"({n_orphan})")
n_tomb = db.execute(text("SELECT count(*) FROM audit_logs WHERE action LIKE '%purged%' AND entity_id = :i"), {"i": t3}).scalar()
check("audit tombstone survives", (n_tomb or 0) >= 1, f"({n_tomb})")

t4, _ = mk(su_tok, f"{TAG} held forever")
req("DELETE", f"/support-desk/tickets/{t4}?reason_code=compliance", su_tok)
req("POST", f"/support-desk/tickets/{t4}/legal-hold", su_tok, {"hold": True})
db.execute(text("UPDATE support_tickets SET archived_at = NOW() - INTERVAL '200 days' WHERE id = :i"), {"i": t4})
db.commit()
s, _ = req("DELETE", f"/support-desk/tickets/{t4}/purge", su_tok)
check("held record purge 409", s == 409, f"({s})")

t5, _ = mk(su_tok, f"{TAG} merge master")
t6, _ = mk(su_tok, f"{TAG} merge dup")
s, _ = req("POST", f"/support-desk/tickets/{t6}/merge", su_tok, {"target_id": t5})
check("merge dup->master 200", s == 200, f"({s})")
req("DELETE", f"/support-desk/tickets/{t5}?reason_code=obsolete", su_tok)
db.execute(text("UPDATE support_tickets SET archived_at = NOW() - INTERVAL '200 days' WHERE id = :i"), {"i": t5})
db.commit()
s, j = req("DELETE", f"/support-desk/tickets/{t5}/purge", su_tok)
check("merge-master purge 409", s == 409, f"({(j or {}).get('detail', s)})")

print("\n== 7. Auto-archive sweep (closed -> deep storage) ==")
t7, _ = mk(su_tok, f"{TAG} old closed record")
req("POST", f"/support-desk/tickets/{t7}/assign", su_tok, {"assigned_agent_id": str(su[0])})
s, _ = req("POST", f"/support-desk/tickets/{t7}/resolve", su_tok, {
    "resolution_code": "solved", "resolution_summary": "probe fix - safe to ignore", "close": True})
check("t7 resolved+closed", s == 200, f"({s})")
s, j0 = req("GET", "/support-desk/me/tickets/closed/stats", su_tok)
before_total = (j0 or {}).get("closed_total") or 0
db.execute(text("UPDATE support_tickets SET closed_at = NOW() - INTERVAL '130 days' WHERE id = :i"), {"i": t7})
db.commit()
s, j = req("GET", "/support-desk/tickets/?scope=archived&limit=100", su_tok)   # triggers the sweep
check("archived list 200 (sweep ran)", s == 200, f"({s})")
check("t7 swept onto the shelf", t7 in ids_of(j))
s, j = req("GET", f"/support-desk/tickets/{t7}", su_tok)
j = j if isinstance(j, dict) else {}
check("t7 reason = auto_retention", j.get("archive_reason_code") == "auto_retention")
check("t7 archived_by NULL (System)", j.get("archived_by_id") is None)
s, j1 = req("GET", "/support-desk/me/tickets/closed/stats", su_tok)
after_total = (j1 or {}).get("closed_total") or 0
check("closed_stats lifetime NOT drained by the sweep", after_total >= before_total,
      f"({before_total} -> {after_total})")

print("\n== 8. /me/tickets/archived/stats shape ==")
s, j = req("GET", "/support-desk/me/tickets/archived/stats", su_tok)
check("archived stats 200", s == 200, f"({s})")
j = j if isinstance(j, dict) else {}
check("total_archived >= 3", (j.get("total_archived") or 0) >= 3, f"({j.get('total_archived')})")
check("by_reason_code has spam + auto_retention",
      "spam" in (j.get("by_reason_code") or {}) and "auto_retention" in (j.get("by_reason_code") or {}),
      f"({sorted((j.get('by_reason_code') or {}).keys())})")
check("by_status_at_archive non-empty", bool(j.get("by_status_at_archive")))
cohort_sum = sum(int(v or 0) for v in (j.get("age_cohorts") or {}).values())
check("age cohorts sum = total", cohort_sum == (j.get("total_archived") or 0),
      f"({cohort_sum} vs {j.get('total_archived')})")
check("retention policy echoed (180/120)", j.get("retention_days") == 180 and j.get("autoarchive_days") == 120,
      f"({j.get('retention_days')}/{j.get('autoarchive_days')})")
check("12-month trend", isinstance(j.get("trend"), list) and len(j["trend"]) == 12, f"({len(j.get('trend') or [])})")
check("restored_30d >= 1 (t1)", (j.get("restored_30d") or 0) >= 1, f"({j.get('restored_30d')})")
check("legal_hold_count >= 1 (t4)", (j.get("legal_hold_count") or 0) >= 1, f"({j.get('legal_hold_count')})")
check("purge_eligible_count >= 1 (t5 backdated)", (j.get("purge_eligible_count") or 0) >= 1,
      f"({j.get('purge_eligible_count')})")
check("top_archivers list", isinstance(j.get("top_archivers"), list))

print("\n== 9. Team seal (agent <= superuser) ==")
if ag_tok:
    s, aj = req("GET", "/support-desk/me/tickets/archived/stats", ag_tok)
    check("agent stats 200", s == 200, f"({s})")
    aj = aj if isinstance(aj, dict) else {}
    check("agent total_archived <= superuser", (aj.get("total_archived") or 0) <= (j.get("total_archived") or 0),
          f"({aj.get('total_archived')} <= {j.get('total_archived')})")
    s, lj = req("GET", "/support-desk/tickets/?scope=archived&limit=100", ag_tok)
    check("agent archived list 200 (sealed)", s == 200, f"({s})")
else:
    print("  [SKIP] no non-superuser agent available")

print("\n== 10. Bulk restore + bulk legal_hold ==")
t8, _ = mk(su_tok, f"{TAG} bulk restore me")
req("DELETE", f"/support-desk/tickets/{t8}?reason_code=created_in_error", su_tok)
t9, _ = mk(su_tok, f"{TAG} bulk live decoy")
s, j = req("POST", "/support-desk/tickets/bulk", su_tok, {"ids": [t8, t9], "action": "restore"})
check("bulk restore 200", s == 200, f"({s})")
j = j if isinstance(j, dict) else {}
check("bulk restore: 1 updated, 1 skipped", j.get("updated") == 1 and j.get("skipped") == 1,
      f"({j.get('updated')}/{j.get('skipped')})")
req("DELETE", f"/support-desk/tickets/{t8}?reason_code=obsolete", su_tok)
s, j = req("POST", "/support-desk/tickets/bulk", su_tok, {"ids": [t8], "action": "legal_hold", "hold": True})
check("bulk hold place 200 (1 updated)", s == 200 and (j or {}).get("updated") == 1, f"({s})")
if ag_tok:
    s, _ = req("POST", "/support-desk/tickets/bulk", ag_tok, {"ids": [t8], "action": "legal_hold", "hold": False})
    check("bulk hold release by agent 403", s == 403, f"({s})")
s, j = req("POST", "/support-desk/tickets/bulk", su_tok, {"ids": [t8], "action": "legal_hold", "hold": False})
check("bulk hold release by superuser 200", s == 200 and (j or {}).get("updated") == 1, f"({s})")

print("\n== 11. CSV export with the archive columns ==")
s, body = req_raw("GET", "/support-desk/tickets/export?scope=archived", su_tok)
check("export scope=archived 200", s == 200, f"({s})")
head = (body.splitlines() or [""])[0]
check("export has Archived at/by/reason/Legal hold columns",
      "Archived at" in head and "Archived by" in head and "Archive reason" in head and "Legal hold" in head)
check("export rows carry a reason code", "spam" in body or "auto_retention" in body or "obsolete" in body)

# -- teardown: tombstone everything the probe made as uncounted young test rows --
db.execute(text(
    f"UPDATE support_tickets SET is_deleted = TRUE, legal_hold = FALSE, "
    f"archive_reason_code = 'test_ticket', archived_at = NOW() "
    f"WHERE subject LIKE '{TAG}%'"))
db.commit()
db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
