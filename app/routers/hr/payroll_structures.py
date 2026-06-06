"""HR Payroll — Salary Structures + component links + CTC preview."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.salary_structure import SalaryStructure
from app.models.hr.salary_structure_component import SalaryStructureComponent
from app.models.hr.salary_component import SalaryComponent
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.models.hr.payroll_config import PayrollAuditAction
from app.schemas.hr.payroll import (
    SalaryStructureCreate, SalaryStructureUpdate, SalaryStructureResponse, SalaryStructureListResponse,
    StructureComponentLinkCreate, StructureComponentLinkUpdate, StructureComponentLinkResponse,
    PreviewRequest, PreviewResponse, PreviewLine,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll import resolve_structure, resolve_components, compute_payslip, load_config, fy_for
from app.utils.hr.payroll.service import write_audit, fy_for_period
from datetime import date

router = APIRouter(prefix="/hr/payroll/structures", tags=["HR — Payroll Structures"])


def _link_to_response(link: SalaryStructureComponent) -> dict:
    c = link.component
    return {
        "id": link.id, "component_id": link.component_id, "sequence": link.sequence,
        "override_calc_type": link.override_calc_type, "override_formula": link.override_formula,
        "override_percent_value": link.override_percent_value,
        "override_percent_of_code": link.override_percent_of_code,
        "override_flat_amount": link.override_flat_amount, "is_active": link.is_active,
        "component_code": c.code if c else None, "component_name": c.name if c else None,
        "component_type": c.component_type if c else None, "calc_type": c.calc_type if c else None,
    }


def _serialize_structure(s: SalaryStructure, with_components=True) -> dict:
    comps = [l for l in s.components if l.is_active] if with_components else []
    return {
        "id": s.id, "code": s.code, "name": s.name, "description": s.description,
        "grade_id": s.grade_id, "pay_scale": s.pay_scale,
        "effective_from": s.effective_from, "effective_to": s.effective_to,
        "is_default": s.is_default, "is_active": s.is_active, "created_at": s.created_at,
        "pf_restrict_to_ceiling": s.pf_restrict_to_ceiling,
        "component_count": len(comps),
        "components": [_link_to_response(l) for l in sorted(comps, key=lambda x: x.sequence)] if with_components else [],
    }


@router.get("/", response_model=SalaryStructureListResponse)
def list_structures(q: Optional[str] = None, is_active: Optional[bool] = None,
                    skip: int = 0, limit: int = Query(100, ge=1, le=200),
                    db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    query = db.query(SalaryStructure).options(
        joinedload(SalaryStructure.components).joinedload(SalaryStructureComponent.component)
    ).filter(SalaryStructure.is_deleted == False)  # noqa: E712
    if is_active is not None:
        query = query.filter(SalaryStructure.is_active == is_active)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SalaryStructure.name.ilike(like), SalaryStructure.code.ilike(like)))
    total = query.count()
    items = query.order_by(SalaryStructure.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": [_serialize_structure(s) for s in items], "total": total}


@router.post("/", response_model=SalaryStructureResponse, status_code=201)
def create_structure(payload: SalaryStructureCreate, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_superuser)):
    if db.query(SalaryStructure.id).filter(SalaryStructure.code == payload.code).first():
        raise HTTPException(409, f"A structure with code '{payload.code}' already exists")
    s = SalaryStructure(
        code=payload.code, name=payload.name, description=payload.description,
        grade_id=payload.grade_id, pay_scale=payload.pay_scale,
        effective_from=payload.effective_from, effective_to=payload.effective_to,
        is_default=payload.is_default, pf_restrict_to_ceiling=payload.pf_restrict_to_ceiling,
        created_by_id=current_user.id,
    )
    db.add(s)
    db.flush()
    if payload.is_default:
        db.query(SalaryStructure).filter(SalaryStructure.id != s.id).update({"is_default": False})
    seq = 10
    for link in payload.components:
        db.add(SalaryStructureComponent(
            structure_id=s.id, component_id=link.component_id,
            sequence=link.sequence if link.sequence is not None else seq,
            override_calc_type=link.override_calc_type, override_formula=link.override_formula,
            override_percent_value=link.override_percent_value,
            override_percent_of_code=link.override_percent_of_code,
            override_flat_amount=link.override_flat_amount,
        ))
        seq += 10
    write_audit(db, entity_type="STRUCTURE", entity_id=s.id, action=PayrollAuditAction.CREATE,
                actor_id=current_user.id, note=f"Created {s.code}")
    db.commit()
    db.refresh(s)
    return _serialize_structure(s)


@router.get("/{structure_id}", response_model=SalaryStructureResponse)
def get_structure_detail(structure_id: UUID, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_superuser)):
    s = db.query(SalaryStructure).options(
        joinedload(SalaryStructure.components).joinedload(SalaryStructureComponent.component)
    ).filter(SalaryStructure.id == structure_id, SalaryStructure.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Structure not found")
    return _serialize_structure(s)


@router.patch("/{structure_id}", response_model=SalaryStructureResponse)
def update_structure(structure_id: UUID, payload: SalaryStructureUpdate,
                     db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    s = db.query(SalaryStructure).filter(SalaryStructure.id == structure_id,
                                         SalaryStructure.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Structure not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(s, k, v)
    s.last_updated_by_id = current_user.id
    if data.get("is_default"):
        db.query(SalaryStructure).filter(SalaryStructure.id != s.id).update({"is_default": False})
    write_audit(db, entity_type="STRUCTURE", entity_id=s.id, action=PayrollAuditAction.UPDATE,
                actor_id=current_user.id)
    db.commit()
    db.refresh(s)
    return _serialize_structure(s)


@router.delete("/{structure_id}", status_code=204)
def delete_structure(structure_id: UUID, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_superuser)):
    s = db.query(SalaryStructure).filter(SalaryStructure.id == structure_id,
                                         SalaryStructure.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Structure not found")
    if db.query(EmployeeCompensation.id).filter(
        EmployeeCompensation.structure_id == s.id,
        EmployeeCompensation.status == CompensationStatus.ACTIVE,
        EmployeeCompensation.is_deleted == False,  # noqa: E712
    ).first():
        raise HTTPException(409, "Structure is assigned to active compensation; reassign first")
    s.is_deleted = True
    s.is_active = False
    s.last_updated_by_id = current_user.id
    write_audit(db, entity_type="STRUCTURE", entity_id=s.id, action=PayrollAuditAction.DELETE,
                actor_id=current_user.id)
    db.commit()
    return


# ─── component links ───

@router.post("/{structure_id}/components", response_model=StructureComponentLinkResponse, status_code=201)
def add_component_link(structure_id: UUID, payload: StructureComponentLinkCreate,
                       db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    s = db.query(SalaryStructure).filter(SalaryStructure.id == structure_id,
                                         SalaryStructure.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Structure not found")
    if not db.query(SalaryComponent.id).filter(SalaryComponent.id == payload.component_id,
                                               SalaryComponent.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Component not found")
    if db.query(SalaryStructureComponent.id).filter(
        SalaryStructureComponent.structure_id == structure_id,
        SalaryStructureComponent.component_id == payload.component_id,
    ).first():
        raise HTTPException(409, "Component already in this structure")
    link = SalaryStructureComponent(
        structure_id=structure_id, component_id=payload.component_id,
        sequence=payload.sequence if payload.sequence is not None else 100,
        override_calc_type=payload.override_calc_type, override_formula=payload.override_formula,
        override_percent_value=payload.override_percent_value,
        override_percent_of_code=payload.override_percent_of_code,
        override_flat_amount=payload.override_flat_amount,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return _link_to_response(link)


@router.patch("/{structure_id}/components/{link_id}", response_model=StructureComponentLinkResponse)
def update_component_link(structure_id: UUID, link_id: UUID, payload: StructureComponentLinkUpdate,
                          db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    link = db.query(SalaryStructureComponent).filter(
        SalaryStructureComponent.id == link_id, SalaryStructureComponent.structure_id == structure_id
    ).first()
    if not link:
        raise HTTPException(404, "Component link not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(link, k, v)
    db.commit()
    db.refresh(link)
    return _link_to_response(link)


@router.delete("/{structure_id}/components/{link_id}", status_code=204)
def delete_component_link(structure_id: UUID, link_id: UUID, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_superuser)):
    link = db.query(SalaryStructureComponent).filter(
        SalaryStructureComponent.id == link_id, SalaryStructureComponent.structure_id == structure_id
    ).first()
    if not link:
        raise HTTPException(404, "Component link not found")
    db.delete(link)
    db.commit()
    return


# ─── preview ───

@router.post("/preview", response_model=PreviewResponse)
def preview(payload: PreviewRequest, db: Session = Depends(get_db),
            current_user: User = Depends(get_current_superuser)):
    if payload.components:
        components = resolve_components(db, payload.components)
    elif payload.structure_id:
        components = resolve_structure(db, payload.structure_id)
    else:
        raise HTTPException(422, "Provide components or structure_id for preview")
    if not components:
        raise HTTPException(404, "No active components to preview")
    monthly_ctc = payload.monthly_ctc or (payload.annual_ctc / 12 if payload.annual_ctc else None)
    if not monthly_ctc:
        raise HTTPException(422, "Provide monthly_ctc or annual_ctc")
    annual_ctc = payload.annual_ctc or (monthly_ctc * 12)
    cfg = load_config(db, fy_for(date.today()), payload.state_code)
    # PF policy: explicit payload flag (live drawer toggle) wins; else the saved
    # structure's policy; else the config/statutory default (cap at ceiling).
    if payload.pf_restrict_to_ceiling is not None:
        cfg["PF_RESTRICT_TO_CEILING"] = payload.pf_restrict_to_ceiling
    elif payload.structure_id:
        s = db.query(SalaryStructure.pf_restrict_to_ceiling).filter(
            SalaryStructure.id == payload.structure_id).first()
        if s is not None:
            cfg["PF_RESTRICT_TO_CEILING"] = s[0]
    result = compute_payslip(
        components=components, monthly_ctc=Decimal(str(monthly_ctc)),
        annual_ctc=Decimal(str(annual_ctc)), monthly_gross_hint=payload.monthly_gross,
        regime=payload.regime.value, declarations=payload.declarations,
        working_days=Decimal("30"), lop_days=Decimal("0"), cfg=cfg,
    )
    lines = [PreviewLine(component_code=l["component_code"], component_name=l["component_name"],
                         component_type=l["component_type"], amount=l["amount"], calc_note=l["calc_note"],
                         is_employer_cost=bool(l.get("is_employer_cost")), is_taxable=bool(l.get("is_taxable")))
             for l in result["lines"]]
    return PreviewResponse(
        gross_earnings=result["gross_earnings"], total_deductions=result["total_deductions"],
        net_pay=result["net_pay"], employer_contributions=result["employer_contributions"],
        ctc_value=result["ctc_value"], monthly_gross=result["gross_earnings"], lines=lines,
    )
