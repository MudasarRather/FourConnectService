"""Data layer for HR Payroll Reports — fetch, shape, summarise.

Pulls payslips (and their component lines), employee compensation rows and
payroll adjustments for a pay period, joins in employee / department /
designation / grade / location metadata, then reshapes into the 13
report-specific views the PDF covers, body table, Excel sheets and CSV
exporter consume.

Design:
    * Renderers receive plain dicts — never SQLAlchemy rows — so covers and
      sheets can be unit-tested without a DB session.
    * ``build_context`` fetches exactly what each report needs; ``build_full_context``
      fetches everything once for the /preview fan-out.
    * Statutory wage bases (EPF / ESI) are *derived* from the contribution
      amounts on the payslip lines (the wage base isn't stored), so ECR / ESI
      reports stay self-consistent with the slip.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.hr.employee import Employee
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.models.hr.payslip import Payslip
from app.models.hr.payroll_adjustment import PayrollAdjustment, AdjustmentStatus
from app.models.hr.salary_component import ComponentType, StatutoryKind

from .common import month_name, month_short


# ════════════════════════════════════════════════════════════════════════════
# Report registry
# ════════════════════════════════════════════════════════════════════════════

REPORT_KEYS = (
    "register", "salary-sheet", "statutory",
    "pf-ecr", "esi", "professional-tax", "tds-24q",
    "department-cost", "variance", "ctc-summary", "headcount",
    "adjustments", "ytd-earnings",
)

# Treasury palette (matches src/styles/payroll-theme.css) — every report gets a
# distinct accent within the gold / emerald / bronze / orange family.
REPORT_META = {
    "register": {
        "name": "Payroll Register", "tagline": "The master pay ledger",
        "subtitle": "Per-employee gross · deductions · net for the pay period",
        "group": "Core", "icon": "₹", "motif": "ledger",
        "accent": "#b8860b", "accent_soft": "#fdf3d6", "accent_deep": "#6b4e08",
    },
    "salary-sheet": {
        "name": "Salary Sheet", "tagline": "Earnings & deductions breakdown",
        "subtitle": "Head-wise build-up of every employee's pay",
        "group": "Core", "icon": "≣", "motif": "editorial",
        "accent": "#d97706", "accent_soft": "#fef3c7", "accent_deep": "#92400e",
    },
    "statutory": {
        "name": "Statutory Summary", "tagline": "Compliance at a glance",
        "subtitle": "PF · ESI · PT · TDS per employee, ready for filing",
        "group": "Core", "icon": "✓", "motif": "seal",
        "accent": "#0d9488", "accent_soft": "#ccfbf1", "accent_deep": "#134e4a",
    },
    "pf-ecr": {
        "name": "PF ECR", "tagline": "Electronic Challan-cum-Return",
        "subtitle": "EPFO upload format — UAN · wages · EE/ER split · NCP",
        "group": "Statutory filing", "icon": "P", "motif": "govt-pf",
        "accent": "#15803d", "accent_soft": "#dcfce7", "accent_deep": "#14532d",
    },
    "esi": {
        "name": "ESI Contribution Statement", "tagline": "ESIC monthly return",
        "subtitle": "Insurable wages · 0.75% EE · 3.25% ER per member",
        "group": "Statutory filing", "icon": "E", "motif": "govt-esi",
        "accent": "#0369a1", "accent_soft": "#e0f2fe", "accent_deep": "#0c4a6e",
    },
    "professional-tax": {
        "name": "Professional Tax", "tagline": "State-wise PT remittance",
        "subtitle": "PT deducted, grouped by work-location state slab",
        "group": "Statutory filing", "icon": "T", "motif": "slab",
        "accent": "#a16207", "accent_soft": "#fef9c3", "accent_deep": "#713f12",
    },
    "tds-24q": {
        "name": "TDS · Form 24Q", "tagline": "Quarterly TDS statement",
        "subtitle": "PAN-wise tax deducted at source — period & year-to-date",
        "group": "Statutory filing", "icon": "₹", "motif": "dossier",
        "accent": "#9333ea", "accent_soft": "#f3e8ff", "accent_deep": "#581c87",
    },
    "department-cost": {
        "name": "Department Cost", "tagline": "Cost-centre payroll spend",
        "subtitle": "Gross · deductions · net · employer cost by department",
        "group": "Analytics", "icon": "▦", "motif": "industrial",
        "accent": "#ea580c", "accent_soft": "#ffedd5", "accent_deep": "#7c2d12",
    },
    "variance": {
        "name": "Variance Report", "tagline": "Month-over-month movement",
        "subtitle": "Net pay shift versus the prior pay period, per employee",
        "group": "Analytics", "icon": "Δ", "motif": "bulletin",
        "accent": "#ca8a04", "accent_soft": "#fef9c3", "accent_deep": "#713f12",
    },
    "ctc-summary": {
        "name": "CTC Summary", "tagline": "Compensation snapshot",
        "subtitle": "Active CTC build-up — annual · monthly · basic · regime",
        "group": "Analytics", "icon": "◈", "motif": "postcard",
        "accent": "#0891b2", "accent_soft": "#cffafe", "accent_deep": "#155e75",
    },
    "headcount": {
        "name": "Headcount & Cost", "tagline": "Workforce distribution",
        "subtitle": "Heads and pay-cost share across departments",
        "group": "Analytics", "icon": "◷", "motif": "blueprint",
        "accent": "#7c3aed", "accent_soft": "#ede9fe", "accent_deep": "#4c1d95",
    },
    "adjustments": {
        "name": "Adjustments Register", "tagline": "Bonus · incentive · arrears",
        "subtitle": "Every one-off amount posted to the pay run",
        "group": "Adjustments", "icon": "✦", "motif": "ticket",
        "accent": "#e11d48", "accent_soft": "#ffe4e6", "accent_deep": "#881337",
    },
    "ytd-earnings": {
        "name": "Year-to-Date Earnings", "tagline": "Fiscal-year cumulative",
        "subtitle": "Gross · deductions · net · TDS accumulated across the FY",
        "group": "Adjustments", "icon": "∑", "motif": "certificate",
        "accent": "#92400e", "accent_soft": "#fef3c7", "accent_deep": "#451a03",
    },
}


def report_meta(key: str) -> dict:
    return REPORT_META.get(key) or REPORT_META["register"]


# ════════════════════════════════════════════════════════════════════════════
# Period helpers
# ════════════════════════════════════════════════════════════════════════════


def fy_for_period(year: int, month: int) -> str:
    """India FY label, e.g. (2026, 5) → '2026-2027'."""
    sy = year if month >= 4 else year - 1
    return f"{sy}-{sy + 1}"


def _fy_months(fy: str) -> list[tuple[int, int]]:
    sy = int(fy.split("-")[0])
    return [(sy, m) for m in range(4, 13)] + [(sy + 1, m) for m in range(1, 4)]


def prior_period(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def period_dict(year: int, month: int) -> dict:
    fy = fy_for_period(year, month)
    return {
        "year": year, "month": month,
        "label": f"{month_name(month)} {year}",
        "short": f"{month_short(month)} {year}",
        "fy": fy,
    }


# ════════════════════════════════════════════════════════════════════════════
# Low-level helpers
# ════════════════════════════════════════════════════════════════════════════


def _emp_name(emp) -> Optional[str]:
    if emp and emp.user:
        return getattr(emp.user, "full_name", None) or getattr(emp.user, "email", None)
    return None


def _line_amt(slip, kind: StatutoryKind) -> float:
    return float(sum(
        (Decimal(str(l.amount)) for l in slip.lines if l.statutory_kind == kind),
        Decimal("0"),
    ))


def _location(emp) -> tuple[str, str]:
    """(location_name, state) with graceful fallbacks."""
    if emp is None:
        return "—", "—"
    loc = getattr(emp, "work_location", None)
    name = (loc.name if loc else None) or getattr(emp, "work_location_text", None) or "—"
    state = (loc.state if loc else None) or "—"
    return name, state


def _payslip_query(db: Session):
    return (
        db.query(Payslip)
        .options(
            joinedload(Payslip.employee).joinedload(Employee.user),
            joinedload(Payslip.employee).joinedload(Employee.department),
            joinedload(Payslip.employee).joinedload(Employee.designation),
            joinedload(Payslip.employee).joinedload(Employee.grade),
            joinedload(Payslip.employee).joinedload(Employee.work_location),
            joinedload(Payslip.lines),
        )
        .filter(Payslip.is_deleted == False)  # noqa: E712
    )


def fetch_payslips(db: Session, year: int, month: int, department_id: Optional[UUID] = None) -> list:
    q = _payslip_query(db).filter(Payslip.period_year == year, Payslip.period_month == month)
    if department_id:
        q = q.join(Employee, Employee.id == Payslip.employee_id).filter(Employee.department_id == department_id)
    return q.all()


# ════════════════════════════════════════════════════════════════════════════
# Canonical "slip row" — flat dict every slip-based report shapes from
# ════════════════════════════════════════════════════════════════════════════


def _slip_row(s) -> dict:
    e = s.employee
    loc_name, state = _location(e)
    gross = float(s.gross_earnings or 0)
    pf_e = _line_amt(s, StatutoryKind.PF_EMPLOYEE)
    pf_r = _line_amt(s, StatutoryKind.PF_EMPLOYER)
    esi_e = _line_amt(s, StatutoryKind.ESI_EMPLOYEE)
    esi_r = _line_amt(s, StatutoryKind.ESI_EMPLOYER)

    earnings, deductions, statutory, employer = [], [], [], []
    basic = hra = 0.0
    for l in s.lines:
        item = {"code": l.component_code, "name": l.component_name, "amount": float(l.amount or 0)}
        if l.component_code == "BASIC":
            basic = item["amount"]
        elif l.component_code == "HRA":
            hra = item["amount"]
        ct = l.component_type
        if ct == ComponentType.EARNING or ct == ComponentType.REIMBURSEMENT:
            earnings.append(item)
        elif ct == ComponentType.EMPLOYER_CONTRIBUTION:
            employer.append(item)
        elif ct == ComponentType.STATUTORY_DEDUCTION:
            statutory.append(item)
        else:  # DEDUCTION
            deductions.append(item)

    # Derived statutory wage bases (not stored on the slip).
    epf_wage = round(pf_e / 0.12) if pf_e else 0.0
    eps = round(min(epf_wage, 15000) * 0.0833) if epf_wage else 0.0
    er_epf = max(0.0, round(pf_r - eps)) if pf_r else 0.0
    esi_wage = round(esi_e / 0.0075) if esi_e else 0.0

    return {
        "payslip_no": s.payslip_no,
        "employee_id": str(s.employee_id),
        "employee_code": (e.employee_id if e else None) or "—",
        "employee_name": _emp_name(e) or "Unknown",
        "department": (e.department.name if e and e.department else None) or "—",
        "designation": (e.designation.name if e and e.designation else None) or "—",
        "grade": (e.grade.name if e and e.grade else None) or "—",
        "location": loc_name,
        "state": state,
        "working_days": float(s.working_days or 0),
        "paid_days": float(s.paid_days or 0),
        "lop_days": float(s.lop_days or 0),
        "tax_regime": (s.tax_regime.value if s.tax_regime else
                       (e.tax_regime.value if e and e.tax_regime else "—")),
        "gross": gross,
        "deductions_total": float(s.total_deductions or 0),
        "net": float(s.net_pay or 0),
        "employer_cost": float(s.employer_contributions or 0),
        "ctc": float(s.ctc_value or 0),
        "basic": basic,
        "hra": hra,
        "other_earnings": round(gross - basic - hra, 2),
        "pan": s.pan or (e.pan if e else None) or "—",
        "uan": s.uan or (e.uan if e else None) or "—",
        "pf_number": s.pf_number or (e.pf_number if e else None) or "—",
        "esic_number": s.esic_number or (e.esic_number if e else None) or "—",
        "bank_name": s.bank_name or (e.bank_name if e else None) or "—",
        "account_number": s.account_number or (e.account_number if e else None) or "—",
        "ifsc": s.ifsc or (e.ifsc if e else None) or "—",
        "pf_employee": pf_e,
        "pf_employer": pf_r,
        "esi_employee": esi_e,
        "esi_employer": esi_r,
        "pt": _line_amt(s, StatutoryKind.PROFESSIONAL_TAX),
        "tds": _line_amt(s, StatutoryKind.TDS),
        "lwf_employee": _line_amt(s, StatutoryKind.LWF_EMPLOYEE),
        "lwf_employer": _line_amt(s, StatutoryKind.LWF_EMPLOYER),
        # statutory wage bases (derived)
        "epf_wage": float(epf_wage),
        "eps_contribution": float(eps),
        "er_epf_contribution": float(er_epf),
        "esi_wage": float(esi_wage),
        # detailed line groups
        "earnings": earnings,
        "deduction_lines": deductions,
        "statutory_lines": statutory,
        "employer_lines": employer,
    }


def _slip_rows(slips) -> list[dict]:
    rows = [_slip_row(s) for s in slips]
    rows.sort(key=lambda r: r["employee_name"].lower())
    return rows


def fetch_compensations(db: Session, department_id: Optional[UUID] = None) -> list[dict]:
    q = (
        db.query(EmployeeCompensation)
        .options(
            joinedload(EmployeeCompensation.employee).joinedload(Employee.user),
            joinedload(EmployeeCompensation.employee).joinedload(Employee.department),
            joinedload(EmployeeCompensation.employee).joinedload(Employee.designation),
            joinedload(EmployeeCompensation.employee).joinedload(Employee.grade),
        )
        .filter(
            EmployeeCompensation.status == CompensationStatus.ACTIVE,
            EmployeeCompensation.is_deleted == False,  # noqa: E712
        )
    )
    if department_id:
        q = q.join(Employee, Employee.id == EmployeeCompensation.employee_id).filter(
            Employee.department_id == department_id)
    out = []
    for c in q.all():
        e = c.employee
        out.append({
            "employee_code": (e.employee_id if e else None) or "—",
            "employee_name": _emp_name(e) or "Unknown",
            "department": (e.department.name if e and e.department else None) or "—",
            "designation": (e.designation.name if e and e.designation else None) or "—",
            "grade": (e.grade.name if e and e.grade else None) or "—",
            "annual_ctc": float(c.annual_ctc or 0),
            "monthly_ctc": float(c.monthly_ctc or 0),
            "monthly_gross": float(c.monthly_gross or 0),
            "basic": float(c.basic_amount or 0),
            "tax_regime": c.tax_regime.value if c.tax_regime else "—",
            "effective_from": c.effective_from,
            "revision_reason": c.revision_reason or "—",
        })
    out.sort(key=lambda r: -r["annual_ctc"])
    return out


def fetch_adjustments(db: Session, year: int, month: int, department_id: Optional[UUID] = None) -> list[dict]:
    q = (
        db.query(PayrollAdjustment)
        .options(
            joinedload(PayrollAdjustment.employee).joinedload(Employee.user),
            joinedload(PayrollAdjustment.employee).joinedload(Employee.department),
        )
        .filter(
            PayrollAdjustment.is_deleted == False,  # noqa: E712
            PayrollAdjustment.status != AdjustmentStatus.CANCELLED,
            PayrollAdjustment.period_year == year,
            PayrollAdjustment.period_month == month,
        )
    )
    if department_id:
        q = q.join(Employee, Employee.id == PayrollAdjustment.employee_id).filter(
            Employee.department_id == department_id)
    out = []
    for a in q.all():
        e = a.employee
        out.append({
            "employee_code": (e.employee_id if e else None) or "—",
            "employee_name": _emp_name(e) or "Unknown",
            "department": (e.department.name if e and e.department else None) or "—",
            "adjustment_type": a.adjustment_type.value if a.adjustment_type else "—",
            "sub_type": a.sub_type or "—",
            "title": a.title or "—",
            "amount": float(a.amount or 0),
            "is_taxable": bool(a.is_taxable),
            "is_deduction": bool(a.is_deduction),
            "status": a.status.value if a.status else "—",
            "reason": a.reason or "—",
        })
    out.sort(key=lambda r: -r["amount"])
    return out


def fetch_ytd_rows(db: Session, year: int, month: int, department_id: Optional[UUID] = None) -> list[dict]:
    """Per-employee cumulative totals across the fiscal year up to & including
    the requested month."""
    fy = fy_for_period(year, month)
    months = _fy_months(fy)
    # Only months up to the selected period (chronological within FY).
    upto = []
    for (y, m) in months:
        upto.append((y, m))
        if (y, m) == (year, month):
            break
    pairs = set(upto)
    q = _payslip_query(db)
    if department_id:
        q = q.join(Employee, Employee.id == Payslip.employee_id).filter(Employee.department_id == department_id)
    by_emp: dict[str, dict] = {}
    for s in q.all():
        if (s.period_year, s.period_month) not in pairs:
            continue
        eid = str(s.employee_id)
        if eid not in by_emp:
            e = s.employee
            by_emp[eid] = {
                "employee_code": (e.employee_id if e else None) or "—",
                "employee_name": _emp_name(e) or "Unknown",
                "department": (e.department.name if e and e.department else None) or "—",
                "months_paid": 0,
                "ytd_gross": 0.0, "ytd_deductions": 0.0, "ytd_net": 0.0,
                "ytd_tds": 0.0, "ytd_pf": 0.0, "ytd_employer": 0.0,
            }
        r = by_emp[eid]
        r["months_paid"] += 1
        r["ytd_gross"] += float(s.gross_earnings or 0)
        r["ytd_deductions"] += float(s.total_deductions or 0)
        r["ytd_net"] += float(s.net_pay or 0)
        r["ytd_employer"] += float(s.employer_contributions or 0)
        r["ytd_tds"] += _line_amt(s, StatutoryKind.TDS)
        r["ytd_pf"] += _line_amt(s, StatutoryKind.PF_EMPLOYEE)
    out = list(by_emp.values())
    for r in out:
        for k in ("ytd_gross", "ytd_deductions", "ytd_net", "ytd_tds", "ytd_pf", "ytd_employer"):
            r[k] = round(r[k], 2)
    out.sort(key=lambda r: -r["ytd_gross"])
    return out


# ════════════════════════════════════════════════════════════════════════════
# Context
# ════════════════════════════════════════════════════════════════════════════


def build_context(db: Session, key: str, year: int, month: int,
                  department_id: Optional[UUID] = None) -> dict:
    """Fetch exactly what a single report needs for export."""
    ctx = {"key": key, "period": period_dict(year, month), "department_id": department_id}

    if key in ("variance",):
        ctx["slips"] = _slip_rows(fetch_payslips(db, year, month, department_id))
        py, pm = prior_period(year, month)
        ctx["prior_slips"] = _slip_rows(fetch_payslips(db, py, pm, department_id))
        ctx["prior_label"] = period_dict(py, pm)["label"]
    elif key == "ctc-summary":
        ctx["comps"] = fetch_compensations(db, department_id)
    elif key == "adjustments":
        ctx["adjustments"] = fetch_adjustments(db, year, month, department_id)
    elif key == "ytd-earnings":
        ctx["ytd"] = fetch_ytd_rows(db, year, month, department_id)
    elif key == "tds-24q":
        ctx["slips"] = _slip_rows(fetch_payslips(db, year, month, department_id))
        ctx["tds_ytd"] = {r["employee_id"]: r for r in _ytd_by_id(db, year, month, department_id)}
    else:
        ctx["slips"] = _slip_rows(fetch_payslips(db, year, month, department_id))
    return ctx


def build_full_context(db: Session, year: int, month: int,
                       department_id: Optional[UUID] = None) -> dict:
    """Fetch everything once — used by /preview to count all reports cheaply."""
    py, pm = prior_period(year, month)
    return {
        "period": period_dict(year, month),
        "department_id": department_id,
        "slips": _slip_rows(fetch_payslips(db, year, month, department_id)),
        "prior_slips": _slip_rows(fetch_payslips(db, py, pm, department_id)),
        "prior_label": period_dict(py, pm)["label"],
        "comps": fetch_compensations(db, department_id),
        "adjustments": fetch_adjustments(db, year, month, department_id),
        "ytd": fetch_ytd_rows(db, year, month, department_id),
        "tds_ytd": {r["employee_id"]: r for r in _ytd_by_id(db, year, month, department_id)},
    }


def _ytd_by_id(db: Session, year: int, month: int, department_id: Optional[UUID] = None) -> list[dict]:
    """Cumulative TDS + gross per employee_id across the FY (24Q YTD column)."""
    fy = fy_for_period(year, month)
    pairs = {(y, m) for (y, m) in _fy_months(fy) if (y, m) <= (year, month)}
    q = _payslip_query(db)
    if department_id:
        q = q.join(Employee, Employee.id == Payslip.employee_id).filter(Employee.department_id == department_id)
    by: dict[str, dict] = {}
    for s in q.all():
        if (s.period_year, s.period_month) not in pairs:
            continue
        eid = str(s.employee_id)
        r = by.setdefault(eid, {"employee_id": eid, "tds_ytd": 0.0, "gross_ytd": 0.0})
        r["tds_ytd"] += _line_amt(s, StatutoryKind.TDS)
        r["gross_ytd"] += float(s.gross_earnings or 0)
    return list(by.values())


# ════════════════════════════════════════════════════════════════════════════
# SHAPE — per-report row transforms
# ════════════════════════════════════════════════════════════════════════════


def _shape_register(ctx) -> list[dict]:
    return ctx.get("slips", [])


def _shape_salary_sheet(ctx) -> list[dict]:
    return ctx.get("slips", [])


def _shape_statutory(ctx) -> list[dict]:
    rows = []
    for r in ctx.get("slips", []):
        rows.append({
            **r,
            "statutory_total": round(
                r["pf_employee"] + r["pf_employer"] + r["esi_employee"]
                + r["esi_employer"] + r["pt"] + r["tds"], 2),
        })
    return rows


def _shape_pf_ecr(ctx) -> list[dict]:
    rows = []
    for r in ctx.get("slips", []):
        if r["pf_employee"] <= 0 and r["pf_employer"] <= 0:
            continue
        rows.append({
            "uan": r["uan"],
            "employee_code": r["employee_code"],
            "employee_name": r["employee_name"],
            "gross_wages": r["gross"],
            "epf_wages": r["epf_wage"],
            "eps_wages": min(r["epf_wage"], 15000.0),
            "edli_wages": min(r["epf_wage"], 15000.0),
            "ee_pf": r["pf_employee"],
            "er_eps": r["eps_contribution"],
            "er_epf": r["er_epf_contribution"],
            "ncp_days": r["lop_days"],
            "refund": 0.0,
        })
    rows.sort(key=lambda r: r["employee_name"].lower())
    return rows


def _shape_esi(ctx) -> list[dict]:
    rows = []
    for r in ctx.get("slips", []):
        if r["esi_employee"] <= 0 and r["esi_employer"] <= 0:
            continue
        rows.append({
            "esic_number": r["esic_number"],
            "employee_code": r["employee_code"],
            "employee_name": r["employee_name"],
            "esi_wages": r["esi_wage"],
            "ee_esi": r["esi_employee"],
            "er_esi": r["esi_employer"],
            "total_esi": round(r["esi_employee"] + r["esi_employer"], 2),
            "paid_days": r["paid_days"],
        })
    rows.sort(key=lambda r: r["employee_name"].lower())
    return rows


def _shape_professional_tax(ctx) -> list[dict]:
    rows = []
    for r in ctx.get("slips", []):
        if r["pt"] <= 0:
            continue
        rows.append({
            "state": r["state"],
            "location": r["location"],
            "employee_code": r["employee_code"],
            "employee_name": r["employee_name"],
            "gross": r["gross"],
            "pt": r["pt"],
        })
    rows.sort(key=lambda r: (r["state"].lower(), r["employee_name"].lower()))
    return rows


def _shape_tds_24q(ctx) -> list[dict]:
    ytd = ctx.get("tds_ytd", {})
    rows = []
    for r in ctx.get("slips", []):
        y = ytd.get(r["employee_id"], {})
        rows.append({
            "pan": r["pan"],
            "employee_code": r["employee_code"],
            "employee_name": r["employee_name"],
            "taxable_gross": r["gross"],
            "tds_period": r["tds"],
            "tds_ytd": round(y.get("tds_ytd", r["tds"]), 2),
            "gross_ytd": round(y.get("gross_ytd", r["gross"]), 2),
        })
    rows.sort(key=lambda r: -r["tds_ytd"])
    return rows


def _group_by(slips, key_fn, label) -> list[dict]:
    groups: dict[str, dict] = {}
    for r in slips:
        gk = key_fn(r)
        g = groups.setdefault(gk, {
            label: gk, "headcount": 0,
            "gross": 0.0, "deductions": 0.0, "net": 0.0,
            "employer_cost": 0.0, "ctc": 0.0,
        })
        g["headcount"] += 1
        g["gross"] += r["gross"]
        g["deductions"] += r["deductions_total"]
        g["net"] += r["net"]
        g["employer_cost"] += r["employer_cost"]
        g["ctc"] += r["ctc"]
    out = list(groups.values())
    for g in out:
        for k in ("gross", "deductions", "net", "employer_cost", "ctc"):
            g[k] = round(g[k], 2)
        g["avg_net"] = round(g["net"] / g["headcount"], 2) if g["headcount"] else 0.0
        g["total_cost"] = round(g["net"] + g["employer_cost"], 2)
    return out


def _shape_department_cost(ctx) -> list[dict]:
    out = _group_by(ctx.get("slips", []), lambda r: r["department"], "department")
    out.sort(key=lambda g: -g["total_cost"])
    return out


def _shape_headcount(ctx) -> list[dict]:
    slips = ctx.get("slips", [])
    out = _group_by(slips, lambda r: r["department"], "department")
    total_heads = sum(g["headcount"] for g in out) or 1
    total_cost = sum(g["total_cost"] for g in out) or 1
    for g in out:
        g["headcount_pct"] = round(g["headcount"] / total_heads * 100, 1)
        g["cost_pct"] = round(g["total_cost"] / total_cost * 100, 1)
        g["avg_cost"] = round(g["total_cost"] / g["headcount"], 2) if g["headcount"] else 0.0
    out.sort(key=lambda g: -g["headcount"])
    return out


def _shape_variance(ctx) -> list[dict]:
    prior = {r["employee_id"]: r for r in ctx.get("prior_slips", [])}
    curr = {r["employee_id"]: r for r in ctx.get("slips", [])}
    ids = set(prior) | set(curr)
    rows = []
    for eid in ids:
        c = curr.get(eid)
        p = prior.get(eid)
        base = c or p
        curr_net = c["net"] if c else 0.0
        prev_net = p["net"] if p else 0.0
        delta = round(curr_net - prev_net, 2)
        pct = round(delta / prev_net * 100, 1) if prev_net else (100.0 if curr_net else 0.0)
        status = "JOINED" if not p else ("EXITED" if not c else ("UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT"))
        rows.append({
            "employee_code": base["employee_code"],
            "employee_name": base["employee_name"],
            "department": base["department"],
            "prev_net": prev_net,
            "curr_net": curr_net,
            "delta": delta,
            "delta_pct": pct,
            "status": status,
        })
    rows.sort(key=lambda r: -abs(r["delta"]))
    return rows


def _shape_ctc_summary(ctx) -> list[dict]:
    return ctx.get("comps", [])


def _shape_adjustments(ctx) -> list[dict]:
    return ctx.get("adjustments", [])


def _shape_ytd_earnings(ctx) -> list[dict]:
    return ctx.get("ytd", [])


SHAPERS = {
    "register": _shape_register,
    "salary-sheet": _shape_salary_sheet,
    "statutory": _shape_statutory,
    "pf-ecr": _shape_pf_ecr,
    "esi": _shape_esi,
    "professional-tax": _shape_professional_tax,
    "tds-24q": _shape_tds_24q,
    "department-cost": _shape_department_cost,
    "variance": _shape_variance,
    "ctc-summary": _shape_ctc_summary,
    "headcount": _shape_headcount,
    "adjustments": _shape_adjustments,
    "ytd-earnings": _shape_ytd_earnings,
}


def shape(key: str, ctx: dict) -> list[dict]:
    fn = SHAPERS.get(key) or _shape_register
    return fn(ctx)


# ════════════════════════════════════════════════════════════════════════════
# SUMMARY — KPI dict per report (covers + preview)
# ════════════════════════════════════════════════════════════════════════════


def _slip_aggregate(slips) -> dict:
    gross = sum(r["gross"] for r in slips)
    ded = sum(r["deductions_total"] for r in slips)
    net = sum(r["net"] for r in slips)
    empr = sum(r["employer_cost"] for r in slips)
    ctc = sum(r["ctc"] for r in slips)
    return {
        "employees": len({r["employee_id"] for r in slips}),
        "headcount": len(slips),
        "departments": len({r["department"] for r in slips if r["department"] != "—"}),
        "gross": round(gross, 2),
        "deductions": round(ded, 2),
        "net": round(net, 2),
        "employer_cost": round(empr, 2),
        "ctc": round(ctc, 2),
        "total_cost": round(net + empr, 2),
        "pf": round(sum(r["pf_employee"] + r["pf_employer"] for r in slips), 2),
        "esi": round(sum(r["esi_employee"] + r["esi_employer"] for r in slips), 2),
        "pt": round(sum(r["pt"] for r in slips), 2),
        "tds": round(sum(r["tds"] for r in slips), 2),
        "avg_net": round(net / len(slips), 2) if slips else 0.0,
    }


def shape_summary(key: str, ctx: dict) -> dict:
    """Per-report KPI dict, plus a few generic keys used by the cover tiles."""
    if key == "ctc-summary":
        comps = ctx.get("comps", [])
        annual = sum(c["annual_ctc"] for c in comps)
        return {
            "employees": len(comps),
            "rows": len(comps),
            "annual_ctc": round(annual, 2),
            "monthly_ctc": round(sum(c["monthly_ctc"] for c in comps), 2),
            "avg_ctc": round(annual / len(comps), 2) if comps else 0.0,
        }
    if key == "adjustments":
        adj = ctx.get("adjustments", [])
        earn = sum(a["amount"] for a in adj if not a["is_deduction"])
        ded = sum(a["amount"] for a in adj if a["is_deduction"])
        return {
            "employees": len({a["employee_code"] for a in adj}),
            "rows": len(adj),
            "additions": round(earn, 2),
            "deductions": round(ded, 2),
            "net_impact": round(earn - ded, 2),
        }
    if key == "ytd-earnings":
        ytd = ctx.get("ytd", [])
        return {
            "employees": len(ytd),
            "rows": len(ytd),
            "ytd_gross": round(sum(r["ytd_gross"] for r in ytd), 2),
            "ytd_net": round(sum(r["ytd_net"] for r in ytd), 2),
            "ytd_tds": round(sum(r["ytd_tds"] for r in ytd), 2),
        }
    if key == "variance":
        rows = _shape_variance(ctx)
        agg = _slip_aggregate(ctx.get("slips", []))
        agg["rows"] = len(rows)
        agg["movers"] = len([r for r in rows if r["status"] in ("UP", "DOWN")])
        agg["net_delta"] = round(sum(r["delta"] for r in rows), 2)
        return agg
    # default — slip aggregate
    agg = _slip_aggregate(ctx.get("slips", []))
    agg["rows"] = len(shape(key, ctx))
    return agg
