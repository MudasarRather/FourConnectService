"""HR Exit Management — shared DB helpers (self-employee resolution, number gen,
policy resolution, clearance default template, progress recompute, response
builders). Keeps the routers thin. Mirrors the Travel / Reimbursements service.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.system_setting import SystemSetting
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_clearance import ExitClearanceItem
from app.models.hr.exit_policy import ExitPolicy
from app.models.hr.exit_settlement import ExitSettlement
from app.models.hr.exit_type import (
    ExitCaseStatus, OPEN_CASE_STATUSES, ClearanceItemStatus, ResignationType,
    ClearanceDepartment,
)


# ─── self-employee resolution ───

def resolve_self_employee(db: Session, user: User) -> Employee:
    emp = db.query(Employee).filter(
        Employee.user_id == user.id, Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "Your account is not linked to an employee profile. Contact HR.")
    return emp


def try_self_employee(db: Session, user: User) -> Optional[Employee]:
    return db.query(Employee).filter(
        Employee.user_id == user.id, Employee.is_deleted == False,  # noqa: E712
    ).first()


# ─── reference number generators ───

def _next_counter(db: Session, key: str, prefix: str, model, col, desc: str) -> str:
    yy = str(date.today().year)[-2:]
    for _ in range(6):
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row:
            try:
                n = int(row.value) + 1
            except Exception:
                n = 1
            row.value = str(n)
        else:
            n = 1
            db.add(SystemSetting(key=key, value="1", description=desc))
        db.flush()
        candidate = f"{prefix}-{yy}-{n:06d}"
        exists = db.query(model.id).filter(col == candidate).first()
        if not exists:
            return candidate
    raise HTTPException(500, f"Could not allocate {prefix} number")


def generate_case_number(db: Session) -> str:
    return _next_counter(db, "exit_case_counter", "EX", ExitCase,
                         ExitCase.case_number, "Counter for ExitCase case_number")


def generate_settlement_number(db: Session) -> str:
    return _next_counter(db, "exit_settlement_counter", "FF", ExitSettlement,
                         ExitSettlement.settlement_number, "Counter for ExitSettlement number")


# ─── open-case guard ───

def open_case_for_employee(db: Session, employee_id: UUID) -> Optional[ExitCase]:
    return (
        db.query(ExitCase)
        .filter(
            ExitCase.employee_id == employee_id,
            ExitCase.is_deleted == False,  # noqa: E712
            ExitCase.status.in_(list(OPEN_CASE_STATUSES)),
        )
        .first()
    )


# ─── self-then-manager handover lanes ───
# The MANAGER (work / knowledge transfer) and PROJECT (client handover) lanes are
# the only clearance obligations the departing employee has ground truth on. They
# follow an employee-submits → reporting-manager-signs-off flow (HR keeps the
# override). Scope is derived from the department — no per-item template flag —
# so it's robust across policy-defined custom templates. sync_clearance_from_systems
# never auto-clears these lanes, so there is no conflict with the manual flow.
HANDOVER_DEPARTMENTS = {ClearanceDepartment.MANAGER, ClearanceDepartment.PROJECT}


def is_self_handover(item: "ExitClearanceItem") -> bool:
    """True when this clearance lane is employee-submitted + manager-signed-off."""
    return item.department in HANDOVER_DEPARTMENTS


# ─── former-employee document portal token ───

PORTAL_TOKEN_TTL_DAYS = 5   # link is live only this long AFTER documents are issued


def ensure_public_token(db: Session, case: ExitCase) -> str:
    """Idempotently mint the unguessable public-portal token for a case.

    Powers the no-auth document portal so a leaver whose ERP login was revoked
    during clearance can still download their letters. Minted at acceptance
    (bootstrap) and lazily backfilled here for older cases. Caller commits.
    """
    if not case.public_token:
        case.public_token = secrets.token_urlsafe(32)
        db.flush()
    return case.public_token


def start_portal_window(db: Session, case: ExitCase) -> datetime:
    """Open (or refresh) the security window: link live for TTL days from now.

    Called when a letter is ISSUED (the delivery moment) and on HR rotate. The
    countdown deliberately starts at issuance — NOT at acceptance — so the link
    can't expire before the documents it carries even exist. Caller commits.
    """
    ensure_public_token(db, case)
    case.public_token_expires_at = datetime.now(timezone.utc) + timedelta(days=PORTAL_TOKEN_TTL_DAYS)
    db.flush()
    return case.public_token_expires_at


def portal_token_valid(case: ExitCase) -> bool:
    """True if the case's portal link is currently usable.

    No expiry set (no letter issued yet) ⇒ valid (the link works but lists nothing).
    Once a window is set, the link dies when it elapses.
    """
    if not case.public_token:
        return False
    exp = case.public_token_expires_at
    if exp is None:
        return True
    if exp.tzinfo is None:                     # defensive: treat naive as UTC
        exp = exp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) <= exp


# ─── default clearance template (fallback when a policy has none) ───

DEFAULT_CLEARANCE_TEMPLATE: List[Dict[str, Any]] = [
    {"department": "MANAGER", "item_key": "mgr_handover", "title": "Work handover completed", "is_mandatory": True, "sort_order": 10},
    {"department": "MANAGER", "item_key": "mgr_knowledge_transfer", "title": "Knowledge transfer done", "is_mandatory": True, "sort_order": 20},
    {"department": "IT", "item_key": "it_email_revoke", "title": "Email / SSO disabled", "is_mandatory": True, "sort_order": 30},
    {"department": "IT", "item_key": "it_erp_login", "title": "ERP login / credentials revoked", "is_mandatory": True, "sort_order": 35},
    {"department": "IT", "item_key": "it_laptop_return", "title": "Laptop / hardware returned", "is_mandatory": True, "sort_order": 40},
    {"department": "IT", "item_key": "it_vpn_revoke", "title": "VPN access revoked", "is_mandatory": True, "sort_order": 50},
    {"department": "IT", "item_key": "it_repo_access", "title": "Code / repo access revoked", "is_mandatory": False, "sort_order": 60},
    {"department": "FINANCE", "item_key": "fin_loan_advance", "title": "Loans / advances cleared", "is_mandatory": True, "sort_order": 70},
    {"department": "FINANCE", "item_key": "fin_reimbursement", "title": "Reimbursements closed", "is_mandatory": True, "sort_order": 80},
    {"department": "ADMIN", "item_key": "adm_access_card", "title": "Access card returned", "is_mandatory": True, "sort_order": 90},
    {"department": "ADMIN", "item_key": "adm_locker", "title": "Locker / parking cleared", "is_mandatory": False, "sort_order": 100},
    {"department": "SECURITY", "item_key": "sec_premises", "title": "Premises access revoked", "is_mandatory": True, "sort_order": 110},
    {"department": "SECURITY", "item_key": "sec_biometric", "title": "Biometric de-registered", "is_mandatory": False, "sort_order": 120},
    {"department": "PROJECT", "item_key": "prj_client_handover", "title": "Client handover done", "is_mandatory": False, "sort_order": 130},
    {"department": "HR", "item_key": "hr_exit_interview", "title": "Exit interview completed", "is_mandatory": True, "sort_order": 140},
    {"department": "HR", "item_key": "hr_records", "title": "Employee records updated", "is_mandatory": True, "sort_order": 150},
    {"department": "HR", "item_key": "hr_ff_ack", "title": "Full & Final acknowledged", "is_mandatory": True, "sort_order": 160},
]

DEFAULT_INTERVIEW_QUESTIONS: List[Dict[str, Any]] = [
    {"key": "overall", "question": "Overall, how was your experience here?", "type": "rating"},
    {"key": "management", "question": "How would you rate your manager / leadership?", "type": "rating"},
    {"key": "culture", "question": "How would you rate the work environment & culture?", "type": "rating"},
    {"key": "growth", "question": "How would you rate your career growth opportunities?", "type": "rating"},
    {"key": "compensation", "question": "How would you rate compensation & benefits?", "type": "rating"},
    {"key": "primary_reason", "question": "What is the primary reason for your departure?", "type": "text"},
    {"key": "suggestions", "question": "What could we have done better?", "type": "text"},
]


# ─── policy resolution ───

def resolve_policy(db: Session, emp: Employee) -> Optional[ExitPolicy]:
    """Most-specific active policy: grade-specific → wildcard (grade_id NULL)."""
    q = db.query(ExitPolicy).filter(
        ExitPolicy.is_active == True, ExitPolicy.is_deleted == False,  # noqa: E712
    )
    if emp.grade_id:
        specific = q.filter(ExitPolicy.grade_id == emp.grade_id).first()
        if specific:
            return specific
    return q.filter(ExitPolicy.grade_id.is_(None)).order_by(ExitPolicy.created_at.asc()).first()


def resolved_notice_days(case: ExitCase, policy: Optional[ExitPolicy]) -> int:
    if case.notice_period_days is not None:
        return case.notice_period_days
    if policy:
        if case.resignation_type == ResignationType.PROBATION_EXIT:
            return policy.probation_notice_days
        return policy.notice_period_days
    return 30


# ─── clearance progress ───

def recompute_clearance_progress(db: Session, case: ExitCase) -> int:
    items = db.query(ExitClearanceItem).filter(ExitClearanceItem.exit_case_id == case.id).all()
    if not items:
        case.clearance_progress_pct = 0
        return 0
    done = sum(1 for i in items if i.status in (ClearanceItemStatus.CLEARED, ClearanceItemStatus.NA))
    pct = round(done * 100 / len(items))
    case.clearance_progress_pct = pct
    return pct


def all_mandatory_cleared(db: Session, case: ExitCase) -> bool:
    items = db.query(ExitClearanceItem).filter(
        ExitClearanceItem.exit_case_id == case.id,
        ExitClearanceItem.is_mandatory == True,  # noqa: E712
    ).all()
    if not items:
        return True
    return all(i.status in (ClearanceItemStatus.CLEARED, ClearanceItemStatus.NA) for i in items)


# ─── F&F settlement pre-verification gate ───────────────────────────────────
def settlement_preflight(db: Session, case: ExitCase) -> Dict[str, Any]:
    """Corporate pre-verification gate for the Full & Final settlement.

    A settlement may NOT be VERIFIED until three obligations are concluded:
      • Clearance is 100% complete   — every checklist item CLEARED or N/A,
      • Company assets are returned  — no allocation still outstanding (LOST/
        DAMAGED units are "resolved": they fold into the F&F recoveries),
      • The exit interview is done   — COMPLETED or explicitly SKIPPED by HR.

    Reuses ``_collect_clearance_facts`` so the asset / interview reads are
    IDENTICAL to the clearance view (single source of truth). Returns
    ``{ready, checks:[{key,label,ok,detail,tab}], blockers:[label,…]}`` —
    consumed by the verify handler (hard 409 gate) and the UI gate panel.
    """
    facts = _collect_clearance_facts(db, case)

    # 1) Clearance — 100% of the checklist (CLEARED or N/A)
    items = db.query(ExitClearanceItem).filter(ExitClearanceItem.exit_case_id == case.id).all()
    total = len(items)
    done = sum(1 for i in items if i.status in (ClearanceItemStatus.CLEARED, ClearanceItemStatus.NA))
    pct = round(done * 100 / total) if total else 100
    clearance_ok = (total == 0) or (done == total)
    clearance_detail = f"{done}/{total} items cleared · {pct}%" if total else "No clearance checklist"

    # 2) Assets — nothing still outstanding (LOST/DAMAGED count as resolved → F&F)
    asset = facts.get("asset")
    if asset is None:
        assets_ok, assets_detail = True, "Asset register unavailable"
    elif asset["total"] == 0:
        assets_ok, assets_detail = True, "No assets were allocated"
    else:
        outstanding = asset["outstanding"]
        assets_ok = outstanding == 0
        bits = []
        if outstanding:
            bits.append(f"{outstanding} still outstanding")
        if asset["returned"]:
            bits.append(f"{asset['returned']} returned")
        if asset["shortfall"]:
            bits.append(f"{asset['shortfall']} lost/damaged → F&F")
        assets_detail = " · ".join(bits) or f"{asset['total']} allocated"

    # 3) Exit interview — concluded (completed, or deliberately skipped by HR)
    iv = facts.get("interview")
    interview_ok = iv in ("COMPLETED", "SKIPPED")
    iv_label = {
        "COMPLETED": "Completed", "SKIPPED": "Skipped by HR",
        "SCHEDULED": "Scheduled — not yet conducted", "IN_PROGRESS": "In progress",
        "PENDING": "Not yet scheduled", "CANCELLED": "Cancelled",
    }
    interview_detail = iv_label.get(iv, "Not yet conducted")

    checks = [
        {"key": "clearance", "label": "Clearance 100% complete", "ok": clearance_ok,
         "detail": clearance_detail, "tab": "clearance"},
        {"key": "assets", "label": "Company assets returned", "ok": assets_ok,
         "detail": assets_detail, "tab": "asset-return"},
        {"key": "interview", "label": "Exit interview concluded", "ok": interview_ok,
         "detail": interview_detail, "tab": "interviews"},
    ]
    blockers = [c["label"] for c in checks if not c["ok"]]
    return {"ready": not blockers, "checks": checks, "blockers": blockers}


def backfill_clearance_items(db: Session, case: ExitCase) -> bool:
    """Ensure the case's checklist holds every item in its *effective* template
    (resolved policy template, else the built-in default). Idempotent — keyed on
    ``item_key`` — so template items added after a case was seeded (e.g. the IT
    ``ERP login / credentials revoked`` row) surface on existing in-flight cases
    without a re-seed. Returns True when anything was added (caller commits)."""
    policy = case.policy
    template: List[Dict[str, Any]] = (
        list(policy.clearance_template)
        if (policy and policy.clearance_template) else DEFAULT_CLEARANCE_TEMPLATE
    )
    if not template:
        return False
    existing = {(i.item_key or "").strip() for i in (case.clearance_items or [])}
    added = False
    for t in template:
        key = str(t.get("item_key", "")).strip()[:60]
        if not key or key in existing:
            continue
        try:
            dept = ClearanceDepartment[str(t.get("department"))]
        except Exception:
            continue
        db.add(ExitClearanceItem(
            exit_case_id=case.id,
            department=dept,
            item_key=key,
            title=str(t.get("title", "Clearance item"))[:200],
            description=t.get("description"),
            is_mandatory=bool(t.get("is_mandatory", True)),
            status=ClearanceItemStatus.PENDING,
            sort_order=int(t.get("sort_order", 0) or 0),
        ))
        existing.add(key)
        added = True
    if added:
        db.flush()
        recompute_clearance_progress(db, case)
    return added


# ─── cross-module auto-sync ─────────────────────────────────────────────────
# The no-dues checklist should REFLECT REALITY. Several obligations are already
# tracked authoritatively in other modules (Asset allocations, Account
# Provisioning, Reimbursements, Exit Interviews, Travel advances). We read those
# sources and (a) attach a live ``system_signal`` to each derivable item for the
# UI, and (b) AUTO-CLEAR items whose obligation is provably satisfied — but only
# the untouched ones, so human decisions (BLOCKED / N/A / reopened) are never
# overridden. Every external read is guarded so a missing module yields an
# "unknown" signal rather than crashing the clearance view.

# Clearance gates that are governed by an Account Provisioning ledger row.
# item_key → (AccountType value, human label). The ERP gate (it_erp_login) is
# handled separately because it ALSO disables the linked User sign-in; these
# others just flip their provisioning row REVOKED. Shared by the sync (live
# signal + auto-clear when REVOKED) and the /revoke-provisioning endpoint.
PROVISIONING_GATES: Dict[str, Tuple[str, str]] = {
    "it_email_revoke": ("EMAIL", "Email / SSO"),
    "it_vpn_revoke": ("VPN", "VPN access"),
    "it_repo_access": ("GIT", "Repo / code access"),
    "sec_biometric": ("BIOMETRIC", "Biometric enrolment"),
    "adm_access_card": ("RFID_SYSTEM", "Access card"),
}


def _collect_clearance_facts(db: Session, case: ExitCase) -> Dict[str, Any]:
    emp = case.employee
    # ERP account (login) — disabled ⇒ revoked
    try:
        user_active = bool(emp.user.is_active) if (emp and emp.user) else None
    except Exception:
        user_active = None
    # Company assets — AssetAllocation lifecycle is the source of truth
    asset = None
    try:
        from app.models.hr.asset import AssetAllocation, AllocationStatus
        rows = db.query(AssetAllocation).filter(AssetAllocation.employee_id == case.employee_id).all()
        asset = {
            "outstanding": sum(1 for r in rows if r.status == AllocationStatus.ALLOCATED),
            "returned": sum(1 for r in rows if r.status == AllocationStatus.RETURNED),
            "shortfall": sum(1 for r in rows if r.status in (AllocationStatus.LOST, AllocationStatus.DAMAGED)),
            "total": len(rows),
        }
    except Exception:
        asset = None
    # Reimbursement claims — anything still in-flight blocks the gate
    reimb = None
    try:
        from app.models.hr.claim import Claim
        from app.models.hr.reimbursement_type import ClaimStatus
        in_flight = {ClaimStatus.DRAFT, ClaimStatus.PENDING_APPROVAL, ClaimStatus.RETURNED}
        rows = db.query(Claim).filter(
            Claim.employee_id == case.employee_id, Claim.is_deleted == False,  # noqa: E712
        ).all()
        reimb = {
            "pending": sum(1 for r in rows if r.status in in_flight),
            "approved": sum(1 for r in rows if r.status == ClaimStatus.APPROVED),
            "total": len(rows),
        }
    except Exception:
        reimb = None
    # Exit interview status
    try:
        interview = case.interview.status.value if (case.interview and case.interview.status) else None
    except Exception:
        interview = None
    # Outstanding travel advances (signal only — other loans aren't tracked here)
    advance = None
    try:
        from app.models.hr.travel_advance import TravelAdvance
        from app.models.hr.travel_type import AdvanceStatus
        owed = {AdvanceStatus[s] for s in ("RELEASED", "SETTLED") if s in AdvanceStatus.__members__}
        rows = db.query(TravelAdvance).filter(
            TravelAdvance.employee_id == case.employee_id, TravelAdvance.is_deleted == False,  # noqa: E712
        ).all()
        n = 0
        amount = Decimal("0")
        for r in rows:
            if r.status not in owed:
                continue
            base = r.approved_amount if r.approved_amount is not None else r.advance_amount
            outstanding = _d(base) - _d(r.recovered_amount)
            if outstanding > 0:
                n += 1
                amount += outstanding
        advance = {"count": n, "amount": float(amount)}
    except Exception:
        advance = None
    # Account Provisioning ledger — one row per account-type per employee. Maps
    # account_type → status so IT/Security gates can read whether email/VPN/repo/
    # biometric/RFID is still provisioned (ACTIVE) or de-provisioned (REVOKED).
    prov = None
    try:
        from app.models.hr.account_provisioning import AccountProvisioning
        rows = db.query(AccountProvisioning).filter(
            AccountProvisioning.employee_id == case.employee_id,
        ).all()
        prov = {}
        for r in rows:
            at = r.account_type.value if hasattr(r.account_type, "value") else str(r.account_type)
            st = r.status.value if hasattr(r.status, "value") else str(r.status)
            prov[at] = st
    except Exception:
        prov = None
    return {"user_active": user_active, "asset": asset, "reimb": reimb,
            "interview": interview, "advance": advance, "prov": prov}


def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal("0")


def sync_clearance_from_systems(db: Session, case: ExitCase) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Attach live system signals + auto-clear provably-satisfied items.

    Returns ``(signals_by_item_key, auto_events)``. ``auto_events`` is a list of
    ``{id, note}`` for each item this call auto-cleared (the router writes the
    audit rows + commits). Idempotent: an item is only ever auto-cleared once,
    and a reopened item (carrying ``[Reopened]``) is left alone."""
    facts = _collect_clearance_facts(db, case)
    by_key: Dict[str, ExitClearanceItem] = {}
    for it in (case.clearance_items or []):
        by_key.setdefault(it.item_key, it)
    signals: Dict[str, Any] = {}
    auto_events: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    def auto_clear(item: Optional[ExitClearanceItem], evidence: str) -> None:
        if item is None or item.status != ClearanceItemStatus.PENDING:
            return
        if item.remarks and "[Reopened]" in item.remarks:
            return  # a human explicitly reopened it — respect that
        item.status = ClearanceItemStatus.CLEARED
        item.signed_off_at = now
        item.signed_off_by_id = None  # system attestation
        tag = f"[Auto] {evidence}"
        item.remarks = (f"{item.remarks.strip()}\n{tag}" if item.remarks and item.remarks.strip() else tag)
        auto_events.append({"id": item.id, "note": tag})

    # ── ERP login / credentials ──
    ua = facts["user_active"]
    if "it_erp_login" in by_key and ua is not None:
        if ua is False:
            signals["it_erp_login"] = {"source": "account", "state": "satisfied", "auto": True,
                                       "label": "ERP account disabled", "detail": "Login revoked in Account Provisioning"}
            auto_clear(by_key["it_erp_login"], "ERP account disabled (Account Provisioning)")
        else:
            signals["it_erp_login"] = {"source": "account", "state": "pending", "auto": True,
                                       "label": "ERP login still active", "detail": "Revoke in Account Provisioning"}

    # ── Hardware / asset return ──
    a = facts["asset"]
    if "it_laptop_return" in by_key and a is not None:
        if a["shortfall"] > 0:
            signals["it_laptop_return"] = {"source": "asset", "state": "attention", "auto": False,
                                           "label": f"{a['shortfall']} unit(s) lost / damaged",
                                           "detail": f"{a['returned']} returned · recovery applies in F&F"}
        elif a["outstanding"] == 0:
            signals["it_laptop_return"] = {"source": "asset", "state": "satisfied", "auto": True,
                                           "label": "All hardware returned" if a["total"] else "No hardware outstanding",
                                           "detail": f"{a['returned']} returned · 0 outstanding"}
            auto_clear(by_key["it_laptop_return"],
                       (f"{a['returned']} asset(s) returned, none outstanding (Asset module)"
                        if a["total"] else "No company hardware outstanding (Asset module)"))
        else:
            signals["it_laptop_return"] = {"source": "asset", "state": "pending", "auto": True,
                                           "label": f"{a['outstanding']} asset(s) still out",
                                           "detail": f"{a['returned']} returned · {a['outstanding']} outstanding"}

    # ── Reimbursements ──
    r = facts["reimb"]
    if "fin_reimbursement" in by_key and r is not None:
        if r["pending"] == 0:
            signals["fin_reimbursement"] = {"source": "reimbursement", "state": "satisfied", "auto": True,
                                            "label": "No claims pending",
                                            "detail": (f"{r['approved']} approved → fold into F&F" if r["approved"] else "Nothing in-flight")}
            auto_clear(by_key["fin_reimbursement"],
                       (f"No reimbursement claims pending; {r['approved']} approved fold into F&F"
                        if r["approved"] else "No reimbursement claims pending (Reimbursements module)"))
        else:
            signals["fin_reimbursement"] = {"source": "reimbursement", "state": "pending", "auto": True,
                                            "label": f"{r['pending']} claim(s) in-flight",
                                            "detail": "Awaiting approval / settlement"}

    # ── Exit interview ──
    istat = facts["interview"]
    if "hr_exit_interview" in by_key and istat is not None:
        if istat == "COMPLETED":
            signals["hr_exit_interview"] = {"source": "interview", "state": "satisfied", "auto": True,
                                            "label": "Exit interview completed", "detail": "Feedback captured"}
            auto_clear(by_key["hr_exit_interview"], "Exit interview completed (Exit Interviews)")
        else:
            signals["hr_exit_interview"] = {"source": "interview", "state": "pending", "auto": True,
                                            "label": f"Interview {istat.replace('_', ' ').title()}",
                                            "detail": "Not yet completed"}

    # ── Travel advances (signal only — finance confirms other loans manually) ──
    adv = facts["advance"]
    if "fin_loan_advance" in by_key and adv is not None:
        amt = adv.get("amount", 0) or 0
        amt_str = f"₹{amt:,.0f}"
        if adv["count"] == 0:
            signals["fin_loan_advance"] = {"source": "advance", "state": "satisfied", "auto": False,
                                           "label": "No travel advance outstanding", "detail": "Confirm other loans manually",
                                           "amount": 0, "count": 0}
        else:
            signals["fin_loan_advance"] = {"source": "advance", "state": "attention", "auto": False,
                                           "label": f"{amt_str} across {adv['count']} advance(s)",
                                           "detail": "Auto-recovered from the F&F",
                                           "amount": amt, "count": adv["count"]}

    # ── IT / Security provisioning gates (Account Provisioning is the source) ──
    # A signal is surfaced only when a provisioning record EXISTS for that account
    # type — REVOKED auto-clears (mirrors ERP), ACTIVE flags "still provisioned"
    # with the revoke action, in-setup states stay pending. No record ⇒ no signal
    # (the gate is purely manual, exactly as before — never a false auto-clear).
    prov = facts["prov"]
    if prov is not None:
        for ikey, (atype, plabel) in PROVISIONING_GATES.items():
            if ikey not in by_key:
                continue
            st = prov.get(atype)
            if st is None:
                continue
            if st == "REVOKED":
                signals[ikey] = {"source": "provisioning", "state": "satisfied", "auto": True,
                                 "label": f"{plabel} de-provisioned", "detail": "Revoked in Account Provisioning"}
                auto_clear(by_key[ikey], f"{plabel} de-provisioned (Account Provisioning)")
            elif st == "ACTIVE":
                signals[ikey] = {"source": "provisioning", "state": "attention", "auto": True,
                                 "label": f"{plabel} still active", "detail": "Revoke in Account Provisioning"}
            else:  # PENDING / REQUESTED / FAILED
                signals[ikey] = {"source": "provisioning", "state": "pending", "auto": True,
                                 "label": f"{plabel} · {st.title()}", "detail": "Not yet active in provisioning"}

    if auto_events:
        db.flush()
        recompute_clearance_progress(db, case)
    return signals, auto_events


