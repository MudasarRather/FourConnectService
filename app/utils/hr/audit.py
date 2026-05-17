"""HR audit event listeners.

Writes to the existing `audit_logs` table on Employee / Department / Designation /
Grade / WorkLocation insert / update / delete. Uses the existing AuditLog model
(action field carries a `hr.{entity}.{op}` prefix; details carries a JSON snapshot).

We deliberately use after-flush listeners + a deferred insert so we don't break
the parent transaction if audit-write fails. Failures are logged to crash.log
but never raise.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session


_REGISTERED = False


def _serialize_column(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return str(value)
    except Exception:
        return None


def _row_snapshot(obj) -> dict:
    """Build a dict snapshot of an ORM object's column values."""
    try:
        mapper = inspect(obj.__class__)
        snapshot = {}
        for col in mapper.columns:
            snapshot[col.key] = _serialize_column(getattr(obj, col.key, None))
        return snapshot
    except Exception:
        return {}


def _changed_columns(obj) -> dict:
    """For updates: only the columns whose value changed in this session."""
    try:
        insp = inspect(obj)
        changes = {}
        for attr in insp.mapper.column_attrs:
            hist = insp.attrs[attr.key].history
            if hist.has_changes():
                old = hist.deleted[0] if hist.deleted else None
                new = hist.added[0] if hist.added else getattr(obj, attr.key)
                changes[attr.key] = {
                    "from": _serialize_column(old),
                    "to": _serialize_column(new),
                }
        return changes
    except Exception:
        return {}


def _write_audit(session: Session, entity_type: str, action: str, entity_id, details: dict):
    """Insert an AuditLog row in the same session. Best-effort; never raises."""
    try:
        # Import lazily to avoid circular imports during module init.
        from app.models.audit_log import AuditLog

        user_id = None
        info = getattr(session, "info", {}) or {}
        actor = info.get("audit_actor_id")
        if actor:
            try:
                user_id = uuid.UUID(str(actor))
            except Exception:
                user_id = None

        log = AuditLog(
            user_id=user_id,
            action=f"hr.{entity_type}.{action}",
            entity_type=entity_type,
            entity_id=entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(str(entity_id)),
            details=json.dumps(details, default=str)[:8000],  # cap blob size
        )
        session.add(log)
    except Exception as exc:  # pragma: no cover - defensive
        try:
            with open("crash.log", "a", encoding="utf-8") as f:
                f.write(f"HR audit write failed for {entity_type}.{action}: {exc}\n")
        except Exception:
            pass


def register_hr_audit_listeners() -> None:
    """Idempotently register SQLAlchemy event listeners on HR tables."""
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    # Import inside to avoid the package importing itself during model registration.
    from app.models.hr.employee import Employee
    from app.models.hr.department import Department
    from app.models.hr.designation import Designation
    from app.models.hr.grade import Grade
    from app.models.hr.location import WorkLocation

    targets = [
        (Employee, "employee"),
        (Department, "department"),
        (Designation, "designation"),
        (Grade, "grade"),
        (WorkLocation, "work_location"),
    ]

    for model, entity_type in targets:
        @event.listens_for(model, "after_insert")
        def _on_insert(mapper, connection, target, _entity_type=entity_type):  # noqa: B023
            session = Session.object_session(target) or Session(bind=connection)
            _write_audit(session, _entity_type, "created", target.id, {"snapshot": _row_snapshot(target)})

        @event.listens_for(model, "after_update")
        def _on_update(mapper, connection, target, _entity_type=entity_type):  # noqa: B023
            session = Session.object_session(target) or Session(bind=connection)
            changes = _changed_columns(target)
            if not changes:
                return
            _write_audit(session, _entity_type, "updated", target.id, {"changes": changes})

        @event.listens_for(model, "after_delete")
        def _on_delete(mapper, connection, target, _entity_type=entity_type):  # noqa: B023
            session = Session.object_session(target) or Session(bind=connection)
            _write_audit(session, _entity_type, "deleted", target.id, {"snapshot": _row_snapshot(target)})
