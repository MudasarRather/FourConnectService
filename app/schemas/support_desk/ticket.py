"""Support Desk — Ticket schemas (admin + self-service + public portal)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────── Comments ───────────────────────────
class CommentCreate(BaseModel):
    body: str
    is_internal: bool = False
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    author_user_id: Optional[UUID] = None
    author_name: Optional[str] = None
    author_kind: str
    body: str
    is_internal: bool
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    actor_user_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    action: str
    detail: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ─────────────────────────── Ticket ───────────────────────────
class TicketCreate(BaseModel):
    subject: str
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    ticket_type: str = "incident"
    priority: str = "medium"
    source: str = "internal"
    organization_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    support_team: Optional[str] = None
    assigned_agent_id: Optional[UUID] = None
    assigned_engineer_id: Optional[UUID] = None
    assigned_pm_id: Optional[UUID] = None
    sla_package_id: Optional[UUID] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class TicketUpdate(BaseModel):
    subject: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    ticket_type: Optional[str] = None
    priority: Optional[str] = None
    organization_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    support_team: Optional[str] = None
    sla_package_id: Optional[UUID] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    tags: Optional[List[str]] = None
    links: Optional[Dict[str, Any]] = None


class TicketAssign(BaseModel):
    assigned_agent_id: Optional[UUID] = None
    assigned_engineer_id: Optional[UUID] = None
    assigned_pm_id: Optional[UUID] = None
    support_team: Optional[str] = None


class TicketStatusChange(BaseModel):
    status: str
    note: Optional[str] = None


class TicketCsat(BaseModel):
    csat_score: int = Field(ge=1, le=5)
    csat_comment: Optional[str] = None


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_number: str
    subject: str
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    ticket_type: str
    priority: str
    source: str
    status: str

    organization_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None

    is_internal: bool
    raised_by_user_id: Optional[UUID] = None

    support_team: Optional[str] = None
    assigned_agent_id: Optional[UUID] = None
    assigned_engineer_id: Optional[UUID] = None
    assigned_pm_id: Optional[UUID] = None

    sla_package_id: Optional[UUID] = None
    response_due_at: Optional[datetime] = None
    resolution_due_at: Optional[datetime] = None
    first_responded_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    sla_response_breached: bool
    sla_resolution_breached: bool

    is_escalated: bool
    escalation_level: int
    escalated_at: Optional[datetime] = None
    reopened_count: int

    linked_change_id: Optional[UUID] = None
    linked_problem_id: Optional[UUID] = None
    links: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)

    csat_score: Optional[int] = None
    csat_comment: Optional[str] = None

    created_at: datetime
    updated_at: datetime

    # enriched (router-attached)
    category_name: Optional[str] = None
    organization_name: Optional[str] = None
    customer_name: Optional[str] = None
    assigned_agent_name: Optional[str] = None
    raised_by_name: Optional[str] = None
    sla_response_state: Optional[str] = None     # ok | due-soon | breached | met
    sla_resolution_state: Optional[str] = None
    comment_count: Optional[int] = None


class TicketDetailResponse(TicketResponse):
    comments: List[CommentResponse] = Field(default_factory=list)
    activities: List[ActivityResponse] = Field(default_factory=list)


class TicketListResponse(BaseModel):
    items: List[TicketResponse]
    total: int
    page: int
    limit: int


# ─────────────────────────── Public portal ───────────────────────────
class PublicTicketCreate(BaseModel):
    """Submission from the no-auth client portal — org code + email gate it."""
    org_code: str
    email: str
    subject: str
    description: Optional[str] = None
    priority: str = "medium"
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class PublicCommentCreate(BaseModel):
    body: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
