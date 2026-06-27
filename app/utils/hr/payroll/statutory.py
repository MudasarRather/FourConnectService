"""India statutory calculations — PF, ESI, Professional Tax, TDS.

Every rate / ceiling / slab is read from ``hr_statutory_config`` (NOT hard-coded
here), so finance can change them without a code deploy. ``load_config`` resolves
the effective national + state rows for a fiscal year into a flat dict; the
payroll batch freezes that dict into ``PayrollBatch.config_snapshot`` so a
re-print reproduces the original numbers after rates change.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.hr.payroll_config import StatutoryConfig

# Fallback defaults used only if a config key is missing (keeps a fresh DB sane).
_DEFAULTS = {
    "PF_RATE": Decimal("0.12"),
    "PF_WAGE_CEILING": Decimal("15000"),
    # Standard corporate practice: restrict employee/employer PF to 12% of the
    # ₹15,000 statutory wage ceiling (= ₹1,800) even when Basic is higher. This is
    # legal and the most common policy. Set False in StatutoryConfig to instead pay
    # 12% on the full Basic (also legal, via Para 26(6) joint option).
    "PF_RESTRICT_TO_CEILING": True,
    "ESI_EMP_RATE": Decimal("0.0075"),
    "ESI_EMPLOYER_RATE": Decimal("0.0325"),
    "ESI_GROSS_THRESHOLD": Decimal("21000"),
    "STD_DEDUCTION_OLD": Decimal("50000"),
    "STD_DEDUCTION_NEW": Decimal("75000"),
    "SEC_80C_CAP": Decimal("150000"),
    "SEC_80D_CAP": Decimal("25000"),
    "CESS_RATE": Decimal("0.04"),
    # Section 87A rebate (FY2025-26 / AY2026-27)
    "REBATE_87A_LIMIT_NEW": Decimal("1200000"),  # total income ≤12L → tax nil (new regime)
    "REBATE_87A_MAX_NEW": Decimal("60000"),
    "REBATE_87A_LIMIT_OLD": Decimal("500000"),   # total income ≤5L → tax nil (old regime)
    "REBATE_87A_MAX_OLD": Decimal("12500"),
}

# Professional Tax is a STATE levy — each state sets its own slabs and five
# states/UTs (Delhi, Haryana, UP, Rajasthan, etc.) levy none at all. So the safe
# NATIONAL default is "no PT": employees in a state with no configured PT_SLABS
# row deduct nothing, rather than inheriting another state's amount. Per-state
# slabs live as state-scoped StatutoryConfig rows and are resolved per employee
# from their work-location state (see ``resolve_pt_slabs``).
_DEFAULT_PT_SLABS = [
    {"upto": None, "amount": 0},
]
_DEFAULT_TDS_NEW = [  # FY2025-26 (AY2026-27) new regime — Union Budget 2025
    {"upto": 400000, "rate": 0.0},
    {"upto": 800000, "rate": 0.05},
    {"upto": 1200000, "rate": 0.10},
    {"upto": 1600000, "rate": 0.15},
    {"upto": 2000000, "rate": 0.20},
    {"upto": 2400000, "rate": 0.25},
    {"upto": None, "rate": 0.30},
]
_DEFAULT_TDS_OLD = [  # Old regime (unchanged)
    {"upto": 250000, "rate": 0.0},
    {"upto": 500000, "rate": 0.05},
    {"upto": 1000000, "rate": 0.20},
    {"upto": None, "rate": 0.30},
]
# Surcharge on income-tax by TOTAL INCOME. New regime caps at 25% (37% removed).
_DEFAULT_SURCHARGE = [
    {"over": 5000000, "rate": 0.10},
    {"over": 10000000, "rate": 0.15},
    {"over": 20000000, "rate": 0.25},
    {"over": 50000000, "rate": 0.37},
]


def load_config(db: Session, fiscal_year: str, state_code: Optional[str] = None) -> Dict:
    """Resolve effective national + state config rows into a flat dict.

    State rows override national rows for the same key. Scalars surface under
    their key; slab tables surface as lists under their key.
    """
    rows: List[StatutoryConfig] = (
        db.query(StatutoryConfig)
        .filter(
            StatutoryConfig.fiscal_year == fiscal_year,
            StatutoryConfig.is_active == True,  # noqa: E712
        )
        .all()
    )
    cfg: Dict = {}
    # national first, then state overrides
    for scope in (None, state_code):
        for r in rows:
            if r.state_code != scope:
                continue
            cfg[r.key] = r.value_json if r.value_json is not None else (
                Decimal(str(r.value_num)) if r.value_num is not None else None
            )
    # Professional Tax is state-scoped: collect EVERY state's PT_SLABS row into a
    # by-state map (independent of the national resolution above, which only sees
    # ``state_code``). The per-employee resolver picks from this map by the
    # employee's work-location state; unmatched states fall back to the national
    # PT_SLABS (= no PT). ``PT_SLABS_NATIONAL`` is a STABLE copy of the national
    # fallback because ``cfg['PT_SLABS']`` is overwritten per employee downstream.
    by_state: Dict[str, list] = {}
    for r in rows:
        if r.key == "PT_SLABS" and r.state_code and r.value_json is not None:
            by_state[str(r.state_code).strip().upper()] = r.value_json
    cfg["PT_SLABS_BY_STATE"] = by_state
    cfg["PT_SLABS_NATIONAL"] = cfg.get("PT_SLABS") or _DEFAULT_PT_SLABS

    # backfill defaults
    for k, v in _DEFAULTS.items():
        cfg.setdefault(k, v)
    cfg.setdefault("PT_SLABS", _DEFAULT_PT_SLABS)
    cfg.setdefault("TDS_SLABS_NEW", _DEFAULT_TDS_NEW)
    cfg.setdefault("TDS_SLABS_OLD", _DEFAULT_TDS_OLD)
    cfg.setdefault("SURCHARGE_SLABS", _DEFAULT_SURCHARGE)
    # HR Settings — Payroll Rules. Attached under a dedicated namespace so the
    # engine can consume them incrementally; present-day computation ignores
    # cfg["RULES"], so this is a no-op for existing payslips (best-effort).
    try:
        from app.utils.hr.payroll.rule_config import get_all_rules
        cfg["RULES"] = get_all_rules(db, fiscal_year)
    except Exception:
        cfg.setdefault("RULES", {})
    return cfg


def _as_bool(v, default=True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("false", "0", "no", "off")


def _dec(v, default="0") -> Decimal:
    if v is None:
        return Decimal(default)
    return v if isinstance(v, Decimal) else Decimal(str(v))


def calc_pf(pf_wage: Decimal, cfg: Dict) -> Decimal:
    rate = _dec(cfg.get("PF_RATE"), "0.12")
    ceiling = _dec(cfg.get("PF_WAGE_CEILING"), "15000")
    # Policy: cap PF wage at the ₹15,000 statutory ceiling (→ ₹1,800) by default, or
    # compute on the full Basic when PF_RESTRICT_TO_CEILING is turned off.
    base = min(pf_wage, ceiling) if _as_bool(cfg.get("PF_RESTRICT_TO_CEILING"), True) else pf_wage
    return (base * rate).quantize(Decimal("0.01"))


def calc_esi(gross: Decimal, cfg: Dict, employer: bool = False) -> Decimal:
    threshold = _dec(cfg.get("ESI_GROSS_THRESHOLD"), "21000")
    if gross > threshold:
        return Decimal("0.00")
    rate = _dec(cfg.get("ESI_EMPLOYER_RATE" if employer else "ESI_EMP_RATE"))
    return (gross * rate).quantize(Decimal("0.01"))


# Indian state / UT name → standard 2-letter code. Work-location ``state`` is
# free-text (HR types "Karnataka"), but PT state rows key on the code ("KA"), so
# we normalize. Keys are letters-only (spaces / "&" / "and" / dots stripped) so
# "Tamil Nadu", "tamilnadu" and "TAMIL NADU" all resolve. An already-valid code
# typed directly is accepted as-is.
_STATE_NAME_TO_CODE = {
    "andhrapradesh": "AP", "arunachalpradesh": "AR", "assam": "AS", "bihar": "BR",
    "chhattisgarh": "CG", "chattisgarh": "CG", "goa": "GA", "gujarat": "GJ",
    "haryana": "HR", "himachalpradesh": "HP", "jharkhand": "JH", "karnataka": "KA",
    "kerala": "KL", "madhyapradesh": "MP", "maharashtra": "MH", "manipur": "MN",
    "meghalaya": "ML", "mizoram": "MZ", "nagaland": "NL", "odisha": "OD",
    "orissa": "OD", "punjab": "PB", "rajasthan": "RJ", "sikkim": "SK",
    "tamilnadu": "TN", "telangana": "TS", "tripura": "TR", "uttarpradesh": "UP",
    "uttarakhand": "UK", "uttaranchal": "UK", "westbengal": "WB",
    # Union Territories
    "andamannicobarislands": "AN", "andamannicobar": "AN", "chandigarh": "CH",
    "dadranagarhavelidamandiu": "DN", "dadranagarhaveli": "DN", "damandiu": "DD",
    "delhi": "DL", "newdelhi": "DL", "nationalcapitalterritoryofdelhi": "DL",
    "jammukashmir": "JK", "ladakh": "LA", "lakshadweep": "LD", "puducherry": "PY",
    "pondicherry": "PY",
}
_STATE_CODES = set(_STATE_NAME_TO_CODE.values())


def india_state_code(value: Optional[str]) -> Optional[str]:
    """Normalize a free-text state name (or an existing code) to its 2-letter code.

    Returns None for blank / non-Indian / unrecognised values so the caller falls
    back to the national PT slabs (= no PT)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    up = s.upper()
    if up in _STATE_CODES:
        return up
    return _STATE_NAME_TO_CODE.get(re.sub(r"[^a-z]", "", s.lower()))


