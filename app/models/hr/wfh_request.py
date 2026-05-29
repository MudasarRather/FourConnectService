"""Work-from-Home + Remote attendance requests.

One model covers both WFH (regular employee working from home) and REMOTE
(field/site staff working away from office) via the `request_type`
discriminator. Approval flow: PENDING → APPROVED / REJECTED. Past-date
approval is blocked (422) — these are forward-looking authorisations.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class WfhStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class WfhRequestType(str, enum.Enum):
    WFH = "WFH"
    REMOTE = "REMOTE"


class WfhRequest(Base):
    __tablename__ = "hr_wfh_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    request_type = Column(Enum(WfhRequestType, name="hr_wfh_request_type"), nullable=False, default=WfhRequestType.WFH)

    wfh_date = Column(Date, nullable=False, index=True)
    wfh_date_until = Column(Date, nullable=True)  # null = single-day
    reason = Column(Text, nullable=False)
    work_summary = Column(Text, nullable=True)  # submitted post-WFH
    status = Column(Enum(WfhStatus, name="hr_wfh_status"), nullable=False, default=WfhStatus.PENDING, index=True)

    manager_approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    manager_approved_at = Column(DateTime(timezone=True), nullable=True)
    decision_notes = Column(Text, nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_wfh_emp_date", "employee_id", "wfh_date"),
    )
