"""Shared Excel scaffolding for HR Payroll Reports.

Ported from the attendance-reports Excel kit and extended with money-aware
number formats. Every per-report sheet module imports these helpers so the
13 workbooks share one corporate spine — accent rail, title block, KPI strip,
themed header — while each report still chooses its own columns, charts and
conditional formats.

xlsxwriter and openpyxl are imported lazily inside the helpers / renderers so
the backend boots even if a binary wheel is missing; the export endpoint
surfaces the ImportError only at call time.
"""
from __future__ import annotations

import io
from datetime import datetime


COMPANY = {
    "name": "Fourreck",
    "legal": "Fourreck Technologies Pvt. Ltd.",
    "web": "fourreck.com",
}

# Unified neutral palette — keeps every workbook on one product line. Per-report
# accent (theme["accent"]) drives KPI rails, tab tint, header fill + chart fills.
BRAND = {
    "ink":          "#111418",
    "ink_muted":    "#475569",
    "ink_dim":      "#94a3b8",
    "rule":         "#94a3b8",
    "rule_soft":    "#cbd5e1",
    "rule_strong":  "#475569",
    "cream":        "#fbf8f0",
    "panel":        "#ffffff",
    "panel_soft":   "#f1f5f9",
    "header_ink":   "#ffffff",
    "danger_bg":    "#fee2e2", "danger_fg": "#7f1d1d",
    "warn_bg":      "#fef3c7", "warn_fg":   "#854d0e",
    "good_bg":      "#ccfbf1", "good_fg":   "#115e59",
    "net_bg":       "#ecfdf5", "net_fg":    "#047857",
    "title_pt":     18,
    "subtitle_pt":  10,
    "meta_pt":      9,
    "kpi_label_pt": 8,
    "kpi_value_pt": 17,
    "header_pt":    10,
    "body_pt":      10,
}

# Indian-grouped rupee number format string for xlsxwriter cells.
MONEY = '[>9999999]"₹"##\\,##\\,##\\,##0;[>99999]"₹"##\\,##\\,##0;"₹"##,##0'
MONEY_P = '"₹"#,##0.00'   # plain western-grouped with paise — safe fallback


def xw_workbook():
    """In-memory xlsxwriter workbook. ``remove_timezone`` guards against
    tz-aware datetimes that Excel can't represent."""
    import xlsxwriter
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {
        "in_memory": True,
        "default_date_format": "dd mmm yyyy",
        "remove_timezone": True,
    })
    return wb, buf


def xw_finalize(wb, buf) -> bytes:
    wb.close()
    buf.seek(0)
    return buf.read()


def corporate_title_block(wb, ws, theme: dict, period: dict, summary: dict, *, last_col: int) -> int:
    """4-row corporate header: accent rail · title · subtitle · period line.
    Returns the next free row index."""
    accent = theme["accent"]
    deep = theme["accent_deep"]
    name = theme["name"]
    subtitle = theme.get("subtitle", "")

    f_rail = wb.add_format({"bg_color": accent})
    f_title = wb.add_format({
        "bold": True, "font_size": BRAND["title_pt"], "font_name": "Calibri",
        "font_color": BRAND["ink"], "bg_color": BRAND["panel"],
        "align": "left", "valign": "vcenter", "indent": 1,
    })
    f_sub = wb.add_format({
        "font_size": BRAND["subtitle_pt"], "italic": True, "font_name": "Calibri",
        "font_color": BRAND["ink_muted"], "bg_color": BRAND["panel"],
        "align": "left", "valign": "vcenter", "indent": 1,
        "bottom": 1, "bottom_color": BRAND["rule_soft"],
    })
    f_period = wb.add_format({
        "bold": True, "font_size": BRAND["meta_pt"], "font_name": "Calibri",
        "font_color": BRAND["ink"], "bg_color": BRAND["panel_soft"],
        "align": "left", "valign": "vcenter", "indent": 1,
        "top": 1, "top_color": BRAND["rule"],
        "bottom": 2, "bottom_color": deep,
    })

    ws.set_row(0, 4)
    ws.merge_range(0, 0, 0, last_col, "", f_rail)
    ws.set_row(1, 32)
    ws.merge_range(1, 0, 1, last_col, f"  {COMPANY['name']}  ·  {name}", f_title)
    ws.set_row(2, 20)
    ws.merge_range(2, 0, 2, last_col, f"  {subtitle}", f_sub)
    ws.set_row(3, 22)
    plabel = period.get("label", "")
    fy = period.get("fy", "")
    ws.merge_range(
        3, 0, 3, last_col,
        f"  Pay period   {plabel}    ·    FY {fy}"
        f"        ·        Generated   {datetime.now().strftime('%d %b %Y · %I:%M %p').lstrip('0')}",
        f_period,
    )
    return 4


