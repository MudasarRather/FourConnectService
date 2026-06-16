"""HR Reimbursements — Claim Category master (admin CRUD)."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.claim_category import ClaimCategory
from app.models.hr.claim_policy import ClaimPolicy
from app.models.hr.claim import Claim
from app.models.hr.reimbursement_type import ClaimStatus, ClaimAuditAction
from app.schemas.hr.reimbursements import (
    ClaimCategoryCreate, ClaimCategoryUpdate, ClaimCategoryResponse, ClaimCategoryListResponse,
    ClaimCancelBody,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.reimbursements import write_claim_audit

router = APIRouter(prefix="/hr/reimbursements/categories", tags=["HR — Reimbursement Categories"])

# Claims in these states still "use" a category (block hard delete / code edit)
_LIVE_STATUSES = (
    ClaimStatus.DRAFT, ClaimStatus.PENDING_APPROVAL, ClaimStatus.RETURNED,
    ClaimStatus.APPROVED, ClaimStatus.SETTLED,
)


def _serialize_schema(payload_schema) -> list:
    return [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in (payload_schema or [])]


def _to_response(db: Session, cat: ClaimCategory, *, with_count: bool = False) -> dict:
    out = {
        "id": cat.id, "code": cat.code, "name": cat.name, "description": cat.description,
        "icon": cat.icon, "color_hex": cat.color_hex, "field_schema": cat.field_schema or [],
        "default_settlement_method": cat.default_settlement_method,
        "requires_attachment": cat.requires_attachment, "is_taxable": cat.is_taxable,
        "gl_code": cat.gl_code, "sort_order": cat.sort_order, "is_active": cat.is_active,
        "created_at": cat.created_at, "claim_count": None,
    }
    if with_count:
        out["claim_count"] = db.query(sa_func.count(Claim.id)).filter(
            Claim.category_id == cat.id, Claim.is_deleted == False,  # noqa: E712
        ).scalar() or 0
    return out


@router.get("/", response_model=ClaimCategoryListResponse)
def list_categories(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    q = db.query(ClaimCategory).filter(ClaimCategory.is_deleted == False)  # noqa: E712
    if not include_inactive:
        q = q.filter(ClaimCategory.is_active == True)  # noqa: E712
    rows = q.order_by(ClaimCategory.sort_order.asc().nullslast(), ClaimCategory.name.asc()).all()
    return {"items": [_to_response(db, c, with_count=True) for c in rows], "total": len(rows)}


@router.get("/{category_id}", response_model=ClaimCategoryResponse)
def get_category(category_id: UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_superuser)):
    cat = db.query(ClaimCategory).filter(
        ClaimCategory.id == category_id, ClaimCategory.is_deleted == False,  # noqa: E712
    ).first()
    if not cat:
        raise HTTPException(404, "Category not found")
    return _to_response(db, cat, with_count=True)


@router.post("/", response_model=ClaimCategoryResponse, status_code=201)
def create_category(payload: ClaimCategoryCreate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    if db.query(ClaimCategory.id).filter(ClaimCategory.code == payload.code).first():
        raise HTTPException(409, f"A category with code {payload.code} already exists")
    data = payload.model_dump()
    data["field_schema"] = _serialize_schema(payload.field_schema)
    cat = ClaimCategory(**data, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(cat)
    db.flush()
    write_claim_audit(db, entity_type="CATEGORY", entity_id=cat.id,
                      action=ClaimAuditAction.CATEGORY_CREATE, actor_id=current_user.id,
                      note=f"Category {cat.code}")
    db.commit()
    db.refresh(cat)
    return _to_response(db, cat, with_count=True)


@router.patch("/{category_id}", response_model=ClaimCategoryResponse)
def update_category(category_id: UUID, payload: ClaimCategoryUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    cat = db.query(ClaimCategory).filter(
        ClaimCategory.id == category_id, ClaimCategory.is_deleted == False,  # noqa: E712
    ).first()
    if not cat:
        raise HTTPException(404, "Category not found")
    data = payload.model_dump(exclude_unset=True)
    if "field_schema" in data and data["field_schema"] is not None:
        data["field_schema"] = _serialize_schema(payload.field_schema)
    for k, v in data.items():
        setattr(cat, k, v)
    cat.updated_by_id = current_user.id
    write_claim_audit(db, entity_type="CATEGORY", entity_id=cat.id,
                      action=ClaimAuditAction.CATEGORY_UPDATE, actor_id=current_user.id,
                      note=f"Category {cat.code}")
    db.commit()
    db.refresh(cat)
    return _to_response(db, cat, with_count=True)


@router.delete("/{category_id}")
def delete_category(category_id: UUID, body: ClaimCancelBody = ClaimCancelBody(),
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    cat = db.query(ClaimCategory).filter(
        ClaimCategory.id == category_id, ClaimCategory.is_deleted == False,  # noqa: E712
    ).first()
    if not cat:
        raise HTTPException(404, "Category not found")
    live = db.query(sa_func.count(Claim.id)).filter(
        Claim.category_id == cat.id, Claim.is_deleted == False,  # noqa: E712
        Claim.status.in_(_LIVE_STATUSES),
    ).scalar() or 0
    if live:
        raise HTTPException(409, f"Cannot delete — {live} live claim(s) use this category. Deactivate it instead.")
    cat.is_deleted = True
    cat.is_active = False
    # Soft-delete its policy too
    pol = db.query(ClaimPolicy).filter(ClaimPolicy.category_id == cat.id,
                                       ClaimPolicy.is_deleted == False).first()  # noqa: E712
    if pol:
        pol.is_deleted = True
    note = f"{cat.code}: {body.reason}" if body.reason else f"Category {cat.code}"
    write_claim_audit(db, entity_type="CATEGORY", entity_id=cat.id,
                      action=ClaimAuditAction.CATEGORY_DELETE, actor_id=current_user.id,
                      note=note)
    db.commit()
    return {"success": True}
