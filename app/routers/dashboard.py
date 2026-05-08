from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models.project import Project
# from app.models.task import Task, TaskStatus
# from app.models.expense import Expense
from app.models.user import User
from app.schemas.project import DashboardSummary
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get summarized statistics for the dashboard"""
    
    # Project stats
    total_projects = db.query(Project).filter(Project.created_by_id == current_user.id).count()
    active_projects = db.query(Project).filter(
        Project.created_by_id == current_user.id, 
        Project.status == "Active"
    ).count()
    
    # Task stats (Placeholder)
    total_tasks = 0 # db.query(Task).filter(Task.created_by == current_user.id).count()
    pending_tasks = 0
    completed_tasks = 0
    
    # Expense stats (Placeholder)
    total_expenses = 1250.75 # Placeholder until Expense model/router is fully integrated
    
    # Recent activities (mocked for now)
    recent_activities = [
        {"id": 1, "type": "task", "message": "Analyzed system performance", "time": "2 hours ago"},
        {"id": 2, "type": "project", "message": "Updated project milestones", "time": "5 hours ago"},
        {"id": 3, "type": "expense", "message": "Expense report submitted", "time": "1 day ago"},
    ]
    
    return {
        "total_projects": total_projects,
        "active_projects": active_projects,
        "total_tasks": total_tasks,
        "pending_tasks": pending_tasks,
        "completed_tasks": completed_tasks,
        "total_expenses": total_expenses,
        "recent_activities": recent_activities
    }
