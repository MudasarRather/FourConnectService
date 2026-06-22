"""HR Travel — the core Travel Request entity.

An employee's official-travel request. The approval workflow is a configurable
N-stage chain snapshotted onto ``approval_steps`` (+ ``current_step``) at submit
time — the exact mechanism the Reimbursements / Leave modules use. Bookings, a
travel advance, a DA record and an expense settlement all hang off this row via
their own tables. Cost allocation reuses the free-text ``cost_center`` /
``budget_head`` pattern (Expense + Claim) and an optional ``project_id`` FK.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric, Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.travel_type import TravelRequestStatus, TravelPriority, CityCategory


class TravelRequest(Base):
    __tablename__ = "hr_travel_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    travel_reference_number = Column(String(20), nullable=False, unique=True, index=True)  # TR-{YY}-{NNNNNN}

    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    # Snapshot of the traveller's org placement at request time (used for routing/reporting)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id", ondelete="SET NULL"), nullable=True, index=True)
    designation_id = Column(UUID(as_uuid=True), ForeignKey("hr_designations.id", ondelete="SET NULL"), nullable=True)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id", ondelete="SET NULL"), nullable=True)

    # ── Travel information ──
    purpose = Column(Text, nullable=False)
    travel_type = Column(String(40), nullable=True)   # Official Tour | Project Visit | Client Visit | …
    category_id = Column(UUID(as_uuid=True), ForeignKey("hr_travel_categories.id", ondelete="SET NULL"), nullable=True, index=True)
    priority = Column(Enum(TravelPriority, name="hr_travel_priority"), nullable=False, default=TravelPriority.NORMAL)

    # Trip shape. The single from/to + departure/return below is the canonical
    # "envelope" (first origin → final destination, first departure → last return)
    # that every downstream consumer (attendance, scheduler, calendar, reports, DA)
    # already reads. For a MULTI_CITY trip the legs live in ``itinerary`` and the
    # envelope is DERIVED from them, so the blast radius stays contained.
    trip_type = Column(String(20), nullable=False, default="ROUND_TRIP")  # ONE_WAY | ROUND_TRIP | MULTI_CITY
    itinerary = Column(JSONB, nullable=True)  # [{from_location, to_location, departure_date, to_city_category?}] for MULTI_CITY

    from_location = Column(String(160), nullable=False)
    to_location = Column(String(160), nullable=False)
    from_location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id", ondelete="SET NULL"), nullable=True)
    to_location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id", ondelete="SET NULL"), nullable=True)
    # Destination city tier — drives the DA daily rate (for MULTI_CITY this is the trip-level tier).
    to_city_category = Column(Enum(CityCategory, name="hr_travel_city_category"), nullable=False, default=CityCategory.TIER_2)

    departure_date = Column(Date, nullable=False, index=True)
    return_date = Column(Date, nullable=False, index=True)
    num_days = Column(Integer, nullable=False, default=1)

    # ── Cost allocation ──
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    cost_center = Column(String(120), nullable=True)
    budget_head = Column(String(120), nullable=True)
    funding_source = Column(String(120), nullable=True)

    # ── Requirements ──
    flight_required = Column(Boolean, nullable=False, default=False)
    train_required = Column(Boolean, nullable=False, default=False)
    hotel_required = Column(Boolean, nullable=False, default=False)
    local_transport_required = Column(Boolean, nullable=False, default=False)
    advance_required = Column(Boolean, nullable=False, default=False)

    # ── Estimated cost breakdown ──
    est_travel_cost = Column(Numeric(12, 2), nullable=False, default=0)
    est_accommodation_cost = Column(Numeric(12, 2), nullable=False, default=0)
    est_local_cost = Column(Numeric(12, 2), nullable=False, default=0)
    est_food_cost = Column(Numeric(12, 2), nullable=False, default=0)
    est_misc_cost = Column(Numeric(12, 2), nullable=False, default=0)
    est_total_cost = Column(Numeric(12, 2), nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="INR")

    # Attachments: list of {file_url, file_path, original_filename, file_size, mime_type, doc_type}
    attachments = Column(JSONB, nullable=False, default=list)
    # Dynamic per-category fields, validated against category.field_schema
    details = Column(JSONB, nullable=False, default=dict)

    status = Column(Enum(TravelRequestStatus, name="hr_travel_request_status"), nullable=False,
                    default=TravelRequestStatus.DRAFT, index=True)

    # ── Submission ──
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    submitted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # ── Configurable approval chain (snapshot) ──
    approval_steps = Column(JSONB, nullable=False, default=list)
    current_step = Column(Integer, nullable=False, default=0)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approver_notes = Column(Text, nullable=True)

    # ── Return / reject / cancel ──
    returned_at = Column(DateTime(timezone=True), nullable=True)
    return_reason = Column(Text, nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    reject_reason = Column(Text, nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    cancelled_reason = Column(Text, nullable=True)

    # ── Execution / completion ──
    executed_at = Column(DateTime(timezone=True), nullable=True)   # travel started → attendance ON_DUTY
    attendance_synced = Column(Boolean, nullable=False, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id])
    category = relationship("TravelCategory", foreign_keys=[category_id])

    __table_args__ = (
        Index("ix_hr_travel_emp_status", "employee_id", "status"),
        Index("ix_hr_travel_status_dep", "status", "departure_date"),
        Index("ix_hr_travel_dep_date", "departure_date"),
    )

    def __repr__(self):
        return f"<TravelRequest {self.travel_reference_number} {self.status}>"
