from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, or_, and_, func
from uuid import UUID
from typing import Optional, List
from datetime import datetime
import re

from app.database import get_db
from app.models.note import ProjectNote
from app.models.user import User
from app.models.project import Project
from app.models.notification import Notification
from app.schemas.notes import NoteCreate, NoteUpdate, NoteResponse, NoteListResponse
from app.config import get_settings
from huggingface_hub import InferenceClient
import os
from pydantic import BaseModel

class GrammarCheckRequest(BaseModel):
    text: str

class GrammarCheckResponse(BaseModel):
    corrected_text: str

router = APIRouter(prefix="/project-notes", tags=["notes"])


# ── Auth helper ──────────────────────────────────────────────
def get_current_user(token: str, db: Session) -> User:
    """Decode JWT and return user using the app's existing auth utility"""
    from app.utils.auth import decode_access_token
    
    clean_token = token.replace("Bearer ", "") if token.startswith("Bearer ") else token
    payload = decode_access_token(clean_token)
    
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _enrich(note: ProjectNote, db: Session) -> dict:
    """Convert a ProjectNote ORM object to a response dict with author info"""
    author = db.query(User).filter(User.id == note.author_id).first()
    
    # Resolve mention names
    mentioned_names = []
    if note.mentions:
        for uid in note.mentions:
            u = db.query(User).filter(User.id == uid).first()
            if u:
                mentioned_names.append(u.full_name)
    
    locked_by_name = None
    if note.locked_by_id:
        locker = db.query(User).filter(User.id == note.locked_by_id).first()
        if locker:
            locked_by_name = locker.full_name

    return {
        "id": note.id,
        "project_id": note.project_id,
        "author_id": note.author_id,
        "author_name": author.full_name if author else "Unknown",
        "author_avatar": author.avatar_url if author else None,
        "note_type": note.note_type,
        "title": note.title,
        "content": note.content,
        "mentions": note.mentions or [],
        "mentioned_names": mentioned_names,
        "is_pinned": note.is_pinned,
        "is_locked": note.is_locked,
        "locked_by_id": note.locked_by_id,
        "locked_by_name": locked_by_name,
        "locked_at": note.locked_at,
        "is_deleted": note.is_deleted,
        "attachment_urls": note.attachment_urls or [],
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }


# ── LIST NOTES ───────────────────────────────────────────────
@router.get("/{project_id}/notes", response_model=NoteListResponse)
def list_notes(
    project_id: UUID,
    note_type: Optional[str] = None,
    search: Optional[str] = None,
    author_id: Optional[UUID] = None,
    is_pinned: Optional[bool] = None,
    is_locked: Optional[bool] = None,
    has_attachments: Optional[bool] = None,
    sort: str = Query("newest", pattern="^(newest|oldest|pinned)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    
    q = db.query(ProjectNote).filter(
        ProjectNote.project_id == project_id,
        ProjectNote.is_deleted == False,
    )

    # Private notes: only visible to creator and admins
    if not user.is_superuser:
        q = q.filter(
            or_(
                ProjectNote.note_type != "private",
                ProjectNote.author_id == user.id,
            )
        )

    # Filters
    if note_type and note_type != "all":
        q = q.filter(ProjectNote.note_type == note_type)
    if search:
        q = q.filter(
            or_(
                ProjectNote.title.ilike(f"%{search}%"),
                ProjectNote.content.ilike(f"%{search}%"),
            )
        )
    if author_id:
        q = q.filter(ProjectNote.author_id == author_id)
    if is_pinned is not None:
        q = q.filter(ProjectNote.is_pinned == is_pinned)
    if is_locked is not None:
        q = q.filter(ProjectNote.is_locked == is_locked)

    total = q.count()

    # Sort
    if sort == "newest":
        q = q.order_by(ProjectNote.is_pinned.desc(), ProjectNote.created_at.desc())
    elif sort == "oldest":
        q = q.order_by(ProjectNote.is_pinned.desc(), ProjectNote.created_at.asc())
    elif sort == "pinned":
        q = q.order_by(ProjectNote.is_pinned.desc(), ProjectNote.updated_at.desc())

    # Paginate
    notes = q.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "items": [_enrich(n, db) for n in notes],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── MENTIONS HELPER ──────────────────────────────────────────
def extract_mentions_from_html(html: str) -> List[str]:
    """Extract UUID strings from <span data-id="..."> tags in content"""
    if not html:
        return []
    # Match data-id="uuid-string"
    pattern = r'data-id="([a-f0-9\-]{36})"'
    return re.findall(pattern, html)


def process_mentions(
    db: Session,
    project_id: UUID,
    note_id: UUID,
    note_title: str,
    author: User,
    new_mentions: List[str],
    old_mentions: List[str] = None
):
    if not new_mentions:
        return

    # Normalize everything to strings for comparison to avoid UUID vs String mismatches
    old_set = {str(m) for m in old_mentions} if old_mentions else set()
    new_set = {str(m) for m in new_mentions}
    
    # Find added mentions (user IDs as strings)
    added_ids = new_set - old_set
    
    for uid_str in added_ids:
        try:
            # Validate UUID format
            target_uid = UUID(uid_str)
            
            # Don't notify self
            if target_uid == author.id:
                continue
            
            # Create notification
            notif = Notification(
                user_id=target_uid,
                type="mention",
                title="New Mention",
                message=f"{author.full_name} mentioned you in note: {note_title}",
                related_project_id=project_id,
                related_user_id=author.id,
                # Link to project notes
                action_url=f"/user/projects/notes?projectId={project_id}"
            )
            db.add(notif)
        except Exception:
            continue


# ── CREATE NOTE ──────────────────────────────────────────────
@router.post("/{project_id}/notes", response_model=NoteResponse)
def create_note(
    project_id: UUID,
    note: NoteCreate,
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)

    # Audit notes cannot be created manually unless admin
    if note.note_type == "audit" and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Audit notes are system-generated only")

    db_note = ProjectNote(
        project_id=project_id,
        author_id=user.id,
        title=note.title,
        content=note.content,
        note_type=note.note_type,
        mentions=[str(m) for m in note.mentions] if note.mentions else [],
        is_pinned=note.is_pinned,
        attachment_urls=[a.dict() if hasattr(a, 'dict') else a for a in note.attachment_urls] if note.attachment_urls else [],
    )
    db.add(db_note)
    db.commit()
    db.refresh(db_note)
    
    # Process mentions
    effective_mentions = db_note.mentions or []
    if not effective_mentions and db_note.content:
        # Fallback to extraction if list is empty
        effective_mentions = extract_mentions_from_html(db_note.content)
        if effective_mentions:
            db_note.mentions = effective_mentions
            db.add(db_note)

    if effective_mentions:
        process_mentions(
            db=db,
            project_id=project_id,
            note_id=db_note.id,
            note_title=db_note.title,
            author=user,
            new_mentions=effective_mentions
        )
        db.commit()

    return _enrich(db_note, db)


# ── MENTION SUGGESTIONS ─────────────────────────────────────
@router.get("/{project_id}/notes/mentions")
def get_mention_suggestions(
    project_id: UUID,
    q: Optional[str] = None,
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    """Get project team members for @mention suggestions"""
    user = get_current_user(token, db)

    from app.models.team_member import TeamMember
    # Get team member user IDs
    members = db.query(TeamMember.user_id).filter(
        TeamMember.project_id == project_id,
        TeamMember.status == "accepted",
    ).all()
    member_ids = [m[0] for m in members]
    
    # Also include project creator
    project = db.query(Project).filter(Project.id == project_id).first()
    if project and project.created_by_id:
        member_ids.append(project.created_by_id)

    users_q = db.query(User).filter(User.id.in_(member_ids), User.is_active == True)
    
    # Exclude current user
    users_q = users_q.filter(User.id != user.id)

    if q:
        users_q = users_q.filter(User.full_name.ilike(f"%{q}%"))
    
    users = users_q.limit(20).all()
    return [
        {"id": str(u.id), "name": u.full_name, "avatar": u.avatar_url}
        for u in users
    ]


# ── GET SINGLE NOTE ──────────────────────────────────────────
@router.get("/{project_id}/notes/{note_id}", response_model=NoteResponse)
def get_note(
    project_id: UUID,
    note_id: UUID,
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    note = db.query(ProjectNote).filter(
        ProjectNote.id == note_id,
        ProjectNote.project_id == project_id,
        ProjectNote.is_deleted == False,
    ).first()

    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Private note check
    if note.note_type == "private" and note.author_id != user.id and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")

    return _enrich(note, db)


# ── UPDATE NOTE ──────────────────────────────────────────────
@router.put("/{project_id}/notes/{note_id}", response_model=NoteResponse)
def update_note(
    project_id: UUID,
    note_id: UUID,
    update: NoteUpdate,
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    note = db.query(ProjectNote).filter(
        ProjectNote.id == note_id,
        ProjectNote.project_id == project_id,
        ProjectNote.is_deleted == False,
    ).first()

    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Locked check
    if note.is_locked and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Note is locked and cannot be edited")

    # Audit notes are immutable unless admin
    if note.note_type == "audit" and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Audit notes cannot be edited")

    # Capture old mentions state
    old_mentions = list(note.mentions) if note.mentions else []

    # Apply updates
    if update.title is not None:
        note.title = update.title
    if update.content is not None:
        note.content = update.content
    if update.note_type is not None and update.note_type != "audit":
        note.note_type = update.note_type
    if update.mentions is not None:
        note.mentions = [str(m) for m in update.mentions]
    if update.is_pinned is not None:
        note.is_pinned = update.is_pinned
    if update.attachment_urls is not None:
        note.attachment_urls = update.attachment_urls

    db.commit()
    db.refresh(note)
    
    # Process mentions if updated OR if we have content that might contain mentions
    effective_mentions = note.mentions or []
    if update.mentions is None and note.content:
        # If frontend didn't send a list, try to extract from current content
        extracted = extract_mentions_from_html(note.content)
        if extracted:
             effective_mentions = extracted
             note.mentions = extracted
             db.add(note)

    if effective_mentions:
        try:
            process_mentions(
                db=db,
                project_id=project_id,
                note_id=note.id,
                note_title=note.title,
                author=user,
                new_mentions=effective_mentions,
                old_mentions=old_mentions
            )
            db.commit()
        except Exception:
            db.rollback()

    return _enrich(note, db)


# ── DELETE NOTE (soft) ───────────────────────────────────────
@router.delete("/{project_id}/notes/{note_id}")
def delete_note(
    project_id: UUID,
    note_id: UUID,
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    note = db.query(ProjectNote).filter(
        ProjectNote.id == note_id,
        ProjectNote.project_id == project_id,
    ).first()

    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    
    # Permission check: Author or Admin
    if note.author_id != user.id and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Only the author or an admin can delete notes")

    if note.is_locked and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Locked notes cannot be deleted")

    if note.note_type == "audit" and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Audit notes cannot be deleted")

    note.is_deleted = True
    db.commit()
    return {"detail": "Note deleted"}


# ── TOGGLE PIN ───────────────────────────────────────────────
@router.patch("/{project_id}/notes/{note_id}/pin")
def toggle_pin(
    project_id: UUID,
    note_id: UUID,
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    note = db.query(ProjectNote).filter(
        ProjectNote.id == note_id,
        ProjectNote.project_id == project_id,
        ProjectNote.is_deleted == False,
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    note.is_pinned = not note.is_pinned
    db.commit()
    return {"is_pinned": note.is_pinned}


# ── TOGGLE LOCK ──────────────────────────────────────────────
@router.patch("/{project_id}/notes/{note_id}/lock")
def toggle_lock(
    project_id: UUID,
    note_id: UUID,
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    note = db.query(ProjectNote).filter(
        ProjectNote.id == note_id,
        ProjectNote.project_id == project_id,
        ProjectNote.is_deleted == False,
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Permission check: Author or Admin
    if note.author_id != user.id and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Only the author or an admin can lock/unlock notes")

    # Only admin can unlock if already locked by someone else? 
    # Logic: Author can lock/unlock their own note. Admin can override.
    if note.is_locked:
        # If locked by someone else (e.g. admin locked it), normal author might be blocked?
        # Current requirement: "user cannot delete or lock other users note". 
        # Implies they CAN lock their own. 
        # If admin locked it, usually author shouldn't unlock.
        if note.locked_by_id and note.locked_by_id != user.id and not user.is_superuser:
             raise HTTPException(status_code=403, detail="Note locked by another user")

    note.is_locked = not note.is_locked
    if note.is_locked:
        note.locked_by_id = user.id
        note.locked_at = datetime.utcnow()
    else:
        note.locked_by_id = None
        note.locked_at = None
    db.commit()
    return {"is_locked": note.is_locked}


# ── GRAMMAR CHECK (AI PROXY) ─────────────────────────────────
@router.post("/grammar-check", response_model=GrammarCheckResponse)
def grammar_check_proxy(
    payload: GrammarCheckRequest,
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
):
    """
    Proxies grammar check requests to HuggingFace Inference API.
    Uses server-side token to keep it secure.
    """
    # Verify auth (optional, but good practice to prevent abuse)
    get_current_user(token, db)

    settings = get_settings()
    hf_token = settings.HF_API_TOKEN
    
    if not hf_token or "placeholder" in hf_token:
        return {"corrected_text": payload.text}

    try:
        client = InferenceClient(token=hf_token)
        
        messages = [
            {"role": "system", "content": "You are an expert HTML content editor. Your task is to rewrite the text content within the provided HTML to correct grammar, spelling, and improve tone. \n\nCRITICAL RULES:\n1. DATA PRESERVATION: You must return the EXACT same HTML structure. Do NOT remove, add, or modify any HTML tags (like <span style...>, <b>, <p>, <ul>, <li>, etc.).\n2. ATTRIBUTES: Do NOT change any tag attributes (class, style, data-id, etc.).\n3. MENTIONS: Preserve all <span class=\"mention-pill\"...> tags and plain text @mentions exactly as they are.\n4. SCOPE: Only polish the human-readable text between the tags.\n\nReturn ONLY the processed HTML string, no markdown formatting, no explanations."},
            {"role": "user", "content": payload.text}
        ]
        
        response = client.chat_completion(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=messages,
            max_tokens=500,
            stream=False
        )
        
        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content
            # Cleanup quotes if model is chatty
            content = content.strip().strip('"')
            return {"corrected_text": content}
        else:
            return {"corrected_text": payload.text}

    except Exception as e:
        print(f"Grammar check failed: {e}")
        return {"corrected_text": payload.text}



