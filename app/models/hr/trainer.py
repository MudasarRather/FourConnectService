"""HR Training & Development — Trainer database (internal / external / vendor).

New table — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Integer, Numeric,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class TrainerType(str, enum.Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    VENDOR = "VENDOR"


class Trainer(Base):
    __tablename__ = "hr_trainers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(200), nullable=False, index=True)
    trainer_type = Column(Enum(TrainerType, name="hr_trainer_type"), nullable=False, default=TrainerType.INTERNAL, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)  # set for INTERNAL trainers
    email = Column(String(200), nullable=True)
    phone = Column(String(40), nullable=True)
    organization = Column(String(200), nullable=True)
    specialization = Column(String(300), nullable=True)
    hourly_rate = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(8), nullable=False, default="INR")
    rating_avg = Column(Numeric(3, 2), nullable=True)
    rating_count = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
