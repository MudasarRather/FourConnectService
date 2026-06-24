"""HR Travel — self-service (`/hr/me/travel`).

Two audiences, both regular (non-superadmin) users:
  • the EMPLOYEE raising/tracking their own travel, requesting advances and filing
    post-travel expenses, and
  • the MANAGER / named approver acting on their team's requests at their chain
    stage (powers the user-side Team Approvals page).
Reads use ``try_self_employee`` (→ unlinked banner, no 404 spam); writes use
``resolve_self_employee``.
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_category import TravelCategory
from app.models.hr.travel_advance import TravelAdvance
from app.models.hr.travel_settlement import TravelSettlement
from app.models.hr.travel_policy import TravelPolicy
from app.models.hr.travel_booking import TravelBooking
from app.models.hr.travel_type import (
    TravelRequestStatus, AdvanceStatus, TravelSettlementStatus, TravelAuditAction, BookingStatus,
)
from app.schemas.hr.travel import (
    TravelRequestResponse, TravelRequestListResponse, TravelRequestCreate, TravelRequestUpdate,
    TravelDecisionBody, TravelCancelBody, TravelCategoryListResponse, MyTravelSummary,
    AdvanceCreate, AdvanceResponse, SettlementSubmitBody, SettlementResponse,
    BookingResponse, BookingListResponse, BookingSelfCreate, BookingUpdate,
)
from app.utils.dependencies import get_current_user
from app.utils.hr.travel import (
    try_self_employee, resolve_self_employee, to_response, write_travel_audit,
    emit_notifications, can_act_on_step, get_category, build_new_request,
    recompute_request_derived, submit_request, apply_decision, reconcile,
    generate_advance_number, generate_settlement_number, generate_booking_number, get_policy_for,
)
from app.utils.hr.lifecycle_guard import guard_within_tenure, guard_on_payroll

router = APIRouter(prefix="/hr/me/travel", tags=["HR — My Travel"])

_OPEN = (TravelRequestStatus.DRAFT, TravelRequestStatus.PENDING_APPROVAL, TravelRequestStatus.RETURNED)


def _empty(page, limit):
    return TravelRequestListResponse(items=[], total=0, page=page, limit=limit, total_pages=1, unlinked=True)


def _own(db: Session, request_id: UUID, emp: Employee, *, lock: bool = False) -> TravelRequest:
    q = db.query(TravelRequest).options(joinedload(TravelRequest.category)).filter(
        TravelRequest.id == request_id, TravelRequest.is_deleted == False)  # noqa: E712
    if lock:
        q = q.with_for_update(of=TravelRequest)
    req = q.first()
    if not req or req.employee_id != emp.id:
        raise HTTPException(404, "Travel request not found")
    return req


_BOOKABLE = (TravelRequestStatus.APPROVED, TravelRequestStatus.IN_PROGRESS)
_SETTLE_CLOSED = (TravelSettlementStatus.SETTLED, TravelSettlementStatus.PAID, TravelSettlementStatus.REVERSED)


def _books_open(db: Session, request_id, verb: str = "changed"):
    """Block self-booking edits once the tour's settlement has closed its books."""
    locked = db.query(TravelSettlement).filter(
        TravelSettlement.travel_request_id == request_id, TravelSettlement.is_deleted == False,  # noqa: E712
        TravelSettlement.status.in_(_SETTLE_CLOSED)).first()
    if locked:
        raise HTTPException(409, f"This tour's settlement ({locked.settlement_number}) is "
                                 f"{locked.status.value.lower()} — its books are closed, so bookings can't be {verb}.")


def _own_booking(db: Session, booking_id: UUID, emp: Employee, *, lock: bool = False):
    q = db.query(TravelBooking).filter(
        TravelBooking.id == booking_id, TravelBooking.is_deleted == False)  # noqa: E712
    if lock:
        q = q.with_for_update(of=TravelBooking)
    b = q.first()
    if not b:
        raise HTTPException(404, "Booking not found")
    owner = db.query(TravelRequest.employee_id).filter(TravelRequest.id == b.travel_request_id).scalar()
    if owner != emp.id:
        raise HTTPException(404, "Booking not found")
    return b


