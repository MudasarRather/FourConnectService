"""HR Shift Reports — data layer.

Unlike attendance reports (one raw row set shaped many ways), each shift report
draws from a DIFFERENT source table, so we dispatch to a per-report builder.
Each builder returns ``{"rows": [...plain dicts...], "summary": {...}}`` — the
renderers (pdf/excel/csv) never touch SQLAlchemy state.

Reports map 1:1 to the Shifts workspace pages so the trail is auditable:
    roster     → Assignment / Management
    coverage   → Coverage
    overtime   → Overtime Rules (× attendance OT requests)
    night      → Night Shifts
    rotation   → Rotation
    workforce  → Workforce
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.hr.shift import Shift, EmployeeShiftAssignment, ShiftType
from app.models.hr.shift_rotation import ShiftRotation
from app.models.hr.shift_coverage import ShiftCoverageRule
from app.models.hr.overtime import OvertimeRequest, OtStatus
from app.models.hr.overtime_rule import OvertimeRule
from app.models.hr.night_policy import NightShiftPolicy
from app.models.hr.workforce_demand import WorkforceDemand
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.user import User

REPORT_KEYS = ("roster", "coverage", "overtime", "night", "rotation", "workforce")

# Each report owns a unique visual identity (motif → a magazine cover renderer).
REPORT_META = {
    "roster": {
        "name": "Shift Roster",
        "tagline": "Who works when — the operating schedule",
        "subtitle": "Every active shift assignment with window, type and department",
        "accent": "#d97706", "accent_soft": "#fef3c7", "accent_deep": "#92400e",
        "icon": "R", "motif": "dispatch",
    },
    "coverage": {
        "name": "Coverage & Staffing",
        "tagline": "Required vs assigned — where the gaps are",
        "subtitle": "Per-rule head-count, shortfalls and critical posts",
        "accent": "#ea580c", "accent_soft": "#ffedd5", "accent_deep": "#7c2d12",
        "icon": "C", "motif": "radar",
    },
    "overtime": {
        "name": "Overtime Ledger",
        "tagline": "Hours beyond the shift, costed",
        "subtitle": "Approved OT scored through the multiplier engine",
        "accent": "#b45309", "accent_soft": "#fef3c7", "accent_deep": "#451a03",
        "icon": "O", "motif": "ledger",
    },
    "night": {
        "name": "Night Shift Operations",
        "tagline": "After-dark workforce & allowances",
        "subtitle": "Night-shift crew with allowance, transport and meal eligibility",
        "accent": "#f59e0b", "accent_soft": "#fde68a", "accent_deep": "#78350f",
        "icon": "N", "motif": "nocturne",
    },
    "rotation": {
        "name": "Rotation Schedule",
        "tagline": "Cyclical shift patterns in motion",
        "subtitle": "Rotations with cadence, steps, crew and current phase",
        "accent": "#ca8a04", "accent_soft": "#fef9c3", "accent_deep": "#713f12",
        "icon": "↻", "motif": "orbit",
    },
    "workforce": {
        "name": "Workforce Demand",
        "tagline": "Demand vs supply — the staffing forecast",
        "subtitle": "Required head-count against assigned capacity, per post",
        "accent": "#c2410c", "accent_soft": "#ffedd5", "accent_deep": "#7c2d12",
        "icon": "W", "motif": "forecast",
    },
}


def report_meta(key: str) -> dict:
    return REPORT_META.get(key) or REPORT_META["roster"]


# ── shared lookups ──────────────────────────────────────────────────────────
def _emp_map(db: Session) -> dict:
    """{employee_id: {name, code, dept_id}} for all non-deleted employees."""
    rows = (db.query(Employee.id, Employee.employee_id, Employee.department_id,
                     Employee.monthly_ctc, User.full_name)
            .outerjoin(User, User.id == Employee.user_id)
            .filter(Employee.is_deleted == False)  # noqa: E712
            .all())
    out = {}
    for eid, code, dept_id, ctc, name in rows:
        out[eid] = {"name": name or "—", "code": code or "—",
                    "dept_id": dept_id, "ctc": float(ctc) if ctc is not None else None}
    return out


def _dept_map(db: Session) -> dict:
    return {d.id: d.name for d in db.query(Department).filter(Department.is_deleted == False).all()}  # noqa: E712


def _shift_map(db: Session) -> dict:
    out = {}
    for s in db.query(Shift).filter(Shift.is_deleted == False).all():  # noqa: E712
        out[s.id] = {
            "code": s.code, "name": s.name,
            "type": s.shift_type.value if hasattr(s.shift_type, "value") else str(s.shift_type),
            "start": s.start_time.strftime("%H:%M") if s.start_time else "—",
            "end": s.end_time.strftime("%H:%M") if s.end_time else "—",
            "night": bool(s.night_allowance) or (str(getattr(s.shift_type, "value", s.shift_type)) == "NIGHT"),
        }
    return out


def _assigned_count(db: Session, shift_id: UUID, dept_id: Optional[UUID], on: date) -> int:
    """Distinct employees with an active assignment to a shift on a date,
    optionally scoped to a department. Mirrors the coverage-alerts logic."""
    q = (db.query(func.count(func.distinct(EmployeeShiftAssignment.employee_id)))
         .filter(EmployeeShiftAssignment.shift_id == shift_id,
                 EmployeeShiftAssignment.effective_from <= on,
                 or_(EmployeeShiftAssignment.effective_until.is_(None),
                     EmployeeShiftAssignment.effective_until >= on)))
    if dept_id:
        q = q.join(Employee, Employee.id == EmployeeShiftAssignment.employee_id).filter(
            Employee.department_id == dept_id)
    return q.scalar() or 0


# ── per-report builders ───────────────────────────────────────────────────────
def _build_roster(db, frm, to, dept_id) -> dict:
    emps, depts, shifts = _emp_map(db), _dept_map(db), _shift_map(db)
    q = (db.query(EmployeeShiftAssignment)
         .filter(EmployeeShiftAssignment.effective_from <= to,
                 or_(EmployeeShiftAssignment.effective_until.is_(None),
                     EmployeeShiftAssignment.effective_until >= frm))
         .order_by(EmployeeShiftAssignment.effective_from.desc()))
    rows = []
    for a in q.all():
        emp = emps.get(a.employee_id) or {}
        if dept_id and emp.get("dept_id") != dept_id:
            continue
        sh = shifts.get(a.shift_id)
        if not sh:
            # Assignment references a deleted/removed shift (or an OFF day with no
            # shift). It is not part of the operating roster — including it as a
            # "—" row would inflate the distinct-shift and shift-type counts
            # (e.g. show 3 shifts when only 2 are live). Skip stale rows.
            continue
        rows.append({
            "employee_code": emp.get("code", "—"),
            "employee_name": emp.get("name", "—"),
            "department": depts.get(emp.get("dept_id"), "—"),
            "shift_code": sh.get("code", "—"),
            "shift_name": sh.get("name", "—"),
            "shift_type": sh.get("type", "—"),
            "window": f"{sh.get('start','—')}–{sh.get('end','—')}",
            "effective_from": a.effective_from.isoformat() if a.effective_from else "—",
            "effective_until": a.effective_until.isoformat() if a.effective_until else "open",
            "is_default": bool(a.is_default),
            "is_night": sh.get("night", False),
        })
    rows.sort(key=lambda r: (r["department"], r["employee_name"]))
    by_type = {}
    for r in rows:
        by_type[r["shift_type"]] = by_type.get(r["shift_type"], 0) + 1
    summary = {
        "rows": len(rows),
        "employees": len({r["employee_code"] for r in rows}),
        "shifts": len({r["shift_code"] for r in rows}),
        "departments": len({r["department"] for r in rows if r["department"] != "—"}),
        "night": sum(1 for r in rows if r["is_night"]),
        "open_ended": sum(1 for r in rows if r["effective_until"] == "open"),
        "by_type": by_type,
    }
    return {"rows": rows, "summary": summary}


def _build_coverage(db, frm, to, dept_id) -> dict:
    depts, shifts = _dept_map(db), _shift_map(db)
    q = db.query(ShiftCoverageRule).filter(
        ShiftCoverageRule.is_deleted == False, ShiftCoverageRule.is_active == True)  # noqa: E712
    if dept_id:
        q = q.filter(ShiftCoverageRule.department_id == dept_id)
    rows = []
    for rule in q.all():
        sh = shifts.get(rule.shift_id) or {}
        assigned = _assigned_count(db, rule.shift_id, rule.department_id, to)
        shortfall = max(0, (rule.min_staff or 0) - assigned)
        status = "OK" if shortfall == 0 else ("CRITICAL" if rule.critical else "WARN")
        rows.append({
            "shift_code": sh.get("code", "—"),
            "shift_name": sh.get("name", "—"),
            "department": depts.get(rule.department_id, "All depts"),
            "label": rule.label or "—",
            "min_staff": rule.min_staff or 0,
            "assigned": assigned,
            "shortfall": shortfall,
            "critical": bool(rule.critical),
            "status": status,
            "coverage_pct": round(min(1.0, assigned / rule.min_staff) * 100) if rule.min_staff else 100,
        })
    rows.sort(key=lambda r: (-r["shortfall"], not r["critical"], r["shift_name"]))
    summary = {
        "rows": len(rows),
        "critical": sum(1 for r in rows if r["status"] == "CRITICAL"),
        "warn": sum(1 for r in rows if r["status"] == "WARN"),
        "covered": sum(1 for r in rows if r["status"] == "OK"),
        "total_shortfall": sum(r["shortfall"] for r in rows),
        "required": sum(r["min_staff"] for r in rows),
        "assigned": sum(r["assigned"] for r in rows),
        "on_date": to.isoformat(),
    }
    return {"rows": rows, "summary": summary}


def _build_overtime(db, frm, to, dept_id) -> dict:
    emps, depts = _emp_map(db), _dept_map(db)
    # highest-priority active OT rule per type (mirrors /overtime-rules/resolve)
    rule_by_type = {}
    for r in (db.query(OvertimeRule)
              .filter(OvertimeRule.is_deleted == False, OvertimeRule.is_active == True)  # noqa: E712
              .order_by(OvertimeRule.priority.desc(), OvertimeRule.created_at.desc()).all()):
        rule_by_type.setdefault(r.ot_type.value if hasattr(r.ot_type, "value") else str(r.ot_type), r)

    q = (db.query(OvertimeRequest)
         .filter(OvertimeRequest.is_deleted == False,  # noqa: E712
                 OvertimeRequest.status == OtStatus.APPROVED,
                 OvertimeRequest.date >= frm, OvertimeRequest.date <= to))
    agg = {}  # employee_id -> accumulator
    for req in q.all():
        emp = emps.get(req.employee_id) or {}
        if dept_id and emp.get("dept_id") != dept_id:
            continue
        ot_type = req.ot_type.value if hasattr(req.ot_type, "value") else str(req.ot_type)
        rule = rule_by_type.get(ot_type)
        mult = float(rule.multiplier) if rule else 1.0
        cap = float(rule.max_ot_hours) if (rule and rule.max_ot_hours is not None) else None
        hrs = float(req.ot_hours or 0)
        payable = min(hrs, cap) if cap is not None else hrs
        ctc = emp.get("ctc")
        hourly = (ctc / 26 / 8) if ctc else None
        cost = round(payable * hourly * mult, 2) if hourly else None
        a = agg.setdefault(req.employee_id, {
            "employee_code": emp.get("code", "—"), "employee_name": emp.get("name", "—"),
            "department": depts.get(emp.get("dept_id"), "—"),
            "ot_hours": 0.0, "payable_hours": 0.0, "weighted_hours": 0.0,
            "est_cost": 0.0, "has_cost": False, "occurrences": 0, "peak_mult": 0.0,
        })
        a["ot_hours"] += hrs
        a["payable_hours"] += payable
        a["weighted_hours"] += payable * mult
        a["occurrences"] += 1
        a["peak_mult"] = max(a["peak_mult"], mult)
        if cost is not None:
            a["est_cost"] += cost
            a["has_cost"] = True
    rows = list(agg.values())
    for r in rows:
        r["ot_hours"] = round(r["ot_hours"], 2)
        r["payable_hours"] = round(r["payable_hours"], 2)
        r["weighted_hours"] = round(r["weighted_hours"], 2)
        r["est_cost"] = round(r["est_cost"], 2) if r["has_cost"] else None
        r["peak_mult"] = round(r["peak_mult"], 2)
    rows.sort(key=lambda r: -r["payable_hours"])
    summary = {
        "rows": len(rows),
        "employees": len(rows),
        "ot_hours": round(sum(r["ot_hours"] for r in rows), 2),
        "payable_hours": round(sum(r["payable_hours"] for r in rows), 2),
        "weighted_hours": round(sum(r["weighted_hours"] for r in rows), 2),
        "est_cost": round(sum(r["est_cost"] or 0 for r in rows), 2),
        "occurrences": sum(r["occurrences"] for r in rows),
        "rules_active": len(rule_by_type),
    }
    return {"rows": rows, "summary": summary}


def _build_night(db, frm, to, dept_id) -> dict:
    emps, depts, shifts = _emp_map(db), _dept_map(db), _shift_map(db)
    policies = {p.shift_id: p for p in db.query(NightShiftPolicy).filter(
        NightShiftPolicy.is_deleted == False).all()}  # noqa: E712
    night_ids = {sid for sid, sh in shifts.items() if sh["night"]}
    q = (db.query(EmployeeShiftAssignment)
         .filter(EmployeeShiftAssignment.shift_id.in_(night_ids or [None]),
                 EmployeeShiftAssignment.effective_from <= to,
                 or_(EmployeeShiftAssignment.effective_until.is_(None),
                     EmployeeShiftAssignment.effective_until >= frm)))
    seen, rows = set(), []
    for a in q.all():
        emp = emps.get(a.employee_id) or {}
        if dept_id and emp.get("dept_id") != dept_id:
            continue
        key = (a.employee_id, a.shift_id)
        if key in seen:
            continue
        seen.add(key)
        sh = shifts.get(a.shift_id) or {}
        pol = policies.get(a.shift_id)
        rows.append({
            "employee_code": emp.get("code", "—"),
            "employee_name": emp.get("name", "—"),
            "department": depts.get(emp.get("dept_id"), "—"),
            "shift_code": sh.get("code", "—"),
            "shift_name": sh.get("name", "—"),
            "window": f"{sh.get('start','—')}–{sh.get('end','—')}",
            "allowance": float(pol.allowance_amount) if pol else 0.0,
            "ot_rate": float(pol.overtime_rate) if pol else 1.5,
            "transport": bool(pol.transport_required) if pol else False,
            "meal": bool(pol.meal_eligible) if pol else False,
            "has_policy": pol is not None,
        })
    rows.sort(key=lambda r: (r["department"], r["employee_name"]))
    summary = {
        "rows": len(rows),
        "employees": len({r["employee_code"] for r in rows}),
        "shifts": len({r["shift_code"] for r in rows}),
        "with_policy": sum(1 for r in rows if r["has_policy"]),
        "transport": sum(1 for r in rows if r["transport"]),
        "meal": sum(1 for r in rows if r["meal"]),
        "allowance_per_night": round(sum(r["allowance"] for r in rows), 2),
    }
    return {"rows": rows, "summary": summary}


def _build_rotation(db, frm, to, dept_id) -> dict:
    depts, shifts = _dept_map(db), _shift_map(db)
    q = db.query(ShiftRotation).filter(
        ShiftRotation.is_deleted == False, ShiftRotation.is_active == True)  # noqa: E712
    rows = []
    for rot in q.all():
        steps = sorted(rot.steps, key=lambda s: s.sequence)
        members = list(rot.members)
        n = len(steps) or 1
        cur = (rot.current_step_index or 0) % n
        cur_step = steps[cur] if steps else None
        cur_label = (cur_step.label if cur_step and cur_step.label
                     else (shifts.get(cur_step.shift_id, {}).get("name", "OFF") if cur_step else "—"))
        if dept_id and rot.department_ids and str(dept_id) not in [str(x) for x in rot.department_ids]:
            continue
        rows.append({
            "name": rot.name,
            "code": rot.code or "—",
            "cycle": rot.cycle.value if hasattr(rot.cycle, "value") else str(rot.cycle),
            "frequency_days": rot.frequency_days or 7,
            "steps": len(steps),
            "members": len(members),
            "step_shifts": " → ".join(
                (s.label or shifts.get(s.shift_id, {}).get("code", "OFF")) for s in steps) or "—",
            "current_step": f"{cur + 1}/{len(steps)}" if steps else "—",
            "current_label": cur_label,
            "anchor_date": rot.anchor_date.isoformat() if rot.anchor_date else "—",
            "last_advanced": rot.last_advanced_on.isoformat() if rot.last_advanced_on else "never",
            "departments": ", ".join(depts.get(UUID(str(x)), str(x)) for x in (rot.department_ids or [])) or "All",
        })
    rows.sort(key=lambda r: r["name"])
    by_cycle = {}
    for r in rows:
        by_cycle[r["cycle"]] = by_cycle.get(r["cycle"], 0) + 1
    summary = {
        "rows": len(rows),
        "members": sum(r["members"] for r in rows),
        "total_steps": sum(r["steps"] for r in rows),
        "by_cycle": by_cycle,
        "active_now": sum(1 for r in rows if r["last_advanced"] != "never"),
    }
    return {"rows": rows, "summary": summary}


def _build_workforce(db, frm, to, dept_id) -> dict:
    depts, shifts = _dept_map(db), _shift_map(db)
    q = (db.query(WorkforceDemand)
         .filter(WorkforceDemand.is_deleted == False, WorkforceDemand.is_active == True,  # noqa: E712
                 WorkforceDemand.valid_from <= to,
                 or_(WorkforceDemand.valid_to.is_(None), WorkforceDemand.valid_to >= frm)))
    if dept_id:
        q = q.filter(WorkforceDemand.department_id == dept_id)
    rows = []
    for d in q.all():
        sh = shifts.get(d.shift_id) or {}
        assigned = _assigned_count(db, d.shift_id, d.department_id, to)
        required = d.required_headcount or 0
        shortfall = max(0, required - assigned)
        rows.append({
            "shift_code": sh.get("code", "—"),
            "shift_name": sh.get("name", "—"),
            "department": depts.get(d.department_id, "All depts"),
            "skill": d.skill or "—",
            "required": required,
            "assigned": assigned,
            "shortfall": shortfall,
            "surplus": max(0, assigned - required),
            "coverage_pct": round(min(1.0, assigned / required) * 100) if required else 100,
            "valid_from": d.valid_from.isoformat() if d.valid_from else "—",
            "valid_to": d.valid_to.isoformat() if d.valid_to else "open",
            "status": "OK" if shortfall == 0 else "GAP",
        })
    rows.sort(key=lambda r: (-r["shortfall"], r["shift_name"]))
    tot_req = sum(r["required"] for r in rows)
    tot_asg = sum(r["assigned"] for r in rows)
    worst = max(rows, key=lambda r: r["shortfall"]) if rows else None
    summary = {
        "rows": len(rows),
        "required": tot_req,
        "assigned": tot_asg,
        "shortfall": sum(r["shortfall"] for r in rows),
        "coverage_pct": round(min(1.0, tot_asg / tot_req) * 100) if tot_req else 100,
        "gaps": sum(1 for r in rows if r["status"] == "GAP"),
        "worst_shift": worst["shift_name"] if worst and worst["shortfall"] else "—",
        "worst_shortfall": worst["shortfall"] if worst else 0,
        "on_date": to.isoformat(),
    }
    return {"rows": rows, "summary": summary}


_BUILDERS = {
    "roster": _build_roster,
    "coverage": _build_coverage,
    "overtime": _build_overtime,
    "night": _build_night,
    "rotation": _build_rotation,
    "workforce": _build_workforce,
}


def build_report(db: Session, key: str, frm: date, to: date,
                 department_id: Optional[UUID] = None) -> dict:
    """Return {'rows': [...], 'summary': {...}} for the requested report."""
    builder = _BUILDERS.get(key)
    if not builder:
        raise KeyError(key)
    return builder(db, frm, to, department_id)
