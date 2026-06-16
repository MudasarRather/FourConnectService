"""HR Reimbursements — claim creation, submission and per-stage decision flow.

Shared by the admin router and the self-service / manager-queue router so the
business rules live in exactly one place.
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.claim import Claim
from app.models.hr.claim_category import ClaimCategory
from app.models.hr.claim_policy import ClaimPolicy
from app.models.hr.reimbursement_type import ClaimStatus, ClaimDecision, ClaimAuditAction
from app.utils.hr.reimbursements.chain import (
    normalize_chain_config, build_claim_steps, step_status,
    auto_skip_unresolvable, mirror_final_columns, assert_transition,
)
from app.utils.hr.reimbursements.service import (
    generate_claim_number, validate_details_against_schema,
    write_claim_audit, emit_notifications,
)

# Statuses that count toward monthly usage caps (everything not terminally rejected)
_USED_STATUSES = (
    ClaimStatus.DRAFT, ClaimStatus.PENDING_APPROVAL, ClaimStatus.RETURNED,
    ClaimStatus.APPROVED, ClaimStatus.SETTLED, ClaimStatus.PAID,
)


def get_category(db: Session, category_id: UUID) -> ClaimCategory:
    cat = db.query(ClaimCategory).filter(
        ClaimCategory.id == category_id, ClaimCategory.is_deleted == False,  # noqa: E712
    ).first()
    if not cat:
        raise HTTPException(404, "Claim category not found")
    if not cat.is_active:
        raise HTTPException(409, "This claim category is inactive")
    return cat


def get_policy(db: Session, category_id: UUID) -> Optional[ClaimPolicy]:
    return db.query(ClaimPolicy).filter(
        ClaimPolicy.category_id == category_id, ClaimPolicy.is_deleted == False,  # noqa: E712
        ClaimPolicy.is_active == True,  # noqa: E712
    ).first()


def _enforce_policy(db: Session, *, employee_id: UUID, category: ClaimCategory,
                    policy: Optional[ClaimPolicy], amount: Decimal,
                    expense_date: date, attachments: list, exclude_claim_id=None) -> None:
    requires_attachment = category.requires_attachment
    above = None
    window_days = None
    if policy:
        requires_attachment = policy.requires_attachment
        above = policy.attachment_required_above
        window_days = policy.submission_window_days
        if policy.max_amount_per_claim is not None and amount > policy.max_amount_per_claim:
            raise HTTPException(422, f"Amount exceeds the per-claim limit of {policy.max_amount_per_claim}")

    # Attachment requirement (unconditional, or only above a threshold)
    need_attach = requires_attachment if above is None else (amount > above)
    if need_attach and not (attachments or []):
        raise HTTPException(422, "A supporting receipt / invoice is required for this claim")

    # Submission window
    if window_days is not None and expense_date:
        age = (date.today() - expense_date).days
        if age > window_days:
            raise HTTPException(422, f"Expense is older than the {window_days}-day submission window")

    # Monthly caps
    if policy and (policy.max_amount_per_month is not None or policy.max_claims_per_month is not None):
        first = date(expense_date.year, expense_date.month, 1)
        nxt = date(first.year + (first.month // 12), (first.month % 12) + 1, 1)
        q = db.query(Claim).filter(
            Claim.employee_id == employee_id,
            Claim.category_id == category.id,
            Claim.is_deleted == False,  # noqa: E712
            Claim.status.in_(_USED_STATUSES),
            Claim.expense_date >= first, Claim.expense_date < nxt,
        )
        if exclude_claim_id:
            q = q.filter(Claim.id != exclude_claim_id)
        rows = q.all()
        if policy.max_claims_per_month is not None and len(rows) >= policy.max_claims_per_month:
            raise HTTPException(422, f"Monthly claim count limit ({policy.max_claims_per_month}) reached for {category.name}")
        if policy.max_amount_per_month is not None:
            month_sum = sum(Decimal(str(r.amount or 0)) for r in rows) + amount
            if month_sum > policy.max_amount_per_month:
                raise HTTPException(422, f"Monthly amount limit ({policy.max_amount_per_month}) exceeded for {category.name}")


def build_new_claim(db: Session, *, employee: Employee, category: ClaimCategory,
                    payload, actor: User) -> Claim:
    """Construct a Claim row (status DRAFT, no chain yet). Caller decides whether
    to submit immediately."""
    validate_details_against_schema(payload.details, category.field_schema)
    attachments = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in (payload.attachments or [])]
    claim = Claim(
        claim_number=generate_claim_number(db),
        employee_id=employee.id,
        category_id=category.id,
        claim_date=date.today(),
        expense_date=payload.expense_date,
        amount=payload.amount,
        currency=payload.currency or "INR",
        description=payload.description,
        vendor=payload.vendor,
        remarks=payload.remarks,
        cost_center=payload.cost_center,
        project_id=payload.project_id,
        attachments=attachments,
        details=payload.details or {},
        status=ClaimStatus.DRAFT,
        created_by_id=actor.id,
    )
    db.add(claim)
    db.flush()
    return claim


def submit_claim(db: Session, claim: Claim, employee: Employee, actor: User, *,
                 enforce: bool = True) -> UUID:
    """DRAFT/RETURNED → PENDING_APPROVAL. Snapshots the approval chain. Returns
    the next approver's user id (or None) for notification."""
    if claim.status not in (ClaimStatus.DRAFT, ClaimStatus.RETURNED):
        raise HTTPException(409, f"Cannot submit a {claim.status.value} claim")
    category = get_category(db, claim.category_id)
    policy = get_policy(db, claim.category_id)
    if enforce:
        _enforce_policy(db, employee_id=claim.employee_id, category=category, policy=policy,
                        amount=claim.amount, expense_date=claim.expense_date,
                        attachments=claim.attachments, exclude_claim_id=claim.id)
    chain_cfg = normalize_chain_config(policy.approval_chain if policy else None)
    steps = build_claim_steps(chain_cfg, employee, claim.amount)
    cur_idx = auto_skip_unresolvable(steps, 0)
    from_status = claim.status.value
    claim.approval_steps = steps
    flag_modified(claim, "approval_steps")
    claim.current_step = cur_idx
    # Clear any prior return/clarification marks on resubmit
    claim.return_reason = None
    claim.returned_at = None
    claim.status = step_status(steps, cur_idx)
    if claim.status == ClaimStatus.APPROVED:
        # Defensive: build_claim_steps always yields ≥1 non-skippable stage.
        claim.approved_at = datetime.now(timezone.utc)
        claim.approved_amount = claim.amount
    claim.submitted_at = datetime.now(timezone.utc)
    claim.submitted_by_id = actor.id
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=ClaimAuditAction.SUBMIT, actor_id=actor.id,
                      from_status=from_status, to_status=claim.status.value,
                      note=f"Submitted {claim.claim_number}")
    next_approver = None
    if cur_idx < len(steps):
        next_approver = steps[cur_idx].get("approver_user_id")
    return UUID(next_approver) if next_approver else None


