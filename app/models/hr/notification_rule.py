"""HR Settings — Notification Rules.

Maps lifecycle EVENTS to delivery CHANNELS per audience. ``event`` is stored as
a plain String (not a PG enum) so new events never need an ``ALTER TYPE``. The
known set + friendly labels live in ``EVENT_CATALOG`` and are surfaced to the UI
via the ``/catalog`` endpoint so the matrix stays in sync with the backend.

In-app delivery is honoured today (via ``app/utils/hr/notify.py`` → the existing
``notifications`` table). EMAIL / SMS / PUSH / WHATSAPP are stored now and
dispatched once those transports are wired (the rule rows are the contract).

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base

# Delivery channels (stored in NotificationRule.channels as a JSON list).
CHANNELS = ["IN_APP", "EMAIL", "SMS", "PUSH", "WHATSAPP"]
AUDIENCES = ["EMPLOYEE", "MANAGER", "HR", "FINANCE", "ADMIN"]

# Known events grouped by the module that raises them. Drives the matrix UI.
EVENT_CATALOG = [
    {"event": "EMPLOYEE_CREATED", "label": "Employee created", "module": "employees"},
    {"event": "PROBATION_ENDING", "label": "Probation ending", "module": "employees"},
    {"event": "CONTRACT_EXPIRY", "label": "Contract expiring", "module": "employees"},
    {"event": "BIRTHDAY", "label": "Birthday", "module": "employees"},
    {"event": "WORK_ANNIVERSARY", "label": "Work anniversary", "module": "employees"},
    {"event": "OFFER_RELEASED", "label": "Offer released", "module": "recruitment"},
    {"event": "LEAVE_SUBMITTED", "label": "Leave applied", "module": "leave"},
    {"event": "LEAVE_APPROVED", "label": "Leave approved", "module": "leave"},
    {"event": "LEAVE_REJECTED", "label": "Leave rejected", "module": "leave"},
    {"event": "ATTENDANCE_MISSING", "label": "Missing punch", "module": "attendance"},
    {"event": "TRAVEL_SUBMITTED", "label": "Travel requested", "module": "travel"},
    {"event": "TRAVEL_APPROVED", "label": "Travel approved", "module": "travel"},
    {"event": "CLAIM_SUBMITTED", "label": "Claim submitted", "module": "reimbursements"},
    {"event": "CLAIM_APPROVED", "label": "Claim approved", "module": "reimbursements"},
    {"event": "CLAIM_REJECTED", "label": "Claim rejected", "module": "reimbursements"},
    {"event": "PAYSLIP_RELEASED", "label": "Payslip released", "module": "payroll"},
    {"event": "PAYROLL_PROCESSED", "label": "Payroll processed", "module": "payroll"},
    {"event": "ASSET_ALLOCATED", "label": "Asset allocated", "module": "assets"},
    {"event": "ASSET_RETURN_DUE", "label": "Asset return due", "module": "assets"},
    {"event": "TRAINING_ASSIGNED", "label": "Training assigned", "module": "training"},
    {"event": "CERTIFICATION_EXPIRY", "label": "Certification expiring", "module": "training"},
    {"event": "EXIT_INITIATED", "label": "Exit initiated", "module": "exit"},
    {"event": "CLEARANCE_PENDING", "label": "Clearance pending", "module": "exit"},
]
KNOWN_EVENTS = {e["event"] for e in EVENT_CATALOG}


class NotificationRule(Base):
    __tablename__ = "hr_notification_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    event = Column(String(60), nullable=False, index=True)
    audience = Column(String(30), nullable=False, default="EMPLOYEE")
    channels = Column(JSONB, nullable=False, default=list)          # ["IN_APP", "EMAIL", ...]
    scope_json = Column(JSONB, nullable=True)                       # optional dept/grade scoping
    template_title = Column(String(160), nullable=True)
    template_body = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("event", "audience", name="uq_hr_notification_rule"),
        Index("ix_hr_notification_rule_event", "event", "is_active"),
    )

    def __repr__(self):
        return f"<NotificationRule {self.event}/{self.audience}>"
