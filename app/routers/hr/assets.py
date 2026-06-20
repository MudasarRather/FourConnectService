"""HR Assets inventory + allocation endpoints (Asset Hangar — core lifecycle).

Owns ``/hr/assets`` — inventory CRUD, allocation/return/acknowledge, plus the
dashboard ``/stats``, single-asset detail and per-asset ``/history`` timeline.
Lifecycle guards live in ``app.utils.hr.assets.state``; every mutation appends an
immutable ``AssetHistory`` row via ``write_asset_history``.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, func, nullslast
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.asset import (
    Asset, AssetAllocation, AssetStatus, AssetType, AssetCondition, AllocationStatus,
)
from app.models.hr.asset_lifecycle import (
    AssetTransfer, AssetTransferStatus, AssetMaintenance, AssetMaintenanceStatus,
    AssetDamage, AssetDamageStatus, AssetDamageSeverity, AssetHistory, AssetEventType,
    AssetDisposal, AssetDisposalStatus,
)
from app.models.hr.onboarding import OnboardingProcess
from app.schemas.hr.asset import (
    AssetCreate, AssetUpdate, AssetResponse, AssetListResponse,
    AssetAllocationCreate, AssetAllocationReturnBody, AssetAllocationResponse,
)
from app.schemas.hr.asset_lifecycle import (
    AssetDashboardStats, AssetHistoryResponse, AssetHistoryListResponse,
)
from app.models.hr.asset_lifecycle import AssetEventType as _EventType
from app.utils.dependencies import get_current_superuser
from app.utils.hr.assets.state import assert_transition, next_status_on_return
from app.utils.hr.assets.audit import write_asset_history
from app.utils.hr.assets.responses import (
    to_asset_response, to_alloc_response, to_history_response,
)

router = APIRouter(prefix="/hr/assets", tags=["HR — Assets"])

# Damage tickets considered "open" (still consuming attention).
_OPEN_DAMAGE = (
    AssetDamageStatus.REPORTED, AssetDamageStatus.UNDER_REVIEW, AssetDamageStatus.IN_REPAIR,
)
_OPEN_DISPOSAL = (AssetDisposalStatus.REQUESTED, AssetDisposalStatus.APPROVED)


def _has_open_disposal(db: Session, asset_id: UUID) -> bool:
    return db.query(AssetDisposal.id).filter(
        AssetDisposal.asset_id == asset_id,
        AssetDisposal.status.in_(_OPEN_DISPOSAL),
        AssetDisposal.is_deleted == False,  # noqa: E712
    ).first() is not None


# ───────────────────────────── Inventory ─────────────────────────────

@router.get("/", response_model=AssetListResponse)
def list_assets(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    asset_type: Optional[AssetType] = None,
    asset_status: Optional[AssetStatus] = None,
    condition: Optional[AssetCondition] = None,
    category_id: Optional[UUID] = None,
    vendor_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    project_id: Optional[UUID] = None,
    location_id: Optional[UUID] = None,
    assigned_employee_id: Optional[UUID] = None,
    warranty_expiring: bool = False,
    search: Optional[str] = None,
    sort_by: str = Query("created_at", pattern="^(created_at|asset_code|purchase_cost|warranty_end)$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = (
        db.query(Asset)
        .options(joinedload(Asset.category), joinedload(Asset.vendor))
        .filter(Asset.is_deleted == False)  # noqa: E712
    )
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)
    if asset_status:
        q = q.filter(Asset.status == asset_status)
    if condition:
        q = q.filter(Asset.condition == condition)
    if category_id:
        q = q.filter(Asset.category_id == category_id)
    if vendor_id:
        q = q.filter(Asset.vendor_id == vendor_id)
    if department_id:
        q = q.filter(Asset.department_id == department_id)
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    if location_id:
        q = q.filter(Asset.location_id == location_id)
    if assigned_employee_id:
        q = q.filter(Asset.assigned_employee_id == assigned_employee_id)
    if warranty_expiring:
        today = date.today()
        q = q.filter(
            Asset.warranty_end != None,  # noqa: E711
            Asset.warranty_end >= today,
            Asset.warranty_end <= today + timedelta(days=30),
        )
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(Asset.asset_code).like(like),
            func.lower(Asset.serial_number).like(like),
            func.lower(Asset.model).like(like),
            func.lower(Asset.brand).like(like),
            func.lower(Asset.tag).like(like),
        ))
    total = q.count()
    # Server-side sort so ordering is stable across pages (not just the visible page).
    _SORT_COLS = {
        "created_at": Asset.created_at,
        "asset_code": Asset.asset_code,
        "purchase_cost": Asset.purchase_cost,
        "warranty_end": Asset.warranty_end,
    }
    sort_col = _SORT_COLS.get(sort_by, Asset.created_at)
    direction = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
    # NULLs (e.g. assets without a cost/warranty) always trail so they don't crowd the top.
    rows = (
        q.order_by(nullslast(direction), Asset.created_at.desc())
        .offset((page - 1) * limit).limit(limit).all()
    )
    return AssetListResponse(
        items=[to_asset_response(db, a) for a in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=AssetResponse, status_code=http_status.HTTP_201_CREATED)
def create_asset(
    payload: AssetCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if db.query(Asset).filter(Asset.asset_code == payload.asset_code).first():
        raise HTTPException(400, "Asset code already exists")
    a = Asset(**payload.model_dump(), created_by_id=admin.id)
    db.add(a)
    db.flush()
    write_asset_history(
        db, a.id, AssetEventType.CREATED, actor_user_id=admin.id,
        to_status=a.status, note=f"Asset {a.asset_code} created.",
    )
    db.commit()
    db.refresh(a)
    return to_asset_response(db, a)


# ── Dashboard stats (declared before /{asset_id} so it isn't swallowed) ──

@router.get("/stats", response_model=AssetDashboardStats)
def asset_stats(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    base = db.query(Asset).filter(Asset.is_deleted == False)  # noqa: E712
    today = date.today()

    def _count(**filt):
        q = base
        for k, v in filt.items():
            q = q.filter(getattr(Asset, k) == v)
        return q.count()

    by_status = {
        s.value: db.query(func.count(Asset.id)).filter(
            Asset.is_deleted == False, Asset.status == s  # noqa: E712
        ).scalar() or 0
        for s in AssetStatus
    }
    by_type = {
        row[0].value if hasattr(row[0], "value") else str(row[0]): row[1]
        for row in db.query(Asset.asset_type, func.count(Asset.id))
        .filter(Asset.is_deleted == False)  # noqa: E712
        .group_by(Asset.asset_type).all()
    }
    by_condition = {
        row[0].value if hasattr(row[0], "value") else str(row[0]): row[1]
        for row in db.query(Asset.condition, func.count(Asset.id))
        .filter(Asset.is_deleted == False)  # noqa: E712
        .group_by(Asset.condition).all()
    }
    total_value = db.query(func.coalesce(func.sum(Asset.purchase_cost), 0)).filter(
        Asset.is_deleted == False  # noqa: E712
    ).scalar() or 0
    unacknowledged = db.query(func.count(AssetAllocation.id)).filter(
        AssetAllocation.status == AllocationStatus.ALLOCATED,
        AssetAllocation.acknowledged_by_employee == False,  # noqa: E712
    ).scalar() or 0
    overdue_returns = db.query(func.count(AssetAllocation.id)).filter(
        AssetAllocation.status == AllocationStatus.ALLOCATED,
        AssetAllocation.expected_return_date != None,  # noqa: E711
        AssetAllocation.expected_return_date < today,
    ).scalar() or 0
    open_damages = db.query(func.count(AssetDamage.id)).filter(
        AssetDamage.status.in_(_OPEN_DAMAGE),
        AssetDamage.is_deleted == False,  # noqa: E712
    ).scalar() or 0
    warranty_expiring_30d = base.filter(
        Asset.warranty_end != None,  # noqa: E711
        Asset.warranty_end >= today,
        Asset.warranty_end <= today + timedelta(days=30),
    ).count()

    return AssetDashboardStats(
        total=base.count(),
        available=_count(status=AssetStatus.AVAILABLE),
        allocated=_count(status=AssetStatus.ALLOCATED),
        reserved=_count(status=AssetStatus.RESERVED),
        maintenance=_count(status=AssetStatus.MAINTENANCE),
        retired=_count(status=AssetStatus.RETIRED),
        total_value=float(total_value),
        unacknowledged=int(unacknowledged),
        overdue_returns=int(overdue_returns),
        open_damages=int(open_damages),
        warranty_expiring_30d=int(warranty_expiring_30d),
        by_type=by_type, by_condition=by_condition, by_status=by_status,
    )


# ── Org-wide audit-log feed (literal — declared before /{asset_id}) ──

@router.get("/audit-logs", response_model=AssetHistoryListResponse)
def asset_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    event_type: Optional[_EventType] = None,
    related_entity_type: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetHistory)
    if event_type:
        q = q.filter(AssetHistory.event_type == event_type)
    if related_entity_type:
        q = q.filter(AssetHistory.related_entity_type == related_entity_type)
    total = q.count()
    rows = q.order_by(AssetHistory.created_at.desc()).offset((page - 1) * limit).limit(limit).all()

    # Batch-resolve asset codes + types for the page (avoid N+1).
    asset_ids = {r.asset_id for r in rows if r.asset_id}
    code_map, type_map = {}, {}
    if asset_ids:
        for aid, code, atype in db.query(Asset.id, Asset.asset_code, Asset.asset_type).filter(Asset.id.in_(asset_ids)).all():
            code_map[aid] = code
            type_map[aid] = getattr(atype, "value", atype)
    items = []
    for r in rows:
        resp = to_history_response(db, r)
        resp.asset_code = code_map.get(r.asset_id)
        resp.asset_type = type_map.get(r.asset_id)
        items.append(resp)
    return AssetHistoryListResponse(
        items=items, total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


# ───────────────────────────── Allocation (list) ─────────────────────────────

@router.get("/allocations", response_model=List[AssetAllocationResponse])
def list_allocations(
    employee_id: Optional[UUID] = None,
    process_id: Optional[UUID] = None,
    allocation_status: Optional[AllocationStatus] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetAllocation).options(joinedload(AssetAllocation.asset))
    if employee_id:
        q = q.filter(AssetAllocation.employee_id == employee_id)
    if process_id:
        q = q.filter(AssetAllocation.process_id == process_id)
    if allocation_status:
        q = q.filter(AssetAllocation.status == allocation_status)
    rows = q.order_by(AssetAllocation.created_at.desc()).limit(500).all()
    return [to_alloc_response(db, al, al.asset) for al in rows]


# ── Per-asset history (two-segment path — distinct from /{asset_id}) ──

@router.get("/{asset_id}/history", response_model=List[AssetHistoryResponse])
def asset_history(
    asset_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(AssetHistory)
        .filter(AssetHistory.asset_id == asset_id)
        .order_by(AssetHistory.created_at.desc())
        .limit(limit).all()
    )
    return [to_history_response(db, h) for h in rows]


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(
    asset_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = (
        db.query(Asset)
        .options(joinedload(Asset.category), joinedload(Asset.vendor))
        .filter(Asset.id == asset_id, Asset.is_deleted == False)  # noqa: E712
        .first()
    )
    if not a:
        raise HTTPException(404, "Asset not found")
    return to_asset_response(db, a)


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: UUID,
    payload: AssetUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Asset).filter(Asset.id == asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Asset not found")
    prior_status = a.status
    data = payload.model_dump(exclude_unset=True)
    # Guard explicit status edits through the state machine (no arbitrary jumps).
    new_status = data.get("status")
    if new_status is not None and new_status != prior_status:
        assert_transition("asset", prior_status, new_status)
    for k, v in data.items():
        setattr(a, k, v)
    db.flush()
    write_asset_history(
        db, a.id, AssetEventType.UPDATED, actor_user_id=admin.id,
        from_status=prior_status, to_status=a.status,
        payload={"fields": list(data.keys())},
    )
    db.commit()
    db.refresh(a)
    return to_asset_response(db, a)


@router.delete("/{asset_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Asset).filter(Asset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "Asset not found")
    if a.status == AssetStatus.ALLOCATED:
        raise HTTPException(409, "Cannot delete an allocated asset; return it first.")
    # Block deletion while any open lifecycle record references the asset.
    open_alloc = db.query(AssetAllocation.id).filter(
        AssetAllocation.asset_id == asset_id,
        AssetAllocation.status == AllocationStatus.ALLOCATED,
    ).first()
    open_transfer = db.query(AssetTransfer.id).filter(
        AssetTransfer.asset_id == asset_id,
        AssetTransfer.status.in_([AssetTransferStatus.REQUESTED, AssetTransferStatus.APPROVED]),
        AssetTransfer.is_deleted == False,  # noqa: E712
    ).first()
    open_maint = db.query(AssetMaintenance.id).filter(
        AssetMaintenance.asset_id == asset_id,
        AssetMaintenance.status.in_([AssetMaintenanceStatus.SCHEDULED, AssetMaintenanceStatus.IN_PROGRESS]),
        AssetMaintenance.is_deleted == False,  # noqa: E712
    ).first()
    if open_alloc or open_transfer or open_maint or _has_open_disposal(db, asset_id):
        raise HTTPException(409, "Cannot delete: asset has an open allocation / transfer / maintenance / disposal.")
    a.is_deleted = True
    db.flush()
    write_asset_history(
        db, a.id, AssetEventType.DELETED, actor_user_id=admin.id,
        from_status=a.status, note="Asset soft-deleted.",
    )
    db.commit()


# ───────────────────────────── Allocate / Return / Acknowledge ─────────────────────────────

@router.post("/{asset_id}/allocate", response_model=AssetAllocationResponse, status_code=http_status.HTTP_201_CREATED)
def allocate_asset(
    asset_id: UUID,
    payload: AssetAllocationCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not asset:
        raise HTTPException(404, "Asset not found")
    if asset.status != AssetStatus.AVAILABLE:
        raise HTTPException(409, f"Asset is {asset.status.value}; only AVAILABLE assets can be allocated.")
    if asset.condition == AssetCondition.RETIRED:
        raise HTTPException(409, "Asset condition is RETIRED; cannot allocate.")
    if _has_open_disposal(db, asset_id):
        raise HTTPException(409, "Asset has a pending disposal; cannot allocate.")
    if not db.query(Employee).filter(Employee.id == payload.employee_id).first():
        raise HTTPException(404, "Employee not found")

    prior = asset.status
    alloc = AssetAllocation(
        asset_id=asset.id,
        employee_id=payload.employee_id,
        process_id=payload.process_id,
        expected_return_date=payload.expected_return_date,
        condition_on_issue=payload.condition_on_issue or asset.condition,
        status=AllocationStatus.ALLOCATED,
        issued_by_user_id=admin.id,
        notes=payload.notes,
    )
    db.add(alloc)
    asset.status = AssetStatus.ALLOCATED
    asset.assigned_employee_id = payload.employee_id
    db.flush()
    write_asset_history(
        db, asset.id, AssetEventType.ALLOCATED, actor_user_id=admin.id,
        actor_employee_id=payload.employee_id, from_status=prior, to_status=asset.status,
        related_entity_type="allocation", related_entity_id=alloc.id,
    )

    if payload.process_id:
        from app.routers.hr.onboarding import _recalculate_progress
        proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == payload.process_id).first()
        if proc:
            _recalculate_progress(db, proc)
    db.commit()
    db.refresh(alloc)
    return to_alloc_response(db, alloc, asset)


@router.post("/allocations/{alloc_id}/return", response_model=AssetAllocationResponse)
def return_asset(
    alloc_id: UUID,
    payload: AssetAllocationReturnBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    al = db.query(AssetAllocation).filter(AssetAllocation.id == alloc_id).first()
    if not al:
        raise HTTPException(404, "Allocation not found")
    if al.status != AllocationStatus.ALLOCATED:
        raise HTTPException(409, f"Allocation is {al.status.value}; nothing to return.")
    assert_transition("allocation", al.status, payload.status)

    al.status = payload.status
    al.returned_date = payload.returned_date
    al.condition_on_return = payload.condition_on_return
    al.returned_to_user_id = admin.id
    al.return_requested = False  # request (if any) is now fulfilled — clear the flag
    if payload.notes:
        al.notes = (al.notes or "") + ("\n" if al.notes else "") + payload.notes

    asset = db.query(Asset).filter(Asset.id == al.asset_id).first()
    new_asset_status = None
    if asset:
        prior = asset.status
        new_asset_status = next_status_on_return(payload.status)
        asset.status = new_asset_status
        asset.assigned_employee_id = None
        if payload.condition_on_return:
            asset.condition = payload.condition_on_return
        event = (
            AssetEventType.RETURNED if payload.status == AllocationStatus.RETURNED else
            AssetEventType.MARKED_LOST if payload.status == AllocationStatus.LOST else
            AssetEventType.MARKED_DAMAGED
        )
        write_asset_history(
            db, asset.id, event, actor_user_id=admin.id,
            actor_employee_id=al.employee_id, from_status=prior, to_status=new_asset_status,
            related_entity_type="allocation", related_entity_id=al.id,
        )
        # Auto-open a damage ticket when the asset comes back damaged / in poor shape.
        if payload.status == AllocationStatus.DAMAGED or payload.condition_on_return == AssetCondition.POOR:
            dmg = AssetDamage(
                asset_id=asset.id, allocation_id=al.id,
                severity=AssetDamageSeverity.MODERATE,
                status=AssetDamageStatus.UNDER_REVIEW,
                reported_by_user_id=admin.id, reported_by_employee_id=al.employee_id,
                title="Returned damaged",
                description=payload.notes or "Asset returned in damaged / poor condition.",
            )
            db.add(dmg)
            db.flush()
            write_asset_history(
                db, asset.id, AssetEventType.DAMAGE_REPORTED, actor_user_id=admin.id,
                related_entity_type="damage", related_entity_id=dmg.id,
                note="Auto-opened on damaged return.",
            )
    db.commit()
    db.refresh(al)
    return to_alloc_response(db, al, asset)


@router.post("/allocations/{alloc_id}/acknowledge", response_model=AssetAllocationResponse)
def acknowledge_allocation(
    alloc_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    al = db.query(AssetAllocation).filter(AssetAllocation.id == alloc_id).first()
    if not al:
        raise HTTPException(404, "Allocation not found")
    if not al.acknowledged_by_employee:  # idempotent — don't overwrite an earlier ack
        al.acknowledged_by_employee = True
        al.acknowledged_at = datetime.utcnow()
        db.flush()
        write_asset_history(
            db, al.asset_id, AssetEventType.ACKNOWLEDGED, actor_user_id=admin.id,
            actor_employee_id=al.employee_id,
            related_entity_type="allocation", related_entity_id=al.id,
        )
        db.commit()
        db.refresh(al)
    return to_alloc_response(db, al)
