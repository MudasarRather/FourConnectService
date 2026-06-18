"""HR Training & Development — Self-service (employee + manager) endpoints.

All reads resolve the caller to their Employee via ``try_self_employee`` and
return a graceful unlinked shape when there is no linked profile (mirrors the
reimbursements self router). Mutations are ownership-guarded.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.training import (
    TrainingProgram, TrainingAssignment, TrainingAssignmentStatus,
)
from app.models.hr.certification import EmployeeCertification, CertificationStatus
from app.models.hr.skill import Skill, EmployeeSkill
from app.models.hr.training_request import TrainingRequest, TrainingRequestStatus
from app.models.hr.training_material import TrainingMaterial
from app.models.hr.training_feedback import TrainingFeedback
from app.models.hr.trainer import Trainer
from app.models.hr.training_audit_log import TrainingAuditAction

from app.schemas.hr.training import TrainingAssignmentResponse
from app.schemas.hr.skill import EmployeeSkillResponse
from app.schemas.hr.training_request import (
    TrainingRequestResponse, TrainingRequestCreate, TrainingRequestDecideInput,
)
from app.schemas.hr.training_material import TrainingMaterialResponse
from app.schemas.hr.training_feedback import TrainingFeedbackCreate
from app.schemas.hr.training_audit import MyTrainingSummary

from app.utils.hr.training.service import (
    try_self_employee, resolve_self_employee, generate_request_number, emp_display,
)
from app.utils.hr.training.flow import assert_assignment_transition, complete_assignment
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.request_ops import (
    to_request_response, submit_request, decide_request,
)
from app.utils.hr.training_reports import (
    SELF_REPORTS, SELF_REPORT_KEYS, build_self_report,
    render_csv, render_excel, render_pdf,
)
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/hr/me/training", tags=["HR — My Training"])

_OPEN = (TrainingAssignmentStatus.NOT_STARTED, TrainingAssignmentStatus.IN_PROGRESS)


# ───────────────────────────── enrollments ─────────────────────────────

def _assignment_resp(db: Session, a: TrainingAssignment) -> TrainingAssignmentResponse:
    p = db.query(TrainingProgram).filter(TrainingProgram.id == a.program_id).first()
    disp = emp_display(db, a.employee_id)
    return TrainingAssignmentResponse(
        id=a.id, program_id=a.program_id, program_name=p.name if p else None,
        program_type=p.training_type if p else None, employee_id=a.employee_id,
        employee_name=disp.get("name"), process_id=a.process_id,
        assigned_date=a.assigned_date, due_date=a.due_date, completion_date=a.completion_date,
        status=a.status, score=a.score, certification_url=a.certification_url, notes=a.notes,
        feedback_submitted=bool(a.feedback_submitted),
    )


@router.get("/")
def my_training(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, current_user)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True}
    rows = db.query(TrainingAssignment).filter(
        TrainingAssignment.employee_id == emp.id,
    ).order_by(TrainingAssignment.due_date.asc().nullslast(), TrainingAssignment.created_at.desc()).all()
    return {
        "items": [_assignment_resp(db, a).model_dump() for a in rows],
        "total": len(rows), "unlinked": False,
    }


@router.get("/summary", response_model=MyTrainingSummary)
def my_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, current_user)
    if not emp:
        return MyTrainingSummary(unlinked=True)
    today = date.today()
    base = db.query(TrainingAssignment).filter(TrainingAssignment.employee_id == emp.id)
    assigned = base.count()
    in_progress = base.filter(TrainingAssignment.status == TrainingAssignmentStatus.IN_PROGRESS).count()
    completed = base.filter(TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED).count()
    overdue = base.filter(
        TrainingAssignment.status.in_(_OPEN),
        TrainingAssignment.due_date.isnot(None),
        TrainingAssignment.due_date < today,
    ).count()
    certs_active = db.query(EmployeeCertification).filter(
        EmployeeCertification.employee_id == emp.id,
        EmployeeCertification.is_deleted == False,  # noqa: E712
        EmployeeCertification.status.in_([
            CertificationStatus.ACTIVE, CertificationStatus.EXPIRING_SOON, CertificationStatus.PENDING_RENEWAL,
        ]),
    ).count()
    certs_expiring = db.query(EmployeeCertification).filter(
        EmployeeCertification.employee_id == emp.id,
        EmployeeCertification.is_deleted == False,  # noqa: E712
        EmployeeCertification.status == CertificationStatus.EXPIRING_SOON,
    ).count()
    pending_requests = db.query(TrainingRequest).filter(
        TrainingRequest.employee_id == emp.id, TrainingRequest.is_deleted == False,  # noqa: E712
        TrainingRequest.status == TrainingRequestStatus.PENDING_APPROVAL,
    ).count()
    from sqlalchemy import func
    avg_gap = db.query(func.avg(EmployeeSkill.gap)).filter(
        EmployeeSkill.employee_id == emp.id, EmployeeSkill.gap.isnot(None),
    ).scalar()
    return MyTrainingSummary(
        unlinked=False, assigned=assigned, in_progress=in_progress, completed=completed,
        overdue=overdue, certifications_active=certs_active, certifications_expiring=certs_expiring,
        pending_requests=pending_requests,
        avg_skill_gap=round(float(avg_gap), 2) if avg_gap is not None else None,
    )


@router.patch("/{assignment_id}/progress", response_model=TrainingAssignmentResponse)
def update_my_progress(
    assignment_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = resolve_self_employee(db, current_user)
    a = db.query(TrainingAssignment).filter(TrainingAssignment.id == assignment_id).first()
    if not a or a.employee_id != emp.id:
        raise HTTPException(404, "Training not found")
    target = (payload or {}).get("status")
    if target not in (TrainingAssignmentStatus.IN_PROGRESS.value, TrainingAssignmentStatus.COMPLETED.value):
        raise HTTPException(422, "You can only mark a training In Progress or Completed")
    if target == TrainingAssignmentStatus.COMPLETED.value:
        complete_assignment(db, a, actor_id=current_user.id)
    else:
        assert_assignment_transition(a.status, TrainingAssignmentStatus.IN_PROGRESS)
        prev = a.status
        a.status = TrainingAssignmentStatus.IN_PROGRESS
        write_training_audit(db, entity_type="ASSIGNMENT", entity_id=a.id,
                             action=TrainingAuditAction.UPDATE, actor_id=current_user.id,
                             from_status=prev.value, to_status=a.status.value)
    db.commit()
    db.refresh(a)
    return _assignment_resp(db, a)


# ───────────────────────────── my certifications ─────────────────────────────

@router.get("/certifications")
def my_certifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, current_user)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True}
    rows = db.query(EmployeeCertification).filter(
        EmployeeCertification.employee_id == emp.id,
        EmployeeCertification.is_deleted == False,  # noqa: E712
    ).order_by(EmployeeCertification.expiry_date.asc().nullslast()).all()
    out = []
    for ec in rows:
        days = (ec.expiry_date - date.today()).days if ec.expiry_date else None
        out.append({
            "id": str(ec.id), "name": ec.name, "issuing_authority": ec.issuing_authority,
            "certificate_number": ec.certificate_number,
            "issue_date": ec.issue_date.isoformat() if ec.issue_date else None,
            "expiry_date": ec.expiry_date.isoformat() if ec.expiry_date else None,
            "status": ec.status.value, "days_to_expiry": days,
            "certificate_url": ec.certificate_url,
        })
    return {"items": out, "total": len(out), "unlinked": False}


# NOTE: certifications are HR-issued only. There is deliberately NO self-service
# create endpoint — the org's certification register must stay authoritative
# (it feeds compliance coverage, the department scorecard and reports). HR awards
# credentials via the admin Certifications "Award" flow, and certs auto-mint when
# an employee completes a certification-required training. Employees can only VIEW
# (GET /certifications above) and export their Credential Portfolio report.


# ───────────────────────────── my skills ─────────────────────────────

@router.get("/skills")
def my_skills(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, current_user)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True}
    rows = (
        db.query(EmployeeSkill, Skill)
        .join(Skill, Skill.id == EmployeeSkill.skill_id)
        .filter(EmployeeSkill.employee_id == emp.id)
        .order_by(EmployeeSkill.gap.desc().nullslast())
        .all()
    )
    out = []
    for es, sk in rows:
        out.append({
            "id": str(es.id), "skill_id": str(es.skill_id), "skill_name": sk.name,
            "skill_category": sk.category.value, "current_level": es.current_level,
            "required_level": es.required_level, "gap": es.gap, "max_level": sk.max_level,
        })
    return {"items": out, "total": len(out), "unlinked": False}


# ───────────────────────────── my requests ─────────────────────────────

@router.get("/requests")
def my_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, current_user)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True}
    rows = db.query(TrainingRequest).filter(
        TrainingRequest.employee_id == emp.id, TrainingRequest.is_deleted == False,  # noqa: E712
    ).order_by(TrainingRequest.created_at.desc()).all()
    return {"items": [to_request_response(db, r) for r in rows], "total": len(rows), "unlinked": False}


@router.post("/requests", response_model=TrainingRequestResponse, status_code=http_status.HTTP_201_CREATED)
def create_my_request(
    payload: TrainingRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = resolve_self_employee(db, current_user)
    data = payload.model_dump(exclude={"employee_id"})
    req = TrainingRequest(
        request_number=generate_request_number(db),
        employee_id=emp.id,
        status=TrainingRequestStatus.DRAFT,
        created_by_id=current_user.id,
        **data,
    )
    db.add(req)
    db.flush()
    write_training_audit(db, entity_type="REQUEST", entity_id=req.id,
                         action=TrainingAuditAction.CREATE, actor_id=current_user.id)
    # Submit immediately (the primary self-service action).
    submit_request(db, req, emp, current_user)
    db.commit()
    db.refresh(req)
    return to_request_response(db, req)


@router.post("/requests/{request_id}/submit", response_model=TrainingRequestResponse)
def submit_my_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = resolve_self_employee(db, current_user)
    req = db.query(TrainingRequest).filter(TrainingRequest.id == request_id, TrainingRequest.is_deleted == False).first()  # noqa: E712
    if not req or req.employee_id != emp.id:
        raise HTTPException(404, "Request not found")
    submit_request(db, req, emp, current_user)
    db.commit()
    db.refresh(req)
    return to_request_response(db, req)


@router.delete("/requests/{request_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def withdraw_my_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = resolve_self_employee(db, current_user)
    req = db.query(TrainingRequest).filter(TrainingRequest.id == request_id, TrainingRequest.is_deleted == False).first()  # noqa: E712
    if not req or req.employee_id != emp.id:
        raise HTTPException(404, "Request not found")
    if req.status not in (TrainingRequestStatus.DRAFT, TrainingRequestStatus.PENDING_APPROVAL, TrainingRequestStatus.RETURNED):
        raise HTTPException(409, "This request can no longer be withdrawn")
    req.status = TrainingRequestStatus.CANCELLED
    write_training_audit(db, entity_type="REQUEST", entity_id=req.id,
                         action=TrainingAuditAction.CANCEL, actor_id=current_user.id,
                         to_status=TrainingRequestStatus.CANCELLED.value)
    db.commit()


# ───────────────────────────── manager approval queue ─────────────────────────────

@router.get("/requests/approval-queue")
def approval_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Requests awaiting THIS user at their active MANAGER stage."""
    rows = db.query(TrainingRequest).filter(
        TrainingRequest.is_deleted == False,  # noqa: E712
        TrainingRequest.status == TrainingRequestStatus.PENDING_APPROVAL,
    ).order_by(TrainingRequest.submitted_at.desc().nullslast()).all()
    mine = []
    uid = str(current_user.id)
    for r in rows:
        steps = r.approval_steps or []
        if 0 <= r.current_step < len(steps):
            step = steps[r.current_step]
            if step.get("approver_type") == "MANAGER" and step.get("approver_user_id") == uid:
                mine.append(to_request_response(db, r))
            elif current_user.is_superuser and step.get("approver_type") == "HR":
                mine.append(to_request_response(db, r))
    return {"items": mine, "total": len(mine)}


