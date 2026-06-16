"""HR Reimbursements — admin/HR/Finance surface (claims, approvals, settlement,
dashboard, audit). Manager-stage decisions for non-superadmins live on the
self-service router (`reimbursement_self.py`)."""
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func, or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.claim import Claim
from app.models.hr.claim_category import ClaimCategory
from app.models.hr.claim_settlement import ClaimSettlement
from app.models.hr.claim_audit_log import ClaimAuditLog
from app.models.hr.reimbursement_type import (
    ClaimStatus, ClaimDecision, SettlementMethod, ClaimAuditAction,
)
from app.models.hr.payroll_adjustment import (
    PayrollAdjustment, AdjustmentType, AdjustmentStatus,
)
from app.schemas.hr.reimbursements import (
    ClaimResponse, ClaimListResponse, ClaimAdminCreate, ClaimUpdate,
    ClaimDecisionBody, ClaimClarificationBody, ClaimEscalateBody, ClaimReversalBody,
    ClaimBulkDecideBody, SettlePayrollBody, SettleDirectBody,
    ReimbursementStats, ClaimAuditListResponse, ApproverCandidateListResponse,
)
from app.utils.dependencies import get_current_superuser, get_current_user
from app.utils.hr.reimbursements import (
    to_response, write_claim_audit, emit_notifications, can_act_on_step,
    settle_via_payroll, settle_direct, validate_details_against_schema,
    auto_skip_unresolvable, step_status, mirror_final_columns,
)
from app.utils.hr.reimbursements.flow import (
    get_category, build_new_claim, submit_claim, apply_decision,
)

router = APIRouter(prefix="/hr/reimbursements", tags=["HR — Reimbursements"])

_TERMINAL = (ClaimStatus.REJECTED, ClaimStatus.CANCELLED, ClaimStatus.REVERSED)


def _get_claim(db: Session, claim_id: UUID, *, lock: bool = False) -> Claim:
    q = db.query(Claim).options(joinedload(Claim.category)).filter(
        Claim.id == claim_id, Claim.is_deleted == False,  # noqa: E712
    )
    if lock:
        # Lock only the claims row — FOR UPDATE can't apply to the nullable
        # side of the category outer join produced by joinedload.
        q = q.with_for_update(of=Claim)
    claim = q.first()
    if not claim:
        raise HTTPException(404, "Claim not found")
    return claim


def _resp(db: Session, claim: Claim) -> dict:
    return to_response(db, claim)


# ─────────────────────────── list + dashboard ───────────────────────────

