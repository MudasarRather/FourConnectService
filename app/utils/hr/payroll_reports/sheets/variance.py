"""Excel renderer: Variance Report (key='variance').

Newsroom-bulletin styling carried into a workbook: caution-gold spine, a
movers/steady KPI strip, delta DATA BARS (red below zero / green above) on the
Change column, a 3-colour conditional heat-map on Change %, status-tinted cells,
a themed TOTAL row, autofilter + frozen panes, and an embedded movement chart on
a second 'Charts' sheet.

Engine: xlsxwriter.

Public entry: render(rows, summary, meta) -> bytes
"""
from __future__ import annotations

from ..excel_common import (
    xw_workbook, xw_finalize, corporate_title_block, corporate_kpi_strip,
    corporate_header_format, body_formats, BRAND, MONEY,
)
from ..data import report_meta
from ..common import inr_compact

# Status → (bg, fg) tint matching the PDF status pills.
_STATUS_TINT = {
    "UP":     ("#dcfce7", "#14532d"),
    "DOWN":   ("#fee2e2", "#7f1d1d"),
    "FLAT":   ("#f1f5f9", "#334155"),
    "JOINED": ("#fef9c3", "#713f12"),
    "EXITED": ("#ede9fe", "#4c1d95"),
}

# Column layout: label, row-key, kind, width
_COLS = [
    ("Code",       "employee_code", "mono",   13),
    ("Employee",   "employee_name", "text",   26),
    ("Department", "department",    "text",   18),
    ("Prev Net",   "prev_net",      "money",  15),
    ("Curr Net",   "curr_net",      "money",  15),
    ("Change",     "delta",         "money",  17),
    ("Change %",   "delta_pct",     "pct",    13),
    ("Status",     "status",        "status", 12),
]


