from fastapi import APIRouter, Depends, HTTPException, status
import traceback
from sqlalchemy.orm import Session, selectinload
from typing import List, Optional
from datetime import datetime, date, time
from sqlalchemy import or_, func, distinct, and_, cast, Date

from app.database import get_db
from app.models.project import Project
from app.models.user import User
from app.models.team_member import TeamMember
from app.models.notification import Notification
from app.schemas.team import (
    TeamMemberResponse, TeamAssignRequest, TeamRespondRequest,
    ApprovedProjectResponse, UserSelectResponse, PaginatedApprovedProjectResponse
)
from app.utils.dependencies import get_current_active_user

router = APIRouter(
    prefix="/team",
    tags=["Team Management"]
)


def create_notification(db: Session, user_id, type: str, title: str, message: str, 
                       project_id=None, related_user_id=None, team_member_id=None, action_url=None):
    """Helper to create a notification"""
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        related_project_id=project_id,
        related_user_id=related_user_id,
        related_team_member_id=team_member_id,
        action_url=action_url
    )
    db.add(notification)
    return notification


@router.get("/users", response_model=List[UserSelectResponse])
def get_available_users(
    exclude_admins: bool = True,
    exclude_self: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get all users available for team assignment"""
    query = db.query(User).filter(User.is_active == True)
    
    # If called from user panel, exclude admins
    if exclude_admins:
        query = query.filter(User.is_superuser == False)
    
    # Exclude current user (so users can't invite themselves)
    if exclude_self:
        query = query.filter(User.id != current_user.id)
    
    users = query.order_by(User.full_name).all()
    return users


@router.get("/owners", response_model=List[UserSelectResponse])
def get_project_owners(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get all users who are owners of at least one approved/active project"""
    # Join Project -> User where Project is approved/active and not deleted
    query = db.query(User).join(Project, Project.created_by_id == User.id).filter(
        Project.status.in_(["Approved", "Active", "active"]),
        Project.is_deleted == False
    ).distinct()
    
    users = query.order_by(User.full_name).all()
    return users


@router.get("/projects")
def get_approved_projects(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    owner_id: Optional[str] = None,
    invite_status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    try:
        with open("debug_error.log", "a") as f:
            f.write(f"\n[{datetime.now()}] Request: start={start_date} end={end_date}\n")
    except:
        pass
    try:
        with open("debug_error.log", "a") as f:
            f.write(f"\n[{datetime.now()}] Request: start={start_date} end={end_date}\n")
    except:
        pass
    """Get approved projects for team assignment with pagination"""
    query = db.query(Project).filter(
        Project.status.in_(["Approved", "Active", "active"]),
        Project.is_deleted == False
    )
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Project.name.ilike(search_term),
                Project.code.ilike(search_term)
            )
        )

    # --- Admin Filters ---
    # Filter by project timeline (start_date to end_date)
    # Find projects whose timeline overlaps with the filter date range
    # Handle null dates gracefully
    if start_date:
        # Strict Duration: Project starts ON or AFTER filter start
        query = query.filter(cast(Project.start_date, Date) >= start_date)
    
    if end_date:
        # Strict Duration: Project ends ON or BEFORE filter end
        query = query.filter(cast(Project.end_date, Date) <= end_date)
        
    if owner_id:
        query = query.filter(Project.created_by_id == owner_id)
        
    if invite_status:
        # Filter projects that have at least one team member with this status
        # We use a subquery or join to filter efficiently
        # exists() is cleaner: select projects where exists (select 1 from team_members where project_id=project.id and status=invite_status)
        query = query.filter(
            Project.team_members.any(TeamMember.status == invite_status)
        )
    
    # Non-admins can only see their own projects
    # Non-admins can only see their own projects OR projects they are invited to (excluding removed)
    if not current_user.is_superuser:
        from sqlalchemy import or_
        # Subquery to find project IDs where user is a team member AND NOT removed
        member_project_ids = db.query(TeamMember.project_id).filter(
            TeamMember.user_id == current_user.id,
            TeamMember.status != 'removed'  # Exclude removed members
        )
        
        query = query.filter(
            or_(
                Project.created_by_id == current_user.id,
                Project.id.in_(member_project_ids)
            )
        )
    
    # Calculate Total Count
    try:
        total = query.count()
    except Exception as e:
        with open("debug_error.log", "a") as f:
            f.write(f"ERROR IN COUNT: {str(e)}\n")
            traceback.print_exc(file=f)
        total = 0

    # Calculate Total Budget (Sum of filtered projects before pagination)
    from sqlalchemy import func
    # Use the same base query logic for budget total
    budget_query = db.query(func.sum(Project.budget_amount)).filter(
        Project.status.in_(["Approved", "Active", "active"]),
        Project.is_deleted == False
    )
    
    if start_date:
        budget_query = budget_query.filter(cast(Project.start_date, Date) >= start_date)
    if end_date:
        budget_query = budget_query.filter(cast(Project.end_date, Date) <= end_date)
    if owner_id:
        budget_query = budget_query.filter(Project.created_by_id == owner_id)
    if invite_status:
        budget_query = budget_query.filter(Project.team_members.any(TeamMember.status == invite_status))
        
    if not current_user.is_superuser:
        member_project_ids = db.query(TeamMember.project_id).filter(TeamMember.user_id == current_user.id)
        budget_query = budget_query.filter(
            or_(
                Project.created_by_id == current_user.id,
                Project.id.in_(member_project_ids)
            )
        )
        
    try:
        total_budget_amount = budget_query.scalar() or 0.0
    except Exception as e:
        with open("debug_error.log", "a") as f:
            f.write(f"ERROR IN BUDGET: {str(e)}\n")
            traceback.print_exc(file=f)
        total_budget_amount = 0.0

    # Calculate Unassigned Projects Count (Derived from filtered projects)
    # Using outer join where team member id is null
    try:
        unassigned_count = query.outerjoin(TeamMember).filter(TeamMember.id == None).count()
    except Exception as e:
        with open("debug_error.log", "a") as f:
            f.write(f"ERROR IN UNASSIGNED COUNT: {str(e)}\n")
            traceback.print_exc(file=f)
        unassigned_count = 0

    # Calculate Pending Invites Count (Derived from TeamMember joined with Project)
    # Note: We must replicate project filters on the TeamMember query
    pending_query = db.query(TeamMember).join(Project).filter(
        TeamMember.status == 'pending',
        Project.status.in_(["Approved", "Active", "active"]),
        Project.is_deleted == False
    )
    if not current_user.is_superuser:
        pending_query = pending_query.filter(Project.created_by_id == current_user.id)
    
    try:
        pending_count = pending_query.count()
    except Exception as e:
        with open("debug_error.log", "a") as f:
            f.write(f"ERROR IN PENDING COUNT: {str(e)}\n")
            traceback.print_exc(file=f)
        pending_count = 0
    
    # Apply Pagination with eager loading to avoid N+1 queries
    offset = (page - 1) * limit
    try:
        projects = query.options(
            selectinload(Project.team_members).selectinload(TeamMember.user),
            selectinload(Project.created_by)
        ).order_by(Project.created_at.desc()).offset(offset).limit(limit).all()
    except Exception as e:
        with open("debug_error.log", "a") as f:
            f.write(f"ERROR IN PROJECTS: {str(e)}\n")
            traceback.print_exc(file=f)
        projects = []
    
    # Add team members and creator name (now using eager-loaded data)
    items = []
    try:
        for p in projects:
            # Team members already loaded via selectinload - no additional queries
            members = p.team_members
            team_members_data = []
            for m in members:
                team_members_data.append({
                    "id": str(m.id),
                    "user_id": str(m.user_id),
                    "assigned_by_id": str(m.assigned_by_id) if m.assigned_by_id else None,
                    "user_name": m.user.full_name if m.user else None,
                    "user_name": m.user.full_name if m.user else None,
                    "user_email": m.user.email if m.user else None,
                    "user_phone": m.user.phone if m.user else None,
                    "user_avatar": m.user.avatar_url if m.user else None,
                    "status": m.status,
                    "role": m.role,
                    "decline_reason": m.decline_reason,
                    "override_reason": m.override_reason,
                    "is_superuser": m.user.is_superuser if m.user else False
                })
            
            items.append({
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "description": p.description,
                "status": p.status,
                "created_by_name": p.created_by.full_name if p.created_by else "Unknown",
                "created_by_id": p.created_by_id,
                "team_count": len(members),
                "team_members": team_members_data,
                "project_type": p.project_type,
                "organization": p.organization or "Fourconnect",
                "start_date": p.start_date,
                "end_date": p.end_date,
                "budget_amount": p.budget_amount or 0.0,
                "currency": p.currency,
            })
    except Exception as e:
        with open("debug_error.log", "a") as f:
            f.write(f"ERROR IN FORMATTING: {str(e)}\n")
            traceback.print_exc(file=f)
        # Re-raise to show 500, but now we have the log.
        raise
    
    import math
    response_data = {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if limit > 0 else 1,
        "total_budget": total_budget_amount,
        "unassigned_count": unassigned_count,
        "pending_count": pending_count
    }

    # Explicitly validate against schema to catch Pydantic errors
    try:
        return PaginatedApprovedProjectResponse(**response_data)
    except Exception as e:
        with open("debug_error.log", "a") as f:
            f.write(f"ERROR IN PYDANTIC VALIDATION: {str(e)}\n")
            traceback.print_exc(file=f)
        raise


