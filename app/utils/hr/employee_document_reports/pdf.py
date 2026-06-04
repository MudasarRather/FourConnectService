"""WeasyPrint PDF designs for HR Employee-Document reports.

Ultra-modern editorial system: a full-bleed gradient hero, oversized display
type, a live status-distribution ribbon, KPI tiles, and a clean grid table for
the body. Four cover motifs share one base sheet:

    feature  — compliance ledger (calm, certificate-like)
    alert    — expired / pending (bold hero count + chevron motif)
    radar    — expiry watch (horizon timeline of buckets)
    digest   — verification / category (big horizontal distribution bars)

All measurements in mm / pt so output is crisp at any zoom and prints to A4.

Public entry: ``render_pdf(report_key, shaped_rows, summary, meta) -> bytes``
"""
from __future__ import annotations

import html
from datetime import datetime, date as date_cls
from typing import Any

from .data import report_meta, columns, STATUS_COLORS, SUMMARY_KEYS, EXPIRING_WINDOW_DAYS


COMPANY = {
    "name": "Fourreck",
    "legal": "Fourreck Technologies Pvt. Ltd.",
    "address_1": "4th Floor, Innovation Tower",
    "address_2": "Hyderabad, Telangana 500032, India",
    "email": "hr@fourreck.com",
    "web": "fourreck.com",
}


# ════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ════════════════════════════════════════════════════════════════════════════


def _esc(v: Any) -> str:
    return "" if v is None else html.escape(str(v))


def _fmt_date(d) -> str:
    if not d:
        return "—"
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except ValueError:
            return d
    return d.strftime("%d %b %Y")


def _fmt_long_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except ValueError:
            return d
    return d.strftime("%A, %d %B %Y")


FORMATTERS = {"date": _fmt_date}


# ════════════════════════════════════════════════════════════════════════════
# Base stylesheet
# ════════════════════════════════════════════════════════════════════════════

