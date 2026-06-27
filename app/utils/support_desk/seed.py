"""Support Desk — idempotent startup defaults.

Seeds:
  * NumberingSeries for ticket / change / problem / service-request IDs.
  * A default SLA package (priority → response/resolution matrix + escalation).
  * A starter set of ticket categories.
  * Baseline module settings.

Only inserts rows that don't already exist — safe to run on every boot.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

DEFAULT_SLA_MATRIX = {
    "critical": {"response_mins": 15, "resolution_mins": 240},
    "urgent": {"response_mins": 30, "resolution_mins": 480},
    "high": {"response_mins": 60, "resolution_mins": 1440},
    "medium": {"response_mins": 240, "resolution_mins": 2880},
    "low": {"response_mins": 480, "resolution_mins": 4320},
}
DEFAULT_ESCALATION = [
    {"level": 1, "after_mins": 30, "notify": "agent"},
    {"level": 2, "after_mins": 120, "notify": "manager"},
    {"level": 3, "after_mins": 480, "notify": "head"},
]
DEFAULT_CATEGORIES = [
    ("Technical Support", "wrench"),
    ("Billing & Payments", "wallet"),
    ("Account & Access", "user"),
    ("Bug Report", "bug"),
    ("Feature Request", "sparkles"),
    ("General Inquiry", "help-circle"),
]
DEFAULT_NUMBERING = [
    ("SUPPORT_TICKET", "TKT"),
    ("SUPPORT_CHANGE", "CHG"),
    ("SUPPORT_PROBLEM", "PRB"),
    ("SUPPORT_SERVICE_REQUEST", "SR"),
]
DEFAULT_SETTINGS = {
    "csat": {"enabled": True, "scale": 5, "ask_on": "resolved"},
    "portal": {"brand_name": "Fourreck Support", "accent": "#fb923c"},
    "sla": {"business_hours_only": False, "default_package": "Standard SLA"},
}


def seed_support_desk_defaults(db: Session) -> None:
    from app.models.hr.numbering_series import NumberingSeries
    from app.models.support_desk.core import SdSlaPackage, SdCategory
    from app.models.support_desk.ops import SdSetting

    changed = False

    # 1) Numbering series (opt-in: each module gets a sensible default)
    for module, prefix in DEFAULT_NUMBERING:
        exists = db.query(NumberingSeries).filter(NumberingSeries.module == module).first()
        if not exists:
            db.add(NumberingSeries(
                module=module, prefix=prefix, separator="-", padding=6,
                include_year=True, include_month=False, financial_year_reset=False,
                current_number=0, is_active=True, is_deleted=False,
            ))
            changed = True

    # 2) Default SLA package
    if not db.query(SdSlaPackage).filter(SdSlaPackage.is_default == True).first():  # noqa: E712
        db.add(SdSlaPackage(
            name="Standard SLA",
            description="Default response/resolution targets by priority.",
            matrix=DEFAULT_SLA_MATRIX,
            escalation_levels=DEFAULT_ESCALATION,
            is_default=True, is_active=True,
        ))
        changed = True

    # 3) Starter categories
    if db.query(SdCategory).count() == 0:
        for i, (name, icon) in enumerate(DEFAULT_CATEGORIES):
            db.add(SdCategory(name=name, icon=icon, sort_order=i, is_active=True))
        changed = True

    # 4) Baseline settings
    for key, value in DEFAULT_SETTINGS.items():
        if not db.query(SdSetting).filter(SdSetting.key == key).first():
            db.add(SdSetting(key=key, value=value))
            changed = True

    if changed:
        db.commit()
