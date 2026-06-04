"""Excel exporter for HR Leave Reports — a fully-structured xlsxwriter workbook.

Cover sheet : a masthead band, KPI dashboard cards (boxed, accent-striped),
              a bordered meta strip, and 1–2 native charts (a column chart built
              from the actual data + a doughnut of the composition split where
              one exists). Tab tinted to the report accent.
Data sheet  : a title band, gradient header, frozen header + first column,
              autofilter, zebra banding, warm status pills, currency / number /
              percent formats, full cell borders and a live =SUM() TOTAL row.

All six reports share the scaffold; the warm accent + KPIs + chart swap per key.
The whole thing is built with xlsxwriter (charts, merges, borders, formulas).
"""
from __future__ import annotations

import io
from datetime import date as date_cls, datetime

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

from .data import report_meta
from .columns import columns_for


# Column classification (kept in lock-step with pdf.py / csv_export.py).
MONEY_KEYS = {"basic_salary", "amount", "liability_amount"}
NUM_KEYS = {
    "total_days", "days", "days_requested", "available_days", "quota",
    "opening", "accrued", "carry_forward_in", "used", "encashed",
    "adjustments", "available",
}
INT_KEYS = {"requests", "employees_affected", "days_until_expiry"}
PCT_KEYS = {"utilisation_pct"}
BOOL_KEYS = {"is_admin_override", "is_expired"}
DATE_KEYS = {"from_date", "to_date", "earned_on", "expires_on"}
DT_KEYS = {"created_at", "decided_at", "paid_at"}
SUMABLE = MONEY_KEYS | NUM_KEYS | INT_KEYS
RIGHT_KEYS = MONEY_KEYS | NUM_KEYS | INT_KEYS | PCT_KEYS

# Warm status palette (bg, fg) — no cool hues anywhere in the leave module.
STATUS_BG = {
    "APPROVED": "#fef3c7", "PENDING_MANAGER": "#fde68a", "PENDING_HR": "#fde68a",
    "PENDING": "#fde68a", "MANAGER_REJECTED": "#ffe0d0", "REJECTED": "#ffe0d0",
    "CANCELLED": "#fff7ed", "WITHDRAWN": "#fff7ed", "DRAFT": "#fffbea", "PAID": "#fde68a",
}
STATUS_FG = {
    "APPROVED": "#92400e", "PENDING_MANAGER": "#854d0e", "PENDING_HR": "#854d0e",
    "PENDING": "#854d0e", "MANAGER_REJECTED": "#7c2d12", "REJECTED": "#7c2d12",
    "CANCELLED": "#9a3412", "WITHDRAWN": "#9a3412", "DRAFT": "#713f12", "PAID": "#78350f",
}

# Per-report chart spec: (category_key, value_key, value_title).
CHART_SPEC = {
    "leave_register":    ("employee_name", "total_days", "Leave days by employee"),
    "department_leaves": ("department", "days", "Approved days by department"),
    "balance_report":    ("employee_name", "used", "Leave used by employee"),
    "liability_report":  ("employee_name", "liability_amount", "Liability by employee (₹)"),
    "comp_off_report":   ("employee_name", "days", "Comp-off days by employee"),
    "encashment_report": ("employee_name", "amount", "Encashment amount by employee (₹)"),
}


def _distribution(summary: dict):
    """Return [(label, value, hex)] for a meaningful composition split, else []."""
    gold, amber, ember = "#fbbf24", "#f59e0b", "#e34a0a"
    s = summary
    if any(k in s for k in ("approved", "pending", "rejected")):
        return [("Approved", s.get("approved", 0), gold), ("Pending", s.get("pending", 0), amber),
                ("Rejected", s.get("rejected", 0), ember)]
    if "paid" in s and "pending" in s:
        return [("Paid", s.get("paid", 0), gold), ("Pending", s.get("pending", 0), amber)]
    if "total_used" in s or "total_available" in s:
        return [("Used", s.get("total_used", 0), ember), ("Available", s.get("total_available", 0), gold)]
    if "auto" in s or "manual" in s:
        return [("Auto", s.get("auto", 0), gold), ("Manual", s.get("manual", 0), amber),
                ("Expired", s.get("expired", 0), ember)]
    return []


