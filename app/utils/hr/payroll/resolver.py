"""Resolve a salary structure into an ordered list of effective components.

Merges each ``SalaryComponent`` default with its per-structure
``SalaryStructureComponent`` override and sorts by the structure-local sequence
(falling back to the component sequence). The result is a list of plain dicts the
engine consumes — decoupled from the ORM so the engine stays pure/testable.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload

from app.models.hr.salary_structure import SalaryStructure
from app.models.hr.salary_structure_component import SalaryStructureComponent
from app.models.hr.salary_component import SalaryComponent


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _merge_component(c: SalaryComponent, ov) -> dict:
    """Merge a SalaryComponent default with a per-link/per-item override object.

    ``ov`` exposes optional attributes: override_calc_type, override_formula,
    override_percent_value, override_percent_of_code, override_flat_amount, sequence.
    Used by both resolve_structure (persisted links) and resolve_components
    (ad-hoc unsaved drawer state).
    """
    return {
        "component_id": c.id,
        "code": c.code,
        "name": c.name,
        "component_type": c.component_type,
        "statutory_kind": c.statutory_kind,
        "calc_type": getattr(ov, "override_calc_type", None) or c.calc_type,
        "formula": ov.override_formula if getattr(ov, "override_formula", None) is not None else c.formula,
        "percent_value": _dec(ov.override_percent_value if getattr(ov, "override_percent_value", None) is not None else c.percent_value),
        "percent_of_code": getattr(ov, "override_percent_of_code", None) or c.percent_of_code,
        "flat_amount": _dec(ov.override_flat_amount if getattr(ov, "override_flat_amount", None) is not None else c.flat_amount),
        "sequence": ov.sequence if getattr(ov, "sequence", None) is not None else c.sequence,
        "is_taxable": c.is_taxable,
        "is_part_of_gross": c.is_part_of_gross,
        "affects_pf_wage": c.affects_pf_wage,
        "affects_esi_wage": c.affects_esi_wage,
        "prorate_on_lop": c.prorate_on_lop,
        "is_employer_cost": c.is_employer_cost,
    }


def resolve_components(db: Session, items) -> List[dict]:
    """Resolve an ad-hoc list of structure-component inputs into ordered effective
    dicts — same shape as ``resolve_structure`` but sourced from a request payload
    instead of persisted links. This lets the structure preview reflect LIVE,
    unsaved override edits (e.g. changing Basic/HRA % in the drawer before saving).

    ``items`` is a list of objects each carrying ``component_id`` plus optional
    overrides (sequence / override_percent_value / override_flat_amount / …).
    """
    ids = [it.component_id for it in items if getattr(it, "component_id", None)]
    if not ids:
        return []
    comps = {c.id: c for c in db.query(SalaryComponent).filter(SalaryComponent.id.in_(ids)).all()}
    out: List[dict] = []
    for it in items:
        c = comps.get(it.component_id)
        if c is None or c.is_deleted or not c.is_active:
            continue
        out.append(_merge_component(c, it))
    out.sort(key=lambda x: (x["sequence"], x["code"]))
    return out


def resolve_structure(db: Session, structure_id) -> List[dict]:
    """Return ordered effective-component dicts for a structure id."""
    links = (
        db.query(SalaryStructureComponent)
        .options(joinedload(SalaryStructureComponent.component))
        .filter(
            SalaryStructureComponent.structure_id == structure_id,
            SalaryStructureComponent.is_active == True,  # noqa: E712
        )
        .all()
    )
    out: List[dict] = []
    for link in links:
        c = link.component
        if c is None or c.is_deleted or not c.is_active:
            continue
        out.append({
            "component_id": c.id,
            "code": c.code,
            "name": c.name,
            "component_type": c.component_type,
            "statutory_kind": c.statutory_kind,
            "calc_type": link.override_calc_type or c.calc_type,
            "formula": link.override_formula if link.override_formula is not None else c.formula,
            "percent_value": _dec(link.override_percent_value if link.override_percent_value is not None else c.percent_value),
            "percent_of_code": link.override_percent_of_code or c.percent_of_code,
            "flat_amount": _dec(link.override_flat_amount if link.override_flat_amount is not None else c.flat_amount),
            "sequence": link.sequence if link.sequence is not None else c.sequence,
            "is_taxable": c.is_taxable,
            "is_part_of_gross": c.is_part_of_gross,
            "affects_pf_wage": c.affects_pf_wage,
            "affects_esi_wage": c.affects_esi_wage,
            "prorate_on_lop": c.prorate_on_lop,
            "is_employer_cost": c.is_employer_cost,
        })
    out.sort(key=lambda x: (x["sequence"], x["code"]))
    return out


def get_structure(db: Session, structure_id) -> Optional[SalaryStructure]:
    return (
        db.query(SalaryStructure)
        .filter(SalaryStructure.id == structure_id, SalaryStructure.is_deleted == False)  # noqa: E712
        .first()
    )
