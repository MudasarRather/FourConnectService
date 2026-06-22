"""Travel Reports — Excel renderer (xlsxwriter).

A polished two-sheet workbook:
  • "Report"   — corporate title block, KPI cards, a fully-bordered + banded
                 data table with currency formats, coloured status pills,
                 traffic-light conditional formats, a totals row, frozen header
                 and autofilter.
  • "Insights" — KPI recap + a readable top-12 native chart (sourced from
                 VISIBLE data so Excel actually plots it).

All number formats are valid Excel codes (no repair prompts) and charts never
reference hidden cells (so they never render as empty frames).
"""
from __future__ import annotations

import io
from typing import List, Dict, Any, Optional

from .data import columns_for, report_meta, status_color

# Valid, repair-safe number formats.
_FMT_CODE = {"inr": '"₹"#,##0', "int": "#,##0", "pct": '0.0"%"', "days": "0.0"}

_CHART: Dict[str, Dict[str, Any]] = {
    "travel-requests": {"cat": "employee", "series": [("Est. Cost", "est_cost")], "type": "bar"},
    "booking-register": {"cat": "employee", "series": [("Total", "total")], "type": "bar"},
    "employee-history": {"cat": "employee", "series": [("Est. spend", "est_cost"), ("Tours", "tours")], "type": "column"},
    "department-travel": {"cat": "department", "series": [("Est. spend", "est_cost")], "type": "bar"},
    "route-analysis": {"cat": "route", "series": [("Trips", "trips")], "type": "bar"},
    "travel-cost": {"cat": "ref", "series": [("Estimated", "estimated"), ("Actual", "actual")], "type": "column"},
    "da-report": {"cat": "employee", "series": [("Eligible DA", "eligible"), ("Approved DA", "approved")], "type": "column"},
    "advance-report": {"cat": "employee", "series": [("Requested", "requested"), ("Approved", "approved")], "type": "column"},
    "advance-outstanding": {"cat": "employee", "series": [("Released", "released")], "type": "bar"},
    "settlement-report": {"cat": "employee", "series": [("Payable", "payable"), ("Recoverable", "recoverable")], "type": "column"},
    "frequent-travelers": {"cat": "employee", "series": [("Tours", "tours")], "type": "bar"},
    "approval-tat": {"cat": "ref", "series": [("TAT (hrs)", "tat_hours")], "type": "bar"},
}
_COND = {
    "travel-cost": {"variance_pct": ("3color", "#16a34a", "#fde68a", "#dc2626")},
    "advance-outstanding": {"age_days": ("3color", "#16a34a", "#f59e0b", "#dc2626")},
    "approval-tat": {"tat_hours": ("databar", "#f59e0b")},
}


def _inr_k(v) -> str:
    v = float(v or 0)
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a/1e7:.2f}".rstrip("0").rstrip(".") + "Cr"
    if a >= 1e5:
        return f"{sign}₹{a/1e5:.2f}".rstrip("0").rstrip(".") + "L"
    if a >= 1e3:
        return f"{sign}₹{a/1e3:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{sign}₹{int(a)}"


def _pretty(v) -> str:
    s = str(v)
    if "days" in s or " " in s:
        return s
    return s.replace("_", " ").title()


def _kpi_text(val, kind) -> str:
    if kind == "inr":
        return _inr_k(val)
    if kind == "pct":
        return f"{val}%"
    if kind == "days":
        return str(val)
    try:
        return f"{int(val):,}"
    except (TypeError, ValueError):
        return str(val)


