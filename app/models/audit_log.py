import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class AuditLog(Base):
    """
    Generic Audit Log for tracking system events.
    Used for reporting and history tracking.
    """
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # Who performed the action
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Action Type (e.g. 'milestone_declined', 'milestone_reassigned')
    action = Column(String, nullable=False, index=True)
    
    # Target Entity
    entity_type = Column(String, nullable=False) # 'milestone', 'project', etc.
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    
    # JSON or Text details (e.g. decline reason, old vs new values)
    details = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f"<AuditLog {self.action} on {self.entity_type}:{self.entity_id} by {self.user_id}>"
