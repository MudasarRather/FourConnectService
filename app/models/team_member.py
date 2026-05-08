import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SQLAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
import enum


class TeamMemberStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REMOVED = "removed"


class TeamMember(Base):
    """Team member assignment for projects"""
    
    __tablename__ = "team_members"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    assigned_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    status = Column(String, default="pending")  # pending, accepted, declined
    role = Column(String, nullable=True)  # Optional role in the project
    
    assigned_at = Column(DateTime, default=datetime.utcnow)
    responded_at = Column(DateTime, nullable=True)
    decline_reason = Column(String, nullable=True)  # Reason for declining invitation
    override_reason = Column(String, nullable=True)  # Reason for admin override
    
    # Relationships
    project = relationship("Project", backref="team_members")
    user = relationship("User", foreign_keys=[user_id], backref="team_memberships")
    assigned_by = relationship("User", foreign_keys=[assigned_by_id])
    
    def __repr__(self):
        return f"<TeamMember {self.user_id} -> Project {self.project_id}>"
