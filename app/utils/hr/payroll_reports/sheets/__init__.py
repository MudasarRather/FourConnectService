"""Per-report Excel workbook renderers for HR Payroll Reports.

Each report key maps to a module exposing ``render(rows, summary, meta) -> bytes``.
The loader wires them into ``RENDERERS`` defensively, falling back to a clean
generic xlsxwriter sheet (title block + KPI strip + themed table) for any report
whose module isn't present yet — so exports never 500 on a half-built package.
"""
from __future__ import annotations

import importlib

from ..columns import body_columns
from ..data import report_meta
from ..excel_common import (
    BRAND, MONEY, xw_workbook, xw_finalize,
    corporate_title_block, corporate_kpi_strip, corporate_header_format, body_formats,
)
from ..common import inr_compact

# report key → module filename (underscored)
_KEY_MODULES = {
    "register": "register",
    "salary-sheet": "salary_sheet",
    "statutory": "statutory",
    "pf-ecr": "pf_ecr",
    "esi": "esi",
    "professional-tax": "professional_tax",
    "tds-24q": "tds_24q",
    "department-cost": "department_cost",
    "variance": "variance",
    "ctc-summary": "ctc_summary",
    "headcount": "headcount",
    "adjustments": "adjustments",
    "ytd-earnings": "ytd_earnings",
}

# fmt → body_formats kind
_FMT_KIND = {None: "text", "inr": "money", "inr_p": "money",
             "days": "days", "pct": "pct", "signed_pct": "pct", "date": "text"}


def _generic_kpis(summary: dict, accent: str) -> list:
    out = []
    if summary.get("employees") is not None:
        out.append(("EMPLOYEES", summary["employees"], "#475569"))
    if summary.get("headcount") is not None and summary.get("headcount") != summary.get("employees"):
        out.append(("RECORDS", summary["headcount"], accent))
    for label, key, color in (("GROSS", "gross", "#b8860b"), ("ANNUAL CTC", "annual_ctc", "#0891b2"),
                              ("YTD GROSS", "ytd_gross", "#b8860b"), ("ADDITIONS", "additions", "#047857")):
        if summary.get(key):
            out.append((label, inr_compact(summary[key]), color))
            break
    for label, key, color in (("DEDUCTIONS", "deductions", "#b91c1c"), ("YTD TDS", "ytd_tds", "#b91c1c")):
        if summary.get(key):
            out.append((label, inr_compact(summary[key]), color))
            break
    for label, key, color in (("NET PAY", "net", "#047857"), ("YTD NET", "ytd_net", "#047857"),
                              ("NET IMPACT", "net_impact", "#047857")):
        if summary.get(key) is not None:
            out.append((label, inr_compact(summary[key]), color))
            break
    return out[:6]


def _generic(key: str):
    def render(rows: list[dict], summary: dict, meta: dict) -> bytes:
        theme = report_meta(key)
        accent = theme["accent"]
        period = meta["period"]
        cols = body_columns(key)
        last_col = len(cols) - 1

        wb, buf = xw_workbook()
        ws = wb.add_worksheet(theme["name"][:31])
        ws.set_tab_color(accent)
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 16)
        ws.set_column(1, 1, 26)
        if last_col >= 2:
            ws.set_column(2, last_col, 15)

        nxt = corporate_title_block(wb, ws, theme, period, summary, last_col=last_col)
        nxt = corporate_kpi_strip(wb, ws, theme, _generic_kpis(summary, accent), start_row=nxt, last_col=last_col)

        f_head = corporate_header_format(wb, theme, align="left")
        f_head_r = corporate_header_format(wb, theme, align="right")
        fmts = body_formats(wb)

        hrow = nxt
        for i, c in enumerate(cols):
            ws.write(hrow, i, c["label"], f_head_r if c["align"] == "right" else f_head)
        ws.set_row(hrow, 28)
        ws.freeze_panes(hrow + 1, 0)

        for ri, r in enumerate(rows):
            zebra = ri % 2 == 1
            rr = hrow + 1 + ri
            for i, c in enumerate(cols):
                kind = _FMT_KIND.get(c.get("fmt"), "text")
                base, zb = fmts[kind]
                f = zb if zebra else base
                v = r.get(c["key"])
                if kind == "text":
                    ws.write(rr, i, v if v not in (None, "") else "—", f)
                else:
                    ws.write_number(rr, i, float(v or 0), f)

        if rows:
            last_row = hrow + len(rows)
            ws.autofilter(hrow, 0, last_row, last_col)
            # total row for money columns
            tr = last_row + 1
            from xlsxwriter.utility import xl_col_to_name as _xlc
            f_tl = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": theme["accent_deep"],
                                  "align": "left", "indent": 1, "border": 1, "border_color": theme["accent_deep"]})
            f_tm = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": theme["accent_deep"],
                                  "align": "right", "num_format": MONEY, "border": 1, "border_color": theme["accent_deep"]})
            f_tb = wb.add_format({"bg_color": theme["accent_deep"], "border": 1, "border_color": theme["accent_deep"]})
            for i, c in enumerate(cols):
                if i == 0:
                    ws.write(tr, i, "TOTAL", f_tl)
                elif c.get("fmt") in ("inr", "inr_p"):
                    col = _xlc(i)
                    ws.write_formula(tr, i, f"=SUM({col}{hrow + 2}:{col}{last_row + 1})", f_tm)
                else:
                    ws.write_blank(tr, i, None, f_tb)

        return xw_finalize(wb, buf)
    return render


def _load() -> dict:
    renderers = {}
    for key, modname in _KEY_MODULES.items():
        try:
            mod = importlib.import_module(f"{__name__}.{modname}")
            fn = getattr(mod, "render", None)
            renderers[key] = fn if callable(fn) else _generic(key)
        except Exception:  # noqa: BLE001
            renderers[key] = _generic(key)
    return renderers


RENDERERS = _load()
