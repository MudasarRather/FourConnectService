"""Helper to append HR Settings change-audit rows. Never raises into the caller."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.hr.settings_audit_log import SettingsAuditLog

logger = logging.getLogger("hr.settings_audit")


def log_settings_change(db: Session, entity_type: str, entity_id, action: str, actor_id,
                        before: dict | None = None, after: dict | None = None,
                        note: str | None = None) -> None:
    """Stage a settings-audit row (caller commits). Best-effort — a logging
    failure must never break the settings write itself."""
    try:
        db.add(SettingsAuditLog(
            entity_type=entity_type, entity_id=entity_id, action=action, actor_id=actor_id,
            before_json=before, after_json=after, note=(note[:300] if note else None),
        ))
    except Exception:  # pragma: no cover
        logger.exception("settings audit log failed for %s/%s", entity_type, action)
