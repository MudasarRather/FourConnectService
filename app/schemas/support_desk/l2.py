"""Support Desk — L2 workbench schemas: worklogs, watchers, swarm sessions.

Response shapes are additive-only: never rename/drop a field the frontend consumes.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

WORK_TYPES = {"work", "diagnosis", "research", "comms", "handoff"}


# ─────────────────────────────── Worklogs ───────────────────────────────
class WorklogCreate(BaseModel):
    minutes: int = Field(..., gt=0, le=1440, description="1..1440 (one calendar day max per entry)")
    note: Optional[str] = Field(None, max_length=2000)
    work_type: str = "work"

    @field_validator("work_type")
    @classmethod
    def _work_type_known(cls, v: str) -> str:
        v = (v or "work").strip().lower()
        if v not in WORK_TYPES:
            raise ValueError(f"work_type must be one of {sorted(WORK_TYPES)}")
        return v


class WorklogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    user_id: UUID
    user_name: Optional[str] = None
    minutes: int
    note: Optional[str] = None
    work_type: str
    created_at: datetime


class WorklogListResponse(BaseModel):
    items: List[WorklogResponse]
    total: int
    total_minutes: int  # ticket-wide sum of live entries (not just this page)


# ─────────────────────────────── Watchers ───────────────────────────────
class WatcherEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    user_name: Optional[str] = None
    created_at: datetime


class WatchersResponse(BaseModel):
    items: List[WatcherEntry]
    total: int
    watching: bool  # is the CALLER subscribed?


class WatchToggleResponse(BaseModel):
    watching: bool
    total: int


# ─────────────────────────────── Swarm ───────────────────────────────
class SwarmStartRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)


class SwarmEndRequest(BaseModel):
    outcome: Optional[str] = Field(None, max_length=4000)


class SwarmParticipant(BaseModel):
    user_id: UUID
    user_name: Optional[str] = None


class SwarmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    status: str
    started_by_id: UUID
    started_by_name: Optional[str] = None
    participants: List[SwarmParticipant] = []
    outcome: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None


class SwarmStateResponse(BaseModel):
    active: Optional[SwarmResponse] = None
    history: List[SwarmResponse] = []
    joined: bool = False  # is the CALLER in the active session?
