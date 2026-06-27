import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Numeric, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Grade(Base):
    __tablename__ = "hr_grades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(60), nullable=False, unique=True)
    code = Column(String(20), nullable=False, unique=True, index=True)
    band = Column(String(20), nullable=True)
    level = Column(Integer, nullable=True)
    # Default pay level for this grade (e.g. "P4"). Pre-fills an employee's
    # pay_level when the grade is selected (promote / add / profile edit);
    # the per-employee value stays editable and can diverge.
    default_pay_level = Column(String(20), nullable=True)
    min_ctc = Column(Numeric(12, 2), nullable=True)
    max_ctc = Column(Numeric(12, 2), nullable=True)
    # Grade-driven eligibility, e.g.
    #   {"leave_days": 24, "travel_class": "ECONOMY", "hotel_cap": 4000, "da_per_day": 800}
    eligibility = Column(JSONB, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employees = relationship("Employee", back_populates="grade")
    designations = relationship("Designation", back_populates="grade")

    def __repr__(self):
        return f"<Grade {self.code}>"
