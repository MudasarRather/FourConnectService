"""
Tasks API Router
Full CRUD + comments + search for the Task Management system.
"""
import uuid
import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, text, func
from pydantic import BaseModel, Field

from app.database import get_db
from app.models.user import User
from app.models.task import (
    Task, TaskStatus, TaskPriority, TaskType,
    TaskDependency, TaskComment, TaskChecklist, TaskActivityLog
)
from app.models.notification import Notification
from app.models.task_assignment import TaskAssignment
from app.models.task_participant import TaskParticipant
from app.utils.dependencies import get_current_user


router = APIRouter(prefix="/tasks", tags=["Tasks"])


# ── Pydantic Schemas ──

class ChecklistItemCreate(BaseModel):
    item_text: str
    is_completed: bool = False

class DependencyCreate(BaseModel):
    depends_on_task_id: str

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: Optional[str] = "general"
    project_id: Optional[str] = None
    module: Optional[str] = None
    assigned_to: Optional[str] = None
    priority: Optional[str] = "medium"
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    estimated_hours: Optional[float] = None
    reviewers: Optional[list] = None
    watchers: Optional[list] = None
    notify_assignee: bool = True
    notify_watchers: bool = True
    notify_on_status_change: bool = True
    checklist: Optional[List[ChecklistItemCreate]] = None
    dependencies: Optional[List[DependencyCreate]] = None
    attachments: Optional[list] = None

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    project_id: Optional[str] = None
    module: Optional[str] = None
    assigned_to: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    progress: Optional[int] = None
    is_blocked: Optional[bool] = None
    reviewers: Optional[list] = None
    watchers: Optional[list] = None
    notify_assignee: Optional[bool] = None
    notify_watchers: Optional[bool] = None
    notify_on_status_change: Optional[bool] = None
    attachments: Optional[list] = None
    checklist: Optional[List[ChecklistItemCreate]] = None
    dependencies: Optional[List[DependencyCreate]] = None

class CommentCreate(BaseModel):
    comment: str

class TaskAssignRequest(BaseModel):
    assigned_to: UUID
    assignment_type: str = "reassignment" # new_assignment, reassignment, escalation, delegation
    role: str = "executor" # owner, executor, reviewer, approver
    reviewers: Optional[List[UUID]] = None
    watchers: Optional[List[UUID]] = None
    new_due_date: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None
    notify_assignee: bool = True
    notify_reviewers: bool = True
    notify_watchers: bool = True
    notify_manager: bool = True

class ParticipantsUpdateRequest(BaseModel):
    reviewers: List[UUID]
    watchers: List[UUID]
    notify_all: bool = True


def get_status_str(status_obj):
    if not status_obj: return "open"
    if hasattr(status_obj, 'value'): return status_obj.value
    return str(status_obj)

def get_priority_str(priority_obj):
    if not priority_obj: return "medium"
    if hasattr(priority_obj, 'value'): return priority_obj.value
    return str(priority_obj)


# ── Helper: generate task code ──
def generate_task_code(db: Session) -> str:
    """Generate next sequential task code like TSK-0001"""
    last = db.query(Task).filter(Task.task_code.isnot(None)).order_by(Task.created_at.desc()).first()
    if last and last.task_code and last.task_code.startswith("TSK-"):
        try:
            num = int(last.task_code.split("-")[1]) + 1
        except (ValueError, IndexError):
            num = 1
    else:
        num = 1
    return f"TSK-{num:04d}"


def log_activity(db: Session, task_id, action: str, user_id, old_value=None, new_value=None):
    """Create an activity log entry"""
    entry = TaskActivityLog(
        task_id=task_id,
        action=action,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        user_id=user_id,
    )
    db.add(entry)


def check_update_expired_tasks(db: Session):
    """Automatically transition IN_PROGRESS tasks to EXPIRED if past due date."""
    from datetime import date as dt_date
    today = dt_date.today()
    
    expired_tasks = db.query(Task).filter(
        or_(
            text("LOWER(status::text) = 'in_progress'"),
            text("LOWER(status::text) = 'extended'")
        ),
        Task.due_date < today
    ).all()
    
    for t in expired_tasks:
        old_status_val = get_status_str(t.status)
        t.status = TaskStatus.EXPIRED
        # Log activity
        entry = TaskActivityLog(
            task_id=t.id,
            action="status_changed",
            old_value=old_status_val,
            new_value="expired",
            user_id=t.assigned_to or t.created_by, # Fallback to creator if unassigned
        )
        db.add(entry)
        
    if expired_tasks:
        db.commit()


