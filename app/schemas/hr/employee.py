import re
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.hr.employee import (
    EmploymentType, EmployeeCategory, MaritalStatus, TaxRegime, LifecycleState,
)


PAN_REGEX = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
IFSC_REGEX = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
AADHAAR_LAST4_REGEX = re.compile(r"^\d{4}$")


# ─────────────────────────────────────── Create ───────────────────────────────────────

class _EmployeeBaseFields(BaseModel):
    """All editable fields. Used by Create/Update — Update marks everything Optional."""

    # If user_id is None, the create endpoint will provision a User shell using create_email + create_full_name.
    user_id: Optional[UUID] = None
    create_email: Optional[EmailStr] = None
    create_full_name: Optional[str] = None
    employee_code: Optional[str] = None

    # Identity
    gender: Optional[str] = None
    dob: Optional[date] = None
    marital_status: Optional[MaritalStatus] = None
    blood_group: Optional[str] = None
    nationality: Optional[str] = None
    religion: Optional[str] = None

    # Statutory
    aadhaar_last_4: Optional[str] = None
    pan: Optional[str] = None
    passport_number: Optional[str] = None
    passport_expiry: Optional[date] = None
    driving_license: Optional[str] = None
    uan: Optional[str] = None
    pf_number: Optional[str] = None
    esic_number: Optional[str] = None
    tax_regime: Optional[TaxRegime] = TaxRegime.NEW

    # Contact
    mobile: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    permanent_address: Optional[str] = None
    current_address: Optional[str] = None
    current_same_as_permanent: Optional[bool] = False

    # Employment
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    employment_type: Optional[EmploymentType] = None
    employee_category: Optional[EmployeeCategory] = None
    joining_date: Optional[date] = None
    confirmation_date: Optional[date] = None
    probation_months: Optional[int] = 6
    reporting_manager_id: Optional[UUID] = None
    hr_manager_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    pay_level: Optional[str] = None
    work_location_id: Optional[UUID] = None
    work_location_text: Optional[str] = None
    notice_period_days: Optional[int] = 30

    # Bank & Salary
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc: Optional[str] = None
    monthly_ctc: Optional[Decimal] = None
    annual_ctc: Optional[Decimal] = None

    @field_validator("pan")
    @classmethod
    def _validate_pan(cls, v):
        if v is None or v == "":
            return None
        v = v.strip().upper()
        if not PAN_REGEX.match(v):
            raise ValueError("Invalid PAN format (expected 5 letters + 4 digits + 1 letter)")
        return v

    @field_validator("ifsc")
    @classmethod
    def _validate_ifsc(cls, v):
        if v is None or v == "":
            return None
        v = v.strip().upper()
        if not IFSC_REGEX.match(v):
            raise ValueError("Invalid IFSC format")
        return v

    @field_validator("aadhaar_last_4")
    @classmethod
    def _validate_aadhaar_last_4(cls, v):
        if v is None or v == "":
            return None
        if not AADHAAR_LAST4_REGEX.match(v):
            raise ValueError("Aadhaar last 4 must be exactly 4 digits")
        return v


class EmployeeCreate(_EmployeeBaseFields):
    """Either provide user_id (link existing User) or create_email + create_full_name (create a new shell)."""
    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    # Optional back-link: when supplied, the employee is being onboarded from
    # a recruitment Offer. The endpoint will set Offer.employee_id = <new employee.id>
    # after the row is created, rejecting if the offer is not ACCEPTED or is
    # already linked.
    offer_id: Optional[UUID] = None


