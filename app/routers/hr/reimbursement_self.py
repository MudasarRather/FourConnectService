"""HR Reimbursements — self-service (`/hr/me/reimbursements`).

Two audiences, both regular (non-superadmin) users:
  • the EMPLOYEE raising/tracking their own claims, and
  • the MANAGER acting on their team's claims at a MANAGER chain stage
    (powers the user-side Team Approvals page).
Reads use `try_self_employee` (→ unlinked banner, no 404 spam); writes use
`resolve_self_employee`.
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.claim import Claim
from app.models.hr.claim_category import ClaimCategory
from app.models.hr.claim_policy import ClaimPolicy
from app.models.hr.reimbursement_type import ClaimStatus, ClaimAuditAction
from app.schemas.hr.reimbursements import (
    ClaimResponse, ClaimListResponse, ClaimCreate, ClaimUpdate, ClaimDecisionBody,
    ClaimCancelBody, ClaimCategoryListResponse, MyClaimSummary, MyBalancesResponse,
)
from app.utils.dependencies import get_current_user
from app.utils.hr.reimbursements import (
    try_self_employee, resolve_self_employee, to_response, write_claim_audit,
    emit_notifications, can_act_on_step,
)
from app.utils.hr.reimbursements.flow import (
    get_category, build_new_claim, submit_claim, apply_decision,
)

router = APIRouter(prefix="/hr/me/reimbursements", tags=["HR — My Reimbursements"])

_USED = (ClaimStatus.DRAFT, ClaimStatus.PENDING_APPROVAL, ClaimStatus.RETURNED,
         ClaimStatus.APPROVED, ClaimStatus.SETTLED, ClaimStatus.PAID)


def _empty_list(page, limit):
    return ClaimListResponse(items=[], total=0, page=page, limit=limit, total_pages=1, unlinked=True)


def _own_claim(db: Session, claim_id: UUID, emp: Employee, *, lock: bool = False) -> Claim:
    q = db.query(Claim).options(joinedload(Claim.category)).filter(
        Claim.id == claim_id, Claim.is_deleted == False)  # noqa: E712
    if lock:
        q = q.with_for_update(of=Claim)   # lock claims row only (category is an outer join)
    claim = q.first()
    if not claim or claim.employee_id != emp.id:
        raise HTTPException(404, "Claim not found")
    return claim


# ─────────────────────────── employee ───────────────────────────

@router.get("/", response_model=ClaimListResponse)
def my_claims(
    status: Optional[ClaimStatus] = None,
    category_id: Optional[UUID] = None,
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, user)
    if not emp:
        return _empty_list(page, limit)
    query = db.query(Claim).options(joinedload(Claim.category)).filter(
        Claim.employee_id == emp.id, Claim.is_deleted == False)  # noqa: E712
    if status:
        query = query.filter(Claim.status == status)
    if category_id:
        query = query.filter(Claim.category_id == category_id)
    total = query.count()
    rows = query.order_by(Claim.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return ClaimListResponse(items=[to_response(db, c) for c in rows], total=total, page=page,
                             limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


@router.get("/summary", response_model=MyClaimSummary)
def my_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = try_self_employee(db, user)
    if not emp:
        return MyClaimSummary(unlinked=True)
    base = db.query(Claim).filter(Claim.employee_id == emp.id, Claim.is_deleted == False)  # noqa: E712
    submitted_amt = base.filter(Claim.status.in_(_USED)).with_entities(
        sa_func.coalesce(sa_func.sum(Claim.amount), 0)).scalar() or 0
    approved_amt = base.filter(Claim.status.in_([ClaimStatus.APPROVED, ClaimStatus.SETTLED, ClaimStatus.PAID])).with_entities(
        sa_func.coalesce(sa_func.sum(sa_func.coalesce(Claim.approved_amount, Claim.amount)), 0)).scalar() or 0
    settled_amt = base.filter(Claim.status.in_([ClaimStatus.SETTLED, ClaimStatus.PAID])).with_entities(
        sa_func.coalesce(sa_func.sum(sa_func.coalesce(Claim.approved_amount, Claim.amount)), 0)).scalar() or 0
    in_flight = base.filter(Claim.status.in_([ClaimStatus.PENDING_APPROVAL, ClaimStatus.RETURNED])).count()
    settled_count = base.filter(Claim.status.in_([ClaimStatus.SETTLED, ClaimStatus.PAID])).count()
    total = base.count()
    return MyClaimSummary(submitted_amount=submitted_amt, approved_amount=approved_amt,
                          settled_amount=settled_amt, in_flight=in_flight,
                          settled_count=settled_count, total_claims=total, unlinked=False)


@router.get("/balances", response_model=MyBalancesResponse)
def my_balances(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = try_self_employee(db, user)
    if not emp:
        return MyBalancesResponse(unlinked=True)
    today = date.today()
    m_start = date(today.year, today.month, 1)
    fy_start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
    cats = db.query(ClaimCategory).filter(
        ClaimCategory.is_deleted == False, ClaimCategory.is_active == True).all()  # noqa: E712
    pols = {p.category_id: p for p in db.query(ClaimPolicy).filter(ClaimPolicy.is_deleted == False).all()}  # noqa: E712
    items = []
    for c in cats:
        base = db.query(Claim).filter(Claim.employee_id == emp.id, Claim.category_id == c.id,
                                      Claim.is_deleted == False, Claim.status.in_(_USED))  # noqa: E712
        spent_month = base.filter(Claim.expense_date >= m_start).with_entities(
            sa_func.coalesce(sa_func.sum(Claim.amount), 0)).scalar() or 0
        spent_year = base.filter(Claim.expense_date >= fy_start).with_entities(
            sa_func.coalesce(sa_func.sum(Claim.amount), 0)).scalar() or 0
        claims_month = base.filter(Claim.expense_date >= m_start).count()
        p = pols.get(c.id)
        items.append({
            "category_id": c.id, "category_code": c.code, "category_name": c.name,
            "color_hex": c.color_hex, "icon": c.icon,
            "spent_this_month": spent_month, "spent_this_year": spent_year,
            "max_amount_per_month": p.max_amount_per_month if p else None,
            "max_amount_per_year": p.max_amount_per_year if p else None,
            "claims_this_month": claims_month,
            "max_claims_per_month": p.max_claims_per_month if p else None,
        })
    return MyBalancesResponse(items=items, unlinked=False)


@router.get("/categories", response_model=ClaimCategoryListResponse)
def my_categories(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(ClaimCategory).filter(
        ClaimCategory.is_deleted == False, ClaimCategory.is_active == True,  # noqa: E712
    ).order_by(ClaimCategory.sort_order.asc().nullslast(), ClaimCategory.name.asc()).all()
    items = [{
        "id": c.id, "code": c.code, "name": c.name, "description": c.description,
        "icon": c.icon, "color_hex": c.color_hex, "field_schema": c.field_schema or [],
        "default_settlement_method": c.default_settlement_method,
        "requires_attachment": c.requires_attachment, "is_taxable": c.is_taxable,
        "gl_code": c.gl_code, "sort_order": c.sort_order, "is_active": c.is_active,
        "created_at": c.created_at, "claim_count": None,
    } for c in rows]
    return ClaimCategoryListResponse(items=items, total=len(items))


@router.get("/{claim_id:uuid}", response_model=ClaimResponse)
def my_claim(claim_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    return to_response(db, _own_claim(db, claim_id, emp))


@router.post("/", response_model=ClaimResponse, status_code=201)
def create_and_submit(payload: ClaimCreate, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    category = get_category(db, payload.category_id)
    claim = build_new_claim(db, employee=emp, category=category, payload=payload, actor=user)
    next_approver = submit_claim(db, claim, emp, user)
    db.commit()
    db.refresh(claim)
    try:
        emit_notifications(db, claim, employee_user_id=user.id, event="submitted",
                           actor=user, next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()
    return to_response(db, claim)


@router.post("/draft", response_model=ClaimResponse, status_code=201)
def create_draft(payload: ClaimCreate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    category = get_category(db, payload.category_id)
    claim = build_new_claim(db, employee=emp, category=category, payload=payload, actor=user)
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.CREATE, actor_id=user.id,
                      to_status=claim.status.value, note=f"Draft {claim.claim_number}")
    db.commit()
    db.refresh(claim)
    return to_response(db, claim)


@router.patch("/{claim_id:uuid}", response_model=ClaimResponse)
def edit_my_claim(claim_id: UUID, payload: ClaimUpdate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    claim = _own_claim(db, claim_id, emp, lock=True)
    if claim.status not in (ClaimStatus.DRAFT, ClaimStatus.RETURNED):
        raise HTTPException(409, f"A {claim.status.value} claim cannot be edited")
    data = payload.model_dump(exclude_unset=True)
    if "category_id" in data and data["category_id"]:
        get_category(db, data["category_id"])
    if "attachments" in data and data["attachments"] is not None:
        data["attachments"] = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in payload.attachments]
    for k, v in data.items():
        setattr(claim, k, v)
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.UPDATE, actor_id=user.id)
    db.commit()
    db.refresh(claim)
    return to_response(db, claim)


@router.post("/{claim_id:uuid}/submit", response_model=ClaimResponse)
def submit_my_claim(claim_id: UUID, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    claim = _own_claim(db, claim_id, emp, lock=True)
    next_approver = submit_claim(db, claim, emp, user)
    db.commit()
    db.refresh(claim)
    try:
        emit_notifications(db, claim, employee_user_id=user.id, event="submitted",
                           actor=user, next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()
    return to_response(db, claim)


@router.delete("/{claim_id:uuid}")
def withdraw_my_claim(claim_id: UUID, body: ClaimCancelBody = ClaimCancelBody(),
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    claim = _own_claim(db, claim_id, emp, lock=True)
    if claim.status not in (ClaimStatus.DRAFT, ClaimStatus.PENDING_APPROVAL, ClaimStatus.RETURNED):
        raise HTTPException(409, f"A {claim.status.value} claim cannot be withdrawn")
    from_status = claim.status.value
    if claim.status == ClaimStatus.DRAFT:
        claim.is_deleted = True
    else:
        claim.status = ClaimStatus.CANCELLED
        claim.cancelled_at = datetime.now(timezone.utc)
        claim.cancelled_by_id = user.id
        claim.cancelled_reason = body.reason
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.CANCEL, actor_id=user.id,
                      from_status=from_status, to_status=("DELETED" if claim.is_deleted else claim.status.value),
                      note=body.reason)
    db.commit()
    return {"success": True}


# ─────────────────────────── manager queue (user-side) ───────────────────────────

@router.get("/approval-queue", response_model=ClaimListResponse)
def my_approval_queue(
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Claims whose current stage is waiting on THIS user (MANAGER stages, or
    USER stages naming them). Superusers also see HR/FINANCE stages here, but
    their primary surface is the admin Approvals tab."""
    rows = db.query(Claim).options(joinedload(Claim.category)).filter(
        Claim.is_deleted == False, Claim.status == ClaimStatus.PENDING_APPROVAL,  # noqa: E712
    ).order_by(Claim.submitted_at.asc()).all()
    actionable = []
    for r in rows:
        steps = list(r.approval_steps or [])
        idx = int(r.current_step or 0)
        if 0 <= idx < len(steps) and can_act_on_step(user, steps[idx]):
            actionable.append(r)
    total = len(actionable)
    paged = actionable[(page - 1) * limit: (page - 1) * limit + limit]
    return ClaimListResponse(items=[to_response(db, c) for c in paged], total=total, page=page,
                             limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


@router.patch("/claims/{claim_id:uuid}/decide", response_model=ClaimResponse)
def manager_decide(claim_id: UUID, body: ClaimDecisionBody, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """A manager (or named USER approver) decides on their current stage."""
    claim = db.query(Claim).options(joinedload(Claim.category)).filter(
        Claim.id == claim_id, Claim.is_deleted == False).with_for_update(of=Claim).first()  # noqa: E712
    if not claim:
        raise HTTPException(404, "Claim not found")
    steps = list(claim.approval_steps or [])
    idx = int(claim.current_step or 0)
    if not (0 <= idx < len(steps)):
        raise HTTPException(409, "Claim is fully resolved")
    if not can_act_on_step(user, steps[idx]):
        raise HTTPException(403, "You are not the approver for the current stage")
    new_status, next_approver, event = apply_decision(
        db, claim, decision=body.decision, notes=body.notes,
        approved_amount=body.approved_amount, actor=user)
    db.commit()
    db.refresh(claim)
    try:
        emp_uid = db.query(Employee.user_id).filter(Employee.id == claim.employee_id).scalar()
        emit_notifications(db, claim, employee_user_id=emp_uid, event=event,
                           actor=user, next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()
    return to_response(db, claim)
