"""HR Settings — unified Audit Logs (read-only).

Reads the canonical ``hr_settings_audit_logs`` AND folds in, at read time, the
configuration changes that live in *other* audit tables so there is ONE
governance ledger of every settings change without migrating or duplicating the
module audit tables:

* compliance / statutory rate changes        → ``payroll_audit_logs`` (CONFIG)
* organization-structure master changes      → ``audit_logs`` (``hr.{department|
  designation|grade|work_location}.*``), which are captured by the SQLAlchemy
  audit listeners in ``app.utils.hr.audit`` rather than ``log_settings_change``.

Folding the org-structure rows here closes a real loophole: the Departments /
Designations / Grades / Work-Locations delete modals tell the admin the change is
"sealed into the settings ledger", but those rows were only ever written to the
generic ``audit_logs`` table and never surfaced on this screen.
"""
import json
from datetime import timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.settings_audit_log import SettingsAuditLog
from app.models.hr.payroll_config import PayrollAuditLog
from app.models.audit_log import AuditLog
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/settings/audit-logs", tags=["HR — Settings Audit"])

# Generic-audit entity_type → unified settings entity_type label. These are the
# org-structure masters audited by the SQLAlchemy listeners (NOT log_settings_change).
ORG_ENTITY = {
    "department": "DEPARTMENT",
    "designation": "DESIGNATION",
    "grade": "GRADE",
    "work_location": "WORK_LOCATION",
}
ORG_LABELS = set(ORG_ENTITY.values())

# Columns that are bookkeeping noise in a human-facing change note.
_NOISE_COLS = {"updated_at", "created_at", "id"}


def _as_aware(dt):
    """Normalise naive timestamps (audit_logs uses naive utcnow) to UTC so the
    cross-source merge sorts correctly against the tz-aware settings rows."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _interpret_org_row(r):
    """Translate a generic ``audit_logs`` org-structure row into the unified shape.

    Soft-deletes are stored as an UPDATE that flips ``is_deleted`` false→true, so
    we re-derive the real action from the changed columns; ``before``/``after`` and
    a human note are reconstructed from the JSON details blob."""
    op = (r.action or "").rsplit(".", 1)[-1].lower()  # created | updated | deleted
    action = {"created": "CREATE", "deleted": "DELETE"}.get(op, "UPDATE")

    before = after = None
    note = None
    try:
        details = json.loads(r.details) if r.details else {}
    except Exception:
        details = {}

    if isinstance(details, dict):
        note = details.get("note")
        snap = details.get("snapshot")
        changes = details.get("changes")
        if isinstance(changes, dict) and changes:
            before = {k: v.get("from") for k, v in changes.items() if isinstance(v, dict)}
            after = {k: v.get("to") for k, v in changes.items() if isinstance(v, dict)}
            # Re-derive CREATE/DELETE/restore from a soft-delete toggle.
            if "is_deleted" in changes and isinstance(changes["is_deleted"], dict):
                to_del = changes["is_deleted"].get("to")
                if to_del in (True, "true", "True"):
                    action = "DELETE"
                elif to_del in (False, "false", "False"):
                    action = "UPDATE"  # restore / reactivate
            if not note:
                touched = [c for c in changes if c not in _NOISE_COLS]
                if action == "DELETE":
                    note = (after or {}).get("name") if after else None
                elif touched:
                    note = "Changed " + ", ".join(touched[:4]) + ("…" if len(touched) > 4 else "")
        elif isinstance(snap, dict):
            if action == "DELETE":
                before = snap
            else:
                after = snap
            if not note:
                note = snap.get("name") or snap.get("label") or snap.get("code")

    return action, before, after, note


@router.get("/")
def list_audit(entity_type: Optional[str] = None, skip: int = 0,
               limit: int = Query(50, ge=1, le=200),
               db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    window = skip + limit

    # ── 1. canonical settings ledger ─────────────────────────────────────────
    sq = db.query(SettingsAuditLog)
    if entity_type:
        sq = sq.filter(SettingsAuditLog.entity_type == entity_type)
    settings_total = sq.count()
    settings_rows = sq.order_by(SettingsAuditLog.created_at.desc()).limit(window).all()

    items = [{
        "id": str(r.id), "source": "settings", "entity_type": r.entity_type, "action": r.action,
        "actor_id": str(r.actor_id) if r.actor_id else None, "note": r.note,
        "from_status": None, "to_status": None,
        "before": r.before_json, "after": r.after_json,
        "created_at": _as_aware(r.created_at),
    } for r in settings_rows]

    # ── 2. fold compliance / statutory config changes (payroll CONFIG audit) ──
    cfg_total = 0
    if not entity_type or entity_type in ("COMPLIANCE", "CONFIG"):
        cq = db.query(PayrollAuditLog).filter(PayrollAuditLog.entity_type == "CONFIG")
        cfg_total = cq.count()
        for r in cq.order_by(PayrollAuditLog.created_at.desc()).limit(window).all():
            items.append({
                "id": str(r.id), "source": "compliance", "entity_type": "COMPLIANCE",
                "action": r.action.value if hasattr(r.action, "value") else str(r.action),
                "actor_id": str(r.actor_id) if r.actor_id else None, "note": r.note,
                "from_status": r.from_status, "to_status": r.to_status,
                "before": None, "after": None,
                "created_at": _as_aware(r.created_at),
            })

    # ── 3. fold org-structure master changes (generic audit_logs) ─────────────
    org_total = 0
    org_filter = None
    if entity_type:
        # Reverse-map a unified label (e.g. "DEPARTMENT") to its audit entity_type.
        org_filter = next((k for k, v in ORG_ENTITY.items() if v == entity_type), None)
    if not entity_type or org_filter:
        oq = db.query(AuditLog).filter(
            AuditLog.action.like("hr.%"),
            AuditLog.entity_type.in_([org_filter] if org_filter else list(ORG_ENTITY.keys())),
        )
        org_total = oq.count()
        for r in oq.order_by(AuditLog.created_at.desc()).limit(window).all():
            action, before, after, note = _interpret_org_row(r)
            items.append({
                "id": str(r.id), "source": "settings",
                "entity_type": ORG_ENTITY.get(r.entity_type, r.entity_type.upper()),
                "action": action,
                "actor_id": str(r.user_id) if r.user_id else None, "note": note,
                "from_status": None, "to_status": None,
                "before": before, "after": after,
                "created_at": _as_aware(r.created_at),
            })

    # ── merge, sort, page ─────────────────────────────────────────────────────
    items.sort(key=lambda x: x["created_at"].timestamp() if x["created_at"] else 0.0, reverse=True)
    page = items[skip:skip + limit]

    # enrich actor names in one query
    actor_ids = {i["actor_id"] for i in page if i["actor_id"]}
    names = {}
    if actor_ids:
        for u in db.query(User).filter(User.id.in_([UUID(a) for a in actor_ids])).all():
            names[str(u.id)] = getattr(u, "full_name", None) or getattr(u, "email", None)
    for i in page:
        i["actor_name"] = names.get(i["actor_id"]) if i["actor_id"] else "System"
        i["created_at"] = i["created_at"].isoformat() if i["created_at"] else None

    return {"items": page, "total": settings_total + cfg_total + org_total}
