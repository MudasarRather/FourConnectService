"""HR Travel — Travel Policy master CRUD (admin)."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.grade import Grade
from app.models.hr.travel_policy import TravelPolicy
from app.models.hr.travel_type import TravelAuditAction
from app.schemas.hr.travel import (
    TravelPolicyCreate, TravelPolicyUpdate, TravelPolicyResponse, TravelPolicyListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.travel import write_travel_audit

router = APIRouter(prefix="/hr/travel-policies", tags=["HR — Travel Policies"])


def _to_resp(db: Session, p: TravelPolicy) -> dict:
    grade_name = None
    if p.grade_id:
        g = db.query(Grade.name).filter(Grade.id == p.grade_id).first()
        grade_name = g[0] if g else None
    return {
        "id": p.id, "policy_name": p.policy_name, "description": p.description,
        "grade_id": p.grade_id, "grade_name": grade_name, "travel_scope": p.travel_scope,
        "flight_eligibility": p.flight_eligibility, "train_class": p.train_class,
        "hotel_category": p.hotel_category, "da_eligible": p.da_eligible,
        "advance_limit": p.advance_limit, "approval_chain": p.approval_chain,
        "eligibility": p.eligibility, "is_active": p.is_active, "created_at": p.created_at,
    }


@router.get("/", response_model=TravelPolicyListResponse)
def list_policies(include_inactive: bool = False, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_superuser)):
    query = db.query(TravelPolicy).filter(TravelPolicy.is_deleted == False)  # noqa: E712
    if not include_inactive:
        query = query.filter(TravelPolicy.is_active == True)  # noqa: E712
    rows = query.order_by(TravelPolicy.grade_id.asc().nullsfirst(), TravelPolicy.policy_name.asc()).all()
    return TravelPolicyListResponse(items=[_to_resp(db, p) for p in rows], total=len(rows))


@router.post("/", response_model=TravelPolicyResponse, status_code=201)
def create_policy(payload: TravelPolicyCreate, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_superuser)):
    p = TravelPolicy(
        policy_name=payload.policy_name, description=payload.description, grade_id=payload.grade_id,
        travel_scope=payload.travel_scope, flight_eligibility=payload.flight_eligibility,
        train_class=payload.train_class, hotel_category=payload.hotel_category,
        da_eligible=payload.da_eligible, advance_limit=payload.advance_limit,
        approval_chain=[s.model_dump(mode="json") for s in payload.approval_chain] if payload.approval_chain else None,
        eligibility=payload.eligibility.model_dump(mode="json") if payload.eligibility else None,
        is_active=payload.is_active, created_by_id=current_user.id,
    )
    db.add(p)
    db.flush()
    write_travel_audit(db, entity_type="POLICY", entity_id=p.id,
                       action=TravelAuditAction.POLICY_CREATE, actor_id=current_user.id,
                       note=f"Policy {p.policy_name}")
    db.commit()
    db.refresh(p)
    return _to_resp(db, p)


@router.patch("/{policy_id}", response_model=TravelPolicyResponse)
def update_policy(policy_id: UUID, payload: TravelPolicyUpdate, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_superuser)):
    p = db.query(TravelPolicy).filter(
        TravelPolicy.id == policy_id, TravelPolicy.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Policy not found")
    data = payload.model_dump(exclude_unset=True)
    if "approval_chain" in data:
        data["approval_chain"] = [s.model_dump(mode="json") for s in payload.approval_chain] if payload.approval_chain else None
    if "eligibility" in data:
        data["eligibility"] = payload.eligibility.model_dump(mode="json") if payload.eligibility else None
    for k, v in data.items():
        setattr(p, k, v)
    write_travel_audit(db, entity_type="POLICY", entity_id=p.id,
                       action=TravelAuditAction.POLICY_UPDATE, actor_id=current_user.id)
    db.commit()
    db.refresh(p)
    return _to_resp(db, p)


@router.delete("/{policy_id}")
def delete_policy(
    policy_id: UUID,
    reason: Optional[str] = Query(None, max_length=400),
    deactivate: bool = Query(False, description="Deactivate (hide) instead of permanently deleting"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    p = db.query(TravelPolicy).filter(
        TravelPolicy.id == policy_id, TravelPolicy.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Policy not found")
    reason_clean = (reason or "").strip()
    note_tail = f" — {reason_clean}" if reason_clean else ""

    # Reversible path — hide the charter but keep it on file (and re-activatable).
    if deactivate:
        p.is_active = False
        write_travel_audit(db, entity_type="POLICY", entity_id=p.id,
                           action=TravelAuditAction.POLICY_UPDATE, actor_id=current_user.id,
                           note=f"Deactivated{note_tail}")
        db.commit()
        return {"success": True, "deactivated": True, "reason": reason_clean or None}

    p.is_deleted = True
    p.is_active = False
    write_travel_audit(db, entity_type="POLICY", entity_id=p.id,
                       action=TravelAuditAction.POLICY_DELETE, actor_id=current_user.id,
                       note=f"Deleted{note_tail}")
    db.commit()
    return {"success": True, "deactivated": False, "reason": reason_clean or None}
