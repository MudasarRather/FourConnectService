"""Excel workbook — Salary Sheet report (xlsxwriter).

A bespoke salary register laid out the way an Indian HR/finance team files it:
a corporate title block + KPI strip, then a two-band table with a grouped
EARNINGS section (Basic · HRA · Other · Gross) and a DEDUCTIONS section
(PF · ESI · PT · TDS · Total) reconciling to NET. Zebra body rows, money
number formats, a heat-map data-bar on net pay, a banded TOTAL row that sums
every money column with live formulas, autofilter + frozen panes.

Accent: #d97706 / soft #fef3c7 / deep #92400e (matches the PDF cover).
"""
from __future__ import annotations

from ..data import report_meta
from ..excel_common import (
    BRAND, MONEY, xw_workbook, xw_finalize,
    corporate_title_block, corporate_kpi_strip, corporate_header_format, body_formats,
)
from ..common import inr_compact


SHEET_NAME = "Salary Sheet"

# (key, header, kind, group)  kind in {text, mono, money}
_COLUMNS = [
    ("employee_code", "Emp Code",  "mono",  "id"),
    ("employee_name", "Employee",  "text",  "id"),
    ("department",    "Department","text",  "id"),
    ("basic",         "Basic",     "money", "earn"),
    ("hra",           "HRA",       "money", "earn"),
    ("other_earnings","Other Earnings", "money", "earn"),
    ("gross",         "Gross",     "money", "earn"),
    ("pf_employee",   "PF (EE)",   "money", "ded"),
    ("esi_employee",  "ESI (EE)",  "money", "ded"),
    ("pt",            "PT",        "money", "ded"),
    ("tds",           "TDS",       "money", "ded"),
    ("deductions_total", "Total Deductions", "money", "ded"),
    ("net",           "Net Pay",   "money", "net"),
]


