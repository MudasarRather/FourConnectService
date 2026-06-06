"""WeasyPrint PDF assembly for HR Payroll Reports.

Each report has its own cover-page motif (in ``covers/<motif>.py``) while
sharing one base typography stack and a clean, money-aware data-table for the
body pages. This module owns the base CSS, the shared body table, the page
header, and the public ``render_pdf`` entry point; the 13 cover designs live
in the ``covers`` sub-package and are wired in via ``COVER_RENDERERS``.

WeasyPrint is imported lazily inside ``render_pdf`` so the backend boots even
on a Windows box that hasn't run ``vendor/setup_gtk.py`` yet (per CLAUDE.md).
"""
from __future__ import annotations

from datetime import datetime

from .common import (
    COMPANY, esc, fmt_date, fmt_long_date, inr, inr_group, fmt_days, fmt_pct,
    fmt_signed_pct,
)
from .columns import body_columns, WIDE_REPORTS, PILL_COLORS
from .data import report_meta
from .covers import COVER_RENDERERS
from .bodies import render_body, KIT_CSS


# ════════════════════════════════════════════════════════════════════════════
# Cell formatters
# ════════════════════════════════════════════════════════════════════════════

FORMATTERS = {
    "inr": lambda v: inr(v),
    "inr_p": lambda v: inr(v, paise=True),
    "days": fmt_days,
    "pct": fmt_pct,
    "signed_pct": fmt_signed_pct,
    "date": fmt_date,
}


def _cell(row: dict, col: dict) -> tuple[str, str]:
    raw = row.get(col["key"])
    if col.get("status"):
        status = str(raw or "")
        pc = PILL_COLORS.get(status, {"bg": "#f1f5f9", "fg": "#334155"})
        pill = (
            f'<span class="status-pill" style="background:{pc["bg"]};color:{pc["fg"]}">'
            f'{esc(status.replace("_", " "))}</span>'
        )
        return pill, ""

    fmt = col.get("fmt")
    if fmt:
        val = FORMATTERS[fmt](raw)
    elif raw is None or raw == "":
        val = "—"
    else:
        val = esc(raw)

    klass = ""
    num = raw if isinstance(raw, (int, float)) else 0
    if col.get("danger_if") and col["danger_if"](num):
        klass = "cell-danger"
    elif col.get("warn_if") and col["warn_if"](num):
        klass = "cell-warn"
    elif col.get("good_if") and col["good_if"](num):
        klass = "cell-good"
    return val, klass


def _row_class(idx: int) -> str:
    return "zebra" if idx % 2 == 1 else ""


def _table_html(report_key: str, shaped_rows: list[dict]) -> str:
    cols = body_columns(report_key)
    head = "".join(
        f'<th class="{"r" if c["align"]=="right" else "c" if c["align"]=="center" else ""}">{esc(c["label"])}</th>'
        for c in cols
    )
    body = []
    for i, row in enumerate(shaped_rows):
        cells = []
        for c in cols:
            html_val, klass = _cell(row, c)
            align_cls = "r" if c["align"] == "right" else "c" if c["align"] == "center" else ""
            cls = (align_cls + " " + klass).strip()
            cells.append(f'<td class="{cls}">{html_val}</td>')
        body.append(f'<tr class="{_row_class(i)}">{"".join(cells)}</tr>')
    return (
        f'<table class="data-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>'
    )


def _totals_row_html(report_key: str, shaped_rows: list[dict]) -> str:
    """A footer totals row summing money columns (skips ratio / pct columns)."""
    if not shaped_rows:
        return ""
    cols = body_columns(report_key)
    SUM_FMT = {"inr", "inr_p"}
    SUM_DAYS = {"days"}
    cells = []
    first = True
    for c in cols:
        if first:
            cells.append('<td class="tot-label">TOTAL</td>')
            first = False
            continue
        fmt = c.get("fmt")
        align = "r" if c["align"] == "right" else "c" if c["align"] == "center" else ""
        if fmt in SUM_FMT:
            tot = sum(float(r.get(c["key"]) or 0) for r in shaped_rows)
            cells.append(f'<td class="tot-val {align}">{inr(tot)}</td>')
        elif c["key"] in ("headcount", "months_paid"):
            tot = sum(float(r.get(c["key"]) or 0) for r in shaped_rows)
            cells.append(f'<td class="tot-val {align}">{int(tot)}</td>')
        elif fmt in SUM_DAYS:
            tot = sum(float(r.get(c["key"]) or 0) for r in shaped_rows)
            cells.append(f'<td class="tot-val {align}">{fmt_days(tot)}</td>')
        else:
            cells.append(f'<td class="tot-blank {align}"></td>')
    return f'<tr class="tot-row">{"".join(cells)}</tr>'


# ════════════════════════════════════════════════════════════════════════════
# Base CSS
# ════════════════════════════════════════════════════════════════════════════

