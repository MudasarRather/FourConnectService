from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from ..database import get_db
from ..models import dpr as models
from ..schemas import dpr as schemas
from ..utils.dependencies import get_current_user
from ..models.user import User

router = APIRouter(prefix="/dpr", tags=["DPR Proposal"])

print("--- [SYS] DPR Router Initialization: Synchronized with is_superuser logic ---")

@router.post("/", response_model=schemas.DprDocumentRead)
def create_dpr(
    dpr_in: schemas.DprDocumentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Core Document
    # Generate unique DPR code
    # Format: DPR-YYYY-0001
    current_year = 2026 # Should be dynamic but for now matches reference
    last_dpr = db.query(models.DprDocument).order_by(models.DprDocument.created_at.desc()).first()
    next_num = 1
    if last_dpr and last_dpr.dpr_code and last_dpr.dpr_code.startswith(f'DPR-{current_year}-'):
        try:
            next_num = int(last_dpr.dpr_code.split('-')[-1]) + 1
        except:
            next_num = 1
    
    generated_code = f"DPR-{current_year}-{next_num:04d}"

    db_dpr = models.DprDocument(
        title=dpr_in.title,
        project_id=dpr_in.project_id,
        dpr_code=generated_code,
        version=dpr_in.version,
        status=dpr_in.status,
        created_by_id=current_user.id
    )
    db.add(db_dpr)
    db.flush() # Get ID

    # 1. Overview
    if dpr_in.overview:
        db.add(models.DprOverview(dpr_id=db_dpr.id, **dpr_in.overview.dict()))
    
    # 2. Client
    if dpr_in.client:
        db.add(models.DprClient(dpr_id=db_dpr.id, **dpr_in.client.dict()))
    
    # 3. Problem Statement
    if dpr_in.problem_statement:
        db.add(models.DprProblemStatement(dpr_id=db_dpr.id, **dpr_in.problem_statement.dict()))
    
    # 4. Objectives
    for obj in dpr_in.objectives:
        db.add(models.DprObjective(dpr_id=db_dpr.id, **obj.dict()))
    
    # 5. Scope
    if dpr_in.scope:
        db.add(models.DprScope(dpr_id=db_dpr.id, **dpr_in.scope.dict()))
    
    # 6. Architecture
    if dpr_in.architecture:
        db.add(models.DprArchitecture(dpr_id=db_dpr.id, **dpr_in.architecture.dict()))
    
    # 7. Implementation
    if dpr_in.implementation:
        db.add(models.DprImplementation(dpr_id=db_dpr.id, **dpr_in.implementation.dict()))
    
    # 8. Milestones
    for ms in dpr_in.milestones:
        db.add(models.DprMilestone(dpr_id=db_dpr.id, **ms.dict()))
    
    # 9. Team
    for tm in dpr_in.team:
        db.add(models.DprTeamMember(dpr_id=db_dpr.id, **tm.dict()))
    
    # 10. Budget
    if dpr_in.budget:
        db.add(models.DprBudget(dpr_id=db_dpr.id, **dpr_in.budget.dict()))
    for item in dpr_in.budget_items:
        db.add(models.DprBudgetItem(dpr_id=db_dpr.id, **item.dict()))
    
    # 11. Risks
    for r in dpr_in.risks:
        db.add(models.DprRisk(dpr_id=db_dpr.id, **r.dict()))
    
    # 12. Compliance
    if dpr_in.compliance:
        db.add(models.DprCompliance(dpr_id=db_dpr.id, **dpr_in.compliance.dict()))
    
    # 13. Outcomes
    if dpr_in.outcomes:
        db.add(models.DprOutcome(dpr_id=db_dpr.id, **dpr_in.outcomes.dict()))
    
    # 14. Attachments
    for att in dpr_in.attachments:
        db.add(models.DprAttachment(dpr_id=db_dpr.id, **att.dict()))
    
    # 15. Approvals
    for app in dpr_in.approvals:
        db.add(models.DprApproval(dpr_id=db_dpr.id, **app.dict()))

    db.commit()
    db.refresh(db_dpr)
    return db_dpr

from sqlalchemy.orm import joinedload

@router.get("/", response_model=List[schemas.DprDocumentRead])
def list_dprs(
    status_filter: Optional[str] = Query(None),
    project_id: Optional[UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(models.DprDocument).options(
        joinedload(models.DprDocument.overview),
        joinedload(models.DprDocument.client),
        joinedload(models.DprDocument.created_by)
    )
    
    if not current_user.is_superuser:
        query = query.filter(models.DprDocument.created_by_id == current_user.id)
    else:
        # Admins see everything EXPECT drafts from other users
        from sqlalchemy import or_, func
        query = query.filter(or_(
            func.lower(models.DprDocument.status) != 'draft',
            models.DprDocument.created_by_id == current_user.id
        ))
    
    if status_filter:
        query = query.filter(func.lower(models.DprDocument.status) == status_filter.lower())
    if project_id:
        query = query.filter(models.DprDocument.project_id == project_id)
        
    return query.all()

@router.get("/{dpr_id}", response_model=schemas.DprDocumentRead)
def get_dpr(
    dpr_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db_dpr = db.query(models.DprDocument).filter(models.DprDocument.id == dpr_id).first()
    if not db_dpr:
        raise HTTPException(status_code=404, detail="DPR not found")
        
    if not current_user.is_superuser and db_dpr.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    return db_dpr

@router.put("/{dpr_id}", response_model=schemas.DprDocumentRead)
def update_dpr(
    dpr_id: UUID,
    dpr_in: schemas.DprDocumentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db_dpr = db.query(models.DprDocument).filter(models.DprDocument.id == dpr_id).first()
    if not db_dpr:
        raise HTTPException(status_code=404, detail="DPR not found")
        
    if not current_user.is_superuser and db_dpr.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Update Core fields
    update_data = dpr_in.dict(exclude_unset=True)
    for key, value in update_data.items():
        if key not in ["overview", "client", "problem_statement", "objectives", "scope", "architecture", 
                      "implementation", "milestones", "team", "budget", "budget_items", 
                      "risks", "compliance", "outcomes", "attachments", "approvals"]:
            setattr(db_dpr, key, value)

    # Simplified nested update: Delete and Re-create for lists, Merge for single objects
    # This is a common pattern for complex wizards to avoid diffing logic
    
    # 1. Overview
    if dpr_in.overview:
        if db_dpr.overview:
            for k, v in dpr_in.overview.dict().items(): setattr(db_dpr.overview, k, v)
        else:
            db_dpr.overview = models.DprOverview(**dpr_in.overview.dict())
            
    # 2. Client
    if dpr_in.client:
        if db_dpr.client:
            for k, v in dpr_in.client.dict().items(): setattr(db_dpr.client, k, v)
        else:
            db_dpr.client = models.DprClient(**dpr_in.client.dict())

    # 3. Problem Statement
    if dpr_in.problem_statement:
        if db_dpr.problem_statement:
            for k, v in dpr_in.problem_statement.dict().items(): setattr(db_dpr.problem_statement, k, v)
        else:
            db_dpr.problem_statement = models.DprProblemStatement(**dpr_in.problem_statement.dict())

    # 4. Objectives (List - Clear and Replace)
    if dpr_in.objectives is not None:
        db.query(models.DprObjective).filter(models.DprObjective.dpr_id == dpr_id).delete()
        for obj in dpr_in.objectives:
            db.add(models.DprObjective(dpr_id=dpr_id, **obj.dict()))

    # 5. Scope
    if dpr_in.scope:
        if db_dpr.scope:
            for k, v in dpr_in.scope.dict().items(): setattr(db_dpr.scope, k, v)
        else:
            db_dpr.scope = models.DprScope(**dpr_in.scope.dict())

    # 6. Architecture
    if dpr_in.architecture:
        if db_dpr.architecture:
            for k, v in dpr_in.architecture.dict().items(): setattr(db_dpr.architecture, k, v)
        else:
            db_dpr.architecture = models.DprArchitecture(**dpr_in.architecture.dict())

    # 7. Implementation
    if dpr_in.implementation:
        if db_dpr.implementation:
            for k, v in dpr_in.implementation.dict().items(): setattr(db_dpr.implementation, k, v)
        else:
            db_dpr.implementation = models.DprImplementation(**dpr_in.implementation.dict())

    # 8. Milestones (List)
    if dpr_in.milestones is not None:
        db.query(models.DprMilestone).filter(models.DprMilestone.dpr_id == dpr_id).delete()
        for ms in dpr_in.milestones:
            db.add(models.DprMilestone(dpr_id=dpr_id, **ms.dict()))

    # 9. Team (List)
    if dpr_in.team is not None:
        db.query(models.DprTeamMember).filter(models.DprTeamMember.dpr_id == dpr_id).delete()
        for tm in dpr_in.team:
            db.add(models.DprTeamMember(dpr_id=dpr_id, **tm.dict()))

    # 10. Budget & Items
    if dpr_in.budget:
        if db_dpr.budget:
            for k, v in dpr_in.budget.dict().items(): setattr(db_dpr.budget, k, v)
        else:
            db_dpr.budget = models.DprBudget(**dpr_in.budget.dict())
    
    if dpr_in.budget_items is not None:
        db.query(models.DprBudgetItem).filter(models.DprBudgetItem.dpr_id == dpr_id).delete()
        for item in dpr_in.budget_items:
            db.add(models.DprBudgetItem(dpr_id=dpr_id, **item.dict()))

    # 11. Risks (List)
    if dpr_in.risks is not None:
        db.query(models.DprRisk).filter(models.DprRisk.dpr_id == dpr_id).delete()
        for r in dpr_in.risks:
            db.add(models.DprRisk(dpr_id=dpr_id, **r.dict()))

    # 12. Compliance
    if dpr_in.compliance:
        if db_dpr.compliance:
            for k, v in dpr_in.compliance.dict().items(): setattr(db_dpr.compliance, k, v)
        else:
            db_dpr.compliance = models.DprCompliance(**dpr_in.compliance.dict())

    # 13. Outcomes
    if dpr_in.outcomes:
        if db_dpr.outcomes:
            for k, v in dpr_in.outcomes.dict().items(): setattr(db_dpr.outcomes, k, v)
        else:
            db_dpr.outcomes = models.DprOutcome(**dpr_in.outcomes.dict())

    # 14. Attachments (List)
    if dpr_in.attachments is not None:
        db.query(models.DprAttachment).filter(models.DprAttachment.dpr_id == dpr_id).delete()
        for att in dpr_in.attachments:
            db.add(models.DprAttachment(dpr_id=dpr_id, **att.dict()))

    # 15. Approvals (List)
    if dpr_in.approvals is not None:
        db.query(models.DprApproval).filter(models.DprApproval.dpr_id == dpr_id).delete()
        for app in dpr_in.approvals:
            db.add(models.DprApproval(dpr_id=dpr_id, **app.dict()))

    db.commit()
    db.refresh(db_dpr)
    return db_dpr

@router.delete("/{dpr_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dpr(
    dpr_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db_dpr = db.query(models.DprDocument).filter(models.DprDocument.id == dpr_id).first()
    if not db_dpr:
        raise HTTPException(status_code=404, detail="DPR not found")
        
    if not current_user.is_superuser and db_dpr.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    db.delete(db_dpr)
    db.commit()
    return None
