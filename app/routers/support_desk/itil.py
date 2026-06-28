"""Support Desk — ITIL CRUD (admin). Routers: change_router, problem_router, asset_router.

Change Requests carry a configurable number and an ITIL status workflow;
Problems hold RCA + linked-record arrays; Customer Assets track client infra.
"""
from __future__ import annotations

import uuid
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.itil import SdChangeRequest, SdProblem, SdCustomerAsset
from app.models.support_desk.core import SdOrganization
from app.models.support_desk.constants import (
    ChangeStatus, ProblemStatus, NUMBERING_MODULE_CHANGE, NUMBERING_MODULE_PROBLEM,
)
from app.schemas.support_desk.itil import (
    ChangeRequestCreate, ChangeRequestUpdate, ChangeRequestResponse,
    ProblemCreate, ProblemUpdate, ProblemResponse,
    CustomerAssetCreate, CustomerAssetUpdate, CustomerAssetResponse,
)
from app.utils.dependencies import get_support_agent
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit


def _number(db: Session, module: str, prefix: str) -> str:
    try:
        from app.utils.hr.numbering import next_number
        n = next_number(db, module)
        if n:
            return n
    except Exception:
        pass
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def _org_names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {str(r[0]): r[1] for r in db.query(SdOrganization.id, SdOrganization.name).filter(SdOrganization.id.in_(ids)).all()}


# ═══════════ Change Requests ═══════════
change_router = APIRouter(prefix="/support-desk/change-requests", tags=["Support Desk — Change Requests"])
_CHANGE_STATUSES = {s.value for s in ChangeStatus}


