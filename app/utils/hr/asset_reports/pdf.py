"""HR Asset Reports — WeasyPrint PDF ("Asset Hangar" mission-control identity).

PAGE 1 is a full-bleed **dark cover**: a blueprint-grid hangar field with an
accent glow + a giant ghosted issue numeral, an oversized display title, a
headline KPI row, and — floating on the dark — a bright **instrument panel**
carrying that report's signature visual (one of sixteen, so no two covers and no
other HR module's reports look alike):

    observatory · register · spectrum · field · map · signoff · servicebay ·
    incident · relay · valuation · strata · constellation · horizon · hygiene ·
    reconcile · foundry

PAGE 2+ carries the detail ledger: an accent flag over a modern bordered table.

WeasyPrint shells out to libgobject/libpango at IMPORT time, so the import is
deferred into render_pdf and ``ensure_gtk_runtime()`` is called first (CLAUDE.md).
CSS constraints respected: NO color-mix() (8-digit hex alpha like ``{a}22``),
flat selectors, every <svg> carries width/height + viewBox, transform-origin set
before any rotate().
"""
from __future__ import annotations

import html
import math
from datetime import date, datetime

from .data import report_meta, REPORT_KEYS

COMPANY = {"name": "Fourreck", "legal": "Fourreck Technologies", "web": "crm.fourreck.com"}
_ISSUE_NO = {k: f"{i + 1:02d}" for i, k in enumerate(REPORT_KEYS)}
_TONE = {"ok": "#34d399", "warn": "#fbbf24", "danger": "#f87171"}
_TONE_DARKTEXT = {"ok": "#047857", "warn": "#c2410c", "danger": "#b91c1c"}


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _fmt(val, kind=None):
    if val is None or val == "":
        return "—"
    if kind == "pct":
        try:
            return f"{round(float(val), 1):g}%"
        except (TypeError, ValueError):
            return _esc(val)
    if kind == "money":
        try:
            return f"₹{float(val):,.0f}"
        except (TypeError, ValueError):
            return _esc(val)
    if kind == "int":
        try:
            return f"{int(round(float(val))):,}"
        except (TypeError, ValueError):
            return _esc(val)
    if isinstance(val, bool):
        return "✓" if val else "—"
    if isinstance(val, date) and not isinstance(val, datetime):
        return val.strftime("%d %b %Y")
    return _esc(val)


def _money_compact(v):
    v = _num(v)
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:.2f} L"
    if abs(v) >= 1e3:
        return f"₹{v / 1e3:.1f} k"
    return f"₹{v:,.0f}"


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ══════════════════════════════ reusable instruments (light panel) ══════════════════════════════
def _readout(items, accent, deep):
    cells = []
    for it in items:
        label, value = it[0], it[1]
        vc = _TONE_DARKTEXT.get(it[2] if len(it) > 2 else "", deep)
        cells.append(f'<div class="ro-cell"><div class="ro-v" style="color:{vc}">{_esc(value)}</div>'
                     f'<div class="ro-l">{_esc(label)}</div></div>')
    return (f'<div class="readout" style="border-color:{accent}">'
            f'<span class="ro-edge" style="background:{accent}"></span>{"".join(cells)}</div>')


def _bars(items, a, d, money=False):
    items = [(k, _num(v)) for k, v in items]
    mx = max([abs(v) for _, v in items] + [1])
    out = "".join(
        f'<div class="bar"><span class="bar-k">{_esc(k)}</span>'
        f'<span class="bar-t"><i style="width:{min(100, abs(v) / mx * 100):.0f}%;background:linear-gradient(90deg,{a},{d})"></i></span>'
        f'<b style="color:{d}">{_money_compact(v) if money else _fmt(v, "int")}</b></div>' for k, v in items)
    return f'<div class="bars">{out}</div>' if out else '<div class="db-empty">No data for this period.</div>'


def _gauge(pct, a, d, big, cap):
    pct = max(0, min(100, _num(pct)))
    ang = math.pi - (pct / 100) * math.pi
    x = 60 + 50 * math.cos(ang)
    y = 60 - 50 * math.sin(ang)
    return f"""<svg viewBox="0 0 120 78" width="210" height="137">
      <path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="{a}2e" stroke-width="12" stroke-linecap="round"/>
      <path d="M10 60 A50 50 0 0 1 {x:.1f} {y:.1f}" fill="none" stroke="{a}" stroke-width="12" stroke-linecap="round"/>
      <text x="60" y="50" text-anchor="middle" font-size="22" font-weight="900" fill="{d}">{_esc(big)}</text>
      <text x="60" y="62" text-anchor="middle" font-size="6" letter-spacing="1.5" fill="{a}">{_esc(cap)}</text>
    </svg>"""


