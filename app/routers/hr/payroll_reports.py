"""HR Payroll — Tax projection, TDS & compliance summaries, report exports."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.models.hr.payslip import Payslip, PayslipLine
from app.models.hr.salary_component import StatutoryKind
from app.models.hr.tax_document import TaxDocument, TaxDocStatus, TaxDocType
from app.schemas.hr.payroll import (
    TaxProjectionRequest, TaxProjectionResponse, TaxRegimeResult,
    TdsSummaryResponse, ComplianceSummary, ReportIndexResponse,
    Form16GenerateRequest, TaxDocumentResponse, TaxDocumentListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll import load_config, fy_for
from app.utils.hr.payroll.statutory import calc_annual_tds, old_regime_deductions, _dec
from app.utils.hr.payroll.service import fy_for_period
from app.utils.hr.tax_summary import aggregate_statutory
from app.utils.hr.form16_pdf import render_form16_pdf
from app.utils.hr import payroll_reports as pr

router = APIRouter(prefix="/hr/payroll", tags=["HR — Payroll Reports"])

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _emp_name(emp):
    if emp and emp.user:
        return getattr(emp.user, "full_name", None) or getattr(emp.user, "email", None)
    return None


def _fy_month_range(fy: str):
    sy = int(fy.split("-")[0])
    return [(sy, m) for m in range(4, 13)] + [(sy + 1, m) for m in range(1, 4)]


# ─────────────────────────── Tax projection ───────────────────────────

def project_tax_for(db: Session, emp: Employee, annual_gross=None, declarations=None):
    """Project annual income-tax for one employee under both regimes.

    Returns ``(fiscal_year, old_result, new_result, comp)``. Shared by the admin
    endpoint below and the self-service projection endpoint (payroll_self.py) so
    the slab / standard-deduction logic lives in exactly one place. Falls back to
    the employee's CTC for annual_gross and to their saved declarations when the
    caller passes none. Never commits.
    """
    comp = db.query(EmployeeCompensation).filter(
        EmployeeCompensation.employee_id == emp.id, EmployeeCompensation.status == CompensationStatus.ACTIVE,
        EmployeeCompensation.is_deleted == False).order_by(EmployeeCompensation.effective_from.desc()).first()  # noqa: E712

    if not annual_gross:
        if comp and comp.monthly_gross:
            annual_gross = Decimal(str(comp.monthly_gross)) * 12
        elif comp and comp.monthly_ctc:
            annual_gross = Decimal(str(comp.monthly_ctc)) * 12
        elif emp.annual_ctc:
            annual_gross = Decimal(str(emp.annual_ctc))
        else:
            annual_gross = Decimal("0")
    annual_gross = Decimal(str(annual_gross))
    decl = declarations or (comp.tds_declarations if comp else None) or {}

    fy = fy_for(date.today())
    cfg = load_config(db, fy, None)

    def regime_result(regime):
        if regime == "OLD":
            std = _dec(cfg.get("STD_DEDUCTION_OLD"), "50000")
            taxable = max(Decimal("0"), annual_gross - std - old_regime_deductions(decl, cfg))
        else:
            std = _dec(cfg.get("STD_DEDUCTION_NEW"), "75000")
            taxable = max(Decimal("0"), annual_gross - std)
        annual_tax = calc_annual_tds(annual_gross, regime, decl, cfg)
        return TaxRegimeResult(regime=regime, annual_gross=annual_gross, taxable_income=taxable,
                               annual_tax=annual_tax, monthly_tds=(annual_tax / 12).quantize(Decimal("0.01")))

    return fy, regime_result("OLD"), regime_result("NEW"), comp


@router.post("/tax/project", response_model=TaxProjectionResponse)
def project_tax(payload: TaxProjectionRequest, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    fy, old_r, new_r, comp = project_tax_for(db, emp, payload.annual_gross, payload.declarations)
    if payload.save_declarations and comp is not None:
        comp.tds_declarations = payload.declarations  # whole reassignment → tracked by SQLAlchemy
        db.commit()
    return TaxProjectionResponse(
        employee_id=emp.id, employee_name=_emp_name(emp), fiscal_year=fy,
        old_regime=old_r, new_regime=new_r,
        recommended="OLD" if old_r.annual_tax < new_r.annual_tax else "NEW",
    )


# ─────────────────────────── TDS summary ───────────────────────────

@router.get("/tds", response_model=TdsSummaryResponse)
def tds_summary(year: int = Query(...), month: int = Query(...), db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    fy = fy_for_period(year, month)
    fy_months = _fy_month_range(fy)
    # period TDS per employee
    period_rows = (
        db.query(Payslip.employee_id, sa_func.coalesce(sa_func.sum(PayslipLine.amount), 0))
        .join(PayslipLine, PayslipLine.payslip_id == Payslip.id)
        .filter(Payslip.is_deleted == False, Payslip.period_year == year, Payslip.period_month == month,  # noqa: E712
                PayslipLine.statutory_kind == StatutoryKind.TDS)
        .group_by(Payslip.employee_id).all()
    )
    period_map = {eid: Decimal(str(v or 0)) for eid, v in period_rows}
    # YTD per employee across FY
    ytd_rows = (
        db.query(Payslip.employee_id, sa_func.coalesce(sa_func.sum(PayslipLine.amount), 0))
        .join(PayslipLine, PayslipLine.payslip_id == Payslip.id)
        .filter(Payslip.is_deleted == False, PayslipLine.statutory_kind == StatutoryKind.TDS,  # noqa: E712
                sa_func.concat(Payslip.period_year, '-', Payslip.period_month).in_(
                    [f"{y}-{m}" for (y, m) in fy_months]))
        .group_by(Payslip.employee_id).all()
    )
    ytd_map = {eid: Decimal(str(v or 0)) for eid, v in ytd_rows}
    emp_ids = set(period_map) | set(ytd_map)
    emps = {e.id: e for e in db.query(Employee).options(joinedload(Employee.user)).filter(Employee.id.in_(emp_ids)).all()} if emp_ids else {}
    items = []
    for eid in emp_ids:
        e = emps.get(eid)
        items.append({"employee_id": eid, "employee_name": _emp_name(e),
                      "employee_code": e.employee_id if e else None, "pan": e.pan if e else None,
                      "tds_period": period_map.get(eid, Decimal("0")), "tds_ytd": ytd_map.get(eid, Decimal("0"))})
    items.sort(key=lambda x: x["tds_ytd"], reverse=True)
    return {"items": items, "total": len(items), "period_label": f"{_MONTHS[month]} {year}",
            "total_tds_period": sum(period_map.values(), Decimal("0"))}


# ─────────────────────────── Compliance summary ───────────────────────────

def _kind_total(db, year, month, kind):
    v = (db.query(sa_func.coalesce(sa_func.sum(PayslipLine.amount), 0))
         .join(Payslip, PayslipLine.payslip_id == Payslip.id)
         .filter(Payslip.is_deleted == False, Payslip.period_year == year, Payslip.period_month == month,  # noqa: E712
                 PayslipLine.statutory_kind == kind).scalar())
    return Decimal(str(v or 0))


@router.get("/compliance", response_model=ComplianceSummary)
def compliance_summary(year: int = Query(...), month: int = Query(...), db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_superuser)):
    pf_e = _kind_total(db, year, month, StatutoryKind.PF_EMPLOYEE)
    pf_r = _kind_total(db, year, month, StatutoryKind.PF_EMPLOYER)
    esi_e = _kind_total(db, year, month, StatutoryKind.ESI_EMPLOYEE)
    esi_r = _kind_total(db, year, month, StatutoryKind.ESI_EMPLOYER)
    pt = _kind_total(db, year, month, StatutoryKind.PROFESSIONAL_TAX)
    tds = _kind_total(db, year, month, StatutoryKind.TDS)
    count = (db.query(sa_func.count(sa_func.distinct(Payslip.employee_id)))
             .filter(Payslip.is_deleted == False, Payslip.period_year == year, Payslip.period_month == month).scalar()) or 0  # noqa: E712
    return ComplianceSummary(period_label=f"{_MONTHS[month]} {year}", fiscal_year=fy_for_period(year, month),
                             employee_count=count, pf_employee=pf_e, pf_employer=pf_r, esi_employee=esi_e,
                             esi_employer=esi_r, professional_tax=pt, tds=tds,
                             total_statutory=pf_e + pf_r + esi_e + esi_r + pt + tds)


# ─────────────────────────── Reports ───────────────────────────
#
# 13 reports across 4 groups, each rendered to a uniquely-designed PDF
# (WeasyPrint), an ultra-modern Excel workbook (xlsxwriter / openpyxl) and a
# pipeline-friendly CSV. The rendering lives in app/utils/hr/payroll_reports;
# this router just fetches, shapes and streams.

_MIME = {
    "pdf":   "application/pdf",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv":   "text/csv; charset=utf-8",
}
_EXT = {"pdf": "pdf", "excel": "xlsx", "csv": "csv"}


@router.get("/reports", response_model=ReportIndexResponse)
def reports_index(current_user: User = Depends(get_current_superuser)):
    reports = []
    for key in pr.REPORT_KEYS:
        m = pr.REPORT_META[key]
        reports.append({
            "key": key, "name": m["name"], "description": m["subtitle"],
            "tagline": m["tagline"], "subtitle": m["subtitle"], "group": m["group"],
            "icon": m["icon"], "motif": m["motif"], "accent": m["accent"],
            "accent_soft": m["accent_soft"], "accent_deep": m["accent_deep"],
            "formats": ["pdf", "excel", "csv"],
        })
    return {"reports": reports}


@router.get("/reports/preview")
def reports_preview(year: int = Query(...), month: int = Query(...),
                    department_id: Optional[UUID] = None,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    """Live telemetry for the Reports hub: aggregate KPIs for the pay period,
    a per-report record count, and a per-group / per-department breakdown."""
    ctx = pr.build_full_context(db, year, month, department_id)
    slips = ctx["slips"]

    # Per-report counts (cheap — context already fetched once).
    counts = {key: len(pr.shape(key, ctx)) for key in pr.REPORT_KEYS}

    agg = pr.shape_summary("register", ctx)  # slip aggregate covers the headline tiles

    # Department breakdown for the hero strip.
    by_dept: dict[str, dict] = {}
    for r in slips:
        d = r["department"]
        g = by_dept.setdefault(d, {"department": d, "headcount": 0, "net": 0.0, "cost": 0.0})
        g["headcount"] += 1
        g["net"] += r["net"]
        g["cost"] += r["net"] + r["employer_cost"]
    dept_list = sorted(
        ({**g, "net": round(g["net"], 2), "cost": round(g["cost"], 2)} for g in by_dept.values()),
        key=lambda g: -g["cost"],
    )[:8]

    # Group rollup for the plate rail.
    groups: dict[str, int] = {}
    for key in pr.REPORT_KEYS:
        grp = pr.REPORT_META[key]["group"]
        groups[grp] = groups.get(grp, 0) + counts[key]

    period = ctx["period"]
    return {
        "period_label": period["label"],
        "fiscal_year": period["fy"],
        "year": year, "month": month,
        "summary": agg,
        "counts": counts,
        "by_department": dept_list,
        "by_group": groups,
    }


@router.get("/reports/{key}/export")
def export_report(key: str,
                  year: int = Query(...), month: int = Query(...),
                  format: str = Query("pdf"),
                  fmt: Optional[str] = Query(None),
                  department_id: Optional[UUID] = None,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_superuser)):
    """Render ``key`` in ``format`` (pdf|excel|csv) and stream the file.

    ``fmt`` is accepted as a legacy alias for ``format`` (older callers passed
    ``fmt=csv``); ``fmt`` wins when present."""
    fmt_final = (fmt or format or "pdf").lower()
    if fmt_final not in _MIME:
        raise HTTPException(400, f"Unknown format '{fmt_final}' — expected pdf|excel|csv")
    if key not in pr.REPORT_KEYS:
        raise HTTPException(404, f"Unknown report '{key}'")

    ctx = pr.build_context(db, key, year, month, department_id)
    shaped = pr.shape(key, ctx)
    summary = pr.shape_summary(key, ctx)
    meta = {"period": ctx["period"]}

    try:
        if fmt_final == "pdf":
            blob = pr.render_pdf(key, shaped, summary, meta)
        elif fmt_final == "excel":
            blob = pr.render_excel(key, shaped, summary, meta)
        else:
            blob = pr.render_csv(key, shaped, summary, meta)
    except OSError as exc:
        if fmt_final == "pdf" and "libgobject" in str(exc).lower():
            raise HTTPException(
                503, "WeasyPrint can't find GTK DLLs. On Windows, run "
                     "`python vendor/setup_gtk.py` once to install them.") from exc
        raise

    theme = pr.report_meta(key)
    fname = f"Fourreck-{theme['name'].replace(' ', '-')}-{year}-{month:02d}.{_EXT[fmt_final]}"
    return Response(
        content=blob, media_type=_MIME[fmt_final],
        headers={"Content-Disposition": f'attachment; filename="{fname}"; '
                                        f"filename*=UTF-8''{quote(fname)}"},
    )


# ─────────────────────────── Form-16 / tax documents ───────────────────────────

def _tax_doc_out(d: TaxDocument) -> dict:
    return {
        "id": d.id, "employee_id": d.employee_id, "fiscal_year": d.fiscal_year,
        "doc_type": d.doc_type.value if hasattr(d.doc_type, "value") else d.doc_type,
        "title": d.title,
        "status": d.status.value if hasattr(d.status, "value") else d.status,
        "tds_total": d.tds_total, "gross_total": d.gross_total,
        "generated_at": d.generated_at, "published_at": d.published_at,
    }


def build_form16_pdf(db: Session, doc: TaxDocument) -> bytes:
    """Render a Form-16 PDF for a tax-document row. Shared by the admin download
    below and the self-service download (payroll_self.py)."""
    emp = db.query(Employee).options(joinedload(Employee.user)).filter(Employee.id == doc.employee_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    agg = aggregate_statutory(db, emp, doc.fiscal_year)
    _, old_r, new_r, _ = project_tax_for(db, emp)
    emp_regime = agg.get("regime")
    chosen = old_r if emp_regime == "OLD" else (
        new_r if emp_regime == "NEW" else (old_r if old_r.annual_tax < new_r.annual_tax else new_r))
    projection = {"regime": chosen.regime, "annual_gross": chosen.annual_gross,
                  "taxable_income": chosen.taxable_income, "annual_tax": chosen.annual_tax}
    try:
        return render_form16_pdf(
            agg, employee_name=_emp_name(emp) or "Employee",
            employee_code=getattr(emp, "employee_id", "") or "", fiscal_year=doc.fiscal_year,
            projection=projection)
    except OSError as exc:
        if any(s in str(exc).lower() for s in ("libgobject", "libpango", "cannot load library")):
            raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run `python vendor/setup_gtk.py`") from exc
        raise


@router.post("/tax-documents/generate", response_model=TaxDocumentResponse)
def generate_form16(payload: Form16GenerateRequest, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    fy = payload.fiscal_year or fy_for(date.today())
    agg = aggregate_statutory(db, emp, fy)
    doc = db.query(TaxDocument).filter(
        TaxDocument.employee_id == emp.id, TaxDocument.fiscal_year == fy,
        TaxDocument.doc_type == TaxDocType.FORM16, TaxDocument.is_deleted == False).first()  # noqa: E712
    now = datetime.now(timezone.utc)
    if not doc:
        doc = TaxDocument(employee_id=emp.id, fiscal_year=fy, doc_type=TaxDocType.FORM16,
                          generated_by_id=current_user.id)
        db.add(doc)
    doc.title = f"Form 16 · FY {fy}"
    doc.tds_total = agg["tds"]
    doc.gross_total = agg["gross"]
    doc.generated_at = now
    doc.status = TaxDocStatus.PUBLISHED if payload.publish else TaxDocStatus.DRAFT
    doc.published_at = now if payload.publish else None
    db.commit(); db.refresh(doc)
    return _tax_doc_out(doc)


@router.get("/tax-documents", response_model=TaxDocumentListResponse)
def list_tax_documents(employee_id: Optional[UUID] = None, fiscal_year: Optional[str] = None,
                       db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    q = db.query(TaxDocument).filter(TaxDocument.is_deleted == False)  # noqa: E712
    if employee_id:
        q = q.filter(TaxDocument.employee_id == employee_id)
    if fiscal_year:
        q = q.filter(TaxDocument.fiscal_year == fiscal_year)
    rows = q.order_by(TaxDocument.fiscal_year.desc(), TaxDocument.created_at.desc()).all()
    return {"items": [_tax_doc_out(d) for d in rows], "total": len(rows)}


@router.get("/tax-documents/{doc_id}/pdf")
def download_tax_document(doc_id: UUID, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_superuser)):
    doc = db.query(TaxDocument).filter(TaxDocument.id == doc_id, TaxDocument.is_deleted == False).first()  # noqa: E712
    if not doc:
        raise HTTPException(404, "Tax document not found")
    pdf = build_form16_pdf(db, doc)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Form16-{doc.fiscal_year}.pdf"'})
