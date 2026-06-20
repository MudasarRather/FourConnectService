"""HR Asset Management — shared utilities (migrate, state machine, history,
offboarding). Keeps the routers thin and the lifecycle guards in one place."""
from app.utils.hr.assets.migrate import ensure_asset_columns
from app.utils.hr.assets.state import (
    assert_transition,
    next_status_on_return,
    ALLOWED_ASSET_STATUS,
)
from app.utils.hr.assets.audit import write_asset_history
from app.utils.hr.assets.offboarding import flag_open_allocations_on_exit

__all__ = [
    "ensure_asset_columns",
    "assert_transition",
    "next_status_on_return",
    "ALLOWED_ASSET_STATUS",
    "write_asset_history",
    "flag_open_allocations_on_exit",
]
