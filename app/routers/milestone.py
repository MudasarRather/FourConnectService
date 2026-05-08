from fastapi import APIRouter, Depends, HTTPException, status, Form, File, UploadFile
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import List, Optional, Any
from uuid import UUID
import uuid
import json
import urllib.request
import os
from datetime import datetime, timezone, timezone

from app.database import get_db
from app.models.user import User
from app.models.project import Project
from app.models.milestone import Milestone, MilestoneStatus
from app.models.team_member import TeamMember, TeamMemberStatus
from app.models.milestone_assignment import MilestoneAssignment, AssignmentStatus
from app.models.milestone_task import MilestoneTask
from app.models.notification import Notification # Added for global access
from app.schemas.milestone import MilestoneCreate, MilestoneUpdate, MilestoneResponse, MilestoneDecline, MilestoneDelete
from app.models.audit_log import AuditLog
from app.utils.dependencies import get_current_active_user
from app.utils.currency import get_rate

# Helper for logging
def log_audit(db, user_id, action, entity_id, details=None):
    try:
        log = AuditLog(
            user_id=user_id,
            action=action,
            entity_type="milestone",
            entity_id=entity_id,
            details=details
        )
        db.add(log)
    except Exception:
        pass


router = APIRouter(
    tags=["milestones"]
)
print("DEBUG: MILESTONE ROUTER LOADED", flush=True)

@router.get("/test-debug")
def test_debug():
    import logging
    import traceback
    logger = logging.getLogger("uvicorn")
    try:
        import sys
        print("DEBUG: HIT /test-debug endpoint", flush=True)
        logger.warning("DEBUG: HIT /test-debug endpoint (LOGGER)")
        return {"status": "ok", "message": "Router is mounted correctly", "python": sys.version}
    except Exception as e:
        logger.error(f"CRASH IN TEST-DEBUG: {e}")
        traceback.print_exc()
        return {"status": "error", "message": str(e), "trace": traceback.format_exc()}

