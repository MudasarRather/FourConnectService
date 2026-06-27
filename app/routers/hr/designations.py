from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.designation import Designation
from app.models.hr.employee import Employee
from app.models.hr.recruitment import JobRequisition, JobPosition
from app.schemas.hr.designation import (
    DesignationCreate, DesignationUpdate, DesignationResponse,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/designations", tags=["HR — Designations"])


def _track_actor(db: Session, actor_id):
    db.info["audit_actor_id"] = str(actor_id)


# ─── usage / dependency counting ─────────────────────────────────────────────
def _usage_for_ids(db: Session, ids):
    """Bulk-count downstream references for a set of designation ids in four
    grouped queries (no N+1). Returns
    {id_str: {employees, reporting_children, requisitions, positions}}.
    `employees` counts only live (non-deleted) holders — the figure that gates
    deletion. `reporting_children` are titles that report UP to this one."""
    ids = list(ids)
    out = {str(i): {"employees": 0, "reporting_children": 0, "requisitions": 0, "positions": 0} for i in ids}
    if not ids:
        return out

    emp_rows = (
        db.query(Employee.designation_id, func.count(Employee.id))
        .filter(Employee.designation_id.in_(ids), Employee.is_deleted == False)  # noqa: E712
        .group_by(Employee.designation_id).all()
    )
    for did, n in emp_rows:
        if did is not None:
            out[str(did)]["employees"] = int(n)

    rep_rows = (
        db.query(Designation.reporting_to_designation_id, func.count(Designation.id))
        .filter(Designation.reporting_to_designation_id.in_(ids), Designation.is_deleted == False)  # noqa: E712
        .group_by(Designation.reporting_to_designation_id).all()
    )
    for did, n in rep_rows:
        if did is not None:
            out[str(did)]["reporting_children"] = int(n)

    req_rows = (
        db.query(JobRequisition.designation_id, func.count(JobRequisition.id))
        .filter(JobRequisition.designation_id.in_(ids))
        .group_by(JobRequisition.designation_id).all()
    )
    for did, n in req_rows:
        if did is not None:
            out[str(did)]["requisitions"] = int(n)

    pos_rows = (
        db.query(JobPosition.designation_id, func.count(JobPosition.id))
        .filter(JobPosition.designation_id.in_(ids))
        .group_by(JobPosition.designation_id).all()
    )
    for did, n in pos_rows:
        if did is not None:
            out[str(did)]["positions"] = int(n)

    return out


# ─── reporting-line integrity (self-reference + cycle guard) ─────────────────
def _would_cycle(db: Session, *, start_parent_id, target_id, max_depth: int = 60) -> bool:
    """Walk UP the reporting chain from `start_parent_id`; True if `target_id` is
    reached (i.e. pointing `target_id` at `start_parent_id` closes a loop)."""
    seen = set()
    cur = start_parent_id
    depth = 0
    while cur is not None and depth < max_depth:
        if cur == target_id:
            return True
        if cur in seen:
            break  # a pre-existing loop elsewhere — stop walking
        seen.add(cur)
        row = db.query(Designation.reporting_to_designation_id).filter(Designation.id == cur).first()
        cur = row[0] if row else None
        depth += 1
    return False


def _validate_reporting(db: Session, *, this_id, parent_id):
    """Enforce reporting-line invariants. `this_id` is None on create."""
    if parent_id is None:
        return
    if this_id is not None and parent_id == this_id:
        raise HTTPException(400, "A designation cannot report to itself")
    if not db.query(Designation.id).filter(
        Designation.id == parent_id, Designation.is_deleted == False  # noqa: E712
    ).first():
        raise HTTPException(400, "The selected reporting designation does not exist")
    if this_id is not None and _would_cycle(db, start_parent_id=parent_id, target_id=this_id):
        raise HTTPException(400, "That reporting line would create a cycle in the hierarchy")


# ─── list ────────────────────────────────────────────────────────────────────
@router.get("/", response_model=List[DesignationResponse])
def list_designations(
    department_id: Optional[UUID] = Query(None),
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Designation)
    if not include_deleted:
        q = q.filter(Designation.is_deleted == False)  # noqa: E712
    if department_id:
        q = q.filter(Designation.department_id == department_id)
    return q.order_by(Designation.name).all()


# ── usage (LITERAL route — declared before /{designation_id} so it isn't
#    swallowed by the UUID path converter) ──────────────────────────────────
@router.get("/usage")
def designations_usage(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """One-shot dependency map for every live designation — powers the spire
    headcounts, card badges and the delete pre-flight without an N+1 fan-out."""
    ids = [r[0] for r in db.query(Designation.id).filter(Designation.is_deleted == False).all()]  # noqa: E712
    return {"items": _usage_for_ids(db, ids)}


@router.get("/{designation_id}/usage")
def designation_usage(
    designation_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Fresh, authoritative dependency counts for a single title (delete time)."""
    if not db.query(Designation.id).filter(Designation.id == designation_id).first():
        raise HTTPException(404, "Designation not found")
    return _usage_for_ids(db, [designation_id])[str(designation_id)]


# ─── create ────────────────────────────────────────────────────────────────
@router.post("/", response_model=DesignationResponse, status_code=status.HTTP_201_CREATED)
def create_designation(
    payload: DesignationCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    if db.query(Designation).filter(Designation.code == payload.code).first():
        raise HTTPException(400, "Designation code already exists")
    _validate_reporting(db, this_id=None, parent_id=payload.reporting_to_designation_id)
    d = Designation(**payload.model_dump(exclude_unset=True))
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


@router.get("/{designation_id}", response_model=DesignationResponse)
def get_designation(
    designation_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    d = db.query(Designation).filter(Designation.id == designation_id).first()
    if not d:
        raise HTTPException(404, "Designation not found")
    return d


@router.patch("/{designation_id}", response_model=DesignationResponse)
def update_designation(
    designation_id: UUID,
    payload: DesignationUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    d = db.query(Designation).filter(Designation.id == designation_id).first()
    if not d:
        raise HTTPException(404, "Designation not found")
    update = payload.model_dump(exclude_unset=True)
    if "code" in update and update["code"] != d.code:
        if db.query(Designation).filter(Designation.code == update["code"], Designation.id != designation_id).first():
            raise HTTPException(400, "Designation code already exists")
    if "reporting_to_designation_id" in update:
        _validate_reporting(db, this_id=designation_id, parent_id=update["reporting_to_designation_id"])
    for k, v in update.items():
        setattr(d, k, v)
    db.commit()
    db.refresh(d)
    return d


@router.delete("/{designation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_designation(
    designation_id: UUID,
    reason: Optional[str] = Query(None, description="Optional removal reason, sealed into the audit ledger."),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    d = db.query(Designation).filter(Designation.id == designation_id).first()
    if not d:
        raise HTTPException(404, "Designation not found")

    # Pre-flight guard: a soft-delete is an UPDATE, so the FK never fires — we
    # must refuse here or live employees would be left pointing at a tombstone.
    holders = (
        db.query(func.count(Employee.id))
        .filter(Employee.designation_id == designation_id, Employee.is_deleted == False)  # noqa: E712
        .scalar()
    ) or 0
    if holders > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{holders} employee{'s' if holders != 1 else ''} still hold this designation — "
            "reassign them before it can be removed.",
        )

    if reason and reason.strip():
        db.info["audit_note"] = reason.strip()
    d.is_deleted = True
    db.commit()
    return None
