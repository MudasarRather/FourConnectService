"""HR Training & Development — report index + exports (CSV / Excel / PDF)."""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.utils.hr.training_reports import (
    REPORTS, REPORT_KEYS, build_report, render_csv, render_excel, render_pdf,
)

router = APIRouter(prefix="/hr/training", tags=["HR — Training Reports"])


@router.get("/reports")
def list_reports(
    _admin: User = Depends(get_current_superuser),
):
    return {"reports": REPORTS}


@router.get("/reports/{report_key}/export")
def export_report(
    report_key: str,
    format: str = Query("csv", pattern="^(csv|excel|pdf)$"),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if report_key not in REPORT_KEYS:
        raise HTTPException(404, f"Unknown report '{report_key}'")
    filters = {"from": date_from, "to": date_to, "department_id": department_id}
    report = build_report(db, report_key, filters)
    stamp = date.today().isoformat()
    fname = f"training_{report_key}_{stamp}"

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
    # pdf
    try:
        pdf = render_pdf(report, report_key)
    except OSError as e:
        # GTK/Pango libs missing on this host — surface a clear 503 (see CLAUDE.md).
        raise HTTPException(503, f"PDF rendering unavailable (WeasyPrint can't find GTK DLLs): {e}")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'},
    )
