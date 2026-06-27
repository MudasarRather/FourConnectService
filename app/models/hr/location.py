import enum
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Enum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class WorkLocationType(str, enum.Enum):
    HQ = "HQ"
    BRANCH = "BRANCH"
    REMOTE = "REMOTE"
    CLIENT_SITE = "CLIENT_SITE"


class WorkLocation(Base):
    __tablename__ = "hr_work_locations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False, unique=True)
    address = Column(String, nullable=True)
    city = Column(String(80), nullable=True)
    state = Column(String(80), nullable=True)
    country = Column(String(80), nullable=True)
    type = Column(Enum(WorkLocationType, name="hr_work_location_type"), nullable=False, default=WorkLocationType.HQ)
    code = Column(String(20), nullable=True)
    timezone = Column(String(40), nullable=True)
    # e.g. {"days": ["SAT", "SUN"], "alternate_saturdays": true}
    weekly_off_pattern = Column(JSONB, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employees = relationship("Employee", back_populates="work_location")

    def __repr__(self):
        return f"<WorkLocation {self.name}>"
