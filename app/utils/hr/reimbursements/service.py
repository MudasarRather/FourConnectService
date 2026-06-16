"""HR Reimbursements — shared DB helpers (number gen, audit, notifications,
response builder, settlement). Keeps the routers thin.
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal
from typing import List, Optional, Dict, Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.system_setting import SystemSetting
from app.models.notification import Notification
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.claim import Claim
from app.models.hr.claim_category import ClaimCategory
from app.models.hr.claim_settlement import ClaimSettlement
from app.models.hr.claim_audit_log import ClaimAuditLog
from app.models.hr.reimbursement_type import (
    ClaimStatus, ClaimAuditAction, SettlementMethod,
)
from app.models.hr.payroll_adjustment import (
    PayrollAdjustment, AdjustmentType, AdjustmentStatus,
)


# ─── self-employee resolution (mirrors leaves.py) ───

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


def generate_claim_number(db: Session) -> str:
    return _next_counter(db, "claim_ref_counter", "CL", Claim, Claim.claim_number,
                         "Monotonic counter for Claim.claim_number")


def generate_settlement_number(db: Session) -> str:
    return _next_counter(db, "claim_settlement_counter", "ST", ClaimSettlement,
                         ClaimSettlement.settlement_number,
                         "Monotonic counter for ClaimSettlement.settlement_number")


# ─── dynamic field validation ───

def validate_details_against_schema(details: Dict[str, Any], field_schema: List[dict]) -> None:
    """Reject unknown keys and missing required fields. Advisory-soft on types."""
    schema = field_schema or []
    allowed = {f.get("key") for f in schema if f.get("key")}
    details = details or {}
    unknown = set(details.keys()) - allowed
    if unknown:
        raise HTTPException(422, f"Unknown detail fields for this category: {', '.join(sorted(unknown))}")
    for f in schema:
        if f.get("required") and not str(details.get(f.get("key"), "")).strip():
            raise HTTPException(422, f"Missing required field: {f.get('label') or f.get('key')}")


# ─── audit + notifications ───

def write_claim_audit(db: Session, *, entity_type: str, entity_id, action: ClaimAuditAction,
                      claim_id=None, actor_id=None, from_status: Optional[str] = None,
                      to_status: Optional[str] = None, note: Optional[str] = None,
                      payload: Optional[Dict] = None) -> None:
    db.add(ClaimAuditLog(
        entity_type=entity_type, entity_id=entity_id, action=action,
        claim_id=claim_id, actor_id=actor_id, from_status=from_status,
        to_status=to_status, note=note, payload=payload,
    ))


def emit_notifications(db: Session, claim: Claim, *, employee_user_id: Optional[UUID],
                       event: str, actor: Optional[User] = None,
                       next_approver_id: Optional[UUID] = None) -> None:
    """Best-effort Notification rows. Caller swallows failures."""
    def _add(user_id, type_, title, message, url):
        if not user_id:
            return
        db.add(Notification(
            user_id=user_id, type=type_, title=title, message=message,
            related_user_id=actor.id if actor else None, action_url=url, is_read=False,
        ))

    ref = claim.claim_number
    admin_url = f"/admin/hr/reimbursements/claims#{ref}"
    self_url = "/user/self-service/reimbursements"

    if event == "submitted":
        _add(employee_user_id, "claim_submitted", "Reimbursement submitted",
             f"{ref} submitted for approval", self_url)
        _add(next_approver_id, "claim_pending", "Reimbursement awaiting you",
             f"{ref} is awaiting your decision", "/user/self-service/team-approvals")
    elif event == "advanced":
        _add(next_approver_id, "claim_pending", "Reimbursement awaiting you",
             f"{ref} is awaiting your decision", "/user/self-service/team-approvals")
    elif event == "approved":
        _add(employee_user_id, "claim_approved", "Reimbursement approved",
             f"{ref} fully approved — awaiting settlement", self_url)
    elif event == "rejected":
        _add(employee_user_id, "claim_rejected", "Reimbursement declined",
             f"{ref} was declined", self_url)
    elif event == "returned":
        _add(employee_user_id, "claim_returned", "Reimbursement returned",
             f"{ref} returned for correction", self_url)
    elif event == "settled":
        _add(employee_user_id, "claim_settled", "Reimbursement settled",
             f"{ref} has been settled", self_url)
    elif event == "paid":
        _add(employee_user_id, "claim_paid", "Reimbursement paid",
             f"{ref} has been paid", self_url)
    elif event == "reversed":
        _add(employee_user_id, "claim_reversed", "Reimbursement reversed",
             f"{ref} was reversed", self_url)


# ─── snapshots + response builder ───

def employee_snapshot(db: Session, employee_id: UUID) -> dict:
    snap = (
        db.query(
            Employee.id, Employee.employee_id.label("code"),
            User.full_name.label("name"), User.email.label("email"),
            Department.name.label("dept"), Designation.name.label("desg"),
            Employee.reporting_manager_id,
        )
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .filter(Employee.id == employee_id)
        .first()
    )
    if not snap:
        return {}
    return {
        "name": snap.name, "code": snap.code, "email": snap.email,
        "dept": snap.dept, "desg": snap.desg,
        "reporting_manager_id": snap.reporting_manager_id,
    }


def enrich_steps_with_names(db: Session, steps: List[dict]) -> List[dict]:
    if not steps:
        return steps
    uids = set()
    for s in steps:
        for k in ("approver_user_id", "decided_by_id"):
            v = s.get(k)
            if v:
                uids.add(v)
    if not uids:
        return [dict(s) for s in steps]
    try:
        uuid_objs = [UUID(u) for u in uids]
    except Exception:
        return [dict(s) for s in steps]
    rows = db.query(User.id, User.full_name).filter(User.id.in_(uuid_objs)).all()
    name_by_id = {str(r[0]): r[1] for r in rows}
    out = []
    for s in steps:
        e = dict(s)
        if e.get("approver_user_id"):
            e["approver_name"] = name_by_id.get(e["approver_user_id"])
        if e.get("decided_by_id"):
            e["decided_by_name"] = name_by_id.get(e["decided_by_id"])
        out.append(e)
    return out


def _latest_settlement(db: Session, claim_id: UUID) -> Optional[ClaimSettlement]:
    return (
        db.query(ClaimSettlement)
        .filter(ClaimSettlement.claim_id == claim_id)
        .order_by(ClaimSettlement.created_at.desc())
        .first()
    )


_EDITABLE = {ClaimStatus.DRAFT, ClaimStatus.RETURNED}
_WITHDRAWABLE = {ClaimStatus.DRAFT, ClaimStatus.PENDING_APPROVAL, ClaimStatus.RETURNED}


def to_response(db: Session, claim: Claim) -> dict:
    """Build the ClaimResponse dict (Pydantic validates on the way out)."""
    snap = employee_snapshot(db, claim.employee_id)
    cat = claim.category
    project_name = None
    if claim.project_id:
        from app.models.project import Project
        prow = db.query(Project.name).filter(Project.id == claim.project_id).first()
        project_name = prow[0] if prow else None
    latest = _latest_settlement(db, claim.id)
    return {
        "id": claim.id,
        "claim_number": claim.claim_number,
        "employee_id": claim.employee_id,
        "employee_name": snap.get("name"),
        "employee_code": snap.get("code"),
        "department": snap.get("dept"),
        "designation": snap.get("desg"),
        "category_id": claim.category_id,
        "category_code": cat.code if cat else None,
        "category_name": cat.name if cat else None,
        "category_icon": cat.icon if cat else None,
        "category_color": cat.color_hex if cat else None,
        "claim_date": claim.claim_date,
        "expense_date": claim.expense_date,
        "amount": claim.amount,
        "currency": claim.currency,
        "description": claim.description,
        "vendor": claim.vendor,
        "remarks": claim.remarks,
        "cost_center": claim.cost_center,
        "project_id": claim.project_id,
        "project_name": project_name,
        "attachments": claim.attachments or [],
        "details": claim.details or {},
        "status": claim.status,
        "submitted_at": claim.submitted_at,
        "approval_steps": enrich_steps_with_names(db, list(claim.approval_steps or [])),
        "current_step": int(claim.current_step or 0),
        "approved_at": claim.approved_at,
        "approver_notes": claim.approver_notes,
        "return_reason": claim.return_reason,
        "reject_reason": claim.reject_reason,
        "clarification_note": claim.clarification_note,
        "approved_amount": claim.approved_amount,
        "settlement_method": claim.settlement_method,
        "settled_at": claim.settled_at,
        "settlement_number": claim.settlement_number,
        "payroll_ref": claim.payroll_ref,
        "paid_at": claim.paid_at,
        "reversed_at": claim.reversed_at,
        "reversal_reason": claim.reversal_reason,
        "latest_settlement": latest,
        "created_at": claim.created_at,
        "can_edit": claim.status in _EDITABLE,
        "can_withdraw": claim.status in _WITHDRAWABLE,
    }


# ─── settlement ───

def settle_via_payroll(db: Session, claim: Claim, *, period_month: Optional[int],
                       period_year: Optional[int], amount: Decimal, is_taxable: bool,
                       actor: User, note: Optional[str] = None) -> ClaimSettlement:
    """Create an APPROVED, unpaid PayrollAdjustment that the next matching pay run
    folds into the payslip. The line renders from sub_type/title — we deliberately
    DO NOT add a REIMBURSEMENT value to the hr_adjustment_type PG enum.
    """
    cat = claim.category
    cat_code = cat.code if cat else "REIMBURSEMENT"
    adj = PayrollAdjustment(
        employee_id=claim.employee_id,
        adjustment_type=AdjustmentType.VARIABLE_PAY,
        sub_type=f"REIMBURSEMENT:{cat_code}",
        title=f"Reimbursement · {cat.name if cat else cat_code} ({claim.claim_number})",
        amount=amount,
        is_taxable=is_taxable,
        is_deduction=False,
        period_month=period_month,
        period_year=period_year,
        reason=note or f"Reimbursement settlement for {claim.claim_number}",
        status=AdjustmentStatus.APPROVED,
        approved_by_id=actor.id,
        approved_at=datetime.now(timezone.utc),
        created_by_id=actor.id,
    )
    db.add(adj)
    db.flush()

    settlement = ClaimSettlement(
        settlement_number=generate_settlement_number(db),
        claim_id=claim.id,
        method=SettlementMethod.PAYROLL,
        amount=amount,
        payroll_adjustment_id=adj.id,
        notes=note,
        settled_by_id=actor.id,
    )
    db.add(settlement)
    db.flush()

    claim.settlement_method = SettlementMethod.PAYROLL
    claim.approved_amount = amount
    claim.settlement_number = settlement.settlement_number
    claim.payroll_adjustment_id = adj.id
    claim.settled_at = datetime.now(timezone.utc)
    claim.settled_by_id = actor.id
    claim.status = ClaimStatus.SETTLED   # flips to PAID when the batch is released
    return settlement


def settle_direct(db: Session, claim: Claim, *, method: SettlementMethod, amount: Decimal,
                  settlement_date: Optional[date], reference: Optional[str],
                  bank_account_last4: Optional[str], notes: Optional[str],
                  actor: User) -> ClaimSettlement:
    """Record a direct disbursement (bank/cash/cheque/petty cash) — paid now."""
    now = datetime.now(timezone.utc)
    settlement = ClaimSettlement(
        settlement_number=generate_settlement_number(db),
        claim_id=claim.id,
        method=method,
        amount=amount,
        settlement_date=settlement_date or date.today(),
        reference=reference,
        bank_account_last4=bank_account_last4,
        notes=notes,
        settled_by_id=actor.id,
    )
    db.add(settlement)
    db.flush()

    claim.settlement_method = method
    claim.approved_amount = amount
    claim.settlement_number = settlement.settlement_number
    claim.settled_at = now
    claim.settled_by_id = actor.id
    claim.paid_at = now
    claim.status = ClaimStatus.PAID
    return settlement
