from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

from app.database import get_db
from app.models.milestone import Milestone
from app.models.milestone_task import MilestoneTask
from app.models.team_member import TeamMember
from app.models.audit_log import AuditLog
from app.models.user import User

router = APIRouter(prefix="/projects", tags=["activity"])

class ActivityItem(BaseModel):
    id: str # Unique ID (or synthetic)
    type: str # 'milestone_created', 'task_completed', 'member_joined', 'admin_action', 'milestone_updated'
    description: str
    user_name: str
    user_avatar: Optional[str] = None
    timestamp: datetime
    details: Optional[dict] = None

    class Config:
        from_attributes = True

@router.get("/{project_id}/activity", response_model=List[ActivityItem])
def get_project_activity(project_id: UUID, db: Session = Depends(get_db)):
    """
    Aggregates activity from various tables to form a project timeline.
    """
    activities = []

    # 1. Milestone Creations
    # Optimization: Eager load created_by, tasks, and task completers to avoid N+1 and lazy load issues
    from sqlalchemy.orm import joinedload
    milestones = db.query(Milestone).options(
        joinedload(Milestone.created_by),
        joinedload(Milestone.tasks).joinedload(MilestoneTask.completed_by)
    ).filter(Milestone.project_id == project_id).all()

    for m in milestones:
        user_name = m.created_by.full_name if m.created_by else "Unknown"
        activities.append({
            "id": f"milestone_{m.id}",
            "type": "milestone_created",
            "description": f"created milestone \"{m.name}\"",
            "user_name": user_name,
            "user_avatar": None, 
            "timestamp": m.created_at or datetime.now(), # Fallback for legacy bad data
            "details": {"milestone_id": str(m.id), "amount": m.budget_amount}
        })
        
        # 2. Milestone Tasks Completion
        for task in m.tasks:
            if task.is_completed and task.completed_at:
                completer = task.completed_by.full_name if task.completed_by else "System"
                activities.append({
                    "id": f"task_{task.id}",
                    "type": "task_completed",
                    "description": f"completed task \"{task.name}\"",
                    "user_name": completer,
                    "timestamp": task.completed_at,
                    "details": {"milestone_name": m.name}
                })

    # 3. Team Members (Joined/Accepted)
    team_members = db.query(TeamMember).filter(
        TeamMember.project_id == project_id
    ).all()
    
    for tm in team_members:
        # Only show activity if the invitation process is COMPLETE (Accepted)
        # User Requirement: "get activity only after team invitation is completed"
        if tm.status == 'accepted':
             assigner = tm.assigned_by.full_name if tm.assigned_by else "Admin"
             assignee = tm.user.full_name if tm.user else "User"
             
             # Fix Attribution for Project Owner
             # If role is 'Owner', they likely created it or were auto-assigned. 
             # We shouldn't say "Rauf invited Umran".
             if tm.role == 'Owner':
                 activities.append({
                    "id": f"team_join_{tm.id}",
                    "type": "member_joined", # Info color
                    "description": "joined as Project Owner",
                    "user_name": assignee, # The Owner themselves
                    "timestamp": tm.assigned_at, # Owners are assigned immediately
                    "details": {"role": "Owner"}
                })
             else:
                 # Regular Members: Show "Joined" event
                 # We use responded_at if available, else assigned_at fallback
                 ts = tm.responded_at if tm.responded_at else tm.assigned_at
                 
                 activities.append({
                    "id": f"team_join_{tm.id}",
                    "type": "member_joined",
                    "description": "joined the team",
                    "user_name": assignee,
                    "timestamp": ts or datetime.now(), # Fallback
                    "details": {"role": tm.role, "invited_by": assigner}
                })

    # 4. Audit Logs (Admin Actions on Project or its entities)
    # Fetch logs where entity_id is project_id OR entity_id is in milestone_ids
    milestone_ids = [m.id for m in milestones]
    audit_logs = db.query(AuditLog).filter(
        (AuditLog.entity_id == project_id) | 
        (AuditLog.entity_id.in_(milestone_ids))
    ).all()
    
    for log in audit_logs:
        actor = log.user.full_name if log.user else "System"
        # Determine strict type: Only Superusers get 'admin_action' tag (Shield Icon)
        # Regular users get 'project_update' (Edit/Zap Icon)
        is_admin = log.user.is_superuser if log.user else False
        
        act_type = "admin_action" if is_admin else "project_update"
        
        activities.append({
            "id": f"audit_{log.id}",
            "type": act_type,
            "description": log.action.replace('_', ' '),
            "user_name": actor,
            "timestamp": log.created_at,
            "details": {"notes": log.details}
        })

    # Sort by timestamp descending (handle None with safe fallback)
    activities.sort(key=lambda x: x['timestamp'] or datetime.min, reverse=True)

    return activities