def _donut(parts, a, d, center_label):
    parts = [(k, _num(v), col) for k, v, col in parts]
    total = sum(v for _, v, _ in parts) or 1
    C = 2 * math.pi * 16
    offset = 0.0
    segs = []
    for _, v, col in parts:
        length = (v / total) * C
        segs.append(f'<circle cx="30" cy="30" r="16" fill="none" stroke="{col}" stroke-width="12" '
                    f'stroke-dasharray="{length:.2f} {C - length:.2f}" stroke-dashoffset="{-offset:.2f}" '
                    f'transform="rotate(-90 30 30)" style="transform-origin:30px 30px"/>')
        offset += length
    legend = "".join(f'<span class="hz-l"><i style="background:{col}"></i>{_esc(k)} · {_esc(_fmt(v, "int"))}</span>'
                     for k, v, col in parts)
    return (f'<div class="donut-wrap"><svg viewBox="0 0 60 60" width="168" height="168">'
            f'<circle cx="30" cy="30" r="16" fill="none" stroke="{a}1f" stroke-width="12"/>'
            f'{"".join(segs)}'
            f'<text x="30" y="32" text-anchor="middle" font-size="8.5" font-weight="900" fill="{d}">{_esc(center_label)}</text>'
            f'</svg><div class="hz-legend">{legend}</div></div>')


def _buckets(items, a, d):
    items = [(k, _num(v)) for k, v in items]
    mx = max([v for _, v in items] + [1])
    cols = "".join(
        f'<div class="bk"><b style="color:{d}">{_esc(_fmt(v, "int"))}</b>'
        f'<span class="bk-col" style="height:{max(4, v / mx * 100):.0f}%;background:linear-gradient(180deg,{a},{d})"></span>'
        f'<span class="bk-l">{_esc(k)}</span></div>' for k, v in items)
    return f'<div class="buckets">{cols}</div>' if cols else '<div class="db-empty">No data.</div>'


def _heat_tiles(items):
    out = "".join(
        f'<div class="htile" style="background:{col}"><div class="htile-v">{_esc(_fmt(v, "int"))}</div>'
        f'<div class="htile-l">{_esc(k)}</div></div>' for k, v, col in items)
    return f'<div class="htiles">{out}</div>'


def _spectrum(parts):
    parts = [(k, _num(v), col) for k, v, col in parts if _num(v) > 0]
    total = sum(v for _, v, _ in parts) or 1
    segs = "".join(
        f'<span class="sp-seg" style="width:{v / total * 100:.1f}%;background:{col}">'
        f'{_esc(_fmt(v, "int")) if v / total > 0.08 else ""}</span>' for _, v, col in parts)
    legend = "".join(f'<span class="hz-l"><i style="background:{col}"></i>{_esc(k)} · {_esc(_fmt(v, "int"))}</span>'
                     for k, v, col in parts)
    bar = f'<div class="spectrum">{segs}</div>' if segs else '<div class="db-empty">No data.</div>'
    return f'{bar}<div class="hz-legend">{legend}</div>'


def _stars(rating, a):
    full = int(round(_num(rating)))
    return "".join(f'<span style="color:{a if i < full else "#e0d4ba"}">★</span>' for i in range(5))


def _depcurve(cost, book, a, d):
    cost = max(_num(cost), 1)
    end_y = 40 - (max(_num(book), 0) / cost) * 32
    return (f'<svg viewBox="0 0 130 46" width="240" height="85">'
            f'<polyline points="5,8 125,{end_y:.1f}" fill="none" stroke="{a}55" stroke-width="1" stroke-dasharray="2 2"/>'
            f'<path d="M5 8 C 45 {8 + (end_y - 8) * 0.5:.1f}, 88 {end_y - 2:.1f}, 125 {end_y:.1f}" '
            f'fill="none" stroke="{a}" stroke-width="2.6" stroke-linecap="round"/>'
            f'<circle cx="5" cy="8" r="2.8" fill="{d}"/><circle cx="125" cy="{end_y:.1f}" r="2.8" fill="{d}"/>'
            f'</svg>')


