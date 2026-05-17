import enum
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Date, Enum, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class EmployeeChangeType(str, enum.Enum):
    HIRED = "HIRED"
    PROFILE_UPDATED = "PROFILE_UPDATED"
    PROMOTED = "PROMOTED"
    TRANSFERRED = "TRANSFERRED"
    CONFIRMED = "CONFIRMED"
    SUSPENDED = "SUSPENDED"
    REINSTATED = "REINSTATED"
    NOTICE_SERVED = "NOTICE_SERVED"
    EXITED = "EXITED"
    ARCHIVED = "ARCHIVED"


class EmployeeHistory(Base):
    """Append-only lifecycle history for an Employee."""
    __tablename__ = "hr_employee_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    change_type = Column(Enum(EmployeeChangeType, name="hr_employee_change_type"), nullable=False, index=True)
    from_value_json = Column(JSONB, nullable=True)
    to_value_json = Column(JSONB, nullable=True)
    effective_date = Column(Date, nullable=True, index=True)
    reason = Column(String, nullable=True)
    actioned_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    employee = relationship("Employee", back_populates="history")
    actioned_by = relationship("User", foreign_keys=[actioned_by_id])

    __table_args__ = (
        Index("ix_hr_history_type_date", "change_type", "created_at"),
    )

    def __repr__(self):
        return f"<EmployeeHistory {self.change_type} on {self.employee_id}>"
