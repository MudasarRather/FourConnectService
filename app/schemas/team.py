from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import UUID


# ============ Team Member Schemas ============

class TeamMemberCreate(BaseModel):
    user_id: UUID
    role: Optional[str] = None


class TeamMemberResponse(BaseModel):
    id: UUID
    project_id: UUID
    user_id: UUID
    user_name: Optional[str] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    user_phone: Optional[str] = None
    user_avatar: Optional[str] = None
    assigned_by_id: UUID
    assigned_by_name: Optional[str] = None
    status: str
    role: Optional[str] = None
    assigned_at: datetime
    responded_at: Optional[datetime] = None
    decline_reason: Optional[str] = None
    responded_at: Optional[datetime] = None
    decline_reason: Optional[str] = None
    override_reason: Optional[str] = None
    is_superuser: bool = False

    class Config:
        from_attributes = True


class TeamAssignRequest(BaseModel):
    """Request to assign multiple team members to a project"""
    user_ids: List[UUID]


class TeamRespondRequest(BaseModel):
    """Request to accept or decline a team invite"""
    accept: bool  # True = accept, False = decline
    reason: Optional[str] = None  # Reason for declining


# ============ Notification Schemas ============

class NotificationResponse(BaseModel):
    id: UUID
    user_id: UUID
    type: str
    title: str
    message: str
    related_project_id: Optional[UUID] = None
    related_project_name: Optional[str] = None
    related_user_id: Optional[UUID] = None
    related_user_name: Optional[str] = None
    related_team_member_id: Optional[UUID] = None
    is_read: bool
    is_dismissed: bool
    action_url: Optional[str] = None
    created_at: datetime
    is_sender_admin: bool = False

    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    items: List[NotificationResponse]
    unread_count: int


class UnreadCountResponse(BaseModel):
    count: int


# ============ Project Selection for Team ============

class ApprovedProjectResponse(BaseModel):
    id: UUID
    code: str
    name: str
    description: Optional[str] = None
    status: str
    created_by_name: Optional[str] = None
    created_by_id: Optional[UUID] = None
    team_count: int = 0
    team_members: Optional[List[dict]] = None  # Team members array for display
    
    # Details for modal
    project_type: Optional[str] = None
    organization: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget_amount: float = 0.0
    currency: str = "USD"

    class Config:
        from_attributes = True


class UserSelectResponse(BaseModel):
    """User info for team member selection"""
    id: UUID
    full_name: str
    email: str
    department: Optional[str] = None
    job_title: Optional[str] = None
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True


class PaginatedApprovedProjectResponse(BaseModel):
    items: List[ApprovedProjectResponse]
    total: int
    page: int
    pages: int
    limit: int
    total_budget: float
    unassigned_count: int
    pending_count: int
