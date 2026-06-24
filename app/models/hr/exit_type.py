"""HR Exit Management — shared enums.

Kept in a dedicated module (no SQLA model here) so exit_case, exit_clearance,
exit_interview, exit_settlement, exit_policy, exit_document and exit_audit_log
can all import these symbols without a circular-dependency dance — mirrors
``travel_type.py`` / ``reimbursement_type.py``.

The Exit Management module is a rich workflow OVERLAY. ``Employee.lifecycle_state``
stays the single source of truth for ACTIVE / ON_NOTICE / EXITED / ARCHIVED;
``ExitCaseStatus`` is the orchestration layer that DRIVES those transitions
through the existing ``/hr/employees/{id}/lifecycle/*`` handlers. Settlement money
folds into payroll via PayrollAdjustment sub-types (``FNF_SETTLEMENT`` /
``FNF_RECOVERY``) — no new ``hr_adjustment_type`` values.
"""
import enum


class ResignationType(str, enum.Enum):
    """How the separation is initiated / classified."""
    VOLUNTARY = "VOLUNTARY"
    RETIREMENT = "RETIREMENT"
    CONTRACT_COMPLETION = "CONTRACT_COMPLETION"
    PROBATION_EXIT = "PROBATION_EXIT"
    MUTUAL_SEPARATION = "MUTUAL_SEPARATION"
    TERMINATION = "TERMINATION"
    TRANSFER = "TRANSFER"


class ExitReasonCategory(str, enum.Enum):
    """Why the employee is leaving (drives attrition analytics)."""
    BETTER_OPPORTUNITY = "BETTER_OPPORTUNITY"
    COMPENSATION = "COMPENSATION"
    RELOCATION = "RELOCATION"
    HIGHER_STUDIES = "HIGHER_STUDIES"
    HEALTH = "HEALTH"
    PERSONAL = "PERSONAL"
    WORK_ENVIRONMENT = "WORK_ENVIRONMENT"
    CAREER_GROWTH = "CAREER_GROWTH"
    RETIREMENT = "RETIREMENT"
    PERFORMANCE = "PERFORMANCE"
    MISCONDUCT = "MISCONDUCT"
    REDUNDANCY = "REDUNDANCY"
    CONTRACT_END = "CONTRACT_END"
    OTHER = "OTHER"


class ExitCaseStatus(str, enum.Enum):
    """Canonical exit-case workflow state machine (overlay on Employee lifecycle).

    Happy path:
        DRAFT → SUBMITTED → MANAGER_REVIEW → ACCEPTED
              → NOTICE_PERIOD (drives Employee → ON_NOTICE)
              → CLEARANCE → SETTLEMENT
              → COMPLETED (drives Employee → EXITED, then ARCHIVED)
    Branches:
        any-open → WITHDRAWN (employee, pre-acceptance)
        SUBMITTED / MANAGER_REVIEW → REJECTED (terminal)
        any-open → CANCELLED (HR; reverts ON_NOTICE if started)
    """
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    MANAGER_REVIEW = "MANAGER_REVIEW"
    ACCEPTED = "ACCEPTED"
    NOTICE_PERIOD = "NOTICE_PERIOD"
    CLEARANCE = "CLEARANCE"
    SETTLEMENT = "SETTLEMENT"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


# Statuses that count as an "open" case (used for the partial-unique guard).
OPEN_CASE_STATUSES = (
    ExitCaseStatus.DRAFT, ExitCaseStatus.SUBMITTED, ExitCaseStatus.MANAGER_REVIEW,
    ExitCaseStatus.ACCEPTED, ExitCaseStatus.NOTICE_PERIOD, ExitCaseStatus.CLEARANCE,
    ExitCaseStatus.SETTLEMENT,
)


class ClearanceDepartment(str, enum.Enum):
    """Department lanes in the no-dues clearance workflow."""
    MANAGER = "MANAGER"
    IT = "IT"
    FINANCE = "FINANCE"
    HR = "HR"
    ADMIN = "ADMIN"
    SECURITY = "SECURITY"
    PROJECT = "PROJECT"


class ClearanceItemStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    CLEARED = "CLEARED"
    BLOCKED = "BLOCKED"
    NA = "NA"          # not applicable for this employee


class SettlementStatus(str, enum.Enum):
    """Full & Final settlement lifecycle (mirrors TravelSettlementStatus).

    DRAFT → VERIFIED → APPROVED → PAID (posts PayrollAdjustment) → CLOSED.
    Branch: REVERSED (admin clawback — never edits a released payslip).
    """
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    APPROVED = "APPROVED"
    PAID = "PAID"
    CLOSED = "CLOSED"
    REVERSED = "REVERSED"


class InterviewStatus(str, enum.Enum):
    # Slot reserved when the separation is accepted, but HR has not yet scheduled
    # or invited the employee. Nothing is actionable by the employee in this state.
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class ExitDocStatus(str, enum.Enum):
    """Experience / Relieving letter lifecycle."""
    NOT_GENERATED = "NOT_GENERATED"
    GENERATED = "GENERATED"
    ISSUED = "ISSUED"
    REVOKED = "REVOKED"


class ExitAuditAction(str, enum.Enum):
    """Every mutation written to ``hr_exit_audit_logs``."""
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    SUBMITTED = "SUBMITTED"
    MANAGER_DECISION = "MANAGER_DECISION"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    CANCELLED = "CANCELLED"
    NOTICE_STARTED = "NOTICE_STARTED"
    NOTICE_WAIVED = "NOTICE_WAIVED"
    NOTICE_ADJUSTED = "NOTICE_ADJUSTED"
    CLEARANCE_SEEDED = "CLEARANCE_SEEDED"
    CLEARANCE_ITEM_UPDATED = "CLEARANCE_ITEM_UPDATED"
    CLEARANCE_REOPENED = "CLEARANCE_REOPENED"
    CLEARANCE_COMPLETED = "CLEARANCE_COMPLETED"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    INTERVIEW_COMPLETED = "INTERVIEW_COMPLETED"
    ASSET_RETURN_FLAGGED = "ASSET_RETURN_FLAGGED"
    SETTLEMENT_DRAFTED = "SETTLEMENT_DRAFTED"
    SETTLEMENT_RECALCULATED = "SETTLEMENT_RECALCULATED"
    SETTLEMENT_VERIFIED = "SETTLEMENT_VERIFIED"
    SETTLEMENT_APPROVED = "SETTLEMENT_APPROVED"
    SETTLEMENT_PAID = "SETTLEMENT_PAID"
    SETTLEMENT_REVERSED = "SETTLEMENT_REVERSED"
    SETTLEMENT_CLOSED = "SETTLEMENT_CLOSED"
    LETTER_GENERATED = "LETTER_GENERATED"
    LETTER_ISSUED = "LETTER_ISSUED"
    LETTER_REVOKED = "LETTER_REVOKED"
    EXITED = "EXITED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"          # case soft-deleted from the registry (pre-accept / closed)
    POLICY_CREATED = "POLICY_CREATED"
    POLICY_UPDATED = "POLICY_UPDATED"
    POLICY_DELETED = "POLICY_DELETED"
