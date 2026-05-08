import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base

class TaskAssignment(Base):
    """Tracks task (re)assignments history"""
    __tablename__ = "task_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    previous_assignee = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    new_assignee = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assignment_type = Column(String(50), nullable=False)  # new_assignment, reassignment, escalation, delegation
    role = Column(String(50), nullable=False)  # owner, executor, reviewer, approver
    notes = Column(Text, nullable=True)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    task = relationship("Task", foreign_keys=[task_id])
    prev_user = relationship("User", foreign_keys=[previous_assignee])
    new_user = relationship("User", foreign_keys=[new_assignee])
    assigner = relationship("User", foreign_keys=[assigned_by])

    def __repr__(self):
        return f"<TaskAssignment {self.task_id}: {self.assignment_type} to {self.new_assignee}>"
