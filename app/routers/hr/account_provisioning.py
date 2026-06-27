"""HR Account Provisioning — IT setup per employee.

The **ERP** account_type is special: it is the employee's actual login. Granting /
revoking it drives the linked ``User`` (``Employee.user_id``) — set password,
flip ``is_active`` + ``is_activated`` — so the joiner can sign in at
``/authentication/user/login`` directly. For HR-onboarded employees this replaces
the self-service whitelist + 8-digit activation-code path (those remain for
employees who self-register at ``/auth/signup``).
"""
from __future__ import annotations

import secrets
import string
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
    AccountCredentialsBody,
)
from app.utils.auth import get_password_hash
from app.utils.dependencies import get_current_superuser
from app.utils.hr.lifecycle_guard import guard_employable


router = APIRouter(prefix="/hr/account-provisioning", tags=["HR — Account Provisioning"])


def _erp_user(db: Session, ap: AccountProvisioning) -> Optional[User]:
    """Resolve the login User behind an ERP provisioning row (via Employee.user_id)."""
    emp = db.query(Employee).filter(Employee.id == ap.employee_id).first()
    if not emp or not getattr(emp, "user_id", None):
        return None
    return db.query(User).filter(User.id == emp.user_id).first()


def _gen_password(n: int = 12) -> str:
    """Strong random password with mixed classes for first-login handover."""
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%*?"]
    chars = [secrets.choice(p) for p in pools]
    alphabet = "".join(pools)
    chars += [secrets.choice(alphabet) for _ in range(max(0, n - len(chars)))]
    # deterministic-free shuffle via secrets
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def _to_response(ap: AccountProvisioning, db: Optional[Session] = None) -> AccountProvisioningResponse:
    resp = AccountProvisioningResponse(
        id=ap.id, employee_id=ap.employee_id, process_id=ap.process_id,
        account_type=ap.account_type, system_username=ap.system_username,
        status=ap.status, requested_at=ap.requested_at, activated_at=ap.activated_at,
        revoked_at=ap.revoked_at, notes=ap.notes,
    )
    if db is not None and ap.account_type == AccountType.ERP:
        user = _erp_user(db, ap)
        if user is not None:
            resp.has_login = True
            resp.login_user_id = user.id
            resp.login_email = user.email
            resp.login_is_active = bool(user.is_active)
            resp.login_is_activated = bool(user.is_activated)
    return resp


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
    return [_to_response(r, db) for r in rows]


@router.post("/", response_model=AccountProvisioningResponse, status_code=http_status.HTTP_201_CREATED)
def create_account(
    payload: AccountProvisioningCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id).first()
    guard_employable(emp, "provision a system account for this employee")
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
    # For the ERP login, default the system username to the linked work email.
    if ap.account_type == AccountType.ERP and not ap.system_username:
        u = _erp_user(db, ap)
        if u:
            ap.system_username = u.email
    db.add(ap)
    db.commit()
    db.refresh(ap)
    return _to_response(ap, db)


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
    return _to_response(ap, db)


@router.post("/{ap_id}/set-credentials", response_model=AccountProvisioningResponse)
def set_credentials(
    ap_id: UUID,
    payload: AccountCredentialsBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Grant / reset the ERP login: set the linked User's password and (optionally)
    activate the account so the employee can sign in directly — no whitelist or
    8-digit activation code required for HR-onboarded employees."""
    ap = db.query(AccountProvisioning).filter(AccountProvisioning.id == ap_id).first()
    if not ap:
        raise HTTPException(404, "Account record not found")
    if ap.account_type != AccountType.ERP:
        raise HTTPException(400, "Login credentials only apply to the ERP login account")
    # Never grant / re-enable a login for someone who is leaving or has left —
    # this is the gate Exit→Clearance's revoke-erp relies on staying shut.
    emp = db.query(Employee).filter(Employee.id == ap.employee_id).first()
    guard_employable(emp, "grant or reset an ERP login for this employee")
    user = _erp_user(db, ap)
    if user is None:
        raise HTTPException(
            409,
            "This employee has no linked user account. Create the employee with a "
            "work email (create_email) so a login can be provisioned.",
        )

    generated: Optional[str] = None
    pwd = (payload.password or "").strip()
    if payload.auto_generate:
        pwd = _gen_password()
        generated = pwd
    # set-credentials MUST set a password — that is its entire purpose. Previously
    # an empty password silently skipped the hash update while still returning 200
    # (and activating), so a "Reset password" with a blank field LEFT THE OLD
    # PASSWORD VALID — a serious security hole (the reset appeared to succeed but
    # nothing changed). Reject it so a reset can never silently fail; pure
    # re-activation without a password change lives on the /activate endpoint.
    if not pwd:
        raise HTTPException(400, "A new password is required to set or reset the ERP login — type one or use auto-generate.")
    if len(pwd) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    user.hashed_password = get_password_hash(pwd)
    # Force-logout any live session: bumping token_version invalidates every JWT
    # minted before this reset (see dependencies.get_current_user). The employee
    # must sign in again with the new password.
    user.token_version = (user.token_version or 1) + 1

    if payload.activate:
        user.is_active = True
        user.is_activated = True          # clears the post-login activation gate
        user.activation_code = None       # any pending self-service code is moot
        ap.status = AccountProvisioningStatus.ACTIVE
        ap.activated_at = datetime.utcnow()
        ap.fulfilled_by_user_id = admin.id

    if not ap.system_username:
        ap.system_username = user.email

    db.commit()
    db.refresh(ap)
    db.refresh(user)
    resp = _to_response(ap, db)
    resp.generated_password = generated
    return resp


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
    # ERP = the real login → block enabling it for a leaving / exited employee.
    if ap.account_type == AccountType.ERP:
        emp = db.query(Employee).filter(Employee.id == ap.employee_id).first()
        guard_employable(emp, "activate the ERP login for this employee")
    ap.status = AccountProvisioningStatus.ACTIVE
    ap.activated_at = datetime.utcnow()
    ap.fulfilled_by_user_id = admin.id
    # ERP = the real login → enable + clear the activation gate on the linked User.
    if ap.account_type == AccountType.ERP:
        user = _erp_user(db, ap)
        if user is not None:
            user.is_active = True
            user.is_activated = True
            user.activation_code = None
            if not ap.system_username:
                ap.system_username = user.email
    db.commit()
    db.refresh(ap)
    return _to_response(ap, db)


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
    # ERP = the real login → disable sign-in for the linked User.
    if ap.account_type == AccountType.ERP:
        user = _erp_user(db, ap)
        if user is not None:
            user.is_active = False
    db.commit()
    db.refresh(ap)
    return _to_response(ap, db)


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
