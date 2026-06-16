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
from app.models.hr.employee import Employee, TaxRegime
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.models.hr.payslip import Payslip, PayslipStatus
from app.models.hr.payroll_config import PayrollAuditAction
from app.models.hr.tax_document import TaxDocument, TaxDocStatus
from app.schemas.hr.payroll import (
    PayslipResponse, MyPayslipListResponse, MyAnnualEarnings,
    MyTaxSummary, MyTaxDeclarations, MyTaxProjectionRequest,
    TaxProjectionResponse, TaxDocumentListResponse,
)
from app.utils.dependencies import get_current_user
from app.utils.hr.payroll.service import write_audit, fy_for_period
from app.utils.hr.tax_summary import aggregate_statutory
from app.routers.hr.payslips import enrich_payslip_detail
from app.routers.hr.payroll_reports import project_tax_for, build_form16_pdf, _tax_doc_out

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


# ─────────────────────────── My tax documents (statutory / Form-16) ───────────────────────────
# NOTE: these literal routes MUST stay declared BEFORE `/{payslip_id}` below, or
# a request to `/tax-summary` would match the UUID path param and 422.

def _latest_comp(db: Session, emp: Employee):
    return db.query(EmployeeCompensation).filter(
        EmployeeCompensation.employee_id == emp.id, EmployeeCompensation.status == CompensationStatus.ACTIVE,
        EmployeeCompensation.is_deleted == False).order_by(  # noqa: E712
            EmployeeCompensation.effective_from.desc()).first()


# Form-12BB declaration heads we persist/project (keys mirror the tax engine).
_DECL_KEYS = ("sec_80c", "sec_80ccd_1b", "sec_80d", "sec_80e", "sec_80g",
              "sec_80tta", "home_loan_interest", "hra_exemption", "lta_exemption")


@router.get("/tax-summary", response_model=MyTaxSummary)
def my_tax_summary(fy: Optional[str] = None, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        return MyTaxSummary(fiscal_year=fy or "", unlinked=True)
    fy = fy or fy_for_period(date.today().year, date.today().month)
    a = aggregate_statutory(db, emp, fy)
    comp = _latest_comp(db, emp)
    regime = (comp.tax_regime.value if comp and comp.tax_regime else a["regime"])
    return MyTaxSummary(
        fiscal_year=fy, regime=regime, pan=a["pan"], uan=a["uan"],
        pf_number=a["pf_number"], esic_number=a["esic_number"],
        tds=a["tds"], pf_employee=a["pf_employee"], pf_employer=a["pf_employer"],
        esi_employee=a["esi_employee"], esi_employer=a["esi_employer"],
        professional_tax=a["professional_tax"], lwf=a["lwf"],
        gross=a["gross"], total_deductions=a["total_deductions"], net_pay=a["net_pay"],
        slips_count=a["slips_count"], months=a["months"],
        declarations=(comp.tds_declarations if comp else None), unlinked=False)


@router.post("/tax-projection", response_model=TaxProjectionResponse)
def my_tax_projection(body: MyTaxProjectionRequest = MyTaxProjectionRequest(),
                      db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        raise HTTPException(404, "No employee profile linked")
    fy, old_r, new_r, _ = project_tax_for(db, emp, body.annual_gross, body.declarations)
    return TaxProjectionResponse(
        employee_id=emp.id, employee_name=None, fiscal_year=fy, old_regime=old_r, new_regime=new_r,
        recommended="OLD" if old_r.annual_tax < new_r.annual_tax else "NEW")


@router.post("/tax-declarations", response_model=TaxProjectionResponse)
def my_save_declarations(body: MyTaxDeclarations, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        raise HTTPException(404, "No employee profile linked")
    comp = _latest_comp(db, emp)
    if not comp:
        raise HTTPException(400, "No active compensation on file — ask HR to set up your salary first.")
    decl = {k: float(getattr(body, k) or 0) for k in _DECL_KEYS}
    comp.tds_declarations = decl   # whole reassignment → tracked by SQLAlchemy
    if body.tax_regime in ("OLD", "NEW"):
        comp.tax_regime = TaxRegime(body.tax_regime)
    db.commit()
    fy, old_r, new_r, _ = project_tax_for(db, emp, None, decl)
    return TaxProjectionResponse(
        employee_id=emp.id, employee_name=None, fiscal_year=fy, old_regime=old_r, new_regime=new_r,
        recommended="OLD" if old_r.annual_tax < new_r.annual_tax else "NEW")


@router.get("/tax-documents", response_model=TaxDocumentListResponse)
def my_tax_documents(fy: Optional[str] = None, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True}
    q = db.query(TaxDocument).filter(
        TaxDocument.employee_id == emp.id, TaxDocument.is_deleted == False,  # noqa: E712
        TaxDocument.status == TaxDocStatus.PUBLISHED)
    if fy:
        q = q.filter(TaxDocument.fiscal_year == fy)
    rows = q.order_by(TaxDocument.fiscal_year.desc(), TaxDocument.created_at.desc()).all()
    return {"items": [_tax_doc_out(d) for d in rows], "total": len(rows), "unlinked": False}


@router.get("/tax-documents/{doc_id}/pdf")
def my_tax_document_pdf(doc_id: UUID, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    emp = _try_self_employee(db, current_user)
    if not emp:
        raise HTTPException(404, "Tax document not found")
    doc = db.query(TaxDocument).filter(
        TaxDocument.id == doc_id, TaxDocument.is_deleted == False,  # noqa: E712
        TaxDocument.employee_id == emp.id, TaxDocument.status == TaxDocStatus.PUBLISHED).first()
    if not doc:
        raise HTTPException(404, "Tax document not found")
    pdf = build_form16_pdf(db, doc)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Form16-{doc.fiscal_year}.pdf"'})


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