# ─── response builders ───

def _user_name(db: Session, user_id: Optional[UUID]) -> Optional[str]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        return None
    return getattr(u, "full_name", None) or getattr(u, "name", None) or getattr(u, "email", None)


def employee_label(emp: Optional[Employee]) -> Dict[str, Any]:
    if not emp:
        return {"employee_name": None, "employee_code": None}
    name = None
    if emp.user is not None:
        name = getattr(emp.user, "full_name", None) or getattr(emp.user, "name", None) or getattr(emp.user, "email", None)
    return {"employee_name": name, "employee_code": emp.employee_code or emp.employee_id}


def case_to_response(db: Session, case: ExitCase) -> Dict[str, Any]:
    """Flat ExitCaseResponse dict."""
    emp = case.employee
    lbl = employee_label(emp)
    return {
        "id": case.id,
        "case_number": case.case_number,
        "employee_id": case.employee_id,
        "employee_name": lbl["employee_name"],
        "employee_code": lbl["employee_code"],
        "department_name": case.department.name if case.department else None,
        "designation_name": (emp.designation.name if emp and emp.designation else None),
        "resignation_type": case.resignation_type,
        "reason_category": case.reason_category,
        "reason_detail": case.reason_detail,
        "status": case.status,
        "initiated_by": case.initiated_by,
        "resignation_date": case.resignation_date,
        "requested_last_working_date": case.requested_last_working_date,
        "notice_period_days": case.notice_period_days,
        "notice_period_start_date": case.notice_period_start_date,
        "last_working_date": case.last_working_date,
        "exit_date": case.exit_date,
        # Service start for the experience-letter tenure — the snapshot taken at
        # case creation, falling back to the live employee value.
        "joining_date_snapshot": case.joining_date_snapshot or (emp.joining_date if emp else None),
        "notice_waived": case.notice_waived,
        "notice_buyout_days": case.notice_buyout_days,
        "manager_id": case.manager_id,
        "manager_name": _user_name(db, case.manager_id),
        "manager_decision": case.manager_decision,
        "eligible_for_rehire": case.eligible_for_rehire,
        "clearance_progress_pct": case.clearance_progress_pct or 0,
        "settlement_net_amount": case.settlement_net_amount,
        "lifecycle_state": (emp.lifecycle_state.value if emp and emp.lifecycle_state else None),
        "public_token": case.public_token,
        "public_token_expires_at": case.public_token_expires_at,
        "personal_email": case.personal_email,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


# Map exit-case status → expected Employee lifecycle state (for the consistency flag).
_EXPECTED_LIFECYCLE = {
    ExitCaseStatus.NOTICE_PERIOD: {LifecycleState.ON_NOTICE},
    ExitCaseStatus.CLEARANCE: {LifecycleState.ON_NOTICE, LifecycleState.SUSPENDED, LifecycleState.ACTIVE, LifecycleState.ON_PROBATION},
    ExitCaseStatus.SETTLEMENT: {LifecycleState.ON_NOTICE, LifecycleState.SUSPENDED, LifecycleState.ACTIVE, LifecycleState.ON_PROBATION},
    ExitCaseStatus.COMPLETED: {LifecycleState.EXITED, LifecycleState.ARCHIVED},
}


def lifecycle_consistent(case: ExitCase) -> bool:
    expected = _EXPECTED_LIFECYCLE.get(case.status)
    if not expected or case.employee is None:
        return True
    return case.employee.lifecycle_state in expected


def clearance_item_to_response(db: Session, item: ExitClearanceItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "department": item.department,
        "item_key": item.item_key,
        "title": item.title,
        "description": item.description,
        "is_mandatory": item.is_mandatory,
        "status": item.status,
        "assignee_user_id": item.assignee_user_id,
        "assignee_name": _user_name(db, item.assignee_user_id),
        "remarks": item.remarks,
        "recovery_amount": item.recovery_amount,
        "signed_off_by_id": item.signed_off_by_id,
        "signed_off_by_name": _user_name(db, item.signed_off_by_id),
        "signed_off_at": item.signed_off_at,
        "sort_order": item.sort_order,
        "submission": item.submission,
        "is_self_handover": is_self_handover(item),
    }
