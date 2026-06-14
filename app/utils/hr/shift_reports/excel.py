"""HR Shift Reports — Excel (xlsxwriter).

One polished engine driven by per-report specs. Every workbook is a structured,
ultra-modern sheet:

    * an accent **rail + title block** (title · subtitle · period band · source
      lineage) so the export is self-describing;
    * a row of **boxed KPI tiles** — each a real bordered card with a coloured
      top rail, big number and caption (no floating numbers);
    * a fully **gridded body** with a visible warm grid, accent header, zebra
      rows, an accent spine on the lead column, status / boolean colouring and
      a deep-accent TOTAL row;
    * conditional formats (data bars / colour scales) that surface outliers; and
    * an embedded chart on a dedicated 'Charts' sheet.

Public entry: ``render_excel(report_key, rows, summary, meta) -> bytes``.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from .data import report_meta

# ── shared neutral palette (warm, brand-consistent, but VISIBLE) ──────────────
INK = "#1a1410"          # primary text
INK_MUTED = "#6b5840"    # secondary / labels
INK_DIM = "#9a8a6a"      # captions
GRID = "#c4b393"         # body grid — strong enough to read on white & cream
GRID_HEAD = "#8a7350"    # header / tile rules
CREAM = "#fdf8ee"        # zebra fill
BAND = "#f6f0e1"         # period band background
WHITE = "#ffffff"

COMPANY = {"name": "Fourreck", "legal": "Fourreck Technologies", "web": "crm.fourreck.com"}

# Where each report draws from — printed under the title so the export is auditable.
LINEAGE = {
    "roster": "SOURCE  ·  Shifts › Assignment & Management",
    "coverage": "SOURCE  ·  Shifts › Coverage",
    "overtime": "SOURCE  ·  Shifts › Overtime Rules  ×  Attendance OT requests",
    "night": "SOURCE  ·  Shifts › Night Shifts",
    "rotation": "SOURCE  ·  Shifts › Rotation",
    "workforce": "SOURCE  ·  Shifts › Workforce Demand",
}

# (label, key, kind) — kind ∈ text|int|hours|pct|money|mult|bool|status
COLS = {
    "roster": [
        ("Code", "employee_code", "text"), ("Employee", "employee_name", "text"),
        ("Department", "department", "text"), ("Shift", "shift_name", "text"),
        ("Type", "shift_type", "text"), ("Window", "window", "text"),
        ("From", "effective_from", "text"), ("Until", "effective_until", "text"),
    ],
    "coverage": [
        ("Shift", "shift_name", "text"), ("Department", "department", "text"), ("Post", "label", "text"),
        ("Required", "min_staff", "int"), ("Assigned", "assigned", "int"),
        ("Shortfall", "shortfall", "int"), ("Coverage", "coverage_pct", "pct"), ("Status", "status", "status"),
    ],
    "overtime": [
        ("Code", "employee_code", "text"), ("Employee", "employee_name", "text"),
        ("Department", "department", "text"), ("Events", "occurrences", "int"),
        ("OT hrs", "ot_hours", "hours"), ("Payable", "payable_hours", "hours"),
        ("Peak", "peak_mult", "mult"), ("Weighted", "weighted_hours", "hours"), ("Est. cost", "est_cost", "money"),
    ],
    "night": [
        ("Code", "employee_code", "text"), ("Employee", "employee_name", "text"),
        ("Department", "department", "text"), ("Shift", "shift_name", "text"), ("Window", "window", "text"),
        ("Allowance", "allowance", "money"), ("OT rate", "ot_rate", "mult"),
        ("Transport", "transport", "bool"), ("Meal", "meal", "bool"),
    ],
    "rotation": [
        ("Rotation", "name", "text"), ("Cycle", "cycle", "text"), ("Every (d)", "frequency_days", "int"),
        ("Steps", "steps", "int"), ("Crew", "members", "int"), ("Pattern", "step_shifts", "text"),
        ("Current", "current_label", "text"), ("Last advanced", "last_advanced", "text"),
    ],
    "workforce": [
        ("Shift", "shift_name", "text"), ("Department", "department", "text"), ("Skill", "skill", "text"),
        ("Required", "required", "int"), ("Assigned", "assigned", "int"), ("Shortfall", "shortfall", "int"),
        ("Coverage", "coverage_pct", "pct"), ("Status", "status", "status"),
    ],
}

_WIDE = {"employee_name", "name", "shift_name", "step_shifts", "department", "label", "current_label"}
_NUMKINDS = {"int", "hours", "pct", "money", "mult"}
_NUMFMT = {"int": "#,##0", "hours": '0.0" h"', "pct": '0"%"', "money": '"₹"#,##0', "mult": '0.00"×"'}
_SUMKINDS = {"int", "hours", "money"}  # which numeric columns get a TOTAL

# status text → (fill, font) traffic-light tones
_STATUS_TONE = {
    "OK": ("#ccfbf1", "#115e59"), "COVERED": ("#ccfbf1", "#115e59"),
    "WARN": ("#ffedd5", "#9a3412"), "GAP": ("#ffedd5", "#9a3412"),
    "CRITICAL": ("#fee2e2", "#991b1b"),
}


def _kpis(key, s, accent):
    if key == "roster":
        return [("Assignments", s["rows"], accent), ("Employees", s["employees"], "#0d9488"),
                ("Shifts", s["shifts"], "#475569"), ("Night posts", s["night"], "#ea580c"),
                ("Open-ended", s["open_ended"], "#7c3aed")]
    if key == "coverage":
        return [("Posts", s["rows"], accent), ("Required", s["required"], "#475569"),
                ("Assigned", s["assigned"], "#0d9488"), ("Shortfall", s["total_shortfall"], "#b91c1c"),
                ("Critical", s["critical"], "#7f1d1d")]
    if key == "overtime":
        return [("Employees", s["employees"], accent), ("OT events", s["occurrences"], "#475569"),
                ("OT hours", round(s["ot_hours"], 1), "#ca8a04"), ("Payable", round(s["payable_hours"], 1), "#ea580c"),
                ("Est. cost", f"₹{s['est_cost']:,.0f}", "#b91c1c")]
    if key == "night":
        return [("Night crew", s["employees"], accent), ("Shifts", s["shifts"], "#475569"),
                ("With policy", s["with_policy"], "#0d9488"), ("Transport", s["transport"], "#7c3aed"),
                ("Allowance/night", f"₹{s['allowance_per_night']:,.0f}", "#ea580c")]
    if key == "rotation":
        return [("Rotations", s["rows"], accent), ("Crew", s["members"], "#0d9488"),
                ("Steps", s["total_steps"], "#475569"), ("Live", s["active_now"], "#ca8a04")]
    return [("Posts", s["rows"], accent), ("Required", s["required"], "#475569"),
            ("Assigned", s["assigned"], "#0d9488"), ("Shortfall", s["shortfall"], "#b91c1c"),
            ("Coverage", f"{s['coverage_pct']}%", "#0d9488")]


# ══════════════════════════════ FORMATS ══════════════════════════════
def _formats(wb, accent, deep, soft):
    f = {
        "rail": wb.add_format({"bg_color": accent}),
        "title": wb.add_format({"bold": True, "font_size": 24, "font_color": INK, "valign": "vcenter",
                                "bg_color": WHITE, "indent": 1}),
        "title_pad": wb.add_format({"bg_color": WHITE}),
        "subtitle": wb.add_format({"italic": True, "font_size": 11, "font_color": INK_MUTED, "valign": "vcenter",
                                   "bg_color": WHITE, "indent": 1, "bottom": 1, "bottom_color": GRID}),
        "subtitle_pad": wb.add_format({"bg_color": WHITE, "bottom": 1, "bottom_color": GRID}),
        "period": wb.add_format({"bold": True, "font_size": 10, "font_color": INK, "bg_color": BAND,
                                 "align": "left", "valign": "vcenter", "indent": 1,
                                 "top": 1, "top_color": GRID, "bottom": 2, "bottom_color": deep}),
        "period_pad": wb.add_format({"bg_color": BAND, "top": 1, "top_color": GRID,
                                     "bottom": 2, "bottom_color": deep}),
        "lineage": wb.add_format({"font_size": 8, "bold": True, "font_color": accent, "bg_color": WHITE,
                                  "align": "left", "valign": "vcenter", "indent": 1}),
        "lineage_pad": wb.add_format({"bg_color": WHITE}),
        "header": wb.add_format({"bold": True, "font_size": 9.5, "font_color": WHITE, "bg_color": accent,
                                 "align": "left", "valign": "vcenter", "indent": 1, "text_wrap": True,
                                 "top": 2, "top_color": deep, "bottom": 2, "bottom_color": deep,
                                 "left": 1, "left_color": deep, "right": 1, "right_color": deep}),
        "header_r": wb.add_format({"bold": True, "font_size": 9.5, "font_color": WHITE, "bg_color": accent,
                                   "align": "right", "valign": "vcenter", "indent": 1,
                                   "top": 2, "top_color": deep, "bottom": 2, "bottom_color": deep,
                                   "left": 1, "left_color": deep, "right": 1, "right_color": deep}),
        "header_c": wb.add_format({"bold": True, "font_size": 9.5, "font_color": WHITE, "bg_color": accent,
                                   "align": "center", "valign": "vcenter",
                                   "top": 2, "top_color": deep, "bottom": 2, "bottom_color": deep,
                                   "left": 1, "left_color": deep, "right": 1, "right_color": deep}),
        "tot_l": wb.add_format({"bold": True, "font_size": 9.5, "font_color": WHITE, "bg_color": deep,
                                "align": "left", "valign": "vcenter", "indent": 1, "border": 1, "border_color": deep}),
        "tot_blank": wb.add_format({"bg_color": deep, "border": 1, "border_color": deep}),
    }
    # body cell formats per (kind, zebra) + lead variants
    for kind, nf in {**_NUMFMT, "text": None, "bool": None, "status": None}.items():
        for z in (0, 1):
            base = {"font_size": 9.5, "valign": "vcenter", "border": 1, "border_color": GRID, "indent": 1}
            if z:
                base["bg_color"] = CREAM
            if nf:
                base["num_format"] = nf
                base["align"] = "right"
            f[("b", kind, z)] = wb.add_format(base)
    # lead-column (first col) — accent left spine + slightly bolder
    for z in (0, 1):
        lead = {"font_size": 9.5, "valign": "vcenter", "bold": True, "font_color": INK, "indent": 1,
                "border": 1, "border_color": GRID, "left": 5, "left_color": accent}
        if z:
            lead["bg_color"] = CREAM
        f[("lead", z)] = wb.add_format(lead)
    # totals numeric
    for kind in _SUMKINDS:
        tot = {"bold": True, "font_size": 9.5, "font_color": WHITE, "bg_color": deep, "align": "right",
               "border": 1, "border_color": deep, "num_format": _NUMFMT[kind]}
        f[("t", kind)] = wb.add_format(tot)
    # status tones + bool tones
    for st, (bg, fg) in _STATUS_TONE.items():
        for z in (0, 1):
            f[("st", st, z)] = wb.add_format({"font_size": 8.5, "bold": True, "font_color": fg, "bg_color": bg,
                                              "align": "center", "valign": "vcenter", "border": 1,
                                              "border_color": GRID})
    for z in (0, 1):
        f[("bool", True, z)] = wb.add_format({"font_size": 9.5, "bold": True, "font_color": "#115e59",
                                              "align": "center", "valign": "vcenter", "border": 1,
                                              "border_color": GRID, "bg_color": CREAM if z else "#ecfdf5"})
        f[("bool", False, z)] = wb.add_format({"font_size": 9.5, "font_color": INK_DIM, "align": "center",
                                               "valign": "vcenter", "border": 1, "border_color": GRID,
                                               "bg_color": CREAM if z else WHITE})
    return f


# ══════════════════════════════ TITLE / KPI BLOCKS ══════════════════════════════
def _title_block(wb, ws, f, theme, key, period, last_col):
    ws.set_row(0, 5)
    ws.merge_range(0, 0, 0, last_col, "", f["rail"])

    ws.set_row(1, 36)
    ws.merge_range(1, 0, 1, last_col, "", f["title_pad"])
    ws.write(1, 0, f"  {theme['name']}", f["title"])

    ws.set_row(2, 20)
    ws.merge_range(2, 0, 2, last_col, "", f["subtitle_pad"])
    ws.write(2, 0, f"  {theme.get('subtitle', theme.get('tagline', ''))}", f["subtitle"])

    ws.set_row(3, 22)
    ws.merge_range(3, 0, 3, last_col, "", f["period_pad"])
    ws.write(3, 0,
             f"  PERIOD   {period['from'].strftime('%d %b %Y')}    →    {period['to'].strftime('%d %b %Y')}"
             f"        ·        GENERATED   {datetime.now().strftime('%d %b %Y, %I:%M %p').lstrip('0')}",
             f["period"])

    ws.set_row(4, 17)
    ws.merge_range(4, 0, 4, last_col, "", f["lineage_pad"])
    ws.write(4, 0, f"  {LINEAGE.get(key, '')}", f["lineage"])
    return 6  # next free row (5 is the spacer baked into _kpi_strip)


def _kpi_strip(wb, ws, kpis, start_row, last_col, deep):
    n = len(kpis) or 1
    span = max(1, (last_col + 1) // n)
    leftover = (last_col + 1) - span * n

    ws.set_row(start_row, 6)                 # spacer
    label_row, value_row = start_row + 1, start_row + 2
    ws.set_row(label_row, 18)
    ws.set_row(value_row, 34)

    c0 = 0
    for i, (label, value, color) in enumerate(kpis):
        c1 = c0 + span - 1 + (1 if i < leftover else 0)
        if c1 > last_col or i == n - 1:
            c1 = last_col if i == n - 1 else min(c1, last_col)
        lab = wb.add_format({"font_size": 8, "bold": True, "font_color": INK_MUTED, "align": "center",
                             "valign": "vcenter", "bg_color": WHITE,
                             "top": 5, "top_color": color, "left": 2, "left_color": GRID_HEAD,
                             "right": 2, "right_color": GRID_HEAD})
        val = wb.add_format({"font_size": 19, "bold": True, "font_color": deep, "align": "center",
                             "valign": "vcenter", "bg_color": WHITE, "left": 2, "left_color": GRID_HEAD,
                             "right": 2, "right_color": GRID_HEAD, "bottom": 2, "bottom_color": GRID_HEAD})
        if c1 > c0:
            ws.merge_range(label_row, c0, label_row, c1, label.upper(), lab)
            ws.merge_range(value_row, c0, value_row, c1, "", val)
            if isinstance(value, (int, float)):
                ws.write_number(value_row, c0, value, val)
            else:
                ws.write_string(value_row, c0, str(value), val)
        else:
            ws.write(label_row, c0, label.upper(), lab)
            if isinstance(value, (int, float)):
                ws.write_number(value_row, c0, value, val)
            else:
                ws.write_string(value_row, c0, str(value), val)
        c0 = c1 + 1
    ws.set_row(value_row + 1, 10)            # spacer
    return value_row + 2


# ══════════════════════════════ CONDITIONAL FORMATS ══════════════════════════════
def _col_idx(cols, key):
    for i, (_l, k, _kind) in enumerate(cols):
        if k == key:
            return i
    return None


def _apply_cf(ws, key, cols, hrow, last):
    def rng(k):
        i = _col_idx(cols, k)
        return (hrow + 1, i, last, i) if i is not None else None
    bars = {
        "coverage": [("coverage_pct", "3cs"), ("shortfall", "#b91c1c"), ("assigned", "#0d9488")],
        "overtime": [("payable_hours", "#ea580c"), ("weighted_hours", "#ca8a04"), ("est_cost", "#b91c1c")],
        "night": [("allowance", "#ea580c")],
        "workforce": [("coverage_pct", "3cs"), ("shortfall", "#b91c1c"), ("assigned", "#0d9488")],
        "rotation": [("members", "#ca8a04"), ("steps", "#475569")],
        "roster": [],
    }
    for k, mode in bars.get(key, []):
        r = rng(k)
        if not r:
            continue
        if mode == "3cs":
            ws.conditional_format(*r, {"type": "3_color_scale", "min_color": "#fecaca",
                                       "mid_color": "#fef9c3", "max_color": "#86efac"})
        else:
            ws.conditional_format(*r, {"type": "data_bar", "bar_color": mode, "bar_solid": True})


# ══════════════════════════════ CHART ══════════════════════════════
def _build_chart(wb, ws, key, cols, summary, theme, hrow, last, rows):
    accent, deep = theme["accent"], theme["accent_deep"]
    sheet = ws.name
    cs = wb.add_worksheet("Charts")
    cs.set_tab_color(accent)
    cs.hide_gridlines(2)
    cs.write(0, 1, f"{theme['name']} — visual summary",
             wb.add_format({"bold": True, "font_size": 13, "font_color": deep}))

    def col(k):
        return _col_idx(cols, k)

    title_fmt = {"name_font": {"size": 12, "bold": True, "color": deep}}
    if key in ("coverage", "workforce"):
        cat = col("shift_name")
        req = col("required") if key == "workforce" else col("min_staff")
        asg = col("assigned")
        ch = wb.add_chart({"type": "column"})
        ch.add_series({"name": "Required", "categories": [sheet, hrow + 1, cat, last, cat],
                       "values": [sheet, hrow + 1, req, last, req], "fill": {"color": deep}})
        ch.add_series({"name": "Assigned", "categories": [sheet, hrow + 1, cat, last, cat],
                       "values": [sheet, hrow + 1, asg, last, asg], "fill": {"color": accent}})
        ch.set_title({"name": "Required vs Assigned", **title_fmt})
        ch.set_legend({"position": "bottom"})
        ch.set_size({"width": 840, "height": max(360, 40 + 26 * len(rows))})
        cs.insert_chart("B3", ch)
        return
    if key == "overtime":
        cat, val = col("employee_name"), col("payable_hours")
        ch = wb.add_chart({"type": "bar"})
        ch.add_series({"name": "Payable hours", "categories": [sheet, hrow + 1, cat, last, cat],
                       "values": [sheet, hrow + 1, val, last, val], "fill": {"color": accent},
                       "data_labels": {"value": True, "num_format": "0.0"}})
        ch.set_title({"name": "Payable OT hours by employee", **title_fmt})
        ch.set_legend({"none": True})
        ch.set_size({"width": 840, "height": max(340, 40 + 26 * len(rows))})
        cs.insert_chart("B3", ch)
        return
    if key == "night":
        cat, val = col("employee_name"), col("allowance")
        ch = wb.add_chart({"type": "column"})
        ch.add_series({"name": "Allowance", "categories": [sheet, hrow + 1, cat, last, cat],
                       "values": [sheet, hrow + 1, val, last, val], "fill": {"color": accent}})
        ch.set_title({"name": "Night allowance by crew member", **title_fmt})
        ch.set_legend({"none": True})
        ch.set_size({"width": 840, "height": 380})
        cs.insert_chart("B3", ch)
        return
    if key == "rotation":
        cat, val = col("name"), col("members")
        ch = wb.add_chart({"type": "column"})
        ch.add_series({"name": "Crew", "categories": [sheet, hrow + 1, cat, last, cat],
                       "values": [sheet, hrow + 1, val, last, val], "fill": {"color": accent},
                       "data_labels": {"value": True}})
        ch.set_title({"name": "Crew per rotation", **title_fmt})
        ch.set_legend({"none": True})
        ch.set_size({"width": 780, "height": 360})
        cs.insert_chart("B3", ch)
        return
    if key == "roster":
        by = list((summary.get("by_type") or {}).items())
        if not by:
            return
        hdr = wb.add_format({"bold": True, "font_color": WHITE, "bg_color": accent, "border": 1, "border_color": deep})
        cell = wb.add_format({"border": 1, "border_color": GRID})
        cs.write(2, 0, "Shift type", hdr)
        cs.write(2, 1, "Count", hdr)
        for i, (t, c) in enumerate(by):
            cs.write(i + 3, 0, t, cell)
            cs.write_number(i + 3, 1, c, cell)
        ch = wb.add_chart({"type": "doughnut"})
        ch.add_series({"name": "Assignments by shift type",
                       "categories": ["Charts", 3, 0, 2 + len(by), 0],
                       "values": ["Charts", 3, 1, 2 + len(by), 1],
                       "data_labels": {"percentage": True, "category": True}})
        ch.set_title({"name": "Assignments by shift type", **title_fmt})
        ch.set_size({"width": 540, "height": 400})
        cs.insert_chart("D3", ch)


# ══════════════════════════════ ENTRY ══════════════════════════════
def render_excel(report_key: str, rows: list, summary: dict, meta: dict) -> bytes:
    theme = report_meta(report_key)
    accent, deep, soft = theme["accent"], theme["accent_deep"], theme["accent_soft"]
    period = meta["period"]
    cols = COLS.get(report_key, COLS["roster"])
    last_col = len(cols) - 1

    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet(theme["name"][:31])
    ws.set_tab_color(accent)
    ws.hide_gridlines(2)
    f = _formats(wb, accent, deep, soft)

    for i, (_label, k, kind) in enumerate(cols):
        ws.set_column(i, i, 26 if k in _WIDE else (13 if kind != "text" else 15))

    row = _title_block(wb, ws, f, theme, report_key, period, last_col)
    row = _kpi_strip(wb, ws, _kpis(report_key, summary, accent), row, last_col, deep)

    # ── table header ──
    hrow = row
    ws.set_row(hrow, 30)
    for i, (label, _k, kind) in enumerate(cols):
        hf = f["header_c"] if kind in ("bool", "status") else (f["header_r"] if kind in _NUMKINDS else f["header"])
        ws.write(hrow, i, label, hf)
    ws.freeze_panes(hrow + 1, 2)

    # ── body ──
    for ri, r in enumerate(rows):
        rr = hrow + 1 + ri
        z = ri % 2
        ws.set_row(rr, 21)
        for i, (_label, k, kind) in enumerate(cols):
            v = r.get(k)
            if i == 0:
                ws.write(rr, i, v if v not in (None, "") else "—", f[("lead", z)])
            elif kind in _NUMKINDS:
                ws.write_number(rr, i, float(v or 0), f[("b", kind, z)])
            elif kind == "bool":
                ws.write(rr, i, "Yes" if v else "No", f[("bool", bool(v), z)])
            elif kind == "status":
                st = str(v or "—")
                fmt = f.get(("st", st, z)) or f[("b", "text", z)]
                ws.write(rr, i, st.title(), fmt)
            else:
                ws.write(rr, i, v if v not in (None, "") else "—", f[("b", "text", z)])
    last = hrow + len(rows)

    if rows:
        # totals
        tr = last + 1
        ws.set_row(tr, 23)
        for i, (_label, k, kind) in enumerate(cols):
            if i == 0:
                ws.write(tr, i, "TOTAL", f["tot_l"])
            elif kind in _SUMKINDS:
                cl = xl_col_to_name(i)
                ws.write_formula(tr, i, f"=SUM({cl}{hrow + 2}:{cl}{last + 1})", f[("t", kind)])
            else:
                ws.write_blank(tr, i, None, f["tot_blank"])
        ws.autofilter(hrow, 0, last, last_col)
        _apply_cf(ws, report_key, cols, hrow, last)
        try:
            _build_chart(wb, ws, report_key, cols, summary, theme, hrow, last, rows)
        except Exception:
            pass  # charts are best-effort; never block the export

    wb.close()
    buf.seek(0)
    return buf.read()
