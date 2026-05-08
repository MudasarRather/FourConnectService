from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import UUID

class ProjectBase(BaseModel):
    name: str
    description: str
    project_type: str
    organization: Optional[str] = None
    cost_center: str
    start_date: datetime
    end_date: datetime
    budget_amount: float
    currency: str = "USD"
    budget_type: str

class ProjectCreate(ProjectBase):
    code: Optional[str] = None
    status: Optional[str] = None
    project_order_path: Optional[str] = None  # Path to uploaded Project Order PDF

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    project_type: Optional[str] = None
    organization: Optional[str] = None
    cost_center: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget_amount: Optional[float] = None
    currency: Optional[str] = None
    budget_type: Optional[str] = None
    status: Optional[str] = None

from app.schemas.team import TeamMemberResponse

class ProjectResponse(BaseModel):
    id: UUID
    code: str
    name: str
    description: Optional[str] = None  # Optional for existing data
    project_type: str
    organization: Optional[str] = None
    cost_center: str
    start_date: Optional[datetime] = None  # Optional for existing data
    end_date: Optional[datetime] = None  # Optional for existing data
    budget_amount: float = 0.0
    currency: str = "USD"
    budget_type: Optional[str] = None  # Optional for existing data
    status: str
    is_approved: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by_id: UUID
    created_by_name: Optional[str] = None
    created_by_employee_code: Optional[str] = None
    created_by_phone: Optional[str] = None
    created_by_address: Optional[str] = None
    project_order_path: Optional[str] = None  # Path to uploaded Project Order PDF
    
    # File Metadata
    file_size: Optional[str] = None
    uploaded_by: Optional[str] = None
    team_members: List[TeamMemberResponse] = []
    current_user_membership_status: Optional[str] = None # 'pending', 'accepted', 'declined', 'owner', 'admin'
    budget_utilized: Optional[float] = 0
    budget_consumed: Optional[float] = 0 # New: Cost of COMPLETED milestones.0
    completion_percentage: float = 0.0 # New: Based on milestone completion

    class Config:
        from_attributes = True

class ProjectListResponse(BaseModel):
    items: List[ProjectResponse]
    total: int
    page: int
    size: int
    pages: int

class DashboardActivity(BaseModel):
    id: int
    type: str
    message: str
    time: str

class DashboardSummary(BaseModel):
    total_projects: int
    active_projects: int
    total_tasks: int
    pending_tasks: int
    completed_tasks: int
    total_expenses: float
    recent_activities: list[DashboardActivity]
