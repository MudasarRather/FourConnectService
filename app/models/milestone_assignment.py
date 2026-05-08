import uuid
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Enum as SQLAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
import enum

class AssignmentStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress" # Accepted
    COMPLETED = "completed"
    DECLINED = "declined"
    REMOVED = "removed"

class MilestoneAssignment(Base):
    """
    Tracks individual assignments of users to milestones.
    Allows multiple assignees per milestone.
    """
    __tablename__ = "milestone_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    milestone_id = Column(UUID(as_uuid=True), ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    status = Column(String, default="pending") # pending, in_progress, declined
    decline_reason = Column(Text, nullable=True)
    decline_count = Column(Integer, default=0) # Tracks number of times declined
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    milestone = relationship("Milestone", back_populates="assignments")
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f"<Assignment {self.status} for User {self.user_id} on Milestone {self.milestone_id}>"
