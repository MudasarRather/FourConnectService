"""HR Travel Management — admin/HR/Finance surface.

Requests, approval chain, bookings, travel advances, DA calculation, expense
settlement, dashboard, calendar and audit. Manager-stage decisions for
non-superadmins live on the self-service router (`travel_self.py`).
"""
from __future__ import annotations

from datetime import datetime, timezone, date, timedelta
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
from app.models.hr.department import Department
from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_booking import TravelBooking
from app.models.hr.travel_advance import TravelAdvance
from app.models.hr.travel_da import TravelDaRecord
from app.models.hr.travel_settlement import TravelSettlement
from app.models.hr.travel_audit_log import TravelAuditLog
from app.models.hr.travel_type import (
    TravelRequestStatus, TravelDecision, TravelAuditAction, BookingStatus,
    AdvanceStatus, DaRecordStatus, TravelSettlementStatus, TravelSettlementMethod,
)
from app.schemas.hr.travel import (
    TravelRequestResponse, TravelRequestListResponse, TravelRequestAdminCreate,
    TravelRequestUpdate, TravelDecisionBody, TravelReturnBody, TravelEscalateBody,
    TravelCancelBody, TravelExecuteBody, TravelBulkDecideBody,
    BookingCreate, BookingUpdate, BookingResponse, BookingListResponse,
    AdvanceCreate, AdvanceDecisionBody, AdvanceRejectBody, AdvanceReleaseBody,
    AdvanceResponse, AdvanceListResponse, AdvanceDetailResponse,
    DaComputeBody, DaApproveBody, DaRecordResponse, DaListResponse,
    SettlementVerifyBody, SettlementSettleBody, SettlementReverseBody,
    SettlementResponse, SettlementListResponse,
    TravelStats, TravelAuditListResponse, ApproverCandidateListResponse,
    CalendarResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.travel import (
    to_response, write_travel_audit, emit_notifications, can_act_on_step,
    auto_skip_unresolvable, step_status, mirror_final_columns, assert_transition,
    get_category, build_new_request, apply_decision, recompute_request_derived,
    compute_da, reconcile, resync_settlement, post_adjustment, cancel_or_reverse, mark_on_duty, unmark_on_duty,
    generate_booking_number, generate_advance_number, generate_settlement_number, get_policy_for,
)
from app.utils.hr.travel.service import trav_today
from app.utils.hr.travel.scheduler import execute_travel, complete_travel
from app.utils.hr.lifecycle_guard import guard_within_tenure, guard_on_payroll

router = APIRouter(prefix="/hr/travel", tags=["HR — Travel Management"])


# ─────────────────────────── helpers ───────────────────────────

def _get_req(db: Session, request_id: UUID, *, lock: bool = False) -> TravelRequest:
    q = db.query(TravelRequest).options(joinedload(TravelRequest.category)).filter(
        TravelRequest.id == request_id, TravelRequest.is_deleted == False)  # noqa: E712
    if lock:
        q = q.with_for_update(of=TravelRequest)
    req = q.first()
    if not req:
        raise HTTPException(404, "Travel request not found")
    return req


def _resp(db: Session, req: TravelRequest) -> dict:
    return to_response(db, req)


def _emp_uid(db: Session, employee_id) -> Optional[UUID]:
    return db.query(Employee.user_id).filter(Employee.id == employee_id).scalar()


def _booking_resp(db: Session, b: TravelBooking) -> dict:
    ref = db.query(TravelRequest.travel_reference_number).filter(TravelRequest.id == b.travel_request_id).scalar()
    return {
        "id": b.id, "booking_number": b.booking_number, "travel_request_id": b.travel_request_id,
        "travel_reference_number": ref, "booking_type": b.booking_type, "vendor": b.vendor,
        "booking_date": b.booking_date, "travel_date": b.travel_date, "pnr_number": b.pnr_number,
        "ticket_number": b.ticket_number, "airline": b.airline, "train_number": b.train_number,
        "seat_number": b.seat_number, "from_place": b.from_place, "to_place": b.to_place,
        "hotel_name": b.hotel_name, "check_in": b.check_in, "check_out": b.check_out,
        "num_nights": b.num_nights, "booking_cost": b.booking_cost, "taxes": b.taxes,
        "total_cost": b.total_cost, "currency": b.currency, "status": b.status, "notes": b.notes,
        "created_at": b.created_at,
    }


def _advance_resp(db: Session, a: TravelAdvance) -> dict:
    ref = db.query(TravelRequest.travel_reference_number).filter(TravelRequest.id == a.travel_request_id).scalar()
    emp = db.query(User.full_name).join(Employee, Employee.user_id == User.id).filter(
        Employee.id == a.employee_id).scalar()
    return {
        "id": a.id, "advance_number": a.advance_number, "travel_request_id": a.travel_request_id,
        "travel_reference_number": ref, "employee_id": a.employee_id, "employee_name": emp,
        "advance_amount": a.advance_amount, "approved_amount": a.approved_amount, "currency": a.currency,
        "purpose": a.purpose, "status": a.status, "approved_at": a.approved_at,
        "disbursement_method": a.disbursement_method, "disbursement_reference": a.disbursement_reference,
        "released_at": a.released_at, "settled_at": a.settled_at, "recovered_amount": a.recovered_amount,
        "reject_reason": a.reject_reason, "payroll_ref": a.payroll_ref, "created_at": a.created_at,
    }


def _da_resp(db: Session, d: TravelDaRecord) -> dict:
    from app.models.hr.grade import Grade
    rq = db.query(TravelRequest.travel_reference_number, TravelRequest.status).filter(
        TravelRequest.id == d.travel_request_id).first()
    ref = rq[0] if rq else None
    rstatus = rq[1].value if rq and rq[1] else None
    emp = db.query(User.full_name).join(Employee, Employee.user_id == User.id).filter(
        Employee.id == d.employee_id).scalar()
    gname = db.query(Grade.name).filter(Grade.id == d.grade_id).scalar() if d.grade_id else None
    # The DA is paid AS PART OF the settlement; the disbursement method lives there,
    # not on the DA row. Surface it so the UI shows the real method (cash/bank/
    # payroll) instead of assuming payroll. travel_request_id is unique on the
    # settlement, so this is at most one row.
    st = db.query(
        TravelSettlement.settlement_method, TravelSettlement.status,
        TravelSettlement.paid_at, TravelSettlement.settled_at,
    ).filter(TravelSettlement.travel_request_id == d.travel_request_id).first()
    s_method = st[0].value if st and st[0] else None
    s_status = st[1].value if st and st[1] else None
    s_paid_at = (st[2] or st[3]) if st else None
    return {
        "id": d.id, "travel_request_id": d.travel_request_id, "travel_reference_number": ref,
        "employee_id": d.employee_id, "employee_name": emp, "grade_id": d.grade_id, "grade_name": gname,
        "city_category": d.city_category, "travel_days": d.travel_days, "daily_rate": d.daily_rate,
        "eligible_da": d.eligible_da, "approved_da": d.approved_da, "currency": d.currency,
        "status": d.status, "request_status": rstatus, "computed_at": d.computed_at,
        "approved_at": d.approved_at, "payroll_ref": d.payroll_ref,
        "settlement_method": s_method, "settlement_status": s_status, "paid_at": s_paid_at,
    }


def _settlement_resp(db: Session, s: TravelSettlement) -> dict:
    ref = db.query(TravelRequest.travel_reference_number).filter(TravelRequest.id == s.travel_request_id).scalar()
    emp = db.query(User.full_name).join(Employee, Employee.user_id == User.id).filter(
        Employee.id == s.employee_id).scalar()
    return {
        "id": s.id, "settlement_number": s.settlement_number, "travel_request_id": s.travel_request_id,
        "travel_reference_number": ref, "employee_id": s.employee_id, "employee_name": emp,
        "expense_lines": s.expense_lines or [], "advance_received": s.advance_received,
        "total_expense": s.total_expense, "approved_expense": s.approved_expense, "da_amount": s.da_amount,
        "payable_amount": s.payable_amount, "recoverable_amount": s.recoverable_amount,
        "currency": s.currency, "status": s.status, "settlement_method": s.settlement_method,
        "submitted_at": s.submitted_at, "verified_at": s.verified_at, "settled_at": s.settled_at,
        "paid_at": s.paid_at, "payroll_ref": s.payroll_ref, "reversal_reason": s.reversal_reason,
        "created_at": s.created_at,
    }


def _notify(db: Session, req: TravelRequest, event: str, actor: User, next_approver=None):
    try:
        emit_notifications(db, req, employee_user_id=_emp_uid(db, req.employee_id), event=event,
                           actor=actor, next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()


# ─────────────────────────── list + dashboard ───────────────────────────

@router.get("/", response_model=TravelRequestListResponse)
def list_requests(
    status: Optional[TravelRequestStatus] = None,
    category_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    travel_type: Optional[str] = None,
    project_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1, le=100000),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    query = db.query(TravelRequest).options(joinedload(TravelRequest.category)).filter(
        TravelRequest.is_deleted == False)  # noqa: E712
    if status:
        query = query.filter(TravelRequest.status == status)
    if category_id:
        query = query.filter(TravelRequest.category_id == category_id)
    if employee_id:
        query = query.filter(TravelRequest.employee_id == employee_id)
    if department_id:
        query = query.filter(TravelRequest.department_id == department_id)
    if travel_type:
        query = query.filter(TravelRequest.travel_type == travel_type)
    if project_id:
        query = query.filter(TravelRequest.project_id == project_id)
    if date_from:
        query = query.filter(TravelRequest.departure_date >= date_from)
    if date_to:
        query = query.filter(TravelRequest.departure_date <= date_to)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            TravelRequest.travel_reference_number.ilike(like),
            TravelRequest.purpose.ilike(like),
            TravelRequest.to_location.ilike(like),
            TravelRequest.from_location.ilike(like),
        ))
    total = query.count()
    rows = query.order_by(TravelRequest.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return TravelRequestListResponse(
        items=[_resp(db, r) for r in rows], total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1))