@router.get("/{project_id}", response_model=List[TeamMemberResponse])
def get_project_team(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get team members for a project"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    members = db.query(TeamMember).filter(TeamMember.project_id == project_id).all()
    
    result = []
    for m in members:
        result.append({
            "id": m.id,
            "project_id": m.project_id,
            "user_id": m.user_id,
            "user_name": m.user.full_name if m.user else None,
            "user_name": m.user.full_name if m.user else None,
            "user_email": m.user.email if m.user else None,
            "user_phone": m.user.phone if m.user else None,
            "user_avatar": m.user.avatar_url if m.user else None,
            "assigned_by_id": m.assigned_by_id,
            "assigned_by_name": m.assigned_by.full_name if m.assigned_by else None,
            "status": m.status,
            "role": m.role,
            "assigned_at": m.assigned_at,
            "responded_at": m.responded_at,
            "decline_reason": m.decline_reason,
            "override_reason": m.override_reason
        })
    
    return result


@router.post("/{project_id}/assign", response_model=List[TeamMemberResponse])
def assign_team_members(
    project_id: str,
    request: TeamAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Assign team members to a project"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.status.in_(["Approved", "Active", "active"]),
        Project.is_deleted == False
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Approved project not found")
    
    # Check permission - owner or admin
    if not current_user.is_superuser and str(project.created_by_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You can only assign team to your own projects")
    
    # Fetch existing active team members for notification purposes
    active_team_members = db.query(TeamMember).filter(
        TeamMember.project_id == project_id,
        TeamMember.status.in_(['accepted', 'pending'])
    ).all()
    active_member_ids = [m.user_id for m in active_team_members]

    created_members = []
    
    for user_id in request.user_ids:
        # Check if user exists
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            continue
        
        # Check if already assigned
        existing = db.query(TeamMember).filter(
            TeamMember.project_id == project_id,
            TeamMember.user_id == user_id
        ).first()
        
        member = None
        is_readd = False
        
        if existing:
            if existing.status == 'removed':
                # Reactivate removed member
                existing.status = 'pending'
                existing.assigned_by_id = current_user.id
                existing.assigned_at = datetime.utcnow()
                existing.decline_reason = None # Clear previous decline reason if any
                member = existing
                is_readd = True
            else:
                # Already active or pending
                continue
        else:
            # Create team member
            member = TeamMember(
                project_id=project.id,
                user_id=user_id,
                assigned_by_id=current_user.id,
                status="pending"
            )
            db.add(member)
        
        db.flush()  # Get the ID / Persist changes
        created_members.append(member)
        
        # 1. Notify the assigned user
        create_notification(
            db,
            user_id=user_id,
            type="team_invite",
            title="Team Invitation",
            message=f"You've been {'re-' if is_readd else ''}invited to join the project '{project.name}'",
            project_id=project.id,
            related_user_id=current_user.id,
            team_member_id=member.id,
            action_url=f"/user/projects/assignteam"
        )

        # 2. Notify Project Owner (if admin is adding and owner is not the one adding)
        if project.created_by_id and str(project.created_by_id) != str(current_user.id) and str(project.created_by_id) != str(user_id):
             create_notification(
                db,
                user_id=project.created_by_id,
                type="team_update",
                title="Team Member Added",
                message=f"{current_user.full_name} added {user.full_name} to project '{project.name}'",
                project_id=project.id,
                related_user_id=current_user.id,
                team_member_id=member.id
            )

        # 3. Notify Other Team Members
        for tm in active_team_members:
            # Don't notify the user being added, the current user, or the owner (already handled)
            if str(tm.user_id) == str(user_id) or str(tm.user_id) == str(current_user.id) or (project.created_by_id and str(tm.user_id) == str(project.created_by_id)):
                continue
            
            create_notification(
                db,
                user_id=tm.user_id,
                type="team_update",
                title="New Team Member",
                message=f"{user.full_name} has been added to the team for '{project.name}'",
                project_id=project.id,
                related_user_id=current_user.id,
                team_member_id=member.id
            )

    # Notify admin if user submitted (Standard flow - keeping this)
    if not current_user.is_superuser:
        admins = db.query(User).filter(User.is_superuser == True).all()
        for admin in admins:
            create_notification(
                db,
                user_id=admin.id,
                type="team_submitted",
                title="Team Assignment Submitted",
                message=f"{current_user.full_name} submitted team for project '{project.name}'",
                project_id=project.id,
                related_user_id=current_user.id,
                action_url=f"/admin/projects/assignteam"
            )
    
    db.commit()
    
    # Return created members
    result = []
    for m in created_members:
        db.refresh(m)
        result.append({
            "id": m.id,
            "project_id": m.project_id,
            "user_id": m.user_id,
            "user_name": m.user.full_name if m.user else None,
            "user_name": m.user.full_name if m.user else None,
            "user_email": m.user.email if m.user else None,
            "user_phone": m.user.phone if m.user else None,
            "user_avatar": m.user.avatar_url if m.user else None,
            "assigned_by_id": m.assigned_by_id,
            "assigned_by_name": m.assigned_by.full_name if m.assigned_by else None,
            "status": m.status,
            "role": m.role,
            "assigned_at": m.assigned_at,
            "responded_at": m.responded_at
        })
    
    return result


@router.post("/{assignment_id}/respond")
def respond_to_invite(
    assignment_id: str,
    request: TeamRespondRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Accept or decline a team invitation"""
    member = db.query(TeamMember).filter(TeamMember.id == assignment_id).first()
    
    if not member:
        raise HTTPException(status_code=404, detail="Team assignment not found")
    
    # Only the assigned user can respond
    if str(member.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You can only respond to your own invitations")
    
    if member.status != "pending":
        raise HTTPException(status_code=400, detail="Already responded to this invitation")
    
    # Note: Users can decline any invitation, including admin ones (with reason)
    # Admins can override declined invitations later

    member.status = "accepted" if request.accept else "declined"
    member.responded_at = datetime.utcnow()
    
    # Store decline reason if provided
    if not request.accept and request.reason:
        member.decline_reason = request.reason
    
    # ----------------------------------------------------
    # Clean up the original "team_invite" notification
    # ----------------------------------------------------
    from app.models.notification import Notification
    existing_notif = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.type == "team_invite",
        Notification.related_team_member_id == member.id
    ).first()
    
    if existing_notif:
        db.delete(existing_notif)
    
    project = member.project
    
    # Build notification message with reason if declined
    if request.accept:
        notif_message = f"{current_user.full_name} accepted invitation for '{project.name}'"
    else:
        reason_text = f" Reason: {request.reason}" if request.reason else ""
        notif_message = f"{current_user.full_name} declined invitation for '{project.name}'.{reason_text}"
    
    # Notify project owner and admins
    notify_users = [member.assigned_by_id]
    admins = db.query(User).filter(User.is_superuser == True).all()
    for admin in admins:
        if str(admin.id) != str(member.assigned_by_id):
            notify_users.append(admin.id)
    
    for uid in notify_users:
        create_notification(
            db,
            user_id=uid,
            type="team_accepted" if request.accept else "team_declined",
            title="Team Response",
            message=notif_message,
            project_id=project.id,
            related_user_id=current_user.id,
            team_member_id=member.id
        )
    
    db.commit()
    
    return {"message": f"Invitation {'accepted' if request.accept else 'declined'}"}


@router.post("/{assignment_id}/override")
def admin_override_decline(
    assignment_id: str,
    reason: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Admin override - force accept a declined invitation"""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    member = db.query(TeamMember).filter(TeamMember.id == assignment_id).first()
    
    if not member:
        raise HTTPException(status_code=404, detail="Team assignment not found")
    
    if member.status != "declined":
        raise HTTPException(status_code=400, detail="Can only override declined invitations")
    
    member.status = "accepted"
    member.responded_at = datetime.utcnow()
    member.override_reason = reason if reason else None
    
    project = member.project
    reason_text = f" Reason: {reason}" if reason else ""
    
    # Notify the invited user that admin overrode their decline
    create_notification(
        db,
        user_id=member.user_id,
        type="admin_override",
        title="Admin Override",
        message=f"Admin has added you to project '{project.name}'.{reason_text}",
        project_id=project.id,
        related_user_id=current_user.id,
        team_member_id=member.id
    )
    
    # Notify the project owner about the override
    if project.created_by_id and str(project.created_by_id) != str(current_user.id) and str(project.created_by_id) != str(member.user_id):
        create_notification(
            db,
            user_id=project.created_by_id,
            type="admin_override",
            title="Admin Override",
            message=f"Admin overrode {member.user.full_name}'s decline for project '{project.name}'.{reason_text}",
            project_id=project.id,
            related_user_id=current_user.id,
            team_member_id=member.id
        )
    
    db.commit()
    
    return {"message": "Override successful - member added to team"}


@router.post("/{assignment_id}/override-pending")
def admin_override_pending(
    assignment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Admin override - force accept a pending invitation"""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    member = db.query(TeamMember).filter(TeamMember.id == assignment_id).first()
    
    if not member:
        raise HTTPException(status_code=404, detail="Team assignment not found")
    
    if member.status != "pending":
        raise HTTPException(status_code=400, detail="Can only override pending invitations")
    
    member.status = "accepted"
    member.responded_at = datetime.utcnow()
    
    project = member.project
    
    # Notify the pending user that admin overrode
    create_notification(
        db,
        user_id=member.user_id,
        type="admin_override",
        title="Admin Override",
        message=f"Admin has confirmed your assignment to project '{project.name}'",
        project_id=project.id,
        related_user_id=current_user.id,
        team_member_id=member.id
    )
    
    # Notify the project owner about the admin override
    if project.created_by_id and str(project.created_by_id) != str(current_user.id):
        create_notification(
            db,
            user_id=project.created_by_id,
            type="admin_override",
            title="Team Member Status Updated",
            message=f"Admin overrode pending status for {member.user.full_name} on project '{project.name}'",
            project_id=project.id,
            related_user_id=current_user.id,
            team_member_id=member.id
        )
    
    db.commit()
    
    return {"message": "Override successful - pending member accepted"}


@router.delete("/{assignment_id}/remove")
def admin_remove_team_member(
    assignment_id: str,
    reason: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Admin removes a team member from a project with reason"""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    member = db.query(TeamMember).filter(TeamMember.id == assignment_id).first()
    
    if not member:
        raise HTTPException(status_code=404, detail="Team assignment not found")
    
    # Prevent removal of project owner
    if member.role == "Owner":
        raise HTTPException(status_code=400, detail="Cannot remove the project owner")
    
    project = member.project
    removed_user = member.user
    removed_user_id = member.user_id
    removed_user_name = removed_user.full_name if removed_user else 'Unknown'
    member_id = member.id
    
    # Soft-delete: instead of removing the record, mark status as 'removed'
    # This allows filtering by "Removed" status in admin panel
    member.status = "removed"
    
    # We still clear notification references to "detach" old notifications from this now-invalid membership scope
    # (Optional, but keeps consistency with previous behavior)
    db.query(Notification).filter(
        Notification.related_team_member_id == member_id
    ).update({Notification.related_team_member_id: None})
    
    # db.delete(member)  <-- Removed
    db.flush()  # Ensure update is processed
    
    # Notify the removed user (no team_member_id since it's deleted)
    create_notification(
        db,
        user_id=removed_user_id,
        type="team_removed",
        title="Removed from Team",
        message=f"Admin has removed you from project '{project.name}'. Reason: {reason or 'No reason provided'}",
        project_id=project.id,
        related_user_id=current_user.id
    )
    
    # Notify the project owner (if different from admin and removed user)
    if project.created_by_id and str(project.created_by_id) != str(current_user.id) and str(project.created_by_id) != str(removed_user_id):
        create_notification(
            db,
            user_id=project.created_by_id,
            type="team_removed",
            title="Team Member Removed",
            message=f"Admin removed {removed_user_name} from your project '{project.name}'. Reason: {reason or 'No reason provided'}",
            project_id=project.id,
            related_user_id=current_user.id
        )
    
    db.commit()
    
    return {"message": f"Team member removed successfully"}