_BASE_CSS = """
@page {
    size: A4;
    margin: 16mm 14mm 20mm 14mm;
    @bottom-left {
        content: "{COMPANY_LEGAL} · {COMPANY_WEB}";
        font-family: 'Helvetica', sans-serif; font-size: 7.5pt; color: #8a8170;
    }
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-family: 'Helvetica', sans-serif; font-size: 7.5pt; color: #8a8170;
    }
}
@page :first { margin: 0; @bottom-left { content: ""; } @bottom-right { content: ""; } }

* { box-sizing: border-box; }
html, body {
    margin: 0; padding: 0;
    font-family: 'Helvetica', 'Arial', sans-serif;
    color: #1a1410;
    -weasy-font-feature: "tnum" on;
}

/* ───────────── Cover shell ───────────── */
.cover {
    width: 210mm; height: 297mm;
    position: relative; overflow: hidden;
    page-break-after: always;
    background: #fffdf8;
}
.hero {
    position: relative;
    height: 96mm;
    padding: 16mm 18mm 0;
    color: #fff;
    overflow: hidden;
}
/* faint geometric watermark inside the hero */
.hero .watermark {
    position: absolute;
    right: -26mm; top: -34mm;
    width: 120mm; height: 120mm; border-radius: 50%;
    background: rgba(255,255,255,0.08);
}
.hero .watermark.two {
    right: 6mm; top: 30mm; width: 70mm; height: 70mm;
    background: rgba(255,255,255,0.06);
}
.hero-top {
    display: flex; align-items: center; justify-content: space-between;
    position: relative; z-index: 2;
}
.brand { display: flex; align-items: center; gap: 4mm; }
.brand .crest {
    width: 11mm; height: 11mm; border-radius: 2.6mm;
    background: rgba(255,255,255,0.16);
    border: 0.5pt solid rgba(255,255,255,0.5);
    text-align: center; line-height: 11mm;
    font-size: 15pt; font-weight: 900;
}
.brand .name { font-size: 11pt; font-weight: 800; letter-spacing: 0.3pt; }
.brand .name small {
    display: block; font-size: 6.5pt; font-weight: 700;
    letter-spacing: 1.6pt; opacity: 0.8; text-transform: uppercase;
}
.hero-tag {
    font-size: 7pt; font-weight: 800; letter-spacing: 2pt; text-transform: uppercase;
    padding: 1.6mm 4mm; border-radius: 6mm;
    background: rgba(255,255,255,0.16); border: 0.5pt solid rgba(255,255,255,0.4);
}
.hero-eyebrow {
    position: relative; z-index: 2;
    margin: 13mm 0 2mm;
    font-size: 8pt; font-weight: 800; letter-spacing: 3.4pt; text-transform: uppercase;
    opacity: 0.9;
}
.hero-title {
    position: relative; z-index: 2;
    font-size: 40pt; font-weight: 900; letter-spacing: -1pt; line-height: 0.98;
    margin: 0;
}
.hero-sub {
    position: relative; z-index: 2;
    margin: 4mm 0 0; max-width: 150mm;
    font-size: 10.5pt; font-style: italic; opacity: 0.92;
}

/* Hero count block (alert motif) */
.hero-count {
    position: absolute; right: 18mm; bottom: 13mm; z-index: 2;
    text-align: right;
}
.hero-count .n {
    font-size: 56pt; font-weight: 900; line-height: 0.9;
    font-variant-numeric: tabular-nums; letter-spacing: -2pt;
}
.hero-count .l {
    font-size: 7.5pt; font-weight: 800; letter-spacing: 2pt;
    text-transform: uppercase; opacity: 0.85;
}
.chevrons { position: absolute; left: 0; bottom: 0; height: 12mm; width: 100%; z-index: 1; }

/* ───────────── Scope chip row ───────────── */
.scope {
    margin: 9mm 18mm 0;
    display: flex; gap: 0;
    border: 0.8pt solid #e6ddca; border-radius: 3mm; overflow: hidden;
    background: #fff;
    box-shadow: 0 1pt 4pt rgba(26,20,16,0.06);
}
.scope .cell {
    flex: 1; padding: 4.5mm 6mm;
    border-right: 0.8pt solid #efe7d6;
}
.scope .cell:last-child { border-right: 0; }
.scope .k {
    font-size: 6.6pt; font-weight: 800; letter-spacing: 1.4pt;
    text-transform: uppercase; color: #9a8c72;
}
.scope .v { margin-top: 1.5mm; font-size: 11pt; font-weight: 800; color: #1a1410; }

/* ───────────── KPI tiles ───────────── */
.kpis { margin: 7mm 18mm 0; display: flex; gap: 4mm; }
.kpi {
    flex: 1; padding: 5.5mm 4mm 6mm; background: #fff;
    border: 0.8pt solid #e6ddca; border-top: 2.6mm solid #999;
    text-align: center; box-shadow: 0 1pt 3pt rgba(26,20,16,0.07);
}
.kpi .l {
    font-size: 6.8pt; font-weight: 800; letter-spacing: 1.4pt;
    text-transform: uppercase; color: #6b5840; margin-bottom: 2.4mm;
}
.kpi .v {
    font-size: 23pt; font-weight: 900; line-height: 1;
    letter-spacing: -0.6pt; font-variant-numeric: tabular-nums;
}
.kpi .v small { font-size: 11pt; opacity: 0.65; font-weight: 800; }

/* ───────────── Status distribution ribbon ───────────── */
.ribbon-wrap { margin: 8mm 18mm 0; }
.ribbon-label {
    font-size: 7pt; font-weight: 800; letter-spacing: 1.6pt; text-transform: uppercase;
    color: #9a8c72; margin-bottom: 2.5mm;
}
.ribbon {
    display: flex; width: 100%; height: 8mm; border-radius: 2mm; overflow: hidden;
    border: 0.6pt solid #e6ddca;
}
.ribbon .seg {
    height: 100%;
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-size: 7pt; font-weight: 800;
}
.ribbon-legend { display: flex; flex-wrap: wrap; gap: 2mm 5mm; margin-top: 3mm; }
.ribbon-legend .li {
    display: flex; align-items: center; gap: 1.8mm;
    font-size: 7.5pt; font-weight: 700; color: #4b3f2c;
}
.ribbon-legend .dot { width: 2.6mm; height: 2.6mm; border-radius: 0.8mm; }

/* ───────────── Digest bars (verification / category covers) ───────────── */
.digest { margin: 8mm 18mm 0; }
.digest .row { margin-bottom: 4.5mm; }
.digest .row .top {
    display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 1.6mm;
}
.digest .row .seg { font-size: 9.5pt; font-weight: 800; color: #1a1410; }
.digest .row .num { font-size: 9.5pt; font-weight: 800; font-variant-numeric: tabular-nums; }
.digest .row .num small { color: #9a8c72; font-weight: 700; }
.digest .track { height: 6mm; border-radius: 1.6mm; background: #f1ead9; overflow: hidden; }
.digest .fill { height: 100%; border-radius: 1.6mm; }

/* Radar buckets (expiry watch cover) */
.buckets { margin: 8mm 18mm 0; display: flex; gap: 4mm; }
.bucket {
    flex: 1; padding: 5mm 4mm; background: #fff; border: 0.8pt solid #e6ddca;
    border-radius: 2.6mm; text-align: center;
}
.bucket .n { font-size: 21pt; font-weight: 900; line-height: 1; font-variant-numeric: tabular-nums; }
.bucket .l { margin-top: 2mm; font-size: 7pt; font-weight: 800; letter-spacing: 1pt; text-transform: uppercase; color: #6b5840; }

/* ───────────── Cover footer ───────────── */
.cover-foot {
    position: absolute; left: 18mm; right: 18mm; bottom: 12mm;
    border-top: 0.6pt solid #e6ddca; padding-top: 4mm;
    display: flex; justify-content: space-between; align-items: center;
}
.cover-foot .legal { font-size: 7.5pt; color: #8a8170; font-weight: 700; }
.cover-foot .conf {
    font-size: 6.6pt; font-weight: 800; letter-spacing: 2pt; text-transform: uppercase;
    color: #b91c1c; padding: 1.4mm 3.5mm; border: 0.6pt solid #f1c9c9; border-radius: 6mm;
}
.cover-foot .gen { font-size: 7.5pt; color: #8a8170; }

/* ───────────── Body pages ───────────── */
.page-head {
    display: flex; justify-content: space-between; align-items: baseline;
    border-bottom: 0.6pt solid #d1cabb; padding-bottom: 2.5mm; margin-bottom: 5mm;
}
.page-head .title { font-size: 11.5pt; font-weight: 800; letter-spacing: -0.2pt; }
.page-head .meta { font-size: 7pt; color: #8a8170; letter-spacing: 0.4pt; }
.section-h { margin: 4mm 0 1.5mm; font-size: 17pt; font-weight: 900; letter-spacing: -0.4pt; }
.section-rule { width: 24mm; height: 1.1mm; margin-bottom: 3.5mm; border-radius: 0.6mm; }
.section-sub { margin: 0 0 5mm; font-size: 9pt; color: #4b5563; }

.data-table { width: 100%; border-collapse: collapse; margin-top: 1mm; font-size: 8pt; table-layout: auto; }
.data-table thead { display: table-header-group; }
.data-table tbody tr { page-break-inside: avoid; break-inside: avoid; }
.data-table th {
    color: #fff; text-align: left; padding: 2.4mm 1.8mm; font-weight: 800;
    font-size: 7pt; letter-spacing: 0.5pt; text-transform: uppercase;
    border: 1pt solid #1a1410; border-bottom-width: 2pt;
}
.data-table th.r { text-align: right; }
.data-table td {
    padding: 1.7mm 1.8mm; border: 1.1pt solid #cabfa6; vertical-align: middle; color: #1a1410;
}
.data-table tr.zebra td { background: #fbf7ee; }
.data-table td.r { text-align: right; font-variant-numeric: tabular-nums; }
.data-table td.cell-danger { background: #fee2e2; color: #7f1d1d; font-weight: 800; }
.data-table td.cell-warn { background: #fef9c3; color: #713f12; font-weight: 800; }
.status-pill {
    display: inline-block; padding: 1mm 2.6mm; border-radius: 6mm;
    font-size: 6.8pt; font-weight: 800; letter-spacing: 0.5pt; text-transform: uppercase; line-height: 1.1;
}
.bar-cell { display: flex; align-items: center; gap: 2mm; }
.bar-track { flex: 1; height: 3mm; background: #f1ead9; border-radius: 1mm; overflow: hidden; min-width: 22mm; }
.bar-fill { height: 100%; border-radius: 1mm; }
.bar-num { font-size: 7.5pt; font-weight: 800; color: #6b5840; }
.empty { margin: 40mm 0; text-align: center; color: #8a8170; font-style: italic; font-size: 10pt; }
"""


