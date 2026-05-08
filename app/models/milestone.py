import uuid
from sqlalchemy import Column, String, Text, Date, DateTime, ForeignKey, Enum as SQLAEnum, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
import enum

class MilestoneStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"

class Milestone(Base):
    """Milestone model for project timeline management"""
    
    __tablename__ = "milestones"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    
    name = Column(String, nullable=False)
    # description = Column(Text, nullable=True) # REMOVED per user request
    due_date = Column(Date, nullable=False)
    
    # New Fields
    start_date = Column(Date, nullable=True)
    priority = Column(String, default="medium") # urgent, high, medium, low
    milestone_type = Column(String, nullable=True)
    estimated_hours = Column(Float, default=0.0)
    budget_amount = Column(Float, default=0.0)
    currency = Column(String, default="USD")
    contribution_percentage = Column(Float, default=0.0) # Calculated from budget vs project budget
    file_path = Column(String, nullable=True)
    
    status = Column(String, default="pending") # pending, in_progress, completed, expired, declined
    decline_reason = Column(Text, nullable=True)
    
    # Tracker Fields
    actual_start_date = Column(Date, nullable=True)
    actual_end_date = Column(Date, nullable=True)
    delay_reason = Column(Text, nullable=True)
    remarks = Column(Text, nullable=True)
    
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    assigned_to_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Audit
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_update_summary = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    project = relationship("Project", back_populates="milestones")
    created_by = relationship("User", foreign_keys=[created_by_id])
    assigned_to = relationship("User", foreign_keys=[assigned_to_id]) # Deprecated
    assignments = relationship("MilestoneAssignment", back_populates="milestone", cascade="all, delete-orphan")
    tasks = relationship("MilestoneTask", back_populates="milestone", cascade="all, delete-orphan")
    last_updated_by = relationship("User", foreign_keys=[last_updated_by_id])
    
    def __repr__(self):
        return f"<Milestone {self.name} (Project {self.project_id})>"
