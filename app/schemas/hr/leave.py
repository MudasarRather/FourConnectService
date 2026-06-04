"""HR Leave & Absence — Pydantic schemas.

Mirrors the WfhRequest / HalfDayRequest schema style. All request bodies use
strict validation; responses embed denormalised display fields (employee_name,
manager_name, day_breakdown) so the frontend never has to re-query for joins.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.hr.leave_type import (
    LeaveType, LeaveStatus, LeaveSession, LeaveDecision, LedgerKind, EncashmentStatus,
)


# ═════════════════════════════════════════════════════════════════════════════
# Approval chain (Phase 4)
# ═════════════════════════════════════════════════════════════════════════════

# Approver-type taxonomy:
#   MANAGER → resolved to employee.reporting_manager_id at submit time
#   HR      → any active superuser; gate is is_superuser==True
#   USER    → a specific named approver; approver_user_id is required
ApproverType = Literal["MANAGER", "HR", "USER"]


class ApprovalStageConfig(BaseModel):
    """One stage in a leave policy's configured approval chain."""
    model_config = ConfigDict(extra="ignore")
    approver_type: ApproverType
    approver_user_id: Optional[UUID] = None
    label: str = Field(..., min_length=1, max_length=60)

    @field_validator("approver_user_id")
    @classmethod
    def _require_user_for_named(cls, v, info):
        # USER stages must name a user; MANAGER/HR stages must not.
        t = info.data.get("approver_type")
        if t == "USER" and not v:
            raise ValueError("USER approval stage requires approver_user_id")
        return v


class ApprovalStageState(ApprovalStageConfig):
    """One stage as snapshotted onto a LeaveRequest — config + per-stage state."""
    step: int = Field(..., ge=0)
    decision: Optional[LeaveDecision] = None
    decided_by_id: Optional[UUID] = None
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    notes: Optional[str] = None
    # Convenience — router resolves approver_user_id → display name for the
    # named-user case so the frontend never has to render a raw UUID.
    approver_name: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════════
# Policy
# ═════════════════════════════════════════════════════════════════════════════

class LeavePolicyResponse(BaseModel):
    id: UUID
    leave_type: LeaveType
    annual_quota: Decimal
    monthly_accrual: Decimal
    max_carry_forward: Decimal
    encashment_allowed: bool
    requires_attachment: bool
    count_holidays_weekoffs: bool
    max_consecutive_days: Optional[int] = None
    requires_notice_days: int = 0
    advance_book_days: Optional[int] = None
    label: Optional[str] = None
    description: Optional[str] = None
    color_hex: Optional[str] = None
    # Phase 4 — null means "use the default two-tier [MANAGER, HR] chain"
    approval_chain: Optional[List[ApprovalStageConfig]] = None
    is_active: bool = True
    model_config = ConfigDict(from_attributes=True)


class LeavePolicyListResponse(BaseModel):
    items: List[LeavePolicyResponse]
    total: int
    # Leave types that currently have NO active (non-deleted) policy — i.e. the
    # set the create-policy wizard is allowed to configure. Empty when every
    # type in the taxonomy already has a live policy.
    creatable_types: List[LeaveType] = Field(default_factory=list)


class LeavePolicyCreate(BaseModel):
    """POST body to configure a policy for a leave type that has no active one.

    Carries the same option surface as ``LeavePolicyUpdate`` (so the create and
    edit wizards stay in lock-step) plus the required ``leave_type`` selector.
    Defaults mirror the model defaults so a minimal body is valid.
    """
    model_config = ConfigDict(extra="ignore")
    leave_type: LeaveType
    annual_quota: Decimal = Field(Decimal("0"), ge=0, le=365)
    monthly_accrual: Decimal = Field(Decimal("0"), ge=0, le=31)
    max_carry_forward: Decimal = Field(Decimal("0"), ge=0, le=365)
    encashment_allowed: bool = False
    requires_attachment: bool = False
    count_holidays_weekoffs: bool = True
    max_consecutive_days: Optional[int] = Field(None, ge=0, le=365)
    requires_notice_days: int = Field(0, ge=0, le=90)
    advance_book_days: Optional[int] = Field(None, ge=0, le=365)
    label: Optional[str] = Field(None, max_length=60)
    description: Optional[str] = Field(None, max_length=400)
    color_hex: Optional[str] = Field(None, max_length=9)
    # null → use the default two-tier [MANAGER, HR] chain; otherwise 1..8 stages
    approval_chain: Optional[List[ApprovalStageConfig]] = None
    is_active: bool = True

    @field_validator("approval_chain")
    @classmethod
    def _chain_size(cls, v):
        if v is None:
            return v
        if len(v) > 8:
            raise ValueError("approval_chain cannot exceed 8 stages")
        return v


