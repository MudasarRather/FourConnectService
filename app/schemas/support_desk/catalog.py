"""Support Desk — Knowledge Base + Service Catalog schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─────────── KB Category ───────────
class KbCategoryCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    parent_id: Optional[UUID] = None
    icon: Optional[str] = None
    sort_order: int = 0


class KbCategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    parent_id: Optional[UUID] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None


class KbCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    slug: Optional[str] = None
    parent_id: Optional[UUID] = None
    icon: Optional[str] = None
    sort_order: int
    created_at: datetime
    updated_at: datetime
    article_count: Optional[int] = None


# ─────────── Article ───────────
class ArticleCreate(BaseModel):
    title: str
    slug: Optional[str] = None
    category_id: Optional[UUID] = None
    tags: List[str] = Field(default_factory=list)
    short_description: Optional[str] = None
    content: Optional[str] = None
    seo_keywords: Optional[str] = None
    media: List[Dict[str, Any]] = Field(default_factory=list)
    visibility: str = "customer"
    status: str = "draft"


class ArticleUpdate(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    category_id: Optional[UUID] = None
    tags: Optional[List[str]] = None
    short_description: Optional[str] = None
    content: Optional[str] = None
    seo_keywords: Optional[str] = None
    media: Optional[List[Dict[str, Any]]] = None
    visibility: Optional[str] = None
    status: Optional[str] = None


class ArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    slug: Optional[str] = None
    category_id: Optional[UUID] = None
    tags: List[str] = Field(default_factory=list)
    short_description: Optional[str] = None
    content: Optional[str] = None
    seo_keywords: Optional[str] = None
    media: List[Dict[str, Any]] = Field(default_factory=list)
    visibility: str
    status: str
    views: int
    helpful_count: int
    author_id: Optional[UUID] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    category_name: Optional[str] = None


# ─────────── Service Item ───────────
class ServiceItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    approval_required: bool = False
    estimated_delivery_hours: Optional[int] = None
    cost: Optional[float] = None
    fields_schema: List[Dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True


class ServiceItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    approval_required: Optional[bool] = None
    estimated_delivery_hours: Optional[int] = None
    cost: Optional[float] = None
    fields_schema: Optional[List[Dict[str, Any]]] = None
    is_active: Optional[bool] = None


class ServiceItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    approval_required: bool
    estimated_delivery_hours: Optional[int] = None
    cost: Optional[float] = None
    fields_schema: List[Dict[str, Any]] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ─────────── Service Request ───────────
class ServiceRequestCreate(BaseModel):
    service_item_id: Optional[UUID] = None
    organization_id: Optional[UUID] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class ServiceRequestUpdate(BaseModel):
    status: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class ServiceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    request_number: Optional[str] = None
    service_item_id: Optional[UUID] = None
    ticket_id: Optional[UUID] = None
    organization_id: Optional[UUID] = None
    requested_by_user_id: Optional[UUID] = None
    status: str
    data: Dict[str, Any] = Field(default_factory=dict)
    approver_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    service_item_name: Optional[str] = None
