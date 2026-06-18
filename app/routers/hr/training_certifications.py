"""HR Training & Development — Certifications (catalog + employee certs + expiry)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.skill import Skill
from app.models.hr.training import TrainingProgram, TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.certification import (
    Certification, EmployeeCertification, CertificationStatus,
)
from app.schemas.hr.certification import (
    CertificationCreate, CertificationUpdate, CertificationResponse,
    EmployeeCertificationCreate, EmployeeCertificationUpdate, EmployeeCertificationResponse,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.service import emp_display
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Certifications"])


def _days_to_expiry(expiry: Optional[date]) -> Optional[int]:
    if not expiry:
        return None
    return (expiry - date.today()).days


# ───────────────────────────── Certification catalog ─────────────────────────────

def _cert_resp(db: Session, c: Certification) -> CertificationResponse:
    held = db.query(func.count(EmployeeCertification.id)).filter(
        EmployeeCertification.certification_id == c.id,
        EmployeeCertification.is_deleted == False,  # noqa: E712
    ).scalar()
    sk = db.query(Skill.name).filter(Skill.id == c.skill_id).first() if c.skill_id else None
    return CertificationResponse(
        id=c.id, name=c.name, code=c.code, issuing_authority=c.issuing_authority,
        category=c.category, validity_months=c.validity_months, description=c.description,
        skill_id=c.skill_id, skill_name=sk[0] if sk else None, is_active=c.is_active,
        held_count=int(held or 0), created_at=c.created_at,
    )


@router.get("/certifications", response_model=List[CertificationResponse])
def list_certifications(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(Certification).filter(Certification.is_deleted == False)  # noqa: E712
    if search:
        q = q.filter(Certification.name.ilike(f"%{search}%"))
    rows = q.order_by(Certification.name.asc()).all()
    return [_cert_resp(db, c) for c in rows]


@router.post("/certifications", response_model=CertificationResponse, status_code=http_status.HTTP_201_CREATED)
def create_certification(
    payload: CertificationCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if db.query(Certification.id).filter(Certification.name == payload.name, Certification.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "Certification name already exists")
    c = Certification(**payload.model_dump(), created_by_id=admin.id)
    db.add(c)
    db.flush()
    write_training_audit(db, entity_type="CERTIFICATION", entity_id=c.id,
                         action=TrainingAuditAction.CREATE, actor_id=admin.id, note=c.name)
    db.commit()
    db.refresh(c)
    return _cert_resp(db, c)


@router.patch("/certifications/{cert_id}", response_model=CertificationResponse)
def update_certification(
    cert_id: UUID,
    payload: CertificationUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    c = db.query(Certification).filter(Certification.id == cert_id, Certification.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Certification not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    write_training_audit(db, entity_type="CERTIFICATION", entity_id=c.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(c)
    return _cert_resp(db, c)


@router.delete("/certifications/{cert_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_certification(
    cert_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    c = db.query(Certification).filter(Certification.id == cert_id, Certification.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Certification not found")
    held = db.query(func.count(EmployeeCertification.id)).filter(
        EmployeeCertification.certification_id == cert_id,
        EmployeeCertification.is_deleted == False,  # noqa: E712
    ).scalar()
    if held:
        raise HTTPException(409, f"Cannot delete a certification held by {held} employee(s).")
    c.is_deleted = True
    write_training_audit(db, entity_type="CERTIFICATION", entity_id=c.id,
                         action=TrainingAuditAction.DELETE, actor_id=admin.id)
    db.commit()


# ───────────────────────────── Employee certifications ─────────────────────────────

def _emp_cert_resp(db: Session, ec: EmployeeCertification) -> EmployeeCertificationResponse:
    disp = emp_display(db, ec.employee_id)
    ren = None
    if ec.renewal_training_program_id:
        r = db.query(TrainingProgram.name).filter(TrainingProgram.id == ec.renewal_training_program_id).first()
        ren = r[0] if r else None
    category = None
    if ec.certification_id:
        c = db.query(Certification.category).filter(Certification.id == ec.certification_id).first()
        category = c[0] if c else None
    return EmployeeCertificationResponse(
        id=ec.id, employee_id=ec.employee_id, employee_name=disp.get("name"),
        employee_code=disp.get("code"), department_name=disp.get("dept"),
        designation_name=disp.get("desg"), category=category,
        certification_id=ec.certification_id, name=ec.name,
        issuing_authority=ec.issuing_authority, certificate_number=ec.certificate_number,
        issue_date=ec.issue_date, expiry_date=ec.expiry_date, status=ec.status,
        days_to_expiry=_days_to_expiry(ec.expiry_date), certificate_url=ec.certificate_url,
        drive_document_id=ec.drive_document_id, source_assignment_id=ec.source_assignment_id,
        renewal_training_program_id=ec.renewal_training_program_id, renewal_program_name=ren,
        notes=ec.notes, created_at=ec.created_at,
    )


@router.get("/employee-certifications", response_model=List[EmployeeCertificationResponse])
def list_employee_certifications(
    employee_id: Optional[UUID] = None,
    cert_status: Optional[CertificationStatus] = None,
    expiring_within_days: Optional[int] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(EmployeeCertification).filter(EmployeeCertification.is_deleted == False)  # noqa: E712
    if employee_id:
        q = q.filter(EmployeeCertification.employee_id == employee_id)
    if cert_status:
        q = q.filter(EmployeeCertification.status == cert_status)
    if expiring_within_days is not None:
        cutoff = date.today() + timedelta(days=int(expiring_within_days))
        q = q.filter(
            EmployeeCertification.expiry_date.isnot(None),
            EmployeeCertification.expiry_date <= cutoff,
        )
    rows = q.order_by(EmployeeCertification.expiry_date.asc().nullslast()).all()
    return [_emp_cert_resp(db, ec) for ec in rows]


@router.get("/certifications/expiring", response_model=List[EmployeeCertificationResponse])
def expiring_certifications(
    window: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    cutoff = date.today() + timedelta(days=window)
    rows = db.query(EmployeeCertification).filter(
        EmployeeCertification.is_deleted == False,  # noqa: E712
        EmployeeCertification.expiry_date.isnot(None),
        EmployeeCertification.expiry_date <= cutoff,
        EmployeeCertification.status != CertificationStatus.REVOKED,
    ).order_by(EmployeeCertification.expiry_date.asc()).all()
    return [_emp_cert_resp(db, ec) for ec in rows]


@router.post("/employee-certifications", response_model=EmployeeCertificationResponse, status_code=http_status.HTTP_201_CREATED)
def create_employee_certification(
    payload: EmployeeCertificationCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not db.query(Employee.id).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Employee not found")
    ec = EmployeeCertification(**payload.model_dump(), created_by_id=admin.id)
    db.add(ec)
    db.flush()
    write_training_audit(db, entity_type="CERTIFICATION", entity_id=ec.id,
                         action=TrainingAuditAction.CREATE, actor_id=admin.id, note=ec.name)
    db.commit()
    db.refresh(ec)
    return _emp_cert_resp(db, ec)


@router.patch("/employee-certifications/{ec_id}", response_model=EmployeeCertificationResponse)
def update_employee_certification(
    ec_id: UUID,
    payload: EmployeeCertificationUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    ec = db.query(EmployeeCertification).filter(EmployeeCertification.id == ec_id, EmployeeCertification.is_deleted == False).first()  # noqa: E712
    if not ec:
        raise HTTPException(404, "Employee certification not found")
    data = payload.model_dump(exclude_unset=True)
    # If expiry changes, clear the notification dedup marker so the monitor re-evaluates.
    if "expiry_date" in data and data["expiry_date"] != ec.expiry_date:
        ec.last_notified_window = None
    for k, v in data.items():
        setattr(ec, k, v)
    write_training_audit(db, entity_type="CERTIFICATION", entity_id=ec.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(ec)
    return _emp_cert_resp(db, ec)


@router.delete("/employee-certifications/{ec_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_employee_certification(
    ec_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    ec = db.query(EmployeeCertification).filter(EmployeeCertification.id == ec_id, EmployeeCertification.is_deleted == False).first()  # noqa: E712
    if not ec:
        raise HTTPException(404, "Employee certification not found")
    ec.is_deleted = True
    write_training_audit(db, entity_type="CERTIFICATION", entity_id=ec.id,
                         action=TrainingAuditAction.DELETE, actor_id=admin.id)
    db.commit()


@router.get("/employee-certifications/{ec_id}/certificate.pdf")
def export_certificate_pdf(
    ec_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Render a category-themed certificate as a PDF download (WeasyPrint)."""
    ec = db.query(EmployeeCertification).filter(
        EmployeeCertification.id == ec_id, EmployeeCertification.is_deleted == False  # noqa: E712
    ).first()
    if not ec:
        raise HTTPException(404, "Employee certification not found")

    holder = emp_display(db, ec.employee_id)
    category = None
    if ec.certification_id:
        c = db.query(Certification.category).filter(Certification.id == ec.certification_id).first()
        category = c[0] if c else None

    try:
        from app.utils.hr.training.certificate_pdf import render_certificate_pdf
        pdf = render_certificate_pdf(ec, holder, category)
    except OSError as exc:
        # GTK/Pango DLLs missing on this host — surface a clear, actionable error.
        raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run vendor/setup_gtk.py.") from exc

    safe_name = "".join(ch if (ch.isalnum() or ch in " -_") else "" for ch in (ec.name or "credential")).strip()
    safe_name = (safe_name or "credential").replace(" ", "_")
    code = holder.get("code") or "cert"
    fname = f"certificate_{safe_name}_{code}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/employee-certifications/{ec_id}/renew", response_model=EmployeeCertificationResponse)