# ════════════════════════════════════════════════════════════════════════════
# Shared cover fragments
# ════════════════════════════════════════════════════════════════════════════


def _gradient(meta: dict) -> str:
    return f"linear-gradient(125deg,{meta['accent_deep']} 0%,{meta['accent']} 58%,{meta['accent']} 100%)"


def _scope_html(summary: dict, shaped_count: int, in_report_label: str = "In this report") -> str:
    cells = [
        ("As of", datetime.now().strftime("%d %b %Y")),
        (in_report_label, f"{shaped_count:,}"),
        ("Active documents", f"{summary['total']:,}"),
        ("Employees", f"{summary['employees']:,}"),
    ]
    return '<div class="scope">' + "".join(
        f'<div class="cell"><div class="k">{html.escape(k)}</div><div class="v">{html.escape(str(v))}</div></div>'
        for k, v in cells
    ) + "</div>"


def _kpi_html(meta: dict, summary: dict, shaped_count: int) -> str:
    accent = meta["accent"]
    tiles = [
        ("In report", f"{shaped_count:,}", accent),
        ("Verified", f"{summary['verified_pct']}<small>%</small>", "#0d9488"),
        ("Pending", f"{summary['pending']:,}", "#ca8a04"),
        ("Expiring ≤30d", f"{summary['expiring']:,}", "#ea580c"),
    ]
    return '<div class="kpis">' + "".join(
        f'<div class="kpi" style="border-top-color:{c}">'
        f'<div class="l">{html.escape(label)}</div>'
        f'<div class="v" style="color:{c}">{val}</div></div>'
        for label, val, c in tiles
    ) + "</div>"


