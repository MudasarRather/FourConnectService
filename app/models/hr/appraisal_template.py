"""HR Settings — Appraisal Templates (config-only).

Rubric definitions consumed by the (future) Performance Management module:
a template (cycle + rating scale) with weighted sections (KRAs, competencies,
goals, behavioural, attendance, manager feedback). No reviews are stored here —
this is the configuration surface only.

New tables — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Numeric, Integer, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class AppraisalTemplate(Base):
    __tablename__ = "hr_appraisal_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False, unique=True)
    code = Column(String(30), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    cycle = Column(String(20), nullable=False, default="ANNUAL")     # ANNUAL | HALF_YEARLY | QUARTERLY | PROBATION | PROJECT | 360
    rating_scale = Column(JSONB, nullable=True)                       # {"max": 5, "labels": [...]}
    applies_to_json = Column(JSONB, nullable=True)                    # {"grade_ids": [...], "department_ids": [...]}
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    sections = relationship("AppraisalTemplateSection", back_populates="template",
                            cascade="all, delete-orphan", order_by="AppraisalTemplateSection.sort_order")

    def __repr__(self):
        return f"<AppraisalTemplate {self.code}>"


class AppraisalTemplateSection(Base):
    __tablename__ = "hr_appraisal_template_sections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("hr_appraisal_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(120), nullable=False)
    weight = Column(Numeric(5, 2), nullable=False, default=0)         # percentage
    section_type = Column(String(20), nullable=False, default="COMPETENCY")  # KRA | COMPETENCY | GOAL | BEHAVIORAL | ATTENDANCE | FEEDBACK
    criteria_json = Column(JSONB, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    template = relationship("AppraisalTemplate", back_populates="sections")

    def __repr__(self):
        return f"<AppraisalTemplateSection {self.title} {self.weight}%>"
