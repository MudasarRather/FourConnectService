"""Excel workbook for the Payroll Register — the master pay ledger.

xlsxwriter build: a treasury-gold ledger with a corporate title block, KPI
strip, a wide earnings -> deductions -> net column layout, zebra body, a heat
data-bar on Net Pay, traffic-light scale on LOP days, a summing TOTAL row, and
an embedded "Net Pay by Employee" bar chart on a dedicated Charts sheet.
"""
from __future__ import annotations

from ..excel_common import (
    xw_workbook, xw_finalize, corporate_title_block, corporate_kpi_strip,
    corporate_header_format, body_formats, BRAND, MONEY,
)
from ..data import report_meta
from ..common import inr_compact


# (label, row-key, kind, width)  — kind keys into body_formats(wb)
_COLUMNS = [
    ("Payslip No.",  "payslip_no",       "mono",  15),
    ("Code",         "employee_code",    "text",  11),
    ("Employee",     "employee_name",    "text",  24),
    ("Department",   "department",       "text",  15),
    ("Designation",  "designation",      "text",  16),
    ("Paid",         "paid_days",        "days",  7.5),
    ("LOP",          "lop_days",         "days",  7.5),
    ("Basic",        "basic",            "money", 13),
    ("HRA",          "hra",              "money", 13),
    ("Other Earn.",  "other_earnings",   "money", 13),
    ("Gross",        "gross",            "money", 14),
    ("PF (EE)",      "pf_employee",      "money", 11),
    ("ESI (EE)",     "esi_employee",     "money", 10),
    ("PT",           "pt",               "money", 9),
    ("TDS",          "tds",              "money", 12),
    ("Deductions",   "deductions_total", "money", 14),
    ("Net Pay",      "net",              "money", 15),
]

_MONEY_KEYS = {"basic", "hra", "other_earnings", "gross", "pf_employee",
               "esi_employee", "pt", "tds", "deductions_total", "net"}


