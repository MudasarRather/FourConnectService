"""HR Payroll compute engine package.

Public API — import from the package, not the submodules:

    from app.utils.hr.payroll import (
        compute_payslip, resolve_structure, get_structure,
        load_config, validate_formula, FormulaError,
        seed_payroll_defaults, fy_for, days_in_month,
    )
"""
from app.utils.hr.payroll.formula import validate_formula, evaluate_formula, FormulaError
from app.utils.hr.payroll.statutory import (
    load_config, calc_pf, calc_esi, calc_professional_tax,
    calc_annual_tds, calc_monthly_tds, resolve_pt_slabs, india_state_code,
)
from app.utils.hr.payroll.resolver import resolve_structure, resolve_components, get_structure
from app.utils.hr.payroll.engine import compute_payslip, days_in_month
from app.utils.hr.payroll.seeds import seed_payroll_defaults, fy_for, fy_start_year

__all__ = [
    "validate_formula", "evaluate_formula", "FormulaError",
    "load_config", "calc_pf", "calc_esi", "calc_professional_tax",
    "calc_annual_tds", "calc_monthly_tds", "resolve_pt_slabs", "india_state_code",
    "resolve_structure", "resolve_components", "get_structure",
    "compute_payslip", "days_in_month",
    "seed_payroll_defaults", "fy_for", "fy_start_year",
]
