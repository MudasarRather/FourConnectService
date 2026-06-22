"""HR Travel — startup seeds (idempotent).

Seeds a sensible default set of travel categories, a baseline DA rate matrix
(null-grade wildcard rates per city tier) and a global default travel policy, so
the module is usable the moment it boots. Only inserts rows that don't exist yet.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.hr.travel_category import TravelCategory
from app.models.hr.travel_policy import TravelPolicy
from app.models.hr.travel_da import TravelDaRate
from app.models.hr.travel_type import CityCategory


_DEFAULT_CATEGORIES = [
    {"code": "OFFICIAL_TOUR", "name": "Official Tour", "icon": "Plane", "color_hex": "#f59e0b", "default_travel_type": "Official Tour"},
    {"code": "PROJECT_VISIT", "name": "Project Visit", "icon": "Map", "color_hex": "#fb923c", "default_travel_type": "Project Visit"},
    {"code": "CLIENT_VISIT", "name": "Client Visit", "icon": "Handshake", "color_hex": "#fbbf24", "default_travel_type": "Client Visit"},
    {"code": "TRAINING", "name": "Training", "icon": "GraduationCap", "color_hex": "#34d399", "default_travel_type": "Training"},
    {"code": "CONFERENCE", "name": "Conference", "icon": "Presentation", "color_hex": "#f97316", "default_travel_type": "Conference"},
    {"code": "INSPECTION", "name": "Inspection", "icon": "ClipboardCheck", "color_hex": "#d97706", "default_travel_type": "Inspection"},
    {"code": "AUDIT_VISIT", "name": "Audit Visit", "icon": "FileSearch", "color_hex": "#b45309", "default_travel_type": "Audit Visit"},
    {"code": "MEETING", "name": "Meeting", "icon": "Users", "color_hex": "#fbbf24", "default_travel_type": "Meeting"},
    {"code": "SITE_VISIT", "name": "Site Visit", "icon": "MapPin", "color_hex": "#fb923c", "default_travel_type": "Site Visit"},
    {"code": "GOVT_MEETING", "name": "Government Meeting", "icon": "Landmark", "color_hex": "#92400e", "default_travel_type": "Government Meeting"},
    {"code": "TENDER_MEETING", "name": "Tender Meeting", "icon": "Gavel", "color_hex": "#d97706", "default_travel_type": "Tender Meeting"},
    {"code": "EMERGENCY", "name": "Emergency Travel", "icon": "Siren", "color_hex": "#ef4444", "default_travel_type": "Emergency Travel"},
]

# Baseline wildcard DA matrix (null grade = applies to all grades without a row).
_DEFAULT_DA_RATES = [
    {"city_category": CityCategory.METRO, "daily_rate": Decimal("2000")},
    {"city_category": CityCategory.TIER_2, "daily_rate": Decimal("1500")},
    {"city_category": CityCategory.TIER_3, "daily_rate": Decimal("1000")},
    {"city_category": CityCategory.INTERNATIONAL, "daily_rate": Decimal("5000")},
]


def seed_travel_defaults(db: Session) -> None:
    created = False

    # Categories
    for i, c in enumerate(_DEFAULT_CATEGORIES):
        exists = db.query(TravelCategory.id).filter(TravelCategory.code == c["code"]).first()
        if not exists:
            db.add(TravelCategory(
                code=c["code"], name=c["name"], icon=c["icon"], color_hex=c["color_hex"],
                default_travel_type=c.get("default_travel_type"), field_schema=[],
                sort_order=f"{i:02d}", is_active=True,
            ))
            created = True

    # DA rate matrix (wildcard grade)
    for r in _DEFAULT_DA_RATES:
        exists = db.query(TravelDaRate.id).filter(
            TravelDaRate.grade_id.is_(None),
            TravelDaRate.city_category == r["city_category"],
            TravelDaRate.is_deleted == False,  # noqa: E712
        ).first()
        if not exists:
            db.add(TravelDaRate(
                grade_id=None, city_category=r["city_category"], daily_rate=r["daily_rate"],
                currency="INR", effective_date=date(date.today().year, 1, 1), is_active=True,
                notes="Default baseline rate (seeded)",
            ))
            created = True

    # Default global policy
    exists = db.query(TravelPolicy.id).filter(
        TravelPolicy.grade_id.is_(None), TravelPolicy.is_deleted == False).first()  # noqa: E712
    if not exists:
        db.add(TravelPolicy(
            policy_name="Standard Travel Policy",
            description="Default entitlement applied to all grades without a specific policy.",
            grade_id=None, travel_scope="ALL", flight_eligibility="ECONOMY",
            train_class="AC3", hotel_category="3 Star", da_eligible=True,
            advance_limit=Decimal("50000"),
            approval_chain=[
                {"approver_type": "MANAGER", "approver_user_id": None, "label": "Reporting Manager", "min_amount": None},
                {"approver_type": "FINANCE", "approver_user_id": None, "label": "Finance", "min_amount": None},
            ],
            is_active=True,
        ))
        created = True

    if created:
        db.commit()
