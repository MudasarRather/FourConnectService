"""HR Training & Development — Skill / competency matrix schemas."""
from datetime import date, datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.skill import SkillCategory, SkillSource


# ───────────────────────────── Skill catalog ─────────────────────────────

class SkillCreate(BaseModel):
    name: str
    code: Optional[str] = None
    category: SkillCategory
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    max_level: int = 5


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    category: Optional[SkillCategory] = None
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    max_level: Optional[int] = None
    is_active: Optional[bool] = None


class SkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: Optional[str] = None
    category: SkillCategory
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    max_level: int
    is_active: bool
    employee_count: Optional[int] = None
    created_at: datetime


# ───────────────────────────── Skill requirements ─────────────────────────────

class SkillRequirementCreate(BaseModel):
    skill_id: UUID
    designation_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    required_level: int = 3
    is_mandatory: bool = True


class SkillRequirementUpdate(BaseModel):
    required_level: Optional[int] = None
    is_mandatory: Optional[bool] = None


class SkillRequirementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    skill_id: UUID
    skill_name: Optional[str] = None
    designation_id: Optional[UUID] = None
    designation_name: Optional[str] = None
    grade_id: Optional[UUID] = None
    grade_name: Optional[str] = None
    required_level: int
    is_mandatory: bool


# ───────────────────────────── Employee skills (matrix rows) ─────────────────────────────

class EmployeeSkillUpsert(BaseModel):
    employee_id: UUID
    skill_id: UUID
    current_level: Optional[int] = None
    required_level: Optional[int] = None
    last_assessed_date: Optional[date] = None
    source: Optional[SkillSource] = None
    notes: Optional[str] = None


class EmployeeSkillUpdate(BaseModel):
    current_level: Optional[int] = None
    required_level: Optional[int] = None
    last_assessed_date: Optional[date] = None
    source: Optional[SkillSource] = None
    notes: Optional[str] = None


class EmployeeSkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    designation_name: Optional[str] = None
    skill_id: UUID
    skill_name: Optional[str] = None
    skill_category: Optional[SkillCategory] = None
    current_level: Optional[int] = None
    required_level: Optional[int] = None
    gap: Optional[int] = None
    last_assessed_date: Optional[date] = None
    source: Optional[SkillSource] = None
    notes: Optional[str] = None


class SkillGapRow(BaseModel):
    skill_id: UUID
    skill_name: str
    skill_category: SkillCategory
    avg_required: Optional[float] = None
    avg_current: Optional[float] = None
    avg_gap: Optional[float] = None
    employees_with_gap: int
    employees_total: int


class SkillMatrixResponse(BaseModel):
    rows: List[EmployeeSkillResponse]
    total: int
