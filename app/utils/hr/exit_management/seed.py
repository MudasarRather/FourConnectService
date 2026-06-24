"""HR Exit Management — idempotent default seed (mirrors seed_travel_defaults).

Ensures one wildcard ExitPolicy exists so accept/clearance/notice resolution
always has a default to fall back on. Wired into ``main.py`` startup.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.hr.exit_policy import ExitPolicy
from app.utils.hr.exit_management.service import (
    DEFAULT_CLEARANCE_TEMPLATE, DEFAULT_INTERVIEW_QUESTIONS,
)

DEFAULT_APPROVAL_LEVELS = [
    {"level": 1, "role": "MANAGER", "label": "Reporting Manager"},
    {"level": 2, "role": "HR", "label": "HR"},
    {"level": 3, "role": "FINANCE", "label": "Finance (F&F)"},
]


def seed_exit_defaults(db: Session) -> None:
    """Create one wildcard ExitPolicy if none exists. Idempotent."""
    existing = db.query(ExitPolicy.id).filter(
        ExitPolicy.is_deleted == False,  # noqa: E712
    ).first()
    if existing:
        return
    db.add(ExitPolicy(
        policy_name="Standard Separation Policy",
        description="Default exit policy applied to any grade without a specific policy.",
        grade_id=None,
        notice_period_days=30,
        probation_notice_days=7,
        buyout_allowed=True,
        buyout_basis="BASIC",
        approval_levels=DEFAULT_APPROVAL_LEVELS,
        clearance_template=DEFAULT_CLEARANCE_TEMPLATE,
        interview_questions=DEFAULT_INTERVIEW_QUESTIONS,
        gratuity_enabled=True,
        gratuity_min_years=5,
        is_active=True,
    ))
    db.commit()
