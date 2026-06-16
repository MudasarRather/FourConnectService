"""HR Reimbursements — configurable N-stage approval chain mechanics.

A direct adaptation of the Leave module's Phase-4 chain (see
``app/routers/hr/leaves.py``). A claim policy stores an ``approval_chain``; at
submit time the chain is snapshotted onto the claim (``approval_steps`` +
``current_step``), resolving MANAGER/USER stages to concrete users and dropping
amount-banded stages that don't apply. The state machine then walks the snapshot
stage-by-stage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException

from app.models.hr.reimbursement_type import ClaimStatus, ClaimDecision


_DEFAULT_CHAIN: List[dict] = [
    {"approver_type": "MANAGER", "approver_user_id": None, "label": "Reporting Manager", "min_amount": None},
    {"approver_type": "FINANCE", "approver_user_id": None, "label": "Finance", "min_amount": None},
    {"approver_type": "HR",      "approver_user_id": None, "label": "HR", "min_amount": None},
]

_VALID_APPROVER_TYPES = {"MANAGER", "FINANCE", "HR", "USER"}

_DEFAULT_LABELS = {
    "MANAGER": "Reporting Manager", "FINANCE": "Finance", "HR": "HR", "USER": "Approver",
}

# Lifecycle state machine. Self-loop on PENDING_APPROVAL = advancing one stage.
_VALID_TRANSITIONS = {
    ClaimStatus.DRAFT: {ClaimStatus.PENDING_APPROVAL, ClaimStatus.CANCELLED},
    ClaimStatus.PENDING_APPROVAL: {
        ClaimStatus.PENDING_APPROVAL, ClaimStatus.APPROVED, ClaimStatus.REJECTED,
        ClaimStatus.RETURNED, ClaimStatus.CANCELLED,
    },
    ClaimStatus.RETURNED: {ClaimStatus.PENDING_APPROVAL, ClaimStatus.CANCELLED},
    ClaimStatus.APPROVED: {
        ClaimStatus.SETTLED, ClaimStatus.PAID, ClaimStatus.REVERSED, ClaimStatus.CANCELLED,
    },
    ClaimStatus.SETTLED: {ClaimStatus.PAID, ClaimStatus.REVERSED},
    ClaimStatus.PAID: {ClaimStatus.REVERSED},
    ClaimStatus.REJECTED: set(),
    ClaimStatus.CANCELLED: set(),
    ClaimStatus.REVERSED: set(),
}


def assert_transition(current: ClaimStatus, next_: ClaimStatus) -> None:
    if next_ not in _VALID_TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"Cannot transition claim from {current.value} to {next_.value}")


def normalize_chain_config(chain: Optional[List[dict]]) -> List[dict]:
    """Return a sanitized chain config (policy's chain or the default)."""
    if not chain:
        return [dict(s) for s in _DEFAULT_CHAIN]
    out: List[dict] = []
    for s in chain:
        t = (s.get("approver_type") or "MANAGER").upper()
        if t not in _VALID_APPROVER_TYPES:
            t = "MANAGER"
        min_amt = s.get("min_amount")
        out.append({
            "approver_type": t,
            "approver_user_id": s.get("approver_user_id"),
            "label": s.get("label") or _DEFAULT_LABELS[t],
            "min_amount": float(min_amt) if min_amt not in (None, "") else None,
        })
    return out or [dict(s) for s in _DEFAULT_CHAIN]


def build_claim_steps(chain_cfg: List[dict], employee, amount: Decimal) -> List[dict]:
    """Snapshot chain config onto a new claim. Drops amount-banded stages whose
    ``min_amount`` exceeds the claim amount; resolves MANAGER → reporting manager
    and USER → its named user; FINANCE/HR stay None (any superuser may act).
    """
    amt = float(amount or 0)
    steps: List[dict] = []
    idx = 0
    for stage in chain_cfg:
        min_amt = stage.get("min_amount")
        if min_amt is not None and amt <= float(min_amt):
            continue  # stage not triggered at this amount
        t = stage["approver_type"]
        resolved = None
        if t == "MANAGER":
            resolved = getattr(employee, "reporting_manager_id", None)
        elif t == "USER":
            resolved = stage.get("approver_user_id")
        steps.append({
            "step": idx,
            "approver_type": t,
            "approver_user_id": str(resolved) if resolved else None,
            "label": stage["label"],
            "min_amount": min_amt,
            "decision": None,
            "decided_by_id": None,
            "decided_at": None,
            "notes": None,
        })
        idx += 1
    if not steps:
        # An empty chain (e.g. every stage banded out) means no approval needed —
        # fall back to a single FINANCE gate so claims can never auto-approve silently.
        steps.append({
            "step": 0, "approver_type": "FINANCE", "approver_user_id": None,
            "label": "Finance", "min_amount": None, "decision": None,
            "decided_by_id": None, "decided_at": None, "notes": None,
        })
    return steps


def step_status(steps: List[dict], idx: int) -> ClaimStatus:
    """Map the current step index to the coarse claim status. The precise active
    stage is read by the frontend from ``approval_steps[current_step]``."""
    if idx >= len(steps):
        return ClaimStatus.APPROVED
    return ClaimStatus.PENDING_APPROVAL


def auto_skip_unresolvable(steps: List[dict], start: int = 0) -> int:
    """Advance past MANAGER stages with no resolvable approver. Marks each
    SKIPPED. Returns the new current_step index."""
    i = start
    now_iso = datetime.now(timezone.utc).isoformat()
    while i < len(steps):
        s = steps[i]
        if s["approver_type"] == "MANAGER" and not s.get("approver_user_id"):
            s["decision"] = ClaimDecision.SKIPPED.value
            s["decided_at"] = now_iso
            s["notes"] = "No reporting manager configured — stage skipped"
            i += 1
            continue
        break
    return i


def can_act_on_step(user, step: dict) -> bool:
    """Permission check for a single approval stage."""
    t = step["approver_type"]
    if t in ("HR", "FINANCE"):
        return bool(user.is_superuser)
    if t == "MANAGER":
        return step.get("approver_user_id") == str(user.id)
    if t == "USER":
        return step.get("approver_user_id") == str(user.id) or bool(user.is_superuser)
    return False


def mirror_final_columns(claim) -> None:
    """Mirror the last APPROVED step into the denormalised approved_* columns."""
    steps = list(claim.approval_steps or [])
    last_approved = next(
        (s for s in reversed(steps) if s.get("decision") == ClaimDecision.APPROVED.value),
        None,
    )
    if last_approved:
        if last_approved.get("decided_by_id"):
            try:
                claim.approved_by_id = UUID(last_approved["decided_by_id"])
            except Exception:
                pass
        if last_approved.get("decided_at"):
            try:
                claim.approved_at = datetime.fromisoformat(last_approved["decided_at"])
            except Exception:
                pass
        claim.approver_notes = last_approved.get("notes")
