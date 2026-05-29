"""Excel exports for HR Attendance Reports.

Seven reports, seven layouts. We use both libraries:

    * ``xlsxwriter`` — for the four reports that need native charts or
      sparklines (overtime gauges, daily roster pivot, late heat-map,
      monthly summary bars). xlsxwriter writes a fresh workbook with rich
      conditional formats.
    * ``openpyxl`` — for the three reports that benefit from formula
      power and a more spreadsheet-native feel (compliance audit with
      traffic-light formatting, anomalies dossier with row tints, WFH
      remote-work journal with merged headers).

Each renderer takes the shaped row list and the summary dict and returns
``bytes`` (the .xlsx blob) so the router can wrap it in a Response.

Design philosophy:
    * Frozen header row on every sheet.
    * Autofilter on the main table.
    * One title band per report, themed to match the PDF cover accent.
    * Conditional formats highlight outliers without burying them.
"""
from __future__ import annotations

import io
from datetime import datetime, date as date_cls

# Lazy-imported in render() so missing deps fail loudly only at export time.

from .data import report_meta, STATUS_COLORS


COMPANY = {
    "name": "Fourreck",
    "legal": "Fourreck Technologies Pvt. Ltd.",
    "web": "fourreck.com",
}


# ════════════════════════════════════════════════════════════════════════════
# Dispatch
# ════════════════════════════════════════════════════════════════════════════


def render_excel(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> bytes:
    fn = RENDERERS.get(report_key, _excel_daily)
    return fn(shaped_rows, summary, meta)


# ════════════════════════════════════════════════════════════════════════════
# xlsxwriter helpers
# ════════════════════════════════════════════════════════════════════════════


def _xw_workbook():
    """Construct an xlsxwriter workbook backed by an in-memory buffer.

    ``remove_timezone=True`` is essential — our check_in_time / check_out_time
    columns come back from PostgreSQL as IST-aware datetimes, but Excel has no
    timezone concept so xlsxwriter raises TypeError on tz-aware input. The
    flag strips tzinfo at write time (preserving the wall-clock value, which
    is what HR cares about anyway).
    """
    import xlsxwriter
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {
        "in_memory": True,
        "default_date_format": "yyyy-mm-dd",
        "remove_timezone": True,
    })
    return wb, buf


def _xw_finalize(wb, buf) -> bytes:
    wb.close()
    buf.seek(0)
    return buf.read()


def _fmt_time(v):
    if isinstance(v, datetime):
        return v.strftime("%I:%M %p").lstrip("0")
    if v is None:
        return ""
    return str(v)


def _coverage_pct(v):
    """Force coverage_pct values to be plain numbers for conditional formatting."""
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return 0


# ════════════════════════════════════════════════════════════════════════════
# 1. Monthly Summary — xlsxwriter with embedded BAR CHART
# ════════════════════════════════════════════════════════════════════════════


def _excel_monthly(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("monthly")
    accent = theme["accent"]
    accent_soft = theme["accent_soft"]
    deep = theme["accent_deep"]
    period = meta["period"]

    wb, buf = _xw_workbook()
    ws = wb.add_worksheet("Monthly Summary")
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    # ── Formats ────────────────────────────────────────────────────────
    f_title = wb.add_format({
        "bold": True, "font_size": 22, "font_color": "#FFFFFF",
        "bg_color": accent, "align": "left", "valign": "vcenter",
        "indent": 1, "font_name": "Calibri",
    })
    f_sub = wb.add_format({
        "italic": True, "font_size": 11, "font_color": "#1a1410",
        "bg_color": accent_soft, "align": "left", "valign": "vcenter", "indent": 1,
    })
    f_period = wb.add_format({
        "font_size": 9, "font_color": "#786c5c", "bg_color": "#fffdf5",
        "align": "left", "valign": "vcenter", "indent": 1,
    })
    f_kpi_label = wb.add_format({
        "font_size": 8, "font_color": "#786c5c", "bold": True,
        "bg_color": "#fffdf5", "align": "center", "border": 1, "border_color": "#e6e1d7",
        "top": 2, "top_color": accent,
    })
    f_kpi_value = wb.add_format({
        "font_size": 18, "font_color": deep, "bold": True,
        "bg_color": "#fffdf5", "align": "center", "border": 1, "border_color": "#e6e1d7",
        "num_format": "0",
    })
    f_header = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "left", "valign": "vcenter", "font_size": 10, "border": 0,
        "indent": 1, "bottom": 2, "bottom_color": deep,
    })
    f_header_r = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "right", "valign": "vcenter", "font_size": 10,
        "indent": 1, "bottom": 2, "bottom_color": deep,
    })
    f_cell = wb.add_format({"font_size": 10, "align": "left", "indent": 1, "border": 0, "bottom": 1, "bottom_color": "#ece6d7"})
    f_cell_zebra = wb.add_format({"font_size": 10, "align": "left", "indent": 1, "bg_color": "#fffdf5", "bottom": 1, "bottom_color": "#ece6d7"})
    f_num = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0", "bottom": 1, "bottom_color": "#ece6d7"})
    f_num_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0", "bg_color": "#fffdf5", "bottom": 1, "bottom_color": "#ece6d7"})
    f_hrs = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0.00\" h\"", "bottom": 1, "bottom_color": "#ece6d7"})
    f_hrs_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0.00\" h\"", "bg_color": "#fffdf5", "bottom": 1, "bottom_color": "#ece6d7"})

    # ── Layout ─────────────────────────────────────────────────────────
    ws.set_column("A:A", 12)   # code
    ws.set_column("B:B", 26)   # name
    ws.set_column("C:C", 22)   # department
    ws.set_column("D:L", 12)   # numeric

    ws.set_row(0, 38)
    ws.merge_range("A1:L1", f" {COMPANY['name'].upper()}   ·   {theme['name'].upper()}", f_title)
    ws.set_row(1, 24)
    ws.merge_range("A2:L2", f"  {theme['subtitle']}", f_sub)
    ws.set_row(2, 18)
    ws.merge_range(
        "A3:L3",
        f"  Period  {period['from'].strftime('%d %b %Y')}  →  {period['to'].strftime('%d %b %Y')}    ·    "
        f"Generated  {datetime.now().strftime('%d %b %Y %I:%M %p')}",
        f_period,
    )

    # KPI strip
    ws.set_row(4, 8)  # spacer
    kpis = [
        ("EMPLOYEES", summary["employees"]),
        ("PRESENT", summary["present"]),
        ("LATE EVENTS", summary["late"]),
        ("ABSENT", summary["absent"]),
        ("ON-TIME %", summary["on_time_pct"]),
        ("OVERTIME HRS", round(summary["overtime_hours"], 1)),
    ]
    ws.set_row(5, 18)
    ws.set_row(6, 28)
    for i, (lbl, val) in enumerate(kpis):
        # Each KPI tile spans ~2 columns; total 6 tiles = 12 cols, we use 11+spacer
        c0 = i * 2 if i < 4 else i * 2  # contiguous
        if c0 >= 12:
            break
        c1 = min(c0 + 1, 11)
        ws.merge_range(5, c0, 5, c1, lbl, f_kpi_label)
        ws.merge_range(6, c0, 6, c1, val, f_kpi_value)

    # Table header
    start_row = 9
    headers = [
        ("Code", "left"), ("Employee", "left"), ("Department", "left"),
        ("Present", "right"), ("Late", "right"), ("Absent", "right"),
        ("WFH", "right"), ("Leave", "right"),
        ("Working hrs", "right"), ("Break hrs", "right"),
        ("OT hrs", "right"), ("Late mins", "right"),
    ]
    for i, (h, a) in enumerate(headers):
        ws.write(start_row, i, h, f_header_r if a == "right" else f_header)
    ws.set_row(start_row, 26)
    ws.freeze_panes(start_row + 1, 0)

    # Body
    for ri, r in enumerate(rows):
        row_idx = start_row + 1 + ri
        zebra = ri % 2 == 1
        ws.set_row(row_idx, 22)
        ws.write(row_idx, 0, r["employee_code"], f_cell_zebra if zebra else f_cell)
        ws.write(row_idx, 1, r["employee_name"], f_cell_zebra if zebra else f_cell)
        ws.write(row_idx, 2, r["department"], f_cell_zebra if zebra else f_cell)
        ws.write_number(row_idx, 3, r["present_days"], f_num_z if zebra else f_num)
        ws.write_number(row_idx, 4, r["late_days"], f_num_z if zebra else f_num)
        ws.write_number(row_idx, 5, r["absent_days"], f_num_z if zebra else f_num)
        ws.write_number(row_idx, 6, r["wfh_days"], f_num_z if zebra else f_num)
        ws.write_number(row_idx, 7, r["leave_days"], f_num_z if zebra else f_num)
        ws.write_number(row_idx, 8, r["total_working_hours"], f_hrs_z if zebra else f_hrs)
        ws.write_number(row_idx, 9, r["total_break_hours"], f_hrs_z if zebra else f_hrs)
        ws.write_number(row_idx, 10, r["total_overtime_hours"], f_hrs_z if zebra else f_hrs)
        ws.write_number(row_idx, 11, r["total_late_minutes"], f_num_z if zebra else f_num)

    # Conditional formats — heat the Late & Absent columns
    last_row = start_row + len(rows)
    if rows:
        ws.conditional_format(start_row + 1, 4, last_row, 4, {
            "type": "3_color_scale",
            "min_color": "#ffffff", "mid_color": "#fef9c3", "max_color": "#f59e0b",
        })
        ws.conditional_format(start_row + 1, 5, last_row, 5, {
            "type": "3_color_scale",
            "min_color": "#ffffff", "mid_color": "#fecaca", "max_color": "#b91c1c",
        })
        # Break hours data bar
        ws.conditional_format(start_row + 1, 9, last_row, 9, {
            "type": "data_bar", "bar_color": "#0284c7", "bar_solid": False,
        })
        ws.conditional_format(start_row + 1, 10, last_row, 10, {
            "type": "data_bar", "bar_color": "#fb923c", "bar_solid": True,
        })
        ws.autofilter(start_row, 0, last_row, len(headers) - 1)

    # ─── Embedded chart sheet ───
    if rows:
        chart = wb.add_chart({"type": "bar"})
        chart.add_series({
            "name": "Working hours",
            "categories": ["Monthly Summary", start_row + 1, 1, last_row, 1],
            "values":     ["Monthly Summary", start_row + 1, 8, last_row, 8],
            "fill": {"color": accent},
            "border": {"color": deep},
        })
        chart.add_series({
            "name": "Break hours",
            "categories": ["Monthly Summary", start_row + 1, 1, last_row, 1],
            "values":     ["Monthly Summary", start_row + 1, 9, last_row, 9],
            "fill": {"color": "#0284c7"},
            "border": {"color": "#0c4a6e"},
        })
        chart.add_series({
            "name": "Overtime hours",
            "categories": ["Monthly Summary", start_row + 1, 1, last_row, 1],
            "values":     ["Monthly Summary", start_row + 1, 10, last_row, 10],
            "fill": {"color": "#ea580c"},
            "border": {"color": "#7c2d12"},
        })
        chart.set_title({"name": "Working hours vs Overtime", "name_font": {"size": 13, "bold": True, "color": deep}})
        chart.set_x_axis({"name": "Hours", "num_format": "0"})
        chart.set_y_axis({"name": "Employee"})
        chart.set_legend({"position": "bottom"})
        chart.set_size({"width": 760, "height": max(360, 40 + 30 * len(rows))})

        chart_ws = wb.add_worksheet("Chart · Hours")
        chart_ws.set_tab_color("#fb923c")
        chart_ws.hide_gridlines(2)
        chart_ws.insert_chart("B2", chart)

    return _xw_finalize(wb, buf)


