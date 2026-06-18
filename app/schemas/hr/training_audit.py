"""HR Training & Development — Audit log + self-service response schemas."""
from datetime import datetime
from typing import Optional, List, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.training_audit_log import TrainingAuditAction


class TrainingAuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    entity_type: str
    entity_id: Optional[UUID] = None
    action: TrainingAuditAction
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    note: Optional[str] = None
    created_at: datetime


class TrainingAuditListResponse(BaseModel):
    items: List[TrainingAuditLogResponse]
    total: int
    page: int
    limit: int


class MyTrainingSummary(BaseModel):
    unlinked: bool = False
    assigned: int = 0
    in_progress: int = 0
    completed: int = 0
    overdue: int = 0
    certifications_active: int = 0
    certifications_expiring: int = 0
    pending_requests: int = 0
    avg_skill_gap: Optional[float] = None
