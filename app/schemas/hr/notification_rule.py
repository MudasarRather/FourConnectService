"""Schemas for HR Settings — Notification Rules."""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationRuleUpsert(BaseModel):
    """Upsert by (event, audience) — used by the matrix to toggle channels."""
    model_config = ConfigDict(extra="ignore")
    event: str = Field(..., min_length=1, max_length=60)
    audience: str = Field("EMPLOYEE", max_length=30)
    channels: List[str] = Field(default_factory=list)
    template_title: Optional[str] = None
    template_body: Optional[str] = None
    is_active: Optional[bool] = True


class NotificationRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    channels: Optional[List[str]] = None
    audience: Optional[str] = None
    template_title: Optional[str] = None
    template_body: Optional[str] = None
    is_active: Optional[bool] = None


class NotificationRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event: str
    audience: str
    channels: List[str] = Field(default_factory=list)
    template_title: Optional[str] = None
    template_body: Optional[str] = None
    is_active: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
