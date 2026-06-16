"""HR Payroll — Tax documents (Form-16 / TDS certificates).

One row records that a tax document has been *issued* for an employee + fiscal
year. The PDF itself is rendered on demand from the employee's RELEASED payslip
statutory data (see ``app/utils/hr/form16_pdf.py``) — we snapshot the headline
totals here so the list view + audit are faithful even if payslips change later.

An employee only sees PUBLISHED documents for their own profile (self-service
ownership pattern, mirrors payslips). Admins generate / publish.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class TaxDocType(str, enum.Enum):
    FORM16 = "FORM16"
    TDS_CERTIFICATE = "TDS_CERTIFICATE"


class TaxDocStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class TaxDocument(Base):
    __tablename__ = "hr_tax_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)

    fiscal_year = Column(String(9), nullable=False)   # e.g. "2025-26"
    doc_type = Column(Enum(TaxDocType, name="hr_tax_doc_type"), nullable=False, default=TaxDocType.FORM16)
    title = Column(String(140), nullable=True)
    status = Column(Enum(TaxDocStatus, name="hr_tax_doc_status"), nullable=False, default=TaxDocStatus.DRAFT, index=True)

    # snapshot headline totals (the PDF re-derives the full detail on demand)
    tds_total = Column(Numeric(14, 2), nullable=False, default=0)
    gross_total = Column(Numeric(14, 2), nullable=False, default=0)

    generated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    generated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        UniqueConstraint("employee_id", "fiscal_year", "doc_type", name="uq_hr_tax_doc_emp_fy_type"),
        Index("ix_hr_tax_doc_emp_fy", "employee_id", "fiscal_year"),
        Index("ix_hr_tax_doc_status", "status", "is_deleted"),
    )

    def __repr__(self):
        return f"<TaxDocument {self.doc_type} {self.fiscal_year} emp={self.employee_id}>"
