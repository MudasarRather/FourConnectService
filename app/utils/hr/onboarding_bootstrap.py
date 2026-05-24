"""Bootstrap helper — initialise the onboarding spine when an Employee is created.

Called from `POST /api/hr/employees/` (employees router) inside the same
transaction as the Employee insert. Best-effort writes; any failure rolls back
the whole employee creation so we never end up with a half-onboarded employee.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.hr.employee import Employee
from app.models.hr.recruitment import Offer
from app.models.hr.onboarding import (
    OnboardingProcess, OnboardingStatus, OnboardingStage,
    OnboardingChecklistTemplate, OnboardingChecklistItem,
    OnboardingDocument, DocumentSlotStatus,
    EmployeeIdentity, IdentityStatus,
    WelcomeKit, WelcomeKitTemplate, WelcomeKitStatus,
)
from app.models.hr.training import TrainingProgram, TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.account_provisioning import (
    AccountProvisioning, AccountType, AccountProvisioningStatus,
)


# Standard document slots created for every new joiner — keep keys stable; the
# frontend renders by these.
DEFAULT_DOC_SLOTS = [
    ("aadhaar",         "Aadhaar Card",              True,  10),
    ("pan",             "PAN Card",                  True,  20),
    ("resume",          "Resume",                    True,  30),
    ("edu_cert",        "Educational Certificates",  True,  40),
    ("exp_letter",      "Experience Letters",        False, 50),
    ("passport_photo",  "Passport Photo",            True,  60),
    ("bank_details",    "Bank Details (Cancelled Cheque)", True, 70),
    ("offer_letter",    "Offer Letter",              True,  80),
    ("nda",             "Signed NDA",                False, 90),
]

# Default account types to provision on day-one.
DEFAULT_ACCOUNTS = [AccountType.ERP, AccountType.EMAIL, AccountType.ATTENDANCE]


def bootstrap_onboarding(
    db: Session,
    employee: Employee,
    offer: Optional[Offer],
    actor_id: Optional[UUID],
) -> OnboardingProcess:
    """Create OnboardingProcess + seed all sub-rows. Returns the new process row."""
    target_joining = employee.joining_date
    if not target_joining and offer is not None:
        target_joining = offer.joining_date

    process = OnboardingProcess(
        employee_id=employee.id,
        offer_id=offer.id if offer else None,
        status=OnboardingStatus.IN_PROGRESS,
        current_stage=OnboardingStage.PRE_JOIN,
        progress_pct=0,
        target_joining_date=target_joining,
        created_by_id=actor_id,
        last_updated_by_id=actor_id,
    )
    db.add(process)
    db.flush()  # need process.id for child rows

    # ── Checklist items from active templates ──
    templates = (
        db.query(OnboardingChecklistTemplate)
        .filter(OnboardingChecklistTemplate.is_active == True)  # noqa: E712
        .order_by(OnboardingChecklistTemplate.sort_order.asc())
        .all()
    )
    for t in templates:
        due: Optional[date] = None
        if target_joining and t.default_due_offset_days is not None:
            due = target_joining + timedelta(days=int(t.default_due_offset_days))
        db.add(OnboardingChecklistItem(
            process_id=process.id,
            template_id=t.id,
            category=t.category,
            task_name=t.task_name,
            description=t.description,
            due_date=due,
            is_mandatory=t.is_mandatory,
            sort_order=t.sort_order,
        ))

    # ── Document slots ──
    for key, label, mandatory, order in DEFAULT_DOC_SLOTS:
        db.add(OnboardingDocument(
            process_id=process.id,
            doc_type_key=key,
            doc_type_label=label,
            is_mandatory=mandatory,
            status=DocumentSlotStatus.PENDING,
            sort_order=order,
        ))

    # ── Identity stub ──
    db.add(EmployeeIdentity(
        employee_id=employee.id,
        status=IdentityStatus.PENDING,
    ))

    # ── Welcome kit from default template ──
    default_kit = (
        db.query(WelcomeKitTemplate)
        .filter(WelcomeKitTemplate.is_active == True, WelcomeKitTemplate.is_deleted == False)  # noqa: E712
        .order_by(WelcomeKitTemplate.created_at.asc())
        .first()
    )
    if default_kit is not None:
        items = []
        for entry in (default_kit.default_items or []):
            items.append({
                "item_name": entry.get("item_name", ""),
                "qty": entry.get("qty", 1),
                "included": bool(entry.get("included", True)),
                "packed": False,
                "delivered": False,
            })
        db.add(WelcomeKit(
            employee_id=employee.id,
            process_id=process.id,
            template_id=default_kit.id,
            items=items,
            status=WelcomeKitStatus.PENDING,
        ))

    # ── Default account provisioning rows ──
    for acc_type in DEFAULT_ACCOUNTS:
        db.add(AccountProvisioning(
            employee_id=employee.id,
            process_id=process.id,
            account_type=acc_type,
            status=AccountProvisioningStatus.PENDING,
        ))

    # ── Mandatory training assignments ──
    mandatory_programs = (
        db.query(TrainingProgram)
        .filter(
            TrainingProgram.is_mandatory_for_new_joiners == True,  # noqa: E712
            TrainingProgram.is_active == True,  # noqa: E712
            TrainingProgram.is_deleted == False,  # noqa: E712
        )
        .all()
    )
    for prog in mandatory_programs:
        due = target_joining + timedelta(days=14) if target_joining else None
        db.add(TrainingAssignment(
            program_id=prog.id,
            employee_id=employee.id,
            process_id=process.id,
            due_date=due,
            status=TrainingAssignmentStatus.NOT_STARTED,
        ))

    db.flush()
    return process