# ════════════════════════════════════════════════════════════════════════════
# 2. Late Arrivals — xlsxwriter with HEAT-MAP scale
# ════════════════════════════════════════════════════════════════════════════


def _excel_late(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("late")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]

    wb, buf = _xw_workbook()
    ws = wb.add_worksheet("Late Arrivals")
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    # Bulletin-style title
    f_band_top = wb.add_format({
        "bold": True, "font_size": 8,
        "font_color": "#fde68a", "bg_color": "#1a1410",
        "align": "center", "valign": "vcenter",
    })
    f_title = wb.add_format({
        "bold": True, "font_size": 32, "font_name": "Georgia",
        "font_color": "#1a1410", "bg_color": theme["accent_soft"],
        "align": "center", "valign": "vcenter",
    })
    f_sub = wb.add_format({
        "italic": True, "font_size": 11, "font_color": "#4b5563",
        "bg_color": theme["accent_soft"], "align": "center", "valign": "vcenter",
    })
    f_ticker = wb.add_format({
        "bold": True, "font_size": 10, "font_color": "#fde68a",
        "bg_color": "#1a1410", "font_name": "Consolas", "align": "center", "valign": "vcenter",
    })

    f_header = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "left", "valign": "vcenter", "indent": 1,
        "border_color": deep, "bottom": 2, "bottom_color": deep, "font_size": 10,
    })
    f_header_r = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "right", "valign": "vcenter", "indent": 1,
        "bottom": 2, "bottom_color": deep, "font_size": 10,
    })
    f_cell = wb.add_format({"font_size": 10, "indent": 1, "bottom": 1, "bottom_color": "#ece6d7"})
    f_cell_z = wb.add_format({"font_size": 10, "indent": 1, "bg_color": "#fefdf8", "bottom": 1, "bottom_color": "#ece6d7"})
    f_num = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0", "bottom": 1, "bottom_color": "#ece6d7"})
    f_num_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0", "bg_color": "#fefdf8", "bottom": 1, "bottom_color": "#ece6d7"})
    f_date = wb.add_format({"font_size": 10, "indent": 1, "num_format": "dd mmm yyyy", "bottom": 1, "bottom_color": "#ece6d7"})
    f_date_z = wb.add_format({"font_size": 10, "indent": 1, "num_format": "dd mmm yyyy", "bg_color": "#fefdf8", "bottom": 1, "bottom_color": "#ece6d7"})
    f_time = wb.add_format({"font_size": 10, "indent": 1, "num_format": "hh:mm AM/PM", "bottom": 1, "bottom_color": "#ece6d7"})
    f_time_z = wb.add_format({"font_size": 10, "indent": 1, "num_format": "hh:mm AM/PM", "bg_color": "#fefdf8", "bottom": 1, "bottom_color": "#ece6d7"})

    ws.set_column("A:A", 13)
    ws.set_column("B:B", 12)
    ws.set_column("C:C", 26)
    ws.set_column("D:D", 22)
    ws.set_column("E:E", 18)
    ws.set_column("F:F", 13)
    ws.set_column("G:G", 12)
    ws.set_column("H:H", 14)

    ws.set_row(0, 16)
    ws.merge_range("A1:H1", "·  P U N C T U A L I T Y   B U L L E T I N  ·", f_band_top)
    ws.set_row(1, 46)
    ws.merge_range("A2:H2", "LATE ARRIVALS", f_title)
    ws.set_row(2, 22)
    ws.merge_range("A3:H3", theme["subtitle"], f_sub)
    ws.set_row(3, 22)
    ws.merge_range(
        "A4:H4",
        f"▶ {summary['late']} BREACHES · {summary['late_minutes']} TOTAL LATE MINUTES · "
        f"{summary['employees']} EMPLOYEES · "
        f"{period['from'].strftime('%d %b').upper()} – {period['to'].strftime('%d %b %Y').upper()}  ◀",
        f_ticker,
    )

    # Headers
    headers = ["Date", "Code", "Employee", "Department", "Shift", "Check-in", "Late mins", "Status"]
    start_row = 5
    ws.set_row(start_row, 24)
    for i, h in enumerate(headers):
        ws.write(start_row, i, h, f_header_r if h == "Late mins" else f_header)
    ws.freeze_panes(start_row + 1, 0)

    # Body
    for ri, r in enumerate(rows):
        row_idx = start_row + 1 + ri
        z = ri % 2 == 1
        ws.set_row(row_idx, 18)
        ws.write_datetime(row_idx, 0, _to_dt(r["date"]), f_date_z if z else f_date)
        ws.write(row_idx, 1, r["employee_code"], f_cell_z if z else f_cell)
        ws.write(row_idx, 2, r["employee_name"], f_cell_z if z else f_cell)
        ws.write(row_idx, 3, r["department"], f_cell_z if z else f_cell)
        ws.write(row_idx, 4, r["shift_name"], f_cell_z if z else f_cell)
        if r.get("check_in_time"):
            ws.write_datetime(row_idx, 5, r["check_in_time"], f_time_z if z else f_time)
        else:
            ws.write(row_idx, 5, "", f_cell_z if z else f_cell)
        ws.write_number(row_idx, 6, r["late_minutes"], f_num_z if z else f_num)
        _write_status_pill(wb, ws, row_idx, 7, r["status"], z)

    last_row = start_row + len(rows)
    if rows:
        # Heat-map on late minutes
        ws.conditional_format(start_row + 1, 6, last_row, 6, {
            "type": "3_color_scale",
            "min_type": "num", "min_value": 0, "min_color": "#ffffff",
            "mid_type": "num", "mid_value": 30, "mid_color": "#fef9c3",
            "max_type": "num", "max_value": 90, "max_color": "#b91c1c",
        })
        # Data bar
        ws.conditional_format(start_row + 1, 6, last_row, 6, {
            "type": "data_bar", "bar_color": "#ca8a04", "bar_only": False,
        })
        ws.autofilter(start_row, 0, last_row, len(headers) - 1)

    return _xw_finalize(wb, buf)


