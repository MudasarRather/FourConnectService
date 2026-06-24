"""HR Account Provisioning schemas."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.account_provisioning import (
    AccountType, AccountProvisioningStatus,
)


class AccountProvisioningCreate(BaseModel):
    employee_id: UUID
    process_id: Optional[UUID] = None
    account_type: AccountType
    system_username: Optional[str] = None
    notes: Optional[str] = None


class AccountProvisioningUpdate(BaseModel):
    system_username: Optional[str] = None
    status: Optional[AccountProvisioningStatus] = None
    notes: Optional[str] = None


class AccountCredentialsBody(BaseModel):
    """Grant / reset the ERP login for the employee's linked User account.

    ``password`` is HR-supplied; set ``auto_generate`` to mint a strong one
    server-side (returned once in the response). ``activate`` flips the linked
    User to is_active + is_activated so they can log in immediately.
    """
    password: Optional[str] = None
    auto_generate: bool = False
    activate: bool = True


class AccountProvisioningResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    process_id: Optional[UUID] = None
    account_type: AccountType
    system_username: Optional[str] = None
    status: AccountProvisioningStatus
    requested_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    notes: Optional[str] = None

    # ── ERP login bridge (populated only for the ERP account_type) ──
    has_login: bool = False
    login_user_id: Optional[UUID] = None
    login_email: Optional[str] = None          # the username = work email
    login_is_active: Optional[bool] = None      # False = login disabled
    login_is_activated: Optional[bool] = None   # passed the activation gate
    generated_password: Optional[str] = None    # returned ONCE by set-credentials