def apply_decision(db: Session, claim: Claim, *, decision: ClaimDecision,
                   notes: Optional[str], approved_amount: Optional[Decimal],
                   actor: User) -> Tuple[ClaimStatus, Optional[UUID], str]:
    """Apply a per-stage decision at the current step. Returns
    (new_status, next_approver_user_id, notification_event). Caller must have
    already verified the actor can act on the current step."""
    if claim.status not in (ClaimStatus.PENDING_APPROVAL,):
        raise HTTPException(409, f"Claim is not awaiting approval (status {claim.status.value})")
    steps = list(claim.approval_steps or [])
    idx = int(claim.current_step or 0)
    if idx >= len(steps):
        raise HTTPException(409, "Claim is fully resolved")
    cur = steps[idx]
    now_iso = datetime.now(timezone.utc).isoformat()
    from_status = claim.status.value
    next_approver: Optional[UUID] = None

    if decision == ClaimDecision.APPROVED:
        cur["decision"] = ClaimDecision.APPROVED.value
        cur["decided_by_id"] = str(actor.id)
        cur["decided_at"] = now_iso
        cur["notes"] = notes
        new_idx = auto_skip_unresolvable(steps, idx + 1)
        claim.approval_steps = steps
        flag_modified(claim, "approval_steps")
        claim.current_step = new_idx
        if new_idx >= len(steps):
            assert_transition(claim.status, ClaimStatus.APPROVED)
            claim.status = ClaimStatus.APPROVED
            claim.approved_at = datetime.now(timezone.utc)
            if approved_amount is not None and approved_amount > 0:
                claim.approved_amount = approved_amount
            elif claim.approved_amount is None:
                claim.approved_amount = claim.amount
            mirror_final_columns(claim)
            event = "approved"
        else:
            claim.status = step_status(steps, new_idx)
            mirror_final_columns(claim)
            na = steps[new_idx].get("approver_user_id")
            next_approver = UUID(na) if na else None
            event = "advanced"
    elif decision == ClaimDecision.REJECTED:
        cur["decision"] = ClaimDecision.REJECTED.value
        cur["decided_by_id"] = str(actor.id)
        cur["decided_at"] = now_iso
        cur["notes"] = notes
        claim.approval_steps = steps
        flag_modified(claim, "approval_steps")
        assert_transition(claim.status, ClaimStatus.REJECTED)
        claim.status = ClaimStatus.REJECTED
        claim.rejected_at = datetime.now(timezone.utc)
        claim.reject_reason = notes
        event = "rejected"
    elif decision == ClaimDecision.RETURNED:
        cur["decision"] = ClaimDecision.RETURNED.value
        cur["decided_by_id"] = str(actor.id)
        cur["decided_at"] = now_iso
        cur["notes"] = notes
        claim.approval_steps = steps
        flag_modified(claim, "approval_steps")
        assert_transition(claim.status, ClaimStatus.RETURNED)
        claim.status = ClaimStatus.RETURNED
        claim.returned_at = datetime.now(timezone.utc)
        claim.return_reason = notes
        event = "returned"
    else:
        raise HTTPException(422, "Unsupported decision")

    action_map = {
        "approved": ClaimAuditAction.APPROVE, "advanced": ClaimAuditAction.APPROVE,
        "rejected": ClaimAuditAction.REJECT, "returned": ClaimAuditAction.RETURN,
    }
    write_claim_audit(db, entity_type="CLAIM", entity_id=claim.id, claim_id=claim.id,
                      action=action_map[event], actor_id=actor.id,
                      from_status=from_status, to_status=claim.status.value,
                      note=notes or f"{decision.value} at step {idx}")
    return claim.status, next_approver, event
