"""Support Desk — Incident Management helpers (Fault Grid / Command Funnel desks).

Single source of truth for:
  • ``ticket_sev()``        — the DERIVED SEV1–SEV4 classification (never stored:
    SEV1 = major incident · SEV2 = priority critical · SEV3 = urgent/high ·
    SEV4 = medium/low). Mirrored client-side in useSupportDesk.sevOf().
  • lens conditions         — the module's sealed lenses over ``support_tickets``
    (active / major / critical / all). Composed WITH the team seal, never instead.
  • ``snapshot_timeline()`` — freezes a ticket's activity trail into a PIR.
  • ``render_pir_pdf()``    — WeasyPrint dossier export (lazy import + GTK bootstrap,
    same discipline as the HR attendance reports — see CLAUDE.md).
  • ``sweep_pir_missing()`` — nudges the commander/owner of terminal major incidents
    that aged past PIR_REQUIRED_AFTER_DAYS with no PIR draft (24h-throttled).
"""
from __future__ import annotations

import html as html_mod
from datetime import timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.support_desk.constants import (
    SEV_FROM_PRIORITY, TERMINAL_TICKET_STATUSES, PIR_REQUIRED_AFTER_DAYS,
    EVT_PIR_OVERDUE, TicketType,
)
from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.incident import SdIncidentReport
from app.utils.support_desk import sla as sla_util


# ─────────────────────────────── SEV classification ───────────────────────────────

def ticket_sev(priority: str | None, is_major_incident: bool) -> int:
    """SEV1..SEV4 for a ticket. MI trumps priority; unknown priorities read as SEV4."""
    if is_major_incident:
        return 1
    return SEV_FROM_PRIORITY.get((priority or "").lower(), 4)


def sev_cond(sev: int):
    """SQLAlchemy condition matching tickets of exactly SEV ``sev`` (see ticket_sev)."""
    mi = SdTicket.is_major_incident == True  # noqa: E712
    if sev == 1:
        return mi
    pris = [p for p, s in SEV_FROM_PRIORITY.items() if s == sev]
    return and_(SdTicket.is_major_incident == False, SdTicket.priority.in_(pris))  # noqa: E712


# ─────────────────────────────── Posture flags ───────────────────────────────
# Single truth for the board's response-posture flags. The list endpoint's ``flag``
# param and the stats rollup BOTH build from these, so a chip's count can never
# drift from the rows its click returns.

AT_RISK_HOURS = 4   # "due soon" horizon (client twin: useSupportDesk.AT_RISK_HOURS)

# exposure_* / unassessed are the Critical desks' exposure chips — counted in
# stats.critical.exposure with the SAME predicates so chips ⇔ rows can never drift.
INCIDENT_FLAGS = ("unacked", "at_risk", "breached", "unowned",
                  "cmdr_unstaffed", "update_overdue", "mi_proposed",
                  "exposure_revenue", "exposure_compliance", "exposure_security",
                  "exposure_public", "unassessed")


def breached_cond():
    return or_(SdTicket.sla_response_breached == True,   # noqa: E712
               SdTicket.sla_resolution_breached == True)  # noqa: E712


def at_risk_cond(now):
    """Live resolution deadline inside the warning window — never breached/paused rows."""
    return and_(~breached_cond(),
                SdTicket.sla_paused_since.is_(None),
                SdTicket.resolution_due_at.isnot(None),
                SdTicket.resolution_due_at > now,
                SdTicket.resolution_due_at <= now + timedelta(hours=AT_RISK_HOURS))


def flag_condition(flag: str, now):
    """Condition for one posture flag (422-validate against INCIDENT_FLAGS upstream)."""
    if flag == "unacked":   # ack debt only matters at SEV1/SEV2
        return and_(SdTicket.acknowledged_at.is_(None),
                    or_(SdTicket.is_major_incident == True,  # noqa: E712
                        SdTicket.priority == "critical"))
    if flag == "at_risk":
        return at_risk_cond(now)
    if flag == "breached":
        return breached_cond()
    if flag == "unowned":
        return SdTicket.assigned_agent_id.is_(None)
    if flag == "cmdr_unstaffed":
        return and_(SdTicket.is_major_incident == True,  # noqa: E712
                    SdTicket.incident_commander_id.is_(None))
    if flag == "update_overdue":
        return and_(SdTicket.next_update_due_at.isnot(None),
                    SdTicket.next_update_due_at < now)
    if flag == "mi_proposed":   # MI candidates awaiting a lead/admin call
        return and_(SdTicket.mi_proposed_at.isnot(None),
                    SdTicket.is_major_incident == False)  # noqa: E712
    # ── exposure chips (Critical desks). Column types: revenue_impact = free-text
    # String(160) → "flagged" means non-null AND non-empty; compliance/security/
    # public_impact = non-null Booleans; business_impact = validated vocab or NULL;
    # affected_users = nullable Integer. ──
    if flag == "exposure_revenue":
        return and_(SdTicket.revenue_impact.isnot(None), SdTicket.revenue_impact != "")
    if flag == "exposure_compliance":
        return SdTicket.compliance_impact == True  # noqa: E712
    if flag == "exposure_security":
        return SdTicket.security_impact == True  # noqa: E712
    if flag == "exposure_public":
        return SdTicket.public_impact == True  # noqa: E712
    if flag == "unassessed":    # nobody has sized the blast radius at all yet
        return and_(SdTicket.business_impact.is_(None),
                    SdTicket.compliance_impact == False,  # noqa: E712
                    SdTicket.security_impact == False,    # noqa: E712
                    SdTicket.public_impact == False,      # noqa: E712
                    SdTicket.affected_users.is_(None))
    raise ValueError(f"unknown incident flag: {flag}")


