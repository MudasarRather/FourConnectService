"""Support Desk — Ticket Templates ("Copperplate Studio" + agent "Projection Room").

ServiceNow/Zendesk-grade template management: lifecycle (draft|active|archived,
with ``is_active`` mirrored for API stability), usage analytics stamped by the
apply flow, clone, versioning-lite (content edits snapshot the previous cut onto
``revisions``, capped at 10), a sealed stats aggregate for the studio hero, and
list filters.

Access model (agent Template Desk):
  • visibility seal — non-superusers see 'global' templates, 'team' templates for
    teams they belong to, and their OWN 'personal' templates (others' personal
    plates 404, matching Zendesk personal macros).
  • agent authorship — any support agent may CREATE a template, but the payload is
    FORCED to visibility='personal' (pin/sort/default routing stripped); update /
    archive / delete are owner-only for personal templates. Superuser paths are
    byte-identical to the pre-desk contract (the admin studio never regresses).
  • per-agent analytics — every apply/macro writes an SdTemplateUsageEvent row;
    /stats gains defaulted my_* blocks. Favorites are per-user stars, separate
    from the admin's GLOBAL ``pinned`` curation flag.

Split out of ``workspace.py`` — the aggregate ``__init__`` imports
``templates_router`` from here; the public prefix is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.workspace import (
    SdTeam, SdTicketTemplate, SdTemplateFavorite, SdTemplateUsageEvent,
)
from app.models.support_desk.ticket import SdTicket
from app.models.support_desk.core import SdCategory
from app.models.support_desk.constants import TicketPriority, TicketType
from app.schemas.support_desk.workspace import (
    TemplateCreate, TemplateUpdate, TemplateResponse,
    TemplateDetailResponse, TemplateApplyResponse,
    TemplateStatsResponse, TemplateStatChip, TemplateCoverageEntry,
)
from app.utils.dependencies import get_support_agent
from app.utils.support_desk.audit import write_audit

templates_router = APIRouter(prefix="/support-desk/ticket-templates", tags=["Support Desk — Templates"])

_PRIORITIES = {p.value for p in TicketPriority}
_TYPES = {t.value for t in TicketType}
_STATUSES = {"draft", "active", "archived"}
_VISIBILITIES = {"global", "team", "personal"}
_REVISION_CAP = 10
_CONTENT_FIELDS = ("subject", "body", "checklist")
# Curation/routing knobs reserved to superusers — stripped from agent payloads.
_ADMIN_ONLY_FIELDS = ("pinned", "sort_order", "visibility", "default_assignee_id", "default_sla_package_id")


def _get_template(db: Session, tpl_id: UUID) -> SdTicketTemplate:
    tpl = db.query(SdTicketTemplate).filter(
        SdTicketTemplate.id == tpl_id,
        SdTicketTemplate.is_deleted == False,  # noqa: E712
    ).first()
    if not tpl:
        raise HTTPException(404, "Template not found")
    return tpl


def _validate_enums(priority: Optional[str], ticket_type: Optional[str]):
    if priority and priority not in _PRIORITIES:
        raise HTTPException(422, f"Unknown priority '{priority}'")
    if ticket_type and ticket_type not in _TYPES:
        raise HTTPException(422, f"Unknown ticket type '{ticket_type}'")


# ─────────────────────────── Visibility seal (agent desk) ───────────────────────────
def _my_team_ids(db: Session, user: User) -> list:
    """Support teams the user belongs to (member OR lead) — the same string-compare
    membership idiom as tickets_self._team_context (member_ids is a JSONB id list)."""
    uid = str(user.id)
    rows = db.query(SdTeam.id, SdTeam.member_ids, SdTeam.lead_user_id).filter(
        SdTeam.is_deleted == False, SdTeam.is_active == True,  # noqa: E712
    ).all()
    return [
        tid for tid, members, lead in rows
        if uid in [str(m) for m in (members or [])] or (lead and str(lead) == uid)
    ]


def _scope_filter(db: Session, user: User):
    """SQL seal for non-superusers: global (or legacy NULL) plates ∪ own personal
    plates ∪ team plates of teams they belong to."""
    conds = [
        SdTicketTemplate.visibility == "global",
        SdTicketTemplate.visibility.is_(None),   # pre-migration rows read as global
        and_(SdTicketTemplate.visibility == "personal", SdTicketTemplate.created_by_id == user.id),
    ]
    team_ids = _my_team_ids(db, user)
    if team_ids:
        conds.append(and_(SdTicketTemplate.visibility == "team", SdTicketTemplate.team_id.in_(team_ids)))
    return or_(*conds)


def _template_visible(db: Session, tpl: SdTicketTemplate, user: User) -> bool:
    """Per-row mirror of _scope_filter — what an agent can list is what they can open."""
    if getattr(user, "is_superuser", False):
        return True
    vis = tpl.visibility or "global"
    if vis == "global":
        return True
    if vis == "personal":
        return str(tpl.created_by_id) == str(user.id)
    if vis == "team":
        return tpl.team_id in set(_my_team_ids(db, user))
    return False


def _get_template_scoped(db: Session, tpl_id: UUID, user: User) -> SdTicketTemplate:
    """404 (not 403) outside the caller's scope — another agent's personal plate is
    invisible, not merely forbidden (no existence leak)."""
    tpl = _get_template(db, tpl_id)
    if not _template_visible(db, tpl, user):
        raise HTTPException(404, "Template not found")
    return tpl


def _own_personal(tpl: SdTicketTemplate, user: User) -> bool:
    return (tpl.visibility or "global") == "personal" and str(tpl.created_by_id) == str(user.id)


def _stamp_favorites(db: Session, user: User, templates: list) -> list:
    """Attach the per-caller ``is_favorite`` flag (one IN-query, read by from_attributes)."""
    if not templates:
        return templates
    ids = [t.id for t in templates]
    fav = {
        r[0] for r in db.query(SdTemplateFavorite.template_id).filter(
            SdTemplateFavorite.user_id == user.id,
            SdTemplateFavorite.template_id.in_(ids),
        ).all()
    }
    for t in templates:
        t.is_favorite = t.id in fav
    return templates


# ─────────────────────────── List ───────────────────────────
@templates_router.get("/", response_model=List[TemplateResponse])
def list_templates(
    include_inactive: bool = False,
    include_archived: bool = False,
    q: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    team_id: Optional[UUID] = None,
    category_id: Optional[UUID] = None,
    ticket_type: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_support_agent),
):
    """List templates. Default (no params) = active only — the pre-studio contract.

    ``status_filter`` (draft|active|archived|all) overrides the legacy
    include_inactive/include_archived pair when present. Non-superusers are sealed
    to their visibility scope (global ∪ own personal ∪ their teams' plates).
    """
    query = db.query(SdTicketTemplate).filter(SdTicketTemplate.is_deleted == False)  # noqa: E712
    if not user.is_superuser:
        query = query.filter(_scope_filter(db, user))
    if status_filter:
        if status_filter not in _STATUSES | {"all"}:
            raise HTTPException(422, f"Unknown status '{status_filter}'")
        if status_filter != "all":
            query = query.filter(SdTicketTemplate.status == status_filter)
    else:
        if not include_inactive:
            query = query.filter(SdTicketTemplate.is_active == True)  # noqa: E712
        elif not include_archived:
            query = query.filter(SdTicketTemplate.status != "archived")
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            SdTicketTemplate.name.ilike(like)
            | SdTicketTemplate.description.ilike(like)
            | SdTicketTemplate.subject.ilike(like)
        )
    if team_id:
        query = query.filter(SdTicketTemplate.team_id == team_id)
    if category_id:
        query = query.filter(SdTicketTemplate.category_id == category_id)
    if ticket_type:
        query = query.filter(SdTicketTemplate.ticket_type == ticket_type)
    rows = query.order_by(
        SdTicketTemplate.pinned.desc(),
        SdTicketTemplate.sort_order.asc(),
        SdTicketTemplate.name.asc(),
    ).all()
    return _stamp_favorites(db, user, rows)


# ─────────────────────────── Stats (sealed hero aggregate) ───────────────────────────
@templates_router.get("/stats", response_model=TemplateStatsResponse)
def template_stats(db: Session = Depends(get_db), user: User = Depends(get_support_agent)):
    base = db.query(SdTicketTemplate).filter(SdTicketTemplate.is_deleted == False)  # noqa: E712

    by_status = dict(
        db.query(SdTicketTemplate.status, func.count(SdTicketTemplate.id))
        .filter(SdTicketTemplate.is_deleted == False)  # noqa: E712
        .group_by(SdTicketTemplate.status)
        .all()
    )
    active = int(by_status.get("active", 0))
    draft = int(by_status.get("draft", 0))
    archived = int(by_status.get("archived", 0))
    total = active + draft + archived

    usage_total = int(
        db.query(func.coalesce(func.sum(SdTicketTemplate.usage_count), 0))
        .filter(SdTicketTemplate.is_deleted == False)  # noqa: E712
        .scalar() or 0
    )
    unused = base.filter(
        SdTicketTemplate.status == "active", SdTicketTemplate.usage_count == 0
    ).count()
    pinned = base.filter(
        SdTicketTemplate.pinned == True, SdTicketTemplate.status == "active"  # noqa: E712
    ).count()

    chip_cols = (
        SdTicketTemplate.id, SdTicketTemplate.name, SdTicketTemplate.icon,
        SdTicketTemplate.accent, SdTicketTemplate.usage_count,
        SdTicketTemplate.last_used_at, SdTicketTemplate.status,
    )
    top_used = [
        TemplateStatChip(id=r[0], name=r[1], icon=r[2], accent=r[3],
                         usage_count=r[4] or 0, last_used_at=r[5], status=r[6] or "active")
        for r in db.query(*chip_cols)
        .filter(SdTicketTemplate.is_deleted == False,  # noqa: E712
                SdTicketTemplate.status == "active", SdTicketTemplate.usage_count > 0)
        .order_by(SdTicketTemplate.usage_count.desc()).limit(5).all()
    ]
    recently_used = [
        TemplateStatChip(id=r[0], name=r[1], icon=r[2], accent=r[3],
                         usage_count=r[4] or 0, last_used_at=r[5], status=r[6] or "active")
        for r in db.query(*chip_cols)
        .filter(SdTicketTemplate.is_deleted == False,  # noqa: E712
                SdTicketTemplate.last_used_at.isnot(None))
        .order_by(SdTicketTemplate.last_used_at.desc()).limit(5).all()
    ]

    # Coverage: counts per category / team (one IN-query name resolve each).
    cat_rows = (
        db.query(SdTicketTemplate.category_id, func.count(SdTicketTemplate.id))
        .filter(SdTicketTemplate.is_deleted == False,  # noqa: E712
                SdTicketTemplate.status == "active",
                SdTicketTemplate.category_id.isnot(None))
        .group_by(SdTicketTemplate.category_id).all()
    )
    cat_names = {}
    if cat_rows:
        cat_names = {
            str(r[0]): r[1]
            for r in db.query(SdCategory.id, SdCategory.name)
            .filter(SdCategory.id.in_([r[0] for r in cat_rows])).all()
        }
    coverage_by_category = [
        TemplateCoverageEntry(id=r[0], name=cat_names.get(str(r[0]), "Unknown"), count=r[1])
        for r in sorted(cat_rows, key=lambda x: -x[1])
    ]

    team_rows = (
        db.query(SdTicketTemplate.team_id, func.count(SdTicketTemplate.id))
        .filter(SdTicketTemplate.is_deleted == False,  # noqa: E712
                SdTicketTemplate.status == "active",
                SdTicketTemplate.team_id.isnot(None))
        .group_by(SdTicketTemplate.team_id).all()
    )
    team_names = {}
    if team_rows:
        team_names = {
            str(r[0]): r[1]
            for r in db.query(SdTeam.id, SdTeam.name)
            .filter(SdTeam.id.in_([r[0] for r in team_rows])).all()
        }
    coverage_by_team = [
        TemplateCoverageEntry(id=r[0], name=team_names.get(str(r[0]), "Unknown"), count=r[1])
        for r in sorted(team_rows, key=lambda x: -x[1])
    ]

    tickets_from_templates = int(
        db.query(func.count(SdTicket.id)).filter(SdTicket.template_id.isnot(None)).scalar() or 0
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    tickets_from_templates_30d = int(
        db.query(func.count(SdTicket.id))
        .filter(SdTicket.template_id.isnot(None), SdTicket.created_at >= cutoff)
        .scalar() or 0
    )

    # ── Per-CALLER analytics (agent Template Desk) — event-sourced, cheap indexes ──
    my_use_total = int(
        db.query(func.count(SdTemplateUsageEvent.id))
        .filter(SdTemplateUsageEvent.user_id == user.id).scalar() or 0
    )
    my_use_30d = int(
        db.query(func.count(SdTemplateUsageEvent.id))
        .filter(SdTemplateUsageEvent.user_id == user.id, SdTemplateUsageEvent.used_at >= cutoff)
        .scalar() or 0
    )
    my_top_rows = (
        db.query(SdTemplateUsageEvent.template_id, func.count(SdTemplateUsageEvent.id).label("n"))
        .filter(SdTemplateUsageEvent.user_id == user.id)
        .group_by(SdTemplateUsageEvent.template_id)
        .order_by(func.count(SdTemplateUsageEvent.id).desc()).limit(5).all()
    )
    my_recent_rows = (
        db.query(SdTemplateUsageEvent.template_id, func.max(SdTemplateUsageEvent.used_at).label("at"))
        .filter(SdTemplateUsageEvent.user_id == user.id)
        .group_by(SdTemplateUsageEvent.template_id)
        .order_by(func.max(SdTemplateUsageEvent.used_at).desc()).limit(5).all()
    )
    chip_ids = {r[0] for r in my_top_rows} | {r[0] for r in my_recent_rows}
    chip_by_id = {}
    if chip_ids:
        chip_by_id = {
            r[0]: r for r in db.query(*chip_cols).filter(
                SdTicketTemplate.id.in_(chip_ids),
                SdTicketTemplate.is_deleted == False,  # noqa: E712
            ).all()
        }

    def _chip(tid, count=None, at=None):
        r = chip_by_id.get(tid)
        if not r:
            return None
        return TemplateStatChip(id=r[0], name=r[1], icon=r[2], accent=r[3],
                                usage_count=count if count is not None else (r[4] or 0),
                                last_used_at=at if at is not None else r[5],
                                status=r[6] or "active")

    my_top_used = [c for c in (_chip(r[0], count=r[1]) for r in my_top_rows) if c]
    my_recent = [c for c in (_chip(r[0], at=r[1]) for r in my_recent_rows) if c]

    return TemplateStatsResponse(
        total=total, active=active, draft=draft, archived=archived,
        pinned=pinned, unused=unused, usage_total=usage_total,
        tickets_from_templates=tickets_from_templates,
        tickets_from_templates_30d=tickets_from_templates_30d,
        top_used=top_used, recently_used=recently_used,
        coverage_by_category=coverage_by_category, coverage_by_team=coverage_by_team,
        my_use_total=my_use_total, my_use_30d=my_use_30d,
        my_top_used=my_top_used, my_recent=my_recent,
    )


# ─────────────────────────── Single ───────────────────────────
@templates_router.get("/{tpl_id}", response_model=TemplateDetailResponse)
def get_template(tpl_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_support_agent)):
    tpl = _get_template_scoped(db, tpl_id, user)
    _stamp_favorites(db, user, [tpl])
    return tpl


# ─────────────────────────── Create ───────────────────────────
@templates_router.post("/", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_support_agent),
):
    """Superusers author any scope (the studio contract, byte-identical). A support
    agent authors PERSONAL plates only — visibility is forced and the admin-only
    curation/routing knobs are stripped, never 422'd (Zendesk personal macros)."""
    if payload.status not in {"draft", "active"}:
        raise HTTPException(422, "New templates start as 'draft' or 'active'")
    _validate_enums(payload.priority, payload.ticket_type)
    data = payload.model_dump(exclude_unset=True)
    data["status"] = payload.status
    if user.is_superuser:
        vis = data.get("visibility", "global")
        if vis not in _VISIBILITIES:
            raise HTTPException(422, f"Unknown visibility '{vis}'")
        if vis == "team" and not data.get("team_id"):
            raise HTTPException(422, "Team-visible templates need a team_id")
    else:
        for k in _ADMIN_ONLY_FIELDS:
            data.pop(k, None)
        data["visibility"] = "personal"
    tpl = SdTicketTemplate(created_by_id=user.id, **data)
    tpl.is_active = tpl.status == "active"
    db.add(tpl)
    db.flush()
    write_audit(
        db, entity_type="ticket_template", op="created", entity_id=tpl.id,
        actor_id=user.id, request=request,
        details={"name": tpl.name, "status": tpl.status, "visibility": tpl.visibility or "global"},
    )
    db.commit()
    db.refresh(tpl)
    return tpl


# ─────────────────────────── Update (versioning-lite) ───────────────────────────
@templates_router.patch("/{tpl_id}", response_model=TemplateResponse)
def update_template(
    tpl_id: UUID,
    payload: TemplateUpdate,
    request: Request,
    reason: Optional[str] = Query(None, max_length=120),
    note: Optional[str] = Query(None, max_length=280),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Superuser edits anything (studio contract). An agent edits ONLY their own
    personal plates, and the admin-only curation/routing knobs are stripped from
    their payload before change detection (so they can never no-op-audit them)."""
    tpl = _get_template(db, tpl_id)
    if not admin.is_superuser:
        if not _template_visible(db, tpl, admin):
            raise HTTPException(404, "Template not found")
        if not _own_personal(tpl, admin):
            raise HTTPException(403, "Only your own personal templates can be edited")
    data = payload.model_dump(exclude_unset=True)
    if not admin.is_superuser:
        for k in _ADMIN_ONLY_FIELDS:
            data.pop(k, None)

    # Legacy alias: is_active-only payloads map onto the status lifecycle.
    if "status" not in data and "is_active" in data:
        data["status"] = "active" if data["is_active"] else "archived"
    data.pop("is_active", None)
    if "status" in data and data["status"] not in _STATUSES:
        raise HTTPException(422, f"Unknown status '{data['status']}'")
    if "visibility" in data:
        if data["visibility"] not in _VISIBILITIES:
            raise HTTPException(422, f"Unknown visibility '{data['visibility']}'")
        if data["visibility"] == "team" and not (data.get("team_id") or tpl.team_id):
            raise HTTPException(422, "Team-visible templates need a team_id")
    _validate_enums(data.get("priority"), data.get("ticket_type"))

    # Effective-change detection (no-op guard: nothing changed -> no bump, no audit).
    changed = {k: v for k, v in data.items() if getattr(tpl, k) != v}
    if not changed:
        return tpl

    # Content edits snapshot the PREVIOUS cut (versioning-lite).
    if any(k in changed for k in _CONTENT_FIELDS):
        snapshot = {
            "subject": tpl.subject,
            "body": tpl.body,
            "checklist": tpl.checklist or [],
            "edited_by": str(admin.id),
            "edited_at": datetime.now(timezone.utc).isoformat(),
            "version": tpl.version or 1,
        }
        # Reassign a NEW list — JSONB in-place mutation is not change-tracked.
        tpl.revisions = ([snapshot] + list(tpl.revisions or []))[:_REVISION_CAP]
        tpl.version = (tpl.version or 1) + 1

    old_status = tpl.status
    for k, v in changed.items():
        setattr(tpl, k, v)
    tpl.is_active = tpl.status == "active"

    details = {"changed": sorted(changed.keys()), "version": tpl.version}
    if "status" in changed:
        details["status"] = {"from": old_status, "to": tpl.status}
        # A reason/note is meaningful only for a lifecycle move (e.g. → archived);
        # the retire bench sends them so the audit records WHY it was shelved.
        if reason:
            details["reason"] = reason
        if note:
            details["note"] = note
    write_audit(
        db, entity_type="ticket_template", op="updated", entity_id=tpl.id,
        actor_id=admin.id, request=request, details=details,
    )
    db.commit()
    db.refresh(tpl)
    return tpl


# ─────────────────────────── Clone ───────────────────────────
@templates_router.post("/{tpl_id}/clone", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
def clone_template(
    tpl_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Clones start as drafts. A superuser clone keeps the source visibility (studio
    contract); an agent clone is a 'clone to mine' — forced personal, so any agent can
    lift a global plate into their own kit and tune it without touching the library."""
    src = _get_template_scoped(db, tpl_id, admin)
    clone = SdTicketTemplate(
        name=f"Copy of {src.name}"[:120],
        description=src.description,
        category_id=src.category_id,
        team_id=src.team_id,
        ticket_type=src.ticket_type,
        priority=src.priority,
        subject=src.subject,
        body=src.body,
        tags=list(src.tags or []),
        checklist=list(src.checklist or []),
        default_sla_package_id=src.default_sla_package_id,
        default_assignee_id=src.default_assignee_id,
        icon=src.icon,
        accent=src.accent,
        status="draft",           # clones start as drafts — activate deliberately
        is_active=False,
        pinned=False,
        sort_order=src.sort_order,
        visibility=(src.visibility or "global") if admin.is_superuser else "personal",
        created_by_id=admin.id,
    )
    db.add(clone)
    db.flush()
    write_audit(
        db, entity_type="ticket_template", op="cloned", entity_id=clone.id,
        actor_id=admin.id, request=request,
        details={"source_id": str(src.id), "name": clone.name},
    )
    db.commit()
    db.refresh(clone)
    return clone


# ─────────────────────────── Apply (usage stamp) ───────────────────────────
@templates_router.post("/{tpl_id}/apply", response_model=TemplateApplyResponse)
def apply_template(
    tpl_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_support_agent),
):
    """Record one use and return the render-ready prefill payload.

    Archived templates never apply; drafts apply only for superusers OR the owner of
    a personal draft (test-drive parity). Others' personal plates 404 via the scope.
    Deliberately NOT idempotent — each apply is one use; the frontend strips its
    ``?template=`` query param immediately so a refresh can't double-count.
    """
    tpl = _get_template_scoped(db, tpl_id, user)
    if tpl.status == "archived":
        raise HTTPException(409, "Template is archived")
    if tpl.status == "draft" and not (user.is_superuser or _own_personal(tpl, user)):
        raise HTTPException(409, "Template is a draft")

    tpl.usage_count = SdTicketTemplate.usage_count + 1   # atomic SQL increment
    tpl.last_used_at = func.now()
    tpl.last_used_by_id = user.id
    db.add(SdTemplateUsageEvent(template_id=tpl.id, user_id=user.id, kind="apply"))
    write_audit(
        db, entity_type="ticket_template", op="applied", entity_id=tpl.id,
        actor_id=user.id, request=request, details={"name": tpl.name},
    )
    db.commit()
    db.refresh(tpl)
    return TemplateApplyResponse(
        template_id=tpl.id,
        name=tpl.name,
        subject=tpl.subject,
        body=tpl.body,
        ticket_type=tpl.ticket_type,
        priority=tpl.priority,
        category_id=tpl.category_id,
        team_id=tpl.team_id,
        tags=list(tpl.tags or []),
        checklist=list(tpl.checklist or []),
        default_sla_package_id=tpl.default_sla_package_id,
        default_assignee_id=tpl.default_assignee_id,
        usage_count=tpl.usage_count or 0,
        version=tpl.version or 1,
    )


# ─────────────────────────── Favorite (per-user star, toggle) ───────────────────────────
@templates_router.post("/{tpl_id}/favorite")
def toggle_favorite(
    tpl_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_support_agent),
):
    """Toggle the caller's star. Pure per-user preference (like saved views) — no
    audit row; distinct from the admin's GLOBAL ``pinned`` curation flag."""
    tpl = _get_template_scoped(db, tpl_id, user)
    row = db.query(SdTemplateFavorite).filter(
        SdTemplateFavorite.user_id == user.id,
        SdTemplateFavorite.template_id == tpl.id,
    ).first()
    if row:
        db.delete(row)
        fav = False
    else:
        db.add(SdTemplateFavorite(user_id=user.id, template_id=tpl.id))
        fav = True
    db.commit()
    return {"template_id": str(tpl.id), "is_favorite": fav}


# ─────────────────────────── Delete (soft) ───────────────────────────
@templates_router.delete("/{tpl_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    tpl_id: UUID,
    request: Request,
    reason: Optional[str] = Query(None, max_length=120),
    note: Optional[str] = Query(None, max_length=280),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Superuser deletes anything (studio contract); an agent deletes only their
    own personal plates — everything else 404s (scope) or 403s (not owner).
    The retire bench sends reason/note so the audit trail records WHY."""
    tpl = _get_template(db, tpl_id)
    if not admin.is_superuser:
        if not _template_visible(db, tpl, admin):
            raise HTTPException(404, "Template not found")
        if not _own_personal(tpl, admin):
            raise HTTPException(403, "Only your own personal templates can be deleted")
    tpl.is_deleted = True
    details = {"name": tpl.name, "status": tpl.status, "usage_count": tpl.usage_count or 0}
    if reason:
        details["reason"] = reason
    if note:
        details["note"] = note
    write_audit(
        db, entity_type="ticket_template", op="deleted", entity_id=tpl.id,
        actor_id=admin.id, request=request, details=details,
    )
    db.commit()
    return None
