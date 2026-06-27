"""HR Settings — Notification Rules (events × channels matrix)."""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.notification_rule import (
    NotificationRule, EVENT_CATALOG, CHANNELS, AUDIENCES,
)
from app.schemas.hr.notification_rule import (
    NotificationRuleUpsert, NotificationRuleUpdate, NotificationRuleResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/settings/notification-rules", tags=["HR — Notification Rules"])


@router.get("/catalog")
def catalog(admin: User = Depends(get_current_superuser)):
    """Static catalog so the matrix UI stays in lock-step with the backend."""
    return {"events": EVENT_CATALOG, "channels": CHANNELS, "audiences": AUDIENCES}


@router.get("/", response_model=List[NotificationRuleResponse])
def list_rules(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return (db.query(NotificationRule)
            .filter(NotificationRule.is_deleted == False)  # noqa: E712
            .order_by(NotificationRule.event, NotificationRule.audience)
            .all())


@router.put("/", response_model=NotificationRuleResponse)
def upsert_rule(payload: NotificationRuleUpsert, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Create or update the rule for (event, audience) — drives matrix toggles."""
    bad = [c for c in payload.channels if c not in CHANNELS]
    if bad:
        raise HTTPException(400, f"Unknown channel(s): {', '.join(bad)}")
    if payload.audience not in AUDIENCES:
        raise HTTPException(400, f"Unknown audience: {payload.audience}")
    db.info["audit_actor_id"] = str(admin.id)
    row = (db.query(NotificationRule)
           .filter(NotificationRule.event == payload.event,
                   NotificationRule.audience == payload.audience,
                   NotificationRule.is_deleted == False)  # noqa: E712
           .first())
    if row:
        row.channels = payload.channels
        if payload.template_title is not None:
            row.template_title = payload.template_title
        if payload.template_body is not None:
            row.template_body = payload.template_body
        if payload.is_active is not None:
            row.is_active = payload.is_active
    else:
        row = NotificationRule(
            event=payload.event, audience=payload.audience, channels=payload.channels,
            template_title=payload.template_title, template_body=payload.template_body,
            is_active=payload.is_active if payload.is_active is not None else True,
        )
        db.add(row)
    db.flush()
    log_settings_change(db, "NOTIFICATION_RULE", row.id, "UPDATE", admin.id, note=f"{payload.event} → {','.join(payload.channels) or 'off'}")
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{rule_id}", response_model=NotificationRuleResponse)
def update_rule(rule_id: UUID, payload: NotificationRuleUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    row = db.query(NotificationRule).filter(NotificationRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Rule not found")
    db.info["audit_actor_id"] = str(admin.id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{rule_id}", status_code=204)
def delete_rule(
    rule_id: UUID,
    reason: str | None = Query(None, max_length=400),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Reset a rule to default → the event falls back to the built-in in-app
    delivery (``dispatch`` defaults to IN_APP when no active rule exists). Soft
    delete only; an optional ``reason`` is sealed into the settings audit ledger."""
    row = db.query(NotificationRule).filter(NotificationRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Rule not found")
    db.info["audit_actor_id"] = str(admin.id)
    row.is_deleted = True
    note = f"{row.event}/{row.audience} reset to default"
    if reason and reason.strip():
        note = f"{note} · {reason.strip()}"
    log_settings_change(db, "NOTIFICATION_RULE", row.id, "DELETE", admin.id, note=note)
    db.commit()
