"""HR Reimbursements / Employee Claims — shared enums.

Kept in a dedicated module (no SQLA model here) so claim_category, claim_policy,
claim, claim_settlement and claim_audit_log can all import these symbols without
a circular-dependency dance — mirrors ``leave_type.py``.

``ClaimStatus`` is the request lifecycle state machine. The fine-grained
"which approver is next" detail lives in ``Claim.approval_steps[current_step]``
(a configurable N-stage chain, snapshotted at submit), exactly like the Leave
module's Phase-4 chain. ``SettlementMethod`` distinguishes payroll fold-in from
direct disbursement.
"""
import enum


class ClaimStatus(str, enum.Enum):
    """Claim lifecycle. Valid transitions enforced in ``_assert_transition``.

    Happy path:
        DRAFT → PENDING_APPROVAL (walks the chain stage-by-stage) → APPROVED
              → SETTLED (payroll, awaiting batch) → PAID
              → PAID (direct disbursement, recorded immediately)
    Branches:
        PENDING_APPROVAL → RETURNED (sent back for correction) → PENDING_APPROVAL
        PENDING_APPROVAL → REJECTED   (terminal)
        DRAFT / PENDING_APPROVAL / RETURNED → CANCELLED (employee withdraw / admin)
        APPROVED / SETTLED / PAID → REVERSED (admin clawback; PAID reversal posts
            a compensating payroll deduction rather than editing a released payslip)
    """
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    SETTLED = "SETTLED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    REVERSED = "REVERSED"


class ClaimDecision(str, enum.Enum):
    """A single approver's per-stage decision recorded on the claim row."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETURNED = "RETURNED"
    SKIPPED = "SKIPPED"   # auto-skip when a MANAGER stage has no resolvable approver


class SettlementMethod(str, enum.Enum):
    """How an approved claim is disbursed."""
    PAYROLL = "PAYROLL"            # folded into the next payslip via PayrollAdjustment
    BANK_TRANSFER = "BANK_TRANSFER"
    CASH = "CASH"
    CHEQUE = "CHEQUE"
    PETTY_CASH = "PETTY_CASH"


class ClaimApproverType(str, enum.Enum):
    """Approval-chain stage roles.

    HR / FINANCE stages gate on ``is_superuser`` (HR & Finance admins act in the
    admin panel). MANAGER binds to the employee's ``reporting_manager_id`` (a
    regular user acting on the user-side Team Approvals page). USER binds to a
    named user, with superuser fallback.
    """
    MANAGER = "MANAGER"
    FINANCE = "FINANCE"
    HR = "HR"
    USER = "USER"


class ClaimAuditAction(str, enum.Enum):
    """Every mutation written to ``hr_claim_audit_logs`` — mirrors PayrollAuditAction."""
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RETURN = "RETURN"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    ESCALATE = "ESCALATE"
    SETTLE = "SETTLE"
    MARK_PAID = "MARK_PAID"
    REVERSE = "REVERSE"
    CANCEL = "CANCEL"
    DELETE = "DELETE"
    CATEGORY_CREATE = "CATEGORY_CREATE"
    CATEGORY_UPDATE = "CATEGORY_UPDATE"
    CATEGORY_DELETE = "CATEGORY_DELETE"
    POLICY_CREATE = "POLICY_CREATE"
    POLICY_UPDATE = "POLICY_UPDATE"
    POLICY_DELETE = "POLICY_DELETE"
