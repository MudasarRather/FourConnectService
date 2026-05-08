from sqlalchemy import Column, String, Float, DateTime, Date, Boolean, ForeignKey, Integer, Numeric, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum
from app.database import Base

# --- Enums ---
class PaymentStatus(str, enum.Enum):
    PENDING = "Pending"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"

class LedgerTransactionType(str, enum.Enum):
    DEBIT = "Debit"
    CREDIT = "Credit"

class DocCategory(str, enum.Enum):
    INVOICE = "Invoice"
    CONTRACT = "Contract"
    PO = "Purchase Order"
    RECEIPT = "Receipt"
    OTHER = "Other"

# --- Models ---

class ProjectPayment(Base):
    __tablename__ = "project_payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    expense_id = Column(UUID(as_uuid=True), ForeignKey("expenses.id"), nullable=True)
    
    vendor_name = Column(String, nullable=False)
    amount_paid = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), default="USD")
    payment_mode = Column(String, nullable=True)
    transaction_ref = Column(String, nullable=True)
    payment_date = Column(Date, nullable=False)
    status = Column(String, default=PaymentStatus.COMPLETED)
    
    # Extended fields
    payment_id = Column(String, nullable=True)
    milestone_ids = Column(JSON, nullable=True)
    milestone_budget_sum = Column(Numeric(14, 2), default=0.00)
    contract_work_order_no = Column(String, nullable=True)
    client_type = Column(String, nullable=True)
    client_department = Column(String, nullable=True)
    payment_type = Column(String, nullable=True)
    payment_category = Column(String, nullable=True)
    invoice_number = Column(String, nullable=True)
    invoice_date = Column(Date, nullable=True)
    invoice_amount_gross = Column(Numeric(14, 2), default=0.00)
    exchange_rate = Column(Numeric(10, 4), default=1.0)
    converted_amount = Column(Numeric(14, 2), nullable=True)
    tax_type = Column(String, nullable=True)
    gst_percent = Column(Numeric(5, 2), default=0.00)
    tds_percent = Column(Numeric(5, 2), default=0.00)
    other_deductions = Column(Numeric(14, 2), default=0.00)
    other_deductions_desc = Column(String, nullable=True)
    net_receivable_amount = Column(Numeric(14, 2), default=0.00)
    retention_amount = Column(Numeric(14, 2), default=0.00)
    retention_percent = Column(Numeric(5, 2), default=0.00)
    balance_remaining = Column(Numeric(14, 2), default=0.00)
    bank_name = Column(String, nullable=True)
    account_holder_name = Column(String, nullable=True)
    account_number = Column(String, nullable=True)
    ifsc_swift_code = Column(String, nullable=True)
    cheque_no = Column(String, nullable=True)
    utr_transaction_ref = Column(String, nullable=True)
    attachments = Column(JSON, nullable=True)
    receipt_pdf_path = Column(String, nullable=True)  # Path to generated payment receipt PDF

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Relationships
    project = relationship("Project", backref="payments")
    expense = relationship("Expense", backref="project_payment")
    created_by = relationship("User")


class ProjectFinancialLedger(Base):
    __tablename__ = "project_financial_ledger"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    
    transaction_date = Column(DateTime(timezone=True), server_default=func.now())
    transaction_type = Column(String, nullable=False)
    description = Column(String, nullable=True)
    
    debit = Column(Numeric(14, 2), default=0.00)
    credit = Column(Numeric(14, 2), default=0.00)
    balance = Column(Numeric(14, 2), default=0.00)
    
    reference_id = Column(UUID(as_uuid=True), nullable=True)
    reference_type = Column(String, nullable=True)
    reversal_reference_id = Column(UUID(as_uuid=True), ForeignKey("project_financial_ledger.id"), nullable=True)

    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Relationships
    project = relationship("Project", backref="ledger_entries")


class ProjectFinancialForecast(Base):
    __tablename__ = "project_financial_forecast"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), primary_key=True)
    
    planned_cost = Column(Numeric(14, 2), default=0.00)
    committed_cost = Column(Numeric(14, 2), default=0.00)
    actual_cost = Column(Numeric(14, 2), default=0.00)
    
    forecast_total_cost = Column(Numeric(14, 2), default=0.00)
    expected_overrun = Column(Numeric(14, 2), default=0.00)
    
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    project = relationship("Project", backref="financial_forecast", uselist=False)


class ProjectFinancialDocument(Base):
    __tablename__ = "project_financial_documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    
    category = Column(String, default=DocCategory.OTHER)
    file_url = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    file_size_bytes = Column(Integer, default=0)
    
    uploaded_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    
    project = relationship("Project", backref="financial_documents")


class ProjectBudget(Base):
    __tablename__ = "project_budgets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    
    budget_type = Column(String, default="Initial") # Initial, Revision
    funding_source = Column(String, nullable=True) # Internal, Grant, Client
    cost_center = Column(String, nullable=True)
    
    allocated_amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), default="USD", nullable=False)
    
    revision_number = Column(Integer, default=1)
    status = Column(String, default="Draft") # Draft, Pending, Approved, Rejected
    effective_date = Column(Date, nullable=True)
    
    is_locked = Column(Boolean, default=False)
    justification = Column(String, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Relationships
    project = relationship("Project", backref="budgets")


class ProjectApprovalRequest(Base):
    __tablename__ = "project_approval_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    
    request_type = Column(String, nullable=False) # Budget_Revision, Expense_Approval
    related_entity_id = Column(UUID(as_uuid=True), nullable=True) # ID of budget or expense
    
    status = Column(String, default="Pending") # Pending, Approved, Rejected
    requested_amount = Column(Numeric(14, 2), nullable=True)
    
    justification = Column(String, nullable=True)
    comments = Column(String, nullable=True)
    
    requested_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    
    actioned_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actioned_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    project = relationship("Project", backref="approval_requests")

