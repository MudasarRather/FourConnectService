import uuid
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class MilestoneTask(Base):
    """
    Sub-tasks within a milestone.
    Used for granular tracking and time estimation.
    """
    __tablename__ = "milestone_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    milestone_id = Column(UUID(as_uuid=True), ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False)
    
    name = Column(String, nullable=False)
    estimated_minutes = Column(Integer, default=0) # Storing in minutes for precision
    # weightage removed per requirements
    is_completed = Column(Boolean, default=False)
    
    completed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    milestone = relationship("Milestone", back_populates="tasks")
    completed_by = relationship("User", foreign_keys=[completed_by_id])

    def __repr__(self):
        return f"<MilestoneTask {self.name} ({self.estimated_minutes}m)>"
