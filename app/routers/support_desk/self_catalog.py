"""Support Desk — self-service Knowledge Base + Announcements (auth=user).

Regular employees read PUBLISHED, non-internal KB articles and the announcements
targeted at them. The admin KB/announcement routers stay superadmin-only; this
exposes a safe read slice under /support-desk/me. prefix=/support-desk/me.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.catalog import SdKnowledgeArticle, SdKbCategory
from app.models.support_desk.ops import SdAnnouncement
from app.models.support_desk.constants import (
    ArticleStatus, ArticleVisibility, AnnouncementAudience,
)
from app.schemas.support_desk.catalog import ArticleResponse, KbCategoryResponse
from app.schemas.support_desk.ops import AnnouncementResponse
from app.utils.dependencies import get_current_user
from app.utils.support_desk import sla as sla_util

router = APIRouter(prefix="/support-desk/me", tags=["Support Desk — My Knowledge & Announcements"])

# Visibilities a logged-in employee may read (internal excluded).
_READABLE = [ArticleVisibility.PUBLIC.value, ArticleVisibility.CUSTOMER.value]


@router.get("/knowledge-base/categories", response_model=List[KbCategoryResponse])
def my_kb_categories(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cats = (db.query(SdKbCategory).filter(SdKbCategory.is_deleted == False)  # noqa: E712
            .order_by(SdKbCategory.sort_order, SdKbCategory.name).all())
    for c in cats:
        c.article_count = (db.query(SdKnowledgeArticle).filter(
            SdKnowledgeArticle.category_id == c.id,
            SdKnowledgeArticle.is_deleted == False,  # noqa: E712
            SdKnowledgeArticle.status == ArticleStatus.PUBLISHED.value,
            SdKnowledgeArticle.visibility.in_(_READABLE),
        ).count())
    return cats


@router.get("/knowledge-base", response_model=List[ArticleResponse])
def my_kb(
    category_id: Optional[UUID] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(SdKnowledgeArticle).filter(
        SdKnowledgeArticle.is_deleted == False,  # noqa: E712
        SdKnowledgeArticle.status == ArticleStatus.PUBLISHED.value,
        SdKnowledgeArticle.visibility.in_(_READABLE),
    )
    if category_id:
        query = query.filter(SdKnowledgeArticle.category_id == category_id)
    if q:
        query = query.filter(SdKnowledgeArticle.title.ilike(f"%{q.strip()}%"))
    return query.order_by(SdKnowledgeArticle.updated_at.desc()).all()


@router.get("/knowledge-base/{article_id}", response_model=ArticleResponse)
def my_article(article_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.query(SdKnowledgeArticle).filter(
        SdKnowledgeArticle.id == article_id,
        SdKnowledgeArticle.is_deleted == False,  # noqa: E712
        SdKnowledgeArticle.status == ArticleStatus.PUBLISHED.value,
        SdKnowledgeArticle.visibility.in_(_READABLE),
    ).first()
    if not a:
        raise HTTPException(404, "Article not found")
    a.views = (a.views or 0) + 1
    db.commit()
    db.refresh(a)
    return a


@router.get("/announcements", response_model=List[AnnouncementResponse])
def my_announcements(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    now = sla_util.now_utc()
    rows = (db.query(SdAnnouncement).filter(
        SdAnnouncement.is_deleted == False,  # noqa: E712
        SdAnnouncement.is_active == True,     # noqa: E712
    ).order_by(SdAnnouncement.publish_date.desc().nullslast()).all())
    uid = str(user.id)
    out = []
    for a in rows:
        # Live window
        if a.publish_date and a.publish_date > now:
            continue
        if a.expiry_date and a.expiry_date < now:
            continue
        # Audience
        if a.audience == AnnouncementAudience.ALL.value:
            out.append(a)
        elif a.audience == AnnouncementAudience.USERS.value and uid in [str(x) for x in (a.target_user_ids or [])]:
            out.append(a)
        # organization/contract targeting needs a customer-account link (deferred) — skip for employees
    return out
