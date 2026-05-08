from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, and_, func, cast, String
from app.database import get_db
from app.utils.dependencies import get_current_active_user
from app.models.user import User
from app.models.drive_document import DriveDocument, DriveActivity, DriveFolder
from app.models.notification import Notification
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid
import os

router = APIRouter(
    prefix="/drive",
    tags=["Document Drive"]
)

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "drive")
os.makedirs(STORAGE_DIR, exist_ok=True)


# ─── HELPERS ───
def _apply_access_filter(query, current_user):
    """Restrict a DriveDocument query to rows the current user is allowed to see.

    Rules:
      - Superusers see everything.
      - The uploader always sees their own document (any status).
      - Everyone else sees a doc only if it's reachable via Organization,
        explicit share, or matching Department — AND its status is NOT
        'Pending Approval'. Pending docs stay hidden from share recipients
        until an admin approves.
    """
    if getattr(current_user, 'is_superuser', False):
        return query
    return query.filter(
        or_(
            DriveDocument.uploaded_by == current_user.id,
            and_(
                DriveDocument.status != 'Pending Approval',
                or_(
                    DriveDocument.access_level == 'Organization',
                    cast(DriveDocument.shared_with, String).contains(str(current_user.id)),
                    and_(
                        DriveDocument.access_level == 'Department',
                        DriveDocument.department == current_user.department,
                    ),
                ),
            ),
        )
    )


def _drive_action_url(is_admin: bool = False) -> str:
    """Where the bell click sends the recipient. Admin-target notifications point to the
    admin portal; user-target notifications point to the user portal."""
    return "/admin/documents/document-drive" if is_admin else "/user/documents/document-drive"


def _notify(db: Session, user_id, type_: str, title: str, message: str, action_url: str):
    """Append a Notification row. Caller is responsible for db.commit()."""
    db.add(Notification(
        user_id=user_id,
        type=type_,
        title=title,
        message=message,
        action_url=action_url,
    ))


def _admin_recipient_ids(db: Session) -> List:
    """All active superuser ids — recipients for 'pending approval' notifications."""
    return [u.id for u in db.query(User).filter(User.is_superuser == True, User.is_active == True).all()]


def log_activity(db: Session, doc_id, user_id, user_name, action, details=None):
    activity = DriveActivity(
        document_id=doc_id,
        user_id=user_id,
        user_name=user_name,
        action=action,
        details=details
    )
    db.add(activity)
    db.commit()