_BASE_CSS = """
@page {
    size: A4;
    margin: 18mm 16mm 22mm 16mm;
    @bottom-left { content: "{COMPANY_LEGAL} · {COMPANY_WEB}"; font-family:'Helvetica',sans-serif; font-size:7.5pt; color:#8a7c63; }
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font-family:'Helvetica',sans-serif; font-size:7.5pt; color:#8a7c63; }
}
@page :first { margin: 0; @bottom-left { content:""; } @bottom-right { content:""; } }
@page wide {
    size: A4 landscape; margin: 14mm 12mm 18mm 12mm;
    @bottom-left { content: "{COMPANY_LEGAL} · {COMPANY_WEB}"; font-family:'Helvetica',sans-serif; font-size:7.5pt; color:#8a7c63; }
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font-family:'Helvetica',sans-serif; font-size:7.5pt; color:#8a7c63; }
}
.body-wide { page: wide; }
.body-wide .data-table { font-size: 7.2pt; }
.body-wide .data-table th { padding: 4pt 4pt; font-size: 6.8pt; }
.body-wide .data-table td { padding: 3.4pt 4pt; }
* { box-sizing: border-box; }
html, body { margin:0; padding:0; font-family:'Helvetica','Arial',sans-serif; color:#1a1410; -weasy-font-feature:"tnum" on; }

/* ───── Cover (shared shell) ───── */
.cover { width:210mm; height:297mm; padding:22mm 20mm; position:relative; overflow:hidden; page-break-after:always; }
.cover-band-top { position:absolute; top:0; left:0; right:0; height:14mm; }
.cover-band-bottom { position:absolute; bottom:0; left:0; right:0; height:8mm; }
.cover-brand { text-align:center; margin-top:4mm; }
.cover-brand .crest { display:inline-block; width:18mm; height:18mm; border-radius:50%; line-height:18mm; text-align:center; font-size:18pt; font-weight:900; margin-bottom:6mm; }
.cover-brand .company { font-size:8pt; letter-spacing:3pt; font-weight:700; color:#8a7c63; text-transform:uppercase; }
.cover-eyebrow { text-align:center; font-size:8pt; letter-spacing:3pt; font-weight:800; margin:14mm 0 3mm; text-transform:uppercase; }
.cover-title { text-align:center; font-size:38pt; font-weight:900; line-height:1.05; margin:0 0 4mm; letter-spacing:-0.5pt; }
.cover-subtitle { text-align:center; font-style:italic; font-size:11pt; color:#4b5563; margin-bottom:12mm; }
.cover-period { margin:0 auto 14mm; padding:7mm 9mm; border-radius:3.5mm; width:154mm; display:flex; justify-content:space-between; align-items:center; position:relative; overflow:hidden; }
.cover-period .label { font-size:7pt; letter-spacing:2pt; font-weight:800; text-transform:uppercase; opacity:0.85; }
.cover-period .value { font-size:11.5pt; font-weight:800; color:#1a1410; margin-top:2mm; letter-spacing:-0.1pt; }
.cover-generated { text-align:center; margin:5mm 0 10mm; font-size:8.5pt; color:#6b7280; letter-spacing:0.3pt; }
.kpi-grid { display:flex; gap:4.5mm; margin:8mm auto 0; width:170mm; }
.kpi-tile { flex:1; padding:6mm 4mm 7mm; background:#ffffff; border:0.8pt solid #8a8170; border-top:2.5mm solid #b8860b; text-align:center; position:relative; box-shadow:0 1pt 3pt rgba(26,20,16,0.08); }
.kpi-label { font-size:7pt; letter-spacing:1.4pt; font-weight:800; color:#6b5840; text-transform:uppercase; margin:2mm 0 2.5mm; }
.kpi-value { font-size:19pt; font-weight:900; line-height:1; letter-spacing:-0.4pt; font-variant-numeric:tabular-nums; }
.kpi-value small { font-size:10pt; opacity:0.7; font-weight:700; }
.chip-row { width:170mm; margin:8mm auto 0; display:flex; flex-wrap:wrap; gap:3mm; justify-content:center; }
.chip { display:inline-block; padding:1.5mm 4mm; border-radius:2mm; font-size:7.5pt; font-weight:800; letter-spacing:0.5pt; text-transform:uppercase; }
.cover-footer { position:absolute; left:0; right:0; bottom:12mm; text-align:center; font-size:7.5pt; color:#8a7c63; }
.cover-footer .legal { font-weight:700; letter-spacing:0.4pt; }
.cover-footer .confidential { margin-top:1mm; font-size:7pt; letter-spacing:2pt; text-transform:uppercase; }

/* ───── Body header ───── */
.page-head { display:flex; justify-content:space-between; align-items:baseline; border-bottom:0.6pt solid #d8cdb5; padding-bottom:2.5mm; margin-bottom:6mm; }
.page-head .title { font-size:12pt; font-weight:800; letter-spacing:-0.2pt; }
.page-head .meta { font-size:7pt; color:#8a7c63; letter-spacing:0.5pt; }
.section-h { margin:5mm 0 2mm; font-size:18pt; font-weight:900; letter-spacing:-0.4pt; line-height:1.1; }
.section-rule { width:26mm; height:1.1mm; margin-bottom:4mm; border-radius:0.6mm; }
.section-sub { margin:0 0 6mm; font-size:9.5pt; color:#4b5563; letter-spacing:0.1pt; }

/* ───── Data table ───── */
.data-table { width:100%; border-collapse:collapse; margin-top:2mm; font-size:8pt; table-layout:auto; }
.data-table thead { display:table-header-group; }
.data-table tbody tr { page-break-inside:avoid; break-inside:avoid; }
.data-table th { color:#fff; text-align:left; padding:2.4mm 1.8mm; font-weight:800; font-size:7pt; letter-spacing:0.5pt; text-transform:uppercase; border:1pt solid #1a1410; border-bottom-width:2pt; }
.data-table th.r { text-align:right; }
.data-table th.c { text-align:center; }
.data-table td { padding:1.6mm 1.8mm; border:1pt solid #2a2118; vertical-align:middle; color:#1a1410; }
.data-table tr.zebra td { background:#fbf6ea; }
.data-table td.r { text-align:right; font-variant-numeric:tabular-nums; }
.data-table td.c { text-align:center; }
.data-table td.cell-danger { background:#fee2e2; color:#7f1d1d; font-weight:800; border-left:1.2pt solid #b91c1c; }
.data-table td.cell-warn { background:#fef9c3; color:#713f12; font-weight:800; border-left:1.2pt solid #a16207; }
.data-table td.cell-good { background:#ccfbf1; color:#115e59; font-weight:800; border-left:1.2pt solid #0d9488; }
.data-table .tot-row td { background:#1a1410; color:#fde68a; font-weight:900; border:1pt solid #1a1410; padding:2.2mm 1.8mm; }
.data-table .tot-row td.tot-label { letter-spacing:1.5pt; }
.data-table .tot-row td.r { text-align:right; }
.status-pill { display:inline-block; padding:1mm 2.5mm; border-radius:6mm; font-size:6.8pt; font-weight:800; letter-spacing:0.6pt; text-transform:uppercase; line-height:1.1; }
.empty { margin:30mm 0; text-align:center; color:#8a7c63; font-style:italic; }
"""


