"""HR Settings — Appraisal Templates (config-only, for the future Performance module)."""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models.user import User
from app.models.hr.appraisal_template import AppraisalTemplate, AppraisalTemplateSection
from app.schemas.hr.appraisal_template import (
    AppraisalTemplateCreate, AppraisalTemplateUpdate, AppraisalTemplateResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/settings/appraisal-templates", tags=["HR — Appraisal Templates"])


def _apply_sections(template: AppraisalTemplate, sections):
    template.sections.clear()
    for i, s in enumerate(sections or []):
        template.sections.append(AppraisalTemplateSection(
            title=s.title, weight=s.weight, section_type=s.section_type,
            criteria_json=s.criteria_json, sort_order=s.sort_order if s.sort_order is not None else i,
        ))


@router.get("/", response_model=List[AppraisalTemplateResponse])
def list_templates(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return (db.query(AppraisalTemplate)
            .options(selectinload(AppraisalTemplate.sections))
            .filter(AppraisalTemplate.is_deleted == False)  # noqa: E712
            .order_by(AppraisalTemplate.name).all())


@router.post("/", response_model=AppraisalTemplateResponse, status_code=201)
def create_template(payload: AppraisalTemplateCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    for col, val in (("code", payload.code), ("name", payload.name)):
        if db.query(AppraisalTemplate).filter(getattr(AppraisalTemplate, col) == val, AppraisalTemplate.is_deleted == False).first():  # noqa: E712
            raise HTTPException(409, f"Template {col} already exists")
    t = AppraisalTemplate(
        name=payload.name, code=payload.code, description=payload.description, cycle=payload.cycle,
        rating_scale=payload.rating_scale, applies_to_json=payload.applies_to_json,
        is_active=payload.is_active if payload.is_active is not None else True,
    )
    _apply_sections(t, payload.sections)
    db.add(t)
    db.flush()
    log_settings_change(db, "APPRAISAL_TEMPLATE", t.id, "CREATE", admin.id, after={"code": t.code}, note=t.name)
    db.commit()
    db.refresh(t)
    return t


@router.get("/{template_id}", response_model=AppraisalTemplateResponse)
def get_template(template_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    t = db.query(AppraisalTemplate).options(selectinload(AppraisalTemplate.sections)).filter(AppraisalTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    return t


@router.patch("/{template_id}", response_model=AppraisalTemplateResponse)
def update_template(template_id: UUID, payload: AppraisalTemplateUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    t = db.query(AppraisalTemplate).filter(AppraisalTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    data = payload.model_dump(exclude_unset=True)
    sections = data.pop("sections", None)
    for col in ("code", "name"):
        if col in data and data[col] != getattr(t, col):
            if db.query(AppraisalTemplate).filter(getattr(AppraisalTemplate, col) == data[col], AppraisalTemplate.id != template_id, AppraisalTemplate.is_deleted == False).first():  # noqa: E712
                raise HTTPException(409, f"Template {col} already exists")
    for k, v in data.items():
        setattr(t, k, v)
    if sections is not None:
        _apply_sections(t, payload.sections)
    log_settings_change(db, "APPRAISAL_TEMPLATE", t.id, "UPDATE", admin.id, note=t.name)
    db.commit()
    db.refresh(t)
    return t


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    t = db.query(AppraisalTemplate).filter(AppraisalTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    t.is_deleted = True
    log_settings_change(db, "APPRAISAL_TEMPLATE", t.id, "DELETE", admin.id, note=t.name)
    db.commit()
