"""HR Document Request — employee-initiated letter / certificate request.

An employee asks HR to issue a document they don't yet hold — experience
letter, salary certificate, NDA copy, etc. HR sees these in their admin queue
and either fulfils (linking the produced EmployeeDocument) or rejects with a
reason. Cancellation is allowed only while still PENDING.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class DocumentRequestType(str, enum.Enum):
    EXPERIENCE_LETTER  = "EXPERIENCE_LETTER"
    RELIEVING_LETTER   = "RELIEVING_LETTER"
    CONFIRMATION_LETTER = "CONFIRMATION_LETTER"
    APPOINTMENT_LETTER = "APPOINTMENT_LETTER"
    SALARY_CERTIFICATE = "SALARY_CERTIFICATE"
    NDA                = "NDA"
    OFFER_LETTER       = "OFFER_LETTER"
    ADDRESS_PROOF      = "ADDRESS_PROOF"
    NO_OBJECTION       = "NO_OBJECTION"
    CUSTOM             = "CUSTOM"


class DocumentRequestStatus(str, enum.Enum):
    PENDING     = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    FULFILLED   = "FULFILLED"
    REJECTED    = "REJECTED"
    CANCELLED   = "CANCELLED"


class DocumentRequest(Base):
    """One outgoing request from an employee for a document HR will issue."""
    __tablename__ = "hr_document_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False, index=True)

    request_type = Column(Enum(DocumentRequestType, name="hr_doc_request_type"),
                          nullable=False, index=True)
    custom_title = Column(String(160), nullable=True)   # only used when type=CUSTOM
    reason = Column(Text, nullable=False)
    notes  = Column(Text, nullable=True)
    purpose = Column(String(120), nullable=True)        # e.g. "Visa application", "Bank loan"

    status = Column(Enum(DocumentRequestStatus, name="hr_doc_request_status"),
                    nullable=False, default=DocumentRequestStatus.PENDING, index=True)

    # Assignment + decision audit
    assigned_to_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    fulfilled_doc_id    = Column(UUID(as_uuid=True), ForeignKey("hr_employee_documents.id"), nullable=True)

    decided_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    decided_at         = Column(DateTime(timezone=True), nullable=True)
    decision_notes     = Column(Text, nullable=True)

    cancelled_at       = Column(DateTime(timezone=True), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    # Relationships
    employee = relationship("Employee", foreign_keys=[employee_id])
    fulfilled_doc = relationship("EmployeeDocument", foreign_keys=[fulfilled_doc_id])
    decided_by = relationship("User", foreign_keys=[decided_by_user_id])
    assigned_to = relationship("User", foreign_keys=[assigned_to_user_id])

    __table_args__ = (
        Index("ix_hr_doc_req_emp_status", "employee_id", "status"),
        Index("ix_hr_doc_req_status_active", "status", "is_deleted"),
    )

    def __repr__(self):
        return f"<DocumentRequest {self.request_type} status={self.status} emp={self.employee_id}>"