def _ribbon_html(summary: dict) -> str:
    segs = [
        ("Verified", summary["verified"], STATUS_COLORS["VERIFIED"]["hex"]),
        ("Pending", summary["pending"], STATUS_COLORS["PENDING"]["hex"]),
        ("Rejected", summary["rejected"], STATUS_COLORS["REJECTED"]["hex"]),
        ("Expired", summary["expired"], STATUS_COLORS["EXPIRED"]["hex"]),
    ]
    total = sum(v for _, v, _ in segs) or 1
    bar = "".join(
        f'<div class="seg" style="width:{max(v / total * 100, 0):.2f}%;background:{c}">'
        f'{v if (v / total) > 0.06 else ""}</div>'
        for _, v, c in segs if v > 0
    )
    legend = "".join(
        f'<div class="li"><span class="dot" style="background:{c}"></span>{html.escape(label)} · {v}</div>'
        for label, v, c in segs
    )
    return (
        '<div class="ribbon-wrap"><div class="ribbon-label">Verification distribution · all active documents</div>'
        f'<div class="ribbon">{bar}</div><div class="ribbon-legend">{legend}</div></div>'
    )


def _hero(meta: dict, extra_zone: str = "") -> str:
    return f"""
    <div class="hero" style="background:{_gradient(meta)}">
        <div class="watermark"></div>
        <div class="watermark two"></div>
        <div class="hero-top">
            <div class="brand">
                <div class="crest">F</div>
                <div class="name">{COMPANY['name']}<small>Employee Documents</small></div>
            </div>
            <div class="hero-tag">{html.escape(meta['tagline'])}</div>
        </div>
        <div class="hero-eyebrow">HR · DOCUMENT INTELLIGENCE · {datetime.now().strftime('%b %Y').upper()}</div>
        <h1 class="hero-title">{html.escape(meta['name'])}</h1>
        <p class="hero-sub">{html.escape(meta['subtitle'])}</p>
        {extra_zone}
    </div>
    """


def _cover_foot() -> str:
    return f"""
    <div class="cover-foot">
        <div class="legal">{COMPANY['legal']} · {COMPANY['address_1']}</div>
        <div class="conf">Confidential</div>
        <div class="gen">Generated {datetime.now().strftime('%d %b %Y · %I:%M %p').lstrip('0')}</div>
    </div>
    """


