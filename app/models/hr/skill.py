"""HR Training & Development — Skills & Competency matrix.

- ``Skill``            : the org's competency catalog (one row per skill).
- ``SkillRequirement``: required proficiency for a skill by designation / grade
                        (drives the default ``required_level`` on a matrix row
                        and org-wide gap analysis).
- ``EmployeeSkill``   : one matrix row per (employee, skill) with the employee's
                        current level vs the required level. ``gap`` is always
                        recomputed server-side as max(required - current, 0).

New tables — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid
from datetime import date

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Text, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class SkillCategory(str, enum.Enum):
    TECHNICAL = "TECHNICAL"
    FUNCTIONAL = "FUNCTIONAL"
    BEHAVIORAL = "BEHAVIORAL"
    DOMAIN = "DOMAIN"
    LANGUAGE = "LANGUAGE"
    CERTIFICATION = "CERTIFICATION"
    OTHER = "OTHER"


class SkillSource(str, enum.Enum):
    """Provenance of an employee's recorded proficiency."""
    MANUAL = "MANUAL"          # set by HR/admin
    ASSESSMENT = "ASSESSMENT"  # derived from an assessment result
    SELF = "SELF"              # self-assessed by the employee
    IMPORT = "IMPORT"          # bulk import


class Skill(Base):
    __tablename__ = "hr_skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False, unique=True, index=True)
    code = Column(String(40), nullable=True, unique=True)
    category = Column(Enum(SkillCategory, name="hr_skill_category"), nullable=False, index=True)
    description = Column(Text, nullable=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True, index=True)
    max_level = Column(Integer, nullable=False, default=5)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class SkillRequirement(Base):
    """Required proficiency for a skill keyed by designation and/or grade."""
    __tablename__ = "hr_skill_requirements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("hr_skills.id", ondelete="CASCADE"), nullable=False, index=True)
    designation_id = Column(UUID(as_uuid=True), ForeignKey("hr_designations.id"), nullable=True, index=True)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id"), nullable=True, index=True)
    required_level = Column(Integer, nullable=False, default=3)
    is_mandatory = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("skill_id", "designation_id", "grade_id", name="uq_hr_skill_req"),
    )


class EmployeeSkill(Base):
    __tablename__ = "hr_employee_skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("hr_skills.id", ondelete="CASCADE"), nullable=False, index=True)
    current_level = Column(Integer, nullable=True)
    required_level = Column(Integer, nullable=True)
    gap = Column(Integer, nullable=True)  # server-computed max(required - current, 0)
    last_assessed_date = Column(Date, nullable=True)
    assessed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    source = Column(Enum(SkillSource, name="hr_skill_source"), nullable=False, default=SkillSource.MANUAL)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("employee_id", "skill_id", name="uq_hr_emp_skill"),
        Index("ix_hr_emp_skill_gap", "skill_id", "gap"),
    )
