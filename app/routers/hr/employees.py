"""HR Employees router — CRUD + lifecycle state machine + history.

All endpoints require superuser. PII (account_number) is masked unless the
caller explicitly opts in via ?reveal_bank=true. Aadhaar is *only* stored
as last 4 digits — there is no full-Aadhaar reveal at any time.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db, engine
from app.models.user import User
from app.models.hr.employee import (
    Employee, LifecycleState, EmployeeCategory,
)
from app.models.hr.employee_history import EmployeeHistory, EmployeeChangeType
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.grade import Grade
from app.models.hr.location import WorkLocation
from app.schemas.hr.employee import (
    EmployeeCreate, EmployeeUpdate,
    EmployeeResponse, EmployeeDetailResponse, EmployeeListResponse,
)
from app.schemas.hr.employee_history import EmployeeHistoryResponse
from app.schemas.hr.employee_lifecycle import (
    LifecycleConfirmBody, LifecyclePromoteBody, LifecycleTransferBody,
    LifecycleSuspendBody, LifecycleReinstateBody,
    LifecycleGiveNoticeBody, LifecycleExitBody, LifecycleArchiveBody,
    LifecyclePutOnProbationBody,
)
from app.utils.auth import get_password_hash
from app.utils.dependencies import get_current_superuser
from app.utils.hr.onboarding_bootstrap import bootstrap_onboarding

router = APIRouter(prefix="/hr/employees", tags=["HR — Employees"])


# ─────────────────────────────────── Helpers ───────────────────────────────────

def _track_actor(db: Session, actor_id):
    db.info["audit_actor_id"] = str(actor_id)


def _mask_account(num: Optional[str]) -> Optional[str]:
    if not num:
        return num
    digits = "".join(ch for ch in num if ch.isdigit())
    if len(digits) <= 4:
        return num
    return "X" * (len(digits) - 4) + digits[-4:]


def _next_employee_id(db: Session) -> str:
    """Allocate the next EMP#### id using a PG sequence; fall back to MAX+1 if the
    sequence isn't present (e.g. tests with sqlite)."""
    try:
        nv = db.execute(text("SELECT nextval('hr_employee_id_seq')")).scalar()
        return f"EMP{int(nv):04d}"
    except Exception:
        db.rollback()
        last = (
            db.query(Employee.employee_id)
            .filter(Employee.employee_id.like("EMP%"))
            .order_by(Employee.employee_id.desc())
            .first()
        )
        n = 1
        if last and last[0] and last[0][3:].isdigit():
            n = int(last[0][3:]) + 1
        return f"EMP{n:04d}"


def _load_with_relations(db: Session, employee_id: UUID) -> Optional[Employee]:
    return (
        db.query(Employee)
        .options(
            joinedload(Employee.user),
            joinedload(Employee.department),
            joinedload(Employee.designation),
            joinedload(Employee.grade),
            joinedload(Employee.work_location),
            joinedload(Employee.reporting_manager),
            joinedload(Employee.hr_manager),
        )
        .filter(Employee.id == employee_id)
        .first()
    )


def _to_list_row(emp: Employee) -> dict:
    return {
        "id": emp.id,
        "user_id": emp.user_id,
        "employee_id": emp.employee_id,
        "employee_code": emp.employee_code,
        "gender": emp.gender,
        "dob": emp.dob,
        "nationality": emp.nationality,
        "department_id": emp.department_id,
        "designation_id": emp.designation_id,
        "employment_type": emp.employment_type.value if emp.employment_type else None,
        "employee_category": emp.employee_category.value if emp.employee_category else None,
        "joining_date": emp.joining_date,
        "grade_id": emp.grade_id,
        "work_location_id": emp.work_location_id,
        "reporting_manager_id": emp.reporting_manager_id,
        "lifecycle_state": emp.lifecycle_state.value if hasattr(emp.lifecycle_state, "value") else emp.lifecycle_state,
        "is_deleted": emp.is_deleted,
        "full_name": getattr(emp.user, "full_name", None) if emp.user else None,
        "email": getattr(emp.user, "email", None) if emp.user else None,
        "avatar_url": getattr(emp.user, "avatar_url", None) if emp.user else None,
        "department_name": getattr(emp.department, "name", None) if emp.department else None,
        "designation_name": getattr(emp.designation, "name", None) if emp.designation else None,
        "created_at": emp.created_at,
        "updated_at": emp.updated_at,
    }


