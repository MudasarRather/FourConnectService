"""HR Training & Development — Training requests (admin / HR side)."""
from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.training_request import TrainingRequest, TrainingRequestStatus
from app.schemas.hr.training_request import (
    TrainingRequestResponse, TrainingRequestDecideInput, TrainingRequestFulfillInput,
)
from app.utils.hr.training.request_ops import (
    to_request_response, decide_request, fulfill_request,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Requests"])


@router.get("/requests", response_model=List[TrainingRequestResponse])
def list_requests(
    request_status: Optional[TrainingRequestStatus] = None,
    employee_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(TrainingRequest).filter(TrainingRequest.is_deleted == False)  # noqa: E712
    if request_status:
        q = q.filter(TrainingRequest.status == request_status)
    if employee_id:
        q = q.filter(TrainingRequest.employee_id == employee_id)
    rows = q.order_by(TrainingRequest.created_at.desc()).limit(500).all()
    return [to_request_response(db, r) for r in rows]


@router.get("/requests/{request_id}", response_model=TrainingRequestResponse)
def get_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(TrainingRequest).filter(TrainingRequest.id == request_id, TrainingRequest.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Request not found")
    return to_request_response(db, r)


@router.patch("/requests/{request_id}/decide", response_model=TrainingRequestResponse)
def decide(
    request_id: UUID,
    payload: TrainingRequestDecideInput,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    # Lock the row so two approvers can't double-advance.
    r = (
        db.query(TrainingRequest)
        .filter(TrainingRequest.id == request_id, TrainingRequest.is_deleted == False)  # noqa: E712
        .with_for_update(of=TrainingRequest)
        .first()
    )
    if not r:
        raise HTTPException(404, "Request not found")
    decide_request(db, r, admin, payload.decision, payload.notes)
    db.commit()
    db.refresh(r)
    return to_request_response(db, r)


@router.post("/requests/{request_id}/fulfill", response_model=TrainingRequestResponse)
def fulfill(
    request_id: UUID,
    payload: TrainingRequestFulfillInput,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    r = (
        db.query(TrainingRequest)
        .filter(TrainingRequest.id == request_id, TrainingRequest.is_deleted == False)  # noqa: E712
        .with_for_update(of=TrainingRequest)
        .first()
    )
    if not r:
        raise HTTPException(404, "Request not found")
    fulfill_request(db, r, admin, due_date=payload.due_date, notes=payload.notes,
                    program_id=payload.program_id)
    db.commit()
    db.refresh(r)
    return to_request_response(db, r)
