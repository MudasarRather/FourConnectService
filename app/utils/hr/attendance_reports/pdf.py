"""WeasyPrint PDF designs for HR Attendance Reports.

Seven reports, seven distinct visual identities. Each report has its own
cover page motif (editorial / bulletin / industrial / postcard / certificate
/ dossier / blueprint) while sharing a base typography stack and a clean
data-table style for the body pages.

Design philosophy:
    * Covers do the heavy lifting on identity — bold typography, motif-specific
      decoration (newsroom rules, dashboard gauges, postcard stamps, etc.)
    * Data tables stay legible. Tinted headers + status pills + alternating
      bands per accent. Bias toward clarity over decoration on body pages.
    * All measurements in `mm` and `pt` — no `px`, so PDF rendering is crisp
      at any zoom and prints cleanly to A4.

Implementation:
    * One Python file, multiple cover-renderers selected by ``meta['motif']``.
    * The table HTML is shared across reports — column sets vary by key.
    * CSS lives in a single Jinja-free string so updates stay localized.

Public entry: ``render_pdf(report_key, shaped_rows, summary, meta) -> bytes``
"""
from __future__ import annotations

import html
import os
from datetime import datetime, date as date_cls, time as time_cls
from pathlib import Path
from typing import Any, Iterable

# WeasyPrint imports are deferred until render time so the backend can boot
# even on a Windows machine that hasn't run vendor/setup_gtk.py yet.

from .data import report_meta, STATUS_COLORS


COMPANY = {
    "name": "Fourreck",
    "legal": "Fourreck Technologies Pvt. Ltd.",
    "address_1": "4th Floor, Innovation Tower",
    "address_2": "Hyderabad, Telangana 500032, India",
    "cin": "U72200TG2020PTC123456",
    "gst": "36ABCFR1234X1ZK",
    "email": "hr@fourreck.com",
    "web": "fourreck.com",
}


# ════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ════════════════════════════════════════════════════════════════════════════


def _esc(v: Any) -> str:
    if v is None:
        return ""
    return html.escape(str(v))


def _fmt_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except ValueError:
            return d
    return d.strftime("%d %b %Y")


def _fmt_long_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except ValueError:
            return d
    return d.strftime("%A, %d %B %Y")


def _fmt_time(t) -> str:
    if not t:
        return ""
    if isinstance(t, datetime):
        return t.strftime("%I:%M %p").lstrip("0")
    if isinstance(t, time_cls):
        return t.strftime("%I:%M %p").lstrip("0")
    return _esc(t)


def _fmt_hours(v) -> str:
    return f"{float(v or 0):.2f} h"


def _fmt_mins(v) -> str:
    return f"{int(v or 0)} min"


def _fmt_pct(v) -> str:
    return f"{int(v or 0)}%"


# ════════════════════════════════════════════════════════════════════════════
# Column definitions per report
# ════════════════════════════════════════════════════════════════════════════


