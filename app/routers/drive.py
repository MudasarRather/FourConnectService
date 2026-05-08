from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, and_, func, cast, String
from app.database import get_db
from app.utils.dependencies import get_current_active_user
from app.models.user import User
from app.models.drive_document import DriveDocument, DriveActivity, DriveFolder
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


# ─── HELPER ───
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
    """Get dashboard stats for document drive"""
    base = db.query(DriveDocument).filter(DriveDocument.is_deleted == False)
    
    total = base.count()
    total_size = db.query(func.sum(DriveDocument.file_size)).filter(DriveDocument.is_deleted == False).scalar() or 0
    favorites = base.filter(DriveDocument.is_favorite == True).count()
    shared = base.filter(DriveDocument.shared_with != None, DriveDocument.shared_with != '[]').count()
    
    # Category breakdown
    categories = db.query(
        DriveDocument.category, func.count(DriveDocument.id)
    ).filter(DriveDocument.is_deleted == False).group_by(DriveDocument.category).all()
    
    # File type breakdown
    type_stats = db.query(
        DriveDocument.file_type, func.count(DriveDocument.id), func.sum(DriveDocument.file_size)
    ).filter(DriveDocument.is_deleted == False).group_by(DriveDocument.file_type).all()
    
    # Recent activity (last 10)
    recent_activity = db.query(DriveActivity).order_by(desc(DriveActivity.created_at)).limit(10).all()
    
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
        
    # Access Control Logic
    # 1. Admins can see everything
    # 2. Uploaders can see their own documents
    # 3. Organization level docs can be seen by everyone
    # 4. Department level docs can be seen by same department
    # 5. Docs shared explicitly with the user
    
    is_admin = getattr(current_user, 'is_superuser', False)
    
    if not is_admin:
        query = query.filter(
            or_(
                DriveDocument.uploaded_by == current_user.id,
                DriveDocument.access_level == 'Organization',
                cast(DriveDocument.shared_with, String).contains(str(current_user.id)),
                # Assuming current_user.department exists
                and_(DriveDocument.access_level == 'Department', DriveDocument.department == current_user.department)
            )
        )
        
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
    """Get recent documents"""
    docs = db.query(DriveDocument).filter(
        DriveDocument.is_deleted == False
    ).order_by(desc(DriveDocument.created_at)).limit(limit).all()
    
    return [_serialize_doc(d, db) for d in docs]


# ─── SINGLE DOC ───
@router.get("/documents/{doc_id}")
def get_document(
    doc_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get single document details"""
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
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
    db: Session = Depends(get_db)
):
    doc = db.query(DriveDocument).filter(DriveDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Optional: Delete file from disk
    # file_path = os.path.join(STORAGE_DIR, doc.file_url.split('/')[-1])
    # if os.path.exists(file_path): os.remove(file_path)
    
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
    docs = db.query(DriveDocument).filter(
        DriveDocument.is_deleted == True
    ).order_by(desc(DriveDocument.deleted_at)).all()
    
    return [_serialize_doc(d, db) for d in docs]


# ─── ACTIVITY LOG ───
@router.get("/activity")
def list_activity(
    document_id: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    query = db.query(DriveActivity)
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
