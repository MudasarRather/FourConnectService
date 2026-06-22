"""HR Travel — DA (Daily Allowance) rate matrix + computed records.

``TravelDaRate`` is the effective-dated rate matrix keyed by (grade, city
category): e.g. Director × METRO = ₹3000/day. A null ``grade_id`` is a wildcard
fallback for grades without an explicit row. ``TravelDaRecord`` is the per-tour
computed allowance (days × rate), approvable and posted to payroll via a
PayrollAdjustment (sub_type ``TRAVEL_DA``).

New tables — auto-created on startup. Default matrix seeded by
``app/utils/hr/travel/bootstrap.py``.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric, Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.travel_type import CityCategory, DaRecordStatus


class TravelDaRate(Base):
    __tablename__ = "hr_travel_da_rates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # null grade = wildcard/default rate for that city category
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id", ondelete="CASCADE"), nullable=True, index=True)
    city_category = Column(Enum(CityCategory, name="hr_travel_city_category"), nullable=False, index=True)
    daily_rate = Column(Numeric(12, 2), nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="INR")
    effective_date = Column(Date, nullable=False, default=func.current_date())
    notes = Column(String(200), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    grade = relationship("Grade", foreign_keys=[grade_id])

    __table_args__ = (
        Index("ix_hr_da_rate_grade_city", "grade_id", "city_category", "effective_date"),
    )

    def __repr__(self):
        return f"<TravelDaRate {self.city_category} {self.daily_rate}>"


class TravelDaRecord(Base):
    __tablename__ = "hr_travel_da_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    travel_request_id = Column(UUID(as_uuid=True), ForeignKey("hr_travel_requests.id", ondelete="CASCADE"),
                               nullable=False, unique=True, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id", ondelete="SET NULL"), nullable=True)
    da_rate_id = Column(UUID(as_uuid=True), ForeignKey("hr_travel_da_rates.id", ondelete="SET NULL"), nullable=True)

    city_category = Column(Enum(CityCategory, name="hr_travel_city_category"), nullable=False)
    travel_days = Column(Integer, nullable=False, default=0)
    daily_rate = Column(Numeric(12, 2), nullable=False, default=0)
    eligible_da = Column(Numeric(12, 2), nullable=False, default=0)
    approved_da = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(3), nullable=False, default="INR")

    status = Column(Enum(DaRecordStatus, name="hr_travel_da_status"), nullable=False,
                    default=DaRecordStatus.COMPUTED, index=True)
    notes = Column(Text, nullable=True)

    computed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    payroll_adjustment_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_adjustments.id", ondelete="SET NULL"), nullable=True)
    payroll_ref = Column(String(80), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    request = relationship("TravelRequest", foreign_keys=[travel_request_id])

    def __repr__(self):
        return f"<TravelDaRecord {self.travel_request_id} {self.eligible_da}>"
