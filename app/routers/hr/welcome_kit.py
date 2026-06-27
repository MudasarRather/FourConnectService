"""HR Welcome Kit — templates + per-employee kits."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.onboarding import (
    WelcomeKit, WelcomeKitTemplate, WelcomeKitStatus,
)
from app.schemas.hr.onboarding import (
    WelcomeKitTemplateUpsert, WelcomeKitTemplateResponse,
    WelcomeKitCreate, WelcomeKitUpdate, WelcomeKitResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.lifecycle_guard import guard_employable
from app.utils.hr.settings_audit import log_settings_change


router = APIRouter(prefix="/hr/welcome-kit", tags=["HR — Welcome Kit"])


# ───────────────────────────── Templates ─────────────────────────────

@router.get("/templates", response_model=List[WelcomeKitTemplateResponse])
def list_templates(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(WelcomeKitTemplate)
        .filter(WelcomeKitTemplate.is_deleted == False)  # noqa: E712
        .order_by(WelcomeKitTemplate.created_at.desc())
        .all()
    )
    return [WelcomeKitTemplateResponse.model_validate(r) for r in rows]


@router.post("/templates", response_model=WelcomeKitTemplateResponse, status_code=http_status.HTTP_201_CREATED)
def create_template(
    payload: WelcomeKitTemplateUpsert,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if db.query(WelcomeKitTemplate).filter(WelcomeKitTemplate.name == payload.name).first():
        raise HTTPException(400, "Template name already exists")
    t = WelcomeKitTemplate(**payload.model_dump())
    db.add(t)
    db.flush()
    log_settings_change(db, "WELCOME_KIT", t.id, "CREATE", admin.id, note=t.name)
    db.commit()
    db.refresh(t)
    return WelcomeKitTemplateResponse.model_validate(t)


@router.patch("/templates/{tpl_id}", response_model=WelcomeKitTemplateResponse)
def update_template(
    tpl_id: UUID,
    payload: WelcomeKitTemplateUpsert,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = db.query(WelcomeKitTemplate).filter(WelcomeKitTemplate.id == tpl_id, WelcomeKitTemplate.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Template not found")
    for k, v in payload.model_dump().items():
        setattr(t, k, v)
    log_settings_change(db, "WELCOME_KIT", t.id, "UPDATE", admin.id, note=t.name)
    db.commit()
    db.refresh(t)
    return WelcomeKitTemplateResponse.model_validate(t)


# ───────────────────────────── Per-employee kits ─────────────────────────────

@router.get("/by-employee/{employee_id}", response_model=Optional[WelcomeKitResponse])
def by_employee(
    employee_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    k = (
        db.query(WelcomeKit)
        .filter(WelcomeKit.employee_id == employee_id)
        .order_by(WelcomeKit.created_at.desc())
        .first()
    )
    if not k:
        return None
    return WelcomeKitResponse.model_validate(k)


@router.post("/", response_model=WelcomeKitResponse, status_code=http_status.HTTP_201_CREATED)
def create_kit(
    payload: WelcomeKitCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id).first()
    guard_employable(emp, "create a welcome kit for this employee")
    items = payload.items or []
    if not items and payload.template_id:
        tpl = db.query(WelcomeKitTemplate).filter(WelcomeKitTemplate.id == payload.template_id).first()
        if tpl:
            items = [
                {
                    "item_name": entry.get("item_name", ""),
                    "qty": entry.get("qty", 1),
                    "included": bool(entry.get("included", True)),
                    "packed": False,
                    "delivered": False,
                }
                for entry in (tpl.default_items or [])
            ]
    kit = WelcomeKit(
        employee_id=payload.employee_id,
        process_id=payload.process_id,
        template_id=payload.template_id,
        items=items,
        status=WelcomeKitStatus.PENDING,
    )
    db.add(kit)
    db.commit()
    db.refresh(kit)
    return WelcomeKitResponse.model_validate(kit)


@router.patch("/{kit_id}", response_model=WelcomeKitResponse)
def update_kit(
    kit_id: UUID,
    payload: WelcomeKitUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    k = db.query(WelcomeKit).filter(WelcomeKit.id == kit_id).first()
    if not k:
        raise HTTPException(404, "Kit not found")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(k, key, value)
    # Stamp timestamps on status transitions
    if payload.status == WelcomeKitStatus.PACKED and k.packed_at is None:
        k.packed_at = datetime.utcnow()
        k.packed_by_user_id = admin.id
    if payload.status == WelcomeKitStatus.DISPATCHED and k.dispatched_at is None:
        k.dispatched_at = datetime.utcnow()
    if payload.status == WelcomeKitStatus.DELIVERED and k.delivered_at is None:
        k.delivered_at = datetime.utcnow()
    db.commit()
    db.refresh(k)
    return WelcomeKitResponse.model_validate(k)
