"""Probe the Cadence Console (status-update) hardening against the RUNNING backend.

Exercises: post w/ cadence arms the clock + phase recorded / stand-down of an ARMED
cadence w/o note = 422 drop-gate / stand-down WITH note = 200 + clocks cleared /
stand-down when UNARMED stays free / bogus phase 422 / tiny body 422 / activity rows
carry preview+phase+note+prev_interval / public update stamps first reply / merged 409 /
commander gets the stand-down ping. Creates disposable probe tickets and archives them.
ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_cadence_console.py
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
ag_id = str(ag[0]) if ag else None
db.close()

made = []


def create(subject, ttype="incident", priority="high"):
    s, j = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": subject, "description": "probe - safe to ignore",
        "priority": priority, "ticket_type": ttype, "source": "internal",
    })
    tid = j.get("id") if isinstance(j, dict) else None
    if tid:
        made.append(tid)
    return s, tid


UPD = "/support-desk/tickets/{}/status-update"

print("-- 1. posting arms the cadence + tags the phase --")
s, inc_id = create("probe: cadence console fault")
check("create probe incident", s in (200, 201) and inc_id, f"(status {s})")
s, j = req("POST", UPD.format(inc_id), su_tok,
           {"body": "probe: investigating the fault", "is_internal": True,
            "interval_minutes": 30, "phase": "investigating"})
check("post w/ cadence 30 -> 200", s == 200, f"(status {s})")
check("cadence armed in response", (j or {}).get("update_interval_minutes") == 30
      and bool((j or {}).get("next_update_due_at")))

print("-- 2. stand-down drop-gate --")
s, j = req("POST", UPD.format(inc_id), su_tok,
           {"body": "probe: standing down", "is_internal": True, "stop_cadence": True})
check("stand-down of ARMED cadence w/o note -> 422", s == 422
      and "reason" in str((j or {}).get("detail", "")).lower(), f"(status {s})")
s, j = req("POST", UPD.format(inc_id), su_tok,
           {"body": "probe: standing down", "is_internal": True, "stop_cadence": True,
            "note": "probe: resolved via monitoring"})
check("stand-down WITH note -> 200", s == 200, f"(status {s})")
check("cadence cleared", s == 200 and (j or {}).get("update_interval_minutes") is None
      and (j or {}).get("next_update_due_at") is None)
s, j = req("POST", UPD.format(inc_id), su_tok,
           {"body": "probe: no-op stop while unarmed", "is_internal": True, "stop_cadence": True})
check("stand-down while UNARMED stays free -> 200", s == 200, f"(status {s})")

print("-- 3. schema hardening --")
s, j = req("POST", UPD.format(inc_id), su_tok,
           {"body": "probe: bogus phase", "is_internal": True, "phase": "panicking"})
check("bogus phase -> 422", s == 422, f"(status {s})")
s, j = req("POST", UPD.format(inc_id), su_tok, {"body": "x", "is_internal": True})
check("1-char body -> 422", s == 422, f"(status {s})")

print("-- 4. the comms log carries preview/phase/note --")
s, rows = req("GET", f"/support-desk/tickets/{inc_id}/activities", su_tok)
upd_rows = [r for r in (rows or []) if r.get("action") == "status_update"]
check("status_update activity rows recorded", s == 200 and len(upd_rows) >= 3, f"({len(upd_rows)} rows)")
with_preview = [r for r in upd_rows if (r.get("detail") or {}).get("preview")]
check("rows carry the body preview", len(with_preview) == len(upd_rows), f"({len(with_preview)}/{len(upd_rows)})")
phased = [r for r in upd_rows if (r.get("detail") or {}).get("phase") == "investigating"]
check("phase recorded on the tagged update", len(phased) >= 1, f"({len(phased)})")
noted = [r for r in upd_rows if (r.get("detail") or {}).get("note") == "probe: resolved via monitoring"]
check("stand-down note recorded", len(noted) >= 1 and noted[0]["detail"].get("stopped") is True,
      f"({len(noted)})")
retimed = [r for r in upd_rows if "prev_interval_min" in (r.get("detail") or {})]
check("interval transitions carry prev_interval_min", len(retimed) >= 1, f"({len(retimed)})")

print("-- 5. public update = first reply --")
s, j = req("POST", UPD.format(inc_id), su_tok,
           {"body": "probe: public stakeholder note", "is_internal": False})
check("public update -> 200 + first_responded_at stamped", s == 200
      and bool((j or {}).get("first_responded_at")), f"(status {s})")

print("-- 6. commander hears the stand-down --")
if ag_id:
    s, j = req("POST", UPD.format(inc_id), su_tok,
               {"body": "probe: re-arming for the ping test", "is_internal": True, "interval_minutes": 15})
    check("re-arm cadence 15", s == 200 and (j or {}).get("update_interval_minutes") == 15, f"(status {s})")
    s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
               {"incident_commander_id": ag_id, "clear": []})
    check("staff commander (fresh)", s == 200, f"(status {s})")
    s, j = req("POST", UPD.format(inc_id), su_tok,
               {"body": "probe: standing down again", "is_internal": True, "stop_cadence": True,
                "note": "probe: bridge call takes over"})
    check("stand-down with commander staffed -> 200", s == 200, f"(status {s})")
    db = SessionLocal()
    n = db.execute(text(
        "SELECT COUNT(*) FROM notifications WHERE user_id = :uid "
        "AND type = 'SUPPORT_INCIDENT_CADENCE_CHANGED' AND title LIKE :pat"
    ), {"uid": ag_id, "pat": "%stood down%"}).fetchone()[0]
    db.close()
    check("commander got the stand-down ping", n >= 1, f"({n} notification(s))")
else:
    print("  [SKIP] no agent to staff as commander")

print("-- 7. merged guard --")
s, dup_id = create("probe: cadence merged dup")
if dup_id:
    s, _ = req("POST", f"/support-desk/tickets/{dup_id}/merge", su_tok, {"target_id": inc_id})
    check("merge dup into master", s == 200, f"(status {s})")
    s, j = req("POST", UPD.format(dup_id), su_tok, {"body": "probe: update on merged", "is_internal": True})
    check("status-update on merged -> 409", s == 409, f"(status {s})")

print("-- cleanup --")
for tid in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{tid}?reason=probe%20cleanup", su_tok)
    check(f"archive {tid[:8]}", s in (204, 409, 404), f"(status {s})")

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
