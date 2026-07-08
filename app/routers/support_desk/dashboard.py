"""Support Desk — admin dashboard / KPI aggregation. prefix=/support-desk/dashboard."""
from __future__ import annotations

from datetime import datetime, date, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.workspace import SdTeam, SdTicketViewer
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES,
)
from app.schemas.support_desk.dashboard import (
    SupportDashboardResponse,
    SupportIntelResponse, IntelSummary, IntelTrendPoint, IntelSlaPoint,
    IntelTeamRow, IntelAgentRow, IntelQuality, IntelAtRiskItem, IntelPresence,
    IntelIncidentItem, IntelHeatCell, IntelCsatPoint, IntelCsatBlock,
)
from app.utils.dependencies import get_support_agent
from app.utils.support_desk import sla as sla_util

router = APIRouter(prefix="/support-desk/dashboard", tags=["Support Desk — Dashboard"])


@router.get("/", response_model=SupportDashboardResponse)
def support_dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    # Team seal (single source of truth in tickets_self): a superuser sees the whole desk
    # (cond is None); a non-superuser agent is sealed to their teams + triage pool + own
    # involvement, so this KPI aggregate can never leak another team's numbers. The seal
    # must be re-applied to EVERY query root below — the averages and CSAT open fresh
    # db.query() roots that would otherwise stay desk-wide.
    from app.routers.support_desk.tickets import _agent_scope
    cond, _ = _agent_scope(db, admin)

    def _scope(q):
        return q.filter(cond) if cond is not None else q

    base = _scope(db.query(SdTicket).filter(SdTicket.is_deleted == False))  # noqa: E712

    def count(q):
        return q.count()

    open_q = base.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES))
    today_start = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)

    resp = SupportDashboardResponse()
    resp.total_tickets = count(base)
    resp.open_tickets = count(open_q)
    resp.unassigned = count(open_q.filter(SdTicket.assigned_agent_id.is_(None)))
    resp.pending = count(base.filter(SdTicket.status.in_([
        TicketStatus.PENDING_CUSTOMER.value, TicketStatus.PENDING_VENDOR.value])))
    resp.on_hold = count(base.filter(SdTicket.status == TicketStatus.ON_HOLD.value))
    resp.critical = count(open_q.filter(SdTicket.priority == TicketPriority.CRITICAL.value))
    resp.escalated = count(base.filter(SdTicket.is_escalated == True,  # noqa: E712
                                       SdTicket.status.in_(OPEN_TICKET_STATUSES)))
    resp.overdue = count(open_q.filter(SdTicket.sla_resolution_breached == True))  # noqa: E712
    resp.sla_breached = count(base.filter(or_(
        SdTicket.sla_response_breached == True, SdTicket.sla_resolution_breached == True)))  # noqa: E712
    # Exclude merged duplicates — a merge force-closes them (stamping resolved_at/closed_at),
    # but that's folding-into-master, not genuine resolution/closure throughput.
    resp.resolved_today = count(base.filter(SdTicket.merged_into_id.is_(None), SdTicket.resolved_at >= today_start))
    resp.closed_today = count(base.filter(SdTicket.merged_into_id.is_(None), SdTicket.closed_at >= today_start))

    # Averages (minutes) — separate query roots, so re-apply the team seal.
    avg_resp = _scope(db.query(func.avg(func.extract("epoch", SdTicket.first_responded_at - SdTicket.created_at)))
        .filter(SdTicket.is_deleted == False, SdTicket.first_responded_at.isnot(None))).scalar()  # noqa: E712
    avg_reso = _scope(db.query(func.avg(func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at)))
        .filter(SdTicket.is_deleted == False, SdTicket.merged_into_id.is_(None), SdTicket.resolved_at.isnot(None))).scalar()  # noqa: E712
    resp.avg_response_mins = round(float(avg_resp) / 60, 1) if avg_resp else None
    resp.avg_resolution_mins = round(float(avg_reso) / 60, 1) if avg_reso else None

    # CSAT — % of rated tickets scoring >= 4 (separate roots, re-seal both).
    rated = _scope(db.query(func.count(SdTicket.id)).filter(
        SdTicket.is_deleted == False, SdTicket.csat_score.isnot(None))).scalar() or 0  # noqa: E712
    if rated:
        happy = _scope(db.query(func.count(SdTicket.id)).filter(
            SdTicket.is_deleted == False, SdTicket.csat_score >= 4)).scalar() or 0  # noqa: E712
        resp.csat = round(happy / rated * 100, 1)

    # Distributions
    pr_rows = (open_q.with_entities(SdTicket.priority, func.count(SdTicket.id))
               .group_by(SdTicket.priority).all())
    resp.priority_counts = {p.value: 0 for p in TicketPriority}
    for k, v in pr_rows:
        resp.priority_counts[k] = v

    st_rows = (base.with_entities(SdTicket.status, func.count(SdTicket.id))
               .group_by(SdTicket.status).all())
    resp.status_counts = {k: v for k, v in st_rows}

    ty_rows = (base.with_entities(SdTicket.ticket_type, func.count(SdTicket.id))
               .group_by(SdTicket.ticket_type).all())
    resp.type_counts = {k: v for k, v in ty_rows}

    return resp