def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("variance")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]

    last_col = len(_COLS) - 1
    DELTA_C = 5      # Change column index
    PCT_C = 6        # Change % column index

    wb, buf = xw_workbook()
    ws = wb.add_worksheet("Variance Report")
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)
    for i, (_, _, _, w) in enumerate(_COLS):
        ws.set_column(i, i, w)

    # ── Title block + KPI strip ──
    nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=last_col)

    movers = int(summary.get("movers", 0) or 0)
    employees = int(summary.get("employees", 0) or 0)
    nrows = int(summary.get("rows", len(rows)) or len(rows))
    net_delta = float(summary.get("net_delta", 0) or 0)
    net = float(summary.get("net", 0) or 0)
    steady = max(0, nrows - movers)
    move_rail = "#b91c1c" if net_delta < 0 else ("#047857" if net_delta > 0 else "#6b7280")

    kpis = [
        ("MOVERS",        movers,    accent),
        ("STEADY",        steady,    "#6b7280"),
        ("ON PAYROLL",    employees, deep),
        ("NET MOVEMENT",  net_delta, move_rail, MONEY),
        ("NET PAYOUT",    net,       "#047857", MONEY),
    ]
    nxt = corporate_kpi_strip(wb, ws, theme, kpis, start_row=nxt, last_col=last_col)

    # ── Header row ──
    f_head = corporate_header_format(wb, theme, align="left")
    f_head_r = corporate_header_format(wb, theme, align="right")
    f_head_c = corporate_header_format(wb, theme, align="center")
    hrow = nxt
    for i, (label, _, kind, _) in enumerate(_COLS):
        if kind in ("money", "pct"):
            ws.write(hrow, i, label, f_head_r)
        elif kind == "status":
            ws.write(hrow, i, label, f_head_c)
        else:
            ws.write(hrow, i, label, f_head)
    ws.set_row(hrow, 28)
    ws.freeze_panes(hrow + 1, 3)  # freeze through Department

    fmts = body_formats(wb)

    # signed-money + signed-pct formats (so deltas read +/-)
    def _signed(base_extra, zebra):
        f = {"font_size": BRAND["body_pt"], "indent": 1, "border": 1,
             "border_color": BRAND["rule_soft"], "valign": "vcenter", "align": "right", "bold": True}
        f.update(base_extra)
        if zebra:
            f["bg_color"] = BRAND["cream"]
        return wb.add_format(f)

    SIGNED_MONEY = '[Green]"₹"#,##0;[Red]-"₹"#,##0;"₹"0'
    SIGNED_PCT = '[Green]+0.0"%";[Red]-0.0"%";0.0"%"'
    f_delta = (_signed({"num_format": SIGNED_MONEY}, False), _signed({"num_format": SIGNED_MONEY}, True))
    f_pct = (_signed({"num_format": SIGNED_PCT}, False), _signed({"num_format": SIGNED_PCT}, True))

    # cached status formats per (status, zebra)
    _status_cache: dict = {}

    def _status_fmt(status: str, zebra: bool):
        key = (status, zebra)
        if key not in _status_cache:
            bg, fg = _STATUS_TINT.get(status, ("#f1f5f9", "#334155"))
            f = {"font_size": BRAND["body_pt"], "bold": True, "align": "center",
                 "valign": "vcenter", "border": 1, "border_color": BRAND["rule_soft"],
                 "bg_color": bg, "font_color": fg}
            _status_cache[key] = wb.add_format(f)
        return _status_cache[key]

    # ── Body rows ──
    for ri, r in enumerate(rows):
        zebra = ri % 2 == 1
        rr = hrow + 1 + ri
        for i, (_, k, kind, _) in enumerate(_COLS):
            v = r.get(k)
            if kind == "status":
                ws.write(rr, i, str(v or "—"), _status_fmt(str(v or ""), zebra))
            elif kind == "money":
                if k == "delta":
                    ws.write_number(rr, i, float(v or 0), f_delta[1 if zebra else 0])
                else:
                    ws.write_number(rr, i, float(v or 0), fmts["money"][1 if zebra else 0])
            elif kind == "pct":
                ws.write_number(rr, i, float(v or 0), f_pct[1 if zebra else 0])
            elif kind == "mono":
                ws.write(rr, i, v if v not in (None, "") else "—", fmts["mono"][1 if zebra else 0])
            else:
                ws.write(rr, i, v if v not in (None, "") else "—", fmts["text"][1 if zebra else 0])

    n = len(rows)
    if n:
        last_row = hrow + n
        ws.autofilter(hrow, 0, last_row, last_col)

        from xlsxwriter.utility import xl_col_to_name as _xlc
        body_first = hrow + 1
        delta_rng = f"{_xlc(DELTA_C)}{body_first + 1}:{_xlc(DELTA_C)}{last_row + 1}"
        pct_rng = f"{_xlc(PCT_C)}{body_first + 1}:{_xlc(PCT_C)}{last_row + 1}"

        # Delta DATA BARS — red below zero, green above, axis at midpoint.
        ws.conditional_format(body_first, DELTA_C, last_row, DELTA_C, {
            "type": "data_bar",
            "bar_color": "#16a34a",
            "bar_negative_color": "#dc2626",
            "bar_axis_position": "automatic",
            "bar_negative_color_same": False,
            "data_bar_2010": True,
        })
        # 3-colour heat-map on Change % (red → white → green).
        ws.conditional_format(body_first, PCT_C, last_row, PCT_C, {
            "type": "3_color_scale",
            "min_color": "#dc2626", "mid_color": "#fffbeb", "max_color": "#16a34a",
            "min_type": "min", "mid_type": "num", "mid_value": 0, "max_type": "max",
        })

        # ── TOTAL row ──
        tr = last_row + 1
        f_tl = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "left", "indent": 1, "border": 1, "border_color": deep,
                              "font_size": BRAND["body_pt"]})
        f_tm = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "right", "num_format": MONEY, "border": 1, "border_color": deep})
        f_td = wb.add_format({"bold": True, "font_color": "#fde68a", "bg_color": deep,
                              "align": "right", "num_format": SIGNED_MONEY, "border": 1, "border_color": deep})
        f_tb = wb.add_format({"bg_color": deep, "border": 1, "border_color": deep})
        ws.write(tr, 0, "TOTAL", f_tl)
        ws.write_blank(tr, 1, None, f_tb)
        ws.write(tr, 2, f"{n} employees", f_tl)
        ws.write_formula(tr, 3, f"=SUM({_xlc(3)}{body_first + 1}:{_xlc(3)}{last_row + 1})", f_tm)
        ws.write_formula(tr, 4, f"=SUM({_xlc(4)}{body_first + 1}:{_xlc(4)}{last_row + 1})", f_tm)
        ws.write_formula(tr, 5, f"=SUM({_xlc(5)}{body_first + 1}:{_xlc(5)}{last_row + 1})", f_td)
        ws.write_blank(tr, 6, None, f_tb)
        ws.write_blank(tr, 7, None, f_tb)

        # ── Charts sheet: top movers by absolute change ──
        _build_chart_sheet(wb, theme, rows, accent, deep, net_delta)

    return xw_finalize(wb, buf)


