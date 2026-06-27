"""HR notification dispatch — honours the configurable NotificationRule matrix.

``dispatch()`` looks up the active rule for an event+audience and fans out to the
enabled channels. IN_APP is delivered today via the existing ``notifications``
table; EMAIL / SMS / PUSH / WHATSAPP are stubbed (logged) until those transports
are wired — the rule rows are the contract, so flipping a channel on becomes a
live behaviour the moment the transport lands.

Delivery semantics (matrix-governed):
  * No rule row for (event, audience)  → default to IN_APP (sensible default so a
    brand-new event is never silently dropped).
  * An active rule row exists          → honour its channels EXACTLY. An empty
    channel list therefore means "muted" (admin unchecked every channel).
  * Inactive / soft-deleted rule        → treated as no rule → IN_APP default.

Call sites adopt this incrementally:
    from app.utils.hr.notify import dispatch
    dispatch(db, "LEAVE_APPROVED", emp.user_id,
             context={"title": "Leave approved", "message": "...", "action_url": "/user/..."})
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.hr.notification_rule import NotificationRule

logger = logging.getLogger("hr.notify")


def _humanise(event: str) -> str:
    return str(event or "").replace("_", " ").title()


def dispatch(db: Session, event: str, recipient_user_id, context: dict | None = None,
             audience: str = "EMPLOYEE") -> bool:
    """Deliver ``event`` to ``recipient_user_id`` over its configured channels.

    Returns True if an in-app row was queued (added to the session — the CALLER
    still commits), False otherwise. Never raises — a notification must never
    break the business action that triggered it.
    """
    if not recipient_user_id:
        return False
    context = context or {}
    try:
        rule = (db.query(NotificationRule)
                .filter(NotificationRule.event == event,
                        NotificationRule.audience == audience,
                        NotificationRule.is_active == True,        # noqa: E712
                        NotificationRule.is_deleted == False)      # noqa: E712
                .first())
    except Exception:
        rule = None

    # No active rule → default in-app. Active rule → exactly its channels ([] = muted).
    channels = ["IN_APP"] if rule is None else list(rule.channels or [])
    title = context.get("title") or (rule.template_title if rule else None) or _humanise(event)
    message = context.get("message") or (rule.template_body if rule else None) or title

    queued = False
    for ch in channels:
        if ch == "IN_APP":
            try:
                db.add(Notification(
                    user_id=recipient_user_id, type=event, title=title, message=message,
                    action_url=context.get("action_url"),
                    related_user_id=context.get("related_user_id"),
                    is_read=False,
                ))
                queued = True
            except Exception:  # pragma: no cover - never let a notification break the caller
                logger.exception("in-app notification failed for %s", event)
        else:
            # Transport not yet wired — record intent so it can be replayed/audited.
            logger.info("notify[%s] channel=%s user=%s title=%s (transport pending)",
                        event, ch, recipient_user_id, title)
    return queued


def already_notified_today(db: Session, recipient_user_id, event: str, on_date: _date | None = None) -> bool:
    """True if a notification of this ``event`` was already created for the user
    today — lets the daily scanners stay idempotent across same-day re-runs."""
    if not recipient_user_id:
        return True
    on_date = on_date or _date.today()
    try:
        row = (db.query(Notification.id)
               .filter(Notification.user_id == recipient_user_id,
                       Notification.type == event,
                       func.date(Notification.created_at) == on_date)
               .first())
        return row is not None
    except Exception:
        return False
