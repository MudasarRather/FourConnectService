"""HR Travel Management — shared enums.

Kept in a dedicated module (no SQLA model here) so travel_category, travel_policy,
travel_request, travel_booking, travel_advance, travel_da, travel_settlement and
travel_audit_log can all import these symbols without a circular-dependency dance
— mirrors ``reimbursement_type.py``.

``TravelRequestStatus`` is the request lifecycle state machine. The fine-grained
"which approver is next" detail lives in
``TravelRequest.approval_steps[current_step]`` (a configurable N-stage chain,
snapshotted at submit) — exactly like the Reimbursements / Leave modules. DA,
advances and settlements fold into payroll via PayrollAdjustment sub-types
(``TRAVEL_DA`` / ``TRAVEL_ADVANCE`` / ``TRAVEL_SETTLEMENT``).
"""
import enum


class TravelRequestStatus(str, enum.Enum):
    """Travel request lifecycle. Valid transitions enforced in ``assert_transition``.

    Happy path:
        DRAFT → PENDING_APPROVAL (walks the chain stage-by-stage) → APPROVED
              → IN_PROGRESS (travel executing — attendance ON_DUTY) → COMPLETED
    Branches:
        PENDING_APPROVAL → RETURNED (sent back for correction) → PENDING_APPROVAL
        PENDING_APPROVAL → REJECTED   (terminal)
        DRAFT / PENDING_APPROVAL / RETURNED / APPROVED / IN_PROGRESS → CANCELLED
    """
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TravelDecision(str, enum.Enum):
    """A single approver's per-stage decision recorded on the request row."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETURNED = "RETURNED"
    SKIPPED = "SKIPPED"   # auto-skip when a MANAGER stage has no resolvable approver


class TravelApproverType(str, enum.Enum):
    """Approval-chain stage roles.

    HR / FINANCE stages gate on ``is_superuser``. MANAGER binds to the employee's
    ``reporting_manager_id`` (a regular user acting on the Team Approvals page).
    DEPT_HEAD / USER bind to a named user, with superuser fallback (the Department
    master has no head field, so a DEPT_HEAD stage is configured with an explicit
    approver and never auto-skips — it can't silently auto-approve).
    """
    MANAGER = "MANAGER"
    DEPT_HEAD = "DEPT_HEAD"
    FINANCE = "FINANCE"
    HR = "HR"
    USER = "USER"


class TravelPriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class CityCategory(str, enum.Enum):
    """DA city tiering. Drives the per-grade daily-allowance rate."""
    METRO = "METRO"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"
    INTERNATIONAL = "INTERNATIONAL"


class BookingType(str, enum.Enum):
    FLIGHT = "FLIGHT"
    TRAIN = "TRAIN"
    HOTEL = "HOTEL"
    TAXI = "TAXI"
    BUS = "BUS"
    RENTAL = "RENTAL"


class BookingStatus(str, enum.Enum):
    PENDING = "PENDING"
    BOOKED = "BOOKED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class AdvanceStatus(str, enum.Enum):
    """Travel advance lifecycle.

    REQUESTED → APPROVED → RELEASED (posts a PayrollAdjustment TRAVEL_ADVANCE)
              → SETTLED (reconciled in the final settlement)
    A negative reconciliation moves the residual to RECOVERED. Branches: REJECTED,
    CANCELLED (terminal before release).
    """
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    RELEASED = "RELEASED"
    SETTLED = "SETTLED"
    RECOVERED = "RECOVERED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class DaRecordStatus(str, enum.Enum):
    COMPUTED = "COMPUTED"
    APPROVED = "APPROVED"
    PAID = "PAID"
    REVERSED = "REVERSED"


class TravelSettlementStatus(str, enum.Enum):
    """Expense-settlement lifecycle (post-travel financial closure).

    DRAFT → SUBMITTED (employee posts expenses) → VERIFIED (manager/finance check)
          → SETTLED (payable/recoverable posted to payroll) → PAID (batch released)
    Branch: REVERSED (admin clawback).
    """
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    VERIFIED = "VERIFIED"
    SETTLED = "SETTLED"
    PAID = "PAID"
    REVERSED = "REVERSED"


class TravelSettlementMethod(str, enum.Enum):
    """How a net-payable settlement is disbursed (recoverables always go to payroll)."""
    PAYROLL = "PAYROLL"
    BANK_TRANSFER = "BANK_TRANSFER"
    CASH = "CASH"
    CHEQUE = "CHEQUE"


class TravelExpenseCategory(str, enum.Enum):
    """Post-travel expense line categories (settlement line items)."""
    TRAVEL = "TRAVEL"
    ACCOMMODATION = "ACCOMMODATION"
    FOOD = "FOOD"
    TAXI = "TAXI"
    FUEL = "FUEL"
    PARKING = "PARKING"
    TOLL = "TOLL"
    COMMUNICATION = "COMMUNICATION"
    MISC = "MISC"


class TravelAuditAction(str, enum.Enum):
    """Every mutation written to ``hr_travel_audit_logs``."""
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RETURN = "RETURN"
    ESCALATE = "ESCALATE"
    CANCEL = "CANCEL"
    EXECUTE = "EXECUTE"
    COMPLETE = "COMPLETE"
    BOOK = "BOOK"
    BOOKING_UPDATE = "BOOKING_UPDATE"
    BOOKING_CANCEL = "BOOKING_CANCEL"
    ADVANCE_REQUEST = "ADVANCE_REQUEST"
    ADVANCE_APPROVE = "ADVANCE_APPROVE"
    ADVANCE_RELEASE = "ADVANCE_RELEASE"
    ADVANCE_REJECT = "ADVANCE_REJECT"
    DA_COMPUTE = "DA_COMPUTE"
    DA_APPROVE = "DA_APPROVE"
    EXPENSE_SUBMIT = "EXPENSE_SUBMIT"
    SETTLE = "SETTLE"
    MARK_PAID = "MARK_PAID"
    REVERSE = "REVERSE"
    DELETE = "DELETE"
    CATEGORY_CREATE = "CATEGORY_CREATE"
    CATEGORY_UPDATE = "CATEGORY_UPDATE"
    CATEGORY_DELETE = "CATEGORY_DELETE"
    POLICY_CREATE = "POLICY_CREATE"
    POLICY_UPDATE = "POLICY_UPDATE"
    POLICY_DELETE = "POLICY_DELETE"
    DA_RATE_CREATE = "DA_RATE_CREATE"
    DA_RATE_UPDATE = "DA_RATE_UPDATE"
    DA_RATE_DELETE = "DA_RATE_DELETE"
