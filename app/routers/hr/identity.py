"""HR Employee Identity — official email, biometric, RFID, badge."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.onboarding import EmployeeIdentity, IdentityStatus, OnboardingProcess
from app.schemas.hr.onboarding import EmployeeIdentityUpdate, EmployeeIdentityResponse
from app.utils.dependencies import get_current_superuser


router = APIRouter(prefix="/hr/identity", tags=["HR — Identity"])


def _get_or_create(db: Session, employee_id: UUID) -> EmployeeIdentity:
    ident = db.query(EmployeeIdentity).filter(EmployeeIdentity.employee_id == employee_id).first()
    if ident:
        return ident
    if not db.query(Employee).filter(Employee.id == employee_id).first():
        raise HTTPException(404, "Employee not found")
    ident = EmployeeIdentity(employee_id=employee_id, status=IdentityStatus.PENDING)
    db.add(ident)
    db.flush()
    return ident


@router.get("/{employee_id}", response_model=EmployeeIdentityResponse)
def get_identity(
    employee_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    ident = _get_or_create(db, employee_id)
    db.commit()
    return EmployeeIdentityResponse.model_validate(ident)


@router.patch("/{employee_id}", response_model=EmployeeIdentityResponse)
def update_identity(
    employee_id: UUID,
    payload: EmployeeIdentityUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    ident = _get_or_create(db, employee_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(ident, k, v)
    db.commit()
    db.refresh(ident)
    return EmployeeIdentityResponse.model_validate(ident)


@router.post("/{employee_id}/issue", response_model=EmployeeIdentityResponse)
def issue_identity(
    employee_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    ident = _get_or_create(db, employee_id)
    if not ident.official_email:
        raise HTTPException(409, "Cannot issue identity without official_email")
    ident.status = IdentityStatus.ISSUED
    ident.issued_at = datetime.utcnow()
    ident.issued_by_user_id = admin.id
    # Trigger process recalculation
    from app.routers.hr.onboarding import _recalculate_progress
    proc = db.query(OnboardingProcess).filter(OnboardingProcess.employee_id == employee_id).first()
    if proc:
        _recalculate_progress(db, proc)
    db.commit()
    db.refresh(ident)
    return EmployeeIdentityResponse.model_validate(ident)


@router.post("/{employee_id}/revoke", response_model=EmployeeIdentityResponse)
def revoke_identity(
    employee_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    ident = _get_or_create(db, employee_id)
    ident.status = IdentityStatus.REVOKED
    ident.revoked_at = datetime.utcnow()
    db.commit()
    db.refresh(ident)
    return EmployeeIdentityResponse.model_validate(ident)
