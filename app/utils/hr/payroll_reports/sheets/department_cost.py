"""Excel workbook — Department Cost (key "department-cost").

Engine: xlsxwriter. A bespoke cost-control workbook:

  · Sheet 1 "Department Cost" — corporate title block + KPI strip, a themed
    table with per-department gross/deductions/net/employer-cost/CTC and a
    computed cost-share %; data-bars on Total Cost, a 3-colour heat scale on
    cost-share, a green→red scale on avg-net, plus a SUM/derived TOTAL row.
  · Sheet 2 "Charts" — an embedded clustered column chart of Total Cost by
    department (the headline ops view) over a small driver table.

Theme accent is the industrial orange (#ea580c) so the workbook reads as the
sibling of the dark PDF cover.
"""
from __future__ import annotations

from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

from ..data import report_meta
from ..excel_common import (
    xw_workbook, xw_finalize,
    corporate_title_block, corporate_kpi_strip, corporate_header_format,
    body_formats, BRAND, MONEY,
)
from ..common import inr_compact


# Column layout — (label, row-key, kind). kind drives the body format bucket.
COLUMNS = [
    ("Department",     "department",    "text"),
    ("Heads",          "headcount",     "num"),
    ("Gross",          "gross",         "money"),
    ("Deductions",     "deductions",    "money"),
    ("Net Pay",        "net",           "money"),
    ("Employer Cost",  "employer_cost", "money"),
    ("CTC",            "ctc",           "money"),
    ("Avg Net / Head", "avg_net",       "money"),
    ("Total Cost",     "total_cost",    "money"),
    ("Cost Share",     "_share",        "pct"),
]