_SEG_COL = {
    "AVAILABLE": "#34d399", "ALLOCATED": "#fbbf24", "RESERVED": "#fb923c",
    "MAINTENANCE": "#9aa1ab", "RETIRED": "#6b7280",
    "NEW": "#34d399", "GOOD": "#a3e635", "FAIR": "#fbbf24", "POOR": "#fb923c", "RETIRED_C": "#6b7280",
    "MINOR": "#fcd34d", "MODERATE": "#fb923c", "MAJOR": "#f87171", "TOTAL_LOSS": "#b91c1c",
}


def _seg_color(name, fallback):
    return _SEG_COL.get(str(name).upper(), fallback)


# ══════════════════════════════ COVER (full-bleed dark hero) ══════════════════════════════
def _title_html(name, a):
    words = _esc(name).split(" ")
    if len(words) == 1:
        return f'<span style="color:{a}">{words[0]}</span>'
    return " ".join(words[:-1]) + f' <span style="color:{a}">{words[-1]}</span>'


def _period_text(period):
    f, t = period.get("from"), period.get("to")
    if not f or not t:
        return "ALL-TIME SNAPSHOT"
    fd = datetime.fromisoformat(f).date() if isinstance(f, str) else f
    td = datetime.fromisoformat(t).date() if isinstance(t, str) else t
    return f'{fd.strftime("%d %b %Y")} &nbsp;→&nbsp; {td.strftime("%d %b %Y")} · {(td - fd).days + 1} DAYS'


def _hk(kpis):
    out = []
    for it in kpis:
        value, label = str(it[0]), it[1]
        col = _TONE.get(it[2] if len(it) > 2 else "", "#ffffff")
        # long (text) values get a smaller, truncated treatment so 4 KPIs stay on one row
        wide = len(value) > 9
        if len(value) > 22:
            value = value[:21] + "…"
        cls = "hk hk-wide" if wide else "hk"
        out.append(f'<div class="{cls}"><div class="hk-v" style="color:{col}">{_esc(value)}</div>'
                   f'<div class="hk-l">{_esc(label)}</div></div>')
    return f'<div class="cv-kpis">{"".join(out)}</div>'


def _hero(meta, report, kpis, panel_title, body, caption=""):
    a, d = meta["accent"], meta["accent_deep"]
    issue = _ISSUE_NO.get(meta["key"], "00")
    glow = (f"background:radial-gradient(130% 105% at 86% 4%, {a}3a 0%, #00000000 50%),"
            f"radial-gradient(95% 95% at -8% 108%, {d}40 0%, #00000000 55%);")
    cap = f'<div class="cv-cap">{caption}</div>' if caption else ""
    return f"""<section class="cover">
      <span class="cv-grid"></span>
      <span class="cv-bg" style="{glow}"></span>
      <span class="cv-num" style="color:{a}1c">{issue}</span>
      <div class="cv-top">
        <div class="cv-brand"><span class="cv-crest" style="background:{a}">{_esc(meta['icon'])}</span>
          {COMPANY['name'].upper()} · ASSET HANGAR</div>
        <div class="cv-meta">REPORT // {issue} &nbsp;·&nbsp; {datetime.now().strftime('%d %b %Y').upper()}</div>
      </div>
      <div class="cv-main">
        <div class="cv-left">
          <span class="cv-eyebrow" style="background:{a}29;color:{a}">{_esc(report.get('eyebrow', meta['name']))}</span>
          <h1 class="cv-title">{_title_html(meta['name'], a)}</h1>
          <p class="cv-sub">{_esc(report.get('subtitle', meta['tagline']))}</p>
          <span class="cv-period">{_period_text(report['period'])}</span>
          {_hk(kpis)}
        </div>
        <div class="cv-right">
          <div class="cv-panel" style="border-top:3mm solid {a}">
            <div class="cv-panel-h" style="color:{d};border-color:{a}">{_esc(panel_title)}</div>
            {body}{cap}
          </div>
        </div>
      </div>
      <div class="cv-foot"><span>{COMPANY['legal']} · {COMPANY['web']}</span>
        <span>CONFIDENTIAL — INTERNAL USE ONLY</span>
        <span>GENERATED {datetime.now().strftime('%d %b %Y · %I:%M %p').upper().lstrip('0')}</span></div>
    </section>"""


