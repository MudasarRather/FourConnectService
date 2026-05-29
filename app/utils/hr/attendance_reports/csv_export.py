"""CSV export — UTF-8 with BOM, RFC-4180 quoting, per-report column set.

CSVs are deliberately *unstyled* — they're for downstream pipelines, not for
human consumption. Each row's raw numbers are written as-is so spreadsheets
can sum them; ISO timestamps for date/time columns.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, date as date_cls
from typing import Iterable

from .data import report_meta


def _columns(key: str) -> list[tuple[str, str]]:
    """Return [(label, accessor)] tuples per report key."""
    if key == "monthly":
        return [
            ("Code", "employee_code"),
            ("Employee", "employee_name"),
            ("Department", "department"),
            ("Designation", "designation"),
            ("Shift", "shift_name"),
            ("Present days", "present_days"),
            ("Late days", "late_days"),
            ("Absent days", "absent_days"),
            ("Half-days", "half_days"),
            ("Leave days", "leave_days"),
            ("WFH days", "wfh_days"),
            ("Week-offs", "week_offs"),
            ("Holidays", "holidays"),
            ("Working hours", "total_working_hours"),
            ("Break hours", "total_break_hours"),
            ("Late minutes", "total_late_minutes"),
            ("Overtime hours", "total_overtime_hours"),
        ]
    if key == "late":
        return [
            ("Date", "date"), ("Code", "employee_code"), ("Employee", "employee_name"),
            ("Department", "department"), ("Shift", "shift_name"),
            ("Check-in", "check_in_time"),
            ("Late minutes", "late_minutes"),
            ("Status", "status"), ("Geo-verified", "geo_verified"),
        ]
    if key == "overtime":
        return [
            ("Date", "date"), ("Code", "employee_code"), ("Employee", "employee_name"),
            ("Department", "department"), ("Shift", "shift_name"),
            ("Check-in", "check_in_time"), ("Check-out", "check_out_time"),
            ("Working hours", "working_hours"),
            ("Overtime hours", "overtime_hours"),
            ("Status", "status"),
        ]
    if key == "wfh":
        return [
            ("Date", "date"), ("Code", "employee_code"), ("Employee", "employee_name"),
            ("Department", "department"),
            ("Check-in", "check_in_time"), ("Check-out", "check_out_time"),
            ("Working hours", "working_hours"),
            ("Overtime hours", "overtime_hours"),
            ("Status", "status"),
        ]
    if key == "compliance":
        return [
            ("Code", "employee_code"), ("Employee", "employee_name"),
            ("Department", "department"), ("Shift", "shift_name"),
            ("Scheduled days", "scheduled_days"),
            ("Actual hours", "actual_hours"),
            ("Expected hours", "expected_hours"),
            ("Coverage %", "coverage_pct"),
            ("Gap hours", "gap_hours"),
            ("Missing punch days", "missing_punch_days"),
            ("Geo-failed days", "geo_failed_days"),
        ]
    if key == "anomalies":
        return [
            ("Date", "date"), ("Code", "employee_code"), ("Employee", "employee_name"),
            ("Department", "department"), ("Status", "status"),
            ("Check-in", "check_in_time"), ("Check-out", "check_out_time"),
            ("Late minutes", "late_minutes"),
            ("Geo-verified", "geo_verified"),
            ("Flagged", "is_flagged"),
            ("Reasons", "reasons"),
        ]
    if key == "breaks":
        return [
            ("Date", "date"), ("Code", "employee_code"), ("Employee", "employee_name"),
            ("Department", "department"), ("Shift", "shift_name"),
            ("Working hours", "working_hours"),
            ("Break hours", "break_hours"),
            ("Break minutes", "break_minutes"),
            ("Ratio %", "break_ratio_pct"),
            ("Length", "intensity"),
            ("Status", "status"),
        ]
    # daily — break_hours already wired
    return [
        ("Date", "date"), ("Code", "employee_code"), ("Employee", "employee_name"),
        ("Department", "department"), ("Designation", "designation"),
        ("Shift", "shift_name"),
        ("Check-in", "check_in_time"), ("Check-out", "check_out_time"),
        ("Working hours", "working_hours"),
        ("Break hours", "break_hours"),
        ("Late minutes", "late_minutes"),
        ("Early exit minutes", "early_exit_minutes"),
        ("Overtime hours", "overtime_hours"),
        ("Status", "status"),
        ("Source", "source"),
        ("Geo-verified", "geo_verified"),
        ("Flagged", "is_flagged"),
    ]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date_cls):
        return v.isoformat()
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return v


def render_csv(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta(report_key)
    cols = _columns(report_key)
    # ``meta`` is the dict the router builds — ``{"period": {"from": ..., "to": ...}}``
    period = meta.get("period") or meta
    from_d = period["from"]
    to_d = period["to"]
    buf = io.StringIO()
    # Manual lead-in so consumers can see what's in the file.
    buf.write(f"# Fourreck Attendance — {theme['name']}\n")
    buf.write(f"# Period: {from_d.isoformat()} to {to_d.isoformat()}\n")
    buf.write(f"# Generated: {datetime.now().isoformat(timespec='seconds')}\n")
    buf.write(
        f"# Rows: {summary['rows']} | Employees: {summary['employees']} | "
        f"On-time: {summary['on_time_pct']}%\n#\n"
    )
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([c[0] for c in cols])
    for row in shaped_rows:
        writer.writerow([_fmt(row.get(c[1])) for c in cols])
    # UTF-8 BOM so Excel opens it with the right encoding
    return ("﻿" + buf.getvalue()).encode("utf-8")
