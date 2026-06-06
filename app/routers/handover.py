import re

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload
from typing import List
from uuid import UUID

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_user
from app.models.handover import (
    Handover, HandoverStakeholder, HandoverModule, HandoverAsset,
    HandoverServer, HandoverCredential, HandoverDocument, HandoverTraining,
    HandoverFinancial, HandoverIssue, HandoverApproval, HandoverDeliverable, HandoverFeedback
)
from app.models.notification import Notification
from app.schemas.handover import HandoverCreate, HandoverUpdate, HandoverResponse

router = APIRouter(prefix="/handover", tags=["Project Handover"])

# Helper to rebuild nested children
CHILD_MAP = {
    'stakeholders': (HandoverStakeholder, 'handover_id'),
    'modules': (HandoverModule, 'handover_id'),
    'assets': (HandoverAsset, 'handover_id'),
    'servers': (HandoverServer, 'handover_id'),
    'credentials': (HandoverCredential, 'handover_id'),
    'documents': (HandoverDocument, 'handover_id'),
    'training': (HandoverTraining, 'handover_id'),
    'financial_invoices': (HandoverFinancial, 'handover_id'),
    'issues': (HandoverIssue, 'handover_id'),
    'approvals': (HandoverApproval, 'handover_id'),
    'deliverables': (HandoverDeliverable, 'handover_id'),
    'feedback': (HandoverFeedback, 'handover_id'),
}

NESTED_KEYS = set(CHILD_MAP.keys())


@router.post("/", response_model=HandoverResponse, status_code=status.HTTP_201_CREATED)
def create_handover(data: HandoverCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    core_data = data.dict(exclude=NESTED_KEYS)
    db_obj = Handover(created_by_id=current_user.id, **core_data)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)

    # Insert nested children
    for key, (Model, fk_field) in CHILD_MAP.items():
        items = getattr(data, key, [])
        for item in items:
            child = Model(**{fk_field: db_obj.id, **item.dict()})
            db.add(child)

    db.commit()
    db.refresh(db_obj)

    # Notifications for submittal
    if db_obj.status == "Internal Review":
        admins = db.query(User).filter(User.is_superuser == True).all()
        for admin in admins:
            notif = Notification(
                user_id=admin.id,
                type="handover_submitted",
                title="Handover Submitted",
                message=f"A new handover for {db_obj.project_name} has been submitted for approval.",
                action_url="/admin/documents/handover?tab=pending",
                related_user_id=current_user.id
            )
            db.add(notif)
        db.commit()

    return db_obj


@router.get("/", response_model=List[HandoverResponse])
def get_all_handovers(project_id: str = None, status_filter: str = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    q = db.query(Handover)
    if project_id:
        q = q.filter(Handover.project_id == project_id)
    if status_filter:
        q = q.filter(Handover.status == status_filter)

    # Role-based scoping (same as SLA)
    if current_user.is_superuser:
        q = q.filter(or_(Handover.status != "Draft", Handover.created_by_id == current_user.id))
    else:
        q = q.filter(Handover.created_by_id == current_user.id)

    return q.order_by(Handover.created_at.desc()).all()


@router.get("/{handover_id}", response_model=HandoverResponse)
def get_handover(handover_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    obj = db.query(Handover).filter(Handover.id == handover_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Handover document not found")
    # Ownership: creator OR superuser. (Drafts especially must not leak across users.)
    if not current_user.is_superuser and obj.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return obj


@router.get("/{handover_id}/export")
def export_handover_pdf(handover_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Render the project handover to an ultra-modern PDF (WeasyPrint, server-side)."""
    rels = (Handover.stakeholders, Handover.modules, Handover.assets, Handover.servers,
            Handover.credentials, Handover.documents, Handover.training,
            Handover.financial_invoices, Handover.issues, Handover.approvals,
            Handover.deliverables, Handover.feedback)
    obj = (
        db.query(Handover)
        .options(*[selectinload(r) for r in rels])
        .filter(Handover.id == handover_id)
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="Handover document not found")
    if not current_user.is_superuser and obj.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        from app.utils.handover_pdf import render_handover_pdf
        pdf = render_handover_pdf(obj)
    except OSError as e:
        if any(t in str(e) for t in ("libgobject", "libpango", "cannot load library")):
            raise HTTPException(status_code=503, detail="WeasyPrint can't find GTK DLLs — run `python vendor/setup_gtk.py`")
        raise

    base = obj.project_name or obj.project_code or "Handover"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_") or "Handover"
    stamp = (obj.updated_at or obj.created_at)
    date_part = stamp.strftime("%Y-%m-%d") if stamp else "draft"
    filename = f"Handover_{safe}_{date_part}.pdf"

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/{handover_id}", response_model=HandoverResponse)
def update_handover(handover_id: str, data: HandoverUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_obj = db.query(Handover).filter(Handover.id == handover_id).first()
    if not db_obj:
        raise HTTPException(status_code=404, detail="Handover document not found")

    old_status = db_obj.status
    update_dict = data.dict(exclude_unset=True)
    core_fields = {k: v for k, v in update_dict.items() if k not in NESTED_KEYS}
    for key, value in core_fields.items():
        setattr(db_obj, key, value)

    # Full overwrite for nested collections
    for key, (Model, fk_field) in CHILD_MAP.items():
        if key in update_dict:
            db.query(Model).filter(getattr(Model, fk_field) == handover_id).delete(synchronize_session=False)
            db.flush()
            items = getattr(data, key, [])
            for item in items:
                child = Model(**{fk_field: db_obj.id, **item.dict()})
                db.add(child)

    new_status = db_obj.status

    # Notifications for status changes
    if old_status != new_status:
        if new_status == "Internal Review":
            # Notify Admins
            admins = db.query(User).filter(User.is_superuser == True).all()
            for admin in admins:
                notif = Notification(
                    user_id=admin.id,
                    type="handover_submitted",
                    title="Handover Submitted",
                    message=f"A new handover for {db_obj.project_name} has been submitted for approval.",
                    action_url="/admin/documents/handover?tab=pending",
                    related_user_id=current_user.id
                )
                db.add(notif)
        elif new_status == "Approved":
            # Notify Creator
            notif = Notification(
                user_id=db_obj.created_by_id,
                type="handover_approved",
                title="Handover Approved",
                message=f"Your handover for {db_obj.project_name} has been approved.",
                action_url="/user/documents/handover?tab=approved",
                related_user_id=db_obj.created_by_id
            )
            db.add(notif)
        elif new_status == "Rejected" or (new_status == "Draft" and old_status == "Internal Review"):
            # Notify Creator of Rejection
            reason_text = f" Reason: {db_obj.rejection_reason}" if db_obj.rejection_reason else ""
            notif = Notification(
                user_id=db_obj.created_by_id,
                type="handover_rejected",
                title="Handover Rejected",
                message=f"Your handover for {db_obj.project_name} was rejected.{reason_text}",
                action_url="/user/documents/handover?tab=rejected",
                related_user_id=current_user.id
            )
            db.add(notif)

    db.commit()
    db.refresh(db_obj)
    return db_obj


@router.delete("/{handover_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_handover(handover_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    obj = db.query(Handover).filter(Handover.id == handover_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Handover document not found")
    if not current_user.is_superuser and obj.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")
    db.delete(obj)
    db.commit()
    return None
