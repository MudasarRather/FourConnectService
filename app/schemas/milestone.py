from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
from uuid import UUID
from app.models.milestone import MilestoneStatus
from app.schemas.user import UserResponse

class MilestoneBase(BaseModel):
    name: str
    description: Optional[str] = None
    due_date: date
    start_date: Optional[date] = None
    status: Optional[str] = "pending"
    priority: Optional[str] = "medium"
    milestone_type: Optional[str] = None
    estimated_hours: Optional[float] = 0.0
    budget_amount: Optional[float] = 0.0
    currency: Optional[str] = "USD"
    assigned_to_id: Optional[UUID] = None
    remarks: Optional[str] = None

class MilestoneTaskBase(BaseModel):
    name: str
    estimated_minutes: int = 0
    is_completed: bool = False

class MilestoneTaskCreate(MilestoneTaskBase):
    pass

class MilestoneTaskResponse(MilestoneTaskBase):
    id: UUID
    milestone_id: UUID
    completed_at: Optional[datetime] = None
    completed_by: Optional[UserResponse] = None
    
    class Config:
        from_attributes = True

class MilestoneAssignmentResponse(BaseModel):
    id: UUID
    user_id: UUID
    status: str
    decline_reason: Optional[str] = None
    decline_count: Optional[int] = 0
    user: Optional[UserResponse] = None
    
    class Config:
        from_attributes = True

class MilestoneCreate(MilestoneBase):
    tasks: Optional[List[MilestoneTaskCreate]] = []

class MilestoneDecline(BaseModel):
    reason: str

class MilestoneDelete(BaseModel):
    reason: Optional[str] = None

class MilestoneUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[date] = None
    start_date: Optional[date] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    milestone_type: Optional[str] = None
    estimated_hours: Optional[float] = None
    budget_amount: Optional[float] = None
    currency: Optional[str] = None
    assigned_to_id: Optional[UUID] = None
    
    # Tracker Updates
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    delay_reason: Optional[str] = None

class MilestoneResponse(MilestoneBase):
    id: UUID
    project_id: UUID
    project_name: Optional[str] = None # Added for global context
    created_by_id: UUID
    created_by: Optional[UserResponse] = None # Added for explicit serialization
    file_path: Optional[str] = None
    
    # File Metadata
    file_size: Optional[str] = None
    uploaded_by: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    assigned_to: Optional[UserResponse] = None # Keeping for backward compat/primary display if needed
    assignments: List[MilestoneAssignmentResponse] = []
    tasks: List[MilestoneTaskResponse] = []
    decline_reason: Optional[str] = None
    
    # Tracker Response
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    delay_reason: Optional[str] = None
    
    # Audit
    last_updated_by_id: Optional[UUID] = None
    last_update_summary: Optional[str] = None
    last_updated_by: Optional[UserResponse] = None
    
    # Financials
    contribution_percentage: float = 0.0
    project_budget_amount: float = 0.0 # Added for tooltip context
    budget_amount_converted: float = 0.0 # Added for normalized charting

    class Config:
        from_attributes = True
