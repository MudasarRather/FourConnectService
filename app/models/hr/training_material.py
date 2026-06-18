"""HR Training & Development — Training material repository.

A training-side index over learning materials. The binary itself lives in the
Document Drive (``drive_document_id`` loosely references ``DriveDocument.id``) or
at an external URL; this row carries the training linkage, type and ordering.

New table — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Integer, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class MaterialType(str, enum.Enum):
    DOCUMENT = "DOCUMENT"
    VIDEO = "VIDEO"
    LINK = "LINK"
    SLIDE = "SLIDE"
    QUIZ = "QUIZ"
    OTHER = "OTHER"


class TrainingMaterial(Base):
    __tablename__ = "hr_training_materials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_programs.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    material_type = Column(Enum(MaterialType, name="hr_training_material_type"), nullable=False, default=MaterialType.DOCUMENT)
    drive_document_id = Column(UUID(as_uuid=True), nullable=True)  # loose ref to DriveDocument.id
    external_url = Column(String(600), nullable=True)
    file_url = Column(String(600), nullable=True)
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
