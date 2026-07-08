"""Seed a default ServiceNow/Zendesk-style ticket taxonomy (idempotent).

request_type → top-level CATEGORY (tagged with request_types) → SUBCATEGORY (parent_id).
Top-level categories with an empty request_types apply to ALL types (the pre-existing
seeded categories keep working as general buckets). Re-running only fills gaps.

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/seed_support_taxonomy.py
"""
import os
import sys
import platform

_ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
_ur.__dict__["processor"] = "Intel"
platform._uname_cache = _ur
platform._Processor.get = staticmethod(lambda: "Intel")

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "nUYmcIFv-UOsbGlwRAUXoIdps5TZ_7wrGrKkexrr_P2VMauiHutx2cGu3hPOONUs")

import app.main  # noqa: F401 — register the full model graph so mappers resolve
from app.database import SessionLocal
from app.models.support_desk.core import SdCategory

TAXONOMY = [
    {"name": "Hardware", "icon": "HardDrive", "types": ["incident", "service_request"],
     "subs": ["Laptop / Desktop", "Monitor", "Printer / Scanner", "Peripherals", "Mobile Device"]},
    {"name": "Software", "icon": "AppWindow", "types": ["incident", "service_request", "bug"],
     "subs": ["Operating System", "Business Application", "Licensing", "Installation / Update", "Email Client"]},
    {"name": "Network & Connectivity", "icon": "Wifi", "types": ["incident", "service_request"],
     "subs": ["VPN", "Wi-Fi", "LAN / Cabling", "Internet / ISP", "Firewall"]},
    {"name": "Access & Identity", "icon": "KeyRound", "types": ["incident", "service_request"],
     "subs": ["Login / SSO", "Password Reset", "Permissions / Roles", "New Account", "Group / Distribution List"]},
    {"name": "Email & Collaboration", "icon": "Mail", "types": ["incident", "service_request"],
     "subs": ["Mailbox", "Calendar", "Shared Drive", "Conferencing", "Spam / Phishing"]},
    {"name": "Product Defect", "icon": "Bug", "types": ["bug"],
     "subs": ["UI / Frontend", "Backend / API", "Data / Records", "Performance", "Integration"]},
    {"name": "Enhancement", "icon": "Sparkles", "types": ["feature_request"],
     "subs": ["New Feature", "Usability", "Reporting", "Automation", "Integration"]},
    {"name": "Service Quality", "icon": "MessageSquareWarning", "types": ["complaint"],
     "subs": ["Delay / SLA", "Staff Conduct", "Billing / Invoice", "Communication", "Other"]},
    {"name": "Change", "icon": "GitPullRequest", "types": ["change"],
     "subs": ["Standard", "Normal", "Emergency", "Configuration"]},
    {"name": "Problem", "icon": "Wrench", "types": ["problem"],
     "subs": ["Recurring Incident", "Known Error", "Root-Cause Analysis"]},
    {"name": "Training", "icon": "GraduationCap", "types": ["training"],
     "subs": ["Onboarding", "Tool / Application", "Process", "Compliance"]},
    {"name": "Implementation", "icon": "Hammer", "types": ["implementation"],
     "subs": ["Setup / Provisioning", "Data Migration", "Configuration", "Go-Live Support"]},
]


def main():
    db = SessionLocal()
    created_main = created_sub = updated = 0
    try:
        order = 100
        for entry in TAXONOMY:
            top = (db.query(SdCategory)
                   .filter(SdCategory.name == entry["name"], SdCategory.parent_id.is_(None),
                           SdCategory.is_deleted == False).first())  # noqa: E712
            if not top:
                top = SdCategory(name=entry["name"], icon=entry["icon"], request_types=entry["types"],
                                 sort_order=order, is_active=True)
                db.add(top); db.flush()
                created_main += 1
            else:
                # merge request types onto the existing top-level category
                existing = set(top.request_types or [])
                merged = existing | set(entry["types"])
                if merged != existing:
                    top.request_types = sorted(merged); updated += 1
                if not top.icon:
                    top.icon = entry["icon"]
            order += 1
            sub_order = 0
            for sub in entry["subs"]:
                exists = (db.query(SdCategory)
                          .filter(SdCategory.name == sub, SdCategory.parent_id == top.id,
                                  SdCategory.is_deleted == False).first())  # noqa: E712
                if not exists:
                    db.add(SdCategory(name=sub, parent_id=top.id, request_types=entry["types"],
                                      sort_order=sub_order, is_active=True))
                    created_sub += 1
                sub_order += 1
        db.commit()
        print(f"[taxonomy-seed] main +{created_main}, subs +{created_sub}, updated {updated}")
        total = db.query(SdCategory).filter(SdCategory.is_deleted == False).count()
        print(f"[taxonomy-seed] total categories now: {total}")
        print("[taxonomy-seed] DONE")
    finally:
        db.close()


if __name__ == "__main__":
    main()
