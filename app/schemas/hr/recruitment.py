"""HR Recruitment schemas (Pydantic v2).

All request and response shapes for the recruitment module. Each entity has
the standard triplet: <Entity>Create, <Entity>Update, <Entity>Response. List
responses paginate as { items, total, page, limit, total_pages }.
"""
import enum
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Any, Dict
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator

from app.models.hr.recruitment import (
    HiringType, RecEmploymentType, Priority, RequisitionStatus,
    PositionStatus, WorkMode, CandidateStatus, ApplicationStage,
    ApplicationSource, InterviewType, InterviewMode, InterviewRound,
    InterviewStatus, FeedbackRecommendation, OfferStatus,
)


# ──────────────────────────────────────────────────────────────────────────────
# Job Requisition
# ──────────────────────────────────────────────────────────────────────────────

class RequisitionBase(BaseModel):
    job_title: str = Field(..., min_length=1, max_length=160)
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    hiring_type: HiringType = HiringType.NEW
    employment_type: RecEmploymentType = RecEmploymentType.FULL_TIME
    number_of_openings: int = Field(default=1, ge=1, le=999)
    priority: Priority = Priority.MEDIUM
    hiring_manager_id: Optional[UUID] = None
    budgeted_salary_min: Optional[Decimal] = None
    budgeted_salary_max: Optional[Decimal] = None
    currency: str = "INR"
    required_skills: Optional[List[str]] = None
    experience_min_years: Optional[Decimal] = None
    experience_max_years: Optional[Decimal] = None
    qualification: Optional[str] = None
    job_description: Optional[str] = None
    reason_for_hiring: Optional[str] = None
    expected_joining_date: Optional[date] = None


class RequisitionCreate(RequisitionBase):
    model_config = ConfigDict(extra="ignore")


class RequisitionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_title: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    hiring_type: Optional[HiringType] = None
    employment_type: Optional[RecEmploymentType] = None
    number_of_openings: Optional[int] = None
    priority: Optional[Priority] = None
    hiring_manager_id: Optional[UUID] = None
    budgeted_salary_min: Optional[Decimal] = None
    budgeted_salary_max: Optional[Decimal] = None
    currency: Optional[str] = None
    required_skills: Optional[List[str]] = None
    experience_min_years: Optional[Decimal] = None
    experience_max_years: Optional[Decimal] = None
    qualification: Optional[str] = None
    job_description: Optional[str] = None
    reason_for_hiring: Optional[str] = None
    expected_joining_date: Optional[date] = None
    status: Optional[RequisitionStatus] = None


