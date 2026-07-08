"""Support Desk — scoping + owner-tier actor-gate verification probe.

Verifies the ServiceNow/Zendesk-style access model end-to-end against the LIVE backend:
  • user-portal personal desks: list + stats honour mine=1 (assigned-to-me only)
  • team seal on single-ticket reads/mutations (cross-team fetch-by-id → 404)
  • owner-tier actor gate: a teammate can read + comment but NOT escalate / move /
    resolve / reassign / hand off a colleague's assigned ticket (403); the assignee,
    the team LEAD and a superuser can
  • claim of an unassigned team ticket still works; bulk skips out-of-tier rows
  • capabilities ships lead_team_ids

Run:  python C:\\Projects\\FourConnectService\\test_support_scope_guards.py
(needs the backend running on 127.0.0.1:8000 with the NEW code)
"""
import sys, time, uuid

# WMI-hang workaround (same as run_server.py) — must run before sqlalchemy imports.
import platform
ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
ur.__dict__["processor"] = "Intel"
platform._uname_cache = ur
platform._Processor.get = staticmethod(lambda: "Intel")

BACKEND = r"C:\Projects\FourConnectService"
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
import os
os.chdir(BACKEND)  # so pydantic-settings finds .env → REMOTE db (same one uvicorn uses)

import requests
import app.main  # noqa: F401 — pulls the FULL model registry so relationship mappers configure
from app.database import SessionLocal
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketComment, SdTicketActivity
from app.models.support_desk.workspace import SdTeam, SdTicketViewer
from app.utils.auth import get_password_hash

BASE = "http://127.0.0.1:8000/api"
SD = f"{BASE}/support-desk"
PW = "Probe#12345"
MARK = "@probe-sg.com"

passed, failed = [], []
def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))

def cleanup(db):
    uids = [u.id for u in db.query(User).filter(User.email.like(f"%{MARK}")).all()]
    tids = [t.id for t in db.query(SdTicket).filter(SdTicket.subject.like("PROBE-SG %")).all()]
    if tids:
        db.query(SdTicketViewer).filter(SdTicketViewer.ticket_id.in_(tids)).delete(synchronize_session=False)
        db.query(SdTicketComment).filter(SdTicketComment.ticket_id.in_(tids)).delete(synchronize_session=False)
        db.query(SdTicketActivity).filter(SdTicketActivity.ticket_id.in_(tids)).delete(synchronize_session=False)
        db.query(SdTicket).filter(SdTicket.id.in_(tids)).delete(synchronize_session=False)
    db.query(SdTeam).filter(SdTeam.name.like("Probe SG %")).delete(synchronize_session=False)
    db.commit()
    if uids:
        # defensive: audit/notification rows may FK the users — try delete, else deactivate
        from sqlalchemy import text
        for table, col in (("sd_audit_logs", "actor_id"), ("hr_notifications", "user_id"),
                           ("notifications", "user_id")):
            try:
                db.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(:ids)"), {"ids": uids})
                db.commit()
            except Exception:
                db.rollback()
        try:
            db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
            db.query(User).filter(User.id.in_(uids)).update({"is_active": False}, synchronize_session=False)
            db.commit()

def mk_user(db, email, name, su=False, agent=False):
    # Idempotent: audit-trail FKs can keep a prior run's user row alive (cleanup falls
    # back to deactivation) — reuse it and refresh the credentials/flags.
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(email=email, full_name=name, hashed_password=get_password_hash(PW))
        db.add(u)
    u.full_name = name
    u.hashed_password = get_password_hash(PW)
    u.is_active = True
    u.is_superuser = su
    u.is_activated = True
    u.is_support_agent = agent
    db.flush()
    return u

def login(email):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": PW}, timeout=30)
    assert r.status_code == 200, f"login {email} -> {r.status_code}: {r.text[:200]}"
    tok = r.json().get("access_token")
    return {"Authorization": f"Bearer {tok}"}

def mk_ticket(db, subject, team_id=None, assignee=None, status="open", priority="medium"):
    t = SdTicket(ticket_number=f"SG-{uuid.uuid4().hex[:8].upper()}",
                 subject=f"PROBE-SG {subject}", description="scope-guard probe",
                 ticket_type="incident", priority=priority, status=status,
                 team_id=team_id, assigned_agent_id=assignee)
    db.add(t); db.flush()
    return t

