"""HR Training & Development (LTCMS) — shared service helpers.

Public surface used by the training sub-routers and the background monitors.
"""
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.service import (
    resolve_self_employee, try_self_employee, generate_request_number,
    user_name, emp_display, employee_snapshot, resolve_eligible_employee_ids,
    enrich_steps_with_names,
)
from app.utils.hr.training.flow import (
    assert_assignment_transition, complete_assignment, recompute_skill_gap,
)
from app.utils.hr.training.chain import (
    build_request_steps, auto_skip_unresolvable, can_act_on_step,
    assert_request_transition, mirror_request_final_columns,
)

__all__ = [
    "write_training_audit",
    "resolve_self_employee", "try_self_employee", "generate_request_number",
    "user_name", "emp_display", "employee_snapshot", "resolve_eligible_employee_ids",
    "enrich_steps_with_names",
    "assert_assignment_transition", "complete_assignment", "recompute_skill_gap",
    "build_request_steps", "auto_skip_unresolvable", "can_act_on_step",
    "assert_request_transition", "mirror_request_final_columns",
]