def resolve_pt_slabs(cfg: Dict, state: Optional[str]) -> List[Dict]:
    """PT slabs for an employee's work-location state.

    State-scoped slabs win; otherwise the national fallback (configured national
    PT_SLABS row, else ``_DEFAULT_PT_SLABS`` = no PT). Reads the STABLE keys
    populated by ``load_config`` — never ``cfg['PT_SLABS']``, which callers
    overwrite per employee for the engine to read."""
    code = india_state_code(state)
    by_state = cfg.get("PT_SLABS_BY_STATE") or {}
    if code and code in by_state and by_state[code]:
        return by_state[code]
    return cfg.get("PT_SLABS_NATIONAL") or _DEFAULT_PT_SLABS


def calc_professional_tax(monthly_gross: Decimal, cfg: Dict) -> Decimal:
    slabs = cfg.get("PT_SLABS") or _DEFAULT_PT_SLABS
    for slab in slabs:
        upto = slab.get("upto")
        if upto is None or monthly_gross <= _dec(upto):
            return _dec(slab.get("amount")).quantize(Decimal("0.01"))
    return Decimal("0.00")


def _progressive_tax(taxable: Decimal, slabs: List[Dict]) -> Decimal:
    tax = Decimal("0")
    prev = Decimal("0")
    for slab in slabs:
        upto = slab.get("upto")
        rate = _dec(slab.get("rate"))
        cap = _dec(upto) if upto is not None else None
        if cap is None:
            tax += max(Decimal("0"), taxable - prev) * rate
            break
        if taxable > cap:
            tax += (cap - prev) * rate
            prev = cap
        else:
            tax += max(Decimal("0"), taxable - prev) * rate
            break
    return tax