# ── per-report covers ──
def _cover_observatory(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    donut = [(r["segment"], r["count"], _seg_color(r["segment"], a)) for r in report["rows"] if r["count"]]
    return _hero(meta, report,
        [(_fmt(s["assets"], "int"), "Assets"), (_money_compact(s["value"]), "Est. value"),
         (_fmt(s["allocated"], "int"), "Allocated", "ok"), (_fmt(s["overdue"], "int"), "Overdue", "danger")],
        "Estate by status", _donut(donut, a, d, _fmt(s["assets"], "int")),
        f'Net book value {_money_compact(s["book_value"])} · {s["open_maintenance"]} in service · {s["lapsed_warranty"]} warranties lapsed')


def _cover_register(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    bc = s.get("by_condition", {})
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Assets"), (_money_compact(s["total_value"]), "Total value"),
         (_fmt(s["allocated"], "int"), "Allocated", "ok"), (_fmt(s["available"], "int"), "Available")],
        "Status distribution",
        _bars([(k, v) for k, v in s.get("by_status", {}).items()], a, d)
        + f'<div class="cv-sub-h">Condition ramp</div>{_spectrum([(k.title(), v, _seg_color(k + "_C" if k == "RETIRED" else k, a)) for k, v in bc.items()])}')


def _cover_spectrum(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    palette = ["#fbbf24", "#f59e0b", "#fb923c", "#d97706", "#b45309", "#92400e", "#fcd34d", "#9aa1ab"]
    rows = report["rows"][:8]
    parts = [(r["category"], r["asset_count"], palette[i % len(palette)]) for i, r in enumerate(rows)]
    return _hero(meta, report,
        [(_fmt(s["categories"], "int"), "Categories"), (_fmt(s["total_assets"], "int"), "Assets"),
         (_money_compact(s["total_value"]), "Value"), (_esc(s["top_category"]), "Largest", "ok")],
        "Classification spectrum",
        _spectrum(parts) + f'<div class="cv-sub-h">Assets per category</div>{_bars([(r["category"], r["asset_count"]) for r in rows], a, d)}')


def _cover_field(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    pct = (s["overdue"] / s["total"] * 100) if s["total"] else 0
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "In field"), (_fmt(s["acknowledged"], "int"), "Acknowledged", "ok"),
         (_fmt(s["unacknowledged"], "int"), "Unack'd", "warn"), (_fmt(s["avg_days_out"], "int"), "Avg days out")],
        "Overdue gauge",
        f'<div class="cv-center">{_gauge(pct, a, d, _fmt(s["overdue"], "int"), "OVERDUE")}</div>',
        f'{s["overdue"]} of {s["total"]} field assets are past their expected return date')


def _cover_map(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    rows = report["rows"][:7]
    bars = "".join(
        f'<div class="bar"><span class="bar-k">{_esc(r["department"])}</span>'
        f'<span class="bar-t"><i style="width:{(r["allocated"] / max(r["asset_count"], 1) * 100):.0f}%;'
        f'background:linear-gradient(90deg,{a},{d})"></i></span>'
        f'<b style="color:{d}">{r["allocated"]}/{r["asset_count"]}</b></div>' for r in rows) \
        or '<div class="db-empty">No departments.</div>'
    return _hero(meta, report,
        [(_fmt(s["departments"], "int"), "Departments"), (_fmt(s["total_assets"], "int"), "Assets"),
         (_money_compact(s["total_value"]), "Value"), (_esc(s["top_department"]), "Top", "ok")],
        "Field deployment by department", f'<div class="bars">{bars}</div>',
        "Bars show allocated against the department's total holdings")


def _cover_signoff(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    rows = report["rows"]
    b0 = sum(1 for r in rows if r["days_pending"] <= 2)
    b1 = sum(1 for r in rows if 2 < r["days_pending"] <= 7)
    b2 = sum(1 for r in rows if 7 < r["days_pending"] <= 14)
    b3 = sum(1 for r in rows if r["days_pending"] > 14)
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Pending", "warn"), (_fmt(s["avg_days_pending"], "int"), "Avg days"),
         (_fmt(s["max_days_pending"], "int"), "Max days", "danger"), (_fmt(s["over_7d"], "int"), "Over 7d", "danger")],
        "Pending-age distribution",
        _buckets([("0-2d", b0), ("3-7d", b1), ("8-14d", b2), ("15d+", b3)], a, d),
        f'{s["over_7d"]} sign-offs are older than a week and need chasing')


def _cover_servicebay(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Jobs"), (_fmt(s["completed"], "int"), "Completed", "ok"),
         (_fmt(s["open"], "int"), "Open", "warn"), (_money_compact(s["total_cost"]), "Cost")],
        "Jobs by status",
        _bars([(k.replace("_", " ").title(), v) for k, v in s.get("by_status", {}).items()], a, d)
        + f'<div class="cv-sub-h">Jobs by type</div>{_bars([(k.title(), v) for k, v in s.get("by_type", {}).items()], a, d)}')


