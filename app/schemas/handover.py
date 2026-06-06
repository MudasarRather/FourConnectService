from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime
from uuid import UUID

class UserCompactDpr(BaseModel):
    id: UUID
    full_name: Optional[str] = None
    class Config:
        from_attributes = True

# --- Child Schemas ---

class HandoverStakeholderBase(BaseModel):
    role: str
    name: str
    organization: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

class HandoverStakeholderCreate(HandoverStakeholderBase): pass
class HandoverStakeholderResponse(HandoverStakeholderBase):
    id: UUID
    class Config: from_attributes = True

class HandoverModuleBase(BaseModel):
    module_name: str
    description: Optional[str] = None
    status: Optional[str] = "Delivered"
    delivery_date: Optional[date] = None

class HandoverModuleCreate(HandoverModuleBase): pass
class HandoverModuleResponse(HandoverModuleBase):
    id: UUID
    class Config: from_attributes = True

class HandoverServerBase(BaseModel):
    server_name: str
    ip_address: Optional[str] = None
    role: Optional[str] = None
    os: Optional[str] = None
    location: Optional[str] = None
    hosting_type: Optional[str] = None

class HandoverServerCreate(HandoverServerBase): pass
class HandoverServerResponse(HandoverServerBase):
    id: UUID
    class Config: from_attributes = True

class HandoverAssetBase(BaseModel):
    asset_name: str
    model: Optional[str] = None
    serial_number: Optional[str] = None
    quantity: Optional[int] = 1
    assigned_to: Optional[str] = None
    location: Optional[str] = None

class HandoverAssetCreate(HandoverAssetBase): pass
class HandoverAssetResponse(HandoverAssetBase):
    id: UUID
    class Config: from_attributes = True

class HandoverCredentialBase(BaseModel):
    system: str
    username: Optional[str] = None
    access_level: Optional[str] = None
    delivered_to: Optional[str] = None
    password: Optional[str] = None

class HandoverCredentialCreate(HandoverCredentialBase): pass
class HandoverCredentialResponse(HandoverCredentialBase):
    id: UUID
    class Config: from_attributes = True

class HandoverDocumentBase(BaseModel):
    document_name: str
    doc_type: Optional[str] = None
    version: Optional[str] = None
    link_url: Optional[str] = None

class HandoverDocumentCreate(HandoverDocumentBase): pass
class HandoverDocumentResponse(HandoverDocumentBase):
    id: UUID
    class Config: from_attributes = True

class HandoverTrainingBase(BaseModel):
    topic: str
    trainer: Optional[str] = None
    training_date: Optional[date] = None
    participants: Optional[str] = None
    training_mode: Optional[str] = None
    completion_status: Optional[str] = "Pending"

class HandoverTrainingCreate(HandoverTrainingBase): pass
class HandoverTrainingResponse(HandoverTrainingBase):
    id: UUID
    class Config: from_attributes = True

class HandoverFinancialBase(BaseModel):
    invoice_no: str
    invoice_date: Optional[date] = None
    amount: float
    status: Optional[str] = "Pending"

class HandoverFinancialCreate(HandoverFinancialBase): pass
class HandoverFinancialResponse(HandoverFinancialBase):
    id: UUID
    class Config: from_attributes = True

class HandoverIssueBase(BaseModel):
    issue_type: Optional[str] = None
    issue_desc: str
    impact: Optional[str] = None
    owner: Optional[str] = None
    expected_resolution: Optional[str] = None

class HandoverIssueCreate(HandoverIssueBase): pass
class HandoverIssueResponse(HandoverIssueBase):
    id: UUID
    class Config: from_attributes = True

class HandoverApprovalBase(BaseModel):
    party: str
    name: str
    designation: Optional[str] = None
    signature_date: Optional[date] = None
    has_signed: Optional[bool] = False

class HandoverApprovalCreate(HandoverApprovalBase): pass
class HandoverApprovalResponse(HandoverApprovalBase):
    id: UUID
    class Config: from_attributes = True

class HandoverDeliverableBase(BaseModel):
    item_name: str
    category: Optional[str] = None
    status: Optional[str] = "Delivered"
    client_remark: Optional[str] = None

class HandoverDeliverableCreate(HandoverDeliverableBase): pass
class HandoverDeliverableResponse(HandoverDeliverableBase):
    id: UUID
    class Config: from_attributes = True

class HandoverFeedbackBase(BaseModel):
    criterion: str
    rating: Optional[str] = None
    comment: Optional[str] = None

class HandoverFeedbackCreate(HandoverFeedbackBase): pass
class HandoverFeedbackResponse(HandoverFeedbackBase):
    id: UUID
    class Config: from_attributes = True


# --- Main Handover ---

class HandoverBase(BaseModel):
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    project_code: Optional[str] = None
    client_organization: Optional[str] = None
    department: Optional[str] = None
    start_date: Optional[date] = None
    completion_date: Optional[date] = None
    project_manager: Optional[str] = None
    project_summary: Optional[str] = None
    architecture_description: Optional[str] = None
    tech_stack_backend: Optional[str] = None
    tech_stack_frontend: Optional[str] = None
    tech_stack_database: Optional[str] = None
    architecture_diagram_url: Optional[str] = None
    backup_frequency: Optional[str] = None
    backup_location: Optional[str] = None
    backup_type: Optional[str] = None
    monitoring_tools: Optional[str] = None
    alert_system: Optional[str] = None
    dashboard_url: Optional[str] = None
    maintenance_schedule: Optional[str] = None
    patch_management_plan: Optional[str] = None
    sla_id: Optional[UUID] = None
    support_start_date: Optional[date] = None
    support_end_date: Optional[date] = None
    support_type: Optional[str] = None
    total_project_value: Optional[float] = None
    amount_received: Optional[float] = 0.0
    pending_amount: Optional[float] = 0.0
    currency: Optional[str] = "INR"
    system_vendor: Optional[str] = None
    client_remarks: Optional[str] = None
    status: Optional[str] = "Draft"
    rejection_reason: Optional[str] = None
    version: Optional[str] = "v1.0"


class HandoverCreate(HandoverBase):
    stakeholders: List[HandoverStakeholderCreate] = []
    modules: List[HandoverModuleCreate] = []
    assets: List[HandoverAssetCreate] = []
    servers: List[HandoverServerCreate] = []
    credentials: List[HandoverCredentialCreate] = []
    documents: List[HandoverDocumentCreate] = []
    training: List[HandoverTrainingCreate] = []
    financial_invoices: List[HandoverFinancialCreate] = []
    issues: List[HandoverIssueCreate] = []
    approvals: List[HandoverApprovalCreate] = []
    deliverables: List[HandoverDeliverableCreate] = []
    feedback: List[HandoverFeedbackCreate] = []


class HandoverResponse(HandoverBase):
    id: UUID
    created_by_id: Optional[UUID] = None
    created_by: Optional[UserCompactDpr] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    stakeholders: List[HandoverStakeholderResponse] = []
    modules: List[HandoverModuleResponse] = []
    assets: List[HandoverAssetResponse] = []
    servers: List[HandoverServerResponse] = []
    credentials: List[HandoverCredentialResponse] = []
    documents: List[HandoverDocumentResponse] = []
    training: List[HandoverTrainingResponse] = []
    financial_invoices: List[HandoverFinancialResponse] = []
    issues: List[HandoverIssueResponse] = []
    approvals: List[HandoverApprovalResponse] = []
    deliverables: List[HandoverDeliverableResponse] = []
    feedback: List[HandoverFeedbackResponse] = []
    class Config:
        from_attributes = True

class HandoverUpdate(HandoverCreate):
    pass
