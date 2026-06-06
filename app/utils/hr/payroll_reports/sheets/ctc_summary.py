"""CTC Summary — Excel workbook (xlsxwriter).

A bespoke compensation snapshot:
  * corporate title block + KPI strip (annual / monthly / avg CTC tiles)
  * themed zebra body table with money columns + a derived Basic-% column
  * data_bar on the Annual CTC column, 3-colour heat scale on Basic %
  * a TOTAL row that sums the money columns
  * a second "Charts" sheet with an embedded column chart of top earners

Cyan / teal accent matches the PDF postcard cover.
"""
from __future__ import annotations

from ..data import report_meta
from ..excel_common import (
    xw_workbook, xw_finalize, corporate_title_block, corporate_kpi_strip,
    corporate_header_format, body_formats, BRAND, MONEY,
)

KEY = "ctc-summary"
NAME = "CTC Summary"

# (label, row-key, kind, width, align)
COLUMNS = [
    ("Emp Code",     "employee_code", "mono",  14, "left"),
    ("Employee",     "employee_name", "text",  26, "left"),
    ("Department",   "department",    "text",  18, "left"),
    ("Designation",  "designation",   "text",  18, "left"),
    ("Grade",        "grade",         "text",  10, "left"),
    ("Regime",       "tax_regime",    "text",  10, "left"),
    ("Basic (Mo.)",  "basic",         "money", 14, "right"),
    ("Monthly Gross", "monthly_gross", "money", 15, "right"),
    ("Monthly CTC",  "monthly_ctc",   "money", 15, "right"),
    ("Annual CTC",   "annual_ctc",    "money", 17, "right"),
    ("Basic %",      "_basic_pct",    "pct",   10, "right"),
]


def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta(KEY)
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]
    last_col = len(COLUMNS) - 1

    wb, buf = xw_workbook()
    ws = wb.add_worksheet(NAME[:31])
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    for i, (_label, _key, _kind, width, _align) in enumerate(COLUMNS):
        ws.set_column(i, i, width)

    # ── title + KPI strip ──────────────────────────────────────────────
    employees = summary.get("employees", len(rows))
    annual = float(summary.get("annual_ctc", 0) or 0)
    monthly = float(summary.get("monthly_ctc", 0) or 0)
    avg = float(summary.get("avg_ctc", 0) or 0)

    nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=last_col)
    kpis = [
        ("EMPLOYEES",   employees, "#0e7490"),
        ("ANNUAL CTC",  annual,    deep,   MONEY),
        ("MONTHLY CTC", monthly,   accent, MONEY),
        ("AVG CTC / HEAD", avg,    "#0891b2", MONEY),
    ]
    nxt = corporate_kpi_strip(wb, ws, theme, kpis, start_row=nxt, last_col=last_col)

    # ── header row ─────────────────────────────────────────────────────
    f_head = corporate_header_format(wb, theme, align="left")
    f_head_r = corporate_header_format(wb, theme, align="right")
    hrow = nxt
    for i, (label, _key, _kind, _w, align) in enumerate(COLUMNS):
        ws.write(hrow, i, label, f_head_r if align == "right" else f_head)
    ws.set_row(hrow, 28)
    ws.freeze_panes(hrow + 1, 2)

    # ── body ───────────────────────────────────────────────────────────
    fmts = body_formats(wb)
    # regime pills
    f_regime_new = wb.add_format({"font_size": BRAND["body_pt"], "indent": 1, "border": 1,
                                  "border_color": BRAND["rule_soft"], "valign": "vcenter",
                                  "align": "center", "bold": True,
                                  "bg_color": "#ccfbf1", "font_color": "#115e59"})
    f_regime_old = wb.add_format({"font_size": BRAND["body_pt"], "indent": 1, "border": 1,
                                  "border_color": BRAND["rule_soft"], "valign": "vcenter",
                                  "align": "center", "bold": True,
                                  "bg_color": "#fef3c7", "font_color": "#854d0e"})

    first_data = hrow + 1
    for ri, r in enumerate(rows):
        zebra = ri % 2 == 1
        rr = first_data + ri
        monthly_ctc = float(r.get("monthly_ctc", 0) or 0)
        basic = float(r.get("basic", 0) or 0)
        basic_pct = (basic / monthly_ctc * 100) if monthly_ctc else 0.0

        for i, (_label, key, kind, _w, _align) in enumerate(COLUMNS):
            if key == "_basic_pct":
                base, zb = fmts["pct"]
                ws.write_number(rr, i, basic_pct, zb if zebra else base)
                continue
            if key == "tax_regime":
                regime = str(r.get(key) or "—").upper()
                fpill = f_regime_new if regime == "NEW" else (f_regime_old if regime == "OLD" else
                                                              (fmts["text"][1] if zebra else fmts["text"][0]))
                ws.write(rr, i, regime, fpill)
                continue
            base, zb = fmts[kind]
            f = zb if zebra else base
            v = r.get(key)
            if kind in ("money", "num", "days", "pct"):
                ws.write_number(rr, i, float(v or 0), f)
            else:
                ws.write(rr, i, v if v not in (None, "") else "—", f)

    last_row = first_data + len(rows) - 1 if rows else hrow

    # ── conditional formats ────────────────────────────────────────────
    if rows:
        from xlsxwriter.utility import xl_col_to_name as _xlc
        annual_col = next(i for i, c in enumerate(COLUMNS) if c[1] == "annual_ctc")
        pct_col = next(i for i, c in enumerate(COLUMNS) if c[1] == "_basic_pct")

        ws.conditional_format(first_data, annual_col, last_row, annual_col, {
            "type": "data_bar",
            "bar_color": accent,
            "bar_solid_fill": True,
            "bar_border_color": deep,
        })
        ws.conditional_format(first_data, pct_col, last_row, pct_col, {
            "type": "3_color_scale",
            "min_color": "#fee2e2", "mid_color": "#fef9c3", "max_color": "#ccfbf1",
        })

        # ── TOTAL row ──────────────────────────────────────────────────
        tr = last_row + 1
        f_tl = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "left", "indent": 1, "border": 1, "border_color": deep,
                              "font_size": BRAND["body_pt"]})
        f_tm = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "right", "num_format": MONEY, "border": 1,
                              "border_color": deep, "font_size": BRAND["body_pt"]})
        f_tp = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                              "align": "right", "num_format": '0.0"%"', "border": 1,
                              "border_color": deep, "font_size": BRAND["body_pt"]})
        f_tb = wb.add_format({"bg_color": deep, "border": 1, "border_color": deep})

        money_keys = {"basic", "monthly_gross", "monthly_ctc", "annual_ctc"}
        for i, (_label, key, _kind, _w, _align) in enumerate(COLUMNS):
            col = _xlc(i)
            rng = f"{col}{first_data + 1}:{col}{last_row + 1}"
            if i == 0:
                ws.write(tr, i, "TOTAL", f_tl)
            elif key in money_keys:
                ws.write_formula(tr, i, f"=SUM({rng})", f_tm)
            elif key == "_basic_pct":
                bcol = _xlc(next(j for j, c in enumerate(COLUMNS) if c[1] == "basic"))
                mcol = _xlc(next(j for j, c in enumerate(COLUMNS) if c[1] == "monthly_ctc"))
                num = f"SUM({bcol}{first_data + 1}:{bcol}{last_row + 1})"
                den = f"SUM({mcol}{first_data + 1}:{mcol}{last_row + 1})"
                ws.write_formula(tr, i, f"=IF({den}=0,0,{num}/{den}*100)", f_tp)
            else:
                ws.write_blank(tr, i, None, f_tb)

        ws.autofilter(hrow, 0, last_row, last_col)

        # ── Charts sheet: top earners by Annual CTC ────────────────────
        _add_chart_sheet(wb, theme, rows)

    return xw_finalize(wb, buf)


