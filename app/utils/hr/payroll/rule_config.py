"""Resolver for HR Settings — Payroll Rules.

``get_rule`` / ``get_all_rules`` resolve the configured calculation knobs, falling
back to the built-in defaults so behaviour is unchanged until an admin sets a
value. Values resolve in order: FY-specific row → global (null FY) row → default.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.hr.payroll_rule_config import PayrollRuleConfig

# key → (kind, default). kind: 'num' | 'str' | 'json'
#
# Every rule here is CONSUMED by an engine (no decorative knobs). The retired
# keys PAYROLL_CYCLE / CYCLE_START_DAY / PROCESSING_DAY (no sub-monthly runner —
# the engine is calendar-month only) and LOP_FORMULA (the LOP divisor is already
# the WORKING_DAYS_BASIS denominator) were removed rather than left inert.
RULE_DEFS = {
    # Default ACTUAL = the engine's historical behaviour (proration denominator =
    # days_in_month), so leaving it unset is a no-op. CALENDAR_30 forces a fixed
    # 30-day basis; FIXED uses WORKING_DAYS_FIXED.
    "WORKING_DAYS_BASIS":   ("str", "ACTUAL"),         # ACTUAL | CALENDAR_30 | FIXED
    "WORKING_DAYS_FIXED":   ("num", 30),
    # Org-wide overtime multiplier — the FALLBACK used when no per-type Overtime
    # Rule (and no night-shift differential) applies. Default 1.0 = straight time,
    # which equals the engine's previous hard-coded no-rule behaviour, so unset is
    # a no-op; raise it to pay an org-wide OT premium. Per-type Overtime Rules and
    # the night differential always override this.
    "OVERTIME_MULTIPLIER":  ("num", 1.0),
    "ENCASHMENT_BASIS":     ("str", "BASIC"),          # BASIC | GROSS | CTC
    "NOTICE_RECOVERY_BASIS":("str", "BASIC"),
    "DEFAULT_TAX_REGIME":   ("str", "NEW"),            # NEW | OLD
}


def _coerce(kind: str, row: PayrollRuleConfig):
    if kind == "num":
        return float(row.value_num) if row.value_num is not None else None
    if kind == "json":
        return row.value_json
    return row.value_str


def get_all_rules(db: Session, fiscal_year: str | None = None) -> Dict[str, Any]:
    """Resolved {key: value} for every known rule (FY row → global row → default)."""
    rows = (db.query(PayrollRuleConfig)
            .filter(PayrollRuleConfig.is_active == True,  # noqa: E712
                    PayrollRuleConfig.key.in_(list(RULE_DEFS.keys())))
            .all())
    fy_map = {r.key: r for r in rows if r.fiscal_year == fiscal_year}
    gl_map = {r.key: r for r in rows if r.fiscal_year is None}
    out: Dict[str, Any] = {}
    for key, (kind, default) in RULE_DEFS.items():
        row = fy_map.get(key) or gl_map.get(key)
        val = _coerce(kind, row) if row is not None else None
        out[key] = val if val is not None else default
    return out


def get_rule(db: Session, key: str, fiscal_year: str | None = None, default: Any = None) -> Any:
    kind, dflt = RULE_DEFS.get(key, ("str", default))
    rows = (db.query(PayrollRuleConfig)
            .filter(PayrollRuleConfig.key == key, PayrollRuleConfig.is_active == True)  # noqa: E712
            .all())
    row = next((r for r in rows if r.fiscal_year == fiscal_year), None) \
        or next((r for r in rows if r.fiscal_year is None), None)
    if row is not None:
        v = _coerce(kind, row)
        if v is not None:
            return v
    return dflt if default is None else default
