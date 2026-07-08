"""Probe the Template Studio backend against the RUNNING backend (port 8000).

Covers: list contract (default = active only), create draft/active + 422 guards,
status lenses + q/type filters, PATCH lifecycle (activate/archive + legacy
is_active alias), versioning-lite (content edit -> revision snapshot + version
bump; no-op -> no bump), clone (-> draft copy), apply (usage stamp + archived 409),
GET by id (revisions), /stats aggregate reconciliation, ticket provenance
(template_id stamped; bogus id dropped), soft delete + 404, and the audit trail
(support.ticket_template.{created,updated,cloned,applied,deleted}).

Creates disposable templates + one ticket and cleans up at the end. ASCII-only.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_template_studio.py
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
STAMP = os.urandom(3).hex().upper()


def req(method, path, token, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
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


db = SessionLocal()
su = db.execute(text(
    "SELECT id, email, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1"
)).fetchone()
print(f"superuser: {su[1] if su else None}")
if not su:
    print("Need a superuser - abort")
    sys.exit(1)
su_tok = create_access_token({"sub": str(su[0]), "tv": su[2] or 1})

tpl_id = clone_id = ticket_id = None
NAME = f"[PROBE] Copperplate {STAMP}"

try:
    # ───────────── baseline + create guards ─────────────
    print("-- create + guards --")
    s, base_list = req("GET", "/support-desk/ticket-templates/", su_tok)
    check("list default 200", s == 200 and isinstance(base_list, list), f"(status {s})")
    check("list default = active only", all(t.get("status", "active") == "active" for t in base_list))

    s, _ = req("POST", "/support-desk/ticket-templates/", su_tok, {"name": NAME, "status": "archived"})
    check("create status=archived -> 422", s == 422, f"(status {s})")
    s, _ = req("POST", "/support-desk/ticket-templates/", su_tok, {"name": NAME, "priority": "chaos"})
    check("create bad priority -> 422", s == 422, f"(status {s})")
    s, _ = req("POST", "/support-desk/ticket-templates/", su_tok, {"name": NAME, "ticket_type": "chaos"})
    check("create bad ticket_type -> 422", s == 422, f"(status {s})")

    s, j = req("POST", "/support-desk/ticket-templates/", su_tok, {
        "name": NAME, "description": "probe template", "status": "draft",
        "priority": "high", "ticket_type": "incident",
        "subject": "VPN down for {{requester.name}}",
        "body": "User {{requester.email}} reports VPN failure.",
        "tags": ["vpn", "network"], "checklist": [{"text": "Ping gateway", "done": False}],
        "icon": "Stamp", "accent": "#cf7f45", "pinned": True, "sort_order": 5,
    })
    tpl_id = j.get("id") if isinstance(j, dict) else None
    check("create draft 201", s == 201 and tpl_id, f"(status {s})")
    check("draft: is_active mirrors False", isinstance(j, dict) and j.get("status") == "draft" and j.get("is_active") is False)
    check("create: version=1 usage=0", isinstance(j, dict) and j.get("version") == 1 and j.get("usage_count") == 0)
    if not tpl_id:
        raise SystemExit(1)

    # ───────────── list lenses ─────────────
    print("-- list lenses --")
    s, j = req("GET", "/support-desk/ticket-templates/", su_tok)
    check("default list hides draft", s == 200 and all(t["id"] != tpl_id for t in j))
    s, j = req("GET", "/support-desk/ticket-templates/?status=draft", su_tok)
    check("?status=draft shows it", s == 200 and any(t["id"] == tpl_id for t in j))
    s, j = req("GET", f"/support-desk/ticket-templates/?status=all&q=Copperplate+{STAMP}", su_tok)
    check("?q name search", s == 200 and len(j) == 1 and j[0]["id"] == tpl_id, f"(got {len(j) if isinstance(j, list) else s})")
    s, j = req("GET", "/support-desk/ticket-templates/?status=all&ticket_type=incident", su_tok)
    check("?ticket_type filter", s == 200 and any(t["id"] == tpl_id for t in j))
    s, _ = req("GET", "/support-desk/ticket-templates/?status=chaos", su_tok)
    check("?status=chaos -> 422", s == 422, f"(status {s})")

    # ───────────── lifecycle + versioning ─────────────
    print("-- lifecycle + versioning --")
    s, j = req("PATCH", f"/support-desk/ticket-templates/{tpl_id}", su_tok, {"status": "active"})
    check("activate", s == 200 and j.get("status") == "active" and j.get("is_active") is True, f"(status {s})")
    check("status-only change: version stays 1", j.get("version") == 1)

    s, j = req("PATCH", f"/support-desk/ticket-templates/{tpl_id}", su_tok, {"subject": "VPN outage for {{requester.name}}"})
    check("content edit bumps version to 2", s == 200 and j.get("version") == 2, f"(got v{j.get('version') if isinstance(j, dict) else s})")
    s, j = req("GET", f"/support-desk/ticket-templates/{tpl_id}", su_tok)
    revs = j.get("revisions") if isinstance(j, dict) else None
    check("revision snapshot holds PREVIOUS subject",
          s == 200 and isinstance(revs, list) and len(revs) == 1 and revs[0].get("subject") == "VPN down for {{requester.name}}")

    s, j = req("PATCH", f"/support-desk/ticket-templates/{tpl_id}", su_tok, {"subject": "VPN outage for {{requester.name}}"})
    check("no-op PATCH: no bump", s == 200 and j.get("version") == 2)

    s, j = req("PATCH", f"/support-desk/ticket-templates/{tpl_id}", su_tok, {"is_active": False})
    check("legacy is_active=false -> archived", s == 200 and j.get("status") == "archived")

    # ───────────── apply ─────────────
    print("-- apply --")
    s, _ = req("POST", f"/support-desk/ticket-templates/{tpl_id}/apply", su_tok)
    check("apply archived -> 409", s == 409, f"(status {s})")
    req("PATCH", f"/support-desk/ticket-templates/{tpl_id}", su_tok, {"status": "active"})
    s, j = req("POST", f"/support-desk/ticket-templates/{tpl_id}/apply", su_tok)
    check("apply 200 + payload", s == 200 and j.get("template_id") == tpl_id and j.get("subject"), f"(status {s})")
    check("apply counts usage=1", j.get("usage_count") == 1)
    s, j = req("GET", f"/support-desk/ticket-templates/{tpl_id}", su_tok)
    check("last_used_at stamped", s == 200 and j.get("last_used_at"))

    # ───────────── clone ─────────────
    print("-- clone --")
    s, j = req("POST", f"/support-desk/ticket-templates/{tpl_id}/clone", su_tok)
    clone_id = j.get("id") if isinstance(j, dict) else None
    check("clone 201 draft copy", s == 201 and clone_id and j.get("status") == "draft"
          and j.get("name") == f"Copy of {NAME}" and j.get("usage_count") == 0 and j.get("pinned") is False, f"(status {s})")

    # ───────────── stats ─────────────
    print("-- stats --")
    s, st = req("GET", "/support-desk/ticket-templates/stats", su_tok)
    check("stats 200", s == 200 and isinstance(st, dict), f"(status {s})")
    if isinstance(st, dict):
        check("stats totals reconcile", st.get("total") == st.get("active", 0) + st.get("draft", 0) + st.get("archived", 0))
        check("stats usage_total >= 1", st.get("usage_total", 0) >= 1)
        check("stats top_used has probe", any(c.get("id") == tpl_id for c in st.get("top_used", [])))

    # ───────────── ticket provenance ─────────────
    print("-- ticket provenance --")
    s, j = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": f"[PROBE] born from template {STAMP}", "description": "probe",
        "ticket_type": "incident", "priority": "high", "template_id": tpl_id,
    })
    ticket_id = j.get("id") if isinstance(j, dict) else None
    check("ticket create w/ template_id 201", s == 201 and ticket_id, f"(status {s})")
    row = db.execute(text("SELECT template_id FROM support_tickets WHERE id = :i"), {"i": ticket_id}).fetchone()
    check("ticket carries template_id", row and str(row[0]) == str(tpl_id), f"(got {row[0] if row else None})")

    s, j2 = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": f"[PROBE] bogus template {STAMP}", "description": "probe",
        "ticket_type": "incident", "priority": "low",
        "template_id": "00000000-0000-0000-0000-000000000001",
    })
    check("bogus template_id silently dropped", s == 201 and isinstance(j2, dict), f"(status {s})")
    if isinstance(j2, dict) and j2.get("id"):
        row = db.execute(text("SELECT template_id FROM support_tickets WHERE id = :i"), {"i": j2["id"]}).fetchone()
        check("bogus id -> NULL stamp", row and row[0] is None)
        db.execute(text("DELETE FROM support_ticket_activities WHERE ticket_id = :i"), {"i": j2["id"]})
        db.execute(text("DELETE FROM support_tickets WHERE id = :i"), {"i": j2["id"]})
        db.commit()

    s, st2 = req("GET", "/support-desk/ticket-templates/stats", su_tok)
    check("stats counts templated ticket", s == 200 and st2.get("tickets_from_templates", 0) >= 1
          and st2.get("tickets_from_templates_30d", 0) >= 1)

    # ───────────── delete + audit trail ─────────────
    print("-- delete + audit --")
    s, _ = req("DELETE", f"/support-desk/ticket-templates/{tpl_id}", su_tok)
    check("delete 204", s == 204, f"(status {s})")
    s, _ = req("GET", f"/support-desk/ticket-templates/{tpl_id}", su_tok)
    check("deleted -> 404", s == 404, f"(status {s})")

    rows = db.execute(text(
        "SELECT action FROM audit_logs WHERE entity_id = :i ORDER BY created_at"
    ), {"i": tpl_id}).fetchall()
    acts = [r[0] for r in rows]
    for op in ("created", "updated", "applied", "deleted"):
        check(f"audit support.ticket_template.{op}", f"support.ticket_template.{op}" in acts, f"(have {len(acts)} rows)")
    rows = db.execute(text(
        "SELECT action FROM audit_logs WHERE entity_id = :i"
    ), {"i": clone_id}).fetchall() if clone_id else []
    check("audit support.ticket_template.cloned", any(r[0] == "support.ticket_template.cloned" for r in rows))

finally:
    # ───────────── cleanup ─────────────
    print("-- cleanup --")
    try:
        if ticket_id:
            db.execute(text("DELETE FROM support_ticket_activities WHERE ticket_id = :i"), {"i": ticket_id})
            db.execute(text("DELETE FROM support_tickets WHERE id = :i"), {"i": ticket_id})
        # Usage events + favorites FK the templates (agent Template Desk) — clear them first.
        db.execute(text(
            "DELETE FROM support_template_usage_events WHERE template_id IN "
            "(SELECT id FROM support_ticket_templates WHERE name LIKE :n)"), {"n": f"%{STAMP}%"})
        db.execute(text(
            "DELETE FROM support_template_favorites WHERE template_id IN "
            "(SELECT id FROM support_ticket_templates WHERE name LIKE :n)"), {"n": f"%{STAMP}%"})
        db.execute(text("DELETE FROM support_ticket_templates WHERE name LIKE :n"), {"n": f"%{STAMP}%"})
        db.commit()
        print("  cleaned probe rows")
    except Exception as e:
        db.rollback()
        print(f"  cleanup issue: {e}")
    db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
