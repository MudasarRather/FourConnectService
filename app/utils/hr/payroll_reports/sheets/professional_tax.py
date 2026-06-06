"""Professional Tax — Excel workbook (xlsxwriter).

State-wise PT remittance register. Rows are grouped visually by state with a
state-subtotal band after each state, a data_bar heat-map on the PT column, a
grand-total row, and a second 'Charts' sheet with an embedded column chart of
PT collected per state. Amber / ochre civic accent to match the PDF cover.

rows: state, location, employee_code, employee_name, gross, pt
summary: employees, pt, rows
"""
from __future__ import annotations

from ..data import report_meta
from ..excel_common import (
    xw_workbook, xw_finalize, corporate_title_block, corporate_kpi_strip,
    corporate_header_format, BRAND, MONEY,
)
from ..common import inr_compact

NAME = "Professional Tax"

# Columns: State | Location | Emp Code | Employee | Gross | PT
_COLS = [
    ("State", 22, "text"),
    ("Location", 18, "text"),
    ("Emp Code", 13, "text"),
    ("Employee", 28, "text"),
    ("Gross Earnings", 16, "money"),
    ("Professional Tax", 16, "money"),
]


def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("professional-tax")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]
    last_col = len(_COLS) - 1

    wb, buf = xw_workbook()
    ws = wb.add_worksheet(NAME[:31])
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)
    for i, (_, width, _) in enumerate(_COLS):
        ws.set_column(i, i, width)

    # ── Title block + KPI strip ──────────────────────────────────────────────
    nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=last_col)

    employees = int(summary.get("employees", 0) or 0)
    pt_total = float(summary.get("pt", 0.0) or 0.0)
    n_rows = int(summary.get("rows", len(rows)) or len(rows))
    n_states = len({(r.get("state") or "—") for r in rows})
    avg_pt = (pt_total / employees) if employees else 0.0

    kpis = [
        ("PT MEMBERS", employees, "#475569"),
        ("STATES", n_states, accent),
        ("SLAB ROWS", n_rows, deep),
        ("PT REMITTED", pt_total, "#b91c1c", MONEY),
        ("AVG / HEAD", avg_pt, "#92400e", MONEY),
    ]
    nxt = corporate_kpi_strip(wb, ws, theme, kpis, start_row=nxt, last_col=last_col)

    # ── Header row ───────────────────────────────────────────────────────────
    f_head = corporate_header_format(wb, theme, align="left")
    f_head_r = corporate_header_format(wb, theme, align="right")

    hrow = nxt
    for i, (label, _, kind) in enumerate(_COLS):
        ws.write(hrow, i, label, f_head_r if kind == "money" else f_head)
    ws.set_row(hrow, 26)
    ws.freeze_panes(hrow + 1, 0)

    # ── Body cell formats (zebra) ────────────────────────────────────────────
    def _mk(extra, zebra=False):
        f = {"font_size": BRAND["body_pt"], "indent": 1, "border": 1,
             "border_color": BRAND["rule_soft"], "valign": "vcenter"}
        f.update(extra)
        if zebra:
            f["bg_color"] = BRAND["cream"]
        return wb.add_format(f)

    f_text = (_mk({"align": "left"}), _mk({"align": "left"}, True))
    f_code = (_mk({"align": "left", "font_name": "Consolas"}),
              _mk({"align": "left", "font_name": "Consolas"}, True))
    f_money = (_mk({"align": "right", "num_format": MONEY}),
               _mk({"align": "right", "num_format": MONEY}, True))

    # state-subtotal band
    f_sub_l = wb.add_format({
        "bold": True, "font_size": BRAND["body_pt"], "italic": True,
        "font_color": deep, "bg_color": theme["accent_soft"],
        "align": "left", "indent": 1, "border": 1, "border_color": accent,
        "valign": "vcenter"})
    f_sub_m = wb.add_format({
        "bold": True, "font_size": BRAND["body_pt"], "num_format": MONEY,
        "font_color": deep, "bg_color": theme["accent_soft"],
        "align": "right", "border": 1, "border_color": accent, "valign": "vcenter"})
    f_sub_b = wb.add_format({
        "bg_color": theme["accent_soft"], "border": 1, "border_color": accent})

    # ── Body: group by state, emit a subtotal band after each state ──────────
    # rows already arrive sorted by (state, name) from the shaper.
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        st = r.get("state") or "—"
        if st not in grouped:
            grouped[st] = []
            order.append(st)
        grouped[st].append(r)

    from xlsxwriter.utility import xl_col_to_name as _xlc
    GROSS_C, PT_C = 4, 5
    rr = hrow + 1
    body_first = rr  # first data row (for grand-total + data_bar range)
    zi = 0
    state_chart_data: list[tuple[str, float]] = []

    for st in order:
        members = grouped[st]
        st_pt = 0.0
        for r in members:
            zebra = zi % 2 == 1
            zi += 1
            ws.write(rr, 0, st, f_text[1] if zebra else f_text[0])
            ws.write(rr, 1, r.get("location") or "—", f_text[1] if zebra else f_text[0])
            ws.write(rr, 2, r.get("employee_code") or "—", f_code[1] if zebra else f_code[0])
            ws.write(rr, 3, r.get("employee_name") or "—", f_text[1] if zebra else f_text[0])
            ws.write_number(rr, GROSS_C, float(r.get("gross") or 0), f_money[1] if zebra else f_money[0])
            pt_v = float(r.get("pt") or 0)
            ws.write_number(rr, PT_C, pt_v, f_money[1] if zebra else f_money[0])
            st_pt += pt_v
            rr += 1

        # state subtotal band
        gross_col = _xlc(GROSS_C)
        pt_col = _xlc(PT_C)
        first_excel = rr - len(members) + 1
        last_excel = rr
        ws.merge_range(rr, 0, rr, 3, f"  {st} — {len(members)} member(s)", f_sub_l)
        ws.write_formula(rr, GROSS_C,
                         f"=SUM({gross_col}{first_excel}:{gross_col}{last_excel})", f_sub_m)
        ws.write_formula(rr, PT_C,
                         f"=SUM({pt_col}{first_excel}:{pt_col}{last_excel})", f_sub_m)
        rr += 1
        state_chart_data.append((st, round(st_pt, 2)))

    body_last = rr - 1  # inclusive last written row (a subtotal band)

    # ── Data bar on the PT column over member rows only ──────────────────────
    if rows:
        pt_col = _xlc(PT_C)
        ws.conditional_format(
            body_first, PT_C, body_last, PT_C,
            {"type": "data_bar", "bar_color": accent, "bar_solid_fill": True,
             "bar_only": False})
        # gross heat (3-colour) for quick read of pay spread
        ws.conditional_format(
            body_first, GROSS_C, body_last, GROSS_C,
            {"type": "3_color_scale",
             "min_color": "#fff7e0", "mid_color": "#f6d77a", "max_color": accent})

    # ── Grand total row ──────────────────────────────────────────────────────
    f_tl = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                          "align": "left", "indent": 1, "border": 1,
                          "border_color": deep, "font_size": BRAND["body_pt"]})
    f_tm = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": deep,
                          "align": "right", "num_format": MONEY, "border": 1,
                          "border_color": deep, "font_size": BRAND["body_pt"]})
    f_tb = wb.add_format({"bg_color": deep, "border": 1, "border_color": deep})

    tr = body_last + 1
    gross_col = _xlc(GROSS_C)
    pt_col = _xlc(PT_C)
    ws.merge_range(tr, 0, tr, 3, "  GRAND TOTAL · ALL STATES", f_tl)
    # Sum member rows (skip the amber subtotal bands by summing only the value
    # range and dividing — instead, sum the raw member rows directly).
    ws.write_number(tr, GROSS_C, round(sum(float(r.get("gross") or 0) for r in rows), 2), f_tm)
    ws.write_number(tr, PT_C, round(sum(float(r.get("pt") or 0) for r in rows), 2), f_tm)
    ws.set_row(tr, 22)

    # ── Autofilter (members only — exclude total) ────────────────────────────
    if rows:
        ws.autofilter(hrow, 0, body_last, last_col)

    # ── Charts sheet: PT per state column chart ──────────────────────────────
    if state_chart_data:
        cs = wb.add_worksheet("Charts")
        cs.set_tab_color(deep)
        cs.hide_gridlines(2)
        cs.set_column(0, 0, 24)
        cs.set_column(1, 1, 18)

        f_ct = wb.add_format({"bold": True, "font_size": 14, "font_color": BRAND["ink"]})
        f_cs = wb.add_format({"italic": True, "font_size": 9, "font_color": BRAND["ink_muted"]})
        f_ch = corporate_header_format(wb, theme, align="left")
        f_chr = corporate_header_format(wb, theme, align="right")
        f_cv = wb.add_format({"border": 1, "border_color": BRAND["rule_soft"],
                              "align": "left", "indent": 1})
        f_cm = wb.add_format({"border": 1, "border_color": BRAND["rule_soft"],
                              "align": "right", "num_format": MONEY})

        cs.write(0, 0, "Professional Tax by State", f_ct)
        cs.write(1, 0, f"{period.get('label', '')} · FY {period.get('fy', '')}", f_cs)
        drow = 3
        cs.write(drow, 0, "State", f_ch)
        cs.write(drow, 1, "PT Collected", f_chr)
        for i, (st, val) in enumerate(state_chart_data):
            cs.write(drow + 1 + i, 0, st, f_cv)
            cs.write_number(drow + 1 + i, 1, val, f_cm)
        dlast = drow + len(state_chart_data)

        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name": "PT Collected",
            "categories": ["Charts", drow + 1, 0, dlast, 0],
            "values": ["Charts", drow + 1, 1, dlast, 1],
            "fill": {"color": accent},
            "border": {"color": deep},
            "data_labels": {"value": True, "num_format": MONEY, "font": {"size": 8}},
        })
        chart.set_title({"name": "PT Remittance by State"})
        chart.set_x_axis({"name": "State"})
        chart.set_y_axis({"name": "PT (₹)", "num_format": MONEY})
        chart.set_legend({"none": True})
        chart.set_size({"width": 560, "height": 340})
        cs.insert_chart(3, 3, chart)

    return xw_finalize(wb, buf)
