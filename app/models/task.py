import uuid
import enum
from sqlalchemy import Column, String, Text, Enum, DateTime, Date, ForeignKey, Boolean, Integer, Float, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


# ── Enums ──

class TaskStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    in_review = "in_review"
    completed = "completed"
    on_hold = "on_hold"
    cancelled = "cancelled"
    expired = "expired"
    extended = "extended"
    upcoming = "upcoming"
    # Legacy compatibility
    pending = "pending"
    
    # Aliases for backward compatibility
    OPEN = open
    IN_PROGRESS = in_progress
    IN_REVIEW = in_review
    COMPLETED = completed
    ON_HOLD = on_hold
    CANCELLED = cancelled
    EXPIRED = expired
    EXTENDED = extended
    UPCOMING = upcoming
    PENDING = pending


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"
    # Legacy compatibility
    urgent = "urgent"
    
    # Aliases
    LOW = low
    MEDIUM = medium
    HIGH = high
    CRITICAL = critical
    URGENT = urgent


class TaskType(str, enum.Enum):
    bug = "bug"
    feature = "feature"
    improvement = "improvement"
    documentation = "documentation"
    finance_task = "finance_task"
    approval_task = "approval_task"
    general = "general"
    
    # Aliases
    BUG = bug
    FEATURE = feature
    IMPROVEMENT = improvement
    DOCUMENTATION = documentation
    FINANCE_TASK = finance_task
    APPROVAL_TASK = approval_task
    GENERAL = general


# ── Main Task Model ──

class Task(Base):
    """Full task model for task management"""

    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    task_code = Column(String, unique=True, nullable=True, index=True)  # Auto-generated TSK-XXXX

    # ── Basic Info ──
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    task_type = Column(String(50), nullable=True, default="general")
    module = Column(String(100), nullable=True)  # Backend, Frontend, Finance, etc.

    # ── Relationships ──
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # ── Status & Priority ──
    status = Column(Enum(TaskStatus), default=TaskStatus.OPEN, nullable=False)
    priority = Column(Enum(TaskPriority), default=TaskPriority.MEDIUM, nullable=False)

    # ── Scheduling ──
    start_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True)
    estimated_hours = Column(Numeric(6, 2), nullable=True)
    actual_hours = Column(Numeric(6, 2), nullable=True)

    # ── Progress ──
    progress = Column(Integer, default=0, nullable=False)  # 0-100
    is_blocked = Column(Boolean, default=False, nullable=False)

    # ── People (JSON arrays of user IDs) ──
    reviewers = Column(JSON, nullable=True)   # ["uuid1", "uuid2"]
    watchers = Column(JSON, nullable=True)    # ["uuid1", "uuid2"]

    # ── Notifications ──
    notify_assignee = Column(Boolean, default=True, nullable=False)
    notify_watchers = Column(Boolean, default=True, nullable=False)
    notify_on_status_change = Column(Boolean, default=True, nullable=False)

    # ── Attachments (JSON) ──
    attachments = Column(JSON, nullable=True)  # [{file_name, file_path, uploaded_by, uploaded_at}]

    # ── Timestamps ──
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──
    project = relationship("Project", back_populates="tasks")
    assignee = relationship("User", foreign_keys=[assigned_to])
    assigner = relationship("User", foreign_keys=[assigned_by])
    creator = relationship("User", foreign_keys=[created_by])

    checklist_items = relationship("TaskChecklist", back_populates="task", cascade="all, delete-orphan")
    comments = relationship("TaskComment", back_populates="task", cascade="all, delete-orphan")
    dependencies = relationship("TaskDependency", foreign_keys="TaskDependency.task_id", back_populates="task", cascade="all, delete-orphan")
    activity_logs = relationship("TaskActivityLog", back_populates="task", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Task {self.task_code}: {self.title}>"


# ── Supporting Tables ──

class TaskDependency(Base):
    """Links tasks to their dependencies"""
    __tablename__ = "task_dependencies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    depends_on_task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)

    task = relationship("Task", foreign_keys=[task_id], back_populates="dependencies")
    depends_on = relationship("Task", foreign_keys=[depends_on_task_id])


class TaskComment(Base):
    """Comments on a task"""
    __tablename__ = "task_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    comment = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    task = relationship("Task", back_populates="comments")
    user = relationship("User")


class TaskChecklist(Base):
    """Checklist items within a task"""
    __tablename__ = "task_checklist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    item_text = Column(String(500), nullable=False)
    is_completed = Column(Boolean, default=False, nullable=False)

    task = relationship("Task", back_populates="checklist_items")


class TaskActivityLog(Base):
    """Audit log for task changes"""
    __tablename__ = "task_activity_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(100), nullable=False)  # task_created, status_changed, etc.
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    task = relationship("Task", back_populates="activity_logs")
    user = relationship("User")