def _cover_incident(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    sev = s.get("by_severity", {})
    heat = [(k.replace("_", " ").title(), sev.get(k, 0), _seg_color(k, a)) for k in ("MINOR", "MODERATE", "MAJOR", "TOTAL_LOSS")]
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Incidents"), (_fmt(s["open"], "int"), "Open", "danger"),
         (_fmt(s["resolved"], "int"), "Resolved", "ok"), (_money_compact(s["recovery"]), "Recovered", "ok")],
        "Severity heat-map", _heat_tiles(heat),
        f'{s["writeoff"]} written off · {_money_compact(s["recovery"])} recovered from liable parties')


def _cover_relay(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Movements"), (_fmt(s["completed"], "int"), "Completed", "ok")],
        "Movements by type",
        _bars([(k.replace("_", " ").title(), v) for k, v in s.get("by_type", {}).items()], a, d)
        + f'<div class="cv-sub-h">By status</div>{_bars([(k.title(), v) for k, v in s.get("by_status", {}).items()], a, d)}')


def _cover_valuation(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _hero(meta, report,
        [(_fmt(s["count"], "int"), "Assets"), (_money_compact(s["total_cost"]), "Cost"),
         (_money_compact(s["total_book"]), "Book value", "ok"), (_money_compact(s["depreciation"]), "Depreciation", "warn")],
        "Cost vs. net book value",
        _bars([("Original cost", s["total_cost"]), ("Net book value", s["total_book"]), ("Depreciation", s["depreciation"])], a, d, money=True)
        + f'<div class="cv-center">{_depcurve(s["total_cost"], s["total_book"], a, d)}</div>',
        f'{_money_compact(s["depreciation"])} carried off the books · avg age {s["avg_age"]} months')


def _cover_strata(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Dated assets"), (_fmt(s["avg_age_months"], "int"), "Avg age (mo)"),
         (_fmt(s["refresh_candidates"], "int"), "Refresh due", "danger")],
        "Age strata (months in service)",
        _buckets([("0-12", s["b_0_12"]), ("12-24", s["b_12_24"]), ("24-36", s["b_24_36"]),
                  ("36-48", s["b_36_48"]), ("48+", s["b_48_plus"])], a, d),
        f'{s["refresh_candidates"]} assets are 36 months or older — flagged for refresh')


def _cover_constellation(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    rows = report["rows"][:6]
    return _hero(meta, report,
        [(_fmt(s["vendors"], "int"), "Vendors"), (_money_compact(s["total_spend"]), "Procurement"),
         (_esc(s["top_vendor"]), "Top vendor", "ok"), (f'{_num(s["avg_rating"]):g}★', "Avg rating")],
        "Spend concentration",
        _bars([(r["vendor"], r["total_spend"]) for r in rows], a, d, money=True)
        + f'<div class="cv-center cv-stars">{_stars(s["avg_rating"], a)}</div>',
        "Procurement value sourced per vendor across the panel")


def _cover_horizon(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    bands = [("Lapsed", s["lapsed"], "#b91c1c"), ("≤30d", s["soon"], "#ea580c"),
             ("31-90d", s["quarter"], "#d97706"), (">90d", s["safe"], "#047857")]
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Covered"), (_fmt(s["lapsed"], "int"), "Lapsed", "danger"),
         (_fmt(s["soon"], "int"), "≤30 days", "warn"), (_fmt(s["safe"], "int"), "Safe", "ok")],
        "Warranty horizon",
        _spectrum([(k, v, col) for k, v, col in bands]),
        f'{s["soon"]} warranties lapse within 30 days · {s["quarter"]} within the quarter')


def _cover_hygiene(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    issues = s.get("by_issue", {})
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Records flagged", "danger")],
        "Data-quality issues by type",
        _bars([(k.title(), v) for k, v in sorted(issues.items(), key=lambda kv: kv[1], reverse=True)], a, d),
        "Each flagged asset is missing a key field or running past warranty")


