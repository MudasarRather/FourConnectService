"""Support Desk — Knowledge Base + Service Catalog CRUD (admin). Routers:
kb_categories_router, articles_router, service_items_router, service_requests_router.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.catalog import (
    SdKbCategory, SdKnowledgeArticle, SdServiceItem, SdServiceRequest,
)
from app.models.support_desk.constants import ArticleStatus, ServiceRequestStatus
from app.schemas.support_desk.catalog import (
    KbCategoryCreate, KbCategoryUpdate, KbCategoryResponse,
    ArticleCreate, ArticleUpdate, ArticleResponse,
    ServiceItemCreate, ServiceItemUpdate, ServiceItemResponse,
    ServiceRequestCreate, ServiceRequestUpdate, ServiceRequestResponse,
)
from app.utils.dependencies import get_current_superuser, get_support_agent
from app.utils.support_desk import sla as sla_util

# Reads (lists) are open to support agents (KB articles, service catalog, service
# requests). Authoring/CUD + approvals stay superuser (admin-only).


# ═══════════ KB Categories ═══════════
kb_categories_router = APIRouter(prefix="/support-desk/kb-categories", tags=["Support Desk — KB Categories"])


@kb_categories_router.get("/", response_model=List[KbCategoryResponse])
def list_kb_cats(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    cats = db.query(SdKbCategory).filter(SdKbCategory.is_deleted == False).order_by(  # noqa: E712
        SdKbCategory.sort_order, SdKbCategory.name).all()
    for c in cats:
        c.article_count = db.query(SdKnowledgeArticle).filter(
            SdKnowledgeArticle.category_id == c.id, SdKnowledgeArticle.is_deleted == False).count()  # noqa: E712
    return cats


@kb_categories_router.post("/", response_model=KbCategoryResponse, status_code=status.HTTP_201_CREATED)
def create_kb_cat(payload: KbCategoryCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    cat = SdKbCategory(**payload.model_dump(exclude_unset=True))
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@kb_categories_router.patch("/{cid}", response_model=KbCategoryResponse)
def update_kb_cat(cid: UUID, payload: KbCategoryUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    cat = db.query(SdKbCategory).filter(SdKbCategory.id == cid, SdKbCategory.is_deleted == False).first()  # noqa: E712
    if not cat:
        raise HTTPException(404, "Category not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(cat, k, v)
    db.commit()
    db.refresh(cat)
    return cat


@kb_categories_router.delete("/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kb_cat(cid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    cat = db.query(SdKbCategory).filter(SdKbCategory.id == cid, SdKbCategory.is_deleted == False).first()  # noqa: E712
    if not cat:
        raise HTTPException(404, "Category not found")
    cat.is_deleted = True
    db.commit()
    return None


# ═══════════ Articles ═══════════
articles_router = APIRouter(prefix="/support-desk/articles", tags=["Support Desk — Knowledge Base"])


@articles_router.get("/", response_model=List[ArticleResponse])
def list_articles(
    category_id: Optional[UUID] = None,
    visibility: Optional[str] = None,
    status_f: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    query = db.query(SdKnowledgeArticle).filter(SdKnowledgeArticle.is_deleted == False)  # noqa: E712
    if category_id:
        query = query.filter(SdKnowledgeArticle.category_id == category_id)
    if visibility:
        query = query.filter(SdKnowledgeArticle.visibility == visibility)
    if status_f:
        query = query.filter(SdKnowledgeArticle.status == status_f)
    if q:
        query = query.filter(SdKnowledgeArticle.title.ilike(f"%{q.strip()}%"))
    arts = query.order_by(SdKnowledgeArticle.updated_at.desc()).all()
    cat_ids = {a.category_id for a in arts if a.category_id}
    names = {}
    if cat_ids:
        names = {str(r[0]): r[1] for r in db.query(SdKbCategory.id, SdKbCategory.name).filter(SdKbCategory.id.in_(cat_ids)).all()}
    for a in arts:
        a.category_name = names.get(str(a.category_id)) if a.category_id else None
    return arts


@articles_router.post("/", response_model=ArticleResponse, status_code=status.HTTP_201_CREATED)
def create_article(payload: ArticleCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = SdKnowledgeArticle(**payload.model_dump(exclude_unset=True), author_id=admin.id)
    if a.status == ArticleStatus.PUBLISHED.value:
        a.published_at = sla_util.now_utc()
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@articles_router.get("/{aid}", response_model=ArticleResponse)
def get_article(aid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = db.query(SdKnowledgeArticle).filter(SdKnowledgeArticle.id == aid, SdKnowledgeArticle.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Article not found")
    return a


@articles_router.patch("/{aid}", response_model=ArticleResponse)
def update_article(aid: UUID, payload: ArticleUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = db.query(SdKnowledgeArticle).filter(SdKnowledgeArticle.id == aid, SdKnowledgeArticle.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Article not found")
    update = payload.model_dump(exclude_unset=True)
    was_published = a.status == ArticleStatus.PUBLISHED.value
    for k, v in update.items():
        setattr(a, k, v)
    if a.status == ArticleStatus.PUBLISHED.value and not was_published and a.published_at is None:
        a.published_at = sla_util.now_utc()
    db.commit()
    db.refresh(a)
    return a


@articles_router.delete("/{aid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_article(aid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = db.query(SdKnowledgeArticle).filter(SdKnowledgeArticle.id == aid, SdKnowledgeArticle.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Article not found")
    a.is_deleted = True
    db.commit()
    return None


# ═══════════ Service Items ═══════════
service_items_router = APIRouter(prefix="/support-desk/service-items", tags=["Support Desk — Service Catalog"])


@service_items_router.get("/", response_model=List[ServiceItemResponse])
def list_service_items(db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    return db.query(SdServiceItem).filter(SdServiceItem.is_deleted == False).order_by(SdServiceItem.name).all()  # noqa: E712


@service_items_router.post("/", response_model=ServiceItemResponse, status_code=status.HTTP_201_CREATED)
def create_service_item(payload: ServiceItemCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    item = SdServiceItem(**payload.model_dump(exclude_unset=True))
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@service_items_router.patch("/{iid}", response_model=ServiceItemResponse)
def update_service_item(iid: UUID, payload: ServiceItemUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    item = db.query(SdServiceItem).filter(SdServiceItem.id == iid, SdServiceItem.is_deleted == False).first()  # noqa: E712
    if not item:
        raise HTTPException(404, "Service item not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@service_items_router.delete("/{iid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service_item(iid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    item = db.query(SdServiceItem).filter(SdServiceItem.id == iid, SdServiceItem.is_deleted == False).first()  # noqa: E712
    if not item:
        raise HTTPException(404, "Service item not found")
    item.is_deleted = True
    db.commit()
    return None


# ═══════════ Service Requests ═══════════
service_requests_router = APIRouter(prefix="/support-desk/service-requests", tags=["Support Desk — Service Requests"])


@service_requests_router.get("/", response_model=List[ServiceRequestResponse])
def list_service_requests(
    status_f: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    query = db.query(SdServiceRequest).filter(SdServiceRequest.is_deleted == False)  # noqa: E712
    if status_f:
        query = query.filter(SdServiceRequest.status == status_f)
    reqs = query.order_by(SdServiceRequest.created_at.desc()).all()
    item_ids = {r.service_item_id for r in reqs if r.service_item_id}
    names = {}
    if item_ids:
        names = {str(r[0]): r[1] for r in db.query(SdServiceItem.id, SdServiceItem.name).filter(SdServiceItem.id.in_(item_ids)).all()}
    for r in reqs:
        r.service_item_name = names.get(str(r.service_item_id)) if r.service_item_id else None
    return reqs


@service_requests_router.patch("/{rid}", response_model=ServiceRequestResponse)
def update_service_request(rid: UUID, payload: ServiceRequestUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = db.query(SdServiceRequest).filter(SdServiceRequest.id == rid, SdServiceRequest.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Service request not found")
    update = payload.model_dump(exclude_unset=True)
    if update.get("status") == ServiceRequestStatus.APPROVED.value and r.approved_at is None:
        r.approver_id = admin.id
        r.approved_at = sla_util.now_utc()
    for k, v in update.items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return r