# ════════════════════════════════════════════════════════════════════════════
# Cover motifs
# ════════════════════════════════════════════════════════════════════════════


def _cover_feature(meta, summary, shaped_count):
    return f"""
    <section class="cover">
        {_hero(meta)}
        {_scope_html(summary, shaped_count)}
        {_kpi_html(meta, summary, shaped_count)}
        {_ribbon_html(summary)}
        {_cover_foot()}
    </section>"""


def _cover_alert(meta, summary, shaped_count):
    accent = meta["accent"]
    # diagonal chevron strip at the bottom of the hero
    chev = (
        f'<svg class="chevrons" viewBox="0 0 210 12" preserveAspectRatio="none">'
        + "".join(
            f'<path d="M{x} 12 L{x+6} 0 L{x+12} 0 L{x+6} 12 Z" fill="rgba(255,255,255,0.10)"/>'
            for x in range(-12, 222, 12)
        )
        + "</svg>"
    )
    hero_count = (
        f'<div class="hero-count"><div class="n">{shaped_count:,}</div>'
        f'<div class="l">documents flagged</div></div>{chev}'
    )
    return f"""
    <section class="cover">
        {_hero(meta, hero_count)}
        {_scope_html(summary, shaped_count, in_report_label="Flagged")}
        {_kpi_html(meta, summary, shaped_count)}
        {_ribbon_html(summary)}
        {_cover_foot()}
    </section>"""


def _cover_radar(meta, summary, shaped_count, buckets):
    bucket_html = "".join(
        f'<div class="bucket"><div class="n" style="color:{c}">{n}</div><div class="l">{html.escape(l)}</div></div>'
        for l, n, c in buckets
    )
    return f"""
    <section class="cover">
        {_hero(meta)}
        {_scope_html(summary, shaped_count, in_report_label="Expiring soon")}
        <div class="buckets">{bucket_html}</div>
        {_kpi_html(meta, summary, shaped_count)}
        {_cover_foot()}
    </section>"""


def _cover_digest(meta, summary, shaped_count, shaped_rows):
    accent = meta["accent"]
    deep = meta["accent_deep"]
    max_v = max((r["value"] for r in shaped_rows), default=1) or 1
    rows_html = ""
    for r in shaped_rows[:9]:
        # colour by status for the verification digest, accent gradient otherwise
        if "status_key" in r:
            sc = STATUS_COLORS.get(r["status_key"], {"hex": accent})["hex"]
        else:
            sc = accent
        w = max(r["value"] / max_v * 100, 1.5)
        rows_html += f"""
        <div class="row">
            <div class="top">
                <span class="seg">{html.escape(r['segment'])}</span>
                <span class="num">{r['value']:,} <small>· {r['pct']}%</small></span>
            </div>
            <div class="track"><div class="fill" style="width:{w:.1f}%;background:linear-gradient(90deg,{sc},{deep})"></div></div>
        </div>"""
    return f"""
    <section class="cover">
        {_hero(meta)}
        {_scope_html(summary, summary['total'], in_report_label="Segments")}
        <div class="digest">{rows_html}</div>
        {_cover_foot()}
    </section>"""


# ════════════════════════════════════════════════════════════════════════════
# Body table
# ════════════════════════════════════════════════════════════════════════════


def _cell(row: dict, col: dict, accent: str) -> tuple[str, str]:
    raw = row.get(col["key"])

    if col.get("bar"):
        pct = int(raw or 0)
        inner = (
            f'<div class="bar-cell"><div class="bar-track">'
            f'<div class="bar-fill" style="width:{max(pct,1)}%;background:{accent}"></div></div>'
            f'<span class="bar-num">{pct}%</span></div>'
        )
        return inner, ""

    if col.get("status"):
        status = str(raw or "")
        sc = STATUS_COLORS.get(status, {"light": "#f1f5f9", "deep": "#334155", "hex": "#475569"})
        pill = (
            f'<span class="status-pill" style="background:{sc["light"]};color:{sc["deep"]};'
            f'border:0.6pt solid {sc["hex"]}55">{_esc(status.replace("_", " ").title())}</span>'
        )
        return pill, ""

    fmt = col.get("fmt")
    if fmt:
        val = FORMATTERS[fmt](raw)
    elif raw is None or raw == "":
        val = "—"
    else:
        val = _esc(raw)

    klass = ""
    if col.get("danger_if") and col["danger_if"](raw):
        klass = "cell-danger"
    elif col.get("warn_if") and col["warn_if"](raw):
        klass = "cell-warn"
    return (val if fmt else _esc(val)), klass


