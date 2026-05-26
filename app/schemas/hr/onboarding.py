"""HR Onboarding Pydantic schemas."""
from datetime import date, datetime
from typing import Optional, List, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.hr.onboarding import (
    OnboardingStatus, OnboardingStage, ChecklistCategory, ChecklistItemStatus,
    DocumentSlotStatus, ApprovalRole, ApprovalDecision,
    TaskStatus as OnbTaskStatus, TaskPriority as OnbTaskPriority,
    IdentityStatus, WelcomeKitStatus,
)


# ────────────────────── Process ──────────────────────

class OnboardingProcessCreate(BaseModel):
    employee_id: UUID
    offer_id: Optional[UUID] = None
    target_joining_date: Optional[date] = None


class OnboardingProcessUpdate(BaseModel):
    status: Optional[OnboardingStatus] = None
    current_stage: Optional[OnboardingStage] = None
    target_joining_date: Optional[date] = None
    actual_joining_date: Optional[date] = None
    on_hold_reason: Optional[str] = None
    progress_pct: Optional[int] = Field(default=None, ge=0, le=100)


class OnboardingProcessResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    offer_id: Optional[UUID] = None
    status: OnboardingStatus
    current_stage: OnboardingStage
    progress_pct: int
    target_joining_date: Optional[date] = None
    actual_joining_date: Optional[date] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    on_hold_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # Optional denormalised fields the API may add (employee snapshot)
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    employee_designation: Optional[str] = None
    employee_department: Optional[str] = None


