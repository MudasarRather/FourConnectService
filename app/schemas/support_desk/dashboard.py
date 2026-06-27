"""Support Desk — dashboard / KPI response schema."""
from __future__ import annotations

from typing import Optional, Dict
from pydantic import BaseModel, Field


class SupportDashboardResponse(BaseModel):
    # KPI cards
    open_tickets: int = 0
    unassigned: int = 0
    pending: int = 0
    critical: int = 0
    overdue: int = 0
    sla_breached: int = 0
    resolved_today: int = 0
    closed_today: int = 0
    escalated: int = 0
    total_tickets: int = 0
    # Averages / satisfaction
    avg_response_mins: Optional[float] = None
    avg_resolution_mins: Optional[float] = None
    csat: Optional[float] = None
    # Distributions (charts + the Liquid Triage Basin)
    priority_counts: Dict[str, int] = Field(default_factory=dict)
    status_counts: Dict[str, int] = Field(default_factory=dict)
    type_counts: Dict[str, int] = Field(default_factory=dict)


class SelfDashboardResponse(BaseModel):
    open: int = 0
    in_progress: int = 0
    pending: int = 0
    resolved: int = 0
    total: int = 0
    priority_counts: Dict[str, int] = Field(default_factory=dict)