def _table_html(report_key: str, shaped_rows: list[dict], accent: str) -> str:
    cols = columns(report_key)
    head = "".join(
        f'<th class="{"r" if c["align"] == "right" else ""}">{_esc(c["label"])}</th>' for c in cols
    )
    body = []
    for i, row in enumerate(shaped_rows):
        cells = []
        for c in cols:
            val, klass = _cell(row, c, accent)
            align = "r" if c["align"] == "right" else ""
            cls = (align + " " + klass).strip()
            cells.append(f'<td class="{cls}">{val}</td>')
        zebra = "zebra" if i % 2 else ""
        body.append(f'<tr class="{zebra}">{"".join(cells)}</tr>')
    return (
        f'<table class="data-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>'
    )


def _body_pages(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> str:
    accent, deep = meta["accent"], meta["accent_deep"]
    ref = f"FRC/EDOC/{report_key.upper()}/{datetime.now().strftime('%Y%m%d-%H%M')}"
    n = len(shaped_rows)
    unit = "segment" if report_key in SUMMARY_KEYS else "record"
    page_head = f"""
    <div class="page-head">
        <div class="title">{COMPANY['name']} · {_esc(meta['name'])}</div>
        <div class="meta">Ref {ref} · Generated {datetime.now().strftime('%d %b %Y')}</div>
    </div>"""
    section = f"""
    <h2 class="section-h" style="color:{deep}">{_esc(meta['name'].upper())}</h2>
    <div class="section-rule" style="background:{accent}"></div>
    <p class="section-sub">{n} {unit}{'' if n == 1 else 's'} · as of {datetime.now().strftime('%d %B %Y')}</p>"""
    table = _table_html(report_key, shaped_rows, accent) if shaped_rows else (
        '<div class="empty">No records match this report right now.</div>'
    )
    accent_css = f".data-table th {{ background:{accent}; }}"
    return f"<section>{page_head}{section}{table}<style>{accent_css}</style></section>"


# ════════════════════════════════════════════════════════════════════════════
# Public renderer
# ════════════════════════════════════════════════════════════════════════════


def render_pdf(report_key: str, shaped_rows: list[dict], summary: dict, meta_arg: dict) -> bytes:
    # Defer GTK/WeasyPrint import to render time (boot-safe on Windows).
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433

    meta = report_meta(report_key)
    motif = meta["motif"]
    shaped_count = len(shaped_rows)

    if motif == "alert":
        cover = _cover_alert(meta, summary, shaped_count)
    elif motif == "radar":
        # expiry buckets from the shaped rows (days_to_expiry already filtered 0..90)
        b7 = sum(1 for r in shaped_rows if (r.get("days_to_expiry") or 0) <= 7)
        b30 = sum(1 for r in shaped_rows if 7 < (r.get("days_to_expiry") or 0) <= 30)
        b90 = sum(1 for r in shaped_rows if 30 < (r.get("days_to_expiry") or 0) <= EXPIRING_WINDOW_DAYS)
        buckets = [
            ("Within 7 days", b7, "#b91c1c"),
            ("8–30 days", b30, "#ea580c"),
            (f"31–{EXPIRING_WINDOW_DAYS} days", b90, "#ca8a04"),
        ]
        cover = _cover_radar(meta, summary, shaped_count, buckets)
    elif motif == "digest":
        cover = _cover_digest(meta, summary, shaped_count, shaped_rows)
    else:
        cover = _cover_feature(meta, summary, shaped_count)

    body = _body_pages(report_key, shaped_rows, summary, meta)
    base_css = _BASE_CSS.replace("{COMPANY_LEGAL}", COMPANY["legal"]).replace("{COMPANY_WEB}", COMPANY["web"])

    full = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <title>{_esc(meta['name'])} · {COMPANY['name']}</title>
    <style>{base_css}</style></head><body>{cover}{body}</body></html>"""

    return HTML(string=full).write_pdf()
