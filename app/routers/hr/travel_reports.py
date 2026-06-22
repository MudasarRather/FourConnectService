"""HR Travel — reports (index + overview + server-side CSV / Excel / PDF export).

Read-only analytics over travel requests, bookings, DA, advances and settlements.
The data + rendering live in ``app.utils.hr.travel_reports`` (mirrors the
attendance_reports package): each report owns a unique WeasyPrint cover motif and
a branded, chart-bearing Excel layout. The cinematic "Dispatch Bureau" UI lives
on the frontend and is fed by ``/overview``.
"""
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
from app.utils.hr.travel_reports import (
    REPORT_KEYS, report_index, report_meta, columns_for,
    fetch_rows, shape_summary, overview,
    render_pdf, render_excel, render_csv,
)

router = APIRouter(prefix="/hr/travel/reports", tags=["HR — Travel Reports"])

_MEDIA = {
    "csv": "text/csv",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
_EXT = {"csv": "csv", "excel": "xlsx", "pdf": "pdf"}


def _period(df: Optional[date], dt: Optional[date]) -> str:
    if df and dt:
        return f"{df.strftime('%d %b %Y')} → {dt.strftime('%d %b %Y')}"
    if df:
        return f"From {df.strftime('%d %b %Y')}"
    if dt:
        return f"Until {dt.strftime('%d %b %Y')}"
    return "All time"


@router.get("/")
def list_reports(current_user: User = Depends(get_current_superuser)):
    """Deck index — key/name/group/desc/accent/motif/icon per report."""
    return {"items": report_index()}


@router.get("/overview")
def reports_overview(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    """Single aggregate for the console — KPIs, per-report counts, status mix,
    monthly throughput and top departments. With no dates it spans ALL records
    (so future-dated / out-of-window tours still surface)."""
    df, dt = date_from, date_to
    if df and dt and dt < df:
        df, dt = dt, df
    return overview(db, df, dt, department_id)


@router.get("/{report_key}")
def report_data(
    report_key: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    """JSON view of a single report (columns + shaped rows + KPI summary)."""
    if report_key not in REPORT_KEYS:
        raise HTTPException(404, "Unknown report")
    rows = fetch_rows(db, report_key, date_from, date_to, department_id)
    summary = shape_summary(report_key, rows)
    cols = [{"label": c["label"], "key": c["key"], "align": c["align"],
             "fmt": c.get("fmt"), "status": bool(c.get("status"))} for c in columns_for(report_key)]
    return {"meta": report_meta(report_key), "columns": cols, "rows": rows, "summary": summary}


@router.get("/{report_key}/export")
def export_report(
    report_key: str,
    format: str = Query("pdf", pattern="^(csv|excel|pdf)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    if report_key not in REPORT_KEYS:
        raise HTTPException(404, "Unknown report")
    rows = fetch_rows(db, report_key, date_from, date_to, department_id)
    summary = shape_summary(report_key, rows)
    meta_arg = {"period": _period(date_from, date_to)}

    try:
        if format == "pdf":
            blob = render_pdf(report_key, rows, summary, meta_arg)
        elif format == "excel":
            blob = render_excel(report_key, rows, summary, meta_arg)
        else:
            blob = render_csv(report_key, rows, summary, meta_arg)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # WeasyPrint GTK / render failures → 503 with detail
        raise HTTPException(503, f"Report render failed: {e}")

    name = report_meta(report_key)["name"].replace(" ", "-").replace("—", "-")
    fname = f"Fourreck-{name}.{_EXT[format]}"
    return Response(content=blob, media_type=_MEDIA[format],
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