@router.get("/", response_model=ClaimListResponse)
def list_claims(
    status: Optional[ClaimStatus] = None,
    category_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    settlement_method: Optional[SettlementMethod] = None,
    project_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    query = db.query(Claim).options(joinedload(Claim.category)).filter(
        Claim.is_deleted == False)  # noqa: E712
    if status:
        query = query.filter(Claim.status == status)
    if category_id:
        query = query.filter(Claim.category_id == category_id)
    if employee_id:
        query = query.filter(Claim.employee_id == employee_id)
    if department_id:
        query = query.join(Employee, Employee.id == Claim.employee_id).filter(
            Employee.department_id == department_id)
    if settlement_method:
        query = query.filter(Claim.settlement_method == settlement_method)
    if project_id:
        query = query.filter(Claim.project_id == project_id)
    if date_from:
        query = query.filter(Claim.expense_date >= date_from)
    if date_to:
        query = query.filter(Claim.expense_date <= date_to)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            Claim.claim_number.ilike(like),
            Claim.vendor.ilike(like),
            Claim.description.ilike(like),
        ))
    total = query.count()
    rows = query.order_by(Claim.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return ClaimListResponse(
        items=[_resp(db, c) for c in rows], total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
    )


@router.get("/stats", response_model=ReimbursementStats)
def stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    base = db.query(Claim).filter(Claim.is_deleted == False)  # noqa: E712

    def _count(*statuses):
        return base.filter(Claim.status.in_(statuses)).count()

    total = base.count()
    pending = _count(ClaimStatus.PENDING_APPROVAL)
    approved_unsettled = _count(ClaimStatus.APPROVED)
    rejected = _count(ClaimStatus.REJECTED)

    settled_amount = db.query(sa_func.coalesce(sa_func.sum(Claim.approved_amount), 0)).filter(
        Claim.is_deleted == False, Claim.status.in_([ClaimStatus.SETTLED, ClaimStatus.PAID]),  # noqa: E712
    ).scalar() or 0
    pending_settlement = db.query(sa_func.coalesce(sa_func.sum(Claim.amount), 0)).filter(
        Claim.is_deleted == False, Claim.status == ClaimStatus.APPROVED,  # noqa: E712
    ).scalar() or 0

    today = date.today()
    month_start = date(today.year, today.month, 1)
    claims_this_month = base.filter(Claim.claim_date >= month_start).count()
    fy_start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
    total_fy = db.query(sa_func.coalesce(sa_func.sum(Claim.approved_amount), 0)).filter(
        Claim.is_deleted == False, Claim.status.in_([ClaimStatus.SETTLED, ClaimStatus.PAID]),  # noqa: E712
        Claim.settled_at >= datetime(fy_start.year, fy_start.month, fy_start.day, tzinfo=timezone.utc),
    ).scalar() or 0

    paid_payroll = base.filter(Claim.status.in_([ClaimStatus.SETTLED, ClaimStatus.PAID]),
                               Claim.settlement_method == SettlementMethod.PAYROLL).count()
    paid_direct = base.filter(Claim.status == ClaimStatus.PAID,
                              Claim.settlement_method != SettlementMethod.PAYROLL).count()

    # Average processing days (submitted → settled)
    avg_days = None
    settled_rows = db.query(Claim.submitted_at, Claim.settled_at).filter(
        Claim.is_deleted == False, Claim.settled_at.isnot(None), Claim.submitted_at.isnot(None),  # noqa: E712
    ).all()
    if settled_rows:
        spans = [(s.settled_at - s.submitted_at).total_seconds() / 86400.0
                 for s in settled_rows if s.settled_at and s.submitted_at]
        if spans:
            avg_days = round(sum(spans) / len(spans), 1)

    # By category
    cat_rows = (
        db.query(ClaimCategory.id, ClaimCategory.code, ClaimCategory.name, ClaimCategory.color_hex,
                 sa_func.count(Claim.id), sa_func.coalesce(sa_func.sum(Claim.amount), 0))
        .join(Claim, Claim.category_id == ClaimCategory.id)
        .filter(Claim.is_deleted == False)  # noqa: E712
        .group_by(ClaimCategory.id, ClaimCategory.code, ClaimCategory.name, ClaimCategory.color_hex)
        .all()
    )
    by_category = [{
        "category_id": r[0], "category_code": r[1], "category_name": r[2], "color_hex": r[3],
        "count": r[4], "amount": r[5],
    } for r in cat_rows]

    # By status
    status_rows = (
        db.query(Claim.status, sa_func.count(Claim.id), sa_func.coalesce(sa_func.sum(Claim.amount), 0))
        .filter(Claim.is_deleted == False)  # noqa: E712
        .group_by(Claim.status).all()
    )
    by_status = [{"status": r[0].value, "count": r[1], "amount": r[2]} for r in status_rows]

    # Monthly trend (last 6 months by claim_date)
    monthly = []
    for i in range(5, -1, -1):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        ms = date(y, m, 1)
        me = date(y + (m // 12), (m % 12) + 1, 1)
        claimed = db.query(sa_func.coalesce(sa_func.sum(Claim.amount), 0)).filter(
            Claim.is_deleted == False, Claim.claim_date >= ms, Claim.claim_date < me).scalar() or 0  # noqa: E712
        settled = db.query(sa_func.coalesce(sa_func.sum(Claim.approved_amount), 0)).filter(
            Claim.is_deleted == False, Claim.status.in_([ClaimStatus.SETTLED, ClaimStatus.PAID]),  # noqa: E712
            Claim.settled_at >= datetime(ms.year, ms.month, ms.day, tzinfo=timezone.utc),
            Claim.settled_at < datetime(me.year, me.month, me.day, tzinfo=timezone.utc)).scalar() or 0
        cnt = db.query(sa_func.count(Claim.id)).filter(
            Claim.is_deleted == False, Claim.claim_date >= ms, Claim.claim_date < me).scalar() or 0  # noqa: E712
        monthly.append({"month": f"{y}-{m:02d}", "claimed": claimed, "settled": settled, "count": cnt})

    settlement_split = {
        "payroll": Decimal(str(db.query(sa_func.coalesce(sa_func.sum(Claim.approved_amount), 0)).filter(
            Claim.is_deleted == False, Claim.settlement_method == SettlementMethod.PAYROLL,  # noqa: E712
            Claim.status.in_([ClaimStatus.SETTLED, ClaimStatus.PAID])).scalar() or 0)),
        "direct": Decimal(str(db.query(sa_func.coalesce(sa_func.sum(Claim.approved_amount), 0)).filter(
            Claim.is_deleted == False, Claim.settlement_method != SettlementMethod.PAYROLL,  # noqa: E712
            Claim.settlement_method.isnot(None),
            Claim.status == ClaimStatus.PAID).scalar() or 0)),
    }

    return ReimbursementStats(
        total_claims=total, pending_approval=pending, approved_unsettled=approved_unsettled,
        rejected=rejected, settled_amount=settled_amount, pending_settlement_amount=pending_settlement,
        claims_this_month=claims_this_month, paid_via_payroll=paid_payroll, paid_via_direct=paid_direct,
        avg_processing_days=avg_days, total_reimbursed_fy=total_fy,
        by_category=by_category, by_status=by_status, monthly_trend=monthly,
        settlement_split=settlement_split,
    )


@router.get("/queue", response_model=ClaimListResponse)
def approval_queue(
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser),
):
    """All PENDING_APPROVAL claims whose current stage this admin can act on
    (HR / FINANCE stages)."""
    rows = db.query(Claim).options(joinedload(Claim.category)).filter(
        Claim.is_deleted == False, Claim.status == ClaimStatus.PENDING_APPROVAL,  # noqa: E712
    ).order_by(Claim.submitted_at.asc()).all()
    actionable = []
    for r in rows:
        steps = list(r.approval_steps or [])
        idx = int(r.current_step or 0)
        if 0 <= idx < len(steps) and can_act_on_step(current_user, steps[idx]):
            actionable.append(r)
    total = len(actionable)
    paged = actionable[(page - 1) * limit: (page - 1) * limit + limit]
    return ClaimListResponse(items=[_resp(db, c) for c in paged], total=total, page=page,
                             limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


@router.get("/audit", response_model=ClaimAuditListResponse)
def list_audit(
    claim_id: Optional[UUID] = None, action: Optional[ClaimAuditAction] = None,
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser),
):
    query = db.query(ClaimAuditLog)
    if claim_id:
        query = query.filter(ClaimAuditLog.claim_id == claim_id)
    if action:
        query = query.filter(ClaimAuditLog.action == action)
    total = query.count()
    rows = query.order_by(ClaimAuditLog.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    # Resolve names + claim numbers in bulk
    actor_ids = {r.actor_id for r in rows if r.actor_id}
    claim_ids = {r.claim_id for r in rows if r.claim_id}
    names = {u.id: u.full_name for u in db.query(User.id, User.full_name).filter(User.id.in_(actor_ids))} if actor_ids else {}
    cnums = {c.id: c.claim_number for c in db.query(Claim.id, Claim.claim_number).filter(Claim.id.in_(claim_ids))} if claim_ids else {}
    items = [{
        "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id,
        "action": r.action.value, "claim_id": r.claim_id, "claim_number": cnums.get(r.claim_id),
        "actor_id": r.actor_id, "actor_name": names.get(r.actor_id),
        "from_status": r.from_status, "to_status": r.to_status, "note": r.note,
        "created_at": r.created_at,
    } for r in rows]
    return ClaimAuditListResponse(items=items, total=total)


@router.get("/approver-candidates", response_model=ApproverCandidateListResponse)
def approver_candidates(q: Optional[str] = None, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_superuser)):
    query = db.query(User).filter(User.is_active == True)  # noqa: E712
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
    rows = query.order_by(User.full_name.asc()).limit(50).all()
    return ApproverCandidateListResponse(items=[
        {"id": u.id, "name": u.full_name, "email": u.email, "is_superuser": u.is_superuser}
        for u in rows
    ])


@router.post("/", response_model=ClaimResponse, status_code=201)
def admin_create_claim(payload: ClaimAdminCreate, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_superuser)):
    """Admin raises a claim on behalf of an employee — lands fully APPROVED
    (all stages auto-approved), ready for settlement."""
    emp = db.query(Employee).filter(Employee.id == payload.employee_id,
                                    Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    category = get_category(db, payload.category_id)
    claim = build_new_claim(db, employee=emp, category=category, payload=payload, actor=current_user)
    # Synthesize a single auto-approved stage
    now_iso = datetime.now(timezone.utc).isoformat()
    claim.approval_steps = [{
        "step": 0, "approver_type": "HR", "approver_user_id": None, "label": "HR (admin entry)",
        "min_amount": None, "decision": ClaimDecision.APPROVED.value,
        "decided_by_id": str(current_user.id), "decided_at": now_iso, "notes": "Entered by HR",
    }]
    flag_modified(claim, "approval_steps")
    claim.current_step = 1
    claim.status = ClaimStatus.APPROVED
    claim.submitted_at = datetime.now(timezone.utc)
    claim.submitted_by_id = current_user.id
    claim.approved_at = datetime.now(timezone.utc)
    claim.approved_amount = claim.amount
    mirror_final_columns(claim)
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.CREATE, actor_id=current_user.id,
                      to_status=claim.status.value, note=f"Admin-entered {claim.claim_number}")
    db.commit()
    db.refresh(claim)
    return _resp(db, claim)


@router.post("/bulk-decide", response_model=ClaimListResponse)
def bulk_decide(body: ClaimBulkDecideBody, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    updated: List[Claim] = []
    for cid in body.ids:
        claim = db.query(Claim).filter(Claim.id == cid, Claim.is_deleted == False).with_for_update().first()  # noqa: E712
        if not claim or claim.status != ClaimStatus.PENDING_APPROVAL:
            continue
        steps = list(claim.approval_steps or [])
        idx = int(claim.current_step or 0)
        if not (0 <= idx < len(steps)) or not can_act_on_step(current_user, steps[idx]):
            continue
        new_status, next_approver, event = apply_decision(
            db, claim, decision=body.decision, notes=body.notes, approved_amount=None, actor=current_user)
        try:
            emp_uid = db.query(Employee.user_id).filter(Employee.id == claim.employee_id).scalar()
            emit_notifications(db, claim, employee_user_id=emp_uid, event=event,
                               actor=current_user, next_approver_id=next_approver)
        except Exception:
            pass
        updated.append(claim)
    db.commit()
    for c in updated:
        db.refresh(c)
    return ClaimListResponse(items=[_resp(db, c) for c in updated], total=len(updated),
                             page=1, limit=len(updated) or 1, total_pages=1)


# ─────────────────────────── single claim ───────────────────────────

@router.get("/{claim_id:uuid}", response_model=ClaimResponse)
def get_claim(claim_id: UUID, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_superuser)):
    return _resp(db, _get_claim(db, claim_id))


@router.patch("/{claim_id:uuid}", response_model=ClaimResponse)
def update_claim(claim_id: UUID, payload: ClaimUpdate, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_superuser)):
    claim = _get_claim(db, claim_id, lock=True)
    if claim.status not in (ClaimStatus.DRAFT, ClaimStatus.RETURNED):
        raise HTTPException(409, f"A {claim.status.value} claim cannot be edited")
    data = payload.model_dump(exclude_unset=True)
    if "category_id" in data and data["category_id"]:
        get_category(db, data["category_id"])
    if "details" in data and data["details"] is not None:
        cat_id = data.get("category_id") or claim.category_id
        cat = get_category(db, cat_id)
        validate_details_against_schema(data["details"], cat.field_schema)
    if "attachments" in data and data["attachments"] is not None:
        data["attachments"] = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in payload.attachments]
    for k, v in data.items():
        setattr(claim, k, v)
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.UPDATE, actor_id=current_user.id)
    db.commit()
    db.refresh(claim)
    return _resp(db, claim)


