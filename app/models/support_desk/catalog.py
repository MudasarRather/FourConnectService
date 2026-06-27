"""Support Desk — Knowledge Base + Service Catalog."""
import uuid

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, Integer, Numeric, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.support_desk.constants import (
    ArticleVisibility, ArticleStatus, ServiceRequestStatus,
)


class SdKbCategory(Base):
    __tablename__ = "support_kb_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False)
    slug = Column(String(140), nullable=True, index=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("support_kb_categories.id"), nullable=True)
    icon = Column(String(40), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SdKnowledgeArticle(Base):
    __tablename__ = "support_kb_articles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    title = Column(String(300), nullable=False)
    slug = Column(String(320), nullable=True, index=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("support_kb_categories.id"), nullable=True, index=True)
    tags = Column(JSONB, nullable=False, default=list)
    short_description = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    seo_keywords = Column(Text, nullable=True)
    media = Column(JSONB, nullable=False, default=list)  # [{type,url,name}]
    visibility = Column(String(20), nullable=False, default=ArticleVisibility.CUSTOMER.value, index=True)
    status = Column(String(20), nullable=False, default=ArticleStatus.DRAFT.value, index=True)
    views = Column(Integer, nullable=False, default=0)
    helpful_count = Column(Integer, nullable=False, default=0)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SdServiceItem(Base):
    """A pre-defined service offering in the catalog."""
    __tablename__ = "support_service_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(80), nullable=True, index=True)
    approval_required = Column(Boolean, nullable=False, default=False)
    estimated_delivery_hours = Column(Integer, nullable=True)
    cost = Column(Numeric(12, 2), nullable=True)
    fields_schema = Column(JSONB, nullable=False, default=list)  # dynamic intake fields
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SdServiceRequest(Base):
    __tablename__ = "support_service_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    request_number = Column(String(40), nullable=True, unique=True, index=True)
    service_item_id = Column(UUID(as_uuid=True), ForeignKey("support_service_items.id"), nullable=True, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("support_organizations.id"), nullable=True)
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    status = Column(String(30), nullable=False, default=ServiceRequestStatus.REQUESTED.value, index=True)
    data = Column(JSONB, nullable=False, default=dict)  # filled dynamic fields
    approver_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
