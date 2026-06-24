"""Scheduled leave upkeep — monthly accrual + fiscal-year-rollover carry-forward.

Invoked by ``tasks_cron.py`` (wired to Windows Task Scheduler). Reuses the exact
router cron logic so scheduled behaviour matches the manual endpoints. Fully
idempotent and safe to run on any cadence (it's designed for a daily run):

* **Monthly accrual** — credits ``monthly_accrual`` for the current month to every
  active employee, capped at the annual quota. Per-(employee, type, month)
  idempotency means running it daily credits each month exactly once.
* **Carry-forward** — only fires on the configured FY-start date (default 01-Apr):
  carries ``min(closing, max_carry_forward)`` from the previous FY into the new one
  (so Earned carries up to its cap; Casual/Sick lapse).

Imports the router functions lazily to avoid an import cycle (the router does not
import this module).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict


def run_leave_maintenance(db) -> Dict[str, Any]:
    from app.models.user import User
    from app.routers.hr.leaves import (
        cron_accrue_monthly, cron_carry_forward, _get_setting, _fy_for,
    )
    from app.schemas.hr.leave import AccrueMonthlyBody, CarryForwardBody

    actor = (
        db.query(User)
        .filter(User.is_superuser == True, User.is_active == True)  # noqa: E712
        .order_by(User.created_at.asc())
        .first()
    )
    if actor is None:
        return {"ok": False, "reason": "no active superuser to act as the system actor"}

    today = date.today()
    out: Dict[str, Any] = {"ok": True, "date": today.isoformat()}

    # 1) Monthly accrual for the current month (idempotent + quota-capped).
    r1 = cron_accrue_monthly(AccrueMonthlyBody(month=today.strftime("%Y-%m")), db=db, admin=actor)
    out["accrual"] = {"month": r1.month, "credited": r1.processed, "skipped": r1.skipped_existing}

    # 2) Carry-forward only on the FY-start date (prev FY → new FY).
    fy_start = _get_setting(db, "fiscal_year_start", "04-01")
    try:
        sm, sd = (int(x) for x in fy_start.split("-"))
    except Exception:
        sm, sd = 4, 1
    if (today.month, today.day) == (sm, sd):
        cur_fy = _fy_for(today, fy_start)
        prev_fy = _fy_for(today - timedelta(days=1), fy_start)  # yesterday is in the prior FY
        r2 = cron_carry_forward(CarryForwardBody(from_fy=prev_fy, to_fy=cur_fy), db=db, admin=actor)
        out["carry_forward"] = {"from": prev_fy, "to": cur_fy, "carried": r2.processed}
    else:
        out["carry_forward"] = "skipped (not the FY-start date)"

    return out
