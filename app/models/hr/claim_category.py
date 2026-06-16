"""HR Reimbursements — Claim Category master.

A configurable category (Travel / Medical / Internet / Food / Fuel / …) that
drives the dynamic per-type fields of a claim. ``field_schema`` is a declarative
list of field descriptors; the frontend renders the "new claim" form from it and
the backend validates ``Claim.details`` against it — so HR can add a new claim
type with NO code change.

New table — auto-created by ``Base.metadata.create_all()`` on startup. Default
rows seeded by ``app/utils/hr/reimbursements/seeds.py``.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.reimbursement_type import SettlementMethod


class ClaimCategory(Base):
    __tablename__ = "hr_claim_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)   # TRAVEL, MEDICAL, …
    name = Column(String(80), nullable=False)
    description = Column(String(400), nullable=True)
    icon = Column(String(40), nullable=True)         # lucide icon name for the UI
    color_hex = Column(String(9), nullable=True)     # UI accent

    # Declarative dynamic-field spec. Each item:
    #   {"key": "from_location", "label": "From", "type": "text"|"number"|"date"|
    #    "currency"|"select"|"textarea", "required": bool, "options": [..]}
    field_schema = Column(JSONB, nullable=False, default=list)

    default_settlement_method = Column(
        Enum(SettlementMethod, name="hr_claim_settlement_method"),
        nullable=False, default=SettlementMethod.PAYROLL,
    )
    requires_attachment = Column(Boolean, nullable=False, default=True)
    is_taxable = Column(Boolean, nullable=False, default=False)
    gl_code = Column(String(40), nullable=True)      # accounting / finance hook

    sort_order = Column(String(8), nullable=True)    # optional display ordering hint
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("code", name="uq_hr_claim_category_code"),
    )

    def __repr__(self):
        return f"<ClaimCategory {self.code}>"