def _build_chart_sheet(wb, theme, rows, accent, deep, net_delta):
    """A second sheet with a clean data block + an embedded bar chart of the
    largest movers (signed change), gold themed to match the PDF."""
    cs = wb.add_worksheet("Charts")
    cs.set_tab_color(accent)
    cs.hide_gridlines(2)
    cs.set_column(0, 0, 28)
    cs.set_column(1, 1, 16)

    f_title = wb.add_format({"bold": True, "font_size": 14, "font_color": BRAND["ink"],
                             "bg_color": theme["accent_soft"], "align": "left", "indent": 1,
                             "valign": "vcenter", "bottom": 2, "bottom_color": deep})
    cs.set_row(0, 26)
    cs.merge_range(0, 0, 0, 1, "  Top movers · net pay change", f_title)

    f_h = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": accent,
                         "align": "left", "indent": 1, "border": 1, "border_color": deep})
    f_hr = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": accent,
                          "align": "right", "border": 1, "border_color": deep})
    cs.write(2, 0, "Employee", f_h)
    cs.write(2, 1, "Change", f_hr)

    # top 12 by absolute movement (already sorted by |delta| from the shaper)
    movers = [r for r in rows if r.get("status") in ("UP", "DOWN")][:12]
    if not movers:
        movers = rows[:12]
    f_txt = wb.add_format({"border": 1, "border_color": BRAND["rule_soft"], "indent": 1, "valign": "vcenter"})
    f_num = wb.add_format({"border": 1, "border_color": BRAND["rule_soft"], "align": "right",
                           "num_format": '[Green]"₹"#,##0;[Red]-"₹"#,##0;"₹"0'})
    for i, r in enumerate(movers):
        cs.write(3 + i, 0, r.get("employee_name", "—"), f_txt)
        cs.write_number(3 + i, 1, float(r.get("delta") or 0), f_num)

    nlast = 3 + len(movers) - 1
    chart = wb.add_chart({"type": "bar"})
    chart.add_series({
        "name": "Net pay change",
        "categories": ["Charts", 3, 0, nlast, 0],
        "values": ["Charts", 3, 1, nlast, 1],
        "fill": {"color": accent},
        "border": {"color": deep},
        "data_labels": {"value": True, "num_format": '"₹"#,##0', "font": {"size": 7}},
    })
    chart.set_title({"name": "Largest net-pay movers vs prior period"})
    chart.set_x_axis({"num_format": '"₹"#,##0', "major_gridlines": {"visible": True}})
    chart.set_y_axis({"reverse": True})
    chart.set_legend({"none": True})
    chart.set_size({"width": 560, "height": 30 + 26 * max(6, len(movers))})
    chart.set_chartarea({"border": {"none": True}})
    cs.insert_chart(2, 3, chart, {"x_offset": 8, "y_offset": 4})
