from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List
from datetime import datetime
import random
import string

from app.database import get_db
from app.models.project import Project
from app.models.user import User
from app.models.team_member import TeamMember
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate, ProjectListResponse
from app.utils.dependencies import get_current_active_user
from sqlalchemy import or_, and_, func

router = APIRouter(
    prefix="/projects",
    tags=["Projects"]
)

def generate_project_code():
    timestamp = datetime.now().strftime("%Y%m")
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"PRJ-{timestamp}-{suffix}"

@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # Auto-generate code if missing
    if not project.code:
        project.code = generate_project_code()
    
    # Check uniqueness
    existing = db.query(Project).filter(Project.code == project.code).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project code already exists"
        )
    
    # Determine initial status based on user role and requested status
    is_admin = current_user.is_superuser
    
    if is_admin:
        # Admin can create: Draft or directly Approved
        if project.status == "Draft":
            initial_status = "Draft"
            is_approved = False
        else:
            # Admin projects are auto-approved unless explicitly saved as draft
            initial_status = "Approved"
            is_approved = True
    else:
        # Regular users: Draft or Pending Approval
        initial_status = project.status if project.status in ["Draft", "Pending Approval"] else "Pending Approval"
        is_approved = False
    
    db_project = Project(
        **project.dict(exclude={'status', 'organization'}),
        organization=project.organization or current_user.organisation or 'Fourconnect',
        created_by_id=current_user.id,
        status=initial_status,
        is_approved=is_approved
    )
    
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project

