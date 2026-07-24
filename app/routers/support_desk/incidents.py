"""Support Desk — Incident Management router (Fault Grid / Command Funnel desks).

Incidents ARE tickets — this router adds the incident-command surface over the
existing SdTicket machinery, never a parallel status machine:

  • sealed lenses + stats + cross-incident timeline (both panels' dashboards)
  • MI command verbs: response roster (commander/comms/ops), impact detail,
    decision log (immutable activity rows — DR/failover/BCP are RECORDED, not automated)
  • the Post-Incident Report lifecycle: draft → in_review → approved → published + PDF
  • "similar incidents" heuristic (the AI-insights panel)

Discipline (same two-gate pattern as every other desk):
  reads   →  _agent_scope on every query root / _get_ticket for single fetch (404 outside scope)
  writes  →  additionally _require_ticket_actor; PIR approve/reject/publish = superuser only

Registration: app/routers/support_desk/__init__.py, with the literal routers BEFORE
the broad tickets router. Inside this file the literal /incidents/* paths register
before /incidents/{ticket_id}/similar (route-shadowing discipline).
"""
from __future__ import annotations

import csv
import io
import json
import uuid as uuid_mod
from datetime import timedelta, datetime, timezone as dt_timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, case, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.core import SdCategory
from app.models.support_desk.workspace import SdTeam
from app.models.support_desk.incident import SdIncidentReport, SdIncidentTask
from app.models.support_desk.itil import SdProblem
from app.models.support_desk.constants import (
    TERMINAL_TICKET_STATUSES, OPEN_TICKET_STATUSES, PirStatus, TicketType,
    EVT_INCIDENT_ROLES_ASSIGNED, EVT_INCIDENT_DECISION, EVT_INCIDENT_IMPACT,
    EVT_PIR_SUBMITTED, EVT_PIR_APPROVED, EVT_PIR_REJECTED, EVT_PIR_PUBLISHED,
    EVT_INCIDENT_DECLARED, EVT_INCIDENT_MI_PROPOSED, EVT_INCIDENT_MI_DECLINED,
    EVT_PIR_ACTION_UPDATED, EVT_INCIDENT_TASK_ASSIGNED, EVT_INCIDENT_SEV_CHANGED,
    INCIDENT_PLAYBOOKS,
    ACTIVITY_CATALOG, TIMELINE_CATEGORIES, MILESTONES_PER_TICKET,
)
from app.schemas.support_desk.incidents import (
    IncidentRow, IncidentListResponse, IncidentStatsResponse, IncidentSlaSplit,
    IncidentCategorySlice, IncidentServiceSlice, IncidentTrendPoint, IncidentFeedItem,
    IncidentPirCounts, IncidentTimelineResponse, IncidentTimelineDay, IncidentTimelineEvent,
    IncidentRolesPatch, IncidentImpactPatch, IncidentDecisionCreate, IncidentParentPatch,
    SimilarIncidentItem,
    PirCreate, PirUpdate, PirReview, PirResponse, PirListItem, PirListResponse,
    PirBoardItem, PirBoardStats, PirBoardResponse,
    IncidentPhasesResponse, MiProposalCreate, MiProposalDecision,
    IncidentActionRow, IncidentActionCounts, IncidentActionsResponse, PirActionStatusPatch,
    IncidentSitrepResponse,
    IncidentTaskItem, IncidentTaskListResponse, IncidentTaskCreate, IncidentTaskPatch,
    PlaybookApplyRequest, IncidentSevChange, IncidentResponderLoad,
    TimelineCatalogEntry, TimelineCatalogResponse,
    TimelinePulseResponse, TimelinePulseDensityPoint, TimelinePulseActor,
    TimelinePulseFlow, TimelinePulseBusiest, TimelinePulseTeam,
    TimelinePinResponse,
    IncidentStreamTicket, IncidentStreamItem, IncidentStreamCounts, IncidentStreamResponse,
    RcaBoardItem, RcaBoardAging, RcaBoardStats, RcaBoardResponse,
    RcaMixSlice, RcaLatency, RcaActionsFollowThrough, RcaKedbStats, RcaTrendWeek,
    RcaCoverage, RcaAnalyticsResponse,
    RcaClusterTicket, RcaClusterSignature, RcaClusterItem, RcaClustersResponse,
    RcaClusterPromote, RcaPromoteResult, RcaClusterPromoteResponse,
    CommandDashboardResponse, CommandDashboardExtras, AdminIncidentBlock,
    NextBreach, AgingBucket, EscalationTiers, IncidentQuality, TasksLive,
    LeaderRow, TeamRow, RcaSummary, PirSummary, RecurringRow, HeatCell, BusyCell,
)
from app.utils.dependencies import get_support_agent, get_current_superuser
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit
from app.utils.support_desk.incidents import (
    ticket_sev, sev_cond, lens_condition, snapshot_timeline, render_pir_pdf,
    INCIDENT_FLAGS, flag_condition, breached_cond, at_risk_cond, sweep_pir_missing,
    build_phase_track, iter_scoped_actions, action_is_overdue, render_sitrep_pdf,
    timeline_meta, timeline_category_case, render_chronicle_pdf, incident_lens_cond,
    pir_owed_cond, pir_eligible_cond, pir_metrics_snapshot,
)
from app.utils.support_desk.rca import (
    RCA_LENSES, RCA_LIVE_STATUSES, rca_effective_status, rca_effective_status_expr,
    rca_eligible_cond, rca_owed_cond, rca_lens_condition,
)

router = APIRouter(prefix="/support-desk", tags=["Support Desk — Incidents"])

_PRI_RANK = case((SdTicket.priority == "critical", 4), (SdTicket.priority == "urgent", 3),
                 (SdTicket.priority == "high", 2), (SdTicket.priority == "medium", 1), else_=0)


# ─────────────────────────────── shared helpers ───────────────────────────────

def _scope_cond(db: Session, admin: User):
    from app.routers.support_desk.tickets import _agent_scope
    cond, _ctx = _agent_scope(db, admin)
    return cond


def _sealed(q, cond):
    return q if cond is None else q.filter(cond)


def _mins(expr_end, expr_start):
    """AVG minutes between two timestamp columns, as a SQL expression."""
    return func.avg(func.extract("epoch", expr_end - expr_start) / 60.0)


def _names_for(db: Session, tickets) -> dict:
    from app.routers.support_desk._common import _user_names
    ids = set()
    for t in tickets:
        ids.update([t.assigned_agent_id, t.incident_commander_id, t.comms_lead_id,
                    t.ops_lead_id, t.mi_proposed_by_id])
    return _user_names(db, ids)


def _row(t: SdTicket, names: dict, cats: dict, teams: dict, pirs: dict,
         parents: dict | None = None, kids: dict | None = None,
         tasks: dict | None = None) -> IncidentRow:
    pir = pirs.get(str(t.id))
    return IncidentRow(
        id=t.id, ticket_number=t.ticket_number, subject=t.subject, status=t.status,
        priority=t.priority, sev=ticket_sev(t.priority, bool(t.is_major_incident)),
        is_major_incident=bool(t.is_major_incident), ticket_type=t.ticket_type,
        category_id=t.category_id, category_name=cats.get(str(t.category_id)),
        team_id=t.team_id, team_name=teams.get(str(t.team_id)),
        assigned_agent_id=t.assigned_agent_id,
        assigned_agent_name=names.get(str(t.assigned_agent_id)),
        incident_commander_id=t.incident_commander_id,
        incident_commander_name=names.get(str(t.incident_commander_id)),
        comms_lead_id=t.comms_lead_id, comms_lead_name=names.get(str(t.comms_lead_id)),
        ops_lead_id=t.ops_lead_id, ops_lead_name=names.get(str(t.ops_lead_id)),
        collaborators=list(t.collaborators or []),
        affected_services=list(t.affected_services or []), affected_users=t.affected_users,
        business_impact=t.business_impact, revenue_impact=t.revenue_impact,
        compliance_impact=bool(t.compliance_impact), security_impact=bool(t.security_impact),
        public_impact=bool(t.public_impact), war_room_url=t.war_room_url,
        acknowledged_at=t.acknowledged_at, update_interval_minutes=t.update_interval_minutes,
        next_update_due_at=t.next_update_due_at, last_status_update_at=t.last_status_update_at,
        is_escalated=bool(t.is_escalated), escalation_level=t.escalation_level or 0,
        response_due_at=t.response_due_at, resolution_due_at=t.resolution_due_at,
        first_responded_at=t.first_responded_at,
        sla_response_breached=bool(t.sla_response_breached),
        sla_resolution_breached=bool(t.sla_resolution_breached),
        sla_paused_since=t.sla_paused_since, rca_summary=t.rca_summary,
        linked_problem_id=t.linked_problem_id,
        incident_started_at=t.incident_started_at, incident_detected_at=t.incident_detected_at,
        resolved_at=t.resolved_at, closed_at=t.closed_at, created_at=t.created_at,
        has_pir=pir is not None, pir_id=pir[0] if pir else None,
        pir_status=pir[1] if pir else None,
        parent_incident_id=t.parent_incident_id,
        parent_incident_number=(parents or {}).get(str(t.parent_incident_id)),
        child_count=(kids or {}).get(str(t.id), 0),
        mi_proposed_at=t.mi_proposed_at,
        mi_proposed_by_id=t.mi_proposed_by_id,
        mi_proposed_by_name=names.get(str(t.mi_proposed_by_id)),
        mi_proposal_note=t.mi_proposal_note,
        task_total=(tasks or {}).get(str(t.id), (0, 0))[0],
        task_done=(tasks or {}).get(str(t.id), (0, 0))[1],
    )


def _task_map(db: Session, ticket_ids: list) -> dict:
    """{ticket_id: (non_skipped_total, done)} — ONE grouped scan per page. skipped rows
    are tombstones and never count against progress (matches IncidentTaskListResponse)."""
    if not ticket_ids:
        return {}
    rows = (db.query(SdIncidentTask.ticket_id,
                     func.count(SdIncidentTask.id),
                     func.sum(case((SdIncidentTask.status == "done", 1), else_=0)))
            .filter(SdIncidentTask.ticket_id.in_(ticket_ids),
                    SdIncidentTask.status != "skipped")
            .group_by(SdIncidentTask.ticket_id).all())
    return {str(r[0]): (int(r[1] or 0), int(r[2] or 0)) for r in rows}


def _pir_map(db: Session, ticket_ids: list) -> dict:
    if not ticket_ids:
        return {}
    rows = (db.query(SdIncidentReport.ticket_id, SdIncidentReport.id, SdIncidentReport.status)
            .filter(SdIncidentReport.ticket_id.in_(ticket_ids),
                    SdIncidentReport.is_deleted == False).all())  # noqa: E712
    return {str(r[0]): (r[1], r[2]) for r in rows}


def _get_pir(db: Session, pir_id: UUID) -> SdIncidentReport:
    p = (db.query(SdIncidentReport)
         .filter(SdIncidentReport.id == pir_id, SdIncidentReport.is_deleted == False)  # noqa: E712
         .first())
    if not p:
        raise HTTPException(404, "Post-incident report not found")
    return p


def _require_pir_scope(db: Session, p: SdIncidentReport, admin: User) -> None:
    """PIR visibility = the linked ticket's team seal (404 outside scope, existence never
    leaks). Deliberately NOT via _get_ticket: an ARCHIVED incident's PIR must stay readable."""
    if getattr(admin, "is_superuser", False):
        return
    cond = _scope_cond(db, admin)
    if cond is None:
        return
    if not db.query(SdTicket.id).filter(SdTicket.id == p.ticket_id, cond).first():
        raise HTTPException(404, "Post-incident report not found")


def _pir_ticket(db: Session, p: SdIncidentReport) -> SdTicket | None:
    return db.get(SdTicket, p.ticket_id)


def _enrich_pir(db: Session, p: SdIncidentReport, out: PirResponse) -> PirResponse:
    from app.routers.support_desk._common import _user_names
    t = _pir_ticket(db, p)
    names = _user_names(db, {p.created_by_id, p.submitted_by_id,
                             getattr(t, "incident_commander_id", None)})
    if t is not None:
        out.ticket_number = t.ticket_number
        out.subject = t.subject
        out.sev = ticket_sev(t.priority, bool(t.is_major_incident))
        out.incident_commander_name = names.get(str(t.incident_commander_id))
        # owner identity for the builder's actor-tier gate
        out.assigned_agent_id = t.assigned_agent_id
        out.team_id = t.team_id
        out.incident_commander_id = t.incident_commander_id
        out.collaborators = list(t.collaborators or [])
    out.created_by_name = names.get(str(p.created_by_id))
    out.submitted_by_name = names.get(str(p.submitted_by_id))
    return out


def _ensure_action_aids(register: list) -> list:
    """Stamp a stable 8-hex ``aid`` onto any action item that lacks one — the address
    that survives draft-era reorders (positional index stays as back-compat only)."""
    out = []
    for a in (register or []):
        d = dict(a or {})
        if not str(d.get("aid") or "").strip():
            d["aid"] = uuid_mod.uuid4().hex[:8]
        out.append(d)
    return out


def _require_pir_reviewer(db: Session, p: SdIncidentReport, admin: User) -> SdTicket:
    """Approve/reject = team lead ∪ superuser, with FOUR-EYES: never your own
    submission (superuser exempt — the desk owner can always break a deadlock).
    403 for non-leads points at the submit path; 409 for the submitter."""
    t = _pir_ticket(db, p)
    if t is None:
        raise HTTPException(404, "Post-incident report not found")
    if getattr(admin, "is_superuser", False):
        return t
    from app.routers.support_desk.tickets_self import _team_context, _is_lead
    ctx = _team_context(db, admin)
    ok = t.team_id and any(str(tm.id) == str(t.team_id) and _is_lead(tm, admin.id)
                           for tm in ctx["teams"])
    if not ok:
        raise HTTPException(403, "Signing off a post-incident report is a team-lead/admin "
                                 "decision — submit it for review and a reviewer will take it.")
    if p.submitted_by_id and str(p.submitted_by_id) == str(admin.id):
        raise HTTPException(409, "Four-eyes rule: you submitted this report — a different "
                                 "team lead or an admin must review it.")
    return t


def _json_safe(v):
    return json.loads(json.dumps(v, default=str))


def _generate_pir_number(db: Session) -> str:
    try:
        from app.utils.hr.numbering import next_number
        n = next_number(db, "SUPPORT_PIR")
        if n:
            return n
    except Exception:
        pass
    return f"PIR{uuid_mod.uuid4().hex[:8].upper()}"


def _notify_superusers(db: Session, event: str, ticket: SdTicket | None, title: str, action_url: str):
    """Fan a PIR-review event out to the (few) superusers. Best-effort."""
    try:
        from app.routers.support_desk.tickets import dispatch_safe
        sus = (db.query(User.id).filter(User.is_superuser == True,  # noqa: E712
                                        User.is_active == True)      # noqa: E712
               .limit(10).all())
        for (uid,) in sus:
            if ticket is not None:
                dispatch_safe(db, event, uid, ticket, title=title, action_url=action_url)
    except Exception:
        pass