def _columns(key: str) -> list[dict]:
    """Returns column descriptors used by the body table.

    align:  left|right|center
    fmt:    name of formatter in FORMATTERS (below) or None for raw
    status: True → render as status pill
    pill_for_value: optional lambda returning (label, color_key) for highlighting
    """
    if key == "monthly":
        return [
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Dept", "key": "department", "align": "left"},
            {"label": "Pre", "key": "present_days", "align": "right"},
            {"label": "Late", "key": "late_days", "align": "right", "warn_if": lambda v: v > 0},
            {"label": "Half", "key": "half_days", "align": "right"},
            {"label": "Abs", "key": "absent_days", "align": "right", "danger_if": lambda v: v > 0},
            {"label": "LWP", "key": "lwp_days", "align": "right", "warn_if": lambda v: v > 0},
            {"label": "Leave", "key": "leave_days", "align": "right", "good_if": lambda v: v > 0},
            {"label": "WFH", "key": "wfh_days", "align": "right"},
            {"label": "LOP", "key": "lop_days", "align": "right", "danger_if": lambda v: v > 0},
            {"label": "Payable", "key": "payable_days", "align": "right"},
            {"label": "Work hrs", "key": "total_working_hours", "align": "right", "fmt": "hours"},
            {"label": "Break hrs", "key": "total_break_hours", "align": "right", "fmt": "hours"},
            {"label": "Excess brk", "key": "excess_break_minutes", "align": "right", "fmt": "mins", "warn_if": lambda v: v > 0},
            {"label": "OT hrs", "key": "total_overtime_hours", "align": "right", "fmt": "hours", "good_if": lambda v: v > 0},
            {"label": "Late", "key": "total_late_minutes", "align": "right", "fmt": "mins", "warn_if": lambda v: v > 0},
            {"label": "Att %", "key": "attendance_pct", "align": "right", "fmt": "pct"},
        ]
    if key == "late":
        return [
            {"label": "Date", "key": "date", "align": "left", "fmt": "date"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Department", "key": "department", "align": "left"},
            {"label": "Shift", "key": "shift_name", "align": "left"},
            {"label": "Check-in", "key": "check_in_time", "align": "left", "fmt": "time"},
            {"label": "Late", "key": "late_minutes", "align": "right", "fmt": "mins", "warn_if": lambda v: v > 0, "danger_if": lambda v: v > 30},
            {"label": "Status", "key": "status", "align": "left", "status": True},
        ]
    if key == "overtime":
        return [
            {"label": "Date", "key": "date", "align": "left", "fmt": "date"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Department", "key": "department", "align": "left"},
            {"label": "Shift", "key": "shift_name", "align": "left"},
            {"label": "Check-out", "key": "check_out_time", "align": "left", "fmt": "time"},
            {"label": "OT hrs", "key": "overtime_hours", "align": "right", "fmt": "hours", "good_if": lambda v: v > 0},
            {"label": "Working hrs", "key": "working_hours", "align": "right", "fmt": "hours"},
            {"label": "Status", "key": "status", "align": "left", "status": True},
        ]
    if key == "wfh":
        return [
            {"label": "Date", "key": "date", "align": "left", "fmt": "date"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Department", "key": "department", "align": "left"},
            {"label": "Check-in", "key": "check_in_time", "align": "left", "fmt": "time"},
            {"label": "Check-out", "key": "check_out_time", "align": "left", "fmt": "time"},
            {"label": "Working hrs", "key": "working_hours", "align": "right", "fmt": "hours"},
            {"label": "Status", "key": "status", "align": "left", "status": True},
        ]
    if key == "compliance":
        return [
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Department", "key": "department", "align": "left"},
            {"label": "Shift", "key": "shift_name", "align": "left"},
            {"label": "Scheduled", "key": "scheduled_days", "align": "right"},
            {"label": "Actual hrs", "key": "actual_hours", "align": "right", "fmt": "hours"},
            {"label": "Expected hrs", "key": "expected_hours", "align": "right", "fmt": "hours"},
            {"label": "Coverage", "key": "coverage_pct", "align": "right", "fmt": "pct",
             "danger_if": lambda v: v < 80, "warn_if": lambda v: v < 95, "good_if": lambda v: v >= 100},
            {"label": "Missing", "key": "missing_punch_days", "align": "right", "danger_if": lambda v: v > 0},
        ]
    if key == "anomalies":
        return [
            {"label": "Date", "key": "date", "align": "left", "fmt": "date"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Department", "key": "department", "align": "left"},
            {"label": "Status", "key": "status", "align": "left", "status": True},
            {"label": "In", "key": "check_in_time", "align": "left", "fmt": "time"},
            {"label": "Out", "key": "check_out_time", "align": "left", "fmt": "time"},
            {"label": "Late", "key": "late_minutes", "align": "right", "fmt": "mins", "danger_if": lambda v: v > 30},
            {"label": "Reasons", "key": "reasons", "align": "left"},
        ]
    if key == "breaks":
        return [
            {"label": "Date", "key": "date", "align": "left", "fmt": "date"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Department", "key": "department", "align": "left"},
            {"label": "Shift", "key": "shift_name", "align": "left"},
            {"label": "Working hrs", "key": "working_hours", "align": "right", "fmt": "hours"},
            {"label": "Break hrs", "key": "break_hours", "align": "right", "fmt": "hours",
             "warn_if": lambda v: v and v > 1.0, "danger_if": lambda v: v and v > 1.5},
            {"label": "Break mins", "key": "break_minutes", "align": "right", "fmt": "mins"},
            {"label": "Ratio", "key": "break_ratio_pct", "align": "right", "fmt": "pct",
             "warn_if": lambda v: v and v > 15, "danger_if": lambda v: v and v > 25},
            {"label": "Length", "key": "intensity", "align": "left"},
        ]
    # daily
    return [
        {"label": "Date", "key": "date", "align": "left", "fmt": "date"},
        {"label": "Code", "key": "employee_code", "align": "left"},
        {"label": "Employee", "key": "employee_name", "align": "left"},
        {"label": "Department", "key": "department", "align": "left"},
        {"label": "Shift", "key": "shift_name", "align": "left"},
        {"label": "In", "key": "check_in_time", "align": "left", "fmt": "time"},
        {"label": "Out", "key": "check_out_time", "align": "left", "fmt": "time"},
        {"label": "Hrs", "key": "working_hours", "align": "right", "fmt": "hours"},
        {"label": "Brk", "key": "break_hours", "align": "right", "fmt": "hours"},
        {"label": "Late", "key": "late_minutes", "align": "right", "fmt": "mins", "warn_if": lambda v: v > 0},
        {"label": "OT", "key": "overtime_hours", "align": "right", "fmt": "hours", "good_if": lambda v: v > 0},
        {"label": "Status", "key": "status", "align": "left", "status": True},
    ]


FORMATTERS = {
    "date": _fmt_date,
    "time": _fmt_time,
    "hours": _fmt_hours,
    "mins": _fmt_mins,
    "pct": _fmt_pct,
}


def _cell(row: dict, col: dict) -> tuple[str, str]:
    """Returns (html, css_class) for a body cell."""
    raw = row.get(col["key"])
    fmt = col.get("fmt")
    if col.get("status"):
        status = raw or ""
        sc = STATUS_COLORS.get(status, {"light": "#f1f5f9", "hex": "#475569", "deep": "#334155"})
        pill = (
            f'<span class="status-pill" '
            f'style="background:{sc["light"]};color:{sc["deep"]};border:1px solid {sc["hex"]}33">'
            f'{_esc(status.replace("_", " "))}</span>'
        )
        return pill, ""

    if fmt:
        val = FORMATTERS[fmt](raw)
    elif raw is None or raw == "":
        val = "—"
    else:
        val = _esc(raw)

    klass = ""
    if col.get("danger_if") and col["danger_if"](raw or 0):
        klass = "cell-danger"
    elif col.get("warn_if") and col["warn_if"](raw or 0):
        klass = "cell-warn"
    elif col.get("good_if") and col["good_if"](raw or 0):
        klass = "cell-good"
    return _esc(val) if not fmt else val, klass


# ════════════════════════════════════════════════════════════════════════════
# Shared CSS & body table
# ════════════════════════════════════════════════════════════════════════════


# Single base sheet, then each cover motif appends its own block.
_BASE_CSS = """
@page {
    size: A4;
    margin: 18mm 16mm 22mm 16mm;
    @bottom-left {
        content: "{COMPANY_LEGAL} · {COMPANY_WEB}";
        font-family: 'Helvetica', sans-serif;
        font-size: 7.5pt;
        color: #786c5c;
    }
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-family: 'Helvetica', sans-serif;
        font-size: 7.5pt;
        color: #786c5c;
    }
}
@page :first {
    margin: 0;
    @bottom-left { content: ""; }
    @bottom-right { content: ""; }
}
/* Wide landscape pages — used by the detailed Monthly Summary table so its
   full payroll column set fits without clipping. */
@page wide {
    size: A4 landscape;
    margin: 14mm 12mm 18mm 12mm;
    @bottom-left {
        content: "{COMPANY_LEGAL} · {COMPANY_WEB}";
        font-family: 'Helvetica', sans-serif; font-size: 7.5pt; color: #786c5c;
    }
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-family: 'Helvetica', sans-serif; font-size: 7.5pt; color: #786c5c;
    }
}
.body-wide { page: wide; }
.body-wide .data-table { font-size: 7pt; }
.body-wide .data-table th { padding: 4pt 4pt; font-size: 6.6pt; }
.body-wide .data-table td { padding: 3.2pt 4pt; }
* { box-sizing: border-box; }
html, body {
    margin: 0; padding: 0;
    font-family: 'Helvetica', 'Arial', sans-serif;
    color: #1a1410;
    -weasy-font-feature: "tnum" on;
}

/* ─────────── Cover (shared) ─────────── */
.cover {
    width: 210mm;
    height: 297mm;
    padding: 22mm 20mm;
    position: relative;
    overflow: hidden;
    page-break-after: always;
}
.cover-band-top {
    position: absolute; top: 0; left: 0; right: 0;
    height: 14mm;
}
.cover-band-bottom {
    position: absolute; bottom: 0; left: 0; right: 0;
    height: 8mm;
}
.cover-brand {
    text-align: center;
    margin-top: 4mm;
}
.cover-brand .crest {
    display: inline-block;
    width: 18mm; height: 18mm;
    border-radius: 50%;
    line-height: 18mm;
    text-align: center;
    font-size: 18pt; font-weight: 900;
    margin-bottom: 6mm;
}
.cover-brand .company {
    font-size: 8pt; letter-spacing: 3pt; font-weight: 700;
    color: #786c5c;
    text-transform: uppercase;
}
.cover-eyebrow {
    text-align: center;
    font-size: 8pt; letter-spacing: 3pt; font-weight: 800;
    margin: 14mm 0 3mm;
    text-transform: uppercase;
}
.cover-title {
    text-align: center;
    font-size: 38pt;
    font-weight: 900;
    line-height: 1.05;
    margin: 0 0 4mm;
    letter-spacing: -0.5pt;
}
.cover-subtitle {
    text-align: center;
    font-style: italic;
    font-size: 11pt;
    color: #4b5563;
    margin-bottom: 12mm;
}
.cover-period {
    margin: 0 auto 14mm;           /* big gap before KPI strip — visually separated */
    padding: 7mm 9mm;
    border-radius: 3.5mm;
    width: 154mm;
    display: flex; justify-content: space-between; align-items: center;
    position: relative;
    overflow: hidden;              /* clip any decorative children to rounded corners */
}
.cover-period .label {
    font-size: 7pt; letter-spacing: 2pt; font-weight: 800;
    text-transform: uppercase;
    opacity: 0.85;
}
.cover-period .value {
    font-size: 11.5pt; font-weight: 800;
    color: #1a1410;
    margin-top: 2mm;
    letter-spacing: -0.1pt;
}
.cover-generated {
    text-align: center;
    margin: 5mm 0 10mm;
    font-size: 8.5pt;
    color: #6b7280;
    letter-spacing: 0.3pt;
}
.kpi-grid {
    display: flex; gap: 4.5mm;
    margin: 8mm auto 0;            /* universal top buffer so KPI tiles can't visually touch whatever precedes them */
    width: 170mm;
}
.kpi-tile {
    flex: 1;
    padding: 6mm 4mm 7mm;
    background: #ffffff;
    /* Sharp rectangular tiles — corporate report aesthetic. Dropping
       border-radius eliminates the WeasyPrint corner-rendering quirk
       where a thick border-top + thin side borders produce mismatched
       rounded vs square corners. The colored top rail is set per-tile
       via inline `border-top-color`. */
    border: 0.8pt solid #8a8170;
    border-top: 2.5mm solid #ea580c;   /* color overridden per tile */
    text-align: center;
    position: relative;
    box-shadow: 0 1pt 3pt rgba(26, 20, 16, 0.08);
}
.kpi-label {
    font-size: 7pt; letter-spacing: 1.6pt; font-weight: 800;
    color: #6b5840;
    text-transform: uppercase;
    margin: 2mm 0 2.5mm;
}
.kpi-value {
    font-size: 22pt; font-weight: 900;
    line-height: 1;
    letter-spacing: -0.4pt;
    font-variant-numeric: tabular-nums;
}
.kpi-value small { font-size: 11pt; opacity: 0.7; font-weight: 700; }
.cover-footer {
    position: absolute;
    left: 0; right: 0;
    bottom: 12mm;
    text-align: center;
    font-size: 7.5pt;
    color: #786c5c;
}
.cover-footer .legal { font-weight: 700; letter-spacing: 0.4pt; }
.cover-footer .confidential {
    margin-top: 1mm; font-size: 7pt; letter-spacing: 2pt; text-transform: uppercase;
}

/* ─────────── Status legend (cover) ─────────── */
.legend {
    width: 170mm; margin: 8mm auto 0;
    display: flex; flex-wrap: wrap; gap: 3mm; justify-content: center;
}
.legend-pill {
    display: inline-block;
    padding: 1.5mm 4mm;
    border-radius: 2mm;
    font-size: 7.5pt; font-weight: 800; letter-spacing: 0.5pt;
    text-transform: uppercase;
}

/* ─────────── Body pages: header band + section title ─────────── */
.page-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 0.6pt solid #d1cabb;
    padding-bottom: 2.5mm; margin-bottom: 6mm;
}
.page-head .title {
    font-size: 12pt; font-weight: 800; letter-spacing: -0.2pt;
}
.page-head .meta {
    font-size: 7pt; color: #786c5c; letter-spacing: 0.5pt;
}
.section-h {
    margin: 5mm 0 2mm;
    font-size: 18pt; font-weight: 900; letter-spacing: -0.4pt;
    line-height: 1.1;
}
.section-rule {
    width: 26mm; height: 1.1mm;
    margin-bottom: 4mm;
    border-radius: 0.6mm;
}
.section-sub {
    margin: 0 0 6mm;
    font-size: 9.5pt;
    color: #4b5563;
    letter-spacing: 0.1pt;
}

/* ─────────── Data table (shared) ───────────
   Uses border-collapse: collapse for clean page-flow. Every cell gets a
   visible grid border on all four sides — corporate spreadsheet feel.
   thead repeats on every continuation page (display: table-header-group),
   and every row carries page-break-inside: avoid so a single row never
   gets split across pages.
   ──────────────────────────────────────────────────────────────────── */
.data-table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 2mm;
    font-size: 8pt;
    table-layout: auto;
}
.data-table thead {
    display: table-header-group;       /* auto-repeat header on each page */
}
.data-table tbody tr {
    page-break-inside: avoid;          /* don't split a row across pages */
    break-inside: avoid;
    -weasy-page-break-inside: avoid;
}
.data-table tbody td {
    page-break-inside: avoid;          /* belt-and-suspenders for WeasyPrint */
    break-inside: avoid;
}
.data-table th {
    color: #fff;
    text-align: left;
    padding: 2.4mm 1.8mm;
    font-weight: 800;
    font-size: 7pt;
    letter-spacing: 0.5pt;
    text-transform: uppercase;
    border: 1pt solid #1a1410;         /* dark border on all sides of header */
    border-bottom-width: 2pt;
}
.data-table th.r { text-align: right; }
.data-table th.c { text-align: center; }
.data-table td {
    padding: 1.6mm 1.8mm;
    /* 1.4pt solid charcoal — heavy enough to render at any PDF viewer
       zoom (30%–200%), but slim enough that 12-column wide tables
       (Daily Roster, Monthly Summary) still fit within A4 portrait. */
    border: 1.4pt solid #1a1410;
    vertical-align: middle;
    color: #1a1410;
}
.data-table tr.zebra td { background: #fbf8f0; }
.data-table td.r { text-align: right; font-variant-numeric: tabular-nums; }
.data-table td.c { text-align: center; }
.data-table td.cell-danger {
    background: #fee2e2; color: #7f1d1d; font-weight: 800;
    border-left: 1.2pt solid #b91c1c;
}
.data-table td.cell-warn {
    background: #fef9c3; color: #713f12; font-weight: 800;
    border-left: 1.2pt solid #a16207;
}
.data-table td.cell-good {
    background: #ccfbf1; color: #115e59; font-weight: 800;
    border-left: 1.2pt solid #0d9488;
}
.status-pill {
    display: inline-block;
    padding: 1mm 2.5mm;
    border-radius: 6mm;
    font-size: 6.8pt;
    font-weight: 800;
    letter-spacing: 0.6pt;
    text-transform: uppercase;
    line-height: 1.1;
}

/* Empty-state message */
.empty {
    margin: 30mm 0;
    text-align: center;
    color: #786c5c;
    font-style: italic;
}
"""


def _row_class(idx: int) -> str:
    return "zebra" if idx % 2 == 1 else ""


def _table_html(report_key: str, shaped_rows: list[dict]) -> str:
    cols = _columns(report_key)
    head_cells = "".join(
        f'<th class="{"r" if c["align"]=="right" else "c" if c["align"]=="center" else ""}">{_esc(c["label"])}</th>'
        for c in cols
    )
    body_rows = []
    for i, row in enumerate(shaped_rows):
        cells = []
        for c in cols:
            html_val, klass = _cell(row, c)
            align_cls = "r" if c["align"] == "right" else "c" if c["align"] == "center" else ""
            cls = (align_cls + " " + klass).strip()
            cells.append(f'<td class="{cls}">{html_val}</td>')
        body_rows.append(f'<tr class="{_row_class(i)}">{"".join(cells)}</tr>')
    return (
        f'<table class="data-table">'
        f'<thead><tr>{head_cells}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        f'</table>'
    )


# ════════════════════════════════════════════════════════════════════════════
# Cover motifs — seven distinct designs
# ════════════════════════════════════════════════════════════════════════════


def _kpi_tiles_html(summary: dict, accent: str, shaped_count: int | None = None) -> str:
    # "Rows in report" reflects the rows the reader will actually see on the
    # body page (shaped count). Falls back to the raw count so existing covers
    # that don't pass shaped_count still render. The cover and the table must
    # agree on the same number so the reader doesn't see "15 records" on the
    # cover and a 1-row table on page 2.
    rows_n = shaped_count if shaped_count is not None else summary["rows"]
    tiles = [
        ("Rows in report", str(rows_n), accent),
        ("Employees", str(summary["employees"]), "#1a1410"),
        ("On-time", f"{summary['on_time_pct']}<small>%</small>", "#0d9488"),
        ("Overtime hrs", f"{summary['overtime_hours']:.1f}", "#ea580c"),
    ]
    return (
        '<div class="kpi-grid">'
        + "".join(
            f'<div class="kpi-tile" style="border-top-color:{c}">'
            f'<div class="kpi-label">{html.escape(label)}</div>'
            f'<div class="kpi-value" style="color:{c}">{val}</div>'
            f'</div>'
            for label, val, c in tiles
        )
        + "</div>"
    )


def _legend_html() -> str:
    keys = ["PRESENT", "LATE", "ABSENT", "LEAVE", "WFH", "HALF_DAY"]
    return '<div class="legend">' + "".join(
        f'<span class="legend-pill" '
        f'style="background:{STATUS_COLORS[k]["light"]};color:{STATUS_COLORS[k]["deep"]}">'
        f'{html.escape(k.replace("_", " "))}</span>'
        for k in keys
    ) + "</div>"


def _cover_editorial(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """Monthly Summary — magazine spread feel. Big serif title, twin gold bands."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    return f"""
    <section class="cover cover-editorial">
        <div class="cover-band-top" style="background:linear-gradient(90deg,{accent},{deep})"></div>
        <div class="cover-band-bottom" style="background:{accent}"></div>

        <div class="cover-brand">
            <span class="crest" style="background:{accent};color:#fff">F</span>
            <div class="company">{COMPANY['legal'].upper()}</div>
        </div>

        <!-- masthead -->
        <div style="display:flex;align-items:center;gap:6mm;margin:14mm 0 6mm;justify-content:center">
            <span style="flex:0 0 28mm;height:1pt;background:{accent}"></span>
            <span style="font-size:8pt;letter-spacing:3.5pt;font-weight:900;color:{accent};text-transform:uppercase">
                ATTENDANCE · INTELLIGENCE · {period['from'].strftime('%b %Y').upper()}
            </span>
            <span style="flex:0 0 28mm;height:1pt;background:{accent}"></span>
        </div>

        <h1 class="cover-title" style="font-family:'Georgia',serif;color:#1a1410">
            {meta['name']}
        </h1>
        <p class="cover-subtitle">{meta['subtitle']}</p>

        <div class="cover-period" style="background:{soft};border:1pt solid {accent}55">
            <div>
                <div class="label" style="color:{deep}">PERIOD FROM</div>
                <div class="value">{_fmt_long_date(period['from'])}</div>
            </div>
            <div style="font-size:14pt;color:{accent}">⟶</div>
            <div style="text-align:right">
                <div class="label" style="color:{deep}">PERIOD TO</div>
                <div class="value">{_fmt_long_date(period['to'])}</div>
            </div>
        </div>
        <div class="cover-generated">Generated {datetime.now().strftime('%d %b %Y · %I:%M %p').lstrip('0')}</div>

        {_kpi_tiles_html(summary, accent, shaped_count)}
        {_legend_html()}

        <div class="cover-footer">
            <div class="legal">{COMPANY['legal']} · {COMPANY['address_1']}, {COMPANY['address_2']}</div>
            <div class="confidential">Confidential · Internal use only</div>
        </div>
    </section>
    """


def _cover_bulletin(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """Late Arrivals — newsroom bulletin. Masthead, monospace ticker, clock motif."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    return f"""
    <section class="cover cover-bulletin" style="background:#fefdf8">
        <!-- newsroom rule -->
        <div style="position:absolute;top:0;left:0;right:0;height:3mm;background:#1a1410"></div>
        <div style="position:absolute;top:3mm;left:0;right:0;height:1mm;background:{accent}"></div>
        <div style="position:absolute;top:5mm;left:0;right:0;height:0.4mm;background:#1a1410"></div>

        <!-- masthead -->
        <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:10mm;padding-bottom:3mm;border-bottom:1.4pt double #1a1410">
            <div style="font-family:'Georgia',serif;font-size:9pt;color:#4b5563">
                Vol. {period['from'].year} · Issue {period['from'].strftime('%m%d')}<br/>
                {_fmt_long_date(datetime.now().date())}
            </div>
            <div style="font-family:'Georgia',serif;font-size:8pt;text-align:right">
                <strong>{COMPANY['name'].upper()}</strong><br/>
                <span style="color:#786c5c">{COMPANY['web']}</span>
            </div>
        </div>

        <div style="text-align:center;margin:10mm 0 4mm">
            <span style="font-size:8pt;letter-spacing:4pt;font-weight:900;color:{deep};text-transform:uppercase">
                — Punctuality Bulletin —
            </span>
        </div>

        <h1 style="font-family:'Georgia',serif;font-size:64pt;font-weight:900;text-align:center;margin:0;letter-spacing:-1.5pt;line-height:1;color:#1a1410">
            LATE
        </h1>
        <h1 style="font-family:'Georgia',serif;font-size:64pt;font-weight:900;text-align:center;margin:0 0 8mm;letter-spacing:-1.5pt;line-height:1;color:{accent}">
            ARRIVALS
        </h1>
        <div style="text-align:center;font-style:italic;font-size:11pt;color:#4b5563;margin-bottom:10mm">
            {meta['subtitle']}
        </div>

        <!-- clock face -->
        <div style="text-align:center;margin:0 0 8mm">
            <div style="display:inline-block;width:38mm;height:38mm;border-radius:50%;border:1.8pt solid {accent};background:{soft};position:relative">
                <div style="position:absolute;left:50%;top:50%;width:1pt;height:14mm;background:{deep};transform:translate(-50%,-100%) rotate(40deg);transform-origin:bottom"></div>
                <div style="position:absolute;left:50%;top:50%;width:1.4pt;height:10mm;background:#1a1410;transform:translate(-50%,-100%) rotate(120deg);transform-origin:bottom"></div>
                <div style="position:absolute;left:50%;top:50%;width:2mm;height:2mm;background:{accent};border-radius:50%;transform:translate(-50%,-50%)"></div>
                <div style="position:absolute;left:50%;top:1.5mm;transform:translateX(-50%);font-size:6pt;font-weight:900;color:{deep}">12</div>
                <div style="position:absolute;left:1.5mm;top:50%;transform:translateY(-50%);font-size:6pt;font-weight:900;color:{deep}">9</div>
                <div style="position:absolute;right:1.5mm;top:50%;transform:translateY(-50%);font-size:6pt;font-weight:900;color:{deep}">3</div>
                <div style="position:absolute;left:50%;bottom:1.5mm;transform:translateX(-50%);font-size:6pt;font-weight:900;color:{deep}">6</div>
            </div>
        </div>

        <!-- ticker -->
        <div style="margin:0 auto 8mm;width:170mm;background:#1a1410;color:#fde68a;padding:2mm 4mm;font-family:'Courier New',monospace;font-size:8pt;letter-spacing:1pt;border-radius:1mm">
            ▶ {summary['late']} BREACHES · {summary['late_minutes']} TOTAL LATE MINUTES · {summary['employees']} EMPLOYEES TOUCHED ◀
        </div>

        <div class="cover-period" style="background:{soft};border:1.2pt double {deep}">
            <div>
                <div class="label" style="color:{deep}">FROM</div>
                <div class="value">{_fmt_long_date(period['from'])}</div>
            </div>
            <div style="font-family:'Georgia',serif;font-size:18pt;color:{accent}">→</div>
            <div style="text-align:right">
                <div class="label" style="color:{deep}">TO</div>
                <div class="value">{_fmt_long_date(period['to'])}</div>
            </div>
        </div>

        {_kpi_tiles_html(summary, accent, shaped_count)}

        <div class="cover-footer">
            <div style="font-family:'Georgia',serif;font-style:italic">— continued on next page —</div>
            <div class="confidential" style="margin-top:3mm">Confidential · {COMPANY['legal']}</div>
        </div>
    </section>
    """


def _cover_industrial(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """Overtime — industrial dashboard. Hex grid, LED-style numbers, gauges."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    ot = summary["overtime_hours"]
    return f"""
    <section class="cover cover-industrial" style="background:#0c0a09">
        <!-- grid pattern -->
        <div style="position:absolute;inset:0;
            background-image:
                linear-gradient(rgba(234,88,12,0.08) 1px,transparent 1px),
                linear-gradient(90deg,rgba(234,88,12,0.08) 1px,transparent 1px);
            background-size: 6mm 6mm"></div>

        <div style="position:absolute;top:0;left:0;right:0;height:14mm;background:linear-gradient(90deg,{deep},{accent},#fb923c)"></div>
        <div style="position:absolute;bottom:0;left:0;right:0;height:6mm;background:{accent}"></div>

        <div class="cover-brand" style="position:relative;z-index:2;margin-top:4mm">
            <span class="crest" style="background:{accent};color:#fff;border:1.6pt solid #fff">F</span>
            <div class="company" style="color:#fde68a">{COMPANY['legal'].upper()}</div>
        </div>

        <div style="text-align:center;position:relative;z-index:2;margin-top:10mm">
            <div style="display:inline-block;padding:2mm 6mm;background:rgba(234,88,12,0.18);border:1pt solid {accent};border-radius:1mm">
                <span style="font-family:'Courier New',monospace;font-size:8pt;letter-spacing:3pt;font-weight:900;color:#fde68a;text-transform:uppercase">
                    ◉ OPERATIONS DASHBOARD · LIVE
                </span>
            </div>
        </div>

        <h1 style="font-family:'Helvetica',sans-serif;font-size:64pt;font-weight:900;text-align:center;color:#fff;letter-spacing:-1pt;margin:8mm 0 0;line-height:1;position:relative;z-index:2">
            OVER<span style="color:{accent}">TIME</span>
        </h1>
        <div style="text-align:center;color:#fde68a;font-size:10pt;letter-spacing:2pt;font-weight:700;margin:2mm 0 12mm;position:relative;z-index:2;text-transform:uppercase">
            ▍ {meta['subtitle']}
        </div>

        <!-- LED gauge ring -->
        <div style="text-align:center;position:relative;z-index:2;margin:0 0 10mm">
            <div style="display:inline-block;padding:6mm;border:1.6pt solid {accent};border-radius:2mm;background:rgba(234,88,12,0.10)">
                <div style="font-family:'Courier New',monospace;font-size:8pt;color:#fde68a;letter-spacing:3pt;font-weight:900">▍ TOTAL OT LOGGED ▍</div>
                <div style="font-family:'Courier New',monospace;font-size:52pt;color:{accent};font-weight:900;line-height:1;margin-top:1mm;letter-spacing:1pt">
                    {ot:.1f}h
                </div>
                <div style="font-family:'Courier New',monospace;font-size:7pt;color:#9ca3af;letter-spacing:1pt;margin-top:1.5mm">
                    ACROSS {summary['employees']} EMPLOYEES · {(period['to']-period['from']).days+1} DAYS
                </div>
            </div>
        </div>

        <!-- LED data row -->
        <div style="display:flex;gap:3mm;margin:0 auto;width:170mm;position:relative;z-index:2">
            <div style="flex:1;background:rgba(0,0,0,0.5);border:1pt solid #292524;border-left:2pt solid #fde68a;padding:4mm 4mm 5mm;border-radius:1mm">
                <div style="font-family:'Courier New',monospace;font-size:6.5pt;color:#9ca3af;letter-spacing:2pt;font-weight:900">RECORDS</div>
                <div style="font-family:'Courier New',monospace;font-size:24pt;color:#fde68a;font-weight:900;line-height:1;margin-top:1mm">{(shaped_count if shaped_count is not None else summary['rows']):04d}</div>
            </div>
            <div style="flex:1;background:rgba(0,0,0,0.5);border:1pt solid #292524;border-left:2pt solid {accent};padding:4mm 4mm 5mm;border-radius:1mm">
                <div style="font-family:'Courier New',monospace;font-size:6.5pt;color:#9ca3af;letter-spacing:2pt;font-weight:900">EMPLOYEES</div>
                <div style="font-family:'Courier New',monospace;font-size:24pt;color:{accent};font-weight:900;line-height:1;margin-top:1mm">{summary['employees']:04d}</div>
            </div>
            <div style="flex:1;background:rgba(0,0,0,0.5);border:1pt solid #292524;border-left:2pt solid #5eead4;padding:4mm 4mm 5mm;border-radius:1mm">
                <div style="font-family:'Courier New',monospace;font-size:6.5pt;color:#9ca3af;letter-spacing:2pt;font-weight:900">ON-TIME %</div>
                <div style="font-family:'Courier New',monospace;font-size:24pt;color:#5eead4;font-weight:900;line-height:1;margin-top:1mm">{summary['on_time_pct']:03d}%</div>
            </div>
            <div style="flex:1;background:rgba(0,0,0,0.5);border:1pt solid #292524;border-left:2pt solid #fb923c;padding:4mm 4mm 5mm;border-radius:1mm">
                <div style="font-family:'Courier New',monospace;font-size:6.5pt;color:#9ca3af;letter-spacing:2pt;font-weight:900">LATE MIN</div>
                <div style="font-family:'Courier New',monospace;font-size:24pt;color:#fb923c;font-weight:900;line-height:1;margin-top:1mm">{summary['late_minutes']:04d}</div>
            </div>
        </div>

        <!-- footer terminal -->
        <div style="position:absolute;bottom:14mm;left:0;right:0;text-align:center;font-family:'Courier New',monospace;font-size:7pt;color:#9ca3af;letter-spacing:1.5pt;z-index:2">
            <div>PERIOD: {period['from'].strftime('%Y-%m-%d')} ⟶ {period['to'].strftime('%Y-%m-%d')} · GENERATED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            <div style="margin-top:2mm;color:{accent}">{COMPANY['legal'].upper()} · CONFIDENTIAL</div>
        </div>
    </section>
    """


def _cover_postcard(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """WFH — travel postcard. Sky palette, stamp seal, dotted divider."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    return f"""
    <section class="cover cover-postcard" style="background:linear-gradient(180deg,{soft} 0%, #fff 60%)">
        <!-- top wavy band -->
        <div style="position:absolute;top:0;left:0;right:0;height:20mm;
            background:linear-gradient(90deg,{accent},#7dd3fc);
            clip-path:polygon(0 0,100% 0,100% 75%,0 100%)"></div>

        <div style="position:relative;z-index:2;margin-top:4mm;text-align:center;color:#fff">
            <span style="font-size:8pt;letter-spacing:4pt;font-weight:900;text-transform:uppercase">
                · {COMPANY['name'].upper()} POSTAGE ·
            </span>
        </div>

        <!-- postage stamp -->
        <div style="position:absolute;top:24mm;right:20mm;width:30mm;height:36mm;background:#fff;border:1.4pt solid {deep};
            box-shadow:0 2pt 10pt rgba(0,0,0,0.10);transform:rotate(-6deg);text-align:center;padding:3mm 0 2mm">
            <div style="font-size:6pt;letter-spacing:2pt;font-weight:900;color:{deep}">REMOTE WORK</div>
            <div style="font-family:'Georgia',serif;font-size:24pt;font-weight:900;color:{accent};margin:2mm 0 1mm">{summary['wfh']}</div>
            <div style="font-size:7pt;color:#4b5563;font-weight:700">days logged</div>
            <div style="margin-top:2mm;font-size:6pt;letter-spacing:1.5pt;color:{deep}">{period['from'].strftime('%b').upper()} · {period['from'].year}</div>
            <!-- perforations -->
            <div style="position:absolute;inset:-1mm;border:0.3pt dashed {deep};pointer-events:none"></div>
        </div>

        <h1 style="font-family:'Georgia',serif;font-size:54pt;font-weight:900;color:{deep};margin:30mm 0 1mm;letter-spacing:-1pt;line-height:1">
            Greetings
        </h1>
        <h1 style="font-family:'Georgia',serif;font-size:30pt;font-style:italic;color:{accent};margin:0 0 2mm;font-weight:400">
            from the home office.
        </h1>
        <p style="font-size:11pt;color:#4b5563;font-style:italic;margin:0 0 12mm">
            {meta['subtitle']}
        </p>

        <!-- dotted divider -->
        <div style="border-top:0.6pt dotted {accent};margin:6mm 0"></div>

        <!-- handwritten-style fields -->
        <div style="display:flex;gap:8mm;margin-bottom:10mm">
            <div style="flex:1">
                <div style="font-size:6.5pt;letter-spacing:2pt;font-weight:900;color:{deep};margin-bottom:1mm">FROM</div>
                <div style="font-family:'Georgia',serif;font-style:italic;font-size:12pt;color:#1a1410;border-bottom:0.4pt solid #d1cabb;padding-bottom:1.5mm">{_fmt_long_date(period['from'])}</div>
            </div>
            <div style="flex:1">
                <div style="font-size:6.5pt;letter-spacing:2pt;font-weight:900;color:{deep};margin-bottom:1mm">TO</div>
                <div style="font-family:'Georgia',serif;font-style:italic;font-size:12pt;color:#1a1410;border-bottom:0.4pt solid #d1cabb;padding-bottom:1.5mm">{_fmt_long_date(period['to'])}</div>
            </div>
        </div>

        <div style="border-top:0.6pt dotted {accent};margin:0 0 8mm"></div>

        {_kpi_tiles_html(summary, accent, shaped_count)}

        <div class="cover-footer">
            <div class="legal">{COMPANY['legal']} · {COMPANY['email']}</div>
            <div class="confidential">Personal & Confidential</div>
        </div>
    </section>
    """


def _cover_certificate(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """Compliance — formal certificate. Ornate border, seal, signature line."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    return f"""
    <section class="cover cover-certificate" style="background:#fffdf5">
        <!-- ornate border -->
        <div style="position:absolute;top:10mm;left:10mm;right:10mm;bottom:10mm;
            border:1.4pt solid {accent}"></div>
        <div style="position:absolute;top:13mm;left:13mm;right:13mm;bottom:13mm;
            border:0.5pt solid {accent}80"></div>
        <!-- corner ornaments -->
        <div style="position:absolute;top:8mm;left:8mm;width:6mm;height:6mm;border-top:1.6pt solid {deep};border-left:1.6pt solid {deep}"></div>
        <div style="position:absolute;top:8mm;right:8mm;width:6mm;height:6mm;border-top:1.6pt solid {deep};border-right:1.6pt solid {deep}"></div>
        <div style="position:absolute;bottom:8mm;left:8mm;width:6mm;height:6mm;border-bottom:1.6pt solid {deep};border-left:1.6pt solid {deep}"></div>
        <div style="position:absolute;bottom:8mm;right:8mm;width:6mm;height:6mm;border-bottom:1.6pt solid {deep};border-right:1.6pt solid {deep}"></div>

        <div style="position:relative;z-index:2;text-align:center;padding-top:18mm">
            <div style="font-size:7pt;letter-spacing:5pt;font-weight:900;color:{deep};text-transform:uppercase">
                ◇ {COMPANY['legal']} ◇
            </div>
            <div style="margin-top:2mm;font-size:6pt;letter-spacing:3pt;color:#786c5c">
                {COMPANY['address_1']} · {COMPANY['address_2']}
            </div>

            <div style="margin:14mm 0 6mm;display:flex;align-items:center;justify-content:center;gap:6mm">
                <span style="display:inline-block;width:24mm;height:0.7pt;background:{accent}"></span>
                <span style="font-family:'Georgia',serif;font-size:8.5pt;letter-spacing:2pt;font-weight:700;color:{deep};text-transform:uppercase">
                    Certificate of
                </span>
                <span style="display:inline-block;width:24mm;height:0.7pt;background:{accent}"></span>
            </div>

            <h1 style="font-family:'Georgia',serif;font-size:54pt;font-weight:900;color:{deep};margin:0;letter-spacing:-1.2pt;line-height:1.05">
                Compliance
            </h1>
            <div style="margin-top:6mm;font-family:'Georgia',serif;font-style:italic;font-size:11pt;color:#4b5563">
                — This is to attest that the attendance records of —
            </div>
            <div style="margin-top:4mm;font-family:'Georgia',serif;font-size:14pt;font-weight:700;color:#1a1410">
                {COMPANY['name']} for the period of
            </div>
            <div style="margin-top:3mm;font-family:'Georgia',serif;font-size:13pt;color:{deep};font-style:italic">
                {_fmt_long_date(period['from'])} &nbsp;—&nbsp; {_fmt_long_date(period['to'])}
            </div>
            <div style="margin-top:6mm;font-family:'Georgia',serif;font-style:italic;font-size:11pt;color:#4b5563;line-height:1.5;padding:0 18mm">
                have been compiled, reviewed and aggregated from the canonical biometric ledger.
                The findings of roster coverage versus scheduled hours are itemised on the
                pages that follow.
            </div>

            <!-- wax seal -->
            <div style="margin:8mm auto 4mm;width:30mm;height:30mm;border-radius:50%;background:radial-gradient(circle at 35% 30%,#fef9c3,{accent} 70%,{deep});display:flex;align-items:center;justify-content:center;position:relative;box-shadow:0 1pt 4pt rgba(0,0,0,0.20)">
                <div style="position:absolute;inset:2mm;border-radius:50%;border:0.8pt dashed #fff8e7"></div>
                <div style="text-align:center;color:#fff;font-family:'Georgia',serif;font-size:6pt;letter-spacing:2pt;font-weight:900">
                    SEAL<br/>
                    <span style="font-size:14pt">F</span><br/>
                    {period['from'].year}
                </div>
            </div>

            <!-- KPI ribbon -->
            <div style="margin:10mm auto 0;display:flex;justify-content:space-between;align-items:center;width:160mm;padding:4mm 6mm;background:{soft};border:0.5pt solid {accent}">
                <div style="text-align:center;flex:1">
                    <div style="font-size:7pt;letter-spacing:1.5pt;color:{deep};font-weight:900">SCHEDULED DAYS</div>
                    <div style="font-family:'Georgia',serif;font-size:18pt;font-weight:900;color:{deep}">{summary['rows']-summary['week_off']-summary['holiday']}</div>
                </div>
                <div style="width:0.4pt;height:14mm;background:{accent}"></div>
                <div style="text-align:center;flex:1">
                    <div style="font-size:7pt;letter-spacing:1.5pt;color:{deep};font-weight:900">EMPLOYEES</div>
                    <div style="font-family:'Georgia',serif;font-size:18pt;font-weight:900;color:{deep}">{summary['employees']}</div>
                </div>
                <div style="width:0.4pt;height:14mm;background:{accent}"></div>
                <div style="text-align:center;flex:1">
                    <div style="font-size:7pt;letter-spacing:1.5pt;color:{deep};font-weight:900">ON-TIME %</div>
                    <div style="font-family:'Georgia',serif;font-size:18pt;font-weight:900;color:{deep}">{summary['on_time_pct']}%</div>
                </div>
                <div style="width:0.4pt;height:14mm;background:{accent}"></div>
                <div style="text-align:center;flex:1">
                    <div style="font-size:7pt;letter-spacing:1.5pt;color:{deep};font-weight:900">DEPARTMENTS</div>
                    <div style="font-family:'Georgia',serif;font-size:18pt;font-weight:900;color:{deep}">{summary['departments']}</div>
                </div>
            </div>

            <!-- signature lines -->
            <div style="position:absolute;bottom:30mm;left:24mm;right:24mm;display:flex;justify-content:space-between">
                <div style="flex:1;text-align:center">
                    <div style="border-top:0.6pt solid #1a1410;margin:0 10mm;padding-top:1.5mm;font-family:'Georgia',serif;font-style:italic;font-size:9pt;color:#4b5563">
                        Authorised by HR
                    </div>
                </div>
                <div style="flex:1;text-align:center">
                    <div style="border-top:0.6pt solid #1a1410;margin:0 10mm;padding-top:1.5mm;font-family:'Georgia',serif;font-style:italic;font-size:9pt;color:#4b5563">
                        Date · {datetime.now().strftime('%d %b %Y')}
                    </div>
                </div>
            </div>
        </div>
    </section>
    """


def _cover_dossier(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """Anomalies — manila dossier with CONFIDENTIAL stamp."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    case_no = f"FRC/HR/ANM/{period['from'].year}/{datetime.now().strftime('%m%d%H%M')}"
    severity = "HIGH" if summary["late"] > 5 else "MODERATE" if summary["late"] > 0 else "LOW"
    return f"""
    <section class="cover cover-dossier" style="background:#f5efd8">
        <!-- manila texture lines -->
        <div style="position:absolute;inset:0;background-image:
            repeating-linear-gradient(0deg,transparent,transparent 0.4mm,rgba(120,108,92,0.04) 0.4mm,rgba(120,108,92,0.04) 0.8mm)"></div>

        <!-- folder tab -->
        <div style="position:absolute;top:0;left:30mm;width:60mm;height:8mm;background:#1a1410;border-radius:0 0 1mm 1mm;color:#fff;text-align:center;line-height:8mm;font-size:7pt;letter-spacing:2pt;font-weight:900;z-index:3">
            HR · ATTENDANCE
        </div>

        <!-- CONFIDENTIAL stamp -->
        <div style="position:absolute;top:36mm;right:18mm;border:1.8pt solid {accent};padding:2mm 5mm;transform:rotate(8deg);background:rgba(254,226,226,0.45);z-index:3">
            <div style="font-family:'Georgia',serif;font-size:14pt;font-weight:900;letter-spacing:2.5pt;color:{accent};text-transform:uppercase">CONFIDENTIAL</div>
            <div style="font-size:6pt;letter-spacing:1.5pt;color:{deep};text-align:center;font-weight:700">EYES ONLY</div>
        </div>

        <!-- case header -->
        <div style="margin-top:20mm;padding:4mm 6mm;background:#1a1410;color:#fde68a;border-radius:1mm">
            <div style="display:flex;justify-content:space-between;align-items:baseline">
                <div style="font-size:7pt;letter-spacing:2.5pt;font-weight:900">CASE NO.</div>
                <div style="font-family:'Courier New',monospace;font-size:9pt;font-weight:900">{case_no}</div>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:1.5mm">
                <div style="font-size:7pt;letter-spacing:2.5pt;font-weight:900">SEVERITY</div>
                <div style="font-size:9pt;font-weight:900;color:{accent}">{severity}</div>
            </div>
        </div>

        <h1 style="font-family:'Georgia',serif;font-size:64pt;font-weight:900;color:{deep};margin:14mm 0 0;letter-spacing:-1.4pt;line-height:1">
            ANOMA-<br/>LIES
        </h1>
        <div style="font-style:italic;font-size:11pt;color:#4b5563;margin:0 0 12mm">
            {meta['subtitle']}
        </div>

        <!-- red-flag tiles -->
        <div style="display:flex;gap:3mm;width:170mm">
            <div style="flex:1;padding:5mm 4mm;background:#fff;border-left:3pt solid {accent};box-shadow:1pt 1pt 4pt rgba(0,0,0,0.06)">
                <div style="font-size:6.5pt;letter-spacing:2pt;color:{deep};font-weight:900">FLAGGED ROWS</div>
                <div style="font-family:'Georgia',serif;font-size:28pt;color:{accent};font-weight:900;line-height:1;margin-top:2mm">{summary['late']}</div>
                <div style="font-size:7pt;color:#786c5c;margin-top:1mm">events under review</div>
            </div>
            <div style="flex:1;padding:5mm 4mm;background:#fff;border-left:3pt solid #b45309;box-shadow:1pt 1pt 4pt rgba(0,0,0,0.06)">
                <div style="font-size:6.5pt;letter-spacing:2pt;color:#7c2d12;font-weight:900">LATE MINUTES</div>
                <div style="font-family:'Georgia',serif;font-size:28pt;color:#b45309;font-weight:900;line-height:1;margin-top:2mm">{summary['late_minutes']}</div>
                <div style="font-size:7pt;color:#786c5c;margin-top:1mm">total accumulated</div>
            </div>
            <div style="flex:1;padding:5mm 4mm;background:#fff;border-left:3pt solid #1a1410;box-shadow:1pt 1pt 4pt rgba(0,0,0,0.06)">
                <div style="font-size:6.5pt;letter-spacing:2pt;color:#1a1410;font-weight:900">EMPLOYEES</div>
                <div style="font-family:'Georgia',serif;font-size:28pt;color:#1a1410;font-weight:900;line-height:1;margin-top:2mm">{summary['employees']}</div>
                <div style="font-size:7pt;color:#786c5c;margin-top:1mm">in scope</div>
            </div>
        </div>

        <!-- evidence summary box -->
        <div style="margin-top:10mm;border:0.8pt dashed {deep};padding:5mm 6mm;background:rgba(254,226,226,0.30)">
            <div style="font-family:'Georgia',serif;font-size:8pt;letter-spacing:2pt;font-weight:900;color:{deep};margin-bottom:2mm">▼ EVIDENCE WINDOW ▼</div>
            <div style="display:flex;justify-content:space-between;font-family:'Georgia',serif;font-size:11pt">
                <div><strong>From:</strong> {_fmt_long_date(period['from'])}</div>
                <div><strong>To:</strong> {_fmt_long_date(period['to'])}</div>
            </div>
            <div style="margin-top:2mm;font-size:8.5pt;color:#4b5563;font-style:italic">
                Compiled by HR · {datetime.now().strftime('%d %b %Y at %I:%M %p').replace(' 0', ' ')}
            </div>
        </div>

        <div class="cover-footer" style="bottom:14mm">
            <div style="font-size:6pt;letter-spacing:2pt;font-weight:900;color:{deep}">DOCUMENT CLASSIFICATION · INTERNAL ONLY</div>
            <div class="confidential" style="margin-top:2mm;color:#1a1410">{COMPANY['legal']}</div>
        </div>
    </section>
    """


def _cover_blueprint(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """Daily Roster — architectural blueprint. Blue grid, technical title block."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    return f"""
    <section class="cover cover-blueprint" style="background:#1e1b4b;color:#fff">
        <!-- blueprint grid -->
        <div style="position:absolute;inset:0;background-image:
            linear-gradient(rgba(255,255,255,0.06) 1px,transparent 1px),
            linear-gradient(90deg,rgba(255,255,255,0.06) 1px,transparent 1px);
            background-size:5mm 5mm"></div>
        <div style="position:absolute;inset:0;background-image:
            linear-gradient(rgba(255,255,255,0.10) 1px,transparent 1px),
            linear-gradient(90deg,rgba(255,255,255,0.10) 1px,transparent 1px);
            background-size:25mm 25mm"></div>

        <!-- crosshair corners -->
        <div style="position:absolute;top:14mm;left:14mm;width:6mm;height:6mm">
            <div style="position:absolute;top:50%;left:0;right:0;height:0.4pt;background:{accent}"></div>
            <div style="position:absolute;top:0;bottom:0;left:50%;width:0.4pt;background:{accent}"></div>
        </div>
        <div style="position:absolute;top:14mm;right:14mm;width:6mm;height:6mm">
            <div style="position:absolute;top:50%;left:0;right:0;height:0.4pt;background:{accent}"></div>
            <div style="position:absolute;top:0;bottom:0;left:50%;width:0.4pt;background:{accent}"></div>
        </div>
        <div style="position:absolute;bottom:14mm;left:14mm;width:6mm;height:6mm">
            <div style="position:absolute;top:50%;left:0;right:0;height:0.4pt;background:{accent}"></div>
            <div style="position:absolute;top:0;bottom:0;left:50%;width:0.4pt;background:{accent}"></div>
        </div>
        <div style="position:absolute;bottom:14mm;right:14mm;width:6mm;height:6mm">
            <div style="position:absolute;top:50%;left:0;right:0;height:0.4pt;background:{accent}"></div>
            <div style="position:absolute;top:0;bottom:0;left:50%;width:0.4pt;background:{accent}"></div>
        </div>

        <!-- scale ruler top -->
        <div style="position:absolute;top:18mm;left:24mm;right:24mm;height:3mm;border-bottom:0.5pt solid rgba(255,255,255,0.45);
            background:
                repeating-linear-gradient(90deg,transparent 0 9.5mm, rgba(255,255,255,0.45) 9.5mm 10mm)"></div>

        <div style="text-align:center;position:relative;z-index:2;margin-top:30mm">
            <span style="font-family:'Courier New',monospace;font-size:7pt;letter-spacing:3.5pt;color:#a5b4fc;font-weight:700">
                ◇ TECHNICAL DRAWING · ATTENDANCE-001 ◇
            </span>
        </div>

        <h1 style="font-family:'Courier New',monospace;font-size:56pt;font-weight:900;text-align:center;letter-spacing:-0.5pt;line-height:1;margin:8mm 0 2mm;position:relative;z-index:2;color:#fff">
            DAILY  ROSTER
        </h1>
        <div style="text-align:center;font-style:italic;font-size:11pt;color:#a5b4fc;margin-bottom:14mm;position:relative;z-index:2">
            {meta['subtitle']}
        </div>

        <!-- isometric box -->
        <div style="position:relative;z-index:2;text-align:center;margin-bottom:14mm">
            <div style="display:inline-block;border:0.6pt solid {accent};background:rgba(124,58,237,0.15);padding:5mm 8mm">
                <div style="font-family:'Courier New',monospace;font-size:7pt;letter-spacing:2pt;color:#a5b4fc;font-weight:700">SCALE 1:1 · ROWS IN REPORT</div>
                <div style="font-family:'Courier New',monospace;font-size:36pt;font-weight:900;color:#fff;line-height:1.1;margin-top:1.5mm">{shaped_count if shaped_count is not None else summary['rows']}</div>
            </div>
        </div>

        <!-- title block (architectural drawing style) -->
        <div style="position:relative;z-index:2;border:0.8pt solid #fff;width:170mm;margin:0 auto">
            <div style="display:flex;border-bottom:0.6pt solid #fff;background:rgba(0,0,0,0.30)">
                <div style="flex:2;padding:3mm 4mm;border-right:0.6pt solid #fff">
                    <div style="font-family:'Courier New',monospace;font-size:6.5pt;letter-spacing:2pt;color:#a5b4fc;font-weight:700">PROJECT</div>
                    <div style="font-family:'Courier New',monospace;font-size:10pt;font-weight:900;margin-top:1mm">FOURRECK HR · ATTENDANCE ROSTER</div>
                </div>
                <div style="flex:1;padding:3mm 4mm;border-right:0.6pt solid #fff">
                    <div style="font-family:'Courier New',monospace;font-size:6.5pt;letter-spacing:2pt;color:#a5b4fc;font-weight:700">DWG NO.</div>
                    <div style="font-family:'Courier New',monospace;font-size:10pt;font-weight:900;margin-top:1mm">ATT-{period['from'].strftime('%y%m%d')}</div>
                </div>
                <div style="flex:1;padding:3mm 4mm">
                    <div style="font-family:'Courier New',monospace;font-size:6.5pt;letter-spacing:2pt;color:#a5b4fc;font-weight:700">REV.</div>
                    <div style="font-family:'Courier New',monospace;font-size:10pt;font-weight:900;margin-top:1mm">A · {datetime.now().strftime('%d.%m.%y')}</div>
                </div>
            </div>
            <div style="display:flex">
                <div style="flex:1;padding:3mm 4mm;border-right:0.6pt solid #fff">
                    <div style="font-family:'Courier New',monospace;font-size:6.5pt;letter-spacing:2pt;color:#a5b4fc;font-weight:700">FROM</div>
                    <div style="font-family:'Courier New',monospace;font-size:9pt;font-weight:900;margin-top:1mm">{period['from'].strftime('%Y-%m-%d')}</div>
                </div>
                <div style="flex:1;padding:3mm 4mm;border-right:0.6pt solid #fff">
                    <div style="font-family:'Courier New',monospace;font-size:6.5pt;letter-spacing:2pt;color:#a5b4fc;font-weight:700">TO</div>
                    <div style="font-family:'Courier New',monospace;font-size:9pt;font-weight:900;margin-top:1mm">{period['to'].strftime('%Y-%m-%d')}</div>
                </div>
                <div style="flex:1;padding:3mm 4mm;border-right:0.6pt solid #fff">
                    <div style="font-family:'Courier New',monospace;font-size:6.5pt;letter-spacing:2pt;color:#a5b4fc;font-weight:700">EMPLOYEES</div>
                    <div style="font-family:'Courier New',monospace;font-size:9pt;font-weight:900;margin-top:1mm">{summary['employees']:03d}</div>
                </div>
                <div style="flex:1;padding:3mm 4mm">
                    <div style="font-family:'Courier New',monospace;font-size:6.5pt;letter-spacing:2pt;color:#a5b4fc;font-weight:700">DEPARTMENTS</div>
                    <div style="font-family:'Courier New',monospace;font-size:9pt;font-weight:900;margin-top:1mm">{summary['departments']:02d}</div>
                </div>
            </div>
        </div>

        <!-- bottom scale ruler -->
        <div style="position:absolute;bottom:30mm;left:24mm;right:24mm;height:3mm;border-top:0.5pt solid rgba(255,255,255,0.45);
            background:
                repeating-linear-gradient(90deg,transparent 0 9.5mm, rgba(255,255,255,0.45) 9.5mm 10mm);z-index:2"></div>

        <div style="position:absolute;bottom:14mm;left:0;right:0;text-align:center;font-family:'Courier New',monospace;font-size:6.5pt;color:#a5b4fc;letter-spacing:2pt;z-index:2">
            <div>{COMPANY['legal'].upper()} · WWW.{COMPANY['web'].upper()}</div>
            <div style="margin-top:1.5mm">DOCUMENT IS THE PROPERTY OF FOURRECK · DO NOT REPRODUCE</div>
        </div>
    </section>
    """


def _cover_cafe(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """Breaks — cafe receipt style. Monospace dot-matrix, perforated edges,
    coffee cup motif. Looks like a thermal-printed bill from a coffee shop."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    total_break_min = summary.get("break_minutes_total")
    # No global break aggregate in summary — fall back to shaped-derived
    rows_n = shaped_count if shaped_count is not None else summary["rows"]
    return f"""
    <section class="cover cover-cafe" style="background:#fefaf3">
        <!-- coffee stain background dots -->
        <div style="position:absolute;top:24mm;right:30mm;width:38mm;height:38mm;border-radius:50%;background:radial-gradient(circle,{soft} 0%,transparent 70%);opacity:0.7"></div>
        <div style="position:absolute;bottom:60mm;left:20mm;width:30mm;height:30mm;border-radius:50%;background:radial-gradient(circle,{soft} 0%,transparent 70%);opacity:0.5"></div>

        <!-- top perforation -->
        <div style="position:absolute;top:0;left:0;right:0;height:6mm;
            background:
                repeating-linear-gradient(90deg,{deep} 0 2mm,transparent 2mm 4mm)"></div>

        <!-- cafe brand -->
        <div style="text-align:center;margin-top:14mm;position:relative;z-index:2">
            <span style="font-family:'Georgia',serif;font-size:9pt;letter-spacing:3pt;color:{deep};font-weight:700;text-transform:uppercase">
                — {COMPANY['name'].upper()} CAFÉ ·  PAYROLL CASHIER —
            </span>
        </div>

        <!-- coffee cup illustration in CSS -->
        <div style="text-align:center;margin-top:10mm;position:relative;z-index:2">
            <div style="display:inline-block;position:relative">
                <!-- steam -->
                <div style="position:absolute;left:50%;top:-12mm;transform:translateX(-50%);font-family:'Georgia',serif;font-size:14pt;color:{deep};opacity:0.55;letter-spacing:1pt">∽ ∽ ∽</div>
                <!-- cup body -->
                <div style="width:38mm;height:30mm;border:2pt solid {deep};border-top-width:0;border-radius:0 0 4mm 4mm;background:linear-gradient(180deg,{soft} 30%,#fff 70%);position:relative">
                    <!-- liquid surface ellipse -->
                    <div style="position:absolute;top:-1.5pt;left:-2pt;right:-2pt;height:3pt;background:{accent};border-radius:50%/100% 100% 0 0"></div>
                </div>
                <!-- handle -->
                <div style="position:absolute;right:-7mm;top:6mm;width:7mm;height:14mm;border:2pt solid {deep};border-left:0;border-radius:0 50% 50% 0"></div>
                <!-- saucer -->
                <div style="margin-top:1mm;width:50mm;height:2mm;background:{deep};border-radius:50%;transform:translateX(-6mm)"></div>
            </div>
        </div>

        <h1 style="font-family:'Georgia',serif;font-size:54pt;font-weight:900;text-align:center;color:{deep};margin:8mm 0 1mm;letter-spacing:-0.8pt;line-height:1;position:relative;z-index:2">
            BREAKS
        </h1>
        <div style="text-align:center;font-style:italic;font-size:11pt;color:#6b5840;margin-bottom:10mm;position:relative;z-index:2">
            {meta['subtitle']}
        </div>

        <!-- receipt body -->
        <div style="margin:0 auto;width:140mm;background:#fff;border:1pt dashed {deep};padding:6mm 8mm;font-family:'Courier New',monospace;font-size:10pt;color:#1a1410">
            <div style="text-align:center;font-weight:900;letter-spacing:2pt;border-bottom:0.4pt dashed {deep};padding-bottom:2mm;margin-bottom:3mm">
                ⊳ DAILY BREAK RECEIPT ⊲
            </div>
            <div style="display:flex;justify-content:space-between"><span>FROM</span><span>{period['from'].strftime('%d-%b-%Y').upper()}</span></div>
            <div style="display:flex;justify-content:space-between"><span>TO</span><span>{period['to'].strftime('%d-%b-%Y').upper()}</span></div>
            <div style="display:flex;justify-content:space-between"><span>EMPLOYEES SCANNED</span><span>{summary['employees']:>4d}</span></div>
            <div style="border-top:0.4pt dashed {deep};margin:3mm 0"></div>
            <div style="display:flex;justify-content:space-between;font-weight:900"><span>BREAK-DAYS</span><span>x {rows_n:>4d}</span></div>
            <div style="display:flex;justify-content:space-between;font-weight:900;font-size:14pt;color:{accent};margin-top:1.5mm"><span>SUB-TOTAL</span><span>{summary['rows']:>4d} rows</span></div>
            <div style="border-top:1pt double {deep};margin:3mm 0 1mm"></div>
            <div style="text-align:center;font-style:italic;color:#6b5840">— thank you for fuelling up —</div>
        </div>

        <!-- Literal spacer — block sibling with explicit height. Margins
             collapse in WeasyPrint; padding-on-parent or a sized spacer
             div doesn't. This is the only reliable way to guarantee gap
             between two flow elements. 30mm = clearly visible separation. -->
        <div style="height:30mm;clear:both;display:block;font-size:0;line-height:0">&nbsp;</div>

        {_kpi_tiles_html(summary, accent, shaped_count)}

        <!-- bottom perforation -->
        <div style="position:absolute;bottom:0;left:0;right:0;height:6mm;
            background:
                repeating-linear-gradient(90deg,{deep} 0 2mm,transparent 2mm 4mm)"></div>

        <div class="cover-footer" style="bottom:14mm">
            <div class="legal">{COMPANY['legal']} · {COMPANY['web']}</div>
            <div class="confidential">Personal & confidential</div>
        </div>
    </section>
    """


COVER_RENDERERS = {
    "editorial": _cover_editorial,
    "bulletin": _cover_bulletin,
    "industrial": _cover_industrial,
    "postcard": _cover_postcard,
    "certificate": _cover_certificate,
    "dossier": _cover_dossier,
    "blueprint": _cover_blueprint,
    "cafe": _cover_cafe,
}


# ════════════════════════════════════════════════════════════════════════════
# Page header (body pages, after cover)
# ════════════════════════════════════════════════════════════════════════════


def _body_pages(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict, period: dict) -> str:
    accent = meta["accent"]
    deep = meta["accent_deep"]
    ref = f"FRC/ATT/{report_key.upper()}/{period['from'].year}/{datetime.now().strftime('%m%d%H%M')}"

    page_head = f"""
    <div class="page-head">
        <div class="title">{_esc(COMPANY['name'])} · {_esc(meta['name'])}</div>
        <div class="meta">Ref {_esc(ref)} · {_fmt_long_date(period['from'])} → {_fmt_long_date(period['to'])}</div>
    </div>
    """

    section_h = f"""
    <h2 class="section-h" style="color:{deep}">{_esc(meta['name'].upper())}</h2>
    <div class="section-rule" style="background:{accent}"></div>
    <p class="section-sub">
        {len(shaped_rows)} record{'' if len(shaped_rows)==1 else 's'} ·
        {summary['employees']} employee{'' if summary['employees']==1 else 's'} ·
        {_fmt_long_date(period['from'])} to {_fmt_long_date(period['to'])}
    </p>
    """

    table = _table_html(report_key, shaped_rows) if shaped_rows else (
        '<div class="empty">No records found for the selected period.</div>'
    )

    # Per-report data-table accent — header bar + zebra band tint
    accent_css = f"""
    .data-table th {{ background: {accent}; }}
    """

    body_cls = "body-wide" if report_key == "monthly" else ""
    return f'<section class="{body_cls}">{page_head}{section_h}{table}<style>{accent_css}</style></section>'


# ════════════════════════════════════════════════════════════════════════════
# Public renderer
# ════════════════════════════════════════════════════════════════════════════


def render_pdf(
    report_key: str,
    shaped_rows: list[dict],
    summary: dict,
    meta_arg: dict,
) -> bytes:
    """Render the requested report as a PDF byte string.

    ``meta_arg`` is the dict returned by ``data.report_meta(key)`` plus a
    ``period`` sub-dict with ``from`` and ``to`` date objects. The caller
    (router) assembles this and passes it through.
    """
    # Bootstrap GTK on Windows (no-op on Linux/macOS); deferred so the backend
    # boots before WeasyPrint is needed.
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML, CSS  # noqa: WPS433

    theme = report_meta(report_key)
    motif = theme["motif"]
    period = meta_arg["period"]

    cover_fn = COVER_RENDERERS.get(motif, _cover_editorial)
    cover_html = cover_fn(theme, summary, period, shaped_count=len(shaped_rows))
    body_html = _body_pages(report_key, shaped_rows, summary, theme, period)

    base_css = (
        _BASE_CSS
        .replace("{COMPANY_LEGAL}", COMPANY["legal"])
        .replace("{COMPANY_WEB}", COMPANY["web"])
    )

    full = f"""
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <title>{_esc(theme['name'])} · {COMPANY['name']}</title>
        <style>{base_css}</style>
    </head>
    <body>
        {cover_html}
        {body_html}
    </body>
    </html>
    """

    return HTML(string=full).write_pdf()
