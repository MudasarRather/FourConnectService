"""HR Training & Development — report builders + exporters.

Eleven reports (one per major Training tab), each exportable as CSV / Excel
(openpyxl, two-sheet branded workbook) / PDF (WeasyPrint, a unique cover motif
per report). A report is a uniform shape:

    {key, title, subtitle, eyebrow, columns, rows, summary, period}

so the renderers stay generic. WeasyPrint is imported lazily (GTK at import
time) — see CLAUDE.md.

Public API (import from the package, never the submodules):
    REPORTS, REPORT_KEYS, REPORT_META, report_meta, build_report,
    render_csv, render_excel, render_pdf
"""
from __future__ import annotations

from .data import (
    REPORTS, REPORT_KEYS, REPORT_META, report_meta, build_report,
    SELF_REPORTS, SELF_REPORT_KEYS, SELF_REPORT_META, build_self_report,
)
from .csv_export import render_csv
from .excel import render_excel
from .pdf import render_pdf

__all__ = [
    "REPORTS", "REPORT_KEYS", "REPORT_META", "report_meta", "build_report",
    "SELF_REPORTS", "SELF_REPORT_KEYS", "SELF_REPORT_META", "build_self_report",
    "render_csv", "render_excel", "render_pdf",
]
