"""HR Account Provisioning — IT setup per employee."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.account_provisioning import (
    AccountProvisioning, AccountType, AccountProvisioningStatus,
)
from app.schemas.hr.account_provisioning import (
    AccountProvisioningCreate, AccountProvisioningUpdate, AccountProvisioningResponse,
)
from app.utils.dependencies import get_current_superuser


router = APIRouter(prefix="/hr/account-provisioning", tags=["HR — Account Provisioning"])


def _to_response(ap: AccountProvisioning) -> AccountProvisioningResponse:
    return AccountProvisioningResponse(
        id=ap.id, employee_id=ap.employee_id, process_id=ap.process_id,
        account_type=ap.account_type, system_username=ap.system_username,
        status=ap.status, requested_at=ap.requested_at, activated_at=ap.activated_at,
        revoked_at=ap.revoked_at, notes=ap.notes,
    )


@router.get("/by-employee/{employee_id}", response_model=List[AccountProvisioningResponse])
def by_employee(
    employee_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(AccountProvisioning)
        .filter(AccountProvisioning.employee_id == employee_id)
        .order_by(AccountProvisioning.created_at.asc())
        .all()
    )
    return [_to_response(r) for r in rows]


@router.post("/", response_model=AccountProvisioningResponse, status_code=http_status.HTTP_201_CREATED)
def create_account(
    payload: AccountProvisioningCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not db.query(Employee).filter(Employee.id == payload.employee_id).first():
        raise HTTPException(404, "Employee not found")
    existing = db.query(AccountProvisioning).filter(
        AccountProvisioning.employee_id == payload.employee_id,
        AccountProvisioning.account_type == payload.account_type,
    ).first()
    if existing:
        raise HTTPException(409, f"{payload.account_type.value} account already exists for this employee")
    ap = AccountProvisioning(
        **payload.model_dump(),
        requested_at=datetime.utcnow(),
        requested_by_user_id=admin.id,
        status=AccountProvisioningStatus.REQUESTED,
    )
    db.add(ap)
    db.commit()
    db.refresh(ap)
    return _to_response(ap)


@router.patch("/{ap_id}", response_model=AccountProvisioningResponse)
def update_account(
    ap_id: UUID,
    payload: AccountProvisioningUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    ap = db.query(AccountProvisioning).filter(AccountProvisioning.id == ap_id).first()
    if not ap:
        raise HTTPException(404, "Account record not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(ap, k, v)
    db.commit()
    db.refresh(ap)
    return _to_response(ap)


@router.post("/{ap_id}/activate", response_model=AccountProvisioningResponse)
def activate(
    ap_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    ap = db.query(AccountProvisioning).filter(AccountProvisioning.id == ap_id).first()
    if not ap:
        raise HTTPException(404, "Not found")
    if ap.status == AccountProvisioningStatus.ACTIVE:
        raise HTTPException(409, "Already active")
    ap.status = AccountProvisioningStatus.ACTIVE
    ap.activated_at = datetime.utcnow()
    ap.fulfilled_by_user_id = admin.id
    db.commit()
    db.refresh(ap)
    return _to_response(ap)


@router.post("/{ap_id}/revoke", response_model=AccountProvisioningResponse)
def revoke(
    ap_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    ap = db.query(AccountProvisioning).filter(AccountProvisioning.id == ap_id).first()
    if not ap:
        raise HTTPException(404, "Not found")
    ap.status = AccountProvisioningStatus.REVOKED
    ap.revoked_at = datetime.utcnow()
    db.commit()
    db.refresh(ap)
    return _to_response(ap)


@router.delete("/{ap_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_account(
    ap_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    ap = db.query(AccountProvisioning).filter(AccountProvisioning.id == ap_id).first()
    if not ap:
        raise HTTPException(404, "Not found")
    db.delete(ap)
    db.commit()
