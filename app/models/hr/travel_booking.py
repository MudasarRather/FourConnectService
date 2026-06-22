"""HR Travel — Booking (centralized travel booking).

Flights / trains / hotels / taxis / buses / rentals booked against an APPROVED
travel request. Mode-specific fields (PNR, airline, hotel dates) are all nullable
— only the ones relevant to ``booking_type`` are filled.

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric, Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.travel_type import BookingType, BookingStatus


class TravelBooking(Base):
    __tablename__ = "hr_travel_bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    booking_number = Column(String(20), nullable=False, unique=True, index=True)   # BK-{YY}-{NNNNNN}
    travel_request_id = Column(UUID(as_uuid=True), ForeignKey("hr_travel_requests.id", ondelete="CASCADE"),
                               nullable=False, index=True)

    booking_type = Column(Enum(BookingType, name="hr_travel_booking_type"), nullable=False, index=True)
    vendor = Column(String(160), nullable=True)
    booking_date = Column(Date, nullable=True)
    travel_date = Column(Date, nullable=True, index=True)
    # Return leg date for round-trip flight/train bookings (nullable — one-way / hotel / local leave it empty)
    return_date = Column(Date, nullable=True)

    # Ticket info (flight / train)
    pnr_number = Column(String(40), nullable=True)
    ticket_number = Column(String(60), nullable=True)
    airline = Column(String(80), nullable=True)
    train_number = Column(String(40), nullable=True)
    seat_number = Column(String(20), nullable=True)
    from_place = Column(String(120), nullable=True)
    to_place = Column(String(120), nullable=True)

    # Hotel info
    hotel_name = Column(String(160), nullable=True)
    check_in = Column(Date, nullable=True)
    check_out = Column(Date, nullable=True)
    num_nights = Column(Integer, nullable=True)

    # Financials
    booking_cost = Column(Numeric(12, 2), nullable=False, default=0)
    taxes = Column(Numeric(12, 2), nullable=False, default=0)
    total_cost = Column(Numeric(12, 2), nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="INR")

    status = Column(Enum(BookingStatus, name="hr_travel_booking_status"), nullable=False,
                    default=BookingStatus.PENDING, index=True)
    notes = Column(Text, nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    request = relationship("TravelRequest", foreign_keys=[travel_request_id])

    __table_args__ = (
        Index("ix_hr_travel_booking_req_type", "travel_request_id", "booking_type"),
    )

    def __repr__(self):
        return f"<TravelBooking {self.booking_number} {self.booking_type}>"