def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("register")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]
    last_col = len(_COLUMNS) - 1

    wb, buf = xw_workbook()
    ws = wb.add_worksheet("Payroll Register")
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)
    ws.set_paper(9)           # A4
    ws.set_landscape()
    ws.fit_to_pages(1, 0)
    ws.repeat_rows(0)

    for i, c in enumerate(_COLUMNS):
        ws.set_column(i, i, c[3])

    # ── Title block + KPI strip ──────────────────────────────────────────────
    nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=last_col)

    net = summary.get("net", 0)
    gross = summary.get("gross", 0)
    deductions = summary.get("deductions", 0)
    employer = summary.get("employer_cost", 0)
    total_cost = summary.get("total_cost", net + employer)
    headcount = summary.get("headcount", summary.get("rows", len(rows)))
    avg_net = summary.get("avg_net", round(net / headcount, 2) if headcount else 0)

    kpis = [
        ("FOLIOS / HEADS", headcount, "#475569"),
        ("GROSS WAGES", float(gross), deep, MONEY),
        ("TOTAL DEDUCTIONS", float(deductions), "#b91c1c", MONEY),
        ("NET DISBURSED", float(net), "#047857", MONEY),
        ("AVG NET / HEAD", float(avg_net), accent, MONEY),
        ("TOTAL COST", float(total_cost), "#1a1410", MONEY),
    ]
    nxt = corporate_kpi_strip(wb, ws, theme, kpis, start_row=nxt, last_col=last_col)

    # ── Header row ───────────────────────────────────────────────────────────
    f_head_l = corporate_header_format(wb, theme, align="left")
    f_head_r = corporate_header_format(wb, theme, align="right")
    fmts = body_formats(wb)

    hrow = nxt
    for i, c in enumerate(_COLUMNS):
        kind = c[2]
        is_right = kind in ("money", "days", "num")
        ws.write(hrow, i, c[0], f_head_r if is_right else f_head_l)
    ws.set_row(hrow, 30)
    ws.freeze_panes(hrow + 1, 3)   # freeze header + first 3 identity columns

    # ── Body rows (zebra) ────────────────────────────────────────────────────
    first_data = hrow + 1
    for ri, r in enumerate(rows):
        rr = first_data + ri
        zebra = ri % 2 == 1
        for i, c in enumerate(_COLUMNS):
            kind = c[2]
            base, zb = fmts[kind]
            f = zb if zebra else base
            v = r.get(c[1])
            if kind in ("money", "days", "num"):
                ws.write_number(rr, i, float(v or 0), f)
            else:
                ws.write(rr, i, v if v not in (None, "") else "—", f)
    ws.set_default_row(15)

    if not rows:
        return xw_finalize(wb, buf)

    last_row = first_data + len(rows) - 1

    # ── TOTAL row summing money columns ──────────────────────────────────────
    from xlsxwriter.utility import xl_col_to_name as _xc
    tr = last_row + 1
    f_tl = wb.add_format({"bold": True, "font_color": "#fff8e1", "bg_color": deep,
                          "align": "left", "indent": 1, "border": 1, "border_color": deep,
                          "valign": "vcenter", "font_size": BRAND["body_pt"]})
    f_tm = wb.add_format({"bold": True, "font_color": "#fff8e1", "bg_color": deep,
                          "align": "right", "num_format": MONEY, "border": 1,
                          "border_color": deep, "valign": "vcenter", "font_size": BRAND["body_pt"]})
    f_td = wb.add_format({"bold": True, "font_color": "#fff8e1", "bg_color": deep,
                          "align": "right", "num_format": "0.0", "border": 1,
                          "border_color": deep, "valign": "vcenter", "font_size": BRAND["body_pt"]})
    f_tb = wb.add_format({"bg_color": deep, "border": 1, "border_color": deep})
    ws.set_row(tr, 22)
    for i, c in enumerate(_COLUMNS):
        col = _xc(i)
        rng = f"{col}{first_data + 1}:{col}{last_row + 1}"
        if i == 0:
            ws.write(tr, i, "TOTAL", f_tl)
        elif c[1] in _MONEY_KEYS:
            ws.write_formula(tr, i, f"=SUM({rng})", f_tm)
        elif c[2] == "days":
            ws.write_formula(tr, i, f"=SUM({rng})", f_td)
        else:
            ws.write_blank(tr, i, None, f_tb)

    # ── Conditional formats ──────────────────────────────────────────────────
    # locate column indices
    idx = {c[1]: i for i, c in enumerate(_COLUMNS)}
    net_c = idx["net"]
    lop_c = idx["lop_days"]
    ded_c = idx["deductions_total"]

    # Gold data-bar across Net Pay column
    ws.conditional_format(first_data, net_c, last_row, net_c, {
        "type": "data_bar", "bar_color": accent, "bar_solid_fill": False,
        "bar_border_color": deep,
    })
    # Traffic-light on LOP days — green (0) -> red (high)
    ws.conditional_format(first_data, lop_c, last_row, lop_c, {
        "type": "3_color_scale",
        "min_color": "#ccfbf1", "mid_color": "#fef3c7", "max_color": "#fee2e2",
        "min_type": "num", "min_value": 0,
        "mid_type": "percentile", "mid_value": 50,
        "max_type": "max",
    })
    # Heat scale on Deductions
    ws.conditional_format(first_data, ded_c, last_row, ded_c, {
        "type": "2_color_scale",
        "min_color": "#fff8e1", "max_color": "#f6c453",
    })

    ws.autofilter(hrow, 0, last_row, last_col)

    # ── Charts sheet — embedded Net Pay by Employee bar chart ────────────────
    chart_ws = wb.add_worksheet("Charts")
    chart_ws.set_tab_color(deep)
    chart_ws.hide_gridlines(2)
    chart_ws.set_column(0, 0, 28)
    chart_ws.set_column(1, 3, 16)

    f_ctitle = wb.add_format({"bold": True, "font_size": 16, "font_color": BRAND["ink"],
                              "valign": "vcenter"})
    f_csub = wb.add_format({"italic": True, "font_size": 10, "font_color": BRAND["ink_muted"]})
    chart_ws.merge_range(0, 0, 0, 3, f"  {theme['name']} — Net Pay by Employee", f_ctitle)
    chart_ws.set_row(0, 26)
    chart_ws.merge_range(1, 0, 1, 3,
                         f"  Pay period {period.get('label','')}  ·  FY {period.get('fy','')}", f_csub)

    f_ch = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": accent,
                          "border": 1, "border_color": deep, "align": "left", "indent": 1})
    f_chr = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": accent,
                           "border": 1, "border_color": deep, "align": "right"})
    f_cn = wb.add_format({"border": 1, "border_color": BRAND["rule_soft"]})
    f_cm = wb.add_format({"num_format": MONEY, "border": 1, "border_color": BRAND["rule_soft"]})

    drow = 3
    chart_ws.write(drow, 0, "Employee", f_ch)
    chart_ws.write(drow, 1, "Net Pay", f_chr)
    chart_ws.write(drow, 2, "Gross", f_chr)
    chart_ws.write(drow, 3, "Deductions", f_chr)
    # cap series length so the bar chart stays readable
    chart_rows = rows[:30]
    for j, r in enumerate(chart_rows):
        cr = drow + 1 + j
        chart_ws.write(cr, 0, r.get("employee_name", "—"), f_cn)
        chart_ws.write_number(cr, 1, float(r.get("net") or 0), f_cm)
        chart_ws.write_number(cr, 2, float(r.get("gross") or 0), f_cm)
        chart_ws.write_number(cr, 3, float(r.get("deductions_total") or 0), f_cm)
    cdata_last = drow + len(chart_rows)

    chart = wb.add_chart({"type": "bar"})
    chart.add_series({
        "name":       "Net Pay",
        "categories": ["Charts", drow + 1, 0, cdata_last, 0],
        "values":     ["Charts", drow + 1, 1, cdata_last, 1],
        "fill":       {"color": accent},
        "border":     {"color": deep},
        "gap":        60,
    })
    chart.set_title({"name": "Net Pay by Employee"})
    chart.set_x_axis({"name": "Net Pay (₹)", "num_format": '₹#,##0'})
    chart.set_y_axis({"name": "Employee", "reverse": True})
    chart.set_legend({"none": True})
    chart.set_size({"width": 720, "height": max(280, 26 * len(chart_rows) + 80)})
    chart.set_chartarea({"border": {"color": deep}, "fill": {"color": "#fffdf4"}})
    chart_ws.insert_chart(drow, 5, chart, {"x_offset": 8, "y_offset": 4})

    return xw_finalize(wb, buf)
