"""HR Reimbursements / Employee Claims — utility package.

Re-exports the chain mechanics, DB service helpers and the startup seed so
routers and main.py can import from one place.
"""
from app.utils.hr.reimbursements.chain import (
    normalize_chain_config,
    build_claim_steps,
    step_status,
    auto_skip_unresolvable,
    can_act_on_step,
    mirror_final_columns,
    assert_transition,
)
from app.utils.hr.reimbursements.service import (
    resolve_self_employee,
    try_self_employee,
    generate_claim_number,
    generate_settlement_number,
    validate_details_against_schema,
    write_claim_audit,
    emit_notifications,
    employee_snapshot,
    enrich_steps_with_names,
    to_response,
    settle_via_payroll,
    settle_direct,
)
from app.utils.hr.reimbursements.seeds import seed_reimbursement_defaults

__all__ = [
    "normalize_chain_config", "build_claim_steps", "step_status",
    "auto_skip_unresolvable", "can_act_on_step", "mirror_final_columns",
    "assert_transition", "resolve_self_employee", "try_self_employee",
    "generate_claim_number", "generate_settlement_number",
    "validate_details_against_schema", "write_claim_audit", "emit_notifications",
    "employee_snapshot", "enrich_steps_with_names", "to_response",
    "settle_via_payroll", "settle_direct", "seed_reimbursement_defaults",
]