@router.get("/", response_model=ProjectListResponse)
def list_projects(
    page: int = 1,
    limit: int = 10,
    start_date: str = None,
    end_date: str = None,
    status: str = None,
    owner_type: str = None,  # 'user' | 'admin' - for separating admin/user projects
    created_by_id: str = None,  # Filter by specific user ID (admin only)
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # Base query: Not deleted
    query = db.query(Project).filter(Project.is_deleted == False)

    # SECURITY: Authorization check for owner_type parameter
    # Only superusers can use 'admin' or 'user' owner_type (accessing other users' projects)
    # Regular users can only use 'self' (their own projects)
    if owner_type in ['admin', 'user'] and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin privileges required."
        )
    
    # SECURITY: created_by_id filter is admin only
    if created_by_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin privileges required."
        )

    # 1. Owner Type Filtering (admin vs user created projects)
    if owner_type == 'user':
        # Only projects created by non-superusers, exclude drafts (for admin viewing user projects)
        query = query.join(User, Project.created_by_id == User.id)
        query = query.filter(User.is_superuser == False)
        query = query.filter(Project.status != "Draft")  # No drafts in admin's user projects view
    elif owner_type == 'admin':
        # Only projects created by superusers (including their drafts)
        # STRICT VIEW: Only show Draft and Approved. Hide Pending/Rejected (as Admin projects are auto-approved)
        query = query.join(User, Project.created_by_id == User.id)
        query = query.filter(User.is_superuser == True)
        query = query.filter(Project.status.in_(["Draft", "Approved"]))
    elif owner_type == 'self':
        # User viewing their own projects (including their drafts)
        query = query.filter(Project.created_by_id == current_user.id)
    else:
        # Default View (Landing Page / Mixed View)
        if current_user.is_superuser:
            # Admin sees ALL projects (usually filtered by Status='Approved' in query)
            pass 
        else:
            # Regular User:
            # 1. Projects I created
            # 2. Projects I am a Team Member of
            
            # Subquery for projects I am a member of
            member_projects = db.query(TeamMember.project_id).filter(
                TeamMember.user_id == current_user.id
            ).scalar_subquery()

            query = query.filter(
                or_(
                    Project.created_by_id == current_user.id,
                    Project.id.in_(member_projects)
                )
            )
            
            # Hide Drafts of others (though team members usually don't see drafts unless invited?)
            # Just to be safe/consistent with "Draft Privacy"
            query = query.filter(
                or_(
                    Project.status != "Draft",
                    Project.created_by_id == current_user.id
                )
            )

    # EXCLUSION LOGIC: Don't show projects where user (Admin or Regular) has explicitly declined/been removed
    # This respects the "Declined" status even for Admins unless they override it via DB directly
    # (Actually, for Admin "All Access" view, we might want to skip this? 
    #  But request said "check also other status declined... if a user has declined... don't show user".
    #  This implied "don't show TO THE USER". Admins probably want to see the project exists.
    #  Let's keep it for Regular Users only to be safe, or if Admin specifically wants to see "Overview".
    #  Let's stick to: "If I declined it, I don't want to see it" - works for Admin too personally, 
    #  but Admin *job* is to see everything.
    #  Let's exclude only for non-superusers to ensure Admin visibility is complete.)
    
    if not current_user.is_superuser:
        excluded_subquery = db.query(TeamMember.project_id).filter(
            TeamMember.user_id == current_user.id,
            TeamMember.status.in_(['declined', 'removed'])
        ).scalar_subquery()
        query = query.filter(Project.id.notin_(excluded_subquery))

    # 1b. Filter by specific user ID (admin only)
    if created_by_id:
        query = query.filter(Project.created_by_id == created_by_id)

    # 2. Status Filtering (for per-tab pagination)
    if status and status != "All":
        # Handle 'Pending' -> 'Pending Approval' mapping if needed
        db_status = "Pending Approval" if status == "Pending" else status
        query = query.filter(Project.status == db_status)

    # 3. Date Filtering
    if start_date:
        try:
            # Parse 'YYYY-MM-DD'
            sd = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(Project.created_at >= sd)
        except ValueError:
            pass # Ignore invalid dates
    
    if end_date:
        try:
            ed = datetime.strptime(end_date, "%Y-%m-%d")
            # Set time to end of day for inclusive filtering
            ed = ed.replace(hour=23, minute=59, second=59)
            query = query.filter(Project.created_at <= ed)
        except ValueError:
            pass

    # 3. Pagination
    total_records = query.count()
    total_pages = (total_records + limit - 1) // limit
    
    # Apply offset/limit
    skip = (page - 1) * limit
    projects = query.order_by(Project.created_at.desc()).offset(skip).limit(limit).all()

    # Form response items
    items = []
    
    # Pre-fetch memberships for these projects to avoid N+1
    project_ids = [p.id for p in projects]
    membership_map = {}
    if project_ids and not current_user.is_superuser:
        user_memberships = db.query(TeamMember).filter(
            TeamMember.user_id == current_user.id,
            TeamMember.project_id.in_(project_ids)
        ).all()
        membership_map = {tm.project_id: tm.status for tm in user_memberships}

    # Import Milestone for budget calc
    from app.models.milestone import Milestone

    for project in projects:
        # Determine status
        status = 'none'
        if str(project.created_by_id) == str(current_user.id):
             status = 'owner'
        elif current_user.is_superuser:
             status = 'admin' 
        else:
             status = membership_map.get(project.id, 'none')

        # Calculate Budget Utilized (Normalized to Project Currency)
        # Note: We must fetch milestones to convert currency
        milestones = db.query(Milestone).filter(Milestone.project_id == project.id).all()
        from app.utils.currency import get_rate
        
        milestones_budget = 0.0
        project_currency = project.currency or 'USD'
        
        for m in milestones:
             # Convert milestone budget to project currency
             rate = get_rate(m.currency, project_currency)
             milestones_budget += (m.budget_amount or 0.0) * rate
             
        milestones_budget = round(milestones_budget, 2)

        # Calculate Completion %
        # Calculate Completion %
        total_ms = len(milestones)
        
        # Calculate Weighted Completion
        total_weight = sum(m.contribution_percentage or 0.0 for m in milestones)
        completed_weight = sum(m.contribution_percentage or 0.0 for m in milestones if m.status == 'completed')
        
        if total_weight > 0.1: # Use weighted if meaningful weights exist
             completion_pct = round(completed_weight, 1)
        else:
             completed_ms = sum(1 for m in milestones if m.status == 'completed')
             completion_pct = round((completed_ms / total_ms) * 100, 1) if total_ms > 0 else 0.0

        # Determine Completed At Date
        completed_at = None
        if completion_pct >= 100:
             # Find the latest updated_at or end_date from completed milestones
             completed_dates = [m.updated_at for m in milestones if m.status == 'completed' and m.updated_at]
             if completed_dates:
                 completed_at = max(completed_dates)
             else:
                 # Fallback to project updated_at if no milestone dates found
                 completed_at = project.updated_at

        project_dict = {
            **{c.name: getattr(project, c.name) for c in project.__table__.columns},
            "created_by_name": project.created_by.full_name if project.created_by else "Unknown",
            "created_by_employee_code": project.created_by.employee_code if project.created_by else None,
            "created_by_phone": project.created_by.phone if project.created_by else None,
            "created_by_address": project.created_by.address if project.created_by else None,
            "current_user_membership_status": status,
            "budget_utilized": milestones_budget,
            "completion_percentage": completion_pct,
            "completed_at": completed_at
        }
        items.append(project_dict)

    return {
        "items": items,
        "total": total_records,
        "page": page,
        "size": limit,
        "pages": total_pages
    }