def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta("salary-sheet")
    accent = theme["accent"]
    deep = theme["accent_deep"]
    soft = "#fef3c7"
    period = meta["period"]

    last_col = len(_COLUMNS) - 1

    wb, buf = xw_workbook()
    ws = wb.add_worksheet(SHEET_NAME[:31])
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)

    # Column widths
    ws.set_column(0, 0, 13)   # code
    ws.set_column(1, 1, 26)   # name
    ws.set_column(2, 2, 18)   # dept
    ws.set_column(3, last_col, 14)  # money cols

    # ── Title block + KPI strip ────────────────────────────────────────────
    nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=last_col)

    kpis = [
        ("EMPLOYEES PAID", int(summary.get("employees", summary.get("rows", len(rows))) or 0), deep),
        ("GROSS EARNINGS", float(summary.get("gross", 0) or 0), "#b8860b", MONEY),
        ("TOTAL DEDUCTIONS", float(summary.get("deductions", 0) or 0), "#b91c1c", MONEY),
        ("NET DISBURSED", float(summary.get("net", 0) or 0), "#047857", MONEY),
        ("AVG NET / HEAD", float(summary.get("avg_net", 0) or 0), accent, MONEY),
    ]
    nxt = corporate_kpi_strip(wb, ws, theme, kpis, start_row=nxt, last_col=last_col)

    # ── Group band row (EARNINGS | DEDUCTIONS) above the header ────────────
    group_row = nxt
    f_grp_id = wb.add_format({
        "bold": True, "font_size": 8, "font_color": BRAND["ink_muted"],
        "bg_color": BRAND["panel_soft"], "align": "left", "valign": "vcenter",
        "indent": 1, "top": 1, "top_color": BRAND["rule_soft"],
    })
    f_grp_earn = wb.add_format({
        "bold": True, "font_size": 8.5, "font_color": "#7a5a00",
        "bg_color": soft, "align": "center", "valign": "vcenter",
        "top": 2, "top_color": "#b8860b", "border": 1, "border_color": "#e3cfa0",
        "italic": True,
    })
    f_grp_ded = wb.add_format({
        "bold": True, "font_size": 8.5, "font_color": "#7f1d1d",
        "bg_color": "#fee2e2", "align": "center", "valign": "vcenter",
        "top": 2, "top_color": "#b91c1c", "border": 1, "border_color": "#f3c0c0",
        "italic": True,
    })
    f_grp_net = wb.add_format({
        "bold": True, "font_size": 8.5, "font_color": "#065f46",
        "bg_color": BRAND["net_bg"], "align": "center", "valign": "vcenter",
        "top": 2, "top_color": "#047857", "border": 1, "border_color": "#b7e4cd",
        "italic": True,
    })
    ws.set_row(group_row, 16)
    ws.merge_range(group_row, 0, group_row, 2, "IDENTITY", f_grp_id)
    ws.merge_range(group_row, 3, group_row, 6, "EARNINGS", f_grp_earn)
    ws.merge_range(group_row, 7, group_row, 11, "DEDUCTIONS", f_grp_ded)
    ws.write(group_row, 12, "TAKE-HOME", f_grp_net)

    # ── Header row ─────────────────────────────────────────────────────────
    hrow = group_row + 1
    f_head_l = corporate_header_format(wb, theme, align="left")
    f_head_r = corporate_header_format(wb, theme, align="right")
    for i, (key, label, kind, grp) in enumerate(_COLUMNS):
        align = "right" if kind == "money" else "left"
        ws.write(hrow, i, label, f_head_r if align == "right" else f_head_l)
    ws.set_row(hrow, 26)
    ws.freeze_panes(hrow + 1, 3)

    # ── Body rows (zebra) ──────────────────────────────────────────────────
    fmts = body_formats(wb, money_fmt=MONEY)
    # custom net-pay cell — emerald tint to stand out
    def _net_fmt(zebra):
        f = {"font_size": BRAND["body_pt"], "indent": 1, "border": 1,
             "border_color": BRAND["rule_soft"], "valign": "vcenter",
             "align": "right", "num_format": MONEY, "bold": True,
             "font_color": "#065f46", "bg_color": "#f0fbf5" if not zebra else "#e6f6ee"}
        return wb.add_format(f)
    net_fmt = (_net_fmt(False), _net_fmt(True))

    kind_for = {
        "id": {"employee_code": "mono", "employee_name": "text", "department": "text"},
    }

    first_data = hrow + 1
    for ri, r in enumerate(rows):
        zebra = ri % 2 == 1
        rr = first_data + ri
        for i, (key, label, kind, grp) in enumerate(_COLUMNS):
            if key == "net":
                f = net_fmt[1 if zebra else 0]
                ws.write_number(rr, i, float(r.get(key, 0) or 0), f)
            elif kind == "money":
                base, zb = fmts["money"]
                ws.write_number(rr, i, float(r.get(key, 0) or 0), zb if zebra else base)
            elif kind == "mono":
                base, zb = fmts["mono"]
                v = r.get(key)
                ws.write(rr, i, v if v not in (None, "") else "—", zb if zebra else base)
            else:
                base, zb = fmts["text"]
                v = r.get(key)
                ws.write(rr, i, v if v not in (None, "") else "—", zb if zebra else base)

    # ── TOTAL row — live SUM formulas on every money column ────────────────
    from xlsxwriter.utility import xl_col_to_name as _xlc
    last_row = hrow + len(rows)  # 0-based index of last data row
    if rows:
        tr = last_row + 1
        f_tot_l = wb.add_format({
            "bold": True, "font_color": "#fff", "bg_color": deep,
            "align": "left", "indent": 1, "valign": "vcenter",
            "border": 1, "border_color": deep, "font_size": 10,
        })
        f_tot_m = wb.add_format({
            "bold": True, "font_color": "#fde68a", "bg_color": deep,
            "align": "right", "num_format": MONEY, "valign": "vcenter",
            "border": 1, "border_color": deep, "font_size": 10,
        })
        f_tot_net = wb.add_format({
            "bold": True, "font_color": "#fff", "bg_color": "#047857",
            "align": "right", "num_format": MONEY, "valign": "vcenter",
            "border": 1, "border_color": "#065f46", "font_size": 10,
        })
        ws.set_row(tr, 22)
        ws.merge_range(tr, 0, tr, 2, f"TOTAL · {len(rows)} EMPLOYEE(S)", f_tot_l)
        for i, (key, label, kind, grp) in enumerate(_COLUMNS):
            if kind != "money":
                continue
            col = _xlc(i)
            formula = f"=SUM({col}{first_data + 1}:{col}{last_row + 1})"
            ws.write_formula(tr, i, formula, f_tot_net if key == "net" else f_tot_m)

        # ── Autofilter (header → last data row) ────────────────────────────
        ws.autofilter(hrow, 0, last_row, last_col)

        # ── Conditional formats ────────────────────────────────────────────
        net_col = _xlc(12)
        gross_col = _xlc(6)
        ded_col = _xlc(11)
        rng_net = f"{net_col}{first_data + 1}:{net_col}{last_row + 1}"
        rng_gross = f"{gross_col}{first_data + 1}:{gross_col}{last_row + 1}"
        rng_ded = f"{ded_col}{first_data + 1}:{ded_col}{last_row + 1}"

        # data bar on net pay — quick visual scan of who takes home most
        ws.conditional_format(rng_net, {
            "type": "data_bar", "bar_color": "#34d399",
            "bar_solid_fill": True, "bar_border_color": "#047857",
        })
        # 3-colour heat on gross
        ws.conditional_format(rng_gross, {
            "type": "3_color_scale",
            "min_color": "#fff7ed", "mid_color": "#fed7aa", "max_color": "#d97706",
        })
        # deduction load heat (red the higher the deduction)
        ws.conditional_format(rng_ded, {
            "type": "2_color_scale",
            "min_color": "#ffffff", "max_color": "#fecaca",
        })

    # footer note
    note_row = (last_row + 3) if rows else (hrow + 2)
    f_note = wb.add_format({"font_size": 7.5, "italic": True, "font_color": BRAND["ink_dim"]})
    ws.merge_range(
        note_row, 0, note_row, last_col,
        "Net Pay = Gross Earnings − Total Deductions. Figures in INR. "
        "Confidential — internal payroll use only.",
        f_note,
    )

    return xw_finalize(wb, buf)