def _booking_dict(db: Session, b: TravelBooking) -> dict:
    ref = db.query(TravelRequest.travel_reference_number).filter(TravelRequest.id == b.travel_request_id).scalar()
    return {
        "id": b.id, "booking_number": b.booking_number, "travel_request_id": b.travel_request_id,
        "travel_reference_number": ref, "booking_type": b.booking_type, "vendor": b.vendor,
        "booking_date": b.booking_date, "travel_date": b.travel_date, "return_date": b.return_date,
        "pnr_number": b.pnr_number,
        "ticket_number": b.ticket_number, "airline": b.airline, "train_number": b.train_number,
        "seat_number": b.seat_number, "from_place": b.from_place, "to_place": b.to_place,
        "hotel_name": b.hotel_name, "check_in": b.check_in, "check_out": b.check_out,
        "num_nights": b.num_nights, "booking_cost": b.booking_cost, "taxes": b.taxes,
        "total_cost": b.total_cost, "currency": b.currency, "status": b.status, "notes": b.notes,
        "created_by_id": b.created_by_id, "created_at": b.created_at,
    }


# ─────────────────────────── employee ───────────────────────────

@router.get("/", response_model=TravelRequestListResponse)
def my_requests(status: Optional[TravelRequestStatus] = None, page: int = Query(1, ge=1, le=100000),
                limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    emp = try_self_employee(db, user)
    if not emp:
        return _empty(page, limit)
    query = db.query(TravelRequest).options(joinedload(TravelRequest.category)).filter(
        TravelRequest.employee_id == emp.id, TravelRequest.is_deleted == False)  # noqa: E712
    if status:
        query = query.filter(TravelRequest.status == status)
    total = query.count()
    rows = query.order_by(TravelRequest.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return TravelRequestListResponse(items=[to_response(db, r) for r in rows], total=total, page=page,
                                     limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


@router.get("/summary", response_model=MyTravelSummary)
def my_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = try_self_employee(db, user)
    if not emp:
        return MyTravelSummary(unlinked=True)
    base = db.query(TravelRequest).filter(
        TravelRequest.employee_id == emp.id, TravelRequest.is_deleted == False)  # noqa: E712
    today = date.today()
    in_flight = base.filter(TravelRequest.status == TravelRequestStatus.PENDING_APPROVAL).count()
    upcoming = base.filter(TravelRequest.status.in_([TravelRequestStatus.APPROVED, TravelRequestStatus.IN_PROGRESS]),
                           TravelRequest.return_date >= today).count()
    completed = base.filter(TravelRequest.status == TravelRequestStatus.COMPLETED).count()
    total = base.count()
    advance_out = db.query(sa_func.coalesce(sa_func.sum(
        sa_func.coalesce(TravelAdvance.approved_amount, TravelAdvance.advance_amount)), 0)).filter(
        TravelAdvance.employee_id == emp.id, TravelAdvance.is_deleted == False,  # noqa: E712
        TravelAdvance.status == AdvanceStatus.RELEASED).scalar() or 0
    pending_settlement = db.query(TravelSettlement).filter(
        TravelSettlement.employee_id == emp.id, TravelSettlement.is_deleted == False,  # noqa: E712
        TravelSettlement.status.in_([TravelSettlementStatus.DRAFT, TravelSettlementStatus.SUBMITTED, TravelSettlementStatus.VERIFIED]),
    ).count()
    fy_start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
    est_year = base.filter(TravelRequest.departure_date >= fy_start,
                           TravelRequest.status.notin_([TravelRequestStatus.REJECTED, TravelRequestStatus.CANCELLED])).with_entities(
        sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0)).scalar() or 0
    return MyTravelSummary(in_flight=in_flight, upcoming=upcoming, completed=completed, total_requests=total,
                           advance_outstanding=advance_out, pending_settlement=pending_settlement,
                           da_payable=Decimal("0"), estimated_spend_year=est_year, unlinked=False)


@router.get("/categories", response_model=TravelCategoryListResponse)
def my_categories(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(TravelCategory).filter(
        TravelCategory.is_deleted == False, TravelCategory.is_active == True,  # noqa: E712
    ).order_by(TravelCategory.sort_order.asc().nullslast(), TravelCategory.name.asc()).all()
    items = [{
        "id": c.id, "code": c.code, "name": c.name, "description": c.description, "icon": c.icon,
        "color_hex": c.color_hex, "field_schema": c.field_schema or [],
        "default_travel_type": c.default_travel_type, "requires_attachment": c.requires_attachment,
        "sort_order": c.sort_order, "is_active": c.is_active, "created_at": c.created_at, "request_count": None,
    } for c in rows]
    return TravelCategoryListResponse(items=items, total=len(items))


@router.get("/{request_id:uuid}", response_model=TravelRequestResponse)
def my_request(request_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    return to_response(db, _own(db, request_id, emp))


@router.post("/", response_model=TravelRequestResponse, status_code=201)
def create_and_submit(payload: TravelRequestCreate, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    category = get_category(db, payload.category_id)
    req = build_new_request(db, employee=emp, category=category, payload=payload, actor=user)
    # Only an on-payroll employee (incl. notice) may raise a request — a suspended
    # or fully separated employee can't incur new travel.
    guard_on_payroll(emp, "raise a travel request")
    # A leaving / departed employee may not travel past their LWD. req.return_date
    # is the derived envelope end (latest leg / return); fall back to departure.
    guard_within_tenure(emp, req.return_date or req.departure_date, "raise a travel request")
    next_approver = submit_request(db, req, emp, user)
    db.commit()
    db.refresh(req)
    try:
        emit_notifications(db, req, employee_user_id=user.id, event="submitted", actor=user,
                           next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()
    return to_response(db, req)


@router.post("/draft", response_model=TravelRequestResponse, status_code=201)
def create_draft(payload: TravelRequestCreate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    category = get_category(db, payload.category_id)
    req = build_new_request(db, employee=emp, category=category, payload=payload, actor=user)
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.CREATE, actor_id=user.id,
                       to_status=req.status.value, note=f"Draft {req.travel_reference_number}")
    db.commit()
    db.refresh(req)
    return to_response(db, req)


@router.patch("/{request_id:uuid}", response_model=TravelRequestResponse)
def edit_my_request(request_id: UUID, payload: TravelRequestUpdate, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    req = _own(db, request_id, emp, lock=True)
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
                       action=TravelAuditAction.UPDATE, actor_id=user.id)
    db.commit()
    db.refresh(req)
    return to_response(db, req)


@router.post("/{request_id:uuid}/submit", response_model=TravelRequestResponse)
def submit_my_request(request_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    req = _own(db, request_id, emp, lock=True)
    next_approver = submit_request(db, req, emp, user)
    db.commit()
    db.refresh(req)
    try:
        emit_notifications(db, req, employee_user_id=user.id, event="submitted", actor=user,
                           next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()
    return to_response(db, req)


# A trip can be removed from the traveller's list in any non-active state. Only
# APPROVED / IN_PROGRESS / COMPLETED (the trip is authorised or under way) are off-limits.
_DELETABLE = (TravelRequestStatus.DRAFT, TravelRequestStatus.PENDING_APPROVAL,
              TravelRequestStatus.RETURNED, TravelRequestStatus.REJECTED, TravelRequestStatus.CANCELLED)


@router.delete("/{request_id:uuid}")
def withdraw_my_request(request_id: UUID, body: TravelCancelBody = TravelCancelBody(),
                        remove: bool = Query(False),
                        db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Withdraw (default) or delete (`remove=true`) the traveller's own request.

    • Withdraw — only for live states (DRAFT/PENDING/RETURNED): a draft is removed,
      a submitted one is cancelled but stays visible as a record.
    • Delete (remove=true) — for any non-active state: the request is removed from
      the traveller's list (is_deleted). A live request is first cancelled so the
      approval queue is cleared and the cancellation is captured in the audit log
      (i.e. there is always a trail — deleting never bypasses it).
    """
    emp = resolve_self_employee(db, user)
    req = _own(db, request_id, emp, lock=True)
    s = req.status
    if remove:
        if s not in _DELETABLE:
            raise HTTPException(409, f"A {s.value} trip cannot be deleted")
    elif s not in _OPEN:
        raise HTTPException(409, f"A {s.value} request cannot be withdrawn")
    from_status = s.value
    # A live request leaving the chain is recorded as a cancellation (audit trail).
    if s in (TravelRequestStatus.PENDING_APPROVAL, TravelRequestStatus.RETURNED):
        req.status = TravelRequestStatus.CANCELLED
        req.cancelled_at = datetime.now(timezone.utc)
        req.cancelled_by_id = user.id
        req.cancelled_reason = body.reason
    # Drafts are always removed; an explicit delete removes any other non-active state.
    if remove or s == TravelRequestStatus.DRAFT:
        req.is_deleted = True
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.CANCEL, actor_id=user.id, from_status=from_status,
                       to_status=("DELETED" if req.is_deleted else req.status.value), note=body.reason)
    db.commit()
    return {"success": True}


# ─────────────────────────── advance request (self) ───────────────────────────

@router.post("/{request_id:uuid}/advance", response_model=AdvanceResponse, status_code=201)
def request_advance(request_id: UUID, payload: AdvanceCreate, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    req = _own(db, request_id, emp)
    if req.status not in (TravelRequestStatus.APPROVED, TravelRequestStatus.IN_PROGRESS):
        raise HTTPException(409, "Advance can only be requested for an approved travel request")
    # No travel cash advance for a trip departing after the employee's LWD.
    guard_within_tenure(emp, req.departure_date, "issue a travel advance")
    existing = db.query(TravelAdvance).filter(
        TravelAdvance.travel_request_id == req.id, TravelAdvance.is_deleted == False,  # noqa: E712
        TravelAdvance.status.in_([AdvanceStatus.REQUESTED, AdvanceStatus.APPROVED, AdvanceStatus.RELEASED]),
    ).first()
    if existing:
        raise HTTPException(409, "An advance for this request is already in progress")
    # Advance cannot exceed the trip estimate, nor the grade's policy advance limit.
    amt = Decimal(str(payload.advance_amount or 0))
    if amt <= 0:
        raise HTTPException(422, "Advance amount must be greater than zero")
    est = Decimal(str(req.est_total_cost or 0))
    if est > 0 and amt > est:
        raise HTTPException(422, f"Advance ₹{amt:,.0f} exceeds the trip's estimated cost of ₹{est:,.0f}")
    policy = get_policy_for(db, grade_id=req.grade_id)
    if policy and policy.advance_limit is not None and amt > Decimal(str(policy.advance_limit)):
        raise HTTPException(422, f"Advance exceeds your policy limit of {policy.advance_limit}")
    a = TravelAdvance(
        advance_number=generate_advance_number(db), travel_request_id=req.id, employee_id=emp.id,
        advance_amount=payload.advance_amount, currency=payload.currency, purpose=payload.purpose,
        status=AdvanceStatus.REQUESTED, created_by_id=user.id)
    db.add(a)
    db.flush()
    write_travel_audit(db, entity_type="ADVANCE", entity_id=a.id, travel_request_id=req.id,
                       action=TravelAuditAction.ADVANCE_REQUEST, actor_id=user.id,
                       note=f"Advance {a.advance_number}")
    db.commit()
    db.refresh(a)
    ref = req.travel_reference_number
    return {
        "id": a.id, "advance_number": a.advance_number, "travel_request_id": a.travel_request_id,
        "travel_reference_number": ref, "employee_id": a.employee_id, "employee_name": user.full_name,
        "advance_amount": a.advance_amount, "approved_amount": a.approved_amount, "currency": a.currency,
        "purpose": a.purpose, "status": a.status, "approved_at": a.approved_at, "released_at": a.released_at,
        "settled_at": a.settled_at, "recovered_amount": a.recovered_amount, "reject_reason": a.reject_reason,
        "payroll_ref": a.payroll_ref, "created_at": a.created_at,
    }


# ─────────────────────────── self bookings (itinerary) ───────────────────────────
# The employee sees every booking on their tour (incl. travel-desk ones) but can
# only create / edit / remove the bookings they recorded themselves.

@router.get("/{request_id:uuid}/bookings", response_model=BookingListResponse)
def my_bookings(request_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    req = _own(db, request_id, emp)
    rows = db.query(TravelBooking).filter(
        TravelBooking.travel_request_id == req.id, TravelBooking.is_deleted == False).order_by(  # noqa: E712
        TravelBooking.travel_date.asc().nullslast(), TravelBooking.created_at.asc()).all()
    items = [_booking_dict(db, b) for b in rows]
    return {"items": items, "total": len(items), "page": 1, "limit": len(items) or 1, "total_pages": 1}


@router.post("/{request_id:uuid}/bookings", response_model=BookingResponse, status_code=201)
def add_my_booking(request_id: UUID, payload: BookingSelfCreate, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    req = _own(db, request_id, emp, lock=True)
    if req.status not in _BOOKABLE:
        raise HTTPException(409, "Bookings can only be recorded for an approved or in-progress tour")
    _books_open(db, req.id, "added")
    total = Decimal(str(payload.booking_cost or 0)) + Decimal(str(payload.taxes or 0))
    if total <= 0:
        raise HTTPException(422, "A booking must have a fare or taxes greater than zero")
    b = TravelBooking(
        booking_number=generate_booking_number(db), travel_request_id=req.id,
        booking_type=payload.booking_type, vendor=payload.vendor, booking_date=payload.booking_date,
        travel_date=payload.travel_date, return_date=payload.return_date,
        pnr_number=payload.pnr_number, ticket_number=payload.ticket_number,
        airline=payload.airline, train_number=payload.train_number, seat_number=payload.seat_number,
        from_place=payload.from_place, to_place=payload.to_place, hotel_name=payload.hotel_name,
        check_in=payload.check_in, check_out=payload.check_out, num_nights=payload.num_nights,
        booking_cost=payload.booking_cost, taxes=payload.taxes, total_cost=total,
        currency=payload.currency, status=BookingStatus.BOOKED, notes=payload.notes,
        created_by_id=user.id)
    db.add(b)
    db.flush()
    write_travel_audit(db, entity_type="BOOKING", entity_id=b.id, travel_request_id=req.id,
                       action=TravelAuditAction.BOOK, actor_id=user.id,
                       note=f"Self {b.booking_type.value} {b.booking_number}")
    db.commit()
    db.refresh(b)
    return _booking_dict(db, b)


@router.patch("/bookings/{booking_id:uuid}", response_model=BookingResponse)
def edit_my_booking(booking_id: UUID, payload: BookingUpdate, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    b = _own_booking(db, booking_id, emp, lock=True)
    if b.created_by_id != user.id:
        raise HTTPException(403, "You can only edit bookings you added yourself")
    if b.status in (BookingStatus.CANCELLED, BookingStatus.COMPLETED):
        raise HTTPException(409, f"A {b.status.value.lower()} booking cannot be edited")
    _books_open(db, b.travel_request_id, "edited")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(b, k, v)
    b.total_cost = Decimal(str(b.booking_cost or 0)) + Decimal(str(b.taxes or 0))
    if b.total_cost <= 0:
        raise HTTPException(422, "A booking must have a fare or taxes greater than zero")
    write_travel_audit(db, entity_type="BOOKING", entity_id=b.id, travel_request_id=b.travel_request_id,
                       action=TravelAuditAction.BOOKING_UPDATE, actor_id=user.id)
    db.commit()
    db.refresh(b)
    return _booking_dict(db, b)


@router.delete("/bookings/{booking_id:uuid}")
def remove_my_booking(booking_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = resolve_self_employee(db, user)
    b = _own_booking(db, booking_id, emp, lock=True)
    if b.created_by_id != user.id:
        raise HTTPException(403, "You can only remove bookings you added yourself")
    _books_open(db, b.travel_request_id, "removed")
    b.is_deleted = True
    b.status = BookingStatus.CANCELLED
    write_travel_audit(db, entity_type="BOOKING", entity_id=b.id, travel_request_id=b.travel_request_id,
                       action=TravelAuditAction.BOOKING_CANCEL, actor_id=user.id)
    db.commit()
    return {"success": True}


# ─────────────────────────── expense settlement (self) ───────────────────────────

def _settlement_dict(s: TravelSettlement, ref: str, name: Optional[str]) -> dict:
    return {
        "id": s.id, "settlement_number": s.settlement_number, "travel_request_id": s.travel_request_id,
        "travel_reference_number": ref, "employee_id": s.employee_id, "employee_name": name,
        "expense_lines": s.expense_lines or [], "advance_received": s.advance_received,
        "total_expense": s.total_expense, "approved_expense": s.approved_expense, "da_amount": s.da_amount,
        "payable_amount": s.payable_amount, "recoverable_amount": s.recoverable_amount, "currency": s.currency,
        "status": s.status, "settlement_method": s.settlement_method, "submitted_at": s.submitted_at,
        "verified_at": s.verified_at, "settled_at": s.settled_at, "paid_at": s.paid_at,
        "payroll_ref": s.payroll_ref, "reversal_reason": s.reversal_reason, "created_at": s.created_at,
    }


@router.get("/{request_id:uuid}/settlement", response_model=Optional[SettlementResponse])
def my_settlement(request_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Fetch the tour's settlement (incl. expense_lines) so the employee can review or edit a draft."""
    emp = resolve_self_employee(db, user)
    req = _own(db, request_id, emp)
    s = db.query(TravelSettlement).filter(
        TravelSettlement.travel_request_id == req.id, TravelSettlement.is_deleted == False).first()  # noqa: E712
    if not s:
        return None
    return _settlement_dict(s, req.travel_reference_number, user.full_name)


@router.post("/{request_id:uuid}/settlement", response_model=SettlementResponse)
def submit_expenses(request_id: UUID, body: SettlementSubmitBody, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Employee files post-travel expenses. Creates/updates the settlement → SUBMITTED."""
    emp = resolve_self_employee(db, user)
    req = _own(db, request_id, emp)
    if req.status != TravelRequestStatus.COMPLETED:
        raise HTTPException(409, "Expenses can only be filed once the tour is completed")
    s = db.query(TravelSettlement).filter(
        TravelSettlement.travel_request_id == req.id, TravelSettlement.is_deleted == False).first()  # noqa: E712
    if not s:
        s = TravelSettlement(
            settlement_number=generate_settlement_number(db), travel_request_id=req.id,
            employee_id=emp.id, currency=req.currency, status=TravelSettlementStatus.DRAFT,
            created_by_id=user.id)
        db.add(s)
        db.flush()
    if s.status not in (TravelSettlementStatus.DRAFT, TravelSettlementStatus.SUBMITTED):
        raise HTTPException(409, f"A {s.status.value} settlement cannot be edited")
    s.expense_lines = [ln.model_dump(mode="json") for ln in body.expense_lines]
    s.notes = body.notes
    s.status = TravelSettlementStatus.SUBMITTED
    s.submitted_at = datetime.now(timezone.utc)
    reconcile(db, s)
    write_travel_audit(db, entity_type="SETTLEMENT", entity_id=s.id, travel_request_id=req.id,
                       action=TravelAuditAction.EXPENSE_SUBMIT, actor_id=user.id,
                       to_status=s.status.value, note=f"{len(s.expense_lines)} expense line(s)")
    db.commit()
    db.refresh(s)
    return _settlement_dict(s, req.travel_reference_number, user.full_name)


# ─────────────────────────── manager queue (user-side) ───────────────────────────

@router.get("/approval-queue", response_model=TravelRequestListResponse)
def my_approval_queue(page: int = Query(1, ge=1, le=100000), limit: int = Query(50, ge=1, le=200),
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(TravelRequest).options(joinedload(TravelRequest.category)).filter(
        TravelRequest.is_deleted == False,  # noqa: E712
        TravelRequest.status == TravelRequestStatus.PENDING_APPROVAL,
    ).order_by(TravelRequest.submitted_at.asc()).all()
    actionable = []
    for r in rows:
        steps = list(r.approval_steps or [])
        idx = int(r.current_step or 0)
        if 0 <= idx < len(steps) and can_act_on_step(user, steps[idx]):
            actionable.append(r)
    total = len(actionable)
    paged = actionable[(page - 1) * limit: (page - 1) * limit + limit]
    return TravelRequestListResponse(items=[to_response(db, c) for c in paged], total=total, page=page,
                                     limit=limit, total_pages=max(1, ceil(total / limit) if limit else 1))


@router.patch("/requests/{request_id:uuid}/decide", response_model=TravelRequestResponse)
def manager_decide(request_id: UUID, body: TravelDecisionBody, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    req = db.query(TravelRequest).options(joinedload(TravelRequest.category)).filter(
        TravelRequest.id == request_id, TravelRequest.is_deleted == False).with_for_update(of=TravelRequest).first()  # noqa: E712
    if not req:
        raise HTTPException(404, "Travel request not found")
    steps = list(req.approval_steps or [])
    idx = int(req.current_step or 0)
    if not (0 <= idx < len(steps)):
        raise HTTPException(409, "Request is fully resolved")
    if not can_act_on_step(user, steps[idx]):
        raise HTTPException(403, "You are not the approver for the current stage")
    _, next_approver, event = apply_decision(db, req, decision=body.decision, notes=body.notes, actor=user)
    db.commit()
    db.refresh(req)
    try:
        emp_uid = db.query(Employee.user_id).filter(Employee.id == req.employee_id).scalar()
        emit_notifications(db, req, employee_user_id=emp_uid, event=event, actor=user,
                           next_approver_id=next_approver)
        db.commit()
    except Exception:
        db.rollback()
    return to_response(db, req)
