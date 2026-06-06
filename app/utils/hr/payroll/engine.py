"""Payroll compute engine — pure (no DB writes).

``compute_payslip`` takes resolved components + the employee's compensation +
the month's attendance LOP and returns a header dict + per-component line dicts.
The router persists those into Payslip / PayslipLine rows.

Component resolution order is sequence-driven (seeds set BASIC=10, HRA=20,
allowances=30-40, statutory=50-70, employer=80) so PERCENT_OF / FORMULA heads
only ever reference already-computed codes. GROSS_TARGET (monthly gross) is
resolved with a 2-pass derivation so the BALANCE (special allowance) head can
absorb the remainder of gross even before employer contributions are known.
"""
from __future__ import annotations

import calendar
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

from app.models.hr.salary_component import ComponentType, CalcType, StatutoryKind
from app.utils.hr.payroll.formula import evaluate_formula
from app.utils.hr.payroll import statutory as stat

Q2 = Decimal("0.01")
_EARNING_TYPES = {ComponentType.EARNING, ComponentType.REIMBURSEMENT}


def _q(v) -> Decimal:
    d = v if isinstance(v, Decimal) else Decimal(str(v or 0))
    return d.quantize(Q2, rounding=ROUND_HALF_UP)


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _statutory_amount(kind, *, pf_wage_paid: Decimal, gross_paid: Decimal,
                      annual_projection: Decimal, regime: str, declarations: Optional[Dict],
                      cfg: Dict, remaining_months: int) -> Decimal:
    if kind in (StatutoryKind.PF_EMPLOYEE, StatutoryKind.PF_EMPLOYER):
        return stat.calc_pf(pf_wage_paid, cfg)
    if kind == StatutoryKind.ESI_EMPLOYEE:
        return stat.calc_esi(gross_paid, cfg, employer=False)
    if kind == StatutoryKind.ESI_EMPLOYER:
        return stat.calc_esi(gross_paid, cfg, employer=True)
    if kind == StatutoryKind.PROFESSIONAL_TAX:
        return stat.calc_professional_tax(gross_paid, cfg)
    if kind == StatutoryKind.TDS:
        return stat.calc_monthly_tds(annual_projection, regime, declarations, cfg, remaining_months)
    return Decimal("0.00")


def _compute_once(components: List[dict], *, gross_target: Decimal, monthly_ctc: Decimal,
                  annual_ctc: Decimal, paid_ratio: Decimal, paid_days: Decimal,
                  working_days: Decimal, lop_days: Decimal, regime: str,
                  declarations: Optional[Dict], cfg: Dict, remaining_months: int):
    ns: Dict[str, Decimal] = {
        "MONTHLY_CTC": monthly_ctc, "CTC": monthly_ctc, "ANNUAL_CTC": annual_ctc,
        "PAID_DAYS": paid_days, "WORKING_DAYS": working_days, "LOP_DAYS": lop_days,
        "GROSS_TARGET": gross_target,
    }
    lines: List[dict] = []
    running_gross_full = Decimal("0")
    pf_wage_paid = Decimal("0")
    gross_paid = Decimal("0")

    for c in components:
        ct = c["calc_type"]
        ctype = c["component_type"]
        ns["GROSS"] = running_gross_full
        full = Decimal("0")
        note = None

        if ct == CalcType.FLAT:
            full = _q(c.get("flat_amount") or 0)
            note = "fixed"
        elif ct == CalcType.PERCENT_OF:
            pv = c.get("percent_value") or Decimal("0")
            base_code = c.get("percent_of_code") or "BASIC"
            base = ns.get(base_code, Decimal("0"))
            full = _q(Decimal(str(pv)) * base)
            pct = (Decimal(str(pv)) * 100).quantize(Decimal("0.##".replace("#", "0")))
            pct_str = f"{pct.normalize():f}"
            note = f"{pct_str}% of {base_code}"
        elif ct == CalcType.FORMULA:
            full = _q(evaluate_formula(c.get("formula") or "0", ns))
            note = "formula"
        elif ct == CalcType.BALANCE:
            full = _q(max(Decimal("0"), gross_target - running_gross_full))
            note = "balance to gross"
        elif ct == CalcType.ATTENDANCE_PRORATED:
            full = _q(c.get("flat_amount") or 0)
            note = "attendance-prorated"
        elif ct == CalcType.STATUTORY:
            annual_projection = running_gross_full * Decimal("12")
            full = _statutory_amount(
                c.get("statutory_kind"),
                pf_wage_paid=pf_wage_paid, gross_paid=gross_paid,
                annual_projection=annual_projection, regime=regime,
                declarations=declarations, cfg=cfg, remaining_months=remaining_months,
            )
            note = str(c.get("statutory_kind"))

        # Proration — earnings/reimbursements (and explicit ATTENDANCE_PRORATED)
        prorates = ct == CalcType.ATTENDANCE_PRORATED or (c.get("prorate_on_lop") and ctype in _EARNING_TYPES)
        amount = _q(full * paid_ratio) if prorates else full
        if prorates and lop_days > 0:
            note = f"{note}; prorated {paid_days}/{working_days}"

        # Accumulate
        if ctype in _EARNING_TYPES and c.get("is_part_of_gross") and not c.get("is_employer_cost"):
            running_gross_full += full
            gross_paid += amount
            if c.get("affects_pf_wage"):
                pf_wage_paid += amount
        # Expose full value to later PERCENT_OF / FORMULA references
        ns[c["code"]] = full

        lines.append({
            "component_id": c["component_id"], "component_code": c["code"],
            "component_name": c["name"], "component_type": ctype,
            "statutory_kind": c.get("statutory_kind"), "sequence": c["sequence"],
            "full_amount": full, "amount": amount,
            "is_taxable": bool(c.get("is_taxable")), "is_employer_cost": bool(c.get("is_employer_cost")),
            "calc_note": note,
        })

    employer = _q(sum((l["amount"] for l in lines if l["is_employer_cost"]
                       or l["component_type"] == ComponentType.EMPLOYER_CONTRIBUTION), Decimal("0")))
    return lines, gross_paid, employer