@router.patch("/requests/{request_id}/decide", response_model=TrainingRequestResponse)
def decide_my_queue(
    request_id: UUID,
    payload: TrainingRequestDecideInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    req = (
        db.query(TrainingRequest)
        .filter(TrainingRequest.id == request_id, TrainingRequest.is_deleted == False)  # noqa: E712
        .with_for_update(of=TrainingRequest)
        .first()
    )
    if not req:
        raise HTTPException(404, "Request not found")
    decide_request(db, req, current_user, payload.decision, payload.notes)
    db.commit()
    db.refresh(req)
    return to_request_response(db, req)


# ───────────────────────────── feedback + materials ─────────────────────────────

@router.post("/{assignment_id}/feedback", status_code=http_status.HTTP_201_CREATED)
def submit_feedback(
    assignment_id: UUID,
    payload: TrainingFeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = resolve_self_employee(db, current_user)
    a = db.query(TrainingAssignment).filter(TrainingAssignment.id == assignment_id).first()
    if not a or a.employee_id != emp.id:
        raise HTTPException(404, "Training not found")
    existing = db.query(TrainingFeedback).filter(
        TrainingFeedback.assignment_id == assignment_id,
        TrainingFeedback.employee_id == emp.id,
    ).first()
    if existing:
        raise HTTPException(409, "You have already submitted feedback for this training")
    data = payload.model_dump()
    data["assignment_id"] = assignment_id
    data["program_id"] = data.get("program_id") or a.program_id
    f = TrainingFeedback(employee_id=emp.id, **data)
    db.add(f)
    a.feedback_submitted = True
    # Roll up trainer rating
    if f.trainer_id and f.trainer_rating:
        tr = db.query(Trainer).filter(Trainer.id == f.trainer_id).first()
        if tr:
            n = int(tr.rating_count or 0)
            cur = float(tr.rating_avg or 0)
            tr.rating_avg = round((cur * n + float(f.trainer_rating)) / (n + 1), 2)
            tr.rating_count = n + 1
    write_training_audit(db, entity_type="FEEDBACK", entity_id=a.id,
                         action=TrainingAuditAction.FEEDBACK, actor_id=current_user.id)
    db.commit()
    return {"ok": True}


@router.get("/materials")
def my_materials(
    program_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, current_user)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True}
    # programs the employee is enrolled in
    prog_ids = [r[0] for r in db.query(TrainingAssignment.program_id).filter(
        TrainingAssignment.employee_id == emp.id,
    ).distinct().all()]
    if program_id:
        prog_ids = [p for p in prog_ids if p == program_id]
    if not prog_ids:
        return {"items": [], "total": 0, "unlinked": False}
    rows = db.query(TrainingMaterial).filter(
        TrainingMaterial.is_deleted == False,  # noqa: E712
        TrainingMaterial.is_active == True,     # noqa: E712
        TrainingMaterial.program_id.in_(prog_ids),
    ).order_by(TrainingMaterial.sort_order.asc().nullslast()).all()
    out = [{
        "id": str(m.id), "program_id": str(m.program_id) if m.program_id else None,
        "title": m.title, "material_type": m.material_type.value,
        "external_url": m.external_url, "file_url": m.file_url,
        "drive_document_id": str(m.drive_document_id) if m.drive_document_id else None,
        "description": m.description,
    } for m in rows]
    return {"items": out, "total": len(out), "unlinked": False}


# ───────────────────────────── my reports (PDF / Excel / CSV) ─────────────────────────────

@router.get("/reports")
def my_reports_list(
    current_user: User = Depends(get_current_user),
):
    """Catalog of the employee's personal learning reports (always available —
    the export endpoint is the one that needs a linked profile)."""
    return {"reports": SELF_REPORTS}


@router.get("/reports/{report_key}/export")
def export_my_report(
    report_key: str,
    format: str = Query("pdf", pattern="^(csv|excel|pdf)$"),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if report_key not in SELF_REPORT_KEYS:
        raise HTTPException(404, f"Unknown report '{report_key}'")
    emp = resolve_self_employee(db, current_user)  # 404 if account isn't linked
    disp = emp_display(db, emp.id)
    filters = {
        "employee_id": emp.id, "employee_name": disp.get("name"), "employee_code": disp.get("code"),
        "from": date_from, "to": date_to,
    }
    report = build_self_report(db, report_key, filters)
    fname = f"my_{report_key}_{date.today().isoformat()}"

    if format == "csv":
        return Response(
            content=render_csv(report), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'},
        )
    if format == "excel":
        return Response(
            content=render_excel(report),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'},
        )
    try:
        pdf = render_pdf(report, report_key)
    except OSError as e:
        raise HTTPException(503, f"PDF rendering unavailable (WeasyPrint can't find GTK DLLs): {e}")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'},
    )
