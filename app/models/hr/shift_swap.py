"""HR Shift Swap Requests — peer-to-peer shift exchange workflow.

Flow: requester proposes a swap with a counterparty for a given date (each on a
known shift) → counterparty accepts (peer_accepted) → manager approves → on
approval the two one-day shift assignments are exchanged. Admins can drive any
step from the console (create on behalf, mark accepted, approve/reject).
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date, Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class SwapStatus(str, enum.Enum):
    PENDING_PEER = "PENDING_PEER"        # waiting for the counterparty to accept
    PENDING_MANAGER = "PENDING_MANAGER"  # peer accepted, waiting for manager
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ShiftSwapRequest(Base):
    __tablename__ = "hr_shift_swap_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    requester_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    counterparty_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    swap_date = Column(Date, nullable=False, index=True)
    requester_shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="SET NULL"), nullable=True)
    counterparty_shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="SET NULL"), nullable=True)
    reason = Column(Text, nullable=True)
    status = Column(Enum(SwapStatus, name="hr_swap_status"), nullable=False, default=SwapStatus.PENDING_PEER, index=True)
    peer_accepted = Column(Boolean, nullable=False, default=False)
    decision_notes = Column(Text, nullable=True)
    decided_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    requester = relationship("Employee", foreign_keys=[requester_employee_id])
    counterparty = relationship("Employee", foreign_keys=[counterparty_employee_id])

    __table_args__ = (
        Index("ix_hr_swap_status_date", "status", "swap_date"),
    )