def check_update_upcoming_tasks(db: Session):
    """
    Automatically transition:
    1. UPCOMING -> OPEN if start date is today or past.
    2. OPEN -> UPCOMING if start date is in the future.
    """
    from datetime import date as dt_date
    today = dt_date.today()
    
    # 1. UPCOMING -> OPEN
    upcoming_tasks = db.query(Task).filter(
        text("LOWER(status::text) = 'upcoming'"),
        Task.start_date <= today
    ).all()
    
    for t in upcoming_tasks:
        t.status = "open"
        log_activity(db, t.id, "status_changed", t.assigned_to or t.created_by, 
                     old_value="upcoming", new_value="open")
        
    # 2. OPEN -> UPCOMING
    to_upcoming = db.query(Task).filter(
        text("LOWER(status::text) = 'open'"),
        Task.start_date > today
    ).all()
    
    for t in to_upcoming:
        t.status = "upcoming"
        log_activity(db, t.id, "status_changed", t.assigned_to or t.created_by,
                     old_value="open", new_value="upcoming")

    if upcoming_tasks or to_upcoming:
        db.commit()


def recalculate_task_progress(db: Session, task_id: UUID):
    """Calculate progress based on checklist items and update task status."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return

    items = db.query(TaskChecklist).filter(TaskChecklist.task_id == task_id).all()
    if not items:
        return

    total = len(items)
    completed = sum(1 for item in items if item.is_completed)
    progress = int((completed / total) * 100) if total > 0 else 0

    if task.progress != progress:
        old_progress = task.progress
        task.progress = progress
        log_activity(db, task.id, "progress_changed", task.assigned_to or task.created_by, 
                     old_value=old_progress, new_value=progress)

        # Automatically mark as completed if 100%
        if progress == 100 and task.status != TaskStatus.COMPLETED:
            old_status = task.status
            task.status = TaskStatus.COMPLETED
            task.completed_at = func.now()
            log_activity(db, task.id, "status_changed", task.assigned_to or task.created_by,
                         old_value=old_status, new_value="completed")
            
            # Send completion notification
            targets = set()
            if task.created_by: targets.add(str(task.created_by))
            if task.assigned_by: targets.add(str(task.assigned_by))
            if task.watchers:
                for w in task.watchers: targets.add(str(w))
            
            for uid in targets:
                db.add(Notification(
                    user_id=uid,
                    type="task_completed",
                    title="Task Completed",
                    message=f"Task '{task.title}' [{task.task_code}] has been completed.",
                    action_url=f"/user/tasks/view?taskId={task.id}"
                ))

    db.commit()


# ── Endpoints ──

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new task with optional checklist, dependencies, and notifications."""
    from datetime import date as dt_date

    task = Task(
        task_code=generate_task_code(db),
        title=body.title,
        description=body.description,
        task_type=body.task_type or "general",
        module=body.module,
        project_id=body.project_id if body.project_id else None,
        assigned_to=body.assigned_to if body.assigned_to else None,
        assigned_by=current_user.id,
        created_by=current_user.id,
        priority=body.priority.lower() if body.priority else "medium",
        start_date=dt_date.fromisoformat(body.start_date) if body.start_date else None,
        due_date=dt_date.fromisoformat(body.due_date) if body.due_date else None,
        estimated_hours=body.estimated_hours,
        reviewers=body.reviewers,
        watchers=body.watchers,
        notify_assignee=body.notify_assignee,
        notify_watchers=body.notify_watchers,
        notify_on_status_change=body.notify_on_status_change,
        attachments=body.attachments,
    )
    
    # Set status based on start_date
    if task.start_date and task.start_date > dt_date.today():
        task.status = "upcoming"
    else:
        task.status = "open"
        
    db.add(task)
    db.flush()  # Get task.id

    # Checklist
    if body.checklist:
        for item in body.checklist:
            db.add(TaskChecklist(
                task_id=task.id,
                item_text=item.item_text,
                is_completed=item.is_completed,
            ))

    # Dependencies
    if body.dependencies:
        for dep in body.dependencies:
            if str(dep.depends_on_task_id) == str(task.id):
                continue  # Prevent self-reference
            db.add(TaskDependency(
                task_id=task.id,
                depends_on_task_id=dep.depends_on_task_id,
            ))

    # Activity log
    log_activity(db, task.id, "task_created", current_user.id, new_value=task.title)

    # Notifications
    notification_targets = set()
    if body.notify_assignee and body.assigned_to:
        notification_targets.add(str(body.assigned_to))
    if body.notify_watchers and body.watchers:
        for w in body.watchers:
            notification_targets.add(str(w))
    if body.reviewers:
        for r in body.reviewers:
            notification_targets.add(str(r))

    # Remove the creator from notifications
    notification_targets.discard(str(current_user.id))

    for uid in notification_targets:
        notif = Notification(
            user_id=uid,
            type="task_assigned",
            title="New Task Assigned",
            message=f"{current_user.full_name} assigned you a task: '{task.title}' [{task.task_code}]",
            action_url=f"/user/tasks/view?taskId={task.id}"
        )
        db.add(notif)

    db.commit()
    # Recalculate if checklist provided
    if body.checklist:
        recalculate_task_progress(db, task.id)
        
    db.refresh(task)

    return _task_to_dict(task, db)


