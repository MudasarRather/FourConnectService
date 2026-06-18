"""HR Training & Development — Skills, requirements & competency matrix."""
from __future__ import annotations

from datetime import date
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.grade import Grade
from app.models.hr.skill import (
    Skill, EmployeeSkill, SkillRequirement, SkillSource,
)
from app.schemas.hr.skill import (
    SkillCreate, SkillUpdate, SkillResponse,
    SkillRequirementCreate, SkillRequirementUpdate, SkillRequirementResponse,
    EmployeeSkillUpsert, EmployeeSkillUpdate, EmployeeSkillResponse,
    SkillMatrixResponse, SkillGapRow,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.flow import recompute_skill_gap
from app.utils.hr.training.service import emp_display
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Skills"])


# ───────────────────────────── helpers ─────────────────────────────

def _dept_name(db: Session, did) -> Optional[str]:
    if not did:
        return None
    r = db.query(Department.name).filter(Department.id == did).first()
    return r[0] if r else None


def _resolve_required_level(db: Session, employee: Employee, skill_id: UUID) -> Optional[int]:
    """Required level for (employee role, skill) — designation first, then grade."""
    if employee.designation_id:
        r = db.query(SkillRequirement.required_level).filter(
            SkillRequirement.skill_id == skill_id,
            SkillRequirement.designation_id == employee.designation_id,
            SkillRequirement.is_deleted == False,  # noqa: E712
        ).first()
        if r:
            return r[0]
    if employee.grade_id:
        r = db.query(SkillRequirement.required_level).filter(
            SkillRequirement.skill_id == skill_id,
            SkillRequirement.grade_id == employee.grade_id,
            SkillRequirement.is_deleted == False,  # noqa: E712
        ).first()
        if r:
            return r[0]
    return None


def _skill_resp(db: Session, s: Skill) -> SkillResponse:
    ec = db.query(func.count(EmployeeSkill.id)).filter(EmployeeSkill.skill_id == s.id).scalar()
    return SkillResponse(
        id=s.id, name=s.name, code=s.code, category=s.category, description=s.description,
        department_id=s.department_id, department_name=_dept_name(db, s.department_id),
        max_level=s.max_level, is_active=s.is_active, employee_count=int(ec or 0),
        created_at=s.created_at,
    )


# ───────────────────────────── Skill catalog ─────────────────────────────

@router.get("/skills", response_model=List[SkillResponse])
def list_skills(
    category: Optional[str] = None,
    search: Optional[str] = None,
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(Skill).filter(Skill.is_deleted == False)  # noqa: E712
    if category:
        q = q.filter(Skill.category == category)
    if department_id:
        q = q.filter(Skill.department_id == department_id)
    if search:
        q = q.filter(Skill.name.ilike(f"%{search}%"))
    rows = q.order_by(Skill.name.asc()).all()
    return [_skill_resp(db, s) for s in rows]


@router.post("/skills", response_model=SkillResponse, status_code=http_status.HTTP_201_CREATED)
def create_skill(
    payload: SkillCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if db.query(Skill.id).filter(Skill.name == payload.name, Skill.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "Skill name already exists")
    s = Skill(**payload.model_dump(), created_by_id=admin.id)
    db.add(s)
    db.flush()
    write_training_audit(db, entity_type="SKILL", entity_id=s.id,
                         action=TrainingAuditAction.CREATE, actor_id=admin.id, note=s.name)
    db.commit()
    db.refresh(s)
    return _skill_resp(db, s)


@router.patch("/skills/{skill_id}", response_model=SkillResponse)
def update_skill(
    skill_id: UUID,
    payload: SkillUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    s = db.query(Skill).filter(Skill.id == skill_id, Skill.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Skill not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    write_training_audit(db, entity_type="SKILL", entity_id=s.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(s)
    return _skill_resp(db, s)


@router.delete("/skills/{skill_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_skill(
    skill_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    s = db.query(Skill).filter(Skill.id == skill_id, Skill.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Skill not found")
    used = db.query(func.count(EmployeeSkill.id)).filter(EmployeeSkill.skill_id == skill_id).scalar()
    if used:
        raise HTTPException(409, f"Cannot delete a skill mapped to {used} employee(s); clear the matrix rows first.")
    s.is_deleted = True
    write_training_audit(db, entity_type="SKILL", entity_id=s.id,
                         action=TrainingAuditAction.DELETE, actor_id=admin.id)
    db.commit()


# ───────────────────────────── Skill requirements ─────────────────────────────

@router.get("/skill-requirements", response_model=List[SkillRequirementResponse])
def list_skill_requirements(
    skill_id: Optional[UUID] = None,
    designation_id: Optional[UUID] = None,
    grade_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(SkillRequirement).filter(SkillRequirement.is_deleted == False)  # noqa: E712
    if skill_id:
        q = q.filter(SkillRequirement.skill_id == skill_id)
    if designation_id:
        q = q.filter(SkillRequirement.designation_id == designation_id)
    if grade_id:
        q = q.filter(SkillRequirement.grade_id == grade_id)
    rows = q.all()
    out = []
    for r in rows:
        sk = db.query(Skill.name).filter(Skill.id == r.skill_id).first()
        dg = db.query(Designation.name).filter(Designation.id == r.designation_id).first() if r.designation_id else None
        gr = db.query(Grade.name).filter(Grade.id == r.grade_id).first() if r.grade_id else None
        out.append(SkillRequirementResponse(
            id=r.id, skill_id=r.skill_id, skill_name=sk[0] if sk else None,
            designation_id=r.designation_id, designation_name=dg[0] if dg else None,
            grade_id=r.grade_id, grade_name=gr[0] if gr else None,
            required_level=r.required_level, is_mandatory=r.is_mandatory,
        ))
    return out


@router.post("/skill-requirements", response_model=SkillRequirementResponse, status_code=http_status.HTTP_201_CREATED)
def create_skill_requirement(
    payload: SkillRequirementCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not payload.designation_id and not payload.grade_id:
        raise HTTPException(422, "Provide a designation or a grade for the requirement")
    r = SkillRequirement(**payload.model_dump(), created_by_id=admin.id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return _req_resp(db, r)


def _req_resp(db: Session, r: SkillRequirement) -> SkillRequirementResponse:
    sk = db.query(Skill.name).filter(Skill.id == r.skill_id).first()
    dg = db.query(Designation.name).filter(Designation.id == r.designation_id).first() if r.designation_id else None
    gr = db.query(Grade.name).filter(Grade.id == r.grade_id).first() if r.grade_id else None
    return SkillRequirementResponse(
        id=r.id, skill_id=r.skill_id, skill_name=sk[0] if sk else None,
        designation_id=r.designation_id, designation_name=dg[0] if dg else None,
        grade_id=r.grade_id, grade_name=gr[0] if gr else None,
        required_level=r.required_level, is_mandatory=r.is_mandatory,
    )


@router.patch("/skill-requirements/{req_id}", response_model=SkillRequirementResponse)
def update_skill_requirement(
    req_id: UUID,
    payload: SkillRequirementUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    r = db.query(SkillRequirement).filter(SkillRequirement.id == req_id, SkillRequirement.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Requirement not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return _req_resp(db, r)


@router.delete("/skill-requirements/{req_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_skill_requirement(
    req_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(SkillRequirement).filter(SkillRequirement.id == req_id, SkillRequirement.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Requirement not found")
    r.is_deleted = True
    db.commit()


# ───────────────────────────── Employee skills / matrix ─────────────────────────────

def _emp_skill_resp(db: Session, es: EmployeeSkill) -> EmployeeSkillResponse:
    disp = emp_display(db, es.employee_id)
    sk = db.query(Skill.name, Skill.category).filter(Skill.id == es.skill_id).first()
    return EmployeeSkillResponse(
        id=es.id, employee_id=es.employee_id, employee_name=disp.get("name"),
        employee_code=disp.get("code"), department_name=disp.get("dept"),
        designation_name=disp.get("desg"), skill_id=es.skill_id,
        skill_name=sk[0] if sk else None, skill_category=sk[1] if sk else None,
        current_level=es.current_level, required_level=es.required_level, gap=es.gap,
        last_assessed_date=es.last_assessed_date, source=es.source, notes=es.notes,
    )


@router.get("/skill-matrix", response_model=SkillMatrixResponse)
def skill_matrix(
    department_id: Optional[UUID] = None,
    designation_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    skill_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = (
        db.query(EmployeeSkill)
        .join(Employee, Employee.id == EmployeeSkill.employee_id)
        .filter(Employee.is_deleted == False)  # noqa: E712
    )
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    if designation_id:
        q = q.filter(Employee.designation_id == designation_id)
    if employee_id:
        q = q.filter(EmployeeSkill.employee_id == employee_id)
    if skill_id:
        q = q.filter(EmployeeSkill.skill_id == skill_id)
    rows = q.order_by(EmployeeSkill.gap.desc().nullslast()).limit(2000).all()
    return SkillMatrixResponse(rows=[_emp_skill_resp(db, es) for es in rows], total=len(rows))


@router.post("/skill-matrix", response_model=EmployeeSkillResponse, status_code=http_status.HTTP_201_CREATED)
def upsert_employee_skill(
    payload: EmployeeSkillUpsert,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    if not db.query(Skill.id).filter(Skill.id == payload.skill_id, Skill.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Skill not found")
    es = db.query(EmployeeSkill).filter(
        EmployeeSkill.employee_id == payload.employee_id,
        EmployeeSkill.skill_id == payload.skill_id,
    ).first()
    created = es is None
    if created:
        es = EmployeeSkill(employee_id=payload.employee_id, skill_id=payload.skill_id)
        db.add(es)
    if payload.current_level is not None:
        es.current_level = payload.current_level
    es.required_level = (
        payload.required_level if payload.required_level is not None
        else (es.required_level if not created else _resolve_required_level(db, emp, payload.skill_id))
    )
    es.last_assessed_date = payload.last_assessed_date or date.today()
    es.source = payload.source or SkillSource.MANUAL
    es.assessed_by_id = admin.id
    if payload.notes is not None:
        es.notes = payload.notes
    recompute_skill_gap(es)
    db.flush()
    write_training_audit(db, entity_type="SKILL", entity_id=es.id,
                         action=TrainingAuditAction.CREATE if created else TrainingAuditAction.UPDATE,
                         actor_id=admin.id)
    db.commit()
    db.refresh(es)
    return _emp_skill_resp(db, es)


@router.patch("/employee-skills/{emp_skill_id}", response_model=EmployeeSkillResponse)
def update_employee_skill(
    emp_skill_id: UUID,
    payload: EmployeeSkillUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    es = db.query(EmployeeSkill).filter(EmployeeSkill.id == emp_skill_id).first()
    if not es:
        raise HTTPException(404, "Skill matrix row not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(es, k, v)
    es.assessed_by_id = admin.id
    recompute_skill_gap(es)
    write_training_audit(db, entity_type="SKILL", entity_id=es.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(es)
    return _emp_skill_resp(db, es)


@router.delete("/employee-skills/{emp_skill_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_employee_skill(
    emp_skill_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    es = db.query(EmployeeSkill).filter(EmployeeSkill.id == emp_skill_id).first()
    if not es:
        raise HTTPException(404, "Skill matrix row not found")
    db.delete(es)
    db.commit()


@router.get("/skill-gap", response_model=List[SkillGapRow])
def skill_gap(
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = (
        db.query(
            Skill.id, Skill.name, Skill.category,
            func.avg(EmployeeSkill.required_level),
            func.avg(EmployeeSkill.current_level),
            func.avg(EmployeeSkill.gap),
            func.sum(case((EmployeeSkill.gap > 0, 1), else_=0)),
            func.count(EmployeeSkill.id),
        )
        .join(EmployeeSkill, EmployeeSkill.skill_id == Skill.id)
        .join(Employee, Employee.id == EmployeeSkill.employee_id)
        .filter(Skill.is_deleted == False, Employee.is_deleted == False)  # noqa: E712
    )
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    q = q.group_by(Skill.id, Skill.name, Skill.category).order_by(func.avg(EmployeeSkill.gap).desc().nullslast())
    out = []
    for sid, sname, scat, areq, acur, agap, withgap, total in q.all():
        out.append(SkillGapRow(
            skill_id=sid, skill_name=sname, skill_category=scat,
            avg_required=round(float(areq), 2) if areq is not None else None,
            avg_current=round(float(acur), 2) if acur is not None else None,
            avg_gap=round(float(agap), 2) if agap is not None else None,
            employees_with_gap=int(withgap or 0), employees_total=int(total or 0),
        ))
    return out
