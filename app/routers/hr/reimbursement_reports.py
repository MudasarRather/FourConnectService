"""HR Reimbursements — report index + exports (PDF / Excel / CSV)."""
from __future__ import annotations

from datetime import date
from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.hr.reimbursements import ReportIndexResponse
from app.utils.dependencies import get_current_superuser
from app.utils.hr import reimbursement_reports as rr

router = APIRouter(prefix="/hr/reimbursements/reports", tags=["HR — Reimbursement Reports"])

_MIME = {
    "pdf": "application/pdf",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}
_EXT = {"pdf": "pdf", "excel": "xlsx", "csv": "csv"}


@router.get("/", response_model=ReportIndexResponse)
def reports_index(current_user: User = Depends(get_current_superuser)):
    items = []
    for key in rr.REPORT_KEYS:
        m = rr.report_meta(key)
        items.append({"key": key, "name": m["name"], "description": m.get("description")})
    return {"items": items}


@router.get("/{key}/export")
def export_report(
    key: str,
    format: str = Query("pdf"),
    fmt: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    category_id: Optional[UUID] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    fmt_final = (fmt or format or "pdf").lower()
    if fmt_final not in _MIME:
        raise HTTPException(400, f"Unknown format '{fmt_final}' — expected pdf|excel|csv")
    if key not in rr.REPORT_KEYS:
        raise HTTPException(404, f"Unknown report '{key}'")

    rows = rr.fetch_rows(db, date_from=date_from, date_to=date_to, category_id=category_id, status=status)
    shaped = rr.shape(key, rows)

    try:
        if fmt_final == "pdf":
            blob = rr.render_pdf(key, shaped)
        elif fmt_final == "excel":
            blob = rr.render_excel(key, shaped)
        else:
            blob = rr.render_csv(key, shaped)
    except OSError as exc:
        if fmt_final == "pdf" and "libgobject" in str(exc).lower():
            raise HTTPException(
                503, "WeasyPrint can't find GTK DLLs. On Windows, run "
                     "`python vendor/setup_gtk.py` once to install them.") from exc
        raise

    meta = rr.report_meta(key)
    fname = f"Fourreck-{meta['name'].replace(' ', '-')}-{date.today().isoformat()}.{_EXT[fmt_final]}"
    return Response(
        content=blob, media_type=_MIME[fmt_final],
        headers={"Content-Disposition": f'attachment; filename="{fname}"; '
                                        f"filename*=UTF-8''{quote(fname)}"},
    )
