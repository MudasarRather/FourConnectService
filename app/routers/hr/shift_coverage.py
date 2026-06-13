"""HR Coverage Management — minimum-staffing rules + live shortfall alerts."""
from __future__ import annotations

from datetime import date
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, and_, func, distinct
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.shift import Shift, EmployeeShiftAssignment
from app.models.hr.shift_coverage import ShiftCoverageRule
from app.schemas.hr.shift_planning import (
    CoverageRuleCreate, CoverageRuleUpdate, CoverageRuleResponse,
    CoverageAlert, CoverageAlertsResponse,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/shift-coverage", tags=["HR — Coverage"])


def _active_on(on_date: date):
    return and_(
        EmployeeShiftAssignment.effective_from <= on_date,
        or_(EmployeeShiftAssignment.effective_until.is_(None),
            EmployeeShiftAssignment.effective_until >= on_date),
    )


def _rule_response(db: Session, r: ShiftCoverageRule) -> CoverageRuleResponse:
    sh = db.query(Shift).filter(Shift.id == r.shift_id).first()
    dept_name = None
    if r.department_id:
        d = db.query(Department.name).filter(Department.id == r.department_id).first()
        dept_name = d[0] if d else None
    return CoverageRuleResponse(
        id=r.id, shift_id=r.shift_id,
        shift_code=sh.code if sh else None, shift_name=sh.name if sh else None,
        department_id=r.department_id, department_name=dept_name,
        min_staff=r.min_staff, label=r.label, critical=r.critical,
        is_active=r.is_active, created_at=r.created_at)


@router.get("/alerts", response_model=CoverageAlertsResponse)
def coverage_alerts(
    on_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    target = on_date or date.today()
    rules = (db.query(ShiftCoverageRule)
             .filter(ShiftCoverageRule.is_deleted == False,  # noqa: E712
                     ShiftCoverageRule.is_active == True)
             .order_by(ShiftCoverageRule.critical.desc()).all())
    alerts, total_short, crit = [], 0, 0
    for r in rules:
        cq = db.query(func.count(distinct(EmployeeShiftAssignment.employee_id))).filter(
            EmployeeShiftAssignment.shift_id == r.shift_id, _active_on(target))
        if r.department_id:
            cq = cq.join(Employee, Employee.id == EmployeeShiftAssignment.employee_id).filter(
                Employee.department_id == r.department_id)
        assigned = cq.scalar() or 0
        shortfall = max(0, (r.min_staff or 0) - assigned)
        if shortfall > 0:
            total_short += shortfall
            status_v = "CRITICAL" if r.critical else "WARN"
            if r.critical:
                crit += 1
        else:
            status_v = "OK"
        sh = db.query(Shift).filter(Shift.id == r.shift_id).first()
        dept_name = None
        if r.department_id:
            d = db.query(Department.name).filter(Department.id == r.department_id).first()
            dept_name = d[0] if d else None
        alerts.append(CoverageAlert(
            rule_id=r.id, shift_id=r.shift_id,
            shift_code=sh.code if sh else None, shift_name=sh.name if sh else None,
            department_id=r.department_id, department_name=dept_name,
            min_staff=r.min_staff or 0, assigned=assigned, shortfall=shortfall,
            critical=r.critical, status=status_v))
    # surface shortfalls first
    alerts.sort(key=lambda a: (a.status == "OK", -a.shortfall))
    return CoverageAlertsResponse(
        on_date=target, alerts=alerts, total_shortfall=total_short, critical_count=crit)


@router.get("/", response_model=dict)
def list_rules(
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(ShiftCoverageRule).filter(ShiftCoverageRule.is_deleted == False)  # noqa: E712
    if is_active is not None:
        q = q.filter(ShiftCoverageRule.is_active == is_active)
    total = q.count()
    rows = q.order_by(ShiftCoverageRule.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [_rule_response(db, r) for r in rows],
        "total": total, "page": page, "limit": limit,
        "total_pages": ceil(total / limit) if limit else 1,
    }


@router.post("/", response_model=CoverageRuleResponse, status_code=http_status.HTTP_201_CREATED)
def create_rule(
    payload: CoverageRuleCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not db.query(Shift).filter(Shift.id == payload.shift_id, Shift.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Shift not found")
    r = ShiftCoverageRule(
        shift_id=payload.shift_id, department_id=payload.department_id,
        min_staff=max(0, payload.min_staff), label=payload.label,
        critical=payload.critical, created_by_id=admin.id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return _rule_response(db, r)


@router.patch("/{rule_id}", response_model=CoverageRuleResponse)
def update_rule(
    rule_id: UUID,
    payload: CoverageRuleUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftCoverageRule).filter(
        ShiftCoverageRule.id == rule_id, ShiftCoverageRule.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Coverage rule not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return _rule_response(db, r)


@router.delete("/{rule_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftCoverageRule).filter(ShiftCoverageRule.id == rule_id).first()
    if not r:
        raise HTTPException(404, "Coverage rule not found")
    r.is_deleted = True
    db.commit()
