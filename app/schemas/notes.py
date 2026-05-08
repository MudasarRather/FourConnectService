from typing import Optional, List, Any
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class AttachmentItem(BaseModel):
    name: str
    url: str
    size: Optional[int] = 0


class NoteCreate(BaseModel):
    title: str
    content: str = ""
    note_type: str = "general"  # general, financial, private, audit, other
    mentions: Optional[List[UUID]] = []
    is_pinned: bool = False
    attachment_urls: Optional[List[AttachmentItem]] = []


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    note_type: Optional[str] = None
    mentions: Optional[List[UUID]] = None
    is_pinned: Optional[bool] = None
    attachment_urls: Optional[List[Any]] = None


class NoteResponse(BaseModel):
    id: UUID
    project_id: UUID
    author_id: UUID
    author_name: Optional[str] = None
    author_avatar: Optional[str] = None
    note_type: str
    title: str
    content: str
    mentions: Optional[List[UUID]] = []
    mentioned_names: Optional[List[str]] = []
    is_pinned: bool = False
    is_locked: bool = False
    locked_by_id: Optional[UUID] = None
    locked_by_name: Optional[str] = None
    locked_at: Optional[datetime] = None
    is_deleted: bool = False
    attachment_urls: Optional[List[Any]] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class NoteListResponse(BaseModel):
    items: List[NoteResponse]
    total: int
    page: int = 1
    page_size: int = 20