# ─── STATS / DASHBOARD ───
@router.get("/stats")
def get_drive_stats(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get dashboard stats for document drive — scoped to documents the user can see."""
    def _scoped():
        return _apply_access_filter(
            db.query(DriveDocument).filter(DriveDocument.is_deleted == False),
            current_user,
        )

    total = _scoped().count()
    total_size = _scoped().with_entities(func.sum(DriveDocument.file_size)).scalar() or 0
    favorites = _scoped().filter(DriveDocument.is_favorite == True).count()
    shared = _scoped().filter(
        DriveDocument.shared_with != None, DriveDocument.shared_with != '[]'
    ).count()

    # Category breakdown
    categories = _scoped().with_entities(
        DriveDocument.category, func.count(DriveDocument.id)
    ).group_by(DriveDocument.category).all()

    # File type breakdown
    type_stats = _scoped().with_entities(
        DriveDocument.file_type, func.count(DriveDocument.id), func.sum(DriveDocument.file_size)
    ).group_by(DriveDocument.file_type).all()

    # Recent activity (last 10) — limited to documents the user can see
    visible_doc_ids = _scoped().with_entities(DriveDocument.id).subquery()
    recent_activity = (
        db.query(DriveActivity)
        .filter(DriveActivity.document_id.in_(db.query(visible_doc_ids)))
        .order_by(desc(DriveActivity.created_at))
        .limit(10)
        .all()
    )
    
    return {
        "total_documents": total,
        "total_size": total_size,
        "favorites_count": favorites,
        "shared_count": shared,
        "categories": [{"name": c[0] or "Uncategorized", "count": c[1]} for c in categories],
        "type_stats": [
            {"type": t[0] or "other", "count": t[1], "size": t[2] or 0} 
            for t in type_stats
        ],
        "recent_activity": [
            {
                "id": str(a.id),
                "document_id": str(a.document_id),
                "user_name": a.user_name,
                "action": a.action,
                "details": a.details,
                "created_at": a.created_at.isoformat() if a.created_at else None
            } for a in recent_activity
        ]
    }


# ─── UPLOAD ───
@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    project_name: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),  # comma-separated
    is_confidential: bool = Form(False),
    access_level: str = Form("Private"),
    shared_with: Optional[str] = Form(None),
    folder_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Upload a document to the drive"""
    allowed_extensions = {
        '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
        '.doc', '.docx', '.xls', '.xlsx', '.csv', '.ppt', '.pptx',
        '.txt', '.log', '.json', '.xml', '.yaml', '.yml',
        '.zip', '.rar', '.7z', '.tar', '.gz',
        '.mp4', '.mp3', '.wav', '.avi', '.mov',
    }
    
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type {ext} not allowed.")

    # Mandatory metadata
    allowed_categories = {'Finance', 'Legal', 'Compliance', 'HR', 'Project', 'Other'}
    allowed_access_levels = {'Private', 'User', 'Organization'}

    if not category or category not in allowed_categories:
        raise HTTPException(
            status_code=400,
            detail="Category is required and must be one of: " + ", ".join(sorted(allowed_categories)),
        )
    if not access_level or access_level not in allowed_access_levels:
        raise HTTPException(
            status_code=400,
            detail="Access level is required and must be one of: Private, User, Organization",
        )
    if access_level == 'User':
        shared_list_check = [s.strip() for s in (shared_with or '').split(',') if s.strip()]
        if not shared_list_check:
            raise HTTPException(
                status_code=400,
                detail="At least one user must be selected when access level is 'User'.",
            )

    file_content = await file.read()

    # 50MB max
    if len(file_content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 50MB.")
    
    unique_filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(STORAGE_DIR, unique_filename)
    
    with open(file_path, "wb") as f:
        f.write(file_content)
    
    # Determine file type category
    file_type_map = {
        '.pdf': 'pdf', '.doc': 'document', '.docx': 'document',
        '.xls': 'spreadsheet', '.xlsx': 'spreadsheet', '.csv': 'spreadsheet',
        '.ppt': 'presentation', '.pptx': 'presentation',
        '.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.gif': 'image', '.webp': 'image', '.svg': 'image',
        '.mp4': 'video', '.avi': 'video', '.mov': 'video',
        '.mp3': 'audio', '.wav': 'audio',
        '.zip': 'archive', '.rar': 'archive', '.7z': 'archive', '.tar': 'archive', '.gz': 'archive',
        '.txt': 'text', '.log': 'text', '.json': 'text', '.xml': 'text', '.yaml': 'text', '.yml': 'text',
    }
    
    file_type = file_type_map.get(ext, 'other')
    
    tag_list = [t.strip() for t in tags.split(',')] if tags else []
    shared_list = [s.strip() for s in shared_with.split(',')] if shared_with else []
    status_val = "Pending Approval" if is_confidential else "Active"
    
    doc = DriveDocument(
        title=title or file.filename,
        description=description,
        file_name=file.filename,
        file_url=f"/storage/drive/{unique_filename}",
        file_type=file_type,
        file_size=len(file_content),
        mime_type=file.content_type,
        category=category,
        tags=tag_list,
        status=status_val,
        is_confidential=is_confidential,
        access_level=access_level,
        shared_with=shared_list,
        uploaded_by=current_user.id,
        department=current_user.department,
        project_id=uuid.UUID(project_id) if project_id else None,
        project_name=project_name,
    )
    
    db.add(doc)
    db.commit()
    db.refresh(doc)

    log_activity(db, doc.id, current_user.id, current_user.full_name, "uploaded", f"Uploaded {file.filename}")

    # Notifications:
    #   - Confidential upload  → notify all admins to approve (recipients see it under /admin/...).
    #   - Non-confidential + shared_with → notify each recipient now (they can see it immediately).
    #   - Confidential + shared_with → defer share notifications until admin approves.
    if is_confidential:
        for admin_id in _admin_recipient_ids(db):
            if admin_id == current_user.id:
                continue  # don't ping the uploader if they happen to be admin
            _notify(
                db, admin_id,
                type_="drive_pending_approval",
                title="Document awaiting approval",
                message=f"{current_user.full_name or 'A user'} uploaded confidential document '{doc.title}' — review required.",
                action_url=_drive_action_url(is_admin=True),
            )
    elif shared_list:
        for recipient_id_str in shared_list:
            try:
                recipient_id = uuid.UUID(recipient_id_str)
            except (ValueError, TypeError):
                continue
            _notify(
                db, recipient_id,
                type_="drive_shared",
                title="A document was shared with you",
                message=f"{current_user.full_name or 'Someone'} shared '{doc.title}' with you.",
                action_url=_drive_action_url(is_admin=False),
            )
    db.commit()

    return {
        "success": True,
        "document": _serialize_doc(doc, db)
    }


# ─── LIST ALL ───
@router.get("/documents")
def list_documents(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    file_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    favorites_only: bool = Query(False),
    shared_with_me: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """List documents with filtering, sorting, pagination"""
    query = db.query(DriveDocument).filter(DriveDocument.is_deleted == False)
    
    if status:
        query = query.filter(DriveDocument.status == status)
    else:
        query = query.filter(DriveDocument.status != "Deleted")
        
    # Access scoping (single source of truth) — see _apply_access_filter
    query = _apply_access_filter(query, current_user)

    if shared_with_me:
        query = query.filter(
            cast(DriveDocument.shared_with, String).contains(str(current_user.id))
        )
    
    if category:
        query = query.filter(DriveDocument.category == category)
    
    if file_type:
        query = query.filter(DriveDocument.file_type == file_type)
    
    if favorites_only:
        query = query.filter(DriveDocument.is_favorite == True)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                DriveDocument.title.ilike(search_term),
                DriveDocument.file_name.ilike(search_term),
                DriveDocument.description.ilike(search_term),
                DriveDocument.category.ilike(search_term),
                DriveDocument.project_name.ilike(search_term),
            )
        )
    
    total = query.count()
    
    # Sorting
    sort_column = getattr(DriveDocument, sort_by, DriveDocument.created_at)
    if sort_dir == "desc":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(sort_column)
    
    docs = query.offset((page - 1) * page_size).limit(page_size).all()
    
    return {
        "documents": [_serialize_doc(d, db) for d in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }


# ─── RECENT ───
@router.get("/recent")
def get_recent_documents(
    limit: int = Query(8, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get recent documents — scoped to what the current user can see."""
    base = db.query(DriveDocument).filter(DriveDocument.is_deleted == False)
    base = _apply_access_filter(base, current_user)
    docs = base.order_by(desc(DriveDocument.created_at)).limit(limit).all()

    return [_serialize_doc(d, db) for d in docs]


# ─── SINGLE DOC ───
@router.get("/documents/{doc_id}")
def get_document(
    doc_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get single document details (scoped: 404 if user can't see it)."""
    query = db.query(DriveDocument).filter(DriveDocument.id == doc_id)
    query = _apply_access_filter(query, current_user)
    doc = query.first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Increment view count
    doc.view_count = (doc.view_count or 0) + 1
    doc.last_accessed_at = datetime.now(timezone.utc)
    doc.last_accessed_by = current_user.id
    db.commit()

    return _serialize_doc(doc, db)


# ─── UPDATE ───
@router.put("/documents/{doc_id}")
def update_document(
    doc_id: str,
    payload: dict,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update document metadata"""
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Capture pre-update state to detect approval transitions.
    prev_status = doc.status

    updatable = ["title", "description", "category", "tags", "status", "is_favorite",
                 "is_locked", "is_confidential", "access_level", "expiry_date", "project_name"]

    changes = []
    for key in updatable:
        if key in payload:
            old_val = getattr(doc, key)
            setattr(doc, key, payload[key])
            changes.append(f"{key}: {old_val} → {payload[key]}")

    db.commit()
    db.refresh(doc)

    if changes:
        log_activity(db, doc.id, current_user.id, current_user.full_name, "updated", "; ".join(changes))

    # Approval transition: status went from "Pending Approval" → anything else (e.g. "Active").
    # Notify the uploader and each share recipient that they can now see/download the doc.
    if prev_status == "Pending Approval" and doc.status != "Pending Approval":
        if doc.uploaded_by and doc.uploaded_by != current_user.id:
            _notify(
                db, doc.uploaded_by,
                type_="drive_approved",
                title="Your document was approved",
                message=f"'{doc.title}' has been approved and is now active.",
                action_url=_drive_action_url(is_admin=False),
            )
        for recipient_id_str in (doc.shared_with or []):
            try:
                recipient_id = uuid.UUID(recipient_id_str)
            except (ValueError, TypeError):
                continue
            if recipient_id == doc.uploaded_by or recipient_id == current_user.id:
                continue
            _notify(
                db, recipient_id,
                type_="drive_shared",
                title="A document was shared with you",
                message=f"'{doc.title}' has been approved and shared with you.",
                action_url=_drive_action_url(is_admin=False),
            )
        db.commit()

    return _serialize_doc(doc, db)


# ─── TOGGLE FAVORITE ───
@router.post("/documents/{doc_id}/favorite")
def toggle_favorite(
    doc_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.is_favorite = not doc.is_favorite
    db.commit()

    action = "favorited" if doc.is_favorite else "unfavorited"
    log_activity(db, doc.id, current_user.id, current_user.full_name, action, f"Document {action}")

    return {"success": True, "is_favorite": doc.is_favorite}


# ─── SHARE DOCUMENT ───
class ShareRequest(BaseModel):
    user_ids: List[str]


@router.post("/documents/{doc_id}/share")
def share_document(
    doc_id: str,
    payload: ShareRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Add users to the document's shared_with list. Only the uploader or a superuser
    can share. Recipients are notified immediately, unless the document is still
    Pending Approval — in which case the share is recorded but no notifications fire
    until the admin approves (PUT /documents/{id} status=Active)."""
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    is_admin = getattr(current_user, 'is_superuser', False)
    if doc.uploaded_by != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Only the uploader or an admin can share this document.")

    # Validate + normalize incoming user ids
    new_ids: List[str] = []
    for raw in payload.user_ids or []:
        try:
            new_ids.append(str(uuid.UUID(raw)))
        except (ValueError, TypeError):
            continue
    if not new_ids:
        raise HTTPException(status_code=400, detail="At least one valid user_id is required.")

    existing = list(doc.shared_with or [])
    added: List[str] = []
    for nid in new_ids:
        if nid not in existing and nid != str(doc.uploaded_by):
            existing.append(nid)
            added.append(nid)
    doc.shared_with = existing
    db.commit()
    db.refresh(doc)

    # Activity log
    if added:
        log_activity(
            db, doc.id, current_user.id, current_user.full_name,
            "shared", f"Shared with {len(added)} user(s)"
        )

    # Notify newly-added recipients only if the doc is already approved.
    # Otherwise, the notification is deferred until admin approval (handled in PUT).
    if added and doc.status != "Pending Approval":
        for nid in added:
            try:
                _notify(
                    db, uuid.UUID(nid),
                    type_="drive_shared",
                    title="A document was shared with you",
                    message=f"{current_user.full_name or 'Someone'} shared '{doc.title}' with you.",
                    action_url=_drive_action_url(is_admin=False),
                )
            except (ValueError, TypeError):
                continue
        db.commit()

    return {"success": True, "shared_with": doc.shared_with, "added_count": len(added)}


# ─── SOFT DELETE (TRASH) ───
@router.delete("/documents/{doc_id}")
def soft_delete_document(
    doc_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Ownership: only the uploader or a superuser can delete. Share recipients
    # cannot delete docs that are merely shared with them.
    if doc.uploaded_by != current_user.id and not getattr(current_user, 'is_superuser', False):
        raise HTTPException(status_code=403, detail="Only the uploader or an admin can delete this document.")

    doc.is_deleted = True
    doc.deleted_at = datetime.now(timezone.utc)
    doc.deleted_by = current_user.id
    doc.status = "Deleted"
    db.commit()

    log_activity(db, doc.id, current_user.id, current_user.full_name, "deleted", "Moved to trash")

    return {"success": True}

# ─── PERMANENT DELETE ───
@router.delete("/documents/{doc_id}/permanent")
def permanent_delete_document(
    doc_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Ownership: uploader or superuser only
    if doc.uploaded_by != current_user.id and not getattr(current_user, 'is_superuser', False):
        raise HTTPException(status_code=403, detail="Access denied")

    # 1. Remove dependent activity rows (FK has no ON DELETE CASCADE)
    db.query(DriveActivity).filter(DriveActivity.document_id == doc.id).delete(synchronize_session=False)

    # 2. Best-effort: delete the physical file from /storage/drive/
    if doc.file_url:
        file_name = os.path.basename(doc.file_url)
        file_path = os.path.join(STORAGE_DIR, file_name)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass  # don't fail the request if the file is locked or already gone

    # 3. Now safe to delete the document row
    db.delete(doc)
    db.commit()

    return {"success": True}


# ─── RESTORE FROM TRASH ───
@router.post("/documents/{doc_id}/restore")
def restore_document(
    doc_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    doc.is_deleted = False
    doc.deleted_at = None
    doc.deleted_by = None
    doc.status = "Active"
    db.commit()
    
    log_activity(db, doc.id, current_user.id, current_user.full_name, "restored", "Restored from trash")
    
    return {"success": True}


# ─── TRASH LIST ───
@router.get("/trash")
def list_trash(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    base = db.query(DriveDocument).filter(DriveDocument.is_deleted == True)
    base = _apply_access_filter(base, current_user)
    docs = base.order_by(desc(DriveDocument.deleted_at)).all()

    return [_serialize_doc(d, db) for d in docs]


# ─── ACTIVITY LOG ───
@router.get("/activity")
def list_activity(
    document_id: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    # Restrict to activity rows for documents the current user can see
    visible_doc_ids = _apply_access_filter(
        db.query(DriveDocument).filter(DriveDocument.is_deleted == False),
        current_user,
    ).with_entities(DriveDocument.id).subquery()

    query = db.query(DriveActivity).filter(
        DriveActivity.document_id.in_(db.query(visible_doc_ids))
    )
    if document_id:
        query = query.filter(DriveActivity.document_id == document_id)

    activities = query.order_by(desc(DriveActivity.created_at)).limit(limit).all()
    
    return [
        {
            "id": str(a.id),
            "document_id": str(a.document_id),
            "user_name": a.user_name,
            "action": a.action,
            "details": a.details,
            "created_at": a.created_at.isoformat() if a.created_at else None
        } for a in activities
    ]


# ─── DOWNLOAD TRACKING ───
@router.post("/documents/{doc_id}/download")
def track_download(
    doc_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    is_admin = getattr(current_user, 'is_superuser', False)
    if doc.status == "Pending Approval":
        if not is_admin and current_user.id != doc.uploaded_by:
            raise HTTPException(status_code=403, detail="Document is pending approval and cannot be downloaded")
    
    doc.download_count = (doc.download_count or 0) + 1
    db.commit()
    
    log_activity(db, doc.id, current_user.id, current_user.full_name, "downloaded", f"Downloaded {doc.file_name}")
    
    return {"success": True, "file_url": doc.file_url}


# ─── FOLDERS ───
@router.get("/folders")
def list_folders(
    parent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    query = db.query(DriveFolder).filter(DriveFolder.is_deleted == False)
    if parent_id:
        query = query.filter(DriveFolder.parent_id == parent_id)
    else:
        query = query.filter(DriveFolder.parent_id == None)
    
    folders = query.order_by(DriveFolder.name).all()
    return [
        {
            "id": str(f.id),
            "name": f.name,
            "parent_id": str(f.parent_id) if f.parent_id else None,
            "color": f.color,
            "icon": f.icon,
            "created_at": f.created_at.isoformat() if f.created_at else None
        } for f in folders
    ]


@router.post("/folders")
def create_folder(
    payload: dict,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    folder = DriveFolder(
        name=payload.get("name", "New Folder"),
        parent_id=payload.get("parent_id"),
        color=payload.get("color", "#f59e0b"),
        created_by=current_user.id
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    
    return {
        "id": str(folder.id),
        "name": folder.name,
        "parent_id": str(folder.parent_id) if folder.parent_id else None,
        "color": folder.color,
    }


# ─── CATEGORIES ───
@router.get("/categories")
def list_categories(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get all unique categories used in documents"""
    cats = db.query(
        DriveDocument.category, func.count(DriveDocument.id)
    ).filter(
        DriveDocument.is_deleted == False,
        DriveDocument.category != None
    ).group_by(DriveDocument.category).all()
    
    return [{"name": c[0], "count": c[1]} for c in cats]


# ─── SERIALIZER ───
def _serialize_doc(doc: DriveDocument, db: Session = None):
    uploader_name = None
    if db and doc.uploaded_by:
        u = db.query(User).filter(User.id == doc.uploaded_by).first()
        if u:
            uploader_name = u.full_name
    
    return {
        "id": str(doc.id),
        "title": doc.title,
        "description": doc.description,
        "file_name": doc.file_name,
        "file_url": doc.file_url,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "mime_type": doc.mime_type,
        "category": doc.category,
        "tags": doc.tags or [],
        "status": doc.status,
        "is_favorite": doc.is_favorite,
        "is_locked": doc.is_locked,
        "is_confidential": doc.is_confidential,
        "version": doc.version,
        "version_number": doc.version_number,
        "uploaded_by": str(doc.uploaded_by) if doc.uploaded_by else None,
        "uploader_name": uploader_name,
        "department": doc.department,
        "project_id": str(doc.project_id) if doc.project_id else None,
        "project_name": doc.project_name,
        "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
        "access_level": doc.access_level,
        "shared_with": doc.shared_with or [],
        "download_count": doc.download_count or 0,
        "view_count": doc.view_count or 0,
        "is_deleted": doc.is_deleted,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }
