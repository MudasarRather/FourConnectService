"""Support Desk — audit writer.

Writes to the shared ``audit_logs`` table with a ``support.{entity}.{op}`` action
prefix. Captures actor + IP + user-agent (the spec's Audit Logs view) inside the
``details`` JSON blob, since AuditLog has no dedicated ip/device columns.
Best-effort: never raises, caller commits.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional


def write_audit(
    db,
    *,
    entity_type: str,
    op: str,
    entity_id,
    actor_id=None,
    request=None,
    details: Optional[dict] = None,
) -> None:
    try:
        from app.models.audit_log import AuditLog

        blob: dict[str, Any] = dict(details or {})
        if request is not None:
            try:
                blob.setdefault("ip", request.client.host if request.client else None)
                blob.setdefault("user_agent", request.headers.get("user-agent"))
            except Exception:
                pass

        uid = None
        if actor_id:
            try:
                uid = uuid.UUID(str(actor_id))
            except Exception:
                uid = None

        eid = entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(str(entity_id))

        db.add(AuditLog(
            user_id=uid,
            action=f"support.{entity_type}.{op}",
            entity_type=f"support.{entity_type}",
            entity_id=eid,
            details=json.dumps(blob, default=str)[:8000],
        ))
    except Exception as exc:  # pragma: no cover - audit must never break the action
        try:
            with open("crash.log", "a", encoding="utf-8") as f:
                f.write(f"support audit write failed for {entity_type}.{op}: {exc}\n")
        except Exception:
            pass
