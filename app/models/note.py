import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean, Enum as SAEnum, JSON
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func
from app.database import Base
import enum


class NoteType(str, enum.Enum):
    general = "general"
    financial = "financial"
    private = "private"
    audit = "audit"
    other = "other"


class ProjectNote(Base):
    """Enhanced project note model with types, mentions, pin/lock support"""

    __tablename__ = "project_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    note_type = Column(String, nullable=False, default="general")
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False, default="")

    # Mentions - stored as array of user UUIDs
    mentions = Column(ARRAY(UUID(as_uuid=True)), nullable=True, default=[])

    # Pin & Lock
    is_pinned = Column(Boolean, default=False, nullable=False)
    is_locked = Column(Boolean, default=False, nullable=False)
    locked_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)

    # Soft delete
    is_deleted = Column(Boolean, default=False, nullable=False)

    # Attachments as JSON array: [{name, url, size}]
    attachment_urls = Column(JSON, nullable=True, default=[])

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<ProjectNote {self.title} ({self.note_type})>"
