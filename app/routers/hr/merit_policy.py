"""HR Settings — Merit & Increment Policy (company appraisal→pay governance).

Defines the rating→hike% bands and the org merit budget that bound every
appraisal-driven salary revision. Consumed by the Performance module's
recommend/approve-hike pipeline (`performance_self.py` / `performance.py`).
"""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.merit_policy import MeritPolicy, DEFAULT_BANDS
from app.schemas.hr.merit_policy import (
    MeritPolicyCreate, MeritPolicyUpdate, MeritPolicyResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/settings/merit-policy", tags=["HR — Merit Policy"])


def _bands_to_dicts(bands):
    return [b.model_dump(mode="json") for b in bands]


def _clear_other_defaults(db: Session, keep_id=None):
    q = db.query(MeritPolicy).filter(MeritPolicy.is_default == True, MeritPolicy.is_deleted == False)  # noqa: E712
    if keep_id is not None:
        q = q.filter(MeritPolicy.id != keep_id)
    for p in q.all():
        p.is_default = False


@router.get("/", response_model=List[MeritPolicyResponse])
def list_policies(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return (db.query(MeritPolicy)
            .filter(MeritPolicy.is_deleted == False)  # noqa: E712
            .order_by(MeritPolicy.is_default.desc(), MeritPolicy.name).all())


@router.post("/", response_model=MeritPolicyResponse, status_code=201)
def create_policy(payload: MeritPolicyCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if db.query(MeritPolicy).filter(MeritPolicy.name == payload.name, MeritPolicy.is_deleted == False).first():  # noqa: E712
        raise HTTPException(409, "A merit policy with that name already exists")
    bands = _bands_to_dicts(payload.bands) if payload.bands else list(DEFAULT_BANDS)
    p = MeritPolicy(
        name=payload.name, description=payload.description,
        merit_budget_pct=payload.merit_budget_pct, bands=bands,
        is_active=payload.is_active if payload.is_active is not None else True,
        is_default=bool(payload.is_default),
        created_by_id=admin.id,
    )
    db.add(p)
    db.flush()
    if p.is_default:
        _clear_other_defaults(db, keep_id=p.id)
    log_settings_change(db, "MERIT_POLICY", p.id, "CREATE", admin.id, after={"name": p.name}, note=p.name)
    db.commit()
    db.refresh(p)
    return p


@router.get("/{policy_id}", response_model=MeritPolicyResponse)
def get_policy(policy_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    p = db.query(MeritPolicy).filter(MeritPolicy.id == policy_id, MeritPolicy.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Merit policy not found")
    return p


@router.patch("/{policy_id}", response_model=MeritPolicyResponse)
def update_policy(policy_id: UUID, payload: MeritPolicyUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    p = db.query(MeritPolicy).filter(MeritPolicy.id == policy_id, MeritPolicy.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Merit policy not found")
    data = payload.model_dump(exclude_unset=True)
    bands = data.pop("bands", None)
    if "name" in data and data["name"] != p.name:
        if db.query(MeritPolicy).filter(MeritPolicy.name == data["name"], MeritPolicy.id != policy_id, MeritPolicy.is_deleted == False).first():  # noqa: E712
            raise HTTPException(409, "A merit policy with that name already exists")
    for k, v in data.items():
        setattr(p, k, v)
    if bands is not None:
        p.bands = _bands_to_dicts(payload.bands)
    db.flush()
    if p.is_default:
        _clear_other_defaults(db, keep_id=p.id)
    log_settings_change(db, "MERIT_POLICY", p.id, "UPDATE", admin.id, note=p.name)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{policy_id}", status_code=204)
def delete_policy(policy_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    p = db.query(MeritPolicy).filter(MeritPolicy.id == policy_id, MeritPolicy.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Merit policy not found")
    p.is_deleted = True
    p.is_default = False
    log_settings_change(db, "MERIT_POLICY", p.id, "DELETE", admin.id, note=p.name)
    db.commit()