class LeavePolicyUsage(BaseModel):
    """Preflight impact report for a policy — drives the delete modal so HR can
    see exactly how many people are affected before removing a leave type."""
    leave_type: LeaveType
    exists: bool = True
    is_active: bool = True
    # Balance rows materialised for this type (one per employee × fiscal year)
    balance_count: int = 0
    employee_count: int = 0          # distinct employees holding a balance
    nonzero_balance_count: int = 0   # employees with a live balance > 0
    # Request volume
    total_requests: int = 0
    active_requests: int = 0         # PENDING_MANAGER / PENDING_HR / APPROVED
    upcoming_approved: int = 0       # APPROVED with to_date >= today
    in_use: bool = False             # any balances or requests at all


class LeavePolicyDeleteBody(BaseModel):
    """DELETE body — a reason is mandatory so the soft-delete is auditable."""
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=6, max_length=2000)
    reason_category: Optional[str] = Field(None, max_length=64)
    # Set True by the frontend once the admin has acknowledged the in-use impact
    # banner. Lets the backend hard-require acknowledgement for live policies.
    acknowledge_impact: bool = False


class LeavePolicyUpdate(BaseModel):
    """PATCH body. Every field optional — only the supplied fields are mutated."""
    model_config = ConfigDict(extra="ignore")
    annual_quota: Optional[Decimal] = Field(None, ge=0, le=365)
    monthly_accrual: Optional[Decimal] = Field(None, ge=0, le=31)
    max_carry_forward: Optional[Decimal] = Field(None, ge=0, le=365)
    encashment_allowed: Optional[bool] = None
    requires_attachment: Optional[bool] = None
    count_holidays_weekoffs: Optional[bool] = None
    max_consecutive_days: Optional[int] = Field(None, ge=0, le=365)
    requires_notice_days: Optional[int] = Field(None, ge=0, le=90)
    advance_book_days: Optional[int] = Field(None, ge=0, le=365)
    label: Optional[str] = Field(None, max_length=60)
    description: Optional[str] = Field(None, max_length=400)
    color_hex: Optional[str] = Field(None, max_length=9)
    # Phase 4 — pass [] to clear back to default; omit to leave unchanged.
    approval_chain: Optional[List[ApprovalStageConfig]] = None
    is_active: Optional[bool] = None

    @field_validator("approval_chain")
    @classmethod
    def _chain_size(cls, v):
        if v is None:
            return v
        if len(v) > 8:
            raise ValueError("approval_chain cannot exceed 8 stages")
        return v


# ═════════════════════════════════════════════════════════════════════════════
# Request — Create / Decide
# ═════════════════════════════════════════════════════════════════════════════

class LeaveRequestCreate(BaseModel):
    """Employee-initiated leave request."""
    model_config = ConfigDict(extra="ignore")
    leave_type: LeaveType
    from_date: date
    to_date: date
    is_half_day: bool = False
    which_session: Optional[LeaveSession] = None
    reason: str = Field(..., min_length=8, max_length=2000)
    attachment_id: Optional[UUID] = None
    contact_during_leave: Optional[str] = Field(None, max_length=120)
    emergency_contact: Optional[str] = Field(None, max_length=120)

    @field_validator("to_date")
    @classmethod
    def _to_after_from(cls, v: date, info):
        from_d = info.data.get("from_date")
        if from_d and v < from_d:
            raise ValueError("to_date cannot be before from_date")
        return v


