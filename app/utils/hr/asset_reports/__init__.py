"""HR Asset Management — report builders + exporters.

Sixteen reports across the asset estate, each exportable as CSV / Excel
(openpyxl, branded two-sheet workbook) / PDF (WeasyPrint, per-report cover
motif). A report is a uniform shape so the renderers stay generic:

    {key, title, subtitle, eyebrow, columns, rows, summary, period}

Public API (import from the package, never the submodules):
    REPORTS, REPORT_KEYS, REPORT_META, report_meta, build_report, build_overview,
    render_csv, render_excel, render_pdf
"""
from __future__ import annotations

from .data import REPORTS, REPORT_KEYS, REPORT_META, report_meta, build_report, build_overview
from .csv_export import render_csv
from .excel import render_excel
from .pdf import render_pdf

__all__ = [
    "REPORTS", "REPORT_KEYS", "REPORT_META", "report_meta", "build_report", "build_overview",
    "render_csv", "render_excel", "render_pdf",
]
