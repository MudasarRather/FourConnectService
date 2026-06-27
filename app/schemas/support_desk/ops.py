"""Support Desk — Announcements, Automation Rules, Settings schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─────────── Announcement ───────────
class AnnouncementCreate(BaseModel):
    title: str
    category: Optional[str] = None
    description: Optional[str] = None
    publish_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    audience: str = "all"
    target_org_id: Optional[UUID] = None
    target_contract_id: Optional[UUID] = None
    target_user_ids: List[UUID] = Field(default_factory=list)
    is_active: bool = True


class AnnouncementUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    publish_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    audience: Optional[str] = None
    target_org_id: Optional[UUID] = None
    target_contract_id: Optional[UUID] = None
    target_user_ids: Optional[List[UUID]] = None
    is_active: Optional[bool] = None


class AnnouncementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    category: Optional[str] = None
    description: Optional[str] = None
    publish_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    audience: str
    target_org_id: Optional[UUID] = None
    target_contract_id: Optional[UUID] = None
    target_user_ids: List[UUID] = Field(default_factory=list)
    is_active: bool
    created_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


# ─────────── Automation Rule ───────────
class AutomationRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    match_type: str = "all"
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    order_index: int = 0
    is_active: bool = True


class AutomationRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    match_type: Optional[str] = None
    conditions: Optional[List[Dict[str, Any]]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    order_index: Optional[int] = None
    is_active: Optional[bool] = None


class AutomationRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: Optional[str] = None
    match_type: str
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    order_index: int
    is_active: bool
    last_run_at: Optional[datetime] = None
    run_count: int
    created_at: datetime
    updated_at: datetime


# ─────────── Setting ───────────
class SettingUpsert(BaseModel):
    key: str
    value: Dict[str, Any] = Field(default_factory=dict)


class SettingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    key: str
    value: Dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime
