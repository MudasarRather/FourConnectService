"""HR Settings — Payroll Rules (calculation policy). Distinct from
``/hr/payroll/config/statutory`` (PF/ESI/PT/TDS rates)."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.payroll_rule_config import PayrollRuleConfig
from app.schemas.hr.payroll_rule import PayrollRuleUpsert, PayrollRulesResponse
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll.rule_config import RULE_DEFS, get_all_rules
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/settings/payroll-rules", tags=["HR — Payroll Rules"])

# UI metadata: how each rule renders + its option set + grouping. Every rule
# here is consumed by an engine — retired knobs (cycle trio, LOP_FORMULA) were
# removed, not left inert. See RULE_DEFS for the consumption notes.
RULE_CATALOG = [
    {"key": "WORKING_DAYS_BASIS", "label": "Working-days basis", "group": "Attendance", "type": "select",
     "options": ["ACTUAL", "CALENDAR_30", "FIXED"]},
    {"key": "WORKING_DAYS_FIXED", "label": "Fixed working days", "group": "Attendance", "type": "number"},
    {"key": "OVERTIME_MULTIPLIER", "label": "Overtime multiplier", "group": "Attendance", "type": "number"},
    {"key": "ENCASHMENT_BASIS", "label": "Leave encashment basis", "group": "Settlement", "type": "select",
     "options": ["BASIC", "GROSS", "CTC"]},
    {"key": "NOTICE_RECOVERY_BASIS", "label": "Notice recovery basis", "group": "Settlement", "type": "select",
     "options": ["BASIC", "GROSS", "CTC"]},
    {"key": "DEFAULT_TAX_REGIME", "label": "Default tax regime", "group": "Tax", "type": "select",
     "options": ["NEW", "OLD"]},
]


@router.get("/catalog")
def catalog(admin: User = Depends(get_current_superuser)):
    return {"rules": RULE_CATALOG, "defaults": {k: v for k, (_, v) in RULE_DEFS.items()}}


@router.get("/", response_model=PayrollRulesResponse)
def get_rules(fiscal_year: Optional[str] = None, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    resolved = get_all_rules(db, fiscal_year)
    configured_keys = {
        r.key for r in db.query(PayrollRuleConfig).filter(
            PayrollRuleConfig.is_active == True,  # noqa: E712
            PayrollRuleConfig.fiscal_year.in_([fiscal_year, None]),
        ).all()
    }
    rules = [{"key": k, "value": resolved.get(k), "configured": k in configured_keys} for k in RULE_DEFS]
    return {"fiscal_year": fiscal_year, "rules": rules}


@router.put("/", status_code=200)
def upsert_rule(payload: PayrollRuleUpsert, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if payload.key not in RULE_DEFS:
        raise HTTPException(400, f"Unknown rule key: {payload.key}")
    fy = payload.fiscal_year or None
    row = (db.query(PayrollRuleConfig)
           .filter(PayrollRuleConfig.key == payload.key, PayrollRuleConfig.fiscal_year == fy)
           .first())
    if not row:
        row = PayrollRuleConfig(key=payload.key, fiscal_year=fy, created_by_id=admin.id)
        db.add(row)
    row.value_num = payload.value_num
    row.value_str = payload.value_str
    row.value_json = payload.value_json
    row.is_active = True
    row.last_updated_by_id = admin.id
    log_settings_change(db, "PAYROLL_RULE", None, "UPDATE", admin.id, note=f"{payload.key}={payload.value_num if payload.value_num is not None else payload.value_str}")
    db.commit()
    db.refresh(row)
    return {"key": row.key, "fiscal_year": row.fiscal_year,
            "value": row.value_num if row.value_num is not None else (row.value_json if row.value_json is not None else row.value_str)}


@router.delete("/{key}", status_code=204)
def reset_rule(key: str, fiscal_year: Optional[str] = None, reason: Optional[str] = None,
               db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Reset a rule to its built-in default by removing the stored override.

    An optional ``reason`` is folded into the settings audit note (no new column;
    safe on the shared prod DB) so resets carry provenance like other masters.
    """
    q = db.query(PayrollRuleConfig).filter(PayrollRuleConfig.key == key,
                                           PayrollRuleConfig.fiscal_year == (fiscal_year or None))
    row = q.first()
    if row:
        db.delete(row)
        note = f"reset {key}" + (f" — {reason.strip()}" if reason and reason.strip() else "")
        log_settings_change(db, "PAYROLL_RULE", None, "DELETE", admin.id, note=note)
        db.commit()
