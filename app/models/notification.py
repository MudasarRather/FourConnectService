import uuid
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class Notification(Base):
    """Notifications for users"""
    
    __tablename__ = "notifications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    
    type = Column(String, nullable=False)  # team_invite, team_accepted, team_declined, admin_override
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    
    # Context references
    related_project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    related_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    related_team_member_id = Column(UUID(as_uuid=True), ForeignKey("team_members.id"), nullable=True)
    
    # Status
    is_read = Column(Boolean, default=False)
    is_dismissed = Column(Boolean, default=False)
    
    # Optional action
    action_url = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id], backref="notifications")
    related_project = relationship("Project", foreign_keys=[related_project_id])
    related_user = relationship("User", foreign_keys=[related_user_id])
    
    def __repr__(self):
        return f"<Notification {self.type} for {self.user_id}>"