# ═══════════════════════════════ Dashboard stats ═══════════════════════════════
@router.get("/incidents/stats", response_model=IncidentStatsResponse)
def incident_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """The incident command rollup powering both panels' module dashboards. Team-sealed
    (superuser = whole desk); grouped aggregates only — StaticPool-friendly."""
    cond = _scope_cond(db, admin)
    nowt = sla_util.now_utc()
    today = nowt.replace(hour=0, minute=0, second=0, microsecond=0)
    active = lens_condition("active")
    out = IncidentStatsResponse()

    # ── active posture: one grouped scan folds count / sev / MI / unacked / SLA split ──
    # (posture predicates come from utils.incidents so the list endpoint's `flag`
    # filter and these counts can never drift)
    breached_f = breached_cond()
    at_risk_f = at_risk_cond(nowt)
    rows = _sealed(db.query(
        SdTicket.priority, SdTicket.is_major_incident,
        func.count(SdTicket.id),
        func.sum(case((SdTicket.acknowledged_at.is_(None), 1), else_=0)),
        func.sum(case((breached_f, 1), else_=0)),
        func.sum(case((at_risk_f, 1), else_=0)),
        func.sum(case((SdTicket.incident_commander_id.is_(None), 1), else_=0)),
        func.sum(case((SdTicket.assigned_agent_id.is_(None), 1), else_=0)),
    ).filter(active), cond).group_by(SdTicket.priority, SdTicket.is_major_incident).all()

    by_sev = {"sev1": 0, "sev2": 0, "sev3": 0, "sev4": 0}
    for pri, mi, n, unack, brc, risk, no_cmd, no_own in rows:
        sev = ticket_sev(pri, bool(mi))
        by_sev[f"sev{sev}"] += int(n or 0)
        out.active_total += int(n or 0)
        out.sla.breached += int(brc or 0)
        out.sla.at_risk += int(risk or 0)
        out.unowned += int(no_own or 0)
        if bool(mi):
            out.major_active += int(n or 0)
            out.roles_unassigned += int(no_cmd or 0)
        if sev <= 2:
            out.unacked += int(unack or 0)
    out.by_sev = by_sev
    out.sla.met = max(0, out.active_total - out.sla.breached - out.sla.at_risk)

    # ── today's flow + cadence lapses ──
    out.new_today = _sealed(db.query(func.count(SdTicket.id))
                            .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                                    SdTicket.created_at >= today), cond).scalar() or 0
    out.resolved_today = _sealed(db.query(func.count(SdTicket.id))
                                 .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                                         SdTicket.resolved_at >= today), cond).scalar() or 0
    out.update_overdue = _sealed(db.query(func.count(SdTicket.id))
                                 .filter(active, flag_condition("update_overdue", nowt)),
                                 cond).scalar() or 0

    # ── MTTA (30d rolling) + MTTR month-over-month trend ──
    d30 = nowt - timedelta(days=30)
    mtta = _sealed(db.query(_mins(SdTicket.acknowledged_at, SdTicket.created_at))
                   .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                           SdTicket.acknowledged_at.isnot(None),
                           SdTicket.acknowledged_at >= d30), cond).scalar()
    out.mtta_minutes_30d = round(float(mtta), 1) if mtta is not None else None
    month_start = today.replace(day=1)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    base_res = _sealed(db.query(_mins(SdTicket.resolved_at, SdTicket.created_at))
                       .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                               SdTicket.resolved_at.isnot(None)), cond)
    cur = base_res.filter(SdTicket.resolved_at >= month_start).scalar()
    prev = base_res.filter(SdTicket.resolved_at >= prev_start,
                           SdTicket.resolved_at < month_start).scalar()
    out.mttr_minutes_current_month = round(float(cur), 1) if cur is not None else None
    out.mttr_minutes_prev_month = round(float(prev), 1) if prev is not None else None
    if cur is not None and prev:
        out.mttr_trend_pct = round((float(cur) - float(prev)) / float(prev) * 100.0, 1)

    # ── category breakdown (active) ──
    cat_rows = _sealed(db.query(SdTicket.category_id, func.count(SdTicket.id),
                                func.sum(case((breached_f, 1), else_=0)))
                       .filter(active), cond).group_by(SdTicket.category_id) \
        .order_by(func.count(SdTicket.id).desc()).limit(8).all()
    cat_ids = [r[0] for r in cat_rows if r[0]]
    cat_names = {str(c.id): c.name for c in db.query(SdCategory.id, SdCategory.name)
                 .filter(SdCategory.id.in_(cat_ids)).all()} if cat_ids else {}
    out.by_category = [IncidentCategorySlice(
        key=str(cid) if cid else None,
        label=cat_names.get(str(cid), "Uncategorised"),
        count=int(n or 0), breached=int(b or 0)) for cid, n, b in cat_rows]

    # ── top affected services (JSONB fold — one bounded scan, no N+1) ──
    svc_rows = _sealed(db.query(SdTicket.affected_services, SdTicket.status,
                                SdTicket.priority, SdTicket.is_major_incident)
                       .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                               SdTicket.created_at >= nowt - timedelta(days=90),
                               func.jsonb_array_length(SdTicket.affected_services) > 0),
                       cond).limit(500).all()
    svc: dict[str, IncidentServiceSlice] = {}
    for services, status, pri, mi in svc_rows:
        for s in (services or []):
            label = str(s).strip()
            if not label:
                continue
            slot = svc.setdefault(label, IncidentServiceSlice(service=label))
            slot.count += 1
            if status not in TERMINAL_TICKET_STATUSES:
                slot.open += 1
            if ticket_sev(pri, bool(mi)) <= 2:
                slot.sev12 += 1
    out.top_services = sorted(svc.values(), key=lambda x: -x.count)[:6]

    # ── 14-day created-vs-resolved flow ──
    d14 = today - timedelta(days=13)
    created = dict(_sealed(db.query(func.date(SdTicket.created_at), func.count(SdTicket.id))
                           .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                                   SdTicket.created_at >= d14), cond)
                   .group_by(func.date(SdTicket.created_at)).all())
    resolved = dict(_sealed(db.query(func.date(SdTicket.resolved_at), func.count(SdTicket.id))
                            .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                                    SdTicket.resolved_at >= d14), cond)
                    .group_by(func.date(SdTicket.resolved_at)).all())
    out.trend_14d = [IncidentTrendPoint(day=(d14 + timedelta(days=i)).date(),
                                        created=int(created.get((d14 + timedelta(days=i)).date(), 0)),
                                        resolved=int(resolved.get((d14 + timedelta(days=i)).date(), 0)))
                     for i in range(14)]

    # ── live feed: latest activity on lens tickets (sealed via the ticket join) ──
    feed_rows = _sealed(db.query(SdTicketActivity, SdTicket)
                        .join(SdTicket, SdTicketActivity.ticket_id == SdTicket.id)
                        .filter(lens_condition("all")), cond) \
        .order_by(SdTicketActivity.created_at.desc()).limit(12).all()
    out.feed = [IncidentFeedItem(
        at=a.created_at, action=a.action, actor=a.actor_name, ticket_id=t.id,
        ticket_number=t.ticket_number, subject=t.subject,
        sev=ticket_sev(t.priority, bool(t.is_major_incident))) for a, t in feed_rows]

    # ── review debt: missing RCA (terminal SEV1/2, 30d) + PIR pipeline ──
    # v2 single truth: returned/stale filings correctly read as missing.
    from app.utils.support_desk.rca import rca_absent_cond
    out.missing_rca = _sealed(db.query(func.count(SdTicket.id))
                              .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                                      SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES)),
                                      or_(SdTicket.is_major_incident == True,  # noqa: E712
                                          SdTicket.priority == "critical"),
                                      SdTicket.resolved_at >= d30,
                                      rca_absent_cond()),
                              cond).scalar() or 0
    scoped_tickets = _sealed(db.query(SdTicket.id), cond).scalar_subquery()
    pir_rows = (db.query(SdIncidentReport.status, func.count(SdIncidentReport.id))
                .filter(SdIncidentReport.is_deleted == False,  # noqa: E712
                        SdIncidentReport.ticket_id.in_(scoped_tickets))
                .group_by(SdIncidentReport.status).all())
    pir = IncidentPirCounts()
    for st, n in pir_rows:
        if hasattr(pir, st or ""):
            setattr(pir, st, int(n or 0))
    has_pir = (db.query(SdIncidentReport.ticket_id)
               .filter(SdIncidentReport.is_deleted == False).scalar_subquery())  # noqa: E712
    pir.missing = _sealed(db.query(func.count(SdTicket.id))
                          .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                                  SdTicket.is_major_incident == True,  # noqa: E712
                                  SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES)),
                                  SdTicket.id.notin_(has_pir)), cond).scalar() or 0
    out.pir = pir

    # ── phase clocks (30d, SQL-only aggregates; the full 7-phase track is per-incident
    # via /incidents/{id}/phases). MTTD needs BOTH impact clocks stamped. ──
    mttd = _sealed(db.query(_mins(SdTicket.incident_detected_at, SdTicket.incident_started_at))
                   .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                           SdTicket.incident_started_at.isnot(None),
                           SdTicket.incident_detected_at.isnot(None),
                           SdTicket.incident_detected_at >= d30), cond).scalar()
    out.mttd_minutes_30d = round(float(mttd), 1) if mttd is not None else None
    det_ack = _sealed(db.query(_mins(SdTicket.acknowledged_at,
                                     func.coalesce(SdTicket.incident_detected_at, SdTicket.created_at)))
                      .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                              SdTicket.acknowledged_at.isnot(None),
                              SdTicket.acknowledged_at >= d30), cond).scalar()
    ack_res = _sealed(db.query(_mins(SdTicket.resolved_at, SdTicket.acknowledged_at))
                      .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                              SdTicket.acknowledged_at.isnot(None),
                              SdTicket.resolved_at.isnot(None),
                              SdTicket.resolved_at >= d30), cond).scalar()
    out.phase_minutes_30d = {k: round(float(v), 1) for k, v in
                             (("detect_to_ack", det_ack), ("ack_to_resolve", ack_res))
                             if v is not None}

    # ── MI-candidate docket + PIR action follow-through (chip ⇔ rows via flag_condition;
    # actions share iter_scoped_actions with the list endpoint + sweep) ──
    out.mi_proposals_pending = _sealed(db.query(func.count(SdTicket.id))
                                       .filter(active, flag_condition("mi_proposed", nowt)),
                                       cond).scalar() or 0
    today_iso = nowt.date().isoformat()
    _act_open = _act_over = 0
    for p, _t, _k, _i, a in iter_scoped_actions(db, cond, limit=300):
        if p.status not in (PirStatus.APPROVED.value, PirStatus.PUBLISHED.value):
            continue
        if str(a.get("status") or "open").lower() != "done":
            _act_open += 1
        if action_is_overdue(a, today_iso):
            _act_over += 1
    out.actions_overdue = _act_over
    # PIR v2 lockstep additions (additive keys — legacy pir.missing untouched):
    # owed = the board's server-side debt lens count; actions_open = live follow-through.
    pir.owed = _sealed(db.query(func.count(SdTicket.id))
                       .filter(pir_owed_cond(nowt)), cond).scalar() or 0
    pir.actions_open = _act_open

    # ── Critical-desk rollup (SEV1 ∪ SEV2, LIVE-ONLY — lockstep with the rows
    # ?lens=critical&live=1&… returns; every per-flag count reuses flag_condition so a
    # chip's number always equals its click's rows). Grouped scans only, all additive. ──
    crit_live = and_(lens_condition("critical"),
                     SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
    c = out.critical
    crit_rows = _sealed(db.query(
        SdTicket.is_major_incident,
        func.count(SdTicket.id),
        func.sum(case((SdTicket.acknowledged_at.is_(None), 1), else_=0)),
        func.sum(case((flag_condition("update_overdue", nowt), 1), else_=0)),
        func.sum(case((at_risk_f, 1), else_=0)),
        func.sum(case((breached_f, 1), else_=0)),
        func.sum(case((SdTicket.assigned_agent_id.is_(None), 1), else_=0)),
    ).filter(crit_live), cond).group_by(SdTicket.is_major_incident).all()
    unacked_live = 0
    for mi, n, unack, upd, risk, brc, unown in crit_rows:
        unacked_live += int(unack or 0)
        if bool(mi):
            c.sev1_active += int(n or 0)
        else:   # non-MI on the critical lens = priority 'critical' = SEV2
            c.sev2_active += int(n or 0)
            c.sev2_unacked += int(unack or 0)
            c.sev2_update_overdue += int(upd or 0)
            c.sev2_at_risk += int(risk or 0)
            c.sev2_breached += int(brc or 0)
            c.sev2_unowned += int(unown or 0)
    live_total = c.sev1_active + c.sev2_active
    if live_total:
        c.ack_coverage_pct = round((live_total - unacked_live) / live_total * 100.0, 1)
    oldest = _sealed(db.query(func.min(SdTicket.created_at))
                     .filter(crit_live, sev_cond(2)), cond).scalar()
    if oldest is not None:
        c.oldest_sev2_age_minutes = round(
            max(0.0, (nowt - sla_util._aware(oldest)).total_seconds() / 60.0), 1)

    # exposure rollup — SAME predicates as the flag lenses (counts ⇔ rows)
    bi_rows = _sealed(db.query(SdTicket.business_impact, func.count(SdTicket.id))
                      .filter(crit_live, SdTicket.business_impact.isnot(None)), cond) \
        .group_by(SdTicket.business_impact).all()
    c.exposure.by_business_impact = {str(k): int(n or 0) for k, n in bi_rows if k}
    exp = _sealed(db.query(
        func.sum(case((flag_condition("exposure_compliance", nowt), 1), else_=0)),
        func.sum(case((flag_condition("exposure_security", nowt), 1), else_=0)),
        func.sum(case((flag_condition("exposure_public", nowt), 1), else_=0)),
        func.sum(case((flag_condition("exposure_revenue", nowt), 1), else_=0)),
        func.sum(case((flag_condition("unassessed", nowt), 1), else_=0)),
    ).filter(crit_live), cond).first()
    if exp:
        (c.exposure.compliance, c.exposure.security, c.exposure.public,
         c.exposure.revenue_flagged, c.exposure.unassessed) = [int(x or 0) for x in exp]

    # 30d MI-docket flow + de-escalations — activity counts over the sealed join (like feed)
    act_counts = dict(_sealed(db.query(SdTicketActivity.action, func.count(SdTicketActivity.id))
                              .join(SdTicket, SdTicketActivity.ticket_id == SdTicket.id)
                              .filter(lens_condition("all"),
                                      SdTicketActivity.action.in_(
                                          ("mi_proposed", "mi_confirmed", "mi_declined")),
                                      SdTicketActivity.created_at >= d30), cond)
                      .group_by(SdTicketActivity.action).all())
    c.mi_proposed_30d = int(act_counts.get("mi_proposed", 0) or 0)
    c.mi_confirmed_30d = int(act_counts.get("mi_confirmed", 0) or 0)
    c.mi_declined_30d = int(act_counts.get("mi_declined", 0) or 0)
    c.de_escalations_30d = _sealed(db.query(func.count(SdTicketActivity.id))
                                   .join(SdTicket, SdTicketActivity.ticket_id == SdTicket.id)
                                   .filter(lens_condition("all"),
                                           SdTicketActivity.action == "incident_sev_changed",
                                           SdTicketActivity.detail["to_sev"].astext == "3",
                                           SdTicketActivity.created_at >= d30), cond).scalar() or 0

    # playbook follow-through over the live critical lens
    crit_ids = _sealed(db.query(SdTicket.id).filter(crit_live), cond).scalar_subquery()
    pb = (db.query(func.count(func.distinct(SdIncidentTask.ticket_id)),
                   func.sum(case((SdIncidentTask.status == "open", 1), else_=0)),
                   func.sum(case((SdIncidentTask.status == "done", 1), else_=0)))
          .filter(SdIncidentTask.ticket_id.in_(crit_ids)).first())
    if pb:
        c.playbook.tickets_with_tasks = int(pb[0] or 0)
        c.playbook.tasks_open = int(pb[1] or 0)
        c.playbook.tasks_done = int(pb[2] or 0)

    # responder load — grouped by assignee over the SAME live critical lens (lens-coherent;
    # deliberately NOT CriticalStats.squad, which slices the wider ticket desk)
    rl_rows = _sealed(db.query(
        SdTicket.assigned_agent_id,
        func.sum(case((SdTicket.is_major_incident == True, 1), else_=0)),   # noqa: E712
        func.sum(case((SdTicket.is_major_incident == False, 1), else_=0)),  # noqa: E712
        func.sum(case((SdTicket.acknowledged_at.is_(None), 1), else_=0)),
    ).filter(crit_live, SdTicket.assigned_agent_id.isnot(None)), cond) \
        .group_by(SdTicket.assigned_agent_id).all()
    if rl_rows:
        from app.routers.support_desk._common import _user_names
        rl_names = _user_names(db, {r[0] for r in rl_rows})
        c.responder_load = sorted(
            (IncidentResponderLoad(user_id=uid, name=rl_names.get(str(uid)),
                                   sev1=int(s1 or 0), sev2=int(s2 or 0),
                                   unacked=int(un or 0))
             for uid, s1, s2, un in rl_rows),
            key=lambda r: (-(r.sev1 + r.sev2), -r.unacked, (r.name or "").lower()))[:25]
    return out


# ═══════════════════════════════ Command dashboard (composed) ═══════════════════════════════

def _cluster_signature_str(sig) -> str:
    """Flatten an RcaClusterSignature into a scannable label for the radar."""
    parts = []
    if getattr(sig, "category_name", None):
        parts.append(str(sig.category_name))
    if getattr(sig, "service", None):
        parts.append(str(sig.service))
    if getattr(sig, "keywords", None):
        parts.append(" ".join(str(k) for k in sig.keywords))
    return " · ".join(parts) or "recurring fault"


def _dashboard_extras(db: Session, cond, now) -> CommandDashboardExtras:
    """Always-returned enrichment (team-sealed): next breach, aging ladder, escalation
    posture, war rooms, 30d quality and live playbook progress. Grouped aggregates only
    (StaticPool-friendly) — re-applies the seal on every fresh query root."""
    active = lens_condition("active")
    d30 = now - timedelta(days=30)
    out = CommandDashboardExtras()

    # ── next resolution breach: soonest live deadline, not breached, not paused ──
    nb = _sealed(db.query(SdTicket.id, SdTicket.ticket_number, SdTicket.resolution_due_at)
                 .filter(active,
                         SdTicket.sla_resolution_breached == False,  # noqa: E712
                         SdTicket.sla_paused_since.is_(None),
                         SdTicket.resolution_due_at.isnot(None)),
                 cond).order_by(SdTicket.resolution_due_at.asc()).first()
    if nb:
        mins = max(0.0, (sla_util._aware(nb[2]) - now).total_seconds() / 60.0)
        out.next_breach = NextBreach(ticket_id=nb[0], ticket_number=nb[1],
                                     minutes=round(mins, 1))

    # ── aging ladder over ACTIVE (age = now − created_at); sev12 = SEV1∪SEV2 subset ──
    t8, t4, t2, t1 = (now - timedelta(hours=8), now - timedelta(hours=4),
                      now - timedelta(hours=2), now - timedelta(hours=1))
    sev12 = or_(SdTicket.is_major_incident == True,  # noqa: E712
                SdTicket.priority == "critical")
    ladder = [
        (">8h", SdTicket.created_at < t8),
        ("4-8h", and_(SdTicket.created_at >= t8, SdTicket.created_at < t4)),
        ("2-4h", and_(SdTicket.created_at >= t4, SdTicket.created_at < t2)),
        ("1-2h", and_(SdTicket.created_at >= t2, SdTicket.created_at < t1)),
        ("<1h", SdTicket.created_at >= t1),
    ]
    cols = []
    for _lbl, c in ladder:
        cols.append(func.sum(case((c, 1), else_=0)))
        cols.append(func.sum(case((and_(c, sev12), 1), else_=0)))
    arow = _sealed(db.query(*cols).filter(active), cond).first()
    out.aging_ladder = [
        AgingBucket(bucket=lbl,
                    count=int((arow[i * 2] if arow else 0) or 0),
                    sev12=int((arow[i * 2 + 1] if arow else 0) or 0))
        for i, (lbl, _c) in enumerate(ladder)]

    # ── escalation posture over ACTIVE ──
    lvl = func.coalesce(SdTicket.escalation_level, 0)
    erow = _sealed(db.query(
        func.sum(case((lvl <= 0, 1), else_=0)),
        func.sum(case((lvl == 1, 1), else_=0)),
        func.sum(case((lvl >= 2, 1), else_=0)),
        func.sum(case((SdTicket.is_escalated == True, 1), else_=0)),  # noqa: E712
        func.sum(case((SdTicket.auto_escalated_at >= d30, 1), else_=0)),
    ).filter(active), cond).first()
    if erow:
        out.escalation = EscalationTiers(
            l1=int(erow[0] or 0), l2=int(erow[1] or 0), l3=int(erow[2] or 0),
            escalated_total=int(erow[3] or 0), auto_escalated_30d=int(erow[4] or 0))

    # ── war rooms open on live incidents ──
    out.war_rooms = _sealed(db.query(func.count(SdTicket.id))
                            .filter(active, SdTicket.war_room_url.isnot(None),
                                    SdTicket.war_room_url != ""), cond).scalar() or 0

    # ── quality over 30d-resolved incidents (csat + reopen/FCR) ──
    res30 = and_(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                 SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30)
    qrow = _sealed(db.query(
        func.count(SdTicket.id),
        func.avg(SdTicket.csat_score),                                    # ignores NULLs
        func.sum(case((SdTicket.csat_score.isnot(None), 1), else_=0)),
        func.sum(case((SdTicket.reopened_count > 0, 1), else_=0)),
    ).filter(res30), cond).first()
    q = IncidentQuality()
    if qrow and int(qrow[0] or 0):
        total = int(qrow[0])
        reopened = int(qrow[3] or 0)
        q.csat_avg = round(float(qrow[1]), 2) if qrow[1] is not None else None
        q.csat_responses = int(qrow[2] or 0)
        q.reopen_rate_pct = round(100.0 * reopened / total, 1)
        q.fcr_pct = round(100.0 * (total - reopened) / total, 1)
    out.quality = q

    # ── live playbook progress over ACTIVE incident ids ──
    active_ids = _sealed(db.query(SdTicket.id).filter(active), cond).scalar_subquery()
    trow = (db.query(
        func.count(func.distinct(SdIncidentTask.ticket_id)),
        func.sum(case((SdIncidentTask.status == "open", 1), else_=0)),
        func.sum(case((SdIncidentTask.status == "done", 1), else_=0)),
    ).filter(SdIncidentTask.ticket_id.in_(active_ids),
             SdIncidentTask.status != "skipped").first())
    tl = TasksLive()
    if trow:
        tl.tickets_with_tasks = int(trow[0] or 0)
        tl.open = int(trow[1] or 0)
        tl.done = int(trow[2] or 0)
        denom = tl.open + tl.done
        tl.progress_pct = round(100.0 * tl.done / denom, 1) if denom else 0.0
    out.tasks_live = tl
    return out


def _admin_block(db: Session, admin: User, cond, now) -> AdminIncidentBlock:
    """Superuser-only desk-wide intelligence. ``cond`` is None for a superuser (whole
    desk); _sealed() stays on every root so the helper is correct even if called scoped.
    Reuses the module's single-truth builders so numbers stay lockstep with their own
    endpoints. Those builders (pir_board/_rca_stats/rca_analytics/rca_clusters) are
    defined later in the module — resolved at call time, so the forward refs are fine."""
    from app.routers.support_desk._common import _user_names
    active = lens_condition("active")
    d30 = now - timedelta(days=30)
    res30 = and_(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                 SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30)
    out = AdminIncidentBlock()

    # ── leaderboard: top solvers (30d) + live active load ──
    lb_rows = _sealed(db.query(
        SdTicket.resolved_by_id, func.count(SdTicket.id),
        _mins(SdTicket.resolved_at, SdTicket.created_at),
    ).filter(res30, SdTicket.resolved_by_id.isnot(None)), cond) \
        .group_by(SdTicket.resolved_by_id) \
        .order_by(func.count(SdTicket.id).desc()).limit(12).all()
    lb_ids = [r[0] for r in lb_rows if r[0]]
    load = dict(_sealed(db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
                        .filter(active, SdTicket.assigned_agent_id.in_(lb_ids)), cond)
                .group_by(SdTicket.assigned_agent_id).all()) if lb_ids else {}
    lb_names = _user_names(db, set(lb_ids))
    out.leaderboard = [LeaderRow(
        user_id=uid, name=lb_names.get(str(uid)), resolved_30d=int(n or 0),
        mttr_minutes=round(float(mt), 1) if mt is not None else None,
        active_load=int(load.get(uid, 0) or 0)) for uid, n, mt in lb_rows]

    # ── per-team posture (ACTIVE) + 30d outcome metrics ──
    breached_f = breached_cond()
    at_risk_f = at_risk_cond(now)
    pt_rows = _sealed(db.query(
        SdTicket.team_id, func.count(SdTicket.id),
        func.sum(case((breached_f, 1), else_=0)),
        func.sum(case((at_risk_f, 1), else_=0)),
    ).filter(active), cond).group_by(SdTicket.team_id) \
        .order_by(func.count(SdTicket.id).desc()).limit(12).all()
    m30 = {str(r[0]): r for r in _sealed(db.query(
        SdTicket.team_id, _mins(SdTicket.resolved_at, SdTicket.created_at),
        func.avg(SdTicket.csat_score), func.count(SdTicket.id),
        func.sum(case((SdTicket.reopened_count > 0, 1), else_=0)),
    ).filter(res30), cond).group_by(SdTicket.team_id).all()}
    team_ids = {r[0] for r in pt_rows if r[0]}
    teams = {str(r.id): r.name for r in db.query(SdTeam.id, SdTeam.name)
             .filter(SdTeam.id.in_(team_ids)).all()} if team_ids else {}
    per_team = []
    for tid, act, brc, risk in pt_rows:
        act = int(act or 0)
        met = max(0, act - int(brc or 0) - int(risk or 0))
        m = m30.get(str(tid))
        cnt30 = int(m[3] or 0) if m else 0
        per_team.append(TeamRow(
            team_id=tid, team_name=teams.get(str(tid)) if tid else None, active=act,
            sla_met_pct=round(100.0 * met / act, 1) if act else 0.0,
            mttr_minutes=round(float(m[1]), 1) if m and m[1] is not None else None,
            csat_avg=round(float(m[2]), 2) if m and m[2] is not None else None,
            reopen_pct=round(100.0 * int(m[4] or 0) / cnt30, 1) if cnt30 else None))
    out.per_team = per_team

    # ── RCA program: pipeline+coverage from _rca_stats(30d), latency/kedb from analytics ──
    rstats = _rca_stats(db, cond, now, 30)
    ranalytics = rca_analytics(days=90, db=db, admin=admin)
    out.rca = RcaSummary(
        coverage_pct=float(rstats.coverage_pct) if rstats.coverage_pct is not None else None,
        owed=rstats.owed, pending=rstats.pending, returned=rstats.returned,
        validated=rstats.validated, stale=rstats.stale,
        cycle_time_median_h=ranalytics.cycle_time.median_hours,
        review_latency_median_h=ranalytics.review_latency.median_hours,
        kedb_known_errors=ranalytics.kedb.known_errors,
        kedb_workarounds=ranalytics.kedb.published_workarounds)

    # ── PIR program: the board's lockstep stats (limit=1 — we only read .stats) ──
    ps = pir_board(lens="all", q=None, sev=None, page=1, limit=1,
                   sort="updated", sort_dir="desc", db=db, admin=admin).stats
    out.pir = PirSummary(
        owed=ps.owed, draft=ps.draft, in_review=ps.in_review, approved=ps.approved,
        published=ps.published, actions_open=ps.actions_open,
        actions_overdue=ps.actions_overdue, actions_due=ps.actions_due,
        coverage_pct=ps.coverage_pct, median_review_hours_30d=ps.median_review_hours_30d,
        published_30d=ps.published_30d)

    # ── recurrence radar: reuse the cluster engine, top 6 ──
    clusters = rca_clusters(days=90, min_size=3, limit=6, db=db, admin=admin).clusters
    out.recurring = [RecurringRow(
        signature=_cluster_signature_str(c.signature), count=c.count, score=c.score,
        sev_worst=c.sev_worst, has_open_problem=c.has_open_problem,
        suggested_problem_title=c.suggested_problem_title) for c in clusters[:6]]

    # ── escalation heatmap (7d × tier), SQL group_by on date(escalated_at) × tier ──
    day0 = (now - timedelta(days=6)).date()
    tier_case = case((func.coalesce(SdTicket.escalation_level, 0) <= 0, 1),
                     (SdTicket.escalation_level == 1, 2), else_=3)
    heat_rows = _sealed(db.query(func.date(SdTicket.escalated_at), tier_case,
                                 func.count(SdTicket.id))
                        .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                                SdTicket.escalated_at.isnot(None),
                                SdTicket.escalated_at >= now - timedelta(days=7)),
                        cond).group_by(func.date(SdTicket.escalated_at), tier_case).all()
    heat = []
    for d, tier, n in heat_rows:
        if d is None:
            continue
        di = (d - day0).days
        if 0 <= di <= 6:
            heat.append(HeatCell(tier=int(tier), day_index=di, count=int(n or 0)))
    out.escalation_heatmap = heat

    # ── busy-hours heatmap (30d creation density, weekday 0=Mon via isodow−1) ──
    dow = func.extract("isodow", SdTicket.created_at)
    hour = func.extract("hour", SdTicket.created_at)
    busy_rows = _sealed(db.query(dow, hour, func.count(SdTicket.id))
                        .filter(lens_condition("all"), SdTicket.merged_into_id.is_(None),
                                SdTicket.created_at >= d30), cond) \
        .group_by(dow, hour).all()
    out.busy_hours = [BusyCell(weekday=int(wd) - 1, hour=int(hr), count=int(n or 0))
                      for wd, hr, n in busy_rows if n]
    return out


@router.get("/incidents/command-dashboard", response_model=CommandDashboardResponse)
def command_dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """ONE composed payload powering both incident dashboards (agent Fault Grid / admin
    Command Funnel). Team-sealed for agents/leads (the ``agent`` + ``extras`` blocks
    always come back scoped to the caller's slice); the desk-wide ``admin`` block is
    populated for superusers only. Every number reuses the module's single-truth
    builders (incident_stats / _rca_stats / rca_analytics / pir_board / rca_clusters)
    so it stays lockstep with its own dedicated endpoint. Literal route — registered
    BEFORE /incidents/{ticket_id}/* (route-shadowing discipline)."""
    is_superuser = bool(getattr(admin, "is_superuser", False))
    now = sla_util.now_utc()
    cond = _scope_cond(db, admin)
    return CommandDashboardResponse(
        generated_at=now,
        is_superuser=is_superuser,
        agent=incident_stats(db=db, admin=admin),
        extras=_dashboard_extras(db, cond, now),
        admin=_admin_block(db, admin, cond, now) if is_superuser else None,
    )