@router.get("/stats", response_model=TravelStats)
def stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    base = db.query(TravelRequest).filter(TravelRequest.is_deleted == False)  # noqa: E712
    today = date.today()
    month_start = date(today.year, today.month, 1)

    total = base.count()
    pending = base.filter(TravelRequest.status == TravelRequestStatus.PENDING_APPROVAL).count()
    active = base.filter(TravelRequest.status == TravelRequestStatus.IN_PROGRESS).count()
    completed = base.filter(TravelRequest.status == TravelRequestStatus.COMPLETED).count()
    upcoming = base.filter(
        TravelRequest.status == TravelRequestStatus.APPROVED,
        TravelRequest.departure_date >= today).count()
    requests_month = base.filter(TravelRequest.created_at >= datetime(
        month_start.year, month_start.month, 1, tzinfo=timezone.utc)).count()

    total_cost = db.query(sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0)).filter(
        TravelRequest.is_deleted == False,  # noqa: E712
        TravelRequest.status.notin_([TravelRequestStatus.REJECTED, TravelRequestStatus.CANCELLED]),
    ).scalar() or 0

    advances_out = db.query(sa_func.coalesce(sa_func.sum(
        sa_func.coalesce(TravelAdvance.approved_amount, TravelAdvance.advance_amount)), 0)).filter(
        TravelAdvance.is_deleted == False,  # noqa: E712
        TravelAdvance.status == AdvanceStatus.RELEASED).scalar() or 0

    settlements_pending = db.query(TravelSettlement).filter(
        TravelSettlement.is_deleted == False,  # noqa: E712
        TravelSettlement.status.in_([TravelSettlementStatus.SUBMITTED, TravelSettlementStatus.VERIFIED]),
    ).count()

    da_payable = db.query(sa_func.coalesce(sa_func.sum(
        sa_func.coalesce(TravelDaRecord.approved_da, TravelDaRecord.eligible_da)), 0)).filter(
        TravelDaRecord.is_deleted == False,  # noqa: E712
        TravelDaRecord.status == DaRecordStatus.APPROVED).scalar() or 0

    # Actual spend = company-paid bookings + reimbursed (settled) expenses + finalised DA.
    total_booked_cost = db.query(sa_func.coalesce(sa_func.sum(TravelBooking.total_cost), 0)).filter(
        TravelBooking.is_deleted == False,  # noqa: E712
        TravelBooking.status != BookingStatus.CANCELLED).scalar() or 0
    reimbursed_total = db.query(sa_func.coalesce(sa_func.sum(TravelSettlement.approved_expense), 0)).filter(
        TravelSettlement.is_deleted == False,  # noqa: E712
        TravelSettlement.status.in_([TravelSettlementStatus.SETTLED, TravelSettlementStatus.PAID])).scalar() or 0
    da_actual_total = db.query(sa_func.coalesce(sa_func.sum(
        sa_func.coalesce(TravelDaRecord.approved_da, TravelDaRecord.eligible_da)), 0)).filter(
        TravelDaRecord.is_deleted == False,  # noqa: E712
        TravelDaRecord.status.in_([DaRecordStatus.APPROVED, DaRecordStatus.PAID])).scalar() or 0
    total_actual_cost = Decimal(str(total_booked_cost)) + Decimal(str(reimbursed_total)) + Decimal(str(da_actual_total))

    # By status
    status_rows = (
        db.query(TravelRequest.status, sa_func.count(TravelRequest.id),
                 sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0))
        .filter(TravelRequest.is_deleted == False)  # noqa: E712
        .group_by(TravelRequest.status).all()
    )
    by_status = [{"status": r[0].value, "count": r[1], "amount": r[2]} for r in status_rows]

    # By type
    type_rows = (
        db.query(TravelRequest.travel_type, sa_func.count(TravelRequest.id),
                 sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0))
        .filter(TravelRequest.is_deleted == False)  # noqa: E712
        .group_by(TravelRequest.travel_type).all()
    )
    by_type = [{"travel_type": r[0] or "Unspecified", "count": r[1], "amount": r[2]} for r in type_rows]

    # By department
    dept_rows = (
        db.query(Department.id, Department.name, sa_func.count(TravelRequest.id),
                 sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0))
        .join(TravelRequest, TravelRequest.department_id == Department.id)
        .filter(TravelRequest.is_deleted == False)  # noqa: E712
        .group_by(Department.id, Department.name).all()
    )
    by_department = [{"department_id": r[0], "department_name": r[1], "count": r[2], "amount": r[3]}
                     for r in dept_rows]

    # Monthly trend (last 6 months by departure_date)
    monthly = []
    for i in range(5, -1, -1):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        ms = date(y, m, 1)
        me = date(y + (m // 12), (m % 12) + 1, 1)
        est = db.query(sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0)).filter(
            TravelRequest.is_deleted == False, TravelRequest.departure_date >= ms,  # noqa: E712
            TravelRequest.departure_date < me).scalar() or 0
        settled = db.query(sa_func.coalesce(sa_func.sum(TravelSettlement.payable_amount), 0)).filter(
            TravelSettlement.is_deleted == False,  # noqa: E712
            TravelSettlement.status.in_([TravelSettlementStatus.SETTLED, TravelSettlementStatus.PAID]),
            TravelSettlement.settled_at >= datetime(ms.year, ms.month, ms.day, tzinfo=timezone.utc),
            TravelSettlement.settled_at < datetime(me.year, me.month, me.day, tzinfo=timezone.utc)).scalar() or 0
        cnt = db.query(sa_func.count(TravelRequest.id)).filter(
            TravelRequest.is_deleted == False, TravelRequest.departure_date >= ms,  # noqa: E712
            TravelRequest.departure_date < me).scalar() or 0
        monthly.append({"month": f"{y}-{m:02d}", "estimated": est, "settled": settled, "count": cnt})

    # Top routes
    route_rows = (
        db.query(TravelRequest.from_location, TravelRequest.to_location, sa_func.count(TravelRequest.id))
        .filter(TravelRequest.is_deleted == False)  # noqa: E712
        .group_by(TravelRequest.from_location, TravelRequest.to_location)
        .order_by(sa_func.count(TravelRequest.id).desc()).limit(6).all()
    )
    top_routes = [{"route": f"{r[0]} → {r[1]}", "count": r[2]} for r in route_rows]

    settlement_split = {
        "payable": Decimal(str(db.query(sa_func.coalesce(sa_func.sum(TravelSettlement.payable_amount), 0)).filter(
            TravelSettlement.is_deleted == False,  # noqa: E712
            TravelSettlement.status.in_([TravelSettlementStatus.SETTLED, TravelSettlementStatus.PAID])).scalar() or 0)),
        "recoverable": Decimal(str(db.query(sa_func.coalesce(sa_func.sum(TravelSettlement.recoverable_amount), 0)).filter(
            TravelSettlement.is_deleted == False,  # noqa: E712
            TravelSettlement.status.in_([TravelSettlementStatus.SETTLED, TravelSettlementStatus.PAID])).scalar() or 0)),
    }

    # Avg approval time (submitted → approved)
    avg_days = None
    rows = db.query(TravelRequest.submitted_at, TravelRequest.approved_at).filter(
        TravelRequest.is_deleted == False, TravelRequest.approved_at.isnot(None),  # noqa: E712
        TravelRequest.submitted_at.isnot(None)).all()
    spans = [(r.approved_at - r.submitted_at).total_seconds() / 86400.0 for r in rows
             if r.approved_at and r.submitted_at]
    if spans:
        avg_days = round(sum(spans) / len(spans), 1)

    return TravelStats(
        active_tours=active, pending_approvals=pending, upcoming_travels=upcoming,
        total_travel_cost=total_cost, total_booked_cost=Decimal(str(total_booked_cost)),
        total_actual_cost=total_actual_cost, advances_outstanding=advances_out,
        settlements_pending=settlements_pending, da_payable=da_payable,
        budget_utilization=0.0, total_requests=total, completed_tours=completed,
        requests_this_month=requests_month, avg_approval_days=avg_days,
        by_status=by_status, by_type=by_type, by_department=by_department,
        monthly_trend=monthly, top_routes=top_routes, settlement_split=settlement_split,
    )


@router.get("/queue", response_model=TravelRequestListResponse)
def approval_queue(page: int = Query(1, ge=1, le=100000), limit: int = Query(50, ge=1, le=200),
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    rows = db.query(TravelRequest).options(joinedload(TravelRequest.category)).filter(
        TravelRequest.is_deleted == False,  # noqa: E712
        TravelRequest.status == TravelRequestStatus.PENDING_APPROVAL,
    ).order_by(TravelRequest.submitted_at.asc()).all()
    actionable = []
    for r in rows:
        steps = list(r.approval_steps or [])
        idx = int(r.current_step or 0)
        if 0 <= idx < len(steps) and can_act_on_step(current_user, steps[idx]):
            actionable.append(r)
    total = len(actionable)
    paged = actionable[(page - 1) * limit: (page - 1) * limit + limit]
    return TravelRequestListResponse(items=[_resp(db, c) for c in paged], total=total, page=page,
                                     limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


@router.get("/calendar", response_model=CalendarResponse)
def calendar(date_from: Optional[date] = None, date_to: Optional[date] = None,
             department_id: Optional[UUID] = None, project_id: Optional[UUID] = None,
             db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    today = date.today()
    df = date_from or date(today.year, today.month, 1)
    dt = date_to or (df + timedelta(days=120))
    query = db.query(TravelRequest).filter(
        TravelRequest.is_deleted == False,  # noqa: E712
        TravelRequest.status.notin_([TravelRequestStatus.DRAFT, TravelRequestStatus.REJECTED, TravelRequestStatus.CANCELLED]),
        TravelRequest.return_date >= df, TravelRequest.departure_date <= dt,
    )
    if department_id:
        query = query.filter(TravelRequest.department_id == department_id)
    if project_id:
        query = query.filter(TravelRequest.project_id == project_id)
    rows = query.order_by(TravelRequest.departure_date.asc()).all()
    items = []
    for r in rows:
        snap = db.query(User.full_name, Department.name).select_from(Employee).join(
            User, User.id == Employee.user_id).outerjoin(
            Department, Department.id == Employee.department_id).filter(
            Employee.id == r.employee_id).first()
        items.append({
            "id": r.id, "travel_reference_number": r.travel_reference_number,
            "employee_name": snap[0] if snap else None, "department": snap[1] if snap else None,
            "travel_type": r.travel_type, "from_location": r.from_location, "to_location": r.to_location,
            "departure_date": r.departure_date, "return_date": r.return_date,
            "status": r.status, "priority": r.priority,
        })
    return CalendarResponse(items=items)


@router.get("/audit", response_model=TravelAuditListResponse)
def list_audit(travel_request_id: Optional[UUID] = None, action: Optional[TravelAuditAction] = None,
               entity_type: Optional[str] = None, page: int = Query(1, ge=1, le=100000),
               limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
               current_user: User = Depends(get_current_superuser)):
    query = db.query(TravelAuditLog)
    if travel_request_id:
        query = query.filter(TravelAuditLog.travel_request_id == travel_request_id)
    if action:
        query = query.filter(TravelAuditLog.action == action)
    if entity_type:
        query = query.filter(TravelAuditLog.entity_type == entity_type)
    total = query.count()
    rows = query.order_by(TravelAuditLog.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    actor_ids = {r.actor_id for r in rows if r.actor_id}
    req_ids = {r.travel_request_id for r in rows if r.travel_request_id}
    names = {u.id: u.full_name for u in db.query(User.id, User.full_name).filter(User.id.in_(actor_ids))} if actor_ids else {}
    refs = {t.id: t.travel_reference_number for t in db.query(TravelRequest.id, TravelRequest.travel_reference_number).filter(TravelRequest.id.in_(req_ids))} if req_ids else {}
    items = [{
        "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id, "action": r.action.value,
        "travel_request_id": r.travel_request_id, "travel_reference_number": refs.get(r.travel_request_id),
        "actor_id": r.actor_id, "actor_name": names.get(r.actor_id), "from_status": r.from_status,
        "to_status": r.to_status, "note": r.note, "created_at": r.created_at,
    } for r in rows]
    return TravelAuditListResponse(items=items, total=total)


@router.get("/approver-candidates", response_model=ApproverCandidateListResponse)
def approver_candidates(q: Optional[str] = None, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_superuser)):
    query = db.query(User).filter(User.is_active == True)  # noqa: E712
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
    rows = query.order_by(User.full_name.asc()).limit(50).all()
    return ApproverCandidateListResponse(items=[
        {"id": u.id, "name": u.full_name, "email": u.email, "is_superuser": u.is_superuser} for u in rows])


# ─────────────────────────── bookings ───────────────────────────

@router.get("/bookings", response_model=BookingListResponse)
def list_bookings(travel_request_id: Optional[UUID] = None, booking_type=None, status: Optional[BookingStatus] = None,
                  page: int = Query(1, ge=1, le=100000), limit: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    query = db.query(TravelBooking).filter(TravelBooking.is_deleted == False)  # noqa: E712
    if travel_request_id:
        query = query.filter(TravelBooking.travel_request_id == travel_request_id)
    if status:
        query = query.filter(TravelBooking.status == status)
    total = query.count()
    rows = query.order_by(TravelBooking.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return BookingListResponse(items=[_booking_resp(db, b) for b in rows], total=total, page=page,
                               limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


# A finalized settlement freezes the tour's books — its bookings feed a
# reconciliation that's already settled/paid, so they can no longer be mutated.
_CLOSED_SETTLEMENT_STATES = (
    TravelSettlementStatus.SETTLED, TravelSettlementStatus.PAID, TravelSettlementStatus.REVERSED)


def _locked_settlement(db: Session, travel_request_id):
    return db.query(TravelSettlement).filter(
        TravelSettlement.travel_request_id == travel_request_id,
        TravelSettlement.is_deleted == False,  # noqa: E712
        TravelSettlement.status.in_(_CLOSED_SETTLEMENT_STATES)).first()


def _guard_bookings_open(db: Session, travel_request_id, verb: str = "changed"):
    locked = _locked_settlement(db, travel_request_id)
    if locked:
        raise HTTPException(
            409,
            f"This tour's settlement ({locked.settlement_number}) is already "
            f"{locked.status.value.lower()} — its books are closed, so bookings can't be {verb}.")


@router.post("/bookings", response_model=BookingResponse, status_code=201)
def create_booking(payload: BookingCreate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, payload.travel_request_id, lock=True)
    if req.status in (TravelRequestStatus.DRAFT, TravelRequestStatus.REJECTED, TravelRequestStatus.CANCELLED):
        raise HTTPException(409, "Bookings can only be added to an approved/active travel request")
    _guard_bookings_open(db, req.id, "added")
    total = Decimal(str(payload.booking_cost or 0)) + Decimal(str(payload.taxes or 0))
    if total <= 0:
        raise HTTPException(422, "A booking must have a fare or taxes greater than zero")
    b = TravelBooking(
        booking_number=generate_booking_number(db), travel_request_id=req.id,
        booking_type=payload.booking_type, vendor=payload.vendor, booking_date=payload.booking_date,
        travel_date=payload.travel_date, pnr_number=payload.pnr_number, ticket_number=payload.ticket_number,
        airline=payload.airline, train_number=payload.train_number, seat_number=payload.seat_number,
        from_place=payload.from_place, to_place=payload.to_place, hotel_name=payload.hotel_name,
        check_in=payload.check_in, check_out=payload.check_out, num_nights=payload.num_nights,
        booking_cost=payload.booking_cost, taxes=payload.taxes, total_cost=total,
        currency=payload.currency, status=payload.status, notes=payload.notes, created_by_id=current_user.id,
    )
    db.add(b)
    db.flush()
    write_travel_audit(db, entity_type="BOOKING", entity_id=b.id, travel_request_id=req.id,
                       action=TravelAuditAction.BOOK, actor_id=current_user.id,
                       note=f"{b.booking_type.value} {b.booking_number}")
    db.commit()
    db.refresh(b)
    return _booking_resp(db, b)


@router.patch("/bookings/{booking_id}", response_model=BookingResponse)
def update_booking(booking_id: UUID, payload: BookingUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    b = db.query(TravelBooking).filter(
        TravelBooking.id == booking_id, TravelBooking.is_deleted == False).first()  # noqa: E712
    if not b:
        raise HTTPException(404, "Booking not found")
    _guard_bookings_open(db, b.travel_request_id, "edited")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(b, k, v)
    b.total_cost = Decimal(str(b.booking_cost or 0)) + Decimal(str(b.taxes or 0))
    if b.total_cost <= 0:
        raise HTTPException(422, "A booking must have a fare or taxes greater than zero")
    write_travel_audit(db, entity_type="BOOKING", entity_id=b.id, travel_request_id=b.travel_request_id,
                       action=TravelAuditAction.BOOKING_UPDATE, actor_id=current_user.id)
    db.commit()
    db.refresh(b)
    return _booking_resp(db, b)


@router.delete("/bookings/{booking_id}")
def delete_booking(booking_id: UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    b = db.query(TravelBooking).filter(
        TravelBooking.id == booking_id, TravelBooking.is_deleted == False).first()  # noqa: E712
    if not b:
        raise HTTPException(404, "Booking not found")
    _guard_bookings_open(db, b.travel_request_id, "removed")
    b.is_deleted = True
    b.status = BookingStatus.CANCELLED
    write_travel_audit(db, entity_type="BOOKING", entity_id=b.id, travel_request_id=b.travel_request_id,
                       action=TravelAuditAction.BOOKING_CANCEL, actor_id=current_user.id)
    db.commit()
    return {"success": True}


# ─────────────────────────── advances ───────────────────────────

@router.get("/advances", response_model=AdvanceListResponse)
def list_advances(status: Optional[AdvanceStatus] = None, travel_request_id: Optional[UUID] = None,
                  page: int = Query(1, ge=1, le=100000), limit: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    query = db.query(TravelAdvance).filter(TravelAdvance.is_deleted == False)  # noqa: E712
    if status:
        query = query.filter(TravelAdvance.status == status)
    if travel_request_id:
        query = query.filter(TravelAdvance.travel_request_id == travel_request_id)
    total = query.count()
    rows = query.order_by(TravelAdvance.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AdvanceListResponse(items=[_advance_resp(db, a) for a in rows], total=total, page=page,
                               limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


@router.get("/advances/{advance_id}/detail", response_model=AdvanceDetailResponse)
def get_advance_detail(advance_id: UUID, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_superuser)):
    """Full single-advance view for the admin drawer — the advance plus its acting
    users, ceiling context, the parent trip, and the rest of the tour's money."""
    a = _get_advance(db, advance_id)
    out = _advance_resp(db, a)

    def _uname(uid):
        return db.query(User.full_name).filter(User.id == uid).scalar() if uid else None

    out["requested_by_name"] = _uname(a.created_by_id)
    out["approved_by_name"] = _uname(a.approved_by_id)
    out["released_by_name"] = _uname(a.released_by_id)
    out["notes"] = a.notes
    out["updated_at"] = a.updated_at

    req = db.query(TravelRequest).filter(TravelRequest.id == a.travel_request_id).first()
    if req:
        est = Decimal(str(req.est_total_cost or 0))
        policy = get_policy_for(db, grade_id=req.grade_id)
        plimit = Decimal(str(policy.advance_limit)) if (policy and policy.advance_limit is not None) else None
        ceiling = _advance_ceiling(db, req)
        out["advance_ceiling"] = ceiling
        if ceiling is not None:
            out["ceiling_source"] = "trip estimate" if (est > 0 and (plimit is None or est <= plimit)) else "grade policy"
        out["department_name"] = (db.query(Department.name).filter(Department.id == req.department_id).scalar()
                                  if req.department_id else None)
        out["trip"] = {
            "from_location": req.from_location, "to_location": req.to_location,
            "departure_date": req.departure_date, "return_date": req.return_date,
            "num_days": req.num_days, "trip_type": req.trip_type, "travel_type": req.travel_type,
            "priority": req.priority.value if req.priority else None, "purpose": req.purpose,
            "est_total_cost": req.est_total_cost, "city_category": req.to_city_category,
            "status": req.status, "project_id": req.project_id, "cost_center": req.cost_center,
            "budget_head": req.budget_head, "funding_source": req.funding_source,
        }

    # the rest of the tour's money — keyed off the same travel request.
    # Build explicit mini-dicts (mirrors app/utils/hr/travel/service.to_response).
    da = (db.query(TravelDaRecord)
          .filter(TravelDaRecord.travel_request_id == a.travel_request_id, TravelDaRecord.is_deleted == False)  # noqa: E712
          .order_by(TravelDaRecord.created_at.desc()).first())
    if da:
        out["da"] = {"id": da.id, "travel_days": da.travel_days, "daily_rate": da.daily_rate,
                     "eligible_da": da.eligible_da, "approved_da": da.approved_da,
                     "city_category": da.city_category, "status": da.status}
    st = (db.query(TravelSettlement)
          .filter(TravelSettlement.travel_request_id == a.travel_request_id, TravelSettlement.is_deleted == False)  # noqa: E712
          .order_by(TravelSettlement.created_at.desc()).first())
    if st:
        out["settlement"] = {"id": st.id, "settlement_number": st.settlement_number,
                             "payable_amount": st.payable_amount, "recoverable_amount": st.recoverable_amount,
                             "status": st.status}
    bks = (db.query(TravelBooking)
           .filter(TravelBooking.travel_request_id == a.travel_request_id, TravelBooking.is_deleted == False)  # noqa: E712
           .all())
    out["booking_count"] = len(bks)
    out["booking_total"] = sum((Decimal(str(b.total_cost or 0)) for b in bks), Decimal("0"))

    return out


def _advance_ceiling(db: Session, req: TravelRequest) -> Optional[Decimal]:
    """A travel advance must not exceed the trip's estimated cost, nor the grade's
    policy advance_limit. Returns the tightest applicable ceiling (None = uncapped)."""
    ceilings = []
    est = Decimal(str(req.est_total_cost or 0))
    if est > 0:
        ceilings.append(est)
    policy = get_policy_for(db, grade_id=req.grade_id)
    if policy and policy.advance_limit is not None:
        ceilings.append(Decimal(str(policy.advance_limit)))
    return min(ceilings) if ceilings else None


def _check_advance_amount(db: Session, req: TravelRequest, amount) -> None:
    ceiling = _advance_ceiling(db, req)
    amt = Decimal(str(amount or 0))
    if amt <= 0:
        raise HTTPException(422, "Advance amount must be greater than zero")
    if ceiling is not None and amt > ceiling:
        raise HTTPException(
            422, f"Advance ₹{amt:,.0f} exceeds the allowed ceiling ₹{ceiling:,.0f} "
                 f"(capped at the trip estimate / grade policy limit).")


@router.post("/advances", response_model=AdvanceResponse, status_code=201)
def create_advance(payload: AdvanceCreate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, payload.travel_request_id)
    # No travel cash advance for a trip departing after the employee's LWD.
    _adv_emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    guard_within_tenure(_adv_emp, req.departure_date, "issue a travel advance")
    _check_advance_amount(db, req, payload.advance_amount)
    a = TravelAdvance(
        advance_number=generate_advance_number(db), travel_request_id=req.id, employee_id=req.employee_id,
        advance_amount=payload.advance_amount, currency=payload.currency, purpose=payload.purpose,
        status=AdvanceStatus.REQUESTED, created_by_id=current_user.id,
    )
    db.add(a)
    db.flush()
    write_travel_audit(db, entity_type="ADVANCE", entity_id=a.id, travel_request_id=req.id,
                       action=TravelAuditAction.ADVANCE_REQUEST, actor_id=current_user.id,
                       note=f"Advance {a.advance_number}")
    db.commit()
    db.refresh(a)
    return _advance_resp(db, a)


def _get_advance(db: Session, advance_id: UUID, *, lock: bool = False) -> TravelAdvance:
    q = db.query(TravelAdvance).filter(TravelAdvance.id == advance_id, TravelAdvance.is_deleted == False)  # noqa: E712
    if lock:
        q = q.with_for_update()
    a = q.first()
    if not a:
        raise HTTPException(404, "Advance not found")
    return a


@router.post("/advances/{advance_id}/approve", response_model=AdvanceResponse)
def approve_advance(advance_id: UUID, body: AdvanceDecisionBody, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    a = _get_advance(db, advance_id, lock=True)
    if a.status != AdvanceStatus.REQUESTED:
        raise HTTPException(409, f"A {a.status.value} advance cannot be approved")
    effective = body.approved_amount or a.advance_amount
    _check_advance_amount(db, _get_req(db, a.travel_request_id), effective)
    a.approved_amount = effective
    a.status = AdvanceStatus.APPROVED
    a.approved_at = datetime.now(timezone.utc)
    a.approved_by_id = current_user.id
    if body.disbursement_method is not None:
        a.disbursement_method = body.disbursement_method
    write_travel_audit(db, entity_type="ADVANCE", entity_id=a.id, travel_request_id=a.travel_request_id,
                       action=TravelAuditAction.ADVANCE_APPROVE, actor_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(a)
    return _advance_resp(db, a)


@router.post("/advances/{advance_id}/reject", response_model=AdvanceResponse)
def reject_advance(advance_id: UUID, body: AdvanceRejectBody, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    a = _get_advance(db, advance_id, lock=True)
    if a.status not in (AdvanceStatus.REQUESTED, AdvanceStatus.APPROVED):
        raise HTTPException(409, f"A {a.status.value} advance cannot be rejected")
    a.status = AdvanceStatus.REJECTED
    a.reject_reason = body.reason
    write_travel_audit(db, entity_type="ADVANCE", entity_id=a.id, travel_request_id=a.travel_request_id,
                       action=TravelAuditAction.ADVANCE_REJECT, actor_id=current_user.id, note=body.reason)
    db.commit()
    db.refresh(a)
    return _advance_resp(db, a)


@router.post("/advances/{advance_id}/release", response_model=AdvanceResponse)
def release_advance(advance_id: UUID, body: AdvanceReleaseBody, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    """Disburse the approved advance. PAYROLL posts a TRAVEL_ADVANCE (non-taxable)
    adjustment to a salary run; BANK_TRANSFER / CASH / CHEQUE are paid directly by
    treasury with a reference and no payroll posting. Either way the advance reaches
    RELEASED and is recovered against the final settlement."""
    a = _get_advance(db, advance_id, lock=True)
    if a.status != AdvanceStatus.APPROVED:
        raise HTTPException(409, f"Only an APPROVED advance can be released (status {a.status.value})")
    amount = a.approved_amount or a.advance_amount
    req = _get_req(db, a.travel_request_id)
    # method: an explicit release-time choice wins, else the one set at approval, else PAYROLL
    method = body.disbursement_method or a.disbursement_method or TravelSettlementMethod.PAYROLL
    a.disbursement_method = method
    if method == TravelSettlementMethod.PAYROLL:
        adj = post_adjustment(
            db, employee_id=a.employee_id, sub_type=f"TRAVEL_ADVANCE:{req.travel_reference_number}",
            title=f"Travel advance · {req.travel_reference_number}", amount=amount,
            is_deduction=False, is_taxable=False, period_month=body.period_month,
            period_year=body.period_year, actor=current_user, reason=body.note)
        a.payroll_adjustment_id = adj.id
        note_txt = f"Released {amount} to payroll"
    else:
        # direct disbursement — treasury pays now, no payroll posting
        a.disbursement_reference = body.disbursement_reference
        ref = f" (ref {body.disbursement_reference})" if body.disbursement_reference else ""
        note_txt = f"Disbursed {amount} via {method.value}{ref}"
    a.status = AdvanceStatus.RELEASED
    a.released_at = datetime.now(timezone.utc)
    a.released_by_id = current_user.id
    write_travel_audit(db, entity_type="ADVANCE", entity_id=a.id, travel_request_id=a.travel_request_id,
                       action=TravelAuditAction.ADVANCE_RELEASE, actor_id=current_user.id,
                       note=note_txt)
    # Re-sync an already-open settlement so its advance snapshot isn't stale.
    resync_settlement(db, a.travel_request_id)
    db.commit()
    db.refresh(a)
    _notify(db, req, "advance_released", current_user)
    return _advance_resp(db, a)


# ─────────────────────────── DA ───────────────────────────

@router.get("/da", response_model=DaListResponse)
def list_da(status: Optional[DaRecordStatus] = None, page: int = Query(1, ge=1, le=100000),
            limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
            current_user: User = Depends(get_current_superuser)):
    query = db.query(TravelDaRecord).filter(TravelDaRecord.is_deleted == False)  # noqa: E712
    if status:
        query = query.filter(TravelDaRecord.status == status)
    total = query.count()
    rows = query.order_by(TravelDaRecord.computed_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return DaListResponse(items=[_da_resp(db, d) for d in rows], total=total, page=page,
                          limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


@router.post("/{request_id:uuid}/da/compute", response_model=DaRecordResponse)
def compute_request_da(request_id: UUID, body: DaComputeBody, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, request_id)
    if req.status in (TravelRequestStatus.DRAFT, TravelRequestStatus.PENDING_APPROVAL,
                      TravelRequestStatus.REJECTED, TravelRequestStatus.CANCELLED, TravelRequestStatus.RETURNED):
        raise HTTPException(409, "DA can only be computed for an approved/in-progress/completed tour")
    rec = compute_da(db, req, actor=current_user, city_category=body.city_category,
                     travel_days=body.travel_days, daily_rate_override=body.daily_rate,
                     override_reason=body.override_reason)
    db.commit()
    db.refresh(rec)
    return _da_resp(db, rec)


@router.post("/da/{da_id}/approve", response_model=DaRecordResponse)
def approve_da(da_id: UUID, body: DaApproveBody, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_superuser)):
    d = db.query(TravelDaRecord).filter(
        TravelDaRecord.id == da_id, TravelDaRecord.is_deleted == False).with_for_update().first()  # noqa: E712
    if not d:
        raise HTTPException(404, "DA record not found")
    if d.status != DaRecordStatus.COMPUTED:
        raise HTTPException(409, f"A {d.status.value} DA record cannot be approved")
    # DA is only an estimate until the trip is over — finalise it on actual completion.
    rstatus = db.query(TravelRequest.status).filter(TravelRequest.id == d.travel_request_id).scalar()
    if rstatus != TravelRequestStatus.COMPLETED:
        raise HTTPException(409, "DA can only be approved after the tour is marked completed")
    d.approved_da = body.approved_da if body.approved_da is not None else d.eligible_da
    d.status = DaRecordStatus.APPROVED
    d.approved_at = datetime.now(timezone.utc)
    d.approved_by_id = current_user.id
    write_travel_audit(db, entity_type="DA", entity_id=d.id, travel_request_id=d.travel_request_id,
                       action=TravelAuditAction.DA_APPROVE, actor_id=current_user.id,
                       note=f"DA approved: {d.approved_da}")
    # Keep the tour's open settlement in step — its DA snapshot is otherwise stale
    # if expenses were filed before the DA was approved.
    resync_settlement(db, d.travel_request_id)
    db.commit()
    db.refresh(d)
    return _da_resp(db, d)


# ─────────────────────────── settlements ───────────────────────────

@router.get("/settlements", response_model=SettlementListResponse)
def list_settlements(status: Optional[TravelSettlementStatus] = None, page: int = Query(1, ge=1, le=100000),
                     limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_superuser)):
    query = db.query(TravelSettlement).filter(TravelSettlement.is_deleted == False)  # noqa: E712
    if status:
        query = query.filter(TravelSettlement.status == status)
    total = query.count()
    rows = query.order_by(TravelSettlement.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return SettlementListResponse(items=[_settlement_resp(db, s) for s in rows], total=total, page=page,
                                  limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


def _get_settlement(db: Session, settlement_id: UUID, *, lock: bool = False) -> TravelSettlement:
    q = db.query(TravelSettlement).filter(
        TravelSettlement.id == settlement_id, TravelSettlement.is_deleted == False)  # noqa: E712
    if lock:
        q = q.with_for_update()
    s = q.first()
    if not s:
        raise HTTPException(404, "Settlement not found")
    return s


@router.get("/settlements/{settlement_id}", response_model=SettlementResponse)
def get_settlement(settlement_id: UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    return _settlement_resp(db, _get_settlement(db, settlement_id))


@router.post("/settlements/{settlement_id}/verify", response_model=SettlementResponse)
def verify_settlement(settlement_id: UUID, body: SettlementVerifyBody, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_superuser)):
    s = _get_settlement(db, settlement_id, lock=True)
    if s.status != TravelSettlementStatus.SUBMITTED:
        raise HTTPException(409, f"Only a SUBMITTED settlement can be verified (status {s.status.value})")
    reconcile(db, s, approved_expense=body.approved_expense)
    s.status = TravelSettlementStatus.VERIFIED
    s.verified_at = datetime.now(timezone.utc)
    s.verified_by_id = current_user.id
    s.verify_notes = body.note
    write_travel_audit(db, entity_type="SETTLEMENT", entity_id=s.id, travel_request_id=s.travel_request_id,
                       action=TravelAuditAction.SETTLE, actor_id=current_user.id,
                       to_status=s.status.value, note="Verified")
    db.commit()
    db.refresh(s)
    return _settlement_resp(db, s)


@router.post("/settlements/{settlement_id}/settle", response_model=SettlementResponse)
def settle_settlement(settlement_id: UUID, body: SettlementSettleBody, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_superuser)):
    """Reconcile + disburse the net payable/recoverable.

    PAYROLL: posts an earning/deduction to the next salary run (idempotent via the
    linked payroll_adjustment_id) and stays SETTLED until the batch releases → PAID.
    CASH / BANK_TRANSFER: disbursed directly *outside* payroll, so it lands PAID
    immediately and posts NO payroll adjustment — settling via cash AND posting to
    payroll would pay the traveller twice."""
    s = _get_settlement(db, settlement_id, lock=True)
    if s.status not in (TravelSettlementStatus.SUBMITTED, TravelSettlementStatus.VERIFIED):
        raise HTTPException(409, f"A {s.status.value} settlement cannot be settled")
    reconcile(db, s)
    req = _get_req(db, s.travel_request_id)
    via_payroll = body.settlement_method == TravelSettlementMethod.PAYROLL
    if via_payroll:
        if s.payable_amount and s.payable_amount > 0:
            adj = post_adjustment(
                db, employee_id=s.employee_id, sub_type=f"TRAVEL_SETTLEMENT:{req.travel_reference_number}",
                title=f"Travel settlement · {req.travel_reference_number}", amount=s.payable_amount,
                is_deduction=False, is_taxable=False, period_month=body.period_month,
                period_year=body.period_year, actor=current_user, reason=body.note)
            s.payroll_adjustment_id = adj.id
        elif s.recoverable_amount and s.recoverable_amount > 0:
            adj = post_adjustment(
                db, employee_id=s.employee_id, sub_type=f"TRAVEL_RECOVERY:{req.travel_reference_number}",
                title=f"Travel advance recovery · {req.travel_reference_number}", amount=s.recoverable_amount,
                is_deduction=True, is_taxable=False, period_month=body.period_month,
                period_year=body.period_year, actor=current_user, reason=body.note)
            s.payroll_adjustment_id = adj.id
        s.status = TravelSettlementStatus.SETTLED
    else:
        # Direct disbursement (cash / bank transfer) — paid here and now, no payroll posting.
        s.status = TravelSettlementStatus.PAID
        s.paid_at = datetime.now(timezone.utc)
    s.settlement_method = body.settlement_method
    s.settled_at = datetime.now(timezone.utc)
    s.settled_by_id = current_user.id

    # Mark the linked advance + DA as settled/paid
    db.query(TravelAdvance).filter(
        TravelAdvance.travel_request_id == s.travel_request_id,
        TravelAdvance.status == AdvanceStatus.RELEASED).update({
            TravelAdvance.status: (AdvanceStatus.RECOVERED if s.recoverable_amount and s.recoverable_amount > 0 else AdvanceStatus.SETTLED),
            TravelAdvance.settled_at: datetime.now(timezone.utc),
            TravelAdvance.recovered_amount: s.recoverable_amount,
        }, synchronize_session=False)
    db.query(TravelDaRecord).filter(
        TravelDaRecord.travel_request_id == s.travel_request_id,
        TravelDaRecord.status == DaRecordStatus.APPROVED).update(
            {TravelDaRecord.status: DaRecordStatus.PAID}, synchronize_session=False)

    write_travel_audit(db, entity_type="SETTLEMENT", entity_id=s.id, travel_request_id=s.travel_request_id,
                       action=TravelAuditAction.SETTLE, actor_id=current_user.id, to_status=s.status.value,
                       note=f"Settled via {body.settlement_method.value} · payable {s.payable_amount} / recoverable {s.recoverable_amount}")
    db.commit()
    db.refresh(s)
    _notify(db, req, "settled", current_user)
    return _settlement_resp(db, s)


@router.post("/settlements/{settlement_id}/reverse", response_model=SettlementResponse)
def reverse_settlement(settlement_id: UUID, body: SettlementReverseBody, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_superuser)):
    s = _get_settlement(db, settlement_id, lock=True)
    if s.status not in (TravelSettlementStatus.SETTLED, TravelSettlementStatus.PAID):
        raise HTTPException(409, f"A {s.status.value} settlement cannot be reversed")
    if s.payroll_adjustment_id:
        cancel_or_reverse(db, s.payroll_adjustment_id, employee_id=s.employee_id,
                          reversal_sub_type=f"TRAVEL_SETTLEMENT_REVERSAL:{s.settlement_number}",
                          title=f"Travel settlement reversal · {s.settlement_number}",
                          actor=current_user, reason=body.reason)
    s.status = TravelSettlementStatus.REVERSED
    s.reversed_at = datetime.now(timezone.utc)
    s.reversal_reason = body.reason
    write_travel_audit(db, entity_type="SETTLEMENT", entity_id=s.id, travel_request_id=s.travel_request_id,
                       action=TravelAuditAction.REVERSE, actor_id=current_user.id, note=body.reason)
    db.commit()
    db.refresh(s)
    return _settlement_resp(db, s)


# ─────────────────────────── admin create / bulk ───────────────────────────

@router.post("/", response_model=TravelRequestResponse, status_code=201)
def admin_create_request(payload: TravelRequestAdminCreate, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_superuser)):
    """Admin raises a request on behalf of an employee — lands fully APPROVED."""
    emp = db.query(Employee).filter(Employee.id == payload.employee_id,
                                    Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    category = get_category(db, payload.category_id)
    req = build_new_request(db, employee=emp, category=category, payload=payload, actor=current_user)
    # Only an on-payroll employee (incl. notice) may have a request raised — a
    # suspended or fully separated employee can't incur new travel.
    guard_on_payroll(emp, "raise a travel request")
    # A leaving / departed employee may not travel past their LWD. req.return_date
    # is the derived envelope end (latest leg / return); fall back to departure.
    guard_within_tenure(emp, req.return_date or req.departure_date, "raise a travel request")
    now_iso = datetime.now(timezone.utc).isoformat()
    req.approval_steps = [{
        "step": 0, "approver_type": "HR", "approver_user_id": None, "label": "HR (admin entry)",
        "min_amount": None, "decision": TravelDecision.APPROVED.value,
        "decided_by_id": str(current_user.id), "decided_at": now_iso, "notes": "Entered by HR",
    }]
    flag_modified(req, "approval_steps")
    req.current_step = 1
    req.status = TravelRequestStatus.APPROVED
    req.submitted_at = datetime.now(timezone.utc)
    req.submitted_by_id = current_user.id
    req.approved_at = datetime.now(timezone.utc)
    mirror_final_columns(req)
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.CREATE, actor_id=current_user.id,
                       to_status=req.status.value, note=f"Admin-entered {req.travel_reference_number}")
    db.commit()
    db.refresh(req)
    return _resp(db, req)


@router.post("/bulk-decide", response_model=TravelRequestListResponse)
def bulk_decide(body: TravelBulkDecideBody, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    updated: List[TravelRequest] = []
    for rid in body.ids:
        req = db.query(TravelRequest).filter(
            TravelRequest.id == rid, TravelRequest.is_deleted == False).with_for_update().first()  # noqa: E712
        if not req or req.status != TravelRequestStatus.PENDING_APPROVAL:
            continue
        steps = list(req.approval_steps or [])
        idx = int(req.current_step or 0)
        if not (0 <= idx < len(steps)) or not can_act_on_step(current_user, steps[idx]):
            continue
        _, next_approver, event = apply_decision(db, req, decision=body.decision, notes=body.notes, actor=current_user)
        try:
            emit_notifications(db, req, employee_user_id=_emp_uid(db, req.employee_id), event=event,
                               actor=current_user, next_approver_id=next_approver)
        except Exception:
            pass
        updated.append(req)
    db.commit()
    for r in updated:
        db.refresh(r)
    return TravelRequestListResponse(items=[_resp(db, r) for r in updated], total=len(updated),
                                     page=1, limit=len(updated) or 1, total_pages=1)


# ─────────────────────────── single request ───────────────────────────

@router.get("/{request_id:uuid}", response_model=TravelRequestResponse)
def get_request(request_id: UUID, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    return _resp(db, _get_req(db, request_id))


@router.patch("/{request_id:uuid}", response_model=TravelRequestResponse)
def update_request(request_id: UUID, payload: TravelRequestUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, request_id, lock=True)
    if req.status not in (TravelRequestStatus.DRAFT, TravelRequestStatus.RETURNED):
        raise HTTPException(409, f"A {req.status.value} request cannot be edited")
    data = payload.model_dump(exclude_unset=True)
    if "category_id" in data and data["category_id"]:
        get_category(db, data["category_id"])
    if "attachments" in data and data["attachments"] is not None:
        data["attachments"] = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in payload.attachments]
    for k, v in data.items():
        setattr(req, k, v)
    recompute_request_derived(req)
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.UPDATE, actor_id=current_user.id)
    db.commit()
    db.refresh(req)
    return _resp(db, req)


@router.delete("/{request_id:uuid}")
def delete_request(request_id: UUID, body: TravelCancelBody = TravelCancelBody(),
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, request_id, lock=True)
    if req.status in (TravelRequestStatus.IN_PROGRESS, TravelRequestStatus.COMPLETED):
        raise HTTPException(409, "An in-progress / completed tour cannot be deleted — cancel it instead")
    from_status = req.status.value
    req.is_deleted = True
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.DELETE, actor_id=current_user.id,
                       from_status=from_status, to_status="DELETED", note=body.reason)
    db.commit()
    return {"success": True}


@router.patch("/{request_id}/decide", response_model=TravelRequestResponse)
def decide(request_id: UUID, body: TravelDecisionBody, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, request_id, lock=True)
    steps = list(req.approval_steps or [])
    idx = int(req.current_step or 0)
    if not (0 <= idx < len(steps)):
        raise HTTPException(409, "Request is fully resolved")
    if not can_act_on_step(current_user, steps[idx]):
        raise HTTPException(403, "You are not the configured approver for the current stage")
    _, next_approver, event = apply_decision(db, req, decision=body.decision, notes=body.notes, actor=current_user)
    db.commit()
    db.refresh(req)
    _notify(db, req, event, current_user, next_approver)
    return _resp(db, req)


@router.post("/{request_id}/request-changes", response_model=TravelRequestResponse)
def return_for_changes(request_id: UUID, body: TravelReturnBody, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, request_id, lock=True)
    if req.status != TravelRequestStatus.PENDING_APPROVAL:
        raise HTTPException(409, "Only requests awaiting approval can be returned")
    steps = list(req.approval_steps or [])
    idx = int(req.current_step or 0)
    if not (0 <= idx < len(steps)) or not can_act_on_step(current_user, steps[idx]):
        raise HTTPException(403, "You are not the configured approver for the current stage")
    _, _, event = apply_decision(db, req, decision=TravelDecision.RETURNED, notes=body.reason, actor=current_user)
    db.commit()
    db.refresh(req)
    _notify(db, req, event, current_user)
    return _resp(db, req)


@router.post("/{request_id}/escalate", response_model=TravelRequestResponse)
def escalate(request_id: UUID, body: TravelEscalateBody, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, request_id, lock=True)
    if req.status != TravelRequestStatus.PENDING_APPROVAL:
        raise HTTPException(409, "Only requests awaiting approval can be escalated")
    steps = list(req.approval_steps or [])
    idx = int(req.current_step or 0)
    if not (0 <= idx < len(steps)):
        raise HTTPException(409, "Request is fully resolved")
    cur = steps[idx]
    cur["decision"] = TravelDecision.SKIPPED.value
    cur["decided_by_id"] = str(current_user.id)
    cur["decided_at"] = datetime.now(timezone.utc).isoformat()
    cur["notes"] = body.note or "Escalated / skipped by admin"
    new_idx = auto_skip_unresolvable(steps, idx + 1)
    req.approval_steps = steps
    flag_modified(req, "approval_steps")
    req.current_step = new_idx
    if new_idx >= len(steps):
        req.status = TravelRequestStatus.APPROVED
        req.approved_at = datetime.now(timezone.utc)
        mirror_final_columns(req)
        event, next_approver = "approved", None
    else:
        req.status = step_status(steps, new_idx)
        na = steps[new_idx].get("approver_user_id")
        next_approver = UUID(na) if na else None
        event = "advanced"
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.ESCALATE, actor_id=current_user.id,
                       to_status=req.status.value, note=body.note)
    db.commit()
    db.refresh(req)
    _notify(db, req, event, current_user, next_approver)
    return _resp(db, req)


@router.post("/{request_id}/cancel", response_model=TravelRequestResponse)
def cancel_request(request_id: UUID, body: TravelCancelBody = TravelCancelBody(),
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    req = _get_req(db, request_id, lock=True)
    if req.status in (TravelRequestStatus.COMPLETED, TravelRequestStatus.REJECTED, TravelRequestStatus.CANCELLED):
        raise HTTPException(409, f"A {req.status.value} request cannot be cancelled")
    from_status = req.status.value
    # Unwind any attendance ON_DUTY rows travel created.
    if req.attendance_synced:
        unmark_on_duty(db, req)
    req.status = TravelRequestStatus.CANCELLED
    req.cancelled_at = datetime.now(timezone.utc)
    req.cancelled_by_id = current_user.id
    req.cancelled_reason = body.reason
    # Unwind money that hasn't hit payroll yet: void unpaid DA + un-released advances.
    # (RELEASED advances are already in payroll and must be recovered via settlement.)
    db.query(TravelDaRecord).filter(
        TravelDaRecord.travel_request_id == req.id, TravelDaRecord.is_deleted == False,  # noqa: E712
        TravelDaRecord.status.in_([DaRecordStatus.COMPUTED, DaRecordStatus.APPROVED]),
    ).update({TravelDaRecord.is_deleted: True}, synchronize_session=False)
    db.query(TravelAdvance).filter(
        TravelAdvance.travel_request_id == req.id, TravelAdvance.is_deleted == False,  # noqa: E712
        TravelAdvance.status.in_([AdvanceStatus.REQUESTED, AdvanceStatus.APPROVED]),
    ).update({TravelAdvance.status: AdvanceStatus.CANCELLED}, synchronize_session=False)
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.CANCEL, actor_id=current_user.id,
                       from_status=from_status, to_status=req.status.value, note=body.reason)
    db.commit()
    db.refresh(req)
    return _resp(db, req)


@router.post("/{request_id}/execute", response_model=TravelRequestResponse)
def execute_request(request_id: UUID, body: TravelExecuteBody = TravelExecuteBody(),
                    db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    """APPROVED → IN_PROGRESS. Optionally auto-marks attendance ON_DUTY for the tour.
    Can't start before the departure date — the scheduler starts it automatically then."""
    req = _get_req(db, request_id, lock=True)
    if req.status != TravelRequestStatus.APPROVED:
        raise HTTPException(409, f"Only an APPROVED request can be started (status {req.status.value})")
    # A trip departing after the employee's LWD can't be started.
    _exec_emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    guard_within_tenure(_exec_emp, req.departure_date, "start a trip")
    if req.departure_date and req.departure_date > trav_today():
        raise HTTPException(409, f"Travel can't start before its departure date "
                                 f"({req.departure_date.strftime('%d %b %Y')}). It starts automatically on the day.")
    execute_travel(db, req, actor_id=current_user.id, sync_attendance=body.sync_attendance)
    db.commit()
    db.refresh(req)
    return _resp(db, req)


@router.post("/{request_id}/complete", response_model=TravelRequestResponse)
def complete_request(request_id: UUID, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_superuser)):
    """IN_PROGRESS → COMPLETED. Opens a DRAFT settlement so expenses can be filed.
    Requires the trip to be started, and can't complete before the return date —
    the scheduler completes it automatically afterwards."""
    req = _get_req(db, request_id, lock=True)
    if req.status != TravelRequestStatus.IN_PROGRESS:
        raise HTTPException(409, "Start the travel first — only an in-progress trip can be completed")
    if req.return_date and req.return_date > trav_today():
        raise HTTPException(409, f"Travel can't be completed before its return date "
                                 f"({req.return_date.strftime('%d %b %Y')}). It completes automatically afterwards.")
    complete_travel(db, req, actor_id=current_user.id)
    db.commit()
    db.refresh(req)
    _notify(db, req, "completed", current_user)
    return _resp(db, req)
