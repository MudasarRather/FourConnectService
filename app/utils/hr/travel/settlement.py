"""HR Travel — expense-settlement reconciliation engine.

Reconciles the released advance against the employee's actual post-travel
expenses + approved DA:

    payable     = max(0, (approved_expense + da_amount) − advance_received)
    recoverable = max(0, advance_received − (approved_expense + da_amount))

Per-diem (DA) is a fixed daily allowance that *covers* meals, local conveyance,
communication and sundry incidentals — the M&IE model used by Google/Microsoft
and Indian govt TA/DA rules. So when a DA is paid, expense lines in those
covered categories are NOT separately reimbursable (that would double-pay the
same trip). The reimbursable expense therefore defaults to:

    reimbursable_expense = total_filed_expense − Σ(lines in DA-covered categories)

Finance can still override this with an explicit approved_expense at the verify
step; once the settlement is verified that signed-off figure is preserved
(detected via ``verified_at``) so a later settle call never silently re-derives it.

Idempotent: recomputes from the live advance / DA / expense lines each call.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.hr.travel_advance import TravelAdvance
from app.models.hr.travel_da import TravelDaRecord
from app.models.hr.travel_settlement import TravelSettlement
from app.models.hr.travel_type import AdvanceStatus, DaRecordStatus, TravelSettlementStatus

# Expense categories the per-diem (DA) is deemed to cover. When a DA is paid,
# claims in these categories are excluded from the separately-reimbursable
# expense by default (the verifier can still re-include via an explicit amount).
# Lodging, long-distance travel, fuel, parking and tolls are NOT covered and
# remain reimbursable on top of DA.
DA_COVERED_CATEGORIES = {"FOOD", "TAXI", "COMMUNICATION", "MISC"}


def _released_advance(db: Session, travel_request_id) -> Decimal:
    adv = db.query(TravelAdvance).filter(
        TravelAdvance.travel_request_id == travel_request_id,
        TravelAdvance.is_deleted == False,  # noqa: E712
        TravelAdvance.status.in_([AdvanceStatus.RELEASED, AdvanceStatus.SETTLED, AdvanceStatus.RECOVERED]),
    ).order_by(TravelAdvance.created_at.desc()).first()
    if not adv:
        return Decimal("0")
    return Decimal(str(adv.approved_amount if adv.approved_amount is not None else adv.advance_amount))


def _approved_da(db: Session, travel_request_id) -> Decimal:
    dar = db.query(TravelDaRecord).filter(
        TravelDaRecord.travel_request_id == travel_request_id,
        TravelDaRecord.is_deleted == False,  # noqa: E712
        TravelDaRecord.status.in_([DaRecordStatus.APPROVED, DaRecordStatus.PAID]),
    ).first()
    if not dar:
        return Decimal("0")
    return Decimal(str(dar.approved_da if dar.approved_da is not None else dar.eligible_da))


def _sum_expense_lines(lines, *, categories=None) -> Decimal:
    """Sum line amounts. With ``categories``, only lines whose category is in
    that set are counted (used to isolate the DA-covered portion)."""
    total = Decimal("0")
    for ln in (lines or []):
        if categories is not None and str(ln.get("category") or "").upper() not in categories:
            continue
        try:
            total += Decimal(str(ln.get("amount") or 0))
        except Exception:
            continue
    return total


def reconcile(db: Session, settlement: TravelSettlement, *,
              approved_expense: Optional[Decimal] = None) -> TravelSettlement:
    """Recompute advance / expense / DA totals and the payable vs. recoverable split.
    ``approved_expense`` lets the verifier approve a different total than the
    auto-derived default (which already excludes DA-covered categories)."""
    advance = _released_advance(db, settlement.travel_request_id)
    da_amount = _approved_da(db, settlement.travel_request_id)
    total_expense = _sum_expense_lines(settlement.expense_lines)

    # M&IE rule: when a DA is paid, per-diem-covered categories aren't separately
    # reimbursable — the default approved expense is the total minus that portion.
    da_covered = (_sum_expense_lines(settlement.expense_lines, categories=DA_COVERED_CATEGORIES)
                  if da_amount > 0 else Decimal("0"))
    default_reimbursable = total_expense - da_covered

    if approved_expense is not None:
        appr = Decimal(str(approved_expense))                       # explicit verifier figure
    elif settlement.verified_at is not None and settlement.approved_expense is not None:
        appr = Decimal(str(settlement.approved_expense))            # preserve a signed-off figure
    else:
        appr = default_reimbursable                                 # auto default (DA-aware)

    reimbursable = (appr + da_amount)
    payable = reimbursable - advance
    recoverable = advance - reimbursable

    settlement.advance_received = advance
    settlement.da_amount = da_amount
    settlement.total_expense = total_expense
    settlement.approved_expense = appr
    settlement.payable_amount = payable if payable > 0 else Decimal("0")
    settlement.recoverable_amount = recoverable if recoverable > 0 else Decimal("0")
    return settlement


# Settlement statuses that are still open to re-reconciliation. SETTLED / PAID /
# REVERSED are terminal — their figures are frozen and must not be re-derived.
_OPEN_SETTLEMENT_STATES = (
    TravelSettlementStatus.DRAFT,
    TravelSettlementStatus.SUBMITTED,
    TravelSettlementStatus.VERIFIED,
)


def resync_settlement(db: Session, travel_request_id) -> Optional[TravelSettlement]:
    """Re-reconcile a tour's open settlement so its DA / advance / expense snapshot
    stays current when a DA is approved or an advance is released *after* the
    employee already filed expenses. The settlement is a stored snapshot that only
    refreshes on submit / verify / settle, so without this the net silently goes
    stale (e.g. DA shows ₹0 even though it was approved). No-op when there's no open
    settlement yet, or it's already terminal. Caller commits."""
    s = (db.query(TravelSettlement)
         .filter(TravelSettlement.travel_request_id == travel_request_id,
                 TravelSettlement.is_deleted == False,  # noqa: E712
                 TravelSettlement.status.in_(_OPEN_SETTLEMENT_STATES))
         .first())
    if not s:
        return None
    reconcile(db, s)
    return s