def corporate_kpi_strip(wb, ws, theme: dict, kpis: list, *, start_row: int, last_col: int) -> int:
    """KPI tile row: label (8pt) + value (17pt) with a colored top rail per tile.

    ``kpis`` = list of (LABEL, value, hex_rail) or (LABEL, value, hex_rail, num_format).
    A string value renders as text; numbers honour the optional num_format."""
    if not kpis or last_col < 0:
        return start_row

    n = len(kpis)
    cols_per = max(1, (last_col + 1) // n)
    leftover = (last_col + 1) - cols_per * n

    ws.set_row(start_row, 8)
    label_row = start_row + 1
    value_row = start_row + 2
    ws.set_row(label_row, 20)
    ws.set_row(value_row, 30)

    c0 = 0
    for i, tile in enumerate(kpis):
        label, value, rail_color = tile[0], tile[1], tile[2]
        num_format = tile[3] if len(tile) > 3 else None
        extra = 1 if i < leftover else 0
        c1 = min(c0 + cols_per - 1 + extra, last_col)

        f_label = wb.add_format({
            "bold": True, "font_size": BRAND["kpi_label_pt"], "font_name": "Calibri",
            "font_color": BRAND["ink_muted"], "bg_color": BRAND["panel"],
            "align": "center", "valign": "vcenter",
            "left": 2, "left_color": BRAND["rule"],
            "right": 2, "right_color": BRAND["rule"],
            "top": 5, "top_color": rail_color,
        })
        vfmt = {
            "bold": True, "font_size": BRAND["kpi_value_pt"], "font_name": "Calibri",
            "font_color": BRAND["ink"], "bg_color": BRAND["panel"],
            "align": "center", "valign": "vcenter",
            "left": 2, "left_color": BRAND["rule"],
            "right": 2, "right_color": BRAND["rule"],
            "bottom": 2, "bottom_color": BRAND["rule"],
        }
        if num_format:
            vfmt["num_format"] = num_format
        f_value = wb.add_format(vfmt)

        if c0 == c1:
            ws.write(label_row, c0, label, f_label)
            if isinstance(value, (int, float)):
                ws.write_number(value_row, c0, value, f_value)
            else:
                ws.write(value_row, c0, str(value), f_value)
        else:
            ws.merge_range(label_row, c0, label_row, c1, label, f_label)
            if isinstance(value, (int, float)):
                ws.merge_range(value_row, c0, value_row, c1, "", f_value)
                ws.write_number(value_row, c0, value, f_value)
            else:
                ws.merge_range(value_row, c0, value_row, c1, str(value), f_value)
        c0 = c1 + 1

    spacer = value_row + 1
    ws.set_row(spacer, 10)
    return spacer + 1


def corporate_header_format(wb, theme: dict, *, align: str = "left") -> object:
    """Themed table-header format — accent fill, white text, dark rules."""
    return wb.add_format({
        "bold": True, "font_color": BRAND["header_ink"],
        "bg_color": theme["accent"], "font_name": "Calibri",
        "align": align, "valign": "vcenter", "indent": 1,
        "font_size": BRAND["header_pt"],
        "top": 2, "top_color": theme["accent_deep"],
        "bottom": 2, "bottom_color": theme["accent_deep"],
        "left": 1, "left_color": theme["accent_deep"],
        "right": 1, "right_color": theme["accent_deep"],
        "text_wrap": False,
    })


def body_formats(wb, *, money_fmt: str = MONEY):
    """A reusable bundle of common body cell formats (normal + zebra).

    Returns a dict keyed by kind → (normal, zebra) format tuple. Kinds:
    text, num, money, days, pct.
    """
    def mk(extra, zebra=False):
        f = {"font_size": BRAND["body_pt"], "indent": 1, "border": 1,
             "border_color": BRAND["rule_soft"], "valign": "vcenter"}
        f.update(extra)
        if zebra:
            f["bg_color"] = BRAND["cream"]
        return wb.add_format(f)

    return {
        "text":  (mk({"align": "left"}), mk({"align": "left"}, True)),
        "num":   (mk({"align": "right", "num_format": "#,##0"}), mk({"align": "right", "num_format": "#,##0"}, True)),
        "money": (mk({"align": "right", "num_format": money_fmt}), mk({"align": "right", "num_format": money_fmt}, True)),
        "days":  (mk({"align": "right", "num_format": "0.0"}), mk({"align": "right", "num_format": "0.0"}, True)),
        "pct":   (mk({"align": "right", "num_format": '0.0"%"'}), mk({"align": "right", "num_format": '0.0"%"'}, True)),
        "mono":  (mk({"align": "left", "font_name": "Consolas"}), mk({"align": "left", "font_name": "Consolas"}, True)),
    }