@router.get("/milestones/{milestone_id}", response_model=MilestoneResponse)
def get_milestone_details(
    milestone_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get details of a specific milestone"""
    milestone = db.query(Milestone).options(
        joinedload(Milestone.created_by),
        joinedload(Milestone.last_updated_by),
        joinedload(Milestone.assignments).joinedload(MilestoneAssignment.user),
        joinedload(Milestone.tasks).joinedload(MilestoneTask.completed_by),
        joinedload(Milestone.project)
    ).filter(Milestone.id == milestone_id).first()

    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")

    # Verify Access (User must be part of the project)
    check_project_access(milestone.project_id, current_user, db)

    # Enrich Response (Similar to list logic)
    m_resp = MilestoneResponse.model_validate(milestone)
    m_resp.project_name = milestone.project.name if milestone.project else "Unknown"

    # Contribution % (Same logic as active_milestones)
    if milestone.tasks and len(milestone.tasks) > 0:
        completed_count = sum(1 for t in milestone.tasks if t.is_completed)
        m_resp.contribution_percentage = round((completed_count / len(milestone.tasks)) * 100, 1)
    else:
         m_resp.contribution_percentage = 0.0

    # Currency Conversion (for budget_amount_converted field in schema)
    proj_currency = milestone.project.currency if milestone.project and milestone.project.currency else 'USD'
    ms_currency = milestone.currency or 'USD'
    rate = get_rate(ms_currency, proj_currency)
    val = (milestone.budget_amount or 0.0) * rate
    m_resp.budget_amount_converted = float(val)

    m_resp.uploaded_by = milestone.created_by.full_name if milestone.created_by else "System"

    return m_resp

def check_project_access(project_id: UUID, user: User, db: Session, require_write: bool = False):
    """
    Check if user has access to the project.
    Returns: The Project object if access allowed.
    Raises: HTTPException 403/404.
    """
    project = db.query(Project).filter(Project.id == project_id, Project.is_deleted == False).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 1. Admin Access
    if user.is_superuser:
        return project

    # 2. Project Owner Access
    if project.created_by_id == user.id:
        return project

    # 3. Team Member Access
    member = db.query(TeamMember).filter(
        TeamMember.project_id == project_id,
        TeamMember.user_id == user.id
    ).first()

    if member:
        if member.status == TeamMemberStatus.ACCEPTED:
            return project
        elif member.status == TeamMemberStatus.DECLINED:
             raise HTTPException(status_code=403, detail="You have declined the invitation to this project.")
        elif member.status == TeamMemberStatus.REMOVED:
             raise HTTPException(status_code=403, detail="You have been removed from this project.")
        elif member.status == TeamMemberStatus.PENDING:
             raise HTTPException(status_code=403, detail="Please accept the project invitation to view details.")

    # No relationship
    raise HTTPException(status_code=403, detail="You do not have access to this project.")


@router.get("/projects/{project_id}/milestones")
def get_project_milestones(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get all milestones for a specific project"""
    try:
        # Verify access
        check_project_access(project_id, current_user, db)
        
        # NOTE: Removed 'Self-Healing' SQL block that was incorrectly forcing status flips
        # and ignoring 'declined' states. The Python logic in mutation endpoints is now
        # the source of truth for status transitions.
        
        milestones = db.query(Milestone).options(
            joinedload(Milestone.created_by),
            joinedload(Milestone.last_updated_by),
            joinedload(Milestone.assignments).joinedload(MilestoneAssignment.user),
            joinedload(Milestone.tasks).joinedload(MilestoneTask.completed_by),
            joinedload(Milestone.project) # Added eager load
        ).filter(Milestone.project_id == project_id).all()

        # Self-Healing: Update status if due date passed
        from datetime import date
        today = date.today()
        needs_commit = False
        for m in milestones:
            if m.status not in ["completed", "expired"] and m.due_date and m.due_date < today:
                m.status = "expired"
                db.add(m)
                needs_commit = True
        
        if needs_commit:
            db.commit()

        # Filter milestones based on assignments (hide if declined 2+ times)
        filtered_milestones = [
            m for m in milestones 
            if not any(
                str(a.user_id) == str(current_user.id) and (getattr(a, 'decline_count', 0) or 0) >= 2 and a.status != 'in_progress'
                for a in m.assignments
            )
        ]

        # Enrich with computed fields (Contribution & Project Budget)
        # Bypassing Pydantic response_model to guarantee field inclusion
        results = []
        for m in filtered_milestones:
            # Use Pydantic to dump base structure
            m_dict = MilestoneResponse.model_validate(m).model_dump()
            
            m_dict['project_name'] = m.project.name if m.project else "Unknown Project"
            
            # Populate project budget
            # project_budget = m.project.budget_amount if m.project else 0.0 # m.project needs lazy load if not in query? 
            # Note: m.project is not eager loaded in lines 95-100? It is not.
            # However, Milestone.project relationship might trigger lazy load.
            # To be safe and avoid N+1 or lazy load failure if detached:
            # We should probably load it or rely on what we have.
            # Actually, `m.project.name` at line 131 assumes it works.
            
            # Use columns directly from DB (already populated by recent fix)
            m_dict['contribution_percentage'] = float(m.contribution_percentage or 0.0)
            
            # Currency Conversion
            proj_currency = m.project.currency if m.project and m.project.currency else 'USD'
            ms_currency = m.currency or 'USD'
            rate = get_rate(ms_currency, proj_currency)
            val = (m.budget_amount or 0.0) * rate
            m_dict['budget_amount_converted'] = float(val)
            
            # File Metadata Logic
            if m.file_path:
                import os
                try:
                    file_full_path = m.file_path
                    if os.path.exists(file_full_path):
                        size_bytes = os.path.getsize(file_full_path)
                        for unit in ['B', 'KB', 'MB', 'GB']:
                            if size_bytes < 1024.0:
                                m_dict['file_size'] = f"{size_bytes:.1f} {unit}"
                                break
                            size_bytes /= 1024.0
                    else:
                        m_dict['file_size'] = "File Missing"
                except Exception:
                    m_dict['file_size'] = "Unknown"
            
            m_dict['uploaded_by'] = m.created_by.full_name if m.created_by else "System"
            
            results.append(m_dict)
            
        return results
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")



@router.get("/milestones/active", response_model=List[MilestoneResponse])
def get_my_active_milestones(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get ALL active (in_progress) milestones for the current user (Global Console)"""
    # Logic: Milestones created by me that are in progress
    # We also join Project to get the name
    milestones = db.query(Milestone).join(Project).options(
        joinedload(Milestone.created_by),
        joinedload(Milestone.tasks).joinedload(MilestoneTask.completed_by),
        joinedload(Milestone.project), # Eager load project
        joinedload(Milestone.assignments).joinedload(MilestoneAssignment.user) # Eager load assignments
    ).filter(
        or_(
            Milestone.created_by_id == current_user.id,
            Milestone.assignments.any(MilestoneAssignment.user_id == current_user.id)
        ),
        Milestone.status == 'in_progress',
        Project.is_deleted == False
    ).all()
    
    # Enrich with project_name manually if schema doesn't auto-map relation to flat field
    results = []
    for m in milestones:
        # Create Pydantic model first to ensure we have a mutable container separate from ORM
        try:
             m_resp = MilestoneResponse.model_validate(m)
        except Exception as e:
             print(f"DEBUG: Pydantic Validation Error for Milestone {m.id}: {e}")
             continue

        m_resp.project_name = m.project.name if m.project else "Unknown Project"
        
        # Calculate Progress based on Completed Tasks count
        if m.tasks and len(m.tasks) > 0:
            completed_count = sum(1 for t in m.tasks if t.is_completed)
            m_resp.contribution_percentage = round((completed_count / len(m.tasks)) * 100, 1)
        else:
            m_resp.contribution_percentage = 0.0
            
        # Currency Conversion (CRITICAL FIX for Schema Validation)
        proj_currency = m.project.currency if m.project and m.project.currency else 'USD'
        ms_currency = m.currency or 'USD'
        rate = get_rate(ms_currency, proj_currency)
        
        # 1. Converted Budget
        val = (m.budget_amount or 0.0) * rate
        m_resp.budget_amount_converted = float(val)
        
        # 2. Project Budget (Context)
        m_resp.project_budget_amount = float(m.project.budget_amount or 0.0)

        # File Metadata Logic
        if m.file_path:
            import os
            try:
                file_full_path = m.file_path
                if os.path.exists(file_full_path):
                    size_bytes = os.path.getsize(file_full_path)
                    for unit in ['B', 'KB', 'MB', 'GB']:
                        if size_bytes < 1024.0:
                            m_resp.file_size = f"{size_bytes:.1f} {unit}"
                            break
                        size_bytes /= 1024.0
                else:
                    m_resp.file_size = "File Missing"
            except Exception:
                m_resp.file_size = "Unknown"
        
        m_resp.uploaded_by = m.created_by.full_name if m.created_by else "System"
        
        results.append(m_resp)

    return results





@router.post("/projects/{project_id}/milestones", response_model=MilestoneResponse)
async def create_milestone(
    project_id: UUID, 
    name: str = Form(...),
    # description: str = Form(None), # Removed
    due_date: str = Form(...),
    start_date: Optional[str] = Form(None),
    priority: str = Form(...),
    milestone_type: str = Form(...), # added
    estimated_hours: float = Form(...),
    budget_amount: float = Form(...),
    currency: str = Form(...),
    file: Optional[UploadFile] = File(None),
    # New: Users assigned
    assigned_to_ids: Optional[str] = Form(None), # JSON list of UUIDs
    assigned_to_id: Optional[str] = Form(None), # Single ID fallback
    tasks: Optional[str] = Form(None), # JSON list of tasks
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a new milestone for a project with optional file attachment"""
    try:
        # Verify access (write)
        project = check_project_access(project_id, current_user, db, require_write=True)
        
        # 0. Check Project Completion Status
        # Requirement: "Once overall project is completed... no one will be able to create milestone"
        # We define "Completed" as Progress >= 100% (Weighted by Budget)
        # Note: We reuse the same weighted logic as the frontend to be consistent.
        if not current_user.is_superuser:
            # Check 100% Completion (Based on Milestone Count)
            all_milestones = db.query(Milestone).filter(Milestone.project_id == project_id).all()
            total_ms = len(all_milestones)
            if total_ms > 0:
                completed_ms = sum(1 for m in all_milestones if m.status == 'completed')
                # Strict 100% check
                if completed_ms == total_ms:
                    raise HTTPException(status_code=403, detail="Project is 100% Completed. New milestones cannot be created.")

        # 0.5 Check Project Expiry
        from datetime import date
        from app.models.system_setting import SystemSetting
        
        if project.end_date:
            p_end = project.end_date
            # Robust conversion: If datetime, convert to date. If date, keep as is.
            if isinstance(p_end, datetime):
                p_end = p_end.date()
                
            if p_end < date.today():
                # Check System Setting
                allow_edit_setting = db.query(SystemSetting).filter(SystemSetting.key == "allow_admin_edit_expired_project").first()
                is_allowed = allow_edit_setting and allow_edit_setting.value.lower() == 'true'
                
                if current_user.is_superuser:
                    pass # Admins are always allowed to bypass expiry
                else:
                     raise HTTPException(status_code=403, detail="Project Timeline Expired. You cannot create milestones for an expired project.")
        
        # BUDGET ENFORCEMENT
        # Rule: Total milestone budget cannot exceed Project Budget.
        # Exception: Admins can override.
        if not current_user.is_superuser:
            # 1. Calculate usage normalized to PROJECT currency
            project_currency = project.currency or 'USD'
            project_budget_limit = project.budget_amount or 0.0
            


            # Calculation
            try:
                current_milestones = db.query(Milestone).filter(Milestone.project_id == project_id).all()
                total_normalized_usage = 0.0
                
                for m in current_milestones:
                    r = get_rate(m.currency, project_currency)
                    total_normalized_usage += (m.budget_amount or 0.0) * r
                
                # Add new request normalized
                req_rate = get_rate(currency, project_currency)
                new_normalized_amount = budget_amount * req_rate
                
                if (total_normalized_usage + new_normalized_amount) > project_budget_limit:
                    remaining = max(0.0, project_budget_limit - total_normalized_usage)
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot create milestone: Budget limit crossed. Remaining budget: {project_currency} {remaining:,.2f}. (Converted request: {project_currency} {new_normalized_amount:,.2f})"
                    )
            except HTTPException:
                raise # Re-raise valid HTTP exceptions
            except Exception as valid_e:
                print(f"Budget Validation Logic Error: {valid_e}")
                # We could choose to block or allow. Let's block with a 500 but cleaner.
                # Actually, better to log and ALLOW if it's a code bug, OR fail safe.
                # Given 'Enforcement', let's re-raise as 500 to see it, but now with logging.
                raise HTTPException(status_code=500, detail=f"Budget Logic Error: {str(valid_e)}")

        # ... file upload logic ...
        file_path = None
        if file:
            # Validate PDF
            header = await file.read(4)
            if header != b'%PDF':
                await file.seek(0)
                raise HTTPException(status_code=400, detail="Invalid PDF file (Content mismatch).")
            await file.seek(0)

            import shutil
            upload_dir = "uploads/milestones"
            os.makedirs(upload_dir, exist_ok=True)
            
            # Generate unique filename
            str_uuid = str(uuid.uuid4())
            filename = f"{str_uuid}_{file.filename}"
            file_path = f"{upload_dir}/{filename}"
            
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

        # Assignment Logic (unchanged)
        target_user_ids = []
        if assigned_to_ids:
            try:
                parsed = json.loads(assigned_to_ids)
                if isinstance(parsed, list):
                    target_user_ids = [UUID(uid) for uid in parsed]
            except:
                pass
        if not target_user_ids and assigned_to_id:
             target_user_ids.append(UUID(assigned_to_id))
        if not target_user_ids:
            target_user_ids.append(current_user.id)
        target_user_ids = list(set(target_user_ids))

        # Milestone Status calculation
        milestone_status = "in_progress"
        
        # Check Project Expiry (Reuse logic or re-calc)
        is_project_expired = False
        if project.end_date:
            p_end_chk = project.end_date
            if isinstance(p_end_chk, datetime): p_end_chk = p_end_chk.date()
            if p_end_chk < date.today():
                is_project_expired = True

        # Rule: If Admin creates on Expired Project -> Auto Approve (Force In Progress)
        if current_user.is_superuser and is_project_expired:
            milestone_status = "in_progress"
        else:
            # Standard Logic
            for uid in target_user_ids:
                if str(uid) != str(current_user.id):
                    milestone_status = "pending"
                    break

        # Process TASKS (Calculate Hours)
        parsed_tasks = []
        final_hours = estimated_hours
        
        if tasks:
            try:
                task_list = json.loads(tasks)
                if isinstance(task_list, list):
                    total_mins = 0
                    for t in task_list:
                        # Validate
                        if t.get('name'):
                            parsed_tasks.append(t)
                            total_mins += int(t.get('estimated_minutes', 0))
                    
                    if parsed_tasks:
                        # Auto-calculate hours from tasks
                        final_hours = round(total_mins / 60, 2)
            except Exception as e:
                print(f"Error parsing tasks: {e}")

        # Calculate Contribution Percentage
        contribution_percentage = 0.0
        if project.budget_amount and project.budget_amount > 0:
            # Currency Conversion
            proj_currency = project.currency or 'USD'
            rate = get_rate(currency, proj_currency)
            converted_amount = budget_amount * rate
            
            if project.budget_amount and project.budget_amount > 0:
                contribution_percentage = (converted_amount / project.budget_amount) * 100
            else:
                contribution_percentage = 0.0

        # Create Object
        new_milestone = Milestone(
            project_id=project_id,
            name=name,
            # description=description,  <-- Removed
            due_date=datetime.strptime(due_date, "%Y-%m-%d").date(),
            start_date=datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None,
            priority=priority,
            milestone_type=milestone_type,
            estimated_hours=final_hours,
            budget_amount=budget_amount,
            currency=currency,
            contribution_percentage=contribution_percentage, # Added
            created_by_id=current_user.id,
            file_path=file_path,
            status=milestone_status
        )
        
        db.add(new_milestone)
        db.flush() 
        
        # Create Tasks
        for t in parsed_tasks:
            new_task = MilestoneTask(
                milestone_id=new_milestone.id,
                name=t.get('name'),
                estimated_minutes=int(t.get('estimated_minutes', 0)),
                # weightage removed
            )
            db.add(new_task)

        # Create Assignments (unchanged)
        for uid in target_user_ids:
            # check admin
            target_user = db.query(User).filter(User.id == uid).first()
            if target_user and target_user.is_superuser and target_user.id != current_user.id:
                 raise HTTPException(status_code=403, detail="You cannot assign an Admin to a milestone.")

            initial_status = AssignmentStatus.PENDING
            if str(uid) == str(current_user.id):
                initial_status = AssignmentStatus.IN_PROGRESS
            elif current_user.is_superuser and is_project_expired:
                # Auto-Approve assignments by Admin on Expired Projects
                initial_status = AssignmentStatus.IN_PROGRESS
            
            assignment = MilestoneAssignment(
                milestone_id=new_milestone.id, user_id=uid, status=initial_status
            )
            db.add(assignment)
             # Notify (unchanged)
            if str(uid) != str(current_user.id):
                from app.models.notification import Notification 
                db.add(Notification(
                    user_id=uid,
                    type="milestone_assigned",
                    title="New Milestone Assigned",
                    message=f"You have been assigned to milestone: {name}",
                    related_project_id=project_id,
                    action_url=f"/user/projects/projectdetails/{project_id}"
                ))

        # Log Creation
        assignee_count = len(target_user_ids)
        task_count = len(parsed_tasks)
        log_audit(db, current_user.id, "milestone_created", new_milestone.id, f"Created milestone: {name}. Tasks: {task_count}. Assigned to {assignee_count} users.")

        # Notify Admins
        notify_admins(db, "New Milestone Created", f"User {current_user.full_name} created milestone '{name}' in project '{project.name}'.", project.id, current_user.id)

        db.commit()
        db.refresh(new_milestone)
        return new_milestone

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"ERROR CREATING MILESTONE: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")


