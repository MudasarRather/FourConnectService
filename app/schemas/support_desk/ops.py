"""Support Desk — Announcements, Automation Rules, Settings schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
_RULE_TRIGGERS = {"on_create", "time_based"}


def _check_trigger(v):
    if v is not None and v not in _RULE_TRIGGERS:
        raise ValueError(f"trigger must be one of {sorted(_RULE_TRIGGERS)}")
    return v


class AutomationRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    match_type: str = "all"
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    order_index: int = 0
    is_active: bool = True
    trigger: str = "on_create"                 # on_create | time_based
    stop_processing: bool = True
    time_threshold_mins: Optional[int] = None  # time_based: minimum open age

    _v_trigger = field_validator("trigger")(_check_trigger)


class AutomationRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    match_type: Optional[str] = None
    conditions: Optional[List[Dict[str, Any]]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    order_index: Optional[int] = None
    is_active: Optional[bool] = None
    trigger: Optional[str] = None
    stop_processing: Optional[bool] = None
    time_threshold_mins: Optional[int] = None

    _v_trigger = field_validator("trigger")(_check_trigger)


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
    # queue engine (all defaulted — stable against pre-migration rows)
    trigger: str = "on_create"
    stop_processing: bool = True
    time_threshold_mins: Optional[int] = None


class RuleReorderRequest(BaseModel):
    """Bulk order update from the drag-reorder UI: [{id, order_index}, ...]."""
    order: List[Dict[str, Any]] = Field(default_factory=list)


class RuleRevisionResponse(BaseModel):
    """One config-versioning cut of a routing rule (the Ledger panel's history)."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    rule_id: UUID
    version: int
    action: str                                   # created | updated | deleted
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    changed_by_id: Optional[UUID] = None
    changed_by_name: Optional[str] = None         # enriched
    created_at: datetime


class ConfigLedgerEntry(BaseModel):
    """One config-audit row (queues/rules/skills/SLA/settings) for the Ledger panel."""
    id: UUID
    action: str                                   # e.g. support.queue.updated
    entity_type: str
    entity_id: UUID
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ConfigLedgerResponse(BaseModel):
    total: int = 0
    page: int = 1
    limit: int = 30
    items: List[ConfigLedgerEntry] = Field(default_factory=list)


# ─────────── Setting ───────────
class SettingUpsert(BaseModel):
    key: str
    value: Dict[str, Any] = Field(default_factory=dict)


class WebhookTestRequest(BaseModel):
    """Uplink Array TEST TRANSMISSION — probe an explicit URL, or the saved one when omitted."""
    url: Optional[str] = None


class SettingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    key: str
    value: Dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime
