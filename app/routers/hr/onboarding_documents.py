"""HR Onboarding Documents — upload + verify per document slot."""
from __future__ import annotations

import os
import uuid as _uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.drive_document import DriveDocument
from app.models.hr.onboarding import (
    OnboardingDocument, OnboardingProcess, DocumentSlotStatus,
)
from app.schemas.hr.onboarding import (
    OnboardingDocumentSlotResponse, DocumentVerifyBody, DocumentRejectBody,
)
from app.utils.dependencies import get_current_superuser


router = APIRouter(prefix="/hr/onboarding/documents", tags=["HR — Onboarding Documents"])


STORAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "storage",
)
ONB_DIR = os.path.join(STORAGE_DIR, "onboarding")
os.makedirs(ONB_DIR, exist_ok=True)

ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
MAX_SIZE = 10 * 1024 * 1024  # 10 MB


def _to_slot_response(db: Session, d: OnboardingDocument) -> OnboardingDocumentSlotResponse:
    drive_url = None
    drive_name = None
    verifier_name = None
    if d.drive_document_id:
        dd = db.query(DriveDocument).filter(DriveDocument.id == d.drive_document_id).first()
        if dd:
            drive_url = dd.file_url
            drive_name = dd.file_name
    if d.verified_by_user_id:
        u = db.query(User.full_name).filter(User.id == d.verified_by_user_id).first()
        verifier_name = u[0] if u else None
    return OnboardingDocumentSlotResponse(
        id=d.id, process_id=d.process_id, doc_type_key=d.doc_type_key,
        doc_type_label=d.doc_type_label, is_mandatory=d.is_mandatory,
        drive_document_id=d.drive_document_id, drive_file_url=drive_url,
        drive_file_name=drive_name, status=d.status, expiry_date=d.expiry_date,
        ocr_data=d.ocr_data, verified_by_user_id=d.verified_by_user_id,
        verified_by_name=verifier_name, verified_at=d.verified_at,
        rejection_reason=d.rejection_reason, sort_order=d.sort_order,
    )


@router.get("/by-process/{process_id}", response_model=list[OnboardingDocumentSlotResponse])
def list_slots(
    process_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(OnboardingDocument)
        .filter(OnboardingDocument.process_id == process_id)
        .order_by(OnboardingDocument.sort_order.asc())
        .all()
    )
    return [_to_slot_response(db, d) for d in rows]


@router.post("/{slot_id}/upload", response_model=OnboardingDocumentSlotResponse)
async def upload_to_slot(
    slot_id: UUID,
    file: UploadFile = File(...),
    expiry_date: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    slot = db.query(OnboardingDocument).filter(OnboardingDocument.id == slot_id).first()
    if not slot:
        raise HTTPException(404, "Document slot not found")
    proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == slot.process_id).first()
    if not proc:
        raise HTTPException(404, "Process not found")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Invalid file type {ext}. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "File too large. Max 10MB.")

    unique = f"{_uuid.uuid4()}{ext}"
    out_path = os.path.join(ONB_DIR, unique)
    with open(out_path, "wb") as f:
        f.write(content)

    drive_doc = DriveDocument(
        title=slot.doc_type_label,
        file_name=file.filename or unique,
        file_url=f"/storage/onboarding/{unique}",
        file_type=ext.lstrip("."),
        file_size=len(content),
        mime_type=file.content_type,
        category="HR",
        status="Under Review",
        uploaded_by=admin.id,
        employee_id=proc.employee_id,
    )
    db.add(drive_doc)
    db.flush()

    slot.drive_document_id = drive_doc.id
    slot.status = DocumentSlotStatus.UPLOADED
    slot.rejection_reason = None
    if expiry_date:
        try:
            from datetime import date as _date
            slot.expiry_date = _date.fromisoformat(expiry_date)
        except Exception:
            pass
    db.commit()
    db.refresh(slot)
    return _to_slot_response(db, slot)


@router.post("/{slot_id}/verify", response_model=OnboardingDocumentSlotResponse)
def verify_slot(
    slot_id: UUID,
    payload: DocumentVerifyBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    slot = db.query(OnboardingDocument).filter(OnboardingDocument.id == slot_id).first()
    if not slot:
        raise HTTPException(404, "Slot not found")
    if slot.status not in (DocumentSlotStatus.UPLOADED, DocumentSlotStatus.REJECTED):
        raise HTTPException(409, f"Cannot verify a slot in status {slot.status.value}")
    slot.status = DocumentSlotStatus.VERIFIED
    slot.verified_by_user_id = admin.id
    slot.verified_at = datetime.utcnow()
    slot.rejection_reason = None
    if payload.expiry_date:
        slot.expiry_date = payload.expiry_date
    if slot.drive_document_id:
        dd = db.query(DriveDocument).filter(DriveDocument.id == slot.drive_document_id).first()
        if dd:
            dd.status = "Approved"

    # Recalculate process progress
    from app.routers.hr.onboarding import _recalculate_progress
    proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == slot.process_id).first()
    if proc:
        _recalculate_progress(db, proc)
    db.commit()
    db.refresh(slot)
    return _to_slot_response(db, slot)


@router.post("/{slot_id}/reject", response_model=OnboardingDocumentSlotResponse)
def reject_slot(
    slot_id: UUID,
    payload: DocumentRejectBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    slot = db.query(OnboardingDocument).filter(OnboardingDocument.id == slot_id).first()
    if not slot:
        raise HTTPException(404, "Slot not found")
    slot.status = DocumentSlotStatus.REJECTED
    slot.rejection_reason = payload.reason
    slot.verified_by_user_id = admin.id
    slot.verified_at = datetime.utcnow()
    from app.routers.hr.onboarding import _recalculate_progress
    proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == slot.process_id).first()
    if proc:
        _recalculate_progress(db, proc)
    db.commit()
    db.refresh(slot)
    return _to_slot_response(db, slot)


@router.delete("/{slot_id}/file", response_model=OnboardingDocumentSlotResponse)
def clear_slot_file(
    slot_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    slot = db.query(OnboardingDocument).filter(OnboardingDocument.id == slot_id).first()
    if not slot:
        raise HTTPException(404, "Slot not found")
    slot.drive_document_id = None
    slot.status = DocumentSlotStatus.PENDING
    slot.verified_at = None
    slot.verified_by_user_id = None
    slot.rejection_reason = None
    db.commit()
    db.refresh(slot)
    return _to_slot_response(db, slot)
