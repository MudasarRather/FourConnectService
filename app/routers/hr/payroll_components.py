"""HR Payroll — Salary Components (earning / deduction / statutory heads)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.salary_component import SalaryComponent, ComponentType, CalcType
from app.models.hr.salary_structure_component import SalaryStructureComponent
from app.models.hr.payroll_config import PayrollAuditAction
from app.schemas.hr.payroll import (
    SalaryComponentCreate, SalaryComponentUpdate, SalaryComponentResponse, SalaryComponentListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll import validate_formula, FormulaError
from app.utils.hr.payroll.service import write_audit

router = APIRouter(prefix="/hr/payroll/components", tags=["HR — Payroll Components"])


def _validate_formula_if_needed(calc_type, formula):
    if calc_type == CalcType.FORMULA:
        if not formula:
            raise HTTPException(422, "A FORMULA component requires a formula expression")
        try:
            validate_formula(formula)
        except FormulaError as e:
            raise HTTPException(422, f"Invalid formula: {e}")


@router.get("/", response_model=SalaryComponentListResponse)
def list_components(
    component_type: Optional[ComponentType] = None,
    calc_type: Optional[CalcType] = None,
    is_active: Optional[bool] = None,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    query = db.query(SalaryComponent).filter(SalaryComponent.is_deleted == False)  # noqa: E712
    if component_type:
        query = query.filter(SalaryComponent.component_type == component_type)
    if calc_type:
        query = query.filter(SalaryComponent.calc_type == calc_type)
    if is_active is not None:
        query = query.filter(SalaryComponent.is_active == is_active)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SalaryComponent.name.ilike(like), SalaryComponent.code.ilike(like)))
    total = query.count()
    items = query.order_by(SalaryComponent.sequence, SalaryComponent.code).offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.post("/", response_model=SalaryComponentResponse, status_code=201)
def create_component(
    payload: SalaryComponentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    if db.query(SalaryComponent.id).filter(SalaryComponent.code == payload.code).first():
        raise HTTPException(409, f"A component with code '{payload.code}' already exists")
    _validate_formula_if_needed(payload.calc_type, payload.formula)
    comp = SalaryComponent(**payload.model_dump(), created_by_id=current_user.id)
    db.add(comp)
    db.flush()
    write_audit(db, entity_type="COMPONENT", entity_id=comp.id,
                action=PayrollAuditAction.CREATE, actor_id=current_user.id, note=f"Created {comp.code}")
    db.commit()
    db.refresh(comp)
    return comp


@router.get("/{component_id}", response_model=SalaryComponentResponse)
def get_component(component_id: UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_superuser)):
    comp = db.query(SalaryComponent).filter(SalaryComponent.id == component_id,
                                            SalaryComponent.is_deleted == False).first()  # noqa: E712
    if not comp:
        raise HTTPException(404, "Component not found")
    return comp


@router.patch("/{component_id}", response_model=SalaryComponentResponse)
def update_component(component_id: UUID, payload: SalaryComponentUpdate,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_superuser)):
    comp = db.query(SalaryComponent).filter(SalaryComponent.id == component_id,
                                            SalaryComponent.is_deleted == False).first()  # noqa: E712
    if not comp:
        raise HTTPException(404, "Component not found")
    data = payload.model_dump(exclude_unset=True)
    if comp.is_system:
        for locked in ("component_type", "calc_type", "statutory_kind"):
            if locked in data:
                raise HTTPException(409, f"Cannot change '{locked}' on a system component")
    new_calc = data.get("calc_type", comp.calc_type)
    new_formula = data.get("formula", comp.formula)
    _validate_formula_if_needed(new_calc, new_formula)
    for k, v in data.items():
        setattr(comp, k, v)
    comp.last_updated_by_id = current_user.id
    write_audit(db, entity_type="COMPONENT", entity_id=comp.id,
                action=PayrollAuditAction.UPDATE, actor_id=current_user.id)
    db.commit()
    db.refresh(comp)
    return comp


@router.delete("/{component_id}", status_code=204)
def delete_component(component_id: UUID, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_superuser)):
    comp = db.query(SalaryComponent).filter(SalaryComponent.id == component_id,
                                            SalaryComponent.is_deleted == False).first()  # noqa: E712
    if not comp:
        raise HTTPException(404, "Component not found")
    if comp.is_system:
        raise HTTPException(409, "System components cannot be deleted")
    if db.query(SalaryStructureComponent.id).filter(
        SalaryStructureComponent.component_id == comp.id,
        SalaryStructureComponent.is_active == True,  # noqa: E712
    ).first():
        raise HTTPException(409, "Component is used by a salary structure; remove it there first")
    comp.is_deleted = True
    comp.is_active = False
    comp.last_updated_by_id = current_user.id
    write_audit(db, entity_type="COMPONENT", entity_id=comp.id,
                action=PayrollAuditAction.DELETE, actor_id=current_user.id)
    db.commit()
    return