def _cover_reconcile(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    parts = [("Found", s["found"], "#047857"), ("Missing", s["missing"], "#b91c1c"), ("Mismatch", s["mismatched"], "#ea580c")]
    return _hero(meta, report,
        [(_fmt(s["expected"], "int"), "Expected"), (_fmt(s["found"], "int"), "Found", "ok"),
         (_fmt(s["missing"], "int"), "Missing", "danger"), (f'{_num(s["accuracy"]):g}%', "Accuracy")],
        "Reconciliation outcome", _donut(parts, a, d, f'{_num(s["accuracy"]):g}%'),
        f'{s["found"]} of {s["expected"]} expected assets verified on the floor across {s["total"]} audits')


def _cover_foundry(meta, report):
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    rec = _num(s["recovery_pct"])
    return _hero(meta, report,
        [(_fmt(s["total"], "int"), "Disposals"), (_money_compact(s["total_sale"]), "Recovered", "ok"),
         (_money_compact(s["total_book"]), "Book value"), (f'{rec:g}%', "Recovery")],
        "Disposals by method",
        _bars([(k.replace("_", " ").title(), v) for k, v in s.get("by_method", {}).items()], a, d)
        + f'<div class="cv-center">{_gauge(min(100, rec), a, d, f"{rec:g}%", "VALUE RECOVERED")}</div>',
        f'{_money_compact(s["total_sale"])} recovered against {_money_compact(s["total_book"])} book value')


COVER_RENDERERS = {
    "observatory": _cover_observatory, "register": _cover_register, "spectrum": _cover_spectrum,
    "field": _cover_field, "map": _cover_map, "signoff": _cover_signoff,
    "servicebay": _cover_servicebay, "incident": _cover_incident, "relay": _cover_relay,
    "valuation": _cover_valuation, "strata": _cover_strata, "constellation": _cover_constellation,
    "horizon": _cover_horizon, "hygiene": _cover_hygiene, "reconcile": _cover_reconcile,
    "foundry": _cover_foundry,
}


# ══════════════════════════════ BODY (detail ledger) ══════════════════════════════
def _table(report):
    cols = report["columns"]
    rows = report["rows"]
    head = "".join(
        f'<th class="{"r" if c.get("align") == "right" else ""}{" lead" if i == 0 else ""}">{_esc(c["label"])}</th>'
        for i, c in enumerate(cols))
    body = []
    for i, row in enumerate(rows):
        cells = []
        for j, c in enumerate(cols):
            val = _fmt(row.get(c["key"]), c.get("fmt"))
            al = "r" if c.get("align") == "right" else ""
            cls = " ".join(x for x in (al, ("lead" if j == 0 else "")) if x)
            cells.append(f'<td class="{cls}">{val}</td>')
        body.append(f'<tr class="{"zebra" if i % 2 else ""}">{"".join(cells)}</tr>')
    if not rows:
        body.append(f'<tr><td colspan="{len(cols)}" class="empty">No records for this period.</td></tr>')
    return f'<table class="dt"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def _body(meta, report):
    a, d = meta["accent"], meta["accent_deep"]
    return f"""<section class="bodypage">
      <div class="flag" style="border-color:{a}">
        <span class="flag-mark" style="background:linear-gradient(135deg,{a},{d})">{_esc(meta['icon'])}</span>
        <div class="flag-txt"><div class="flag-name" style="color:{d}">{_esc(meta['name'])}</div>
          <div class="flag-tag">DETAIL LEDGER · {len(report['rows'])} RECORDS · {_esc(report['period']['label'])}</div></div>
        <div class="flag-issue" style="color:{a}">// {_ISSUE_NO.get(meta['key'], '00')}</div></div>
      {_table(report)}</section>"""


# ══════════════════════════════ CSS ══════════════════════════════
def _css(a, d, s):
    css = """
@page { size: A4 landscape; margin: 12mm 13mm 13mm;
  @bottom-left { content: "%LEGAL% · %WEB%"; font-size: 7pt; color: #9a8a6a; }
  @bottom-center { content: "Asset Hangar · confidential"; font-size: 7pt; color: #c4b59a; }
  @bottom-right { content: "Page " counter(page) " / " counter(pages); font-size: 7pt; color: #9a8a6a; } }
@page :first { margin: 0; @bottom-left { content: ""; } @bottom-center { content: ""; } @bottom-right { content: ""; } }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #1a1410; margin: 0; }

/* ─── COVER ─── */
.cover { position: relative; width: 297mm; height: 210mm; overflow: hidden; page-break-after: always;
  color: #fff; padding: 12mm 15mm 9mm; background: #0b0907; display: flex; flex-direction: column; }
.cv-grid { position: absolute; inset: 0; z-index: 0;
  background-image: linear-gradient(#ffffff0a 1px, transparent 1px), linear-gradient(90deg, #ffffff0a 1px, transparent 1px);
  background-size: 13mm 13mm; }
.cv-bg { position: absolute; inset: 0; z-index: 0; }
.cv-num { position: absolute; right: 4mm; bottom: -34mm; z-index: 0; font-size: 250pt; font-weight: 900; line-height: 1; letter-spacing: -10pt; }
.cv-top { position: relative; z-index: 2; display: flex; align-items: center; justify-content: space-between; }
.cv-brand { display: flex; align-items: center; gap: 3mm; font-size: 8pt; font-weight: 800; letter-spacing: 2.4pt; color: #ffffffe6; }
.cv-crest { width: 9mm; height: 9mm; border-radius: 2.2mm; display: flex; align-items: center; justify-content: center; font-size: 12pt; font-weight: 900; color: #0b0907; }
.cv-meta { font-size: 7.2pt; font-weight: 800; letter-spacing: 2pt; color: #ffffff8c; }
.cv-main { position: relative; z-index: 2; flex: 1; display: flex; gap: 9mm; align-items: center; }
.cv-left { flex: 0 0 126mm; }
.cv-eyebrow { display: inline-block; font-size: 7.4pt; font-weight: 900; letter-spacing: 2.6pt; text-transform: uppercase; padding: 1.8mm 4mm; border-radius: 5mm; }
.cv-title { font-size: 47pt; font-weight: 900; letter-spacing: -1.6pt; line-height: 0.95; margin: 6mm 0 4mm; color: #fff; }
.cv-sub { font-size: 11pt; line-height: 1.5; color: #ffffffcc; margin: 0 0 7mm; max-width: 112mm; }
.cv-period { display: inline-block; font-size: 7.6pt; font-weight: 800; letter-spacing: 1pt; color: #ffffffd9; padding: 2mm 4.4mm; border-radius: 5mm; border: 0.8pt solid #ffffff33; }
.cv-kpis { display: flex; gap: 5mm; margin-top: 9mm; flex-wrap: nowrap; }
.hk { flex: 1 1 0; min-width: 0; overflow: hidden; }
.hk-v { font-size: 18pt; font-weight: 900; line-height: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hk-wide .hk-v { font-size: 12.5pt; line-height: 1.15; white-space: normal; }
.hk-l { font-size: 6.4pt; font-weight: 800; letter-spacing: 1pt; text-transform: uppercase; color: #ffffff8c; margin-top: 2mm; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cv-right { flex: 1 1 0; }
.cv-panel { background: #fffdf9; color: #1a1410; border-radius: 4.5mm; padding: 7mm 8mm 6.5mm; box-shadow: 0 8mm 22mm #00000073; }
.cv-panel-h { font-size: 8.4pt; font-weight: 900; text-transform: uppercase; letter-spacing: 1.6pt; margin-bottom: 5mm; padding-bottom: 2mm; border-bottom: 1.6pt solid; }
.cv-sub-h { font-size: 7.4pt; font-weight: 900; text-transform: uppercase; letter-spacing: 1.4pt; color: #6b5840; margin: 5mm 0 3.5mm; }
.cv-center { text-align: center; margin-top: 2mm; }
.cv-stars { font-size: 19pt; letter-spacing: 2.5pt; }
.cv-cap { font-size: 8pt; color: #9a8a6a; font-style: italic; margin-top: 4.5mm; text-align: center; line-height: 1.4; }
.cv-foot { position: relative; z-index: 2; display: flex; justify-content: space-between; align-items: center;
  font-size: 6.8pt; font-weight: 700; letter-spacing: 1pt; color: #ffffff73; border-top: 0.8pt solid #ffffff24; padding-top: 3mm; }

/* ─── instruments (on the light panel) ─── */
.readout { position: relative; display: flex; border: 1.4pt solid; border-radius: 2.6mm; overflow: hidden; margin-bottom: 5mm; }
.ro-edge { position: absolute; left: 0; top: 0; bottom: 0; width: 1.6mm; }
.ro-cell { flex: 1 1 0; padding: 3.4mm 2mm; text-align: center; border-left: 0.6pt solid #00000012; }
.ro-cell:first-child { border-left: none; padding-left: 4mm; }
.ro-v { font-size: 15pt; font-weight: 900; line-height: 1; }
.ro-l { font-size: 6pt; font-weight: 800; letter-spacing: 0.7pt; text-transform: uppercase; color: #6b5840; margin-top: 1.6mm; }
.bars { display: flex; flex-direction: column; gap: 2.4mm; }
.bar { display: flex; align-items: center; gap: 3mm; }
.bar-k { flex: 0 0 42mm; font-size: 8pt; font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-t { flex: 1; height: 4.6mm; background: #00000010; border-radius: 2.3mm; overflow: hidden; }
.bar-t i { display: block; height: 100%; border-radius: 2.3mm; }
.bar b { flex: 0 0 24mm; text-align: right; font-weight: 900; font-size: 9pt; }
.db-empty { color: #9a8a6a; font-style: italic; font-size: 9pt; padding: 4mm 0; }
.htiles { display: flex; gap: 3mm; }
.htile { flex: 1 1 0; border-radius: 2.6mm; padding: 7mm 2mm; text-align: center; color: #fff; }
.htile-v { font-size: 23pt; font-weight: 900; line-height: 1; }
.htile-l { font-size: 6.8pt; font-weight: 800; letter-spacing: 0.8pt; text-transform: uppercase; margin-top: 2mm; }
.spectrum { display: flex; height: 11mm; border-radius: 2.4mm; overflow: hidden; background: #00000010; }
.sp-seg { height: 100%; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 7.4pt; font-weight: 900; }
.hz-legend { display: flex; gap: 5mm; margin-top: 3mm; flex-wrap: wrap; }
.hz-l { display: inline-flex; align-items: center; gap: 1.8mm; font-size: 7.6pt; font-weight: 700; color: #6b5840; }
.hz-l i { width: 3mm; height: 3mm; border-radius: 0.8mm; display: inline-block; }
.buckets { display: flex; align-items: flex-end; gap: 4mm; height: 46mm; padding: 0 2mm; }
.bk { flex: 1 1 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; }
.bk b { font-size: 10pt; font-weight: 900; margin-bottom: 1.6mm; }
.bk-col { width: 66%; border-radius: 1.6mm 1.6mm 0 0; min-height: 1.5mm; }
.bk-l { font-size: 7.2pt; font-weight: 800; color: #6b5840; margin-top: 2mm; }
.donut-wrap { display: flex; flex-direction: column; align-items: center; }

/* ─── BODY ─── */
.bodypage { padding: 1mm 0; }
.flag { display: flex; align-items: center; gap: 4mm; margin-bottom: 5mm; padding-bottom: 3mm; border-bottom: 2.6pt solid; }
.flag-mark { width: 12mm; height: 12mm; border-radius: 2.6mm; color: #fff; font-weight: 900; font-size: 16pt; display: flex; align-items: center; justify-content: center; }
.flag-txt { flex: 1; }
.flag-name { font-size: 17pt; font-weight: 900; letter-spacing: -0.4pt; }
.flag-tag { font-size: 7pt; font-weight: 800; letter-spacing: 1.4pt; color: #9a8a6a; margin-top: 0.6mm; }
.flag-issue { font-size: 18pt; font-weight: 900; }
.dt { width: 100%; border-collapse: collapse; font-size: 7.6pt; }
.dt th { background: %ACCENT%; color: #fff; text-align: left; padding: 2.6mm 1.8mm; font-size: 6.8pt; text-transform: uppercase; letter-spacing: 0.6pt; font-weight: 800; border: 0.5pt solid %DEEP%; }
.dt th.r { text-align: right; }
.dt td { padding: 2mm 1.8mm; border: 0.5pt solid #e7dcc6; }
.dt td.lead { font-weight: 800; color: %DEEP%; }
.dt td.r { text-align: right; font-variant-numeric: tabular-nums; }
.dt tr.zebra td { background: %SOFT%; }
.dt tbody tr { page-break-inside: avoid; }
.dt td.empty { text-align: center; color: #9a8a6a; font-style: italic; padding: 12mm; }
"""
    return (css.replace("%ACCENT%", a).replace("%DEEP%", d).replace("%SOFT%", s)
            .replace("%LEGAL%", COMPANY["legal"]).replace("%WEB%", COMPANY["web"]))


def render_pdf(report: dict, report_key: str) -> bytes:
    # Lazy import — WeasyPrint binds GTK at import time (see CLAUDE.md).
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433 — lazy after GTK PATH prep

    meta = report_meta(report_key)
    cover_fn = COVER_RENDERERS.get(meta.get("motif"), _cover_register)
    cover = cover_fn(meta, report)
    body = _body(meta, report)
    css = _css(meta["accent"], meta["accent_deep"], meta["accent_soft"])
    full = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{_esc(meta["name"])} · {COMPANY["name"]}</title><style>{css}</style></head>'
            f'<body>{cover}{body}</body></html>')
    return HTML(string=full).write_pdf()