def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("department-cost")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]

    rows = list(rows or [])
    last_col = len(COLUMNS) - 1

    # ── grand total cost for cost-share computation ───────────────────────────
    grand_total = sum(float(r.get("total_cost") or 0) for r in rows) or 0.0

    wb, buf = xw_workbook()
    ws = wb.add_worksheet("Department Cost"[:31])
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    # column widths
    ws.set_column(0, 0, 24)   # Department
    ws.set_column(1, 1, 9)    # Heads
    ws.set_column(2, 8, 15)   # money cols
    ws.set_column(9, 9, 12)   # share

    # ── title block + KPI strip ───────────────────────────────────────────
    nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=last_col)

    employees = int(summary.get("employees", summary.get("headcount", 0)) or 0)
    gross = float(summary.get("gross", 0) or 0)
    net = float(summary.get("net", 0) or 0)
    employer_cost = float(summary.get("employer_cost", 0) or 0)
    total_cost = float(summary.get("total_cost", 0) or 0)
    cost_per_head = (total_cost / employees) if employees else 0.0
    load_pct = (employer_cost / gross * 100.0) if gross else 0.0

    KPIS = [
        ("COST CENTRES", int(summary.get("rows", len(rows)) or len(rows)), accent),
        ("HEADCOUNT", employees, BRAND["ink_muted"]),
        ("GROSS PAYROLL", gross, "#b8860b", MONEY),
        ("NET DISBURSED", net, "#047857", MONEY),
        ("EMPLOYER COST", employer_cost, "#b45309", MONEY),
        ("TOTAL CTC COST", total_cost, accent, MONEY),
    ]
    nxt = corporate_kpi_strip(wb, ws, theme, KPIS, start_row=nxt, last_col=last_col)

    # a slim "load factor" caption line above the table
    f_cap = wb.add_format({
        "font_size": 9, "italic": True, "font_color": BRAND["ink_muted"],
        "bg_color": BRAND["panel"], "align": "left", "indent": 1, "valign": "vcenter",
    })
    ws.merge_range(
        nxt, 0, nxt, last_col,
        f"  Employer add-on load factor {load_pct:.1f}% of gross   ·   "
        f"average fully-loaded cost {inr_compact(cost_per_head)} per head   ·   "
        f"cost share computed against {inr_compact(grand_total)} grand total",
        f_cap,
    )
    ws.set_row(nxt, 18)
    nxt += 1

    # ── header row ────────────────────────────────────────────────────────
    f_head = corporate_header_format(wb, theme, align="left")
    f_head_r = corporate_header_format(wb, theme, align="right")
    hrow = nxt
    for i, (label, _key, kind) in enumerate(COLUMNS):
        ws.write(hrow, i, label, f_head if kind == "text" else f_head_r)
    ws.set_row(hrow, 30)
    ws.freeze_panes(hrow + 1, 1)

    # ── body rows ────────────────────────────────────────────────────────
    fmts = body_formats(wb)
    first_data = hrow + 1
    for ri, r in enumerate(rows):
        zebra = ri % 2 == 1
        rr = first_data + ri
        share = (float(r.get("total_cost") or 0) / grand_total * 100.0) if grand_total else 0.0
        for i, (_label, key, kind) in enumerate(COLUMNS):
            base, zb = fmts[kind]
            f = zb if zebra else base
            if key == "_share":
                ws.write_number(rr, i, share, f)
            elif kind == "text":
                v = r.get(key)
                ws.write(rr, i, v if v not in (None, "") else "—", f)
            else:
                ws.write_number(rr, i, float(r.get(key) or 0), f)

    last_row = first_data + len(rows) - 1 if rows else hrow

    # ── TOTAL row ────────────────────────────────────────────────────────
    f_tl = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                          "align": "left", "indent": 1, "border": 1, "border_color": deep,
                          "valign": "vcenter"})
    f_tn = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                          "align": "right", "num_format": "#,##0", "border": 1, "border_color": deep,
                          "valign": "vcenter"})
    f_tm = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                          "align": "right", "num_format": MONEY, "border": 1, "border_color": deep,
                          "valign": "vcenter"})
    f_tp = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                          "align": "right", "num_format": '0.0"%"', "border": 1, "border_color": deep,
                          "valign": "vcenter"})
    trow = last_row + 1 if rows else hrow + 1
    if rows:
        for i, (_label, key, kind) in enumerate(COLUMNS):
            col = xl_col_to_name(i)
            rng = f"{col}{first_data + 1}:{col}{last_row + 1}"
            if i == 0:
                ws.write(trow, i, "ALL DEPARTMENTS", f_tl)
            elif key == "headcount":
                ws.write_formula(trow, i, f"=SUM({rng})", f_tn)
            elif key == "_share":
                ws.write_formula(trow, i, f"=SUM({rng})", f_tp)
            elif key == "avg_net":
                # weighted: total net / total heads, not a naive sum of averages
                net_c = xl_col_to_name([c[1] for c in COLUMNS].index("net"))
                head_c = xl_col_to_name([c[1] for c in COLUMNS].index("headcount"))
                nrng = f"{net_c}{first_data + 1}:{net_c}{last_row + 1}"
                hrng = f"{head_c}{first_data + 1}:{head_c}{last_row + 1}"
                ws.write_formula(trow, i, f"=IFERROR(SUM({nrng})/SUM({hrng}),0)", f_tm)
            else:
                ws.write_formula(trow, i, f"=SUM({rng})", f_tm)
        ws.set_row(trow, 22)

    # ── autofilter + conditional formats ──────────────────────────────────
    if rows:
        ws.autofilter(hrow, 0, last_row, last_col)

        tc_i = [c[1] for c in COLUMNS].index("total_cost")
        share_i = [c[1] for c in COLUMNS].index("_share")
        avgnet_i = [c[1] for c in COLUMNS].index("avg_net")
        empc_i = [c[1] for c in COLUMNS].index("employer_cost")

        # data bars on Total Cost
        ws.conditional_format(first_data, tc_i, last_row, tc_i, {
            "type": "data_bar", "bar_color": accent, "bar_solid_fill": True,
            "bar_border_color": deep,
        })
        # data bars on Employer Cost (lighter)
        ws.conditional_format(first_data, empc_i, last_row, empc_i, {
            "type": "data_bar", "bar_color": "#fdba74", "bar_solid_fill": True,
        })
        # 3-colour heat on cost share — light->orange->deep
        ws.conditional_format(first_data, share_i, last_row, share_i, {
            "type": "3_color_scale",
            "min_color": "#fff7ed", "mid_color": "#fdba74", "max_color": accent,
        })
        # green->red scale on avg net per head (high take-home = green)
        ws.conditional_format(first_data, avgnet_i, last_row, avgnet_i, {
            "type": "3_color_scale",
            "min_color": "#fee2e2", "mid_color": "#fef3c7", "max_color": "#bbf7d0",
        })

    # ════════════════════════════════════════════════════════════════════
    # CHARTS sheet — embedded clustered column: Total Cost by department
    # ════════════════════════════════════════════════════════════════════
    cs = wb.add_worksheet("Charts")
    cs.set_tab_color(deep)
    cs.hide_gridlines(2)
    cs.set_column(0, 0, 26)
    cs.set_column(1, 3, 16)

    f_ctitle = wb.add_format({"bold": True, "font_size": 15, "font_color": BRAND["ink"],
                              "valign": "vcenter"})
    f_csub = wb.add_format({"italic": True, "font_size": 10, "font_color": BRAND["ink_muted"]})
    cs.merge_range(0, 0, 0, 3, "Department Cost — Total Cost by Cost Centre", f_ctitle)
    cs.set_row(0, 26)
    cs.merge_range(1, 0, 1, 3,
                   f"{period.get('label','')}  ·  FY {period.get('fy','')}  ·  fully-loaded cost-to-company",
                   f_csub)

    # driver table (kept compact for chart sourcing)
    dh = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": accent,
                        "align": "left", "border": 1, "border_color": deep, "indent": 1})
    dhr = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": accent,
                         "align": "right", "border": 1, "border_color": deep})
    dtxt = wb.add_format({"border": 1, "border_color": BRAND["rule_soft"], "indent": 1})
    dmon = wb.add_format({"border": 1, "border_color": BRAND["rule_soft"],
                          "num_format": MONEY, "align": "right"})

    dtop = 3
    cs.write(dtop, 0, "Department", dh)
    cs.write(dtop, 1, "Total Cost", dhr)
    cs.write(dtop, 2, "Net Pay", dhr)
    cs.write(dtop, 3, "Employer Cost", dhr)
    for ri, r in enumerate(rows):
        rr = dtop + 1 + ri
        cs.write(rr, 0, str(r.get("department") or "—"), dtxt)
        cs.write_number(rr, 1, float(r.get("total_cost") or 0), dmon)
        cs.write_number(rr, 2, float(r.get("net") or 0), dmon)
        cs.write_number(rr, 3, float(r.get("employer_cost") or 0), dmon)

    n = len(rows)
    if n:
        d_first = dtop + 1
        d_last = dtop + n
        # clustered column chart: Total Cost vs Net vs Employer Cost
        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name": "Total Cost",
            "categories": ["Charts", d_first, 0, d_last, 0],
            "values":     ["Charts", d_first, 1, d_last, 1],
            "fill": {"color": accent}, "border": {"color": deep},
            "data_labels": {"value": True, "num_format": '"₹"#,##0', "font": {"size": 7}},
        })
        chart.add_series({
            "name": "Net Pay",
            "categories": ["Charts", d_first, 0, d_last, 0],
            "values":     ["Charts", d_first, 2, d_last, 2],
            "fill": {"color": "#16a34a"},
        })
        chart.add_series({
            "name": "Employer Cost",
            "categories": ["Charts", d_first, 0, d_last, 0],
            "values":     ["Charts", d_first, 3, d_last, 3],
            "fill": {"color": "#fdba74"},
        })
        chart.set_title({"name": "Cost-to-Company by Department"})
        chart.set_x_axis({"name": "Department", "num_font": {"rotation": -30, "size": 9}})
        chart.set_y_axis({"name": "₹ Cost", "num_format": '"₹"#,##0'})
        chart.set_legend({"position": "bottom"})
        chart.set_size({"width": 720, "height": 380})
        chart.set_style(10)
        cs.insert_chart(dtop, 5, chart, {"x_offset": 6, "y_offset": 2})

        # a second pie: cost-share of total
        pie = wb.add_chart({"type": "pie"})
        pie.add_series({
            "name": "Cost Share",
            "categories": ["Charts", d_first, 0, d_last, 0],
            "values":     ["Charts", d_first, 1, d_last, 1],
            "data_labels": {"percentage": True, "font": {"size": 8}},
            "points": [
                {"fill": {"color": c}} for c in
                ["#ea580c", "#fb923c", "#7c2d12", "#fdba74", "#b45309", "#fed7aa", "#9a3412", "#fff7ed"]
            ][:n],
        })
        pie.set_title({"name": "Total Cost Share"})
        pie.set_size({"width": 720, "height": 320})
        pie.set_style(10)
        cs.insert_chart(dtop + 22, 5, pie, {"x_offset": 6, "y_offset": 2})

    return xw_finalize(wb, buf)