def notify_admins(db: Session, title: str, message: str, project_id: UUID, current_user_id: UUID):
    try:
        from app.models.user import User
        from app.models.notification import Notification
        
        admins = db.query(User).filter(User.is_superuser == True).all()
        for admin in admins:
            # Notify all admins, even the creator if they are admin (as per "notification for everything")
            # But usually we exclude self. Let's exclude self to avoid duplication if admin created it.
            if str(admin.id) != str(current_user_id):
                db.add(Notification(
                    user_id=admin.id,
                    type="admin_notice",
                    title=title,
                    message=message,
                    related_project_id=project_id,
                    action_url=f"/admin/projects/details" # General admin link
                ))
    except Exception as e:
        print(f"Error notifying admins: {e}")


@router.patch("/milestones/{milestone_id}", response_model=MilestoneResponse)
async def update_milestone(
    milestone_id: UUID,
    name: str = Form(None),
    description: str = Form(None),
    due_date: str = Form(None),
    start_date: str = Form(None),
    priority: str = Form(None),
    milestone_type: str = Form(None),
    estimated_hours: float = Form(None),
    budget_amount: float = Form(None),
    currency: str = Form(None),
    assigned_to_ids: str = Form(None), # JSON list
    assigned_to_id: str = Form(None), # Fallback
    tasks: str = Form(None), # JSON list of tasks
    status_val: str = Form(None, alias="status"), 
    # Tracker Fields
    actual_start_date: str = Form(None),
    actual_end_date: str = Form(None),
    delay_reason: str = Form(None),
    remarks: str = Form(None),
    file: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update a milestone (Supports File Upload)"""
    import logging
    logger = logging.getLogger("uvicorn")
    logger.warning(f"DEBUG: HIT REAL update_milestone for {milestone_id}")
    logger.warning(f"DEBUG: assigned_to_ids RAW: {assigned_to_ids}")
    
    try:
        milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
        if not milestone:
            raise HTTPException(status_code=404, detail="Milestone not found")
        
        # Check access to the PARENT project
        project = check_project_access(milestone.project_id, current_user, db, require_write=True)
        
        # PERMISSIONS
        is_admin = current_user.is_superuser
        is_creator = (milestone.created_by_id == current_user.id)
        
        # Check if user is an assignee
        is_assignee = False
        assignment = db.query(MilestoneAssignment).filter(
            MilestoneAssignment.milestone_id == milestone.id,
            MilestoneAssignment.user_id == current_user.id
        ).first()
        
        # STRICT: Only In Progress or Completed can edit. Pending MUST accept first.
        if assignment and assignment.status in [AssignmentStatus.IN_PROGRESS, AssignmentStatus.COMPLETED]:
            is_assignee = True

        if not (is_admin or is_creator):
            if not is_assignee:
                status_msg = assignment.status if assignment else "None"
                # Differentiate error message for clarity
                if assignment and assignment.status == AssignmentStatus.PENDING:
                     raise HTTPException(status_code=403, detail=f"Status is {status_msg}. You must accept the milestone invitation before updating progress.")
                elif assignment and assignment.status == AssignmentStatus.DECLINED:
                     raise HTTPException(status_code=403, detail="You have declined this milestone.")
                
                raise HTTPException(
                    status_code=403, 
                    detail=f"Access Denied. User: {current_user.email}, Role: Assignee, Status: {status_msg}. You must be In Progress or Completed."
                )
            
            # If Assignee (but not creator/admin), RESTRICT structural changes
            # Check if any restricted field is present in the form data
            restricted_vars = {
                'name': name, 'description': description, 'due_date': due_date, 
                'start_date': start_date, 'priority': priority, 
                'milestone_type': milestone_type, 'estimated_hours': estimated_hours, 
                'budget_amount': budget_amount, 'currency': currency,
                'assigned_to_ids': assigned_to_ids, 'assigned_to_id': assigned_to_id, 'file': file
            }
            
            present_fields = [k for k, v in restricted_vars.items() if v is not None]
            
            if present_fields:
                 raise HTTPException(
                    status_code=403, 
                    detail=f"Restricted fields detected: {', '.join(present_fields)}. Assignees can only update progress."
                )

        # PAST DUE CHECK (Block non-admins)
        # Exception: Allow updating tracker even if past due? 
        # Requirement: "Pending invited... should not be able to accept...".
        # But for updating tasks on an active milestone that is technically past due... usually we want to allow closure.
        # Let's keep the block for now unless user complains, but maybe relax for tracker?
        # Current logic: Block updates if due_date < today.
        # If I am late, I cannot mark it done? That seems broken.
        # I will Relax this check for TRACKER updates (tasks, actuals), but enforce for structural details.
        
        # PAST DUE CHECK (Block EVERYONE except Admin)
        # Strict Requirement: "Once... expired no work can be done... whether as milestone creator or invited member"
        if milestone.due_date and milestone.due_date < datetime.now().date() and not is_admin:
             # Exception: Allow assignees to work on Admin-created milestones
             creator = db.query(User).filter(User.id == milestone.created_by_id).first()
             is_admin_created = creator and creator.is_superuser
             
             if is_assignee and is_admin_created:
                 pass # Allow work on late milestones if they were assigned by Admin
             else:
                 raise HTTPException(status_code=403, detail="This milestone is expired. No further updates are allowed.")

        # -- AUDIT TRAIL LOGIC --
        changes = []
        
        if name is not None and milestone.name != name:
            changes.append("name")
            milestone.name = name
            
        if description is not None and milestone.description != description:
             changes.append("description")
             milestone.description = description
             
        if priority is not None and milestone.priority != priority:
             changes.append("priority")
             milestone.priority = priority
             
        if milestone_type is not None and milestone.milestone_type != milestone_type:
             changes.append("milestone_type")
             milestone.milestone_type = milestone_type
             
        if estimated_hours is not None and milestone.estimated_hours != estimated_hours:
             changes.append("estimated_hours")
             milestone.estimated_hours = estimated_hours
             
        if budget_amount is not None and milestone.budget_amount != budget_amount:
             changes.append("budget_amount")
             milestone.budget_amount = budget_amount
             
        if currency is not None and milestone.currency != currency:
             changes.append("currency")
             milestone.currency = currency
             
        # Handle dates
        if due_date is not None:
             new_due = datetime.strptime(due_date, "%Y-%m-%d").date()
             if milestone.due_date != new_due:
                 changes.append("due_date")
                 milestone.due_date = new_due
                 
        if start_date is not None:
             new_start = datetime.strptime(start_date, "%Y-%m-%d").date()
             if milestone.start_date != new_start:
                 changes.append("start_date")
                 milestone.start_date = new_start
        
        # Status manual override is restricted: Only allow manually setting 'completed'
        if status_val and status_val == 'completed':
            milestone.status = 'completed'
            milestone.status = 'completed'
            changes.append("status set to completed")
            
        # Tracker Updates
        if actual_start_date is not None:
             new_actual = datetime.strptime(actual_start_date, "%Y-%m-%d").date()
             milestone.actual_start_date = new_actual
             changes.append("actual start date")

        if actual_end_date is not None:
             new_actual_end = datetime.strptime(actual_end_date, "%Y-%m-%d").date()
             if new_actual_end > milestone.due_date:
                  raise HTTPException(status_code=400, detail="Actual End Date cannot be later than the Milestone Due Date.")
             milestone.actual_end_date = new_actual_end
             changes.append("actual end date")

        if delay_reason is not None and milestone.delay_reason != delay_reason:
             milestone.delay_reason = delay_reason
             changes.append("delay reason")

        if remarks is not None and milestone.remarks != remarks:
             milestone.remarks = remarks
             changes.append("remarks")
        
        # Handle Tasks Update
        if tasks:
            print(f"DEBUG: Received tasks payload: {tasks}")
            try:
                task_list = json.loads(tasks)
                if isinstance(task_list, list):
                    # Smart Update Logic
                    tasks_updated = False
                    current_tasks = db.query(MilestoneTask).filter(MilestoneTask.milestone_id == milestone.id).all()
                    current_task_map = {t.name: t for t in current_tasks} # Map by name for now, ID better if strictly preserved
                    
                    # Track existing IDs to find deletions? 
                    # Simpler approach: If names match, update. If new, create.
                    # BUT user wants to preserve completion metadata. So we MUST mapping strictly.
                    
                    # 1. Update Existing
                    processed_ids = []
                    
                    for t_in in task_list:
                        # Find matching existing task
                        t_db = None
                        if t_in.get('id'):
                             # If frontend sent ID, use it (Best)
                             t_db = next((ct for ct in current_tasks if str(ct.id) == str(t_in.get('id'))), None)
                        
                        if not t_db and t_in.get('name'):
                             # Fallback to name match if no ID (for robustness)
                             t_db = current_task_map.get(t_in.get('name'))
                             
                        if t_db:
                            processed_ids.append(t_db.id)
                            # Check completion flip
                            new_status = bool(t_in.get('is_completed', False))
                            if not t_db.is_completed and new_status:
                                # Mark as Completed NOW
                                t_db.is_completed = True
                                t_db.completed_by_id = current_user.id
                                t_db.completed_at = datetime.now(timezone.utc)
                                tasks_updated = True
                                
                                # Notify Admins
                                notify_admins(db, "Milestone Task Completed", f"User {current_user.full_name} completed task '{t_db.name}' in milestone '{milestone.name}'.", milestone.project_id, current_user.id)
                                
                            elif t_db.is_completed and not new_status:
                                # Re-opened? (If allowed)
                                t_db.is_completed = False
                                t_db.completed_by_id = None
                                t_db.completed_at = None
                                tasks_updated = True
                        else:
                            # New Task
                            if t_in.get('name'):
                                new_t = MilestoneTask(
                                    milestone_id=milestone.id,
                                    name=t_in.get('name'),
                                    estimated_minutes=int(t_in.get('estimated_minutes', 0)),
                                    # weightage removed
                                    is_completed=bool(t_in.get('is_completed', False))
                                )
                                if new_t.is_completed:
                                     new_t.completed_by_id = current_user.id
                                     new_t.completed_at = datetime.now(timezone.utc)
                                     # Notify Admins (New Task Created & Completed instantly?)
                                     notify_admins(db, "Milestone Task Completed", f"User {current_user.full_name} completed new task '{new_t.name}' in milestone '{milestone.name}'.", milestone.project_id, current_user.id)

                                db.add(new_t)
                                tasks_updated = True

                    if tasks_updated:
                         changes.append("task progress updated")
                         # General Update Notification handled at end
                         
                    # AUTO-COMPLETE STATUS CHECK
                    # Recalculate completion status
                    fresh_tasks = db.query(MilestoneTask).filter(MilestoneTask.milestone_id == milestone.id).all()
                    if fresh_tasks:
                        all_completed = all(t.is_completed for t in fresh_tasks)
                        if all_completed and milestone.status != "completed":
                            milestone.status = "completed"
                            changes.append("status automatically updated to completed")

                        elif not all_completed and milestone.status == "completed":
                            # Revert to in_progress if a task was unchecked (and not expired)
                            from datetime import date
                            if milestone.due_date < date.today():
                                milestone.status = "expired"
                            else:
                                milestone.status = "in_progress"
                            changes.append("status reverted to in_progress")
                    
            except Exception as e:
                print(f"ERROR UPDATING TASKS: {e}")
                pass 
                
        elif estimated_hours is not None: 
             milestone.estimated_hours = estimated_hours

        # Handle File Upload
        if file:
            header = await file.read(4)
            if header != b'%PDF':
                await file.seek(0)
                raise HTTPException(status_code=400, detail="Invalid PDF file (Content mismatch).")
            await file.seek(0)

            import os, shutil, uuid
            upload_dir = "uploads/milestones"
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"{uuid.uuid4()}_{file.filename}"
            file_path = f"{upload_dir}/{filename}"
            
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
                
            milestone.file_path = file_path
            changes.append("file attachment")

        # -- ASSIGNMENT SYNC LOGIC --
        target_ids = None
        
        if assigned_to_ids is not None:
            # Parse JSON
            try:
                parsed = json.loads(assigned_to_ids)
                if isinstance(parsed, list):
                    target_ids = [UUID(uid) for uid in parsed]
            except:
                pass
        
        # Fallback
        if target_ids is None and assigned_to_id is not None:
            target_ids = [UUID(assigned_to_id)] if assigned_to_id else []

        if target_ids is not None:
            # Get existing
            current_assignments = db.query(MilestoneAssignment).filter(MilestoneAssignment.milestone_id == milestone.id).all()
            current_map = {a.user_id: a for a in current_assignments}
            current_uids = set(current_map.keys())
            new_uids = set(target_ids)
            
            to_add = new_uids - current_uids
            to_remove = current_uids - new_uids
            
            # Add
            has_new_pending = False
            
            # Re-invite Declined/Removed users
            intersection = new_uids & current_uids
            
            import logging
            logger = logging.getLogger("uvicorn")
            logger.warning(f"DEBUG: ID Analysis.")
            logger.warning(f"DEBUG: New UIDs: {new_uids}")
            logger.warning(f"DEBUG: Current UIDs: {current_uids}")
            logger.warning(f"DEBUG: Intersection: {intersection}")
            logger.warning(f"DEBUG: To Add: {to_add}")
            
            # Cache superuser flag to avoid session expiry issues after db.commit()
            # Cache superuser flag
            is_admin_override = current_user.is_superuser
            
            for uid in intersection:
                existing = current_map[uid]
                # Reactivate if it was Declined or Soft-Removed
                if existing.status in [AssignmentStatus.DECLINED, AssignmentStatus.REMOVED]:
                     
                     # 1. Admin Override (Priority): Auto-Accept
                     if is_admin_override:
                         logger.warning(f"DEBUG: Admin Override for user {uid}. Setting IN_PROGRESS. DeclineCount: {existing.decline_count}")
                         existing.status = AssignmentStatus.IN_PROGRESS
                         existing.decline_reason = None
                         # existing.decline_count = 0  <-- REMOVED: Keep existing count to maintain "Creator Lock"
                         db.add(existing)
                         db.commit() # Persist immediate override
                         logger.warning(f"DEBUG: Committed IN_PROGRESS for {uid}. Status in DB should be active.")
                         
                         # Notification & Audit (Simplified)
                         try:
                             # ... notification logic same as before
                             pass
                         except:
                             pass
                         
                         continue

                     # 2. Standard Logic (Non-Admin)
                     # Check 2-Strike Rule
                     if (existing.decline_count or 0) >= 2:
                          logger.warning("DEBUG: Not superuser. User has 2 strikes. Blocking assignment.")
                          continue

                     
                     # Only auto-accept if assigning SELF.
                     if str(uid) == str(current_user.id):
                         existing.status = AssignmentStatus.IN_PROGRESS
                     else:
                         existing.status = AssignmentStatus.PENDING
                         has_new_pending = True

                     existing.decline_reason = None
                     
                     target_user = db.query(User).filter(User.id == uid).first()
                     user_name = target_user.full_name if target_user else "Unknown"
                     
                     # Notify
                     if str(uid) != str(current_user.id):
                        from app.models.notification import Notification
                        db.add(Notification(
                            user_id=uid,
                            type="milestone_assigned",
                            title="Milestone Invitation (Re-sent)",
                            message=f"You have been re-invited to milestone: {milestone.name}",
                            related_project_id=milestone.project_id,
                            action_url=f"/user/projects/projectdetails/{milestone.project_id}"
                        ))
                     
                     log_audit(db, current_user.id, "milestone_reassigned", milestone.id, f"Re-invited {user_name}")
                     changes.append(f"re-invited {user_name}")
            for uid in to_add:
                # Check if target is Admin (Restriction)
                target_user = db.query(User).filter(User.id == uid).first()
                if target_user and target_user.is_superuser and target_user.id != current_user.id:
                    raise HTTPException(status_code=403, detail="You cannot assign an Admin to a milestone.")

                initial_status = AssignmentStatus.PENDING
                # Only auto-accept if assigning SELF. 
                if str(uid) == str(current_user.id):
                    initial_status = AssignmentStatus.IN_PROGRESS 
                else:
                    has_new_pending = True
                
                new_assign = MilestoneAssignment(milestone_id=milestone.id, user_id=uid, status=initial_status)
                db.add(new_assign)
                
                # Notify
                if str(uid) != str(current_user.id):
                    from app.models.notification import Notification
                    db.add(Notification(
                        user_id=uid,
                        type="milestone_assigned",
                        title="New Milestone Assigned",
                        message=f"You have been assigned to milestone: {milestone.name}",
                        related_project_id=milestone.project_id,
                        action_url=f"/user/projects/projectdetails/{milestone.project_id}"
                    ))
                
                # Notify Admins
                notify_admins(db, "Milestone Assignment", f"User {current_user.full_name} assigned {target_user.full_name} to milestone '{milestone.name}'.", milestone.project_id, current_user.id)

                log_audit(db, current_user.id, "milestone_assigned", milestone.id, f"Assigned to {target_user.full_name}")
                changes.append(f"added assignee {target_user.full_name}")

            # Remove
            for uid in to_remove:
                # Protection: If user is permanently banned (Declined 2+ times), DO NOT remove the assignment.
                # The frontend might not send them back because they are unselectable/locked, but we must persist the ban record.
                existing_assign = current_map[uid]

                if (getattr(existing_assign, 'decline_count', 0) or 0) >= 2:
                    # UPDATED: Allow Admin to remove banned users
                    allow_remove = is_admin_override or current_user.is_superuser
                    
                    # BYPASS: Force allow removal
                    if not allow_remove:
                        logger.warning(f"DEBUG: Blocking removal of banned user {uid} for non-admin.")
                        continue

                # Protection: Creator cannot remove Admin if Admin assigned themselves
                target_user_r = db.query(User).filter(User.id == uid).first()
                if target_user_r and target_user_r.is_superuser:
                    # If current user is NOT admin, they certainly cannot remove an admin
                    # Even if they are the Creator.
                    if not is_admin_override:
                         # Skip removal - preserve the admin assignment silently
                         logger.warning(f"DEBUG: Blocking removal of Admin {uid}.")
                         continue

                # Soft Delete instead of hard delete to preserve decline_count
                existing_assign.status = AssignmentStatus.REMOVED
                db.add(existing_assign)
                
                user_name = target_user_r.full_name if target_user_r else "Unknown"
                log_audit(db, current_user.id, "milestone_unassigned", milestone.id, f"Removed assignee {user_name}")
                changes.append(f"removed assignee {user_name}")

            # -- FULL STATUS RECALCULATION (Enforce Strict Rule) --
            # Flush session to ensure assignments are updated for the query
            db.flush()
            
            re_assignments = db.query(MilestoneAssignment).filter(MilestoneAssignment.milestone_id == milestone.id).all()
            has_unaccepted = False
            has_accepted = False
            for a in re_assignments:
                # Ignore permanently banned users UNLESS they are active (Admin Override)
                if (getattr(a, 'decline_count', 0) or 0) >= 2 and a.status != AssignmentStatus.IN_PROGRESS:
                    continue
                
                # Check status exactly
                if a.status in [AssignmentStatus.PENDING, AssignmentStatus.DECLINED]:
                    has_unaccepted = True
                elif a.status in [AssignmentStatus.IN_PROGRESS, AssignmentStatus.COMPLETED]:
                    has_accepted = True
                
                # We can't break early because we need to know if SOMEONE accepted
            
            old_status = milestone.status
            if has_unaccepted:
                # Force back to pending if anyone is unaccepted (and not completed)
                if milestone.status not in ["completed", "pending"]:
                     milestone.status = "pending"
            elif has_accepted:
                # Everyone active (who isn't banned) has accepted!
                if milestone.status == "pending":
                    from datetime import date
                    if milestone.due_date < date.today():
                        milestone.status = "expired"
                    else:
                        milestone.status = "in_progress"
            # If NO ONE is assigned (all banned or empty), keep current status or pending if new
            elif milestone.status == "in_progress":
                 milestone.status = "pending"
            
            if old_status != milestone.status:
                changes.append(f"status automatically updated from {old_status} to {milestone.status}")

        if changes:
             milestone.last_updated_by_id = current_user.id
             summary = f"Updated {', '.join(changes)}"
             milestone.last_update_summary = summary
             milestone.last_updated_at = datetime.now(timezone.utc)
             
             # Log Update
             log_audit(db, current_user.id, "milestone_updated", milestone.id, summary)

             # Notify Admins of Update
             notify_admins(db, "Milestone Updated", f"User {current_user.full_name} updated milestone '{milestone.name}': {summary}", milestone.project_id, current_user.id)
        
        db.commit()
        db.refresh(milestone)
        return milestone

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to update milestone: {str(e)}")


@router.post("/milestones/{milestone_id}/delete")
def delete_milestone(
    milestone_id: UUID,
    delete_data: MilestoneDelete,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete a milestone with reason (Strict Permissions)"""
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
        
    project = check_project_access(milestone.project_id, current_user, db, require_write=True)
    
    # PERMISSIONS
    is_admin = current_user.is_superuser
    is_creator = (milestone.created_by_id == current_user.id)
    
    # PAST DUE CHECK (Block non-admins)
    if milestone.due_date < datetime.now().date() and not is_admin:
         raise HTTPException(status_code=403, detail="This milestone is past due. Only Admins can delete it.")

    # 1. Admin can always delete
    if is_admin:
        # User requested Admin should also mention reason
        # We accept it if provided
        pass 
    # 2. Creator can delete but needs reason 
    elif is_creator:
        if not delete_data.reason or len(delete_data.reason.strip()) < 5:
             raise HTTPException(status_code=400, detail="A valid reason is required to delete a milestone.")
    # 3. Project Owner (if not creator) CANNOT delete
    else:
        raise HTTPException(status_code=403, detail="Only the milestone creator or an admin can delete milestones.")

    # NOTIFICATIONS (For Admin AND Creator)
    # We want to notify users if an Admin deletes their task too
    if (is_creator or is_admin):
        from app.models.notification import Notification
        
        reason_text = delete_data.reason or "No reason provided"
        actor_name = current_user.full_name or "Admin"
        
        # 1. Notify Project Owner (if not self)
        if project.created_by_id != current_user.id:
            db.add(Notification(
                user_id=project.created_by_id,
                type="milestone_deleted",
                title="Milestone Deleted",
                message=f"{actor_name} deleted milestone '{milestone.name}'. Reason: {reason_text}",
                related_project_id=project.id,
                action_url=f"/user/projects/projectdetails/{project.id}"
            ))

        # 2. Notify Assigned Users
        # Using relationship
        for assignment in milestone.assignments:
             if assignment.user_id != current_user.id:
                 db.add(Notification(
                    user_id=assignment.user_id,
                    type="milestone_deleted",
                    title="Milestone Deleted",
                    message=f"{actor_name} deleted milestone '{milestone.name}'. Reason: {reason_text}",
                    related_project_id=project.id,
                    action_url=f"/user/projects/projectdetails/{project.id}"
                ))
        
    db.delete(milestone)
    db.commit()
    return {"message": "Milestone deleted successfully"}


@router.post("/milestones/{milestone_id}/accept", response_model=MilestoneResponse)
def accept_milestone(
    milestone_id: UUID, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_active_user)
):
    """Accept a milestone assignment"""
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
        
    # Find assignment
    assignment = db.query(MilestoneAssignment).filter(
        MilestoneAssignment.milestone_id == milestone.id,
        MilestoneAssignment.user_id == current_user.id
    ).first()
    
    if not assignment:
        raise HTTPException(status_code=403, detail="You are not assigned to this milestone.")
        
    assignment.status = AssignmentStatus.IN_PROGRESS
    assignment.decline_reason = None
    
    # Audit
    log_audit(db, current_user.id, "milestone_accepted", milestone_id, "Accepted assignment")

    # 1. Commit the assignment change FIRST to ensure DB is consistent
    db.commit()
    
    
    # 2. Re-fetch milestone and its assignments fresh
    db.expire_all() 
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    all_assignments = db.query(MilestoneAssignment).filter(MilestoneAssignment.milestone_id == milestone.id).all()
    
    # Robust check using string values
    # Updated Logic: Milestone logic remains 'pending' if ANYONE is 'pending' OR 'declined'
    re_assignments = db.query(MilestoneAssignment).filter(MilestoneAssignment.milestone_id == milestone.id).all()
    has_unaccepted = False
    has_accepted = False
    for a in re_assignments:
        # Ignore permanently banned users so they don't block start
        if (getattr(a, 'decline_count', 0) or 0) >= 2:
            continue

        if a.status in [AssignmentStatus.PENDING, AssignmentStatus.DECLINED]:
            has_unaccepted = True
        elif a.status in [AssignmentStatus.IN_PROGRESS, AssignmentStatus.COMPLETED]:
            has_accepted = True
    
    if has_unaccepted:
        # Strict enforcement: if anyone unaccepted, force back to pending (except completed)
        if milestone.status not in ["completed", "pending"]:
             milestone.status = "pending"
             db.add(milestone)
    elif has_accepted:
        # All accepted! Flip to in_progress or expired
        if milestone.status == "pending":
             from datetime import date
             if milestone.due_date < date.today():
                 milestone.status = "expired"
             else:
                 milestone.status = "in_progress"
             db.add(milestone)
    
    db.commit()
    db.refresh(milestone)

    
    # Notify creator (if not self)
    
    
    # Notify creator (if not self)
    if str(milestone.created_by_id) != str(current_user.id):
        from app.models.notification import Notification
        notif = Notification(
            user_id=milestone.created_by_id,
            type="milestone_accepted",
            title="Milestone Accepted",
            message=f"{current_user.full_name} accepted the milestone: {milestone.name}",
            related_project_id=milestone.project_id,
            action_url=f"/user/projects/projectdetails/{milestone.project_id}"
        )
        db.add(notif)
        db.commit()
        
    return milestone

