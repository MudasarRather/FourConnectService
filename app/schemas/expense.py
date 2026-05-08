from pydantic import BaseModel, Field
from typing import Optional, List, Any
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal


class AllocationItem(BaseModel):
    category: Optional[str] = None
    cost_center: Optional[str] = None
    percentage: float = 0
    amount: float = 0


class AttachmentItem(BaseModel):
    file_name: str
    file_url: str
    doc_type: Optional[str] = "other"
    size: Optional[int] = 0


class ExpenseCreate(BaseModel):
    # Basic Info
    title: str
    category: str
    expense_date: date
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    department: Optional[str] = None
    cost_center: Optional[str] = None
    expense_type: Optional[str] = None
    priority: str = "medium"
    description: Optional[str] = None

    # Financial
    amount: float
    currency: str = "USD"
    exchange_rate: float = 1.0
    base_amount: Optional[float] = None
    payment_method: str = "cash"
    payment_status: str = "unpaid"
    expense_status: str = "draft"
    is_recurring: bool = False
    installment_count: Optional[int] = None

    # Vendor
    vendor_name: Optional[str] = None
    vendor_type: Optional[str] = None
    vendor_contact: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    purchase_order_ref: Optional[str] = None

    # Tax
    tax_applicable: bool = False
    tax_type: Optional[str] = None
    tax_percentage: Optional[float] = 0
    tax_amount: Optional[float] = 0
    withholding_tax: Optional[float] = 0
    total_after_tax: Optional[float] = None

    # Allocation
    allocation_type: str = "full"
    allocations: Optional[List[dict]] = None

    # Attachments
    attachments: Optional[List[dict]] = None

    # Notes
    notes: Optional[str] = None
    is_internal_note: bool = False


class ExpenseUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    expense_date: Optional[date] = None
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    department: Optional[str] = None
    cost_center: Optional[str] = None
    expense_type: Optional[str] = None
    priority: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    exchange_rate: Optional[float] = None
    base_amount: Optional[float] = None
    payment_method: Optional[str] = None
    payment_status: Optional[str] = None
    expense_status: Optional[str] = None
    is_recurring: Optional[bool] = None
    installment_count: Optional[int] = None
    vendor_name: Optional[str] = None
    vendor_type: Optional[str] = None
    vendor_contact: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    purchase_order_ref: Optional[str] = None
    tax_applicable: Optional[bool] = None
    tax_type: Optional[str] = None
    tax_percentage: Optional[float] = None
    tax_amount: Optional[float] = None
    withholding_tax: Optional[float] = None
    total_after_tax: Optional[float] = None
    allocation_type: Optional[str] = None
    allocations: Optional[List[dict]] = None
    attachments: Optional[List[dict]] = None
    notes: Optional[str] = None
    is_internal_note: Optional[bool] = None


class ExpenseUserSubset(BaseModel):
    id: Any
    email: str
    full_name: Optional[str] = None
    
    class Config:
        from_attributes = True

class ExpenseResponse(BaseModel):
    id: Any
    title: str
    category: str
    expense_date: date
    project_id: Optional[Any] = None
    task_id: Optional[Any] = None
    department: Optional[str] = None
    cost_center: Optional[str] = None
    expense_type: Optional[str] = None
    priority: str
    description: Optional[str] = None

    amount: float
    currency: str
    exchange_rate: float
    base_amount: Optional[float] = None
    payment_method: str
    payment_status: str
    expense_status: str
    is_recurring: bool
    installment_count: Optional[int] = None

    vendor_name: Optional[str] = None
    vendor_type: Optional[str] = None
    vendor_contact: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    purchase_order_ref: Optional[str] = None

    tax_applicable: bool
    tax_type: Optional[str] = None
    tax_percentage: Optional[float] = None
    tax_amount: Optional[float] = None
    withholding_tax: Optional[float] = None
    total_after_tax: Optional[float] = None

    allocation_type: str
    allocations: Optional[Any] = None
    attachments: Optional[Any] = None

    notes: Optional[str] = None
    is_internal_note: bool
    rejection_reason: Optional[str] = None

    approval_status: Optional[str] = None
    approved_by_id: Optional[Any] = None
    approval_level: Optional[int] = None

    user_id: Any
    created_at: datetime
    updated_at: datetime
    
    created_by: Optional[ExpenseUserSubset] = None

    # Reversal fields
    is_reversal: bool = False
    reversal_parent_id: Optional[Any] = None
    reversal_type: Optional[str] = None
    reversal_reason: Optional[str] = None
    reversal_date: Optional[datetime] = None
    reversed_amount: Optional[float] = None
    is_fully_reversed: bool = False

    class Config:
        from_attributes = True


class DuplicateCheckRequest(BaseModel):
    invoice_number: str
    vendor_name: Optional[str] = None


class DuplicateCheckResponse(BaseModel):
    is_duplicate: bool
    matching_expense_id: Optional[str] = None
    matching_title: Optional[str] = None


class RejectRequest(BaseModel):
    reason: str


class ReversalPreviewResponse(BaseModel):
    expense_id: Any
    title: str
    vendor_name: Optional[str] = None
    category: str
    original_amount: float
    already_reversed_amount: float
    remaining_reversible_amount: float
    expense_status: str
    expense_date: Optional[date] = None
    currency: str = "INR"
    is_fully_reversed: bool = False


class ReversalRequest(BaseModel):
    reversal_type: str  # "FULL" or "PARTIAL"
    reversed_amount: Optional[float] = None  # Required if PARTIAL
    reason_category: str  # Wrong Amount, Duplicate Entry, etc.
    reason_details: Optional[str] = None
    refund_received: bool = False
    refund_date: Optional[date] = None
    refund_mode: Optional[str] = None
    refund_reference: Optional[str] = None
    attachments: Optional[List[dict]] = None


class ReversalHistoryItem(BaseModel):
    id: Any
    reversal_type: Optional[str] = None
    reversed_amount: Optional[float] = None
    reversal_reason: Optional[str] = None
    reversal_date: Optional[datetime] = None
    expense_status: str
    created_at: datetime
    created_by: Optional[ExpenseUserSubset] = None

    class Config:
        from_attributes = True
