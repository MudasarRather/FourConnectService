"""Idempotent seed script for the Archive Documents page.

Inserts ~30 archived rows + 3 control rows (Drafts/Rejected) used to verify
the archive query correctly excludes non-terminal statuses.

All rows are tagged in their `title` / `project_name` / `dpr_code` with the
sentinel '[SEED]' so re-running this script wipes the previous seed first
without touching real user data.

Owner: user@fourreck.com (the only configured superuser).

Run:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" seed_archive_data.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta, timezone, date
from app.database import SessionLocal
from app.models.user import User
from app.models.sla import SlaAgreement
from app.models.handover import Handover
from app.models.dpr import DprDocument

SEED_TAG = "[SEED]"

CLIENTS = [
    "Acme Corporation", "Globex Industries", "Initech Systems", "Umbrella Holdings",
    "Stark Enterprises", "Wayne Industries", "Wonka Co.", "Tyrell Corporation",
    "Cyberdyne Systems", "Hooli Networks",
]

# Days-ago for each row. Index 0..9 → archive rows; index 10 → control row.
SLA_AGES =       [365, 400, 500, 700, 800, 1000, 1200, 1300, 1400, 1500, 730]
SLA_STATUSES =   ["Expired"] * 7 + ["Active"] * 3 + ["Draft"]              # last is the control
HANDOVER_AGES =  [365, 420, 600, 720, 900, 1100, 1250, 1300, 1450, 1500, 800]
HANDOVER_STATUSES = ["Approved"] * 7 + ["Completed"] * 3 + ["Rejected"]     # last is the control
DPR_AGES =       [400, 500, 650, 800, 1000, 1150, 1300, 1450, 1600, 1700, 900]
DPR_STATUSES =   ["Approved"] * 10 + ["Draft"]                              # last is the control


def wipe_existing(db):
    """Remove any rows from a previous run so the script is idempotent."""
    n = 0
    n += db.query(SlaAgreement).filter(SlaAgreement.title.like(f"{SEED_TAG}%")).delete(synchronize_session=False)
    n += db.query(Handover).filter(Handover.project_name.like(f"{SEED_TAG}%")).delete(synchronize_session=False)
    n += db.query(DprDocument).filter(DprDocument.title.like(f"{SEED_TAG}%")).delete(synchronize_session=False)
    db.commit()
    print(f"[seed] wiped {n} previous seed rows")


def seed_slas(db, owners):
    """SLA `created_at` is naive (datetime.utcnow). Use naive datetimes.
    `owners` = list of user ids; rotated round-robin so admin/umran/mudasar each own a slice."""
    inserted = 0
    base = datetime.utcnow()
    for i in range(11):
        client = CLIENTS[i % len(CLIENTS)]
        days = SLA_AGES[i]
        status = SLA_STATUSES[i]
        created = base - timedelta(days=days)
        sla = SlaAgreement(
            client_organization_name=client,
            title=f"{SEED_TAG} {client} Managed Services SLA",
            description=f"Three-year managed services agreement covering uptime, support tiers, and escalation paths for {client}.",
            agreement_type="Managed Services",
            start_date=created,
            end_date=created + timedelta(days=365 * 3),
            status=status,
            version="1.0",
            contract_reference=f"SLA-REF-{2020 + (i % 5)}-{1000 + i:04d}",
            agreement_value=float(50000 + i * 12500),
            currency="USD" if i % 2 else "INR",
            created_by_id=owners[i % len(owners)],
            created_at=created,
            updated_at=created + timedelta(days=14),
        )
        db.add(sla)
        inserted += 1
    db.commit()
    print(f"[seed] inserted {inserted} SLA rows (10 archive + 1 Draft control)")
    return inserted


def seed_handovers(db, owners):
    """Handover `created_at` is timezone-aware (server_default=func.now()).
    `owners` rotated round-robin so each test user owns a slice."""
    inserted = 0
    base = datetime.now(timezone.utc)
    for i in range(11):
        client = CLIENTS[i % len(CLIENTS)]
        days = HANDOVER_AGES[i]
        status = HANDOVER_STATUSES[i]
        created = base - timedelta(days=days)
        completion = (created + timedelta(days=180)).date()
        hand = Handover(
            project_name=f"{SEED_TAG} {client} Platform Rollout",
            project_code=f"PRJ-{2020 + (i % 5)}-{500 + i:03d}",
            client_organization=client,
            department="Engineering",
            project_manager=f"PM {i + 1:02d}",
            start_date=(created - timedelta(days=180)).date(),
            completion_date=completion,
            project_summary=f"Full-cycle platform delivery & handover for {client}.",
            tech_stack_backend="Python · FastAPI · PostgreSQL",
            tech_stack_frontend="Vue 3 · Vite",
            tech_stack_database="PostgreSQL 16",
            support_start_date=completion,
            support_end_date=completion + timedelta(days=365),
            support_type="L2 + L3",
            total_project_value=float(120000 + i * 25000),
            amount_received=float(120000 + i * 25000),
            pending_amount=0.0,
            currency="INR" if i % 2 else "USD",
            status=status,
            version="v1.0",
            created_by_id=owners[i % len(owners)],
            created_at=created,
            updated_at=created + timedelta(days=21),
        )
        db.add(hand)
        inserted += 1
    db.commit()
    print(f"[seed] inserted {inserted} Handover rows (10 archive + 1 Rejected control)")
    return inserted


def seed_dprs(db, owners):
    """DPR `created_at` is naive. `owners` rotated round-robin."""
    inserted = 0
    base = datetime.utcnow()
    for i in range(11):
        client = CLIENTS[i % len(CLIENTS)]
        days = DPR_AGES[i]
        status = DPR_STATUSES[i]
        created = base - timedelta(days=days)
        year = created.year
        dpr = DprDocument(
            dpr_code=f"DPR-{year}-{9000 + i:04d}",
            title=f"{SEED_TAG} {client} Detailed Project Report",
            version="v1.0",
            status=status,
            created_by_id=owners[i % len(owners)],
            created_at=created,
            updated_at=created + timedelta(days=30),
        )
        db.add(dpr)
        inserted += 1
    db.commit()
    print(f"[seed] inserted {inserted} DPR rows (10 archive + 1 Draft control)")
    return inserted


def main():
    db = SessionLocal()
    try:
        # Spread ownership across admin + umran + mudasar (whichever exist).
        # Order matters: admin first so index 0 (least-old) is admin, but every other
        # row alternates so all three users have a meaningful slice in their own portal.
        order = ["user@fourreck.com", "umran@fourreck.com", "mudasar@fourreck.com"]
        owners = []
        for email in order:
            u = db.query(User).filter(User.email == email).first()
            if u:
                owners.append(u.id)
                print(f"[seed] owner: {email} (id={u.id}, is_superuser={u.is_superuser})")
        if not owners:
            print("[seed] FATAL: no seed-owner users found — create user@fourreck.com first.")
            sys.exit(1)
        wipe_existing(db)
        n = 0
        n += seed_slas(db, owners)
        n += seed_handovers(db, owners)
        n += seed_dprs(db, owners)
        print(f"[seed] inserted {n} rows total. Round-robin owners: {[email for email in order if any(True for _ in owners)]}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