@router.delete("/{claim_id:uuid}")
def delete_claim(claim_id: UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_superuser)):
    claim = _get_claim(db, claim_id, lock=True)
    if claim.status in (ClaimStatus.SETTLED, ClaimStatus.PAID):
        raise HTTPException(409, "Settled / paid claims cannot be deleted — reverse them instead")
    claim.is_deleted = True
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.DELETE, actor_id=current_user.id)
    db.commit()
    return {"success": True}


@router.patch("/{claim_id}/decide", response_model=ClaimResponse)
def decide(claim_id: UUID, body: ClaimDecisionBody, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_superuser)):
    claim = _get_claim(db, claim_id, lock=True)
    steps = list(claim.approval_steps or [])
    idx = int(claim.current_step or 0)
    if not (0 <= idx < len(steps)):
        raise HTTPException(409, "Claim is fully resolved")
    if not can_act_on_step(current_user, steps[idx]):
        raise HTTPException(403, "You are not the configured approver for the current stage")
    new_status, next_approver, event = apply_decision(
        db, claim, decision=body.decision, notes=body.notes,
        approved_amount=body.approved_amount, actor=current_user)
    db.commit()
    db.refresh(claim)
    try:
        emp_uid = db.query(Employee.user_id).filter(Employee.id == claim.employee_id).scalar()
        emit_notifications(db, claim, employee_user_id=emp_uid, event=event,
                           actor=current_user, next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()
    return _resp(db, claim)


@router.post("/{claim_id}/request-clarification", response_model=ClaimResponse)
def request_clarification(claim_id: UUID, body: ClaimClarificationBody, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_superuser)):
    claim = _get_claim(db, claim_id, lock=True)
    if claim.status != ClaimStatus.PENDING_APPROVAL:
        raise HTTPException(409, "Only claims awaiting approval can be returned for clarification")
    steps = list(claim.approval_steps or [])
    idx = int(claim.current_step or 0)
    if not (0 <= idx < len(steps)) or not can_act_on_step(current_user, steps[idx]):
        raise HTTPException(403, "You are not the configured approver for the current stage")
    from_status = claim.status.value
    claim.status = ClaimStatus.RETURNED
    claim.returned_at = datetime.now(timezone.utc)
    claim.return_reason = body.note
    claim.clarification_note = body.note
    claim.clarification_requested_at = datetime.now(timezone.utc)
    claim.clarification_requested_by_id = current_user.id
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.REQUEST_CLARIFICATION, actor_id=current_user.id,
                      from_status=from_status, to_status=claim.status.value, note=body.note)
    db.commit()
    db.refresh(claim)
    try:
        emp_uid = db.query(Employee.user_id).filter(Employee.id == claim.employee_id).scalar()
        emit_notifications(db, claim, employee_user_id=emp_uid, event="returned", actor=current_user)
        db.commit()
    except Exception:
        db.rollback()
    return _resp(db, claim)


