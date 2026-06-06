"""HR Payroll — Self-service payslips (employee views/downloads own slips).

Ownership-enforced: an employee only ever sees their OWN RELEASED payslips.
A payslip belonging to someone else returns 404 (no enumeration).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.payslip import Payslip, PayslipStatus
from app.models.hr.payroll_config import PayrollAuditAction
from app.schemas.hr.payroll import (
    PayslipResponse, MyPayslipListResponse, MyAnnualEarnings,
)
from app.utils.dependencies import get_current_user
from app.utils.hr.payroll.service import write_audit, fy_for_period
from app.routers.hr.payslips import enrich_payslip_detail

router = APIRouter(prefix="/hr/me/payslips", tags=["HR — My Payslips"])

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _try_self_employee(db: Session, user: User) -> Optional[Employee]:
    return db.query(Employee).filter(Employee.user_id == user.id,
                                     Employee.is_deleted == False).first()  # noqa: E712


def _own_payslip(db: Session, emp: Employee, payslip_id: UUID) -> Payslip:
    s = db.query(Payslip).options(joinedload(Payslip.employee), joinedload(Payslip.lines)).filter(
        Payslip.id == payslip_id, Payslip.is_deleted == False,  # noqa: E712
        Payslip.employee_id == emp.id, Payslip.status == PayslipStatus.RELEASED).first()
    if not s:
        raise HTTPException(404, "Payslip not found")
    return s


@router.get("/", response_model=MyPayslipListResponse)
def my_payslips(year: Optional[int] = None, skip: int = 0, limit: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True}
    q = db.query(Payslip).filter(Payslip.employee_id == emp.id, Payslip.is_deleted == False,  # noqa: E712
                                 Payslip.status == PayslipStatus.RELEASED)
    if year:
        q = q.filter(Payslip.period_year == year)
    total = q.count()
    rows = q.order_by(Payslip.period_year.desc(), Payslip.period_month.desc()).offset(skip).limit(limit).all()
    items = [{
        "id": s.id, "payslip_no": s.payslip_no, "period_month": s.period_month,
        "period_year": s.period_year, "status": s.status, "gross_earnings": s.gross_earnings,
        "total_deductions": s.total_deductions, "net_pay": s.net_pay, "employee_id": s.employee_id,
        "employee_name": None, "employee_code": emp.employee_id, "department_name": None,
    } for s in rows]
    return {"items": items, "total": total, "unlinked": False}


@router.get("/annual", response_model=MyAnnualEarnings)
def my_annual(fy: Optional[str] = None, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        return MyAnnualEarnings(fiscal_year=fy or "", months=[], total_gross=Decimal(0),
                                total_net=Decimal(0), total_deductions=Decimal(0), unlinked=True)
    fy = fy or fy_for_period(date.today().year, date.today().month)
    start_year = int(fy.split("-")[0])
    # FY months: Apr (start_year) .. Mar (start_year+1)
    seq = [(start_year, m) for m in range(4, 13)] + [(start_year + 1, m) for m in range(1, 4)]
    rows = db.query(Payslip).filter(Payslip.employee_id == emp.id, Payslip.is_deleted == False,  # noqa: E712
                                    Payslip.status == PayslipStatus.RELEASED).all()
    by_key = {(r.period_year, r.period_month): r for r in rows}
    months = []
    tg = tn = td = Decimal(0)
    for (y, m) in seq:
        r = by_key.get((y, m))
        g = r.gross_earnings if r else Decimal(0)
        n = r.net_pay if r else Decimal(0)
        d = r.total_deductions if r else Decimal(0)
        tg += g; tn += n; td += d
        months.append({"month": m, "year": y, "label": _MONTHS[m],
                       "gross": str(g), "net": str(n), "deductions": str(d)})
    return MyAnnualEarnings(fiscal_year=fy, months=months, total_gross=tg, total_net=tn,
                            total_deductions=td, unlinked=False)


@router.get("/{payslip_id}", response_model=PayslipResponse)
def my_payslip(payslip_id: UUID, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        raise HTTPException(404, "Payslip not found")
    s = _own_payslip(db, emp, payslip_id)
    return enrich_payslip_detail(s, db)


@router.get("/{payslip_id}/pdf")
def my_payslip_pdf(payslip_id: UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        raise HTTPException(404, "Payslip not found")
    s = _own_payslip(db, emp, payslip_id)
    detail = enrich_payslip_detail(s, db)
    try:
        from app.utils.hr.payslip_pdf import render_payslip_pdf
        pdf = render_payslip_pdf(s, employee_name=detail["employee_name"] or "Employee",
                                 employee_code=detail["employee_code"] or "",
                                 department=detail["department_name"], designation=detail["designation_name"])
    except OSError as e:
        if "libgobject" in str(e) or "libpango" in str(e) or "cannot load library" in str(e):
            raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run `python vendor/setup_gtk.py`")
        raise
    write_audit(db, entity_type="PAYSLIP", entity_id=s.id, action=PayrollAuditAction.PAYSLIP_DOWNLOAD,
                batch_id=s.batch_id, actor_id=current_user.id)
    db.commit()
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{s.payslip_no}.pdf"'})
