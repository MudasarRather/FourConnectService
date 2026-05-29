"""HR Employee Documents schemas (Pydantic v2).

Request/response shapes for the unified employee-documents hub. List responses
paginate as { items, total, page, limit, total_pages }. Sensitive document
numbers are masked unless a superuser explicitly requests a reveal.
"""
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.hr.employee_document import (
    DocumentCategory, DocVerificationStatus, DocSource, DocTemplateType,
)
from app.models.hr.document_request import (
    DocumentRequestType, DocumentRequestStatus,
)


# ──────────────────────────────────────────────────────────────────────────────
# Employee Document
# ──────────────────────────────────────────────────────────────────────────────

class EmployeeDocumentBase(BaseModel):
    employee_id: UUID
    category: DocumentCategory
    doc_type: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=200)
    document_number: Optional[str] = Field(default=None, max_length=120)
    issued_by: Optional[str] = Field(default=None, max_length=200)
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    attributes: Optional[Dict[str, Any]] = None
    is_confidential: Optional[bool] = None


class EmployeeDocumentCreate(EmployeeDocumentBase):
    model_config = ConfigDict(extra="ignore")


class EmployeeDocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: Optional[DocumentCategory] = None
    doc_type: Optional[str] = None
    title: Optional[str] = None
    document_number: Optional[str] = None
    issued_by: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    attributes: Optional[Dict[str, Any]] = None
    is_confidential: Optional[bool] = None


class EmployeeDocumentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    action: str
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime


class EmployeeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    category: DocumentCategory
    doc_type: str
    title: str
    document_number: Optional[str] = None
    document_number_masked: bool = False
    issued_by: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    verification_status: DocVerificationStatus
    verified_by_user_id: Optional[UUID] = None
    verified_by_name: Optional[str] = None
    verified_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    source: DocSource
    onboarding_document_id: Optional[UUID] = None
    is_confidential: bool = False
    is_archived: bool = False
    # Resolved file info (from the linked DriveDocument)
    drive_document_id: Optional[UUID] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    has_file: bool = False
    # Enriched
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    days_to_expiry: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class EmployeeDocumentDetailResponse(EmployeeDocumentResponse):
    events: List[EmployeeDocumentEventResponse] = Field(default_factory=list)


class EmployeeDocumentListResponse(BaseModel):
    items: List[EmployeeDocumentResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ──────────────────────────────────────────────────────────────────────────────
# Actions
# ──────────────────────────────────────────────────────────────────────────────

class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: Optional[str] = None
    expiry_date: Optional[date] = None


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=1, max_length=1000)


class ResubmitBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(default=None, max_length=1000)


class DeleteBody(BaseModel):
    """Optional context attached to a soft-delete — captured into the audit trail."""
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(default=None, max_length=2000)
    reason_category: Optional[str] = Field(default=None, max_length=64)


class BulkVerifyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: List[UUID] = Field(..., min_length=1)
    action: str = Field(..., pattern="^(verify|reject|resubmit)$")
    reason: Optional[str] = None


class DownloadTokenResponse(BaseModel):
    token: str
    expires_in: int
    url: str


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────────────────────

class ChartPoint(BaseModel):
    label: str
    value: int
    key: Optional[str] = None


class EdocDashboardResponse(BaseModel):
    # Widgets
    total_documents: int
    pending_verification: int
    expiring_soon: int          # expiry within 90 days (and not expired)
    missing_documents: int      # count of (employee × mandatory doc) gaps
    uploaded_this_month: int
    compliance_pending: int
    contract_expiry: int        # contracts expiring within 90 days
    archived_documents: int
    # Charts
    category_distribution: List[ChartPoint]
    expiry_timeline: List[ChartPoint]     # buckets: 0-30 / 31-60 / 61-90 / expired
    department_missing: List[ChartPoint]
    verification_status: List[ChartPoint]


# ──────────────────────────────────────────────────────────────────────────────
# Templates (table now, UI in Pass 2)
# ──────────────────────────────────────────────────────────────────────────────

class TemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    template_type: DocTemplateType
    description: Optional[str] = None
    body: Optional[str] = None
    placeholders: Optional[List[str]] = None
    is_active: bool = True


class TemplateCreate(TemplateBase):
    model_config = ConfigDict(extra="ignore")


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    template_type: Optional[DocTemplateType] = None
    description: Optional[str] = None
    body: Optional[str] = None
    placeholders: Optional[List[str]] = None
    is_active: Optional[bool] = None


class TemplateResponse(TemplateBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


class TemplateListResponse(BaseModel):
    items: List[TemplateResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ──────────────────────────────────────────────────────────────────────────────
# Employee self-service: My Documents Summary
# ──────────────────────────────────────────────────────────────────────────────

class MyDocCategoryBreakdown(BaseModel):
    """Per-category snapshot for the self-service hero KPI tiles."""
    category: DocumentCategory
    total: int = 0
    pending: int = 0
    verified: int = 0
    rejected: int = 0
    resubmit_required: int = 0
    expired: int = 0
    expiring_soon: int = 0   # within 90 days
    is_mandatory: bool = False
    missing_required_types: List[str] = Field(default_factory=list)


class MyDocumentsSummaryResponse(BaseModel):
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    total_documents: int = 0
    pending_count: int = 0
    verified_count: int = 0
    rejected_count: int = 0
    expiring_soon_count: int = 0
    expired_count: int = 0
    by_category: List[MyDocCategoryBreakdown] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Document Requests (employee → HR)
# ──────────────────────────────────────────────────────────────────────────────

class DocumentRequestCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    request_type: DocumentRequestType
    custom_title: Optional[str] = Field(default=None, max_length=160)
    reason: str = Field(..., min_length=3, max_length=2000)
    notes: Optional[str] = Field(default=None, max_length=2000)
    purpose: Optional[str] = Field(default=None, max_length=120)


class DocumentRequestDecision(BaseModel):
    """Admin-side patch: advance the lifecycle (in_progress / fulfilled / rejected)."""
    model_config = ConfigDict(extra="ignore")
    status: DocumentRequestStatus
    decision_notes: Optional[str] = Field(default=None, max_length=2000)
    fulfilled_doc_id: Optional[UUID] = None
    assigned_to_user_id: Optional[UUID] = None


class DocumentRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    request_type: DocumentRequestType
    custom_title: Optional[str] = None
    reason: str
    notes: Optional[str] = None
    purpose: Optional[str] = None
    status: DocumentRequestStatus
    assigned_to_user_id: Optional[UUID] = None
    assigned_to_name: Optional[str] = None
    fulfilled_doc_id: Optional[UUID] = None
    decided_by_user_id: Optional[UUID] = None
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_notes: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class DocumentRequestListResponse(BaseModel):
    items: List[DocumentRequestResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ──────────────────────────────────────────────────────────────────────────────
# Self-service upload — multipart form fields (parsed via Form(...))
# ──────────────────────────────────────────────────────────────────────────────

class MyUploadMeta(BaseModel):
    """Parsed metadata for an employee self-upload (form-encoded alongside the file)."""
    model_config = ConfigDict(extra="ignore")
    category: DocumentCategory
    doc_type: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=200)
    document_number: Optional[str] = Field(default=None, max_length=120)
    issued_by: Optional[str] = Field(default=None, max_length=200)
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
