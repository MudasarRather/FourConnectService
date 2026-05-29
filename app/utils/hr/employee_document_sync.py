"""Surface onboarding-uploaded documents into the unified employee-documents hub.

`OnboardingDocument` slots (filled during the joining flow) are the same files
we want to manage long-term. Rather than copy files, we create lightweight
`EmployeeDocument` rows (`source=ONBOARDING`) that back-link to the slot and
reuse its `DriveDocument`. Idempotent: re-running keeps existing rows in sync
and only creates rows for slots that don't yet have one.

The caller owns the transaction — this helper `flush()`es but does not commit.
"""
from __future__ import annotations

from typing import Dict, Tuple

from sqlalchemy.orm import Session

from app.models.hr.onboarding import (
    OnboardingDocument, OnboardingProcess, DocumentSlotStatus,
)
from app.models.hr.employee_document import (
    EmployeeDocument, DocumentCategory, DocVerificationStatus, DocSource,
    CONFIDENTIAL_CATEGORIES,
)


# Onboarding slot key → (employee-documents category, doc_type)
_SLOT_MAP: Dict[str, Tuple[DocumentCategory, str]] = {
    "aadhaar":        (DocumentCategory.KYC, "AADHAAR"),
    "pan":            (DocumentCategory.KYC, "PAN"),
    "bank_details":   (DocumentCategory.KYC, "BANK_PASSBOOK"),
    "edu_cert":       (DocumentCategory.EDUCATION, "DEGREE"),
    "exp_letter":     (DocumentCategory.EXPERIENCE_LETTER, "EXPERIENCE_LETTER"),
    "offer_letter":   (DocumentCategory.CONTRACT, "OFFER_LETTER"),
    "nda":            (DocumentCategory.CONTRACT, "NDA"),
    "passport_photo": (DocumentCategory.ID_PROOF, "PHOTO"),
    "resume":         (DocumentCategory.OTHER, "RESUME"),
}

# Onboarding slot status → unified verification status.
_STATUS_MAP = {
    DocumentSlotStatus.PENDING: DocVerificationStatus.PENDING,
    DocumentSlotStatus.UPLOADED: DocVerificationStatus.PENDING,    # awaiting review
    DocumentSlotStatus.VERIFIED: DocVerificationStatus.VERIFIED,
    DocumentSlotStatus.REJECTED: DocVerificationStatus.REJECTED,
    DocumentSlotStatus.EXPIRED: DocVerificationStatus.EXPIRED,
}


def _map_slot(key: str) -> Tuple[DocumentCategory, str]:
    return _SLOT_MAP.get(key, (DocumentCategory.OTHER, (key or "DOCUMENT").upper()))


def sync_onboarding_documents(db: Session, employee_id) -> int:
    """Ensure every uploaded/decided onboarding slot for this employee has a
    matching EmployeeDocument row. Returns the count of rows created.

    Only slots that have a file attached (drive_document_id) or a non-PENDING
    status are surfaced — empty pending slots stay represented as "missing".
    """
    proc = (
        db.query(OnboardingProcess)
        .filter(OnboardingProcess.employee_id == employee_id)
        .first()
    )
    if not proc:
        return 0

    slots = (
        db.query(OnboardingDocument)
        .filter(OnboardingDocument.process_id == proc.id)
        .all()
    )
    if not slots:
        return 0

    # Existing surfaced rows keyed by their onboarding slot id.
    existing = {
        d.onboarding_document_id: d
        for d in db.query(EmployeeDocument)
        .filter(
            EmployeeDocument.employee_id == employee_id,
            EmployeeDocument.onboarding_document_id.isnot(None),
        )
        .all()
    }

    created = 0
    for slot in slots:
        has_file = slot.drive_document_id is not None
        if not has_file and slot.status == DocumentSlotStatus.PENDING:
            continue  # nothing to surface yet

        category, doc_type = _map_slot(slot.doc_type_key)
        mapped_status = _STATUS_MAP.get(slot.status, DocVerificationStatus.PENDING)

        row = existing.get(slot.id)
        if row is None:
            row = EmployeeDocument(
                employee_id=employee_id,
                category=category,
                doc_type=doc_type,
                title=slot.doc_type_label,
                drive_document_id=slot.drive_document_id,
                expiry_date=slot.expiry_date,
                verification_status=mapped_status,
                verified_by_user_id=slot.verified_by_user_id,
                verified_at=slot.verified_at,
                rejection_reason=slot.rejection_reason,
                source=DocSource.ONBOARDING,
                onboarding_document_id=slot.id,
                is_confidential=category in CONFIDENTIAL_CATEGORIES,
                attributes={},
            )
            db.add(row)
            created += 1
        else:
            # Keep the surfaced row aligned with the onboarding slot.
            row.drive_document_id = slot.drive_document_id
            row.verification_status = mapped_status
            row.verified_by_user_id = slot.verified_by_user_id
            row.verified_at = slot.verified_at
            row.rejection_reason = slot.rejection_reason
            if slot.expiry_date:
                row.expiry_date = slot.expiry_date

    if created:
        db.flush()
    return created


def sync_all_onboarding_documents(db: Session) -> int:
    """Surface onboarding documents for EVERY employee that has a process.

    Idempotent self-heal used by the employee-documents dashboard so docs
    uploaded during onboarding appear in the hub without first opening each
    employee individually. Returns total rows created. Caller commits.
    """
    emp_ids = [
        r[0] for r in db.query(OnboardingProcess.employee_id)
        .filter(OnboardingProcess.employee_id.isnot(None))
        .distinct().all()
    ]
    total = 0
    for eid in emp_ids:
        total += sync_onboarding_documents(db, eid)
    return total
