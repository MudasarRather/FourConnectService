"""Probe the agent Template Desk backend against the RUNNING backend (port 8000).

Covers the visibility seal (personal|team|global), agent-authored personal
templates (forced personal + admin-knob stripping), owner-only PATCH/DELETE,
apply gates (others' personal 404, global draft 409, own personal draft
test-drive), per-user favorites toggle + is_favorite stamping, clone-to-mine,
per-agent usage events + /stats my_* blocks, and the run-template MACRO on an
existing ticket (owner-tier gate, internal note vs public reply, first_responded_at,
apply_priority + merge_tags, usage kind='macro', activity + audit rows), plus
superuser regressions (studio contract unchanged).

Creates disposable rows and cleans up at the end. ASCII-only prints.

Run from the backend root so .env resolves:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_agent_templates.py
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
MARK = f"[PROBE-AGT {STAMP}]"


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
agents = db.execute(text(
    "SELECT id, email, token_version, COALESCE(is_support_agent, FALSE) FROM users "
    "WHERE is_superuser = FALSE AND is_active = TRUE ORDER BY created_at LIMIT 2"
)).fetchall()
print(f"superuser: {su[1] if su else None}")
print(f"agents: {[a[1] for a in agents]}")
if not su or len(agents) < 2:
    print("Need a superuser + two non-superuser users - abort")
    sys.exit(1)

A, B = agents[0], agents[1]
_flag_restore = {str(a[0]): bool(a[3]) for a in (A, B)}
db.execute(text("UPDATE users SET is_support_agent = TRUE WHERE id IN (:a, :b)"),
           {"a": str(A[0]), "b": str(B[0])})
db.commit()

su_tok = create_access_token({"sub": str(su[0]), "tv": su[2] or 1})
a_tok = create_access_token({"sub": str(A[0]), "tv": A[2] or 1})
b_tok = create_access_token({"sub": str(B[0]), "tv": B[2] or 1})

g_active_id = g_draft_id = p_id = p_draft_id = a_clone_id = su_clone_id = ticket_id = None
TPL = "/support-desk/ticket-templates"

try:
    # ───────────── superuser fixtures + studio regression ─────────────
    print("-- superuser fixtures (studio contract) --")
    s, j = req("POST", f"{TPL}/", su_tok, {
        "name": f"{MARK} Global Active", "status": "active", "priority": "high",
        "ticket_type": "incident", "subject": "VPN outage - {{requester.name}}",
        "body": "Standard VPN outage response for {{requester.email}}.",
        "tags": ["vpn", "network"], "pinned": True, "sort_order": 3, "icon": "Stamp",
    })
    g_active_id = j.get("id") if isinstance(j, dict) else None
    check("su create global active 201", s == 201 and g_active_id, f"(status {s})")
    check("su create: visibility defaults 'global'", isinstance(j, dict) and j.get("visibility") == "global")
    check("su create: pinned honored", isinstance(j, dict) and j.get("pinned") is True)

    s, j = req("POST", f"{TPL}/", su_tok, {"name": f"{MARK} Global Draft", "status": "draft"})
    g_draft_id = j.get("id") if isinstance(j, dict) else None
    check("su create global draft 201", s == 201 and g_draft_id, f"(status {s})")

    s, _ = req("POST", f"{TPL}/", su_tok, {"name": f"{MARK} BadVis", "visibility": "cosmic"})
    check("su create bad visibility -> 422", s == 422, f"(status {s})")
    s, _ = req("POST", f"{TPL}/", su_tok, {"name": f"{MARK} TeamNoTeam", "visibility": "team"})
    check("su create team visibility w/o team_id -> 422", s == 422, f"(status {s})")

    # ───────────── agent authorship: forced personal ─────────────
    print("-- agent authorship --")
    s, j = req("POST", f"{TPL}/", a_tok, {
        "name": f"{MARK} A Personal", "status": "active", "priority": "high",
        "ticket_type": "service_request", "subject": "Access request - {{requester.name}}",
        "body": "Grant checklist for {{requester.email}}.",
        "tags": ["access"], "checklist": [{"text": "Verify manager approval", "done": False}],
        # deliberate privilege grabs — must be stripped, not 422'd:
        "visibility": "global", "pinned": True, "sort_order": 99,
    })
    p_id = j.get("id") if isinstance(j, dict) else None
    check("agent create 201", s == 201 and p_id, f"(status {s})")
    check("agent create: FORCED personal", isinstance(j, dict) and j.get("visibility") == "personal")
    check("agent create: pinned stripped", isinstance(j, dict) and j.get("pinned") is False)
    check("agent create: sort_order stripped", isinstance(j, dict) and j.get("sort_order") == 0)

    s, j = req("POST", f"{TPL}/", a_tok, {"name": f"{MARK} A Draft", "status": "draft", "subject": "wip"})
    p_draft_id = j.get("id") if isinstance(j, dict) else None
    check("agent create own draft 201", s == 201 and p_draft_id, f"(status {s})")

    # ───────────── visibility seal ─────────────
    print("-- visibility seal --")
    s, j = req("GET", f"{TPL}/?status=all", a_tok)
    ids_a = {t["id"] for t in j} if isinstance(j, list) else set()
    check("A list sees own personal + globals", s == 200 and p_id in ids_a and g_active_id in ids_a)
    s, j = req("GET", f"{TPL}/?status=all", b_tok)
    ids_b = {t["id"] for t in j} if isinstance(j, list) else set()
    check("B list EXCLUDES A's personal", s == 200 and p_id not in ids_b and g_active_id in ids_b)
    s, _ = req("GET", f"{TPL}/{p_id}", b_tok)
    check("B GET A's personal -> 404", s == 404, f"(status {s})")
    s, j = req("GET", f"{TPL}/{p_id}", a_tok)
    check("A GET own personal 200", s == 200 and isinstance(j, dict) and j.get("id") == p_id)
    s, j = req("GET", f"{TPL}/{p_id}", su_tok)
    check("su GET agent personal 200 (admin oversight)", s == 200)

    # ───────────── owner-only mutation ─────────────
    print("-- owner-only mutation --")
    s, _ = req("PATCH", f"{TPL}/{p_id}", b_tok, {"name": "hijack"})
    check("B PATCH A's personal -> 404", s == 404, f"(status {s})")
    s, _ = req("PATCH", f"{TPL}/{g_active_id}", a_tok, {"name": "hijack global"})
    check("A PATCH global -> 403", s == 403, f"(status {s})")
    s, j = req("PATCH", f"{TPL}/{p_id}", a_tok, {"body": "Grant checklist v2 for {{requester.email}}."})
    check("A PATCH own personal 200", s == 200)
    check("content edit -> version bump", isinstance(j, dict) and j.get("version") == 2)
    s, j = req("GET", f"{TPL}/{p_id}", a_tok)
    check("revision snapshot recorded", s == 200 and len(j.get("revisions") or []) == 1)
    s, j = req("PATCH", f"{TPL}/{p_id}", a_tok, {"pinned": True, "visibility": "global"})
    check("A PATCH admin knobs stripped (no-op 200, still personal/unpinned)",
          s == 200 and j.get("pinned") is False and j.get("visibility") == "personal")
    s, _ = req("DELETE", f"{TPL}/{p_draft_id}", b_tok)
    check("B DELETE A's draft -> 404", s == 404, f"(status {s})")
    s, _ = req("DELETE", f"{TPL}/{g_active_id}", a_tok)
    check("A DELETE global -> 403", s == 403, f"(status {s})")

    # ───────────── apply gates + usage events ─────────────
    print("-- apply gates --")
    s, _ = req("POST", f"{TPL}/{p_id}/apply", b_tok, {})
    check("B apply A's personal -> 404", s == 404, f"(status {s})")
    s, _ = req("POST", f"{TPL}/{g_draft_id}/apply", a_tok, {})
    check("A apply global draft -> 409", s == 409, f"(status {s})")
    s, j = req("POST", f"{TPL}/{p_draft_id}/apply", a_tok, {})
    check("A apply OWN personal draft -> 200 (test-drive)", s == 200, f"(status {s})")
    s, j = req("POST", f"{TPL}/{g_active_id}/apply", a_tok, {})
    check("A apply global active 200", s == 200 and isinstance(j, dict) and j.get("template_id") == g_active_id)

    # ───────────── favorites ─────────────
    print("-- favorites --")
    s, j = req("POST", f"{TPL}/{g_active_id}/favorite", a_tok)
    check("A favorite toggle -> true", s == 200 and isinstance(j, dict) and j.get("is_favorite") is True)
    s, j = req("GET", f"{TPL}/?status=all", a_tok)
    fav_map = {t["id"]: t.get("is_favorite") for t in j} if isinstance(j, list) else {}
    check("A list stamps is_favorite", fav_map.get(g_active_id) is True and fav_map.get(p_id) is False)
    s, j = req("GET", f"{TPL}/?status=all", b_tok)
    fav_b = {t["id"]: t.get("is_favorite") for t in j} if isinstance(j, list) else {}
    check("favorites are per-user (B unaffected)", fav_b.get(g_active_id) is False)
    s, j = req("POST", f"{TPL}/{g_active_id}/favorite", a_tok)
    check("A favorite toggle -> false", s == 200 and j.get("is_favorite") is False)
    s, j = req("POST", f"{TPL}/{g_active_id}/favorite", a_tok)
    check("A favorite re-toggle -> true", s == 200 and j.get("is_favorite") is True)

    # ───────────── clone-to-mine ─────────────
    print("-- clone --")
    s, j = req("POST", f"{TPL}/{g_active_id}/clone", a_tok, {})
    a_clone_id = j.get("id") if isinstance(j, dict) else None
    check("agent clone 201", s == 201 and a_clone_id, f"(status {s})")
    check("agent clone -> personal draft owned by A",
          isinstance(j, dict) and j.get("visibility") == "personal"
          and j.get("status") == "draft" and str(j.get("created_by_id")) == str(A[0]))
    s, j = req("POST", f"{TPL}/{g_active_id}/clone", su_tok, {})
    su_clone_id = j.get("id") if isinstance(j, dict) else None
    check("su clone keeps source visibility", s == 201 and isinstance(j, dict) and j.get("visibility") == "global")
    s, _ = req("POST", f"{TPL}/{p_id}/clone", b_tok, {})
    check("B clone A's personal -> 404", s == 404, f"(status {s})")

    # ───────────── MACRO: run template on an existing ticket ─────────────
    print("-- macro (run on ticket) --")
    s, j = req("POST", "/support-desk/tickets/", su_tok, {
        "subject": f"{MARK} macro target", "description": "probe",
        "ticket_type": "incident", "priority": "medium",
        "assigned_agent_id": str(A[0]),
    })
    ticket_id = j.get("id") if isinstance(j, dict) else None
    check("fixture ticket created + assigned to A", s in (200, 201) and ticket_id, f"(status {s})")
    if ticket_id:
        row = db.execute(text("SELECT assigned_agent_id FROM support_tickets WHERE id = :t"),
                         {"t": str(ticket_id)}).fetchone()
        if not row or str(row[0]) != str(A[0]):
            db.execute(text("UPDATE support_tickets SET assigned_agent_id = :a WHERE id = :t"),
                       {"a": str(A[0]), "t": str(ticket_id)})
            db.commit()
            print("  [note] assignee forced via DB (create path ignored assigned_agent_id)")

    ME = "/support-desk/me/tickets"
    s, _ = req("POST", f"{ME}/{ticket_id}/run-template/{p_id}", b_tok,
               {"mode": "internal_note", "body": "hijack note"})
    check("B (not owner-tier) macro -> 403/404", s in (403, 404), f"(status {s})")
    s, _ = req("POST", f"{ME}/{ticket_id}/run-template/{p_id}", a_tok, {"mode": "internal_note", "body": "   "})
    check("empty rendered body -> 422", s == 422, f"(status {s})")
    s, _ = req("POST", f"{ME}/{ticket_id}/run-template/{p_id}", a_tok, {"mode": "sideways", "body": "x"})
    check("bad mode -> 422", s == 422, f"(status {s})")

    s, j = req("POST", f"{ME}/{ticket_id}/run-template/{p_id}", a_tok, {
        "mode": "internal_note", "body": "Rendered: grant checklist for probe@x.dev",
        "apply_priority": True, "merge_tags": True,
    })
    check("A macro internal note 201", s == 201 and isinstance(j, dict), f"(status {s})")
    check("macro note is internal", isinstance(j, dict) and j.get("is_internal") is True)
    row = db.execute(text(
        "SELECT priority, tags, first_responded_at FROM support_tickets WHERE id = :t"
    ), {"t": str(ticket_id)}).fetchone()
    check("apply_priority adopted (medium -> high)", row and row[0] == "high", f"(got {row[0] if row else None})")
    tags_now = row[1] if row else []
    check("merge_tags unioned", isinstance(tags_now, list) and "access" in tags_now)
    check("internal note does NOT stamp first_responded_at", row and row[2] is None)

    s, j = req("POST", f"{ME}/{ticket_id}/run-template/{p_id}", a_tok, {"mode": "reply", "body": "Public rendered reply."})
    check("A macro public reply 201", s == 201 and isinstance(j, dict) and j.get("is_internal") is False)
    row = db.execute(text("SELECT first_responded_at FROM support_tickets WHERE id = :t"),
                     {"t": str(ticket_id)}).fetchone()
    check("public reply stamps first_responded_at", row and row[0] is not None)

    n_act = db.execute(text(
        "SELECT COUNT(*) FROM support_ticket_activities WHERE ticket_id = :t AND action = 'template_run'"
    ), {"t": str(ticket_id)}).scalar()
    check("activity rows action='template_run' == 2", n_act == 2, f"(got {n_act})")
    n_macro_ev = db.execute(text(
        "SELECT COUNT(*) FROM support_template_usage_events WHERE user_id = :u AND kind = 'macro'"
    ), {"u": str(A[0])}).scalar()
    check("usage events kind='macro' == 2", n_macro_ev == 2, f"(got {n_macro_ev})")
    n_audit = db.execute(text(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'support.ticket_template.macro_applied'"
    )).scalar()
    check("audit op macro_applied written", (n_audit or 0) >= 2, f"(got {n_audit})")

    # archived template refuses the macro
    s, _ = req("PATCH", f"{TPL}/{p_draft_id}", a_tok, {"status": "archived"})
    check("A archive own draft 200", s == 200, f"(status {s})")
    s, _ = req("POST", f"{ME}/{ticket_id}/run-template/{p_draft_id}", a_tok, {"mode": "internal_note", "body": "x"})
    check("macro on archived template -> 409", s == 409, f"(status {s})")

    # ───────────── stats: my_* blocks ─────────────
    print("-- stats --")
    s, j = req("GET", f"{TPL}/stats", a_tok)
    check("stats 200 + shared fields intact", s == 200 and isinstance(j, dict)
          and all(k in j for k in ("total", "active", "usage_total", "tickets_from_templates_30d", "top_used")))
    # A made: 2 applies + 2 macros = 4 events
    check("my_use_total counts A's events", (j.get("my_use_total") or 0) >= 4, f"(got {j.get('my_use_total')})")
    check("my_use_30d populated", (j.get("my_use_30d") or 0) >= 4, f"(got {j.get('my_use_30d')})")
    check("my_top_used populated", len(j.get("my_top_used") or []) >= 1)
    check("my_recent populated", len(j.get("my_recent") or []) >= 1)
    s, j = req("GET", f"{TPL}/stats", b_tok)
    check("B my_use_total == 0 (per-caller)", s == 200 and (j.get("my_use_total") or 0) == 0,
          f"(got {j.get('my_use_total') if isinstance(j, dict) else None})")

    # ───────────── su regression: legacy alias + delete ─────────────
    print("-- su regressions --")
    s, j = req("PATCH", f"{TPL}/{g_draft_id}", su_tok, {"is_active": False})
    check("legacy is_active alias -> archived", s == 200 and j.get("status") == "archived" and j.get("is_active") is False)
    s, _ = req("DELETE", f"{TPL}/{su_clone_id}", su_tok)
    check("su soft delete 204", s == 204, f"(status {s})")
    s, j = req("DELETE", f"{TPL}/{p_id}", a_tok)
    check("A delete own personal 204", s == 204, f"(status {s})")

finally:
    print("-- cleanup --")
    try:
        ids = [x for x in (g_active_id, g_draft_id, p_id, p_draft_id, a_clone_id, su_clone_id) if x]
        if ids:
            in_list = ",".join(f"'{i}'" for i in ids)
            db.execute(text(f"DELETE FROM support_template_usage_events WHERE template_id IN ({in_list})"))
            db.execute(text(f"DELETE FROM support_template_favorites WHERE template_id IN ({in_list})"))
            db.execute(text(f"DELETE FROM support_ticket_templates WHERE id IN ({in_list})"))
        if ticket_id:
            db.execute(text("DELETE FROM support_ticket_activities WHERE ticket_id = :t"), {"t": str(ticket_id)})
            db.execute(text("DELETE FROM support_ticket_comments WHERE ticket_id = :t"), {"t": str(ticket_id)})
            db.execute(text("DELETE FROM support_ticket_viewers WHERE ticket_id = :t"), {"t": str(ticket_id)})
            db.execute(text("DELETE FROM support_tickets WHERE id = :t"), {"t": str(ticket_id)})
        for uid, flag in _flag_restore.items():
            db.execute(text("UPDATE users SET is_support_agent = :f WHERE id = :u"), {"f": flag, "u": uid})
        db.commit()
        print("  cleanup done (probe rows removed, agent flags restored)")
    except Exception as e:
        db.rollback()
        print(f"  cleanup WARNING: {e}")
    db.close()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
