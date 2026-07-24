"""Probe the Response Roster console backend against the RUNNING backend (port 8000).

Exercises: GET /tickets/{id}/roster-candidates (shape, user info, holders, history,
non-incident 422, q typeahead) and the hardened PATCH /tickets/{id}/incident-roles
(fresh staff w/o note OK, replace/stand-down w/o note = 422 handoff gate, note lands
in activity history + response, bogus user 400, empty patch 422). Creates disposable
probe tickets and archives them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_roster_console.py
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
ag_id = str(ag[0]) if ag else None
ag_tok = mint(ag) if ag else None
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


print("-- 1. roster-candidates read --")
s, inc_id = create("probe: roster console fault")
check("create probe incident", s in (200, 201) and inc_id, f"(status {s})")
s, j = req("GET", f"/support-desk/tickets/{inc_id}/roster-candidates", su_tok)
check("GET roster-candidates 200", s == 200 and j and j.get("ok") is True, f"(status {s})")
cands = (j or {}).get("candidates") or []
check("candidates non-empty (caller merged in)", len(cands) >= 1, f"({len(cands)} people)")
need = {"id", "name", "email", "department", "designation", "is_agent", "is_lead",
        "on_team", "is_you", "is_assignee", "command_load"}
check("candidate rows carry full user info", bool(cands) and need.issubset(set(cands[0].keys())),
      f"(keys {sorted(set(cands[0].keys()) & need) if cands else '?'})")
holders = (j or {}).get("holders") or {}
check("holders has all 3 seats (empty)", set(holders.keys()) ==
      {"incident_commander_id", "comms_lead_id", "ops_lead_id"}
      and all(v is None for v in holders.values()), f"({holders})")
check("history list present", isinstance((j or {}).get("history"), list))

s, sr_id = create("probe: roster non-incident", ttype="service_request")
check("create probe service_request", s in (200, 201) and sr_id, f"(status {s})")
s, j = req("GET", f"/support-desk/tickets/{sr_id}/roster-candidates", su_tok)
check("non-incident -> 422", s == 422, f"(status {s})")

s, j = req("GET", f"/support-desk/tickets/{inc_id}/roster-candidates?q=zz--noone--zz", su_tok)
check("q with no hits still 200 (pool kept)", s == 200 and j.get("ok") is True, f"(status {s})")

print("-- 2. fresh staffing needs no note --")
s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
           {"incident_commander_id": su_id, "clear": []})
check("fresh commander w/o note -> 200", s == 200 and j and j.get("incident_commander_id") == su_id,
      f"(status {s})")
check("response carries commander name", bool((j or {}).get("incident_commander_name")))

print("-- 3. handoff drop-gate --")
other = ag_id or su_id
if ag_id:
    s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
               {"incident_commander_id": ag_id, "clear": []})
    check("replace seated commander w/o note -> 422", s == 422
          and "handoff" in str((j or {}).get("detail", "")).lower(), f"(status {s})")
    s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
               {"incident_commander_id": ag_id, "clear": [], "note": "probe: shift change"})
    check("replace WITH note -> 200", s == 200 and (j or {}).get("incident_commander_id") == ag_id,
          f"(status {s})")
    check("note echoed in response", (j or {}).get("note") == "probe: shift change")
else:
    print("  [SKIP] no non-superuser agent; replace tests via stand-down only")

s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
           {"clear": ["incident_commander_id"]})
check("stand-down w/o note -> 422", s == 422, f"(status {s})")
s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
           {"clear": ["incident_commander_id"], "note": "probe: stand-down"})
check("stand-down WITH note -> 200", s == 200 and (j or {}).get("incident_commander_id") is None,
      f"(status {s})")

print("-- 4. chain history carries the notes --")
s, j = req("GET", f"/support-desk/tickets/{inc_id}/roster-candidates", su_tok)
hist = (j or {}).get("history") or []
check("history recorded the moves", s == 200 and len(hist) >= 2, f"({len(hist)} moves)")
notes = [h.get("note") for h in hist if h.get("note")]
check("handoff notes present in history", "probe: stand-down" in notes, f"(notes {notes})")
check("history changes exclude the note key",
      all("note" not in (h.get("changes") or {}) for h in hist))
check("commander seat empty again",
      ((j or {}).get("holders") or {}).get("incident_commander_id") is None)

print("-- 5. guards --")
s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok, {"clear": []})
check("empty patch -> 422", s == 422, f"(status {s})")
s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
           {"ops_lead_id": "00000000-0000-0000-0000-000000000001", "clear": []})
check("bogus user -> 400", s == 400, f"(status {s})")
s, j = req("PATCH", f"/support-desk/tickets/{inc_id}/incident-roles", su_tok,
           {"clear": ["not_a_role"]})
check("unknown clear field -> 422", s == 422, f"(status {s})")

print("-- 6. seal sanity (agent) --")
if ag_tok:
    s, j = req("GET", f"/support-desk/tickets/{inc_id}/roster-candidates", ag_tok)
    check("agent read sealed (200 in-scope or 404 outside; never 500)",
          s in (200, 404), f"(status {s})")
else:
    print("  [SKIP] no agent for seal probe")

print("-- cleanup --")
for tid in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{tid}?reason=probe%20cleanup", su_tok)
    check(f"archive {tid[:8]}", s in (204, 409, 404), f"(status {s})")

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
