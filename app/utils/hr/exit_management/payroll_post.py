"""HR Exit Management — payroll posting seam (F&F → payslip).

Mirrors ``travel/payroll_post.py``. F&F money folds into the next matching pay run
via PayrollAdjustments whose ``sub_type`` namespaces the exit earning/deduction.
We do NOT add values to the ``hr_adjustment_type`` PG enum — the payslip line
renders from sub_type/title. ARREAR carries the net settlement earning; DEDUCTION
carries the recoveries.

  • FNF_SETTLEMENT:{case}  — net settlement earning (ARREAR, taxable)
  • FNF_RECOVERY:{case}    — recoveries (DEDUCTION)
  • FNF_*_REVERSAL         — compensating entry for an already-released item
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.payroll_adjustment import (
    PayrollAdjustment, AdjustmentType, AdjustmentStatus,
)


def post_adjustment(db: Session, *, employee_id, sub_type: str, title: str,
                    amount: Decimal, is_deduction: bool, is_taxable: bool,
                    period_month: Optional[int], period_year: Optional[int],
                    actor: User, reason: Optional[str] = None) -> PayrollAdjustment:
    """Create an APPROVED, unpaid PayrollAdjustment the next matching run folds in."""
    adj = PayrollAdjustment(
        employee_id=employee_id,
        adjustment_type=(AdjustmentType.DEDUCTION if is_deduction else AdjustmentType.ARREAR),
        sub_type=sub_type,
        title=title,
        amount=amount,
        is_taxable=is_taxable,
        is_deduction=is_deduction,
        period_month=period_month,
        period_year=period_year,
        reason=reason or title,
        status=AdjustmentStatus.APPROVED,
        approved_by_id=actor.id,
        approved_at=datetime.now(timezone.utc),
        created_by_id=actor.id,
    )
    db.add(adj)
    db.flush()
    return adj


def cancel_or_reverse(db: Session, adjustment_id, *, employee_id, reversal_sub_type: str,
                      title: str, actor: User, reason: Optional[str] = None):
    """Unwind a linked payroll adjustment. Unpaid → CANCELLED; already PAID →
    post a compensating opposite entry (never edit a released payslip)."""
    if not adjustment_id:
        return None
    adj = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.id == adjustment_id).with_for_update().first()
    if not adj:
        return None
    if adj.status == AdjustmentStatus.PAID:
        comp = PayrollAdjustment(
            employee_id=employee_id,
            adjustment_type=(AdjustmentType.ARREAR if adj.is_deduction else AdjustmentType.DEDUCTION),
            sub_type=reversal_sub_type,
            title=title,
            amount=adj.amount,
            is_taxable=adj.is_taxable,
            is_deduction=not adj.is_deduction,
            reason=reason or title,
            status=AdjustmentStatus.APPROVED,
            approved_by_id=actor.id,
            approved_at=datetime.now(timezone.utc),
            created_by_id=actor.id,
        )
        db.add(comp)
        db.flush()
        return comp
    if adj.status in (AdjustmentStatus.DRAFT, AdjustmentStatus.APPROVED):
        adj.status = AdjustmentStatus.CANCELLED
    return adj