def _rebate_87a(taxable: Decimal, base_tax: Decimal, regime: str, cfg: Dict) -> Decimal:
    """Section 87A rebate amount to subtract from slab tax.

    New regime FY2025-26: total income ≤ ₹12L → full rebate (tax nil). Just above
    the limit, MARGINAL RELIEF caps tax payable at (income − limit), so the rebate
    is base_tax − (income − limit) and phases out around ₹12.75L.
    Old regime: income ≤ ₹5L → rebate up to ₹12,500.
    """
    regime = (regime or "NEW").upper()
    if regime == "OLD":
        limit = _dec(cfg.get("REBATE_87A_LIMIT_OLD"), "500000")
        maxr = _dec(cfg.get("REBATE_87A_MAX_OLD"), "12500")
        return min(base_tax, maxr) if taxable <= limit else Decimal("0")
    limit = _dec(cfg.get("REBATE_87A_LIMIT_NEW"), "1200000")
    maxr = _dec(cfg.get("REBATE_87A_MAX_NEW"), "60000")
    if taxable <= limit:
        return min(base_tax, maxr)
    excess = taxable - limit  # marginal relief above the limit
    return max(Decimal("0"), base_tax - excess)


def _surcharge_rate(total_income: Decimal, regime: str, bands: List[Dict]):
    """Highest applicable surcharge (rate, threshold). New regime capped at 25%."""
    rate = Decimal("0")
    thr = Decimal("0")
    for b in bands:
        over = _dec(b.get("over"))
        if total_income > over:
            rate, thr = _dec(b.get("rate")), over
    if (regime or "NEW").upper() == "NEW" and rate > Decimal("0.25"):
        rate = Decimal("0.25")
    return rate, thr


def _surcharge(taxable: Decimal, tax_after_rebate: Decimal, regime: str,
               slabs: List[Dict], cfg: Dict) -> Decimal:
    """Surcharge on income-tax, with marginal relief at each threshold."""
    bands = cfg.get("SURCHARGE_SLABS") or _DEFAULT_SURCHARGE
    rate, thr = _surcharge_rate(taxable, regime, bands)
    if rate == 0:
        return Decimal("0")
    surcharge = tax_after_rebate * rate
    # Marginal relief: total (tax + surcharge) must not exceed the tax at the
    # threshold (surcharge at the lower band) plus the income earned beyond it.
    base_at_thr = _progressive_tax(thr, slabs)
    tax_at_thr = max(Decimal("0"), base_at_thr - _rebate_87a(thr, base_at_thr, regime, cfg))
    rate_below, _ = _surcharge_rate(thr, regime, bands)
    cap = (tax_at_thr * (Decimal("1") + rate_below)) + (taxable - thr)
    if (tax_after_rebate + surcharge) > cap:
        surcharge = max(Decimal("0"), cap - tax_after_rebate)
    return surcharge


