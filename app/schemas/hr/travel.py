"""HR Travel Management — Pydantic v2 schemas.

Mirrors the Reimbursements module's schema layout. All response models set
``from_attributes=True`` so ORM objects serialise directly. The approval-chain
shapes (``TravelStageConfig`` / ``TravelStageState``) reuse the Reimbursements
design, swapped to ``TravelApproverType`` / ``TravelDecision`` with an optional
per-stage ``min_amount`` for amount-banded routing.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Literal, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.hr.travel_type import (
    TravelRequestStatus, TravelDecision, TravelPriority, CityCategory,
    BookingType, BookingStatus, AdvanceStatus, DaRecordStatus,
    TravelSettlementStatus, TravelSettlementMethod,
)

ApproverType = Literal["MANAGER", "DEPT_HEAD", "FINANCE", "HR", "USER"]
FieldType = Literal["text", "number", "date", "currency", "select", "textarea"]


# ═════════════════════════════════════════════════════════════════════════════
# Approval chain
# ═════════════════════════════════════════════════════════════════════════════

class TravelStageConfig(BaseModel):
    """One stage in a travel policy's configured approval chain."""
    model_config = ConfigDict(extra="ignore")
    approver_type: ApproverType
    approver_user_id: Optional[UUID] = None
    label: str = Field(..., min_length=1, max_length=80)
    min_amount: Optional[Decimal] = None   # stage applies only when est. cost > min_amount

    @field_validator("approver_user_id")
    @classmethod
    def _require_user_for_named(cls, v, info):
        if info.data.get("approver_type") in ("USER", "DEPT_HEAD") and not v:
            raise ValueError(f"{info.data.get('approver_type')} stage requires approver_user_id")
        return v


class TravelStageState(TravelStageConfig):
    """One stage as snapshotted onto a request — config + per-stage state."""
    step: int = Field(..., ge=0)
    decision: Optional[TravelDecision] = None
    decided_by_id: Optional[UUID] = None
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    notes: Optional[str] = None
    approver_name: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════════
# Category
# ═════════════════════════════════════════════════════════════════════════════

class TravelFieldSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = Field(..., min_length=1, max_length=60)
    label: str = Field(..., min_length=1, max_length=80)
    type: FieldType = "text"
    required: bool = False
    options: Optional[List[str]] = None
    placeholder: Optional[str] = None


class TravelCategoryBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=400)
    icon: Optional[str] = None
    color_hex: Optional[str] = Field(None, max_length=9)
    field_schema: List[TravelFieldSpec] = Field(default_factory=list)
    default_travel_type: Optional[str] = Field(None, max_length=40)
    requires_attachment: bool = False
    sort_order: Optional[str] = Field(None, max_length=8)
    is_active: bool = True


class TravelCategoryCreate(TravelCategoryBase):
    code: str = Field(..., min_length=2, max_length=40)

    @field_validator("code")
    @classmethod
    def _upper_code(cls, v):
        return v.strip().upper().replace(" ", "_")


class TravelCategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=400)
    icon: Optional[str] = None
    color_hex: Optional[str] = Field(None, max_length=9)
    field_schema: Optional[List[TravelFieldSpec]] = None
    default_travel_type: Optional[str] = Field(None, max_length=40)
    requires_attachment: Optional[bool] = None
    sort_order: Optional[str] = Field(None, max_length=8)
    is_active: Optional[bool] = None


class TravelCategoryResponse(TravelCategoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    created_at: datetime
    request_count: Optional[int] = None


class TravelCategoryListResponse(BaseModel):
    items: List[TravelCategoryResponse]
    total: int


# ═════════════════════════════════════════════════════════════════════════════
# Policy
# ═════════════════════════════════════════════════════════════════════════════

class TravelEligibility(BaseModel):
    model_config = ConfigDict(extra="ignore")
    department_ids: Optional[List[UUID]] = None
    designation_ids: Optional[List[UUID]] = None
    grade_ids: Optional[List[UUID]] = None


class TravelPolicyBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policy_name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=400)
    grade_id: Optional[UUID] = None
    travel_scope: str = "ALL"
    flight_eligibility: Optional[str] = Field(None, max_length=40)
    train_class: Optional[str] = Field(None, max_length=40)
    hotel_category: Optional[str] = Field(None, max_length=40)
    da_eligible: bool = True
    advance_limit: Optional[Decimal] = None
    approval_chain: Optional[List[TravelStageConfig]] = None
    eligibility: Optional[TravelEligibility] = None
    is_active: bool = True


