"""HR Attendance Reports — download endpoints.

Two endpoints per report:

    GET /api/hr/attendance/reports/preview
        ?from=YYYY-MM-DD&to=YYYY-MM-DD&department_id=<uuid>
        → JSON { rows, summary } — drives the live preview KPIs

    GET /api/hr/attendance/reports/{report_key}/export
        ?format=pdf|excel|csv&from=...&to=...&department_id=...
        → File download (FileResponse-ish, returned as Response with proper
          Content-Disposition + Content-Type).

The seven report keys are: monthly, late, overtime, wfh, compliance,
anomalies, daily. Each one delegates to ``app.utils.hr.attendance_reports``
which builds a uniquely-designed PDF or Excel.
"""
from __future__ import annotations

from datetime import date as date_cls, timedelta
from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.utils.hr.attendance_reports import (
    REPORT_KEYS, fetch_rows, shape, shape_summary, report_meta,
    render_pdf, render_excel, render_csv,
)


router = APIRouter(prefix="/hr/attendance/reports", tags=["HR — Attendance Reports"])


MAX_RANGE_DAYS = 366  # one year max


def _validate_range(from_date: date_cls, to_date: date_cls) -> None:
    if from_date > to_date:
        raise HTTPException(400, "`from` must be on or before `to`")
    if (to_date - from_date).days > MAX_RANGE_DAYS:
        raise HTTPException(
            400, f"Date range too wide — limit is {MAX_RANGE_DAYS} days "
                 f"(got {(to_date - from_date).days}). Run multiple smaller reports."
        )


MIME_MAP = {
    "pdf":   "application/pdf",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv":   "text/csv; charset=utf-8",
}
EXT_MAP = {"pdf": "pdf", "excel": "xlsx", "csv": "csv"}


@router.get("/preview")
def reports_preview(
    from_: date_cls = Query(..., alias="from"),
    to:   date_cls = Query(...),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """JSON preview used by the reports landing page.

    Returns the raw row count + per-report shaped counts + summary KPIs,
    so the UI can show real numbers per card without needing the full
    row dump on the wire.
    """
    _validate_range(from_, to)
    rows = fetch_rows(db, from_, to, department_id)
    summary = shape_summary(rows)

    counts = {}
    for key in REPORT_KEYS:
        shaped = shape(key, rows)
        counts[key] = len(shaped)

    # Department coverage strip
    by_dept = {}
    for r in rows:
        d = r["department"]
        if d == "—":
            continue
        if d not in by_dept:
            by_dept[d] = {"department": d, "present": 0, "total": 0}
        if r["status"] in ("PRESENT", "LATE", "WFH", "REMOTE", "HALF_DAY"):
            by_dept[d]["present"] += 1
        by_dept[d]["total"] += 1
    dept_list = []
    for d in by_dept.values():
        d["coverage"] = round((d["present"] / d["total"]) * 100) if d["total"] else 0
        dept_list.append(d)
    dept_list.sort(key=lambda d: -d["coverage"])

    return {
        "from": from_.isoformat(),
        "to": to.isoformat(),
        "days": (to - from_).days + 1,
        "summary": summary,
        "counts": counts,
        "by_department": dept_list[:8],
    }


@router.get("/{report_key}/export")
def export_report(
    report_key: str,
    format: str = Query("pdf", pattern="^(pdf|excel|csv)$"),
    from_: date_cls = Query(..., alias="from"),
    to:   date_cls = Query(...),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Generate the requested report in the requested format and stream it."""
    if report_key not in REPORT_KEYS:
        raise HTTPException(
            404, f"Unknown report '{report_key}' — expected one of {list(REPORT_KEYS)}"
        )
    _validate_range(from_, to)

    rows = fetch_rows(db, from_, to, department_id)
    shaped = shape(report_key, rows)
    summary = shape_summary(rows)
    meta = {"period": {"from": from_, "to": to}}

    try:
        if format == "pdf":
            blob = render_pdf(report_key, shaped, summary, meta)
        elif format == "excel":
            blob = render_excel(report_key, shaped, summary, meta)
        else:  # csv
            blob = render_csv(report_key, shaped, summary, meta)
    except OSError as exc:
        # WeasyPrint can't find GTK DLLs on Windows. Surface the real cause
        # so the caller knows to run vendor/setup_gtk.py.
        if format == "pdf" and "libgobject" in str(exc).lower():
            raise HTTPException(
                503,
                "WeasyPrint can't find GTK DLLs. On Windows, run "
                "`python vendor/setup_gtk.py` once to install them.",
            ) from exc
        raise

    theme = report_meta(report_key)
    filename = f"Fourreck-{theme['name'].replace(' ', '-')}-{from_}-to-{to}.{EXT_MAP[format]}"
    # RFC 5987 — quote the filename so spaces / accents survive HTTP transit
    content_disposition = (
        f'attachment; filename="{filename}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=blob,
        media_type=MIME_MAP[format],
        headers={"Content-Disposition": content_disposition},
    )
