"""Adjustments Register — Excel workbook (xlsxwriter).

A bespoke "ticket stub" workbook for one-off pay-run amounts. Rows are tinted
by adjustment type (bonus / incentive / variable / arrear / deduction), the KPI
strip splits additions vs deductions vs net impact, the Amount column carries a
crimson→emerald data-bar heat, deduction rows render their amount negative, and
a TOTAL row sums additions/deductions/signed-net. A second "Charts" sheet plots
amount-by-type as a column chart in the report's rose palette.

Public entry point:
    render(rows, summary, meta) -> bytes   (.xlsx)
"""
from __future__ import annotations

from ..data import report_meta
from ..excel_common import (
    xw_workbook, xw_finalize,
    corporate_title_block, corporate_kpi_strip, corporate_header_format,
    BRAND, MONEY,
)
from ..common import inr_compact


# Per-type row tint (soft fill, deeper ink). Mirrors the cover's rose family.
_TYPE_STYLE = {
    "BONUS":        {"bg": "#fff1f3", "fg": "#9f1239", "tag": "Bonus"},
    "INCENTIVE":    {"bg": "#fff7ed", "fg": "#9a3412", "tag": "Incentive"},
    "VARIABLE_PAY": {"bg": "#fef9c3", "fg": "#854d0e", "tag": "Variable Pay"},
    "ARREAR":       {"bg": "#eef2ff", "fg": "#3730a3", "tag": "Arrear"},
    "DEDUCTION":    {"bg": "#fee2e2", "fg": "#991b1b", "tag": "Deduction"},
}
_STATUS_STYLE = {
    "PAID":     {"bg": "#dcfce7", "fg": "#166534"},
    "APPROVED": {"bg": "#dbeafe", "fg": "#1e40af"},
    "DRAFT":    {"bg": "#f1f5f9", "fg": "#475569"},
}

SHEET_NAME = "Adjustments Register"

# Columns: Code · Employee · Department · Type · Sub-type · Title · Taxable ·
#          Status · Addition · Deduction · Net
_HEADERS = [
    ("Code", 12, "left"),
    ("Employee", 24, "left"),
    ("Department", 16, "left"),
    ("Type", 15, "left"),
    ("Sub-type", 15, "left"),
    ("Title", 30, "left"),
    ("Taxable", 9, "center"),
    ("Status", 12, "center"),
    ("Addition", 15, "right"),
    ("Deduction", 15, "right"),
    ("Net Impact", 15, "right"),
]
LAST_COL = len(_HEADERS) - 1
COL_ADD, COL_DED, COL_NET = 8, 9, 10