# ─────────────────────────────── Module lenses ───────────────────────────────
# "Incident" for this module = incident-TYPE tickets ∪ anything flagged a major
# incident (an MI is an incident by definition, whatever its stored type). Every
# lens excludes archived records; merge-tombstones are excluded from ACTIVE lenses
# and stats but stay visible in ALL for the paper trail.

def incident_lens_cond():
    return or_(SdTicket.ticket_type == TicketType.INCIDENT.value,
               SdTicket.is_major_incident == True)  # noqa: E712


def lens_condition(lens: str):
    """Condition for one of the module's boards. Compose with the team seal."""
    base = and_(SdTicket.is_deleted == False, incident_lens_cond())  # noqa: E712
    live = SdTicket.merged_into_id.is_(None)
    lens = (lens or "active").lower()
    if lens == "major":
        return and_(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.is_major_incident == True, live)  # noqa: E712
    if lens == "critical":   # SEV1 ∪ SEV2
        return and_(base, live, or_(SdTicket.is_major_incident == True,  # noqa: E712
                                    SdTicket.priority == "critical"))
    if lens == "all":
        return base
    # active — the open response floor (on_hold included: parked ≠ finished)
    return and_(base, live, SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))


# ─────────────────────────────── Phase clocks ───────────────────────────────
# ServiceNow-style incident phase timeline, fully DERIVED — no stored phase column.
# started/detected come off the impact clocks, declared off the major_incident
# activity, acknowledged/resolved/closed off the ticket stamps, and first_mitigation
# off the decision log (mitigation-family kinds) ∪ status updates whose lifecycle
# phase says the fix is in flight. Order in PHASE_KEYS is the canonical track order.

MITIGATION_DECISION_KINDS = ("mitigation", "rollback", "failover",
                             "activate_dr", "invoke_bcp")
MITIGATION_UPDATE_PHASES = ("mitigating", "monitoring")

PHASE_KEYS = ("started", "detected", "declared", "acknowledged",
              "first_mitigation", "resolved", "closed")
PHASE_LABELS = {
    "started": "Started", "detected": "Detected", "declared": "Declared",
    "acknowledged": "Acknowledged", "first_mitigation": "Mitigating",
    "resolved": "Resolved", "closed": "Closed",
}


def _mins_between(a, b) -> float | None:
    a, b = sla_util._aware(a), sla_util._aware(b)
    if a is None or b is None:
        return None
    return round(max(0.0, (b - a).total_seconds() / 60.0), 1)


def build_phase_track(db: Session, t: SdTicket) -> dict:
    """Derive the incident's phase timeline + inter-phase durations.

    Returns {"phases": [{key,label,at,source}...] (all 7, at=None when unreached),
             "durations_minutes": {"<from>_to_<to>": mins for consecutive PRESENT
                                   pairs, "total": detected→resolved}}.
    One bounded activity read; everything else is ticket columns."""
    rows = (db.query(SdTicketActivity)
            .filter(SdTicketActivity.ticket_id == t.id,
                    SdTicketActivity.action.in_(
                        ("major_incident", "decision_logged", "status_update")))
            .order_by(SdTicketActivity.created_at.asc())
            .limit(500).all())
    declared_at = None
    first_mitigation = None
    for r in rows:
        d = r.detail or {}
        if r.action == "major_incident" and declared_at is None and d.get("on") is True:
            declared_at = r.created_at
        if first_mitigation is None:
            if (r.action == "decision_logged"
                    and str(d.get("kind") or "") in MITIGATION_DECISION_KINDS):
                first_mitigation = r.created_at
            elif (r.action == "status_update"
                    and str(d.get("phase") or "") in MITIGATION_UPDATE_PHASES):
                first_mitigation = r.created_at

    at = {
        "started": t.incident_started_at,
        "detected": t.incident_detected_at or t.created_at,
        "declared": declared_at,
        "acknowledged": t.acknowledged_at,
        "first_mitigation": first_mitigation,
        "resolved": t.resolved_at,
        "closed": t.closed_at,
    }
    source = {
        "detected": "incident_detected_at" if t.incident_detected_at else "created_at",
        "declared": "activity", "first_mitigation": "activity",
    }
    phases = [{
        "key": k, "label": PHASE_LABELS[k],
        "at": sla_util._aware(at[k]),
        "source": source.get(k, "ticket"),
    } for k in PHASE_KEYS]

    durations: dict[str, float] = {}
    prev = None
    for p in phases:
        if p["at"] is None:
            continue
        if prev is not None and p["at"] >= prev["at"]:
            mins = _mins_between(prev["at"], p["at"])
            if mins is not None:
                durations[f'{prev["key"]}_to_{p["key"]}'] = mins
        prev = p
    total = _mins_between(at["detected"], t.resolved_at)
    if total is not None:
        durations["total"] = total
    return {"phases": phases, "durations_minutes": durations}


# ─────────────────────────────── PIR action items ───────────────────────────────
# The corrective/preventive registers live as JSONB inside each PIR. One shared
# iterator feeds the /incidents/actions rollup, the stats counter and the overdue
# sweep so their notions of "open" and "overdue" can never drift.

def iter_scoped_actions(db: Session, cond, limit: int = 500):
    """Yield (pir, ticket, kind, index, action_dict) for every action item on
    non-deleted PIRs whose linked ticket matches ``cond`` (the team seal —
    None = whole desk). Bounded fold: newest-updated ``limit`` PIRs."""
    q = (db.query(SdIncidentReport, SdTicket)
         .join(SdTicket, SdTicket.id == SdIncidentReport.ticket_id)
         .filter(SdIncidentReport.is_deleted == False,  # noqa: E712
                 SdTicket.is_deleted == False))  # noqa: E712
    if cond is not None:
        q = q.filter(cond)
    rows = q.order_by(SdIncidentReport.updated_at.desc().nullslast()).limit(limit).all()
    for pir, ticket in rows:
        for kind, reg in (("corrective", pir.corrective_actions or []),
                          ("preventive", pir.preventive_actions or [])):
            for idx, a in enumerate(reg):
                if isinstance(a, dict) and str(a.get("action") or "").strip():
                    yield pir, ticket, kind, idx, a


