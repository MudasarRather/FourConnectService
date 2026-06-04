"""HR Leave Reports — server-side CSV + Excel + PDF generation.

Public API (mirrors attendance_reports for consistency):
    REPORT_KEYS, report_meta, fetch_rows, shape, shape_summary,
    render_pdf, render_excel, render_csv

Each report key has its own data shape but they all share one branded PDF
cover and one xlsxwriter workbook scaffold — keeps the module compact while
still producing flagship-quality output.
"""
from .data import (
    REPORT_KEYS,
    REPORT_META,
    fetch_rows,
    shape,
    shape_summary,
    report_meta,
)
from .csv_export import render_csv
from .excel import render_excel
from .pdf import render_pdf

__all__ = [
    "REPORT_KEYS",
    "REPORT_META",
    "fetch_rows",
    "shape",
    "shape_summary",
    "report_meta",
    "render_csv",
    "render_excel",
    "render_pdf",
]