def compute_payslip(*, components: List[dict], monthly_ctc: Decimal, annual_ctc: Decimal,
                    monthly_gross_hint: Optional[Decimal], regime: str,
                    declarations: Optional[Dict], working_days: Decimal, lop_days: Decimal,
                    cfg: Dict, encashment_amount: Decimal = Decimal("0"),
                    remaining_months: int = 12, adjustments: Optional[List[Dict]] = None) -> Dict:
    """Compute one payslip. Returns {header fields..., 'lines': [...]}.

    ``monthly_gross_hint`` (from EmployeeCompensation.monthly_gross) is preferred
    as GROSS_TARGET; otherwise it's derived in a 2-pass loop (ctc − employer
    contributions).
    """
    monthly_ctc = _q(monthly_ctc)
    annual_ctc = _q(annual_ctc or monthly_ctc * 12)
    working_days = Decimal(str(working_days)) if working_days else Decimal("30")
    lop_days = Decimal(str(lop_days or 0))
    if lop_days > working_days:
        lop_days = working_days
    paid_days = working_days - lop_days
    paid_ratio = (paid_days / working_days) if working_days > 0 else Decimal("1")

    # Resolve GROSS_TARGET: hint, else 2-pass derivation.
    if monthly_gross_hint and monthly_gross_hint > 0:
        gross_target = _q(monthly_gross_hint)
        lines, gross_paid, employer = _compute_once(
            components, gross_target=gross_target, monthly_ctc=monthly_ctc, annual_ctc=annual_ctc,
            paid_ratio=paid_ratio, paid_days=paid_days, working_days=working_days, lop_days=lop_days,
            regime=regime, declarations=declarations, cfg=cfg, remaining_months=remaining_months)
    else:
        gt = monthly_ctc
        for _ in range(2):
            lines, gross_paid, employer = _compute_once(
                components, gross_target=gt, monthly_ctc=monthly_ctc, annual_ctc=annual_ctc,
                paid_ratio=Decimal("1"), paid_days=working_days, working_days=working_days, lop_days=Decimal("0"),
                regime=regime, declarations=declarations, cfg=cfg, remaining_months=remaining_months)
            gt = _q(monthly_ctc - employer)
        gross_target = gt
        # Final pass with real proration on the derived gross target.
        lines, gross_paid, employer = _compute_once(
            components, gross_target=gross_target, monthly_ctc=monthly_ctc, annual_ctc=annual_ctc,
            paid_ratio=paid_ratio, paid_days=paid_days, working_days=working_days, lop_days=lop_days,
            regime=regime, declarations=declarations, cfg=cfg, remaining_months=remaining_months)

    # Optional leave-encashment payout as an extra earning line.
    if encashment_amount and encashment_amount > 0:
        amt = _q(encashment_amount)
        lines.append({
            "component_id": None, "component_code": "LEAVE_ENCASH",
            "component_name": "Leave Encashment", "component_type": ComponentType.EARNING,
            "statutory_kind": None, "sequence": 45, "full_amount": amt, "amount": amt,
            "is_taxable": True, "is_employer_cost": False, "calc_note": "approved unpaid encashment",
        })
        gross_paid += amt

    # Payroll adjustments (bonus / incentive / variable pay / arrear / ad-hoc deduction).
    for adj in (adjustments or []):
        amt = _q(adj.get("amount") or 0)
        if amt <= 0:
            continue
        is_ded = bool(adj.get("is_deduction"))
        seq = 70 if is_ded else 46
        lines.append({
            "component_id": None,
            "component_code": (adj.get("code") or adj.get("adjustment_type") or "ADJUSTMENT"),
            "component_name": adj.get("title") or "Adjustment",
            "component_type": ComponentType.DEDUCTION if is_ded else ComponentType.EARNING,
            "statutory_kind": None, "sequence": seq, "full_amount": amt, "amount": amt,
            "is_taxable": bool(adj.get("is_taxable", True)), "is_employer_cost": False,
            "calc_note": adj.get("note") or (adj.get("adjustment_type") or "adjustment"),
        })

    gross_earnings = _q(sum((l["amount"] for l in lines
                             if l["component_type"] in _EARNING_TYPES and not l["is_employer_cost"]), Decimal("0")))
    total_deductions = _q(sum((l["amount"] for l in lines
                               if l["component_type"] in (ComponentType.DEDUCTION, ComponentType.STATUTORY_DEDUCTION)
                               and not l["is_employer_cost"]), Decimal("0")))
    employer = _q(sum((l["amount"] for l in lines if l["is_employer_cost"]
                       or l["component_type"] == ComponentType.EMPLOYER_CONTRIBUTION), Decimal("0")))
    net_pay = _q(gross_earnings - total_deductions)
    ctc_value = _q(gross_earnings + employer)

    lines.sort(key=lambda x: (x["sequence"], x["component_code"]))
    return {
        "working_days": working_days, "lop_days": lop_days, "paid_days": paid_days,
        "gross_earnings": gross_earnings, "total_deductions": total_deductions,
        "net_pay": net_pay, "employer_contributions": employer, "ctc_value": ctc_value,
        "encashment_amount": _q(encashment_amount or 0), "lines": lines,
    }