def render_excel(report_key: str, rows: List[Dict[str, Any]], summary: dict,
                 meta_arg: Optional[dict] = None) -> bytes:
    try:
        import xlsxwriter
        from xlsxwriter.utility import xl_col_to_name
    except Exception:
        raise RuntimeError("Excel export unavailable (xlsxwriter not installed)")

    meta = report_meta(report_key)
    accent, deep, soft = meta["accent"], meta["deep"], meta["soft"]
    cols = columns_for(report_key)
    period = (meta_arg or {}).get("period") or "All time"
    ncol = max(1, len(cols))

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Report")
    ws.hide_gridlines(2)
    ws.set_default_row(18)
    try:
        ws.set_tab_color(accent)
    except Exception:
        pass

    # ── formats ──
    f_rail = wb.add_format({"bg_color": accent})
    f_title = wb.add_format({"font_name": "Calibri Light", "font_size": 22, "bold": True,
                             "font_color": deep, "valign": "vcenter", "bg_color": "#ffffff"})
    f_sub = wb.add_format({"font_size": 10.5, "italic": True, "font_color": "#6b5d48",
                           "valign": "vcenter", "bg_color": "#ffffff"})
    f_meta = wb.add_format({"font_size": 9, "font_color": "#8a7a5f", "bg_color": soft,
                            "valign": "vcenter", "align": "left", "indent": 1})
    f_kpi_lbl = wb.add_format({"font_size": 8, "bold": True, "font_color": "#7a6c54", "align": "center",
                               "valign": "vcenter", "bg_color": "#ffffff", "top": 5, "top_color": accent,
                               "left": 1, "right": 1, "left_color": "#e5dcca", "right_color": "#e5dcca"})
    f_kpi_val = wb.add_format({"font_size": 20, "bold": True, "font_color": deep, "align": "center",
                               "valign": "vcenter", "bg_color": "#ffffff", "left": 1, "right": 1, "bottom": 1,
                               "left_color": "#e5dcca", "right_color": "#e5dcca", "bottom_color": "#e5dcca"})
    f_blank = wb.add_format({"bg_color": "#ffffff"})

    def header_fmt(align):
        return wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": accent, "font_size": 9.5,
                              "align": "center" if align in ("center", "right") else "left", "valign": "vcenter",
                              "bottom": 2, "bottom_color": deep, "top": 2, "top_color": deep,
                              "left": 1, "right": 1, "left_color": deep, "right_color": deep, "text_wrap": True})

    fmt_cache: Dict[tuple, Any] = {}

    def body_fmt(align, fmt, zebra, mono=False, tone=None, last=False):
        kk = (align, fmt, zebra, mono, tone, last)
        if kk in fmt_cache:
            return fmt_cache[kk]
        d = {"align": align, "valign": "vcenter", "font_size": 10, "border": 1, "border_color": "#e7ddca",
             "bg_color": "#fbf6ec" if zebra else "#ffffff"}
        if mono:
            d.update({"font_name": "Consolas", "font_size": 9, "font_color": "#4a3f2e"})
        if fmt in _FMT_CODE:
            d["num_format"] = _FMT_CODE[fmt]
        if tone == "danger":
            d.update({"font_color": "#b91c1c", "bold": True})
        elif tone == "good":
            d.update({"font_color": "#047857", "bold": True})
        elif tone == "warn":
            d.update({"font_color": "#b45309", "bold": True})
        fmt_cache[kk] = wb.add_format(d)
        return fmt_cache[kk]

    pill_cache: Dict[tuple, Any] = {}

    def pill_fmt(value, zebra):
        c = status_color(value)
        kk = (value, zebra)
        if kk not in pill_cache:
            pill_cache[kk] = wb.add_format({"bg_color": c["light"], "font_color": c["deep"], "bold": True,
                                            "font_size": 9, "align": "center", "valign": "vcenter",
                                            "border": 1, "border_color": c["hex"]})
        return pill_cache[kk]

    last_letter = xl_col_to_name(ncol - 1)

    # ── title block ──
    ws.merge_range(0, 0, 0, ncol - 1, "", f_rail); ws.set_row(0, 6)
    ws.merge_range(1, 0, 1, ncol - 1, f"Fourreck  ·  {meta['name']}", f_title); ws.set_row(1, 34)
    ws.merge_range(2, 0, 2, ncol - 1, meta["subtitle"], f_sub); ws.set_row(2, 18)
    ws.merge_range(3, 0, 3, ncol - 1, f"  {meta['group']} report   ·   Period: {period}   ·   {len(rows)} record(s)", f_meta)
    ws.set_row(3, 22)
    ws.set_row(4, 8)
    ws.merge_range(4, 0, 4, ncol - 1, "", f_blank)

    # ── KPI cards (tiled cleanly, no overlap) ──
    tiles = (summary.get("tiles") or [])[:4]
    lbl_row, val_row = 5, 6
    if tiles:
        edges = [round(i * ncol / len(tiles)) for i in range(len(tiles) + 1)]
        for i, (label, val, kind) in enumerate(tiles):
            c0, c1 = edges[i], max(edges[i], edges[i + 1] - 1)
            if c1 > c0:
                ws.merge_range(lbl_row, c0, lbl_row, c1, str(label).upper(), f_kpi_lbl)
                ws.merge_range(val_row, c0, val_row, c1, _kpi_text(val, kind), f_kpi_val)
            else:
                ws.write(lbl_row, c0, str(label).upper(), f_kpi_lbl)
                ws.write(val_row, c0, _kpi_text(val, kind), f_kpi_val)
        ws.set_row(lbl_row, 16); ws.set_row(val_row, 30)
    ws.set_row(7, 8)
    ws.merge_range(7, 0, 7, ncol - 1, "", f_blank)

    # ── data table ──
    hdr_row = 8
    for ci, c in enumerate(cols):
        ws.write(hdr_row, ci, c["label"], header_fmt(c["align"]))
        w = {"left": 20, "center": 13, "right": 15}.get(c["align"], 16)
        if c["key"] in ("route", "detail"):
            w = 30
        elif c["key"] in ("employee", "from_loc", "to_loc"):
            w = 24
        elif c["key"] in ("booking_no", "advance_no", "settlement_no", "ref"):
            w = 17
        ws.set_column(ci, ci, w)
    ws.set_row(hdr_row, 26)

    first = hdr_row + 1
    for ri, r in enumerate(rows):
        zebra = ri % 2 == 1
        rownum = first + ri
        for ci, c in enumerate(cols):
            v = r.get(c["key"])
            if c.get("status"):
                ws.write(rownum, ci, _pretty(v), pill_fmt(v, zebra))
                continue
            tone = None
            for pred, t in (("danger_if", "danger"), ("good_if", "good"), ("warn_if", "warn")):
                fn = c.get(pred)
                try:
                    if fn and fn(v):
                        tone = t; break
                except Exception:
                    pass
            cf = body_fmt(c["align"], c.get("fmt"), zebra, c.get("mono"), tone)
            if c.get("fmt") in ("inr", "int", "pct", "days") and isinstance(v, (int, float)):
                ws.write_number(rownum, ci, float(v), cf)
            else:
                ws.write(rownum, ci, "—" if v is None else str(v), cf)
    last = first + max(0, len(rows) - 1)

    # ── totals row ──
    if rows:
        trow = last + 1
        f_tot = wb.add_format({"bold": True, "font_size": 10, "font_color": deep, "bg_color": soft,
                               "top": 2, "top_color": deep, "border": 1, "border_color": "#e7ddca", "align": "left"})
        f_tot_num = wb.add_format({"bold": True, "font_size": 10, "font_color": deep, "bg_color": soft,
                                   "top": 2, "top_color": deep, "border": 1, "border_color": "#e7ddca",
                                   "align": "right", "num_format": _FMT_CODE["inr"]})
        f_tot_int = wb.add_format({"bold": True, "font_size": 10, "font_color": deep, "bg_color": soft,
                                   "top": 2, "top_color": deep, "border": 1, "border_color": "#e7ddca",
                                   "align": "center", "num_format": _FMT_CODE["int"]})
        f_tot_blank = wb.add_format({"bg_color": soft, "top": 2, "top_color": deep, "border": 1, "border_color": "#e7ddca"})
        for ci, c in enumerate(cols):
            if ci == 0:
                ws.write(trow, ci, "TOTAL", f_tot)
            elif c.get("fmt") in ("inr", "int") and not c.get("status"):
                L = xl_col_to_name(ci)
                ws.write_formula(trow, ci, f"=SUM({L}{first+1}:{L}{last+1})",
                                 f_tot_num if c["fmt"] == "inr" else f_tot_int)
            else:
                ws.write_blank(trow, ci, None, f_tot_blank)

    # freeze + autofilter + conditional formats
    ws.freeze_panes(first, 0)
    if rows:
        ws.autofilter(hdr_row, 0, last, ncol - 1)
        for ck, spec in _COND.get(report_key, {}).items():
            ci = next((i for i, c in enumerate(cols) if c["key"] == ck), None)
            if ci is None:
                continue
            rng = (first, ci, last, ci)
            if spec[0] == "3color":
                ws.conditional_format(*rng, {"type": "3_color_scale", "min_color": spec[1],
                                             "mid_color": spec[2], "max_color": spec[3]})
            elif spec[0] == "databar":
                ws.conditional_format(*rng, {"type": "data_bar", "bar_color": spec[1]})
        for ci, c in enumerate(cols):
            if c.get("bar"):
                ws.conditional_format(first, ci, last, ci, {"type": "data_bar", "bar_color": accent})

    # ════ Insights sheet (chart reads VISIBLE data → always renders) ════
    spec = _CHART.get(report_key)
    if spec and rows:
        ins = wb.add_worksheet("Insights")
        ins.hide_gridlines(2)
        try:
            ins.set_tab_color(deep)
        except Exception:
            pass
        ins.set_column(0, 0, 30)
        ins.set_column(1, 1 + len(spec["series"]), 16)
        ins.merge_range(0, 0, 0, 1 + len(spec["series"]), "", f_rail); ins.set_row(0, 6)
        ins.merge_range(1, 0, 1, 1 + len(spec["series"]), f"Insights  ·  {meta['name']}", f_title); ins.set_row(1, 32)
        ins.merge_range(2, 0, 2, 1 + len(spec["series"]), "Top performers across the selected scope", f_sub)

        srt = spec["series"][0][1]
        try:
            top = sorted(rows, key=lambda r: float(r.get(srt) or 0), reverse=True)[:12]
        except Exception:
            top = rows[:12]
        h0 = 4
        f_axh = wb.add_format({"bold": True, "font_color": "#fff", "bg_color": accent, "border": 1,
                               "border_color": deep, "align": "center", "valign": "vcenter"})
        f_axc = wb.add_format({"font_size": 10, "border": 1, "border_color": "#e7ddca", "align": "left"})
        f_axn = wb.add_format({"font_size": 10, "border": 1, "border_color": "#e7ddca", "align": "right",
                               "num_format": _FMT_CODE["inr"]})
        ins.write(h0, 0, spec["cat"].title(), f_axh)
        for si, (sname, _sk) in enumerate(spec["series"]):
            ins.write(h0, 1 + si, sname, f_axh)
        for ri, r in enumerate(top):
            ins.write(h0 + 1 + ri, 0, str(r.get(spec["cat"], "—"))[:30], f_axc)
            for si, (_n, sk) in enumerate(spec["series"]):
                ins.write_number(h0 + 1 + ri, 1 + si, float(r.get(sk) or 0), f_axn)
        d_first, d_last = h0 + 1, h0 + len(top)

        chart = wb.add_chart({"type": spec["type"]})
        palette = [accent, deep, "#f59e0b", "#16a34a"]
        for si, (sname, _sk) in enumerate(spec["series"]):
            chart.add_series({
                "name": sname,
                "categories": ["Insights", d_first, 0, d_last, 0],
                "values": ["Insights", d_first, 1 + si, d_last, 1 + si],
                "fill": {"color": palette[si % len(palette)]},
                "border": {"none": True},
                "gap": 80,
            })
        chart.set_title({"name": f"{meta['name']} — Top {len(top)}"})
        chart.set_legend({"position": "bottom" if len(spec["series"]) > 1 else "none"})
        chart.set_x_axis({"num_font": {"rotation": -30}})
        chart.set_size({"width": 760, "height": 380})
        chart.set_style(10)
        ins.insert_chart(h0, 2 + len(spec["series"]), chart, {"x_offset": 12, "y_offset": 4})

    wb.close()
    buf.seek(0)
    return buf.read()
