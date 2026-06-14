"""HR Shift Reports — server-side PDF + Excel + CSV generation.

Six reports, each with its OWN magazine-style PDF cover and a tailored Excel
sheet, mapping 1:1 to the Shifts workspace pages:
    roster · coverage · overtime · night · rotation · workforce

Public API:
    REPORT_KEYS
    report_meta(key) -> dict
    build_report(db, key, from, to, department_id) -> {"rows": [...], "summary": {...}}
    render_pdf(key, rows, summary, meta) -> bytes
    render_excel(key, rows, summary, meta) -> bytes
    render_csv(key, rows, summary, meta) -> bytes
"""
from .data import REPORT_KEYS, REPORT_META, report_meta, build_report
from .pdf import render_pdf
from .excel import render_excel
from .csv_export import render_csv

__all__ = [
    "REPORT_KEYS", "REPORT_META", "report_meta", "build_report",
    "render_pdf", "render_excel", "render_csv",
]
