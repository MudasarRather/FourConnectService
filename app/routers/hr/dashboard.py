"""HR dashboard stats — relocated from the old single-file router."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee, LifecycleState
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr", tags=["HR"])


@router.get("/dashboard-stats")
def hr_dashboard_stats(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Aggregate stats for the admin HR Dashboard.

    Live counters: total / active / probation / notice / recent hires (30d).
    Stubs (0 until their phases land): pending_leave_approvals, open_positions,
    todays_attendance_pct, upcoming_exits_30d.
    """
    base = db.query(Employee).filter(Employee.is_deleted == False)  # noqa: E712

    total = base.count()
    active_employees = base.filter(
        Employee.lifecycle_state.in_([LifecycleState.ACTIVE, LifecycleState.ON_PROBATION])
    ).count()
    on_probation = base.filter(Employee.lifecycle_state == LifecycleState.ON_PROBATION).count()
    on_notice = base.filter(Employee.lifecycle_state == LifecycleState.ON_NOTICE).count()
    suspended = base.filter(Employee.lifecycle_state == LifecycleState.SUSPENDED).count()
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    recent_hires_30d = base.filter(Employee.created_at >= thirty_days_ago).count()

    return {
        "total_employees": int(total),
        "active_employees": int(active_employees),
        "employees_on_probation": int(on_probation),
        "employees_on_notice": int(on_notice),
        "employees_suspended": int(suspended),
        "recent_hires_30d": int(recent_hires_30d),
        # Stubs — populated as their phases ship
        "pending_leave_approvals": 0,
        "open_positions": 0,
        "todays_attendance_pct": 0,
        "upcoming_exits_30d": int(
            base.filter(
                Employee.lifecycle_state == LifecycleState.ON_NOTICE,
                Employee.last_working_date.isnot(None),
            ).count()
        ),
    }
