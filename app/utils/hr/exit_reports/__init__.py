"""HR Exit Management — reports package (mirrors travel_reports).

Public API:
    REPORT_KEYS, report_index, report_meta, overview,
    fetch_rows, shape_summary, columns_for,
    render_pdf, render_excel, render_csv
"""
from app.utils.hr.exit_reports.data import (
    REPORT_KEYS, report_index, report_meta, overview,
    fetch_rows, shape_summary, columns_for, columns_public, status_color,
)
from app.utils.hr.exit_reports.pdf import render_pdf
from app.utils.hr.exit_reports.excel import render_excel
from app.utils.hr.exit_reports.csv_export import render_csv

__all__ = [
    "REPORT_KEYS", "report_index", "report_meta", "overview",
    "fetch_rows", "shape_summary", "columns_for", "columns_public", "status_color",
    "render_pdf", "render_excel", "render_csv",
]
