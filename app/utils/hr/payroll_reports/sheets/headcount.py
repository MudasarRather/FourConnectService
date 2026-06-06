"""Excel workbook: Headcount & Cost (key="headcount").

Engine: xlsxwriter. A corporate "Headcount & Cost" sheet (title block + KPI
strip + themed table with heat-mapped cost share, data-bars on headcount, a
totals row) PLUS a second "Charts" sheet carrying an embedded pie (headcount
share by department) and a column chart (total cost by department).

Violet accent line (#7c3aed) matching the blueprint PDF cover.
"""
from __future__ import annotations

from xlsxwriter.utility import xl_col_to_name

from ..data import report_meta
from ..excel_common import (
    BRAND, MONEY, xw_workbook, xw_finalize,
    corporate_title_block, corporate_kpi_strip, corporate_header_format, body_formats,
)
from ..common import inr_compact

SHEET_NAME = "Headcount & Cost"
CHART_SHEET = "Charts"

# Column plan (sheet col index -> meaning):
#  0 Department | 1 Headcount | 2 Head % | 3 Total Cost | 4 Cost % | 5 Avg Cost/Head
LAST_COL = 5


def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("headcount")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]

    rows = list(rows or [])

    wb, buf = xw_workbook()
    ws = wb.add_worksheet(SHEET_NAME[:31])
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    ws.set_column(0, 0, 30)   # Department
    ws.set_column(1, 1, 12)   # Headcount
    ws.set_column(2, 2, 11)   # Head %
    ws.set_column(3, 3, 18)   # Total Cost
    ws.set_column(4, 4, 11)   # Cost %
    ws.set_column(5, 5, 18)   # Avg Cost / Head

    # ── Title block + KPI strip ──────────────────────────────────────────────
    heads = int(summary.get("headcount", summary.get("employees", 0)) or 0)
    employees = int(summary.get("employees", heads) or 0)
    total_cost = float(summary.get("total_cost", 0) or 0)
    depts = int(summary.get("rows", len(rows)) or len(rows))
    avg_cost = (total_cost / heads) if heads else 0.0
    top_dept = rows[0]["department"] if rows else "—"

    nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=LAST_COL)
    kpis = [
        ("DEPARTMENTS", depts, "#67e8f9"),
        ("HEADCOUNT", heads, "#a78bfa"),
        ("TOTAL PAY COST", total_cost, "#34d399", MONEY),
        ("AVG / HEAD", round(avg_cost), "#fbbf24", MONEY),
    ]
    nxt = corporate_kpi_strip(wb, ws, theme, kpis, start_row=nxt, last_col=LAST_COL)

    # A small context line above the table.
    f_ctx = wb.add_format({
        "italic": True, "font_size": 9, "font_color": BRAND["ink_muted"],
        "align": "left", "indent": 1, "valign": "vcenter",
    })
    ws.merge_range(nxt, 0, nxt, LAST_COL,
                   f"  Workforce distribution & pay-cost share  ·  largest cohort: {top_dept}",
                   f_ctx)
    ws.set_row(nxt, 18)
    nxt += 1

    # ── Header row ───────────────────────────────────────────────────────────
    headers = ["Department", "Headcount", "Head %", "Total Cost", "Cost %", "Avg Cost / Head"]
    aligns = ["left", "right", "right", "right", "right", "right"]
    f_head = corporate_header_format(wb, theme, align="left")
    f_head_r = corporate_header_format(wb, theme, align="right")
    hrow = nxt
    for i, (label, al) in enumerate(zip(headers, aligns)):
        ws.write(hrow, i, label, f_head_r if al == "right" else f_head)
    ws.set_row(hrow, 28)
    ws.freeze_panes(hrow + 1, 1)

    # ── Body rows ────────────────────────────────────────────────────────────
    fmts = body_formats(wb)
    first_data = hrow + 1
    for ri, r in enumerate(rows):
        zebra = ri % 2 == 1
        rr = first_data + ri
        ws.write(rr, 0, r.get("department") or "—", fmts["text"][1 if zebra else 0])
        ws.write_number(rr, 1, int(r.get("headcount") or 0), fmts["num"][1 if zebra else 0])
        ws.write_number(rr, 2, float(r.get("headcount_pct") or 0), fmts["pct"][1 if zebra else 0])
        ws.write_number(rr, 3, float(r.get("total_cost") or 0), fmts["money"][1 if zebra else 0])
        ws.write_number(rr, 4, float(r.get("cost_pct") or 0), fmts["pct"][1 if zebra else 0])
        ws.write_number(rr, 5, float(r.get("avg_cost") or 0), fmts["money"][1 if zebra else 0])

    last_data = first_data + len(rows) - 1 if rows else hrow

    # ── Totals row ───────────────────────────────────────────────────────────
    if rows:
        tr = last_data + 1
        f_tl = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "left", "indent": 1, "border": 1, "border_color": deep,
                              "valign": "vcenter"})
        f_tn = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "right", "num_format": "#,##0", "border": 1, "border_color": deep,
                              "valign": "vcenter"})
        f_tp = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "right", "num_format": '0.0"%"', "border": 1, "border_color": deep,
                              "valign": "vcenter"})
        f_tm = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "right", "num_format": MONEY, "border": 1, "border_color": deep,
                              "valign": "vcenter"})
        hc = xl_col_to_name(1)
        tcc = xl_col_to_name(3)
        ws.write(tr, 0, "TOTAL · ALL DEPARTMENTS", f_tl)
        ws.write_formula(tr, 1, f"=SUM({hc}{first_data + 1}:{hc}{last_data + 1})", f_tn,
                         sum(int(r.get("headcount") or 0) for r in rows))
        ws.write_number(tr, 2, 100.0, f_tp)
        ws.write_formula(tr, 3, f"=SUM({tcc}{first_data + 1}:{tcc}{last_data + 1})", f_tm,
                         sum(float(r.get("total_cost") or 0) for r in rows))
        ws.write_number(tr, 4, 100.0, f_tp)
        ws.write_formula(
            tr, 5,
            f"=IF(SUM({hc}{first_data + 1}:{hc}{last_data + 1})=0,0,"
            f"SUM({tcc}{first_data + 1}:{tcc}{last_data + 1})/SUM({hc}{first_data + 1}:{hc}{last_data + 1}))",
            f_tm, round(avg_cost),
        )
        ws.set_row(tr, 22)

        # ── Conditional formats ──────────────────────────────────────────────
        # Data bars on the headcount column.
        ws.conditional_format(first_data, 1, last_data, 1, {
            "type": "data_bar", "bar_color": accent, "bar_solid_fill": True,
            "bar_border_color": deep,
        })
        # Heat-map (3-colour scale) on cost share — cool->violet for hotter spend.
        ws.conditional_format(first_data, 4, last_data, 4, {
            "type": "3_color_scale",
            "min_color": "#ecfdf5", "mid_color": "#ede9fe", "max_color": accent,
        })
        # Data bars on total cost.
        ws.conditional_format(first_data, 3, last_data, 3, {
            "type": "data_bar", "bar_color": "#34d399", "bar_solid_fill": True,
        })

        ws.autofilter(hrow, 0, last_data, LAST_COL)

    # ── Charts sheet: pie (headcount share) + column (cost by dept) ───────────
    cs = wb.add_worksheet(CHART_SHEET[:31])
    cs.set_tab_color(deep)
    cs.hide_gridlines(2)
    cs.set_column(0, 0, 30)
    cs.set_column(1, 2, 18)

    f_ch_title = wb.add_format({"bold": True, "font_size": 14, "font_color": BRAND["ink"],
                                "valign": "vcenter"})
    f_ch_sub = wb.add_format({"italic": True, "font_size": 9, "font_color": BRAND["ink_muted"]})
    f_ch_h = corporate_header_format(wb, theme, align="left")
    f_ch_hr = corporate_header_format(wb, theme, align="right")
    cs.merge_range(0, 0, 0, 2, f"{theme['name']} — Charts", f_ch_title)
    cs.set_row(0, 24)
    cs.merge_range(1, 0, 1, 2, f"{period.get('label', '')}  ·  FY {period.get('fy', '')}", f_ch_sub)

    # Source mini-table on the Charts sheet (drives both charts).
    dh = 3  # data header row
    cs.write(dh, 0, "Department", f_ch_h)
    cs.write(dh, 1, "Headcount", f_ch_hr)
    cs.write(dh, 2, "Total Cost", f_ch_hr)
    f_t = wb.add_format({"font_size": 10, "border": 1, "border_color": BRAND["rule_soft"], "indent": 1})
    f_n = wb.add_format({"font_size": 10, "border": 1, "border_color": BRAND["rule_soft"],
                         "align": "right", "num_format": "#,##0"})
    f_m = wb.add_format({"font_size": 10, "border": 1, "border_color": BRAND["rule_soft"],
                         "align": "right", "num_format": MONEY})
    for i, r in enumerate(rows):
        rr = dh + 1 + i
        cs.write(rr, 0, r.get("department") or "—", f_t)
        cs.write_number(rr, 1, int(r.get("headcount") or 0), f_n)
        cs.write_number(rr, 2, float(r.get("total_cost") or 0), f_m)

    n = len(rows)
    if n:
        first = dh + 1
        last = dh + n
        slice_palette = ["#7c3aed", "#a78bfa", "#22d3ee", "#34d399", "#fbbf24",
                         "#f472b6", "#60a5fa", "#fb7185", "#4c1d95", "#14b8a6"]
        points = [{"fill": {"color": slice_palette[i % len(slice_palette)]}} for i in range(n)]

        pie = wb.add_chart({"type": "pie"})
        pie.add_series({
            "name": "Headcount share by department",
            "categories": [CHART_SHEET, first, 0, last, 0],
            "values": [CHART_SHEET, first, 1, last, 1],
            "points": points,
            "data_labels": {"percentage": True, "category": True, "font": {"size": 8}},
        })
        pie.set_title({"name": "Headcount Share by Department"})
        pie.set_style(10)
        pie.set_size({"width": 460, "height": 300})
        cs.insert_chart(dh, 4, pie, {"x_offset": 8, "y_offset": 4})

        col = wb.add_chart({"type": "column"})
        col.add_series({
            "name": "Total cost by department",
            "categories": [CHART_SHEET, first, 0, last, 0],
            "values": [CHART_SHEET, first, 2, last, 2],
            "points": points,
            "data_labels": {"value": False},
            "gap": 60,
        })
        col.set_title({"name": "Total Pay Cost by Department"})
        col.set_legend({"none": True})
        col.set_x_axis({"num_font": {"rotation": -30, "size": 8}})
        col.set_y_axis({"num_format": MONEY})
        col.set_style(11)
        col.set_size({"width": 460, "height": 300})
        cs.insert_chart(dh + 17, 4, col, {"x_offset": 8, "y_offset": 4})

    return xw_finalize(wb, buf)
