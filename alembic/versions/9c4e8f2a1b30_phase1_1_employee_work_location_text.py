"""phase1.1 employee work_location_text column

Revision ID: 9c4e8f2a1b30
Revises: 7a1f4b2c8d10
Create Date: 2026-05-16 09:00:00.000000

Phase 1.0.1 adds a freeform `work_location_text` column on `hr_employees` so the
Add Employee wizard can capture work location as a typed string rather than a
constrained FK dropdown. The existing `work_location_id` FK stays in place for
future Phase 2 reporting/filtering — `work_location_text` is the preferred
display value.

Idempotent: uses ``ADD COLUMN IF NOT EXISTS``.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9c4e8f2a1b30"
down_revision: Union[str, None] = "7a1f4b2c8d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hr_employees ADD COLUMN IF NOT EXISTS work_location_text VARCHAR(160)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hr_employees DROP COLUMN IF EXISTS work_location_text")
