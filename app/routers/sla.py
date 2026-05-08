from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_user
from app.models.sla import SlaAgreement, SlaServiceScope, SlaMetric, SlaEscalation, SlaPenalty, SlaSignatory
from app.models.notification import Notification
from app.schemas.sla import SlaAgreementCreate, SlaAgreementUpdate, SlaAgreementResponse

router = APIRouter(prefix="/sla", tags=["SLA Agreements"])

@router.post("/", response_model=SlaAgreementResponse, status_code=status.HTTP_201_CREATED)
def create_sla_draft(sla: SlaAgreementCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Create the base agreement
    db_sla = SlaAgreement(
        project_id=sla.project_id,
        created_by_id=current_user.id,
        **sla.dict(exclude={'project_id', 'services', 'escalations', 'penalties', 'signatories'})
    )
    db.add(db_sla)
    db.commit()
    db.refresh(db_sla)
    
    # Process nested scopes
    if sla.services:
        for svc in sla.services:
            db_svc = SlaServiceScope(
                agreement_id=db_sla.id,
                service_name=svc.service_name,
                description=svc.description,
                service_category=svc.service_category
            )
            db.add(db_svc)
            db.flush() # get ID for metrics
            if svc.metrics:
                for metric in svc.metrics:
                    db_metric = SlaMetric(
                        service_scope_id=db_svc.id,
                        **metric.dict()
                    )
                    db.add(db_metric)
    
    if sla.escalations:
        for esc in sla.escalations:
            db.add(SlaEscalation(agreement_id=db_sla.id, **esc.dict()))
            
    if sla.penalties:
        for pen in sla.penalties:
            db.add(SlaPenalty(agreement_id=db_sla.id, **pen.dict()))
            
    if sla.signatories:
        for sig in sla.signatories:
            db.add(SlaSignatory(agreement_id=db_sla.id, **sig.dict()))
            
    db.commit()
    db.refresh(db_sla)
    
    # Notify admins on submission
    if db_sla.status == 'Pending':
        admins = db.query(User).filter(User.is_superuser == True).all()
        for admin in admins:
            if admin.id != current_user.id: # Skip self
                admin_notif = Notification(
                    user_id=admin.id,
                    type="sla_submitted",
                    title="SLA Submitted",
                    message=f"A new SLA '{db_sla.title}' has been submitted for review by {current_user.full_name}.",
                    related_project_id=db_sla.project_id,
                    related_user_id=current_user.id,
                    action_url=f"/admin/documents/sla?tab=pending"
                )
                db.add(admin_notif)
        db.commit()
        
    return db_sla

@router.get("/", response_model=List[SlaAgreementResponse])
def get_slas(
    project_id: UUID = None,
    status: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    q = db.query(SlaAgreement)
    if project_id:
        q = q.filter(SlaAgreement.project_id == project_id)
    if status:
        q = q.filter(SlaAgreement.status == status)
        
    # Standard role-based scoping
    if current_user.is_superuser:
        # Admins see all non-drafts + their own drafts
        q = q.filter(or_(SlaAgreement.status != "Draft", SlaAgreement.created_by_id == current_user.id))
    else:
        # Regular users only see their own
        q = q.filter(SlaAgreement.created_by_id == current_user.id)
        
    return q.order_by(SlaAgreement.created_at.desc()).all()

@router.get("/{sla_id}", response_model=SlaAgreementResponse)
def get_sla(sla_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_sla = db.query(SlaAgreement).filter(SlaAgreement.id == sla_id).first()
    if not db_sla:
        raise HTTPException(status_code=404, detail="SLA agreement not found")
    return db_sla

@router.put("/{sla_id}", response_model=SlaAgreementResponse)
def update_sla(sla_id: UUID, update_data: SlaAgreementUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_sla = db.query(SlaAgreement).filter(SlaAgreement.id == sla_id).first()
    if not db_sla:
        raise HTTPException(status_code=404, detail="SLA agreement not found")
        
    old_status = db_sla.status
    update_dict = update_data.dict(exclude_unset=True)
    
    # Update core SLA data
    core_fields_to_update = {k: v for k, v in update_dict.items() if k not in ['services', 'escalations', 'penalties', 'signatories']}
    for key, value in core_fields_to_update.items():
        setattr(db_sla, key, value)
        
    new_status = db_sla.status
    
    # Send Notifications on status change
    if old_status != new_status:
        if new_status == 'Rejected' and current_user.is_superuser:
            new_notif = Notification(
                user_id=db_sla.created_by_id,
                type="sla_rejected",
                title="SLA Rejected",
                message=f"Your SLA '{db_sla.title}' was rejected. Reason: {update_dict.get('rejection_reason', 'No reason provided')}",
                related_project_id=db_sla.project_id,
                related_user_id=current_user.id,
                action_url="/user/documents/sla?tab=rejected"
            )
            db.add(new_notif)
            db.commit()
        elif new_status == 'Pending':
            admins = db.query(User).filter(User.is_superuser == True).all()
            for admin in admins:
                if admin.id != current_user.id:
                    admin_notif = Notification(
                        user_id=admin.id,
                        type="sla_submitted",
                        title="SLA Resubmitted",
                        message=f"The SLA '{db_sla.title}' has been updated and resubmitted by {current_user.full_name}.",
                        related_project_id=db_sla.project_id,
                        related_user_id=current_user.id,
                        action_url=f"/admin/documents/sla?tab=pending"
                    )
                    db.add(admin_notif)
            db.commit()
        
    # Handle nested collections
    if 'services' in update_dict:
        # Delete existing metrics first to avoid FK constraint violations
        scope_ids = db.query(SlaServiceScope.id).filter(SlaServiceScope.agreement_id == sla_id).subquery()
        db.query(SlaMetric).filter(SlaMetric.service_scope_id.in_(scope_ids)).delete()
        # Delete existing scopes
        db.query(SlaServiceScope).filter(SlaServiceScope.agreement_id == sla_id).delete()
        db.flush()
        # Rebuild
        for svc in update_data.services:
            db_svc = SlaServiceScope(agreement_id=sla_id, service_name=svc.service_name, description=svc.description, service_category=svc.service_category)
            db.add(db_svc)
            db.flush()
            if svc.metrics:
                for metric in svc.metrics:
                    db.add(SlaMetric(service_scope_id=db_svc.id, **metric.dict()))
                    
    if 'escalations' in update_dict:
        db.query(SlaEscalation).filter(SlaEscalation.agreement_id == sla_id).delete()
        db.flush()
        for esc in update_data.escalations:
            db.add(SlaEscalation(agreement_id=sla_id, **esc.dict()))
            
    if 'penalties' in update_dict:
        db.query(SlaPenalty).filter(SlaPenalty.agreement_id == sla_id).delete()
        db.flush()
        for pen in update_data.penalties:
            db.add(SlaPenalty(agreement_id=sla_id, **pen.dict()))
            
    if 'signatories' in update_dict:
        db.query(SlaSignatory).filter(SlaSignatory.agreement_id == sla_id).delete()
        db.flush()
        for sig in update_data.signatories:
            db.add(SlaSignatory(agreement_id=sla_id, **sig.dict()))
            
    db.commit()
    db.refresh(db_sla)
    return db_sla

@router.delete("/{sla_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sla(sla_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_sla = db.query(SlaAgreement).filter(SlaAgreement.id == sla_id).first()
    if not db_sla:
        raise HTTPException(status_code=404, detail="SLA agreement not found")
        
    # Check permissions
    if not current_user.is_superuser and db_sla.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    db.delete(db_sla)
    db.commit()
    return None
