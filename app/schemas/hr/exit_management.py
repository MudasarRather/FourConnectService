"""HR Exit Management — Pydantic v2 schemas.

Request/response shapes for the Exit Management module. Response models set
``from_attributes=True`` so ORM rows serialise directly. The exit-case workflow
DRIVES the existing employee lifecycle — these schemas never re-declare lifecycle
state; ``StartNoticeBody`` / ``FinalizeExitBody`` mirror ``LifecycleGiveNoticeBody``
/ ``LifecycleExitBody`` so the bridge can construct those bodies 1-1.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Literal, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.hr.exit_type import (
    ResignationType, ExitReasonCategory, ExitCaseStatus,
    ClearanceDepartment, ClearanceItemStatus, SettlementStatus,
    InterviewStatus, ExitDocStatus,
)

SettlementMethod = Literal["PAYROLL", "BANK_TRANSFER", "CASH"]


# ═════════════════════════════════════════════════════════════════════════════
# Exit Policy
# ═════════════════════════════════════════════════════════════════════════════

class ApprovalLevel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    level: int = Field(..., ge=1)
    role: Literal["MANAGER", "HR", "FINANCE", "DEPT_HEAD"]
    label: str = Field(..., min_length=1, max_length=80)


class ClearanceTemplateItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    department: ClearanceDepartment
    item_key: str = Field(..., min_length=1, max_length=60)
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    is_mandatory: bool = True
    sort_order: int = 0


class InterviewQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = Field(..., min_length=1, max_length=60)
    question: str = Field(..., min_length=1, max_length=400)
    type: Literal["rating", "text", "choice"] = "rating"


class ExitPolicyBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policy_name: str = Field(..., min_length=1, max_length=160)
    description: Optional[str] = None
    grade_id: Optional[UUID] = None
    employee_category: Optional[str] = None
    notice_period_days: int = Field(30, ge=0, le=365)
    probation_notice_days: int = Field(7, ge=0, le=180)
    buyout_allowed: bool = True
    buyout_basis: Literal["BASIC", "GROSS"] = "BASIC"
    approval_levels: List[ApprovalLevel] = []
    clearance_template: List[ClearanceTemplateItem] = []
    interview_questions: List[InterviewQuestion] = []
    gratuity_enabled: bool = True
    gratuity_min_years: Decimal = Decimal("5")
    is_active: bool = True


class ExitPolicyCreate(ExitPolicyBase):
    pass


class ExitPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policy_name: Optional[str] = Field(None, min_length=1, max_length=160)
    description: Optional[str] = None
    grade_id: Optional[UUID] = None
    employee_category: Optional[str] = None
    notice_period_days: Optional[int] = Field(None, ge=0, le=365)
    probation_notice_days: Optional[int] = Field(None, ge=0, le=180)
    buyout_allowed: Optional[bool] = None
    buyout_basis: Optional[Literal["BASIC", "GROSS"]] = None
    approval_levels: Optional[List[ApprovalLevel]] = None
    clearance_template: Optional[List[ClearanceTemplateItem]] = None
    interview_questions: Optional[List[InterviewQuestion]] = None
    gratuity_enabled: Optional[bool] = None
    gratuity_min_years: Optional[Decimal] = None
    is_active: Optional[bool] = None


class ExitPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    policy_name: str
    description: Optional[str] = None
    grade_id: Optional[UUID] = None
    grade_name: Optional[str] = None
    employee_category: Optional[str] = None
    notice_period_days: int
    probation_notice_days: int
    buyout_allowed: bool
    buyout_basis: str
    approval_levels: Any = []
    clearance_template: Any = []
    interview_questions: Any = []
    gratuity_enabled: bool
    gratuity_min_years: Decimal
    is_active: bool
    created_at: Optional[datetime] = None


class ExitPolicyListResponse(BaseModel):
    items: List[ExitPolicyResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═════════════════════════════════════════════════════════════════════════════
# Exit Case
# ═════════════════════════════════════════════════════════════════════════════

class ExitCaseCreate(BaseModel):
    """HR-initiated separation."""
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    resignation_type: ResignationType
    reason_category: Optional[ExitReasonCategory] = None
    reason_detail: Optional[str] = None
    resignation_date: Optional[date] = None
    requested_last_working_date: Optional[date] = None
    policy_id: Optional[UUID] = None   # exit policy to apply (defaults to the grade match)


class MyResignationCreate(BaseModel):
    """Employee self-service resignation. Cannot pick TERMINATION / MUTUAL."""
    model_config = ConfigDict(extra="ignore")
    resignation_type: ResignationType = ResignationType.VOLUNTARY
    reason_category: Optional[ExitReasonCategory] = None
    reason_detail: Optional[str] = Field(None, max_length=4000)
    requested_last_working_date: Optional[date] = None
    personal_email: Optional[str] = Field(None, max_length=255)   # where final documents are sent

    @field_validator("resignation_type")
    @classmethod
    def _block_admin_only(cls, v):
        if v in (ResignationType.TERMINATION, ResignationType.MUTUAL_SEPARATION):
            raise ValueError("This separation type can only be initiated by HR.")
        return v


class ExitCaseUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    resignation_type: Optional[ResignationType] = None
    reason_category: Optional[ExitReasonCategory] = None
    reason_detail: Optional[str] = None
    resignation_date: Optional[date] = None
    requested_last_working_date: Optional[date] = None
    personal_email: Optional[str] = Field(None, max_length=255)
    policy_id: Optional[UUID] = None


class ExitSubmitBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason_detail: Optional[str] = None


class ManagerDecisionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: Literal["APPROVED", "REJECTED"]
    notes: Optional[str] = None


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    notice_period_days: Optional[int] = Field(None, ge=0, le=365)
    last_working_date: Optional[date] = None
    notice_waived: Optional[bool] = None
    eligible_for_rehire: Optional[bool] = None


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=1, max_length=2000)


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=1, max_length=2000)


class WithdrawBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(None, max_length=2000)


class DeleteCaseBody(BaseModel):
    """Reason captured when an HR admin expunges a pre-accept / closed case."""
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(None, max_length=2000)


class StartNoticeBody(BaseModel):
    """Mirrors LifecycleGiveNoticeBody."""
    model_config = ConfigDict(extra="ignore")
    notice_period_start_date: date
    last_working_date: date

    @model_validator(mode="after")
    def _lwd_after_start(self):
        if self.last_working_date < self.notice_period_start_date:
            raise ValueError("last_working_date must be >= notice_period_start_date")
        return self


class WaiveNoticeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=1, max_length=2000)
    buyout_days: Optional[int] = Field(None, ge=0, le=365)


class NoticeAdjustBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    last_working_date: Optional[date] = None
    notice_period_days: Optional[int] = Field(None, ge=0, le=365)
    reason: Optional[str] = None


class FinalizeExitBody(BaseModel):
    """Mirrors LifecycleExitBody."""
    model_config = ConfigDict(extra="ignore")
    exit_date: date
    eligible_for_rehire: Optional[bool] = None


# ── Clearance ──

class ClearanceItemUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[ClearanceItemStatus] = None
    remarks: Optional[str] = None
    recovery_amount: Optional[Decimal] = None
    assignee_user_id: Optional[UUID] = None


class ClearanceReopenBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=1, max_length=1000)


# ── HR-gate "apply" bodies — the tasks marked in the sign-off modal that actually
# write to the relevant records (employee record / F&F settlement), mirroring the
# revoke-erp / revoke-provisioning "the action DOES the work" pattern. ──

class HrRecordsApplyBody(BaseModel):
    """`hr_records` gate — finalise the employee's exit records. Each true task
    performs (or audits) the real update; ``lifecycle_exited`` runs the real
    lifecycle EXIT (Employee History + asset off-boarding)."""
    model_config = ConfigDict(extra="ignore")
    hris_status: bool = False
    documents_archived: bool = False
    statutory_updated: bool = False
    lifecycle_exited: bool = False
    remarks: Optional[str] = Field(None, max_length=2000)
    assignee_user_id: Optional[UUID] = None


class FfAckApplyBody(BaseModel):
    """`hr_ff_ack` gate — record the Full & Final acknowledgement on the
    authoritative settlement record."""
    model_config = ConfigDict(extra="ignore")
    statement_shared: bool = False
    employee_acknowledged: bool = False
    payout_confirmed: bool = False
    remarks: Optional[str] = Field(None, max_length=2000)
    assignee_user_id: Optional[UUID] = None


class FinLoansApplyBody(BaseModel):
    """`fin_loan_advance` gate — verify the employee's outstanding loans /
    advances against live data and schedule the recovery into the F&F. Travel
    advances auto-recover (engine); ``loan_recovery_amount`` captures any other
    loan / salary advance finance is closing (→ settlement.loan_recovery)."""
    model_config = ConfigDict(extra="ignore")
    loan_balance_computed: bool = False
    advance_balance_computed: bool = False
    recovery_scheduled: bool = False
    employee_acknowledged: bool = False
    loan_recovery_amount: Optional[Decimal] = Field(None, ge=0)
    remarks: Optional[str] = Field(None, max_length=2000)
    assignee_user_id: Optional[UUID] = None


class ClearanceApplyEffect(BaseModel):
    """One real record-write performed by an apply endpoint, surfaced to the UI so
    it can inform the user exactly what changed."""
    key: str
    label: str
    done: bool
    detail: Optional[str] = None
    target: Optional[str] = None          # which record was touched
    severity: Optional[str] = None        # 'info' | 'success' | 'major'


class ClearanceApplyResponse(BaseModel):
    item: ClearanceItemResponse
    effects: List[ClearanceApplyEffect] = []


class HandoverAttachment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=1000)


class HandoverSubmitBody(BaseModel):
    """Employee-submitted handover for a MANAGER / PROJECT clearance lane."""
    model_config = ConfigDict(extra="ignore")
    notes: Optional[str] = Field(None, max_length=4000)
    successor_name: Optional[str] = Field(None, max_length=200)
    # checklist keyed by playbook step index ("0".."n") → done?
    checklist: Optional[Dict[str, bool]] = None
    attachments: Optional[List[HandoverAttachment]] = None


class ClearanceSignoffBody(BaseModel):
    """Reporting-manager decision on a handover lane."""
    model_config = ConfigDict(extra="ignore")
    decision: Literal["CLEARED", "BLOCKED"]
    note: Optional[str] = Field(None, max_length=2000)


class ClearanceItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    department: ClearanceDepartment
    item_key: str
    title: str
    description: Optional[str] = None
    is_mandatory: bool
    status: ClearanceItemStatus
    assignee_user_id: Optional[UUID] = None
    assignee_name: Optional[str] = None
    remarks: Optional[str] = None
    recovery_amount: Optional[Decimal] = None
    signed_off_by_id: Optional[UUID] = None
    signed_off_by_name: Optional[str] = None
    signed_off_at: Optional[datetime] = None
    sort_order: int
    submission: Optional[Dict[str, Any]] = None
    is_self_handover: bool = False


# ── Interview ──

class InterviewScheduleBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scheduled_at: Optional[datetime] = None
    mode: Optional[Literal["IN_PERSON", "VIDEO", "FORM"]] = "FORM"
    conducted_by_id: Optional[UUID] = None
    details: Optional[str] = Field(None, max_length=2000)   # link / room / agenda for the employee


class InterviewSubmitBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    responses: List[Dict[str, Any]] = []          # [{question, answer, rating}]
    ratings: Dict[str, Any] = {}                  # {management, culture, ...}
    would_recommend: Optional[bool] = None
    primary_reason_category: Optional[ExitReasonCategory] = None
    feedback_summary: Optional[str] = None
    mode: Optional[Literal["IN_PERSON", "VIDEO", "FORM"]] = None


class ExitInterviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: InterviewStatus
    scheduled_at: Optional[datetime] = None
    conducted_at: Optional[datetime] = None
    conducted_by_id: Optional[UUID] = None
    conducted_by_name: Optional[str] = None   # resolved interviewer name (injected)
    mode: Optional[str] = None
    details: Optional[str] = None             # appointment instructions / link / agenda
    responses: Any = []
    ratings: Any = {}
    would_recommend: Optional[bool] = None
    primary_reason_category: Optional[ExitReasonCategory] = None
    feedback_summary: Optional[str] = None
    is_confidential: bool = True
    questions: Any = []   # resolved policy interview_questions for the form


# ── Settlement ──

class SettlementRecalcBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    overrides: Optional[Dict[str, Any]] = None   # pin any line: {"gratuity_amount": 50000, ...}
    reason: Optional[str] = Field(None, max_length=2000)   # audit note when lines are pinned


class SettlementUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pending_salary: Optional[Decimal] = None
    leave_encashment_amount: Optional[Decimal] = None
    incentives_amount: Optional[Decimal] = None
    bonus_amount: Optional[Decimal] = None
    reimbursements_amount: Optional[Decimal] = None
    gratuity_amount: Optional[Decimal] = None
    other_earnings: Optional[Decimal] = None
    notice_recovery: Optional[Decimal] = None
    loan_recovery: Optional[Decimal] = None
    advance_recovery: Optional[Decimal] = None
    asset_recovery: Optional[Decimal] = None
    other_deductions: Optional[Decimal] = None


class SettlementVerifyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    notes: Optional[str] = None


class SettlementApproveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    notes: Optional[str] = None


class SettlementPayBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    settlement_method: SettlementMethod = "PAYROLL"
    period_month: Optional[int] = Field(None, ge=1, le=12)
    period_year: Optional[int] = Field(None, ge=2000, le=2100)
    note: Optional[str] = None


class SettlementReverseBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=1, max_length=2000)


class SettlementCloseBody(BaseModel):
    """Optional closure record captured when a PAID F&F is closed (archived).

    Nothing here is required — the close is still a clean PAID → CLOSED
    transition — but the closure category + remarks are written to the exit
    audit trail so the final account carries a reasoned sign-off."""
    model_config = ConfigDict(extra="ignore")
    category: Optional[str] = Field(None, max_length=60)   # e.g. SETTLED_IN_FULL
    notes: Optional[str] = Field(None, max_length=2000)


class ArchiveCaseBody(BaseModel):
    """Optional archival record captured when a COMPLETED case is consigned to
    the permanent archive. Nothing here is required — archive remains a clean
    COMPLETED → (employee ARCHIVED) transition — but the category + remarks are
    folded into the employee-history reason and the exit audit trail so the
    archival carries a reasoned sign-off."""
    model_config = ConfigDict(extra="ignore")
    category: Optional[str] = Field(None, max_length=60)   # e.g. RETENTION_COMPLIANCE
    notes: Optional[str] = Field(None, max_length=2000)


class ExitSettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    settlement_number: str
    status: SettlementStatus
    pending_salary: Decimal
    leave_encashment_amount: Decimal
    leave_encashment_days: Decimal
    incentives_amount: Decimal
    bonus_amount: Decimal
    reimbursements_amount: Decimal
    gratuity_amount: Decimal
    other_earnings: Decimal
    total_earnings: Decimal
    notice_recovery: Decimal
    loan_recovery: Decimal
    advance_recovery: Decimal
    asset_recovery: Decimal
    other_deductions: Decimal
    total_recoveries: Decimal
    net_amount: Decimal
    currency: str
    computation_snapshot: Any = {}
    settlement_method: Optional[str] = None
    verified_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    payroll_ref: Optional[str] = None
    period_month: Optional[int] = None
    period_year: Optional[int] = None
    # Full & Final acknowledgement (recorded from the HR clearance gate)
    ff_statement_shared_at: Optional[datetime] = None
    ff_acknowledged_at: Optional[datetime] = None
    ff_acknowledged_by_id: Optional[UUID] = None
    payout_confirmed_at: Optional[datetime] = None
    ff_ack_snapshot: Optional[Dict[str, Any]] = None


# ── Document / letters ──

class LetterGenerateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    template_id: Optional[UUID] = None


class LetterRevokeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=1, max_length=2000)


class ExitDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    doc_type: str
    status: ExitDocStatus
    drive_document_id: Optional[UUID] = None
    employee_document_id: Optional[UUID] = None
    verification_code: Optional[str] = None
    issued_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class LetterVerifyResponse(BaseModel):
    valid: bool
    employee_name: Optional[str] = None
    doc_type: Optional[str] = None
    issued_at: Optional[datetime] = None
    revoked: bool = False
    message: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════════
# Exit Case responses
# ═════════════════════════════════════════════════════════════════════════════

class ExitCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    case_number: str
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    designation_name: Optional[str] = None
    resignation_type: ResignationType
    reason_category: Optional[ExitReasonCategory] = None
    reason_detail: Optional[str] = None
    status: ExitCaseStatus
    initiated_by: str
    resignation_date: Optional[date] = None
    requested_last_working_date: Optional[date] = None
    notice_period_days: Optional[int] = None
    notice_period_start_date: Optional[date] = None
    last_working_date: Optional[date] = None
    exit_date: Optional[date] = None
    joining_date_snapshot: Optional[date] = None   # service start — drives the experience-letter tenure
    notice_waived: bool = False
    notice_buyout_days: Optional[int] = None
    manager_id: Optional[UUID] = None
    manager_name: Optional[str] = None
    manager_decision: Optional[str] = None
    eligible_for_rehire: Optional[bool] = None
    clearance_progress_pct: int = 0
    settlement_net_amount: Optional[Decimal] = None
    lifecycle_state: Optional[str] = None
    public_token: Optional[str] = None       # former-employee document portal link
    public_token_expires_at: Optional[datetime] = None
    personal_email: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ExitCaseDetailResponse(ExitCaseResponse):
    lifecycle_consistent: bool = True
    clearance_items: List[ClearanceItemResponse] = []
    interview: Optional[ExitInterviewResponse] = None
    settlement: Optional[ExitSettlementResponse] = None
    documents: List[ExitDocumentResponse] = []
    policy: Optional[ExitPolicyResponse] = None
    rejection_reason: Optional[str] = None
    cancel_reason: Optional[str] = None
    manager_notes: Optional[str] = None


class ExitCaseListResponse(BaseModel):
    items: List[ExitCaseResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ── Audit ──

class ExitAuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    entity_type: str
    entity_id: Optional[UUID] = None
    action: str
    exit_case_id: Optional[UUID] = None
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    payload: Any = None
    note: Optional[str] = None
    created_at: Optional[datetime] = None


class ExitAuditLogListResponse(BaseModel):
    items: List[ExitAuditLogResponse]
    total: int
    page: int
    limit: int
    total_pages: int
