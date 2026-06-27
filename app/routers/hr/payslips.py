"""HR Payroll — Payslips (admin explorer + PDF)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.payslip import Payslip, PayslipStatus
from app.models.hr.payroll_config import PayrollAuditAction
from app.schemas.hr.payroll import PayslipResponse, PayslipListResponse, PayslipHoldBody
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll.service import write_audit

router = APIRouter(prefix="/hr/payroll/payslips", tags=["HR — Payslips"])


def _emp_label(emp: Employee):
    if emp and emp.user:
        return getattr(emp.user, "full_name", None) or getattr(emp.user, "email", None)
    return None


def enrich_payslip_detail(s: Payslip, db: Session) -> dict:
    emp = s.employee
    dept = desg = None
    if emp:
        if emp.department_id:
            d = db.query(Department.name).filter(Department.id == emp.department_id).first()
            dept = d[0] if d else None
        if emp.designation_id:
            dg = db.query(Designation.name).filter(Designation.id == emp.designation_id).first()
            desg = dg[0] if dg else None
    base = {k: getattr(s, k) for k in (
        "id", "batch_id", "employee_id", "payslip_no", "period_month", "period_year", "status",
        "working_days", "lop_days", "paid_days", "tax_regime", "gross_earnings", "total_deductions",
        "net_pay", "employer_contributions", "ctc_value", "encashment_amount", "bank_name",
        "account_number", "ifsc", "pan", "uan", "remarks", "created_at")}
    base["lines"] = sorted(s.lines, key=lambda x: x.sequence)
    base["employee_name"] = _emp_label(emp)
    base["employee_code"] = emp.employee_id if emp else None
    base["department_name"] = dept
    base["designation_name"] = desg
    return base


@router.get("/", response_model=PayslipListResponse)
def list_payslips(employee_id: Optional[UUID] = None, year: Optional[int] = None,
                  month: Optional[int] = None, status: Optional[PayslipStatus] = None,
                  skip: int = 0, limit: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    q = db.query(Payslip).options(joinedload(Payslip.employee)).filter(Payslip.is_deleted == False)  # noqa: E712
    if employee_id:
        q = q.filter(Payslip.employee_id == employee_id)
    if year:
        q = q.filter(Payslip.period_year == year)
    if month:
        q = q.filter(Payslip.period_month == month)
    if status:
        q = q.filter(Payslip.status == status)
    total = q.count()
    rows = q.order_by(Payslip.period_year.desc(), Payslip.period_month.desc()).offset(skip).limit(limit).all()
    items = [{
        "id": s.id, "payslip_no": s.payslip_no, "period_month": s.period_month,
        "period_year": s.period_year, "status": s.status, "gross_earnings": s.gross_earnings,
        "total_deductions": s.total_deductions, "net_pay": s.net_pay, "employee_id": s.employee_id,
        "employee_name": _emp_label(s.employee), "employee_code": s.employee.employee_id if s.employee else None,
        "department_name": None,
    } for s in rows]
    return {"items": items, "total": total}


@router.get("/{payslip_id}", response_model=PayslipResponse)
def get_payslip(payslip_id: UUID, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    s = db.query(Payslip).options(joinedload(Payslip.employee), joinedload(Payslip.lines)).filter(
        Payslip.id == payslip_id, Payslip.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Payslip not found")
    return enrich_payslip_detail(s, db)


@router.get("/{payslip_id}/pdf")
def payslip_pdf(payslip_id: UUID, password: Optional[str] = None, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    s = db.query(Payslip).options(joinedload(Payslip.employee), joinedload(Payslip.lines)).filter(
        Payslip.id == payslip_id, Payslip.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Payslip not found")
    detail = enrich_payslip_detail(s, db)
    try:
        from app.utils.hr.payslip_pdf import render_payslip_pdf
        pdf = render_payslip_pdf(s, employee_name=detail["employee_name"] or "Employee",
                                 employee_code=detail["employee_code"] or "",
                                 department=detail["department_name"], designation=detail["designation_name"],
                                 password=password)
    except OSError as e:
        if "libgobject" in str(e) or "libpango" in str(e) or "cannot load library" in str(e):
            raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run `python vendor/setup_gtk.py`")
        raise
    s.pdf_generated_at = datetime.now(timezone.utc)
    write_audit(db, entity_type="PAYSLIP", entity_id=s.id, action=PayrollAuditAction.PAYSLIP_DOWNLOAD,
                batch_id=s.batch_id, actor_id=current_user.id)
    db.commit()
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{s.payslip_no}.pdf"'})


@router.post("/{payslip_id}/hold", response_model=PayslipResponse)
def hold_payslip(payslip_id: UUID, body: PayslipHoldBody, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_superuser)):
    s = db.query(Payslip).filter(Payslip.id == payslip_id, Payslip.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Payslip not found")
    if s.status == PayslipStatus.RELEASED:
        raise HTTPException(409, "Released payslips cannot be held — return the run first")
    from_status = s.status.value if s.status else None
    s.status = PayslipStatus.HELD
    reason = body.reason.strip()
    extra = (body.note or "").strip()
    s.remarks = f"HELD: {reason}" + (f" — {extra}" if extra else "")
    write_audit(db, entity_type="PAYSLIP", entity_id=s.id, action=PayrollAuditAction.UPDATE,
                batch_id=s.batch_id, actor_id=current_user.id, from_status=from_status,
                to_status="HELD", note=reason)
    db.commit()
    db.refresh(s)
    return enrich_payslip_detail(s, db)


@router.post("/{payslip_id}/release", response_model=PayslipResponse)
def release_payslip(payslip_id: UUID, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    s = db.query(Payslip).filter(Payslip.id == payslip_id, Payslip.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Payslip not found")
    s.status = PayslipStatus.RELEASED
    write_audit(db, entity_type="PAYSLIP", entity_id=s.id, action=PayrollAuditAction.RELEASE,
                batch_id=s.batch_id, actor_id=current_user.id, to_status="RELEASED")
    db.commit()
    try:
        from app.utils.hr.notify import dispatch
        uid = s.employee.user_id if s.employee else None
        dispatch(db, "PAYSLIP_RELEASED", uid, context={
            "title": "Payslip released",
            "message": f"Your payslip for {int(s.period_month):02d}/{s.period_year} is now available.",
            "action_url": "/user/self-service/payslips",
        })
        db.commit()
    except Exception:
        db.rollback()
    db.refresh(s)
    return enrich_payslip_detail(s, db)
