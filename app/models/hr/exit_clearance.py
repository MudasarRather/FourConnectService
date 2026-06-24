"""HR Exit Management — Clearance checklist item (no-dues workflow).

Each row is one obligation in one department lane (MANAGER / IT / FINANCE / HR /
ADMIN / SECURITY / PROJECT). Seeded from the resolved ``ExitPolicy.clearance_template``
(or the built-in default) when a case is accepted. ``recovery_amount`` on an
unreturned asset / unsettled advance feeds the F&F settlement recoveries.

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.exit_type import ClearanceDepartment, ClearanceItemStatus


class ExitClearanceItem(Base):
    __tablename__ = "hr_exit_clearance_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    exit_case_id = Column(UUID(as_uuid=True), ForeignKey("hr_exit_cases.id", ondelete="CASCADE"),
                          nullable=False, index=True)

    department = Column(Enum(ClearanceDepartment, name="hr_exit_clearance_dept"), nullable=False, index=True)
    item_key = Column(String(60), nullable=False)   # stable key e.g. "it_email_revoke"
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    is_mandatory = Column(Boolean, nullable=False, default=True)

    status = Column(Enum(ClearanceItemStatus, name="hr_exit_clearance_status"), nullable=False,
                    default=ClearanceItemStatus.PENDING, index=True)
    assignee_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    remarks = Column(Text, nullable=True)
    recovery_amount = Column(Numeric(12, 2), nullable=True)   # unreturned-asset / dues charge → F&F

    # Employee-submitted work/knowledge/client handover payload for the MANAGER &
    # PROJECT lanes (the "self-then-manager" lanes). Shape:
    #   {notes, successor_name, checklist:{<step-index>:bool}, attachments:[{name,url}],
    #    submitted_at, submitted_by_id, history:[{event, at, by, by_name, note}]}
    # NOT Mutable-wrapped — reassign the whole dict or flag_modified() after in-place edits.
    submission = Column(JSONB, nullable=True)

    signed_off_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    signed_off_at = Column(DateTime(timezone=True), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    exit_case = relationship("ExitCase", back_populates="clearance_items")
    assignee = relationship("User", foreign_keys=[assignee_user_id])
    signed_off_by = relationship("User", foreign_keys=[signed_off_by_id])

    __table_args__ = (
        Index("ix_hr_exit_clr_case_dept", "exit_case_id", "department"),
    )

    def __repr__(self):
        return f"<ExitClearanceItem {self.department}/{self.item_key} {self.status}>"
