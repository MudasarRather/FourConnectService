from typing import Optional, List, Any, Union
from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel, Field

# --- Payment Schemas ---

class ProjectPaymentBase(BaseModel):
    vendor_name: str
    amount_paid: Optional[float] = 0.0
    currency: str = "USD"
    payment_mode: Optional[str] = None
    transaction_ref: Optional[str] = None
    payment_date: date
    status: str = "Pending"

class ProjectPaymentCreate(ProjectPaymentBase):
    expense_id: Optional[UUID] = None
    payment_id: Optional[str] = None # Manual override or generation
    # Extended fields (optional for backward compatibility)
    milestone_ids: Optional[List[str]] = None
    milestone_budget_sum: Optional[float] = 0.0
    contract_work_order_no: Optional[str] = None
    client_type: Optional[str] = "Private"
    client_department: Optional[str] = None
    payment_type: Optional[str] = "Running Bill"
    payment_category: Optional[str] = "Services"
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    invoice_amount_gross: Optional[float] = 0.0
    exchange_rate: Optional[float] = 1.0
    converted_amount: Optional[float] = None
    tax_type: Optional[str] = "None"
    gst_percent: Optional[float] = 0.0
    tds_percent: Optional[float] = 0.0
    other_deductions: Optional[float] = 0.0
    other_deductions_desc: Optional[str] = None
    net_receivable_amount: Optional[float] = 0.0
    retention_amount: Optional[float] = 0.0
    retention_percent: Optional[float] = 0.0
    balance_remaining: Optional[float] = 0.0
    bank_name: Optional[str] = None
    account_holder_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_swift_code: Optional[str] = None
    cheque_no: Optional[str] = None
    utr_transaction_ref: Optional[str] = None
    attachments: Optional[List[Any]] = None

class ProjectPaymentUpdate(ProjectPaymentCreate):
    pass

class UserSummary(BaseModel):
    id: UUID
    full_name: str
    email: str
    
    class Config:
        from_attributes = True

class ProjectPaymentResponse(BaseModel):
    id: UUID
    project_id: UUID
    vendor_name: str
    amount_paid: Optional[float] = 0.0
    currency: Optional[str] = "USD"
    payment_mode: Optional[str] = None
    transaction_ref: Optional[str] = None
    payment_date: Optional[date] = None
    status: Optional[str] = "Pending"
    expense_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    created_by_id: Optional[UUID] = None
    created_by: Optional[UserSummary] = None
    
    # Extended fields - all optional for backward compatibility
    payment_id: Optional[str] = None
    milestone_ids: Optional[Any] = None
    milestone_budget_sum: Optional[float] = None
    contract_work_order_no: Optional[str] = None
    client_type: Optional[str] = None
    client_department: Optional[str] = None
    payment_type: Optional[str] = None
    payment_category: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    invoice_amount_gross: Optional[float] = None
    exchange_rate: Optional[float] = None
    converted_amount: Optional[float] = None
    tax_type: Optional[str] = None
    gst_percent: Optional[float] = None
    tds_percent: Optional[float] = None
    other_deductions: Optional[float] = None
    other_deductions_desc: Optional[str] = None
    net_receivable_amount: Optional[float] = None
    retention_amount: Optional[float] = None
    retention_percent: Optional[float] = None
    balance_remaining: Optional[float] = None
    bank_name: Optional[str] = None
    account_holder_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_swift_code: Optional[str] = None
    cheque_no: Optional[str] = None
    utr_transaction_ref: Optional[str] = None
    attachments: Optional[Any] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# --- Ledger Schemas ---

class ProjectFinancialLedgerResponse(BaseModel):
    id: UUID
    project_id: UUID
    transaction_date: datetime
    transaction_type: str
    description: Optional[str] = None
    debit: float
    credit: float
    balance: float
    reference_id: Optional[UUID] = None
    reference_type: Optional[str] = None
    created_by_id: Optional[UUID] = None

    class Config:
        from_attributes = True

# --- Forecast Schemas ---

class ProjectFinancialForecastBase(BaseModel):
    planned_cost: float = 0.0
    committed_cost: float = 0.0
    actual_cost: float = 0.0
    forecast_total_cost: float = 0.0
    expected_overrun: float = 0.0

class ProjectFinancialForecastUpdate(BaseModel):
    forecast_total_cost: Optional[float] = None

class ProjectFinancialForecastResponse(ProjectFinancialForecastBase):
    project_id: UUID
    last_updated: datetime

    class Config:
        from_attributes = True

# --- Aggregate Schemas ---

class FinancialSummary(BaseModel):
    total_budget: float
    total_spent: float
    remaining_budget: float
    milestone_budget: float
    currency: str
    burn_rate: float
    forecast_variance: float
    budget_utilized_percentage: float = 0.0
    milestone_allocation_percentage: float = 0.0

# --- Document Schemas ---

class FinancialDocumentBase(BaseModel):
    category: str = "Other"
    file_name: str
    file_url: str
    file_size_bytes: int = 0

class FinancialDocumentCreate(FinancialDocumentBase):
    pass

class FinancialDocumentResponse(FinancialDocumentBase):
    id: Optional[Union[str, UUID]] = None
    project_id: Optional[UUID] = None
    uploaded_by_id: Optional[UUID] = None
    uploaded_by_name: Optional[str] = None  # Author's display name
    document_id: Optional[str] = None  # Formatted ID like DOC-XXXX
    uploaded_at: Optional[datetime] = None

    class Config:
        from_attributes = True
