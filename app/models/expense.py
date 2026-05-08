import uuid
import enum
from sqlalchemy import Column, String, Text, Numeric, DateTime, Date, ForeignKey, Enum, Boolean, Integer, Float
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class ExpenseStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    VOID = "void"
    PENDING_REVERSAL = "pending_reversal"
    REVERSED = "reversed"


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    BANK = "bank"
    CARD = "card"
    ONLINE = "online"


class ExpensePaymentStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PAID = "paid"
    PARTIAL = "partial"


class VendorType(str, enum.Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class AllocationType(str, enum.Enum):
    FULL = "full"
    SPLIT = "split"


class Priority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Expense(Base):
    """Comprehensive expense model"""

    __tablename__ = "expenses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # ── Basic Info ──
    title = Column(String, nullable=False)
    category = Column(String, nullable=False)
    expense_date = Column(Date, nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    task_id = Column(UUID(as_uuid=True), nullable=True)
    department = Column(String, nullable=True)
    cost_center = Column(String, nullable=True)
    expense_type = Column(String, nullable=True)
    priority = Column(Enum(Priority), default=Priority.MEDIUM, nullable=False)
    description = Column(Text, nullable=True)

    # ── Financial Details ──
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), default="USD", nullable=False)
    exchange_rate = Column(Float, default=1.0, nullable=False)
    base_amount = Column(Numeric(12, 2), nullable=True)  # auto-calculated
    payment_method = Column(Enum(PaymentMethod), default=PaymentMethod.CASH, nullable=False)
    payment_status = Column(Enum(ExpensePaymentStatus), default=ExpensePaymentStatus.UNPAID, nullable=False)
    expense_status = Column(Enum(ExpenseStatus), default=ExpenseStatus.DRAFT, nullable=False)
    is_recurring = Column(Boolean, default=False, nullable=False)
    installment_count = Column(Integer, nullable=True)

    # ── Vendor ──
    vendor_name = Column(String, nullable=True)
    vendor_type = Column(Enum(VendorType), nullable=True)
    vendor_contact = Column(String, nullable=True)
    vendor_tax_id = Column(String, nullable=True)
    invoice_number = Column(String, nullable=True)
    invoice_date = Column(Date, nullable=True)
    purchase_order_ref = Column(String, nullable=True)

    # ── Tax ──
    tax_applicable = Column(Boolean, default=False, nullable=False)
    tax_type = Column(String, nullable=True)  # GST, VAT, custom
    tax_percentage = Column(Float, default=0.0, nullable=True)
    tax_amount = Column(Numeric(12, 2), default=0, nullable=True)
    withholding_tax = Column(Float, default=0.0, nullable=True)
    total_after_tax = Column(Numeric(12, 2), nullable=True)

    # ── Allocation ──
    allocation_type = Column(Enum(AllocationType), default=AllocationType.FULL, nullable=False)
    allocations = Column(JSON, nullable=True)  # [{category, cost_center, percentage, amount}]

    # ── Attachments ──
    attachments = Column(JSON, nullable=True)  # [{file_name, file_url, doc_type, size}]

    # ── Notes ──
    notes = Column(Text, nullable=True)
    is_internal_note = Column(Boolean, default=False, nullable=False)
    rejection_reason = Column(Text, nullable=True)

    # ── Approval ──
    approval_status = Column(String, default="pending", nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approval_level = Column(Integer, default=1, nullable=True)

    # ── Metadata ──
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # ── Reversal ──
    is_reversal = Column(Boolean, default=False, nullable=False)
    reversal_parent_id = Column(UUID(as_uuid=True), ForeignKey("expenses.id"), nullable=True)
    reversal_type = Column(String(20), nullable=True)  # FULL or PARTIAL
    reversal_reason = Column(Text, nullable=True)
    reversal_date = Column(DateTime(timezone=True), nullable=True)
    reversed_amount = Column(Numeric(18, 2), nullable=True)
    is_fully_reversed = Column(Boolean, default=False, nullable=False)

    created_by = relationship("User", foreign_keys=[user_id])
    reversal_children = relationship("Expense", backref="reversal_parent", remote_side=[id], foreign_keys=[reversal_parent_id])

    def __repr__(self):
        return f"<Expense {self.title} - {self.amount} {self.currency}>"
