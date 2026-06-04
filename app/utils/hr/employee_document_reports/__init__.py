"""HR Employee-Document Reports — server-side PDF + CSV generation.

Each report (expired / pending / expiring / compliance / verification /
category) shares data fetching + shaping but renders with an ultra-modern
WeasyPrint cover (alert / radar / digest / feature motifs).

Public API:
    REPORT_KEYS
    fetch_rows(db, department_id) -> list[dict]
    shape(report_key, rows)       -> list[dict]
    shape_summary(rows)           -> dict
    report_meta(key)              -> dict
    render_pdf(report_key, rows, summary, meta) -> bytes
    render_csv(report_key, rows, summary, meta) -> bytes
"""
from .data import (
    REPORT_KEYS,
    SUMMARY_KEYS,
    fetch_rows,
    shape,
    shape_summary,
    report_meta,
)
from .pdf import render_pdf
from .csv_export import render_csv

__all__ = [
    "REPORT_KEYS",
    "SUMMARY_KEYS",
    "fetch_rows",
    "shape",
    "shape_summary",
    "report_meta",
    "render_pdf",
    "render_csv",
]