class TravelPolicyCreate(TravelPolicyBase):
    pass


class TravelPolicyUpdate(TravelPolicyBase):
    policy_name: Optional[str] = Field(None, min_length=1, max_length=120)


class TravelPolicyResponse(TravelPolicyBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    grade_name: Optional[str] = None
    created_at: datetime


class TravelPolicyListResponse(BaseModel):
    items: List[TravelPolicyResponse]
    total: int


# ═════════════════════════════════════════════════════════════════════════════
# DA rate matrix
# ═════════════════════════════════════════════════════════════════════════════

class DaRateBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    grade_id: Optional[UUID] = None
    city_category: CityCategory
    daily_rate: Decimal = Field(..., ge=0)
    currency: str = Field("INR", min_length=3, max_length=3)
    effective_date: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=200)
    is_active: bool = True


class DaRateCreate(DaRateBase):
    pass


class DaRateUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    grade_id: Optional[UUID] = None
    city_category: Optional[CityCategory] = None
    daily_rate: Optional[Decimal] = Field(None, ge=0)
    effective_date: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None


class DaRateResponse(DaRateBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    grade_name: Optional[str] = None
    created_at: datetime


class DaRateListResponse(BaseModel):
    items: List[DaRateResponse]
    total: int


# ═════════════════════════════════════════════════════════════════════════════
# Attachments / expense lines
# ═════════════════════════════════════════════════════════════════════════════

class TravelAttachment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_url: str
    file_path: Optional[str] = None
    original_filename: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    doc_type: Optional[str] = None


class ExpenseLine(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str = Field(..., max_length=40)            # TRAVEL | ACCOMMODATION | FOOD | …
    expense_date: Optional[date] = None
    vendor: Optional[str] = Field(None, max_length=160)
    amount: Decimal = Field(..., ge=0)
    gst: Optional[Decimal] = Field(None, ge=0)
    currency: str = Field("INR", min_length=3, max_length=3)
    note: Optional[str] = Field(None, max_length=300)
    attachments: List[TravelAttachment] = Field(default_factory=list)


# ═════════════════════════════════════════════════════════════════════════════
# Travel Request
# ═════════════════════════════════════════════════════════════════════════════

TRIP_TYPES = ("ONE_WAY", "ROUND_TRIP", "MULTI_CITY")


LEG_MODES = ("FLIGHT", "TRAIN", "BUS", "TAXI", "RENTAL")


class TravelLeg(BaseModel):
    """A single hop of a multi-city itinerary (origin → destination on a date).

    Each leg carries its own transport ``mode`` — a multi-city trip can mix modes
    (e.g. A→B by flight, B→C by train, C→A by flight). The trip-level
    flight_required / train_required / local_transport_required flags are DERIVED
    from the union of leg modes (see flow._modes_to_flags)."""
    model_config = ConfigDict(extra="ignore")
    from_location: str = Field(..., min_length=1, max_length=160)
    to_location: str = Field(..., min_length=1, max_length=160)
    departure_date: date
    mode: str = "FLIGHT"
    to_city_category: Optional[CityCategory] = None  # per-leg tier (display / future per-leg DA)

    @field_validator("mode")
    @classmethod
    def _mode_valid(cls, v):
        v = (v or "FLIGHT").upper()
        if v not in LEG_MODES:
            raise ValueError(f"leg mode must be one of {', '.join(LEG_MODES)}")
        return v


class TravelRequestCreate(BaseModel):
    """Employee self-service travel request submission."""
    model_config = ConfigDict(extra="ignore")
    purpose: str = Field(..., min_length=3, max_length=2000)
    travel_type: Optional[str] = Field(None, max_length=40)
    category_id: Optional[UUID] = None
    priority: TravelPriority = TravelPriority.NORMAL
    trip_type: str = "ROUND_TRIP"
    itinerary: Optional[List[TravelLeg]] = None
    from_location: str = Field(..., min_length=1, max_length=160)
    to_location: str = Field(..., min_length=1, max_length=160)
    from_location_id: Optional[UUID] = None
    to_location_id: Optional[UUID] = None
    to_city_category: CityCategory = CityCategory.TIER_2
    departure_date: date
    return_date: date
    project_id: Optional[UUID] = None
    cost_center: Optional[str] = Field(None, max_length=120)
    budget_head: Optional[str] = Field(None, max_length=120)
    funding_source: Optional[str] = Field(None, max_length=120)
    flight_required: bool = False
    train_required: bool = False
    hotel_required: bool = False
    local_transport_required: bool = False
    advance_required: bool = False
    est_travel_cost: Decimal = Field(Decimal("0"), ge=0)
    est_accommodation_cost: Decimal = Field(Decimal("0"), ge=0)
    est_local_cost: Decimal = Field(Decimal("0"), ge=0)
    est_food_cost: Decimal = Field(Decimal("0"), ge=0)
    est_misc_cost: Decimal = Field(Decimal("0"), ge=0)
    currency: str = Field("INR", min_length=3, max_length=3)
    attachments: List[TravelAttachment] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("trip_type")
    @classmethod
    def _trip_type_valid(cls, v):
        v = (v or "ROUND_TRIP").upper()
        if v not in TRIP_TYPES:
            raise ValueError(f"trip_type must be one of {', '.join(TRIP_TYPES)}")
        return v

    @field_validator("return_date")
    @classmethod
    def _return_after_departure(cls, v, info):
        dep = info.data.get("departure_date")
        if dep and v and v < dep:
            raise ValueError("return_date cannot be before departure_date")
        return v

    @model_validator(mode="after")
    def _validate_itinerary(self):
        if (self.trip_type or "ROUND_TRIP").upper() == "MULTI_CITY":
            legs = self.itinerary or []
            if len(legs) < 2:
                raise ValueError("A multi-city trip needs at least 2 legs")
            prev = None
            for lg in legs:
                if prev is not None and lg.departure_date < prev:
                    raise ValueError("Itinerary legs must be in departure-date order")
                prev = lg.departure_date
        return self


class TravelRequestAdminCreate(TravelRequestCreate):
    """Admin raising a request on behalf of an employee (lands pre-approved)."""
    employee_id: UUID


class TravelRequestUpdate(BaseModel):
    """Edit a DRAFT / RETURNED request — all optional."""
    model_config = ConfigDict(extra="ignore")
    purpose: Optional[str] = Field(None, min_length=3, max_length=2000)
    travel_type: Optional[str] = Field(None, max_length=40)
    category_id: Optional[UUID] = None
    priority: Optional[TravelPriority] = None
    trip_type: Optional[str] = None
    itinerary: Optional[List[TravelLeg]] = None
    from_location: Optional[str] = Field(None, min_length=1, max_length=160)
    to_location: Optional[str] = Field(None, min_length=1, max_length=160)
    from_location_id: Optional[UUID] = None
    to_location_id: Optional[UUID] = None
    to_city_category: Optional[CityCategory] = None
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    project_id: Optional[UUID] = None
    cost_center: Optional[str] = Field(None, max_length=120)
    budget_head: Optional[str] = Field(None, max_length=120)
    funding_source: Optional[str] = Field(None, max_length=120)
    flight_required: Optional[bool] = None
    train_required: Optional[bool] = None
    hotel_required: Optional[bool] = None
    local_transport_required: Optional[bool] = None
    advance_required: Optional[bool] = None
    est_travel_cost: Optional[Decimal] = Field(None, ge=0)
    est_accommodation_cost: Optional[Decimal] = Field(None, ge=0)
    est_local_cost: Optional[Decimal] = Field(None, ge=0)
    est_food_cost: Optional[Decimal] = Field(None, ge=0)
    est_misc_cost: Optional[Decimal] = Field(None, ge=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    attachments: Optional[List[TravelAttachment]] = None
    details: Optional[Dict[str, Any]] = None


class BookingMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    booking_number: str
    booking_type: BookingType
    vendor: Optional[str] = None
    travel_date: Optional[date] = None
    total_cost: Decimal
    status: BookingStatus


class AdvanceMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    advance_number: str
    advance_amount: Decimal
    approved_amount: Optional[Decimal] = None
    status: AdvanceStatus


class DaMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    travel_days: int
    daily_rate: Decimal
    eligible_da: Decimal
    approved_da: Optional[Decimal] = None
    city_category: CityCategory
    status: DaRecordStatus


class SettlementMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    settlement_number: str
    payable_amount: Decimal
    recoverable_amount: Decimal
    status: TravelSettlementStatus


class TravelRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    travel_reference_number: str
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    grade_id: Optional[UUID] = None
    grade_name: Optional[str] = None
    purpose: str
    travel_type: Optional[str] = None
    category_id: Optional[UUID] = None
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    category_icon: Optional[str] = None
    category_color: Optional[str] = None
    priority: TravelPriority
    trip_type: str = "ROUND_TRIP"
    itinerary: Optional[List[TravelLeg]] = None
    from_location: str
    to_location: str
    to_city_category: CityCategory
    departure_date: date
    return_date: date
    num_days: int
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    cost_center: Optional[str] = None
    budget_head: Optional[str] = None
    funding_source: Optional[str] = None
    flight_required: bool
    train_required: bool
    hotel_required: bool
    local_transport_required: bool
    advance_required: bool
    est_travel_cost: Decimal
    est_accommodation_cost: Decimal
    est_local_cost: Decimal
    est_food_cost: Decimal
    est_misc_cost: Decimal
    est_total_cost: Decimal
    currency: str
    attachments: List[TravelAttachment] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)
    status: TravelRequestStatus
    submitted_at: Optional[datetime] = None
    approval_steps: List[TravelStageState] = Field(default_factory=list)
    current_step: int = 0
    approved_at: Optional[datetime] = None
    approver_notes: Optional[str] = None
    return_reason: Optional[str] = None
    reject_reason: Optional[str] = None
    cancelled_reason: Optional[str] = None
    executed_at: Optional[datetime] = None
    attendance_synced: bool = False
    completed_at: Optional[datetime] = None
    created_at: datetime
    # Hydrated relations (detail view)
    bookings: List[BookingMini] = Field(default_factory=list)
    advance: Optional[AdvanceMini] = None
    da: Optional[DaMini] = None
    settlement: Optional[SettlementMini] = None
    # Actual-cost rollup (booked + reimbursed expenses + finalised DA) vs the estimate
    booked_cost: Optional[Decimal] = None
    actual_cost: Optional[Decimal] = None
    cost_variance: Optional[Decimal] = None
    # Convenience flags
    can_edit: bool = False
    can_withdraw: bool = False
    can_execute: bool = False
    can_complete: bool = False


class TravelRequestListResponse(BaseModel):
    items: List[TravelRequestResponse]
    total: int
    page: int
    limit: int
    total_pages: int
    unlinked: bool = False


# ═════════════════════════════════════════════════════════════════════════════
# Booking
# ═════════════════════════════════════════════════════════════════════════════

class BookingBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    booking_type: BookingType
    vendor: Optional[str] = Field(None, max_length=160)
    booking_date: Optional[date] = None
    travel_date: Optional[date] = None
    return_date: Optional[date] = None
    pnr_number: Optional[str] = Field(None, max_length=40)
    ticket_number: Optional[str] = Field(None, max_length=60)
    airline: Optional[str] = Field(None, max_length=80)
    train_number: Optional[str] = Field(None, max_length=40)
    seat_number: Optional[str] = Field(None, max_length=20)
    from_place: Optional[str] = Field(None, max_length=120)
    to_place: Optional[str] = Field(None, max_length=120)
    hotel_name: Optional[str] = Field(None, max_length=160)
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    num_nights: Optional[int] = Field(None, ge=0)
    booking_cost: Decimal = Field(Decimal("0"), ge=0)
    taxes: Decimal = Field(Decimal("0"), ge=0)
    currency: str = Field("INR", min_length=3, max_length=3)
    notes: Optional[str] = Field(None, max_length=1000)


class BookingCreate(BookingBase):
    travel_request_id: UUID
    status: BookingStatus = BookingStatus.BOOKED


class BookingSelfCreate(BookingBase):
    """Employee self-recording a booking for their own approved/active tour.

    ``travel_request_id`` comes from the path; the status is forced to BOOKED
    server-side (the employee is recording something they actually booked).
    """
    pass


class BookingUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    vendor: Optional[str] = Field(None, max_length=160)
    booking_date: Optional[date] = None
    travel_date: Optional[date] = None
    return_date: Optional[date] = None
    pnr_number: Optional[str] = Field(None, max_length=40)
    ticket_number: Optional[str] = Field(None, max_length=60)
    airline: Optional[str] = Field(None, max_length=80)
    train_number: Optional[str] = Field(None, max_length=40)
    seat_number: Optional[str] = Field(None, max_length=20)
    from_place: Optional[str] = Field(None, max_length=120)
    to_place: Optional[str] = Field(None, max_length=120)
    hotel_name: Optional[str] = Field(None, max_length=160)
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    num_nights: Optional[int] = Field(None, ge=0)
    booking_cost: Optional[Decimal] = Field(None, ge=0)
    taxes: Optional[Decimal] = Field(None, ge=0)
    status: Optional[BookingStatus] = None
    notes: Optional[str] = Field(None, max_length=1000)


class BookingResponse(BookingBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    booking_number: str
    travel_request_id: UUID
    travel_reference_number: Optional[str] = None
    employee_name: Optional[str] = None
    total_cost: Decimal
    status: BookingStatus
    created_by_id: Optional[UUID] = None
    created_at: datetime


class BookingListResponse(BaseModel):
    items: List[BookingResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═════════════════════════════════════════════════════════════════════════════
# Advance
# ═════════════════════════════════════════════════════════════════════════════

class AdvanceCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    travel_request_id: UUID
    advance_amount: Decimal = Field(..., gt=0)
    currency: str = Field("INR", min_length=3, max_length=3)
    purpose: Optional[str] = Field(None, max_length=1000)


class AdvanceDecisionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approved_amount: Optional[Decimal] = Field(None, gt=0)
    disbursement_method: Optional[TravelSettlementMethod] = None   # how it'll be paid; defaults PAYROLL
    note: Optional[str] = Field(None, max_length=1000)


class AdvanceRejectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=3, max_length=1000)


class AdvanceReleaseBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    period_month: Optional[int] = Field(None, ge=1, le=12)         # payroll method only
    period_year: Optional[int] = Field(None, ge=2000, le=2100)     # payroll method only
    disbursement_method: Optional[TravelSettlementMethod] = None   # override the approved method at release
    disbursement_reference: Optional[str] = Field(None, max_length=120)  # bank/cash/cheque ref
    note: Optional[str] = Field(None, max_length=500)


class AdvanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    advance_number: str
    travel_request_id: UUID
    travel_reference_number: Optional[str] = None
    employee_id: UUID
    employee_name: Optional[str] = None
    advance_amount: Decimal
    approved_amount: Optional[Decimal] = None
    currency: str
    purpose: Optional[str] = None
    status: AdvanceStatus
    disbursement_method: Optional[TravelSettlementMethod] = None
    disbursement_reference: Optional[str] = None
    approved_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    settled_at: Optional[datetime] = None
    recovered_amount: Optional[Decimal] = None
    reject_reason: Optional[str] = None
    payroll_ref: Optional[str] = None
    created_at: datetime


class AdvanceTripContext(BaseModel):
    """Trip facts surfaced alongside an advance in the detail drawer."""
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    num_days: Optional[int] = None
    trip_type: Optional[str] = None
    travel_type: Optional[str] = None
    priority: Optional[str] = None
    purpose: Optional[str] = None
    est_total_cost: Optional[Decimal] = None
    city_category: Optional[CityCategory] = None
    status: Optional[TravelRequestStatus] = None
    project_id: Optional[UUID] = None
    cost_center: Optional[str] = None
    budget_head: Optional[str] = None
    funding_source: Optional[str] = None


class AdvanceDetailResponse(AdvanceResponse):
    """Enriched single-advance payload for the admin detail drawer — adds the
    acting users, ceiling context, the parent trip, and the rest of the tour's
    money (DA, settlement, bookings) so the whole picture lives in one view."""
    requested_by_name: Optional[str] = None
    approved_by_name: Optional[str] = None
    released_by_name: Optional[str] = None
    notes: Optional[str] = None
    updated_at: Optional[datetime] = None
    advance_ceiling: Optional[Decimal] = None
    ceiling_source: Optional[str] = None      # "trip estimate" | "grade policy"
    department_name: Optional[str] = None
    trip: Optional[AdvanceTripContext] = None
    da: Optional[DaMini] = None
    settlement: Optional[SettlementMini] = None
    booking_count: int = 0
    booking_total: Decimal = Decimal("0")


class AdvanceListResponse(BaseModel):
    items: List[AdvanceResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═════════════════════════════════════════════════════════════════════════════
# DA
# ═════════════════════════════════════════════════════════════════════════════

class DaComputeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    city_category: Optional[CityCategory] = None   # override the request's destination tier
    travel_days: Optional[int] = Field(None, ge=0)  # override computed days
    daily_rate: Optional[Decimal] = Field(None, ge=0)  # manual override
    override_reason: Optional[str] = Field(None, max_length=500)  # required when the override exceeds the policy rate


class DaApproveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approved_da: Optional[Decimal] = Field(None, ge=0)   # approver may settle a lower DA
    note: Optional[str] = Field(None, max_length=500)


class DaRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    travel_request_id: UUID
    travel_reference_number: Optional[str] = None
    employee_id: UUID
    employee_name: Optional[str] = None
    grade_id: Optional[UUID] = None
    grade_name: Optional[str] = None
    city_category: CityCategory
    travel_days: int
    daily_rate: Decimal
    eligible_da: Decimal
    approved_da: Optional[Decimal] = None
    currency: str
    status: DaRecordStatus
    request_status: Optional[str] = None
    computed_at: datetime
    approved_at: Optional[datetime] = None
    payroll_ref: Optional[str] = None
    # How the DA was actually disbursed — lives on the linked settlement, NOT the
    # DA row. A DA is paid as part of the settlement: PAYROLL posts a payslip line;
    # CASH/BANK_TRANSFER/CHEQUE are paid directly (no payroll posting). Surfaced so
    # the UI can show the real method instead of assuming payroll.
    settlement_method: Optional[str] = None
    settlement_status: Optional[str] = None
    paid_at: Optional[datetime] = None


class DaListResponse(BaseModel):
    items: List[DaRecordResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═════════════════════════════════════════════════════════════════════════════
# Settlement
# ═════════════════════════════════════════════════════════════════════════════

class SettlementSubmitBody(BaseModel):
    """Employee submits post-travel expenses for settlement."""
    model_config = ConfigDict(extra="ignore")
    expense_lines: List[ExpenseLine] = Field(default_factory=list)
    notes: Optional[str] = Field(None, max_length=1000)


class SettlementVerifyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approved_expense: Optional[Decimal] = Field(None, ge=0)   # finance may approve a lower total
    note: Optional[str] = Field(None, max_length=1000)


class SettlementSettleBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    settlement_method: TravelSettlementMethod = TravelSettlementMethod.PAYROLL
    period_month: Optional[int] = Field(None, ge=1, le=12)
    period_year: Optional[int] = Field(None, ge=2000, le=2100)
    note: Optional[str] = Field(None, max_length=500)


class SettlementReverseBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=3, max_length=1000)


class SettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    settlement_number: str
    travel_request_id: UUID
    travel_reference_number: Optional[str] = None
    employee_id: UUID
    employee_name: Optional[str] = None
    expense_lines: List[ExpenseLine] = Field(default_factory=list)
    advance_received: Decimal
    total_expense: Decimal
    approved_expense: Decimal
    da_amount: Decimal
    payable_amount: Decimal
    recoverable_amount: Decimal
    currency: str
    status: TravelSettlementStatus
    settlement_method: Optional[TravelSettlementMethod] = None
    submitted_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    settled_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    payroll_ref: Optional[str] = None
    reversal_reason: Optional[str] = None
    created_at: datetime


class SettlementListResponse(BaseModel):
    items: List[SettlementResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═════════════════════════════════════════════════════════════════════════════
# Action bodies (request approval chain)
# ═════════════════════════════════════════════════════════════════════════════

class TravelDecisionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: TravelDecision   # APPROVED | REJECTED | RETURNED
    notes: Optional[str] = Field(None, max_length=1000)


class TravelReturnBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=3, max_length=1000)


class TravelEscalateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: Optional[str] = Field(None, max_length=1000)


class TravelCancelBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(None, max_length=1000)


class TravelExecuteBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sync_attendance: bool = True   # auto-mark ON_DUTY for the tour dates
    note: Optional[str] = Field(None, max_length=500)


class TravelBulkDecideBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: List[UUID] = Field(..., min_length=1)
    decision: TravelDecision
    notes: Optional[str] = Field(None, max_length=1000)


# ═════════════════════════════════════════════════════════════════════════════
# Dashboard / stats
# ═════════════════════════════════════════════════════════════════════════════

class TravelTypeCount(BaseModel):
    travel_type: Optional[str] = None
    count: int
    amount: Decimal


class DepartmentCount(BaseModel):
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    count: int
    amount: Decimal


class StatusCount(BaseModel):
    status: str
    count: int
    amount: Decimal


class MonthlyTrendPoint(BaseModel):
    month: str
    estimated: Decimal
    settled: Decimal
    count: int


class RouteCount(BaseModel):
    route: str
    count: int


class TravelStats(BaseModel):
    active_tours: int = 0
    pending_approvals: int = 0
    upcoming_travels: int = 0
    total_travel_cost: Decimal = Decimal("0")
    total_booked_cost: Decimal = Decimal("0")
    total_actual_cost: Decimal = Decimal("0")
    advances_outstanding: Decimal = Decimal("0")
    settlements_pending: int = 0
    da_payable: Decimal = Decimal("0")
    budget_utilization: float = 0.0
    total_requests: int = 0
    completed_tours: int = 0
    requests_this_month: int = 0
    avg_approval_days: Optional[float] = None
    by_status: List[StatusCount] = Field(default_factory=list)
    by_type: List[TravelTypeCount] = Field(default_factory=list)
    by_department: List[DepartmentCount] = Field(default_factory=list)
    monthly_trend: List[MonthlyTrendPoint] = Field(default_factory=list)
    top_routes: List[RouteCount] = Field(default_factory=list)
    settlement_split: Dict[str, Decimal] = Field(default_factory=dict)


# ═════════════════════════════════════════════════════════════════════════════
# Self-service summary
# ═════════════════════════════════════════════════════════════════════════════

class MyTravelSummary(BaseModel):
    in_flight: int = 0
    upcoming: int = 0
    completed: int = 0
    total_requests: int = 0
    advance_outstanding: Decimal = Decimal("0")
    pending_settlement: int = 0
    da_payable: Decimal = Decimal("0")
    estimated_spend_year: Decimal = Decimal("0")
    unlinked: bool = False


# ═════════════════════════════════════════════════════════════════════════════
# Calendar
# ═════════════════════════════════════════════════════════════════════════════

class CalendarEvent(BaseModel):
    id: UUID
    travel_reference_number: str
    employee_name: Optional[str] = None
    department: Optional[str] = None
    travel_type: Optional[str] = None
    from_location: str
    to_location: str
    departure_date: date
    return_date: date
    status: TravelRequestStatus
    priority: TravelPriority


class CalendarResponse(BaseModel):
    items: List[CalendarEvent] = Field(default_factory=list)


# ═════════════════════════════════════════════════════════════════════════════
# Audit
# ═════════════════════════════════════════════════════════════════════════════

class TravelAuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    entity_type: str
    entity_id: Optional[UUID] = None
    action: str
    travel_request_id: Optional[UUID] = None
    travel_reference_number: Optional[str] = None
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime


class TravelAuditListResponse(BaseModel):
    items: List[TravelAuditEntry]
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