def old_regime_deductions(declarations: Optional[Dict], cfg: Dict) -> Decimal:
    """Total Chapter VI-A + exemption deductions an employee declares on Form 12BB,
    applied under the OLD regime only (the NEW regime ignores all of these). Each
    head is capped per Indian law (FY2025-26 / AY2026-27); caps are config-driven
    where a key exists, else a statutory default. Excludes the standard deduction
    (handled separately). Keys mirror Form 12BB."""
    decl = declarations or {}
    total = Decimal("0")
    total += min(_dec(decl.get("sec_80c")), _dec(cfg.get("SEC_80C_CAP"), "150000"))           # 80C/80CCC/80CCD(1)
    total += min(_dec(decl.get("sec_80ccd_1b")), _dec(cfg.get("SEC_80CCD1B_CAP"), "50000"))    # NPS additional
    total += min(_dec(decl.get("sec_80d")), _dec(cfg.get("SEC_80D_CAP"), "25000"))             # medical insurance (base self/family cap; seed-aligned)
    total += _dec(decl.get("sec_80e"))                                                          # education-loan interest (no cap)
    total += _dec(decl.get("sec_80g"))                                                          # donations (simplified — no qualifying-limit math)
    total += min(_dec(decl.get("sec_80tta")), _dec(cfg.get("SEC_80TTA_CAP"), "10000"))          # savings-interest
    total += min(_dec(decl.get("home_loan_interest")), _dec(cfg.get("SEC_24B_CAP"), "200000"))  # Sec 24(b), self-occupied
    total += _dec(decl.get("hra_exemption"))                                                    # HRA (employee-computed)
    total += _dec(decl.get("lta_exemption"))                                                    # LTA
    return total


def calc_annual_tds(annual_gross: Decimal, regime: str, declarations: Optional[Dict], cfg: Dict) -> Decimal:
    """Annual income-tax liability (slab tax − 87A rebate + surcharge, then cess)
    for a projected annual gross. FY2025-26 / AY2026-27 rules."""
    decl = declarations or {}
    regime = (regime or "NEW").upper()
    if regime == "OLD":
        std = _dec(cfg.get("STD_DEDUCTION_OLD"), "50000")
        taxable = annual_gross - std - old_regime_deductions(decl, cfg)
        slabs = cfg.get("TDS_SLABS_OLD") or _DEFAULT_TDS_OLD
    else:
        std = _dec(cfg.get("STD_DEDUCTION_NEW"), "75000")
        taxable = annual_gross - std
        slabs = cfg.get("TDS_SLABS_NEW") or _DEFAULT_TDS_NEW
    taxable = max(Decimal("0"), taxable)
    base_tax = _progressive_tax(taxable, slabs)
    tax_after_rebate = max(Decimal("0"), base_tax - _rebate_87a(taxable, base_tax, regime, cfg))
    surcharge = _surcharge(taxable, tax_after_rebate, regime, slabs, cfg)
    cess = _dec(cfg.get("CESS_RATE"), "0.04")
    return ((tax_after_rebate + surcharge) * (Decimal("1") + cess)).quantize(Decimal("0.01"))


def calc_monthly_tds(annual_gross: Decimal, regime: str, declarations: Optional[Dict], cfg: Dict,
                     remaining_months: int = 12, tds_paid_ytd: Decimal = Decimal("0")) -> Decimal:
    """Monthly TDS by CUMULATIVE AVERAGING (India income-tax u/s 192): spread the
    REMAINING annual tax (annual liability − TDS already deducted this FY) over the
    remaining months of the year. This:
      • levels collection to ~annual/12 for a steady earner (the old
        ``annual/remaining_months`` escalated every month — annual/12, /11, /10 …,
        landing the whole year's tax in March), and
      • AUTO-CARRIES a shortfall: a month that under-collected (e.g. TDS capped to
        keep net ≥ 0 in a heavy-LOP month) lowers ``tds_paid_ytd``, so the next
        months automatically collect the balance — no separate carry-forward ledger.
    """
    annual = calc_annual_tds(annual_gross, regime, declarations, cfg)
    months = remaining_months if remaining_months and remaining_months > 0 else 12
    paid = tds_paid_ytd if isinstance(tds_paid_ytd, Decimal) else Decimal(str(tds_paid_ytd or 0))
    remaining_tax = max(Decimal("0"), annual - paid)
    return (remaining_tax / Decimal(months)).quantize(Decimal("0.01"))