class LeaveRequestAdminCreate(LeaveRequestCreate):
    """Admin manual entry — bypasses both pending stages, lands APPROVED."""
    employee_id: UUID
    admin_note: Optional[str] = Field(None, max_length=400)


class LeaveDecisionBody(BaseModel):
    """Manager / HR per-stage decision payload."""
    model_config = ConfigDict(extra="ignore")
    decision: LeaveDecision   # APPROVED | REJECTED
    notes: Optional[str] = Field(None, max_length=1000)


class LeaveBulkDecideBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: List[UUID] = Field(..., min_length=1, max_length=200)
    decision: LeaveDecision
    notes: Optional[str] = Field(None, max_length=1000)


class LeaveWithdrawBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: Optional[str] = Field(None, max_length=400)


class LeaveDeleteBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(None, max_length=2000)
    reason_category: Optional[str] = Field(None, max_length=64)


# ═════════════════════════════════════════════════════════════════════════════
# Proof request (HR asks employee for supporting documents)
# ═════════════════════════════════════════════════════════════════════════════

class LeaveProofRequestBody(BaseModel):
    """HR-side body for POST /leaves/{id}/request-proof — optional note that
    will be shown to the employee in the self-service drawer."""
    model_config = ConfigDict(extra="ignore")
    note: Optional[str] = Field(default=None, max_length=2000)


class ProofDeleteBody(BaseModel):
    """Optional body for DELETE /leaves/me/{leave_id}/proof/{attachment_id}.
    Captures a short reason + freeform note that get persisted to the audit log."""
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(default=None, max_length=120)
    note: Optional[str] = Field(default=None, max_length=2000)


class LeaveProofAttachmentResponse(BaseModel):
    """One uploaded proof file, surfaced both on the LeaveRequestResponse
    embedded list and on the dedicated proofs listing endpoint."""
    id: UUID
    file_url: str
    original_filename: str
    file_size: int
    mime_type: Optional[str] = None
    uploaded_by_id: UUID
    uploaded_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ═════════════════════════════════════════════════════════════════════════════
# Day breakdown — per-date paid/holiday/week-off classification
# ═════════════════════════════════════════════════════════════════════════════

class LeaveDayBreakdown(BaseModel):
    on_date: date
    is_holiday: bool = False
    is_week_off: bool = False
    is_paid: bool = True
    is_half_day: bool = False
    which_session: Optional[LeaveSession] = None
    holiday_name: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════════
# Request — Response
# ═════════════════════════════════════════════════════════════════════════════

class LeaveRequestResponse(BaseModel):
    id: UUID
    reference_no: str
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    designation_name: Optional[str] = None

    leave_type: LeaveType
    from_date: date
    to_date: date
    total_days: Decimal
    is_half_day: bool
    which_session: Optional[LeaveSession] = None
    fy_breakdown: Dict[str, float] = Field(default_factory=dict)

    reason: str
    attachment_id: Optional[UUID] = None
    proof_requested: bool = False
    proof_requested_at: Optional[datetime] = None
    proof_request_note: Optional[str] = None
    proof_submitted_at: Optional[datetime] = None
    proof_attachments: List[LeaveProofAttachmentResponse] = Field(default_factory=list)
    proof_attachment_count: int = 0
    contact_during_leave: Optional[str] = None
    emergency_contact: Optional[str] = None

    status: LeaveStatus

    manager_id: Optional[UUID] = None
    manager_name: Optional[str] = None
    manager_decision: Optional[LeaveDecision] = None
    manager_decided_at: Optional[datetime] = None
    manager_notes: Optional[str] = None

    hr_id: Optional[UUID] = None
    hr_name: Optional[str] = None
    hr_decision: Optional[LeaveDecision] = None
    hr_decided_at: Optional[datetime] = None
    hr_notes: Optional[str] = None

    cancelled_at: Optional[datetime] = None
    cancelled_reason: Optional[str] = None
    is_admin_override: bool = False

    # Phase 4 — N-stage chain snapshot. For legacy in-flight rows created
    # before Phase 4 this may be empty; the frontend should fall back to
    # the manager_*/hr_* fields above in that case. current_step indexes
    # into approval_steps and equals len(approval_steps) when fully approved.
    approval_steps: List[ApprovalStageState] = Field(default_factory=list)
    current_step: int = 0

    # Optional — populated by the /{id} detail endpoint; not by list endpoints
    day_breakdown: Optional[List[LeaveDayBreakdown]] = None

    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LeaveRequestListResponse(BaseModel):
    items: List[LeaveRequestResponse]
    total: int
    page: int
    limit: int
    total_pages: int
    # Self-service: true when the caller has no linked Employee profile.
    # Lets the frontend show a calm "ask HR" banner instead of a 404 toast.
    # Always false on admin / manager queues.
    unlinked: bool = False


