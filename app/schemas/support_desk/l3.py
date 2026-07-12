"""Support Desk — L3 workbench schemas (handoff dossier + problem cascade solve)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─────────── Handoff dossier ───────────
class DossierDiagnosis(BaseModel):
    author_name: Optional[str] = None
    created_at: Optional[datetime] = None
    body: str


class DossierTierMove(BaseModel):
    direction: Optional[str] = None
    tier: Optional[int] = None
    queue: Optional[str] = None
    actor_name: Optional[str] = None
    at: Optional[datetime] = None


class DossierProblem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    problem_number: Optional[str] = None
    title: str
    status: str
    severity: str
    workaround: Optional[str] = None
    workaround_published: bool = False
    linked_count: int = 0


class DossierChange(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    change_number: Optional[str] = None
    title: str
    status: str
    risk_level: str
    implementation_date: Optional[datetime] = None


class HandoffDossierResponse(BaseModel):
    ticket_id: UUID
    # escalation record
    is_escalated: bool = False
    escalation_level: int = 0
    escalated_at: Optional[datetime] = None
    escalation_reason: Optional[str] = None
    escalation_reason_code: Optional[str] = None
    escalation_type: Optional[str] = None
    escalated_by_name: Optional[str] = None
    auto_escalated: bool = False
    acknowledged_at: Optional[datetime] = None
    acknowledged_by_name: Optional[str] = None
    ack_due_at: Optional[datetime] = None
    # investigation record
    diagnoses: List[DossierDiagnosis] = Field(default_factory=list)
    tier_path: List[DossierTierMove] = Field(default_factory=list)
    worklog_minutes: int = 0
    reopened_count: int = 0
    rca_summary: Optional[str] = None
    breach_reason: Optional[str] = None
    # linked records
    problem: Optional[DossierProblem] = None
    change: Optional[DossierChange] = None


# ─────────── Problem cascade solve ───────────
class ProblemCascadeRequest(BaseModel):
    resolution_summary: str
    resolution_code: str = "solved"          # ResolutionCode value
    resolution_category: Optional[str] = None  # RootCauseCategory value
    root_cause: Optional[str] = None         # stamped onto the problem record
    mark_problem_resolved: bool = True


class CascadeTicketResult(BaseModel):
    ticket_id: UUID
    ticket_number: Optional[str] = None
    ok: bool
    reason: Optional[str] = None             # skipped-why, when ok=False


class ProblemCascadeResponse(BaseModel):
    problem_id: UUID
    problem_status: str
    resolved: int = 0
    skipped: int = 0
    results: List[CascadeTicketResult] = Field(default_factory=list)
