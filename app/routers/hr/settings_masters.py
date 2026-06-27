"""HR Settings — configurable workforce-taxonomy masters.

Exposes CRUD for the three master tables (employment types, employee categories,
separation reasons) under ``/hr/settings/masters/*``. ``is_system`` rows back live
enum values: their code is immutable and they can't be hard-deleted (deactivate
instead), so existing Employee / ExitCase rows that store the enum string keep
resolving.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.settings_master import (
    EmploymentTypeMaster, EmployeeCategoryMaster, SeparationReasonMaster,
)
from app.models.hr.exit_case import ExitCase
from app.schemas.hr.settings_master import (
    SimpleMasterCreate, SimpleMasterUpdate, SimpleMasterResponse,
    SeparationReasonCreate, SeparationReasonUpdate, SeparationReasonResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/settings/masters", tags=["HR — Settings Masters"])


def _track(db: Session, admin: User):
    db.info["audit_actor_id"] = str(admin.id)


def _list(db: Session, model):
    return (db.query(model)
            .filter(model.is_deleted == False)  # noqa: E712
            .order_by(model.sort_order, model.code)
            .all())


def _create(db: Session, admin, model, data: dict, *, sep: bool = False):
    _track(db, admin)
    q = db.query(model).filter(model.code == data["code"], model.is_deleted == False)  # noqa: E712
    if sep:
        q = q.filter(model.category == data.get("category", "EXIT_REASON"))
    if q.first():
        raise HTTPException(409, "A value with this code already exists")
    row = model(**data)
    db.add(row)
    db.flush()
    log_settings_change(db, model.__name__, row.id, "CREATE", admin.id, after={"code": data.get("code")}, note=data.get("label"))
    db.commit()
    db.refresh(row)
    return row


def _get(db: Session, model, row_id: UUID):
    row = db.query(model).filter(model.id == row_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    return row


def _update(db: Session, admin, model, row_id: UUID, data: dict, *, sep: bool = False):
    _track(db, admin)
    row = _get(db, model, row_id)
    if "code" in data and data["code"] != row.code:
        if row.is_system:
            raise HTTPException(400, "Built-in value codes are immutable")
        dup = db.query(model).filter(model.code == data["code"], model.id != row_id, model.is_deleted == False)  # noqa: E712
        if sep:
            dup = dup.filter(model.category == (data.get("category") or row.category))
        if dup.first():
            raise HTTPException(409, "A value with this code already exists")
    for k, v in data.items():
        setattr(row, k, v)
    log_settings_change(db, model.__name__, row.id, "UPDATE", admin.id, note=getattr(row, "label", None))
    db.commit()
    db.refresh(row)
    return row


def _delete(db: Session, admin, model, row_id: UUID, *, reason: Optional[str] = None,
            usage: int = 0, noun: str = "employee"):
    _track(db, admin)
    row = _get(db, model, row_id)
    if row.is_system:
        raise HTTPException(400, "Built-in values can't be deleted — deactivate them instead")
    if usage > 0:
        raise HTTPException(
            409,
            f"This value is still referenced by {usage} {noun}{'s' if usage != 1 else ''} — "
            "reassign them, or deactivate it instead of deleting.",
        )
    row.is_deleted = True
    note = (reason.strip() if reason and reason.strip() else None) or getattr(row, "label", None)
    log_settings_change(db, model.__name__, row.id, "DELETE", admin.id, note=note)
    db.commit()


def _employment_usage(db: Session) -> dict:
    """Live (non-deleted) employee count per employment-type enum code. Built-in
    codes mirror the `EmploymentType` enum; custom codes can't be stored on the
    enum column yet, so they always come back 0 (safe to delete)."""
    rows = (
        db.query(Employee.employment_type, func.count(Employee.id))
        .filter(Employee.is_deleted == False)  # noqa: E712
        .group_by(Employee.employment_type)
        .all()
    )
    out = {}
    for et, n in rows:
        if et is None:
            continue
        out[et.value if hasattr(et, "value") else str(et)] = int(n)
    return out


def _category_usage(db: Session) -> dict:
    """Live (non-deleted) employee count per employee-category enum code. Built-in
    codes mirror the `EmployeeCategory` enum (PERMANENT/PROBATIONARY/…); custom
    codes can't be stored on the enum column yet, so they always come back 0."""
    rows = (
        db.query(Employee.employee_category, func.count(Employee.id))
        .filter(Employee.is_deleted == False)  # noqa: E712
        .group_by(Employee.employee_category)
        .all()
    )
    out = {}
    for ec, n in rows:
        if ec is None:
            continue
        out[ec.value if hasattr(ec, "value") else str(ec)] = int(n)
    return out


def _separation_usage(db: Session) -> dict:
    """Live exit-case counts per separation-reason code, split by vocabulary.

    ``resignation_type`` feeds the RESIGNATION_TYPE vocabulary, ``reason_category``
    feeds EXIT_REASON. Built-in codes mirror the exit enums; custom codes can't be
    stored on those enum columns yet, so they never appear here (always 0 → safe to
    delete). Grouping by the enum column only ever yields valid enum members, so we
    never bind an invalid literal (which Postgres would reject)."""
    out = {"RESIGNATION_TYPE": {}, "EXIT_REASON": {}}
    rt = (
        db.query(ExitCase.resignation_type, func.count(ExitCase.id))
        .group_by(ExitCase.resignation_type)
        .all()
    )
    for v, n in rt:
        if v is None:
            continue
        out["RESIGNATION_TYPE"][v.value if hasattr(v, "value") else str(v)] = int(n)
    rc = (
        db.query(ExitCase.reason_category, func.count(ExitCase.id))
        .group_by(ExitCase.reason_category)
        .all()
    )
    for v, n in rc:
        if v is None:
            continue
        out["EXIT_REASON"][v.value if hasattr(v, "value") else str(v)] = int(n)
    return out