class RequisitionResponse(RequisitionBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    requisition_number: str
    status: RequisitionStatus
    approved_by_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    rejected_reason: Optional[str] = None
    requested_by_id: UUID
    department_name: Optional[str] = None
    designation_name: Optional[str] = None
    location_name: Optional[str] = None
    hiring_manager_name: Optional[str] = None
    requested_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RequisitionListResponse(BaseModel):
    items: List[RequisitionResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class RequisitionApproval(BaseModel):
    approve: bool
    reason: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Job Position
# ──────────────────────────────────────────────────────────────────────────────

class PositionBase(BaseModel):
    job_title: str = Field(..., min_length=1, max_length=160)
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    recruiter_id: Optional[UUID] = None
    hiring_manager_id: Optional[UUID] = None
    openings_count: int = Field(default=1, ge=1, le=999)
    experience_min_years: Optional[Decimal] = None
    experience_max_years: Optional[Decimal] = None
    salary_min: Optional[Decimal] = None
    salary_max: Optional[Decimal] = None
    currency: str = "INR"
    work_mode: WorkMode = WorkMode.ONSITE
    employment_type: RecEmploymentType = RecEmploymentType.FULL_TIME
    skills_required: Optional[List[str]] = None
    qualification: Optional[str] = None
    job_description: Optional[str] = None
    perks: Optional[str] = None
    publish_date: Optional[date] = None
    expiry_date: Optional[date] = None


class PositionCreate(PositionBase):
    model_config = ConfigDict(extra="ignore")
    requisition_id: Optional[UUID] = None


class PositionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_title: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    recruiter_id: Optional[UUID] = None
    hiring_manager_id: Optional[UUID] = None
    openings_count: Optional[int] = None
    experience_min_years: Optional[Decimal] = None
    experience_max_years: Optional[Decimal] = None
    salary_min: Optional[Decimal] = None
    salary_max: Optional[Decimal] = None
    currency: Optional[str] = None
    work_mode: Optional[WorkMode] = None
    employment_type: Optional[RecEmploymentType] = None
    skills_required: Optional[List[str]] = None
    qualification: Optional[str] = None
    job_description: Optional[str] = None
    perks: Optional[str] = None
    publish_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: Optional[PositionStatus] = None


class PositionCloseReason(str, enum.Enum):
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    BUDGET = "BUDGET"
    RESCOPED = "RESCOPED"
    OTHER = "OTHER"


class PositionCloseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: PositionCloseReason
    note: Optional[str] = Field(default=None, max_length=1000)


class PositionResponse(PositionBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    job_code: str
    requisition_id: Optional[UUID] = None
    filled_count: int
    status: PositionStatus
    department_name: Optional[str] = None
    designation_name: Optional[str] = None
    location_name: Optional[str] = None
    recruiter_name: Optional[str] = None
    hiring_manager_name: Optional[str] = None
    applications_count: int = 0
    close_reason: Optional[str] = None
    close_note: Optional[str] = None
    closed_at: Optional[datetime] = None
    closed_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class PositionListResponse(BaseModel):
    items: List[PositionResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ──────────────────────────────────────────────────────────────────────────────
# Candidate
# ──────────────────────────────────────────────────────────────────────────────

class CandidateBase(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=160)
    email: Optional[str] = None
    mobile: Optional[str] = None
    gender: Optional[str] = None
    dob: Optional[date] = None
    current_city: Optional[str] = None
    current_state: Optional[str] = None
    current_country: Optional[str] = None
    preferred_locations: Optional[List[str]] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    github_url: Optional[str] = None
    current_company: Optional[str] = None
    current_designation: Optional[str] = None
    total_experience_years: Optional[Decimal] = None
    relevant_experience_years: Optional[Decimal] = None
    current_salary: Optional[Decimal] = None
    expected_salary: Optional[Decimal] = None
    currency: str = "INR"
    notice_period_days: Optional[int] = None
    highest_qualification: Optional[str] = None
    university: Optional[str] = None
    passing_year: Optional[int] = None
    skills: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    resume_url: Optional[str] = None
    cover_letter_url: Optional[str] = None
    documents: Optional[List[Dict[str, Any]]] = None
    source: ApplicationSource = ApplicationSource.DIRECT
    notes: Optional[str] = None


class CandidateCreate(CandidateBase):
    model_config = ConfigDict(extra="ignore")


class CandidateUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    full_name: Optional[str] = None
    email: Optional[str] = None
    mobile: Optional[str] = None
    gender: Optional[str] = None
    dob: Optional[date] = None
    current_city: Optional[str] = None
    current_state: Optional[str] = None
    current_country: Optional[str] = None
    preferred_locations: Optional[List[str]] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    github_url: Optional[str] = None
    current_company: Optional[str] = None
    current_designation: Optional[str] = None
    total_experience_years: Optional[Decimal] = None
    relevant_experience_years: Optional[Decimal] = None
    current_salary: Optional[Decimal] = None
    expected_salary: Optional[Decimal] = None
    currency: Optional[str] = None
    notice_period_days: Optional[int] = None
    highest_qualification: Optional[str] = None
    university: Optional[str] = None
    passing_year: Optional[int] = None
    skills: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    resume_url: Optional[str] = None
    cover_letter_url: Optional[str] = None
    documents: Optional[List[Dict[str, Any]]] = None
    source: Optional[ApplicationSource] = None
    notes: Optional[str] = None
    status: Optional[CandidateStatus] = None
    is_blacklisted: Optional[bool] = None
    is_in_talent_pool: Optional[bool] = None


class CandidateResponse(CandidateBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    candidate_code: str
    status: CandidateStatus
    is_blacklisted: bool
    is_in_talent_pool: bool
    applications_count: int = 0
    created_at: datetime
    updated_at: datetime


class CandidateListResponse(BaseModel):
    items: List[CandidateResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ──────────────────────────────────────────────────────────────────────────────
# Application
# ──────────────────────────────────────────────────────────────────────────────

class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidate_id: UUID
    position_id: UUID
    source: ApplicationSource = ApplicationSource.PORTAL
    recruiter_id: Optional[UUID] = None
    notes: Optional[str] = None


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    current_stage: Optional[ApplicationStage] = None
    recruiter_id: Optional[UUID] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    score: Optional[Decimal] = None
    notes: Optional[str] = None
    rejection_reason: Optional[str] = None


class ApplicationStageChange(BaseModel):
    stage: ApplicationStage
    notes: Optional[str] = None
    rejection_reason: Optional[str] = None


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    application_code: str
    candidate_id: UUID
    position_id: UUID
    applied_date: datetime
    recruiter_id: Optional[UUID] = None
    source: ApplicationSource
    current_stage: ApplicationStage
    stage_changed_at: datetime
    rating: Optional[int] = None
    score: Optional[Decimal] = None
    notes: Optional[str] = None
    rejection_reason: Optional[str] = None
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    position_title: Optional[str] = None
    position_code: Optional[str] = None
    department_name: Optional[str] = None
    recruiter_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ApplicationListResponse(BaseModel):
    items: List[ApplicationResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ──────────────────────────────────────────────────────────────────────────────
# Interview Panel
# ──────────────────────────────────────────────────────────────────────────────

class PanelMember(BaseModel):
    user_id: Optional[UUID] = None
    name: str
    role: Optional[str] = None
    email: Optional[str] = None


class InterviewPanelBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    department_id: Optional[UUID] = None
    expertise: Optional[List[str]] = None
    description: Optional[str] = None
    members: Optional[List[PanelMember]] = None
    is_active: bool = True


class InterviewPanelCreate(InterviewPanelBase):
    model_config = ConfigDict(extra="ignore")


class InterviewPanelUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    department_id: Optional[UUID] = None
    expertise: Optional[List[str]] = None
    description: Optional[str] = None
    members: Optional[List[PanelMember]] = None
    is_active: Optional[bool] = None


class InterviewPanelResponse(InterviewPanelBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    department_name: Optional[str] = None
    member_count: int = 0
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────────
# Interview
# ──────────────────────────────────────────────────────────────────────────────

class InterviewerEntry(BaseModel):
    user_id: Optional[UUID] = None
    name: str
    email: Optional[str] = None


class InterviewBase(BaseModel):
    application_id: UUID
    panel_id: Optional[UUID] = None
    interview_type: InterviewType = InterviewType.HR
    mode: InterviewMode = InterviewMode.ONLINE
    round: InterviewRound = InterviewRound.R1
    scheduled_at: datetime
    duration_minutes: int = Field(default=60, ge=10, le=600)
    meeting_link: Optional[str] = None
    venue: Optional[str] = None
    interviewers: Optional[List[InterviewerEntry]] = None
    notes: Optional[str] = None


class InterviewCreate(InterviewBase):
    model_config = ConfigDict(extra="ignore")


class InterviewUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    panel_id: Optional[UUID] = None
    interview_type: Optional[InterviewType] = None
    mode: Optional[InterviewMode] = None
    round: Optional[InterviewRound] = None
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    meeting_link: Optional[str] = None
    venue: Optional[str] = None
    interviewers: Optional[List[InterviewerEntry]] = None
    notes: Optional[str] = None
    status: Optional[InterviewStatus] = None


class InterviewResponse(InterviewBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    interview_code: str
    status: InterviewStatus
    candidate_name: Optional[str] = None
    position_title: Optional[str] = None
    panel_name: Optional[str] = None
    feedback_count: int = 0
    created_at: datetime
    updated_at: datetime


class InterviewListResponse(BaseModel):
    items: List[InterviewResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class InterviewFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    technical_rating: Optional[int] = Field(default=None, ge=1, le=5)
    communication_rating: Optional[int] = Field(default=None, ge=1, le=5)
    cultural_fit_rating: Optional[int] = Field(default=None, ge=1, le=5)
    overall_rating: Optional[int] = Field(default=None, ge=1, le=5)
    recommendation: FeedbackRecommendation = FeedbackRecommendation.HOLD
    strengths: Optional[str] = None
    weaknesses: Optional[str] = None
    detailed_feedback: Optional[str] = None


class InterviewFeedbackResponse(InterviewFeedbackCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    interview_id: UUID
    interviewer_id: UUID
    interviewer_name: Optional[str] = None
    submitted_at: datetime


# ──────────────────────────────────────────────────────────────────────────────
# Offer
# ──────────────────────────────────────────────────────────────────────────────

class OfferBase(BaseModel):
    application_id: UUID
    designation: Optional[str] = None
    department_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    offered_salary: Decimal = Field(..., gt=0)
    bonus: Optional[Decimal] = Decimal("0")
    currency: str = "INR"
    joining_date: Optional[date] = None
    reporting_manager_id: Optional[UUID] = None
    offer_valid_till: Optional[date] = None
    notes: Optional[str] = None
    offer_letter_url: Optional[str] = None


class OfferCreate(OfferBase):
    model_config = ConfigDict(extra="ignore")


class OfferUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    designation: Optional[str] = None
    department_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    offered_salary: Optional[Decimal] = None
    bonus: Optional[Decimal] = None
    currency: Optional[str] = None
    joining_date: Optional[date] = None
    reporting_manager_id: Optional[UUID] = None
    offer_valid_till: Optional[date] = None
    notes: Optional[str] = None
    offer_letter_url: Optional[str] = None
    status: Optional[OfferStatus] = None


class OfferResponse(OfferBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    offer_code: str
    candidate_id: UUID
    position_id: UUID
    status: OfferStatus
    approved_by_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    candidate_response_at: Optional[datetime] = None
    candidate_response_note: Optional[str] = None
    candidate_name: Optional[str] = None
    position_title: Optional[str] = None
    # Back-link to the Employee record created from this offer (NULL until onboarded)
    employee_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class OfferListResponse(BaseModel):
    items: List[OfferResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class OfferResponseAction(BaseModel):
    accept: bool
    note: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Onboarding prefill — flat payload consumed by the Add Employee wizard
# when a recruiter picks an accepted offer.
# ──────────────────────────────────────────────────────────────────────────────

class OnboardingPrefillCandidate(BaseModel):
    id: UUID
    candidate_code: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    mobile: Optional[str] = None
    gender: Optional[str] = None
    dob: Optional[date] = None
    current_city: Optional[str] = None
    current_state: Optional[str] = None
    current_country: Optional[str] = None
    notice_period_days: Optional[int] = None
    highest_qualification: Optional[str] = None
    current_designation: Optional[str] = None


class OnboardingPrefillPosition(BaseModel):
    id: UUID
    job_title: Optional[str] = None
    job_code: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    employment_type: Optional[str] = None
    work_mode: Optional[str] = None


class OnboardingPrefillOffer(BaseModel):
    joining_date: Optional[date] = None
    designation_text: Optional[str] = None
    offered_salary: Optional[Decimal] = None
    bonus: Optional[Decimal] = None
    currency: Optional[str] = None
    reporting_manager_id: Optional[UUID] = None
    offer_valid_till: Optional[date] = None
    # Mirror of the offer's department/grade/location overrides
    department_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    location_id: Optional[UUID] = None


class OnboardingPrefillResponse(BaseModel):
    offer_id: UUID
    offer_code: str
    candidate: OnboardingPrefillCandidate
    position: OnboardingPrefillPosition
    offer: OnboardingPrefillOffer


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard & Analytics
# ──────────────────────────────────────────────────────────────────────────────

class RecruitmentDashboardStats(BaseModel):
    open_positions: int = 0
    applications_received: int = 0
    candidates_in_pipeline: int = 0
    pending_interviews: int = 0
    offers_pending: int = 0
    hires_this_month: int = 0
    rejected_candidates: int = 0
    avg_time_to_hire_days: float = 0.0
    offer_acceptance_rate: float = 0.0


class FunnelStage(BaseModel):
    stage: str
    count: int


class DepartmentHiring(BaseModel):
    department: str
    open_positions: int
    applications: int
    hires: int


class MonthlyTrendItem(BaseModel):
    month: str  # "2026-03"
    applications: int
    hires: int


class RecruitmentDashboardData(BaseModel):
    stats: RecruitmentDashboardStats
    funnel: List[FunnelStage]
    department_hiring: List[DepartmentHiring]
    monthly_trend: List[MonthlyTrendItem]
    candidate_status_distribution: List[FunnelStage]
    sources_distribution: List[FunnelStage] = []
    recent_activities: List[Dict[str, Any]] = []


class PipelineCard(BaseModel):
    application_id: UUID
    candidate_id: UUID
    candidate_name: str
    candidate_email: Optional[str] = None
    position_title: str
    position_code: str
    applied_date: datetime
    rating: Optional[int] = None
    stage: ApplicationStage
    avatar_initials: str = ""


class PipelineStage(BaseModel):
    stage: ApplicationStage
    label: str
    count: int
    cards: List[PipelineCard]
