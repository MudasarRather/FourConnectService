"""HR Exit Management — reports router (prefix /hr/exit/reports).

Registered BEFORE the broad /hr/exit router so /reports/* is never shadowed by
the /{case_id:uuid} catch-all.
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.utils.hr.exit_reports import (
    REPORT_KEYS, report_index, report_meta, overview,
    fetch_rows, shape_summary, columns_public, render_pdf, render_excel, render_csv,
)

router = APIRouter(prefix="/hr/exit/reports", tags=["HR — Exit Reports"])


@router.get("/")
def list_reports(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return {"items": report_index()}


@router.get("/overview")
def reports_overview(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    return overview(db, date_from=date_from, date_to=date_to, department_id=department_id)


@router.get("/{report_key}")
def report_data(
    report_key: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if report_key not in REPORT_KEYS:
        raise HTTPException(404, "Unknown report")
    rows = fetch_rows(db, report_key, date_from=date_from, date_to=date_to, department_id=department_id)
    return {
        "meta": report_meta(report_key),
        "columns": columns_public(report_key),
        "rows": rows,
        "summary": shape_summary(db, report_key, rows),
    }


@router.get("/{report_key}/export")
def export_report(
    report_key: str,
    format: str = Query("pdf", pattern="^(pdf|excel|csv)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if report_key not in REPORT_KEYS:
        raise HTTPException(404, "Unknown report")
    period = ""
    if date_from or date_to:
        period = f"{date_from or '…'} → {date_to or '…'}"
    name = report_meta(report_key).get("name", report_key).replace(" ", "_")
    try:
        if format == "pdf":
            data = render_pdf(db, report_key, date_from=date_from, date_to=date_to, department_id=department_id, period_label=period)
            return Response(content=data, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})
        if format == "excel":
            data = render_excel(db, report_key, date_from=date_from, date_to=date_to, department_id=department_id, period_label=period)
            return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
        data = render_csv(db, report_key, date_from=date_from, date_to=date_to, department_id=department_id, period_label=period)
        return Response(content=data, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    except OSError as exc:
        if "libgobject" in str(exc) or "pango" in str(exc).lower():
            raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run vendor/setup_gtk.py")
        raise
