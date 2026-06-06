"""HR Payroll — Pydantic v2 schemas (request/response DTOs).

One module for the whole payroll surface. Response models set
``from_attributes=True`` so ORM rows serialize directly. Money fields are
``Decimal``; the frontend receives JSON numbers/strings and renders INR.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.hr.salary_component import ComponentType, CalcType, StatutoryKind
from app.models.hr.employee_compensation import CompensationStatus
from app.models.hr.payroll_batch import PayrollBatchStatus
from app.models.hr.payslip import PayslipStatus
from app.models.hr.payroll_config import PayrollAuditAction
from app.models.hr.payroll_adjustment import AdjustmentType, AdjustmentStatus
from app.models.hr.employee import TaxRegime


# ═══════════════════════════ Salary Components ═══════════════════════════

class SalaryComponentBase(BaseModel):
    name: str
    component_type: ComponentType
    calc_type: CalcType = CalcType.FLAT
    statutory_kind: Optional[StatutoryKind] = None
    formula: Optional[str] = None
    percent_value: Optional[Decimal] = None
    percent_of_code: Optional[str] = None
    flat_amount: Optional[Decimal] = None
    sequence: int = 100
    is_taxable: bool = True
    is_part_of_gross: bool = True
    affects_pf_wage: bool = False
    affects_esi_wage: bool = False
    prorate_on_lop: bool = True
    is_employer_cost: bool = False


class SalaryComponentCreate(SalaryComponentBase):
    code: str = Field(..., min_length=1, max_length=40)

    @field_validator("code")
    @classmethod
    def _upper_code(cls, v: str) -> str:
        return v.strip().upper().replace(" ", "_")


class SalaryComponentUpdate(BaseModel):
    name: Optional[str] = None
    component_type: Optional[ComponentType] = None
    calc_type: Optional[CalcType] = None
    statutory_kind: Optional[StatutoryKind] = None
    formula: Optional[str] = None
    percent_value: Optional[Decimal] = None
    percent_of_code: Optional[str] = None
    flat_amount: Optional[Decimal] = None
    sequence: Optional[int] = None
    is_taxable: Optional[bool] = None
    is_part_of_gross: Optional[bool] = None
    affects_pf_wage: Optional[bool] = None
    affects_esi_wage: Optional[bool] = None
    prorate_on_lop: Optional[bool] = None
    is_employer_cost: Optional[bool] = None
    is_active: Optional[bool] = None


class SalaryComponentResponse(SalaryComponentBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    is_system: bool
    is_active: bool
    created_at: datetime


class SalaryComponentListResponse(BaseModel):
    items: List[SalaryComponentResponse]
    total: int


# ═══════════════════════════ Salary Structures ═══════════════════════════

class StructureComponentLinkCreate(BaseModel):
    component_id: UUID
    sequence: Optional[int] = None
    override_calc_type: Optional[CalcType] = None
    override_formula: Optional[str] = None
    override_percent_value: Optional[Decimal] = None
    override_percent_of_code: Optional[str] = None
    override_flat_amount: Optional[Decimal] = None


class StructureComponentLinkUpdate(BaseModel):
    sequence: Optional[int] = None
    override_calc_type: Optional[CalcType] = None
    override_formula: Optional[str] = None
    override_percent_value: Optional[Decimal] = None
    override_percent_of_code: Optional[str] = None
    override_flat_amount: Optional[Decimal] = None
    is_active: Optional[bool] = None


class StructureComponentLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    component_id: UUID
    sequence: int
    override_calc_type: Optional[CalcType] = None
    override_formula: Optional[str] = None
    override_percent_value: Optional[Decimal] = None
    override_percent_of_code: Optional[str] = None
    override_flat_amount: Optional[Decimal] = None
    is_active: bool
    # enriched
    component_code: Optional[str] = None
    component_name: Optional[str] = None
    component_type: Optional[ComponentType] = None
    calc_type: Optional[CalcType] = None


class SalaryStructureCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=40)
    name: str
    description: Optional[str] = None
    grade_id: Optional[UUID] = None
    pay_scale: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_default: bool = False
    pf_restrict_to_ceiling: bool = True
    components: List[StructureComponentLinkCreate] = Field(default_factory=list)

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper().replace(" ", "-")


class SalaryStructureUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    grade_id: Optional[UUID] = None
    pay_scale: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    pf_restrict_to_ceiling: Optional[bool] = None


class SalaryStructureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    name: str
    description: Optional[str] = None
    grade_id: Optional[UUID] = None
    pay_scale: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_default: bool
    is_active: bool
    pf_restrict_to_ceiling: bool = True
    created_at: datetime
    component_count: int = 0
    components: List[StructureComponentLinkResponse] = Field(default_factory=list)


class SalaryStructureListResponse(BaseModel):
    items: List[SalaryStructureResponse]
    total: int


# ─── Preview (dry-run CTC → breakdown) ───

class PreviewLine(BaseModel):
    component_code: str
    component_name: str
    component_type: ComponentType
    amount: Decimal
    calc_note: Optional[str] = None
    is_employer_cost: bool = False
    is_taxable: bool = False


class PreviewRequest(BaseModel):
    structure_id: Optional[UUID] = None
    # Ad-hoc / unsaved component list — when provided, the preview is computed from
    # these (live drawer edits) instead of the persisted structure. Falls back to
    # structure_id when omitted.
    components: Optional[List[StructureComponentLinkCreate]] = None
    monthly_ctc: Optional[Decimal] = None
    annual_ctc: Optional[Decimal] = None
    monthly_gross: Optional[Decimal] = None
    regime: TaxRegime = TaxRegime.NEW
    state_code: Optional[str] = None
    declarations: Optional[Dict[str, Any]] = None
    # PF policy for the preview (live drawer toggle). None → use structure/default.
    pf_restrict_to_ceiling: Optional[bool] = None


class PreviewResponse(BaseModel):
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    employer_contributions: Decimal
    ctc_value: Decimal
    monthly_gross: Decimal
    lines: List[PreviewLine]


# ═══════════════════════════ Employee Compensation ═══════════════════════════

class CompensationCreate(BaseModel):
    structure_id: Optional[UUID] = None
    effective_from: date
    annual_ctc: Decimal
    monthly_ctc: Optional[Decimal] = None
    tax_regime: Optional[TaxRegime] = None
    revision_reason: Optional[str] = None
    revision_ref: Optional[str] = None
    tds_declarations: Optional[Dict[str, Any]] = None
    activate: bool = True  # activate immediately (supersede prior open row)


class CompensationUpdate(BaseModel):
    structure_id: Optional[UUID] = None
    effective_from: Optional[date] = None
    annual_ctc: Optional[Decimal] = None
    monthly_ctc: Optional[Decimal] = None
    tax_regime: Optional[TaxRegime] = None
    revision_reason: Optional[str] = None
    tds_declarations: Optional[Dict[str, Any]] = None


class CompensationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    structure_id: Optional[UUID] = None
    effective_from: date
    effective_to: Optional[date] = None
    annual_ctc: Decimal
    monthly_ctc: Decimal
    monthly_gross: Optional[Decimal] = None
    basic_amount: Optional[Decimal] = None
    breakdown: Optional[Dict[str, Any]] = None
    tax_regime: Optional[TaxRegime] = None
    revision_reason: Optional[str] = None
    revision_ref: Optional[str] = None
    status: CompensationStatus
    created_at: datetime
    # enriched
    structure_name: Optional[str] = None
    employee_name: Optional[str] = None


class CompensationListResponse(BaseModel):
    items: List[CompensationResponse]
    total: int


# ═══════════════════════════ Payroll Batch ═══════════════════════════

class PayrollBatchCreate(BaseModel):
    period_month: int = Field(..., ge=1, le=12)
    period_year: int = Field(..., ge=2000, le=2100)
    pay_date: Optional[date] = None
    department_id: Optional[UUID] = None
    notes: Optional[str] = None


class PayrollBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    batch_no: str
    period_month: int
    period_year: int
    pay_date: Optional[date] = None
    status: PayrollBatchStatus
    department_id: Optional[UUID] = None
    total_employees: int
    total_gross: Decimal
    total_deductions: Decimal
    total_net: Decimal
    total_employer_cost: Decimal
    notes: Optional[str] = None
    generated_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    locked_at: Optional[datetime] = None
    created_at: datetime
    department_name: Optional[str] = None


class PayrollBatchListResponse(BaseModel):
    items: List[PayrollBatchResponse]
    total: int


class BatchActionBody(BaseModel):
    note: Optional[str] = None
    pay_date: Optional[date] = None


class BatchDeleteBody(BaseModel):
    """Reason capture for soft-deleting a pay run (corporate workflow + audit)."""
    reason: str = Field(..., min_length=3, max_length=300)
    note: Optional[str] = None


class BatchProgress(BaseModel):
    status: PayrollBatchStatus
    done: int
    total: int
    pct: float


# ─── Run eligibility (pre-flight roster: who gets paid + who's blocked & why) ───

class EligibilityRequest(BaseModel):
    period_month: int = Field(..., ge=1, le=12)
    period_year: int = Field(..., ge=2000, le=2100)
    department_id: Optional[UUID] = None


class EligibilityRow(BaseModel):
    employee_id: UUID
    employee_code: Optional[str] = None
    employee_name: Optional[str] = None
    department_name: Optional[str] = None
    lifecycle_state: str
    monthly_ctc: Decimal
    lop_days: Decimal
    paid_days: Optional[Decimal] = None      # attendance/leave-aware (engine)
    working_days: Optional[Decimal] = None
    est_gross: Optional[Decimal] = None       # estimated period gross earnings
    est_net: Optional[Decimal] = None         # estimated period net pay (after LOP + deductions)
    eligible: bool
    reason: Optional[str] = None         # machine key, e.g. "no_compensation"
    reason_label: Optional[str] = None   # human label for the UI
    final_settlement: bool = False


class EligibilityResponse(BaseModel):
    period_month: int
    period_year: int
    department_id: Optional[UUID] = None
    total_candidates: int
    eligible_count: int
    blocked_count: int
    final_settlement_count: int
    estimated_monthly_ctc: Decimal = Decimal("0")
    estimated_gross: Decimal = Decimal("0")
    estimated_net: Decimal = Decimal("0")
    estimated_employer_cost: Decimal = Decimal("0")
    rows: List[EligibilityRow] = Field(default_factory=list)


# ═══════════════════════════ Payslips ═══════════════════════════

class PayslipLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    component_code: str
    component_name: str
    component_type: ComponentType
    statutory_kind: Optional[StatutoryKind] = None
    sequence: int
    full_amount: Decimal
    amount: Decimal
    is_taxable: bool
    is_employer_cost: bool
    calc_note: Optional[str] = None


class PayslipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    batch_id: UUID
    employee_id: UUID
    payslip_no: str
    period_month: int
    period_year: int
    status: PayslipStatus
    working_days: Decimal
    lop_days: Decimal
    paid_days: Decimal
    tax_regime: Optional[TaxRegime] = None
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    employer_contributions: Decimal
    ctc_value: Decimal
    encashment_amount: Decimal
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc: Optional[str] = None
    pan: Optional[str] = None
    uan: Optional[str] = None
    remarks: Optional[str] = None
    created_at: datetime
    lines: List[PayslipLineResponse] = Field(default_factory=list)
    # enriched
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    designation_name: Optional[str] = None


class PayslipListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    payslip_no: str
    period_month: int
    period_year: int
    status: PayslipStatus
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None


class PayslipListResponse(BaseModel):
    items: List[PayslipListItem]
    total: int


class PayslipHoldBody(BaseModel):
    """Reason capture for placing a payslip on hold (corporate workflow)."""
    reason: str = Field(..., min_length=3, max_length=300)
    note: Optional[str] = None


class MyPayslipListResponse(BaseModel):
    items: List[PayslipListItem]
    total: int
    unlinked: bool = False


class MyAnnualEarnings(BaseModel):
    fiscal_year: str
    months: List[Dict[str, Any]]  # [{month, year, label, gross, net, deductions}]
    total_gross: Decimal
    total_net: Decimal
    total_deductions: Decimal
    unlinked: bool = False


# ═══════════════════════════ Statutory Config ═══════════════════════════

class StatutoryConfigCreate(BaseModel):
    fiscal_year: str
    state_code: Optional[str] = None
    key: str
    value_num: Optional[Decimal] = None
    value_json: Optional[Any] = None
    effective_from: date
    description: Optional[str] = None


class StatutoryConfigUpdate(BaseModel):
    value_num: Optional[Decimal] = None
    value_json: Optional[Any] = None
    effective_to: Optional[date] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None


class StatutoryConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    fiscal_year: str
    state_code: Optional[str] = None
    key: str
    value_num: Optional[Decimal] = None
    value_json: Optional[Any] = None
    effective_from: date
    effective_to: Optional[date] = None
    is_active: bool
    description: Optional[str] = None


class StatutoryConfigListResponse(BaseModel):
    items: List[StatutoryConfigResponse]
    total: int


# ═══════════════════════════ Dashboard + Audit ═══════════════════════════

class PayrollDashboardStats(BaseModel):
    fiscal_year: str
    period_label: str
    structures_count: int
    components_count: int
    active_compensations: int
    employees_on_payroll: int
    current_gross: Decimal
    current_net: Decimal
    current_deductions: Decimal
    current_employer_cost: Decimal
    current_headcount: int
    pending_approvals: int
    batches_by_status: Dict[str, int]
    cost_trend: List[Dict[str, Any]]  # [{label, gross, net, deductions}]


class PayrollAuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    entity_type: str
    entity_id: UUID
    action: PayrollAuditAction
    batch_id: Optional[UUID] = None
    actor_id: Optional[UUID] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime
    actor_name: Optional[str] = None


class PayrollAuditListResponse(BaseModel):
    items: List[PayrollAuditEntry]
    total: int


# ═══════════════════════════ Adjustments (Phase B) ═══════════════════════════

class AdjustmentCreate(BaseModel):
    employee_id: UUID
    adjustment_type: AdjustmentType
    sub_type: Optional[str] = None
    title: str
    amount: Decimal
    is_taxable: bool = True
    is_deduction: bool = False
    period_month: Optional[int] = None
    period_year: Optional[int] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    reason: Optional[str] = None


class AdjustmentUpdate(BaseModel):
    sub_type: Optional[str] = None
    title: Optional[str] = None
    amount: Optional[Decimal] = None
    is_taxable: Optional[bool] = None
    is_deduction: Optional[bool] = None
    period_month: Optional[int] = None
    period_year: Optional[int] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    reason: Optional[str] = None


class AdjustmentActionBody(BaseModel):
    """Reason capture for cancelling / deleting an adjustment (audit workflow).

    Optional overall so legacy callers that POST no body still work; the UI
    always supplies a reason. Mirrors ``BatchDeleteBody`` semantics.
    """
    reason: Optional[str] = Field(None, max_length=300)
    note: Optional[str] = Field(None, max_length=500)


class AdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    adjustment_type: AdjustmentType
    sub_type: Optional[str] = None
    title: str
    amount: Decimal
    is_taxable: bool
    is_deduction: bool
    period_month: Optional[int] = None
    period_year: Optional[int] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    reason: Optional[str] = None
    status: AdjustmentStatus
    payroll_ref: Optional[str] = None
    paid_at: Optional[datetime] = None
    created_at: datetime
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None


class AdjustmentListResponse(BaseModel):
    items: List[AdjustmentResponse]
    total: int


# ═══════════════════════════ Tax / TDS / Compliance ═══════════════════════════

class TaxProjectionRequest(BaseModel):
    employee_id: UUID
    annual_gross: Optional[Decimal] = None
    declarations: Optional[Dict[str, Any]] = None
    save_declarations: bool = False


class TaxRegimeResult(BaseModel):
    regime: str
    annual_gross: Decimal
    taxable_income: Decimal
    annual_tax: Decimal
    monthly_tds: Decimal


class TaxProjectionResponse(BaseModel):
    employee_id: UUID
    employee_name: Optional[str] = None
    fiscal_year: str
    old_regime: TaxRegimeResult
    new_regime: TaxRegimeResult
    recommended: str


class TdsSummaryItem(BaseModel):
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    pan: Optional[str] = None
    tds_period: Decimal
    tds_ytd: Decimal


class TdsSummaryResponse(BaseModel):
    items: List[TdsSummaryItem]
    total: int
    period_label: str
    total_tds_period: Decimal


class ComplianceSummary(BaseModel):
    period_label: str
    fiscal_year: str
    employee_count: int
    pf_employee: Decimal
    pf_employer: Decimal
    esi_employee: Decimal
    esi_employer: Decimal
    professional_tax: Decimal
    tds: Decimal
    total_statutory: Decimal


class ReportInfo(BaseModel):
    key: str
    name: str
    description: str
    # Added (non-breaking) for the redesigned Reports hub — older clients ignore these.
    tagline: Optional[str] = None
    subtitle: Optional[str] = None
    group: Optional[str] = None
    icon: Optional[str] = None
    motif: Optional[str] = None
    accent: Optional[str] = None
    accent_soft: Optional[str] = None
    accent_deep: Optional[str] = None
    formats: List[str] = ["pdf", "excel", "csv"]


class ReportIndexResponse(BaseModel):
    reports: List[ReportInfo]
