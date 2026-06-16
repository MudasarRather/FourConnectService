"""HR Reimbursements — Claim Policy CRUD (limits + approval chain per category)."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.claim_category import ClaimCategory
from app.models.hr.claim_policy import ClaimPolicy
from app.models.hr.reimbursement_type import ClaimAuditAction
from app.schemas.hr.reimbursements import (
    ClaimPolicyCreate, ClaimPolicyUpdate, ClaimPolicyResponse, ClaimPolicyListResponse,
    ClaimCancelBody,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.reimbursements import write_claim_audit, normalize_chain_config

router = APIRouter(prefix="/hr/reimbursements/policies", tags=["HR — Reimbursement Policies"])


def _jsonify_chain(chain) -> Optional[list]:
    """Serialize approval-chain stages for JSONB (UUIDs → str for psycopg2)."""
    if chain is None:
        return None
    out = []
    for s in chain:
        d = s.model_dump() if hasattr(s, "model_dump") else dict(s)
        if d.get("approver_user_id") is not None:
            d["approver_user_id"] = str(d["approver_user_id"])
        if d.get("min_amount") is not None:
            d["min_amount"] = float(d["min_amount"])
        out.append(d)
    # Run through normalize to guarantee a valid, defaulted shape
    return normalize_chain_config(out)


def _jsonify_eligibility(elig) -> Optional[dict]:
    if elig is None:
        return None
    d = elig.model_dump() if hasattr(elig, "model_dump") else dict(elig)
    for k in ("department_ids", "designation_ids", "grade_ids"):
        if d.get(k):
            d[k] = [str(x) for x in d[k]]
    return d


def _to_response(db: Session, pol: ClaimPolicy) -> dict:
    cat = db.query(ClaimCategory.code, ClaimCategory.name).filter(
        ClaimCategory.id == pol.category_id).first()
    return {
        "id": pol.id, "category_id": pol.category_id,
        "category_code": cat.code if cat else None,
        "category_name": cat.name if cat else None,
        "max_amount_per_claim": pol.max_amount_per_claim,
        "max_amount_per_month": pol.max_amount_per_month,
        "max_amount_per_year": pol.max_amount_per_year,
        "max_claims_per_month": pol.max_claims_per_month,
        "requires_attachment": pol.requires_attachment,
        "attachment_required_above": pol.attachment_required_above,
        "default_settlement_method": pol.default_settlement_method,
        "eligibility": pol.eligibility,
        "approval_chain": pol.approval_chain,
        "submission_window_days": pol.submission_window_days,
        "label": pol.label, "description": pol.description,
        "is_active": pol.is_active, "created_at": pol.created_at,
    }


@router.get("/", response_model=ClaimPolicyListResponse)
def list_policies(db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    rows = db.query(ClaimPolicy).filter(ClaimPolicy.is_deleted == False).all()  # noqa: E712
    return {"items": [_to_response(db, p) for p in rows], "total": len(rows)}


@router.get("/by-category/{category_id}", response_model=ClaimPolicyResponse)
def get_policy_for_category(category_id: UUID, db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_superuser)):
    pol = db.query(ClaimPolicy).filter(
        ClaimPolicy.category_id == category_id, ClaimPolicy.is_deleted == False,  # noqa: E712
    ).first()
    if not pol:
        raise HTTPException(404, "No policy configured for this category")
    return _to_response(db, pol)


@router.post("/", response_model=ClaimPolicyResponse, status_code=201)
def create_policy(payload: ClaimPolicyCreate, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_superuser)):
    if not db.query(ClaimCategory.id).filter(
        ClaimCategory.id == payload.category_id, ClaimCategory.is_deleted == False,  # noqa: E712
    ).first():
        raise HTTPException(404, "Category not found")
    if db.query(ClaimPolicy.id).filter(ClaimPolicy.category_id == payload.category_id,
                                       ClaimPolicy.is_deleted == False).first():  # noqa: E712
        raise HTTPException(409, "A policy already exists for this category")
    data = payload.model_dump(exclude={"approval_chain", "eligibility"})
    pol = ClaimPolicy(
        **data,
        approval_chain=_jsonify_chain(payload.approval_chain),
        eligibility=_jsonify_eligibility(payload.eligibility),
        updated_by_id=current_user.id,
    )
    db.add(pol)
    db.flush()
    write_claim_audit(db, entity_type="POLICY", entity_id=pol.id,
                      action=ClaimAuditAction.POLICY_CREATE, actor_id=current_user.id,
                      note=f"Policy for category {pol.category_id}")
    db.commit()
    db.refresh(pol)
    return _to_response(db, pol)


@router.patch("/{policy_id}", response_model=ClaimPolicyResponse)
def update_policy(policy_id: UUID, payload: ClaimPolicyUpdate, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_superuser)):
    pol = db.query(ClaimPolicy).filter(
        ClaimPolicy.id == policy_id, ClaimPolicy.is_deleted == False,  # noqa: E712
    ).first()
    if not pol:
        raise HTTPException(404, "Policy not found")
    data = payload.model_dump(exclude_unset=True, exclude={"approval_chain", "eligibility"})
    for k, v in data.items():
        setattr(pol, k, v)
    fields_set = payload.model_dump(exclude_unset=True)
    if "approval_chain" in fields_set:
        pol.approval_chain = _jsonify_chain(payload.approval_chain)
    if "eligibility" in fields_set:
        pol.eligibility = _jsonify_eligibility(payload.eligibility)
    pol.updated_by_id = current_user.id
    write_claim_audit(db, entity_type="POLICY", entity_id=pol.id,
                      action=ClaimAuditAction.POLICY_UPDATE, actor_id=current_user.id,
                      note=f"Policy for category {pol.category_id}")
    db.commit()
    db.refresh(pol)
    return _to_response(db, pol)


@router.delete("/{policy_id}")
def delete_policy(policy_id: UUID, body: ClaimCancelBody = ClaimCancelBody(),
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_superuser)):
    pol = db.query(ClaimPolicy).filter(
        ClaimPolicy.id == policy_id, ClaimPolicy.is_deleted == False,  # noqa: E712
    ).first()
    if not pol:
        raise HTTPException(404, "Policy not found")
    pol.is_deleted = True
    write_claim_audit(db, entity_type="POLICY", entity_id=pol.id,
                      action=ClaimAuditAction.POLICY_DELETE, actor_id=current_user.id,
                      note=body.reason or f"Policy for category {pol.category_id} removed (reverts to defaults)")
    db.commit()
    return {"success": True}