@router.post("/{claim_id}/escalate", response_model=ClaimResponse)
def escalate(claim_id: UUID, body: ClaimEscalateBody, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_superuser)):
    """Superuser skips the current stage (e.g. unresponsive manager) and advances."""
    claim = _get_claim(db, claim_id, lock=True)
    if claim.status != ClaimStatus.PENDING_APPROVAL:
        raise HTTPException(409, "Only claims awaiting approval can be escalated")
    steps = list(claim.approval_steps or [])
    idx = int(claim.current_step or 0)
    if not (0 <= idx < len(steps)):
        raise HTTPException(409, "Claim is fully resolved")
    cur = steps[idx]
    cur["decision"] = ClaimDecision.SKIPPED.value
    cur["decided_by_id"] = str(current_user.id)
    cur["decided_at"] = datetime.now(timezone.utc).isoformat()
    cur["notes"] = body.note or "Escalated / skipped by admin"
    new_idx = auto_skip_unresolvable(steps, idx + 1)
    claim.approval_steps = steps
    flag_modified(claim, "approval_steps")
    claim.current_step = new_idx
    if new_idx >= len(steps):
        claim.status = ClaimStatus.APPROVED
        claim.approved_at = datetime.now(timezone.utc)
        if claim.approved_amount is None:
            claim.approved_amount = claim.amount
        mirror_final_columns(claim)
        event = "approved"
        next_approver = None
    else:
        claim.status = step_status(steps, new_idx)
        na = steps[new_idx].get("approver_user_id")
        next_approver = UUID(na) if na else None
        event = "advanced"
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.ESCALATE, actor_id=current_user.id,
                      to_status=claim.status.value, note=body.note)
    db.commit()
    db.refresh(claim)
    try:
        emp_uid = db.query(Employee.user_id).filter(Employee.id == claim.employee_id).scalar()
        emit_notifications(db, claim, employee_user_id=emp_uid, event=event,
                           actor=current_user, next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()
    return _resp(db, claim)


# ─────────────────────────── settlement ───────────────────────────

@router.post("/{claim_id}/settle/payroll", response_model=ClaimResponse)
def settle_payroll(claim_id: UUID, body: SettlePayrollBody, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    claim = _get_claim(db, claim_id, lock=True)
    if claim.status != ClaimStatus.APPROVED:
        raise HTTPException(409, f"Only APPROVED claims can be settled (status {claim.status.value})")
    amount = body.approved_amount or claim.approved_amount or claim.amount
    if amount > (claim.approved_amount or claim.amount):
        raise HTTPException(422, "Settlement amount cannot exceed the approved amount")
    cat = claim.category
    is_taxable = body.is_taxable if body.is_taxable is not None else (cat.is_taxable if cat else False)
    settle_via_payroll(db, claim, period_month=body.period_month, period_year=body.period_year,
                       amount=amount, is_taxable=is_taxable, actor=current_user, note=body.note)
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.SETTLE, actor_id=current_user.id,
                      from_status=ClaimStatus.APPROVED.value, to_status=claim.status.value,
                      note=f"Payroll settlement {claim.settlement_number}")
    db.commit()
    db.refresh(claim)
    try:
        emp_uid = db.query(Employee.user_id).filter(Employee.id == claim.employee_id).scalar()
        emit_notifications(db, claim, employee_user_id=emp_uid, event="settled", actor=current_user)
        db.commit()
    except Exception:
        db.rollback()
    return _resp(db, claim)


@router.post("/{claim_id}/settle/direct", response_model=ClaimResponse)
def settle_direct_route(claim_id: UUID, body: SettleDirectBody, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_superuser)):
    claim = _get_claim(db, claim_id, lock=True)
    if claim.status != ClaimStatus.APPROVED:
        raise HTTPException(409, f"Only APPROVED claims can be settled (status {claim.status.value})")
    amount = body.amount or claim.approved_amount or claim.amount
    if amount > (claim.approved_amount or claim.amount):
        raise HTTPException(422, "Settlement amount cannot exceed the approved amount")
    settle_direct(db, claim, method=body.method, amount=amount, settlement_date=body.settlement_date,
                  reference=body.reference, bank_account_last4=body.bank_account_last4,
                  notes=body.notes, actor=current_user)
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.SETTLE, actor_id=current_user.id,
                      from_status=ClaimStatus.APPROVED.value, to_status=claim.status.value,
                      note=f"Direct settlement {claim.settlement_number} via {body.method.value}")
    db.commit()
    db.refresh(claim)
    try:
        emp_uid = db.query(Employee.user_id).filter(Employee.id == claim.employee_id).scalar()
        emit_notifications(db, claim, employee_user_id=emp_uid, event="paid", actor=current_user)
        db.commit()
    except Exception:
        db.rollback()
    return _resp(db, claim)