def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("adjustments")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    period = meta["period"]

    additions = float(summary.get("additions", 0) or 0)
    deductions = float(summary.get("deductions", 0) or 0)
    net_impact = summary.get("net_impact")
    if net_impact is None:
        net_impact = additions - deductions

    wb, buf = xw_workbook()
    ws = wb.add_worksheet(SHEET_NAME[:31])
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)
    for i, (_, w, _a) in enumerate(_HEADERS):
        ws.set_column(i, i, w)

    nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=LAST_COL)
    kpis = [
        ("POSTINGS", summary.get("rows", len(rows)), accent),
        ("EMPLOYEES", summary.get("employees", 0), BRAND["ink_muted"]),
        ("ADDITIONS", additions, "#047857", MONEY),
        ("DEDUCTIONS", deductions, "#b91c1c", MONEY),
        ("NET IMPACT", net_impact, deep, MONEY),
    ]
    nxt = corporate_kpi_strip(wb, ws, theme, kpis, start_row=nxt, last_col=LAST_COL)

    # ── Header ──────────────────────────────────────────────────────────
    f_head = corporate_header_format(wb, theme, align="left")
    f_head_c = corporate_header_format(wb, theme, align="center")
    f_head_r = corporate_header_format(wb, theme, align="right")
    hrow = nxt
    for i, (label, _w, align) in enumerate(_HEADERS):
        f = f_head_r if align == "right" else f_head_c if align == "center" else f_head
        ws.write(hrow, i, label, f)
    ws.set_row(hrow, 26)
    ws.freeze_panes(hrow + 1, 2)

    # ── Body cell-format cache (keyed per type/zebra/kind) ──────────────
    cache: dict = {}

    def cell(kind: str, bg: str, fg: str, *, bold=False):
        key = (kind, bg, fg, bold)
        if key in cache:
            return cache[key]
        base = {
            "font_size": BRAND["body_pt"], "valign": "vcenter",
            "border": 1, "border_color": "#f1d5db", "bg_color": bg,
            "font_color": fg,
        }
        if kind == "text":
            base.update({"align": "left", "indent": 1})
        elif kind == "center":
            base.update({"align": "center"})
        elif kind == "money":
            base.update({"align": "right", "num_format": MONEY})
        elif kind == "money_neg":
            base.update({"align": "right", "num_format": '"−"' + MONEY.split(";")[-1]})
        elif kind == "tag":
            base.update({"align": "left", "indent": 1, "bold": True})
        if bold:
            base["bold"] = True
        f = wb.add_format(base)
        cache[key] = f
        return f

    f_status_cache: dict = {}

    def status_fmt(status: str):
        if status not in f_status_cache:
            st = _STATUS_STYLE.get(status, {"bg": "#f1f5f9", "fg": "#475569"})
            f_status_cache[status] = wb.add_format({
                "font_size": 8, "bold": True, "align": "center", "valign": "vcenter",
                "border": 1, "border_color": "#f1d5db",
                "bg_color": st["bg"], "font_color": st["fg"],
            })
        return f_status_cache[status]

    # ── Body rows ───────────────────────────────────────────────────────
    r0 = hrow + 1
    for ri, r in enumerate(rows):
        rr = r0 + ri
        a_type = str(r.get("adjustment_type") or "").upper()
        style = _TYPE_STYLE.get(a_type, {"bg": "#fdf2f8", "fg": "#1a0a10", "tag": a_type.title() or "—"})
        is_ded = bool(r.get("is_deduction")) or a_type == "DEDUCTION"
        amount = float(r.get("amount") or 0)

        # subtle zebra: darken the type tint slightly on odd rows via white text bg? keep tint, alt border
        bg, fg = style["bg"], "#3a1620"

        ws.write(rr, 0, r.get("employee_code") or "—", cell("text", bg, fg))
        ws.write(rr, 1, r.get("employee_name") or "—", cell("text", bg, fg, bold=True))
        ws.write(rr, 2, r.get("department") or "—", cell("text", bg, fg))
        ws.write(rr, 3, style["tag"], cell("tag", bg, style["fg"]))
        ws.write(rr, 4, r.get("sub_type") or "—", cell("text", bg, fg))
        ws.write(rr, 5, r.get("title") or "—", cell("text", bg, fg))
        ws.write(rr, 6, "Yes" if r.get("is_taxable") else "No", cell("center", bg, fg))
        ws.write(rr, 7, str(r.get("status") or "—").title(), status_fmt(str(r.get("status") or "")))

        add_val = 0.0 if is_ded else amount
        ded_val = amount if is_ded else 0.0
        net_val = -amount if is_ded else amount

        ws.write_number(rr, COL_ADD, add_val, cell("money", bg, "#047857" if add_val else "#b8a7ac"))
        ws.write_number(rr, COL_DED, ded_val, cell("money", bg, "#b91c1c" if ded_val else "#b8a7ac"))
        ws.write_number(rr, COL_NET, net_val,
                        cell("money", bg, "#047857" if net_val >= 0 else "#b91c1c", bold=True))

    last_row = r0 + len(rows) - 1 if rows else hrow

    # ── TOTAL row ───────────────────────────────────────────────────────
    from xlsxwriter.utility import xl_col_to_name as _xlc
    if rows:
        tr = last_row + 1
        f_tl = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": deep,
                              "align": "left", "indent": 1, "valign": "vcenter",
                              "border": 1, "border_color": deep})
        f_tm = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": deep,
                              "align": "right", "num_format": MONEY, "valign": "vcenter",
                              "border": 1, "border_color": deep})
        f_tb = wb.add_format({"bg_color": deep, "border": 1, "border_color": deep})
        ws.merge_range(tr, 0, tr, COL_ADD - 1, f"TOTAL  ·  {len(rows)} postings", f_tl)
        for c in (COL_ADD, COL_DED, COL_NET):
            col = _xlc(c)
            ws.write_formula(tr, c, f"=SUM({col}{r0 + 1}:{col}{last_row + 1})", f_tm)

        # ── Autofilter + conditional heat on money columns ──────────────
        ws.autofilter(hrow, 0, last_row, LAST_COL)
        ws.conditional_format(r0, COL_ADD, last_row, COL_ADD, {
            "type": "data_bar", "bar_color": "#34d399", "bar_solid_fill": True,
            "bar_only": False,
        })
        ws.conditional_format(r0, COL_DED, last_row, COL_DED, {
            "type": "data_bar", "bar_color": "#f87171", "bar_solid_fill": True,
        })
        ws.conditional_format(r0, COL_NET, last_row, COL_NET, {
            "type": "3_color_scale",
            "min_color": "#fecaca", "mid_color": "#fef3c7", "max_color": "#bbf7d0",
        })

    # ── Charts sheet — amount by adjustment type ────────────────────────
    cs = wb.add_worksheet("Charts")
    cs.set_tab_color(deep)
    cs.hide_gridlines(2)
    cs.set_column(0, 0, 22)
    cs.set_column(1, 2, 16)

    by_type: dict[str, float] = {}
    for r in rows:
        t = _TYPE_STYLE.get(str(r.get("adjustment_type") or "").upper(),
                            {"tag": str(r.get("adjustment_type") or "Other").title()})["tag"]
        by_type[t] = by_type.get(t, 0.0) + float(r.get("amount") or 0)

    f_ch_title = wb.add_format({"bold": True, "font_size": 13, "font_color": deep})
    f_ch_head = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": accent,
                               "align": "left", "border": 1, "border_color": deep, "indent": 1})
    f_ch_headr = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": accent,
                                "align": "right", "border": 1, "border_color": deep})
    f_ch_t = wb.add_format({"border": 1, "border_color": "#f1d5db", "indent": 1, "align": "left"})
    f_ch_m = wb.add_format({"border": 1, "border_color": "#f1d5db", "num_format": MONEY, "align": "right"})

    cs.write(0, 0, "Adjustments by Type", f_ch_title)
    cs.write(2, 0, "Type", f_ch_head)
    cs.write(2, 1, "Amount", f_ch_headr)
    dr = 3
    type_order = list(by_type.keys()) or ["—"]
    for t in type_order:
        cs.write(dr, 0, t, f_ch_t)
        cs.write_number(dr, 1, round(by_type.get(t, 0.0), 2), f_ch_m)
        dr += 1
    data_last = dr - 1

    if by_type:
        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name": "Amount (₹)",
            "categories": ["Charts", 3, 0, data_last, 0],
            "values":     ["Charts", 3, 1, data_last, 1],
            "fill":       {"color": accent},
            "border":     {"color": deep},
            "data_labels": {"value": True, "num_format": '"₹"#,##0'},
            "gap": 80,
        })
        chart.set_title({"name": "Adjustment Amount by Type"})
        chart.set_x_axis({"name": "Type"})
        chart.set_y_axis({"name": "Amount (₹)", "num_format": '"₹"#,##0'})
        chart.set_legend({"none": True})
        chart.set_size({"width": 560, "height": 340})
        cs.insert_chart(2, 3, chart, {"x_offset": 8, "y_offset": 4})

        # Net-impact callout under the table
        f_note = wb.add_format({"italic": True, "font_color": BRAND["ink_muted"], "font_size": 9})
        cs.write(data_last + 2, 0, "Net impact on pay run", f_note)
        cs.write(data_last + 2, 1, inr_compact(net_impact), f_note)

    return xw_finalize(wb, buf)
