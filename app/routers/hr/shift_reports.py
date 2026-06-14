"""HR Shift Reports — export + preview endpoints.

GET /hr/shifts/reports/preview                 → JSON KPIs + per-report row counts
GET /hr/shifts/reports/{report_key}/export     → PDF | Excel | CSV download
"""
from __future__ import annotations

from datetime import date as date_cls
from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.utils.hr.shift_reports import (
    REPORT_KEYS, REPORT_META, report_meta, build_report,
    render_pdf, render_excel, render_csv,
)

router = APIRouter(prefix="/hr/shifts/reports", tags=["HR — Shift Reports"])

MIME_MAP = {
    "pdf": "application/pdf",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}
EXT_MAP = {"pdf": "pdf", "excel": "xlsx", "csv": "csv"}
MAX_RANGE_DAYS = 400


def _validate_range(frm: date_cls, to: date_cls):
    if to < frm:
        raise HTTPException(400, "`to` must be on or after `from`")
    if (to - frm).days > MAX_RANGE_DAYS:
        raise HTTPException(400, f"Range too wide — max {MAX_RANGE_DAYS} days")


@router.get("/catalog")
def reports_catalog(_admin: User = Depends(get_current_superuser)):
    """Static metadata for the report cards (name/tagline/accent/motif)."""
    return {"reports": [{"key": k, **REPORT_META[k]} for k in REPORT_KEYS]}


@router.get("/preview")
def reports_preview(
    from_: date_cls = Query(..., alias="from"),
    to: date_cls = Query(...),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Per-report row counts + headline KPI so the UI shows real numbers per card."""
    _validate_range(from_, to)
    counts, summaries = {}, {}
    for key in REPORT_KEYS:
        try:
            built = build_report(db, key, from_, to, department_id)
            counts[key] = built["summary"].get("rows", len(built["rows"]))
            summaries[key] = built["summary"]
        except Exception:
            counts[key] = 0
            summaries[key] = {}
    return {
        "from": from_.isoformat(), "to": to.isoformat(),
        "days": (to - from_).days + 1,
        "counts": counts, "summaries": summaries,
    }


@router.get("/{report_key}/export")
def export_report(
    report_key: str,
    format: str = Query("pdf", pattern="^(pdf|excel|csv)$"),
    from_: date_cls = Query(..., alias="from"),
    to: date_cls = Query(...),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if report_key not in REPORT_KEYS:
        raise HTTPException(404, f"Unknown report '{report_key}' — expected one of {list(REPORT_KEYS)}")
    _validate_range(from_, to)

    built = build_report(db, report_key, from_, to, department_id)
    rows, summary = built["rows"], built["summary"]
    meta = {"period": {"from": from_, "to": to}}

    try:
        if format == "pdf":
            blob = render_pdf(report_key, rows, summary, meta)
        elif format == "excel":
            blob = render_excel(report_key, rows, summary, meta)
        else:
            blob = render_csv(report_key, rows, summary, meta)
    except OSError as exc:
        if format == "pdf" and "libgobject" in str(exc).lower():
            raise HTTPException(
                503, "WeasyPrint can't find GTK DLLs. On Windows, run "
                     "`python vendor/setup_gtk.py` once to install them.") from exc
        raise

    theme = report_meta(report_key)
    filename = f"Fourreck-Shifts-{theme['name'].replace(' ', '-')}-{from_}-to-{to}.{EXT_MAP[format]}"
    disposition = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(content=blob, media_type=MIME_MAP[format],
                    headers={"Content-Disposition": disposition})
