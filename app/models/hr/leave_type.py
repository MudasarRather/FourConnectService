"""HR Leave & Absence — shared enums.

Kept in a dedicated module (no SQLA model here) so leave_policy, leave_request,
leave_balance and leave_balance_history can all import these symbols without a
circular-dependency dance.

`LeaveType` is the canonical taxonomy. `LeaveStatus` is the request lifecycle
state machine (two-tier approval: Manager → HR). `LeaveSession` distinguishes
half-day mornings vs afternoons. `LedgerKind` enumerates the bookkeeping
transactions written to `hr_leave_balance_history`.
"""
import enum


class LeaveType(str, enum.Enum):
    """Canonical leave taxonomy. Static set — new types require a code change
    plus a Postgres `ALTER TYPE ... ADD VALUE` migration."""
    CASUAL = "CASUAL"
    SICK = "SICK"
    EARNED = "EARNED"
    MATERNITY = "MATERNITY"
    PATERNITY = "PATERNITY"
    BEREAVEMENT = "BEREAVEMENT"
    COMP_OFF = "COMP_OFF"
    LWP = "LWP"             # Leave Without Pay — skips balance debit entirely
    STUDY = "STUDY"
    SPECIAL = "SPECIAL"


class LeaveStatus(str, enum.Enum):
    """Request lifecycle. Valid transitions enforced in router `_assert_transition`.

    Happy path:
        DRAFT → PENDING_MANAGER → PENDING_HR → APPROVED
    Branches:
        any PENDING_* → CANCELLED (employee withdraw, only while PENDING_MANAGER)
        PENDING_MANAGER → MANAGER_REJECTED  (terminal)
        PENDING_HR     → REJECTED           (terminal)
        APPROVED       → CANCELLED          (admin only; reverses ledger)
        any PENDING_*  → LAPSED             (mgr/HR close-out; dates already passed)
    Admin manual override skips both pending states and lands APPROVED with
    `is_admin_override=True`.
    """
    DRAFT = "DRAFT"
    PENDING_MANAGER = "PENDING_MANAGER"
    PENDING_HR = "PENDING_HR"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MANAGER_REJECTED = "MANAGER_REJECTED"
    CANCELLED = "CANCELLED"
    WITHDRAWN = "WITHDRAWN"
    # A pending request whose dates have already passed without being actioned.
    # It can no longer be approved (the days are gone); the reporting manager or
    # HR closes it as LAPSED with a mandatory remark for the audit trail.
    LAPSED = "LAPSED"


class LeaveSession(str, enum.Enum):
    """Which half of the day a half-day leave covers."""
    FIRST = "FIRST"     # morning off
    SECOND = "SECOND"   # afternoon off
    FULL = "FULL"       # convenience value for full-day requests


class LeaveDecision(str, enum.Enum):
    """The manager / HR per-stage decision recorded on the request row."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"   # used when the employee has no reporting manager


class LedgerKind(str, enum.Enum):
    """Why a row was written to `hr_leave_balance_history`."""
    OPENING_SEED = "OPENING_SEED"
    ACCRUAL = "ACCRUAL"
    REQUEST_APPROVED = "REQUEST_APPROVED"
    REQUEST_CANCELLED = "REQUEST_CANCELLED"
    CARRY_FORWARD = "CARRY_FORWARD"
    ENCASHMENT = "ENCASHMENT"
    ADMIN_ADJUST = "ADMIN_ADJUST"
    # Phase 2 additions — comp-off lifecycle
    COMP_OFF_EARNED = "COMP_OFF_EARNED"     # credit (auto or manual)
    COMP_OFF_USED = "COMP_OFF_USED"         # debit when a COMP_OFF leave is approved
    COMP_OFF_EXPIRED = "COMP_OFF_EXPIRED"   # expiry sweep
    # Attendance-driven LWP debit for the monthly late-mark accumulation penalty
    # (distinct from a no-show LWP debit). Value added live via
    # add_late_penalty_support.py.
    LATE_PENALTY = "LATE_PENALTY"


class EncashmentStatus(str, enum.Enum):
    """Lifecycle for LeaveEncashment requests.

    Corporate chain:  PENDING_MANAGER → PENDING (HR) → APPROVED → PAID
      PENDING_MANAGER → REJECTED  (manager declines)
      PENDING         → REJECTED  (HR declines; balance not yet locked)
      PENDING_MANAGER / PENDING → CANCELLED  (employee withdraws)
    Employees with no reporting manager (or who are their own manager) skip the
    manager gate and start at PENDING.
    """
    PENDING_MANAGER = "PENDING_MANAGER"  # awaiting reporting-manager endorsement
    PENDING = "PENDING"                  # awaiting HR sanction
    APPROVED = "APPROVED"   # HR-sanctioned; balance locked, not yet paid
    REJECTED = "REJECTED"
    PAID = "PAID"           # Finance disbursed against a payroll batch
    CANCELLED = "CANCELLED"
