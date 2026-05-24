"""HR Account provisioning — IT system setup tracking per employee.

One row per account-type per employee (ERP, EMAIL, VPN, BIOMETRIC, ATTENDANCE,
RFID_SYSTEM, GIT, SLACK, OTHER). Tracks request → fulfillment → revocation.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class AccountType(str, enum.Enum):
    ERP = "ERP"
    EMAIL = "EMAIL"
    VPN = "VPN"
    BIOMETRIC = "BIOMETRIC"
    ATTENDANCE = "ATTENDANCE"
    RFID_SYSTEM = "RFID_SYSTEM"
    GIT = "GIT"
    SLACK = "SLACK"
    DRIVE = "DRIVE"
    OTHER = "OTHER"


class AccountProvisioningStatus(str, enum.Enum):
    PENDING = "PENDING"
    REQUESTED = "REQUESTED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    FAILED = "FAILED"


class AccountProvisioning(Base):
    __tablename__ = "hr_account_provisioning"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    process_id = Column(UUID(as_uuid=True), ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True, index=True)

    account_type = Column(Enum(AccountType, name="hr_account_type"), nullable=False, index=True)
    system_username = Column(String(200), nullable=True)
    status = Column(
        Enum(AccountProvisioningStatus, name="hr_account_provisioning_status"),
        nullable=False, default=AccountProvisioningStatus.PENDING, index=True,
    )
    requested_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    fulfilled_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_hr_account_emp_type", "employee_id", "account_type", unique=True),
    )

    employee = relationship("Employee", foreign_keys=[employee_id])