@change_router.get("/", response_model=List[ChangeRequestResponse])
def list_changes(status_f: Optional[str] = None, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    query = db.query(SdChangeRequest).filter(SdChangeRequest.is_deleted == False)  # noqa: E712
    if status_f:
        query = query.filter(SdChangeRequest.status == status_f)
    rows = query.order_by(SdChangeRequest.created_at.desc()).all()
    names = _org_names(db, {r.organization_id for r in rows})
    for r in rows:
        r.organization_name = names.get(str(r.organization_id)) if r.organization_id else None
    return rows


@change_router.post("/", response_model=ChangeRequestResponse, status_code=status.HTTP_201_CREATED)
def create_change(payload: ChangeRequestCreate, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    c = SdChangeRequest(**payload.model_dump(exclude_unset=True),
                        change_number=_number(db, NUMBERING_MODULE_CHANGE, "CHG"),
                        created_by_id=admin.id, status=ChangeStatus.DRAFT.value)
    db.add(c)
    db.flush()
    write_audit(db, entity_type="change", op="created", entity_id=c.id, actor_id=admin.id, request=request,
                details={"title": c.title})
    db.commit()
    db.refresh(c)
    return c


@change_router.get("/{cid}", response_model=ChangeRequestResponse)
def get_change(cid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    c = db.query(SdChangeRequest).filter(SdChangeRequest.id == cid, SdChangeRequest.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Change request not found")
    return c


@change_router.patch("/{cid}", response_model=ChangeRequestResponse)
def update_change(cid: UUID, payload: ChangeRequestUpdate, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    c = db.query(SdChangeRequest).filter(SdChangeRequest.id == cid, SdChangeRequest.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Change request not found")
    update = payload.model_dump(exclude_unset=True)
    if "status" in update:
        if update["status"] not in _CHANGE_STATUSES:
            raise HTTPException(422, f"Invalid status '{update['status']}'")
        if update["status"] == ChangeStatus.APPROVED.value and c.approved_at is None:
            c.approver_id = admin.id
            c.approved_at = sla_util.now_utc()
    for k, v in update.items():
        setattr(c, k, v)
    write_audit(db, entity_type="change", op="updated", entity_id=c.id, actor_id=admin.id, request=request, details=update)
    db.commit()
    db.refresh(c)
    return c


@change_router.delete("/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_change(cid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    c = db.query(SdChangeRequest).filter(SdChangeRequest.id == cid, SdChangeRequest.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Change request not found")
    c.is_deleted = True
    db.commit()
    return None


# ═══════════ Problems ═══════════
problem_router = APIRouter(prefix="/support-desk/problems", tags=["Support Desk — Problem Management"])


@problem_router.get("/", response_model=List[ProblemResponse])
def list_problems(status_f: Optional[str] = None, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    query = db.query(SdProblem).filter(SdProblem.is_deleted == False)  # noqa: E712
    if status_f:
        query = query.filter(SdProblem.status == status_f)
    return query.order_by(SdProblem.created_at.desc()).all()


@problem_router.post("/", response_model=ProblemResponse, status_code=status.HTTP_201_CREATED)
def create_problem(payload: ProblemCreate, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    data = payload.model_dump(exclude_unset=True)
    # store UUID lists as strings in JSONB
    for key in ("linked_ticket_ids", "linked_change_ids", "linked_asset_ids"):
        if key in data and data[key] is not None:
            data[key] = [str(x) for x in data[key]]
    p = SdProblem(**data, problem_number=_number(db, NUMBERING_MODULE_PROBLEM, "PRB"),
                  created_by_id=admin.id, status=ProblemStatus.OPEN.value)
    db.add(p)
    db.flush()
    write_audit(db, entity_type="problem", op="created", entity_id=p.id, actor_id=admin.id, request=request,
                details={"title": p.title})
    db.commit()
    db.refresh(p)
    return p


@problem_router.get("/{pid}", response_model=ProblemResponse)
def get_problem(pid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    p = db.query(SdProblem).filter(SdProblem.id == pid, SdProblem.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Problem not found")
    return p


@problem_router.patch("/{pid}", response_model=ProblemResponse)
def update_problem(pid: UUID, payload: ProblemUpdate, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    p = db.query(SdProblem).filter(SdProblem.id == pid, SdProblem.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Problem not found")
    data = payload.model_dump(exclude_unset=True)
    for key in ("linked_ticket_ids", "linked_change_ids", "linked_asset_ids"):
        if key in data and data[key] is not None:
            data[key] = [str(x) for x in data[key]]
    for k, v in data.items():
        setattr(p, k, v)
    write_audit(db, entity_type="problem", op="updated", entity_id=p.id, actor_id=admin.id, request=request, details={})
    db.commit()
    db.refresh(p)
    return p


@problem_router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_problem(pid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    p = db.query(SdProblem).filter(SdProblem.id == pid, SdProblem.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Problem not found")
    p.is_deleted = True
    db.commit()
    return None


# ═══════════ Customer Assets ═══════════
asset_router = APIRouter(prefix="/support-desk/customer-assets", tags=["Support Desk — Customer Assets"])


@asset_router.get("/", response_model=List[CustomerAssetResponse])
def list_assets(organization_id: Optional[UUID] = None, asset_type: Optional[str] = None,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    query = db.query(SdCustomerAsset).filter(SdCustomerAsset.is_deleted == False)  # noqa: E712
    if organization_id:
        query = query.filter(SdCustomerAsset.organization_id == organization_id)
    if asset_type:
        query = query.filter(SdCustomerAsset.asset_type == asset_type)
    rows = query.order_by(SdCustomerAsset.name).all()
    names = _org_names(db, {r.organization_id for r in rows})
    for r in rows:
        r.organization_name = names.get(str(r.organization_id)) if r.organization_id else None
    return rows


@asset_router.post("/", response_model=CustomerAssetResponse, status_code=status.HTTP_201_CREATED)
def create_asset(payload: CustomerAssetCreate, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    a = SdCustomerAsset(**payload.model_dump(exclude_unset=True))
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@asset_router.patch("/{aid}", response_model=CustomerAssetResponse)
def update_asset(aid: UUID, payload: CustomerAssetUpdate, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    a = db.query(SdCustomerAsset).filter(SdCustomerAsset.id == aid, SdCustomerAsset.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Asset not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return a


@asset_router.delete("/{aid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(aid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    a = db.query(SdCustomerAsset).filter(SdCustomerAsset.id == aid, SdCustomerAsset.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Asset not found")
    a.is_deleted = True
    db.commit()
    return None