def _bar_series(rows, cat_key, val_key, top_n=12):
    """Aggregate (category → sum value), sorted desc, capped at top_n."""
    agg: dict[str, float] = {}
    for r in rows:
        cat = r.get(cat_key)
        if cat in (None, "", "—"):
            cat = "—"
        try:
            agg[str(cat)] = agg.get(str(cat), 0.0) + float(r.get(val_key) or 0)
        except (TypeError, ValueError):
            continue
    items = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [k for k, _ in items], [round(v, 2) for _, v in items]


def _kpi_text(k: str, v) -> str:
    if k.startswith("total_liability") or k.startswith("total_amount"):
        try:
            return f"₹ {float(v):,.0f}"
        except (TypeError, ValueError):
            return str(v)
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".") or "0"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def render_excel(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta(report_key)
    cols = columns_for(report_key)
    period = meta.get("period") or meta
    from_d = period["from"]
    to_d = period["to"]
    accent = theme["accent"]
    accent_deep = theme["accent_deep"]
    accent_soft = theme["accent_soft"]
    cream = "#fffdf6"
    ink = "#2a1a0a"
    muted = "#8a6a32"

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})

    # Sheet order matters: Cover is added FIRST so it remains the active sheet,
    # and the _chart helper is added LAST so it can actually be hidden
    # (xlsxwriter silently refuses to hide the active/first sheet — that's why
    # the raw "_chart" tab was showing up).
    cover = wb.add_worksheet("Cover")
    ws = wb.add_worksheet("Data")
    chart_ws = wb.add_worksheet("_chart")
    chart_ws.hide()
    cover.activate()

    # ═══════════════════════════════ COVER ═══════════════════════════════
    cover.hide_gridlines(2)
    cover.set_tab_color(accent)
    cover.set_column("A:A", 2.5)
    cover.set_column("B:N", 10.5)
    try:
        cover.ignore_errors({"number_stored_as_text": "A1:N60"})
    except (AttributeError, TypeError):
        pass  # older xlsxwriter — the green "stored as text" marker is cosmetic only

    f_band = wb.add_format({"bg_color": accent_deep})
    f_eye = wb.add_format({"font_name": "Inter", "font_size": 9, "bold": True,
                           "font_color": accent_deep, "align": "left", "valign": "vcenter"})
    f_title = wb.add_format({"font_name": "Inter", "font_size": 32, "bold": True,
                             "font_color": accent_deep, "align": "left", "valign": "vcenter"})
    f_tag = wb.add_format({"font_name": "Inter", "font_size": 13, "italic": True,
                           "font_color": accent_deep, "align": "left", "valign": "vcenter"})
    f_sub = wb.add_format({"font_name": "Inter", "font_size": 10, "font_color": "#6b4a22",
                           "align": "left", "valign": "vcenter"})
    f_section = wb.add_format({"font_name": "Inter", "font_size": 9, "bold": True,
                               "font_color": muted, "align": "left", "valign": "vcenter"})
    f_meta = wb.add_format({"font_name": "Inter", "font_size": 10, "font_color": ink,
                            "align": "left", "valign": "vcenter", "bg_color": accent_soft,
                            "border": 1, "border_color": accent, "indent": 1})
    f_foot = wb.add_format({"font_name": "Inter", "font_size": 8, "font_color": "#a07a3a",
                            "align": "left", "valign": "vcenter", "bg_color": accent_soft,
                            "indent": 1})

    # Masthead band
    cover.merge_range(0, 0, 0, 13, "", f_band)
    cover.set_row(0, 9)
    cover.set_row(1, 6)
    cover.write(2, 1, "FOURRECK HRMS  ·  LEAVE REPORT", f_eye)
    cover.set_row(3, 42)
    cover.merge_range(3, 1, 3, 13, theme["name"], f_title)
    cover.merge_range(4, 1, 4, 13, theme["tagline"], f_tag)
    cover.set_row(4, 20)
    cover.merge_range(5, 1, 5, 13, theme.get("subtitle", ""), f_sub)
    cover.set_row(5, 16)

    # Meta strip
    cover.set_row(7, 22)
    cover.merge_range(
        7, 1, 7, 12,
        f"  Period   {from_d:%d %b %Y}  →  {to_d:%d %b %Y}        "
        f"Generated   {datetime.now():%d %b %Y · %H:%M}        Rows   {len(shaped_rows):,}",
        f_meta,
    )

    # ── KPI dashboard cards ──
    cover.write(9, 1, "SNAPSHOT", f_section)
    chips = list(summary.items())[:6]
    RS, RL, RV = 10, 11, 12
    cover.set_row(RS, 5)
    cover.set_row(RL, 16)
    cover.set_row(RV, 30)
    f_stripe = wb.add_format({"bg_color": accent})
    f_card_lbl = wb.add_format({"font_name": "Inter", "font_size": 8, "bold": True,
                                "font_color": muted, "bg_color": cream, "align": "left",
                                "valign": "vcenter", "indent": 1,
                                "top": 2, "left": 2, "right": 2, "border_color": accent})
    f_card_val = wb.add_format({"font_name": "Inter", "font_size": 20, "bold": True,
                                "font_color": accent_deep, "bg_color": cream, "align": "left",
                                "valign": "vcenter", "indent": 1,
                                "bottom": 2, "left": 2, "right": 2, "border_color": accent})
    for i, (k, v) in enumerate(chips):
        c0 = 1 + i * 2
        c1 = c0 + 1
        cover.merge_range(RS, c0, RS, c1, "", f_stripe)
        cover.merge_range(RL, c0, RL, c1, k.replace("_", " ").title(), f_card_lbl)
        cover.merge_range(RV, c0, RV, c1, _kpi_text(k, v), f_card_val)

    # ── Charts ──
    cover.write(14, 1, "VISUAL BREAKDOWN", f_section)

    cat_key, val_key, val_title = CHART_SPEC.get(report_key, (None, None, "Breakdown"))
    chart_row = 0
    if cat_key:
        labels, values = _bar_series(shaped_rows, cat_key, val_key)
        if labels:
            for ri, (lbl, val) in enumerate(zip(labels, values)):
                chart_ws.write(chart_row + ri, 0, lbl)
                chart_ws.write_number(chart_row + ri, 1, float(val))
            lbl_fmt = "₹ #,##0" if val_key in MONEY_KEYS else "#,##0.#"
            bar = wb.add_chart({"type": "bar"})
            bar.add_series({
                "name": val_title,
                "categories": ["_chart", chart_row, 0, chart_row + len(labels) - 1, 0],
                "values":     ["_chart", chart_row, 1, chart_row + len(labels) - 1, 1],
                "gradient": {"colors": [accent_soft, accent, accent_deep], "angle": 0},
                "border": {"color": accent_deep},
                "data_labels": {"value": True, "num_format": lbl_fmt,
                                "font": {"size": 8, "color": accent_deep, "bold": True}},
                "gap": 90,
            })
            bar.set_title({"name": val_title, "name_font": {"size": 12, "color": accent_deep, "bold": True}})
            bar.set_legend({"none": True})
            bar.set_x_axis({"num_font": {"size": 8, "color": muted},
                            "major_gridlines": {"visible": True, "line": {"color": "#eedfc6", "dash_type": "dash"}},
                            "line": {"none": True}})
            bar.set_y_axis({"num_font": {"size": 9, "color": ink, "bold": True}, "reverse": True,
                            "line": {"color": accent}})
            bar.set_chartarea({"border": {"none": True}, "fill": {"color": cream}})
            bar.set_plotarea({"fill": {"color": cream}})
            bar.set_size({"width": 540, "height": 300})
            cover.insert_chart(15, 1, bar, {"x_offset": 4, "y_offset": 4})

    # Doughnut from the composition split (if one exists), beside the bar.
    dist = _distribution(summary)
    if dist and sum(float(x[1] or 0) for x in dist) > 0:
        dcol = 4  # helper columns E/F on _chart sheet
        for ri, (lbl, val, _h) in enumerate(dist):
            chart_ws.write(ri, dcol, lbl)
            chart_ws.write_number(ri, dcol + 1, float(val or 0))
        dough = wb.add_chart({"type": "doughnut"})
        dcol_l = xl_col_to_name(dcol)
        dcol_v = xl_col_to_name(dcol + 1)
        dough.add_series({
            "name": "Composition",
            "categories": f"=_chart!${dcol_l}$1:${dcol_l}${len(dist)}",
            "values":     f"=_chart!${dcol_v}$1:${dcol_v}${len(dist)}",
            "points": [{"fill": {"color": h}} for _, _, h in dist],
            "data_labels": {"percentage": True, "font": {"bold": True, "color": "#ffffff", "size": 9}},
        })
        dough.set_hole_size(58)
        dough.set_title({"name": "Composition", "name_font": {"size": 11, "color": accent_deep, "bold": True}})
        dough.set_legend({"position": "bottom", "font": {"size": 9}})
        dough.set_chartarea({"border": {"none": True}, "fill": {"color": cream}})
        dough.set_size({"width": 320, "height": 300})
        cover.insert_chart(15, 10, dough, {"x_offset": 4, "y_offset": 4})

    cover.merge_range(31, 1, 31, 13, "  FOURRECK HRMS  ·  CONFIDENTIAL  ·  LEAVE & ABSENCE MODULE", f_foot)
    cover.set_row(31, 18)

    # ═══════════════════════════════ DATA ═══════════════════════════════
    ws.hide_gridlines(2)
    ws.set_tab_color(accent_deep)
    ncols = len(cols)

    # Title band (row 0), header (row 1), data from row 2.
    title_fmt = wb.add_format({"font_name": "Inter", "font_size": 13, "bold": True,
                               "font_color": cream, "bg_color": accent_deep,
                               "align": "left", "valign": "vcenter", "indent": 1})
    ws.merge_range(0, 0, 0, ncols - 1,
                   f"{theme['name']}   ·   {from_d:%d %b %Y} → {to_d:%d %b %Y}   ·   {len(shaped_rows):,} rows",
                   title_fmt)
    ws.set_row(0, 28)

    header_fmt = wb.add_format({"bold": True, "font_size": 10, "font_name": "Inter",
                               "font_color": cream, "bg_color": accent_deep, "align": "left",
                               "valign": "vcenter", "border": 1, "border_color": accent_deep})
    header_num = wb.add_format({"bold": True, "font_size": 10, "font_name": "Inter",
                               "font_color": cream, "bg_color": accent_deep, "align": "right",
                               "valign": "vcenter", "border": 1, "border_color": accent_deep})

    def _base(extra=None):
        f = {"font_size": 10, "font_name": "Inter", "valign": "vcenter",
             "border": 1, "border_color": "#ecdcc0"}
        if extra:
            f.update(extra)
        return wb.add_format(f)

    cell_fmt = _base({"text_wrap": False})
    cell_alt = _base({"bg_color": accent_soft})
    date_fmt = _base({"num_format": "dd mmm yyyy"})
    date_alt = _base({"num_format": "dd mmm yyyy", "bg_color": accent_soft})
    dt_fmt = _base({"num_format": "dd mmm yyyy hh:mm"})
    dt_alt = _base({"num_format": "dd mmm yyyy hh:mm", "bg_color": accent_soft})
    money_fmt = _base({"num_format": "₹ #,##0.00", "align": "right"})
    money_alt = _base({"num_format": "₹ #,##0.00", "align": "right", "bg_color": accent_soft})
    num_fmt = _base({"num_format": "0.0", "align": "right"})
    num_alt = _base({"num_format": "0.0", "align": "right", "bg_color": accent_soft})
    int_fmt = _base({"num_format": "#,##0", "align": "right"})
    int_alt = _base({"num_format": "#,##0", "align": "right", "bg_color": accent_soft})
    pct_fmt = _base({"num_format": '0.0"%"', "align": "right"})
    pct_alt = _base({"num_format": '0.0"%"', "align": "right", "bg_color": accent_soft})
    bool_fmt = _base({"align": "center"})
    bool_alt = _base({"align": "center", "bg_color": accent_soft})

    status_cache: dict = {}
    def _status_fmt(val, alt):
        key = (val, alt)
        if key not in status_cache:
            status_cache[key] = wb.add_format({
                "font_size": 10, "font_name": "Inter", "bold": True, "valign": "vcenter",
                "align": "center", "font_color": STATUS_FG.get(val, "#7c2d12"),
                "bg_color": STATUS_BG.get(val, "#fff7ed"), "border": 1, "border_color": "#ecdcc0",
            })
        return status_cache[key]

    HEADER_ROW = 1
    DATA_START = 2
    ws.set_row(HEADER_ROW, 26)
    for ci, (label, key, width_px) in enumerate(cols):
        # Roomier widths so headers + autofilter arrows are never clipped.
        ws.set_column(ci, ci, max(12, width_px / 6.2))
        ws.write(HEADER_ROW, ci, label, header_num if key in RIGHT_KEYS else header_fmt)

    for idx, row in enumerate(shaped_rows):
        ri = DATA_START + idx
        alt = (idx % 2 == 1)
        ws.set_row(ri, 19)
        for ci, (_label, key, _w) in enumerate(cols):
            val = row.get(key)
            if val is None or val == "":
                ws.write_blank(ri, ci, None, cell_alt if alt else cell_fmt)
            elif key in DT_KEYS and isinstance(val, datetime):
                ws.write_datetime(ri, ci, val, dt_alt if alt else dt_fmt)
            elif key in DATE_KEYS and isinstance(val, (date_cls, datetime)):
                ws.write_datetime(ri, ci, val, date_alt if alt else date_fmt)
            elif key in MONEY_KEYS:
                ws.write_number(ri, ci, float(val), money_alt if alt else money_fmt)
            elif key in PCT_KEYS:
                ws.write_number(ri, ci, float(val), pct_alt if alt else pct_fmt)
            elif key in NUM_KEYS:
                ws.write_number(ri, ci, float(val), num_alt if alt else num_fmt)
            elif key in INT_KEYS:
                ws.write_number(ri, ci, int(val), int_alt if alt else int_fmt)
            elif key in BOOL_KEYS:
                ws.write(ri, ci, "Yes" if val else "No", bool_alt if alt else bool_fmt)
            elif key == "status":
                ws.write(ri, ci, str(val).replace("_", " ").title(), _status_fmt(val, alt))
            else:
                ws.write(ri, ci, str(val), cell_alt if alt else cell_fmt)

    n = len(shaped_rows)
    ws.freeze_panes(DATA_START, 1)
    if n:
        last_data = DATA_START + n - 1
        ws.autofilter(HEADER_ROW, 0, last_data, ncols - 1)
        tr = last_data + 1
        ws.set_row(tr, 22)
        tot_lbl = wb.add_format({"bold": True, "font_size": 10, "font_name": "Inter",
                                 "font_color": cream, "bg_color": accent_deep, "align": "left",
                                 "valign": "vcenter", "border": 1, "border_color": accent_deep, "indent": 1})
        tot_money = wb.add_format({"bold": True, "font_size": 10, "font_name": "Inter",
                                   "font_color": cream, "bg_color": accent_deep, "align": "right",
                                   "valign": "vcenter", "num_format": "₹ #,##0.00",
                                   "border": 1, "border_color": accent_deep})
        tot_num = wb.add_format({"bold": True, "font_size": 10, "font_name": "Inter",
                                 "font_color": cream, "bg_color": accent_deep, "align": "right",
                                 "valign": "vcenter", "num_format": "#,##0.0",
                                 "border": 1, "border_color": accent_deep})
        tot_blank = wb.add_format({"bg_color": accent_deep, "border": 1, "border_color": accent_deep})
        for ci, (_label, key, _w) in enumerate(cols):
            if ci == 0:
                ws.write(tr, ci, "TOTAL", tot_lbl)
            elif key in SUMABLE:
                colL = xl_col_to_name(ci)
                ws.write_formula(
                    tr, ci, f"=SUM({colL}{DATA_START + 1}:{colL}{last_data + 1})",
                    tot_money if key in MONEY_KEYS else tot_num,
                )
            else:
                ws.write_blank(tr, ci, None, tot_blank)

    wb.close()
    return buf.getvalue()
