"""add offer employee_id fk

Revision ID: a2f5b9c7d3e4
Revises: 9c4e8f2a1b30
Create Date: 2026-05-19

Adds Offer.employee_id (nullable UUID, FK to hr_employees.id, ON DELETE SET NULL).
This is the back-link set when POST /api/hr/employees/ is called with offer_id —
it lets us trace which recruitment offer became which Employee row.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a2f5b9c7d3e4"
down_revision = "9c4e8f2a1b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hr_rec_offers",
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_hr_rec_offers_employee_id",
        "hr_rec_offers",
        "hr_employees",
        ["employee_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_hr_rec_offers_employee_id",
        "hr_rec_offers",
        ["employee_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_hr_rec_offers_employee_id", table_name="hr_rec_offers")
    op.drop_constraint("fk_hr_rec_offers_employee_id", "hr_rec_offers", type_="foreignkey")
    op.drop_column("hr_rec_offers", "employee_id")