# ═══════════════════════════════ Sealed list ═══════════════════════════════
@router.get("/incidents/", response_model=IncidentListResponse)
def list_incidents(
    lens: str = Query("active", description="active|major|critical|all"),
    live: bool = Query(False, description="Exclude terminal (resolved/closed) rows from ANY "
                                          "lens — the Critical boards' live view. Additive: "
                                          "omitted keeps the original lens shape byte-identical."),
    sev: Optional[int] = Query(None, ge=1, le=4),
    flag: Optional[str] = Query(None, description="|".join(INCIDENT_FLAGS)),
    status_f: Optional[str] = Query(None, alias="status"),
    category_id: Optional[UUID] = None,
    service: Optional[str] = Query(None, max_length=120),
    owner_id: Optional[UUID] = None,
    q: Optional[str] = Query(None, max_length=160),
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=100),
    sort_by: Optional[str] = Query(None, description="created_at|ticket_number"),
    sort_dir: str = Query("desc", description="asc|desc"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Server-paged sealed board. Defaults (page=1, limit=100, no flag/sort) keep the
    original single-window shape for older callers. ``flag`` filters by the same
    posture predicates the stats chips count with (utils.incidents.flag_condition),
    so a chip's number always equals its click's rows. Default order: SEV-first,
    newest-first; ``sort_by`` narrows to a single column for sortable headers."""
    if lens not in ("active", "major", "critical", "all"):
        raise HTTPException(422, "lens must be one of: active, major, critical, all")
    if flag and flag not in INCIDENT_FLAGS:
        raise HTTPException(422, "flag must be one of: " + ", ".join(INCIDENT_FLAGS))
    if sort_by and sort_by not in ("created_at", "ticket_number"):
        raise HTTPException(422, "sort_by must be one of: created_at, ticket_number")
    cond = _scope_cond(db, admin)
    query = _sealed(db.query(SdTicket).filter(lens_condition(lens)), cond)
    if live:   # lens 'critical' deliberately keeps terminal rows (review debt) — live=1 opts out
        query = query.filter(SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
    if sev:
        query = query.filter(sev_cond(sev))
    if flag:
        query = query.filter(flag_condition(flag, sla_util.now_utc()))
    if status_f:
        query = query.filter(SdTicket.status == status_f)
    if category_id:
        query = query.filter(SdTicket.category_id == category_id)
    if service:
        query = query.filter(SdTicket.affected_services.contains([service]))
    if owner_id:
        query = query.filter(or_(SdTicket.assigned_agent_id == owner_id,
                                 SdTicket.incident_commander_id == owner_id))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like),
                                 SdTicket.ticket_number.ilike(like)))
    if date_from:
        query = query.filter(SdTicket.created_at >= date_from)
    if date_to:
        query = query.filter(SdTicket.created_at <= date_to)
    total = query.count()
    if sort_by:
        col = SdTicket.created_at if sort_by == "created_at" else SdTicket.ticket_number
        order = (col.asc(), SdTicket.id.asc()) if sort_dir == "asc" else (col.desc(), SdTicket.id.desc())
    else:
        order = (SdTicket.is_major_incident.desc(), _PRI_RANK.desc(), SdTicket.created_at.desc())
    rows = (query.order_by(*order).offset((page - 1) * limit).limit(limit).all())
    names = _names_for(db, rows)
    cat_ids = {t.category_id for t in rows if t.category_id}
    cats = {str(c.id): c.name for c in db.query(SdCategory.id, SdCategory.name)
            .filter(SdCategory.id.in_(cat_ids)).all()} if cat_ids else {}
    team_ids = {t.team_id for t in rows if t.team_id}
    teams = {str(r.id): r.name for r in db.query(SdTeam.id, SdTeam.name)
             .filter(SdTeam.id.in_(team_ids)).all()} if team_ids else {}
    pirs = _pir_map(db, [t.id for t in rows])
    # parent numbers + live-child rollups for the page (both single bounded queries)
    parent_ids = {t.parent_incident_id for t in rows if t.parent_incident_id}
    parents = {str(r.id): r.ticket_number for r in db.query(SdTicket.id, SdTicket.ticket_number)
               .filter(SdTicket.id.in_(parent_ids)).all()} if parent_ids else {}
    kid_rows = (db.query(SdTicket.parent_incident_id, func.count(SdTicket.id))
                .filter(SdTicket.parent_incident_id.in_([t.id for t in rows]),
                        SdTicket.is_deleted == False,  # noqa: E712
                        SdTicket.merged_into_id.is_(None))
                .group_by(SdTicket.parent_incident_id).all()) if rows else []
    kids = {str(pid): int(n or 0) for pid, n in kid_rows}
    tasks = _task_map(db, [t.id for t in rows])
    return IncidentListResponse(total=total, page=page, limit=limit,
                                items=[_row(t, names, cats, teams, pirs, parents, kids, tasks)
                                       for t in rows])


# ═══════════════════════════════ Cross-incident timeline ═══════════════════════════════

_TIMELINE_ACTORS = ("human", "system")
_TIMELINE_EXPOSURES = ("security", "compliance", "public", "revenue")


def _validated_kinds(kinds: Optional[str]) -> list[str]:
    """Split + 422-validate the kinds CSV against the catalog. Grouped CSV values
    (the admin PIR-trail select) arrive as one comma-joined string — same path."""
    wanted = [k.strip() for k in (kinds or "").split(",") if k.strip()]
    unknown = [k for k in wanted if k not in ACTIVITY_CATALOG]
    if unknown:
        raise HTTPException(422, f"Unknown event kind(s): {', '.join(sorted(unknown))} — "
                                 "see /incidents/timeline/catalog for the registry.")
    return wanted


def _parse_since(since: Optional[str]):
    """Cursor format: '<created_at ISO>~<activity uuid>' (total order created_at, id)."""
    if not since:
        return None
    try:
        ts_s, uid_s = since.split("~", 1)
        ts = datetime.fromisoformat(ts_s)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt_timezone.utc)
        return ts, UUID(uid_s)
    except (ValueError, TypeError):
        raise HTTPException(422, "since must be '<ISO timestamp>~<activity id>' "
                                 "(the cursor a previous timeline response returned).")


def _cursor_of(a: SdTicketActivity) -> str:
    return f"{a.created_at.isoformat()}~{a.id}"


def _timeline_event(a: SdTicketActivity, t: SdTicket) -> IncidentTimelineEvent:
    meta = timeline_meta(a.action)
    return IncidentTimelineEvent(
        at=a.created_at, action=a.action, actor=a.actor_name, detail=a.detail or {},
        ticket_id=t.id, ticket_number=t.ticket_number, subject=t.subject,
        sev=ticket_sev(t.priority, bool(t.is_major_incident)), status=t.status,
        id=a.id, category=meta["category"],
        label=meta["label"] or a.action.replace("_", " "),
        is_milestone=bool(a.is_milestone), actor_user_id=a.actor_user_id,
        team_id=t.team_id, assigned_agent_id=t.assigned_agent_id,
        incident_commander_id=t.incident_commander_id)


def _timeline_query(db: Session, admin: User, *, date_from, date_to, sev, category_id,
                    owner_id, kinds, q, team_id=None, actor_id=None, actor=None,
                    exposure=None, mi_only=None, milestones=None, since=None):
    cond = _scope_cond(db, admin)
    query = _sealed(db.query(SdTicketActivity, SdTicket)
                    .join(SdTicket, SdTicketActivity.ticket_id == SdTicket.id)
                    .filter(lens_condition("all")), cond)
    if sev:
        query = query.filter(sev_cond(sev))
    if category_id:
        query = query.filter(SdTicket.category_id == category_id)
    if owner_id:
        query = query.filter(or_(SdTicket.assigned_agent_id == owner_id,
                                 SdTicket.incident_commander_id == owner_id))
    if team_id:
        # AND-composes UNDER the seal — an out-of-scope team just yields an empty page.
        query = query.filter(SdTicket.team_id == team_id)
    if actor_id:
        query = query.filter(SdTicketActivity.actor_user_id == actor_id)
    if actor == "human":
        query = query.filter(SdTicketActivity.actor_user_id.isnot(None))
    elif actor == "system":
        query = query.filter(SdTicketActivity.actor_user_id.is_(None))
    if exposure:
        query = query.filter(flag_condition(f"exposure_{exposure}", sla_util.now_utc()))
    if mi_only:
        query = query.filter(SdTicket.is_major_incident == True)  # noqa: E712
    if milestones:
        query = query.filter(SdTicketActivity.is_milestone == True)  # noqa: E712
    wanted = _validated_kinds(kinds)
    if wanted:
        query = query.filter(SdTicketActivity.action.in_(wanted))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))
    if date_from:
        query = query.filter(SdTicketActivity.created_at >= date_from)
    if date_to:
        query = query.filter(SdTicketActivity.created_at <= date_to)
    if since:
        ts, uid = since
        query = query.filter(or_(SdTicketActivity.created_at > ts,
                                 and_(SdTicketActivity.created_at == ts,
                                      SdTicketActivity.id > uid)))
    return query


@router.get("/incidents/timeline", response_model=IncidentTimelineResponse)
def incident_timeline(
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    sev: Optional[int] = Query(None, ge=1, le=4),
    category_id: Optional[UUID] = None,
    owner_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    actor_id: Optional[UUID] = None,
    actor: Optional[str] = Query(None, description="human|system"),
    exposure: Optional[str] = Query(None, description="security|compliance|public|revenue"),
    mi_only: Optional[int] = Query(None, ge=0, le=1),
    milestones: Optional[int] = Query(None, ge=0, le=1, description="pinned milestones only"),
    since: Optional[str] = Query(None, description="incremental cursor from a prior response"),
    kinds: Optional[str] = Query(None, description="csv of activity actions (see /timeline/catalog)"),
    q: Optional[str] = Query(None, max_length=160),
    tz_offset: int = Query(0, ge=-840, le=840, description="minutes east of UTC for day buckets"),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """The chronological river: every activity on every incident-lens ticket the caller
    can see, newest-first, bucketed into the caller's LOCAL days (tz_offset, same
    convention as the calendar). ``since`` turns the call incremental (live ticker):
    only events newer than the cursor return, and ``cursor`` advances. The cursor is
    taken from THIS response's newest event — meaningful on page 1 / live polls."""
    if actor and actor not in _TIMELINE_ACTORS:
        raise HTTPException(422, "actor must be 'human' or 'system'")
    if exposure and exposure not in _TIMELINE_EXPOSURES:
        raise HTTPException(422, f"exposure must be one of: {', '.join(_TIMELINE_EXPOSURES)}")
    query = _timeline_query(db, admin, date_from=date_from, date_to=date_to, sev=sev,
                            category_id=category_id, owner_id=owner_id, kinds=kinds, q=q,
                            team_id=team_id, actor_id=actor_id, actor=actor,
                            exposure=exposure, mi_only=mi_only, milestones=milestones,
                            since=_parse_since(since))
    total = query.count()
    rows = (query.order_by(SdTicketActivity.created_at.desc(), SdTicketActivity.id.desc())
            .offset((page - 1) * limit).limit(limit).all())
    days: dict = {}
    order: list = []
    for a, t in rows:
        local_day = (a.created_at + timedelta(minutes=tz_offset)).date()
        if local_day not in days:
            days[local_day] = IncidentTimelineDay(day=local_day)
            order.append(local_day)
        days[local_day].events.append(_timeline_event(a, t))
    cursor = _cursor_of(rows[0][0]) if rows else since
    return IncidentTimelineResponse(total=total, page=page, limit=limit,
                                    days=[days[d] for d in order], cursor=cursor)


def _export_filters(*, date_from, date_to, sev, category_id, owner_id, team_id, actor_id,
                    actor, exposure, mi_only, milestones, kinds, q) -> dict:
    """Echo of the caller's filters for export headers/metadata (None values dropped)."""
    raw = {"from": date_from, "to": date_to, "sev": sev, "category_id": category_id,
           "owner_id": owner_id, "team_id": team_id, "actor_id": actor_id, "actor": actor,
           "exposure": exposure, "mi_only": mi_only, "milestones": milestones,
           "kinds": kinds, "q": q}
    return {k: (v.isoformat() if isinstance(v, datetime) else str(v))
            for k, v in raw.items() if v not in (None, "", 0)}


@router.get("/incidents/timeline/export.csv")
def incident_timeline_csv(
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    sev: Optional[int] = Query(None, ge=1, le=4),
    category_id: Optional[UUID] = None,
    owner_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    actor_id: Optional[UUID] = None,
    actor: Optional[str] = Query(None),
    exposure: Optional[str] = Query(None),
    mi_only: Optional[int] = Query(None, ge=0, le=1),
    milestones: Optional[int] = Query(None, ge=0, le=1),
    kinds: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=160),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """CSV stream of the same sealed timeline query (capped at 2000 rows). The first
    8 columns are a frozen contract; label/category/milestone are additive trailers."""
    if actor and actor not in _TIMELINE_ACTORS:
        raise HTTPException(422, "actor must be 'human' or 'system'")
    if exposure and exposure not in _TIMELINE_EXPOSURES:
        raise HTTPException(422, f"exposure must be one of: {', '.join(_TIMELINE_EXPOSURES)}")
    query = _timeline_query(db, admin, date_from=date_from, date_to=date_to, sev=sev,
                            category_id=category_id, owner_id=owner_id, kinds=kinds, q=q,
                            team_id=team_id, actor_id=actor_id, actor=actor,
                            exposure=exposure, mi_only=mi_only, milestones=milestones)
    rows = query.order_by(SdTicketActivity.created_at.desc()).limit(2000).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["at_utc", "ticket_number", "subject", "sev", "status", "event", "actor",
                "detail", "label", "category", "milestone"])
    for a, t in rows:
        meta = timeline_meta(a.action)
        w.writerow([a.created_at.isoformat() if a.created_at else "",
                    t.ticket_number, t.subject,
                    f"SEV{ticket_sev(t.priority, bool(t.is_major_incident))}",
                    t.status, a.action, a.actor_name or "System",
                    json.dumps(a.detail or {}, default=str)[:500],
                    meta["label"] or a.action.replace("_", " "), meta["category"],
                    "yes" if a.is_milestone else ""])
    buf.seek(0)
    stamp = sla_util.now_utc().strftime("%Y%m%d-%H%M")
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition":
                                      f'attachment; filename="incident-timeline-{stamp}.csv"'})


@router.get("/incidents/timeline/catalog", response_model=TimelineCatalogResponse)
def timeline_catalog(admin: User = Depends(get_support_agent)):
    """The event-type registry (ACTIVITY_CATALOG): labels, categories, tones,
    milestone eligibility. Static taxonomy — no seal needed, zero row data. Drives
    the timeline desks' kind chips so client and server can never drift."""
    entries = [TimelineCatalogEntry(action=k, label=m["label"] or k.replace("_", " "),
                                    category=m["category"], tone=m["tone"],
                                    milestone_eligible=m["milestone"], system=m["system"])
               for k, m in ACTIVITY_CATALOG.items()]
    return TimelineCatalogResponse(
        categories=list(TIMELINE_CATEGORIES), actions=entries,
        milestone_cap=MILESTONES_PER_TICKET,
        milestone_eligible=[k for k, m in ACTIVITY_CATALOG.items() if m["milestone"]])


@router.get("/incidents/timeline/pulse", response_model=TimelinePulseResponse)
def timeline_pulse(
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    sev: Optional[int] = Query(None, ge=1, le=4),
    category_id: Optional[UUID] = None,
    owner_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    mi_only: Optional[int] = Query(None, ge=0, le=1),
    tz_offset: int = Query(0, ge=-840, le=840),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Window-scoped aggregates for the timeline hero instruments — density buckets,
    category/sev mix, actor leaderboard, raised-vs-restored flow, window MTTA/MTTR,
    busiest incident, per-team split. Everything aggregates in SQL under the SAME
    seal + filters as the feed, so the instrument can never disagree with the rows."""
    nowt = sla_util.now_utc()
    to_at = date_to or nowt
    from_at = date_from or (to_at - timedelta(days=7))
    if from_at >= to_at:
        raise HTTPException(422, "from must precede to")
    bucket = "hour" if (to_at - from_at) <= timedelta(hours=48) else "day"

    def base():
        return _timeline_query(db, admin, date_from=from_at, date_to=to_at, sev=sev,
                               category_id=category_id, owner_id=owner_id, kinds=None,
                               q=None, team_id=team_id, mi_only=mi_only)

    shifted = SdTicketActivity.created_at + timedelta(minutes=tz_offset)
    b_expr = func.date_trunc(bucket, shifted)
    density = [TimelinePulseDensityPoint(at=b, count=int(n or 0))
               for b, n in (base().with_entities(b_expr, func.count(SdTicketActivity.id))
                            .group_by(b_expr).order_by(b_expr).all())]
    total_events = sum(p.count for p in density)

    cat_expr = timeline_category_case()
    by_category = {str(c): int(n or 0)
                   for c, n in (base().with_entities(cat_expr, func.count(SdTicketActivity.id))
                                .group_by(cat_expr).all())}
    by_sev: dict[str, int] = {}
    for mi, pri, n in (base().with_entities(SdTicket.is_major_incident, SdTicket.priority,
                                            func.count(SdTicketActivity.id))
                       .group_by(SdTicket.is_major_incident, SdTicket.priority).all()):
        key = f"sev{ticket_sev(pri, bool(mi))}"
        by_sev[key] = by_sev.get(key, 0) + int(n or 0)

    milestones_n = base().filter(SdTicketActivity.is_milestone == True).count()  # noqa: E712
    system_n = base().filter(SdTicketActivity.actor_user_id.is_(None)).count()
    human_n = total_events - system_n

    top_actors = [TimelinePulseActor(actor_user_id=uid, name=name, count=int(n or 0))
                  for uid, name, n in
                  (base().filter(SdTicketActivity.actor_user_id.isnot(None))
                   .with_entities(SdTicketActivity.actor_user_id, SdTicketActivity.actor_name,
                                  func.count(SdTicketActivity.id))
                   .group_by(SdTicketActivity.actor_user_id, SdTicketActivity.actor_name)
                   .order_by(func.count(SdTicketActivity.id).desc()).limit(5).all())]

    flow = TimelinePulseFlow(
        created=base().filter(SdTicketActivity.action == "created").count(),
        resolved=base().filter(SdTicketActivity.action == "resolved").count())

    # Window MTTA/MTTR over TICKETS (created→acked / created→resolved landing in-window),
    # same seal + ticket-side filters as the feed.
    cond = _scope_cond(db, admin)
    tq = _sealed(db.query(SdTicket).filter(lens_condition("all")), cond)
    if sev:
        tq = tq.filter(sev_cond(sev))
    if category_id:
        tq = tq.filter(SdTicket.category_id == category_id)
    if owner_id:
        tq = tq.filter(or_(SdTicket.assigned_agent_id == owner_id,
                           SdTicket.incident_commander_id == owner_id))
    if team_id:
        tq = tq.filter(SdTicket.team_id == team_id)
    if mi_only:
        tq = tq.filter(SdTicket.is_major_incident == True)  # noqa: E712
    mtta = (tq.filter(SdTicket.acknowledged_at.isnot(None),
                      SdTicket.acknowledged_at >= from_at, SdTicket.acknowledged_at <= to_at)
            .with_entities(_mins(SdTicket.acknowledged_at, SdTicket.created_at)).scalar())
    mttr = (tq.filter(SdTicket.resolved_at.isnot(None),
                      SdTicket.resolved_at >= from_at, SdTicket.resolved_at <= to_at)
            .with_entities(_mins(SdTicket.resolved_at, SdTicket.created_at)).scalar())

    busiest = None
    brow = (base().with_entities(SdTicketActivity.ticket_id, func.count(SdTicketActivity.id))
            .group_by(SdTicketActivity.ticket_id)
            .order_by(func.count(SdTicketActivity.id).desc()).first())
    if brow:
        bt = db.query(SdTicket).filter(SdTicket.id == brow[0]).first()
        if bt:
            busiest = TimelinePulseBusiest(
                ticket_id=bt.id, ticket_number=bt.ticket_number, subject=bt.subject,
                sev=ticket_sev(bt.priority, bool(bt.is_major_incident)), events=int(brow[1] or 0))

    team_rows = (base().with_entities(SdTicket.team_id, func.count(SdTicketActivity.id))
                 .group_by(SdTicket.team_id).order_by(func.count(SdTicketActivity.id).desc())
                 .all())
    t_ids = {tid for tid, _n in team_rows if tid}
    t_names = {str(r.id): r.name for r in db.query(SdTeam.id, SdTeam.name)
               .filter(SdTeam.id.in_(t_ids)).all()} if t_ids else {}
    by_team = [TimelinePulseTeam(team_id=tid, team_name=t_names.get(str(tid)) if tid else None,
                                 count=int(n or 0)) for tid, n in team_rows]

    return TimelinePulseResponse(
        from_at=from_at, to_at=to_at, tz_offset=tz_offset, bucket=bucket,
        total_events=total_events, density=density, by_category=by_category, by_sev=by_sev,
        milestones=milestones_n, system_events=system_n, human_events=human_n,
        top_actors=top_actors, flow=flow,
        mtta_minutes=round(float(mtta), 1) if mtta is not None else None,
        mttr_minutes=round(float(mttr), 1) if mttr is not None else None,
        busiest=busiest, by_team=by_team)


@router.get("/incidents/timeline/export.json")
def incident_timeline_json(
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    sev: Optional[int] = Query(None, ge=1, le=4),
    category_id: Optional[UUID] = None,
    owner_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    actor_id: Optional[UUID] = None,
    actor: Optional[str] = Query(None),
    exposure: Optional[str] = Query(None),
    mi_only: Optional[int] = Query(None, ge=0, le=1),
    milestones: Optional[int] = Query(None, ge=0, le=1),
    kinds: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=160),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """JSON export of the sealed timeline (same 2000-row cap as the CSV —
    StaticPool discipline). Flat events, filters echoed, cap flagged."""
    if actor and actor not in _TIMELINE_ACTORS:
        raise HTTPException(422, "actor must be 'human' or 'system'")
    if exposure and exposure not in _TIMELINE_EXPOSURES:
        raise HTTPException(422, f"exposure must be one of: {', '.join(_TIMELINE_EXPOSURES)}")
    query = _timeline_query(db, admin, date_from=date_from, date_to=date_to, sev=sev,
                            category_id=category_id, owner_id=owner_id, kinds=kinds, q=q,
                            team_id=team_id, actor_id=actor_id, actor=actor,
                            exposure=exposure, mi_only=mi_only, milestones=milestones)
    total = query.count()
    rows = query.order_by(SdTicketActivity.created_at.desc()).limit(2000).all()
    events = [_timeline_event(a, t).model_dump(mode="json") for a, t in rows]
    payload = {"generated_at": sla_util.now_utc().isoformat(),
               "filters": _export_filters(date_from=date_from, date_to=date_to, sev=sev,
                                          category_id=category_id, owner_id=owner_id,
                                          team_id=team_id, actor_id=actor_id, actor=actor,
                                          exposure=exposure, mi_only=mi_only,
                                          milestones=milestones, kinds=kinds, q=q),
               "total": total, "capped": total > len(events), "events": events}
    stamp = sla_util.now_utc().strftime("%Y%m%d-%H%M")
    return StreamingResponse(iter([json.dumps(payload, default=str)]),
                             media_type="application/json",
                             headers={"Content-Disposition":
                                      f'attachment; filename="incident-timeline-{stamp}.json"'})


@router.get("/incidents/timeline/export.pdf")
def incident_timeline_pdf(
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    sev: Optional[int] = Query(None, ge=1, le=4),
    category_id: Optional[UUID] = None,
    owner_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    actor_id: Optional[UUID] = None,
    actor: Optional[str] = Query(None),
    exposure: Optional[str] = Query(None),
    mi_only: Optional[int] = Query(None, ge=0, le=1),
    milestones: Optional[int] = Query(None, ge=0, le=1),
    kinds: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=160),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """The printable "shift chronicle" — the filtered window as a warm-amber dossier
    (newest 400 events + a stats strip). WeasyPrint with the sitrep's GTK guard."""
    if actor and actor not in _TIMELINE_ACTORS:
        raise HTTPException(422, "actor must be 'human' or 'system'")
    if exposure and exposure not in _TIMELINE_EXPOSURES:
        raise HTTPException(422, f"exposure must be one of: {', '.join(_TIMELINE_EXPOSURES)}")
    query = _timeline_query(db, admin, date_from=date_from, date_to=date_to, sev=sev,
                            category_id=category_id, owner_id=owner_id, kinds=kinds, q=q,
                            team_id=team_id, actor_id=actor_id, actor=actor,
                            exposure=exposure, mi_only=mi_only, milestones=milestones)
    rows = query.order_by(SdTicketActivity.created_at.desc()).limit(400).all()
    events = [_timeline_event(a, t).model_dump(mode="json") for a, t in rows]
    by_cat: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    stones = 0
    for e in events:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
        by_sev[f"sev{e['sev']}"] = by_sev.get(f"sev{e['sev']}", 0) + 1
        stones += 1 if e["is_milestone"] else 0
    window = None
    if date_from or date_to:
        window = (f"{date_from.date().isoformat() if date_from else '…'} → "
                  f"{date_to.date().isoformat() if date_to else 'now'}")
    stats = {"total": len(events), "milestones": stones, "by_category": by_cat,
             "by_sev": by_sev, "window": window,
             "generated_at": sla_util.now_utc().strftime("%Y-%m-%d %H:%M")}
    filters = _export_filters(date_from=date_from, date_to=date_to, sev=sev,
                              category_id=category_id, owner_id=owner_id, team_id=team_id,
                              actor_id=actor_id, actor=actor, exposure=exposure,
                              mi_only=mi_only, milestones=milestones, kinds=kinds, q=q)
    try:
        pdf = render_chronicle_pdf(events, stats, filters)
    except OSError as exc:
        raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run vendor/setup_gtk.py "
                                 f"on this machine ({exc})")
    stamp = sla_util.now_utc().strftime("%Y%m%d-%H%M")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="incident-chronicle-{stamp}.pdf"'})


# ═══════════════════════ Timeline milestone pins ═══════════════════════
# Curated key beats on an incident's record. Owner-tier verb (assignee / collaborator /
# team lead / claim-eligible / live swarm / superuser — the same bar as every other
# incident write). Audit-ledger only: a pin never writes an activity row, so it can't
# spam the very feed it curates. No notification events either.

def _pin_target(db: Session, activity_id: UUID, admin: User):
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor
    a = db.query(SdTicketActivity).filter(SdTicketActivity.id == activity_id).first()
    if not a:
        raise HTTPException(404, "Activity not found")
    t = _get_ticket(db, a.ticket_id, admin)   # 404 outside scope / archived
    _require_incident(t)
    _require_ticket_actor(db, t, admin, "curate its timeline milestones")
    return a, t


def _pin_receipt(db: Session, a: SdTicketActivity) -> TimelinePinResponse:
    from app.routers.support_desk._common import _user_names
    names = _user_names(db, {a.pinned_by_id}) if a.pinned_by_id else {}
    return TimelinePinResponse(id=a.id, ticket_id=a.ticket_id, action=a.action,
                               is_milestone=bool(a.is_milestone), pinned_by_id=a.pinned_by_id,
                               pinned_by_name=names.get(str(a.pinned_by_id)),
                               pinned_at=a.pinned_at)


@router.post("/incidents/activities/{activity_id}/pin", response_model=TimelinePinResponse)
def pin_timeline_milestone(activity_id: UUID, request: Request,
                           db: Session = Depends(get_db),
                           admin: User = Depends(get_support_agent)):
    a, t = _pin_target(db, activity_id, admin)
    meta = timeline_meta(a.action)
    if not meta["milestone"]:
        raise HTTPException(422, f"'{a.action}' isn't milestone-eligible — pins mark the "
                                 "beats that tell the incident's story (see the catalog).")
    if a.is_milestone:
        raise HTTPException(409, "Already pinned.")
    pinned = (db.query(func.count(SdTicketActivity.id))
              .filter(SdTicketActivity.ticket_id == t.id,
                      SdTicketActivity.is_milestone == True).scalar() or 0)  # noqa: E712
    if pinned >= MILESTONES_PER_TICKET:
        raise HTTPException(409, f"Milestone cap reached ({MILESTONES_PER_TICKET} per "
                                 "incident) — unpin a lesser beat first.")
    a.is_milestone = True
    a.pinned_by_id = admin.id
    a.pinned_at = sla_util.now_utc()
    write_audit(db, entity_type="ticket", op="timeline_pinned", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"activity_id": str(a.id), "action": a.action,
                         "at": a.created_at.isoformat() if a.created_at else None})
    db.commit()
    db.refresh(a)
    return _pin_receipt(db, a)


@router.delete("/incidents/activities/{activity_id}/pin", response_model=TimelinePinResponse)
def unpin_timeline_milestone(activity_id: UUID, request: Request,
                             db: Session = Depends(get_db),
                             admin: User = Depends(get_support_agent)):
    a, t = _pin_target(db, activity_id, admin)
    if not a.is_milestone:
        raise HTTPException(409, "Not pinned.")
    a.is_milestone = False
    a.pinned_by_id = None
    a.pinned_at = None
    write_audit(db, entity_type="ticket", op="timeline_unpinned", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"activity_id": str(a.id), "action": a.action})
    db.commit()
    db.refresh(a)
    return _pin_receipt(db, a)


