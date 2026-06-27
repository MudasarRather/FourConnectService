import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Department(Base):
    __tablename__ = "hr_departments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False, unique=True)
    code = Column(String(20), nullable=False, unique=True, index=True)
    parent_department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True)
    head_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=True)
    cost_center = Column(String(40), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    parent = relationship("Department", remote_side=[id], foreign_keys=[parent_department_id])
    employees = relationship("Employee", back_populates="department", foreign_keys="Employee.department_id")
    head = relationship("Employee", foreign_keys=[head_employee_id], post_update=True)

    __table_args__ = (
        Index("ix_hr_departments_active", "is_deleted"),
    )

    def __repr__(self):
        return f"<Department {self.code}:{self.name}>"