def main():
    db = SessionLocal()
    cleanup(db)  # idempotent re-run
    print("== fixtures ==")
    admin = mk_user(db, f"sg_admin{MARK}", "SG Admin", su=True)
    lead = mk_user(db, f"sg_lead{MARK}", "SG Lead", agent=True)
    a = mk_user(db, f"sg_agent_a{MARK}", "SG Agent A", agent=True)
    b = mk_user(db, f"sg_agent_b{MARK}", "SG Agent B", agent=True)
    c = mk_user(db, f"sg_agent_c{MARK}", "SG Agent C", agent=True)
    alpha = SdTeam(name="Probe SG Alpha", lead_user_id=lead.id,
                   member_ids=[str(a.id), str(b.id)], is_active=True)
    gamma = SdTeam(name="Probe SG Gamma", lead_user_id=c.id, member_ids=[str(c.id)], is_active=True)
    db.add_all([alpha, gamma]); db.flush()
    t1 = mk_ticket(db, "A working", team_id=alpha.id, assignee=a.id, status="in_progress")
    t2 = mk_ticket(db, "unowned alpha", team_id=alpha.id, status="open")
    t3 = mk_ticket(db, "gamma C", team_id=gamma.id, assignee=c.id, status="in_progress")
    t4 = mk_ticket(db, "B working", team_id=alpha.id, assignee=b.id, status="in_progress")
    db.commit()
    T1, T2, T3, T4 = str(t1.id), str(t2.id), str(t3.id), str(t4.id)
    _ids.update(a=a.id, b=b.id, c=c.id, lead=lead.id, alpha=alpha.id)
    db.close()

    H = {k: login(f"sg_{k}{MARK}") for k in ("admin", "lead", "agent_a", "agent_b", "agent_c")}
    A, B, C, L, SU = H["agent_a"], H["agent_b"], H["agent_c"], H["lead"], H["admin"]

    print("== 1. personal-desk mine=1 scoping ==")
    r = requests.get(f"{SD}/tickets/", params={"scope": "open", "mine": True, "limit": 100}, headers=A).json()
    ids = {x["id"] for x in r.get("items", [])}
    check("mine=1 list has A's ticket", T1 in ids)
    check("mine=1 list excludes teammate's + unowned", T4 not in ids and T2 not in ids)
    check("mine=1 list: every row assigned to A",
          all(str(x.get("assigned_agent_id")) == str(a_id()) for x in r.get("items", [])))
    r2 = requests.get(f"{SD}/tickets/", params={"scope": "open", "limit": 100}, headers=A).json()
    ids2 = {x["id"] for x in r2.get("items", [])}
    check("no-mine list stays team-wide (T1,T2,T4 visible)", {T1, T2, T4} <= ids2)
    check("team seal still hides other team (T3)", T3 not in ids2)
    r3 = requests.get(f"{SD}/tickets/", params={"scope": "open", "mine": True, "limit": 100}, headers=SU).json()
    check("mine=1 works for superuser too (no probe tickets assigned)",
          not ({T1, T2, T3, T4} & {x["id"] for x in r3.get("items", [])}))

    print("== 2. single-ticket team seal (fetch-by-id) ==")
    check("A GET other team's ticket -> 404",
          requests.get(f"{SD}/tickets/{T3}", headers=A).status_code == 404)
    check("A escalate other team's ticket -> 404",
          requests.post(f"{SD}/tickets/{T3}/escalate", json={"reason": "x"}, headers=A).status_code == 404)
    check("A comment on other team's ticket -> 404",
          requests.post(f"{SD}/tickets/{T3}/comments", json={"body": "x", "is_internal": True}, headers=A).status_code == 404)
    check("C (its assignee) still works his ticket",
          requests.get(f"{SD}/tickets/{T3}", headers=C).status_code == 200)

    print("== 3. owner-tier gate — teammate B on A's assigned ticket ==")
    check("B can READ teammate's ticket (team visibility)",
          requests.get(f"{SD}/tickets/{T1}", headers=B).status_code == 200)
    check("B can COMMENT (internal note)",
          requests.post(f"{SD}/tickets/{T1}/comments", json={"body": "sg note", "is_internal": True}, headers=B).status_code == 201)
    check("B status-move -> 403",
          requests.post(f"{SD}/tickets/{T1}/status", json={"status": "pending_customer"}, headers=B).status_code == 403)
    check("B escalate -> 403",
          requests.post(f"{SD}/tickets/{T1}/escalate", json={"reason": "grab"}, headers=B).status_code == 403)
    check("B resolve -> 403",
          requests.post(f"{SD}/tickets/{T1}/resolve",
                        json={"resolution_code": "solved", "resolution_summary": "not mine to close"},
                        headers=B).status_code == 403)
    check("B self-router resolve -> 403",
          requests.post(f"{SD}/me/tickets/{T1}/resolve",
                        json={"resolution_code": "solved", "resolution_summary": "not mine to close"},
                        headers=B).status_code == 403)
    check("B poach via /assign -> 403",
          requests.post(f"{SD}/tickets/{T1}/assign", json={"assigned_agent_id": str(b_id())}, headers=B).status_code == 403)
    check("B poach via /handoff -> 403",
          requests.post(f"{SD}/me/tickets/{T1}/handoff", json={"to_agent_id": str(b_id())}, headers=B).status_code == 403)
    check("B hold -> 403",
          requests.post(f"{SD}/tickets/{T1}/hold", json={"hold_reason": "grab"}, headers=B).status_code == 403)
    check("B archive -> 403",
          requests.delete(f"{SD}/tickets/{T1}", headers=B).status_code == 403)
    br = requests.post(f"{SD}/tickets/bulk",
                       json={"ids": [T1], "action": "resolve", "resolution_code": "solved",
                             "resolution_summary": "bulk grab"}, headers=B).json()
    res0 = (br.get("results") or [{}])[0]
    check("B bulk resolve -> skipped w/ owner reason", bool(res0.get("skipped")) and "another agent" in (res0.get("error") or ""))

    print("== 4. sanctioned paths still work ==")
    check("B claims the UNASSIGNED team ticket",
          requests.post(f"{SD}/me/tickets/{T2}/claim", json={}, headers=B).status_code == 200)
    check("A escalates OWN ticket",
          requests.post(f"{SD}/tickets/{T1}/escalate", json={"reason": "vendor blocked"}, headers=A).status_code == 200)
    check("A hands OWN ticket off to B",
          requests.post(f"{SD}/me/tickets/{T1}/handoff", json={"to_agent_id": str(b_id()), "reason_code": "workload_balance"}, headers=A).status_code == 200)
    # hand T1 back to A for the remaining checks (B is now its assignee → allowed)
    check("B (new assignee) hands it back",
          requests.post(f"{SD}/me/tickets/{T1}/handoff", json={"to_agent_id": str(a_id()), "reason_code": "workload_balance"}, headers=B).status_code == 200)

    print("== 5. lead + superuser tiers ==")
    caps = requests.get(f"{SD}/me/tickets/capabilities", headers=L).json()
    check("lead capabilities carry lead_team_ids", str(alpha_id()) in [str(x) for x in caps.get("lead_team_ids", [])])
    check("lead reassigns teammate's ticket (T4 -> A)",
          requests.post(f"{SD}/tickets/{T4}/assign", json={"assigned_agent_id": str(a_id())}, headers=L).status_code == 200)
    check("lead status-moves A's ticket",
          requests.post(f"{SD}/tickets/{T1}/status", json={"status": "pending_customer"}, headers=L).status_code == 200)
    check("superuser assigns anywhere (T3 stays C)",
          requests.post(f"{SD}/tickets/{T3}/assign", json={"assigned_agent_id": str(c_id())}, headers=SU).status_code == 200)

    print("== 6. desk stats mine=1 ==")
    sa = requests.get(f"{SD}/me/tickets/escalated/stats", params={"mine": True}, headers=A).json()
    sb = requests.get(f"{SD}/me/tickets/escalated/stats", params={"mine": True}, headers=B).json()
    a_esc = sa.get("active_escalations", 0)
    b_esc = sb.get("active_escalations", 0)
    check("A's mine escalated-stats counts his escalation", (a_esc or 0) >= 1, f"a={a_esc}")
    check("B's mine escalated-stats is 0", (b_esc or 0) == 0, f"b={b_esc}")
    cc = requests.get(f"{SD}/me/tickets/command-center/stats", params={"mine": True}, headers=B).json()
    check("command-center stats accepts mine", isinstance(cc, dict))

    print("== 7. self-router visibility sealed for cross-team agent ==")
    check("C GET /me/tickets/{T1} -> 404",
          requests.get(f"{SD}/me/tickets/{T1}", headers=C).status_code == 404)
    check("C self-resolve T1 -> 404",
          requests.post(f"{SD}/me/tickets/{T1}/resolve",
                        json={"resolution_code": "solved", "resolution_summary": "cross-team resolve attempt"},
                        headers=C).status_code == 404)

    print("== 8. assignee closes the loop ==")
    check("A resolves OWN ticket",
          requests.post(f"{SD}/tickets/{T1}/resolve",
                        json={"resolution_code": "solved", "resolution_summary": "probe fix applied"},
                        headers=A).status_code == 200)

    print(f"\n===== {len(passed)} passed / {len(failed)} failed =====")
    if failed:
        print("FAILED:", *failed, sep="\n  - ")

    db2 = SessionLocal()
    cleanup(db2)
    db2.close()
    print("fixtures cleaned up")
    return 0 if not failed else 1

# tiny id memos (populated inside main after fixture creation)
_ids = {}
def a_id(): return _ids["a"]
def b_id(): return _ids["b"]
def c_id(): return _ids["c"]
def alpha_id(): return _ids["alpha"]

if __name__ == "__main__":
    raise SystemExit(main())
