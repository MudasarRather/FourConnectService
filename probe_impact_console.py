"""Probe the Blast Radius (incident-impact) hardening against the RUNNING backend.

Exercises: fresh stamp free / no-op 422 / revision-without-note 422 (drop-gate) /
revision-with-note 200 + note echo / clock leapfrog vs EXISTING values 422 (the closed
loophole) / future clock 422 / negative users 422 / bogus business_impact 422 / activity
rows carry before-after diffs + note / commander exposure ping. Creates disposable probe
tickets and archives them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_impact_console.py
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

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


def iso(dt) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
ag_id = str(ag[0]) if ag else None
db.close()

made = []
now = datetime.now(timezone.utc)


def create(subject, ttype="incident", priority="high"):
    s, j = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": subject, "description": "probe - safe to ignore",
        "priority": priority, "ticket_type": ttype, "source": "internal",
    })
    tid = j.get("id") if isinstance(j, dict) else None
    if tid:
        made.append(tid)
    return s, tid


IMP = "/support-desk/tickets/{}/incident-impact"

print("-- 1. fresh stamp is free --")
s, inc_id = create("probe: blast radius fault")
check("create probe incident", s in (200, 201) and inc_id, f"(status {s})")
s, j = req("PATCH", IMP.format(inc_id), su_tok, {
    "affected_services": ["probe-svc", "probe-db"], "affected_users": 40,
    "incident_started_at": iso(now - timedelta(hours=2)),
    "incident_detected_at": iso(now - timedelta(hours=1)),
})
check("first stamp w/o note -> 200", s == 200, f"(status {s}, detail {j})")
check("fields echo only changed", s == 200 and set(j.get("fields") or []) ==
      {"affected_services", "affected_users", "incident_started_at", "incident_detected_at"},
      f"({j.get('fields') if j else '?'})")

print("-- 2. no-op + revision drop-gate --")
s, j = req("PATCH", IMP.format(inc_id), su_tok, {
    "affected_services": ["probe-svc", "probe-db"], "affected_users": 40,
    "incident_started_at": iso(now - timedelta(hours=2)),
    "incident_detected_at": iso(now - timedelta(hours=1)),
})
check("identical re-stamp -> 422 (no-op)", s == 422, f"(status {s})")
s, j = req("PATCH", IMP.format(inc_id), su_tok, {"affected_users": 55})
check("revision w/o note -> 422", s == 422
      and "reason" in str((j or {}).get("detail", "")).lower(), f"(status {s})")
s, j = req("PATCH", IMP.format(inc_id), su_tok,
           {"affected_users": 55, "note": "probe: better telemetry"})
check("revision WITH note -> 200", s == 200, f"(status {s}, detail {j})")
check("note echoed", (j or {}).get("note") == "probe: better telemetry")

print("-- 3. clock discipline --")
# detected alone, earlier than the EXISTING started clock — the closed loophole
s, j = req("PATCH", IMP.format(inc_id), su_tok,
           {"incident_detected_at": iso(now - timedelta(hours=3)), "note": "probe: clock"})
check("detected < existing started -> 422", s == 422
      and "started" in str((j or {}).get("detail", "")).lower(), f"(status {s})")
s, j = req("PATCH", IMP.format(inc_id), su_tok,
           {"incident_started_at": iso(now + timedelta(hours=2)), "note": "probe: clock"})
check("future start clock -> 422", s == 422
      and "future" in str((j or {}).get("detail", "")).lower(), f"(status {s})")

print("-- 4. schema hardening --")
s, j = req("PATCH", IMP.format(inc_id), su_tok, {"affected_users": -5})
check("negative users -> 422", s == 422, f"(status {s})")
s, j = req("PATCH", IMP.format(inc_id), su_tok, {"business_impact": "apocalyptic"})
check("bogus business_impact -> 422", s == 422, f"(status {s})")
s, j = req("PATCH", IMP.format(inc_id), su_tok, {"business_impact": "high"})
check("fresh business_impact 'high' -> 200 (no note needed)", s == 200, f"(status {s}, detail {j})")

print("-- 5. the assessment log carries diffs + notes --")
s, rows = req("GET", f"/support-desk/tickets/{inc_id}/activities", su_tok)
imp_rows = [r for r in (rows or []) if r.get("action") == "incident_impact_set"]
check("impact activity rows recorded", s == 200 and len(imp_rows) >= 3, f"({len(imp_rows)} rows)")
with_changes = [r for r in imp_rows if isinstance(r.get("detail", {}).get("changes"), dict)]
check("rows carry before-after changes", len(with_changes) == len(imp_rows), f"({len(with_changes)}/{len(imp_rows)})")
noted = [r for r in imp_rows if r.get("detail", {}).get("note") == "probe: better telemetry"]
diff_ok = False
for r in noted:
    ch = r["detail"]["changes"].get("affected_users") or {}
    diff_ok = str(ch.get("from")) == "40" and str(ch.get("to")) == "55"
check("revision diff is 40 -> 55 with the note", diff_ok, f"(noted rows {len(noted)})")

print("-- 6. exposure declaration pings the commander --")
if ag_id:
    s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
               {"incident_commander_id": ag_id, "clear": []})
    check("staff commander (fresh)", s == 200, f"(status {s})")
    s, j = req("PATCH", IMP.format(inc_id), su_tok, {"security_impact": True})
    check("declare security exposure -> 200 (fresh flag, free)", s == 200, f"(status {s})")
    db = SessionLocal()
    n = db.execute(text(
        "SELECT COUNT(*) FROM notifications WHERE user_id = :uid "
        "AND type = 'SUPPORT_INCIDENT_IMPACT_STAMPED' AND title LIKE :pat"
    ), {"uid": ag_id, "pat": "%Exposure declared%"}).fetchone()[0]
    db.close()
    check("commander got the exposure ping", n >= 1, f"({n} notification(s))")
else:
    print("  [SKIP] no agent to staff as commander")

print("-- 7. non-incident guard still holds --")
s, sr_id = create("probe: blast radius non-incident", ttype="service_request")
s, j = req("PATCH", IMP.format(sr_id), su_tok, {"affected_users": 1})
check("impact on non-incident -> 422", s == 422, f"(status {s})")

print("-- cleanup --")
for tid in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{tid}?reason=probe%20cleanup", su_tok)
    check(f"archive {tid[:8]}", s in (204, 409, 404), f"(status {s})")

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