# ════════════════════════════════════════════════════════════════════════════
# 3. Overtime — xlsxwriter with embedded LINE CHART of OT per day
# ════════════════════════════════════════════════════════════════════════════


def _excel_overtime(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("overtime")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]

    wb, buf = _xw_workbook()
    ws = wb.add_worksheet("Overtime")
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    # Industrial dashboard band
    f_title = wb.add_format({
        "bold": True, "font_size": 30, "font_color": "#fde68a", "bg_color": "#0c0a09",
        "align": "left", "indent": 1, "valign": "vcenter",
    })
    f_kpi_card_label = wb.add_format({
        "bold": True, "font_size": 8, "font_color": "#9ca3af",
        "bg_color": "#0c0a09", "align": "center", "border": 0,
        "font_name": "Consolas", "valign": "vcenter",
    })
    f_kpi_card_val = wb.add_format({
        "bold": True, "font_size": 22, "font_color": accent,
        "bg_color": "#0c0a09", "align": "center", "border": 0,
        "font_name": "Consolas", "valign": "vcenter", "num_format": "0.0",
    })
    f_header = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "left", "indent": 1, "font_size": 10,
        "bottom": 2, "bottom_color": deep, "valign": "vcenter",
    })
    f_header_r = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "right", "indent": 1, "font_size": 10,
        "bottom": 2, "bottom_color": deep, "valign": "vcenter",
    })
    f_cell = wb.add_format({"font_size": 10, "indent": 1, "bottom": 1, "bottom_color": "#ffe7d4"})
    f_cell_z = wb.add_format({"font_size": 10, "indent": 1, "bg_color": "#fff7ed", "bottom": 1, "bottom_color": "#ffe7d4"})
    f_hrs = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0.00\" h\"", "bottom": 1, "bottom_color": "#ffe7d4"})
    f_hrs_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0.00\" h\"", "bg_color": "#fff7ed", "bottom": 1, "bottom_color": "#ffe7d4"})
    f_date = wb.add_format({"font_size": 10, "indent": 1, "num_format": "dd mmm yyyy", "bottom": 1, "bottom_color": "#ffe7d4"})
    f_date_z = wb.add_format({"font_size": 10, "indent": 1, "num_format": "dd mmm yyyy", "bg_color": "#fff7ed", "bottom": 1, "bottom_color": "#ffe7d4"})
    f_time = wb.add_format({"font_size": 10, "indent": 1, "num_format": "hh:mm AM/PM", "bottom": 1, "bottom_color": "#ffe7d4"})
    f_time_z = wb.add_format({"font_size": 10, "indent": 1, "num_format": "hh:mm AM/PM", "bg_color": "#fff7ed", "bottom": 1, "bottom_color": "#ffe7d4"})

    ws.set_column("A:A", 13)
    ws.set_column("B:B", 12)
    ws.set_column("C:C", 26)
    ws.set_column("D:D", 22)
    ws.set_column("E:E", 18)
    ws.set_column("F:F", 13)
    ws.set_column("G:H", 13)
    ws.set_column("I:I", 14)

    ws.set_row(0, 44)
    ws.merge_range("A1:I1", "  OPERATIONS · OVERTIME DASHBOARD", f_title)

    # KPI cards
    ws.set_row(1, 18)
    ws.set_row(2, 28)
    cards = [
        ("RECORDS", float(summary["rows"])),
        ("EMPLOYEES", float(summary["employees"])),
        ("ON-TIME %", float(summary["on_time_pct"])),
        ("TOTAL OT", float(summary["overtime_hours"])),
    ]
    cols_per = 9 // len(cards)  # ~2 each but cards span ~2.25 cols
    for i, (lbl, val) in enumerate(cards):
        c0 = i * cols_per
        c1 = min(c0 + cols_per - 1, 8)
        if i == len(cards) - 1:
            c1 = 8
        ws.merge_range(1, c0, 1, c1, lbl, f_kpi_card_label)
        ws.merge_range(2, c0, 2, c1, val, f_kpi_card_val)

    # Headers
    headers = ["Date", "Code", "Employee", "Department", "Shift",
               "Check-in", "Check-out", "OT hours", "Working hrs"]
    start_row = 5
    ws.set_row(start_row, 24)
    for i, h in enumerate(headers):
        ws.write(start_row, i, h, f_header_r if h in ("OT hours", "Working hrs") else f_header)
    ws.freeze_panes(start_row + 1, 0)

    for ri, r in enumerate(rows):
        row_idx = start_row + 1 + ri
        z = ri % 2 == 1
        ws.set_row(row_idx, 18)
        ws.write_datetime(row_idx, 0, _to_dt(r["date"]), f_date_z if z else f_date)
        ws.write(row_idx, 1, r["employee_code"], f_cell_z if z else f_cell)
        ws.write(row_idx, 2, r["employee_name"], f_cell_z if z else f_cell)
        ws.write(row_idx, 3, r["department"], f_cell_z if z else f_cell)
        ws.write(row_idx, 4, r["shift_name"], f_cell_z if z else f_cell)
        if r.get("check_in_time"):
            ws.write_datetime(row_idx, 5, r["check_in_time"], f_time_z if z else f_time)
        else:
            ws.write(row_idx, 5, "", f_cell_z if z else f_cell)
        if r.get("check_out_time"):
            ws.write_datetime(row_idx, 6, r["check_out_time"], f_time_z if z else f_time)
        else:
            ws.write(row_idx, 6, "", f_cell_z if z else f_cell)
        ws.write_number(row_idx, 7, r["overtime_hours"], f_hrs_z if z else f_hrs)
        ws.write_number(row_idx, 8, r["working_hours"], f_hrs_z if z else f_hrs)

    last_row = start_row + len(rows)
    if rows:
        ws.conditional_format(start_row + 1, 7, last_row, 7, {
            "type": "data_bar", "bar_color": accent, "bar_solid": True,
        })
        ws.conditional_format(start_row + 1, 8, last_row, 8, {
            "type": "data_bar", "bar_color": "#fb923c", "bar_solid": False,
        })
        ws.autofilter(start_row, 0, last_row, len(headers) - 1)

        # Embedded column chart on a second sheet
        chart_ws = wb.add_worksheet("Chart · OT by employee")
        chart_ws.set_tab_color("#fb923c")
        chart_ws.hide_gridlines(2)

        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name": "OT hours",
            "categories": ["Overtime", start_row + 1, 2, last_row, 2],
            "values":     ["Overtime", start_row + 1, 7, last_row, 7],
            "fill": {"color": accent},
            "border": {"color": deep},
            "data_labels": {"value": True, "num_format": "0.0", "font": {"size": 8}},
        })
        chart.set_title({"name": "Overtime hours per employee", "name_font": {"size": 13, "bold": True, "color": deep}})
        chart.set_x_axis({"name": "Employee"})
        chart.set_y_axis({"name": "OT hours", "num_format": "0.0"})
        chart.set_legend({"none": True})
        chart.set_size({"width": 840, "height": 480})
        chart_ws.insert_chart("B2", chart)

    return _xw_finalize(wb, buf)


