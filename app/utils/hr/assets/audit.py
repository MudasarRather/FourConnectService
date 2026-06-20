"""Asset history / audit-trail writer.

``write_asset_history`` appends an immutable ``AssetHistory`` row. Routers call it
in the SAME session right before ``db.commit()`` (mirrors ``write_training_audit``)
so the timeline is always consistent with the mutation that produced it.
"""
from __future__ import annotations

from typing import Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.hr.asset_lifecycle import AssetHistory, AssetEventType


def _val(s) -> Optional[str]:
    if s is None:
        return None
    return getattr(s, "value", str(s))


def write_asset_history(
    db: Session,
    asset_id: UUID,
    event_type: AssetEventType,
    *,
    actor_user_id: Optional[UUID] = None,
    actor_employee_id: Optional[UUID] = None,
    from_status: Optional[Any] = None,
    to_status: Optional[Any] = None,
    related_entity_type: Optional[str] = None,
    related_entity_id: Optional[UUID] = None,
    payload: Optional[dict] = None,
    note: Optional[str] = None,
) -> AssetHistory:
    """Insert a history row (not committed — the caller commits)."""
    row = AssetHistory(
        asset_id=asset_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        actor_employee_id=actor_employee_id,
        from_status=_val(from_status),
        to_status=_val(to_status),
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        payload=payload or {},
        note=(note[:500] if note else None),
    )
    db.add(row)
    return row