def _to_detail(emp: Employee, reveal_bank: bool) -> dict:
    base = {c.name: getattr(emp, c.name) for c in emp.__table__.columns}
    # Mask sensitive bank/account fields unless explicit reveal
    if not reveal_bank:
        base["account_number"] = _mask_account(emp.account_number)
    # Coerce enum values to plain strings for response
    for k in ("marital_status", "tax_regime", "employment_type", "employee_category", "lifecycle_state"):
        v = base.get(k)
        if v is not None and hasattr(v, "value"):
            base[k] = v.value
    base["user"] = emp.user
    base["department"] = emp.department
    base["designation"] = emp.designation
    base["grade"] = emp.grade
    base["work_location"] = emp.work_location
    base["reporting_manager"] = emp.reporting_manager
    base["hr_manager"] = emp.hr_manager
    return base


def _serialise_employee_snapshot(emp: Employee) -> dict:
    """Snapshot used in EmployeeHistory rows."""
    return {
        "department_id": str(emp.department_id) if emp.department_id else None,
        "designation_id": str(emp.designation_id) if emp.designation_id else None,
        "grade_id": str(emp.grade_id) if emp.grade_id else None,
        "work_location_id": str(emp.work_location_id) if emp.work_location_id else None,
        "reporting_manager_id": str(emp.reporting_manager_id) if emp.reporting_manager_id else None,
        "lifecycle_state": emp.lifecycle_state.value if hasattr(emp.lifecycle_state, "value") else str(emp.lifecycle_state),
        "employee_category": emp.employee_category.value if (emp.employee_category and hasattr(emp.employee_category, "value")) else None,
        "monthly_ctc": float(emp.monthly_ctc) if emp.monthly_ctc is not None else None,
        "pay_level": emp.pay_level,
    }


def _write_history(
    db: Session,
    employee: Employee,
    change_type: EmployeeChangeType,
    before: Optional[dict],
    after: Optional[dict],
    actor_id,
    reason: Optional[str],
    effective_date,
):
    row = EmployeeHistory(
        employee_id=employee.id,
        change_type=change_type,
        from_value_json=before,
        to_value_json=after,
        effective_date=effective_date,
        reason=reason,
        actioned_by_id=actor_id,
    )
    db.add(row)


# ─────────────────────────────────── List + Get ───────────────────────────────────

