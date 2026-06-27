"""Idempotent payroll defaults — statutory config, core components, default structure.

Called once at startup (beside the attendance finalizer). Safe to re-run: it
only inserts rows that don't already exist, so it never clobbers admin edits.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.hr.salary_component import (
    SalaryComponent, ComponentType, CalcType, StatutoryKind,
)
from app.models.hr.salary_structure import SalaryStructure
from app.models.hr.salary_structure_component import SalaryStructureComponent
from app.models.hr.payroll_config import StatutoryConfig


def fy_for(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[-2:]}"


def fy_start_year(fy: str) -> int:
    return int(fy.split("-")[0])


# (key, calc_type, percent_value, percent_of_code, statutory_kind, type, seq, flags)
_CORE_COMPONENTS = [
    dict(code="BASIC", name="Basic Pay", calc_type=CalcType.PERCENT_OF, percent_value="0.40",
         percent_of_code="CTC", component_type=ComponentType.EARNING, sequence=10,
         is_part_of_gross=True, affects_pf_wage=True, affects_esi_wage=True, prorate_on_lop=True),
    dict(code="HRA", name="House Rent Allowance", calc_type=CalcType.PERCENT_OF, percent_value="0.40",
         percent_of_code="BASIC", component_type=ComponentType.EARNING, sequence=20,
         is_part_of_gross=True, affects_esi_wage=True, prorate_on_lop=True),
    dict(code="SPECIAL_ALLOWANCE", name="Special Allowance", calc_type=CalcType.BALANCE,
         component_type=ComponentType.EARNING, sequence=40,
         is_part_of_gross=True, affects_esi_wage=True, prorate_on_lop=True),
    dict(code="PF_EMP", name="Provident Fund (Employee)", calc_type=CalcType.STATUTORY,
         statutory_kind=StatutoryKind.PF_EMPLOYEE, component_type=ComponentType.STATUTORY_DEDUCTION,
         sequence=50, is_part_of_gross=False, prorate_on_lop=False, is_taxable=False),
    dict(code="ESI_EMP", name="ESI (Employee)", calc_type=CalcType.STATUTORY,
         statutory_kind=StatutoryKind.ESI_EMPLOYEE, component_type=ComponentType.STATUTORY_DEDUCTION,
         sequence=51, is_part_of_gross=False, prorate_on_lop=False, is_taxable=False),
    dict(code="PT", name="Professional Tax", calc_type=CalcType.STATUTORY,
         statutory_kind=StatutoryKind.PROFESSIONAL_TAX, component_type=ComponentType.STATUTORY_DEDUCTION,
         sequence=52, is_part_of_gross=False, prorate_on_lop=False, is_taxable=False),
    dict(code="TDS", name="Income Tax (TDS)", calc_type=CalcType.STATUTORY,
         statutory_kind=StatutoryKind.TDS, component_type=ComponentType.STATUTORY_DEDUCTION,
         sequence=60, is_part_of_gross=False, prorate_on_lop=False, is_taxable=False),
    dict(code="PF_EMPR", name="Provident Fund (Employer)", calc_type=CalcType.STATUTORY,
         statutory_kind=StatutoryKind.PF_EMPLOYER, component_type=ComponentType.EMPLOYER_CONTRIBUTION,
         sequence=80, is_part_of_gross=False, prorate_on_lop=False, is_employer_cost=True, is_taxable=False),
    dict(code="ESI_EMPR", name="ESI (Employer)", calc_type=CalcType.STATUTORY,
         statutory_kind=StatutoryKind.ESI_EMPLOYER, component_type=ComponentType.EMPLOYER_CONTRIBUTION,
         sequence=81, is_part_of_gross=False, prorate_on_lop=False, is_employer_cost=True, is_taxable=False),
]

_TDS_NEW = [  # FY2025-26 (AY2026-27) new regime — Union Budget 2025
    {"upto": 400000, "rate": 0.0}, {"upto": 800000, "rate": 0.05},
    {"upto": 1200000, "rate": 0.10}, {"upto": 1600000, "rate": 0.15},
    {"upto": 2000000, "rate": 0.20}, {"upto": 2400000, "rate": 0.25},
    {"upto": None, "rate": 0.30},
]
_TDS_OLD = [
    {"upto": 250000, "rate": 0.0}, {"upto": 500000, "rate": 0.05},
    {"upto": 1000000, "rate": 0.20}, {"upto": None, "rate": 0.30},
]
# Professional Tax is a STATE levy. The NATIONAL fallback is "no PT" so employees
# in a state with no configured slab row (or a non-PT state like Delhi / Haryana /
# UP) deduct nothing — never another state's amount. Add one PT_SLABS row per
# state you operate in (e.g. _PT_KA below); the engine resolves them per employee
# from the work-location state.
_PT_NATIONAL = [{"upto": None, "amount": 0}]
_PT_KA = [{"upto": 24999, "amount": 0}, {"upto": None, "amount": 200}]


def _seed_config(db: Session, fy: str) -> None:
    if db.query(StatutoryConfig.id).filter(StatutoryConfig.fiscal_year == fy).first():
        return
    eff = date(fy_start_year(fy), 4, 1)
    scalar = [
        ("PF_RATE", "0.12"), ("PF_WAGE_CEILING", "15000"),
        ("ESI_EMP_RATE", "0.0075"), ("ESI_EMPLOYER_RATE", "0.0325"), ("ESI_GROSS_THRESHOLD", "21000"),
        ("STD_DEDUCTION_OLD", "50000"), ("STD_DEDUCTION_NEW", "75000"),
        ("SEC_80C_CAP", "150000"), ("SEC_80D_CAP", "25000"), ("CESS_RATE", "0.04"),
    ]
    for key, val in scalar:
        db.add(StatutoryConfig(fiscal_year=fy, state_code=None, key=key, value_num=val,
                               effective_from=eff, description="Seeded default"))
    db.add(StatutoryConfig(fiscal_year=fy, state_code=None, key="TDS_SLABS_NEW", value_json=_TDS_NEW,
                           effective_from=eff, description="New-regime slabs (seeded)"))
    db.add(StatutoryConfig(fiscal_year=fy, state_code=None, key="TDS_SLABS_OLD", value_json=_TDS_OLD,
                           effective_from=eff, description="Old-regime slabs (seeded)"))
    db.add(StatutoryConfig(fiscal_year=fy, state_code=None, key="PT_SLABS", value_json=_PT_NATIONAL,
                           effective_from=eff, description="National fallback — no PT (state rows override)"))
    db.add(StatutoryConfig(fiscal_year=fy, state_code="KA", key="PT_SLABS", value_json=_PT_KA,
                           effective_from=eff, description="Karnataka PT slabs (seeded)"))


def _seed_components(db: Session) -> dict:
    """Insert core components if none exist. Returns {code: SalaryComponent}."""
    by_code = {}
    existing = {c.code: c for c in db.query(SalaryComponent).all()}
    if existing:
        return existing
    for spec in _CORE_COMPONENTS:
        comp = SalaryComponent(
            code=spec["code"], name=spec["name"], component_type=spec["component_type"],
            calc_type=spec["calc_type"], statutory_kind=spec.get("statutory_kind"),
            percent_value=spec.get("percent_value"), percent_of_code=spec.get("percent_of_code"),
            sequence=spec["sequence"], is_taxable=spec.get("is_taxable", True),
            is_part_of_gross=spec.get("is_part_of_gross", True),
            affects_pf_wage=spec.get("affects_pf_wage", False),
            affects_esi_wage=spec.get("affects_esi_wage", False),
            prorate_on_lop=spec.get("prorate_on_lop", True),
            is_employer_cost=spec.get("is_employer_cost", False),
            is_system=True, is_active=True,
        )
        db.add(comp)
        by_code[spec["code"]] = comp
    db.flush()
    return by_code


def _seed_default_structure(db: Session, by_code: dict) -> None:
    if db.query(SalaryStructure.id).first():
        return
    struct = SalaryStructure(code="STR-DEFAULT", name="Standard (Permanent)",
                             description="Seeded default salary structure", is_default=True, is_active=True)
    db.add(struct)
    db.flush()
    for spec in _CORE_COMPONENTS:
        comp = by_code.get(spec["code"])
        if comp is None:
            comp = db.query(SalaryComponent).filter(SalaryComponent.code == spec["code"]).first()
        if comp is not None:
            db.add(SalaryStructureComponent(structure_id=struct.id, component_id=comp.id,
                                            sequence=spec["sequence"]))


def seed_payroll_defaults(db: Session) -> None:
    try:
        _seed_config(db, fy_for(date.today()))
        by_code = _seed_components(db)
        _seed_default_structure(db, by_code)
        db.commit()
    except Exception:
        db.rollback()
        import traceback
        traceback.print_exc()