# ═══════════════════════════════ PIR lifecycle ═══════════════════════════════
# ═══════════════════════════════ PIR action-item tracker ═══════════════════════════════
@router.get("/incidents/actions", response_model=IncidentActionsResponse)
def list_incident_actions(
    status_f: Optional[str] = Query(None, alias="status", description="open|done"),
    overdue: Optional[bool] = Query(None),
    kind: Optional[str] = Query(None, description="corrective|preventive"),
    owner_id: Optional[UUID] = Query(None),
    pir_status: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=160),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Cross-incident follow-through board: every corrective/preventive action item,
    flattened out of the sealed PIR registers, overdue-first. Same seal as the PIR
    desk (scoped ticket join); shares iter_scoped_actions with stats + the overdue
    sweep so 'open'/'overdue' can never mean different things on different surfaces."""
    if status_f and status_f not in ("open", "in_progress", "done"):
        raise HTTPException(422, "status must be 'open', 'in_progress' or 'done'")
    if kind and kind not in ("corrective", "preventive"):
        raise HTTPException(422, "kind must be 'corrective' or 'preventive'")
    if pir_status and pir_status not in {s.value for s in PirStatus}:
        raise HTTPException(422, "Unknown PIR status")
    cond = _scope_cond(db, admin)
    today_iso = sla_util.now_utc().date().isoformat()
    needle = (q or "").strip().lower()

    counts = IncidentActionCounts()
    rows: list[IncidentActionRow] = []
    for p, t, k, idx, a in iter_scoped_actions(db, cond, limit=500):
        st = str(a.get("status") or "open").lower()
        over = action_is_overdue(a, today_iso)
        # counts describe the WHOLE sealed register, before user filters.
        # `open` = anything not done (in_progress included); `in_progress` is its subset.
        counts.open += 1 if st != "done" else 0
        counts.in_progress += 1 if st == "in_progress" else 0
        counts.done += 1 if st == "done" else 0
        counts.overdue += 1 if over else 0
        # status filter: 'open' means NOT done (open ∪ in_progress — the working set);
        # 'in_progress' and 'done' are exact.
        if status_f == "done" and st != "done":
            continue
        if status_f == "open" and st == "done":
            continue
        if status_f == "in_progress" and st != "in_progress":
            continue
        if overdue is not None and over != overdue:
            continue
        if kind and k != kind:
            continue
        if owner_id and str(a.get("owner_id") or "") != str(owner_id):
            continue
        if pir_status and p.status != pir_status:
            continue
        if needle and needle not in " ".join((
                str(a.get("action") or ""), p.report_number or "",
                t.ticket_number or "", t.subject or "")).lower():
            continue
        rows.append(IncidentActionRow(
            pir_id=p.id, report_number=p.report_number, pir_status=p.status,
            ticket_id=t.id, ticket_number=t.ticket_number, subject=t.subject,
            sev=ticket_sev(t.priority, bool(t.is_major_incident)),
            kind=k, index=idx, aid=a.get("aid"), action=str(a.get("action") or ""),
            owner_id=a.get("owner_id"), owner_name=a.get("owner_name"),
            target_date=(str(a.get("target_date"))[:10] if a.get("target_date") else None),
            status=st, overdue=over,
            status_changed_at=a.get("status_changed_at"),
            status_changed_by=a.get("status_changed_by"),
            status_note=a.get("status_note")))
    # overdue burns first, then nearest target date, dateless items last
    rows.sort(key=lambda r: (not r.overdue, r.status == "done",
                             r.target_date or "9999-12-31", r.report_number))
    total = len(rows)
    start = (page - 1) * limit
    return IncidentActionsResponse(total=total, page=page, limit=limit, counts=counts,
                                   items=rows[start:start + limit])


@router.get("/incidents/pirs", response_model=PirListResponse)
def list_pirs(
    status_f: Optional[str] = Query(None, alias="status"),
    q: Optional[str] = Query(None, max_length=160),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Sealed PIR desk: a non-superuser sees only reports whose incident sits inside
    their team scope (audit-reader pattern — subquery of scoped ticket ids)."""
    # Opportunistic PIR-debt sweep on desk load (24h-throttled internally; the cron
    # remains the backstop). Never let a sweep hiccup 500 the list itself.
    try:
        sweep_pir_missing(db)
    except Exception:
        db.rollback()
    query = (db.query(SdIncidentReport, SdTicket)
             .join(SdTicket, SdIncidentReport.ticket_id == SdTicket.id)
             .filter(SdIncidentReport.is_deleted == False))  # noqa: E712
    cond = _scope_cond(db, admin)
    if cond is not None:
        query = query.filter(cond)
    if status_f:
        if status_f not in {s.value for s in PirStatus}:
            raise HTTPException(422, "Unknown PIR status")
        query = query.filter(SdIncidentReport.status == status_f)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdIncidentReport.title.ilike(like),
                                 SdIncidentReport.report_number.ilike(like),
                                 SdTicket.ticket_number.ilike(like),
                                 SdTicket.subject.ilike(like)))
    total = query.count()
    rows = (query.order_by(SdIncidentReport.updated_at.desc())
            .offset((page - 1) * limit).limit(limit).all())
    from app.routers.support_desk._common import _user_names
    names = _user_names(db, {p.created_by_id for p, _t in rows})
    items = []
    for p, t in rows:
        item = PirListItem.model_validate(p)
        item.ticket_number = t.ticket_number
        item.subject = t.subject
        item.sev = ticket_sev(t.priority, bool(t.is_major_incident))
        item.created_by_name = names.get(str(p.created_by_id))
        items.append(item)
    return PirListResponse(total=total, items=items)


_PIR_LENSES = ("owed", "drafting", "in_review", "approved", "published", "actions_due", "all")
_PIR_SORTS = ("updated", "submitted", "created", "sev", "age")