@router.post("/milestones/{milestone_id}/decline", response_model=MilestoneResponse)
def decline_milestone(
    milestone_id: UUID, 
    decline_data: MilestoneDecline,
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_active_user)
):
    """Decline a milestone assignment"""
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
        
    # Find assignment
    assignment = db.query(MilestoneAssignment).filter(
        MilestoneAssignment.milestone_id == milestone.id,
        MilestoneAssignment.user_id == current_user.id
    ).first()
    
    if not assignment:
         raise HTTPException(status_code=403, detail="You are not assigned to this milestone.")
        
    assignment.status = AssignmentStatus.DECLINED
    assignment.decline_reason = decline_data.reason
    current_count = (assignment.decline_count or 0) + 1
    assignment.decline_count = current_count

    log_audit(db, current_user.id, "milestone_declined", milestone_id, f"Declined. Reason: {decline_data.reason} (Count: {current_count})")

    # Status Recalculation (Enforce Strict Rule + 2-Strike)
    db.flush()
    all_assignments = db.query(MilestoneAssignment).filter(MilestoneAssignment.milestone_id == milestone.id).all()
    has_unaccepted = False
    has_accepted = False
    for a in all_assignments:
        # Ignore permanently banned users (including the one who just declined if count >= 2)
        if (getattr(a, 'decline_count', 0) or 0) >= 2:
            continue
        
        if a.status in [AssignmentStatus.PENDING, AssignmentStatus.DECLINED]:
            has_unaccepted = True
        elif a.status in [AssignmentStatus.IN_PROGRESS, AssignmentStatus.COMPLETED]:
            has_accepted = True

    if has_unaccepted:
        # If any active member is pending/declined, force status to pending (revert if needed)
        if milestone.status in ["in_progress", "expired"]:
            milestone.status = "pending"
            db.add(milestone)
    elif has_accepted:
        # Everyone active has accepted! Advance to in_progress/expired if was pending
        if milestone.status == "pending":
             from datetime import date
             if milestone.due_date < date.today():
                 milestone.status = "expired"
             else:
                 milestone.status = "in_progress"
             db.add(milestone)

    db.commit()
    db.refresh(milestone)
    
    # Notify creator
    if str(milestone.created_by_id) != str(current_user.id):
        from app.models.notification import Notification
        
        msg_title = "Milestone Declined"
        msg_body = f"{current_user.full_name} declined milestone: {milestone.name}. Reason: {decline_data.reason}"
        
        if current_count >= 2:
             msg_title = "Milestone Declined (Permanently)"
             msg_body += " (This user has declined twice and is now permanently removed)."
        
        notif = Notification(
            user_id=milestone.created_by_id,
            type="milestone_declined",
            title=msg_title,
            message=msg_body,
            related_project_id=milestone.project_id,
            action_url=f"/user/projects/projectdetails/{milestone.project_id}"
        )
        db.add(notif)
        db.commit()
        
    return milestone
