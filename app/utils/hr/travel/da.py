"""HR Travel — DA (Daily Allowance) engine.

Resolves the applicable daily rate from the effective-dated rate matrix
(grade × city category, with a null-grade wildcard fallback) and computes /
refreshes the per-tour ``TravelDaRecord`` (days × rate).
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_da import TravelDaRate, TravelDaRecord
from app.models.hr.travel_type import CityCategory, DaRecordStatus, TravelAuditAction
from app.utils.hr.travel.service import write_travel_audit

# Override guardrails — an admin can override the matrix rate with a justification,
# but never beyond these ceilings (stops the per-diem being inflated to absorb costs).
DA_OVERRIDE_MAX_MULTIPLE = Decimal("3")        # ≤ 3× the policy rate when one exists
DA_OVERRIDE_ABS_CEILING = Decimal("50000")     # absolute ₹/day backstop when no policy rate


def resolve_da_rate(db: Session, *, grade_id: Optional[UUID], city_category: CityCategory,
                    on_date: Optional[date] = None) -> Optional[TravelDaRate]:
    """Latest active rate effective on/before ``on_date`` for (grade, city). Falls
    back to the null-grade wildcard rate when the grade has no explicit row."""
    on_date = on_date or date.today()

    def _query(gid):
        q = db.query(TravelDaRate).filter(
            TravelDaRate.city_category == city_category,
            TravelDaRate.is_active == True, TravelDaRate.is_deleted == False,  # noqa: E712
            TravelDaRate.effective_date <= on_date,
        )
        q = q.filter(TravelDaRate.grade_id == gid) if gid else q.filter(TravelDaRate.grade_id.is_(None))
        return q.order_by(TravelDaRate.effective_date.desc()).first()

    if grade_id:
        rate = _query(grade_id)
        if rate:
            return rate
    return _query(None)


def compute_da(db: Session, req: TravelRequest, *, actor: User,
               city_category: Optional[CityCategory] = None,
               travel_days: Optional[int] = None,
               daily_rate_override: Optional[Decimal] = None,
               override_reason: Optional[str] = None) -> TravelDaRecord:
    """Create or refresh the request's DA record. Resets an existing COMPUTED
    record; an already-APPROVED/PAID record is not silently recomputed.

    A manual ``daily_rate_override`` above the policy matrix rate is only accepted
    with a justification (``override_reason``); the override + reason are written to
    the audit trail. This stops the per-diem being quietly inflated to soak up
    expenses (a daily rate that conveniently equals the trip's expense total)."""
    city = city_category or req.to_city_category
    days = travel_days if travel_days is not None else int(req.num_days or 0)

    # Always resolve the policy rate so an override can be sanity-checked against it.
    rate_row = resolve_da_rate(db, grade_id=req.grade_id, city_category=city,
                               on_date=req.departure_date)
    policy_rate = Decimal(str(rate_row.daily_rate)) if rate_row else None

    override_note = ""
    if daily_rate_override is not None:
        rate = Decimal(str(daily_rate_override))
        if rate < 0:
            raise HTTPException(422, "Daily rate cannot be negative")
        if policy_rate is not None and rate > policy_rate:
            if not (override_reason and override_reason.strip()):
                raise HTTPException(
                    422,
                    f"Daily rate ₹{rate:,.0f} exceeds the policy rate ₹{policy_rate:,.0f} for this "
                    f"grade/{city.value}. A justification is required to override it.")
            # Hard ceiling: even with a justification an override can't exceed 3× the
            # policy rate — stops the per-diem being inflated to soak up expenses.
            ceiling = (policy_rate * DA_OVERRIDE_MAX_MULTIPLE).quantize(Decimal("0.01"))
            if rate > ceiling:
                raise HTTPException(
                    422,
                    f"Daily rate ₹{rate:,.0f} exceeds the maximum override ceiling ₹{ceiling:,.0f} "
                    f"({DA_OVERRIDE_MAX_MULTIPLE}× the policy rate ₹{policy_rate:,.0f}).")
            override_note = (f" · OVERRIDE ₹{rate:,.0f} > policy ₹{policy_rate:,.0f}: "
                             f"{override_reason.strip()}")
        elif policy_rate is None:
            # No matrix rate to anchor against — apply an absolute backstop ceiling.
            if rate > DA_OVERRIDE_ABS_CEILING:
                raise HTTPException(
                    422,
                    f"Daily rate ₹{rate:,.0f} exceeds the maximum allowed ₹{DA_OVERRIDE_ABS_CEILING:,.0f} "
                    f"per day (no policy rate is configured for this grade/{city.value}).")
            if rate > 0 and not (override_reason and override_reason.strip()):
                raise HTTPException(
                    422,
                    f"No policy rate is configured for this grade/{city.value}; a manual daily rate of "
                    f"₹{rate:,.0f} requires a justification.")
            if rate > 0:
                override_note = f" · MANUAL ₹{rate:,.0f} (no policy rate): {override_reason.strip()}"
    else:
        rate = policy_rate if policy_rate is not None else Decimal("0")

    eligible = (rate * Decimal(days)).quantize(Decimal("0.01"))

    record = db.query(TravelDaRecord).filter(
        TravelDaRecord.travel_request_id == req.id, TravelDaRecord.is_deleted == False).first()  # noqa: E712
    if record and record.status in (DaRecordStatus.APPROVED, DaRecordStatus.PAID):
        # Don't clobber an approved/paid DA on recompute.
        return record

    if not record:
        record = TravelDaRecord(
            travel_request_id=req.id, employee_id=req.employee_id, created_by_id=actor.id)
        db.add(record)

    record.grade_id = req.grade_id
    record.da_rate_id = rate_row.id if rate_row else None
    record.city_category = city
    record.travel_days = days
    record.daily_rate = rate
    record.eligible_da = eligible
    record.currency = req.currency or "INR"
    record.status = DaRecordStatus.COMPUTED
    record.computed_at = datetime.now(timezone.utc)
    db.flush()
    write_travel_audit(db, entity_type="DA", entity_id=record.id, travel_request_id=req.id,
                       action=TravelAuditAction.DA_COMPUTE, actor_id=actor.id,
                       note=f"DA computed: {days}d × {rate} = {eligible}{override_note}")
    return record
