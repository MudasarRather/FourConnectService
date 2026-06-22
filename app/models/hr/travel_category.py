"""HR Travel — Travel Category master.

A configurable travel category (Client Visit / Audit / Inspection / Project Visit
/ Conference / Training / Government Meeting / Tender Meeting / Site Visit / …)
that drives optional dynamic per-type fields on a travel request. ``field_schema``
is a declarative list of field descriptors rendered by the frontend and validated
on the backend — so HR can add a new travel category with NO code change.

New table — auto-created by ``Base.metadata.create_all()`` on startup. Default
rows seeded by ``app/utils/hr/travel/bootstrap.py``.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class TravelCategory(Base):
    __tablename__ = "hr_travel_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)   # CLIENT_VISIT, AUDIT, …
    name = Column(String(80), nullable=False)
    description = Column(String(400), nullable=True)
    icon = Column(String(40), nullable=True)         # lucide icon name for the UI
    color_hex = Column(String(9), nullable=True)     # UI accent

    # Declarative dynamic-field spec (same shape as ClaimCategory.field_schema).
    field_schema = Column(JSONB, nullable=False, default=list)

    # Default travel-type hint when a request picks this category.
    default_travel_type = Column(String(40), nullable=True)
    requires_attachment = Column(Boolean, nullable=False, default=False)

    sort_order = Column(String(8), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("code", name="uq_hr_travel_category_code"),
    )

    def __repr__(self):
        return f"<TravelCategory {self.code}>"
