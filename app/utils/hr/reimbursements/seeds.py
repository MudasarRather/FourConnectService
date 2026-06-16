"""Idempotent reimbursement defaults — claim categories + per-category policies.

Called once at startup (beside the payroll seed). Safe to re-run: only inserts
rows whose ``code``/``category_id`` don't already exist, so it never clobbers
admin edits.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.hr.claim_category import ClaimCategory
from app.models.hr.claim_policy import ClaimPolicy
from app.models.hr.reimbursement_type import SettlementMethod


# Each: (code, name, icon, color_hex, is_taxable, field_schema)
_DEFAULT_CATEGORIES = [
    dict(
        code="TRAVEL", name="Travel", icon="Plane", color_hex="#3b82f6", is_taxable=False,
        description="Air / train / taxi / fuel / hotel and local conveyance.",
        field_schema=[
            {"key": "purpose", "label": "Travel Purpose", "type": "text", "required": True},
            {"key": "from_location", "label": "From", "type": "text", "required": True},
            {"key": "to_location", "label": "To", "type": "text", "required": True},
            {"key": "mode", "label": "Mode of Travel", "type": "select", "required": True,
             "options": ["Air", "Train", "Taxi", "Bus", "Fuel", "Hotel", "Local Conveyance"]},
            {"key": "travel_date", "label": "Travel Date", "type": "date", "required": False},
            {"key": "return_date", "label": "Return Date", "type": "date", "required": False},
            {"key": "mileage_km", "label": "Mileage (km)", "type": "number", "required": False},
        ],
    ),
    dict(
        code="MEDICAL", name="Medical", icon="Stethoscope", color_hex="#ef4444", is_taxable=False,
        description="Hospitalization, consultation, medicines, diagnostics.",
        field_schema=[
            {"key": "patient_name", "label": "Patient Name", "type": "text", "required": True},
            {"key": "relationship", "label": "Relationship", "type": "select", "required": True,
             "options": ["Self", "Spouse", "Child", "Parent", "Other"]},
            {"key": "hospital", "label": "Hospital / Clinic", "type": "text", "required": True},
            {"key": "doctor", "label": "Doctor", "type": "text", "required": False},
            {"key": "treatment", "label": "Treatment Type", "type": "select", "required": False,
             "options": ["Consultation", "Hospitalization", "Medicines", "Diagnostics", "Dental", "Eye Care", "Emergency"]},
        ],
    ),
    dict(
        code="INTERNET", name="Internet", icon="Wifi", color_hex="#8b5cf6", is_taxable=False,
        description="Broadband / fiber / mobile data for remote work.",
        field_schema=[
            {"key": "provider", "label": "Service Provider", "type": "text", "required": True},
            {"key": "billing_month", "label": "Billing Month", "type": "text", "required": True},
            {"key": "invoice_number", "label": "Invoice Number", "type": "text", "required": False},
            {"key": "plan", "label": "Plan / Type", "type": "select", "required": False,
             "options": ["Broadband", "Fiber", "Mobile Data", "Remote Connectivity"]},
        ],
    ),
    dict(
        code="FOOD", name="Food", icon="Utensils", color_hex="#14b8a6", is_taxable=False,
        description="Client meetings, project duty, night-shift meals, training.",
        field_schema=[
            {"key": "vendor", "label": "Restaurant / Vendor", "type": "text", "required": True},
            {"key": "meal_date", "label": "Meal Date", "type": "date", "required": False},
            {"key": "participants", "label": "Participants", "type": "number", "required": False},
            {"key": "occasion", "label": "Occasion", "type": "select", "required": False,
             "options": ["Client Meeting", "Business Travel", "Project Duty", "Night Shift", "Training"]},
        ],
    ),
    dict(
        code="FUEL", name="Fuel", icon="Fuel", color_hex="#f59e0b", is_taxable=False,
        description="Fuel and vehicle running expenses.",
        field_schema=[
            {"key": "vehicle", "label": "Vehicle", "type": "text", "required": False},
            {"key": "distance_km", "label": "Distance (km)", "type": "number", "required": False},
            {"key": "fuel_type", "label": "Fuel Type", "type": "select", "required": False,
             "options": ["Petrol", "Diesel", "CNG", "EV"]},
        ],
    ),
]

# Default approval chain — Manager → Finance → HR (Finance kicks in only above a band).
_DEFAULT_CHAIN = [
    {"approver_type": "MANAGER", "approver_user_id": None, "label": "Reporting Manager", "min_amount": None},
    {"approver_type": "FINANCE", "approver_user_id": None, "label": "Finance", "min_amount": None},
    {"approver_type": "HR", "approver_user_id": None, "label": "HR", "min_amount": 25000},
]


def seed_reimbursement_defaults(db: Session) -> None:
    created_any = False
    for c in _DEFAULT_CATEGORIES:
        existing = db.query(ClaimCategory).filter(ClaimCategory.code == c["code"]).first()
        if existing:
            cat = existing
        else:
            cat = ClaimCategory(
                code=c["code"], name=c["name"], description=c.get("description"),
                icon=c.get("icon"), color_hex=c.get("color_hex"),
                field_schema=c["field_schema"], is_taxable=c.get("is_taxable", False),
                requires_attachment=True,
                default_settlement_method=SettlementMethod.PAYROLL,
                is_active=True,
            )
            db.add(cat)
            db.flush()
            created_any = True

        # Default policy per category (only if none exists)
        pol = db.query(ClaimPolicy).filter(ClaimPolicy.category_id == cat.id).first()
        if not pol:
            db.add(ClaimPolicy(
                category_id=cat.id,
                requires_attachment=True,
                default_settlement_method=SettlementMethod.PAYROLL,
                approval_chain=[dict(s) for s in _DEFAULT_CHAIN],
                label=f"{cat.name} Policy",
                is_active=True,
            ))
            created_any = True

    if created_any:
        db.commit()