def _add_chart_sheet(wb, theme: dict, rows: list[dict]) -> None:
    accent = theme["accent"]
    deep = theme["accent_deep"]
    cs = wb.add_worksheet("Charts")
    cs.set_tab_color(deep)
    cs.hide_gridlines(2)
    cs.set_column(0, 0, 28)
    cs.set_column(1, 2, 16)

    f_title = wb.add_format({"bold": True, "font_size": 14, "font_color": BRAND["ink"]})
    f_sub = wb.add_format({"italic": True, "font_size": 9, "font_color": BRAND["ink_muted"]})
    f_h = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": accent,
                         "align": "left", "border": 1, "border_color": deep})
    f_hr = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": accent,
                          "align": "right", "border": 1, "border_color": deep})
    f_name = wb.add_format({"border": 1, "border_color": BRAND["rule_soft"]})
    f_money = wb.add_format({"num_format": MONEY, "border": 1, "border_color": BRAND["rule_soft"]})

    cs.write(0, 0, "CTC Summary · Top Earners", f_title)
    cs.write(1, 0, "Top 10 employees by Annual CTC", f_sub)

    top = sorted(rows, key=lambda r: float(r.get("annual_ctc", 0) or 0), reverse=True)[:10]
    hdr = 3
    cs.write(hdr, 0, "Employee", f_h)
    cs.write(hdr, 1, "Annual CTC", f_hr)
    cs.write(hdr, 2, "Monthly CTC", f_hr)
    for i, r in enumerate(top):
        rr = hdr + 1 + i
        cs.write(rr, 0, r.get("employee_name", "—"), f_name)
        cs.write_number(rr, 1, float(r.get("annual_ctc", 0) or 0), f_money)
        cs.write_number(rr, 2, float(r.get("monthly_ctc", 0) or 0), f_money)

    n = len(top)
    if n:
        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name": "Annual CTC",
            "categories": ["Charts", hdr + 1, 0, hdr + n, 0],
            "values":     ["Charts", hdr + 1, 1, hdr + n, 1],
            "fill":       {"color": accent},
            "border":     {"color": deep},
            "gap":        80,
        })
        chart.set_title({"name": "Annual CTC — Top Earners"})
        chart.set_x_axis({"name": "Employee"})
        chart.set_y_axis({"name": "Annual CTC (₹)", "num_format": '₹#,##0'})
        chart.set_legend({"position": "none"})
        chart.set_size({"width": 720, "height": 380})
        cs.insert_chart(hdr, 4, chart, {"x_offset": 8, "y_offset": 4})
