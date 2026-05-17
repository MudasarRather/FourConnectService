import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Designation(Base):
    __tablename__ = "hr_designations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False, unique=True)
    code = Column(String(30), nullable=False, unique=True, index=True)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id"), nullable=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True)
    level = Column(Integer, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    grade = relationship("Grade", back_populates="designations")
    department = relationship("Department", foreign_keys=[department_id])
    employees = relationship("Employee", back_populates="designation")

    def __repr__(self):
        return f"<Designation {self.code}:{self.name}>"
