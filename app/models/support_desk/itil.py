"""Support Desk — ITIL: Change Requests, Problem Management, Customer Assets."""
import uuid

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.support_desk.constants import ChangeStatus, RiskLevel, ProblemStatus


class SdChangeRequest(Base):
    __tablename__ = "support_change_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    change_number = Column(String(40), nullable=True, unique=True, index=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    impact = Column(Text, nullable=True)
    risk_level = Column(String(20), nullable=False, default=RiskLevel.LOW.value, index=True)
    # Planning
    implementation_date = Column(DateTime(timezone=True), nullable=True)
    rollback_plan = Column(Text, nullable=True)
    testing_plan = Column(Text, nullable=True)
    approver_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("support_organizations.id"), nullable=True)
    status = Column(String(20), nullable=False, default=ChangeStatus.DRAFT.value, index=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SdProblem(Base):
    __tablename__ = "support_problems"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    problem_number = Column(String(40), nullable=True, unique=True, index=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), nullable=False, default=RiskLevel.MEDIUM.value, index=True)
    impact = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default=ProblemStatus.OPEN.value, index=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("support_organizations.id"), nullable=True)
    # Linked records (loose: arrays of ticket/change/asset ids in JSONB)
    linked_ticket_ids = Column(JSONB, nullable=False, default=list)
    linked_change_ids = Column(JSONB, nullable=False, default=list)
    linked_asset_ids = Column(JSONB, nullable=False, default=list)
    # RCA
    root_cause = Column(Text, nullable=True)
    resolution_plan = Column(Text, nullable=True)
    preventive_measures = Column(Text, nullable=True)
    lessons_learned = Column(Text, nullable=True)
    # Known-Error DB (L3 workbench): the interim fix lower tiers can apply while the
    # permanent fix ships, and whether it's published to them. Owner = the accountable
    # L3 engineer. Columns exist in the live DB via add_support_problem_kedb_columns.py.
    workaround = Column(Text, nullable=True)
    workaround_published = Column(Boolean, nullable=False, default=False)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SdCustomerAsset(Base):
    """Client infrastructure tracked by the support desk."""
    __tablename__ = "support_customer_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("support_organizations.id"), nullable=True, index=True)
    name = Column(String(160), nullable=False)
    asset_type = Column(String(60), nullable=True, index=True)  # server/firewall/license/db/cloud/...
    serial_number = Column(String(120), nullable=True)
    model = Column(String(120), nullable=True)
    vendor = Column(String(120), nullable=True)
    # Maintenance
    warranty_start = Column(DateTime(timezone=True), nullable=True)
    warranty_end = Column(DateTime(timezone=True), nullable=True)
    amc = Column(String(80), nullable=True)
    vendor_contact = Column(String(160), nullable=True)
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
