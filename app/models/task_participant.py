import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, UUID, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base

class TaskParticipant(Base):
    """Tracks primary participants like reviewers, watchers, and approvers"""
    __tablename__ = "task_participants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    participant_type = Column(String(50), nullable=False)  # reviewer, watcher, approver
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint('task_id', 'user_id', 'participant_type', name='idx_task_participant_unique'),
    )

    # Relationships
    task = relationship("Task", foreign_keys=[task_id])
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f"<TaskParticipant {self.task_id}: {self.participant_type} - {self.user_id}>"
