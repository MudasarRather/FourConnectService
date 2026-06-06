"""CSV export for HR Payroll Reports — UTF-8 with BOM, RFC-4180 quoting.

Unstyled, pipeline-friendly. Reuses the same column order as the PDF body
table (``columns.body_columns``) so the CSV and PDF never drift, but writes
raw numeric values (no ₹ prefix, no grouping) so spreadsheets can sum them.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, date as date_cls

from .columns import body_columns
from .common import COMPANY
from .data import report_meta


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date_cls):
        return v.isoformat()
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        return f"{v:.2f}"
    return v


def render_csv(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta(report_key)
    cols = body_columns(report_key)
    period = meta.get("period") or {}

    buf = io.StringIO()
    buf.write(f"# {COMPANY['legal']} — {theme['name']}\n")
    buf.write(f"# Pay period: {period.get('label', '')}  ·  FY {period.get('fy', '')}\n")
    buf.write(f"# Generated: {datetime.now().isoformat(timespec='seconds')}\n")
    buf.write(f"# Rows: {len(shaped_rows)}  ·  Employees: {summary.get('employees', summary.get('rows', 0))}\n#\n")

    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([c["label"] for c in cols])
    for row in shaped_rows:
        writer.writerow([_fmt(row.get(c["key"])) for c in cols])

    return ("﻿" + buf.getvalue()).encode("utf-8")