@router.get("/dashboard")
def task_dashboard(
    period: str = Query("weekly", description="daily|weekly|all"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get aggregate task statistics for the dashboard."""
    from datetime import date as dt_date, timedelta
    check_update_expired_tasks(db)
    check_update_upcoming_tasks(db)
    today = dt_date.today()

    # Base query — all tasks visible to this user
    base = db.query(Task)
    if not current_user.is_superuser:
        base = base.filter(
            or_(
                Task.assigned_to == current_user.id,
                Task.created_by == current_user.id,
                Task.assigned_by == current_user.id,
            )
        )

    all_tasks = base.all()
    total = len(all_tasks)
    
    overdue = sum(1 for t in all_tasks if t.due_date and t.due_date < today and get_status_str(t.status) not in ('completed', 'cancelled', 'closed'))
    due_today = sum(1 for t in all_tasks if t.due_date and t.due_date == today)
    in_progress = sum(1 for t in all_tasks if get_status_str(t.status) == 'in_progress')
    completed = sum(1 for t in all_tasks if get_status_str(t.status) == 'completed')
    blocked = sum(1 for t in all_tasks if t.is_blocked)
    open_count = sum(1 for t in all_tasks if get_status_str(t.status) == 'open')

    # Filter based on period using rolling windows from now
    now_utc = datetime.now(timezone.utc)
    start_time = None
    if period == "daily":
        start_time = now_utc - timedelta(hours=24)
    elif period == "weekly":
        start_time = now_utc - timedelta(days=7)

    filtered_tasks = all_tasks
    if start_time:
        filtered_tasks = [t for t in all_tasks if (t.created_at and t.created_at >= start_time) or (t.updated_at and t.updated_at >= start_time)]

    total_f = len(filtered_tasks)
    completed_f = sum(1 for t in filtered_tasks if get_status_str(t.status) == 'completed')
    
    # Calculate percentage growth/performance
    performance_pct = int((completed_f / total_f) * 100) if total_f > 0 else 0

    # Recent tasks (last 5) with calculated progress
    recent = sorted(all_tasks, key=lambda t: t.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:10]
    
    # Priority breakdown
    priority_counts = {}
    for t in all_tasks:
        p = get_priority_str(t.priority)
        priority_counts[p] = priority_counts.get(p, 0) + 1

    # Status breakdown
    status_counts = {}
    for t in all_tasks:
        s = get_status_str(t.status)
        status_counts[s] = status_counts.get(s, 0) + 1

    # Weekly completion trend (last 4 weeks)
    weekly_trend = []
    for i in range(3, -1, -1):
        week_start = today - timedelta(days=today.weekday() + 7 * i)
        week_end = week_start + timedelta(days=6)
        count = sum(1 for t in all_tasks if t.status and get_status_str(t.status) == 'completed' and t.updated_at and week_start <= t.updated_at.date() <= week_end)
        weekly_trend.append({"week": week_start.isoformat(), "completed": count})

    return {
        "total": total,
        "overdue": overdue,
        "due_today": due_today,
        "in_progress": in_progress,
        "completed": completed,
        "blocked": blocked,
        "open": open_count,
        "performance_pct": performance_pct,
        "priority_counts": priority_counts,
        "status_counts": status_counts,
        "recent_tasks": [_task_to_dict(t, db) for t in recent],
        "weekly_trend": weekly_trend,
    }


@router.get("/performance")
def user_performance_chart(
    period: str = Query("weekly", description="daily|weekly"),
    user_id: Optional[str] = Query(None, description="Optional User UUID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get time-series data for the performance graph."""
    from datetime import date as dt_date, datetime, timezone, timedelta
    
    target_user_id = current_user.id
    if user_id and user_id.strip():
        try:
            target_user_id = UUID(user_id)
        except ValueError:
            pass
    now_utc = datetime.now(timezone.utc)
    
    # Let's define the buckets
    buckets = []
    if period == "weekly":
        # Last 7 days rolling window
        start_time = now_utc - timedelta(days=7)
        for i in range(7):
            b_start = start_time + timedelta(days=i)
            b_end = b_start + timedelta(days=1) - timedelta(microseconds=1)
            buckets.append({
                "label": b_end.strftime("%a").upper(),
                "start": b_start,
                "end": b_end,
                "in_progress": 0, "completed": 0, "expired": 0
            })
    else:
        # Last 24 hours: 6 buckets of 4 hours rolling
        start_time = now_utc - timedelta(hours=24)
        for i in range(6):
            b_start = start_time + timedelta(hours=i*4)
            b_end = b_start + timedelta(hours=4) - timedelta(microseconds=1)
            # Label as local Hour + AM/PM or generic offset. 
            # We'll just show the starting hour of the bucket.
            buckets.append({
                "label": f"{b_start.strftime('%H:%M')}",
                "start": b_start,
                "end": b_end,
                "in_progress": 0, "completed": 0, "expired": 0
            })

    # Fetch tasks assigned to this user (performance should reflect their own tasks, not ones they merely authored)
    user_tasks = db.query(Task).filter(
        Task.assigned_to == target_user_id
    ).all()

    # Calculate active tasks overall in this period for the "0 Tasks" badge
    period_start = buckets[0]["start"]
    active_in_period = [t for t in user_tasks if (t.created_at and t.created_at >= period_start) or (t.updated_at and t.updated_at >= period_start)]
    total_tasks_in_period = len(active_in_period)

    # Distribute tasks into buckets based on updated_at
    # If a task hasn't been updated, fallback to created_at
    for t in user_tasks:
        timestamp = t.updated_at if t.updated_at else t.created_at
        if not timestamp: continue
        
        # Ensure timestamp is UTC aware for comparison
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        status_val = get_status_str(t.status)
        if status_val not in ('in_progress', 'completed', 'expired'):
            continue
            
        for b in buckets:
            if b["start"] <= timestamp <= b["end"]:
                b[status_val] += 1
                break

    return {
        "user_id": target_user_id,
        "total_tasks": total_tasks_in_period,
        "period": period,
        "chart_data": [
            {
                "label": b["label"],
                "in_progress": b["in_progress"],
                "completed": b["completed"],
                "expired": b["expired"]
            }
            for b in buckets
        ]
    }


@router.get("/")
def list_tasks(
    scope: Optional[str] = Query(None, description="all|my|created|watching"),
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    assigned_to: Optional[str] = None,
    assigned_by: Optional[str] = None,
    priority: Optional[str] = None,
    task_type: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: Optional[str] = Query("created_at", description="created_at|due_date|priority|status|title"),
    sort_dir: Optional[str] = Query("desc", description="asc|desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List tasks with optional filters, scoping, search, and pagination."""
    from datetime import date as dt_date
    check_update_expired_tasks(db)
    check_update_upcoming_tasks(db)

    q = db.query(Task).options(
        joinedload(Task.assignee),
        joinedload(Task.creator),
        joinedload(Task.project),
    )

    # Scope filtering
    if scope == "my":
        q = q.filter(Task.assigned_to == current_user.id)
    elif scope == "created":
        q = q.filter(Task.created_by == current_user.id)
    elif scope == "watching":
        # JSON array contains check
        q = q.filter(Task.watchers.op('?')(str(current_user.id)))
    elif scope == "archived":
        # Completed tasks aged 6+ months
        six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
        q = q.filter(
            Task.status == TaskStatus.COMPLETED,
            Task.completed_at <= six_months_ago
            # If completed_at is NULL (old records), we can fallback to created_at or just skip them
        ).order_by(Task.completed_at.desc())
    elif not current_user.is_superuser:
        # Default: show tasks related to user
        q = q.filter(
            or_(
                Task.assigned_to == current_user.id,
                Task.created_by == current_user.id,
                Task.assigned_by == current_user.id,
            )
        )

    # Field filters
    if project_id:
        q = q.filter(Task.project_id == project_id)
    if status:
        q = q.filter(Task.status == status)
    if assigned_to:
        q = q.filter(Task.assigned_to == assigned_to)
    if assigned_by:
        q = q.filter(Task.assigned_by == assigned_by)
    if priority:
        q = q.filter(Task.priority == priority)
    if task_type:
        q = q.filter(Task.task_type == task_type)

    # Search
    if search:
        q = q.filter(
            or_(
                Task.title.ilike(f"%{search}%"),
                Task.task_code.ilike(f"%{search}%"),
                Task.description.ilike(f"%{search}%"),
            )
        )

    # Date range
    if date_from:
        try:
            q = q.filter(Task.due_date >= dt_date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Task.due_date <= dt_date.fromisoformat(date_to))
        except ValueError:
            pass

    # Count before pagination
    total = q.count()

    # Sorting
    sort_column = getattr(Task, sort_by, Task.created_at)
    if sort_dir == "asc":
        q = q.order_by(sort_column.asc())
    else:
        q = q.order_by(sort_column.desc())

    # Pagination
    offset = (page - 1) * limit
    tasks = q.offset(offset).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    return {
        "items": [_task_to_dict(t, db) for t in tasks],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": total_pages,
    }


@router.get("/search")
def search_tasks(
    q: str = Query("", min_length=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search tasks by title or code for dependency picker."""
    query = db.query(Task)
    if q:
        query = query.filter(
            or_(
                Task.title.ilike(f"%{q}%"),
                Task.task_code.ilike(f"%{q}%"),
            )
        )
    tasks = query.order_by(Task.created_at.desc()).limit(20).all()
    return [
        {
            "id": str(t.id),
            "task_code": t.task_code,
            "title": t.title,
            "status": t.status.value if t.status else "open",
            "project_id": str(t.project_id) if t.project_id else None
        }
        for t in tasks
    ]


@router.get("/users/list")
def list_users_for_assignment(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all active users for task assignment dropdowns."""
    users = db.query(User).filter(User.is_active == True).order_by(User.full_name).all()
    return [
        {"id": str(u.id), "full_name": u.full_name, "email": u.email, "avatar_url": u.avatar_url, "job_title": u.job_title, "is_superuser": u.is_superuser}
        for u in users
    ]


@router.get("/{task_id}")
def get_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get task with full details."""
    task = db.query(Task).options(
        joinedload(Task.assignee),
        joinedload(Task.creator),
        joinedload(Task.project),
        joinedload(Task.checklist_items),
        joinedload(Task.comments),
        joinedload(Task.dependencies),
        joinedload(Task.activity_logs),
    ).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return _task_to_dict(task, db, full=True)


@router.put("/{task_id}")
def update_task(
    task_id: UUID,
    body: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a task and log changes."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    update_data = body.dict(exclude_unset=True)
    from datetime import date as dt_date, datetime, timezone

    old_status = task.status
    old_due_date = task.due_date

    for key, value in update_data.items():
        if key in ("checklist", "dependencies"):
            continue

        old_val = getattr(task, key, None)

        if key in ("project_id", "assigned_to", "module"):
            if isinstance(value, str) and value.strip() == "":
                value = None

        if key in ("project_id", "assigned_to"):
            if value is not None and value != "":
                if isinstance(value, str):
                    try:
                        value = uuid.UUID(value)
                    except ValueError:
                        value = None

        if key in ("start_date", "due_date"):
            if value == "":
                value = None
            elif value:
                if isinstance(value, str):
                    # Handle ISO string with time part if present
                    if "T" in value:
                        value = value.split("T")[0]
                    value = dt_date.fromisoformat(value)
        if key == "status" and value:
            try:
                value = TaskStatus(value)
            except ValueError:
                pass
        if key == "priority":
            if value == "":
                value = None
            elif value:
                try:
                    value = TaskPriority(value)
                except ValueError:
                    pass

        setattr(task, key, value)
        
        # Capture completed_at if status explicitly changed to completed
        if key == "status" and value == TaskStatus.COMPLETED:
            task.completed_at = func.now()
        elif key == "status" and value != TaskStatus.COMPLETED:
            # If changing AWAY from completed, we nullify completed_at to keep data clean
            task.completed_at = None
        
        # If start_date changed, check if it should be upcoming
        if key == "start_date" and get_status_str(task.status) in ('open', 'upcoming'):
            if task.start_date and task.start_date > dt_date.today():
                task.status = "upcoming"
            elif get_status_str(task.status) == 'upcoming':
                task.status = "open"

        # Log significant changes
        if key in ("status", "priority", "assigned_to", "progress", "is_blocked", "due_date"):
            log_activity(db, task.id, f"{key}_changed", current_user.id,
                         old_value=str(old_val) if old_val else None,
                         new_value=str(value) if value else None)

    # Auto-extend logic for admins
    if current_user.is_superuser and "due_date" in update_data:
        # If it was EXPIRED or EXTENDED and the new due date is greater than the old one
        if old_status in (TaskStatus.EXPIRED, TaskStatus.EXTENDED) and (task.status in (TaskStatus.EXPIRED, TaskStatus.EXTENDED) or task.status == old_status):
            if task.due_date and old_due_date and task.due_date > old_due_date:
                task.status = TaskStatus.EXTENDED
                if old_status != TaskStatus.EXTENDED:
                    log_activity(db, task.id, "status_changed", current_user.id,
                                 old_value="expired", new_value="extended")

    # Handle Nested Updates: Checklist
    if "checklist" in update_data:
        # Simple approach: clear and recreation for now, or we could match by text/id
        # Let's do clear and recreate to match Create flow simplicity
        db.query(TaskChecklist).filter(TaskChecklist.task_id == task.id).delete()
        if body.checklist:
            for item in body.checklist:
                db.add(TaskChecklist(
                    task_id=task.id,
                    item_text=item.item_text,
                    is_completed=item.is_completed
                ))
        log_activity(db, task.id, "checklist_updated", current_user.id)

    # Handle Nested Updates: Dependencies
    if "dependencies" in update_data:
        db.query(TaskDependency).filter(TaskDependency.task_id == task.id).delete()
        if body.dependencies:
            for dep in body.dependencies:
                db.add(TaskDependency(
                    task_id=task.id,
                    depends_on_task_id=uuid.UUID(dep.depends_on_task_id)
                ))
        log_activity(db, task.id, "dependencies_updated", current_user.id)
    # Notify on status change
    if "status" in update_data and task.notify_on_status_change:
        targets = set()
        if task.assigned_to:
            targets.add(str(task.assigned_to))
        if task.watchers:
            for w in task.watchers:
                targets.add(str(w))
        targets.discard(str(current_user.id))
        for uid in targets:
            db.add(Notification(
                user_id=uid,
                type="task_status_changed",
                title="Task Status Updated",
                message=f"Task '{task.title}' [{task.task_code}] status changed to {update_data['status']}",
                action_url=f"/user/tasks/view?taskId={task.id}"
            ))

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    recalculate_task_progress(db, task.id)
    db.refresh(task)
    return _task_to_dict(task, db)


@router.delete("/{task_id}")
def delete_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a task permanently."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    db.delete(task)
    db.commit()
    return {"message": "Task deleted successfully"}


@router.post("/{task_id}/assign")
def assign_task(
    task_id: UUID,
    body: TaskAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Assign or reassign a task to a user."""
    from app.models.project import Project
    from app.models.team_member import TeamMember
    
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Permission check: Admin, Project Manager, or Task Creator
    is_pm = False
    if task.project_id:
        pm_role = db.query(TeamMember).filter(
            TeamMember.project_id == task.project_id,
            TeamMember.user_id == current_user.id,
            TeamMember.role.in_(["owner", "manager", "admin"])
        ).first()
        if pm_role:
            is_pm = True
    
    # Task Creator or Admin (Superuser) or PM can assign
    is_authorized = current_user.is_superuser or is_pm or str(task.created_by) == str(current_user.id)
    
    if not is_authorized:
        raise HTTPException(status_code=403, detail="Not authorized to assign this task. Only Task Creators, Project Managers, or Admins can perform this action.")

    # Verify new assignee belongs to project team if project exists
    if task.project_id:
        membership = db.query(TeamMember).filter(
            TeamMember.project_id == task.project_id,
            TeamMember.user_id == body.assigned_to
        ).first()
        if not membership:
            raise HTTPException(status_code=400, detail="New assignee must be a member of the project team")

    # Verify assignee is active
    assignee = db.query(User).filter(User.id == body.assigned_to, User.is_active == True).first()
    if not assignee:
        raise HTTPException(status_code=400, detail="Cannot assign task to inactive user")

    old_assignee = task.assigned_to
    
    # Validation for reassignment notes
    if old_assignee and old_assignee != body.assigned_to and not body.notes:
        raise HTTPException(status_code=400, detail="Assignment notes are required for reassignment")

    # Store alignment record
    assignment_record = TaskAssignment(
        task_id=task.id,
        previous_assignee=old_assignee,
        new_assignee=body.assigned_to,
        assignment_type=body.assignment_type,
        role=body.role,
        notes=body.notes,
        assigned_by=current_user.id
    )
    db.add(assignment_record)

    # Update task
    task.assigned_to = body.assigned_to
    task.assigned_by = current_user.id
    
    if body.priority:
        task.priority = TaskPriority(body.priority.lower())
    
    if body.new_due_date:
        from datetime import date as dt_date
        new_due = dt_date.fromisoformat(body.new_due_date)
        if task.start_date and new_due < task.start_date:
            raise HTTPException(status_code=400, detail="Due date cannot be earlier than start date")
        task.due_date = new_due

    # Manage participants (reviewers/watchers)
    # First, clear existing of these types if updating them
    if body.reviewers is not None or body.watchers is not None:
        if body.reviewers is not None:
            # Clear existing reviewers
            db.query(TaskParticipant).filter(
                TaskParticipant.task_id == task.id,
                TaskParticipant.participant_type == "reviewer"
            ).delete()
            for r_id in body.reviewers:
                db.add(TaskParticipant(task_id=task.id, user_id=r_id, participant_type="reviewer"))
            task.reviewers = [str(r) for r in body.reviewers]

        if body.watchers is not None:
            # Clear existing watchers
            db.query(TaskParticipant).filter(
                TaskParticipant.task_id == task.id,
                TaskParticipant.participant_type == "watcher"
            ).delete()
            for w_id in body.watchers:
                db.add(TaskParticipant(task_id=task.id, user_id=w_id, participant_type="watcher"))
            task.watchers = [str(w) for w in body.watchers]

    # Log activity
    log_activity(db, task.id, "task_reassigned", current_user.id, 
                 old_value=str(old_assignee) if old_assignee else "unassigned",
                 new_value=str(body.assigned_to))

    # Trigger notifications
    targets = set()
    if body.notify_assignee: targets.add(str(body.assigned_to))
    if body.notify_reviewers and body.reviewers:
        for r in body.reviewers: targets.add(str(r))
    if body.notify_watchers and body.watchers:
        for w in body.watchers: targets.add(str(w))
    
    targets.discard(str(current_user.id))
    
    for uid in targets:
        db.add(Notification(
            user_id=uid,
            type="task_assigned",
            title="Task Assignment Update",
            message=f"Task {task.task_code} has been assigned to {assignee.full_name}.",
            action_url=f"/user/tasks/view?taskId={task.id}"
        ))

    db.commit()
    return {"message": "Task assigned successfully", "assignment_id": str(assignment_record.id)}


@router.get("/{task_id}/assignments")
def get_task_assignments(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get assignment history for a task."""
    records = db.query(TaskAssignment).filter(TaskAssignment.task_id == task_id).order_by(TaskAssignment.assigned_at.desc()).all()
    
    return [
        {
            "id": str(r.id),
            "previous_assignee": str(r.previous_assignee) if r.previous_assignee else None,
            "previous_assignee_name": r.prev_user.full_name if r.prev_user else None,
            "new_assignee": str(r.new_assignee),
            "new_assignee_name": r.new_user.full_name if r.new_user else None,
            "assignment_type": r.assignment_type,
            "role": r.role,
            "notes": r.notes,
            "assigned_by": str(r.assigned_by),
            "assigned_by_name": r.assigner.full_name if r.assigner else "System",
            "assigned_at": r.assigned_at.isoformat()
        }
        for r in records
    ]


@router.get("/{task_id}/participants")
def get_task_participants(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all participants for a task."""
    participants = db.query(TaskParticipant).filter(TaskParticipant.task_id == task_id).all()
    return [
        {
            "user_id": str(p.user_id),
            "full_name": p.user.full_name,
            "avatar_url": p.user.avatar_url,
            "participant_type": p.participant_type,
            "created_at": p.created_at.isoformat()
        }
        for p in participants
    ]


@router.post("/{task_id}/participants")
def update_task_participants(
    task_id: UUID,
    body: ParticipantsUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update reviewers and watchers list."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Clear existing
    db.query(TaskParticipant).filter(TaskParticipant.task_id == task_id).delete()
    
    # Add reviewers
    for r_id in body.reviewers:
        db.add(TaskParticipant(task_id=task_id, user_id=r_id, participant_type="reviewer"))
    
    # Add watchers
    for w_id in body.watchers:
        db.add(TaskParticipant(task_id=task_id, user_id=w_id, participant_type="watcher"))
    
    # Update task JSON fields
    task.reviewers = [str(uid) for uid in body.reviewers]
    task.watchers = [str(uid) for uid in body.watchers]
    
    log_activity(db, task.id, "participants_updated", current_user.id)
    db.commit()
    
    return {"message": "Participants updated successfully"}


@router.post("/{task_id}/comments")
def add_comment(
    task_id: UUID,
    body: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a comment to a task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    comment = TaskComment(
        task_id=task.id,
        user_id=current_user.id,
        comment=body.comment,
    )
    db.add(comment)
    log_activity(db, task.id, "comment_added", current_user.id, new_value=body.comment[:100])

    db.commit()
    db.refresh(comment)
    return {
        "id": str(comment.id),
        "task_id": str(comment.task_id),
        "user_id": str(comment.user_id),
        "user_name": current_user.full_name,
        "comment": comment.comment,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


@router.put("/{task_id}/checklist/{item_id}")
def update_checklist_item(
    task_id: UUID,
    item_id: UUID,
    is_completed: bool = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a task checklist item's completion status."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    item = db.query(TaskChecklist).filter(TaskChecklist.id == item_id, TaskChecklist.task_id == task_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    old_state = item.is_completed
    item.is_completed = is_completed

    log_activity(
        db, task.id, "checklist_updated", current_user.id,
        old_value=f"{item.item_text}: {old_state}",
        new_value=f"{item.item_text}: {is_completed}"
    )

    from datetime import datetime, timezone
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    recalculate_task_progress(db, task.id)
    return {"message": "Checklist updated", "progress": task.progress}

# ── Response Helper ──

def _task_to_dict(task: Task, db: Session, full: bool = False) -> dict:
    """Convert a Task ORM object to a response dict."""
    d = {
        "id": str(task.id),
        "task_code": task.task_code,
        "title": task.title,
        "description": task.description,
        "task_type": task.task_type,
        "module": task.module,
        "project_id": str(task.project_id) if task.project_id else None,
        "project_name": task.project.name if task.project else None,
        "assigned_to": str(task.assigned_to) if task.assigned_to else None,
        "assignee_name": task.assignee.full_name if task.assignee else None,
        "assignee_avatar": task.assignee.avatar_url if task.assignee else None,
        "assigned_by": str(task.assigned_by) if task.assigned_by else None,
        "assigner_name": task.assigner.full_name if task.assigner else None,
        "created_by": str(task.created_by) if task.created_by else None,
        "creator_name": task.creator.full_name if task.creator else None,
        "creator_avatar": task.creator.avatar_url if task.creator else None,
        "status": get_status_str(task.status),
        "priority": get_priority_str(task.priority),
        "start_date": task.start_date.isoformat() if task.start_date else None,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "estimated_hours": float(task.estimated_hours) if task.estimated_hours else None,
        "actual_hours": float(task.actual_hours) if task.actual_hours else None,
        "progress": task.progress or 0,
        "is_blocked": task.is_blocked or False,
        "reviewers": task.reviewers or [],
        "watchers": task.watchers or [],
        "notify_assignee": task.notify_assignee,
        "notify_watchers": task.notify_watchers,
        "notify_on_status_change": task.notify_on_status_change,
        "attachments": task.attachments or [],
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }

    # Always include checklist (needed for subtask button & validation)
    checklist_items = task.checklist_items if hasattr(task, 'checklist_items') and task.checklist_items else []
    if not checklist_items:
        checklist_items = db.query(TaskChecklist).filter(TaskChecklist.task_id == task.id).all()
    d["checklist"] = [
        {"id": str(c.id), "item_text": c.item_text, "is_completed": c.is_completed}
        for c in checklist_items
    ]

    # Resolve watcher names
    watcher_names = {}
    if task.watchers:
        watcher_users = db.query(User).filter(User.id.in_(task.watchers)).all()
        watcher_names = {str(u.id): u.full_name for u in watcher_users}
    d["watcher_names"] = watcher_names

    if full:
        d["comments"] = [
            {
                "id": str(c.id), "user_id": str(c.user_id),
                "user_name": c.user.full_name if c.user else "Unknown",
                "comment": c.comment,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in sorted((task.comments or []), key=lambda x: x.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        ]
        d["dependencies"] = [
            {
                "id": str(dep.id),
                "depends_on_task_id": str(dep.depends_on_task_id),
                "depends_on_title": dep.depends_on.title if dep.depends_on else None,
                "depends_on_code": dep.depends_on.task_code if dep.depends_on else None,
                "depends_on_status": get_status_str(dep.depends_on.status) if dep.depends_on and dep.depends_on.status else None,
            }
            for dep in (task.dependencies or [])
        ]
        d["activity_log"] = [
            {
                "id": str(a.id), "action": a.action,
                "old_value": a.old_value, "new_value": a.new_value,
                "user_name": a.user.full_name if a.user else "System",
                "timestamp": a.timestamp.isoformat() if a.timestamp else None,
            }
            for a in sorted((task.activity_logs or []), key=lambda x: x.timestamp or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        ]

    return d
