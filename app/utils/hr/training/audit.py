"""HR Training & Development — audit log helper.

``write_training_audit`` is written in the SAME transaction as every state change,
mirroring ``write_claim_audit`` in the reimbursements module.
"""
from __future__ import annotations

from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models.hr.training_audit_log import TrainingAuditLog, TrainingAuditAction


def write_training_audit(
    db: Session, *, entity_type: str, entity_id, action: TrainingAuditAction,
    actor_id=None, from_status: Optional[str] = None, to_status: Optional[str] = None,
    note: Optional[str] = None, payload: Optional[Dict[str, Any]] = None,
) -> None:
    db.add(TrainingAuditLog(
        entity_type=entity_type, entity_id=entity_id, action=action,
        actor_id=actor_id, from_status=from_status, to_status=to_status,
        note=note, payload=payload,
    ))
