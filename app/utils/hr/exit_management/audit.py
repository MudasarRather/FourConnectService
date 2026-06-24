"""HR Exit Management — audit log writer."""
from __future__ import annotations

from typing import Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.hr.exit_audit_log import ExitAuditLog
from app.models.hr.exit_type import ExitAuditAction


def write_exit_audit(
    db: Session,
    *,
    entity_type: str,
    action: ExitAuditAction,
    exit_case_id: Optional[UUID] = None,
    entity_id: Optional[UUID] = None,
    actor_id: Optional[UUID] = None,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    payload: Optional[Any] = None,
    note: Optional[str] = None,
) -> ExitAuditLog:
    """Add an audit row to the session. Caller commits."""
    row = ExitAuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        exit_case_id=exit_case_id,
        actor_id=actor_id,
        from_status=str(from_status) if from_status is not None else None,
        to_status=str(to_status) if to_status is not None else None,
        payload=payload,
        note=(note[:300] if note else None),
    )
    db.add(row)
    return row
