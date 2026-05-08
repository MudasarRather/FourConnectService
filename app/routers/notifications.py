from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.user import User
from app.models.notification import Notification
from app.schemas.team import NotificationResponse, NotificationListResponse, UnreadCountResponse
from app.utils.dependencies import get_current_active_user

router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"]
)


@router.get("/", response_model=NotificationListResponse)
def get_notifications(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get user's notifications"""
    notifications = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_dismissed == False
    ).order_by(Notification.created_at.desc()).limit(limit).all()
    
    unread_count = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False,
        Notification.is_dismissed == False
    ).count()
    
    # Build response with related names
    items = []
    for n in notifications:
        item = {
            "id": n.id,
            "user_id": n.user_id,
            "type": n.type,
            "title": n.title,
            "message": n.message,
            "related_project_id": n.related_project_id,
            "related_project_name": n.related_project.name if n.related_project else None,
            "related_user_id": n.related_user_id,
            "related_user_name": n.related_user.full_name if n.related_user else None,
            "related_team_member_id": n.related_team_member_id,
            "is_read": n.is_read,
            "is_dismissed": n.is_dismissed,
            "action_url": n.action_url,
            "created_at": n.created_at,
            "is_sender_admin": n.related_user.is_superuser if n.related_user else False
        }
        items.append(item)
    
    return {"items": items, "unread_count": unread_count}


@router.get("/unread-count", response_model=UnreadCountResponse)
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get count of unread notifications (for badge)"""
    count = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False,
        Notification.is_dismissed == False
    ).count()
    
    return {"count": count}


@router.put("/{notification_id}/read")
def mark_as_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Mark a notification as read"""
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    notification.is_read = True
    db.commit()
    
    return {"message": "Marked as read"}


@router.put("/read-all")
def mark_all_as_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Mark all notifications as read"""
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).update({"is_read": True})
    
    db.commit()
    
    return {"message": "All marked as read"}


@router.delete("/{notification_id}")
def dismiss_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Dismiss (hide) a notification"""
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    notification.is_dismissed = True
    db.commit()
    
    return {"message": "Notification dismissed"}