def action_is_overdue(a: dict, today_iso: str) -> bool:
    """Open + target_date strictly before today (ISO date-string compare)."""
    if str(a.get("status") or "open").lower() == "done":
        return False
    td = str(a.get("target_date") or "")[:10]
    return bool(td) and td < today_iso


def sweep_pir_actions_overdue(db: Session) -> int:
    """Chase open PIR action items whose target_date lapsed — one DIGEST nudge per
    ticket per 24h (deduped on the last 'pir_action_overdue' activity), sent to the
    action owner, else the commander, else the assignee. Only approved/published
    reports are chased: while a PIR is in draft/review, the register is still being
    written. Registered in tasks_cron (step 15d)."""
    from app.routers.support_desk._common import _notify_safe
    from app.models.support_desk.constants import EVT_PIR_ACTION_OVERDUE, PirStatus
    nowt = sla_util.now_utc()
    today_iso = nowt.date().isoformat()
    day_ago = nowt - timedelta(days=1)
    per_ticket: dict = {}
    for pir, ticket, kind, idx, a in iter_scoped_actions(db, None, limit=300):
        if pir.status not in (PirStatus.APPROVED.value, PirStatus.PUBLISHED.value):
            continue
        if action_is_overdue(a, today_iso):
            per_ticket.setdefault(ticket.id, {"ticket": ticket, "items": []})
            per_ticket[ticket.id]["items"].append((pir, kind, idx, a))
    if not per_ticket:
        return 0
    recent = {str(r[0]) for r in (db.query(SdTicketActivity.ticket_id)
              .filter(SdTicketActivity.ticket_id.in_(list(per_ticket.keys())),
                      SdTicketActivity.action == "pir_action_overdue",
                      SdTicketActivity.created_at > day_ago).all())}
    n = 0
    for tid, bundle in per_ticket.items():
        if str(tid) in recent:
            continue  # already chased today
        t = bundle["ticket"]
        items = bundle["items"]
        first_pir, _, _, first = items[0]
        owner = first.get("owner_id")
        recipient = owner or t.incident_commander_id or t.assigned_agent_id
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System",
            action="pir_action_overdue",
            detail={"auto": True, "count": len(items),
                    "pir": first_pir.report_number,
                    "sample": str(first.get("action") or "")[:120]}))
        _notify_safe(db, EVT_PIR_ACTION_OVERDUE, recipient, t,
                     title=(f"{len(items)} post-incident action item(s) overdue "
                            f"on {t.ticket_number}"),
                     action_url="/user/support/incidents/post-incident")
        n += 1
    if n:
        db.commit()
    return n


# ─────────────────────────────── PIR debt (single truth) ───────────────────────────────
# One predicate family for "this closure OWES a formal review" — shared by the PIR
# board's owed lens, /incidents/stats and sweep_pir_missing, so a chip's count, its
# click's rows and the nudge sweep can never disagree (rca.py discipline).

def pir_absent_cond():
    """No live PIR on the ticket (session-less scalar subquery — composable anywhere)."""
    has_pir = (select(SdIncidentReport.ticket_id)
               .where(SdIncidentReport.is_deleted == False)  # noqa: E712
               .scalar_subquery())
    return SdTicket.id.notin_(has_pir)


def pir_eligible_cond(now=None, days: int = 90, major_only: bool = False):
    """Terminal incident-lens closures whose severity warrants a formal review —
    SEV1 (major) ∪ SEV2 (priority critical); ``major_only=True`` narrows to SEV1
    (the sweep's nudge population). Windowed on the terminal stamp (default 90d,
    ``days=0`` disables) so ancient pre-program closures never flood the program.
    Merged tickets are excluded — the surviving incident carries the review."""
    nowt = now or sla_util.now_utc()
    stamp = func.coalesce(SdTicket.resolved_at, SdTicket.closed_at, SdTicket.created_at)
    sev_pick = (SdTicket.is_major_incident == True if major_only  # noqa: E712
                else or_(SdTicket.is_major_incident == True,      # noqa: E712
                         SdTicket.priority == "critical"))
    conds = [
        SdTicket.is_deleted == False,  # noqa: E712
        or_(SdTicket.ticket_type == TicketType.INCIDENT.value,
            SdTicket.is_major_incident == True),  # noqa: E712
        SdTicket.merged_into_id.is_(None),
        SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES)),
        sev_pick,
    ]
    if days:
        conds.append(stamp >= nowt - timedelta(days=days))
    return and_(*conds)


def pir_owed_cond(now=None, days: int = 90, major_only: bool = False):
    """Eligible closure with NO live PIR — the debt lens itself."""
    return and_(pir_eligible_cond(now, days, major_only), pir_absent_cond())