@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # Ensure we don't return deleted projects unless specifically handled? 
    # For now, let's treat deleted as not found
    project = db.query(Project).options(
        joinedload(Project.created_by)
    ).filter(Project.id == project_id, Project.is_deleted == False).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # STRICT ACCESS CONTROL
    if not current_user.is_superuser:
        # Check if owner
        if str(project.created_by_id) != str(current_user.id):
            # Check if accepted team member
            member = db.query(TeamMember).filter(
                TeamMember.project_id == project.id,
                TeamMember.user_id == current_user.id
            ).first()

            has_access = False
            if member:
                if member.status == "accepted":
                    has_access = True
                elif member.status == "declined":
                    raise HTTPException(status_code=403, detail="You have declined the invitation to this project.")
                elif member.status == "removed":
                    raise HTTPException(status_code=403, detail="You have been removed from this project.")
                elif member.status == "pending":
                    raise HTTPException(status_code=403, detail="Please accept the project invitation to view details.")
            
            if not has_access:
                raise HTTPException(status_code=403, detail="You do not have access to this project.")

    team_members = db.query(TeamMember).options(
        joinedload(TeamMember.user)
    ).filter(
        TeamMember.project_id == project.id,
        TeamMember.status == "accepted"
    ).all()
    
    # Populate extra user details for team members from joined relationship
    for tm in team_members:
        user = tm.user 
        if user:
            tm.user_name = user.full_name
            tm.user_email = user.email
            tm.user_phone = user.phone
            tm.user_avatar = user.avatar_url
            tm.is_superuser = user.is_superuser
            
        # Calculate Budget Utilized (Sum of all milestone budgets)
        # We use all milestones (completed, pending, active) as these represent "Allocated" budget
        from app.utils.currency import get_rate

        total_allocated = 0
        total_consumed = 0
        project_currency = project.currency or 'USD'
        for m in project.milestones:
            # Use stored percentage for consistency (Creation Time Rate)
            val = 0.0
            if m.contribution_percentage and m.contribution_percentage > 0 and (project.budget_amount or 0) > 0:
                 from decimal import Decimal
                 # Calculate from percentage
                 val = (m.contribution_percentage / 100.0) * (project.budget_amount or 0)
            else:
                 # Fallback to Dynamic Rate
                 rate = get_rate(m.currency, project_currency)
                 val = (m.budget_amount or 0) * rate
            
            # Attach for Schema Response
            setattr(m, 'budget_amount_converted', val)
            
            total_allocated += val
            
            if m.status == 'completed':
                total_consumed += val
        
        total_allocated = round(total_allocated, 2)
        total_consumed = round(total_consumed, 2)
        
        # Calculate Completion %
        total_ms = len(project.milestones)
        completed_ms = sum(1 for m in project.milestones if m.status == 'completed')
        completion_pct = round((completed_ms / total_ms) * 100, 1) if total_ms > 0 else 0.0
            
    # Create Response Model Manually to inject computed fields
    project_resp = ProjectResponse.model_validate(project)
    project_resp.budget_utilized = total_allocated
    project_resp.budget_consumed = total_consumed
    project_resp.completion_percentage = completion_pct
    
    # Explicitly populate flattened fields not handled by from_attributes automatically
    project_resp.created_by_name = project.created_by.full_name if project.created_by else "Unknown"
    project_resp.created_by_employee_code = project.created_by.employee_code if project.created_by else None
    project_resp.created_by_phone = project.created_by.phone if project.created_by else None
    project_resp.created_by_address = project.created_by.address if project.created_by else None
    
    # Populate Team Members
    # We need to serialize them properly
    # The Pydantic model expects a list of TeamMemberResponse
    # But current logic relies on ORM relation. 
    # Let's trust from_attributes for basic fields, but team_members might need explicit handling 
    # if the nested structure is complex. 
    # Actually, ProjectResponse has `team_members: List[TeamMemberResponse]`.
    # `from_orm` (model_validate) handles lists if the ORM has the relationship.
    # However, we enriched `tm` objects in lines 310-317. 
    # So we should probably re-assign them to ensure enriched data is used.
    # But `model_validate(project)` uses `project.team_members`, which are the raw/enriched ORM objects.
    # So `project_resp.team_members` should be populated.
    
    # Inject Membership for Current User
    if current_user.is_superuser:
        project_resp.current_user_membership_status = 'admin'
    elif str(project.created_by_id) == str(current_user.id):
        project_resp.current_user_membership_status = 'owner'
    else:
        # Find membership
        my_membership = next((tm for tm in (project.team_members or []) if str(tm.user_id) == str(current_user.id)), None)
        if my_membership:
             project_resp.current_user_membership_status = my_membership.status
        else:
             project_resp.current_user_membership_status = 'none'

    # File Metadata Logic
    if project.project_order_path:
        import os
        try:
            # Check if path exists
            # We need to handle potential relative paths from 'uploads'
             # The path in DB is likely 'uploads/...' or absolute?
             # Based on debugs, it should be 'uploads/filename'.
             # os.path.getsize needs absolute path or relative to CWD.
             # CWD is .../backend. 'uploads' is in .../backend.
            file_full_path = project.project_order_path
            
            # If it's stored as relative 'uploads/...', safe to use directly if CWD is correct.
            # If stored as absolute, use as is.
            if not os.path.exists(file_full_path) and not os.path.isabs(file_full_path):
                 # Try prefixing with CWD if needed, but relative usually works
                 pass
            
            if os.path.exists(file_full_path):
                size_bytes = os.path.getsize(file_full_path)
                # Format
                for unit in ['B', 'KB', 'MB', 'GB']:
                    if size_bytes < 1024.0:
                        project_resp.file_size = f"{size_bytes:.1f} {unit}"
                        break
                    size_bytes /= 1024.0
            else:
                 project_resp.file_size = "File Missing"
        except Exception as e:
            print(f"Error calculating file size: {e}")
            project_resp.file_size = "Unknown"
            
        # Uploaded By
        project_resp.uploaded_by = project.created_by.full_name if project.created_by else "System"

    return project_resp



