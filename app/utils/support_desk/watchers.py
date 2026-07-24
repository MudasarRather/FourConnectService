"""Support Desk — watcher fan-out.

One bounded helper so every notifying path treats watchers identically. Watchers are
NOTIFY-ONLY subscribers (``SdTicketWatcher``); act rights stay with the owner tier.

Deliberately narrow: exactly three event families call this —
  • status change            (tickets._transition_status)
  • tier move / escalation   (queue_ops.tier_escalate / tier_descend)
  • resolution               (a status change; same caller)

The helper never raises and excludes the actor plus anyone the event already pinged
(assignee/requester/lead) so nobody is notified twice for one action.
"""
from sqlalchemy.orm import Session


def notify_ticket_watchers(db: Session, ticket, event: str, title: str, *,
                           actor_id=None, exclude_ids=None,
                           action_url: str | None = None) -> int:
    """Ping every watcher of ``ticket`` except the actor + ``exclude_ids``.
    ``action_url`` overrides the default my-tickets deep link (the incident
    broadcast lands people on the Major desk instead). Returns how many
    notifications were queued. Never raises."""
    try:
        from app.models.support_desk.collab import SdTicketWatcher
        from app.utils.hr.notify import dispatch

        skip = {str(x) for x in (exclude_ids or []) if x}
        if actor_id:
            skip.add(str(actor_id))
        base = action_url or "/user/support/tickets/my"
        deep = f"{base}{'&' if '?' in base else '?'}ticket={ticket.id}"
        rows = (db.query(SdTicketWatcher.user_id)
                .filter(SdTicketWatcher.ticket_id == ticket.id).all())
        sent = 0
        for (uid,) in rows:
            if str(uid) in skip:
                continue
            dispatch(db, event, uid,
                     context={"title": title,
                              "message": f"{ticket.ticket_number}: {ticket.subject}",
                              "action_url": deep},
                     audience="SUPPORT")
            sent += 1
        return sent
    except Exception:
        return 0
