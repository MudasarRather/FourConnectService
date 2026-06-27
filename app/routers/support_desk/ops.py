"""Support Desk — Announcements, Automation Rules, Settings CRUD (admin).
Routers: announcements_router, automation_router, settings_router.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ops import SdAnnouncement, SdAutomationRule, SdSetting
from app.schemas.support_desk.ops import (
    AnnouncementCreate, AnnouncementUpdate, AnnouncementResponse,
    AutomationRuleCreate, AutomationRuleUpdate, AutomationRuleResponse,
    SettingUpsert, SettingResponse,
)
from app.utils.dependencies import get_current_superuser


def _stringify_ids(data: dict, key: str):
    if key in data and data[key] is not None:
        data[key] = [str(x) for x in data[key]]


# ═══════════ Announcements ═══════════
announcements_router = APIRouter(prefix="/support-desk/announcements", tags=["Support Desk — Announcements"])


@announcements_router.get("/", response_model=List[AnnouncementResponse])
def list_announcements(active_only: bool = False, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    query = db.query(SdAnnouncement).filter(SdAnnouncement.is_deleted == False)  # noqa: E712
    if active_only:
        query = query.filter(SdAnnouncement.is_active == True)  # noqa: E712
    return query.order_by(SdAnnouncement.created_at.desc()).all()


@announcements_router.post("/", response_model=AnnouncementResponse, status_code=status.HTTP_201_CREATED)
def create_announcement(payload: AnnouncementCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    data = payload.model_dump(exclude_unset=True)
    _stringify_ids(data, "target_user_ids")
    a = SdAnnouncement(**data, created_by_id=admin.id)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@announcements_router.patch("/{aid}", response_model=AnnouncementResponse)
def update_announcement(aid: UUID, payload: AnnouncementUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = db.query(SdAnnouncement).filter(SdAnnouncement.id == aid, SdAnnouncement.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Announcement not found")
    data = payload.model_dump(exclude_unset=True)
    _stringify_ids(data, "target_user_ids")
    for k, v in data.items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return a


@announcements_router.delete("/{aid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_announcement(aid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = db.query(SdAnnouncement).filter(SdAnnouncement.id == aid, SdAnnouncement.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Announcement not found")
    a.is_deleted = True
    db.commit()
    return None


# ═══════════ Automation Rules ═══════════
automation_router = APIRouter(prefix="/support-desk/automation-rules", tags=["Support Desk — Automation Rules"])


@automation_router.get("/", response_model=List[AutomationRuleResponse])
def list_rules(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return (db.query(SdAutomationRule).filter(SdAutomationRule.is_deleted == False)  # noqa: E712
            .order_by(SdAutomationRule.order_index, SdAutomationRule.created_at).all())


@automation_router.post("/", response_model=AutomationRuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(payload: AutomationRuleCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = SdAutomationRule(**payload.model_dump(exclude_unset=True), created_by_id=admin.id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@automation_router.patch("/{rid}", response_model=AutomationRuleResponse)
def update_rule(rid: UUID, payload: AutomationRuleUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = db.query(SdAutomationRule).filter(SdAutomationRule.id == rid, SdAutomationRule.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rule not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return r


@automation_router.delete("/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = db.query(SdAutomationRule).filter(SdAutomationRule.id == rid, SdAutomationRule.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rule not found")
    r.is_deleted = True
    db.commit()
    return None


# ═══════════ Settings ═══════════
settings_router = APIRouter(prefix="/support-desk/settings", tags=["Support Desk — Settings"])


@settings_router.get("/", response_model=List[SettingResponse])
def list_settings(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return db.query(SdSetting).order_by(SdSetting.key).all()


@settings_router.get("/{key}", response_model=SettingResponse)
def get_setting(key: str, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    s = db.query(SdSetting).filter(SdSetting.key == key).first()
    if not s:
        raise HTTPException(404, "Setting not found")
    return s


@settings_router.put("/", response_model=SettingResponse)
def upsert_setting(payload: SettingUpsert, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    s = db.query(SdSetting).filter(SdSetting.key == payload.key).first()
    if s:
        s.value = payload.value
        s.updated_by_id = admin.id
    else:
        s = SdSetting(key=payload.key, value=payload.value, updated_by_id=admin.id)
        db.add(s)
    db.commit()
    db.refresh(s)
    return s
