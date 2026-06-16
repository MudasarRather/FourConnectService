"""HR Payroll — per-employee statutory/tax aggregation for a fiscal year.

Shared by the self-service tax-summary endpoint and the Form-16 PDF generator,
so the roll-up of an employee's OWN RELEASED payslip statutory lines lives in
exactly one place. Pure read; never mutates.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.hr.employee import Employee
from app.models.hr.payslip import Payslip, PayslipStatus
from app.models.hr.salary_component import StatutoryKind

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fy_month_range(fy: str):
    """[(year, month), …] for Apr(start)…Mar(start+1) of an FY label like '2025-26'."""
    sy = int(str(fy).split("-")[0])
    return [(sy, m) for m in range(4, 13)] + [(sy + 1, m) for m in range(1, 4)]


def _d(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal(0)


def aggregate_statutory(db: Session, emp: Employee, fy: str) -> dict:
    """Roll up the employee's RELEASED payslips for ``fy`` by statutory_kind.

    Returns per-head totals, a month-by-month series, headline gross/deductions/
    net, and the identity snapshot (PAN/UAN/PF/ESIC/regime) preferring the latest
    payslip's snapshot, falling back to the Employee record.
    """
    seq = fy_month_range(fy)
    seq_set = set(seq)
    rows = (
        db.query(Payslip)
        .options(joinedload(Payslip.lines))
        .filter(
            Payslip.employee_id == emp.id,
            Payslip.is_deleted == False,  # noqa: E712
            Payslip.status == PayslipStatus.RELEASED,
        )
        .all()
    )
    by_key = {(r.period_year, r.period_month): r for r in rows
              if (r.period_year, r.period_month) in seq_set}

    totals = {
        "tds": Decimal(0), "pf_employee": Decimal(0), "pf_employer": Decimal(0),
        "esi_employee": Decimal(0), "esi_employer": Decimal(0),
        "professional_tax": Decimal(0), "lwf": Decimal(0),
    }
    gross = ded = net = Decimal(0)
    months = []
    latest = None

    for (y, m) in seq:
        r = by_key.get((y, m))
        mt = {"tds": Decimal(0), "pf": Decimal(0), "esi": Decimal(0), "pt": Decimal(0), "gross": Decimal(0)}
        if r:
            gross += _d(r.gross_earnings)
            ded += _d(r.total_deductions)
            net += _d(r.net_pay)
            mt["gross"] = _d(r.gross_earnings)
            for ln in r.lines:
                k = ln.statutory_kind
                if not k:
                    continue
                amt = _d(ln.amount)
                if k == StatutoryKind.TDS:
                    totals["tds"] += amt; mt["tds"] += amt
                elif k == StatutoryKind.PF_EMPLOYEE:
                    totals["pf_employee"] += amt; mt["pf"] += amt
                elif k == StatutoryKind.PF_EMPLOYER:
                    totals["pf_employer"] += amt
                elif k == StatutoryKind.ESI_EMPLOYEE:
                    totals["esi_employee"] += amt; mt["esi"] += amt
                elif k == StatutoryKind.ESI_EMPLOYER:
                    totals["esi_employer"] += amt
                elif k == StatutoryKind.PROFESSIONAL_TAX:
                    totals["professional_tax"] += amt; mt["pt"] += amt
                elif k in (StatutoryKind.LWF_EMPLOYEE, StatutoryKind.LWF_EMPLOYER):
                    totals["lwf"] += amt
            if latest is None or (y, m) > (latest.period_year, latest.period_month):
                latest = r
        months.append({
            "month": m, "year": y, "label": _MONTHS[m],
            "tds": str(mt["tds"]), "pf": str(mt["pf"]), "esi": str(mt["esi"]),
            "pt": str(mt["pt"]), "gross": str(mt["gross"]),
        })

    def pick(slip_attr: str, emp_attr: Optional[str] = None):
        v = getattr(latest, slip_attr, None) if latest else None
        if not v:
            v = getattr(emp, emp_attr or slip_attr, None)
        return v

    regime = (latest.tax_regime if latest and latest.tax_regime else getattr(emp, "tax_regime", None))
    regime = regime.value if regime is not None and hasattr(regime, "value") else regime

    return {
        "fiscal_year": fy,
        "regime": regime,
        "pan": pick("pan"),
        "uan": pick("uan"),
        "pf_number": pick("pf_number"),
        "esic_number": pick("esic_number"),
        **{k: v for k, v in totals.items()},
        "gross": gross,
        "total_deductions": ded,
        "net_pay": net,
        "slips_count": len(by_key),
        "months": months,
    }