@router.post("/{claim_id}/reverse", response_model=ClaimResponse)
def reverse(claim_id: UUID, body: ClaimReversalBody, db: Session = Depends(get_db),
            current_user: User = Depends(get_current_superuser)):
    """Clawback. Unsettled/unpaid payroll adjustments are cancelled; a PAID
    payroll claim posts a compensating DEDUCTION rather than editing a released payslip."""
    claim = _get_claim(db, claim_id, lock=True)
    if claim.status not in (ClaimStatus.APPROVED, ClaimStatus.SETTLED, ClaimStatus.PAID):
        raise HTTPException(409, f"A {claim.status.value} claim cannot be reversed")

    # Unwind the payroll adjustment if one is linked
    if claim.payroll_adjustment_id:
        adj = db.query(PayrollAdjustment).filter(
            PayrollAdjustment.id == claim.payroll_adjustment_id).with_for_update().first()
        if adj:
            if adj.status == AdjustmentStatus.PAID:
                # Released — post a compensating deduction for the next run
                comp = PayrollAdjustment(
                    employee_id=claim.employee_id, adjustment_type=AdjustmentType.DEDUCTION,
                    sub_type=f"REIMBURSEMENT_REVERSAL:{claim.claim_number}",
                    title=f"Reimbursement reversal ({claim.claim_number})",
                    amount=adj.amount, is_taxable=adj.is_taxable, is_deduction=True,
                    reason=body.reason, status=AdjustmentStatus.APPROVED,
                    approved_by_id=current_user.id, approved_at=datetime.now(timezone.utc),
                    created_by_id=current_user.id,
                )
                db.add(comp)
            elif adj.status in (AdjustmentStatus.DRAFT, AdjustmentStatus.APPROVED):
                adj.status = AdjustmentStatus.CANCELLED

    # Mark settlement records reversed
    db.query(ClaimSettlement).filter(ClaimSettlement.claim_id == claim.id).update(
        {ClaimSettlement.is_reversed: True, ClaimSettlement.reversed_at: datetime.now(timezone.utc)},
        synchronize_session=False)

    from_status = claim.status.value
    claim.status = ClaimStatus.REVERSED
    claim.reversed_at = datetime.now(timezone.utc)
    claim.reversed_by_id = current_user.id
    claim.reversal_reason = body.reason
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.REVERSE, actor_id=current_user.id,
                      from_status=from_status, to_status=claim.status.value, note=body.reason)
    db.commit()
    db.refresh(claim)
    try:
        emp_uid = db.query(Employee.user_id).filter(Employee.id == claim.employee_id).scalar()
        emit_notifications(db, claim, employee_user_id=emp_uid, event="reversed", actor=current_user)
        db.commit()
    except Exception:
        db.rollback()
    return _resp(db, claim)