# ═════════════════════════════════════════════════════════════════════════════
# Dashboard stats
# ═════════════════════════════════════════════════════════════════════════════

class LeaveTypeCount(BaseModel):
    leave_type: LeaveType
    count: int
    days: Decimal


class LeaveStats(BaseModel):
    """Lightweight tile-row counters for the admin dashboard hero."""
    pending_manager: int = 0
    pending_hr: int = 0
    approved_today: int = 0
    approved_this_month: int = 0
    rejected_this_month: int = 0
    on_leave_today: int = 0
    upcoming_30d: int = 0
    by_type_ytd: List[LeaveTypeCount] = Field(default_factory=list)


# ═════════════════════════════════════════════════════════════════════════════
# Balance
# ═════════════════════════════════════════════════════════════════════════════

class LeaveBalanceResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    leave_type: LeaveType
    fiscal_year: str
    opening_balance: Decimal
    accrued: Decimal
    carry_forward_in: Decimal
    used: Decimal
    encashed: Decimal
    adjustments: Decimal
    closing_balance: Decimal
    # Convenience computed fields (router fills them)
    available: Decimal = Decimal("0")
    quota: Decimal = Decimal("0")
    utilisation_pct: float = 0.0
    model_config = ConfigDict(from_attributes=True)


class LeaveBalanceListResponse(BaseModel):
    items: List[LeaveBalanceResponse]
    total: int
    fiscal_year: str
    # Same semantics as LeaveRequestListResponse.unlinked.
    unlinked: bool = False


class LeaveBalanceAdjustBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    leave_type: LeaveType
    fiscal_year: Optional[str] = None     # default → current FY
    delta: Decimal                         # signed
    reason: str = Field(..., min_length=4, max_length=400)


# ═════════════════════════════════════════════════════════════════════════════
# Ledger history
# ═════════════════════════════════════════════════════════════════════════════

class LeaveHistoryResponse(BaseModel):
    id: UUID
    employee_id: UUID
    leave_type: LeaveType
    fiscal_year: str
    kind: LedgerKind
    delta: Decimal
    balance_before: Decimal
    balance_after: Decimal
    note: Optional[str] = None
    actor_user_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    related_request_id: Optional[UUID] = None
    is_auto_generated: bool = False
    earned_on: Optional[date] = None
    expires_on: Optional[date] = None
    related_encashment_id: Optional[UUID] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LeaveHistoryListResponse(BaseModel):
    items: List[LeaveHistoryResponse]
    total: int


# ═════════════════════════════════════════════════════════════════════════════
# Comp-Off (Phase 2)
# ═════════════════════════════════════════════════════════════════════════════

class CompOffGrantBody(BaseModel):
    """Admin manual comp-off grant."""
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    earned_on: date
    days: Decimal = Field(..., gt=0, le=2)
    reason: str = Field(..., min_length=4, max_length=400)
    # Override the default expiry — admin can extend/shorten the window
    expires_on: Optional[date] = None


