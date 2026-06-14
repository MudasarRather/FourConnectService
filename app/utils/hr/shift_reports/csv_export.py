"""HR Shift Reports — CSV export (RFC-4180, UTF-8 BOM for Excel)."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from .data import report_meta

_COLS = {
    "roster": [
        ("Code", "employee_code"), ("Employee", "employee_name"), ("Department", "department"),
        ("Shift code", "shift_code"), ("Shift", "shift_name"), ("Type", "shift_type"),
        ("Window", "window"), ("From", "effective_from"), ("Until", "effective_until"),
    ],
    "coverage": [
        ("Shift code", "shift_code"), ("Shift", "shift_name"), ("Department", "department"),
        ("Post", "label"), ("Required", "min_staff"), ("Assigned", "assigned"),
        ("Shortfall", "shortfall"), ("Coverage %", "coverage_pct"), ("Critical", "critical"), ("Status", "status"),
    ],
    "overtime": [
        ("Code", "employee_code"), ("Employee", "employee_name"), ("Department", "department"),
        ("OT events", "occurrences"), ("OT hours", "ot_hours"), ("Payable hours", "payable_hours"),
        ("Peak multiplier", "peak_mult"), ("Weighted hours", "weighted_hours"), ("Est. cost", "est_cost"),
    ],
    "night": [
        ("Code", "employee_code"), ("Employee", "employee_name"), ("Department", "department"),
        ("Shift code", "shift_code"), ("Shift", "shift_name"), ("Window", "window"),
        ("Allowance", "allowance"), ("OT rate", "ot_rate"), ("Transport", "transport"), ("Meal", "meal"),
    ],
    "rotation": [
        ("Rotation", "name"), ("Code", "code"), ("Cycle", "cycle"), ("Every (days)", "frequency_days"),
        ("Steps", "steps"), ("Crew", "members"), ("Pattern", "step_shifts"),
        ("Current step", "current_step"), ("Current shift", "current_label"),
        ("Anchor", "anchor_date"), ("Last advanced", "last_advanced"), ("Departments", "departments"),
    ],
    "workforce": [
        ("Shift code", "shift_code"), ("Shift", "shift_name"), ("Department", "department"),
        ("Skill", "skill"), ("Required", "required"), ("Assigned", "assigned"),
        ("Shortfall", "shortfall"), ("Surplus", "surplus"), ("Coverage %", "coverage_pct"),
        ("Valid from", "valid_from"), ("Valid to", "valid_to"), ("Status", "status"),
    ],
}


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return str(v)


def render_csv(report_key: str, rows: list, summary: dict, meta: dict) -> bytes:
    theme = report_meta(report_key)
    cols = _COLS.get(report_key, _COLS["roster"])
    period = meta.get("period") or meta
    frm, to = period["from"], period["to"]

    buf = io.StringIO()
    buf.write(f"# Fourreck Shifts — {theme['name']}\n")
    buf.write(f"# {theme['tagline']}\n")
    buf.write(f"# Period: {frm.isoformat()} to {to.isoformat()}\n")
    buf.write(f"# Generated: {datetime.now().isoformat(timespec='seconds')}\n")
    buf.write(f"# Records: {summary.get('rows', len(rows))}\n#\n")

    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow([c[0] for c in cols])
    for r in rows:
        w.writerow([_fmt(r.get(c[1])) for c in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")