def renew_employee_certification(
    ec_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Kick off a renewal: flag the cert PENDING_RENEWAL and, when a renewal program
    is configured and no open enrollment exists, create one. Idempotent."""
    ec = db.query(EmployeeCertification).filter(EmployeeCertification.id == ec_id, EmployeeCertification.is_deleted == False).first()  # noqa: E712
    if not ec:
        raise HTTPException(404, "Employee certification not found")
    ec.status = CertificationStatus.PENDING_RENEWAL
    created_assignment = None
    if ec.renewal_training_program_id:
        open_exists = db.query(TrainingAssignment.id).filter(
            TrainingAssignment.employee_id == ec.employee_id,
            TrainingAssignment.program_id == ec.renewal_training_program_id,
            TrainingAssignment.status.in_(
                (TrainingAssignmentStatus.NOT_STARTED, TrainingAssignmentStatus.IN_PROGRESS)
            ),
        ).first()
        if not open_exists:
            a = TrainingAssignment(
                program_id=ec.renewal_training_program_id,
                employee_id=ec.employee_id,
                assigned_date=date.today(),
                due_date=ec.expiry_date,
                status=TrainingAssignmentStatus.NOT_STARTED,
                enrollment_source="COMPLIANCE",
                notes=f"Renewal for certification: {ec.name}",
            )
            db.add(a)
            db.flush()
            created_assignment = a.id
    write_training_audit(db, entity_type="CERTIFICATION", entity_id=ec.id,
                         action=TrainingAuditAction.RENEW, actor_id=admin.id,
                         payload={"assignment_id": str(created_assignment)} if created_assignment else None)
    db.commit()
    db.refresh(ec)
    return _emp_cert_resp(db, ec)