@router.get("/", response_model=EmployeeListResponse)
def list_employees(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    department_id: Optional[UUID] = None,
    designation_id: Optional[UUID] = None,
    grade_id: Optional[UUID] = None,
    work_location_id: Optional[UUID] = None,
    reporting_manager_id: Optional[UUID] = None,
    employment_type: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
    include_deleted: bool = False,
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = (
        db.query(Employee)
        .options(
            joinedload(Employee.user),
            joinedload(Employee.department),
            joinedload(Employee.designation),
        )
    )
    if not include_deleted:
        q = q.filter(Employee.is_deleted == False)  # noqa: E712
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    if designation_id:
        q = q.filter(Employee.designation_id == designation_id)
    if grade_id:
        q = q.filter(Employee.grade_id == grade_id)
    if work_location_id:
        q = q.filter(Employee.work_location_id == work_location_id)
    if reporting_manager_id:
        q = q.filter(Employee.reporting_manager_id == reporting_manager_id)
    if employment_type:
        q = q.filter(Employee.employment_type == employment_type)
    if lifecycle_state:
        q = q.filter(Employee.lifecycle_state == lifecycle_state)
    if search:
        like = f"%{search.strip()}%"
        q = q.join(User, Employee.user_id == User.id, isouter=True).filter(
            or_(
                Employee.employee_id.ilike(like),
                Employee.employee_code.ilike(like),
                User.full_name.ilike(like),
                User.email.ilike(like),
            )
        )

    total = q.with_entities(func.count(Employee.id)).scalar() or 0

    sort_col = getattr(Employee, sort_by, Employee.created_at)
    q = q.order_by(sort_col.desc() if sort_dir.lower() == "desc" else sort_col.asc())
    rows = q.offset((page - 1) * limit).limit(limit).all()

    return {
        "items": [_to_list_row(e) for e in rows],
        "total": int(total),
        "page": page,
        "limit": limit,
    }


@router.get("/{employee_pk}", response_model=EmployeeDetailResponse)
def get_employee(
    employee_pk: UUID,
    reveal_bank: bool = Query(False),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    return _to_detail(emp, reveal_bank=reveal_bank)


# ─────────────────────────────────── Create ───────────────────────────────────

@router.post("/", response_model=EmployeeDetailResponse, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: EmployeeCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    data = payload.model_dump(exclude_unset=True)

    user_id = data.pop("user_id", None)
    create_email = data.pop("create_email", None)
    create_full_name = data.pop("create_full_name", None)

    # ── Optional back-link to a recruitment Offer ──────────────────────────
    # Validate eagerly so the new employee row isn't half-created on failure.
    offer_id = data.pop("offer_id", None)
    offer_obj = None
    if offer_id:
        from app.models.hr.recruitment import Offer, OfferStatus
        offer_obj = db.query(Offer).filter(Offer.id == offer_id).first()
        if not offer_obj:
            raise HTTPException(404, "Linked offer not found")
        if offer_obj.status != OfferStatus.ACCEPTED:
            raise HTTPException(
                409, f"Offer must be accepted to onboard (current status: {offer_obj.status})"
            )
        if offer_obj.employee_id is not None:
            raise HTTPException(409, "Offer is already linked to another employee")

    if not user_id:
        if not create_email or not create_full_name:
            raise HTTPException(400, "Provide either user_id, or create_email + create_full_name to provision a new User.")
        # Provision a User shell — caller can later generate an activation code.
        if db.query(User).filter(User.email == create_email).first():
            raise HTTPException(400, "A user with this email already exists. Pass user_id to link.")
        new_user = User(
            email=create_email,
            full_name=create_full_name,
            hashed_password=get_password_hash("ChangeMe@123"),
            employee_code=data.get("employee_code"),
            is_active=True,
            is_activated=False,
        )
        db.add(new_user)
        db.flush()
        user_id = new_user.id

    # Block double-link
    if db.query(Employee).filter(Employee.user_id == user_id, Employee.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "An Employee record already exists for this user")

    # Resolve lifecycle from category
    cat = data.get("employee_category")
    if cat == EmployeeCategory.PROBATIONARY or cat == EmployeeCategory.PROBATIONARY.value:
        lifecycle = LifecycleState.ON_PROBATION
    else:
        lifecycle = LifecycleState.ACTIVE

    emp = Employee(
        user_id=user_id,
        employee_id=_next_employee_id(db),
        lifecycle_state=lifecycle,
        created_by_id=admin.id,
        last_updated_by_id=admin.id,
        **data,
    )
    db.add(emp)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(400, f"Integrity error: {exc.orig}") from exc

    # Set the back-link on the offer once we have the employee's id.
    if offer_obj is not None:
        offer_obj.employee_id = emp.id

    history_reason = "Created via HR Employees"
    if offer_obj is not None:
        history_reason = f"Onboarded from offer {offer_obj.offer_code}"

    _write_history(
        db,
        employee=emp,
        change_type=EmployeeChangeType.HIRED,
        before=None,
        after=_serialise_employee_snapshot(emp),
        actor_id=admin.id,
        reason=history_reason,
        effective_date=emp.joining_date or datetime.utcnow().date(),
    )

    # Auto-bootstrap onboarding spine (checklist, documents, identity, welcome kit,
    # default accounts, mandatory training). Same transaction so partial failure rolls back.
    bootstrap_onboarding(db, employee=emp, offer=offer_obj, actor_id=admin.id)

    db.commit()
    emp = _load_with_relations(db, emp.id)
    return _to_detail(emp, reveal_bank=False)


# ─────────────────────────────────── Update ───────────────────────────────────

@router.patch("/{employee_pk}", response_model=EmployeeDetailResponse)
def update_employee(
    employee_pk: UUID,
    payload: EmployeeUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp or emp.is_deleted:
        raise HTTPException(404, "Employee not found")

    before = _serialise_employee_snapshot(emp)
    update = payload.model_dump(exclude_unset=True)
    if not update:
        return _to_detail(emp, reveal_bank=False)

    # Linked-user fields (full_name / email) are pulled out before we assign
    # the remainder to the Employee record — they live on the User model.
    user_full_name = update.pop("full_name", None)
    user_email = update.pop("email", None)

    if (user_full_name is not None or user_email is not None) and emp.user_id:
        user = db.query(User).filter(User.id == emp.user_id).first()
        if not user:
            raise HTTPException(409, "Linked user account is missing")
        if user_email is not None and user_email != user.email:
            # Enforce uniqueness against other users
            existing = (
                db.query(User.id)
                .filter(User.email == user_email, User.id != user.id)
                .first()
            )
            if existing:
                raise HTTPException(409, "Email is already in use by another user")
            user.email = user_email
        if user_full_name is not None:
            user.full_name = user_full_name

    for k, v in update.items():
        setattr(emp, k, v)
    emp.last_updated_by_id = admin.id

    db.flush()
    after = _serialise_employee_snapshot(emp)

    _write_history(
        db,
        employee=emp,
        change_type=EmployeeChangeType.PROFILE_UPDATED,
        before=before,
        after=after,
        actor_id=admin.id,
        reason=None,
        effective_date=datetime.utcnow().date(),
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


# ─────────────────────────────────── Delete (soft + hard) ───────────────────────────────────

@router.delete("/{employee_pk}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    employee_pk: UUID,
    force: bool = Query(False, description="When true, the employee row is permanently deleted (admin-only, irreversible)."),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = db.query(Employee).filter(Employee.id == employee_pk).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    if force:
        # HARD delete — row + history rows are removed. The linked User
        # account is preserved (other modules may reference it via FK).
        db.query(EmployeeHistory).filter(EmployeeHistory.employee_id == emp.id).delete(synchronize_session=False)
        db.delete(emp)
        db.commit()
        return None

    # SOFT delete (default): mark archived + soft-delete flag. Restorable.
    if emp.is_deleted:
        return None
    before = _serialise_employee_snapshot(emp)
    emp.is_deleted = True
    emp.lifecycle_state = LifecycleState.ARCHIVED
    emp.archived_at = datetime.utcnow()
    emp.archived_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.ARCHIVED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason="Soft-deleted by admin",
        effective_date=datetime.utcnow().date(),
    )
    db.commit()
    return None


# ─────────────────────────────────── Lifecycle ───────────────────────────────────

def _require_state(emp: Employee, allowed: List[LifecycleState]):
    if emp.lifecycle_state not in allowed:
        raise HTTPException(
            409,
            f"Invalid lifecycle transition: employee is {emp.lifecycle_state.value if hasattr(emp.lifecycle_state, 'value') else emp.lifecycle_state}; expected one of {[s.value for s in allowed]}",
        )


@router.post("/{employee_pk}/lifecycle/confirm", response_model=EmployeeDetailResponse)
def lifecycle_confirm(
    employee_pk: UUID,
    body: LifecycleConfirmBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    _require_state(emp, [LifecycleState.ON_PROBATION])
    before = _serialise_employee_snapshot(emp)
    eff = body.effective_date or datetime.utcnow().date()
    emp.lifecycle_state = LifecycleState.ACTIVE
    emp.confirmation_date = eff
    emp.employee_category = EmployeeCategory.PERMANENT
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.CONFIRMED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason, effective_date=eff,
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/put-on-probation", response_model=EmployeeDetailResponse)
def lifecycle_put_on_probation(
    employee_pk: UUID,
    body: LifecyclePutOnProbationBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Move an ACTIVE employee onto probation.

    Sets `lifecycle_state = ON_PROBATION`, `employee_category = PROBATIONARY`,
    and records the probation window. Confirmation date defaults to today + N months.
    """
    import calendar
    from datetime import date as _date
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    _require_state(emp, [LifecycleState.ACTIVE])
    before = _serialise_employee_snapshot(emp)
    eff = body.effective_date or datetime.utcnow().date()
    months = body.probation_months or 6
    emp.lifecycle_state = LifecycleState.ON_PROBATION
    emp.employee_category = EmployeeCategory.PROBATIONARY
    emp.probation_months = months
    # Confirmation date is when probation ends — caller can override
    if body.confirmation_date:
        emp.confirmation_date = body.confirmation_date
    else:
        # Pure-stdlib month math (avoid dateutil dependency)
        y = eff.year + (eff.month - 1 + months) // 12
        m = (eff.month - 1 + months) % 12 + 1
        day = min(eff.day, calendar.monthrange(y, m)[1])
        emp.confirmation_date = _date(y, m, day)
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.PROFILE_UPDATED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id,
        reason=body.reason or f"Placed on probation for {months} month(s)",
        effective_date=eff,
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/promote", response_model=EmployeeDetailResponse)
def lifecycle_promote(
    employee_pk: UUID,
    body: LifecyclePromoteBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    _require_state(emp, [LifecycleState.ACTIVE, LifecycleState.ON_PROBATION])
    if not db.query(Designation).filter(Designation.id == body.new_designation_id).first():
        raise HTTPException(400, "new_designation_id not found")
    before = _serialise_employee_snapshot(emp)
    emp.designation_id = body.new_designation_id
    if body.new_grade_id:
        if not db.query(Grade).filter(Grade.id == body.new_grade_id).first():
            raise HTTPException(400, "new_grade_id not found")
        emp.grade_id = body.new_grade_id
    if body.new_pay_level:
        emp.pay_level = body.new_pay_level
    if body.new_monthly_ctc is not None:
        emp.monthly_ctc = body.new_monthly_ctc
        emp.annual_ctc = body.new_monthly_ctc * 12
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.PROMOTED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason,
        effective_date=body.effective_date or datetime.utcnow().date(),
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/transfer", response_model=EmployeeDetailResponse)
def lifecycle_transfer(
    employee_pk: UUID,
    body: LifecycleTransferBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    _require_state(emp, [LifecycleState.ACTIVE, LifecycleState.ON_PROBATION])
    before = _serialise_employee_snapshot(emp)
    if body.new_department_id:
        if not db.query(Department).filter(Department.id == body.new_department_id).first():
            raise HTTPException(400, "new_department_id not found")
        emp.department_id = body.new_department_id
    if body.new_work_location_id:
        if not db.query(WorkLocation).filter(WorkLocation.id == body.new_work_location_id).first():
            raise HTTPException(400, "new_work_location_id not found")
        emp.work_location_id = body.new_work_location_id
    if body.new_reporting_manager_id:
        if not db.query(User).filter(User.id == body.new_reporting_manager_id).first():
            raise HTTPException(400, "new_reporting_manager_id not found")
        emp.reporting_manager_id = body.new_reporting_manager_id
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.TRANSFERRED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason,
        effective_date=body.effective_date or datetime.utcnow().date(),
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/suspend", response_model=EmployeeDetailResponse)
def lifecycle_suspend(
    employee_pk: UUID,
    body: LifecycleSuspendBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    _require_state(emp, [LifecycleState.ACTIVE, LifecycleState.ON_PROBATION])
    before = _serialise_employee_snapshot(emp)
    emp.lifecycle_state = LifecycleState.SUSPENDED
    emp.suspension_reason = body.reason
    emp.suspension_date = body.effective_date or datetime.utcnow().date()
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.SUSPENDED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason,
        effective_date=emp.suspension_date,
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/reinstate", response_model=EmployeeDetailResponse)
def lifecycle_reinstate(
    employee_pk: UUID,
    body: LifecycleReinstateBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    _require_state(emp, [LifecycleState.SUSPENDED])
    before = _serialise_employee_snapshot(emp)
    emp.lifecycle_state = LifecycleState.ACTIVE
    emp.suspension_reason = None
    emp.suspension_date = None
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.REINSTATED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason,
        effective_date=body.effective_date or datetime.utcnow().date(),
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/give-notice", response_model=EmployeeDetailResponse)
def lifecycle_give_notice(
    employee_pk: UUID,
    body: LifecycleGiveNoticeBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    _require_state(emp, [LifecycleState.ACTIVE, LifecycleState.ON_PROBATION])
    if body.last_working_date < body.notice_period_start_date:
        raise HTTPException(400, "last_working_date must be >= notice_period_start_date")
    before = _serialise_employee_snapshot(emp)
    emp.lifecycle_state = LifecycleState.ON_NOTICE
    emp.notice_period_start_date = body.notice_period_start_date
    emp.last_working_date = body.last_working_date
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.NOTICE_SERVED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason,
        effective_date=body.notice_period_start_date,
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/exit", response_model=EmployeeDetailResponse)
def lifecycle_exit(
    employee_pk: UUID,
    body: LifecycleExitBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    _require_state(emp, [LifecycleState.ON_NOTICE, LifecycleState.ACTIVE, LifecycleState.SUSPENDED])
    before = _serialise_employee_snapshot(emp)
    emp.lifecycle_state = LifecycleState.EXITED
    emp.exit_date = body.exit_date
    emp.last_working_date = body.exit_date
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.EXITED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason,
        effective_date=body.exit_date,
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/archive", response_model=EmployeeDetailResponse)
def lifecycle_archive(
    employee_pk: UUID,
    body: LifecycleArchiveBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    before = _serialise_employee_snapshot(emp)
    emp.lifecycle_state = LifecycleState.ARCHIVED
    emp.archived_at = datetime.utcnow()
    emp.archived_by_id = admin.id
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.ARCHIVED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason,
        effective_date=body.effective_date or datetime.utcnow().date(),
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


@router.post("/{employee_pk}/lifecycle/unarchive", response_model=EmployeeDetailResponse)
def lifecycle_unarchive(
    employee_pk: UUID,
    body: LifecycleArchiveBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Restore an archived employee. Lifecycle state is inferred from the
    presence of date markers (exit_date → EXITED, last_working_date set with
    no exit → ON_NOTICE, suspension_date set → SUSPENDED, otherwise ACTIVE).
    Soft-delete flag (is_deleted) is also cleared if it was set."""
    _track_actor(db, admin.id)
    emp = _load_with_relations(db, employee_pk)
    if not emp:
        raise HTTPException(404, "Employee not found")
    if emp.lifecycle_state != LifecycleState.ARCHIVED:
        raise HTTPException(409, f"Cannot unarchive — employee is {emp.lifecycle_state}")
    before = _serialise_employee_snapshot(emp)

    # Restore the most plausible pre-archive state from the data we still hold.
    if emp.exit_date is not None:
        emp.lifecycle_state = LifecycleState.EXITED
    elif emp.notice_period_start_date is not None and emp.last_working_date is not None:
        emp.lifecycle_state = LifecycleState.ON_NOTICE
    elif emp.suspension_date is not None and emp.suspension_reason:
        emp.lifecycle_state = LifecycleState.SUSPENDED
    else:
        emp.lifecycle_state = LifecycleState.ACTIVE
    emp.archived_at = None
    emp.archived_by_id = None
    emp.is_deleted = False
    emp.last_updated_by_id = admin.id
    db.flush()
    _write_history(
        db, emp, EmployeeChangeType.REINSTATED,
        before=before, after=_serialise_employee_snapshot(emp),
        actor_id=admin.id, reason=body.reason or "Restored from archive",
        effective_date=body.effective_date or datetime.utcnow().date(),
    )
    db.commit()
    db.refresh(emp)
    return _to_detail(emp, reveal_bank=False)


# ─────────────────────────────────── History ───────────────────────────────────

@router.get("/history/all", response_model=List[EmployeeHistoryResponse])
def list_all_history(
    change_type: Optional[str] = None,
    from_date: Optional[str] = None,  # ISO date
    to_date: Optional[str] = None,
    employee_search: Optional[str] = None,   # name / email / employee_id
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = (
        db.query(EmployeeHistory)
        .join(Employee, EmployeeHistory.employee_id == Employee.id)
        .options(joinedload(EmployeeHistory.actioned_by), joinedload(EmployeeHistory.employee).joinedload(Employee.user))
    )
    if change_type:
        q = q.filter(EmployeeHistory.change_type == change_type)
    if from_date:
        q = q.filter(EmployeeHistory.effective_date >= from_date)
    if to_date:
        q = q.filter(EmployeeHistory.effective_date <= to_date)
    if employee_search:
        like = f"%{employee_search.strip()}%"
        q = q.join(User, Employee.user_id == User.id, isouter=True).filter(
            or_(
                Employee.employee_id.ilike(like),
                User.full_name.ilike(like),
                User.email.ilike(like),
            )
        )
    rows = q.order_by(EmployeeHistory.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "employee_id": r.employee_id,
            "change_type": r.change_type.value if hasattr(r.change_type, "value") else r.change_type,
            "from_value_json": r.from_value_json,
            "to_value_json": r.to_value_json,
            "effective_date": r.effective_date,
            "reason": r.reason,
            "actioned_by_id": r.actioned_by_id,
            "created_at": r.created_at,
            "actioned_by_name": getattr(r.actioned_by, "full_name", None) if r.actioned_by else None,
            "employee_name": (r.employee.user.full_name if (r.employee and r.employee.user) else None),
            "employee_code": (r.employee.employee_id if r.employee else None),
        })
    return out


@router.get("/{employee_pk}/history", response_model=List[EmployeeHistoryResponse])
def list_employee_history(
    employee_pk: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not db.query(Employee.id).filter(Employee.id == employee_pk).first():
        raise HTTPException(404, "Employee not found")
    rows = (
        db.query(EmployeeHistory)
        .options(joinedload(EmployeeHistory.actioned_by))
        .filter(EmployeeHistory.employee_id == employee_pk)
        .order_by(EmployeeHistory.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "employee_id": r.employee_id,
            "change_type": r.change_type.value if hasattr(r.change_type, "value") else r.change_type,
            "from_value_json": r.from_value_json,
            "to_value_json": r.to_value_json,
            "effective_date": r.effective_date,
            "reason": r.reason,
            "actioned_by_id": r.actioned_by_id,
            "created_at": r.created_at,
            "actioned_by_name": getattr(r.actioned_by, "full_name", None) if r.actioned_by else None,
        }
        for r in rows
    ]


@router.post("/bulk-import", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def bulk_import_employees(
    admin: User = Depends(get_current_superuser),
):
    """CSV import — UI in Phase 1.0, implementation in Phase 1.1."""
    raise HTTPException(501, "Bulk import lands in Phase 1.1")


@router.get("/export/csv", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def export_employees_csv(
    admin: User = Depends(get_current_superuser),
):
    raise HTTPException(501, "CSV export lands in Phase 1.1")
