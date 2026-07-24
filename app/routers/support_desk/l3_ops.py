"""Support Desk — L3 workbench router: handoff dossier + problem cascade solve.

ServiceNow/Zendesk parity surfaces for the engineering (L3) desk:

  • GET  /tickets/{id}/handoff-dossier   — everything the receiving engineer needs
        in one sealed read: the structured escalation record (incl. esc-ACK state),
        every "[Ln handoff — technical diagnosis]" internal note the lower tiers
        wrote, the ticket's tier path, banked worklog minutes, and snapshots of the
        linked problem / change records.
  • POST /problems/{pid}/resolve-linked  — the Zendesk problem→incident cascade:
        resolving a Problem offers to resolve every linked, still-open ticket with
        one shared resolution. Per-ticket eligibility (seal, terminal, merged,
        actor gate) is evaluated ticket-by-ticket and reported back — one bad
        ticket never sinks the batch. Unowned tickets are assigned to the caller
        first (repo rule: no owner ⇒ no resolution) with a cascade-tagged activity.

Team-sealed like every desk surface via the broad router's ``_get_ticket``.
Registered in ``routers/support_desk/__init__.py`` BEFORE the broad tickets router
(route-shadowing discipline; literal suffixes after the id).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketComment, SdTicketActivity
from app.models.support_desk.collab import SdTicketWorklog
from app.models.support_desk.itil import SdProblem, SdChangeRequest
from app.models.support_desk.constants import ProblemStatus, TERMINAL_TICKET_STATUSES
from app.schemas.support_desk.l3 import (
    HandoffDossierResponse, DossierDiagnosis, DossierTierMove, DossierProblem, DossierChange,
    ProblemCascadeRequest, ProblemCascadeResponse, CascadeTicketResult,
)
from app.utils.dependencies import get_support_agent
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit

l3_router = APIRouter(prefix="/support-desk", tags=["Support Desk — L3 Workbench"])

_DIAG_MARK = "handoff — technical diagnosis]"


def _user_names(db: Session, ids) -> dict:
    ids = [i for i in {str(x) for x in ids if x}]
    if not ids:
        return {}
    rows = db.query(User.id, User.full_name, User.email).filter(User.id.in_(ids)).all()
    return {str(uid): (name or email or "Agent") for uid, name, email in rows}


# ─────────────────────────────── Handoff dossier ───────────────────────────────
@l3_router.get("/tickets/{ticket_id}/handoff-dossier", response_model=HandoffDossierResponse)
def handoff_dossier(ticket_id: UUID, db: Session = Depends(get_db),
                    admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)

    names = _user_names(db, [t.escalated_by_id, t.escalation_acknowledged_by_id])

    # Every technical-diagnosis note lower tiers attached on tier-escalate.
    diag_rows = (db.query(SdTicketComment)
                 .filter(SdTicketComment.ticket_id == t.id,
                         SdTicketComment.is_internal == True,  # noqa: E712
                         SdTicketComment.body.like(f"%{_DIAG_MARK}%"))
                 .order_by(SdTicketComment.created_at.asc()).all())
    diagnoses = [DossierDiagnosis(author_name=c.author_name, created_at=c.created_at,
                                  body=(c.body or "").split("]\n", 1)[-1].strip() or (c.body or ""))
                 for c in diag_rows]

    # Tier path — the tier_moved trail apply_tier_move writes.
    move_rows = (db.query(SdTicketActivity)
                 .filter(SdTicketActivity.ticket_id == t.id,
                         SdTicketActivity.action == "tier_moved")
                 .order_by(SdTicketActivity.created_at.asc()).all())
    tier_path = [DossierTierMove(direction=(m.detail or {}).get("direction"),
                                 tier=(m.detail or {}).get("tier"),
                                 queue=(m.detail or {}).get("queue"),
                                 actor_name=m.actor_name, at=m.created_at)
                 for m in move_rows]

    worklog_minutes = int(db.query(func.coalesce(func.sum(SdTicketWorklog.minutes), 0))
                          .filter(SdTicketWorklog.ticket_id == t.id,
                                  SdTicketWorklog.is_deleted == False).scalar() or 0)  # noqa: E712

    problem = None
    if t.linked_problem_id:
        p = db.query(SdProblem).filter(SdProblem.id == t.linked_problem_id,
                                       SdProblem.is_deleted == False).first()  # noqa: E712
        if p:
            problem = DossierProblem.model_validate(p)
            problem.linked_count = len(p.linked_ticket_ids or [])
    change = None
    if t.linked_change_id:
        c = db.query(SdChangeRequest).filter(SdChangeRequest.id == t.linked_change_id,
                                             SdChangeRequest.is_deleted == False).first()  # noqa: E712
        if c:
            change = DossierChange.model_validate(c)

    return HandoffDossierResponse(
        ticket_id=t.id,
        is_escalated=bool(t.is_escalated),
        escalation_level=t.escalation_level or 0,
        escalated_at=t.escalated_at,
        escalation_reason=t.escalation_reason,
        escalation_reason_code=t.escalation_reason_code,
        escalation_type=t.escalation_type,
        escalated_by_name=names.get(str(t.escalated_by_id)) if t.escalated_by_id else None,
        auto_escalated=bool(t.auto_escalated_at),
        acknowledged_at=t.escalation_acknowledged_at,
        acknowledged_by_name=(names.get(str(t.escalation_acknowledged_by_id))
                              if t.escalation_acknowledged_by_id else None),
        ack_due_at=t.escalation_response_due_at,
        diagnoses=diagnoses,
        tier_path=tier_path,
        worklog_minutes=worklog_minutes,
        reopened_count=t.reopened_count or 0,
        rca_summary=t.rca_summary,
        breach_reason=t.breach_reason,
        problem=problem,
        change=change,
    )


# ─────────────────────────────── Problem cascade solve ───────────────────────────────
@l3_router.post("/problems/{pid}/resolve-linked", response_model=ProblemCascadeResponse)
def resolve_linked(pid: UUID, payload: ProblemCascadeRequest, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _apply_resolution, _log_activity,
        _RESOLUTION_CODES, _ROOT_CAUSES, require_resolution_summary,
    )
    if payload.resolution_code not in _RESOLUTION_CODES:
        raise HTTPException(422, f"Invalid resolution_code '{payload.resolution_code}'")
    if payload.resolution_category and payload.resolution_category not in _ROOT_CAUSES:
        raise HTTPException(422, f"Invalid resolution_category '{payload.resolution_category}'")
    if payload.root_cause_category and payload.root_cause_category not in _ROOT_CAUSES:
        raise HTTPException(422, f"Invalid root_cause_category '{payload.root_cause_category}'")
    require_resolution_summary(payload.resolution_summary)

    p = db.query(SdProblem).filter(SdProblem.id == pid,
                                   SdProblem.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Problem not found")
    # Owner-tier on the PROBLEM before we stamp its root cause / status below. The per-ticket
    # loop is already actor-gated, but an agent who commands NONE of the linked tickets must
    # not be able to rewrite the problem's root_cause / mark it resolved via this route.
    from app.routers.support_desk.itil import _require_problem_actor
    _require_problem_actor(db, p, admin, "cascade a resolution from it")
    if p.status in (ProblemStatus.CLOSED.value,):
        raise HTTPException(409, "This problem is closed — reopen it before cascading a resolution.")

    results: list[CascadeTicketResult] = []
    resolved = 0
    for raw in (p.linked_ticket_ids or []):
        try:
            tid = UUID(str(raw))
        except (ValueError, TypeError):
            continue
        number = None
        try:
            t = _get_ticket(db, tid, admin)          # 404 outside the team seal
            number = t.ticket_number
            if t.merged_into_id:
                results.append(CascadeTicketResult(ticket_id=tid, ticket_number=number,
                                                   ok=False, reason="merged into another ticket"))
                continue
            if t.status in TERMINAL_TICKET_STATUSES:
                results.append(CascadeTicketResult(ticket_id=tid, ticket_number=number,
                                                   ok=False, reason="already resolved/closed"))
                continue
            _require_ticket_actor(db, t, admin, "resolve it")
            # Repo rule: no owner ⇒ no resolution. The cascade resolver takes ownership.
            if not t.assigned_agent_id:
                t.assigned_agent_id = admin.id
                _log_activity(db, t, admin, "assigned",
                              {"agent_id": str(admin.id), "by": "problem_cascade"})
            _apply_resolution(db, t, admin,
                              resolution_code=payload.resolution_code,
                              resolution_category=payload.resolution_category,
                              resolution_summary=payload.resolution_summary,
                              time_spent_minutes=None, note=None, attachments=None, close=False)
            _log_activity(db, t, admin, "problem_cascade_resolved",
                          {"problem_id": str(p.id), "problem_number": p.problem_number})
            # RCA v2 propagation: the problem's known root cause IS this ticket's root
            # cause — stamp it (as a provenance-marked filing) so the cascade never
            # mints fresh RCA debt. Fills only EMPTY/stale slots; a live human filing
            # (filed/validated) is never overwritten.
            inherited = False
            cause_text = (payload.root_cause or p.root_cause or "").strip()
            if payload.propagate_rca and len(cause_text) >= 10:
                from app.utils.support_desk.rca import (
                    RCA_LIVE_STATUSES, rca_effective_status,
                )
                if rca_effective_status(t) not in RCA_LIVE_STATUSES:
                    t.rca_summary = cause_text
                    if not (t.rca_preventive or "").strip() and (p.preventive_measures or "").strip():
                        t.rca_preventive = p.preventive_measures.strip()
                    if payload.root_cause_category:
                        t.rca_category = payload.root_cause_category
                    t.rca_status = "filed"
                    t.rca_filed_at = sla_util.now_utc()
                    t.rca_filed_by_id = admin.id
                    t.rca_reviewed_at = None
                    t.rca_reviewed_by_id = None
                    t.rca_review_note = None
                    t.rca_inherited_from_problem_id = p.id
                    _log_activity(db, t, admin, "rca_inherited",
                                  {"problem_id": str(p.id), "problem_number": p.problem_number})
                    inherited = True
            results.append(CascadeTicketResult(ticket_id=tid, ticket_number=number, ok=True,
                                               rca_inherited=inherited))
            resolved += 1
        except HTTPException as e:
            results.append(CascadeTicketResult(ticket_id=tid, ticket_number=number,
                                               ok=False, reason=str(e.detail)))

    if payload.root_cause and payload.root_cause.strip():
        p.root_cause = payload.root_cause.strip()
    if payload.mark_problem_resolved:
        p.status = ProblemStatus.RESOLVED.value
    write_audit(db, entity_type="problem", op="cascade_resolved", entity_id=p.id,
                actor_id=admin.id, request=request,
                details={"resolved": resolved, "skipped": len(results) - resolved,
                         "code": payload.resolution_code})
    db.commit()
    db.refresh(p)
    return ProblemCascadeResponse(problem_id=p.id, problem_status=p.status,
                                  resolved=resolved, skipped=len(results) - resolved,
                                  results=results)
