"""HR Payroll Reports — server-side PDF + Excel + CSV generation.

Thirteen reports across four groups (Core / Statutory filing / Analytics /
Adjustments). Each has its OWN WeasyPrint cover motif and Excel layout; they
share data fetching, shaping and a money-aware body table.

Public API (consumed by app/routers/hr/payroll_reports.py):

    REPORT_KEYS                              -> tuple[str]
    REPORT_META                              -> dict
    report_meta(key)                         -> dict
    build_context(db, key, year, month, dept)-> ctx for one report's export
    build_full_context(db, year, month, dept)-> ctx covering all reports (preview)
    shape(key, ctx)                          -> list[dict] (table rows)
    shape_summary(key, ctx)                  -> dict (KPI tiles + preview)
    render_pdf / render_excel / render_csv(key, rows, summary, meta) -> bytes
"""
from .data import (
    REPORT_KEYS, REPORT_META, report_meta,
    build_context, build_full_context, shape, shape_summary,
    period_dict, fy_for_period,
)
from .pdf import render_pdf
from .excel import render_excel
from .csv_export import render_csv

__all__ = [
    "REPORT_KEYS", "REPORT_META", "report_meta",
    "build_context", "build_full_context", "shape", "shape_summary",
    "period_dict", "fy_for_period",
    "render_pdf", "render_excel", "render_csv",
]
