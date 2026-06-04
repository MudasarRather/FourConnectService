"""HR Employee-Document Reports — preview + download endpoints.

    GET /api/hr/employee-documents/reports/preview
        ?department_id=<uuid>
        → JSON { summary, counts } driving the live report cards

    GET /api/hr/employee-documents/reports/{report_key}/export
        ?format=pdf|csv&department_id=<uuid>
        → File download (ultra-modern WeasyPrint PDF, or CSV)

Report keys: expired, pending, expiring, compliance, verification, category.
Reports are point-in-time snapshots (no date range) — they reflect the current
active document estate.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.utils.hr.employee_document_reports import (
    REPORT_KEYS, fetch_rows, shape, shape_summary, report_meta,
    render_pdf, render_csv,
)


router = APIRouter(prefix="/hr/employee-documents/reports", tags=["HR — Employee Document Reports"])

MIME_MAP = {"pdf": "application/pdf", "csv": "text/csv; charset=utf-8"}
EXT_MAP = {"pdf": "pdf", "csv": "csv"}


@router.get("/preview")
def reports_preview(
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Per-report row counts + estate summary for the report cards."""
    rows = fetch_rows(db, department_id)
    summary = shape_summary(rows)
    counts = {key: len(shape(key, rows)) for key in REPORT_KEYS}
    return {"summary": summary, "counts": counts}


@router.get("/{report_key}/export")
def export_report(
    report_key: str,
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if report_key not in REPORT_KEYS:
        raise HTTPException(
            404, f"Unknown report '{report_key}' — expected one of {list(REPORT_KEYS)}"
        )

    rows = fetch_rows(db, department_id)
    shaped = shape(report_key, rows)
    summary = shape_summary(rows)
    meta = {"generated_at": None}

    try:
        if format == "pdf":
            blob = render_pdf(report_key, shaped, summary, meta)
        else:
            blob = render_csv(report_key, shaped, summary, meta)
    except OSError as exc:
        if format == "pdf" and "libgobject" in str(exc).lower():
            raise HTTPException(
                503,
                "WeasyPrint can't find GTK DLLs. On Windows, run "
                "`python vendor/setup_gtk.py` once to install them.",
            ) from exc
        raise

    theme = report_meta(report_key)
    filename = f"Fourreck-{theme['name'].replace(' ', '-')}.{EXT_MAP[format]}"
    content_disposition = (
        f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename)}'
    )
    return Response(
        content=blob,
        media_type=MIME_MAP[format],
        headers={"Content-Disposition": content_disposition},
    )
