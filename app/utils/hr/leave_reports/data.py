"""Data layer for HR Leave Reports — fetch + shape.

Pulls from the canonical leave tables (`hr_leave_requests`, `hr_leave_balances`,
`hr_leave_balance_history`, `hr_leave_encashments`) for the requested window
and reshapes the rows into 6 report-specific views.

Defensive design:
  * Always scopes by date / fiscal-year — no implicit "all time".
  * Filters out soft-deleted rows.
  * Returns plain dicts (not SQLAlchemy rows) so renderers can be tested
    in isolation without touching the DB.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func as sa_func, or_
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.leave_type import LeaveType, LeaveStatus, LedgerKind, EncashmentStatus
from app.models.hr.leave_request import LeaveRequest
from app.models.hr.leave_balance import LeaveBalance
from app.models.hr.leave_balance_history import LeaveBalanceHistory
from app.models.hr.leave_encashment import LeaveEncashment
from app.models.hr.leave_policy import LeavePolicy


REPORT_KEYS = (
    "leave_register",       # per-employee leave history
    "department_leaves",    # department-wise aggregation
    "balance_report",       # current balance snapshot
    "liability_report",     # payroll impact: unused × basic / 30
    "comp_off_report",      # comp-off earnings + usage + expiries
    "encashment_report",    # encashment requests with amounts
)


# Accents are drawn ONLY from the leave module's warm spectrum (yellow → gold
# → amber → orange → ember → brass). The leave module forbids green/blue/
# purple/teal anywhere; the six reports are kept distinguishable by spacing
# them across that warm ramp rather than reaching for cool hues.
REPORT_META = {
    "leave_register": {
        "name": "Leave Register",
        "tagline": "Per-employee leave activity",
        "subtitle": "Approved · pending · rejected — every request in the window",
        "accent": "#fbbf24",       # honey gold
        "accent_soft": "#fef3c7",
        "accent_deep": "#92400e",
        "icon": "L",
        "motif": "register",
    },
    "department_leaves": {
        "name": "Department Leaves",
        "tagline": "Team-by-team leave footprint",
        "subtitle": "Total approved days, headcount, average per employee",
        "accent": "#fb923c",       # tangerine
        "accent_soft": "#ffedd5",
        "accent_deep": "#7c2d12",
        "icon": "D",
        "motif": "team",
    },
    "balance_report": {
        "name": "Balance Snapshot",
        "tagline": "Current balance per employee × leave-type",
        "subtitle": "Quota · used · available — for the active fiscal year",
        "accent": "#eab308",       # warm yellow
        "accent_soft": "#fef9c3",
        "accent_deep": "#713f12",
        "icon": "B",
        "motif": "ledger",
    },
    "liability_report": {
        "name": "Liability Report",
        "tagline": "Unused-leave payroll exposure",
        "subtitle": "₹ value if every employee encashed their full balance today",
        "accent": "#e34a0a",       # ember (warm danger)
        "accent_soft": "#ffe0d0",
        "accent_deep": "#7c2d12",
        "icon": "₹",
        "motif": "finance",
    },
    "comp_off_report": {
        "name": "Comp-Off Report",
        "tagline": "Compensatory off earned, used, expired",
        "subtitle": "Auto-credited vs manual grants — with expiry tracking",
        "accent": "#f97316",       # amber-orange
        "accent_soft": "#fed7aa",
        "accent_deep": "#7c2d12",
        "icon": "C",
        "motif": "ticker",
    },
    "encashment_report": {
        "name": "Encashment Report",
        "tagline": "Leave-to-cash conversion ledger",
        "subtitle": "Days encashed × basic snapshot = payroll line item",
        "accent": "#d97706",       # brass / deep gold
        "accent_soft": "#fde68a",
        "accent_deep": "#78350f",
        "icon": "E",
        "motif": "voucher",
    },
}


def report_meta(key: str) -> dict:
    return REPORT_META.get(key) or REPORT_META["leave_register"]


def _fy_for(on_date: date) -> str:
    """Fiscal-year label assuming Apr–Mar (default). Callers can override via
    the explicit fy_label arg on per-report shapers."""
    sy = on_date.year if on_date >= date(on_date.year, 4, 1) else on_date.year - 1
    return f"{sy}-{str(sy + 1)[-2:]}"


def _employee_snapshot_for(db: Session, employee_ids: list) -> dict:
    """Bulk-load employee join for a list of ids → dict[employee_id, snapshot]."""
    if not employee_ids:
        return {}
    rows = (
        db.query(
            Employee.id, Employee.employee_id.label("code"),
            User.full_name.label("name"), User.email.label("email"),
            Department.name.label("dept"),
            Designation.name.label("desg"),
            Employee.monthly_ctc,
        )
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .filter(Employee.id.in_(employee_ids))
        .all()
    )
    return {str(r.id): {
        "code": r.code, "name": r.name, "email": r.email,
        "dept": r.dept or "—", "desg": r.desg or "—",
        "monthly_ctc": float(r.monthly_ctc or 0),
    } for r in rows}


# ════════════════════════════════════════════════════════════════════════════
# FETCH — single dispatcher
# ════════════════════════════════════════════════════════════════════════════

def fetch_rows(
    db: Session,
    report_key: str,
    from_date: date,
    to_date: date,
    *,
    department_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
) -> list[dict]:
    """Dispatcher — each report has its own fetcher.

    All fetchers return plain dicts so renderers stay DB-free.
    """
    if report_key == "leave_register":
        return _fetch_leave_register(db, from_date, to_date, department_id, employee_id)
    if report_key == "department_leaves":
        return _fetch_department_leaves(db, from_date, to_date, department_id)
    if report_key == "balance_report":
        return _fetch_balance_report(db, from_date, to_date, department_id, employee_id)
    if report_key == "liability_report":
        return _fetch_liability_report(db, from_date, to_date, department_id)
    if report_key == "comp_off_report":
        return _fetch_comp_off_report(db, from_date, to_date, department_id, employee_id)
    if report_key == "encashment_report":
        return _fetch_encashment_report(db, from_date, to_date, department_id, employee_id)
    raise ValueError(f"Unknown report_key: {report_key}")


def _fetch_leave_register(db, from_date, to_date, dept_id, employee_id) -> list[dict]:
    q = db.query(LeaveRequest).filter(
        LeaveRequest.is_deleted == False,  # noqa: E712
        LeaveRequest.to_date >= from_date,
        LeaveRequest.from_date <= to_date,
    )
    if dept_id:
        q = q.join(Employee, Employee.id == LeaveRequest.employee_id).filter(
            Employee.department_id == dept_id
        )
    if employee_id:
        q = q.filter(LeaveRequest.employee_id == employee_id)
    rows = q.order_by(LeaveRequest.from_date.desc()).all()
    emp_ids = list({r.employee_id for r in rows})
    snaps = _employee_snapshot_for(db, emp_ids)
    out = []
    for r in rows:
        snap = snaps.get(str(r.employee_id), {})
        out.append({
            "reference_no": r.reference_no,
            "employee_id": str(r.employee_id),
            "employee_code": snap.get("code") or "—",
            "employee_name": snap.get("name") or "—",
            "department": snap.get("dept") or "—",
            "leave_type": r.leave_type.value,
            "from_date": r.from_date,
            "to_date": r.to_date,
            "total_days": float(r.total_days or 0),
            "status": r.status.value,
            "manager_decision": r.manager_decision.value if r.manager_decision else "",
            "hr_decision": r.hr_decision.value if r.hr_decision else "",
            "reason": (r.reason or "").replace("\n", " ").strip()[:200],
            "is_admin_override": bool(r.is_admin_override),
            "created_at": r.created_at.replace(tzinfo=None) if r.created_at else None,
        })
    return out


def _fetch_department_leaves(db, from_date, to_date, dept_id) -> list[dict]:
    """Aggregate approved leave days per (department × leave_type)."""
    q = (
        db.query(
            Department.id.label("dept_id"),
            Department.name.label("dept"),
            LeaveRequest.leave_type,
            sa_func.count(LeaveRequest.id).label("requests"),
            sa_func.coalesce(sa_func.sum(LeaveRequest.total_days), 0).label("days"),
            sa_func.count(sa_func.distinct(LeaveRequest.employee_id)).label("employees"),
        )
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .filter(
            LeaveRequest.is_deleted == False,  # noqa: E712
            LeaveRequest.status == LeaveStatus.APPROVED,
            LeaveRequest.from_date >= from_date,
            LeaveRequest.from_date <= to_date,
        )
        .group_by(Department.id, Department.name, LeaveRequest.leave_type)
    )
    if dept_id:
        q = q.filter(Employee.department_id == dept_id)
    out = []
    for r in q.all():
        out.append({
            "department": r.dept or "Unassigned",
            "leave_type": r.leave_type.value,
            "requests": int(r.requests or 0),
            "days": float(r.days or 0),
            "employees_affected": int(r.employees or 0),
        })
    out.sort(key=lambda x: (x["department"], -x["days"]))
    return out


def _fetch_balance_report(db, from_date, to_date, dept_id, employee_id) -> list[dict]:
    fy = _fy_for(from_date)
    q = db.query(LeaveBalance).filter(
        LeaveBalance.fiscal_year == fy,
        LeaveBalance.is_deleted == False,  # noqa: E712
    )
    if employee_id:
        q = q.filter(LeaveBalance.employee_id == employee_id)
    if dept_id:
        q = q.join(Employee, Employee.id == LeaveBalance.employee_id).filter(
            Employee.department_id == dept_id
        )
    bals = q.all()
    emp_ids = list({b.employee_id for b in bals})
    snaps = _employee_snapshot_for(db, emp_ids)
    out = []
    for b in bals:
        snap = snaps.get(str(b.employee_id), {})
        used = float(b.used or 0)
        # "Quota" = the entitlement actually GRANTED into the ledger
        # (opening + accrued + carry-forward + adjustments), NOT the policy
        # annual ceiling. This keeps Quota / Used / Encashed / Available
        # internally consistent — quota − used − encashed == available — so a
        # type the employee was never credited (or whose credit was reverted)
        # shows Quota 0 instead of the policy max, which previously read like
        # a balance had been granted.
        granted = (
            float(b.opening_balance or 0) + float(b.accrued or 0)
            + float(b.carry_forward_in or 0) + float(b.adjustments or 0)
        )
        util = round((used / granted) * 100, 1) if granted > 0 else 0.0
        out.append({
            "fiscal_year": b.fiscal_year,
            "employee_id": str(b.employee_id),
            "employee_code": snap.get("code") or "—",
            "employee_name": snap.get("name") or "—",
            "department": snap.get("dept") or "—",
            "leave_type": b.leave_type.value,
            "quota": granted,
            "opening": float(b.opening_balance or 0),
            "accrued": float(b.accrued or 0),
            "carry_forward_in": float(b.carry_forward_in or 0),
            "used": used,
            "encashed": float(b.encashed or 0),
            "adjustments": float(b.adjustments or 0),
            "available": float(b.closing_balance or 0),
            "utilisation_pct": util,
        })
    out.sort(key=lambda x: (x["employee_name"], x["leave_type"]))
    return out


def _fetch_liability_report(db, from_date, to_date, dept_id) -> list[dict]:
    """Payroll exposure: for each (employee × encashable leave type),
    `available × basic_salary / 30`."""
    fy = _fy_for(from_date)
    pol_map = {p.leave_type: p for p in db.query(LeavePolicy).all()}
    encashable = {k for k, p in pol_map.items() if p.encashment_allowed}
    q = db.query(LeaveBalance).filter(
        LeaveBalance.fiscal_year == fy,
        LeaveBalance.is_deleted == False,  # noqa: E712
        LeaveBalance.leave_type.in_(list(encashable)) if encashable else False,
    )
    if dept_id:
        q = q.join(Employee, Employee.id == LeaveBalance.employee_id).filter(
            Employee.department_id == dept_id
        )
    rows = q.all()
    emp_ids = list({b.employee_id for b in rows})
    snaps = _employee_snapshot_for(db, emp_ids)
    out = []
    for b in rows:
        snap = snaps.get(str(b.employee_id), {})
        basic = float(snap.get("monthly_ctc") or 0)
        avail = float(b.closing_balance or 0)
        exposure = round((basic * avail) / 30.0, 2) if basic else 0.0
        out.append({
            "fiscal_year": b.fiscal_year,
            "employee_id": str(b.employee_id),
            "employee_code": snap.get("code") or "—",
            "employee_name": snap.get("name") or "—",
            "department": snap.get("dept") or "—",
            "leave_type": b.leave_type.value,
            "available_days": avail,
            "basic_salary": basic,
            "liability_amount": exposure,
        })
    out.sort(key=lambda x: -x["liability_amount"])
    return out


def _fetch_comp_off_report(db, from_date, to_date, dept_id, employee_id) -> list[dict]:
    q = db.query(LeaveBalanceHistory).filter(
        LeaveBalanceHistory.leave_type == LeaveType.COMP_OFF,
        LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_EARNED,
        LeaveBalanceHistory.created_at >= datetime.combine(from_date, datetime.min.time()),
        LeaveBalanceHistory.created_at <= datetime.combine(to_date, datetime.max.time()),
    )
    if employee_id:
        q = q.filter(LeaveBalanceHistory.employee_id == employee_id)
    if dept_id:
        q = q.join(Employee, Employee.id == LeaveBalanceHistory.employee_id).filter(
            Employee.department_id == dept_id
        )
    rows = q.all()
    emp_ids = list({r.employee_id for r in rows})
    snaps = _employee_snapshot_for(db, emp_ids)
    today = date.today()
    out = []
    for r in rows:
        snap = snaps.get(str(r.employee_id), {})
        is_expired = bool(r.expires_on and r.expires_on < today)
        days_until = (r.expires_on - today).days if r.expires_on else None
        out.append({
            "employee_id": str(r.employee_id),
            "employee_code": snap.get("code") or "—",
            "employee_name": snap.get("name") or "—",
            "department": snap.get("dept") or "—",
            "earned_on": r.earned_on,
            "expires_on": r.expires_on,
            "days": float(r.delta or 0),
            "source": "Auto" if r.is_auto_generated else "Manual",
            "note": (r.note or "")[:200],
            "is_expired": is_expired,
            "days_until_expiry": days_until,
        })
    out.sort(key=lambda x: (x["earned_on"] or date.min, x["employee_name"]), reverse=True)
    return out


def _fetch_encashment_report(db, from_date, to_date, dept_id, employee_id) -> list[dict]:
    q = db.query(LeaveEncashment).filter(
        LeaveEncashment.is_deleted == False,  # noqa: E712
        LeaveEncashment.created_at >= datetime.combine(from_date, datetime.min.time()),
        LeaveEncashment.created_at <= datetime.combine(to_date, datetime.max.time()),
    )
    if employee_id:
        q = q.filter(LeaveEncashment.employee_id == employee_id)
    if dept_id:
        q = q.join(Employee, Employee.id == LeaveEncashment.employee_id).filter(
            Employee.department_id == dept_id
        )
    rows = q.order_by(LeaveEncashment.created_at.desc()).all()
    emp_ids = list({r.employee_id for r in rows})
    snaps = _employee_snapshot_for(db, emp_ids)
    out = []
    for r in rows:
        snap = snaps.get(str(r.employee_id), {})
        out.append({
            "reference_no": r.reference_no,
            "employee_id": str(r.employee_id),
            "employee_code": snap.get("code") or "—",
            "employee_name": snap.get("name") or "—",
            "department": snap.get("dept") or "—",
            "leave_type": r.leave_type.value,
            "fiscal_year": r.fiscal_year,
            "days_requested": float(r.days_requested or 0),
            "basic_salary": float(r.basic_salary_snapshot or 0),
            "amount": float(r.amount or 0),
            "status": r.status.value,
            "decided_at": r.decided_at.replace(tzinfo=None) if r.decided_at else None,
            "paid_at": r.paid_at.replace(tzinfo=None) if r.paid_at else None,
            "payroll_ref": r.payroll_ref or "",
        })
    return out


# ════════════════════════════════════════════════════════════════════════════
# SHAPE & SUMMARY
# ════════════════════════════════════════════════════════════════════════════

def shape(report_key: str, rows: list[dict]) -> list[dict]:
    """Currently each fetcher returns rows in their final shape — this hook
    stays in the API so renderers can call `shape(key, rows)` symmetrically
    with attendance_reports."""
    return list(rows)


def shape_summary(report_key: str, rows: list[dict]) -> dict:
    """Per-report headline counters used by the cover page + the JSON preview."""
    if report_key == "leave_register":
        days = sum(r.get("total_days", 0) for r in rows)
        emps = len({r["employee_id"] for r in rows})
        approved = sum(1 for r in rows if r.get("status") == "APPROVED")
        rejected = sum(1 for r in rows if r.get("status") in ("REJECTED", "MANAGER_REJECTED"))
        pending = sum(1 for r in rows if r.get("status", "").startswith("PENDING"))
        return {
            "rows": len(rows), "employees": emps, "days_total": round(days, 1),
            "approved": approved, "pending": pending, "rejected": rejected,
        }
    if report_key == "department_leaves":
        days = sum(r.get("days", 0) for r in rows)
        depts = len({r["department"] for r in rows})
        return {
            "rows": len(rows), "departments": depts,
            "days_total": round(days, 1),
            "employees": sum(r.get("employees_affected", 0) for r in rows),
        }
    if report_key == "balance_report":
        return {
            "rows": len(rows),
            "employees": len({r["employee_id"] for r in rows}),
            "total_quota": round(sum(r.get("quota", 0) for r in rows), 1),
            "total_used": round(sum(r.get("used", 0) for r in rows), 1),
            "total_available": round(sum(r.get("available", 0) for r in rows), 1),
        }
    if report_key == "liability_report":
        return {
            "rows": len(rows),
            "employees": len({r["employee_id"] for r in rows}),
            "total_days": round(sum(r.get("available_days", 0) for r in rows), 1),
            "total_liability": round(sum(r.get("liability_amount", 0) for r in rows), 2),
        }
    if report_key == "comp_off_report":
        return {
            "rows": len(rows),
            "employees": len({r["employee_id"] for r in rows}),
            "total_days": round(sum(r.get("days", 0) for r in rows), 1),
            "auto": sum(1 for r in rows if r.get("source") == "Auto"),
            "manual": sum(1 for r in rows if r.get("source") == "Manual"),
            "expired": sum(1 for r in rows if r.get("is_expired")),
        }
    if report_key == "encashment_report":
        return {
            "rows": len(rows),
            "employees": len({r["employee_id"] for r in rows}),
            "total_days": round(sum(r.get("days_requested", 0) for r in rows), 1),
            "total_amount": round(sum(r.get("amount", 0) for r in rows), 2),
            "paid": sum(1 for r in rows if r.get("status") == "PAID"),
            "pending": sum(1 for r in rows if r.get("status") == "PENDING"),
        }
    return {"rows": len(rows)}