def pir_metrics_snapshot(db: Session, t: SdTicket) -> dict:
    """The FROZEN metrics record stamped onto a PIR at submit (ServiceNow parity —
    the published record must never drift against live recomputation). Clocks come
    from build_phase_track; counts are three bounded scalar scans."""
    from app.models.support_desk.ticket import SdTicketActivity as _Act
    track = build_phase_track(db, t)
    at = {p["key"]: p["at"] for p in track["phases"]}
    durations = track["durations_minutes"]

    def _iso(v):
        return v.isoformat() if v else None

    decisions = (db.query(func.count(_Act.id))
                 .filter(_Act.ticket_id == t.id, _Act.action == "decision_logged")
                 .scalar() or 0)
    updates = (db.query(func.count(_Act.id))
               .filter(_Act.ticket_id == t.id, _Act.action == "status_update")
               .scalar() or 0)
    watchers = 0
    try:
        from app.models.support_desk.collab import SdTicketWatcher
        watchers = (db.query(func.count(SdTicketWatcher.id))
                    .filter(SdTicketWatcher.ticket_id == t.id).scalar() or 0)
    except Exception:
        pass
    return {
        "frozen_at": sla_util.now_utc().isoformat(),
        "sev": ticket_sev(t.priority, bool(t.is_major_incident)),
        "started_at": _iso(at.get("started")),
        "detected_at": _iso(at.get("detected")),
        "acknowledged_at": _iso(at.get("acknowledged")),
        "first_mitigation_at": _iso(at.get("first_mitigation")),
        "resolved_at": _iso(at.get("resolved")),
        "mttd_minutes": _mins_between(at.get("started"), at.get("detected")),
        "mtta_minutes": _mins_between(at.get("detected"), at.get("acknowledged")),
        "mttr_minutes": _mins_between(at.get("detected"), at.get("resolved")),
        "duration_minutes": durations.get("total"),
        "affected_users": t.affected_users,
        "affected_services": list(t.affected_services or []),
        "decision_count": int(decisions),
        "update_count": int(updates),
        "watcher_count": int(watchers),
        "war_room_used": bool(t.war_room_url),
    }


# ─────────────────────────────── PIR helpers ───────────────────────────────

def snapshot_timeline(db: Session, ticket_id, limit: int = 200) -> list[dict]:
    """Freeze the ticket's activity trail (oldest→newest) for the PIR document."""
    rows = (db.query(SdTicketActivity)
            .filter(SdTicketActivity.ticket_id == ticket_id)
            .order_by(SdTicketActivity.created_at.asc())
            .limit(limit).all())
    return [{
        "at": r.created_at.isoformat() if r.created_at else None,
        "event": r.action,
        "actor": r.actor_name or "System",
        "detail": r.detail or {},
    } for r in rows]


def _esc(v) -> str:
    return html_mod.escape(str(v)) if v not in (None, "") else "—"


