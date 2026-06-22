"""Travel Reports — server-side report engine.

Public API mirrors ``attendance_reports`` so the router stays symmetrical:

    from app.utils.hr.travel_reports import (
        REPORT_KEYS, report_index, report_meta, columns_for,
        fetch_rows, shape_summary, overview,
        render_pdf, render_excel, render_csv,
    )

Each report owns a unique PDF cover motif (see pdf.py) and a branded,
chart-bearing Excel layout (see excel.py).
"""
from .data import (
    REPORT_KEYS,
    REPORT_META,
    report_index,
    report_meta,
    columns_for,
    fetch_rows,
    shape_summary,
    overview,
    status_color,
)
from .pdf import render_pdf
from .excel import render_excel
from .csv_export import render_csv

__all__ = [
    "REPORT_KEYS", "REPORT_META", "report_index", "report_meta", "columns_for",
    "fetch_rows", "shape_summary", "overview", "status_color",
    "render_pdf", "render_excel", "render_csv",
]