@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    project_update: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update a project. Admin can update any project with status changes.
    Regular users can only update their own draft projects.
    """
    project = db.query(Project).filter(Project.id == project_id, Project.is_deleted == False).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Permission checks
    is_owner = str(project.created_by_id) == str(current_user.id)
    is_admin = current_user.is_superuser
    
    # Non-admin can only edit their own drafts
    if not is_admin:
        if not is_owner:
            raise HTTPException(status_code=403, detail="You can only edit your own projects")
        if project.status not in ["Draft"]:
            raise HTTPException(status_code=403, detail="You can only edit draft projects")
    
    # Update fields
    update_data = project_update.dict(exclude_unset=True)
    
    # Handle status changes (admin only for approval/rejection)
    if "status" in update_data:
        new_status = update_data["status"]
        if new_status in ["Approved", "Rejected"] and not is_admin:
            raise HTTPException(status_code=403, detail="Only admin can approve or reject projects")
        
        # Set is_approved flag based on status
        if new_status == "Approved":
            project.is_approved = True
            
            # Auto-assign project owner as team member with "accepted" status
            existing_owner = db.query(TeamMember).filter(
                TeamMember.project_id == project.id,
                TeamMember.user_id == project.created_by_id
            ).first()
            
            if not existing_owner:
                owner_member = TeamMember(
                    project_id=project.id,
                    user_id=project.created_by_id,
                    assigned_by_id=current_user.id,
                    status="accepted",
                    role="Owner"
                )
                db.add(owner_member)
        elif new_status == "Rejected":
            project.is_approved = False
    
    for field, value in update_data.items():
        setattr(project, field, value)
    
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete a project. Admin can delete any project.
    Regular users can only delete their own draft projects.
    Soft delete is performed.
    """
    project = db.query(Project).filter(Project.id == project_id, Project.is_deleted == False).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    is_owner = str(project.created_by_id) == str(current_user.id)
    is_admin = current_user.is_superuser
    
    if not is_admin:
        if not is_owner:
            raise HTTPException(status_code=403, detail="You can only delete your own projects")
        if project.status not in ["Draft"]:
            raise HTTPException(status_code=403, detail="You can only delete draft projects")
    
    # Soft delete
    project.is_deleted = True
    db.commit()
    return None