# ── Employment Types ─────────────────────────────────────────────────────────
@router.get("/employment-types/", response_model=List[SimpleMasterResponse])
def list_employment_types(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _list(db, EmploymentTypeMaster)


# Live workforce composition — powers the engagement-orbit headcounts, card
# share bars and the delete pre-flight. Keyed by enum code (e.g. "FULL_TIME").
@router.get("/employment-types/usage")
def employment_types_usage(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return {"items": _employment_usage(db)}


@router.post("/employment-types/", response_model=SimpleMasterResponse, status_code=201)
def create_employment_type(payload: SimpleMasterCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _create(db, admin, EmploymentTypeMaster, payload.model_dump())


@router.patch("/employment-types/{row_id}", response_model=SimpleMasterResponse)
def update_employment_type(row_id: UUID, payload: SimpleMasterUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _update(db, admin, EmploymentTypeMaster, row_id, payload.model_dump(exclude_unset=True))


@router.delete("/employment-types/{row_id}", status_code=204)
def delete_employment_type(
    row_id: UUID,
    reason: Optional[str] = Query(None, description="Optional removal reason, recorded in the settings ledger."),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    # Pre-flight: refuse if any live employee is still engaged under this code.
    row = db.query(EmploymentTypeMaster).filter(EmploymentTypeMaster.id == row_id).first()
    usage = 0
    if row is not None and not row.is_system:
        usage = (
            db.query(func.count(Employee.id))
            .filter(Employee.employment_type == row.code, Employee.is_deleted == False)  # noqa: E712
            .scalar()
        ) or 0
    _delete(db, admin, EmploymentTypeMaster, row_id, reason=reason, usage=usage)


# ── Employee Categories ──────────────────────────────────────────────────────
@router.get("/employee-categories/", response_model=List[SimpleMasterResponse])
def list_employee_categories(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _list(db, EmployeeCategoryMaster)


# Live workforce composition — powers the cohort-strata headcounts, card fill
# gauges and the delete pre-flight. Keyed by enum code (e.g. "PERMANENT").
@router.get("/employee-categories/usage")
def employee_categories_usage(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return {"items": _category_usage(db)}


@router.post("/employee-categories/", response_model=SimpleMasterResponse, status_code=201)
def create_employee_category(payload: SimpleMasterCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _create(db, admin, EmployeeCategoryMaster, payload.model_dump())


@router.patch("/employee-categories/{row_id}", response_model=SimpleMasterResponse)
def update_employee_category(row_id: UUID, payload: SimpleMasterUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _update(db, admin, EmployeeCategoryMaster, row_id, payload.model_dump(exclude_unset=True))


@router.delete("/employee-categories/{row_id}", status_code=204)
def delete_employee_category(
    row_id: UUID,
    reason: Optional[str] = Query(None, description="Optional removal reason, recorded in the settings ledger."),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    # Pre-flight: refuse if any live employee is still classified under this code.
    row = db.query(EmployeeCategoryMaster).filter(EmployeeCategoryMaster.id == row_id).first()
    usage = 0
    if row is not None and not row.is_system:
        usage = (
            db.query(func.count(Employee.id))
            .filter(Employee.employee_category == row.code, Employee.is_deleted == False)  # noqa: E712
            .scalar()
        ) or 0
    _delete(db, admin, EmployeeCategoryMaster, row_id, reason=reason, usage=usage)


# ── Separation Reasons ───────────────────────────────────────────────────────
@router.get("/separation-reasons/", response_model=List[SeparationReasonResponse])
def list_separation_reasons(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _list(db, SeparationReasonMaster)


# Live exit-case consumption per reason code, split by vocabulary
# ({"RESIGNATION_TYPE": {code: n}, "EXIT_REASON": {code: n}}). Powers the
# departures-board status lamps, card "exits cited" stubs and the delete
# pre-flight — proving the reason lexicon is wired to real Exit Management data.
@router.get("/separation-reasons/usage")
def separation_reasons_usage(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return {"items": _separation_usage(db)}


@router.post("/separation-reasons/", response_model=SeparationReasonResponse, status_code=201)
def create_separation_reason(payload: SeparationReasonCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _create(db, admin, SeparationReasonMaster, payload.model_dump(), sep=True)


@router.patch("/separation-reasons/{row_id}", response_model=SeparationReasonResponse)
def update_separation_reason(row_id: UUID, payload: SeparationReasonUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return _update(db, admin, SeparationReasonMaster, row_id, payload.model_dump(exclude_unset=True), sep=True)


@router.delete("/separation-reasons/{row_id}", status_code=204)
def delete_separation_reason(
    row_id: UUID,
    reason: Optional[str] = Query(None, description="Optional removal reason, recorded in the settings ledger."),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    # Pre-flight: refuse if any exit case still cites this reason. Custom codes can't
    # be stored on the exit enum columns, so their usage is always 0 (safe to delete);
    # built-in codes can be referenced but are deletion-locked anyway (deactivate only).
    row = db.query(SeparationReasonMaster).filter(SeparationReasonMaster.id == row_id).first()
    usage = 0
    if row is not None and not row.is_system:
        usage = _separation_usage(db).get(row.category, {}).get(row.code, 0)
    _delete(db, admin, SeparationReasonMaster, row_id, reason=reason, usage=usage, noun="exit case")
