"""HR Assets inventory + allocation endpoints."""
from __future__ import annotations

from datetime import datetime, date
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.asset import (
    Asset, AssetAllocation, AssetStatus, AssetType, AllocationStatus,
)
from app.models.hr.onboarding import OnboardingProcess
from app.schemas.hr.asset import (
    AssetCreate, AssetUpdate, AssetResponse, AssetListResponse,
    AssetAllocationCreate, AssetAllocationReturnBody, AssetAllocationResponse,
)
from app.utils.dependencies import get_current_superuser


router = APIRouter(prefix="/hr/assets", tags=["HR — Assets"])


def _employee_label(db: Session, employee_id: Optional[UUID]) -> Optional[str]:
    if not employee_id:
        return None
    row = (
        db.query(User.full_name)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.id == employee_id)
        .first()
    )
    return row[0] if row else None


def _to_asset_response(db: Session, a: Asset) -> AssetResponse:
    return AssetResponse(
        id=a.id, asset_code=a.asset_code, asset_type=a.asset_type,
        brand=a.brand, model=a.model, serial_number=a.serial_number,
        purchase_date=a.purchase_date, purchase_cost=a.purchase_cost,
        condition=a.condition, status=a.status,
        assigned_employee_id=a.assigned_employee_id,
        assigned_employee_name=_employee_label(db, a.assigned_employee_id),
        location_id=a.location_id, notes=a.notes,
        created_at=a.created_at, updated_at=a.updated_at,
    )


def _to_alloc_response(db: Session, al: AssetAllocation, asset: Optional[Asset] = None) -> AssetAllocationResponse:
    a = asset or db.query(Asset).filter(Asset.id == al.asset_id).first()
    return AssetAllocationResponse(
        id=al.id, asset_id=al.asset_id,
        asset_code=a.asset_code if a else None, asset_type=a.asset_type if a else None,
        employee_id=al.employee_id, employee_name=_employee_label(db, al.employee_id),
        process_id=al.process_id, allocated_date=al.allocated_date,
        expected_return_date=al.expected_return_date, returned_date=al.returned_date,
        condition_on_issue=al.condition_on_issue, condition_on_return=al.condition_on_return,
        status=al.status, acknowledged_by_employee=al.acknowledged_by_employee,
        acknowledged_at=al.acknowledged_at, notes=al.notes, created_at=al.created_at,
    )


# ───────────────────────────── Inventory ─────────────────────────────

@router.get("/", response_model=AssetListResponse)
def list_assets(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    asset_type: Optional[AssetType] = None,
    asset_status: Optional[AssetStatus] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(Asset).filter(Asset.is_deleted == False)  # noqa: E712
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)
    if asset_status:
        q = q.filter(Asset.status == asset_status)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(Asset.asset_code).like(like),
            func.lower(Asset.serial_number).like(like),
            func.lower(Asset.model).like(like),
            func.lower(Asset.brand).like(like),
        ))
    total = q.count()
    rows = q.order_by(Asset.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AssetListResponse(
        items=[_to_asset_response(db, a) for a in rows],
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
    db.commit()
    db.refresh(a)
    return _to_asset_response(db, a)


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: UUID,
    payload: AssetUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(Asset).filter(Asset.id == asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Asset not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return _to_asset_response(db, a)


@router.delete("/{asset_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(Asset).filter(Asset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "Asset not found")
    if a.status == AssetStatus.ALLOCATED:
        raise HTTPException(409, "Cannot delete an allocated asset; return it first.")
    a.is_deleted = True
    db.commit()


# ───────────────────────────── Allocation ─────────────────────────────

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
    return [_to_alloc_response(db, al, al.asset) for al in rows]


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
        raise HTTPException(409, f"Asset is {asset.status.value}; cannot allocate.")
    if not db.query(Employee).filter(Employee.id == payload.employee_id).first():
        raise HTTPException(404, "Employee not found")

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

    if payload.process_id:
        from app.routers.hr.onboarding import _recalculate_progress
        proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == payload.process_id).first()
        if proc:
            _recalculate_progress(db, proc)
    db.commit()
    db.refresh(alloc)
    return _to_alloc_response(db, alloc, asset)


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
        raise HTTPException(409, f"Allocation is {al.status.value}")
    al.status = payload.status
    al.returned_date = payload.returned_date
    al.condition_on_return = payload.condition_on_return
    al.returned_to_user_id = admin.id
    if payload.notes:
        al.notes = (al.notes or "") + ("\n" if al.notes else "") + payload.notes
    asset = db.query(Asset).filter(Asset.id == al.asset_id).first()
    if asset:
        asset.status = AssetStatus.AVAILABLE if payload.status == AllocationStatus.RETURNED else AssetStatus.RETIRED
        asset.assigned_employee_id = None
        if payload.condition_on_return:
            asset.condition = payload.condition_on_return
    db.commit()
    db.refresh(al)
    return _to_alloc_response(db, al, asset)


@router.post("/allocations/{alloc_id}/acknowledge", response_model=AssetAllocationResponse)
def acknowledge_allocation(
    alloc_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    al = db.query(AssetAllocation).filter(AssetAllocation.id == alloc_id).first()
    if not al:
        raise HTTPException(404, "Allocation not found")
    al.acknowledged_by_employee = True
    al.acknowledged_at = datetime.utcnow()
    db.commit()
    db.refresh(al)
    return _to_alloc_response(db, al)
