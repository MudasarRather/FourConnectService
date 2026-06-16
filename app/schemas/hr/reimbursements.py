"""HR Reimbursements / Employee Claims — Pydantic v2 schemas.

Mirrors the Leave module's schema layout. All response models set
``from_attributes=True`` so ORM objects serialise directly. The approval-chain
shapes (``ApprovalStageConfig`` / ``ApprovalStageState``) reuse Leave's design,
swapped to ``ClaimApproverType``/``ClaimDecision`` with an optional per-stage
``min_amount`` for amount-banded routing.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Literal, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.hr.reimbursement_type import (
    ClaimStatus, ClaimDecision, SettlementMethod, ClaimApproverType,
)

ApproverType = Literal["MANAGER", "FINANCE", "HR", "USER"]
FieldType = Literal["text", "number", "date", "currency", "select", "textarea"]


# ═════════════════════════════════════════════════════════════════════════════
# Approval chain
# ═════════════════════════════════════════════════════════════════════════════

class ApprovalStageConfig(BaseModel):
    """One stage in a claim policy's configured approval chain."""
    model_config = ConfigDict(extra="ignore")
    approver_type: ApproverType
    approver_user_id: Optional[UUID] = None
    label: str = Field(..., min_length=1, max_length=80)
    min_amount: Optional[Decimal] = None   # stage applies only when claim amount > min_amount

    @field_validator("approver_user_id")
    @classmethod
    def _require_user_for_named(cls, v, info):
        if info.data.get("approver_type") == "USER" and not v:
            raise ValueError("USER approval stage requires approver_user_id")
        return v


class ApprovalStageState(ApprovalStageConfig):
    """One stage as snapshotted onto a Claim — config + per-stage state."""
    step: int = Field(..., ge=0)
    decision: Optional[ClaimDecision] = None
    decided_by_id: Optional[UUID] = None
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    notes: Optional[str] = None
    approver_name: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════════
# Category
# ═════════════════════════════════════════════════════════════════════════════

class ClaimFieldSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = Field(..., min_length=1, max_length=60)
    label: str = Field(..., min_length=1, max_length=80)
    type: FieldType = "text"
    required: bool = False
    options: Optional[List[str]] = None
    placeholder: Optional[str] = None


class ClaimCategoryBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=400)
    icon: Optional[str] = None
    color_hex: Optional[str] = Field(None, max_length=9)
    field_schema: List[ClaimFieldSpec] = Field(default_factory=list)
    default_settlement_method: SettlementMethod = SettlementMethod.PAYROLL
    requires_attachment: bool = True
    is_taxable: bool = False
    gl_code: Optional[str] = Field(None, max_length=40)
    sort_order: Optional[str] = Field(None, max_length=8)
    is_active: bool = True


class ClaimCategoryCreate(ClaimCategoryBase):
    code: str = Field(..., min_length=2, max_length=40)

    @field_validator("code")
    @classmethod
    def _upper_code(cls, v):
        return v.strip().upper().replace(" ", "_")


class ClaimCategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=400)
    icon: Optional[str] = None
    color_hex: Optional[str] = Field(None, max_length=9)
    field_schema: Optional[List[ClaimFieldSpec]] = None
    default_settlement_method: Optional[SettlementMethod] = None
    requires_attachment: Optional[bool] = None
    is_taxable: Optional[bool] = None
    gl_code: Optional[str] = Field(None, max_length=40)
    sort_order: Optional[str] = Field(None, max_length=8)
    is_active: Optional[bool] = None


class ClaimCategoryResponse(ClaimCategoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    created_at: datetime
    claim_count: Optional[int] = None    # populated on list for the admin grid


class ClaimCategoryListResponse(BaseModel):
    items: List[ClaimCategoryResponse]
    total: int


# ═════════════════════════════════════════════════════════════════════════════
# Policy
# ═════════════════════════════════════════════════════════════════════════════

class ClaimEligibility(BaseModel):
    model_config = ConfigDict(extra="ignore")
    department_ids: Optional[List[UUID]] = None
    designation_ids: Optional[List[UUID]] = None
    grade_ids: Optional[List[UUID]] = None
    employment_types: Optional[List[str]] = None


class ClaimPolicyBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    max_amount_per_claim: Optional[Decimal] = None
    max_amount_per_month: Optional[Decimal] = None
    max_amount_per_year: Optional[Decimal] = None
    max_claims_per_month: Optional[int] = None
    requires_attachment: bool = True
    attachment_required_above: Optional[Decimal] = None
    default_settlement_method: SettlementMethod = SettlementMethod.PAYROLL
    eligibility: Optional[ClaimEligibility] = None
    approval_chain: Optional[List[ApprovalStageConfig]] = None
    submission_window_days: Optional[int] = None
    label: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=400)
    is_active: bool = True


