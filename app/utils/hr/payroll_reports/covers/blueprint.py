"""Cover motif: "blueprint" — Headcount & Cost.

An architectural drafting sheet. Dark navy/indigo ground laid with a cyan/violet
engineering grid (fine + coarse linear-gradients), self-drawn dimension lines, an
org-distribution bar sketch, and a drafting TITLE BLOCK boxed bottom-right with
Project / Scale / Sheet metadata. Violet accent (the analytics dark cover; its
sibling "industrial" keeps orange).

Everything is CSS/HTML art — no images, no external fonts. Selectors scoped under
``.cover-blueprint`` so nothing leaks into the shared body table.
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, kpi_tiles_html, now_stamp, month_name,
)


def _dist_sketch(summary: dict, period: dict, accent: str) -> str:
    """A small hand-drawn org-distribution bar chart from cost share.

    We don't get per-department rows here, so we synthesise a clean schematic
    from the headline totals — a drafted elevation, not real data. Kept honest:
    bars carry no numeric labels, only the schematic shape + a single annotated
    'TOTAL COST' dimension.
    """
    heads = int(summary.get("headcount", summary.get("employees", 0)) or 0)
    total_cost = float(summary.get("total_cost", 0) or 0)
    # Deterministic schematic profile (a drafted distribution silhouette).
    profile = [0.92, 0.74, 0.58, 0.46, 0.33, 0.22]
    bars = []
    x = 0.0
    step = 100.0 / len(profile)
    for i, h in enumerate(profile):
        bh = 2 + h * 34  # mm-ish within the 40mm plot
        left = x + step * 0.18
        w = step * 0.64
        bars.append(
            f'<div style="position:absolute;bottom:0;left:{left:.2f}%;width:{w:.2f}%;'
            f'height:{bh:.2f}mm;background:linear-gradient(180deg,{accent},#5b21b6);'
            f'border:0.4pt solid #c4b5fd;border-bottom:none;'
            f'box-shadow:inset 0 0 0 0.3pt rgba(196,181,253,0.35)"></div>'
        )
        # column tick on the baseline
        bars.append(
            f'<div style="position:absolute;bottom:-2.4mm;left:{left + w/2:.2f}%;'
            f'width:0.5pt;height:1.8mm;background:#67e8f9"></div>'
        )
        x += step
    return f"""
    <div class="bp-plot">
        <div class="bp-plot-head">FIG.01 — WORKFORCE DISTRIBUTION · SCHEMATIC ELEVATION</div>
        <div class="bp-plot-frame">
            <div class="bp-plot-bars">{''.join(bars)}</div>
            <div class="bp-baseline"></div>
            <div class="bp-ord"></div>
            <div class="bp-dim-x">
                <span class="bp-arrow l">&#9664;</span>
                <span class="bp-dim-text">{esc(heads)} HEADS PLOTTED</span>
                <span class="bp-arrow r">&#9654;</span>
            </div>
        </div>
    </div>
    """


def _title_block(period: dict, accent: str, shaped_count) -> str:
    """The drafting title block — boxed grid bottom-right, engineering-drawing style."""
    sheet_no = f"PAY-A-{period.get('year', '')}-{int(period.get('month', 0) or 0):02d}"
    drawn = now_stamp()
    rows = int(shaped_count or 0)
    cells = [
        ("PROJECT", f"{COMPANY['name'].upper()} · PAYROLL"),
        ("DRAWING", "HEADCOUNT &amp; COST"),
        ("PAY PERIOD", esc(period.get("label", ""))),
        ("FISCAL YEAR", f"FY {esc(period.get('fy', ''))}"),
        ("SCALE", "1 : 1  N.T.S."),
        ("DEPARTMENTS", f"{rows} PLOTTED"),
        ("SHEET", sheet_no),
        ("REV", "A &#8212; ISSUED"),
        ("DRAWN", esc(drawn)),
        ("DRAWN BY", "PAYROLL / HRIS"),
        ("CHK&#8217;D", "FINANCE"),
        ("CLASS", "CONFIDENTIAL"),
    ]
    body = "".join(
        f'<div class="bp-tb-cell"><div class="bp-tb-k">{k}</div>'
        f'<div class="bp-tb-v">{v}</div></div>'
        for k, v in cells
    )
    return f"""
    <div class="bp-titleblock">
        <div class="bp-tb-strip" style="background:{accent}">
            <span>{esc(COMPANY['legal'].upper())}</span>
            <span class="bp-tb-strip-r">DRAWING No. {sheet_no}</span>
        </div>
        <div class="bp-tb-grid">{body}</div>
    </div>
    """


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#7c3aed")
    soft = meta.get("accent_soft", "#ede9fe")
    deep = meta.get("accent_deep", "#4c1d95")
    icon = esc(meta.get("icon", "O"))

    heads = int(summary.get("headcount", summary.get("employees", 0)) or 0)
    employees = int(summary.get("employees", heads) or 0)
    total_cost = float(summary.get("total_cost", 0) or 0)
    depts = int((shaped_count if shaped_count is not None else summary.get("rows", 0)) or 0)
    avg_cost = (total_cost / heads) if heads else 0.0

    tiles = kpi_tiles_html([
        ("Departments", esc(depts), "#67e8f9"),
        ("Headcount", esc(heads), "#a78bfa"),
        ("Total Cost", inr_compact(total_cost), "#34d399"),
        ("Avg / Head", inr_compact(avg_cost), "#fbbf24"),
    ])

    dist = _dist_sketch(summary, period, accent)
    title_block = _title_block(period, accent, depts)

    return f"""
    <section class="cover cover-blueprint">
      <style>
        .cover-blueprint {{
            background:#0b1020;
            color:#e2e8f0;
            padding:0;
        }}
        /* Engineering grid — fine cyan lines + coarse violet lines + a vignette */
        .cover-blueprint .bp-grid {{
            position:absolute; inset:0;
            background-image:
                repeating-linear-gradient(0deg, rgba(103,232,249,0.10) 0, rgba(103,232,249,0.10) 0.25pt, transparent 0.25pt, transparent 6mm),
                repeating-linear-gradient(90deg, rgba(103,232,249,0.10) 0, rgba(103,232,249,0.10) 0.25pt, transparent 0.25pt, transparent 6mm),
                repeating-linear-gradient(0deg, rgba(167,139,250,0.20) 0, rgba(167,139,250,0.20) 0.5pt, transparent 0.5pt, transparent 30mm),
                repeating-linear-gradient(90deg, rgba(167,139,250,0.20) 0, rgba(167,139,250,0.20) 0.5pt, transparent 0.5pt, transparent 30mm);
        }}
        .cover-blueprint .bp-vignette {{
            position:absolute; inset:0;
            background:
                radial-gradient(120% 80% at 18% 8%, rgba(124,58,237,0.30), transparent 55%),
                radial-gradient(110% 70% at 95% 100%, rgba(11,16,32,0.85), transparent 60%);
        }}
        /* Drafting border — double frame with corner ticks */
        .cover-blueprint .bp-frame {{
            position:absolute; top:10mm; left:10mm; right:10mm; bottom:10mm;
            border:0.8pt solid rgba(167,139,250,0.55);
        }}
        .cover-blueprint .bp-frame-inner {{
            position:absolute; top:12mm; left:12mm; right:12mm; bottom:12mm;
            border:0.4pt solid rgba(103,232,249,0.45);
        }}
        .cover-blueprint .bp-corner {{
            position:absolute; width:6mm; height:6mm; border:0.8pt solid #67e8f9;
        }}
        .cover-blueprint .bp-corner.tl {{ top:10mm; left:10mm; border-right:none; border-bottom:none; }}
        .cover-blueprint .bp-corner.tr {{ top:10mm; right:10mm; border-left:none; border-bottom:none; }}
        .cover-blueprint .bp-corner.bl {{ bottom:10mm; left:10mm; border-right:none; border-top:none; }}
        .cover-blueprint .bp-corner.br {{ bottom:10mm; right:10mm; border-left:none; border-top:none; }}

        .cover-blueprint .bp-content {{ position:absolute; top:18mm; left:20mm; right:20mm; }}

        /* Brand mark — drafted crest */
        .cover-blueprint .bp-brand {{ display:flex; align-items:center; gap:5mm; }}
        .cover-blueprint .bp-crest {{
            width:15mm; height:15mm; border:0.8pt solid #a78bfa;
            background:linear-gradient(135deg, rgba(124,58,237,0.45), rgba(11,16,32,0.2));
            color:#ddd6fe; font-size:15pt; font-weight:900; text-align:center; line-height:15mm;
            box-shadow:inset 0 0 0 0.4pt rgba(103,232,249,0.5);
        }}
        .cover-blueprint .bp-brand-txt .co {{
            font-size:7.5pt; letter-spacing:3pt; font-weight:800; color:#a5b4fc; text-transform:uppercase;
        }}
        .cover-blueprint .bp-brand-txt .legal {{
            font-size:6.5pt; letter-spacing:1.2pt; color:#7c8aa8; margin-top:1mm; text-transform:uppercase;
        }}
        .cover-blueprint .bp-brand-spec {{
            margin-left:auto; text-align:right; font-family:'Courier New',monospace;
            font-size:6.5pt; letter-spacing:1pt; color:#67e8f9; line-height:1.6;
        }}

        .cover-blueprint .bp-eyebrow {{
            margin-top:14mm; font-family:'Courier New',monospace; font-size:8pt; letter-spacing:4pt;
            font-weight:700; color:#67e8f9; text-transform:uppercase;
        }}
        .cover-blueprint .bp-eyebrow:before {{ content:"// "; color:#a78bfa; }}
        .cover-blueprint .bp-title {{
            margin:3mm 0 0; font-size:42pt; font-weight:900; line-height:0.98; letter-spacing:-1pt;
            color:#f8fafc;
            text-shadow:0 0 0.4pt #67e8f9;
        }}
        .cover-blueprint .bp-title .amp {{ color:#a78bfa; font-style:italic; }}
        .cover-blueprint .bp-sub {{
            margin:4mm 0 0; font-size:10.5pt; font-style:italic; color:#94a3b8; max-width:120mm;
        }}

        /* dimension line under the title */
        .cover-blueprint .bp-dimline {{
            position:relative; margin:6mm 0 0; height:6mm; width:128mm;
        }}
        .cover-blueprint .bp-dimline .rule {{
            position:absolute; top:2.6mm; left:3mm; right:3mm; height:0.5pt; background:#67e8f9;
        }}
        .cover-blueprint .bp-dimline .end {{
            position:absolute; top:0; width:0.6pt; height:5mm; background:#67e8f9;
        }}
        .cover-blueprint .bp-dimline .end.l {{ left:3mm; }}
        .cover-blueprint .bp-dimline .end.r {{ right:3mm; }}
        .cover-blueprint .bp-dimline .lbl {{
            position:absolute; top:-0.6mm; left:50%; transform:translateX(-50%);
            background:#0b1020; padding:0 2mm; font-family:'Courier New',monospace;
            font-size:6.5pt; letter-spacing:2pt; color:#a78bfa;
        }}

        /* period band — drafted */
        .cover-blueprint .bp-period {{
            margin-top:9mm; display:flex; width:128mm;
            border:0.6pt solid rgba(167,139,250,0.55);
            background:rgba(124,58,237,0.10);
        }}
        .cover-blueprint .bp-period .seg {{ flex:1; padding:4mm 5mm; }}
        .cover-blueprint .bp-period .seg + .seg {{ border-left:0.5pt dashed rgba(103,232,249,0.5); }}
        .cover-blueprint .bp-period .k {{
            font-family:'Courier New',monospace; font-size:6.5pt; letter-spacing:2pt; color:#67e8f9; text-transform:uppercase;
        }}
        .cover-blueprint .bp-period .v {{ margin-top:1.6mm; font-size:13pt; font-weight:800; color:#f1f5f9; }}

        /* generated stamp */
        .cover-blueprint .bp-gen {{
            margin-top:6mm; font-family:'Courier New',monospace; font-size:7pt;
            letter-spacing:1.5pt; color:#7c8aa8;
        }}

        /* KPI strip — recolor shared tiles for the dark sheet */
        .cover-blueprint .kpi-grid {{ width:128mm; margin:8mm 0 0; gap:4mm; }}
        .cover-blueprint .kpi-tile {{
            background:rgba(15,23,42,0.72); border:0.5pt solid rgba(148,163,184,0.35);
            box-shadow:none; padding:5mm 3mm 6mm;
        }}
        .cover-blueprint .kpi-label {{ color:#94a3b8; }}

        /* distribution sketch */
        .cover-blueprint .bp-plot {{ position:absolute; left:20mm; bottom:62mm; width:108mm; }}
        .cover-blueprint .bp-plot-head {{
            font-family:'Courier New',monospace; font-size:6.5pt; letter-spacing:1.5pt; color:#67e8f9; margin-bottom:2.5mm;
        }}
        .cover-blueprint .bp-plot-frame {{ position:relative; height:40mm; padding-left:4mm; }}
        .cover-blueprint .bp-plot-bars {{ position:absolute; left:4mm; right:0; bottom:0; top:0; }}
        .cover-blueprint .bp-baseline {{ position:absolute; left:4mm; right:0; bottom:0; height:0.6pt; background:#67e8f9; }}
        .cover-blueprint .bp-ord {{ position:absolute; left:4mm; bottom:0; top:0; width:0.6pt; background:#67e8f9; }}
        .cover-blueprint .bp-dim-x {{
            position:absolute; left:4mm; right:0; bottom:-7mm; text-align:center;
            font-family:'Courier New',monospace; font-size:6pt; letter-spacing:1.5pt; color:#a78bfa;
        }}
        .cover-blueprint .bp-dim-x .bp-arrow {{ color:#67e8f9; font-size:5pt; }}
        .cover-blueprint .bp-dim-x .bp-dim-text {{ margin:0 2mm; }}

        /* drafting title block — bottom-right boxed grid */
        .cover-blueprint .bp-titleblock {{
            position:absolute; right:20mm; bottom:18mm; width:108mm;
            border:0.8pt solid #a78bfa; background:rgba(15,23,42,0.9);
        }}
        .cover-blueprint .bp-tb-strip {{
            display:flex; justify-content:space-between; align-items:center;
            padding:1.8mm 3mm; color:#fff; font-size:6.5pt; font-weight:800; letter-spacing:1.5pt;
        }}
        .cover-blueprint .bp-tb-strip-r {{ font-family:'Courier New',monospace; letter-spacing:1pt; }}
        .cover-blueprint .bp-tb-grid {{
            display:flex; flex-wrap:wrap;
        }}
        .cover-blueprint .bp-tb-cell {{
            width:25%; padding:2.2mm 2.5mm;
            border-top:0.4pt solid rgba(167,139,250,0.45);
            border-left:0.4pt solid rgba(167,139,250,0.45);
        }}
        .cover-blueprint .bp-tb-k {{
            font-family:'Courier New',monospace; font-size:5.5pt; letter-spacing:1pt; color:#67e8f9; text-transform:uppercase;
        }}
        .cover-blueprint .bp-tb-v {{
            margin-top:1mm; font-size:7.5pt; font-weight:700; color:#e2e8f0; line-height:1.15;
        }}

        /* confidential footer along the very bottom */
        .cover-blueprint .bp-footer {{
            position:absolute; left:20mm; bottom:13mm; width:106mm;
            font-size:6.5pt; color:#64748b; letter-spacing:0.5pt;
        }}
        .cover-blueprint .bp-footer .conf {{
            color:#c4b5fd; font-weight:800; letter-spacing:2pt; text-transform:uppercase;
        }}
      </style>

      <div class="bp-grid"></div>
      <div class="bp-vignette"></div>
      <div class="bp-frame"></div>
      <div class="bp-frame-inner"></div>
      <div class="bp-corner tl"></div>
      <div class="bp-corner tr"></div>
      <div class="bp-corner bl"></div>
      <div class="bp-corner br"></div>

      <div class="bp-content">
        <div class="bp-brand">
            <div class="bp-crest">{icon}</div>
            <div class="bp-brand-txt">
                <div class="co">{esc(COMPANY['name'])} · Payroll Drawing Office</div>
                <div class="legal">{esc(COMPANY['legal'])}</div>
            </div>
            <div class="bp-brand-spec">
                CIN {esc(COMPANY['cin'])}<br>
                GSTIN {esc(COMPANY['gst'])}<br>
                {esc(COMPANY['web'])}
            </div>
        </div>

        <div class="bp-eyebrow">Payroll &middot; {esc(meta.get('group', 'Analytics'))} &middot; Sheet A</div>
        <h1 class="bp-title">Headcount <span class="amp">&amp;</span> Cost</h1>
        <p class="bp-sub">{esc(meta.get('subtitle', 'Heads and pay-cost share across departments'))}</p>

        <div class="bp-dimline">
            <span class="end l"></span><span class="rule"></span><span class="end r"></span>
            <span class="lbl">WORKFORCE COST ELEVATION</span>
        </div>

        <div class="bp-period">
            <div class="seg">
                <div class="k">Pay Period</div>
                <div class="v">{esc(period.get('label', month_name(period.get('month', 1))))}</div>
            </div>
            <div class="seg">
                <div class="k">Fiscal Year</div>
                <div class="v">FY {esc(period.get('fy', ''))}</div>
            </div>
            <div class="seg">
                <div class="k">Total Pay Cost</div>
                <div class="v">{inr(total_cost)}</div>
            </div>
        </div>

        <div class="bp-gen">GENERATED {esc(now_stamp().upper())} &middot; {esc(employees)} EMPLOYEES ON RECORD</div>

        {tiles}
      </div>

      {dist}
      {title_block}

      <div class="bp-footer">
        <span class="conf">Confidential</span> &middot; Internal use only &middot;
        {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])} &middot;
        Reproduction without authorisation is prohibited.
      </div>
    </section>
    """