def _body_pages(report_key: str, shaped_rows: list[dict], summary: dict, theme: dict, period: dict) -> str:
    accent = theme["accent"]
    deep = theme["accent_deep"]
    ref = f"FRC/PAY/{report_key.upper().replace('-', '')}/{period['year']}/{datetime.now().strftime('%m%d%H%M')}"

    page_head = f"""
    <div class="page-head">
        <div class="title">{esc(COMPANY['name'])} · {esc(theme['name'])}</div>
        <div class="meta">Ref {esc(ref)} · {esc(period['label'])} · FY {esc(period['fy'])}</div>
    </div>
    """
    section_h = f"""
    <h2 class="section-h" style="color:{deep}">{esc(theme['name'].upper())}</h2>
    <div class="section-rule" style="background:{accent}"></div>
    <p class="section-sub">
        {len(shaped_rows)} record{'' if len(shaped_rows) == 1 else 's'} ·
        {summary.get('employees', summary.get('rows', 0))} employee(s) ·
        {esc(period['label'])}
    </p>
    """
    if shaped_rows:
        table = _table_html(report_key, shaped_rows)
        totals = _totals_row_html(report_key, shaped_rows)
        if totals:
            table = table.replace("</tbody>", f"{totals}</tbody>")
    else:
        table = '<div class="empty">No records found for the selected pay period.</div>'

    accent_css = f".data-table th {{ background:{accent}; }}"
    body_cls = "body-wide" if report_key in WIDE_REPORTS else ""
    return f'<section class="{body_cls}">{page_head}{section_h}{table}<style>{accent_css}</style></section>'


def render_pdf(report_key: str, shaped_rows: list[dict], summary: dict, meta_arg: dict) -> bytes:
    """Render ``report_key`` to PDF bytes. ``meta_arg`` carries a ``period`` dict
    (year/month/label/fy)."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433 — lazy by design

    theme = report_meta(report_key)
    motif = theme["motif"]
    period = meta_arg["period"]

    cover_fn = COVER_RENDERERS.get(motif) or COVER_RENDERERS.get("ledger")
    cover_html = cover_fn(theme, summary, period, shaped_count=len(shaped_rows))
    try:
        body_html = render_body(report_key, shaped_rows, summary, theme, period)
    except Exception:  # noqa: BLE001 — never let a bespoke body sink the export
        body_html = _body_pages(report_key, shaped_rows, summary, theme, period)

    base_css = (_BASE_CSS + KIT_CSS).replace("{COMPANY_LEGAL}", COMPANY["legal"]).replace("{COMPANY_WEB}", COMPANY["web"])

    full = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <title>{esc(theme['name'])} · {COMPANY['name']}</title>
    <style>{base_css}</style></head>
    <body>{cover_html}{body_html}</body></html>"""

    return HTML(string=full).write_pdf()