class ClaimPolicyCreate(ClaimPolicyBase):
    category_id: UUID


class ClaimPolicyUpdate(ClaimPolicyBase):
    pass


class ClaimPolicyResponse(ClaimPolicyBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    category_id: UUID
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    created_at: datetime


class ClaimPolicyListResponse(BaseModel):
    items: List[ClaimPolicyResponse]
    total: int


# ═════════════════════════════════════════════════════════════════════════════
# Claim
# ═════════════════════════════════════════════════════════════════════════════

class ClaimAttachment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_url: str
    file_path: Optional[str] = None
    original_filename: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    doc_type: Optional[str] = None


class ClaimCreate(BaseModel):
    """Employee self-service claim submission."""
    model_config = ConfigDict(extra="ignore")
    category_id: UUID
    expense_date: date
    amount: Decimal = Field(..., gt=0)
    currency: str = Field("INR", min_length=3, max_length=3)
    description: Optional[str] = Field(None, max_length=2000)
    vendor: Optional[str] = Field(None, max_length=160)
    remarks: Optional[str] = Field(None, max_length=2000)
    cost_center: Optional[str] = Field(None, max_length=120)
    project_id: Optional[UUID] = None
    attachments: List[ClaimAttachment] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("expense_date")
    @classmethod
    def _not_future(cls, v):
        if v and v > date.today():
            raise ValueError("expense_date cannot be in the future")
        return v


class ClaimAdminCreate(ClaimCreate):
    """Admin raising a claim on behalf of an employee (lands pre-approved)."""
    employee_id: UUID


class ClaimUpdate(BaseModel):
    """Edit a DRAFT / RETURNED claim — all optional."""
    model_config = ConfigDict(extra="ignore")
    category_id: Optional[UUID] = None
    expense_date: Optional[date] = None
    amount: Optional[Decimal] = Field(None, gt=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    description: Optional[str] = Field(None, max_length=2000)
    vendor: Optional[str] = Field(None, max_length=160)
    remarks: Optional[str] = Field(None, max_length=2000)
    cost_center: Optional[str] = Field(None, max_length=120)
    project_id: Optional[UUID] = None
    attachments: Optional[List[ClaimAttachment]] = None
    details: Optional[Dict[str, Any]] = None


class ClaimSettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    settlement_number: str
    method: SettlementMethod
    amount: Decimal
    settlement_date: Optional[date] = None
    reference: Optional[str] = None
    bank_account_last4: Optional[str] = None
    payroll_ref: Optional[str] = None
    notes: Optional[str] = None
    is_reversed: bool = False
    created_at: datetime


class ClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    claim_number: str
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    category_id: UUID
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    category_icon: Optional[str] = None
    category_color: Optional[str] = None
    claim_date: date
    expense_date: date
    amount: Decimal
    currency: str
    description: Optional[str] = None
    vendor: Optional[str] = None
    remarks: Optional[str] = None
    cost_center: Optional[str] = None
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    attachments: List[ClaimAttachment] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)
    status: ClaimStatus
    submitted_at: Optional[datetime] = None
    approval_steps: List[ApprovalStageState] = Field(default_factory=list)
    current_step: int = 0
    approved_at: Optional[datetime] = None
    approver_notes: Optional[str] = None
    return_reason: Optional[str] = None
    reject_reason: Optional[str] = None
    clarification_note: Optional[str] = None
    approved_amount: Optional[Decimal] = None
    settlement_method: Optional[SettlementMethod] = None
    settled_at: Optional[datetime] = None
    settlement_number: Optional[str] = None
    payroll_ref: Optional[str] = None
    paid_at: Optional[datetime] = None
    reversed_at: Optional[datetime] = None
    reversal_reason: Optional[str] = None
    latest_settlement: Optional[ClaimSettlementResponse] = None
    created_at: datetime
    # Convenience flags for the frontend
    can_edit: bool = False
    can_withdraw: bool = False


class ClaimListResponse(BaseModel):
    items: List[ClaimResponse]
    total: int
    page: int
    limit: int
    total_pages: int
    unlinked: bool = False   # self-service: caller has no linked Employee profile


# ═════════════════════════════════════════════════════════════════════════════
# Action bodies
# ═════════════════════════════════════════════════════════════════════════════

class ClaimDecisionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: ClaimDecision   # APPROVED | REJECTED | RETURNED
    notes: Optional[str] = Field(None, max_length=1000)
    approved_amount: Optional[Decimal] = Field(None, ge=0)  # final approver may settle a lower amount


class ClaimReturnBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=3, max_length=1000)


class ClaimClarificationBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: str = Field(..., min_length=3, max_length=1000)


class ClaimEscalateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: Optional[str] = Field(None, max_length=1000)


class ClaimCancelBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(None, max_length=1000)


class ClaimBulkDecideBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: List[UUID] = Field(..., min_length=1)
    decision: ClaimDecision
    notes: Optional[str] = Field(None, max_length=1000)


class SettlePayrollBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    period_month: Optional[int] = Field(None, ge=1, le=12)   # null → next available run
    period_year: Optional[int] = Field(None, ge=2000, le=2100)
    approved_amount: Optional[Decimal] = Field(None, gt=0)   # defaults to claim.approved_amount/amount
    is_taxable: Optional[bool] = None                         # defaults to category.is_taxable
    note: Optional[str] = Field(None, max_length=500)


class SettleDirectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    method: SettlementMethod   # BANK_TRANSFER | CASH | CHEQUE | PETTY_CASH
    settlement_date: Optional[date] = None
    amount: Optional[Decimal] = Field(None, gt=0)
    reference: Optional[str] = Field(None, max_length=120)
    bank_account_last4: Optional[str] = Field(None, max_length=4)
    notes: Optional[str] = Field(None, max_length=500)

    @field_validator("method")
    @classmethod
    def _not_payroll(cls, v):
        if v == SettlementMethod.PAYROLL:
            raise ValueError("Use the /settle/payroll endpoint for payroll settlement")
        return v


class ClaimReversalBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=3, max_length=1000)


# ═════════════════════════════════════════════════════════════════════════════
# Dashboard / stats
# ═════════════════════════════════════════════════════════════════════════════

class CategoryCount(BaseModel):
    category_id: Optional[UUID] = None
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    color_hex: Optional[str] = None
    count: int
    amount: Decimal


class StatusCount(BaseModel):
    status: str
    count: int
    amount: Decimal


class MonthlyTrendPoint(BaseModel):
    month: str           # YYYY-MM
    claimed: Decimal
    settled: Decimal
    count: int


class ReimbursementStats(BaseModel):
    total_claims: int = 0
    pending_approval: int = 0
    approved_unsettled: int = 0
    rejected: int = 0
    settled_amount: Decimal = Decimal("0")
    pending_settlement_amount: Decimal = Decimal("0")
    claims_this_month: int = 0
    paid_via_payroll: int = 0
    paid_via_direct: int = 0
    avg_processing_days: Optional[float] = None
    total_reimbursed_fy: Decimal = Decimal("0")
    by_category: List[CategoryCount] = Field(default_factory=list)
    by_status: List[StatusCount] = Field(default_factory=list)
    monthly_trend: List[MonthlyTrendPoint] = Field(default_factory=list)
    settlement_split: Dict[str, Decimal] = Field(default_factory=dict)


# ═════════════════════════════════════════════════════════════════════════════
# Self-service summary / balances
# ═════════════════════════════════════════════════════════════════════════════

class MyClaimSummary(BaseModel):
    submitted_amount: Decimal = Decimal("0")
    approved_amount: Decimal = Decimal("0")
    settled_amount: Decimal = Decimal("0")
    in_flight: int = 0
    settled_count: int = 0
    total_claims: int = 0
    unlinked: bool = False


class CategoryBalance(BaseModel):
    category_id: UUID
    category_code: str
    category_name: str
    color_hex: Optional[str] = None
    icon: Optional[str] = None
    spent_this_month: Decimal = Decimal("0")
    spent_this_year: Decimal = Decimal("0")
    max_amount_per_month: Optional[Decimal] = None
    max_amount_per_year: Optional[Decimal] = None
    claims_this_month: int = 0
    max_claims_per_month: Optional[int] = None


class MyBalancesResponse(BaseModel):
    items: List[CategoryBalance] = Field(default_factory=list)
    unlinked: bool = False


# ═════════════════════════════════════════════════════════════════════════════
# Audit
# ═════════════════════════════════════════════════════════════════════════════

class ClaimAuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    entity_type: str
    entity_id: Optional[UUID] = None
    action: str
    claim_id: Optional[UUID] = None
    claim_number: Optional[str] = None
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime


class ClaimAuditListResponse(BaseModel):
    items: List[ClaimAuditEntry]
    total: int


# ═════════════════════════════════════════════════════════════════════════════
# Approver candidates / reports
# ═════════════════════════════════════════════════════════════════════════════

class ApproverCandidate(BaseModel):
    id: UUID
    name: Optional[str] = None
    email: Optional[str] = None
    is_superuser: bool = False


class ApproverCandidateListResponse(BaseModel):
    items: List[ApproverCandidate]


class ReportInfo(BaseModel):
    key: str
    name: str
    description: Optional[str] = None


class ReportIndexResponse(BaseModel):
    items: List[ReportInfo]