# ════════════════════════════════════════════════════════════════════════════
# 4. WFH — openpyxl with calendar-feel merged header
# ════════════════════════════════════════════════════════════════════════════


def _excel_wfh(rows: list[dict], summary: dict, meta: dict) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    theme = report_meta("wfh")
    accent = theme["accent"].lstrip("#")
    accent_soft = theme["accent_soft"].lstrip("#")
    deep = theme["accent_deep"].lstrip("#")
    period = meta["period"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Work From Home"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = accent

    thin = Side(style="thin", color="EAE6D5")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Title band — postcard style
    ws.merge_cells("A1:H1")
    ws["A1"] = "  ✉  GREETINGS FROM THE HOME OFFICE"
    ws["A1"].font = Font(name="Georgia", size=22, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor=accent)
    ws["A1"].alignment = Alignment(vertical="center", horizontal="left", indent=1)
    ws.row_dimensions[1].height = 42

    ws.merge_cells("A2:H2")
    ws["A2"] = theme["subtitle"]
    ws["A2"].font = Font(size=11, italic=True, color="4B5563")
    ws["A2"].fill = PatternFill("solid", fgColor=accent_soft)
    ws["A2"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[2].height = 22

    ws.merge_cells("A3:H3")
    ws["A3"] = (
        f"   ·   Period  {period['from'].strftime('%d %b %Y')}  →  {period['to'].strftime('%d %b %Y')}   ·   "
        f"{summary['wfh']} WFH days   ·   {summary['employees']} employees   ·   "
        f"{summary['working_hours']:.1f}h logged   ·"
    )
    ws["A3"].font = Font(size=10, bold=True, color=deep)
    ws["A3"].fill = PatternFill("solid", fgColor="FFFAF0")
    ws["A3"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[3].height = 22

    # KPI tiles row
    kpis = [
        ("WFH days",     summary["wfh"]),
        ("Remote days",  summary["remote"]),
        ("Employees",    summary["employees"]),
        ("Departments",  summary["departments"]),
        ("Working hrs",  summary["working_hours"]),
        ("OT hrs",       summary["overtime_hours"]),
    ]
    ws.row_dimensions[4].height = 12
    for i, (lbl, val) in enumerate(kpis):
        col_lbl = get_column_letter(i + 1)
        # Header label
        c_label = ws.cell(row=5, column=i + 1, value=lbl.upper())
        c_label.font = Font(size=8, bold=True, color=deep)
        c_label.fill = PatternFill("solid", fgColor="FFFDF5")
        c_label.alignment = Alignment(vertical="center", horizontal="center")
        c_label.border = border
        # Value
        c_val = ws.cell(row=6, column=i + 1, value=val)
        c_val.font = Font(size=16, bold=True, color=accent, name="Georgia")
        c_val.fill = PatternFill("solid", fgColor="FFFDF5")
        c_val.alignment = Alignment(vertical="center", horizontal="center")
        c_val.border = border
    ws.row_dimensions[5].height = 16
    ws.row_dimensions[6].height = 28

    # Spacer
    ws.row_dimensions[7].height = 14

    # Headers
    headers = ["Date", "Code", "Employee", "Department", "Check-in", "Check-out", "Working hrs", "Status"]
    head_row = 8
    for i, h in enumerate(headers):
        c = ws.cell(row=head_row, column=i + 1, value=h.upper())
        c.font = Font(size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=accent)
        c.alignment = Alignment(vertical="center",
                                horizontal="right" if h == "Working hrs" else "left",
                                indent=1)
    ws.row_dimensions[head_row].height = 26
    ws.freeze_panes = f"A{head_row + 1}"

    # Body
    for ri, r in enumerate(rows):
        row_idx = head_row + 1 + ri
        is_zebra = ri % 2 == 1
        fill = PatternFill("solid", fgColor="F0F9FF") if is_zebra else PatternFill("solid", fgColor="FFFFFF")
        for col in range(1, 9):
            cell = ws.cell(row=row_idx, column=col)
            cell.font = Font(size=10, color="1A1410")
            cell.fill = fill
            cell.alignment = Alignment(vertical="center", indent=1)
            cell.border = Border(bottom=Side(style="thin", color="DDEEF6"))
        ws.cell(row=row_idx, column=1, value=r["date"]).number_format = "dd mmm yyyy"
        ws.cell(row=row_idx, column=2, value=r["employee_code"])
        ws.cell(row=row_idx, column=3, value=r["employee_name"])
        ws.cell(row=row_idx, column=4, value=r["department"])
        if r.get("check_in_time"):
            ws.cell(row=row_idx, column=5, value=r["check_in_time"]).number_format = "hh:mm AM/PM"
        if r.get("check_out_time"):
            ws.cell(row=row_idx, column=6, value=r["check_out_time"]).number_format = "hh:mm AM/PM"
        hr_cell = ws.cell(row=row_idx, column=7, value=r["working_hours"])
        hr_cell.number_format = '0.00" h"'
        hr_cell.alignment = Alignment(horizontal="right", vertical="center", indent=1)

        st = r["status"]
        sc = STATUS_COLORS.get(st, {"light": "#f1f5f9", "deep": "#334155"})
        scell = ws.cell(row=row_idx, column=8, value=st.replace("_", " "))
        scell.fill = PatternFill("solid", fgColor=sc["light"].lstrip("#"))
        scell.font = Font(size=10, bold=True, color=sc["deep"].lstrip("#"))
        scell.alignment = Alignment(horizontal="center", vertical="center")

    # Column widths
    widths = [14, 13, 28, 22, 13, 13, 14, 16]
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w

    # Autofilter
    if rows:
        ws.auto_filter.ref = f"A{head_row}:{get_column_letter(len(headers))}{head_row + len(rows)}"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ════════════════════════════════════════════════════════════════════════════
# 5. Compliance — openpyxl with TRAFFIC-LIGHT coverage column
# ════════════════════════════════════════════════════════════════════════════


def _excel_compliance(rows: list[dict], summary: dict, meta: dict) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import ColorScaleRule, CellIsRule

    theme = report_meta("compliance")
    accent = theme["accent"].lstrip("#")
    accent_soft = theme["accent_soft"].lstrip("#")
    deep = theme["accent_deep"].lstrip("#")
    period = meta["period"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Compliance Audit"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = accent

    # Certificate-style title
    ws.merge_cells("A1:I1")
    ws["A1"] = "  ◇  CERTIFICATE OF COMPLIANCE  ◇  ATTENDANCE AUDIT"
    ws["A1"].font = Font(name="Georgia", size=22, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor=accent)
    ws["A1"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[1].height = 42

    ws.merge_cells("A2:I2")
    ws["A2"] = f"  Attested for {COMPANY['legal']} · {theme['subtitle']}"
    ws["A2"].font = Font(name="Georgia", size=11, italic=True, color="4B5563")
    ws["A2"].fill = PatternFill("solid", fgColor=accent_soft)
    ws["A2"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[2].height = 22

    ws.merge_cells("A3:I3")
    ws["A3"] = (
        f"PERIOD  ◇  {period['from'].strftime('%d %B %Y')}  —  {period['to'].strftime('%d %B %Y')}   ·   "
        f"COMPILED  {datetime.now().strftime('%d %B %Y')}   ·   "
        f"REF  FRC/HR/COMP/{period['from'].year}/{datetime.now().strftime('%m%d%H%M')}"
    )
    ws["A3"].font = Font(size=9, color=deep, bold=True)
    ws["A3"].fill = PatternFill("solid", fgColor="FFFDF5")
    ws["A3"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[3].height = 22

    # KPI ribbon
    kpis = [
        ("EMPLOYEES",      summary["employees"]),
        ("DEPARTMENTS",    summary["departments"]),
        ("ON-TIME %",      f"{summary['on_time_pct']}%"),
        ("PRESENT",        summary["present"]),
        ("LATE",           summary["late"]),
        ("ABSENT",         summary["absent"]),
    ]
    ws.row_dimensions[4].height = 14
    for i, (lbl, val) in enumerate(kpis):
        col = i + 1 + ((9 - len(kpis)) // 2)  # center 6 tiles in 9 cols
        if col > 9:
            break
        ws.cell(row=5, column=col, value=lbl).font = Font(size=8, bold=True, color=deep)
        ws.cell(row=5, column=col).fill = PatternFill("solid", fgColor=accent_soft)
        ws.cell(row=5, column=col).alignment = Alignment(vertical="center", horizontal="center")
        v_cell = ws.cell(row=6, column=col, value=val)
        v_cell.font = Font(name="Georgia", size=15, bold=True, color=deep)
        v_cell.fill = PatternFill("solid", fgColor="FFFDF5")
        v_cell.alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[5].height = 16
    ws.row_dimensions[6].height = 26
    ws.row_dimensions[7].height = 14

    # Headers
    headers = [
        "Code", "Employee", "Department", "Shift",
        "Scheduled", "Actual hrs", "Expected hrs", "Coverage %", "Missing",
    ]
    head_row = 8
    for i, h in enumerate(headers):
        c = ws.cell(row=head_row, column=i + 1, value=h.upper())
        c.font = Font(size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=accent)
        c.alignment = Alignment(
            vertical="center",
            horizontal="right" if h in ("Scheduled", "Actual hrs", "Expected hrs", "Coverage %", "Missing") else "left",
            indent=1,
        )
    ws.row_dimensions[head_row].height = 26
    ws.freeze_panes = f"A{head_row + 1}"

    # Body
    for ri, r in enumerate(rows):
        row_idx = head_row + 1 + ri
        zebra = ri % 2 == 1
        bg = "F0FDFA" if zebra else "FFFFFF"
        bottom = Border(bottom=Side(style="thin", color="D6E9E5"))
        for col in range(1, 10):
            cell = ws.cell(row=row_idx, column=col)
            cell.font = Font(size=10, color="1A1410")
            cell.fill = PatternFill("solid", fgColor=bg)
            cell.alignment = Alignment(vertical="center", indent=1)
            cell.border = bottom
        ws.cell(row=row_idx, column=1, value=r["employee_code"])
        ws.cell(row=row_idx, column=2, value=r["employee_name"])
        ws.cell(row=row_idx, column=3, value=r["department"])
        ws.cell(row=row_idx, column=4, value=r["shift_name"])
        ws.cell(row=row_idx, column=5, value=r["scheduled_days"]).alignment = Alignment(horizontal="right", indent=1)
        c_actual = ws.cell(row=row_idx, column=6, value=r["actual_hours"])
        c_actual.number_format = '0.00" h"'
        c_actual.alignment = Alignment(horizontal="right", indent=1)
        c_exp = ws.cell(row=row_idx, column=7, value=r["expected_hours"])
        c_exp.number_format = '0.00" h"'
        c_exp.alignment = Alignment(horizontal="right", indent=1)
        c_cov = ws.cell(row=row_idx, column=8, value=r["coverage_pct"] / 100.0)
        c_cov.number_format = "0%"
        c_cov.alignment = Alignment(horizontal="right", indent=1)
        c_cov.font = Font(size=10, bold=True)
        c_miss = ws.cell(row=row_idx, column=9, value=r["missing_punch_days"])
        c_miss.alignment = Alignment(horizontal="right", indent=1)
        if r["missing_punch_days"] > 0:
            c_miss.fill = PatternFill("solid", fgColor="FEE2E2")
            c_miss.font = Font(size=10, bold=True, color="7F1D1D")

    if rows:
        last = head_row + len(rows)
        # Traffic-light gradient on coverage column
        rule = ColorScaleRule(
            start_type="num", start_value=0.5, start_color="B91C1C",
            mid_type="num", mid_value=0.85, mid_color="FACC15",
            end_type="num", end_value=1.0, end_color="0D9488",
        )
        ws.conditional_formatting.add(f"H{head_row + 1}:H{last}", rule)
        ws.auto_filter.ref = f"A{head_row}:I{last}"

    widths = [12, 26, 22, 18, 12, 14, 14, 14, 11]
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w

    # Signature block
    sig_row = head_row + len(rows) + 4
    ws.row_dimensions[sig_row].height = 26
    ws.merge_cells(start_row=sig_row, start_column=1, end_row=sig_row, end_column=4)
    ws.merge_cells(start_row=sig_row, start_column=6, end_row=sig_row, end_column=9)
    ws.cell(row=sig_row, column=1, value="").border = Border(top=Side(style="thin", color="1a1410"))
    ws.cell(row=sig_row + 1, column=1, value="Authorised by HR").alignment = Alignment(horizontal="center")
    ws.cell(row=sig_row + 1, column=1).font = Font(italic=True, color="4B5563", size=9, name="Georgia")
    ws.merge_cells(start_row=sig_row + 1, start_column=1, end_row=sig_row + 1, end_column=4)
    ws.cell(row=sig_row, column=6, value="").border = Border(top=Side(style="thin", color="1a1410"))
    ws.cell(row=sig_row + 1, column=6, value=f"Date · {datetime.now().strftime('%d %b %Y')}").alignment = Alignment(horizontal="center")
    ws.cell(row=sig_row + 1, column=6).font = Font(italic=True, color="4B5563", size=9, name="Georgia")
    ws.merge_cells(start_row=sig_row + 1, start_column=6, end_row=sig_row + 1, end_column=9)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ════════════════════════════════════════════════════════════════════════════
# 6. Anomalies — openpyxl with row-level severity tinting
# ════════════════════════════════════════════════════════════════════════════


def _excel_anomalies(rows: list[dict], summary: dict, meta: dict) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    theme = report_meta("anomalies")
    accent = theme["accent"].lstrip("#")
    accent_soft = theme["accent_soft"].lstrip("#")
    deep = theme["accent_deep"].lstrip("#")
    period = meta["period"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Anomalies Dossier"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = accent

    # Dossier-style header
    ws.merge_cells("A1:I1")
    ws["A1"] = "  ⚑  CONFIDENTIAL  ·  ATTENDANCE ANOMALIES DOSSIER"
    ws["A1"].font = Font(name="Georgia", size=22, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="1A1410")
    ws["A1"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[1].height = 44

    ws.merge_cells("A2:I2")
    ws["A2"] = (
        f"  CASE FRC/HR/ANM/{period['from'].year}/{datetime.now().strftime('%m%d%H%M')}   "
        f"·   COMPILED {datetime.now().strftime('%d %B %Y')}"
    )
    ws["A2"].font = Font(size=10, bold=True, color="FDE68A")
    ws["A2"].fill = PatternFill("solid", fgColor="1A1410")
    ws["A2"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[2].height = 20

    ws.merge_cells("A3:I3")
    ws["A3"] = (
        f"   Evidence window: {period['from'].strftime('%d %B %Y')} to {period['to'].strftime('%d %B %Y')}   "
        f"·   {summary['late']} flagged events   ·   {summary['late_minutes']} accumulated late minutes"
    )
    ws["A3"].font = Font(size=10, italic=True, color=deep)
    ws["A3"].fill = PatternFill("solid", fgColor="F5EFD8")  # manila
    ws["A3"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[3].height = 22

    # Severity tile row
    severity = "HIGH" if summary["late"] > 5 else "MODERATE" if summary["late"] > 0 else "LOW"
    sev_color = {"HIGH": accent, "MODERATE": "B45309", "LOW": "0D9488"}[severity]
    ws.merge_cells("A4:I4")
    ws["A4"] = f"     SEVERITY  ·  {severity}     "
    ws["A4"].font = Font(name="Georgia", size=14, bold=True, color="FFFFFF")
    ws["A4"].fill = PatternFill("solid", fgColor=sev_color)
    ws["A4"].alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[4].height = 28
    ws.row_dimensions[5].height = 14

    # Headers
    headers = ["Date", "Code", "Employee", "Department", "Status",
               "Check-in", "Check-out", "Late mins", "Reasons"]
    head_row = 6
    for i, h in enumerate(headers):
        c = ws.cell(row=head_row, column=i + 1, value=h.upper())
        c.font = Font(size=10, bold=True, color="FDE68A")
        c.fill = PatternFill("solid", fgColor="1A1410")
        c.alignment = Alignment(vertical="center",
                                horizontal="right" if h == "Late mins" else "left",
                                indent=1)
    ws.row_dimensions[head_row].height = 28
    ws.freeze_panes = f"A{head_row + 1}"

    # Body — manila row with severity stripe on left
    for ri, r in enumerate(rows):
        row_idx = head_row + 1 + ri
        zebra = ri % 2 == 1
        bg = "FAF4DC" if zebra else "FEF9EC"  # warmer manila
        # Severity strip color
        sev = r.get("severity", 0)
        stripe = accent if sev >= 5 else "B45309" if sev >= 3 else "92400E"

        for col in range(1, 10):
            cell = ws.cell(row=row_idx, column=col)
            cell.font = Font(size=10, color="1A1410")
            cell.fill = PatternFill("solid", fgColor=bg)
            cell.alignment = Alignment(vertical="center", indent=1)
            cell.border = Border(bottom=Side(style="dashed", color="D1CABB"))

        # Severity stripe on date column
        date_cell = ws.cell(row=row_idx, column=1, value=r["date"])
        date_cell.number_format = "dd mmm yyyy"
        date_cell.border = Border(
            left=Side(style="thick", color=stripe),
            bottom=Side(style="dashed", color="D1CABB"),
        )
        date_cell.font = Font(size=10, bold=True, color="1A1410")
        ws.cell(row=row_idx, column=2, value=r["employee_code"])
        ws.cell(row=row_idx, column=3, value=r["employee_name"])
        ws.cell(row=row_idx, column=4, value=r["department"])

        st = r["status"]
        sc = STATUS_COLORS.get(st, {"light": "#f1f5f9", "deep": "#334155"})
        scell = ws.cell(row=row_idx, column=5, value=st.replace("_", " "))
        scell.fill = PatternFill("solid", fgColor=sc["light"].lstrip("#"))
        scell.font = Font(size=9, bold=True, color=sc["deep"].lstrip("#"))
        scell.alignment = Alignment(horizontal="center", vertical="center")

        if r.get("check_in_time"):
            ws.cell(row=row_idx, column=6, value=r["check_in_time"]).number_format = "hh:mm AM/PM"
        if r.get("check_out_time"):
            cout = ws.cell(row=row_idx, column=7, value=r["check_out_time"])
            cout.number_format = "hh:mm AM/PM"
        else:
            # Highlight missing check-out
            no_out = ws.cell(row=row_idx, column=7, value="—")
            no_out.fill = PatternFill("solid", fgColor="FECACA")
            no_out.font = Font(size=10, bold=True, color="7F1D1D")
            no_out.alignment = Alignment(horizontal="center", vertical="center")

        c_late = ws.cell(row=row_idx, column=8, value=r["late_minutes"])
        c_late.alignment = Alignment(horizontal="right", indent=1)
        if r["late_minutes"] > 30:
            c_late.fill = PatternFill("solid", fgColor="FEE2E2")
            c_late.font = Font(size=10, bold=True, color="7F1D1D")
        c_late.number_format = "0"

        rcell = ws.cell(row=row_idx, column=9, value=r.get("reasons", ""))
        rcell.font = Font(size=10, italic=True, color=deep)
        rcell.alignment = Alignment(vertical="center", indent=1, wrap_text=True)

    if rows:
        ws.auto_filter.ref = f"A{head_row}:I{head_row + len(rows)}"

    widths = [14, 12, 26, 22, 14, 13, 13, 11, 36]
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ════════════════════════════════════════════════════════════════════════════
# 7. Daily Roster — xlsxwriter with autofilter + frozen panes + status pills
# ════════════════════════════════════════════════════════════════════════════


def _excel_daily(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("daily")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]

    wb, buf = _xw_workbook()
    ws = wb.add_worksheet("Daily Roster")
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    # Blueprint-style header
    f_title = wb.add_format({
        "bold": True, "font_size": 22, "font_color": "#FFFFFF",
        "bg_color": "#1e1b4b", "align": "left", "indent": 1, "valign": "vcenter",
        "font_name": "Consolas",
    })
    f_sub = wb.add_format({
        "italic": True, "font_size": 10, "font_color": "#a5b4fc",
        "bg_color": "#1e1b4b", "align": "left", "indent": 1, "valign": "vcenter",
        "font_name": "Consolas",
    })
    f_titleblock_lbl = wb.add_format({
        "bold": True, "font_size": 8, "font_color": "#a5b4fc",
        "bg_color": "#312e81", "align": "left", "indent": 1, "valign": "vcenter",
        "font_name": "Consolas",
    })
    f_titleblock_val = wb.add_format({
        "bold": True, "font_size": 11, "font_color": "#FFFFFF",
        "bg_color": "#312e81", "align": "left", "indent": 1, "valign": "vcenter",
        "font_name": "Consolas",
    })
    f_header = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "left", "indent": 1, "font_size": 10,
        "bottom": 2, "bottom_color": deep, "valign": "vcenter",
    })
    f_header_r = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "right", "indent": 1, "font_size": 10,
        "bottom": 2, "bottom_color": deep, "valign": "vcenter",
    })
    f_cell = wb.add_format({"font_size": 10, "indent": 1, "bottom": 1, "bottom_color": "#e9e5f7"})
    f_cell_z = wb.add_format({"font_size": 10, "indent": 1, "bg_color": "#f5f3ff", "bottom": 1, "bottom_color": "#e9e5f7"})
    f_hrs = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0.00\" h\"", "bottom": 1, "bottom_color": "#e9e5f7"})
    f_hrs_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0.00\" h\"", "bg_color": "#f5f3ff", "bottom": 1, "bottom_color": "#e9e5f7"})
    f_num = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0", "bottom": 1, "bottom_color": "#e9e5f7"})
    f_num_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0", "bg_color": "#f5f3ff", "bottom": 1, "bottom_color": "#e9e5f7"})
    f_date = wb.add_format({"font_size": 10, "indent": 1, "num_format": "dd mmm yyyy", "bottom": 1, "bottom_color": "#e9e5f7"})
    f_date_z = wb.add_format({"font_size": 10, "indent": 1, "num_format": "dd mmm yyyy", "bg_color": "#f5f3ff", "bottom": 1, "bottom_color": "#e9e5f7"})
    f_time = wb.add_format({"font_size": 10, "indent": 1, "num_format": "hh:mm AM/PM", "bottom": 1, "bottom_color": "#e9e5f7"})
    f_time_z = wb.add_format({"font_size": 10, "indent": 1, "num_format": "hh:mm AM/PM", "bg_color": "#f5f3ff", "bottom": 1, "bottom_color": "#e9e5f7"})

    ws.set_column("A:A", 14)
    ws.set_column("B:B", 12)
    ws.set_column("C:C", 26)
    ws.set_column("D:D", 20)
    ws.set_column("E:E", 16)
    ws.set_column("F:G", 13)
    ws.set_column("H:K", 11)
    ws.set_column("L:L", 14)

    ws.set_row(0, 32)
    ws.merge_range("A1:L1", "   ◇  ATTENDANCE  ·  DAILY ROSTER  ·  TECHNICAL DRAWING", f_title)
    ws.set_row(1, 22)
    ws.merge_range("A2:L2", f"   {theme['subtitle']}", f_sub)

    # Title-block strip
    ws.set_row(2, 24)
    ws.merge_range("A3:B3", "  PROJECT", f_titleblock_lbl)
    ws.merge_range("C3:F3", "FOURRECK HR ATTENDANCE", f_titleblock_val)
    ws.merge_range("G3:H3", "  DWG NO.", f_titleblock_lbl)
    ws.merge_range("I3:J3", f"ATT-{period['from'].strftime('%y%m%d')}", f_titleblock_val)
    ws.merge_range("K3:L3", f"  REV. A · {datetime.now().strftime('%d.%m.%y')}", f_titleblock_lbl)

    # Period strip
    ws.set_row(3, 22)
    ws.merge_range("A4:B4", "  FROM", f_titleblock_lbl)
    ws.merge_range("C4:D4", period["from"].strftime("%Y-%m-%d"), f_titleblock_val)
    ws.merge_range("E4:F4", "  TO", f_titleblock_lbl)
    ws.merge_range("G4:H4", period["to"].strftime("%Y-%m-%d"), f_titleblock_val)
    ws.merge_range("I4:J4", "  EMPLOYEES", f_titleblock_lbl)
    ws.merge_range("K4:L4", f"  {summary['employees']} · {summary['rows']} ROWS", f_titleblock_val)
    ws.set_row(4, 12)

    # Headers
    headers = ["Date", "Code", "Employee", "Department", "Shift",
               "Check-in", "Check-out", "Hours", "Break", "Late", "OT", "Status"]
    start_row = 5
    ws.set_row(start_row, 26)
    for i, h in enumerate(headers):
        ws.write(start_row, i, h, f_header_r if h in ("Hours", "Break", "Late", "OT") else f_header)
    ws.freeze_panes(start_row + 1, 0)

    # Body
    for ri, r in enumerate(rows):
        row_idx = start_row + 1 + ri
        z = ri % 2 == 1
        ws.set_row(row_idx, 18)
        ws.write_datetime(row_idx, 0, _to_dt(r["date"]), f_date_z if z else f_date)
        ws.write(row_idx, 1, r["employee_code"], f_cell_z if z else f_cell)
        ws.write(row_idx, 2, r["employee_name"], f_cell_z if z else f_cell)
        ws.write(row_idx, 3, r["department"], f_cell_z if z else f_cell)
        ws.write(row_idx, 4, r["shift_name"], f_cell_z if z else f_cell)
        if r.get("check_in_time"):
            ws.write_datetime(row_idx, 5, r["check_in_time"], f_time_z if z else f_time)
        else:
            ws.write(row_idx, 5, "", f_cell_z if z else f_cell)
        if r.get("check_out_time"):
            ws.write_datetime(row_idx, 6, r["check_out_time"], f_time_z if z else f_time)
        else:
            ws.write(row_idx, 6, "", f_cell_z if z else f_cell)
        ws.write_number(row_idx, 7, r["working_hours"], f_hrs_z if z else f_hrs)
        ws.write_number(row_idx, 8, r["break_hours"], f_hrs_z if z else f_hrs)
        ws.write_number(row_idx, 9, r["late_minutes"], f_num_z if z else f_num)
        ws.write_number(row_idx, 10, r["overtime_hours"], f_hrs_z if z else f_hrs)
        _write_status_pill(wb, ws, row_idx, 11, r["status"], z)

    last_row = start_row + len(rows)
    if rows:
        ws.conditional_format(start_row + 1, 8, last_row, 8, {
            "type": "data_bar", "bar_color": "#0284c7", "bar_solid": False,
        })
        ws.conditional_format(start_row + 1, 9, last_row, 9, {
            "type": "3_color_scale",
            "min_color": "#ffffff", "mid_color": "#fef9c3", "max_color": "#b91c1c",
        })
        ws.conditional_format(start_row + 1, 10, last_row, 10, {
            "type": "data_bar", "bar_color": "#fb923c",
        })
        ws.autofilter(start_row, 0, last_row, len(headers) - 1)

    return _xw_finalize(wb, buf)


# ════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ════════════════════════════════════════════════════════════════════════════


def _to_dt(d):
    """Convert date/datetime/string to datetime (xlsxwriter requires datetime)."""
    if isinstance(d, datetime):
        return d
    if isinstance(d, date_cls):
        return datetime(d.year, d.month, d.day)
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d)
        except ValueError:
            return None
    return None


def _write_status_pill(wb, ws, row, col, status, zebra=False):
    """Write a status string with a pill-style background tinted by STATUS_COLORS."""
    sc = STATUS_COLORS.get(status, {"light": "#f1f5f9", "deep": "#334155"})
    fmt = wb.add_format({
        "font_size": 9, "bold": True, "italic": False,
        "font_color": sc["deep"],
        "bg_color": sc["light"],
        "align": "center", "valign": "vcenter",
        "bottom": 1, "bottom_color": "#ece6d7",
    })
    ws.write(row, col, str(status).replace("_", " "), fmt)


def _excel_breaks(rows: list[dict], summary: dict, meta: dict) -> bytes:
    """Breaks — cafe receipt style. Monospaced title band, dashed dividers,
    intensity tags in pill format, ratio data bar."""
    theme = report_meta("breaks")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]

    wb, buf = _xw_workbook()
    ws = wb.add_worksheet("Breaks")
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    # Receipt-style header (monospace, dark on cream)
    f_title = wb.add_format({
        "bold": True, "font_size": 24, "font_color": deep,
        "bg_color": theme["accent_soft"], "align": "center", "valign": "vcenter",
        "font_name": "Georgia",
    })
    f_sub = wb.add_format({
        "italic": True, "font_size": 11, "font_color": "#6b5840",
        "bg_color": theme["accent_soft"], "align": "center", "valign": "vcenter",
    })
    f_receipt = wb.add_format({
        "font_size": 10, "font_color": deep, "bg_color": "#fefaf3",
        "align": "center", "valign": "vcenter", "font_name": "Consolas", "bold": True,
    })

    f_header = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "left", "valign": "vcenter", "indent": 1, "font_size": 10,
        "bottom": 2, "bottom_color": deep,
    })
    f_header_r = wb.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": accent,
        "align": "right", "valign": "vcenter", "indent": 1, "font_size": 10,
        "bottom": 2, "bottom_color": deep,
    })
    f_cell = wb.add_format({"font_size": 10, "indent": 1, "bottom": 1, "bottom_color": "#ece6d7"})
    f_cell_z = wb.add_format({"font_size": 10, "indent": 1, "bg_color": "#fefaf3", "bottom": 1, "bottom_color": "#ece6d7"})
    f_hrs = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0.00\" h\"", "bottom": 1, "bottom_color": "#ece6d7"})
    f_hrs_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0.00\" h\"", "bg_color": "#fefaf3", "bottom": 1, "bottom_color": "#ece6d7"})
    f_num = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0", "bottom": 1, "bottom_color": "#ece6d7"})
    f_num_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0", "bg_color": "#fefaf3", "bottom": 1, "bottom_color": "#ece6d7"})
    f_pct = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0\"%\"", "bottom": 1, "bottom_color": "#ece6d7"})
    f_pct_z = wb.add_format({"font_size": 10, "align": "right", "indent": 1, "num_format": "0\"%\"", "bg_color": "#fefaf3", "bottom": 1, "bottom_color": "#ece6d7"})
    f_date = wb.add_format({"font_size": 10, "indent": 1, "num_format": "dd mmm yyyy", "bottom": 1, "bottom_color": "#ece6d7"})
    f_date_z = wb.add_format({"font_size": 10, "indent": 1, "num_format": "dd mmm yyyy", "bg_color": "#fefaf3", "bottom": 1, "bottom_color": "#ece6d7"})

    # Intensity pill formats
    INTENSITY_STYLES = {
        "SHORT":    {"bg": "#dcfce7", "fg": "#14532d"},
        "STANDARD": {"bg": "#fef9c3", "fg": "#713f12"},
        "LONG":     {"bg": "#fee2e2", "fg": "#7f1d1d"},
    }

    ws.set_column("A:A", 14)
    ws.set_column("B:B", 12)
    ws.set_column("C:C", 26)
    ws.set_column("D:D", 22)
    ws.set_column("E:E", 18)
    ws.set_column("F:G", 14)
    ws.set_column("H:I", 12)
    ws.set_column("J:J", 14)

    ws.set_row(0, 44)
    ws.merge_range("A1:J1", "☕  BREAK RECEIPT  ·  FOURRECK CAFÉ  ☕", f_title)
    ws.set_row(1, 22)
    ws.merge_range("A2:J2", theme["subtitle"], f_sub)
    ws.set_row(2, 22)
    ws.merge_range(
        "A3:J3",
        f"FROM  {period['from'].strftime('%d-%b-%Y').upper()}    →    "
        f"TO  {period['to'].strftime('%d-%b-%Y').upper()}    ·    "
        f"BREAK-DAYS  {len(rows)}    ·    EMPLOYEES  {summary['employees']}",
        f_receipt,
    )
    ws.set_row(3, 12)

    # Headers
    headers = ["Date", "Code", "Employee", "Department", "Shift",
               "Working hrs", "Break hrs", "Break mins", "Ratio %", "Length"]
    start_row = 4
    ws.set_row(start_row, 26)
    for i, h in enumerate(headers):
        ws.write(start_row, i, h, f_header_r if h in ("Working hrs", "Break hrs", "Break mins", "Ratio %") else f_header)
    ws.freeze_panes(start_row + 1, 0)

    for ri, r in enumerate(rows):
        row_idx = start_row + 1 + ri
        z = ri % 2 == 1
        ws.set_row(row_idx, 18)
        ws.write_datetime(row_idx, 0, _to_dt(r["date"]), f_date_z if z else f_date)
        ws.write(row_idx, 1, r["employee_code"], f_cell_z if z else f_cell)
        ws.write(row_idx, 2, r["employee_name"], f_cell_z if z else f_cell)
        ws.write(row_idx, 3, r["department"], f_cell_z if z else f_cell)
        ws.write(row_idx, 4, r["shift_name"], f_cell_z if z else f_cell)
        ws.write_number(row_idx, 5, r["working_hours"], f_hrs_z if z else f_hrs)
        ws.write_number(row_idx, 6, r["break_hours"], f_hrs_z if z else f_hrs)
        ws.write_number(row_idx, 7, r["break_minutes"], f_num_z if z else f_num)
        ws.write_number(row_idx, 8, r["break_ratio_pct"], f_pct_z if z else f_pct)
        # Intensity pill
        st = INTENSITY_STYLES.get(r["intensity"], {"bg": "#f1f5f9", "fg": "#334155"})
        f_pill = wb.add_format({
            "font_size": 9, "bold": True, "italic": False,
            "font_color": st["fg"], "bg_color": st["bg"],
            "align": "center", "valign": "vcenter",
            "bottom": 1, "bottom_color": "#ece6d7",
        })
        ws.write(row_idx, 9, r["intensity"], f_pill)

    last_row = start_row + len(rows)
    if rows:
        # Break hours data bar (cafe brown)
        ws.conditional_format(start_row + 1, 6, last_row, 6, {
            "type": "data_bar", "bar_color": accent, "bar_solid": True,
        })
        # Ratio % heat-map
        ws.conditional_format(start_row + 1, 8, last_row, 8, {
            "type": "3_color_scale",
            "min_type": "num", "min_value": 0, "min_color": "#dcfce7",
            "mid_type": "num", "mid_value": 15, "mid_color": "#fef9c3",
            "max_type": "num", "max_value": 30, "max_color": "#fecaca",
        })
        ws.autofilter(start_row, 0, last_row, len(headers) - 1)

    return _xw_finalize(wb, buf)


RENDERERS = {
    "monthly": _excel_monthly,
    "late": _excel_late,
    "overtime": _excel_overtime,
    "wfh": _excel_wfh,
    "compliance": _excel_compliance,
    "anomalies": _excel_anomalies,
    "daily": _excel_daily,
    "breaks": _excel_breaks,
}