class EmployeeUpdate(BaseModel):
    """Self-contained Update schema; strict whitelist (no lifecycle/employee_id mutation here).

    Lifecycle changes use dedicated /lifecycle/* endpoints.
    """
    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    # Linked User fields — when present, applied to the related User record.
    # full_name / email may be null to skip; backend validates uniqueness on email.
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    # employee_code is canonical on User (unique) and mirrored on Employee;
    # backend updates both inside update_employee. employee_id stays
    # immutable (auto-generated sequence — use a separate admin tool to
    # renumber).
    employee_code: Optional[str] = None

    # Identity
    gender: Optional[str] = None
    dob: Optional[date] = None
    marital_status: Optional[MaritalStatus] = None
    blood_group: Optional[str] = None
    nationality: Optional[str] = None
    religion: Optional[str] = None
    confirmation_date: Optional[date] = None

    # Statutory
    aadhaar_last_4: Optional[str] = None
    pan: Optional[str] = None
    passport_number: Optional[str] = None
    passport_expiry: Optional[date] = None
    driving_license: Optional[str] = None
    uan: Optional[str] = None
    pf_number: Optional[str] = None
    esic_number: Optional[str] = None
    tax_regime: Optional[TaxRegime] = None

    # Contact
    mobile: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    permanent_address: Optional[str] = None
    current_address: Optional[str] = None
    current_same_as_permanent: Optional[bool] = None

    # Employment (non-lifecycle)
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    employment_type: Optional[EmploymentType] = None
    employee_category: Optional[EmployeeCategory] = None
    joining_date: Optional[date] = None
    probation_months: Optional[int] = None
    reporting_manager_id: Optional[UUID] = None
    hr_manager_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    pay_level: Optional[str] = None
    work_location_id: Optional[UUID] = None
    work_location_text: Optional[str] = None
    notice_period_days: Optional[int] = None

    # Bank & Salary
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc: Optional[str] = None
    monthly_ctc: Optional[Decimal] = None
    annual_ctc: Optional[Decimal] = None

    _validate_pan = _EmployeeBaseFields._validate_pan
    _validate_ifsc = _EmployeeBaseFields._validate_ifsc
    _validate_aadhaar_last_4 = _EmployeeBaseFields._validate_aadhaar_last_4


# ─────────────────────────────────────── Read ───────────────────────────────────────

class _UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    full_name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None


class _DepartmentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: str


class _DesignationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: str


class _GradeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: str


class _LocationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str


class EmployeeResponse(BaseModel):
    """Lean row used in list endpoints. PII is masked by default."""
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    user_id: UUID
    employee_id: str
    employee_code: Optional[str] = None

    # Identity (subset)
    gender: Optional[str] = None
    dob: Optional[date] = None
    nationality: Optional[str] = None

    # Employment (subset)
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    employment_type: Optional[str] = None
    employee_category: Optional[str] = None
    joining_date: Optional[date] = None
    grade_id: Optional[UUID] = None
    work_location_id: Optional[UUID] = None
    work_location_text: Optional[str] = None
    reporting_manager_id: Optional[UUID] = None

    # Lifecycle
    lifecycle_state: str
    is_deleted: bool

    # Joined / cached display fields (filled by router using joinedload)
    full_name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    department_name: Optional[str] = None
    designation_name: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class EmployeeDetailResponse(BaseModel):
    """Full detail (drawer view). Sensitive fields are masked unless the
    caller requests reveal with proper privilege.
    """
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    user_id: UUID
    employee_id: str
    employee_code: Optional[str] = None

    # Identity
    gender: Optional[str] = None
    dob: Optional[date] = None
    marital_status: Optional[str] = None
    blood_group: Optional[str] = None
    nationality: Optional[str] = None
    religion: Optional[str] = None

    # Statutory (masked by default)
    aadhaar_last_4: Optional[str] = None  # always last 4 only
    pan: Optional[str] = None
    passport_number: Optional[str] = None
    passport_expiry: Optional[date] = None
    driving_license: Optional[str] = None
    uan: Optional[str] = None
    pf_number: Optional[str] = None
    esic_number: Optional[str] = None
    tax_regime: Optional[str] = None

    # Contact
    mobile: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    permanent_address: Optional[str] = None
    current_address: Optional[str] = None
    current_same_as_permanent: bool = False

    # Employment
    employment_type: Optional[str] = None
    employee_category: Optional[str] = None
    joining_date: Optional[date] = None
    confirmation_date: Optional[date] = None
    probation_months: Optional[int] = None
    pay_level: Optional[str] = None
    notice_period_days: Optional[int] = None
    work_location_text: Optional[str] = None

    # Bank & Salary (account_number masked except last 4 in the router)
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc: Optional[str] = None
    monthly_ctc: Optional[Decimal] = None
    annual_ctc: Optional[Decimal] = None

    # Lifecycle
    lifecycle_state: str
    suspension_reason: Optional[str] = None
    suspension_date: Optional[date] = None
    notice_period_start_date: Optional[date] = None
    last_working_date: Optional[date] = None
    exit_date: Optional[date] = None
    is_deleted: bool
    archived_at: Optional[datetime] = None

    # Nested
    user: Optional[_UserSummary] = None
    department: Optional[_DepartmentSummary] = None
    designation: Optional[_DesignationSummary] = None
    grade: Optional[_GradeSummary] = None
    work_location: Optional[_LocationSummary] = None
    reporting_manager: Optional[_UserSummary] = None
    hr_manager: Optional[_UserSummary] = None

    created_at: datetime
    updated_at: datetime


class EmployeeListResponse(BaseModel):
    items: List[EmployeeResponse]
    total: int
    page: int
    limit: int