@router.get("/intel", response_model=SupportIntelResponse)
def support_dashboard_intel(
    days: int = Query(30, ge=7, le=90),
    tz_offset: int = Query(0, ge=-840, le=840),   # viewer minutes east of UTC (JS -getTimezoneOffset())
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Consolidated ADMIN intel payload for /admin/support-desk/tickets/dashboard.

    ServiceNow/Zendesk-grade desk analytics in ONE call: range-parameterised volume
    trend, SLA attainment series, channel mix, per-team scoreboard, agent leaderboard,
    FCR/reopen quality, aging ladder, at-risk deadlines, live presence, active major
    incidents, busiest-hours matrix and CSAT analytics.

    Sealed exactly like `/support-desk/dashboard/`: superusers see the whole desk;
    non-superuser agents are sealed to their teams — the seal is re-applied to EVERY
    fresh query root (including the presence join). Grouped aggregates only, no
    per-row loops — StaticPool-friendly. The legacy `/` endpoint stays untouched."""
    from app.routers.support_desk.tickets import _agent_scope
    from app.routers.support_desk._common import _user_names
    cond, _ = _agent_scope(db, admin)

    def _scope(q):
        return q.filter(cond) if cond is not None else q

    if days not in (7, 14, 30, 90):
        days = 30
    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = sod - timedelta(days=days - 1)   # inclusive first day of the window
    open_set = OPEN_TICKET_STATUSES
    terminal = list(TERMINAL_TICKET_STATUSES)
    CRIT = TicketPriority.CRITICAL.value
    breach_any = or_(SdTicket.sla_response_breached == True,   # noqa: E712
                     SdTicket.sla_resolution_breached == True)  # noqa: E712

    out = SupportIntelResponse(
        generated_at=now, range_days=days,
        is_superuser=bool(getattr(admin, "is_superuser", False)))

    desk = _scope(db.query(SdTicket).filter(SdTicket.is_deleted == False))  # noqa: E712
    active = desk.filter(SdTicket.status.in_(open_set))
    # Throughput/quality math never counts merged duplicates — a merge force-closes
    # them, which is folding-into-master, not genuine resolution.
    resolved_range_q = desk.filter(SdTicket.merged_into_id.is_(None),
                                   SdTicket.resolved_at.isnot(None),
                                   SdTicket.resolved_at >= since)

    # ── summary ──
    s = out.summary
    row = active.with_entities(
        func.count(SdTicket.id),
        func.sum(case((SdTicket.assigned_agent_id.is_(None), 1), else_=0))).first()
    s.open_now = int(row[0] or 0)
    s.unassigned_now = int(row[1] or 0)
    s.on_hold_now = desk.filter(SdTicket.status == TicketStatus.ON_HOLD.value).count()
    mtta = (desk.filter(SdTicket.acknowledged_at.isnot(None), SdTicket.acknowledged_at >= since)
            .with_entities(func.avg(func.extract("epoch", SdTicket.acknowledged_at - SdTicket.created_at)))
            .scalar())
    s.mtta_minutes = round(float(mtta) / 60.0, 1) if mtta is not None else None
    afr = (desk.filter(SdTicket.first_responded_at.isnot(None), SdTicket.first_responded_at >= since)
           .with_entities(func.avg(func.extract("epoch", SdTicket.first_responded_at - SdTicket.created_at)))
           .scalar())
    s.avg_first_response_minutes = round(float(afr) / 60.0, 1) if afr is not None else None
    mttr = (resolved_range_q
            .with_entities(func.avg(func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at)))
            .scalar())
    s.mttr_minutes = round(float(mttr) / 60.0, 1) if mttr is not None else None

    # ── volume trend (created / resolved / newly-breached per day, zero-filled) ──
    def _daily(q, col):
        day = func.date_trunc("day", col)
        return {k: int(v or 0) for k, v in
                q.with_entities(day, func.count(SdTicket.id)).group_by(day).all()}

    cre = _daily(desk.filter(SdTicket.created_at >= since), SdTicket.created_at)
    res = _daily(resolved_range_q, SdTicket.resolved_at)
    first_breach = func.coalesce(SdTicket.sla_resolution_breached_at, SdTicket.sla_response_breached_at)
    bre = _daily(desk.filter(first_breach.isnot(None), first_breach >= since), first_breach)

    def _on(day, rows):
        return next((v for k, v in rows.items() if k is not None and k.date() == day.date()), 0)

    span = [sod - timedelta(days=i) for i in range(days - 1, -1, -1)]
    out.volume_trend = [IntelTrendPoint(day=d, created=_on(d, cre), resolved=_on(d, res),
                                        breached=_on(d, bre)) for d in span]
    s.created_range = sum(p.created for p in out.volume_trend)
    s.resolved_range = sum(p.resolved for p in out.volume_trend)
    s.backlog_delta = s.created_range - s.resolved_range

    # ── SLA attainment series (events bucketed by when they happened; the stored
    #     breach flags are maintained pause-aware — do NOT recompute deadlines here) ──
    rp_day = func.date_trunc("day", SdTicket.first_responded_at)
    rp_rows = {k: (int(t or 0), int(m or 0)) for k, t, m in
               desk.filter(SdTicket.first_responded_at.isnot(None), SdTicket.first_responded_at >= since)
               .with_entities(rp_day, func.count(SdTicket.id),
                              func.sum(case((SdTicket.sla_response_breached == False, 1), else_=0)))  # noqa: E712
               .group_by(rp_day).all()}
    rs_day = func.date_trunc("day", SdTicket.resolved_at)
    rs_rows = {k: (int(t or 0), int(m or 0)) for k, t, m in
               resolved_range_q
               .with_entities(rs_day, func.count(SdTicket.id),
                              func.sum(case((SdTicket.sla_resolution_breached == False, 1), else_=0)))  # noqa: E712
               .group_by(rs_day).all()}

    def _on2(day, rows):
        return next((v for k, v in rows.items() if k is not None and k.date() == day.date()), (0, 0))

    for d in span:
        rp = _on2(d, rp_rows)
        rs = _on2(d, rs_rows)
        out.sla_trend.append(IntelSlaPoint(day=d, responded=rp[0], response_met=rp[1],
                                           resolved=rs[0], resolution_met=rs[1]))
    tot_rp = sum(p.responded for p in out.sla_trend)
    tot_rs = sum(p.resolved for p in out.sla_trend)
    s.first_response_met_pct = (round(100.0 * sum(p.response_met for p in out.sla_trend) / tot_rp, 1)
                                if tot_rp else None)
    s.resolution_met_pct = (round(100.0 * sum(p.resolution_met for p in out.sla_trend) / tot_rs, 1)
                            if tot_rs else None)

    # ── channel mix (tickets created in range, by source) ──
    out.channel_mix = {(src or "unset"): int(c or 0) for src, c in
                       desk.filter(SdTicket.created_at >= since)
                       .with_entities(SdTicket.source, func.count(SdTicket.id))
                       .group_by(SdTicket.source).all()}

    # ── team scoreboard ──
    a_rows = (active.with_entities(
        SdTicket.team_id, func.count(SdTicket.id),
        func.sum(case((SdTicket.assigned_agent_id.is_(None), 1), else_=0)),
        func.sum(case((SdTicket.priority == CRIT, 1), else_=0)),
        func.sum(case((breach_any, 1), else_=0)))
        .group_by(SdTicket.team_id).all())
    b_rows = {tid: (int(n or 0), int(m or 0), (round(float(cs), 2) if cs is not None else None))
              for tid, n, m, cs in
              resolved_range_q.with_entities(
                  SdTicket.team_id, func.count(SdTicket.id),
                  func.sum(case((SdTicket.sla_resolution_breached == False, 1), else_=0)),  # noqa: E712
                  func.avg(SdTicket.csat_score))
              .group_by(SdTicket.team_id).all()}
    team_ids = {tid for tid, *_ in a_rows if tid} | {tid for tid in b_rows if tid}
    tmeta = {tid: (nm, col) for tid, nm, col in
             db.query(SdTeam.id, SdTeam.name, SdTeam.color).filter(SdTeam.id.in_(team_ids)).all()} \
        if team_ids else {}
    seen = set()
    for tid, opn, una, crit, brc in a_rows:
        seen.add(tid)
        b = b_rows.get(tid, (0, 0, None))
        nm, col = tmeta.get(tid, ("Untriaged", None))
        out.team_scoreboard.append(IntelTeamRow(
            team_id=tid, name=nm, color=col,
            open=int(opn or 0), unassigned=int(una or 0), critical=int(crit or 0),
            breached_active=int(brc or 0), resolved_range=b[0],
            sla_met_pct=(round(100.0 * b[1] / b[0], 1) if b[0] else None), csat_avg=b[2]))
    for tid, b in b_rows.items():           # teams with throughput but nothing active
        if tid in seen:
            continue
        nm, col = tmeta.get(tid, ("Untriaged", None))
        out.team_scoreboard.append(IntelTeamRow(
            team_id=tid, name=nm, color=col, resolved_range=b[0],
            sla_met_pct=(round(100.0 * b[1] / b[0], 1) if b[0] else None), csat_avg=b[2]))
    out.team_scoreboard.sort(key=lambda t: (-t.breached_active, -t.open))

    # ── agent leaderboard (resolution credit = resolver, falling back to assignee) ──
    credit = func.coalesce(SdTicket.resolved_by_id, SdTicket.assigned_agent_id)
    l_rows = (resolved_range_q.filter(credit.isnot(None))
              .with_entities(
                  credit, func.count(SdTicket.id),
                  func.avg(func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at)),
                  func.avg(SdTicket.csat_score), func.count(SdTicket.csat_score))
              .group_by(credit).order_by(func.count(SdTicket.id).desc()).limit(10).all())
    top_ids = [r[0] for r in l_rows]
    load = {aid: (int(o or 0), int(b or 0)) for aid, o, b in
            active.filter(SdTicket.assigned_agent_id.in_(top_ids))
            .with_entities(SdTicket.assigned_agent_id, func.count(SdTicket.id),
                           func.sum(case((breach_any, 1), else_=0)))
            .group_by(SdTicket.assigned_agent_id).all()} if top_ids else {}
    lnames = _user_names(db, set(top_ids)) if top_ids else {}
    for aid, n, mt, cs, cn in l_rows:
        ld = load.get(aid, (0, 0))
        out.leaderboard.append(IntelAgentRow(
            agent_id=aid, name=lnames.get(str(aid)), resolved_range=int(n or 0),
            mttr_minutes=(round(float(mt) / 60.0, 1) if mt is not None else None),
            csat_avg=(round(float(cs), 2) if cs is not None else None),
            csat_count=int(cn or 0), active_load=ld[0], breaching=ld[1]))

    # ── quality: reopen rate + FCR (never-reopened share of resolutions) ──
    q = out.quality
    q.reopens_range = desk.filter(SdTicket.merged_into_id.is_(None),
                                  SdTicket.last_reopened_at.isnot(None),
                                  SdTicket.last_reopened_at >= since).count()
    solves = s.resolved_range + q.reopens_range
    q.reopen_rate_range = round(100.0 * q.reopens_range / solves, 1) if solves else 0.0
    if s.resolved_range:
        fcr = resolved_range_q.filter(SdTicket.reopened_count == 0).count()
        q.fcr_pct = round(100.0 * fcr / s.resolved_range, 1)

    # ── aging ladder over the active desk (same buckets as the pulse) ──
    age_case = case(
        (SdTicket.created_at >= now - timedelta(hours=4), "<4h"),
        (SdTicket.created_at >= now - timedelta(hours=24), "4-24h"),
        (SdTicket.created_at >= now - timedelta(days=3), "1-3d"),
        (SdTicket.created_at >= now - timedelta(days=7), "3-7d"),
        else_=">7d")
    aged = {k: int(c or 0) for k, c in
            active.with_entities(age_case, func.count(SdTicket.id)).group_by(age_case).all()}
    out.aging = {b: aged.get(b, 0) for b in ("<4h", "4-24h", "1-3d", "3-7d", ">7d")}

    # ── at-risk rail — nearest resolution deadlines still running (or blown) ──
    risk = (desk.filter(SdTicket.status.notin_(terminal),
                        SdTicket.sla_paused_since.is_(None),
                        SdTicket.resolution_due_at.isnot(None))
            .order_by(SdTicket.resolution_due_at.asc()).limit(10).all())
    r_names = _user_names(db, {t.assigned_agent_id for t in risk})
    r_tids = {t.team_id for t in risk if t.team_id}
    r_teams = {tid: nm for tid, nm in
               db.query(SdTeam.id, SdTeam.name).filter(SdTeam.id.in_(r_tids)).all()} if r_tids else {}
    for t in risk:
        due = sla_util._aware(t.resolution_due_at)
        out.at_risk.append(IntelAtRiskItem(
            id=t.id, ticket_number=t.ticket_number, subject=t.subject,
            priority=t.priority, status=t.status,
            team_name=r_teams.get(t.team_id), assignee_name=r_names.get(str(t.assigned_agent_id)),
            due_at=due, minutes_left=(int((due - now).total_seconds() // 60) if due else None),
            unassigned=(t.assigned_agent_id is None), breached=bool(t.sla_resolution_breached)))

    # ── live presence — "agents on the floor" = drawer heartbeats (<60s) ∪ agents who
    #     ACTED on a sealed ticket in the last 15 min. The viewer heartbeat alone only
    #     beats while a ticket drawer is open, so the floor read 0 essentially all day
    #     even mid-shift; timeline actors capture everyone actually working. Both roots
    #     are sealed via the ticket join. ──
    pres = (db.query(SdTicketViewer.user_id, SdTicketViewer.ticket_id)
            .join(SdTicket, SdTicket.id == SdTicketViewer.ticket_id)
            .filter(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicketViewer.last_seen_at >= now - timedelta(seconds=60)))
    if cond is not None:
        pres = pres.filter(cond)
    vrows = pres.all()
    viewer_ids = {r[0] for r in vrows}
    act_q = (db.query(func.distinct(SdTicketActivity.actor_user_id))
             .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
             .filter(SdTicketActivity.actor_user_id.isnot(None),
                     SdTicketActivity.created_at >= now - timedelta(minutes=15)))
    if cond is not None:
        act_q = act_q.filter(cond)
    actor_ids = {r[0] for r in act_q.all()}
    out.presence = IntelPresence(agents_online=len(viewer_ids | actor_ids),
                                 tickets_watched=len({r[1] for r in vrows}))

    # ── active major incidents ──
    mi = active.filter(SdTicket.is_major_incident == True)  # noqa: E712
    out.major_incidents_active = mi.count()
    for t in mi.order_by(SdTicket.created_at.asc()).limit(5).all():
        created = sla_util._aware(t.created_at)
        out.major_incidents.append(IntelIncidentItem(
            id=t.id, ticket_number=t.ticket_number, subject=t.subject,
            priority=t.priority, status=t.status,
            minutes_open=(int((now - created).total_seconds() // 60) if created else 0),
            acknowledged=bool(t.acknowledged_at)))

    # ── busiest-hours matrix (created-in-range; Postgres dow 0 = Sunday).
    #     Bucketed in the VIEWER's local time via tz_offset (calendar idiom) so
    #     "Thursday 10:00" means the admin's 10:00, not UTC. ──
    local_ts = SdTicket.created_at + timedelta(minutes=tz_offset)
    dow = func.extract("dow", local_ts)
    hr = func.extract("hour", local_ts)
    out.busy_matrix = [IntelHeatCell(dow=int(d), hour=int(h), count=int(c or 0))
                       for d, h, c in
                       desk.filter(SdTicket.created_at >= since)
                       .with_entities(dow, hr, func.count(SdTicket.id))
                       .group_by(dow, hr).all()]

    # ── CSAT analytics over rated resolutions in range ──
    rated_q = resolved_range_q.filter(SdTicket.csat_score.isnot(None))
    dist = {str(int(sc)): int(c or 0) for sc, c in
            rated_q.with_entities(SdTicket.csat_score, func.count(SdTicket.id))
            .group_by(SdTicket.csat_score).all() if sc is not None}
    cs_day = func.date_trunc("day", SdTicket.resolved_at)
    cs_rows = {k: ((round(float(a), 2) if a is not None else None), int(c or 0)) for k, a, c in
               rated_q.with_entities(cs_day, func.avg(SdTicket.csat_score), func.count(SdTicket.id))
               .group_by(cs_day).all()}
    trend = []
    for d in span:
        v = next(((a, c) for k, (a, c) in cs_rows.items() if k is not None and k.date() == d.date()),
                 (None, 0))
        trend.append(IntelCsatPoint(day=d, avg=v[0], count=v[1]))
    n_rated = sum(dist.values())
    tot = sum((v[1] * (v[0] or 0)) for v in ((p.avg, p.count) for p in trend) if v[1])
    out.csat = IntelCsatBlock(
        avg=(round(tot / n_rated, 2) if n_rated else None), count=n_rated,
        response_rate_pct=(round(100.0 * n_rated / s.resolved_range, 1) if s.resolved_range else None),
        distribution={str(i): dist.get(str(i), 0) for i in range(1, 6)}, trend=trend)

    return out