@router.get("/archived/list")
def list_archived_projects(
    page: int = 1,
    limit: int = 20,
    search: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    List archived projects — those with 100% overall completion
    that have been completed for more than 6 months.
    """
    from app.models.milestone import Milestone
    from app.utils.currency import get_rate
    from dateutil.relativedelta import relativedelta

    six_months_ago = datetime.utcnow() - relativedelta(months=6)

    # Base query: not deleted
    query = db.query(Project).filter(Project.is_deleted == False)

    # Access control for non-admin users
    if not current_user.is_superuser:
        member_projects = db.query(TeamMember.project_id).filter(
            TeamMember.user_id == current_user.id
        ).scalar_subquery()
        query = query.filter(
            or_(
                Project.created_by_id == current_user.id,
                Project.id.in_(member_projects)
            )
        )

    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Project.name.ilike(search_term),
                Project.code.ilike(search_term)
            )
        )

    all_projects = query.order_by(Project.created_at.desc()).all()

    # Filter by completion and age
    archived_items = []
    for project in all_projects:
        milestones = db.query(Milestone).filter(Milestone.project_id == project.id).all()

        total_ms = len(milestones)
        if total_ms == 0:
            continue

        # Calculate Weighted Completion (same logic as list_projects)
        total_weight = sum(m.contribution_percentage or 0.0 for m in milestones)
        completed_weight = sum(m.contribution_percentage or 0.0 for m in milestones if m.status == 'completed')

        if total_weight > 0.1:
            completion_pct = round(completed_weight, 1)
        else:
            completed_ms = sum(1 for m in milestones if m.status == 'completed')
            completion_pct = round((completed_ms / total_ms) * 100, 1) if total_ms > 0 else 0.0

        if completion_pct < 100:
            continue

        # Find completed_at date
        completed_dates = [m.updated_at for m in milestones if m.status == 'completed' and m.updated_at]
        if completed_dates:
            completed_at = max(completed_dates)
        else:
            completed_at = project.updated_at

        if not completed_at or completed_at > six_months_ago:
            continue

        # Calculate Budget Utilized
        project_currency = project.currency or 'USD'
        milestones_budget = 0.0
        for m in milestones:
            rate = get_rate(m.currency, project_currency)
            milestones_budget += (m.budget_amount or 0.0) * rate
        milestones_budget = round(milestones_budget, 2)

        # Count team members
        team_count = db.query(func.count(TeamMember.id)).filter(
            TeamMember.project_id == project.id,
            TeamMember.status == "accepted"
        ).scalar() or 0

        project_dict = {
            **{c.name: getattr(project, c.name) for c in project.__table__.columns},
            "created_by_name": project.created_by.full_name if project.created_by else "Unknown",
            "created_by_employee_code": project.created_by.employee_code if project.created_by else None,
            "created_by_phone": project.created_by.phone if project.created_by else None,
            "created_by_address": project.created_by.address if project.created_by else None,
            "budget_utilized": milestones_budget,
            "completion_percentage": completion_pct,
            "completed_at": completed_at,
            "team_member_count": team_count,
            "milestone_count": total_ms,
            "current_user_membership_status": "admin" if current_user.is_superuser else "owner" if str(project.created_by_id) == str(current_user.id) else "member"
        }
        archived_items.append(project_dict)

    # Pagination
    total_records = len(archived_items)
    total_pages = (total_records + limit - 1) // limit if total_records > 0 else 1
    skip = (page - 1) * limit
    paginated = archived_items[skip:skip + limit]

    return {
        "items": paginated,
        "total": total_records,
        "page": page,
        "size": limit,
        "pages": total_pages
    }
