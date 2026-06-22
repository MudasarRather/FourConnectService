"""HR Travel Management — utility package.

Re-exports the chain mechanics, DB service helpers, request flow, DA engine,
settlement reconciliation, payroll posting, attendance sync and the startup seed
so routers and main.py can import from one place.
"""
from app.utils.hr.travel.chain import (
    normalize_chain_config,
    build_request_steps,
    step_status,
    auto_skip_unresolvable,
    can_act_on_step,
    mirror_final_columns,
    assert_transition,
)
from app.utils.hr.travel.service import (
    resolve_self_employee,
    try_self_employee,
    generate_request_number,
    generate_booking_number,
    generate_advance_number,
    generate_settlement_number,
    validate_details_against_schema,
    write_travel_audit,
    emit_notifications,
    employee_snapshot,
    enrich_steps_with_names,
    to_response,
)
from app.utils.hr.travel.flow import (
    get_category,
    get_policy_for,
    build_new_request,
    recompute_request_derived,
    submit_request,
    apply_decision,
)
from app.utils.hr.travel.da import resolve_da_rate, compute_da
from app.utils.hr.travel.settlement import reconcile, resync_settlement
from app.utils.hr.travel.payroll_post import post_adjustment, cancel_or_reverse
from app.utils.hr.travel.attendance_sync import mark_on_duty, unmark_on_duty
from app.utils.hr.travel.bootstrap import seed_travel_defaults

__all__ = [
    "normalize_chain_config", "build_request_steps", "step_status",
    "auto_skip_unresolvable", "can_act_on_step", "mirror_final_columns",
    "assert_transition", "resolve_self_employee", "try_self_employee",
    "generate_request_number", "generate_booking_number", "generate_advance_number",
    "generate_settlement_number", "validate_details_against_schema",
    "write_travel_audit", "emit_notifications", "employee_snapshot",
    "enrich_steps_with_names", "to_response", "get_category", "get_policy_for",
    "build_new_request", "recompute_request_derived", "submit_request", "apply_decision",
    "resolve_da_rate", "compute_da", "reconcile", "resync_settlement", "post_adjustment", "cancel_or_reverse",
    "mark_on_duty", "unmark_on_duty", "seed_travel_defaults",
]
