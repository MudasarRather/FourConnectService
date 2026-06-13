"""HR Night Shift Policies + night-ops roster.

CRUD (upsert by shift) over per-shift night config, plus a roster endpoint that
lists employees on NIGHT-type shifts for a date with their policy details.
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.shift import Shift, EmployeeShiftAssignment, ShiftType
from app.models.hr.night_policy import NightShiftPolicy
from app.schemas.hr.shift_ops import (
    NightPolicyUpsert, NightPolicyResponse, NightRosterEmployee, NightRosterResponse,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/night-policies", tags=["HR — Night Shifts"])


def _resp(db: Session, p: NightShiftPolicy) -> NightPolicyResponse:
    sh = db.query(Shift).filter(Shift.id == p.shift_id).first()
    return NightPolicyResponse(
        id=p.id, shift_id=p.shift_id, shift_code=sh.code if sh else None, shift_name=sh.name if sh else None,
        allowance_amount=float(p.allowance_amount or 0), overtime_rate=float(p.overtime_rate or 0),
        transport_required=p.transport_required, meal_eligible=p.meal_eligible,
        safety_compliance=p.safety_compliance, notes=p.notes, created_at=p.created_at)


@router.get("/", response_model=list[NightPolicyResponse])
def list_policies(db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    rows = db.query(NightShiftPolicy).filter(NightShiftPolicy.is_deleted == False).all()  # noqa: E712
    return [_resp(db, p) for p in rows]


@router.put("/", response_model=NightPolicyResponse)
def upsert_policy(
    payload: NightPolicyUpsert,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not db.query(Shift).filter(Shift.id == payload.shift_id, Shift.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Shift not found")
    p = db.query(NightShiftPolicy).filter(NightShiftPolicy.shift_id == payload.shift_id, NightShiftPolicy.is_deleted == False).first()  # noqa: E712
    if not p:
        p = NightShiftPolicy(shift_id=payload.shift_id, created_by_id=admin.id)
        db.add(p)
    p.allowance_amount = payload.allowance_amount
    p.overtime_rate = payload.overtime_rate
    p.transport_required = payload.transport_required
    p.meal_eligible = payload.meal_eligible
    p.safety_compliance = payload.safety_compliance
    p.notes = payload.notes
    db.commit()
    db.refresh(p)
    return _resp(db, p)


@router.delete("/{policy_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_policy(policy_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    p = db.query(NightShiftPolicy).filter(NightShiftPolicy.id == policy_id).first()
    if not p:
        raise HTTPException(404, "Policy not found")
    p.is_deleted = True
    db.commit()


@router.get("/roster", response_model=NightRosterResponse)
def night_roster(
    on_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    target = on_date or date.today()
    rows = (
        db.query(EmployeeShiftAssignment, Shift, User.full_name)
        .join(Shift, Shift.id == EmployeeShiftAssignment.shift_id)
        .join(Employee, Employee.id == EmployeeShiftAssignment.employee_id)
        .join(User, User.id == Employee.user_id)
        .filter(Shift.shift_type == ShiftType.NIGHT,
                EmployeeShiftAssignment.effective_from <= target,
                or_(EmployeeShiftAssignment.effective_until.is_(None),
                    EmployeeShiftAssignment.effective_until >= target)).all())
    policies = {p.shift_id: p for p in db.query(NightShiftPolicy).filter(NightShiftPolicy.is_deleted == False).all()}  # noqa: E712
    staff = []
    for a, s, name in rows:
        if s.weekly_off_days and target.weekday() in (s.weekly_off_days or []):
            continue
        pol = policies.get(s.id)
        staff.append(NightRosterEmployee(
            employee_id=a.employee_id, employee_name=name, shift_id=s.id,
            shift_code=s.code, shift_name=s.name,
            allowance_amount=float(pol.allowance_amount) if pol else 0.0,
            transport_required=bool(pol.transport_required) if pol else False,
            meal_eligible=bool(pol.meal_eligible) if pol else False))
    return NightRosterResponse(on_date=target, count=len(staff), staff=staff)
