"""Support Desk — shared Team Ops telemetry helpers.

Extracted verbatim from ``app/routers/support_desk/tickets_self.py`` so the agent
Team Ops desk (/me/tickets/team-queue*) and the admin Team Command overview
(/teams/overview) compute their lenses from ONE source — every count reconciles
across both panels by construction.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import or_, and_

from app.models.support_desk.ticket import SdTicket
from app.models.support_desk.constants import (
    TicketPriority, OPEN_TICKET_STATUSES, SLA_PAUSE_STATUSES,
    TEAM_IDLE_HOURS, TEAM_DUE_SOON_HOURS,
)


def team_ops_conds(now):
    """The shared lens/telemetry conditions of the Team Ops desk — pause-aware, single
    source for BOTH the list lenses and the stats sums so every count reconciles."""
    open_set = list(OPEN_TICKET_STATUSES)
    return {
        "breach": or_(SdTicket.sla_response_breached == True,     # noqa: E712
                      SdTicket.sla_resolution_breached == True),  # noqa: E712
        "due_soon": and_(SdTicket.status.in_(open_set),
                         SdTicket.sla_paused_since.is_(None),
                         SdTicket.resolved_at.is_(None),
                         SdTicket.sla_resolution_breached == False,  # noqa: E712
                         SdTicket.resolution_due_at.isnot(None),
                         SdTicket.resolution_due_at > now,
                         SdTicket.resolution_due_at <= now + timedelta(hours=TEAM_DUE_SOON_HOURS)),
        "idle": and_(SdTicket.status.in_(open_set),
                     SdTicket.updated_at < now - timedelta(hours=TEAM_IDLE_HOURS)),
        "critical": or_(SdTicket.priority == TicketPriority.CRITICAL.value,
                        SdTicket.is_major_incident == True),  # noqa: E712
        "pending": SdTicket.status.in_(list(SLA_PAUSE_STATUSES)),
    }


def team_on_shift(bh: dict | None, now) -> Optional[bool]:
    """Is the team inside its business hours right now? Defensive against every stored
    shape ({tz, days, start, end}; days as ints or names). None when unknowable."""
    if not isinstance(bh, dict) or not bh.get("start") or not bh.get("end"):
        return None
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo(str(bh.get("tz") or "Asia/Kolkata")))
        days = bh.get("days")
        if days:
            names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
            ok = False
            for d in days:
                if isinstance(d, int) and d % 7 == local.weekday():
                    ok = True
                elif isinstance(d, str) and d.strip().lower()[:3] == names[local.weekday()]:
                    ok = True
            if not ok:
                return False
        hhmm = local.strftime("%H:%M")
        return str(bh["start"]) <= hhmm < str(bh["end"])
    except Exception:
        return None