def _pir_html(pir: SdIncidentReport, ticket: SdTicket | None, names: dict) -> str:
    """Self-contained magazine-style PIR dossier (warm amber brand, print CSS)."""
    sev = ticket_sev(getattr(ticket, "priority", None),
                     bool(getattr(ticket, "is_major_incident", False))) if ticket else 4
    dur = "—"
    if ticket is not None and ticket.resolved_at and (ticket.incident_started_at or ticket.created_at):
        start = ticket.incident_started_at or ticket.created_at
        mins = max(0, int((ticket.resolved_at - start).total_seconds() // 60))
        dur = f"{mins // 60}h {mins % 60:02d}m"
    whys = "".join(
        f'<div class="why"><span class="why-n">WHY {i + 1}</span><p>{_esc(w)}</p></div>'
        for i, w in enumerate((pir.five_whys or [])[:5]) if str(w or "").strip())
    def _acts(rows):
        if not rows:
            return '<p class="muted">None recorded.</p>'
        out = ['<table><tr><th>Action</th><th>Owner</th><th>Target</th><th>Status</th></tr>']
        for a in rows:
            out.append(f"<tr><td>{_esc(a.get('action'))}</td><td>{_esc(a.get('owner_name'))}</td>"
                       f"<td>{_esc(a.get('target_date'))}</td><td>{_esc(a.get('status') or 'open')}</td></tr>")
        out.append("</table>")
        return "".join(out)
    tl = ""
    for e in (pir.timeline_snapshot or [])[:120]:
        at = _esc((e.get("at") or "")[:16].replace("T", " "))
        tl += (f'<div class="tl-row"><span class="tl-at">{at}</span>'
               f'<span class="tl-ev">{_esc(e.get("event"))}</span>'
               f'<span class="tl-actor">{_esc(e.get("actor"))}</span></div>')
    appr = ""
    for a in (pir.approvals or []):
        appr += (f'<div class="appr"><b>{_esc(a.get("name"))}</b> · {_esc(a.get("role"))} · '
                 f'{_esc(a.get("decision"))} · {_esc((a.get("at") or "")[:16].replace("T", " "))}'
                 + (f'<br><i>{_esc(a.get("note"))}</i>' if a.get("note") else "") + "</div>")
    tnum = _esc(getattr(ticket, "ticket_number", None))

    # ── v2 sections ──
    m = pir.metrics_snapshot or {}
    def _fmins(v):
        if v is None:
            return "—"
        v = float(v)
        return f"{int(v // 60)}h {int(v % 60):02d}m" if v >= 60 else f"{v:.0f}m"
    metrics = ""
    if m:
        metrics = (
            '<h2>Metrics — Frozen Record</h2><table class="grid">'
            f'<tr><td><b>MTTD (started → detected)</b>{_fmins(m.get("mttd_minutes"))}</td>'
            f'<td><b>MTTA (detected → acknowledged)</b>{_fmins(m.get("mtta_minutes"))}</td>'
            f'<td><b>MTTR (detected → resolved)</b>{_fmins(m.get("mttr_minutes"))}</td></tr>'
            f'<tr><td><b>Total duration</b>{_fmins(m.get("duration_minutes"))}</td>'
            f'<td><b>Decisions · updates · watchers</b>{_esc(m.get("decision_count"))} · '
            f'{_esc(m.get("update_count"))} · {_esc(m.get("watcher_count"))}</td>'
            f'<td><b>War room</b>{"LINKED" if m.get("war_room_used") else "—"}</td></tr></table>')
    def _reg(rows):
        return "".join(f'<li>{_esc(r)}</li>' for r in (rows or []) if str(r or "").strip())
    retro = ""
    if (pir.went_well or []) or (pir.went_wrong or []):
        retro = ('<h2>Blameless Retro</h2><table class="grid"><tr>'
                 f'<td><b>What went well</b><ul class="reg">{_reg(pir.went_well)}</ul></td>'
                 f'<td><b>What went wrong</b><ul class="reg">{_reg(pir.went_wrong)}</ul></td>'
                 '</tr></table>')
    factors = "".join(f'<span class="tag">{_esc(f)}</span>'
                      for f in (pir.contributing_factors or []) if str(f or "").strip())
    ppl = " · ".join(f'{_esc(x.get("name"))}{(" (" + _esc(x.get("role")) + ")") if x.get("role") else ""}'
                     for x in (pir.participants or []) if isinstance(x, dict) and x.get("name"))
    meeting = ""
    if pir.review_meeting_at or (pir.review_meeting_notes or "").strip() or ppl:
        meet_at = _esc(str(pir.review_meeting_at)[:16].replace("T", " ")
                       if pir.review_meeting_at else None)
        meeting = ('<h2>Review Meeting</h2>'
                   f'<p><b>Scheduled:</b> {meet_at}</p>'
                   + (f'<p><b>Participants:</b> {ppl}</p>' if ppl else "")
                   + (f'<p>{_esc(pir.review_meeting_notes)}</p>'
                      if (pir.review_meeting_notes or "").strip() else ""))
    # unpublished exports carry a diagonal watermark — the record of record is the
    # PUBLISHED dossier; anything earlier is work in progress.
    wm = ""
    if pir.status in ("draft", "in_review"):
        wm_text = "DRAFT" if pir.status == "draft" else "IN REVIEW"
        wm = (f'<div style="position: fixed; top: 42%; left: 6%; width: 88%; text-align: center;'
              f' transform: rotate(-24deg); font-size: 84px; letter-spacing: 18px; font-weight: 800;'
              f' color: rgba(176, 117, 20, 0.13); z-index: 0;">{wm_text}</div>')
    rev_n = len(pir.revisions or [])
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 18mm 16mm 20mm 16mm;
        @bottom-right {{ content: "{_esc(pir.report_number)} · page " counter(page) " / " counter(pages);
                        font: 8px 'Segoe UI', sans-serif; color: #9a8468; }} }}
body {{ font: 10.5px/1.55 'Segoe UI', Arial, sans-serif; color: #241c12; }}
.tag {{ display: inline-block; margin: 2px 4px 2px 0; padding: 2px 9px; border-radius: 12px;
       font-size: 8.5px; letter-spacing: .8px; border: 1px solid #e0cba0; color: #8a5a14;
       background: #faf4e7; }}
ul.reg {{ margin: 4px 0 2px 14px; padding: 0; }}
ul.reg li {{ margin: 2px 0; }}
.cover {{ background: linear-gradient(135deg, #1b130a, #3a2410 55%, #7c4a12); color: #f7ecd9;
         border-radius: 10px; padding: 26px 28px 22px; margin-bottom: 18px; }}
.cover .eyebrow {{ font-size: 9px; letter-spacing: 3px; color: #e8b04b; text-transform: uppercase; }}
.cover h1 {{ margin: 6px 0 4px; font-size: 22px; line-height: 1.2; }}
.cover .meta {{ font-size: 9.5px; color: #d8c5a5; }}
.badges span {{ display: inline-block; margin: 8px 6px 0 0; padding: 3px 10px; border-radius: 20px;
               font-size: 9px; letter-spacing: 1px; border: 1px solid rgba(232,176,75,.55); color: #f2d8a8; }}
h2 {{ font-size: 12.5px; letter-spacing: 1.6px; text-transform: uppercase; color: #8a5a14;
     border-bottom: 2px solid #e8b04b; padding-bottom: 4px; margin: 18px 0 8px; }}
p {{ margin: 4px 0; white-space: pre-wrap; }}
.muted {{ color: #8f8371; }}
.grid {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
.grid td {{ border: 1px solid #e5d9c2; padding: 6px 9px; font-size: 9.5px; }}
.grid td b {{ color: #8a5a14; display: block; font-size: 8px; letter-spacing: 1.4px; text-transform: uppercase; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
th {{ background: #f6ecd9; color: #6d4a10; text-align: left; font-size: 8.5px; letter-spacing: 1px;
     text-transform: uppercase; padding: 5px 8px; border: 1px solid #e5d9c2; }}
td {{ border: 1px solid #eee2cd; padding: 5px 8px; }}
.why {{ margin: 6px 0; padding-left: 10px; border-left: 3px solid #e8b04b; }}
.why-n {{ font-size: 8px; letter-spacing: 2px; color: #b07514; }}
.tl-row {{ display: flex; gap: 10px; padding: 3px 0; border-bottom: 1px dotted #e8dcc4; font-size: 9px; }}
.tl-at {{ width: 110px; color: #9a8468; font-family: Consolas, monospace; }}
.tl-ev {{ width: 170px; font-weight: 600; color: #6d4a10; }}
.appr {{ margin: 6px 0; padding: 7px 10px; background: #faf4e7; border: 1px solid #ecdfc4; border-radius: 6px; }}
</style></head><body>
{wm}
<div class="cover">
  <div class="eyebrow">Post-Incident Report · Fourconnect Support Desk</div>
  <h1>{_esc(pir.title)}</h1>
  <div class="meta">{_esc(pir.report_number)} · incident {tnum} · status {_esc(pir.status).upper()}{(' · revision ' + str(rev_n)) if rev_n else ''}</div>
  <div class="badges"><span>SEV{sev}</span><span>DURATION {dur}</span>
    <span>AFFECTED USERS {_esc(getattr(ticket, 'affected_users', None))}</span>
    <span>COMMANDER {_esc(names.get('commander'))}</span></div>
</div>
<h2>Executive Summary</h2><p>{_esc(pir.executive_summary)}</p>
{metrics}
<h2>Impact</h2>
<table class="grid"><tr>
  <td><b>Business impact</b>{_esc(pir.business_impact)}</td>
  <td><b>Technical impact</b>{_esc(pir.technical_impact)}</td></tr>
<tr><td><b>Affected services</b>{_esc(', '.join(getattr(ticket, 'affected_services', None) or []) or None)}</td>
  <td><b>Exposure</b>{'Compliance · ' if getattr(ticket, 'compliance_impact', False) else ''}{'Security · ' if getattr(ticket, 'security_impact', False) else ''}{'Public' if getattr(ticket, 'public_impact', False) else ''}&nbsp;</td></tr></table>
<h2>Root Cause</h2>
<p><b>Category:</b> {_esc(pir.root_cause_category)}</p><p>{_esc(pir.root_cause)}</p>
{('<p><b>Contributing factors:</b><br>' + factors + '</p>') if factors else ''}
{('<h2>Five-Why Analysis</h2>' + whys) if whys else ''}
{retro}
<h2>Corrective Actions</h2>{_acts(pir.corrective_actions or [])}
<h2>Preventive Actions</h2>{_acts(pir.preventive_actions or [])}
<h2>Lessons Learned</h2><p>{_esc(pir.lessons_learned)}</p>
{meeting}
{('<h2>Timeline</h2>' + tl) if tl else ''}
{('<h2>Approvals</h2>' + appr) if appr else ''}
</body></html>"""


def render_pir_pdf(pir: SdIncidentReport, ticket: SdTicket | None, names: dict) -> bytes:
    """HTML → PDF via WeasyPrint. Import stays INSIDE the function — WeasyPrint loads
    libgobject/libpango at import time and would crash boot on a machine without the
    GTK runtime (see CLAUDE.md). ensure_gtk_runtime() prepends vendor DLLs to PATH."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: PLC0415 — deliberate lazy import
    return HTML(string=_pir_html(pir, ticket, names)).write_pdf()


# ─────────────────────────────── Executive sitrep ───────────────────────────────

def _sitrep_html(t: SdTicket, s: dict) -> str:
    """One-page executive situation report (same warm-amber print language as the
    PIR dossier). ``s`` is the /incidents/{id}/sitrep response dict — PDF and JSON
    are rendered from the SAME assembly so they can never diverge."""
    sev = s.get("sev") or 4
    ph_rows = ""
    for p in s.get("phases") or []:
        at = p.get("at")
        at = _esc(str(at)[:16].replace("T", " ")) if at else "—"
        ph_rows += (f'<tr><td class="pk">{_esc(p.get("label"))}</td><td>{at}</td></tr>')
    dec_rows = ""
    for d in (s.get("decisions") or {}).get("latest") or []:
        dec_rows += (f'<div class="tl-row"><span class="tl-at">'
                     f'{_esc(str(d.get("at") or "")[:16].replace("T", " "))}</span>'
                     f'<span class="tl-ev">{_esc(d.get("kind"))}</span>'
                     f'<span>{_esc(d.get("decision"))}</span></div>')
    roster = s.get("roster") or {}
    impact = s.get("impact") or {}
    cadence = s.get("cadence") or {}
    last = s.get("last_update") or {}
    flags = " · ".join(f for f, on in (("COMPLIANCE", impact.get("compliance_impact")),
                                       ("SECURITY", impact.get("security_impact")),
                                       ("PUBLIC", impact.get("public_impact"))) if on) or "—"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 16mm 15mm 18mm 15mm;
        @bottom-right {{ content: "SITREP · {_esc(t.ticket_number)} · page " counter(page);
                        font: 8px 'Segoe UI', sans-serif; color: #9a8468; }} }}
body {{ font: 10.5px/1.55 'Segoe UI', Arial, sans-serif; color: #241c12; }}
.cover {{ background: linear-gradient(135deg, #1b130a, #3a2410 55%, #7c4a12); color: #f7ecd9;
         border-radius: 10px; padding: 24px 26px 20px; margin-bottom: 16px; }}
.cover .eyebrow {{ font-size: 9px; letter-spacing: 3px; color: #e8b04b; text-transform: uppercase; }}
.cover h1 {{ margin: 6px 0 4px; font-size: 20px; line-height: 1.25; }}
.cover .meta {{ font-size: 9.5px; color: #d8c5a5; }}
.badges span {{ display: inline-block; margin: 8px 6px 0 0; padding: 3px 10px; border-radius: 20px;
               font-size: 9px; letter-spacing: 1px; border: 1px solid rgba(232,176,75,.55); color: #f2d8a8; }}
h2 {{ font-size: 12px; letter-spacing: 1.6px; text-transform: uppercase; color: #8a5a14;
     border-bottom: 2px solid #e8b04b; padding-bottom: 4px; margin: 16px 0 8px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 4px; }}
td {{ border: 1px solid #eee2cd; padding: 5px 9px; font-size: 9.5px; }}
td.pk {{ width: 140px; color: #6d4a10; font-weight: 600; letter-spacing: .6px;
        text-transform: uppercase; font-size: 8.5px; background: #faf4e7; }}
.grid td b {{ color: #8a5a14; display: block; font-size: 8px; letter-spacing: 1.4px; text-transform: uppercase; }}
.tl-row {{ display: flex; gap: 10px; padding: 3px 0; border-bottom: 1px dotted #e8dcc4; font-size: 9px; }}
.tl-at {{ width: 110px; color: #9a8468; font-family: Consolas, monospace; }}
.tl-ev {{ width: 130px; font-weight: 600; color: #6d4a10; text-transform: uppercase; font-size: 8.5px; }}
p {{ margin: 4px 0; white-space: pre-wrap; }}
.muted {{ color: #8f8371; }}
</style></head><body>
<div class="cover">
  <div class="eyebrow">Executive Situation Report · Fourconnect Support Desk</div>
  <h1>{_esc(t.subject)}</h1>
  <div class="meta">{_esc(t.ticket_number)} · status {_esc(t.status).upper()} · generated {_esc(str(s.get("generated_at") or "")[:16].replace("T", " "))} UTC</div>
  <div class="badges"><span>SEV{sev}</span><span>RUNNING {_esc(s.get("running") or "—")}</span>
    <span>USERS {_esc(impact.get("affected_users"))}</span>
    <span>CHILDREN {_esc((s.get("children") or {}).get("count", 0))}</span>
    <span>SUBSCRIBERS {_esc(s.get("watchers_total", 0))}</span></div>
</div>
<h2>Phase Track</h2><table>{ph_rows}</table>
<h2>Command Roster</h2>
<table class="grid"><tr>
  <td><b>Incident commander</b>{_esc(roster.get("commander_name"))}</td>
  <td><b>Comms lead</b>{_esc(roster.get("comms_lead_name"))}</td>
  <td><b>Ops lead</b>{_esc(roster.get("ops_lead_name"))}</td></tr></table>
<h2>Blast Radius</h2>
<table class="grid"><tr>
  <td><b>Affected services</b>{_esc(', '.join(impact.get("affected_services") or []) or None)}</td>
  <td><b>Business impact</b>{_esc(impact.get("business_impact"))}</td></tr>
<tr><td><b>Users / revenue</b>{_esc(impact.get("affected_users"))} · {_esc(impact.get("revenue_impact"))}</td>
  <td><b>Exposure flags</b>{flags}</td></tr></table>
<h2>Stakeholder Comms</h2>
<p><b>Cadence:</b> {_esc(cadence.get("interval_minutes"))} min · next due {_esc(str(cadence.get("next_due_at") or "")[:16].replace("T", " ") or "—")}{' · OVERDUE' if cadence.get("overdue") else ''}</p>
<p><b>Last update ({_esc(str(last.get("at") or "")[:16].replace("T", " ") or "—")} · {_esc(last.get("actor"))}):</b> {_esc(last.get("preview"))}</p>
{('<h2>Latest Command Decisions</h2>' + dec_rows) if dec_rows else ''}
<h2>Records</h2>
<p>Decisions logged: <b>{_esc((s.get("decisions") or {}).get("count", 0))}</b> ·
   PIR: <b>{_esc(((s.get("pir") or {}).get("status") or "not opened")).upper()}</b> ·
   War room: <b>{'LINKED' if s.get("war_room_url") else '—'}</b></p>
</body></html>"""


def render_sitrep_pdf(t: SdTicket, sitrep: dict) -> bytes:
    """Sitrep HTML → PDF. Same lazy-import + GTK-bootstrap discipline as the PIR."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: PLC0415 — deliberate lazy import
    return HTML(string=_sitrep_html(t, sitrep)).write_pdf()


# ─────────────────────────────── PIR-missing sweep ───────────────────────────────

def sweep_pir_missing(db: Session) -> int:
    """Nudge the commander (else owner) of terminal MAJOR incidents that aged past
    PIR_REQUIRED_AFTER_DAYS with no PIR record. One nudge per ticket per 24h,
    deduped on the last 'pir_overdue' activity (nudge-owner throttle pattern).
    Safe to call from tasks_cron AND opportunistically on PIR-desk list-load."""
    from app.routers.support_desk._common import _notify_safe
    nowt = sla_util.now_utc()
    cutoff = nowt - timedelta(days=PIR_REQUIRED_AFTER_DAYS)
    day_ago = nowt - timedelta(days=1)
    # pir_owed_cond is the single debt truth (major_only — the nudge population);
    # days=0 disables the board's 90d window (an old un-reviewed MI still gets chased)
    # and the aging gate keeps the sweep's original >3d grace period.
    rows = (db.query(SdTicket)
            .filter(pir_owed_cond(nowt, days=0, major_only=True),
                    func.coalesce(SdTicket.resolved_at, SdTicket.closed_at,
                                  SdTicket.created_at) < cutoff)
            .limit(200).all())
    if not rows:
        return 0
    recent = {str(r[0]) for r in (db.query(SdTicketActivity.ticket_id)
              .filter(SdTicketActivity.ticket_id.in_([t.id for t in rows]),
                      SdTicketActivity.action == "pir_overdue",
                      SdTicketActivity.created_at > day_ago).all())}
    n = 0
    for t in rows:
        if str(t.id) in recent:
            continue  # already nudged today
        recipient = t.incident_commander_id or t.assigned_agent_id or t.resolved_by_id
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System", action="pir_overdue",
            detail={"auto": True, "required_after_days": PIR_REQUIRED_AFTER_DAYS}))
        _notify_safe(db, EVT_PIR_OVERDUE, recipient, t,
                     title=f"Post-incident report overdue — {t.ticket_number} closed without a PIR",
                     action_url="/user/support/incidents/post-incident")
        n += 1
    if n:
        db.commit()
    return n


# ─────────────────────────────── Timeline taxonomy helpers ───────────────────────────────
# Read-side enrichment over `support_ticket_activities.action` (ACTIVITY_CATALOG in
# constants.py). The pulse category case-expr is BUILT from the catalog so the pulse
# rollup and the catalog endpoint can never drift (same single-truth discipline as
# flag_condition ⇔ stats).

def timeline_meta(action: str) -> dict:
    """Catalog entry for an action, with the safe fallback for unregistered writers."""
    from app.models.support_desk.constants import ACTIVITY_CATALOG, TIMELINE_DEFAULT_META
    return ACTIVITY_CATALOG.get(action) or TIMELINE_DEFAULT_META


def timeline_category_case():
    """SQLAlchemy case() mapping SdTicketActivity.action → catalog category."""
    from sqlalchemy import case
    from app.models.support_desk.constants import ACTIVITY_CATALOG, TIMELINE_DEFAULT_META
    by_cat: dict[str, list[str]] = {}
    for action, meta in ACTIVITY_CATALOG.items():
        by_cat.setdefault(meta["category"], []).append(action)
    return case(*[(SdTicketActivity.action.in_(actions), cat)
                  for cat, actions in by_cat.items()],
                else_=TIMELINE_DEFAULT_META["category"])


# ─────────────────────────────── Shift chronicle PDF ───────────────────────────────

def _chronicle_html(events: list[dict], stats: dict, filters: dict) -> str:
    """Printable incident chronicle — the filtered timeline window as a dossier
    (same warm-amber print language as the PIR / sitrep exports). ``events`` are
    enriched feed dicts (at/action/label/category/actor/ticket_number/subject/sev/
    is_milestone), newest-first."""
    f_bits = " · ".join(f"{k} {v}" for k, v in filters.items() if v not in (None, "", []))
    rows = ""
    day = None
    for e in events:
        d = str(e.get("at") or "")[:10]
        if d != day:
            day = d
            rows += f'<div class="day">{_esc(d)}</div>'
        pin = '<span class="pin">★</span>' if e.get("is_milestone") else ""
        rows += (f'<div class="tl-row"><span class="tl-at">'
                 f'{_esc(str(e.get("at") or "")[11:16])}</span>'
                 f'<span class="tl-ev">{_esc(e.get("label") or e.get("action"))}{pin}</span>'
                 f'<span class="tl-tk">{_esc(e.get("ticket_number"))} · SEV{_esc(e.get("sev") or 4)}</span>'
                 f'<span class="tl-sub">{_esc((e.get("subject") or "")[:70])}</span>'
                 f'<span class="tl-by">{_esc(e.get("actor") or "System")}</span></div>')
    cats = " · ".join(f"{k.upper()} {v}" for k, v in (stats.get("by_category") or {}).items())
    sevs = " · ".join(f"{k.upper()} {v}" for k, v in (stats.get("by_sev") or {}).items())
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 14mm 13mm 16mm 13mm;
        @bottom-right {{ content: "INCIDENT CHRONICLE · page " counter(page);
                        font: 8px 'Segoe UI', sans-serif; color: #9a8468; }} }}
body {{ font: 9.5px/1.5 'Segoe UI', Arial, sans-serif; color: #241c12; }}
.cover {{ background: linear-gradient(135deg, #1b130a, #3a2410 55%, #7c4a12); color: #f7ecd9;
         border-radius: 10px; padding: 22px 24px 18px; margin-bottom: 14px; }}
.cover .eyebrow {{ font-size: 9px; letter-spacing: 3px; color: #e8b04b; text-transform: uppercase; }}
.cover h1 {{ margin: 6px 0 4px; font-size: 19px; }}
.cover .meta {{ font-size: 9px; color: #d8c5a5; }}
.badges span {{ display: inline-block; margin: 8px 6px 0 0; padding: 3px 10px; border-radius: 20px;
               font-size: 8.5px; letter-spacing: 1px; border: 1px solid rgba(232,176,75,.55); color: #f2d8a8; }}
.day {{ font-size: 10.5px; font-weight: 700; letter-spacing: 1.6px; color: #8a5a14;
       border-bottom: 2px solid #e8b04b; padding-bottom: 3px; margin: 14px 0 5px; }}
.tl-row {{ display: flex; gap: 8px; padding: 2.5px 0; border-bottom: 1px dotted #e8dcc4; }}
.tl-at {{ width: 34px; color: #9a8468; font-family: Consolas, monospace; }}
.tl-ev {{ width: 150px; font-weight: 600; color: #6d4a10; }}
.tl-tk {{ width: 130px; font-family: Consolas, monospace; color: #7c5a24; }}
.tl-sub {{ flex: 1; }}
.tl-by {{ width: 90px; text-align: right; color: #8f8371; }}
.pin {{ color: #b07514; margin-left: 4px; }}
</style></head><body>
<div class="cover">
  <div class="eyebrow">Incident Chronicle · Fourconnect Support Desk</div>
  <h1>The record of the desk, {_esc(stats.get("window") or "selected window")}</h1>
  <div class="meta">generated {_esc(stats.get("generated_at"))} UTC{(" · filters: " + _esc(f_bits)) if f_bits else ""}</div>
  <div class="badges"><span>EVENTS {_esc(stats.get("total", 0))}</span>
    <span>MILESTONES {_esc(stats.get("milestones", 0))}</span>
    {f'<span>{cats}</span>' if cats else ''}{f'<span>{sevs}</span>' if sevs else ''}</div>
</div>
{rows or '<p class="muted">No events in this window.</p>'}
</body></html>"""


def render_chronicle_pdf(events: list[dict], stats: dict, filters: dict) -> bytes:
    """Chronicle HTML → PDF. Same lazy-import + GTK-bootstrap discipline as the PIR."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: PLC0415 — deliberate lazy import
    return HTML(string=_chronicle_html(events, stats, filters)).write_pdf()