class OnboardingProcessListResponse(BaseModel):
    items: List[OnboardingProcessResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ────────────────────── Checklist ──────────────────────

class ChecklistTemplateCreate(BaseModel):
    category: ChecklistCategory
    task_name: str
    description: Optional[str] = None
    default_assignee_role: Optional[str] = None
    default_due_offset_days: int = 0
    is_mandatory: bool = True
    sort_order: int = 0


class ChecklistTemplateUpdate(BaseModel):
    category: Optional[ChecklistCategory] = None
    task_name: Optional[str] = None
    description: Optional[str] = None
    default_assignee_role: Optional[str] = None
    default_due_offset_days: Optional[int] = None
    is_mandatory: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class ChecklistTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    category: ChecklistCategory
    task_name: str
    description: Optional[str] = None
    default_assignee_role: Optional[str] = None
    default_due_offset_days: int
    is_active: bool
    is_mandatory: bool
    sort_order: int


class ChecklistItemCreate(BaseModel):
    process_id: UUID
    category: ChecklistCategory
    task_name: str
    description: Optional[str] = None
    assigned_to_user_id: Optional[UUID] = None
    due_date: Optional[date] = None
    is_mandatory: bool = False
    sort_order: int = 0


class ChecklistItemUpdate(BaseModel):
    task_name: Optional[str] = None
    description: Optional[str] = None
    assigned_to_user_id: Optional[UUID] = None
    due_date: Optional[date] = None
    status: Optional[ChecklistItemStatus] = None
    remarks: Optional[str] = None
    sort_order: Optional[int] = None


class ChecklistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    process_id: UUID
    template_id: Optional[UUID] = None
    category: ChecklistCategory
    task_name: str
    description: Optional[str] = None
    assigned_to_user_id: Optional[UUID] = None
    assigned_to_name: Optional[str] = None
    due_date: Optional[date] = None
    status: ChecklistItemStatus
    is_mandatory: bool
    completed_by_user_id: Optional[UUID] = None
    completed_by_name: Optional[str] = None
    completed_at: Optional[datetime] = None
    remarks: Optional[str] = None
    sort_order: int


# ────────────────────── Documents ──────────────────────

class OnboardingDocumentSlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    process_id: UUID
    doc_type_key: str
    doc_type_label: str
    is_mandatory: bool
    drive_document_id: Optional[UUID] = None
    drive_file_url: Optional[str] = None
    drive_file_name: Optional[str] = None
    status: DocumentSlotStatus
    expiry_date: Optional[date] = None
    ocr_data: Optional[Any] = None
    verified_by_user_id: Optional[UUID] = None
    verified_by_name: Optional[str] = None
    verified_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    sort_order: int


class DocumentVerifyBody(BaseModel):
    notes: Optional[str] = None
    expiry_date: Optional[date] = None


class DocumentRejectBody(BaseModel):
    reason: str


# ────────────────────── Approvals ──────────────────────

class JoiningApprovalCreate(BaseModel):
    process_id: UUID
    approver_role: ApprovalRole
    approver_user_id: Optional[UUID] = None
    sort_order: int = 0


class JoiningApprovalDecideBody(BaseModel):
    decision: ApprovalDecision
    notes: Optional[str] = None


class JoiningApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    process_id: UUID
    approver_role: ApprovalRole
    approver_user_id: Optional[UUID] = None
    approver_name: Optional[str] = None
    status: ApprovalDecision
    sort_order: int
    decision_at: Optional[datetime] = None
    decision_notes: Optional[str] = None


# ────────────────────── Tasks ──────────────────────

class OnboardingTaskCreate(BaseModel):
    process_id: UUID
    title: str
    description: Optional[str] = None
    category: Optional[ChecklistCategory] = None
    assigned_to_user_id: Optional[UUID] = None
    due_date: Optional[date] = None
    priority: OnbTaskPriority = OnbTaskPriority.MEDIUM
    sla_hours: Optional[int] = None
    escalation_user_id: Optional[UUID] = None
    depends_on_task_id: Optional[UUID] = None


class OnboardingTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[ChecklistCategory] = None
    assigned_to_user_id: Optional[UUID] = None
    due_date: Optional[date] = None
    status: Optional[OnbTaskStatus] = None
    priority: Optional[OnbTaskPriority] = None
    sla_hours: Optional[int] = None
    escalation_user_id: Optional[UUID] = None


class OnboardingTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    process_id: UUID
    title: str
    description: Optional[str] = None
    category: Optional[ChecklistCategory] = None
    assigned_to_user_id: Optional[UUID] = None
    assigned_to_name: Optional[str] = None
    due_date: Optional[date] = None
    status: OnbTaskStatus
    priority: OnbTaskPriority
    completed_at: Optional[datetime] = None
    sla_hours: Optional[int] = None
    escalation_user_id: Optional[UUID] = None
    depends_on_task_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


# ────────────────────── Identity ──────────────────────

class EmployeeIdentityUpdate(BaseModel):
    official_email: Optional[str] = None
    biometric_id: Optional[str] = None
    rfid_card_number: Optional[str] = None
    access_card_number: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    qr_payload: Optional[str] = None


class EmployeeIdentityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    official_email: Optional[str] = None
    biometric_id: Optional[str] = None
    rfid_card_number: Optional[str] = None
    access_card_number: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    qr_payload: Optional[str] = None
    status: IdentityStatus
    issued_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


# ────────────────────── Welcome Kit ──────────────────────

class WelcomeKitTemplateUpsert(BaseModel):
    name: str
    description: Optional[str] = None
    default_items: List[dict] = Field(default_factory=list)


class WelcomeKitTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: Optional[str] = None
    default_items: List[Any] = Field(default_factory=list)
    is_active: bool


class WelcomeKitCreate(BaseModel):
    employee_id: UUID
    process_id: Optional[UUID] = None
    template_id: Optional[UUID] = None
    items: Optional[List[dict]] = None


class WelcomeKitUpdate(BaseModel):
    status: Optional[WelcomeKitStatus] = None
    items: Optional[List[dict]] = None
    tracking_number: Optional[str] = None
    notes: Optional[str] = None


class WelcomeKitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    process_id: Optional[UUID] = None
    template_id: Optional[UUID] = None
    status: WelcomeKitStatus
    items: List[Any] = Field(default_factory=list)
    tracking_number: Optional[str] = None
    packed_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    notes: Optional[str] = None


# ────────────────────── Dashboard / Journey ──────────────────────

class OnboardingStageState(BaseModel):
    key: OnboardingStage
    label: str
    count: int
    percent: int
    is_active: bool
    is_complete: bool


class JourneyStateResponse(BaseModel):
    process_id: Optional[UUID] = None
    current_stage: Optional[OnboardingStage] = None
    progress_pct: Optional[int] = None
    stages: List[OnboardingStageState]


class DashboardStatsResponse(BaseModel):
    pending_joinings: int          # accepted offers w/o employee yet
    today_joining: int             # joining_date == today
    pending_documents: int         # processes with at least 1 PENDING/REJECTED mandatory doc
    pending_asset_allocation: int  # processes with no laptop allocated yet
    probation_employees: int
    training_pending: int
    incomplete_onboarding: int
    completed_onboarding: int = 0   # processes with status == COMPLETED
    department_wise_joining: List[dict]  # [{department, count}]


class HotTaskResponse(BaseModel):
    id: UUID
    process_id: UUID
    employee_name: Optional[str] = None
    title: str
    due_date: Optional[date] = None
    status: OnbTaskStatus
    priority: OnbTaskPriority
    sla_breach: bool = False


# ────────────────────── Pending Joining (read-only from Recruitment) ──────────────────────

class PendingJoiningResponse(BaseModel):
    offer_id: UUID
    offer_code: str
    candidate_id: UUID
    candidate_name: str
    candidate_email: Optional[str] = None
    candidate_mobile: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    joining_date: Optional[date] = None
    offered_salary: Optional[float] = None
    accepted_at: Optional[datetime] = None
    has_employee: bool = False


# ────────────────────── Process detail (deep) ──────────────────────

class ProcessDetailResponse(BaseModel):
    process: OnboardingProcessResponse
    checklist: List[ChecklistItemResponse]
    documents: List[OnboardingDocumentSlotResponse]
    approvals: List[JoiningApprovalResponse]
    tasks: List[OnboardingTaskResponse]
    identity: Optional[EmployeeIdentityResponse] = None
    welcome_kit: Optional[WelcomeKitResponse] = None