class EncashmentOption(BaseModel):
    """Employee-scoped encashable leave type + the caller's available balance.
    Drives the self-service encashment modal (which can't read admin policies)."""
    leave_type: LeaveType
    label: Optional[str] = None
    available_balance: Decimal = Decimal("0")


class CompOffImpact(BaseModel):
    """Preflight for deleting a comp-off grant — drives the delete modal so HR
    sees what reversing the credit does to the employee's balance."""
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    days: Decimal
    earned_on: Optional[date] = None
    expires_on: Optional[date] = None
    is_auto_generated: bool = False
    is_expired: bool = False
    fiscal_year: str
    # Balance reversal preview
    balance_active: Decimal = Decimal("0")     # current usable comp-off reserve
    balance_after: Decimal = Decimal("0")      # closing balance after removing this credit
    would_go_negative: bool = False            # credit was (partly) already spent
    # When the credit is auto-generated it likely came from a real worked day —
    # deleting it diverges from attendance truth; surfaced as a soft warning.
    note: Optional[str] = None


class CompOffDeleteBody(BaseModel):
    """DELETE body — reason mandatory so the reversal is auditable."""
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=6, max_length=2000)
    reason_category: Optional[str] = Field(None, max_length=64)
    # The frontend sets True once the admin has acknowledged a negative-balance
    # or auto-generated warning; the backend hard-requires it for risky deletes.
    acknowledge_impact: bool = False


class CompOffEntry(BaseModel):
    """A single COMP_OFF earning row surfaced in the comp-off section."""
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    days: Decimal
    earned_on: Optional[date] = None
    expires_on: Optional[date] = None
    is_auto_generated: bool = False
    note: Optional[str] = None
    actor_name: Optional[str] = None
    created_at: datetime
    is_expired: bool = False
    days_until_expiry: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class CompOffListResponse(BaseModel):
    items: List[CompOffEntry]
    total: int
    fiscal_year: str


class CompOffStats(BaseModel):
    """Hero counters for the comp-off section."""
    total_earned: Decimal = Decimal("0")
    total_used: Decimal = Decimal("0")
    total_expired: Decimal = Decimal("0")
    balance_active: Decimal = Decimal("0")
    expiring_in_30d: int = 0
    auto_generated_count: int = 0
    manual_count: int = 0


# ═════════════════════════════════════════════════════════════════════════════
# Encashment (Phase 2)
# ═════════════════════════════════════════════════════════════════════════════

class EncashmentPreviewBody(BaseModel):
    """Self-service / admin pre-flight calc."""
    model_config = ConfigDict(extra="ignore")
    leave_type: LeaveType = LeaveType.EARNED
    days_requested: Decimal = Field(..., gt=0, le=365)
    # Optional override; defaults to employee.monthly_ctc snapshot at request-time
    basic_salary: Optional[Decimal] = None


class EncashmentPreviewResponse(BaseModel):
    formula_used: str
    basic_salary: Decimal
    days_requested: Decimal
    amount: Decimal
    available_balance: Decimal
    encashment_allowed: bool
    fiscal_year: str


class EncashmentCreateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    leave_type: LeaveType = LeaveType.EARNED
    days_requested: Decimal = Field(..., gt=0, le=365)
    request_notes: Optional[str] = Field(None, max_length=2000)


class EncashmentAdminCreateBody(EncashmentCreateBody):
    """Admin manual entry — pre-approved by HR."""
    employee_id: UUID
    basic_salary_override: Optional[Decimal] = None


class EncashmentDecisionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: EncashmentStatus   # APPROVED | REJECTED
    notes: Optional[str] = Field(None, max_length=1000)
    basic_salary_override: Optional[Decimal] = None


class EncashmentManagerDecideBody(BaseModel):
    """Reporting-manager endorsement (stage 1). APPROVED forwards to HR; REJECTED
    is terminal."""
    model_config = ConfigDict(extra="ignore")
    decision: Literal["APPROVED", "REJECTED"]
    notes: Optional[str] = Field(None, max_length=1000)


class EncashmentPayBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    payroll_ref: Optional[str] = Field(None, max_length=80)


class EncashmentResponse(BaseModel):
    id: UUID
    reference_no: str
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    leave_type: LeaveType
    fiscal_year: str
    days_requested: Decimal
    basic_salary_snapshot: Decimal
    formula_used: str
    amount: Decimal
    status: EncashmentStatus
    request_notes: Optional[str] = None
    # Stage 1 — manager endorsement
    manager_id: Optional[UUID] = None
    manager_name: Optional[str] = None
    manager_decision: Optional[str] = None
    manager_decided_at: Optional[datetime] = None
    manager_notes: Optional[str] = None
    # Stage 2 — HR sanction
    decided_by_id: Optional[UUID] = None
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_notes: Optional[str] = None
    paid_at: Optional[datetime] = None
    paid_by_id: Optional[UUID] = None
    payroll_ref: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class EncashmentListResponse(BaseModel):
    items: List[EncashmentResponse]
    total: int
    page: int = 1
    limit: int = 50
    total_pages: int = 1


class EncashmentStats(BaseModel):
    pending: int = 0
    approved: int = 0
    paid: int = 0
    rejected: int = 0
    pending_amount: Decimal = Decimal("0")
    paid_this_fy: Decimal = Decimal("0")


# ═════════════════════════════════════════════════════════════════════════════
# Calendar
# ═════════════════════════════════════════════════════════════════════════════

class LeaveCalendarEntry(BaseModel):
    """A single leave span surfaced in the team-availability calendar."""
    id: UUID
    reference_no: str
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    leave_type: LeaveType
    from_date: date
    to_date: date
    is_half_day: bool = False
    which_session: Optional[LeaveSession] = None
    status: LeaveStatus
    color_hex: Optional[str] = None


class LeaveCalendarResponse(BaseModel):
    items: List[LeaveCalendarEntry]
    from_date: date
    to_date: date
    total: int


# ═════════════════════════════════════════════════════════════════════════════
# Cron — accrual / carry-forward
# ═════════════════════════════════════════════════════════════════════════════

class AccrueMonthlyBody(BaseModel):
    """Cron body. month in "YYYY-MM" format; defaults to current month."""
    model_config = ConfigDict(extra="ignore")
    month: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}$")


class CarryForwardBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    from_fy: str = Field(..., pattern=r"^\d{4}-\d{2}$")   # e.g. "2025-26"
    to_fy: str = Field(..., pattern=r"^\d{4}-\d{2}$")


class CronRunResult(BaseModel):
    processed: int = 0
    skipped_existing: int = 0
    fiscal_year: Optional[str] = None
    month: Optional[str] = None
    notes: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════════
# Reports (Phase 3)
# ═════════════════════════════════════════════════════════════════════════════

class LeaveReportInfo(BaseModel):
    """One row in the /reports/index response — drives the admin Reports grid."""
    key: str
    name: str
    tagline: str
    subtitle: Optional[str] = None
    accent: str
    accent_soft: str
    accent_deep: str
    icon: str
    motif: str


class LeaveReportIndexResponse(BaseModel):
    items: List[LeaveReportInfo]
    total: int


class LeaveReportPreview(BaseModel):
    """JSON preview of a report — used by the preview drawer before exporting."""
    key: str
    name: str
    period: Dict[str, str]
    summary: Dict[str, object]
    rows: List[Dict[str, object]]
    total_rows: int


# ═════════════════════════════════════════════════════════════════════════════
# Audit logs (Phase 3)
# ═════════════════════════════════════════════════════════════════════════════

class LeaveAuditEntry(BaseModel):
    id: UUID
    action: str
    actor_user_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    employee_id: Optional[UUID] = None
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    target_table: Optional[str] = None
    target_id: Optional[UUID] = None
    payload: Dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LeaveAuditListResponse(BaseModel):
    items: List[LeaveAuditEntry]
    total: int
    page: int = 1
    limit: int = 50
    total_pages: int = 1
    actions_available: List[str] = Field(default_factory=list)
