"""HR Payroll — Salary Structure.

A named template (e.g. "Permanent — Senior", "Consultant", "Intern") that groups
an ordered set of salary components. Employees are assigned a structure via
``EmployeeCompensation.structure_id`` (the authoritative per-period link) with
``Employee.salary_structure_id`` acting as the default fallback.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SalaryStructure(Base):
    __tablename__ = "hr_salary_structures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)   # e.g. "STR-PERM-SR"
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)

    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id", ondelete="SET NULL"), nullable=True)
    pay_scale = Column(String(60), nullable=True)

    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)

    is_default = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    # PF policy: True (default) caps PF wage at the ₹15,000 statutory ceiling (→ ₹1,800);
    # False computes PF as 12% of the full Basic. Per-structure so different employee
    # bands (e.g. permanent vs consultant) can follow different PF policies.
    pf_restrict_to_ceiling = Column(Boolean, nullable=False, default=True, server_default="true")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    components = relationship(
        "SalaryStructureComponent",
        back_populates="structure",
        cascade="all, delete-orphan",
        order_by="SalaryStructureComponent.sequence",
    )

    __table_args__ = (
        Index("ix_hr_salary_struct_active", "is_active", "is_deleted"),
    )

    def __repr__(self):
        return f"<SalaryStructure {self.code}>"
