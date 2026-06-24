"""HR Travel — shared DB helpers (self-employee resolution, number gen, audit,
notifications, response builders). Keeps the routers thin. Mirrors the
Reimbursements service layer.
"""
from __future__ import annotations

from datetime import date
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
from app.models.hr.grade import Grade
from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_booking import TravelBooking
from app.models.hr.travel_advance import TravelAdvance
from app.models.hr.travel_da import TravelDaRecord
from app.models.hr.travel_settlement import TravelSettlement
from app.models.hr.travel_audit_log import TravelAuditLog
from app.models.hr.travel_type import TravelRequestStatus, TravelAuditAction, DaRecordStatus


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


def generate_request_number(db: Session) -> str:
    return _next_counter(db, "travel_ref_counter", "TR", TravelRequest,
                         TravelRequest.travel_reference_number, "Counter for TravelRequest ref number")


def generate_booking_number(db: Session) -> str:
    return _next_counter(db, "travel_booking_counter", "BK", TravelBooking,
                         TravelBooking.booking_number, "Counter for TravelBooking number")


def generate_advance_number(db: Session) -> str:
    return _next_counter(db, "travel_advance_counter", "AD", TravelAdvance,
                         TravelAdvance.advance_number, "Counter for TravelAdvance number")


def generate_settlement_number(db: Session) -> str:
    return _next_counter(db, "travel_settlement_counter", "TS", TravelSettlement,
                         TravelSettlement.settlement_number, "Counter for TravelSettlement number")


# ─── dynamic field validation ───

def trav_today():
    """Business 'today' for date-gated lifecycle transitions. Uses IST so a trip
    starts/completes on the right calendar day for an India-based workforce;
    falls back to UTC if the attendance TZ helper is unavailable."""
    from datetime import datetime
    try:
        from app.utils.hr.attendance_logic import IST
        return datetime.now(IST).date()
    except Exception:
        from datetime import timezone
        return datetime.now(timezone.utc).date()


def validate_details_against_schema(details: Dict[str, Any], field_schema: List[dict]) -> None:
    schema = field_schema or []
    allowed = {f.get("key") for f in schema if f.get("key")}
    details = details or {}
    # Keys prefixed with "_" are reserved system slots (e.g. "__preferences" for
    # the traveller's flight/stay wishes) and bypass the per-category schema.
    unknown = {k for k in details.keys() if not str(k).startswith("_")} - allowed
    if unknown:
        raise HTTPException(422, f"Unknown detail fields for this category: {', '.join(sorted(unknown))}")
    for f in schema:
        if f.get("required") and not str(details.get(f.get("key"), "")).strip():
            raise HTTPException(422, f"Missing required field: {f.get('label') or f.get('key')}")


# ─── audit + notifications ───

def write_travel_audit(db: Session, *, entity_type: str, entity_id, action: TravelAuditAction,
                       travel_request_id=None, actor_id=None, from_status: Optional[str] = None,
                       to_status: Optional[str] = None, note: Optional[str] = None,
                       payload: Optional[Dict] = None) -> None:
    db.add(TravelAuditLog(
        entity_type=entity_type, entity_id=entity_id, action=action,
        travel_request_id=travel_request_id, actor_id=actor_id, from_status=from_status,
        to_status=to_status, note=note, payload=payload,
    ))


