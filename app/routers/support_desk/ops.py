"""Support Desk — Announcements, Automation Rules, Settings CRUD (admin).
Routers: announcements_router, automation_router, settings_router.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ops import SdAnnouncement, SdAutomationRule, SdRuleRevision, SdSetting
from app.schemas.support_desk.ops import (
    AnnouncementCreate, AnnouncementUpdate, AnnouncementResponse,
    AutomationRuleCreate, AutomationRuleUpdate, AutomationRuleResponse,
    RuleReorderRequest, RuleRevisionResponse, SettingUpsert, SettingResponse,
    WebhookTestRequest,
)
from app.schemas.support_desk.workspace import RuleSimulateRequest, RuleSimulateResponse
from app.utils.dependencies import get_current_superuser
from app.utils.support_desk.audit import write_audit


def _stringify_ids(data: dict, key: str):
    if key in data and data[key] is not None:
        data[key] = [str(x) for x in data[key]]


def _rule_snapshot(r: SdAutomationRule) -> dict:
    """The rule's full config state — one versioning cut for the Ledger panel."""
    return {
        "name": r.name, "description": r.description,
        "match_type": r.match_type, "conditions": r.conditions or [],
        "actions": r.actions or [], "order_index": r.order_index,
        "trigger": r.trigger, "stop_processing": bool(r.stop_processing),
        "time_threshold_mins": r.time_threshold_mins, "is_active": bool(r.is_active),
    }


def _write_revision(db: Session, rule: SdAutomationRule, actor_id, action: str) -> None:
    """Config versioning (ServiceNow-style): snapshot the rule AFTER the change."""
    version = (db.query(SdRuleRevision)
               .filter(SdRuleRevision.rule_id == rule.id).count()) + 1
    db.add(SdRuleRevision(rule_id=rule.id, version=version, action=action,
                          snapshot=_rule_snapshot(rule), changed_by_id=actor_id))


# ═══════════ Announcements ═══════════
announcements_router = APIRouter(prefix="/support-desk/announcements", tags=["Support Desk — Announcements"])


