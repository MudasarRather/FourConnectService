"""Data layer for HR Attendance Reports — fetch + shape.

Pulls one row per (employee, date) from the canonical ``hr_attendance`` table
across the requested date range, joins in employee/department/designation/shift
metadata, then reshapes into the 7 report-specific views the PDF and Excel
generators consume.

Defensive design:
    * Filters out soft-deleted attendance and soft-deleted employees.
    * Always scopes by ``date BETWEEN from AND to``; no implicit "all time".
    * Returns plain dicts (not SQLAlchemy rows) so renderers can be tested
      in isolation without touching the DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Optional
from uuid import UUID

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.hr.attendance import Attendance, AttendanceStatus
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.shift import Shift
from app.models.user import User


REPORT_KEYS = ("monthly", "late", "overtime", "wfh", "compliance", "anomalies", "daily", "breaks")


REPORT_META = {
    "monthly": {
        "name": "Monthly Summary",
        "tagline": "Per-employee attendance digest",
        "subtitle": "Days · hours · overtime — month at a glance",
        # Editorial gold — magazine-style cover
        "accent": "#d97706",
        "accent_soft": "#fef3c7",
        "accent_deep": "#92400e",
        "icon": "M",
        "motif": "editorial",
    },
    "late": {
        "name": "Late Arrivals",
        "tagline": "Punch-in compliance ledger",
        "subtitle": "Every breach of grace, ranked",
        # Caution yellow — bulletin / news style
        "accent": "#ca8a04",
        "accent_soft": "#fef9c3",
        "accent_deep": "#713f12",
        "icon": "L",
        "motif": "bulletin",
    },
    "overtime": {
        "name": "Overtime",
        "tagline": "Hours beyond the call",
        "subtitle": "Approved OT with shift + check-out context",
        # Industrial orange — dashboard / gauge style
        "accent": "#ea580c",
        "accent_soft": "#ffedd5",
        "accent_deep": "#7c2d12",
        "icon": "O",
        "motif": "industrial",
    },
    "wfh": {
        "name": "Work From Home",
        "tagline": "Remote attendance journal",
        "subtitle": "WFH / Remote days · hours logged · weekly cadence",
        # Sky blue — travel postcard style
        "accent": "#0284c7",
        "accent_soft": "#e0f2fe",
        "accent_deep": "#0c4a6e",
        "icon": "H",
        "motif": "postcard",
    },
    "compliance": {
        "name": "Shift Compliance",
        "tagline": "Roster coverage audit",
        "subtitle": "Scheduled vs actual — gaps, missing punches, geo flags",
        # Audit teal — certificate / seal style
        "accent": "#0d9488",
        "accent_soft": "#ccfbf1",
        "accent_deep": "#134e4a",
        "icon": "✓",
        "motif": "certificate",
    },
    "anomalies": {
        "name": "Anomalies",
        "tagline": "Flagged events for review",
        "subtitle": "Missing check-outs · geo-failed · 30+ min late · admin flags",
        # Alert red — investigation / dossier style
        "accent": "#b91c1c",
        "accent_soft": "#fee2e2",
        "accent_deep": "#7f1d1d",
        "icon": "!",
        "motif": "dossier",
    },
    "daily": {
        "name": "Daily Roster",
        "tagline": "Day-by-day audit trail",
        "subtitle": "Every employee, every working day — full punches & status",
        # Blueprint violet — architectural blueprint style
        "accent": "#7c3aed",
        "accent_soft": "#ede9fe",
        "accent_deep": "#4c1d95",
        "icon": "D",
        "motif": "blueprint",
    },
    "breaks": {
        "name": "Breaks",
        "tagline": "Off-the-clock time ledger",
        "subtitle": "Break minutes per day · ratio of break to working hours · longest sessions",
        # Cafe brown — coffee-cup / cafe-receipt style
        "accent": "#92400e",
        "accent_soft": "#fef3c7",
        "accent_deep": "#451a03",
        "icon": "B",
        "motif": "cafe",
    },
}

# Status-to-color ramp shared between PDF rows, Excel cells, and the cover legend.
STATUS_COLORS = {
    "PRESENT":  {"hex": "#0d9488", "light": "#ccfbf1", "deep": "#115e59"},
    "LATE":     {"hex": "#a16207", "light": "#fef9c3", "deep": "#713f12"},
    "HALF_DAY": {"hex": "#c2410c", "light": "#ffedd5", "deep": "#7c2d12"},
    "ABSENT":   {"hex": "#b91c1c", "light": "#fee2e2", "deep": "#7f1d1d"},
    "LEAVE":    {"hex": "#7c3aed", "light": "#ede9fe", "deep": "#4c1d95"},
    "WFH":      {"hex": "#0284c7", "light": "#e0f2fe", "deep": "#0c4a6e"},
    "REMOTE":   {"hex": "#0f766e", "light": "#ccfbf1", "deep": "#134e4a"},
    "WEEK_OFF": {"hex": "#64748b", "light": "#f1f5f9", "deep": "#334155"},
    "HOLIDAY":  {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    "ON_DUTY":  {"hex": "#2563eb", "light": "#dbeafe", "deep": "#1e3a8a"},
}


def report_meta(key: str) -> dict:
    return REPORT_META.get(key) or REPORT_META["daily"]


# ════════════════════════════════════════════════════════════════════════════
# FETCH
# ════════════════════════════════════════════════════════════════════════════


def fetch_rows(
    db: Session,
    from_date: date,
    to_date: date,
    department_id: Optional[UUID] = None,
) -> list[dict]:
    """One row per (employee, date) in [from, to] with full snapshot.

    Returns plain dicts so the renderers don't drag SQLAlchemy state around.
    """
    q = (
        db.query(
            Attendance.id,
            Attendance.date,
            Attendance.check_in_time,
            Attendance.check_out_time,
            Attendance.working_hours,
            Attendance.break_hours,
            Attendance.late_minutes,
            Attendance.early_exit_minutes,
            Attendance.overtime_hours,
            Attendance.status,
            Attendance.source,
            Attendance.geo_verified,
            Attendance.is_flagged,
            Attendance.is_locked,
            Attendance.remarks,
            Employee.id.label("employee_id"),
            Employee.employee_id.label("employee_code"),
            User.full_name.label("employee_name"),
            Department.name.label("department"),
            Designation.name.label("designation"),
            Shift.name.label("shift_name"),
            Shift.start_time.label("shift_start"),
            Shift.end_time.label("shift_end"),
        )
        .join(Employee, Employee.id == Attendance.employee_id)
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .outerjoin(Shift, Shift.id == Attendance.shift_id)
        .filter(
            Attendance.is_deleted == False,  # noqa: E712
            Employee.is_deleted == False,    # noqa: E712
            Attendance.date >= from_date,
            Attendance.date <= to_date,
        )
    )
    if department_id:
        q = q.filter(Employee.department_id == department_id)

    rows = []
    for r in q.order_by(Attendance.date.asc(), User.full_name.asc()).all():
        # Excel (xlsxwriter and openpyxl) refuses tz-aware datetimes — strip
        # tzinfo at the boundary so every renderer sees naive wall-clock IST.
        ci = r.check_in_time.replace(tzinfo=None) if r.check_in_time else None
        co = r.check_out_time.replace(tzinfo=None) if r.check_out_time else None
        rows.append({
            "id": str(r.id),
            "date": r.date,
            "check_in_time": ci,
            "check_out_time": co,
            "working_hours": float(r.working_hours or 0),
            "break_hours": float(r.break_hours or 0),
            "late_minutes": int(r.late_minutes or 0),
            "early_exit_minutes": int(r.early_exit_minutes or 0),
            "overtime_hours": float(r.overtime_hours or 0),
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "source": r.source.value if hasattr(r.source, "value") else str(r.source),
            "geo_verified": bool(r.geo_verified),
            "is_flagged": bool(r.is_flagged),
            "is_locked": bool(r.is_locked),
            "remarks": r.remarks or "",
            "employee_id": str(r.employee_id),
            "employee_code": r.employee_code or "—",
            "employee_name": r.employee_name or "Unknown",
            "department": r.department or "—",
            "designation": r.designation or "—",
            "shift_name": r.shift_name or "—",
            "shift_start": r.shift_start,
            "shift_end": r.shift_end,
        })
    return rows


# ════════════════════════════════════════════════════════════════════════════
# SUMMARY (KPI strip shown on every cover)
# ════════════════════════════════════════════════════════════════════════════


def shape_summary(rows: list[dict]) -> dict:
    present = late = absent = half = leave = wfh = remote = on_duty = week_off = holiday = 0
    ot_hours = 0.0
    late_minutes = 0
    work_hours = 0.0
    emp_ids = set()
    dept_set = set()
    for r in rows:
        emp_ids.add(r["employee_id"])
        if r["department"] != "—":
            dept_set.add(r["department"])
        s = r["status"]
        if s == "PRESENT":
            present += 1
        elif s == "LATE":
            present += 1
            late += 1
        elif s == "HALF_DAY":
            half += 1
        elif s == "ABSENT":
            absent += 1
        elif s == "LEAVE":
            leave += 1
        elif s == "WFH":
            wfh += 1
            present += 1
        elif s == "REMOTE":
            remote += 1
            present += 1
        elif s == "ON_DUTY":
            on_duty += 1
        elif s == "WEEK_OFF":
            week_off += 1
        elif s == "HOLIDAY":
            holiday += 1
        ot_hours += r["overtime_hours"] or 0
        late_minutes += r["late_minutes"] or 0
        work_hours += r["working_hours"] or 0

    total = len(rows)
    workable = total - week_off - holiday  # denominator for on-time rate
    on_time_pct = round(((present - late) / workable) * 100) if workable else 0

    return {
        "rows": total,
        "employees": len(emp_ids),
        "departments": len(dept_set),
        "present": present,
        "late": late,
        "absent": absent,
        "half_day": half,
        "leave": leave,
        "wfh": wfh,
        "remote": remote,
        "on_duty": on_duty,
        "week_off": week_off,
        "holiday": holiday,
        "overtime_hours": round(ot_hours, 2),
        "late_minutes": late_minutes,
        "working_hours": round(work_hours, 1),
        "on_time_pct": on_time_pct,
    }


# ════════════════════════════════════════════════════════════════════════════
# SHAPE — per-report transforms
# ════════════════════════════════════════════════════════════════════════════


def _shape_monthly(rows: list[dict]) -> list[dict]:
    by_emp: dict[str, dict] = {}
    for r in rows:
        key = r["employee_id"]
        if key not in by_emp:
            by_emp[key] = {
                "employee_code": r["employee_code"],
                "employee_name": r["employee_name"],
                "department": r["department"],
                "designation": r["designation"],
                "shift_name": r["shift_name"],
                "present_days": 0,
                "late_days": 0,
                "absent_days": 0,
                "half_days": 0,
                "leave_days": 0,
                "wfh_days": 0,
                "week_offs": 0,
                "holidays": 0,
                "total_working_hours": 0.0,
                "total_break_hours": 0.0,
                "total_late_minutes": 0,
                "total_overtime_hours": 0.0,
            }
        e = by_emp[key]
        s = r["status"]
        if s == "PRESENT":
            e["present_days"] += 1
        elif s == "LATE":
            e["present_days"] += 1
            e["late_days"] += 1
        elif s == "HALF_DAY":
            e["half_days"] += 1
        elif s == "ABSENT":
            e["absent_days"] += 1
        elif s == "LEAVE":
            e["leave_days"] += 1
        elif s in ("WFH", "REMOTE"):
            e["wfh_days"] += 1
            e["present_days"] += 1
        elif s == "WEEK_OFF":
            e["week_offs"] += 1
        elif s == "HOLIDAY":
            e["holidays"] += 1
        e["total_working_hours"] += r["working_hours"] or 0
        e["total_break_hours"] += r["break_hours"] or 0
        e["total_late_minutes"] += r["late_minutes"] or 0
        e["total_overtime_hours"] += r["overtime_hours"] or 0
    out = list(by_emp.values())
    for e in out:
        e["total_working_hours"] = round(e["total_working_hours"], 2)
        e["total_break_hours"] = round(e["total_break_hours"], 2)
        e["total_overtime_hours"] = round(e["total_overtime_hours"], 2)
    out.sort(key=lambda x: x["employee_name"].lower())
    return out


def _shape_late(rows: list[dict]) -> list[dict]:
    return sorted(
        [r for r in rows if (r["late_minutes"] or 0) > 0],
        key=lambda r: -(r["late_minutes"] or 0),
    )


def _shape_overtime(rows: list[dict]) -> list[dict]:
    return sorted(
        [r for r in rows if (r["overtime_hours"] or 0) > 0],
        key=lambda r: -(r["overtime_hours"] or 0),
    )


def _shape_wfh(rows: list[dict]) -> list[dict]:
    return sorted(
        [r for r in rows if r["status"] in ("WFH", "REMOTE")],
        key=lambda r: r["date"],
        reverse=True,
    )


def _shape_compliance(rows: list[dict]) -> list[dict]:
    by_emp: dict[str, dict] = {}
    for r in rows:
        key = r["employee_id"]
        if key not in by_emp:
            by_emp[key] = {
                "employee_code": r["employee_code"],
                "employee_name": r["employee_name"],
                "department": r["department"],
                "shift_name": r["shift_name"],
                "scheduled_days": 0,
                "actual_hours": 0.0,
                "expected_hours": 0.0,
                "missing_punch_days": 0,
                "geo_failed_days": 0,
            }
        e = by_emp[key]
        # Days that *should* have been worked (anything that isn't a day off)
        if r["status"] not in ("ABSENT", "WEEK_OFF", "HOLIDAY"):
            e["scheduled_days"] += 1
            e["expected_hours"] += 8.0
        e["actual_hours"] += r["working_hours"] or 0
        # Scheduled day but never punched in
        if (not r["check_in_time"]
                and r["status"] not in ("ABSENT", "WEEK_OFF", "HOLIDAY", "LEAVE")):
            e["missing_punch_days"] += 1
        # Punched in but geofence failed
        if r["check_in_time"] and not r["geo_verified"]:
            e["geo_failed_days"] += 1
    out = list(by_emp.values())
    for e in out:
        e["actual_hours"] = round(e["actual_hours"], 2)
        e["coverage_pct"] = (
            round((e["actual_hours"] / e["expected_hours"]) * 100)
            if e["expected_hours"] else 0
        )
        e["gap_hours"] = round(e["expected_hours"] - e["actual_hours"], 2)
    out.sort(key=lambda x: x["coverage_pct"])
    return out


def _shape_anomalies(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        reasons = []
        if r["is_flagged"]:
            reasons.append("Flagged")
        if r["check_in_time"] and not r["geo_verified"]:
            reasons.append("Geo-failed")
        if (r["late_minutes"] or 0) > 30:
            reasons.append(f"Late {r['late_minutes']}m")
        if r["check_in_time"] and not r["check_out_time"]:
            reasons.append("No check-out")
        if r["status"] == "ABSENT" and r["shift_name"] != "—":
            reasons.append("Rostered absent")
        if not reasons:
            continue
        item = {**r, "reasons": " · ".join(reasons), "severity": _anom_severity(r)}
        out.append(item)
    out.sort(key=lambda x: (x["severity"], x["date"]), reverse=True)
    return out


def _anom_severity(r: dict) -> int:
    # Higher = more severe (used to sort the dossier)
    score = 0
    if r["is_flagged"]:
        score += 3
    if (r["late_minutes"] or 0) > 60:
        score += 3
    elif (r["late_minutes"] or 0) > 30:
        score += 2
    if r["check_in_time"] and not r["check_out_time"]:
        score += 2
    if r["check_in_time"] and not r["geo_verified"]:
        score += 1
    return score


def _shape_daily(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["date"], r["employee_name"].lower()), reverse=False)


def _shape_breaks(rows: list[dict]) -> list[dict]:
    """Per-day rows where the employee actually took any break time.

    For each row we surface:
        - break_hours: raw break window total (DB-driven)
        - break_minutes: same as int for spreadsheet sums
        - break_ratio_pct: break time as % of (working_hours + break_hours);
          0 if no working time logged.
        - intensity: short / standard / long bucket so the renderers can
          colour-code at a glance.
    """
    out = []
    for r in rows:
        bh = r["break_hours"] or 0.0
        wh = r["working_hours"] or 0.0
        if bh <= 0:
            continue
        denom = wh + bh
        ratio = round((bh / denom) * 100, 1) if denom > 0 else 0
        if bh <= 0.5:
            intensity = "SHORT"
        elif bh <= 1.5:
            intensity = "STANDARD"
        else:
            intensity = "LONG"
        out.append({
            **r,
            "break_minutes": int(round(bh * 60)),
            "break_ratio_pct": ratio,
            "intensity": intensity,
        })
    out.sort(key=lambda x: -(x["break_hours"] or 0))
    return out


SHAPERS = {
    "monthly": _shape_monthly,
    "late": _shape_late,
    "overtime": _shape_overtime,
    "wfh": _shape_wfh,
    "compliance": _shape_compliance,
    "anomalies": _shape_anomalies,
    "daily": _shape_daily,
    "breaks": _shape_breaks,
}


def shape(key: str, rows: list[dict]) -> list[dict]:
    shaper = SHAPERS.get(key) or _shape_daily
    return shaper(rows)