@router.get("/incidents/pirs/board", response_model=PirBoardResponse)
def pir_board(
    lens: str = Query("all"),
    q: Optional[str] = Query(None, max_length=160),
    sev: Optional[int] = Query(None, ge=1, le=4),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    sort: str = Query("updated"),
    sort_dir: str = Query("desc"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """THE sealed PIR desk board — ONE response carries rows + lockstep stats so a
    chip's number always equals its click's rows (rca/board discipline; kills the
    old 4-parallel-count client pattern). The ``owed`` lens is TICKETS still owing a
    review (pir_owed_cond single truth — server-side, no more client-side folds
    capped at one page); every other lens is PIR documents.
    Literal route — registered BEFORE /incidents/pirs/{pir_id}."""
    if lens not in _PIR_LENSES:
        raise HTTPException(422, f"Unknown lens '{lens}'. Valid: {', '.join(_PIR_LENSES)}")
    if sort not in _PIR_SORTS:
        raise HTTPException(422, f"Unknown sort '{sort}'. Valid: {', '.join(_PIR_SORTS)}")
    desc = (sort_dir or "desc").lower() != "asc"
    cond = _scope_cond(db, admin)
    nowt = sla_util.now_utc()
    today_iso = nowt.date().isoformat()
    # Opportunistic debt sweep on desk load (24h-throttled internally) — never let a
    # sweep hiccup 500 the board.
    try:
        sweep_pir_missing(db)
    except Exception:
        db.rollback()
    sev_f = sev_cond(sev) if sev else None

    def _tseal(query):
        query = _sealed(query, cond)
        return query.filter(sev_f) if sev_f is not None else query

    # ── lockstep stats (honor sev, not q — the chips describe the sealed program) ──
    stats = PirBoardStats()
    stats.owed = _tseal(db.query(func.count(SdTicket.id))
                        .filter(pir_owed_cond(nowt))).scalar() or 0
    for st, n in _tseal(db.query(SdIncidentReport.status, func.count(SdIncidentReport.id))
                        .join(SdTicket, SdTicket.id == SdIncidentReport.ticket_id)
                        .filter(SdIncidentReport.is_deleted == False)  # noqa: E712
                        ).group_by(SdIncidentReport.status).all():
        if hasattr(stats, st or ""):
            setattr(stats, st, int(n or 0))
    # follow-through fold (bounded; same iterator as /incidents/actions + the sweep)
    overdue_pids: set = set()
    for p_, t_, _k, _i, a_ in iter_scoped_actions(db, cond, limit=500):
        if sev and ticket_sev(t_.priority, bool(t_.is_major_incident)) != sev:
            continue
        if p_.status not in (PirStatus.APPROVED.value, PirStatus.PUBLISHED.value):
            continue
        if str(a_.get("status") or "open").lower() != "done":
            stats.actions_open += 1
        if action_is_overdue(a_, today_iso):
            stats.actions_overdue += 1
            overdue_pids.add(p_.id)
    stats.actions_due = len(overdue_pids)
    # coverage over the 90d eligible window: reviewed closures / eligible closures
    eligible = _tseal(db.query(func.count(SdTicket.id))
                      .filter(pir_eligible_cond(nowt))).scalar() or 0
    if eligible:
        stats.coverage_pct = round((eligible - stats.owed) * 100.0 / eligible, 1)
    # review latency (30d median, submitted → approved)
    d30 = nowt - timedelta(days=30)
    pairs = _tseal(db.query(SdIncidentReport.submitted_at, SdIncidentReport.approved_at)
                   .join(SdTicket, SdTicket.id == SdIncidentReport.ticket_id)
                   .filter(SdIncidentReport.is_deleted == False,  # noqa: E712
                           SdIncidentReport.approved_at.isnot(None),
                           SdIncidentReport.approved_at >= d30)).limit(500).all()
    hrs = sorted(max(0.0, (ap - su).total_seconds() / 3600.0)
                 for su, ap in pairs if su and ap)
    if hrs:
        mid = len(hrs) // 2
        med = hrs[mid] if len(hrs) % 2 else (hrs[mid - 1] + hrs[mid]) / 2.0
        stats.median_review_hours_30d = round(med, 1)
    stats.published_30d = _tseal(db.query(func.count(SdIncidentReport.id))
                                 .join(SdTicket, SdTicket.id == SdIncidentReport.ticket_id)
                                 .filter(SdIncidentReport.is_deleted == False,  # noqa: E712
                                         SdIncidentReport.published_at.isnot(None),
                                         SdIncidentReport.published_at >= d30)).scalar() or 0
    stats.meetings_upcoming = _tseal(db.query(func.count(SdIncidentReport.id))
                                     .join(SdTicket, SdTicket.id == SdIncidentReport.ticket_id)
                                     .filter(SdIncidentReport.is_deleted == False,  # noqa: E712
                                             SdIncidentReport.review_meeting_at.isnot(None),
                                             SdIncidentReport.review_meeting_at >= nowt)
                                     ).scalar() or 0

    from app.routers.support_desk._common import _user_names
    terminal_stamp = func.coalesce(SdTicket.resolved_at, SdTicket.closed_at,
                                   SdTicket.created_at)
    items: list[PirBoardItem] = []

    if lens == "owed":
        # ── debt rows: TICKETS without a report ──
        tq = _tseal(db.query(SdTicket).filter(pir_owed_cond(nowt)))
        if q:
            like = f"%{q.strip()}%"
            tq = tq.filter(or_(SdTicket.ticket_number.ilike(like),
                               SdTicket.subject.ilike(like)))
        total = tq.count()
        if sort == "sev":
            order = [_rca_sev_order().desc() if desc else _rca_sev_order().asc()]
        else:  # updated/created/submitted/age all read as debt age here
            order = [terminal_stamp.desc() if not desc else terminal_stamp.asc()]
        rows = tq.order_by(*order).offset((page - 1) * limit).limit(limit).all()
        names = _user_names(db, {x for t in rows
                                 for x in (t.assigned_agent_id, t.incident_commander_id)})
        team_ids = {t.team_id for t in rows if t.team_id}
        teams = {str(r.id): r.name for r in db.query(SdTeam.id, SdTeam.name)
                 .filter(SdTeam.id.in_(team_ids)).all()} if team_ids else {}
        for t in rows:
            stamp = t.resolved_at or t.closed_at or t.created_at
            age = max(0, int((nowt - sla_util._aware(stamp)).total_seconds() // 86400)) \
                if stamp else None
            items.append(PirBoardItem(
                kind="owed", ticket_id=t.id, ticket_number=t.ticket_number,
                subject=t.subject, sev=ticket_sev(t.priority, bool(t.is_major_incident)),
                is_major_incident=bool(t.is_major_incident), ticket_status=t.status,
                team_id=t.team_id, team_name=teams.get(str(t.team_id)),
                assigned_agent_id=t.assigned_agent_id,
                assigned_agent_name=names.get(str(t.assigned_agent_id)),
                incident_commander_id=t.incident_commander_id,
                incident_commander_name=names.get(str(t.incident_commander_id)),
                collaborators=list(t.collaborators or []),
                terminal_at=stamp, age_days=age))
    else:
        # ── document rows: PIRs joined to their sealed tickets ──
        pq = (db.query(SdIncidentReport, SdTicket)
              .join(SdTicket, SdTicket.id == SdIncidentReport.ticket_id)
              .filter(SdIncidentReport.is_deleted == False))  # noqa: E712
        if cond is not None:
            pq = pq.filter(cond)
        if sev_f is not None:
            pq = pq.filter(sev_f)
        if lens == "drafting":
            pq = pq.filter(SdIncidentReport.status == PirStatus.DRAFT.value)
        elif lens in ("in_review", "approved", "published"):
            pq = pq.filter(SdIncidentReport.status == lens)
        elif lens == "actions_due":
            pq = pq.filter(SdIncidentReport.id.in_(list(overdue_pids) or [uuid_mod.uuid4()]))
        if q:
            like = f"%{q.strip()}%"
            pq = pq.filter(or_(SdIncidentReport.title.ilike(like),
                               SdIncidentReport.report_number.ilike(like),
                               SdTicket.ticket_number.ilike(like),
                               SdTicket.subject.ilike(like)))
        total = pq.count()
        order_col = {"updated": SdIncidentReport.updated_at,
                     "submitted": SdIncidentReport.submitted_at,
                     "created": SdIncidentReport.created_at,
                     "age": terminal_stamp}.get(sort)
        if sort == "sev":
            order = [_rca_sev_order().desc() if desc else _rca_sev_order().asc()]
        else:
            order = [order_col.desc().nullslast() if desc else order_col.asc().nullsfirst()]
        rows = pq.order_by(*order).offset((page - 1) * limit).limit(limit).all()
        names = _user_names(db, {x for p, t in rows
                                 for x in (p.created_by_id, p.submitted_by_id,
                                           t.assigned_agent_id, t.incident_commander_id)})
        team_ids = {t.team_id for _p, t in rows if t.team_id}
        teams = {str(r.id): r.name for r in db.query(SdTeam.id, SdTeam.name)
                 .filter(SdTeam.id.in_(team_ids)).all()} if team_ids else {}
        for p, t in rows:
            a_total = a_done = a_over = 0
            for _kind, reg in (("corrective", p.corrective_actions or []),
                               ("preventive", p.preventive_actions or [])):
                for a in reg:
                    if not (isinstance(a, dict) and str(a.get("action") or "").strip()):
                        continue
                    a_total += 1
                    if str(a.get("status") or "open").lower() == "done":
                        a_done += 1
                    elif action_is_overdue(a, today_iso):
                        a_over += 1
            stamp = t.resolved_at or t.closed_at or t.created_at
            items.append(PirBoardItem(
                kind="pir", pir_id=p.id, report_number=p.report_number, title=p.title,
                status=p.status, submitted_at=p.submitted_at,
                submitted_by_id=p.submitted_by_id,
                submitted_by_name=names.get(str(p.submitted_by_id)),
                approved_at=p.approved_at, published_at=p.published_at,
                review_meeting_at=p.review_meeting_at,
                created_by_id=p.created_by_id,
                created_by_name=names.get(str(p.created_by_id)),
                updated_at=p.updated_at,
                actions_total=a_total, actions_done=a_done, actions_overdue=a_over,
                has_metrics=bool(p.metrics_snapshot),
                ticket_id=t.id, ticket_number=t.ticket_number, subject=t.subject,
                sev=ticket_sev(t.priority, bool(t.is_major_incident)),
                is_major_incident=bool(t.is_major_incident), ticket_status=t.status,
                team_id=t.team_id, team_name=teams.get(str(t.team_id)),
                assigned_agent_id=t.assigned_agent_id,
                assigned_agent_name=names.get(str(t.assigned_agent_id)),
                incident_commander_id=t.incident_commander_id,
                incident_commander_name=names.get(str(t.incident_commander_id)),
                collaborators=list(t.collaborators or []),
                terminal_at=stamp))

    return PirBoardResponse(total=total, page=page, limit=limit, lens=lens,
                            stats=stats, items=items, generated_at=nowt)


@router.get("/incidents/pirs/{pir_id}", response_model=PirResponse)
def get_pir(pir_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    p = _get_pir(db, pir_id)
    _require_pir_scope(db, p, admin)
    return _enrich_pir(db, p, PirResponse.model_validate(p))


@router.patch("/incidents/pirs/{pir_id}", response_model=PirResponse)
def update_pir(pir_id: UUID, payload: PirUpdate, request: Request,
               db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Section edits — draft/in_review only. After approval the document is sealed;
    a changed conclusion means a REJECT back to draft, never a silent edit."""
    p = _get_pir(db, pir_id)
    _require_pir_scope(db, p, admin)
    if p.status not in (PirStatus.DRAFT.value, PirStatus.IN_REVIEW.value):
        raise HTTPException(409, "This report is approved/published — it can no longer be edited. "
                                 "Ask an admin to reject it back to draft first.")
    t = _pir_ticket(db, p)
    if t is not None:
        from app.routers.support_desk.tickets import _require_ticket_actor
        _require_ticket_actor(db, t, admin, "edit its post-incident report")
    data = payload.model_dump(exclude_unset=True)
    refresh = data.pop("refresh_timeline", False)
    refresh_metrics = data.pop("refresh_metrics", False)
    for k in ("corrective_actions", "preventive_actions"):
        if k in data and data[k] is not None:
            data[k] = _ensure_action_aids(_json_safe(data[k]))
    if "participants" in data and data["participants"] is not None:
        data["participants"] = _json_safe(data["participants"])
    meeting_touched = ("review_meeting_at" in data
                       and data["review_meeting_at"] != p.review_meeting_at)
    # review-meeting fields are CLEARABLE (explicit null unsets); everything else keeps
    # the ignore-None contract existing autosave callers rely on.
    clearable = {"review_meeting_at", "review_meeting_notes"}
    for k, v in data.items():
        if v is not None or k in clearable:
            setattr(p, k, v)
    if refresh:
        p.timeline_snapshot = snapshot_timeline(db, p.ticket_id)
    if refresh_metrics and t is not None:
        p.metrics_snapshot = pir_metrics_snapshot(db, t)
    nowt = sla_util.now_utc()
    # append-only revision trail (cap 50, oldest drop) — the document's edit history
    rev = {"at": nowt.isoformat(), "by_id": str(admin.id),
           "by_name": getattr(admin, "full_name", None) or getattr(admin, "email", None) or "Agent",
           "fields": sorted(data.keys())}
    p.revisions = (list(p.revisions or []) + [rev])[-50:]
    if t is not None:
        from app.routers.support_desk.tickets import _log_activity
        _log_activity(db, t, admin, "pir_updated", {"pir": p.report_number,
                                                    "fields": sorted(data.keys())})
        if meeting_touched:
            _log_activity(db, t, admin, "pir_meeting_set",
                          {"pir": p.report_number,
                           "at": (p.review_meeting_at.isoformat()
                                  if p.review_meeting_at else None),
                           "cleared": p.review_meeting_at is None})
    write_audit(db, entity_type="pir", op="updated", entity_id=p.id, actor_id=admin.id,
                request=request, details={"report": p.report_number, "fields": sorted(data.keys())})
    db.commit()
    db.refresh(p)
    return _enrich_pir(db, p, PirResponse.model_validate(p))


@router.post("/incidents/pirs/{pir_id}/submit", response_model=PirResponse)
def submit_pir(pir_id: UUID, request: Request,
               db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """draft → in_review. The drop-gate: no review without the substance that makes a
    PIR worth reviewing (exec summary + root cause + ≥1 corrective action)."""
    p = _get_pir(db, pir_id)
    _require_pir_scope(db, p, admin)
    if p.status != PirStatus.DRAFT.value:
        raise HTTPException(409, f"Only a draft can be submitted (this report is {p.status}).")
    missing = []
    if not (p.executive_summary or "").strip():
        missing.append("executive summary")
    if not (p.root_cause or "").strip():
        missing.append("root cause")
    if not (p.corrective_actions or []):
        missing.append("at least one corrective action")
    if missing:
        raise HTTPException(422, "Cannot submit for review — missing: " + ", ".join(missing) + ".")
    t = _pir_ticket(db, p)
    if t is not None:
        from app.routers.support_desk.tickets import _require_ticket_actor
        _require_ticket_actor(db, t, admin, "submit its post-incident report")
    p.status = PirStatus.IN_REVIEW.value
    p.submitted_at = sla_util.now_utc()
    p.submitted_by_id = admin.id
    if t is not None:
        # Freeze the metrics record at the review threshold — the document under
        # review (and the eventually-published record) never drifts against live
        # recomputation. Draft-era refreshes go through PATCH refresh_metrics.
        p.metrics_snapshot = pir_metrics_snapshot(db, t)
        from app.routers.support_desk.tickets import _log_activity, dispatch_safe
        _log_activity(db, t, admin, "pir_submitted", {"pir": p.report_number})
        _notify_superusers(db, EVT_PIR_SUBMITTED, t,
                           title=f"PIR awaiting review — {p.report_number}: {p.title}",
                           action_url="/admin/support-desk/incidents/post-incident")
        # Team leads review too (lead ∪ superuser sign-off) — ping them on their panel.
        try:
            for uid in _team_lead_ids(db, t.team_id) - {str(admin.id)}:
                dispatch_safe(db, EVT_PIR_SUBMITTED, uid, t,
                              title=f"PIR awaiting review — {p.report_number}: {p.title}",
                              action_url="/user/support/incidents/post-incident?lens=in_review")
        except Exception:
            pass
    write_audit(db, entity_type="pir", op="submitted", entity_id=p.id, actor_id=admin.id,
                request=request, details={"report": p.report_number})
    db.commit()
    db.refresh(p)
    return _enrich_pir(db, p, PirResponse.model_validate(p))


def _stamp_approval(p: SdIncidentReport, admin: User, decision: str, note: str | None):
    entry = {"role": "admin" if getattr(admin, "is_superuser", False) else "lead",
             "user_id": str(admin.id),
             "name": getattr(admin, "full_name", None) or "Reviewer",
             "decision": decision, "note": (note or "").strip() or None,
             "at": sla_util.now_utc().isoformat()}
    p.approvals = list(p.approvals or []) + [entry]


@router.post("/incidents/pirs/{pir_id}/approve", response_model=PirResponse)
def approve_pir(pir_id: UUID, request: Request, payload: PirReview | None = None,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Reviewer sign-off: team lead ∪ superuser, four-eyes (never your own submission —
    superuser exempt). One-shot: only an in_review report can be approved."""
    p = _get_pir(db, pir_id)
    _require_pir_scope(db, p, admin)
    if p.status != PirStatus.IN_REVIEW.value:
        raise HTTPException(409, f"Only a report in review can be approved (this one is {p.status}).")
    t = _require_pir_reviewer(db, p, admin)
    p.status = PirStatus.APPROVED.value
    p.approved_at = sla_util.now_utc()
    p.approved_by_id = admin.id
    _stamp_approval(p, admin, "approved", payload.note if payload else None)
    from app.routers.support_desk.tickets import _log_activity, dispatch_safe
    _log_activity(db, t, admin, "pir_approved", {"pir": p.report_number})
    if p.created_by_id and str(p.created_by_id) != str(admin.id):
        dispatch_safe(db, EVT_PIR_APPROVED, p.created_by_id, t,
                      title=f"PIR approved — {p.report_number}: {p.title}",
                      action_url="/user/support/incidents/post-incident")
    write_audit(db, entity_type="pir", op="approved", entity_id=p.id, actor_id=admin.id,
                request=request, details={"report": p.report_number})
    db.commit()
    db.refresh(p)
    return _enrich_pir(db, p, PirResponse.model_validate(p))


@router.post("/incidents/pirs/{pir_id}/reject", response_model=PirResponse)
def reject_pir(pir_id: UUID, payload: PirReview, request: Request,
               db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Reviewer reject (team lead ∪ superuser, four-eyes): in_review → draft. The note
    is REQUIRED — a rejection without a reason gives the author nothing to fix."""
    p = _get_pir(db, pir_id)
    _require_pir_scope(db, p, admin)
    if p.status != PirStatus.IN_REVIEW.value:
        raise HTTPException(409, f"Only a report in review can be rejected (this one is {p.status}).")
    if not (payload.note or "").strip():
        raise HTTPException(422, "A rejection note is required — tell the author what to fix.")
    t = _require_pir_reviewer(db, p, admin)
    author_id = p.submitted_by_id or p.created_by_id
    p.status = PirStatus.DRAFT.value
    p.submitted_at = None
    p.submitted_by_id = None
    _stamp_approval(p, admin, "rejected", payload.note)
    from app.routers.support_desk.tickets import _log_activity, dispatch_safe
    _log_activity(db, t, admin, "pir_rejected", {"pir": p.report_number,
                                                 "note": payload.note.strip()[:300]})
    if author_id and str(author_id) != str(admin.id):
        dispatch_safe(db, EVT_PIR_REJECTED, author_id, t,
                      title=f"PIR returned to draft — {p.report_number}: {p.title}",
                      action_url="/user/support/incidents/post-incident")
    write_audit(db, entity_type="pir", op="rejected", entity_id=p.id, actor_id=admin.id,
                request=request, details={"report": p.report_number, "note": payload.note.strip()[:500]})
    db.commit()
    db.refresh(p)
    return _enrich_pir(db, p, PirResponse.model_validate(p))


@router.post("/incidents/pirs/{pir_id}/publish", response_model=PirResponse)
def publish_pir(pir_id: UUID, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Superuser publish: approved → published (the org-visible record of record).
    Publishing DISTRIBUTES: watchers + response roster + team leads are notified and
    the fan-out receipt is stamped onto the document (ServiceNow comms parity)."""
    p = _get_pir(db, pir_id)
    if p.status != PirStatus.APPROVED.value:
        raise HTTPException(409, f"Only an approved report can be published (this one is {p.status}).")
    nowt = sla_util.now_utc()
    p.status = PirStatus.PUBLISHED.value
    p.published_at = nowt
    p.published_by_id = admin.id
    t = _pir_ticket(db, p)
    recipients: set[str] = set()
    watcher_n = roster_n = leads_n = 0
    if t is not None:
        from app.routers.support_desk.tickets import _log_activity, dispatch_safe, _panel_base
        # ── stakeholder distribution: watchers ∪ roster ∪ team leads ──
        try:
            from app.models.support_desk.collab import SdTicketWatcher
            watcher_ids = {str(r[0]) for r in db.query(SdTicketWatcher.user_id)
                           .filter(SdTicketWatcher.ticket_id == t.id).all()}
        except Exception:
            watcher_ids = set()
        roster_ids = {str(x) for x in (t.incident_commander_id, t.comms_lead_id,
                                       t.ops_lead_id, t.assigned_agent_id) if x}
        lead_ids = {str(x) for x in _team_lead_ids(db, t.team_id)}
        watcher_n, roster_n, leads_n = len(watcher_ids), len(roster_ids), len(lead_ids)
        recipients = (watcher_ids | roster_ids | lead_ids) \
            - {str(admin.id), str(p.created_by_id or "")}
        for uid in recipients:
            dispatch_safe(db, EVT_PIR_PUBLISHED, uid, t,
                          title=f"PIR published — {p.report_number}: {p.title}",
                          action_url=f"{_panel_base(db, uid)}/incidents/post-incident")
        if p.created_by_id and str(p.created_by_id) != str(admin.id):
            dispatch_safe(db, EVT_PIR_PUBLISHED, p.created_by_id, t,
                          title=f"PIR published — {p.report_number}: {p.title}",
                          action_url="/user/support/incidents/post-incident")
            recipients.add(str(p.created_by_id))
        p.distribution = {"at": nowt.isoformat(), "recipients": len(recipients),
                          "watchers": watcher_n, "roster": roster_n, "leads": leads_n}
        _log_activity(db, t, admin, "pir_published",
                      {"pir": p.report_number, "recipients": len(recipients)})
    write_audit(db, entity_type="pir", op="published", entity_id=p.id, actor_id=admin.id,
                request=request, details={"report": p.report_number,
                                          "recipients": len(recipients)})
    db.commit()
    db.refresh(p)
    return _enrich_pir(db, p, PirResponse.model_validate(p))


@router.get("/incidents/pirs/{pir_id}/export.pdf")
def export_pir_pdf(pir_id: UUID, db: Session = Depends(get_db),
                   admin: User = Depends(get_support_agent)):
    """The dossier PDF. WeasyPrint is imported lazily inside the renderer (GTK
    bootstrap first) — a missing GTK runtime surfaces as a clear 503, never a boot crash."""
    p = _get_pir(db, pir_id)
    _require_pir_scope(db, p, admin)
    t = _pir_ticket(db, p)
    from app.routers.support_desk._common import _user_names
    names_raw = _user_names(db, {getattr(t, "incident_commander_id", None)})
    names = {"commander": names_raw.get(str(getattr(t, "incident_commander_id", None)))}
    try:
        pdf = render_pir_pdf(p, t, names)
    except OSError as exc:
        raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run vendor\\setup_gtk.py "
                                 f"on the backend host and retry. ({exc})")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="{p.report_number}.pdf"'})


@router.patch("/incidents/pirs/{pir_id}/actions/{kind}/{index}", response_model=IncidentActionRow)
def patch_pir_action_status(pir_id: UUID, kind: str, index: int, payload: PirActionStatusPatch,
                            request: Request, db: Session = Depends(get_db),
                            admin: User = Depends(get_support_agent)):
    """The status-only carve-out on the PIR seal: once a report is approved/published
    the DOCUMENT stays sealed, but follow-through must go on — each action item's
    ``status`` (+ additive audit keys) may still move. While the report is draft/
    in-review, actions are edited through the normal PIR PATCH instead (409 here).
    Actor: superuser, owner-tier on the linked ticket, or the action's named owner."""
    from sqlalchemy.orm.attributes import flag_modified
    from app.routers.support_desk.tickets import _require_ticket_actor, _log_activity, dispatch_safe
    if kind not in ("corrective", "preventive"):
        raise HTTPException(422, "kind must be 'corrective' or 'preventive'")
    p = _get_pir(db, pir_id)
    _require_pir_scope(db, p, admin)
    if p.status not in (PirStatus.APPROVED.value, PirStatus.PUBLISHED.value):
        raise HTTPException(409, "Track action status after approval — while the report is "
                                 "draft/in review, edit the actions via the PIR editor instead.")
    field = f"{kind}_actions"
    register = list(getattr(p, field) or [])
    # ── stable-aid addressing: when the caller sends the item's aid, it is the
    # authoritative coordinate — a draft-era reorder can shift positional indices,
    # and silently patching the wrong item is exactly the loophole this closes. ──
    if payload.aid:
        hits = [i for i, row in enumerate(register)
                if str((row or {}).get("aid") or "") == payload.aid]
        if not hits:
            raise HTTPException(404, "Action item not found (stale address — refresh the board)")
        index = hits[0]
    if index < 0 or index >= len(register):
        raise HTTPException(404, "Action item not found")
    a = dict(register[index] or {})
    t = _pir_ticket(db, p)
    if t is None:
        raise HTTPException(404, "Post-incident report not found")
    # named owner may close their own item even off-roster; everyone else needs
    # owner-tier on the linked ticket (superuser passes inside the actor gate).
    if str(a.get("owner_id") or "") != str(admin.id) and not getattr(admin, "is_superuser", False):
        _require_ticket_actor(db, t, admin, "update its post-incident action items")
    old = str(a.get("status") or "open").lower()
    if old == payload.status:
        raise HTTPException(422, f"This action is already {payload.status}.")
    nowt = sla_util.now_utc()
    a["status"] = payload.status
    a["status_changed_at"] = nowt.isoformat()
    a["status_changed_by"] = getattr(admin, "name", None) or getattr(admin, "email", "")
    if payload.note:
        a["status_note"] = payload.note.strip()
    register[index] = a
    setattr(p, field, register)
    flag_modified(p, field)   # JSONB in-place mutation — SQLAlchemy needs the nudge
    _log_activity(db, t, admin, "pir_action_status",
                  {"pir": p.report_number, "kind": kind, "index": index,
                   "action": str(a.get("action") or "")[:120],
                   "from": old, "to": payload.status,
                   **({"note": payload.note.strip()} if payload.note else {})})
    write_audit(db, entity_type="pir", op="action_status", entity_id=p.id, actor_id=admin.id,
                request=request, details={"kind": kind, "index": index,
                                          "from": old, "to": payload.status})
    if p.created_by_id and str(p.created_by_id) != str(admin.id):
        dispatch_safe(db, EVT_PIR_ACTION_UPDATED, p.created_by_id, t,
                      title=f"Action item marked {payload.status} on {p.report_number}",
                      action_url="/user/support/incidents/post-incident")
    db.commit()
    today_iso = nowt.date().isoformat()
    return IncidentActionRow(
        pir_id=p.id, report_number=p.report_number, pir_status=p.status,
        ticket_id=t.id, ticket_number=t.ticket_number, subject=t.subject,
        sev=ticket_sev(t.priority, bool(t.is_major_incident)),
        kind=kind, index=index, aid=a.get("aid"), action=str(a.get("action") or ""),
        owner_id=a.get("owner_id"), owner_name=a.get("owner_name"),
        target_date=(str(a.get("target_date"))[:10] if a.get("target_date") else None),
        status=payload.status, overdue=action_is_overdue(a, today_iso),
        status_changed_at=a.get("status_changed_at"),
        status_changed_by=a.get("status_changed_by"),
        status_note=a.get("status_note"))


@router.post("/incidents/{ticket_id}/pir-nudge")
def nudge_pir_review(ticket_id: UUID, request: Request, payload: dict | None = None,
                     db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Manually chase the review a CLOSED incident owes — the human counterpart to the
    sweep_pir_missing cron. Unlike /tickets/{id}/nudge-owner (which 409s terminal tickets
    and so can NEVER fire on a closed incident), this is BUILT for terminal incidents: it
    pings the commander ∪ owner ∪ team lead(s) to file the post-incident review. Shares
    the 'pir_overdue' 24h throttle with the auto-sweep, so the two never double-ping — a
    throttled call is a benign 200 no-op ({status:'throttled'}), never an error."""
    from app.routers.support_desk._common import _notify_safe
    from app.routers.support_desk.tickets import _get_ticket, _log_activity
    from app.models.support_desk.constants import EVT_PIR_OVERDUE
    t = _get_ticket(db, ticket_id, admin)                          # 404 + team seal
    if not (t.ticket_type == TicketType.INCIDENT.value or t.is_major_incident):
        raise HTTPException(422, "Only incidents owe a post-incident review — this ticket is a "
                                 f"{t.ticket_type} and not a major incident.")
    if t.status not in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This incident is still live — a review is only owed once it is "
                                 "resolved or closed.")
    existing = (db.query(SdIncidentReport.id)
                .filter(SdIncidentReport.ticket_id == t.id,
                        SdIncidentReport.is_deleted == False).first())   # noqa: E712
    if existing:
        raise HTTPException(409, "A post-incident report already exists for this incident — "
                                 "chase it in review, not here.")
    recipients = {str(x) for x in (t.incident_commander_id, t.assigned_agent_id) if x}
    recipients |= {str(x) for x in _team_lead_ids(db, t.team_id)}
    recipients.discard(str(admin.id))
    if not recipients:
        raise HTTPException(409, "No commander, owner, or team lead to nudge — assign one first.")
    nowt = sla_util.now_utc()
    recent = (db.query(SdTicketActivity.id)
              .filter(SdTicketActivity.ticket_id == t.id,
                      SdTicketActivity.action == "pir_overdue",
                      SdTicketActivity.created_at > nowt - timedelta(days=1)).first())
    if recent:
        return {"status": "throttled", "recipients": 0,
                "detail": "This incident was already nudged for its review in the last 24 hours."}
    _log_activity(db, t, admin, "pir_overdue", {"manual": True, "recipients": len(recipients)})
    for uid in recipients:
        _notify_safe(db, EVT_PIR_OVERDUE, uid, t,
                     title=f"Post-incident review owed — {t.ticket_number} closed without a PIR",
                     action_url="/user/support/incidents/post-incident")
    write_audit(db, entity_type="ticket", op="pir_review_nudged", entity_id=t.id,
                actor_id=admin.id, request=request, details={"recipients": len(recipients)})
    db.commit()
    return {"status": "sent", "recipients": len(recipients)}


@router.post("/incidents/pirs/{pir_id}/actions/{kind}/{index}/remind")
def remind_pir_action(pir_id: UUID, kind: str, index: int, request: Request,
                      payload: dict | None = None, db: Session = Depends(get_db),
                      admin: User = Depends(get_support_agent)):
    """Chase ONE open PIR action item — pings the action's OWNER (else commander, else
    assignee), not blindly the incident's agent. Manual counterpart to
    sweep_pir_actions_overdue; the incident is terminal by definition, so the generic
    nudge-owner endpoint can't do this. Stable-aid addressing (a draft reorder can shift
    indices); 24h-throttled per incident on the shared 'pir_action_overdue' marker.
    Throttle = benign 200 no-op."""
    from app.routers.support_desk._common import _notify_safe
    from app.routers.support_desk.tickets import _log_activity
    from app.models.support_desk.constants import EVT_PIR_ACTION_OVERDUE
    if kind not in ("corrective", "preventive"):
        raise HTTPException(422, "kind must be 'corrective' or 'preventive'")
    p = _get_pir(db, pir_id)
    _require_pir_scope(db, p, admin)
    if p.status not in (PirStatus.APPROVED.value, PirStatus.PUBLISHED.value):
        raise HTTPException(409, "Action reminders start after the report is approved — while it "
                                 "is draft/in review the register is still being written.")
    register = list(getattr(p, f"{kind}_actions") or [])
    aid = (payload or {}).get("aid")
    if aid:
        hits = [i for i, row in enumerate(register) if str((row or {}).get("aid") or "") == str(aid)]
        if not hits:
            raise HTTPException(404, "Action item not found (stale address — refresh the board)")
        index = hits[0]
    if index < 0 or index >= len(register):
        raise HTTPException(404, "Action item not found")
    a = dict(register[index] or {})
    if str(a.get("status") or "open").lower() == "done":
        raise HTTPException(409, "This action is already done — nothing to remind.")
    t = _pir_ticket(db, p)
    if t is None:
        raise HTTPException(404, "Post-incident report not found")
    recipient = a.get("owner_id") or t.incident_commander_id or t.assigned_agent_id
    if not recipient:
        raise HTTPException(409, "This action has no owner to remind — assign an owner first.")
    if str(recipient) == str(admin.id):
        raise HTTPException(409, "You own this action — the reminder would go to you.")
    nowt = sla_util.now_utc()
    recent = (db.query(SdTicketActivity.id)
              .filter(SdTicketActivity.ticket_id == t.id,
                      SdTicketActivity.action == "pir_action_overdue",
                      SdTicketActivity.created_at > nowt - timedelta(days=1)).first())
    if recent:
        return {"status": "throttled",
                "detail": "This incident's actions were already chased in the last 24 hours."}
    _log_activity(db, t, admin, "pir_action_overdue",
                  {"manual": True, "pir": p.report_number, "kind": kind, "index": index,
                   "action": str(a.get("action") or "")[:120]})
    _notify_safe(db, EVT_PIR_ACTION_OVERDUE, recipient, t,
                 title=f"Post-incident action reminder — {p.report_number}: "
                       f"{str(a.get('action') or '')[:80]}",
                 action_url="/user/support/incidents/post-incident")
    write_audit(db, entity_type="pir", op="action_reminded", entity_id=p.id, actor_id=admin.id,
                request=request, details={"kind": kind, "index": index})
    db.commit()
    return {"status": "sent", "recipient": str(recipient)}


# ═══════════════════════════════ Response playbooks ═══════════════════════════════
@router.get("/incidents/playbooks")
def list_incident_playbooks(admin: User = Depends(get_support_agent)):
    """The curated response-playbook library (static — INCIDENT_PLAYBOOKS). Applying one
    SNAPSHOTS its tasks onto the ticket (template_key provenance), so later edits to the
    library never rewrite history. Literal route — registered BEFORE /incidents/{id}/*."""
    return [{"key": k, "label": p["label"], "description": p.get("description"),
             "task_count": len(p["tasks"]), "tasks": list(p["tasks"])}
            for k, p in INCIDENT_PLAYBOOKS.items()]


# ═══════════════════════════════ RCA desks (RCA v2) ═══════════════════════════════
# Literal /incidents/rca/* routes — registered BEFORE every /incidents/{ticket_id}
# handler (route-shadowing discipline). One sealed response carries rows + stats +
# aging in LOCKSTEP (rca.py single-truth conditions), so the desk chips can never
# drift from the rows a lens click returns.

_RCA_STOPWORDS = {"with", "from", "that", "this", "when", "after", "before", "into",
                  "over", "under", "down", "unable", "cannot", "error", "issue",
                  "problem", "failed", "failing", "failure", "not", "the", "and"}


def _rca_sev_order():
    return case((SdTicket.is_major_incident == True, 1),  # noqa: E712
                (SdTicket.priority == "critical", 2),
                (SdTicket.priority.in_(("urgent", "high")), 3), else_=4)


def _rca_aging(db: Session, cond, now, days: int) -> RcaBoardAging:
    """Aging ladder over the OWED set — buckets on the terminal stamp."""
    stamp = func.coalesce(SdTicket.resolved_at, SdTicket.closed_at, SdTicket.created_at)
    d3, d7, d14 = (now - timedelta(days=3), now - timedelta(days=7), now - timedelta(days=14))
    row = _sealed(db.query(
        func.sum(case((stamp >= d3, 1), else_=0)),
        func.sum(case((and_(stamp < d3, stamp >= d7), 1), else_=0)),
        func.sum(case((and_(stamp < d7, stamp >= d14), 1), else_=0)),
        func.sum(case((stamp < d14, 1), else_=0)),
    ).filter(rca_owed_cond(now, days)), cond).first()
    return RcaBoardAging(d0_3=int(row[0] or 0), d3_7=int(row[1] or 0),
                         d7_14=int(row[2] or 0), d14_plus=int(row[3] or 0))


def _rca_stats(db: Session, cond, now, days: int, extra=None) -> RcaBoardStats:
    """The lens chips — same seal/window/modifiers as the board rows (minus the lens)."""
    def _count(c):
        q = _sealed(db.query(func.count(SdTicket.id)).filter(c), cond)
        if extra is not None:
            q = q.filter(*extra)
        return int(q.scalar() or 0)
    eff = rca_effective_status_expr()
    base = and_(SdTicket.is_deleted == False,  # noqa: E712
                SdTicket.merged_into_id.is_(None))
    eligible = _count(rca_eligible_cond(now, days))
    owed = _count(rca_owed_cond(now, days))
    stats = RcaBoardStats(
        owed=owed,
        pending=_count(and_(base, eff == "filed")),
        returned=_count(and_(base, eff == "returned")),
        validated=_count(and_(base, eff == "validated")),
        stale=_count(and_(base, eff == "stale")),
        eligible=eligible,
        coverage_pct=(int(round(100 * (eligible - owed) / eligible)) if eligible else 100),
        aging=_rca_aging(db, cond, now, days),
    )
    return stats


def _rca_board_item(t: SdTicket, names: dict, teams: dict, now, can_file: bool = True) -> RcaBoardItem:
    eff = rca_effective_status(t)
    owed_age = None
    if eff not in RCA_LIVE_STATUSES and t.status in TERMINAL_TICKET_STATUSES:
        stamp = sla_util._aware(t.resolved_at or t.closed_at or t.created_at)
        if stamp:
            owed_age = round(max(0.0, (now - stamp).total_seconds() / 3600.0), 1)
    return RcaBoardItem(
        ticket_id=t.id, ticket_number=t.ticket_number, subject=t.subject,
        sev=ticket_sev(t.priority, bool(t.is_major_incident)), priority=t.priority,
        status=t.status, team_id=t.team_id,
        team_name=teams.get(str(t.team_id)) if t.team_id else None,
        assigned_agent_id=t.assigned_agent_id,
        assigned_agent_name=names.get(str(t.assigned_agent_id)) if t.assigned_agent_id else None,
        is_major_incident=bool(t.is_major_incident),
        breached=bool(t.sla_response_breached or t.sla_resolution_breached),
        breach_reason=t.breach_reason, resolved_at=t.resolved_at, closed_at=t.closed_at,
        rca_status=eff, rca_category=t.rca_category,
        rca_summary_preview=((t.rca_summary or "")[:240] or None),
        rca_five_whys=list(t.rca_five_whys or []), rca_factors=list(t.rca_factors or []),
        rca_corrective=t.rca_corrective, rca_preventive=t.rca_preventive,
        rca_filed_at=t.rca_filed_at, rca_filed_by_id=t.rca_filed_by_id,
        rca_filed_by_name=names.get(str(t.rca_filed_by_id)) if t.rca_filed_by_id else None,
        rca_reviewed_at=t.rca_reviewed_at, rca_reviewed_by_id=t.rca_reviewed_by_id,
        rca_reviewed_by_name=names.get(str(t.rca_reviewed_by_id)) if t.rca_reviewed_by_id else None,
        rca_review_note=t.rca_review_note,
        inherited=t.rca_inherited_from_problem_id is not None,
        linked_problem_id=t.linked_problem_id, owed_age_hours=owed_age, can_file=can_file)


@router.get("/incidents/rca/board", response_model=RcaBoardResponse)
def rca_board(
    lens: str = Query("owed"),
    sev: Optional[int] = Query(None, ge=1, le=4),
    q: Optional[str] = Query(None, max_length=120),
    days: int = Query(30, ge=7, le=365),
    incident_only: bool = Query(False, description="Restrict to incident-type ∪ MI records (RCA debt spans breached requests too, so default off)"),
    owner_id: Optional[UUID] = Query(None, description="Scope to one agent — rows they own (assigned) or filed the RCA on. Honors the manager 'actor' deep link."),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    sort: str = Query("owed_age"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """The RCA desk's sealed board: one response carrying the lens rows AND the chip
    stats AND the debt-aging ladder, all built from rca.py's single-truth conditions.
    Lenses: owed (debt) · pending (filed, awaiting review) · returned · validated ·
    stale · all. Stats honor sev/incident_only but not q/lens (chips = whole desk)."""
    lens = (lens or "owed").lower()
    if lens not in RCA_LENSES:
        raise HTTPException(422, f"Unknown lens '{lens}' — one of {', '.join(RCA_LENSES)}.")
    if sort not in ("owed_age", "resolved_at", "filed_at", "sev"):
        raise HTTPException(422, "sort must be one of owed_age|resolved_at|filed_at|sev")
    now = sla_util.now_utc()
    cond = _scope_cond(db, admin)
    extra = []
    if incident_only:
        extra.append(incident_lens_cond())
    if sev:
        extra.append(sev_cond(sev))
    query = _sealed(db.query(SdTicket).filter(rca_lens_condition(lens, now, days)), cond)
    for e in extra:
        query = query.filter(e)
    if owner_id:
        # Manager 'actor' deep link (?actor=): the agent's own RCA desk — tickets they
        # own (owed lens, no filing yet) OR filings they authored (filed/validated/...).
        query = query.filter(or_(SdTicket.assigned_agent_id == owner_id,
                                 SdTicket.rca_filed_by_id == owner_id))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.ticket_number.ilike(like),
                                 SdTicket.subject.ilike(like)))
    total = query.count()
    stamp = func.coalesce(SdTicket.resolved_at, SdTicket.closed_at, SdTicket.created_at)
    order = {
        "owed_age": stamp.asc(),
        "resolved_at": SdTicket.resolved_at.desc().nullslast(),
        "filed_at": SdTicket.rca_filed_at.desc().nullslast(),
        "sev": _rca_sev_order().asc(),
    }[sort]
    rows = query.order_by(order, SdTicket.created_at.desc()) \
                .offset((page - 1) * limit).limit(limit).all()
    from app.routers.support_desk._common import _user_names
    uids = set()
    for t in rows:
        uids.update([t.assigned_agent_id, t.rca_filed_by_id, t.rca_reviewed_by_id])
    names = _user_names(db, uids)
    team_ids = {t.team_id for t in rows if t.team_id}
    teams = {str(r.id): r.name for r in db.query(SdTeam.id, SdTeam.name)
             .filter(SdTeam.id.in_(team_ids)).all()} if team_ids else {}
    # Per-row FILE eligibility — mirrors POST /{id}/rca's _require_ticket_actor EXACTLY
    # (assignee ∪ collaborator ∪ team lead ∪ claim-eligible-when-unassigned ∪ live-swarm ∪
    # superuser), so the agent desk only offers FILE where the backend will accept it.
    # Superusers short-circuit (ctx None ⇒ always True); the swarm probe only fires for the
    # handful of otherwise-ineligible rows, so cost stays bounded by the page size.
    from app.routers.support_desk.tickets import _ticket_actor_error
    from app.routers.support_desk.tickets_self import _team_context
    actor_ctx = None if getattr(admin, "is_superuser", False) else _team_context(db, admin)
    def _can_file(t: SdTicket) -> bool:
        return actor_ctx is None or _ticket_actor_error(t, admin, actor_ctx, db) is None
    return RcaBoardResponse(
        items=[_rca_board_item(t, names, teams, now, can_file=_can_file(t)) for t in rows],
        total=total, page=page, limit=limit, lens=lens, days=days,
        stats=_rca_stats(db, cond, now, days, extra=extra or None),
        generated_at=now)


@router.get("/incidents/rca/analytics", response_model=RcaAnalyticsResponse)
def rca_analytics(
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """RCA program analytics (sealed): coverage, cause/breach-reason mix, cycle time
    (resolved→filed) + review latency (filed→ruled) as median/p90, debt aging,
    corrective-action follow-through (PIR registers), KEDB deflection, weekly trend.
    Every block is present at zero data — the desks stay data-rich at zero rows."""
    now = sla_util.now_utc()
    cutoff = now - timedelta(days=days)
    cond = _scope_cond(db, admin)
    base = and_(SdTicket.is_deleted == False,  # noqa: E712
                SdTicket.merged_into_id.is_(None))

    eligible = int(_sealed(db.query(func.count(SdTicket.id))
                           .filter(rca_eligible_cond(now, days)), cond).scalar() or 0)
    owed = int(_sealed(db.query(func.count(SdTicket.id))
                       .filter(rca_owed_cond(now, days)), cond).scalar() or 0)
    coverage = RcaCoverage(eligible=eligible, covered=eligible - owed,
                           pct=(int(round(100 * (eligible - owed) / eligible)) if eligible else 100))

    filed_stamp = func.coalesce(SdTicket.rca_filed_at, SdTicket.resolved_at, SdTicket.created_at)
    cat_rows = _sealed(db.query(func.coalesce(SdTicket.rca_category, "uncategorized"),
                                func.count(SdTicket.id))
                       .filter(base, rca_effective_status_expr().isnot(None),
                               filed_stamp >= cutoff), cond) \
        .group_by(func.coalesce(SdTicket.rca_category, "uncategorized")).all()
    category_mix = sorted([RcaMixSlice(key=str(k), count=int(c or 0)) for k, c in cat_rows],
                          key=lambda s: -s.count)

    br_rows = _sealed(db.query(SdTicket.breach_reason, func.count(SdTicket.id))
                      .filter(base, breached_cond(),
                              SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES)),
                              SdTicket.breach_reason.isnot(None), SdTicket.breach_reason != "",
                              func.coalesce(SdTicket.resolved_at, SdTicket.closed_at) >= cutoff),
                      cond).group_by(SdTicket.breach_reason).all()
    breach_reason_mix = sorted([RcaMixSlice(key=str(k), count=int(c or 0)) for k, c in br_rows],
                               key=lambda s: -s.count)[:10]

    def _latency(start_col, end_col, extra_cond=None) -> RcaLatency:
        qy = _sealed(db.query(start_col, end_col)
                     .filter(base, start_col.isnot(None), end_col.isnot(None),
                             end_col >= cutoff), cond)
        if extra_cond is not None:
            qy = qy.filter(extra_cond)
        pairs = qy.order_by(end_col.desc()).limit(2000).all()
        hours = sorted(max(0.0, (sla_util._aware(b) - sla_util._aware(a)).total_seconds() / 3600.0)
                       for a, b in pairs if a and b)
        if not hours:
            return RcaLatency(n=0)
        def _pct(p):
            i = min(len(hours) - 1, max(0, int(round(p * (len(hours) - 1)))))
            return round(hours[i], 1)
        return RcaLatency(median_hours=_pct(0.5), p90_hours=_pct(0.9), n=len(hours))

    cycle_time = _latency(SdTicket.resolved_at, SdTicket.rca_filed_at)
    review_latency = _latency(SdTicket.rca_filed_at, SdTicket.rca_reviewed_at)

    total_a = done_a = overdue_a = 0
    today_iso = now.date().isoformat()
    for _pir, _t, _kind, _idx, a in iter_scoped_actions(db, cond):
        total_a += 1
        if str(a.get("status") or "open").lower() == "done":
            done_a += 1
        elif action_is_overdue(a, today_iso):
            overdue_a += 1
    actions = RcaActionsFollowThrough(
        total=total_a, done=done_a, open=total_a - done_a, overdue=overdue_a,
        done_pct=(int(round(100 * done_a / total_a)) if total_a else 100))

    known = int(db.query(func.count(SdProblem.id))
                .filter(SdProblem.is_deleted == False,  # noqa: E712
                        SdProblem.status == "known_error").scalar() or 0)
    published = int(db.query(func.count(SdProblem.id))
                    .filter(SdProblem.is_deleted == False,  # noqa: E712
                            SdProblem.workaround_published == True).scalar() or 0)  # noqa: E712
    linked_total = 0
    for (ltids,) in (db.query(SdProblem.linked_ticket_ids)
                     .filter(SdProblem.is_deleted == False)  # noqa: E712
                     .order_by(SdProblem.updated_at.desc().nullslast()).limit(500).all()):
        linked_total += len(ltids or [])
    kedb = RcaKedbStats(known_errors=known, published_workarounds=published,
                        linked_ticket_total=linked_total)

    week = func.date_trunc("week", SdTicket.rca_filed_at)
    filed_rows = _sealed(db.query(week, func.count(SdTicket.id))
                         .filter(base, SdTicket.rca_filed_at >= cutoff), cond) \
        .group_by(week).all()
    vweek = func.date_trunc("week", SdTicket.rca_reviewed_at)
    val_rows = _sealed(db.query(vweek, func.count(SdTicket.id))
                       .filter(base, SdTicket.rca_status == "validated",
                               SdTicket.rca_reviewed_at >= cutoff), cond) \
        .group_by(vweek).all()
    trend_map: dict = {}
    for w, c in filed_rows:
        trend_map.setdefault(w, {"filed": 0, "validated": 0})["filed"] = int(c or 0)
    for w, c in val_rows:
        trend_map.setdefault(w, {"filed": 0, "validated": 0})["validated"] = int(c or 0)
    trend = [RcaTrendWeek(week_start=w, **v)
             for w, v in sorted(trend_map.items(), key=lambda kv: (kv[0] is None, kv[0]))]

    return RcaAnalyticsResponse(
        days=days, coverage=coverage, category_mix=category_mix,
        breach_reason_mix=breach_reason_mix, cycle_time=cycle_time,
        review_latency=review_latency, debt_aging=_rca_aging(db, cond, now, 30),
        actions_follow_through=actions, kedb=kedb, trend=trend, generated_at=now)


def _rca_subject_tokens(subject: str) -> list[str]:
    toks = [w.strip(".,:;!?()[]'\"").lower() for w in (subject or "").split()]
    return [w for w in toks if len(w) > 3 and w not in _RCA_STOPWORDS][:2]


@router.get("/incidents/rca/clusters", response_model=RcaClustersResponse)
def rca_clusters(
    days: int = Query(90, ge=7, le=365),
    min_size: int = Query(3, ge=2, le=20),
    limit: int = Query(10, ge=1, le=25),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Recurrence detection (proactive problem management): terminal incidents from the
    window grouped by cause signature — category+service (strongest), category+keyword,
    then keyword-pair fallback. Clusters ≥ min_size nominate PROBLEM candidates; ones
    already covered by an open problem are flagged instead of re-nominated. Same
    feature weights as /incidents/{id}/similar; bounded 500-row fold, no ML."""
    now = sla_util.now_utc()
    cutoff = now - timedelta(days=days)
    cond = _scope_cond(db, admin)
    rows = _sealed(db.query(SdTicket)
                   .filter(lens_condition("all"),
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES)),
                           SdTicket.created_at >= cutoff), cond) \
        .order_by(SdTicket.resolved_at.desc().nullslast()).limit(500).all()

    # signature keys per ticket, strongest first: cat+service > cat+keyword > kw-pair
    buckets: dict = {}
    for t in rows:
        keys = []
        kws = _rca_subject_tokens(t.subject or "")
        for s in (t.affected_services or [])[:5]:
            sv = str(s).strip().lower()
            if sv and t.category_id:
                keys.append((0, ("csvc", str(t.category_id), sv)))
        if t.category_id:
            for w in kws:
                keys.append((1, ("ckw", str(t.category_id), w)))
        if len(kws) == 2:
            keys.append((2, ("kw2", kws[0], kws[1])))
        for rank, key in keys:
            buckets.setdefault(key, {"rank": rank, "tickets": {}})
            buckets[key]["tickets"][str(t.id)] = t

    # greedy disjoint clustering: strongest key first, biggest first; a ticket
    # belongs to exactly one cluster (its strongest surviving signature).
    candidates = sorted(buckets.items(),
                        key=lambda kv: (kv[1]["rank"], -len(kv[1]["tickets"])))
    claimed: set = set()
    clusters: list[RcaClusterItem] = []
    cat_ids = {t.category_id for t in rows if t.category_id}
    cat_names = {}
    if cat_ids:
        cat_names = {str(r.id): r.name for r in
                     db.query(SdCategory.id, SdCategory.name)
                     .filter(SdCategory.id.in_(cat_ids)).all()}
    open_problems = (db.query(SdProblem)
                     .filter(SdProblem.is_deleted == False,  # noqa: E712
                             SdProblem.status != "closed")
                     .order_by(SdProblem.updated_at.desc().nullslast()).limit(300).all())

    for key, b in candidates:
        members = [t for tid, t in b["tickets"].items() if tid not in claimed]
        if len(members) < min_size:
            continue
        members.sort(key=lambda t: (t.resolved_at or t.created_at or now), reverse=True)
        ids = {str(t.id) for t in members}
        claimed |= ids
        sev_worst = min(ticket_sev(t.priority, bool(t.is_major_incident)) for t in members)
        stamps = [sla_util._aware(t.resolved_at or t.created_at) for t in members]
        stamps = [s for s in stamps if s]
        cats = [t.rca_category for t in members if t.rca_category]
        rca_hint = max(set(cats), key=cats.count) if cats else None
        open_p = next((p for p in open_problems
                       if ids & {str(x) for x in (p.linked_ticket_ids or [])}), None)
        kind, *parts = key
        sig = RcaClusterSignature(
            category_id=UUID(parts[0]) if kind in ("csvc", "ckw") else None,
            category_name=cat_names.get(parts[0]) if kind in ("csvc", "ckw") else None,
            service=parts[1] if kind == "csvc" else None,
            keywords=(parts[1:] if kind == "ckw" else parts if kind == "kw2" else []))
        label = (sig.category_name or (sig.keywords[0] if sig.keywords else "recurring fault"))
        svc = f" — {sig.service}" if sig.service else ""
        clusters.append(RcaClusterItem(
            signature=sig, count=len(members),
            score=round(len(members) * (2.0 if sev_worst <= 2 else 1.0), 1),
            sev_worst=sev_worst,
            first_seen=min(stamps) if stamps else None,
            last_seen=max(stamps) if stamps else None,
            has_open_problem=open_p is not None,
            open_problem_id=open_p.id if open_p else None,
            open_problem_number=open_p.problem_number if open_p else None,
            rca_hint=rca_hint,
            suggested_problem_title=f"Recurring: {label}{svc} ({len(members)} incidents / {days}d)"[:300],
            ticket_ids=[t.id for t in members],
            tickets=[RcaClusterTicket(
                id=t.id, ticket_number=t.ticket_number, subject=t.subject,
                sev=ticket_sev(t.priority, bool(t.is_major_incident)),
                resolved_at=t.resolved_at, rca_status=rca_effective_status(t))
                for t in members[:5]]))
    clusters.sort(key=lambda c: -c.score)
    return RcaClustersResponse(days=days, min_size=min_size,
                               clusters=clusters[:limit], scanned=len(rows),
                               generated_at=now)


@router.post("/incidents/rca/clusters/promote", response_model=RcaClusterPromoteResponse,
             status_code=201)
def rca_cluster_promote(payload: RcaClusterPromote, request: Request,
                        db: Session = Depends(get_db),
                        admin: User = Depends(get_support_agent)):
    """Promote a recurrence cluster into a PROBLEM record (status=investigating) and
    link its tickets. Per-ticket sealing: out-of-scope/merged members are skipped with
    a reason (cascade-result pattern) — 422 if fewer than 2 members survive."""
    from app.routers.support_desk.tickets import _get_ticket, _log_activity
    from app.routers.support_desk.itil import _number
    from app.models.support_desk.constants import NUMBERING_MODULE_PROBLEM
    results: list[RcaPromoteResult] = []
    linked: list[SdTicket] = []
    for tid in payload.ticket_ids:
        try:
            t = _get_ticket(db, tid, admin)      # 404 outside the team seal
            if t.merged_into_id is not None:
                results.append(RcaPromoteResult(ticket_id=tid, ok=False,
                                                reason="merged into another ticket"))
                continue
            linked.append(t)
            results.append(RcaPromoteResult(ticket_id=tid, ok=True))
        except HTTPException as e:
            results.append(RcaPromoteResult(ticket_id=tid, ok=False, reason=str(e.detail)))
    if len(linked) < 2:
        raise HTTPException(422, "A problem needs at least two in-scope incidents — "
                                 "fewer than two of the cluster's tickets survived the seal.")
    p = SdProblem(
        title=payload.title.strip()[:300],
        description=(payload.statement or "").strip() or None,
        root_cause=(payload.root_cause_hint or "").strip() or None,
        status="investigating",
        linked_ticket_ids=[str(t.id) for t in linked],
        owner_id=admin.id, created_by_id=admin.id,
        problem_number=_number(db, NUMBERING_MODULE_PROBLEM, "PRB"),
    )
    db.add(p)
    db.flush()
    for t in linked:
        if t.linked_problem_id is None:
            t.linked_problem_id = p.id
        _log_activity(db, t, admin, "cluster_promoted",
                      {"problem_id": str(p.id), "problem_number": p.problem_number,
                       "cluster_size": len(linked)})
    write_audit(db, entity_type="problem", op="cluster_promoted", entity_id=p.id,
                actor_id=admin.id, request=request,
                details={"title": p.title, "linked": len(linked),
                         "skipped": len(results) - len(linked)})
    db.commit()
    db.refresh(p)
    return RcaClusterPromoteResponse(problem_id=p.id, problem_number=p.problem_number,
                                     linked=len(linked),
                                     skipped=len(results) - len(linked), results=results)


# ═══════════════════════════════ Similar incidents (AI insights) ═══════════════════════════════
@router.get("/incidents/{ticket_id}/similar", response_model=list[SimilarIncidentItem])
def similar_incidents(ticket_id: UUID, db: Session = Depends(get_db),
                      admin: User = Depends(get_support_agent)):
    """Heuristic precedent finder: terminal incidents from the last 180 days that share
    a category, an affected service, or subject keywords — WITH their recorded fix, so
    a responder starts from precedent instead of zero. Registered AFTER every literal
    /incidents/* path (route-shadowing discipline)."""
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    cond = _scope_cond(db, admin)
    d180 = sla_util.now_utc() - timedelta(days=180)
    words = [w.lower() for w in (t.subject or "").split() if len(w) > 3][:3]
    matchers = []
    if t.category_id:
        matchers.append(SdTicket.category_id == t.category_id)
    for s in (t.affected_services or [])[:5]:
        matchers.append(SdTicket.affected_services.contains([s]))
    for w in words:
        matchers.append(SdTicket.subject.ilike(f"%{w}%"))
    if not matchers:
        return []
    rows = _sealed(db.query(SdTicket)
                   .filter(lens_condition("all"),
                           SdTicket.id != t.id,
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES)),
                           SdTicket.created_at >= d180,
                           or_(SdTicket.resolution_summary.isnot(None),
                               SdTicket.rca_summary.isnot(None)),
                           or_(*matchers)), cond) \
        .order_by(SdTicket.resolved_at.desc().nullslast()).limit(50).all()
    my_services = {str(s).strip().lower() for s in (t.affected_services or [])}
    scored = []
    for c in rows:
        score, why = 0.0, []
        if t.category_id and c.category_id == t.category_id:
            score += 2.0
            why.append("same category")
        c_services = {str(s).strip().lower() for s in (c.affected_services or [])}
        if my_services & c_services:
            score += 2.0
            why.append("shared service: " + sorted(my_services & c_services)[0])
        hit = [w for w in words if w in (c.subject or "").lower()]
        if hit:
            score += 1.0 * len(hit)
            why.append("keywords: " + ", ".join(hit))
        if score > 0:
            scored.append((score, c, "; ".join(why)))
    scored.sort(key=lambda x: -x[0])
    return [SimilarIncidentItem(
        id=c.id, ticket_number=c.ticket_number, subject=c.subject,
        sev=ticket_sev(c.priority, bool(c.is_major_incident)),
        resolved_at=c.resolved_at, resolution_summary=c.resolution_summary,
        rca_summary=c.rca_summary, root_cause_hint=c.resolution_category,
        score=round(s, 1), reason=why) for s, c, why in scored[:5]]


# ═══════════════════════════════ Children of a master incident ═══════════════════════════════
@router.get("/incidents/{ticket_id}/children", response_model=IncidentListResponse)
def list_incident_children(ticket_id: UUID, db: Session = Depends(get_db),
                           admin: User = Depends(get_support_agent)):
    """Live children rolled under a master incident. Sealed like every lens — the
    parent fetch 404s outside scope, and each child row passes the seal too."""
    from app.routers.support_desk.tickets import _get_ticket
    parent = _get_ticket(db, ticket_id, admin)
    cond = _scope_cond(db, admin)
    rows = _sealed(db.query(SdTicket)
                   .filter(SdTicket.parent_incident_id == parent.id,
                           SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None)), cond) \
        .order_by(SdTicket.is_major_incident.desc(), _PRI_RANK.desc(),
                  SdTicket.created_at.desc()).limit(100).all()
    names = _names_for(db, rows)
    cat_ids = {t.category_id for t in rows if t.category_id}
    cats = {str(c.id): c.name for c in db.query(SdCategory.id, SdCategory.name)
            .filter(SdCategory.id.in_(cat_ids)).all()} if cat_ids else {}
    team_ids = {t.team_id for t in rows if t.team_id}
    teams = {str(r.id): r.name for r in db.query(SdTeam.id, SdTeam.name)
             .filter(SdTeam.id.in_(team_ids)).all()} if team_ids else {}
    pirs = _pir_map(db, [t.id for t in rows])
    parents = {str(parent.id): parent.ticket_number}
    return IncidentListResponse(total=len(rows),
                                items=[_row(t, names, cats, teams, pirs, parents) for t in rows])


def _require_incident(t: SdTicket) -> None:
    """422 unless the ticket sits inside the incident lens (type OR MI flag)."""
    if t.ticket_type != TicketType.INCIDENT.value and not t.is_major_incident:
        raise HTTPException(422, "This ticket isn't an incident — phase clocks and sitreps "
                                 "apply to incident-lens tickets only.")


# ═══════════════════════════════ Phase clocks ═══════════════════════════════
@router.get("/incidents/{ticket_id}/phases", response_model=IncidentPhasesResponse)
def incident_phases(ticket_id: UUID, db: Session = Depends(get_db),
                    admin: User = Depends(get_support_agent)):
    """The derived phase timeline (started → detected → declared → acked →
    mitigating → resolved → closed) + inter-phase durations. Sealed single fetch."""
    from app.routers.support_desk.tickets import _get_ticket
    from app.utils.support_desk.incidents import _mins_between
    t = _get_ticket(db, ticket_id, admin)
    _require_incident(t)
    track = build_phase_track(db, t)
    d = track["durations_minutes"]
    by_key = {p["key"]: p["at"] for p in track["phases"]}
    return IncidentPhasesResponse(
        ticket_id=t.id, ticket_number=t.ticket_number,
        sev=ticket_sev(t.priority, bool(t.is_major_incident)),
        phases=track["phases"], durations_minutes=d,
        mttd_minutes=_mins_between(by_key.get("started"), by_key.get("detected")),
        mtta_minutes=_mins_between(by_key.get("detected"), by_key.get("acknowledged")),
        mttr_minutes=_mins_between(by_key.get("detected"), by_key.get("resolved")))


# ═══════════════════════ Merged dossier stream (replay) ═══════════════════════

_STREAM_TYPES = ("activity", "comment", "worklog", "task")
_STREAM_SOURCE_CAP = 400   # per-source bound before the Python merge — replay-sized


@router.get("/incidents/{ticket_id}/stream", response_model=IncidentStreamResponse)
def incident_stream(
    ticket_id: UUID,
    types: Optional[str] = Query(None, description="csv of activity|comment|worklog|task"),
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """ServiceNow-style merged activity stream for ONE incident: activities +
    conversation + worklogs + response tasks in a single chronological record
    (PIR lifecycle rides along as governance activity rows). Powers the timeline
    desks' dossier/replay panel. Archived incidents stay readable — replaying a
    settled record is exactly the deep-storage use case — so this seals via the
    raw fetch + scope check (list_activities pattern), NOT _get_ticket."""
    from app.routers.support_desk.tickets import _require_ticket_scope
    from app.routers.support_desk._common import _user_names
    from app.models.support_desk.ticket import SdTicketComment
    from app.models.support_desk.collab import SdTicketWorklog
    wanted = [x.strip() for x in (types or "").split(",") if x.strip()]
    unknown = [x for x in wanted if x not in _STREAM_TYPES]
    if unknown:
        raise HTTPException(422, f"Unknown stream type(s): {', '.join(sorted(unknown))} — "
                                 f"valid: {', '.join(_STREAM_TYPES)}")
    take = set(wanted or _STREAM_TYPES)
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    _require_ticket_scope(db, t, admin)
    _require_incident(t)

    def _window(q_, col):
        if date_from:
            q_ = q_.filter(col >= date_from)
        if date_to:
            q_ = q_.filter(col <= date_to)
        return q_

    items: list[IncidentStreamItem] = []
    counts = IncidentStreamCounts()
    name_ids: set = set()

    if "activity" in take:
        acts = (_window(db.query(SdTicketActivity)
                        .filter(SdTicketActivity.ticket_id == t.id), SdTicketActivity.created_at)
                .order_by(SdTicketActivity.created_at.desc()).limit(_STREAM_SOURCE_CAP).all())
        counts.activity = len(acts)
        for a in acts:
            meta = timeline_meta(a.action)
            items.append(IncidentStreamItem(
                kind="activity", id=a.id, at=a.created_at, actor_user_id=a.actor_user_id,
                actor=a.actor_name or "System",
                title=meta["label"] or a.action.replace("_", " "),
                category=meta["category"], tone=meta["tone"],
                is_milestone=bool(a.is_milestone),
                meta={"action": a.action, "detail": a.detail or {}}))
    if "comment" in take:
        cms = (_window(db.query(SdTicketComment)
                       .filter(SdTicketComment.ticket_id == t.id), SdTicketComment.created_at)
               .order_by(SdTicketComment.created_at.desc()).limit(_STREAM_SOURCE_CAP).all())
        counts.comment = len(cms)
        for c in cms:
            # Agent surface: internals included (list_comments semantics); redacted
            # bodies were tombstoned at write time, so serving c.body stays honest.
            items.append(IncidentStreamItem(
                kind="comment", id=c.id, at=c.created_at, actor_user_id=c.author_user_id,
                actor=c.author_name, title="Internal note" if c.is_internal else "Reply",
                body=c.body, category="comms", tone="dim" if c.is_internal else "hi",
                is_internal=bool(c.is_internal),
                meta={"author_kind": c.author_kind, "is_redacted": bool(c.is_redacted),
                      "attachments": len(c.attachments or [])}))
    if "worklog" in take:
        wls = (_window(db.query(SdTicketWorklog)
                       .filter(SdTicketWorklog.ticket_id == t.id,
                               SdTicketWorklog.is_deleted == False),  # noqa: E712
                       SdTicketWorklog.created_at)
               .order_by(SdTicketWorklog.created_at.desc()).limit(_STREAM_SOURCE_CAP).all())
        counts.worklog = len(wls)
        for wl in wls:
            name_ids.add(wl.user_id)
            items.append(IncidentStreamItem(
                kind="worklog", id=wl.id, at=wl.created_at, actor_user_id=wl.user_id,
                actor=None,  # filled from _user_names below
                title=f"{wl.minutes}m {wl.work_type}", body=wl.note,
                category="sla", tone="dim",
                meta={"minutes": wl.minutes, "work_type": wl.work_type}))
    if "task" in take:
        tks = (_window(db.query(SdIncidentTask)
                       .filter(SdIncidentTask.ticket_id == t.id), SdIncidentTask.created_at)
               .order_by(SdIncidentTask.created_at.desc()).limit(_STREAM_SOURCE_CAP).all())
        counts.task = len(tks)
        for k in tks:
            if k.owner_id:
                name_ids.add(k.owner_id)
            items.append(IncidentStreamItem(
                kind="task", id=k.id, at=k.created_at, actor_user_id=k.owner_id,
                actor=None, title=k.title, category="command",
                tone="live" if k.status == "done" else "dim",
                meta={"seq": k.seq, "status": k.status, "template_key": k.template_key,
                      "done_at": k.done_at.isoformat() if k.done_at else None}))

    names = _user_names(db, name_ids) if name_ids else {}
    for it in items:
        if it.actor is None and it.actor_user_id:
            it.actor = names.get(str(it.actor_user_id))
    items.sort(key=lambda x: (x.at, str(x.id)), reverse=True)
    total = len(items)
    paged = items[(page - 1) * limit: (page - 1) * limit + limit]
    header = IncidentStreamTicket(
        id=t.id, ticket_number=t.ticket_number, subject=t.subject, status=t.status,
        priority=t.priority, sev=ticket_sev(t.priority, bool(t.is_major_incident)),
        is_major_incident=bool(t.is_major_incident), acknowledged_at=t.acknowledged_at,
        mi_proposed_at=t.mi_proposed_at, war_room_url=t.war_room_url, team_id=t.team_id,
        assigned_agent_id=t.assigned_agent_id, incident_commander_id=t.incident_commander_id,
        parent_incident_id=t.parent_incident_id, created_at=t.created_at,
        resolved_at=t.resolved_at, archived_at=t.archived_at)
    return IncidentStreamResponse(ticket=header, total=total, page=page, limit=limit,
                                  counts=counts, items=paged)


# ═══════════════════════════════ Executive sitrep ═══════════════════════════════
def _assemble_sitrep(db: Session, t: SdTicket) -> dict:
    """One dict feeding BOTH the sitrep JSON and the PDF so they can never diverge."""
    from app.routers.support_desk._common import _user_names
    nowt = sla_util.now_utc()
    track = build_phase_track(db, t)
    names = _user_names(db, {t.incident_commander_id, t.comms_lead_id, t.ops_lead_id})
    # running clock: detected → resolved (or now while live)
    det = sla_util._aware(t.incident_detected_at or t.created_at)
    end = sla_util._aware(t.resolved_at) or nowt
    running = None
    if det and end >= det:
        mins = int((end - det).total_seconds() // 60)
        running = f"{mins // 60}h {mins % 60:02d}m"
    acts = (db.query(SdTicketActivity)
            .filter(SdTicketActivity.ticket_id == t.id,
                    SdTicketActivity.action.in_(("status_update", "decision_logged")))
            .order_by(SdTicketActivity.created_at.desc()).limit(80).all())
    last_update = {}
    decisions = []
    dec_count = 0
    for r in acts:
        d = r.detail or {}
        if r.action == "status_update" and not last_update:
            last_update = {"at": r.created_at, "actor": r.actor_name,
                           "phase": d.get("phase"), "audience": d.get("audience"),
                           "preview": str(d.get("preview") or d.get("note") or "")[:240]}
        if r.action == "decision_logged":
            dec_count += 1
            if len(decisions) < 5:
                decisions.append({"at": r.created_at, "kind": d.get("kind"),
                                  "decision": str(d.get("decision") or "")[:200],
                                  "actor": r.actor_name})
    kids = (db.query(SdTicket.status, func.count(SdTicket.id))
            .filter(SdTicket.parent_incident_id == t.id,
                    SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None))
            .group_by(SdTicket.status).all())
    kid_total = sum(int(n or 0) for _s, n in kids)
    kid_open = sum(int(n or 0) for s, n in kids if s not in TERMINAL_TICKET_STATUSES)
    watchers = 0
    try:
        from app.models.support_desk.collab import SdTicketWatcher
        watchers = (db.query(func.count(SdTicketWatcher.id))
                    .filter(SdTicketWatcher.ticket_id == t.id).scalar() or 0)
    except Exception:
        pass
    pir = _pir_map(db, [t.id]).get(str(t.id))
    pir_number = None
    if pir:
        row = db.query(SdIncidentReport.report_number).filter(SdIncidentReport.id == pir[0]).first()
        pir_number = row[0] if row else None
    return {
        "ticket_id": t.id, "ticket_number": t.ticket_number, "subject": t.subject,
        "status": t.status, "sev": ticket_sev(t.priority, bool(t.is_major_incident)),
        "is_major_incident": bool(t.is_major_incident),
        "running": running, "generated_at": nowt,
        "phases": track["phases"], "durations_minutes": track["durations_minutes"],
        "roster": {
            "commander_id": t.incident_commander_id,
            "commander_name": names.get(str(t.incident_commander_id)),
            "comms_lead_id": t.comms_lead_id,
            "comms_lead_name": names.get(str(t.comms_lead_id)),
            "ops_lead_id": t.ops_lead_id,
            "ops_lead_name": names.get(str(t.ops_lead_id)),
        },
        "impact": {
            "affected_services": list(t.affected_services or []),
            "affected_users": t.affected_users, "business_impact": t.business_impact,
            "revenue_impact": t.revenue_impact,
            "compliance_impact": bool(t.compliance_impact),
            "security_impact": bool(t.security_impact),
            "public_impact": bool(t.public_impact),
        },
        "cadence": {
            "interval_minutes": t.update_interval_minutes,
            "next_due_at": t.next_update_due_at,
            "last_update_at": t.last_status_update_at,
            "overdue": bool(t.next_update_due_at
                            and sla_util._aware(t.next_update_due_at) < nowt),
        },
        "last_update": last_update,
        "decisions": {"count": dec_count, "latest": decisions},
        "sla": {
            "response_due_at": t.response_due_at, "resolution_due_at": t.resolution_due_at,
            "sla_response_breached": bool(t.sla_response_breached),
            "sla_resolution_breached": bool(t.sla_resolution_breached),
            "sla_paused_since": t.sla_paused_since,
        },
        "children": {"count": kid_total, "open_count": kid_open},
        "watchers_total": int(watchers),
        "pir": {"id": pir[0] if pir else None, "report_number": pir_number,
                "status": pir[1] if pir else None},
        "war_room_url": t.war_room_url,
    }


@router.get("/incidents/{ticket_id}/sitrep", response_model=IncidentSitrepResponse)
def incident_sitrep(ticket_id: UUID, db: Session = Depends(get_db),
                    admin: User = Depends(get_support_agent)):
    """Executive situation snapshot — phases, roster, impact, comms cadence, latest
    decisions, SLA posture, children, PIR state — in one sealed read."""
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    _require_incident(t)
    return _assemble_sitrep(db, t)


@router.get("/incidents/{ticket_id}/sitrep.pdf")
def incident_sitrep_pdf(ticket_id: UUID, db: Session = Depends(get_db),
                        admin: User = Depends(get_support_agent)):
    """The sitrep as a one-page WeasyPrint brief (same lazy-import + GTK discipline
    as the PIR dossier — a missing GTK runtime is a clear 503, never a boot crash)."""
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    _require_incident(t)
    sitrep = _json_safe(_assemble_sitrep(db, t))
    try:
        pdf = render_sitrep_pdf(t, sitrep)
    except OSError as exc:
        raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run vendor\\setup_gtk.py "
                                 f"on the backend host and retry. ({exc})")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="SITREP-{t.ticket_number}.pdf"'})


# ═══════════════════════════════ Ticket-suffix command verbs ═══════════════════════════════

def _require_incident_lead(db: Session, t: SdTicket, admin: User) -> None:
    """Confirming/declining an MI proposal — and declaring a fresh MI directly — is a
    team-lead/superuser call. Lead resolves through tickets_self._is_lead (the single
    lead definition: lead_user_id OR member_roles == 'lead')."""
    if getattr(admin, "is_superuser", False):
        return
    from app.routers.support_desk.tickets_self import _team_context, _is_lead
    ctx = _team_context(db, admin)
    if t.team_id and any(str(tm.id) == str(t.team_id) and _is_lead(tm, admin.id)
                         for tm in ctx["teams"]):
        return
    raise HTTPException(403, "Confirming or declining a major-incident call is a team-lead/"
                             "admin decision — propose it via the MI-candidate flow instead.")


def _team_lead_ids(db: Session, team_id) -> set:
    """The lead user-ids of one team (lead_user_id ∪ member_roles=='lead')."""
    if not team_id:
        return set()
    tm = db.query(SdTeam).filter(SdTeam.id == team_id).first()
    if not tm:
        return set()
    leads = {str(tm.lead_user_id)} if tm.lead_user_id else set()
    leads |= {uid for uid, role in (tm.member_roles or {}).items() if role == "lead"}
    return {UUID(x) for x in leads if x and x != "None"}


@router.post("/tickets/{ticket_id}/mi-proposal", status_code=201)
def propose_major_incident(ticket_id: UUID, payload: MiProposalCreate, request: Request,
                           db: Session = Depends(get_db),
                           admin: User = Depends(get_support_agent)):
    """ServiceNow-style MI candidate: an owner-tier responder makes the case for major
    status; the team lead / a superuser confirms or declines. Idempotent — a second
    propose while one is pending 409s with the standing proposer."""
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe, _panel_base)
    from app.routers.support_desk._common import _user_names
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "propose it as a major incident")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — propose on the surviving incident.")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — reopen it first if the "
                                 "disruption recurred, then propose.")
    if t.is_major_incident:
        raise HTTPException(409, "Already a declared major incident — no proposal needed.")
    if t.mi_proposed_at is not None:
        names = _user_names(db, {t.mi_proposed_by_id})
        who = names.get(str(t.mi_proposed_by_id)) or "a teammate"
        raise HTTPException(409, f"A major-incident proposal is already pending on this ticket "
                                 f"(proposed by {who}) — the lead's call resolves it.")
    nowt = sla_util.now_utc()
    note = payload.note.strip()
    t.mi_proposed_at = nowt
    t.mi_proposed_by_id = admin.id
    t.mi_proposal_note = note
    # pre-stage impact context for the confirming lead — only where nothing is stamped yet
    if payload.business_impact and not t.business_impact:
        t.business_impact = payload.business_impact
    if payload.affected_users is not None and t.affected_users is None:
        t.affected_users = payload.affected_users
    _log_activity(db, t, admin, "mi_proposed",
                  {"note": note, **({"business_impact": payload.business_impact}
                                    if payload.business_impact else {})})
    write_audit(db, entity_type="ticket", op="mi_proposed", entity_id=t.id, actor_id=admin.id,
                request=request, details={"note": note})
    recipients = _team_lead_ids(db, t.team_id) - {admin.id}
    for uid in recipients:
        # Panel-aware deep link: a non-superuser lead lives on /user/support — the old
        # hardcoded /admin/... sent them to a panel their token can't open.
        dispatch_safe(db, EVT_INCIDENT_MI_PROPOSED, uid, t,
                      title=f"MI candidate — {t.ticket_number} proposed as a major incident",
                      action_url=f"{_panel_base(db, uid)}/incidents/major")
    _notify_superusers(db, EVT_INCIDENT_MI_PROPOSED, t,
                       title=f"MI candidate — {t.ticket_number} proposed as a major incident",
                       action_url="/admin/support-desk/incidents/major")
    db.commit()
    return {"ok": True, "mi_proposed_at": t.mi_proposed_at.isoformat(),
            "mi_proposed_by_id": str(admin.id), "note": note}


def _apply_mi_declare(db, t, admin, *, interval: int | None, open_war_room: bool,
                      _log_activity, detail_extra: dict | None = None) -> dict:
    """The shared declare mutations used by confirm + direct declare: flag the MI,
    arm the cadence if asked, auto-open the war room if asked. Returns activity detail."""
    nowt = sla_util.now_utc()
    t.is_major_incident = True
    detail = {"on": True, **(detail_extra or {})}
    if interval:
        t.update_interval_minutes = interval
        t.next_update_due_at = nowt + timedelta(minutes=interval)
        detail["update_interval_minutes"] = interval
    if open_war_room:
        try:
            from app.routers.support_desk.l2_ops import _active_swarm
            from app.models.support_desk.collab import SdSwarmSession
            swarm = _active_swarm(db, t.id)
            if swarm is None:
                swarm = SdSwarmSession(ticket_id=t.id, started_by_id=admin.id,
                                       participant_ids=[str(admin.id)])
                db.add(swarm)
                _log_activity(db, t, admin, "swarm_started",
                              {"auto": True, "via": "major_incident"})
            # an explicit war_room_url always wins; the auto-stamp only fills a blank
            if not t.war_room_url:
                t.war_room_url = f"/user/support/queues/l2?ticket={t.id}"
            detail["war_room"] = True
        except Exception:
            pass  # declare must never fail on the war-room convenience
    return detail


@router.post("/tickets/{ticket_id}/mi-proposal/confirm")
def confirm_mi_proposal(ticket_id: UUID, payload: MiProposalDecision, request: Request,
                        db: Session = Depends(get_db),
                        admin: User = Depends(get_support_agent)):
    """Lead/superuser confirms the candidate: the ticket becomes a declared major
    incident (cadence + war room armed as asked); the proposal stamps clear (history
    lives in the mi_proposed/mi_confirmed activity rows); the proposer hears back."""
    from app.routers.support_desk.tickets import _get_ticket, _log_activity, dispatch_safe, _panel_base
    from app.routers.support_desk._common import _user_names
    t = _get_ticket(db, ticket_id, admin)
    _require_incident_lead(db, t, admin)
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — the proposal died with it.")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — reopen it first if the "
                                 "disruption recurred.")
    if t.mi_proposed_at is None:
        raise HTTPException(409, "Already a declared major incident — nothing pending."
                            if t.is_major_incident else
                            "No pending major-incident proposal on this ticket.")
    proposer_id = t.mi_proposed_by_id
    proposal_note = t.mi_proposal_note
    names = _user_names(db, {proposer_id})
    detail = _apply_mi_declare(db, t, admin,
                               interval=payload.update_interval_minutes,
                               open_war_room=payload.open_war_room,
                               _log_activity=_log_activity,
                               detail_extra={"via": "proposal_confirmed",
                                             "proposed_by": names.get(str(proposer_id)),
                                             "proposal_note": proposal_note})
    note = (payload.note or "").strip() or None
    if note:
        detail["note"] = note
    t.mi_proposed_at = None
    t.mi_proposed_by_id = None
    t.mi_proposal_note = None
    _log_activity(db, t, admin, "major_incident", detail)
    _log_activity(db, t, admin, "mi_confirmed",
                  {"proposed_by": names.get(str(proposer_id)),
                   **({"note": note} if note else {})})
    write_audit(db, entity_type="ticket", op="mi_confirmed", entity_id=t.id, actor_id=admin.id,
                request=request, details={"proposed_by": str(proposer_id) if proposer_id else None,
                                          **({"note": note} if note else {})})
    for uid in {proposer_id, t.assigned_agent_id} - {None, admin.id}:
        dispatch_safe(db, EVT_INCIDENT_DECLARED, uid, t,
                      title=f"Major incident declared — {t.ticket_number} "
                            f"(your proposal was confirmed)" if uid == proposer_id else
                            f"Major incident declared — {t.ticket_number}",
                      action_url=f"{_panel_base(db, uid)}/incidents/major")
    db.commit()
    return {"ok": True, "is_major_incident": True,
            "update_interval_minutes": t.update_interval_minutes,
            "war_room_url": t.war_room_url}


@router.post("/tickets/{ticket_id}/mi-proposal/withdraw")
def withdraw_mi_proposal(ticket_id: UUID, request: Request,
                         db: Session = Depends(get_db),
                         admin: User = Depends(get_support_agent)):
    """The PROPOSER (or a superuser) pulls their own candidate back off the pad —
    e.g. the signal recovered before a lead ruled. Leads use decline (with a note)
    instead; a foreign agent gets a 403, not a silent no-op."""
    from app.routers.support_desk.tickets import _get_ticket, _log_activity
    t = _get_ticket(db, ticket_id, admin)
    if t.mi_proposed_at is None:
        raise HTTPException(409, "No pending major-incident proposal on this ticket.")
    if not getattr(admin, "is_superuser", False) and str(t.mi_proposed_by_id) != str(admin.id):
        raise HTTPException(403, "Only the proposer (or an admin) can withdraw this candidate — "
                                 "leads decline it with a note instead.")
    t.mi_proposed_at = None
    t.mi_proposed_by_id = None
    t.mi_proposal_note = None
    _log_activity(db, t, admin, "mi_withdrawn", {})
    write_audit(db, entity_type="ticket", op="mi_withdrawn", entity_id=t.id, actor_id=admin.id,
                request=request, details={})
    db.commit()
    return {"ok": True, "withdrawn": True}


@router.post("/tickets/{ticket_id}/mi-proposal/decline")
def decline_mi_proposal(ticket_id: UUID, payload: MiProposalDecision, request: Request,
                        db: Session = Depends(get_db),
                        admin: User = Depends(get_support_agent)):
    """Lead/superuser declines the candidate — the note is MANDATORY (the proposer
    deserves the why). Stamps clear; the paper trail stays in the activity rows."""
    from app.routers.support_desk.tickets import _get_ticket, _log_activity, dispatch_safe, _panel_base
    from app.routers.support_desk._common import _user_names
    t = _get_ticket(db, ticket_id, admin)
    _require_incident_lead(db, t, admin)
    if t.mi_proposed_at is None:
        raise HTTPException(409, "No pending major-incident proposal on this ticket.")
    note = (payload.note or "").strip()
    if not note:
        raise HTTPException(422, "A decline note is required — tell the proposer why this "
                                 "stays a normal incident.")
    proposer_id = t.mi_proposed_by_id
    names = _user_names(db, {proposer_id})
    t.mi_proposed_at = None
    t.mi_proposed_by_id = None
    t.mi_proposal_note = None
    _log_activity(db, t, admin, "mi_declined",
                  {"note": note, "proposed_by": names.get(str(proposer_id))})
    write_audit(db, entity_type="ticket", op="mi_declined", entity_id=t.id, actor_id=admin.id,
                request=request, details={"note": note,
                                          "proposed_by": str(proposer_id) if proposer_id else None})
    if proposer_id and str(proposer_id) != str(admin.id):
        dispatch_safe(db, EVT_INCIDENT_MI_DECLINED, proposer_id, t,
                      title=f"MI proposal declined on {t.ticket_number} — {note[:80]}",
                      action_url=f"{_panel_base(db, proposer_id)}/incidents/active")
    db.commit()
    return {"ok": True, "declined": True, "note": note}


@router.patch("/tickets/{ticket_id}/incident-roles")
def set_incident_roles(ticket_id: UUID, payload: IncidentRolesPatch, request: Request,
                       db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Assign the MI response roster (commander / comms lead / ops lead). The roster
    coordinates an ACTIVE response — 409 on terminal tickets (reopen first)."""
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe
    from app.routers.support_desk._common import _user_names
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "assign its incident command roster")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This incident is resolved/closed — there's no active response "
                                 "to staff. Reopen it first if the disruption recurred.")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — staff the surviving incident instead.")
    prev = {f: getattr(t, f) for f in ("incident_commander_id", "comms_lead_id", "ops_lead_id")}
    changes: dict = {}
    handoffs: list = []   # role fields where someone ALREADY holding the seat is replaced/stood down
    for field in ("incident_commander_id", "comms_lead_id", "ops_lead_id"):
        val = getattr(payload, field)
        if field in payload.clear:
            if prev[field] is not None:
                setattr(t, field, None)
                changes[field] = None
                handoffs.append(field)
        elif val is not None:
            u = db.query(User).filter(User.id == val, User.is_active == True).first()  # noqa: E712
            if not u:
                raise HTTPException(400, f"{field.replace('_', ' ')}: user not found or inactive")
            if prev[field] != val:
                setattr(t, field, val)
                changes[field] = str(val)
                if prev[field] is not None:
                    handoffs.append(field)
    if not changes:
        raise HTTPException(422, "Nothing to change — provide at least one role or a clear[] entry.")
    note = (payload.note or "").strip() or None
    # Handoff drop-gate: fresh staffing needs no ceremony, but replacing or standing down a
    # seated responder is a command handoff — it must carry a reason the chain log can show.
    if handoffs and not note:
        pretty = ", ".join(f.replace("_id", "").replace("_", " ") for f in handoffs)
        raise HTTPException(422, f"Changing a staffed seat ({pretty}) is a handoff — "
                                 "add a short reason note so the chain of command stays auditable.")
    names = _user_names(db, {t.incident_commander_id, t.comms_lead_id, t.ops_lead_id}
                        | {v for v in prev.values() if v})
    detail = {k: (names.get(v) if v else None) for k, v in changes.items()}
    if note:
        detail["note"] = note
    _log_activity(db, t, admin, "incident_roles_set", detail)
    for field, val in changes.items():
        role = field.replace("_id", "").replace("_", " ")
        if val and val != str(admin.id):
            dispatch_safe(db, EVT_INCIDENT_ROLES_ASSIGNED, UUID(val), t,
                          title=f"You are now {role} on {t.ticket_number}",
                          action_url="/user/support/incidents/major")
        # the outgoing holder learns they were relieved — silence here loses the baton
        if field in handoffs and prev[field] and str(prev[field]) != str(admin.id) \
                and str(prev[field]) != (val or ""):
            dispatch_safe(db, EVT_INCIDENT_ROLES_ASSIGNED, prev[field], t,
                          title=f"You've been stood down as {role} on {t.ticket_number}",
                          action_url="/user/support/incidents/major")
    write_audit(db, entity_type="ticket", op="incident_roles", entity_id=t.id, actor_id=admin.id,
                request=request, details={**changes, **({"note": note} if note else {})})
    db.commit()
    return {"ok": True, "note": note,
            "incident_commander_id": str(t.incident_commander_id) if t.incident_commander_id else None,
            "incident_commander_name": names.get(str(t.incident_commander_id)),
            "comms_lead_id": str(t.comms_lead_id) if t.comms_lead_id else None,
            "comms_lead_name": names.get(str(t.comms_lead_id)),
            "ops_lead_id": str(t.ops_lead_id) if t.ops_lead_id else None,
            "ops_lead_name": names.get(str(t.ops_lead_id))}


_ROLE_FIELDS = ("incident_commander_id", "comms_lead_id", "ops_lead_id")


@router.get("/tickets/{ticket_id}/roster-candidates")
def roster_candidates(ticket_id: UUID, q: Optional[str] = Query(None, max_length=80),
                      db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Everything the roster console needs in ONE sealed read: the staffing pool (the
    handling team's members — the people who actually hold the ticket seal — plus the
    assignee, the current holders and the caller), each enriched with directory info and
    their LIVE command load; who holds each seat now and since when; and the recent
    chain-of-command log (`incident_roles_set` activity incl. handoff notes).

    ``q`` (2+ chars) additionally searches the whole active directory as a typeahead,
    capped at 25 rows — same enumeration discipline as /teams/people for non-superusers.
    Read seal: _get_ticket scope (team seal) — the write stays actor-gated on PATCH."""
    from app.routers.support_desk.tickets import _get_ticket
    from app.routers.support_desk.tickets_self import _team_members_of
    from app.models.hr.employee import Employee
    from sqlalchemy.orm import joinedload
    t = _get_ticket(db, ticket_id, admin)
    if not (t.ticket_type == TicketType.INCIDENT.value or t.is_major_incident):
        raise HTTPException(422, "The response roster is for incidents — this ticket is "
                                 f"a {t.ticket_type} and not a major incident.")

    team = (db.query(SdTeam).filter(SdTeam.id == t.team_id, SdTeam.is_deleted == False).first()  # noqa: E712
            if t.team_id else None)
    pool_ids = _team_members_of(db, t.team_id)
    on_team = {str(x) for x in pool_ids}
    for extra in (t.assigned_agent_id, t.incident_commander_id, t.comms_lead_id,
                  t.ops_lead_id, admin.id):
        if extra:
            pool_ids.add(extra)

    users = (db.query(User).filter(User.id.in_(pool_ids), User.is_active == True).all()  # noqa: E712
             if pool_ids else [])
    if q and len(q.strip()) >= 2:
        like = f"%{q.strip()}%"
        found = (db.query(User)
                 .filter(User.is_active == True,  # noqa: E712
                         or_(User.full_name.ilike(like), User.email.ilike(like)))
                 .order_by(User.full_name).limit(25).all())
        have = {str(u.id) for u in users}
        users += [u for u in found if str(u.id) not in have]

    all_ids = [u.id for u in users]
    emp_by_user = {}
    if all_ids:
        for e in (db.query(Employee)
                  .options(joinedload(Employee.department), joinedload(Employee.designation))
                  .filter(Employee.user_id.in_(all_ids), Employee.is_deleted == False).all()):  # noqa: E712
            emp_by_user[str(e.user_id)] = e

    # Live command load — seats this person ALREADY holds on OTHER active incidents.
    load: dict = {}
    if all_ids:
        rows = (db.query(SdTicket.incident_commander_id, SdTicket.comms_lead_id, SdTicket.ops_lead_id)
                .filter(SdTicket.is_deleted == False,  # noqa: E712
                        SdTicket.id != t.id,
                        SdTicket.merged_into_id.is_(None),
                        SdTicket.status.notin_(TERMINAL_TICKET_STATUSES),
                        or_(SdTicket.ticket_type == TicketType.INCIDENT.value,
                            SdTicket.is_major_incident == True),  # noqa: E712
                        or_(SdTicket.incident_commander_id.in_(all_ids),
                            SdTicket.comms_lead_id.in_(all_ids),
                            SdTicket.ops_lead_id.in_(all_ids))).all())
        for cmd_id, comms_id, ops_id in rows:
            for uid in (cmd_id, comms_id, ops_id):
                if uid is not None:
                    load[str(uid)] = load.get(str(uid), 0) + 1

    member_roles = (team.member_roles or {}) if team else {}
    lead_id = str(team.lead_user_id) if team and team.lead_user_id else None

    def _cand(u: User) -> dict:
        uid = str(u.id)
        e = emp_by_user.get(uid)
        return {
            "id": uid,
            "name": u.full_name or u.email or "Agent",
            "email": u.email,
            "department": e.department.name if e and e.department else None,
            "designation": e.designation.name if e and e.designation else None,
            "is_agent": bool(getattr(u, "is_support_agent", False) or u.is_superuser),
            "is_lead": uid == lead_id or member_roles.get(uid) == "lead",
            "on_team": uid in on_team,
            "is_you": uid == str(admin.id),
            "is_assignee": t.assigned_agent_id is not None and uid == str(t.assigned_agent_id),
            "command_load": load.get(uid, 0),
        }

    candidates = sorted((_cand(u) for u in users),
                        key=lambda c: (not c["is_lead"], not c["on_team"],
                                       not c["is_agent"], (c["name"] or "").lower()))

    # Chain of command — the roles activity is the roster's audit trail. The most recent
    # entry touching a field IS when its current holder took (or left) the seat.
    acts = (db.query(SdTicketActivity)
            .filter(SdTicketActivity.ticket_id == t.id,
                    SdTicketActivity.action == "incident_roles_set")
            .order_by(SdTicketActivity.created_at.desc()).limit(40).all())
    held_since: dict = {}
    for f in _ROLE_FIELDS:
        if getattr(t, f) is None:
            held_since[f] = None
            continue
        stamp = None
        for a in acts:
            if f in (a.detail or {}):
                stamp = {"at": a.created_at.isoformat() if a.created_at else None,
                         "by": a.actor_name}
                break
        held_since[f] = stamp
    history = [{
        "at": a.created_at.isoformat() if a.created_at else None,
        "actor": a.actor_name,
        "changes": {k: v for k, v in (a.detail or {}).items() if k != "note"},
        "note": (a.detail or {}).get("note"),
    } for a in acts[:10]]

    holder_names = {c["id"]: c["name"] for c in candidates}
    missing = {getattr(t, f) for f in _ROLE_FIELDS
               if getattr(t, f) is not None and str(getattr(t, f)) not in holder_names}
    if missing:   # a deactivated user can still hold a seat — name them anyway
        from app.routers.support_desk._common import _user_names
        holder_names.update(_user_names(db, missing))
    holders = {}
    for f in _ROLE_FIELDS:
        v = getattr(t, f)
        holders[f] = None if v is None else {
            "id": str(v), "name": holder_names.get(str(v)),
            "since": (held_since.get(f) or {}).get("at") if held_since.get(f) else None,
            "staffed_by": (held_since.get(f) or {}).get("by") if held_since.get(f) else None,
        }

    return {"ok": True,
            "team": {"id": str(team.id), "name": team.name} if team else None,
            "assigned_agent_id": str(t.assigned_agent_id) if t.assigned_agent_id else None,
            "candidates": candidates,
            "holders": holders,
            "history": history}


_EXPOSURE_FLAGS = ("security_impact", "compliance_impact", "public_impact")


def _aware_utc(dt):
    """Timezone-pin a datetime for safe comparison (naive → UTC). None passes through."""
    if dt is None:
        return None
    return dt.replace(tzinfo=dt_timezone.utc) if dt.tzinfo is None else dt


@router.patch("/tickets/{ticket_id}/incident-impact")
def set_incident_impact(ticket_id: UUID, payload: IncidentImpactPatch, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Impact detail: affected services, real start/detect clocks, exposure flags.
    Allowed post-resolution too — the post-incident review refines these numbers.
    Discipline: clocks are validated against the EFFECTIVE pair (payload merged over the
    stored row) and can't sit in the future; a stamp that changes nothing is a 422;
    REVISING a value already on record needs a reason ``note`` (first stamps are free).
    Every stamp records a before→after diff on the activity row."""
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe,
    )
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "edit its incident impact detail")
    if not (t.ticket_type == TicketType.INCIDENT.value or t.is_major_incident):
        raise HTTPException(422, "Impact detail is for incidents — this ticket is "
                                 f"a {t.ticket_type} and not a major incident.")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — record impact on the surviving incident.")
    data = payload.model_dump(exclude_unset=True)
    note = (data.pop("note", None) or "").strip() or None
    if not data:
        raise HTTPException(422, "Nothing to change.")

    # Clock discipline. Future check FIRST — a future clock also breaks the pair check,
    # and "can't be in the future" is the message that names the actual mistake.
    horizon = _aware_utc(sla_util.now_utc()) + timedelta(minutes=10)
    for f, label in (("incident_started_at", "start"), ("incident_detected_at", "detection")):
        v = _aware_utc(data.get(f))
        if v and v > horizon:
            raise HTTPException(422, f"The {label} clock can't be in the future — "
                                     "impact clocks record what already happened.")
    # Pair check against the EFFECTIVE values (payload merged over the stored row) —
    # updating one clock alone can no longer leapfrog the other (the old check only
    # fired when BOTH were in the payload).
    eff = {f: _aware_utc(data[f] if f in data else getattr(t, f))
           for f in ("incident_started_at", "incident_detected_at")}
    if (eff["incident_started_at"] and eff["incident_detected_at"]
            and eff["incident_detected_at"] < eff["incident_started_at"]):
        raise HTTPException(422, "Detected time can't be before the disruption started.")

    def _norm(v):
        if isinstance(v, datetime):
            return _aware_utc(v)
        if isinstance(v, str) and not v.strip():
            return None
        return v

    changes: dict = {}
    for k, v in data.items():
        if _norm(getattr(t, k)) == _norm(v):
            continue
        changes[k] = {"from": getattr(t, k), "to": v}
        setattr(t, k, v)
    if not changes:
        raise HTTPException(422, "Nothing to change — the assessment already reads exactly this way.")

    # Revision drop-gate: the FIRST stamp of a field is free; overwriting a value that
    # was already on record is a post-facto correction and must carry a reason.
    def _stamped(v):
        return not (v is None or v is False or v == "" or v == [])
    revised = sorted(k for k, ch in changes.items() if _stamped(ch["from"]))
    if revised and not note:
        pretty = ", ".join(k.replace("incident_", "").replace("_", " ") for k in revised)
        raise HTTPException(422, f"Revising a stamped assessment ({pretty}) needs a short "
                                 "reason note — impact numbers must stay auditable.")

    detail = {"fields": sorted(changes.keys()),   # back-compat key older renderers may read
              "changes": {k: {"from": ch["from"], "to": ch["to"]} for k, ch in changes.items()}}
    if note:
        detail["note"] = note
    _log_activity(db, t, admin, "incident_impact_set", detail)

    # New exposure (security/compliance/public flipping ON) is command-relevant — the
    # commander hears about it the moment it lands, not at the next sync.
    exposure_on = [k for k in _EXPOSURE_FLAGS if k in changes and changes[k]["to"] is True]
    if exposure_on and t.incident_commander_id and str(t.incident_commander_id) != str(admin.id):
        pretty = " + ".join(k.replace("_impact", "") for k in exposure_on)
        dispatch_safe(db, EVT_INCIDENT_IMPACT, t.incident_commander_id, t,
                      title=f"Exposure declared on {t.ticket_number}: {pretty}",
                      action_url="/user/support/incidents/major")

    write_audit(db, entity_type="ticket", op="incident_impact", entity_id=t.id, actor_id=admin.id,
                request=request, details=_json_safe({**{k: data[k] for k in changes},
                                                     **({"note": note} if note else {})}))
    db.commit()
    return {"ok": True, "fields": sorted(changes.keys()), "note": note}


@router.post("/tickets/{ticket_id}/decision", status_code=201)
def log_incident_decision(ticket_id: UUID, payload: IncidentDecisionCreate, request: Request,
                          db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """The decision log: an immutable, typed activity row (who decided what, when).
    DR / failover / BCP invocations are RECORDED here — command accountability, not
    infra automation. 409 on terminal — decisions coordinate a live response."""
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "log a command decision on it")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This incident is resolved/closed — the decision log is sealed. "
                                 "Record follow-ups in the post-incident report instead.")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — log decisions on the surviving incident.")
    _log_activity(db, t, admin, "decision_logged",
                  {"kind": payload.kind, "decision": payload.decision.strip(),
                   "reason": (payload.reason or "").strip() or None,
                   "note": (payload.note or "").strip() or None})
    if t.incident_commander_id and t.incident_commander_id != admin.id:
        dispatch_safe(db, EVT_INCIDENT_DECISION, t.incident_commander_id, t,
                      title=f"Decision logged on {t.ticket_number}: {payload.decision.strip()[:80]}",
                      action_url="/user/support/incidents/major")
    write_audit(db, entity_type="ticket", op="decision", entity_id=t.id, actor_id=admin.id,
                request=request, details={"kind": payload.kind,
                                          "reason": (payload.reason or "").strip()[:300] or None,
                                          "decision": payload.decision.strip()[:500]})
    db.commit()
    return {"ok": True, "kind": payload.kind}


@router.patch("/tickets/{ticket_id}/incident-parent")
def set_incident_parent(ticket_id: UUID, payload: IncidentParentPatch, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Link this incident under a master incident (ONE level deep), or clear the link.
    The rollup coordinates a live response — 409 on terminal/merged children; the master
    must itself be a live, non-child incident. Distinct from merge (dedup tombstone):
    a child stays a separately-worked ticket that resolves on its own evidence."""
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "link it under a master incident")
    if not (t.ticket_type == TicketType.INCIDENT.value or t.is_major_incident):
        raise HTTPException(422, "Parent links are for incidents — this ticket is "
                                 f"a {t.ticket_type} and not a major incident.")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — link the surviving incident instead.")
    if t.status in TERMINAL_TICKET_STATUSES and payload.parent_id:
        raise HTTPException(409, "This incident is resolved/closed — reopen it before "
                                 "rolling it under a master incident.")

    if payload.parent_id is None:
        if not payload.clear:
            raise HTTPException(422, "Provide parent_id to link, or clear=true to unlink.")
        if t.parent_incident_id is None:
            raise HTTPException(422, "This incident has no master link to clear.")
        old_parent = db.get(SdTicket, t.parent_incident_id)
        note = (payload.note or "").strip() or None
        t.parent_incident_id = None
        _log_activity(db, t, admin, "incident_unlinked",
                      {"parent": getattr(old_parent, "ticket_number", None), "note": note})
        write_audit(db, entity_type="ticket", op="incident_unlinked", entity_id=t.id,
                    actor_id=admin.id, request=request,
                    details={"parent": getattr(old_parent, "ticket_number", None), "note": note})
        db.commit()
        return {"ok": True, "parent_incident_id": None, "parent_incident_number": None}

    if payload.parent_id == t.id:
        raise HTTPException(422, "An incident can't be its own master.")
    parent = _get_ticket(db, payload.parent_id, admin)   # 404 outside scope — no existence leak
    if not (parent.ticket_type == TicketType.INCIDENT.value or parent.is_major_incident):
        raise HTTPException(422, "The master must be an incident (or a declared major incident).")
    if parent.merged_into_id:
        raise HTTPException(409, "That master was merged — link to the surviving incident.")
    if parent.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "That incident is resolved/closed — a master must be live.")
    if parent.parent_incident_id is not None:
        raise HTTPException(422, "That incident is itself a child — links go one level deep. "
                                 "Link to its master instead.")
    has_children = db.query(SdTicket.id).filter(SdTicket.parent_incident_id == t.id,
                                                SdTicket.is_deleted == False).first()  # noqa: E712
    if has_children:
        raise HTTPException(422, "This incident has children of its own — links go one level "
                                 "deep. Unlink its children first.")
    note = (payload.note or "").strip() or None
    t.parent_incident_id = parent.id
    _log_activity(db, t, admin, "incident_linked", {"parent": parent.ticket_number, "note": note})
    _log_activity(db, parent, admin, "child_incident_linked", {"child": t.ticket_number, "note": note})
    if parent.incident_commander_id and parent.incident_commander_id != admin.id:
        dispatch_safe(db, EVT_INCIDENT_DECISION, parent.incident_commander_id, parent,
                      title=f"{t.ticket_number} rolled under {parent.ticket_number}",
                      action_url="/user/support/incidents/active")
    write_audit(db, entity_type="ticket", op="incident_linked", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"parent": parent.ticket_number, "child": t.ticket_number, "note": note})
    db.commit()
    return {"ok": True, "parent_incident_id": str(parent.id),
            "parent_incident_number": parent.ticket_number}


# ═══════════════════════════════ Response playbooks / incident tasks ═══════════════════════════════
# Tasks carry no due dates — the cadence sweep already polices response tempo, so there
# is deliberately NO task-overdue cron. There is also NO DELETE: 'skipped' is the
# tombstone, keeping the response checklist an honest paper trail.

def _next_task_seq(db: Session, ticket_id) -> int:
    mx = (db.query(func.max(SdIncidentTask.seq))
          .filter(SdIncidentTask.ticket_id == ticket_id).scalar())
    return (int(mx) + 10) if mx is not None else 10


def _task_list_response(db: Session, t: SdTicket) -> IncidentTaskListResponse:
    """Ordered checklist + counts. progress = done/(open+done) — skipped rows are
    tombstones and never count against progress."""
    from app.routers.support_desk._common import _user_names
    rows = (db.query(SdIncidentTask)
            .filter(SdIncidentTask.ticket_id == t.id)
            .order_by(SdIncidentTask.seq.asc(), SdIncidentTask.created_at.asc())
            .limit(500).all())
    names = _user_names(db, {r.owner_id for r in rows} | {r.done_by_id for r in rows})
    items = []
    n_open = n_done = n_skip = 0
    for r in rows:
        if r.status == "done":
            n_done += 1
        elif r.status == "skipped":
            n_skip += 1
        else:
            n_open += 1
        item = IncidentTaskItem.model_validate(r)
        item.owner_name = names.get(str(r.owner_id))
        item.done_by_name = names.get(str(r.done_by_id))
        items.append(item)
    countable = n_open + n_done
    return IncidentTaskListResponse(
        total=len(rows), open=n_open, done=n_done, skipped=n_skip,
        progress_pct=round(n_done / countable * 100.0, 1) if countable else None,
        items=items)


@router.get("/tickets/{ticket_id}/tasks", response_model=IncidentTaskListResponse)
def list_incident_tasks(ticket_id: UUID, db: Session = Depends(get_db),
                        admin: User = Depends(get_support_agent)):
    """The incident's response checklist (playbook + ad-hoc tasks), seq-ordered.
    Sealed single fetch; 422 outside the incident lens."""
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    _require_incident(t)
    return _task_list_response(db, t)


@router.post("/tickets/{ticket_id}/tasks", response_model=IncidentTaskItem, status_code=201)
def add_incident_task(ticket_id: UUID, payload: IncidentTaskCreate, request: Request,
                      db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Add one ad-hoc response task. Owner-tier; live incidents only (409 terminal/merged) —
    post-resolution follow-ups belong in the PIR action registers instead."""
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe, _panel_base)
    from app.routers.support_desk._common import _user_names
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "add a response task")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — work the surviving incident's checklist.")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This incident is resolved/closed — the response checklist "
                                 "coordinates a live response. Track follow-ups in the "
                                 "post-incident report instead.")
    _require_incident(t)
    owner_id = payload.owner_id
    if owner_id is not None:
        u = db.query(User).filter(User.id == owner_id, User.is_active == True).first()  # noqa: E712
        if not u:
            raise HTTPException(400, "owner: user not found or inactive")
    task = SdIncidentTask(
        ticket_id=t.id, seq=_next_task_seq(db, t.id),
        title=payload.title.strip(), note=(payload.note or "").strip() or None,
        owner_id=owner_id, status="open", created_by_id=admin.id)
    db.add(task)
    db.flush()
    names = _user_names(db, {owner_id}) if owner_id else {}
    _log_activity(db, t, admin, "task_added",
                  {"task": task.title[:120],
                   **({"owner": names.get(str(owner_id))} if owner_id else {})})
    write_audit(db, entity_type="ticket", op="task_added", entity_id=t.id, actor_id=admin.id,
                request=request, details={"task": task.title[:200]})
    if owner_id and str(owner_id) != str(admin.id):
        dispatch_safe(db, EVT_INCIDENT_TASK_ASSIGNED, owner_id, t,
                      title=f"Response task assigned on {t.ticket_number}: {task.title[:80]}",
                      action_url=f"{_panel_base(db, owner_id)}/incidents/critical")
    db.commit()
    db.refresh(task)
    out = IncidentTaskItem.model_validate(task)
    out.owner_name = names.get(str(owner_id)) if owner_id else None
    return out


@router.post("/tickets/{ticket_id}/tasks/apply-template",
             response_model=IncidentTaskListResponse, status_code=201)
def apply_incident_playbook(ticket_id: UUID, payload: PlaybookApplyRequest, request: Request,
                            db: Session = Depends(get_db),
                            admin: User = Depends(get_support_agent)):
    """Apply a curated playbook: SNAPSHOTS its task titles onto the ticket (stamped with
    template_key — later library edits never rewrite history). Idempotent: 409 while any
    non-skipped row of the same playbook is still on the board (skip them all to re-apply)."""
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor, _log_activity
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "apply a response playbook")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — work the surviving incident's checklist.")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This incident is resolved/closed — playbooks drive a live "
                                 "response. Track follow-ups in the post-incident report instead.")
    _require_incident(t)
    key = payload.template_key.strip()
    pb = INCIDENT_PLAYBOOKS.get(key)
    if not pb:
        raise HTTPException(422, f"Unknown playbook '{key}' — one of: "
                                 + ", ".join(INCIDENT_PLAYBOOKS))
    existing = (db.query(SdIncidentTask.id)
                .filter(SdIncidentTask.ticket_id == t.id,
                        SdIncidentTask.template_key == key,
                        SdIncidentTask.status != "skipped").first())
    if existing:
        raise HTTPException(409, f"The '{pb['label']}' playbook is already on this incident — "
                                 "check off the existing tasks instead of stacking duplicates.")
    seq = _next_task_seq(db, t.id)
    for i, title in enumerate(pb["tasks"]):
        db.add(SdIncidentTask(ticket_id=t.id, seq=seq + i * 10, title=title,
                              status="open", template_key=key, created_by_id=admin.id))
    _log_activity(db, t, admin, "playbook_applied",
                  {"template_key": key, "label": pb["label"], "count": len(pb["tasks"])})
    write_audit(db, entity_type="ticket", op="playbook_applied", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"template_key": key, "count": len(pb["tasks"])})
    db.commit()
    return _task_list_response(db, t)


@router.patch("/tickets/{ticket_id}/tasks/{task_id}", response_model=IncidentTaskItem)
def patch_incident_task(ticket_id: UUID, task_id: UUID, payload: IncidentTaskPatch,
                        request: Request, db: Session = Depends(get_db),
                        admin: User = Depends(get_support_agent)):
    """Task check-off / staffing / edit. Actor: superuser, the task's NAMED OWNER (they may
    close their own item even off-roster — mirror of the PIR-action carve-out), or
    owner-tier on the ticket.

    Transitions: open→done free (stamps done_at/by) · open→skipped needs a status note ·
    done→open needs a correction note · skipped→open free · done→skipped 422 (reopen
    first) · skipped→done 422 (reopen first) · same-status 422. STATUS MOVES STAY ALLOWED
    POST-RESOLUTION (follow-through, like PIR actions); owner changes on a terminal/merged
    ticket 409 — staffing coordinates a live response. No DELETE — skipped is the tombstone."""
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe, _panel_base)
    from app.routers.support_desk._common import _user_names
    t = _get_ticket(db, ticket_id, admin)
    task = (db.query(SdIncidentTask)
            .filter(SdIncidentTask.id == task_id, SdIncidentTask.ticket_id == t.id).first())
    if not task:
        raise HTTPException(404, "Response task not found on this ticket")
    if str(task.owner_id or "") != str(admin.id) and not getattr(admin, "is_superuser", False):
        _require_ticket_actor(db, t, admin, "update its response tasks")

    changes: dict = {}
    # ── staffing ──
    if payload.clear_owner or payload.owner_id is not None:
        if t.status in TERMINAL_TICKET_STATUSES:
            raise HTTPException(409, "This incident is resolved/closed — task staffing "
                                     "coordinates a live response (status check-offs stay open).")
        if t.merged_into_id:
            raise HTTPException(409, "This ticket was merged — staff tasks on the surviving incident.")
        if payload.clear_owner:
            if task.owner_id is not None:
                task.owner_id = None
                changes["owner"] = None
        else:
            u = db.query(User).filter(User.id == payload.owner_id,
                                      User.is_active == True).first()  # noqa: E712
            if not u:
                raise HTTPException(400, "owner: user not found or inactive")
            if task.owner_id != payload.owner_id:
                task.owner_id = payload.owner_id
                changes["owner"] = str(payload.owner_id)

    # ── body edits ──
    if payload.title is not None and payload.title.strip() and payload.title.strip() != task.title:
        task.title = payload.title.strip()
        changes["title"] = task.title[:120]
    if payload.note is not None:
        new_note = payload.note.strip() or None
        if new_note != task.note:
            task.note = new_note
            changes["note"] = True

    # ── status transition ──
    status_changed = False
    if payload.status is not None:
        old, new = task.status, payload.status
        # the reason: status_note is canonical; a bare `note` is accepted as fallback
        reason = (payload.status_note or "").strip() or (payload.note or "").strip()
        if old == new:
            raise HTTPException(422, f"This task is already {new}.")
        if old == "done" and new == "skipped":
            raise HTTPException(422, "A completed task can't be skipped — reopen it first "
                                     "if it was done in error.")
        if old == "skipped" and new == "done":
            raise HTTPException(422, "A skipped task must be reopened before it can be completed.")
        if old == "open" and new == "skipped" and not reason:
            raise HTTPException(422, "Skipping a task needs a short status note — say why "
                                     "it doesn't apply here.")
        if old == "done" and new == "open" and not reason:
            raise HTTPException(422, "Reopening a completed task is a correction — add a "
                                     "short note saying why.")
        task.status = new
        if new == "done":
            task.done_at = sla_util.now_utc()
            task.done_by_id = admin.id
        else:   # leaving done clears the completion stamp
            task.done_at = None
            task.done_by_id = None
        task.status_note = reason[:300] if reason else None
        status_changed = True
        _log_activity(db, t, admin, "task_status",
                      {"task": task.title[:120], "from": old, "to": new,
                       **({"note": reason[:300]} if reason else {})})
        changes["status"] = {"from": old, "to": new}

    if not changes and not status_changed:
        raise HTTPException(422, "Nothing to change.")

    names = _user_names(db, {task.owner_id, task.done_by_id})
    if "owner" in changes:
        _log_activity(db, t, admin, "task_assigned",
                      {"task": task.title[:120],
                       "owner": names.get(str(task.owner_id)) if task.owner_id else None})
        if task.owner_id and str(task.owner_id) != str(admin.id):
            dispatch_safe(db, EVT_INCIDENT_TASK_ASSIGNED, task.owner_id, t,
                          title=f"Response task assigned on {t.ticket_number}: {task.title[:80]}",
                          action_url=f"{_panel_base(db, task.owner_id)}/incidents/critical")
    write_audit(db, entity_type="ticket", op="task_updated", entity_id=t.id, actor_id=admin.id,
                request=request, details=_json_safe({"task_id": str(task.id), **changes}))
    db.commit()
    db.refresh(task)
    out = IncidentTaskItem.model_validate(task)
    out.owner_name = names.get(str(task.owner_id))
    out.done_by_name = names.get(str(task.done_by_id))
    return out


# ═══════════════════════════════ Severity reclassification ═══════════════════════════════
@router.post("/tickets/{ticket_id}/sev")
def change_incident_sev(ticket_id: UUID, payload: IncidentSevChange, request: Request,
                        db: Session = Depends(get_db),
                        admin: User = Depends(get_support_agent)):
    """Severity reclassification on a NON-MI incident (SEV is derived from priority —
    this verb moves priority through the front door with a mandatory case note).

    • target_sev=2 (promote → priority 'critical'): OWNER-TIER — raising the alarm is
      safe to over-do (mirrors the MI-propose asymmetry). 422 if already critical.
    • target_sev=3 (de-escalate → priority 'high'): LEAD/SUPERUSER — removing the desk's
      eyes carries the decline-an-MI authority bar. 422 unless currently critical.
    SEV1 stays on the major-incident verbs (409 here). SLA re-arm mirrors the generic
    PATCH exactly: deadlines recompute ONLY while first_responded_at is None."""
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe, _panel_base)
    from app.routers.support_desk._common import resolve_sla_package
    t = _get_ticket(db, ticket_id, admin)
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — reclassify the surviving incident.")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This incident is resolved/closed — its severity is settled "
                                 "history. Reopen it first if the disruption recurred.")
    if t.is_major_incident:
        raise HTTPException(409, "SEV1 is the major-incident flag — declare or stand it down "
                                 "via the major-incident verbs, not a severity change.")
    _require_incident(t)
    note = payload.note.strip()
    old_sev = ticket_sev(t.priority, False)
    old_pri = t.priority
    if payload.target_sev == 2:
        _require_ticket_actor(db, t, admin, "promote it to SEV2")
        if t.priority == "critical":
            raise HTTPException(422, "Already SEV2 (priority critical) — nothing to promote.")
        new_pri = "critical"
    else:
        try:
            _require_incident_lead(db, t, admin)
        except HTTPException:
            raise HTTPException(403, "De-escalating a critical is a team-lead/admin call — "
                                     "it takes the desk's eyes off the incident.")
        if t.priority != "critical":
            raise HTTPException(422, "Only a SEV2 (priority critical) incident can be "
                                     f"de-escalated — this one runs at '{t.priority}'.")
        new_pri = "high"
    t.priority = new_pri
    # SLA re-arm — the generic PATCH's exact rule: recompute deadlines only before the
    # first response; after it the clocks are history and stay untouched.
    if t.first_responded_at is None:
        pkg = resolve_sla_package(db, t.sla_package_id, t.organization_id)
        rd, rsd = sla_util.compute_deadlines(pkg, t.priority, start=t.created_at)
        t.sla_package_id = pkg.id if pkg else t.sla_package_id
        t.response_due_at, t.resolution_due_at = rd, rsd
    new_sev = payload.target_sev
    _log_activity(db, t, admin, "incident_sev_changed",
                  {"from_sev": old_sev, "to_sev": new_sev,
                   "from_priority": old_pri, "to_priority": new_pri, "note": note})
    write_audit(db, entity_type="ticket", op="sev_changed", entity_id=t.id, actor_id=admin.id,
                request=request, details={"from_sev": old_sev, "to_sev": new_sev,
                                          "from_priority": old_pri, "to_priority": new_pri,
                                          "note": note[:500]})
    for uid in {t.assigned_agent_id, t.incident_commander_id} - {None, admin.id}:
        dispatch_safe(db, EVT_INCIDENT_SEV_CHANGED, uid, t,
                      title=(f"Severity raised to SEV2 — {t.ticket_number}" if new_sev == 2
                             else f"Severity lowered to SEV3 — {t.ticket_number}"),
                      action_url=f"{_panel_base(db, uid)}/incidents/critical")
    db.commit()
    return {"ok": True, "from_sev": old_sev, "sev": new_sev, "priority": t.priority,
            "note": note,
            "response_due_at": t.response_due_at.isoformat() if t.response_due_at else None,
            "resolution_due_at": t.resolution_due_at.isoformat() if t.resolution_due_at else None}


@router.post("/tickets/{ticket_id}/pir", response_model=PirResponse, status_code=201)
def create_pir(ticket_id: UUID, request: Request, payload: PirCreate | None = None,
               db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Open the post-incident review. One PIR per incident — idempotent (409 carries the
    existing report id). Snapshots the activity trail at creation."""
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor, _log_activity
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "open its post-incident review")
    if not (t.ticket_type == TicketType.INCIDENT.value or t.is_major_incident):
        raise HTTPException(422, "Post-incident reports are for incidents — this ticket is "
                                 f"a {t.ticket_type} and not a major incident.")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — review the surviving incident instead.")
    existing = (db.query(SdIncidentReport)
                .filter(SdIncidentReport.ticket_id == t.id,
                        SdIncidentReport.is_deleted == False).first())  # noqa: E712
    if existing:
        raise HTTPException(409, f"A post-incident report already exists for this incident "
                                 f"({existing.report_number}).")
    p = SdIncidentReport(
        ticket_id=t.id,
        report_number=_generate_pir_number(db),
        title=(payload.title.strip() if payload and payload.title and payload.title.strip()
               else f"Post-Incident Review — {t.subject}"[:300]),
        status=PirStatus.DRAFT.value,
        timeline_snapshot=snapshot_timeline(db, t.id),
        business_impact=t.business_impact,
        root_cause=t.rca_summary or None,
        created_by_id=admin.id,
    )
    db.add(p)
    db.flush()
    _log_activity(db, t, admin, "pir_created", {"pir": p.report_number})
    write_audit(db, entity_type="pir", op="created", entity_id=p.id, actor_id=admin.id,
                request=request, details={"report": p.report_number, "ticket": t.ticket_number})
    db.commit()
    db.refresh(p)
    return _enrich_pir(db, p, PirResponse.model_validate(p))
