"""HR Payroll — Statutory configuration + audit log.

``StatutoryConfig`` holds the CONFIGURABLE, effective-dated rates the engine
reads (PF rate/ceiling, ESI rates/threshold, PT slabs, TDS slabs for both
regimes, standard deduction, 80C/80D caps, HRA exemption %). Scalars live in
``value_num``; slab tables live in ``value_json`` as ordered lists.

``PayrollAuditLog`` records every state transition, config change, and payslip
download for the (Phase-C) Audit Logs screen.

New tables — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric,
    Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class PayrollAuditAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    GENERATE = "GENERATE"
    REGENERATE = "REGENERATE"
    VERIFY = "VERIFY"
    APPROVE = "APPROVE"
    RELEASE = "RELEASE"
    LOCK = "LOCK"
    CANCEL = "CANCEL"
    REOPEN = "REOPEN"
    RETURN = "RETURN"
    CONFIG_CHANGE = "CONFIG_CHANGE"
    PAYSLIP_VIEW = "PAYSLIP_VIEW"
    PAYSLIP_DOWNLOAD = "PAYSLIP_DOWNLOAD"


class StatutoryConfig(Base):
    __tablename__ = "hr_statutory_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    fiscal_year = Column(String(7), nullable=False, index=True)   # "2026-27"
    state_code = Column(String(10), nullable=True)                # PT / LWF; null = national
    key = Column(String(60), nullable=False)                      # PF_RATE, PT_SLABS, TDS_SLABS_NEW, …

    value_num = Column(Numeric(14, 4), nullable=True)
    value_json = Column(JSONB, nullable=True)                     # slab tables [{upto, rate, fixed}]

    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    description = Column(String(200), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("fiscal_year", "state_code", "key", name="uq_hr_statutory_cfg"),
        Index("ix_hr_stat_cfg_lookup", "key", "state_code", "effective_from"),
    )

    def __repr__(self):
        return f"<StatutoryConfig {self.fiscal_year}/{self.state_code or 'IN'}/{self.key}>"


class PayrollAuditLog(Base):
    __tablename__ = "hr_payroll_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    entity_type = Column(String(40), nullable=False)   # BATCH / PAYSLIP / STRUCTURE / COMPONENT / COMPENSATION / CONFIG
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    action = Column(Enum(PayrollAuditAction, name="hr_payroll_audit_action"), nullable=False)

    batch_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_batches.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=True)
    payload = Column(JSONB, nullable=True)
    note = Column(String(300), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_hr_payroll_audit_entity", "entity_type", "entity_id"),
    )
