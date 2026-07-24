"""Probe the Coupling Bay enhancement against the RUNNING backend (port 8000).

Exercises the incident-parent PATCH's new structured `note` field end to end:
link with note -> `incident_linked` (child) + `child_incident_linked` (parent) both
carry it; unlink with note -> `incident_unlinked` carries it; note omitted -> null
(back-compat); oversize note 422; the standing guards still hold (self 422,
child-as-master 422, master-with-children 422, terminal master 409).
Creates disposable probe tickets and archives them at the end. ASCII-only output.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_coupling_bay.py
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


def acts(tid, token, action):
    s, j = req("GET", f"/support-desk/tickets/{tid}/activities", token)
    return [a for a in (j or []) if a.get("action") == action] if s == 200 else []


db = SessionLocal()
su = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
print(f"superuser: {su[1] if su else None}")
if not su:
    print("No superuser found - abort"); sys.exit(1)
su_tok = create_access_token({"sub": str(su[0]), "tv": su[2] or 1})
su_id = str(su[0])
db.close()

made = []


def create(subject):
    s, j = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": subject, "description": "probe - safe to ignore",
        "priority": "high", "ticket_type": "incident", "source": "internal",
    })
    if s not in (200, 201) or not j or not j.get("id"):
        print(f"ticket create failed (status {s}) - abort"); sys.exit(1)
    made.append(j["id"])
    return j["id"]


print("-- setup: master + two children --")
master = create("probe: coupling master")
child_a = create("probe: coupling child A")
child_b = create("probe: coupling child B")
check("three probe incidents created", len(made) == 3)

print("-- link with structured note --")
s, j = req("PATCH", f"/support-desk/tickets/{child_a}/incident-parent", su_tok,
           {"parent_id": master, "note": "Same root cause suspected"})
check("link+note 200", s == 200 and j and j.get("parent_incident_id"), f"(status {s})")
rows = acts(child_a, su_tok, "incident_linked")
check("child row carries note", bool(rows) and rows[-1]["detail"].get("note") == "Same root cause suspected")
rows = acts(master, su_tok, "child_incident_linked")
check("master row carries note", bool(rows) and rows[-1]["detail"].get("note") == "Same root cause suspected")

print("-- back-compat: note omitted --")
s, j = req("PATCH", f"/support-desk/tickets/{child_b}/incident-parent", su_tok,
           {"parent_id": master})
check("link sans note 200", s == 200, f"(status {s})")
rows = acts(child_b, su_tok, "incident_linked")
check("note is null when omitted", bool(rows) and rows[-1]["detail"].get("note") is None)

print("-- validation guards still hold --")
s, _ = req("PATCH", f"/support-desk/tickets/{child_a}/incident-parent", su_tok,
           {"parent_id": master, "note": "x" * 301})
check("oversize note 422", s == 422, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{master}/incident-parent", su_tok,
           {"parent_id": master})
check("self-link 422", s == 422, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{master}/incident-parent", su_tok,
           {"parent_id": child_a})
check("master-under-its-child blocked", s == 422, f"(status {s})")

print("-- unlink with note --")
s, j = req("PATCH", f"/support-desk/tickets/{child_b}/incident-parent", su_tok,
           {"clear": True, "note": "Linked in error"})
check("unlink+note 200", s == 200 and j and j.get("parent_incident_id") is None, f"(status {s})")
rows = acts(child_b, su_tok, "incident_unlinked")
check("unlink row carries note", bool(rows) and rows[-1]["detail"].get("note") == "Linked in error")
s, _ = req("PATCH", f"/support-desk/tickets/{child_b}/incident-parent", su_tok, {"clear": True})
check("double-unlink 422", s == 422, f"(status {s})")

print("-- children endpoint reflects the rollup --")
s, j = req("GET", f"/support-desk/incidents/{master}/children", su_tok)
kid_ids = {str(i["id"]) for i in (j or {}).get("items", [])}
check("children lists child A only", s == 200 and kid_ids == {str(child_a)},
      f"(status {s}, kids {len(kid_ids)})")

print("-- terminal master 409 --")
s, _ = req("POST", f"/support-desk/tickets/{master}/assign", su_tok, {"assigned_agent_id": su_id})
check("assign master owner 200", s == 200, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{child_a}/incident-parent", su_tok, {"clear": True})
check("release child A 200", s == 200, f"(status {s})")
s, _ = req("POST", f"/support-desk/tickets/{master}/resolve", su_tok, {
    "resolution_code": "solved", "resolution_summary": "probe resolution - safe to ignore",
})
check("resolve master 200", s == 200, f"(status {s})")
s, _ = req("PATCH", f"/support-desk/tickets/{child_a}/incident-parent", su_tok,
           {"parent_id": master})
check("link to terminal master 409", s == 409, f"(status {s})")

print("-- cleanup --")
for t in made:
    s, _ = req("DELETE", f"/support-desk/tickets/{t}?reason=probe%20cleanup", su_tok)
    check(f"archive {t[:8]}", s in (204, 409, 404), f"(status {s})")

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
