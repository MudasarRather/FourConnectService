"""HR Overtime Rules — config layer that scores overtime hours.

CRUD over OT rules plus a ``/resolve`` helper the UI uses to preview which rule
applies to a given (ot_type, hours) and the resulting multiplier / payable hours.
Does NOT mutate the existing OvertimeRequest approval flow.
"""
from __future__ import annotations

from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.overtime import OtType
from app.models.hr.overtime_rule import OvertimeRule
from app.schemas.hr.shift_ops import (
    OvertimeRuleCreate, OvertimeRuleUpdate, OvertimeRuleResponse, OtResolveResult,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/overtime-rules", tags=["HR — Overtime Rules"])


def _resp(r: OvertimeRule) -> OvertimeRuleResponse:
    return OvertimeRuleResponse(
        id=r.id, name=r.name,
        ot_type=r.ot_type.value if hasattr(r.ot_type, "value") else str(r.ot_type),
        threshold_hours=float(r.threshold_hours or 0), multiplier=float(r.multiplier or 1),
        max_ot_hours=float(r.max_ot_hours) if r.max_ot_hours is not None else None,
        approval_required=r.approval_required, department_ids=r.department_ids or [],
        priority=int(r.priority or 0), description=r.description,
        is_active=r.is_active, created_at=r.created_at,
    )


@router.get("/", response_model=dict)
def list_rules(
    ot_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(OvertimeRule).filter(OvertimeRule.is_deleted == False)  # noqa: E712
    if ot_type:
        q = q.filter(OvertimeRule.ot_type == ot_type)
    if is_active is not None:
        q = q.filter(OvertimeRule.is_active == is_active)
    total = q.count()
    rows = q.order_by(OvertimeRule.priority.desc(), OvertimeRule.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"items": [_resp(r) for r in rows], "total": total, "page": page, "limit": limit,
            "total_pages": ceil(total / limit) if limit else 1}


@router.get("/resolve", response_model=OtResolveResult)
def resolve_rule(
    ot_type: str = Query(...),
    hours: float = Query(..., ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rule = (db.query(OvertimeRule)
            .filter(OvertimeRule.is_deleted == False, OvertimeRule.is_active == True,  # noqa: E712
                    OvertimeRule.ot_type == ot_type)
            .order_by(OvertimeRule.priority.desc(), OvertimeRule.created_at.desc()).first())
    if not rule:
        return OtResolveResult(matched=False, ot_type=ot_type, multiplier=1.0,
                               requested_hours=hours, payable_hours=hours, approval_required=True)
    cap = float(rule.max_ot_hours) if rule.max_ot_hours is not None else None
    payable = min(hours, cap) if cap is not None else hours
    return OtResolveResult(
        matched=True, rule_id=rule.id, rule_name=rule.name, ot_type=ot_type,
        multiplier=float(rule.multiplier or 1), requested_hours=hours,
        payable_hours=round(payable, 2), capped=cap is not None and hours > cap,
        approval_required=rule.approval_required)


@router.post("/", response_model=OvertimeRuleResponse, status_code=http_status.HTTP_201_CREATED)
def create_rule(
    payload: OvertimeRuleCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if payload.ot_type not in OtType.__members__:
        raise HTTPException(400, "Invalid ot_type")
    r = OvertimeRule(
        name=payload.name, ot_type=OtType(payload.ot_type),
        threshold_hours=payload.threshold_hours, multiplier=payload.multiplier,
        max_ot_hours=payload.max_ot_hours, approval_required=payload.approval_required,
        department_ids=[str(d) for d in (payload.department_ids or [])],
        priority=payload.priority, description=payload.description, created_by_id=admin.id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return _resp(r)


@router.patch("/{rule_id}", response_model=OvertimeRuleResponse)
def update_rule(
    rule_id: UUID,
    payload: OvertimeRuleUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(OvertimeRule).filter(OvertimeRule.id == rule_id, OvertimeRule.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rule not found")
    data = payload.model_dump(exclude_unset=True)
    if "ot_type" in data and data["ot_type"]:
        if data["ot_type"] not in OtType.__members__:
            raise HTTPException(400, "Invalid ot_type")
        r.ot_type = OtType(data.pop("ot_type"))
    if "department_ids" in data and data["department_ids"] is not None:
        r.department_ids = [str(d) for d in data.pop("department_ids")]
    for k, v in data.items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return _resp(r)


@router.delete("/{rule_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(OvertimeRule).filter(OvertimeRule.id == rule_id).first()
    if not r:
        raise HTTPException(404, "Rule not found")
    r.is_deleted = True
    db.commit()
