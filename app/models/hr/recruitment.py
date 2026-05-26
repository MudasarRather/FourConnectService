"""HR Recruitment models — Phase 4 hiring.

Complete corporate-grade recruitment flow:
  Job Requisition → Approval → Open Position → Candidate → Application →
  Screening → Interview Scheduling → Interview Feedback → Offer → Joined → Employee

All models follow project conventions:
  - UUID primary keys
  - DateTime(timezone=True) with server_default=func.now()
  - Soft delete via is_deleted on top-level entities
  - is_active flags where appropriate
  - JSON columns for flexible structured data (skills tags, etc.)
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Numeric, Integer, Text, JSON, Index, Sequence, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────

class HiringType(str, enum.Enum):
    NEW = "NEW"
    REPLACEMENT = "REPLACEMENT"
    BACKFILL = "BACKFILL"


class RecEmploymentType(str, enum.Enum):
    FULL_TIME = "FULL_TIME"
    PART_TIME = "PART_TIME"
    CONTRACT = "CONTRACT"
    INTERN = "INTERN"
    CONSULTANT = "CONSULTANT"


class Priority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class RequisitionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONVERTED = "CONVERTED"   # Position created from this requisition
    ARCHIVED = "ARCHIVED"


class PositionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    ON_HOLD = "ON_HOLD"
    CLOSED = "CLOSED"
    ARCHIVED = "ARCHIVED"


class WorkMode(str, enum.Enum):
    ONSITE = "ONSITE"
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"


class CandidateStatus(str, enum.Enum):
    NEW = "NEW"
    SCREENING = "SCREENING"
    SHORTLISTED = "SHORTLISTED"
    INTERVIEW = "INTERVIEW"
    SELECTED = "SELECTED"
    OFFERED = "OFFERED"
    JOINED = "JOINED"
    REJECTED = "REJECTED"
    ON_HOLD = "ON_HOLD"
    TALENT_POOL = "TALENT_POOL"
    ARCHIVED = "ARCHIVED"


class ApplicationStage(str, enum.Enum):
    APPLIED = "APPLIED"
    SCREENING = "SCREENING"
    SHORTLISTED = "SHORTLISTED"
    INTERVIEW = "INTERVIEW"
    SELECTED = "SELECTED"
    OFFER = "OFFER"
    JOINED = "JOINED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ApplicationSource(str, enum.Enum):
    PORTAL = "PORTAL"
    REFERRAL = "REFERRAL"
    LINKEDIN = "LINKEDIN"
    NAUKRI = "NAUKRI"
    INDEED = "INDEED"
    AGENCY = "AGENCY"
    WALK_IN = "WALK_IN"
    CAMPUS = "CAMPUS"
    DIRECT = "DIRECT"
    OTHER = "OTHER"


class InterviewType(str, enum.Enum):
    HR = "HR"
    TECHNICAL = "TECHNICAL"
    MANAGERIAL = "MANAGERIAL"
    CULTURAL = "CULTURAL"
    FINAL = "FINAL"
    CLIENT = "CLIENT"


class InterviewMode(str, enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    PHONE = "PHONE"


class InterviewRound(str, enum.Enum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    FINAL = "FINAL"


class InterviewStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    RESCHEDULED = "RESCHEDULED"


class FeedbackRecommendation(str, enum.Enum):
    STRONG_HIRE = "STRONG_HIRE"
    HIRE = "HIRE"
    HOLD = "HOLD"
    NO_HIRE = "NO_HIRE"
    STRONG_NO_HIRE = "STRONG_NO_HIRE"


class OfferStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    RELEASED = "RELEASED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"


# ──────────────────────────────────────────────────────────────────────────────
# Sequences
# ──────────────────────────────────────────────────────────────────────────────

requisition_id_seq = Sequence("hr_rec_requisition_seq", start=1, increment=1)
position_id_seq = Sequence("hr_rec_position_seq", start=1, increment=1)
candidate_id_seq = Sequence("hr_rec_candidate_seq", start=1, increment=1)
application_id_seq = Sequence("hr_rec_application_seq", start=1, increment=1)
interview_id_seq = Sequence("hr_rec_interview_seq", start=1, increment=1)
offer_id_seq = Sequence("hr_rec_offer_seq", start=1, increment=1)


# ──────────────────────────────────────────────────────────────────────────────
# Job Requisition
# ──────────────────────────────────────────────────────────────────────────────

class JobRequisition(Base):
    """Internal hiring request needing approval before becoming an Open Position."""
    __tablename__ = "hr_rec_requisitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    requisition_number = Column(String(30), unique=True, nullable=False, index=True)

    job_title = Column(String(160), nullable=False)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True, index=True)
    designation_id = Column(UUID(as_uuid=True), ForeignKey("hr_designations.id"), nullable=True, index=True)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id"), nullable=True)
    location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)

    hiring_type = Column(Enum(HiringType, name="hr_rec_hiring_type"), nullable=False, default=HiringType.NEW)
    employment_type = Column(Enum(RecEmploymentType, name="hr_rec_employment_type"), nullable=False, default=RecEmploymentType.FULL_TIME)
    number_of_openings = Column(Integer, nullable=False, default=1)
    priority = Column(Enum(Priority, name="hr_rec_priority"), nullable=False, default=Priority.MEDIUM)

    hiring_manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    requested_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    budgeted_salary_min = Column(Numeric(14, 2), nullable=True)
    budgeted_salary_max = Column(Numeric(14, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="INR")

    required_skills = Column(JSON, nullable=True, default=list)  # list[str]
    experience_min_years = Column(Numeric(4, 1), nullable=True)
    experience_max_years = Column(Numeric(4, 1), nullable=True)
    qualification = Column(String(255), nullable=True)

    job_description = Column(Text, nullable=True)
    reason_for_hiring = Column(Text, nullable=True)
    expected_joining_date = Column(Date, nullable=True)

    status = Column(Enum(RequisitionStatus, name="hr_rec_requisition_status"), nullable=False, default=RequisitionStatus.DRAFT, index=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_reason = Column(Text, nullable=True)

    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    department = relationship("Department", foreign_keys=[department_id])
    designation = relationship("Designation", foreign_keys=[designation_id])
    grade = relationship("Grade", foreign_keys=[grade_id])
    location = relationship("WorkLocation", foreign_keys=[location_id])
    hiring_manager = relationship("User", foreign_keys=[hiring_manager_id])
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])

    positions = relationship("JobPosition", back_populates="requisition", cascade="save-update")


# ──────────────────────────────────────────────────────────────────────────────
# Job Position (Open Position)
# ──────────────────────────────────────────────────────────────────────────────

class JobPosition(Base):
    """Public-facing open position. Created from an approved requisition."""
    __tablename__ = "hr_rec_positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    job_code = Column(String(30), unique=True, nullable=False, index=True)

    requisition_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_requisitions.id"), nullable=True, index=True)

    job_title = Column(String(160), nullable=False)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True, index=True)
    designation_id = Column(UUID(as_uuid=True), ForeignKey("hr_designations.id"), nullable=True, index=True)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id"), nullable=True)
    location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)

    recruiter_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    hiring_manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    openings_count = Column(Integer, nullable=False, default=1)
    filled_count = Column(Integer, nullable=False, default=0)

    experience_min_years = Column(Numeric(4, 1), nullable=True)
    experience_max_years = Column(Numeric(4, 1), nullable=True)
    salary_min = Column(Numeric(14, 2), nullable=True)
    salary_max = Column(Numeric(14, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="INR")

    work_mode = Column(Enum(WorkMode, name="hr_rec_work_mode"), nullable=False, default=WorkMode.ONSITE)
    employment_type = Column(Enum(RecEmploymentType, name="hr_rec_position_emp_type"), nullable=False, default=RecEmploymentType.FULL_TIME)

    skills_required = Column(JSON, nullable=True, default=list)  # list[str]
    qualification = Column(String(255), nullable=True)
    job_description = Column(Text, nullable=True)
    perks = Column(Text, nullable=True)

    publish_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)

    status = Column(Enum(PositionStatus, name="hr_rec_position_status"), nullable=False, default=PositionStatus.DRAFT, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)

    # Manual-close audit trail (set by POST /positions/{id}/close).
    close_reason = Column(String(40), nullable=True)   # FILLED | CANCELLED | BUDGET | RESCOPED | OTHER
    close_note = Column(Text, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    requisition = relationship("JobRequisition", back_populates="positions", foreign_keys=[requisition_id])
    department = relationship("Department", foreign_keys=[department_id])
    designation = relationship("Designation", foreign_keys=[designation_id])
    grade = relationship("Grade", foreign_keys=[grade_id])
    location = relationship("WorkLocation", foreign_keys=[location_id])
    recruiter = relationship("User", foreign_keys=[recruiter_id])
    hiring_manager = relationship("User", foreign_keys=[hiring_manager_id])
    created_by = relationship("User", foreign_keys=[created_by_id])

    applications = relationship("Application", back_populates="position", cascade="save-update")


# ──────────────────────────────────────────────────────────────────────────────
# Candidate (central repository)
# ──────────────────────────────────────────────────────────────────────────────

class Candidate(Base):
    """Central candidate repository. Independent of any specific position."""
    __tablename__ = "hr_rec_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    candidate_code = Column(String(30), unique=True, nullable=False, index=True)

    # Basic
    full_name = Column(String(160), nullable=False, index=True)
    email = Column(String(255), nullable=True, index=True)
    mobile = Column(String(30), nullable=True, index=True)
    gender = Column(String(20), nullable=True)
    dob = Column(Date, nullable=True)

    # Location
    current_city = Column(String(120), nullable=True)
    current_state = Column(String(120), nullable=True)
    current_country = Column(String(120), nullable=True)
    preferred_locations = Column(JSON, nullable=True, default=list)  # list[str]

    # Online
    linkedin_url = Column(String(500), nullable=True)
    portfolio_url = Column(String(500), nullable=True)
    github_url = Column(String(500), nullable=True)

    # Professional
    current_company = Column(String(160), nullable=True)
    current_designation = Column(String(160), nullable=True)
    total_experience_years = Column(Numeric(4, 1), nullable=True)
    relevant_experience_years = Column(Numeric(4, 1), nullable=True)
    current_salary = Column(Numeric(14, 2), nullable=True)
    expected_salary = Column(Numeric(14, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="INR")
    notice_period_days = Column(Integer, nullable=True)

    # Education
    highest_qualification = Column(String(160), nullable=True)
    university = Column(String(255), nullable=True)
    passing_year = Column(Integer, nullable=True)

    # Skills & tags
    skills = Column(JSON, nullable=True, default=list)  # list[str]
    tags = Column(JSON, nullable=True, default=list)    # list[str]

    # Documents
    resume_url = Column(String(500), nullable=True)
    cover_letter_url = Column(String(500), nullable=True)
    documents = Column(JSON, nullable=True, default=list)  # list[{name, url, kind}]

    # Status & metadata
    status = Column(Enum(CandidateStatus, name="hr_rec_candidate_status"), nullable=False, default=CandidateStatus.NEW, index=True)
    source = Column(Enum(ApplicationSource, name="hr_rec_candidate_source"), nullable=False, default=ApplicationSource.DIRECT)
    notes = Column(Text, nullable=True)
    is_blacklisted = Column(Boolean, default=False, nullable=False)
    is_in_talent_pool = Column(Boolean, default=False, nullable=False)

    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    created_by = relationship("User", foreign_keys=[created_by_id])
    applications = relationship("Application", back_populates="candidate", cascade="save-update")

    __table_args__ = (
        Index("ix_hr_rec_candidates_name_email", "full_name", "email"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Application — candidate × position
# ──────────────────────────────────────────────────────────────────────────────

class Application(Base):
    """A candidate's application to a specific open position."""
    __tablename__ = "hr_rec_applications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    application_code = Column(String(30), unique=True, nullable=False, index=True)

    candidate_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_candidates.id"), nullable=False, index=True)
    position_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_positions.id"), nullable=False, index=True)

    applied_date = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    recruiter_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    source = Column(Enum(ApplicationSource, name="hr_rec_application_source"), nullable=False, default=ApplicationSource.PORTAL)

    current_stage = Column(Enum(ApplicationStage, name="hr_rec_application_stage"), nullable=False, default=ApplicationStage.APPLIED, index=True)
    stage_changed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    rating = Column(Integer, nullable=True)   # 1-5 internal rating
    score = Column(Numeric(5, 2), nullable=True)  # auto-screening match score 0-100
    notes = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    candidate = relationship("Candidate", back_populates="applications", foreign_keys=[candidate_id])
    position = relationship("JobPosition", back_populates="applications", foreign_keys=[position_id])
    recruiter = relationship("User", foreign_keys=[recruiter_id])

    interviews = relationship("Interview", back_populates="application", cascade="save-update, delete")
    offers = relationship("Offer", back_populates="application", cascade="save-update")

    __table_args__ = (
        UniqueConstraint("candidate_id", "position_id", name="uq_hr_rec_application_unique"),
        Index("ix_hr_rec_app_stage", "current_stage"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Interview Panel
# ──────────────────────────────────────────────────────────────────────────────

class InterviewPanel(Base):
    """Reusable group of interviewers for a department / skill area."""
    __tablename__ = "hr_rec_interview_panels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True)
    expertise = Column(JSON, nullable=True, default=list)  # list[str]
    description = Column(Text, nullable=True)

    # Members stored as JSON array of {user_id, name, role}
    members = Column(JSON, nullable=True, default=list)

    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    department = relationship("Department", foreign_keys=[department_id])


# ──────────────────────────────────────────────────────────────────────────────
# Interview
# ──────────────────────────────────────────────────────────────────────────────

class Interview(Base):
    """Scheduled interview for an application."""
    __tablename__ = "hr_rec_interviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    interview_code = Column(String(30), unique=True, nullable=False, index=True)

    application_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_applications.id"), nullable=False, index=True)
    panel_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_interview_panels.id"), nullable=True)

    interview_type = Column(Enum(InterviewType, name="hr_rec_interview_type"), nullable=False, default=InterviewType.HR)
    mode = Column(Enum(InterviewMode, name="hr_rec_interview_mode"), nullable=False, default=InterviewMode.ONLINE)
    round = Column(Enum(InterviewRound, name="hr_rec_interview_round"), nullable=False, default=InterviewRound.R1)

    scheduled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    duration_minutes = Column(Integer, nullable=False, default=60)
    meeting_link = Column(String(500), nullable=True)
    venue = Column(String(255), nullable=True)

    # Interviewers as list of {user_id, name, email}
    interviewers = Column(JSON, nullable=True, default=list)

    status = Column(Enum(InterviewStatus, name="hr_rec_interview_status"), nullable=False, default=InterviewStatus.SCHEDULED, index=True)

    notes = Column(Text, nullable=True)

    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    application = relationship("Application", back_populates="interviews", foreign_keys=[application_id])
    panel = relationship("InterviewPanel", foreign_keys=[panel_id])
    created_by = relationship("User", foreign_keys=[created_by_id])

    feedback_entries = relationship("InterviewFeedback", back_populates="interview", cascade="all, delete-orphan")


# ──────────────────────────────────────────────────────────────────────────────
# Interview Feedback
# ──────────────────────────────────────────────────────────────────────────────

class InterviewFeedback(Base):
    """One feedback entry per interviewer per interview."""
    __tablename__ = "hr_rec_interview_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    interview_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_interviews.id", ondelete="CASCADE"), nullable=False, index=True)
    interviewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    technical_rating = Column(Integer, nullable=True)   # 1-5
    communication_rating = Column(Integer, nullable=True)
    cultural_fit_rating = Column(Integer, nullable=True)
    overall_rating = Column(Integer, nullable=True)

    recommendation = Column(Enum(FeedbackRecommendation, name="hr_rec_feedback_rec"), nullable=False, default=FeedbackRecommendation.HOLD)
    strengths = Column(Text, nullable=True)
    weaknesses = Column(Text, nullable=True)
    detailed_feedback = Column(Text, nullable=True)

    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    interview = relationship("Interview", back_populates="feedback_entries")
    interviewer = relationship("User", foreign_keys=[interviewer_id])

    __table_args__ = (
        UniqueConstraint("interview_id", "interviewer_id", name="uq_hr_rec_feedback_unique"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Offer
# ──────────────────────────────────────────────────────────────────────────────

class Offer(Base):
    """Offer letter for a candidate's application."""
    __tablename__ = "hr_rec_offers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    offer_code = Column(String(30), unique=True, nullable=False, index=True)

    application_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_applications.id"), nullable=False, index=True)
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_candidates.id"), nullable=False, index=True)
    position_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_positions.id"), nullable=False)

    designation = Column(String(160), nullable=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id"), nullable=True)
    location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)

    offered_salary = Column(Numeric(14, 2), nullable=False)
    bonus = Column(Numeric(14, 2), nullable=True, default=0)
    currency = Column(String(10), nullable=False, default="INR")
    joining_date = Column(Date, nullable=True)
    reporting_manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    offer_valid_till = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    offer_letter_url = Column(String(500), nullable=True)

    status = Column(Enum(OfferStatus, name="hr_rec_offer_status"), nullable=False, default=OfferStatus.DRAFT, index=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    candidate_response_at = Column(DateTime(timezone=True), nullable=True)
    candidate_response_note = Column(Text, nullable=True)

    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Back-link to the employee that was onboarded from this offer.
    # Set when POST /api/hr/employees/ is called with offer_id; nullable until then.
    employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hr_employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    application = relationship("Application", back_populates="offers", foreign_keys=[application_id])
    candidate = relationship("Candidate", foreign_keys=[candidate_id])
    position = relationship("JobPosition", foreign_keys=[position_id])
    department = relationship("Department", foreign_keys=[department_id])
    grade = relationship("Grade", foreign_keys=[grade_id])
    location = relationship("WorkLocation", foreign_keys=[location_id])
    reporting_manager = relationship("User", foreign_keys=[reporting_manager_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    employee = relationship("Employee", foreign_keys=[employee_id])