@announcements_router.get("/", response_model=List[AnnouncementResponse])
def list_announcements(active_only: bool = False, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    query = db.query(SdAnnouncement).filter(SdAnnouncement.is_deleted == False)  # noqa: E712
    if active_only:
        query = query.filter(SdAnnouncement.is_active == True)  # noqa: E712
    return query.order_by(SdAnnouncement.created_at.desc()).all()


@announcements_router.post("/", response_model=AnnouncementResponse, status_code=status.HTTP_201_CREATED)
def create_announcement(payload: AnnouncementCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    data = payload.model_dump(exclude_unset=True)
    _stringify_ids(data, "target_user_ids")
    a = SdAnnouncement(**data, created_by_id=admin.id)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@announcements_router.patch("/{aid}", response_model=AnnouncementResponse)
def update_announcement(aid: UUID, payload: AnnouncementUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = db.query(SdAnnouncement).filter(SdAnnouncement.id == aid, SdAnnouncement.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Announcement not found")
    data = payload.model_dump(exclude_unset=True)
    _stringify_ids(data, "target_user_ids")
    for k, v in data.items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return a


@announcements_router.delete("/{aid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_announcement(aid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = db.query(SdAnnouncement).filter(SdAnnouncement.id == aid, SdAnnouncement.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Announcement not found")
    a.is_deleted = True
    db.commit()
    return None


# ═══════════ Automation Rules ═══════════
automation_router = APIRouter(prefix="/support-desk/automation-rules", tags=["Support Desk — Automation Rules"])


@automation_router.get("/", response_model=List[AutomationRuleResponse])
def list_rules(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return (db.query(SdAutomationRule).filter(SdAutomationRule.is_deleted == False)  # noqa: E712
            .order_by(SdAutomationRule.order_index, SdAutomationRule.created_at).all())


@automation_router.post("/", response_model=AutomationRuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(payload: AutomationRuleCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = SdAutomationRule(**payload.model_dump(exclude_unset=True), created_by_id=admin.id)
    db.add(r)
    db.flush()
    _write_revision(db, r, admin.id, "created")
    write_audit(db, entity_type="rule", op="created", entity_id=r.id, actor_id=admin.id,
                details={"name": r.name, "trigger": r.trigger})
    db.commit()
    db.refresh(r)
    return r


# NOTE: literal routes MUST register before /{rid} or FastAPI eats them as UUIDs
# (the leave-module route-shadowing lesson).
@automation_router.patch("/reorder", response_model=List[AutomationRuleResponse])
def reorder_rules(payload: RuleReorderRequest, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Bulk order update from the drag-reorder UI. Unknown ids are ignored; every rule
    named in the payload gets the given order_index (first-match evaluation order)."""
    wanted = {}
    for row in payload.order or []:
        rid, idx = row.get("id"), row.get("order_index")
        if rid is None or idx is None:
            continue
        try:
            wanted[str(rid)] = int(idx)
        except (TypeError, ValueError):
            continue
    if not wanted:
        raise HTTPException(422, "order must be a non-empty list of {id, order_index}")
    rules = (db.query(SdAutomationRule)
             .filter(SdAutomationRule.is_deleted == False,  # noqa: E712
                     SdAutomationRule.id.in_(list(wanted.keys()))).all())
    changed = []
    for r in rules:
        if r.order_index != wanted[str(r.id)]:
            changed.append({"id": str(r.id), "name": r.name,
                            "from": r.order_index, "to": wanted[str(r.id)]})
        r.order_index = wanted[str(r.id)]
    if changed:
        from uuid import uuid4
        write_audit(db, entity_type="rule", op="reordered", entity_id=uuid4(),
                    actor_id=admin.id, details={"moves": changed[:40]})
    db.commit()
    return (db.query(SdAutomationRule).filter(SdAutomationRule.is_deleted == False)  # noqa: E712
            .order_by(SdAutomationRule.order_index, SdAutomationRule.created_at).all())


@automation_router.post("/simulate", response_model=RuleSimulateResponse)
def simulate_rules(payload: RuleSimulateRequest, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Dry-run a sample ticket through the rule chain + the category/type fallback
    router. Writes NOTHING — powers the Queue Config simulator panel."""
    from app.models.support_desk.ticket import SdTicket
    from app.models.support_desk.workspace import SdQueue
    from app.utils.support_desk.rules import evaluate_rules
    from app.utils.support_desk.assignment import match_route

    # A detached probe — never db.add()ed, so nothing can persist.
    probe = SdTicket(
        subject=payload.subject, description=payload.description,
        ticket_type=payload.ticket_type, priority=payload.priority,
        source=payload.source, impact=payload.impact, urgency=payload.urgency,
        category_id=payload.category_id, subcategory_id=payload.subcategory_id,
        organization_id=payload.organization_id,
        tags=list(payload.tags or []), status="open",
    )
    result = evaluate_rules(db, probe, trigger="on_create", dry_run=True)
    decision = dict(result.get("decision") or {})
    fallback_used = False
    if not decision.get("queue_id") and not decision.get("team_id"):
        matched = match_route(db, probe)   # read-only category/type preview
        queue, team = matched.get("queue"), matched.get("team")
        if queue is not None:
            decision.update({"queue_id": str(queue.id), "queue_name": queue.name, "via": "category_router"})
            fallback_used = True
        if team is not None:
            decision.update({"team_id": str(team.id), "team_name": team.name})
            fallback_used = True
        if not queue:
            default_q = (db.query(SdQueue)
                         .filter(SdQueue.is_deleted == False, SdQueue.is_active == True,  # noqa: E712
                                 SdQueue.is_default == True).first())  # noqa: E712
            if default_q:
                decision.update({"queue_id": str(default_q.id), "queue_name": default_q.name, "via": "default_queue"})
                fallback_used = True

    # Config-v2 truthfulness: the dry-run must show the SAME capacity spill + per-queue
    # SLA re-class the real route would apply (match_route already hops rule-less paths;
    # rule-routed decisions hop here).
    if decision.get("queue_id"):
        from app.utils.support_desk.assignment import apply_overflow
        from app.models.support_desk.core import SdSlaPackage
        q0 = db.query(SdQueue).filter(SdQueue.id == decision["queue_id"], SdQueue.is_deleted == False).first()  # noqa: E712
        if q0 is not None:
            q1, hopped = apply_overflow(db, q0)
            if hopped:
                decision.update({
                    "overflow_from_id": str(q0.id), "overflow_from": q0.name,
                    "queue_id": str(q1.id), "queue_name": q1.name,
                })
                if q1.team_id:
                    decision["team_id"] = str(q1.team_id)
            final_q = q1
            pkg_id = getattr(final_q, "sla_package_id", None)
            if pkg_id and not decision.get("sla_package_id"):
                pkg = db.query(SdSlaPackage).filter(SdSlaPackage.id == pkg_id, SdSlaPackage.is_deleted == False).first()  # noqa: E712
                if pkg is not None:
                    decision.update({"queue_sla_package_id": str(pkg.id), "queue_sla_package": pkg.name})
    return RuleSimulateResponse(matched=result.get("matched") or [], decision=decision, fallback_used=fallback_used)


@automation_router.get("/{rid}/revisions", response_model=List[RuleRevisionResponse])
def list_rule_revisions(rid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """The rule's config-versioning history, newest first (works for deleted rules
    too — the Ledger panel links here from tombstoned audit rows)."""
    if not db.query(SdAutomationRule).filter(SdAutomationRule.id == rid).first():
        raise HTTPException(404, "Rule not found")
    revs = (db.query(SdRuleRevision).filter(SdRuleRevision.rule_id == rid)
            .order_by(SdRuleRevision.version.desc()).all())
    names = {}
    ids = {r.changed_by_id for r in revs if r.changed_by_id}
    if ids:
        names = {str(u.id): u.full_name for u in db.query(User).filter(User.id.in_(ids)).all()}
    for r in revs:
        r.changed_by_name = names.get(str(r.changed_by_id)) if r.changed_by_id else None
    return revs


@automation_router.patch("/{rid}", response_model=AutomationRuleResponse)
def update_rule(rid: UUID, payload: AutomationRuleUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = db.query(SdAutomationRule).filter(SdAutomationRule.id == rid, SdAutomationRule.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rule not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    _write_revision(db, r, admin.id, "updated")
    write_audit(db, entity_type="rule", op="updated", entity_id=r.id, actor_id=admin.id,
                details={"name": r.name, "fields": sorted(data.keys())})
    db.commit()
    db.refresh(r)
    return r


@automation_router.delete("/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rid: UUID, reason: Optional[str] = Query(None, max_length=300),
                db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Decommission a rule. ``reason`` (the Decommission Chamber's coded verdict +
    optional note) lands in the audit row so the Ledger records WHY, not just who."""
    r = db.query(SdAutomationRule).filter(SdAutomationRule.id == rid, SdAutomationRule.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rule not found")
    r.is_deleted = True
    _write_revision(db, r, admin.id, "deleted")
    write_audit(db, entity_type="rule", op="deleted", entity_id=r.id, actor_id=admin.id,
                details={"name": r.name, **({"reason": reason} if reason else {})})
    db.commit()
    return None


# ═══════════ Settings ═══════════
settings_router = APIRouter(prefix="/support-desk/settings", tags=["Support Desk — Settings"])


@settings_router.get("/", response_model=List[SettingResponse])
def list_settings(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return db.query(SdSetting).order_by(SdSetting.key).all()


@settings_router.get("/{key}", response_model=SettingResponse)
def get_setting(key: str, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    s = db.query(SdSetting).filter(SdSetting.key == key).first()
    if not s:
        raise HTTPException(404, "Setting not found")
    return s


@settings_router.put("/", response_model=SettingResponse)
def upsert_setting(payload: SettingUpsert, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    s = db.query(SdSetting).filter(SdSetting.key == payload.key).first()
    if s:
        s.value = payload.value
        s.updated_by_id = admin.id
    else:
        s = SdSetting(key=payload.key, value=payload.value, updated_by_id=admin.id)
        db.add(s)
    db.flush()
    write_audit(db, entity_type="setting", op="updated", entity_id=s.id, actor_id=admin.id,
                details={"key": s.key})
    db.commit()
    db.refresh(s)
    if payload.key == "queue_notifications":
        # the wires engine caches this row — a panel save must apply instantly
        from app.utils.support_desk import wires
        wires.invalidate_cache()
    return s


@settings_router.post("/test-webhook")
def test_webhook(payload: WebhookTestRequest, db: Session = Depends(get_db),
                 admin: User = Depends(get_current_superuser)):
    """TEST TRANSMISSION for the Uplink Array — synchronously POSTs the
    Slack-compatible test payload to the given (or saved) webhook URL and
    reports the verdict. Superuser-only; nothing is persisted."""
    from app.utils.support_desk import wires
    url = (payload.url or "").strip() or (wires.config(db).get("webhook_url") or "").strip()
    if not url:
        raise HTTPException(422, "No webhook URL wired — enter one first.")
    ok, detail = wires.send_test(url)
    return {"ok": ok, "detail": detail, "url": url}
