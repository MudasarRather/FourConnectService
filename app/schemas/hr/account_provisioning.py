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
