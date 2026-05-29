"""HR Attendance Reports — server-side PDF + Excel + CSV generation.

Each report (monthly / late / overtime / wfh / compliance / anomalies / daily)
has its OWN unique PDF design and Excel layout — they share data fetching and
shaping but render with distinct visual identities.

Public API:
    fetch_rows(db, from_date, to_date, department_id) -> List[AttRow]
    shape(report_key, rows) -> List[dict]
    shape_summary(rows) -> dict
    render_pdf(report_key, rows, summary, meta) -> bytes
    render_excel(report_key, rows, summary, meta) -> bytes
    render_csv(report_key, rows, summary, meta) -> bytes

The router at app/routers/hr/attendance_reports.py wires these into HTTP
download endpoints. The frontend triggers downloads via plain GETs.
"""
from .data import (
    REPORT_KEYS,
    fetch_rows,
    shape,
    shape_summary,
    report_meta,
)
from .pdf import render_pdf
from .excel import render_excel
from .csv_export import render_csv

__all__ = [
    "REPORT_KEYS",
    "fetch_rows",
    "shape",
    "shape_summary",
    "report_meta",
    "render_pdf",
    "render_excel",
    "render_csv",
]