def emit_notifications(db: Session, req: TravelRequest, *, employee_user_id: Optional[UUID],
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

    ref = req.travel_reference_number
    self_url = "/user/self-service/travel"
    queue_url = "/user/self-service/team-approvals"

    if event == "submitted":
        _add(employee_user_id, "travel_submitted", "Travel request submitted",
             f"{ref} submitted for approval", self_url)
        _add(next_approver_id, "travel_pending", "Travel request awaiting you",
             f"{ref} is awaiting your decision", queue_url)
    elif event == "advanced":
        _add(next_approver_id, "travel_pending", "Travel request awaiting you",
             f"{ref} is awaiting your decision", queue_url)
    elif event == "approved":
        _add(employee_user_id, "travel_approved", "Travel approved",
             f"{ref} fully approved — book your travel", self_url)
    elif event == "rejected":
        _add(employee_user_id, "travel_rejected", "Travel declined", f"{ref} was declined", self_url)
    elif event == "returned":
        _add(employee_user_id, "travel_returned", "Travel returned",
             f"{ref} returned for correction", self_url)
    elif event == "advance_released":
        _add(employee_user_id, "travel_advance", "Travel advance released",
             f"Advance for {ref} released to payroll", self_url)
    elif event == "settled":
        _add(employee_user_id, "travel_settled", "Travel settled",
             f"{ref} expense settlement processed", self_url)
    elif event == "completed":
        _add(employee_user_id, "travel_completed", "Travel completed",
             f"{ref} marked complete — submit your expenses", self_url)


# ─── snapshots + response builder ───

def employee_snapshot(db: Session, employee_id: UUID) -> dict:
    snap = (
        db.query(
            Employee.id, Employee.employee_id.label("code"),
            User.full_name.label("name"), User.email.label("email"),
            Department.name.label("dept"), Designation.name.label("desg"),
            Grade.name.label("grade"), Employee.grade_id, Employee.reporting_manager_id,
            Employee.lifecycle_state, Employee.last_working_date,
        )
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .outerjoin(Grade, Grade.id == Employee.grade_id)
        .filter(Employee.id == employee_id)
        .first()
    )
    if not snap:
        return {}
    return {
        "name": snap.name, "code": snap.code, "email": snap.email,
        "dept": snap.dept, "desg": snap.desg, "grade": snap.grade,
        "grade_id": snap.grade_id, "reporting_manager_id": snap.reporting_manager_id,
        "lifecycle_state": snap.lifecycle_state, "last_working_date": snap.last_working_date,
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


_EDITABLE = {TravelRequestStatus.DRAFT, TravelRequestStatus.RETURNED}
_WITHDRAWABLE = {TravelRequestStatus.DRAFT, TravelRequestStatus.PENDING_APPROVAL, TravelRequestStatus.RETURNED}


def _booking_mini(b: TravelBooking) -> dict:
    return {
        "id": b.id, "booking_number": b.booking_number, "booking_type": b.booking_type,
        "vendor": b.vendor, "travel_date": b.travel_date, "return_date": b.return_date,
        "total_cost": b.total_cost, "status": b.status,
    }


def to_response(db: Session, req: TravelRequest, *, deep: bool = True) -> dict:
    """Build the TravelRequestResponse dict (Pydantic validates on the way out)."""
    _today = trav_today()
    snap = employee_snapshot(db, req.employee_id)

    # Why an approval would be refused, surfaced ONLY while the request is awaiting a
    # decision so the approver UI can disable the Approve action (and say why) before
    # the click instead of failing it with a 409 toast. Mirrors the raising guards in
    # flow.apply_decision exactly — see lifecycle_guard.travel_approval_block_reason.
    approval_block = None
    if req.status == TravelRequestStatus.PENDING_APPROVAL and snap.get("lifecycle_state") is not None:
        from types import SimpleNamespace
        from app.utils.hr.lifecycle_guard import travel_approval_block_reason
        approval_block = travel_approval_block_reason(
            SimpleNamespace(lifecycle_state=snap["lifecycle_state"],
                            last_working_date=snap.get("last_working_date")),
            req.return_date or req.departure_date,
        )
    cat = req.category
    project_name = None
    if req.project_id:
        from app.models.project import Project
        prow = db.query(Project.name).filter(Project.id == req.project_id).first()
        project_name = prow[0] if prow else None
    grade_name = None
    if req.grade_id:
        grow = db.query(Grade.name).filter(Grade.id == req.grade_id).first()
        grade_name = grow[0] if grow else snap.get("grade")
    else:
        grade_name = snap.get("grade")

    bookings, advance, da, settlement = [], None, None, None
    booked_cost = actual_cost = cost_variance = None
    if deep:
        b_rows = db.query(TravelBooking).filter(
            TravelBooking.travel_request_id == req.id, TravelBooking.is_deleted == False).all()  # noqa: E712
        bookings = [_booking_mini(b) for b in b_rows]
        adv = db.query(TravelAdvance).filter(
            TravelAdvance.travel_request_id == req.id, TravelAdvance.is_deleted == False).order_by(  # noqa: E712
            TravelAdvance.created_at.desc()).first()
        if adv:
            advance = {
                "id": adv.id, "advance_number": adv.advance_number, "advance_amount": adv.advance_amount,
                "approved_amount": adv.approved_amount, "status": adv.status,
            }
        dar = db.query(TravelDaRecord).filter(
            TravelDaRecord.travel_request_id == req.id, TravelDaRecord.is_deleted == False).first()  # noqa: E712
        if dar:
            da = {
                "id": dar.id, "travel_days": dar.travel_days, "daily_rate": dar.daily_rate,
                "eligible_da": dar.eligible_da, "approved_da": dar.approved_da,
                "city_category": dar.city_category, "status": dar.status,
            }
        st = db.query(TravelSettlement).filter(
            TravelSettlement.travel_request_id == req.id, TravelSettlement.is_deleted == False).first()  # noqa: E712
        if st:
            settlement = {
                "id": st.id, "settlement_number": st.settlement_number,
                "payable_amount": st.payable_amount, "recoverable_amount": st.recoverable_amount,
                "status": st.status,
            }

        # Actual-cost rollup — reuses the rows already fetched above (no extra queries).
        # actual = company-paid bookings + reimbursed (approved) expenses + finalised DA.
        booked_cost = sum((Decimal(str(b.total_cost or 0)) for b in b_rows), Decimal("0"))
        reimbursed = Decimal(str(st.approved_expense or 0)) if st else Decimal("0")
        da_actual = Decimal("0")
        if dar and dar.status in (DaRecordStatus.APPROVED, DaRecordStatus.PAID):
            da_actual = Decimal(str(dar.approved_da if dar.approved_da is not None else (dar.eligible_da or 0)))
        actual_cost = booked_cost + reimbursed + da_actual
        cost_variance = actual_cost - Decimal(str(req.est_total_cost or 0))

    return {
        "id": req.id,
        "travel_reference_number": req.travel_reference_number,
        "employee_id": req.employee_id,
        "employee_name": snap.get("name"),
        "employee_code": snap.get("code"),
        "department": snap.get("dept"),
        "designation": snap.get("desg"),
        "grade_id": req.grade_id or snap.get("grade_id"),
        "grade_name": grade_name,
        "purpose": req.purpose,
        "travel_type": req.travel_type,
        "category_id": req.category_id,
        "category_code": cat.code if cat else None,
        "category_name": cat.name if cat else None,
        "category_icon": cat.icon if cat else None,
        "category_color": cat.color_hex if cat else None,
        "priority": req.priority,
        "trip_type": getattr(req, "trip_type", None) or "ROUND_TRIP",
        "itinerary": getattr(req, "itinerary", None),
        "from_location": req.from_location,
        "to_location": req.to_location,
        "to_city_category": req.to_city_category,
        "departure_date": req.departure_date,
        "return_date": req.return_date,
        "num_days": req.num_days,
        "project_id": req.project_id,
        "project_name": project_name,
        "cost_center": req.cost_center,
        "budget_head": req.budget_head,
        "funding_source": req.funding_source,
        "flight_required": req.flight_required,
        "train_required": req.train_required,
        "hotel_required": req.hotel_required,
        "local_transport_required": req.local_transport_required,
        "advance_required": req.advance_required,
        "est_travel_cost": req.est_travel_cost,
        "est_accommodation_cost": req.est_accommodation_cost,
        "est_local_cost": req.est_local_cost,
        "est_food_cost": req.est_food_cost,
        "est_misc_cost": req.est_misc_cost,
        "est_total_cost": req.est_total_cost,
        "currency": req.currency,
        "attachments": req.attachments or [],
        "details": req.details or {},
        "status": req.status,
        "submitted_at": req.submitted_at,
        "approval_steps": enrich_steps_with_names(db, list(req.approval_steps or [])),
        "current_step": int(req.current_step or 0),
        "approved_at": req.approved_at,
        "approver_notes": req.approver_notes,
        "return_reason": req.return_reason,
        "reject_reason": req.reject_reason,
        "cancelled_reason": req.cancelled_reason,
        "executed_at": req.executed_at,
        "attendance_synced": req.attendance_synced,
        "completed_at": req.completed_at,
        "created_at": req.created_at,
        "bookings": bookings,
        "advance": advance,
        "da": da,
        "settlement": settlement,
        "booked_cost": booked_cost,
        "actual_cost": actual_cost,
        "cost_variance": cost_variance,
        "can_edit": req.status in _EDITABLE,
        "can_withdraw": req.status in _WITHDRAWABLE,
        # Date-gated: a trip can only be started on/after its departure date, and
        # completed on/after its return date (and only once it's started).
        "can_execute": req.status == TravelRequestStatus.APPROVED
        and (req.departure_date is None or req.departure_date <= _today),
        "can_complete": req.status == TravelRequestStatus.IN_PROGRESS
        and (req.return_date is None or req.return_date <= _today),
        "approval_block": approval_block,
    }
