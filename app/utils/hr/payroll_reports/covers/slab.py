"""Professional Tax cover — motif "slab".

A STATE PROFESSIONAL-TAX SLAB motif: an ascending CSS "staircase" of slab tiers
(Rs0 · Rs150 · Rs200), a rotated municipal rubber-stamp (dashed amber circle),
and a state-wise civic framing in amber / ochre tones. Communicates the core
idea of the report — "tax by slab, by state".

Self-contained: every selector is scoped under ``.cover-slab`` so nothing leaks
into the shared body table. mm / pt units only (WeasyPrint print rules).
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, inr_group, kpi_tiles_html,
    period_band_html, chips_html, now_stamp, fmt_date, fmt_long_date, month_name,
)


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#a16207")
    soft = meta.get("accent_soft", "#fef9c3")
    deep = meta.get("accent_deep", "#713f12")

    rows = shaped_count if shaped_count is not None else summary.get("rows", 0)
    employees = summary.get("employees", 0)
    pt_total = summary.get("pt", 0.0)
    avg_pt = (pt_total / employees) if employees else 0.0

    # KPI strip — 4 tiles, statutory-document feel.
    tiles = [
        ("PT Members", str(employees), accent),
        ("Slab Rows", str(rows), "#1a1410"),
        ("PT Remitted", inr_compact(pt_total), deep),
        ("Avg / Head", inr_compact(avg_pt), "#92400e"),
    ]

    # State chips — civic framing.
    chips = chips_html([
        ("STATE SLAB", soft, deep),
        ("STATUTORY FILING", "#1a1410", soft),
        (f"FY {period.get('fy', '')}", soft, deep),
    ])

    # Ascending slab staircase — three civic PT tiers (Rs0 / Rs150 / Rs200).
    # Heights are absolute mm so the steps read as a clean municipal bar-chart.
    slabs = [
        ("UP TO ₹15K", "₹0", "#e8d9a8", 16.0),
        ("₹15K–₹20K", "₹150", "#cda43e", 30.0),
        ("ABOVE ₹20K", "₹200", accent, 46.0),
    ]
    bars = []
    for i, (band, amt, col, h) in enumerate(slabs):
        left = 4.0 + i * 26.0
        bars.append(f"""
            <div class="slab-step" style="left:{left}mm;height:{h}mm;
                 background:linear-gradient(180deg,{col},{deep});">
                <div class="slab-amt">{esc(amt)}</div>
            </div>
            <div class="slab-band" style="left:{left}mm">{esc(band)}</div>
        """)
    staircase = "".join(bars)

    gen = now_stamp()
    legal = COMPANY["legal"]
    addr = f"{COMPANY['address_1']}, {COMPANY['address_2']}"
    tan = COMPANY.get("tan", "")
    pan = COMPANY.get("pan", "")

    return f"""
    <section class="cover cover-slab">
      <style>
        .cover-slab {{ width:210mm; height:297mm; padding:0; margin:0;
            position:relative; overflow:hidden; page-break-after:always;
            background:
              repeating-linear-gradient(135deg, {soft}00 0mm, {soft}00 9mm, {soft}55 9mm, {soft}55 9.5mm),
              linear-gradient(170deg, #fffdf5 0%, {soft} 100%);
            font-family:'Helvetica','Arial',sans-serif; color:#1a1410; }}

        /* top + bottom civic bands */
        .cover-slab .cs-top {{ position:absolute; top:0; left:0; right:0; height:16mm;
            background:linear-gradient(90deg,{deep},{accent} 60%,{deep});
            border-bottom:1.4mm solid {soft}; }}
        .cover-slab .cs-top-rule {{ position:absolute; top:16mm; left:0; right:0; height:1.2mm;
            background:repeating-linear-gradient(90deg,{accent} 0,{accent} 4mm,transparent 4mm,transparent 8mm); }}
        .cover-slab .cs-bottom {{ position:absolute; bottom:0; left:0; right:0; height:10mm;
            background:linear-gradient(90deg,{deep},{accent} 60%,{deep}); }}
        .cover-slab .cs-bottom-rule {{ position:absolute; bottom:10mm; left:0; right:0; height:0.8mm;
            background:repeating-linear-gradient(90deg,{accent} 0,{accent} 4mm,transparent 4mm,transparent 8mm); }}

        /* masthead */
        .cover-slab .cs-brand {{ position:absolute; top:5mm; left:18mm; right:18mm;
            display:flex; align-items:center; justify-content:space-between; }}
        .cover-slab .cs-crest {{ width:11mm; height:11mm; border-radius:2.2mm;
            background:{soft}; color:{deep}; font-size:15pt; font-weight:900;
            text-align:center; line-height:11mm;
            border:0.8pt solid {soft}; box-shadow:0 0 0 1.2pt {accent} inset; }}
        .cover-slab .cs-co {{ font-size:8pt; letter-spacing:3pt; font-weight:800;
            color:{soft}; text-transform:uppercase; }}
        .cover-slab .cs-co small {{ display:block; font-size:6.4pt; letter-spacing:1.4pt;
            color:{soft}; opacity:0.78; font-weight:600; margin-top:0.8mm; }}
        .cover-slab .cs-ids {{ text-align:right; font-size:6.2pt; letter-spacing:0.8pt;
            color:{soft}; opacity:0.9; font-weight:700; line-height:1.5; }}

        /* hero */
        .cover-slab .cs-hero {{ position:absolute; top:34mm; left:18mm; right:18mm; }}
        .cover-slab .cs-eyebrow {{ font-size:8pt; letter-spacing:3.4pt; font-weight:800;
            color:{accent}; text-transform:uppercase; margin-bottom:3mm; }}
        .cover-slab .cs-eyebrow:before {{ content:"◼  "; color:{accent}; }}
        .cover-slab .cs-title {{ font-size:42pt; font-weight:900; line-height:0.98;
            letter-spacing:-1pt; margin:0; color:#1a1410; }}
        .cover-slab .cs-title b {{ color:{deep}; }}
        .cover-slab .cs-tagline {{ margin:3mm 0 0; font-size:12pt; font-style:italic;
            color:{deep}; letter-spacing:0.1pt; }}
        .cover-slab .cs-sub {{ margin:2mm 0 0; font-size:9.5pt; color:#5b4a30;
            max-width:120mm; line-height:1.45; }}

        /* slab staircase art */
        .cover-slab .cs-stage {{ position:absolute; top:96mm; left:18mm;
            width:90mm; height:56mm; }}
        .cover-slab .cs-stage-base {{ position:absolute; bottom:8mm; left:0; width:84mm;
            height:0.9mm; background:{deep}; }}
        .cover-slab .slab-step {{ position:absolute; bottom:8mm; width:22mm;
            border:0.8pt solid {deep}; border-bottom:none;
            box-shadow:0 1pt 3pt rgba(113,63,18,0.25); }}
        .cover-slab .slab-amt {{ position:absolute; top:-7mm; left:0; right:0;
            text-align:center; font-size:11pt; font-weight:900; color:{deep};
            font-variant-numeric:tabular-nums; }}
        .cover-slab .slab-band {{ position:absolute; bottom:0.5mm; width:22mm;
            text-align:center; font-size:5.6pt; font-weight:800; letter-spacing:0.5pt;
            color:{soft}; text-transform:uppercase; }}
        .cover-slab .cs-stage-cap {{ position:absolute; bottom:1.5mm; left:0;
            font-size:6.4pt; letter-spacing:1.6pt; font-weight:800; color:{deep};
            text-transform:uppercase; opacity:0.85; }}

        /* municipal rubber stamp */
        .cover-slab .cs-stamp {{ position:absolute; top:100mm; right:20mm;
            width:44mm; height:44mm; border-radius:50%;
            border:1.4mm double {accent}; color:{accent};
            transform:rotate(-13deg); opacity:0.92;
            background:radial-gradient(circle, {soft}40 0%, transparent 72%); }}
        .cover-slab .cs-stamp-inner {{ position:absolute; top:5mm; left:5mm; right:5mm; bottom:5mm;
            border-radius:50%; border:0.6mm dashed {accent};
            display:flex; flex-direction:column; align-items:center; justify-content:center; }}
        .cover-slab .cs-stamp-top {{ font-size:6pt; letter-spacing:1.4pt; font-weight:800;
            text-transform:uppercase; }}
        .cover-slab .cs-stamp-big {{ font-size:15pt; font-weight:900; letter-spacing:0.5pt;
            line-height:1; margin:1mm 0; }}
        .cover-slab .cs-stamp-bot {{ font-size:5.4pt; letter-spacing:1pt; font-weight:700;
            text-transform:uppercase; opacity:0.85; }}
        .cover-slab .cs-stamp-star {{ font-size:7pt; margin:0.6mm 0; }}

        /* period band */
        .cover-slab .cs-period {{ position:absolute; top:166mm; left:18mm; right:18mm; }}

        /* generated stamp */
        .cover-slab .cs-gen {{ position:absolute; top:188mm; left:18mm; right:18mm;
            text-align:center; font-size:8.5pt; color:#6b5a3a; letter-spacing:0.4pt; }}
        .cover-slab .cs-gen b {{ color:{deep}; }}

        /* KPI strip */
        .cover-slab .cs-kpis {{ position:absolute; top:200mm; left:18mm; right:18mm; }}
        .cover-slab .kpi-grid {{ width:auto; margin:0; }}

        /* chips */
        .cover-slab .cs-chips {{ position:absolute; top:248mm; left:18mm; right:18mm; }}
        .cover-slab .chip-row {{ width:auto; margin:0; justify-content:flex-start; }}

        /* footer */
        .cover-slab .cs-foot {{ position:absolute; bottom:13mm; left:18mm; right:18mm;
            text-align:center; }}
        .cover-slab .cs-foot .legal {{ font-size:7.4pt; font-weight:700; color:#7a6a4a;
            letter-spacing:0.4pt; }}
        .cover-slab .cs-foot .conf {{ margin-top:1.4mm; font-size:6.8pt; letter-spacing:2.4pt;
            font-weight:800; text-transform:uppercase; color:{accent}; }}
      </style>

      <div class="cs-top"></div>
      <div class="cs-top-rule"></div>
      <div class="cs-bottom"></div>
      <div class="cs-bottom-rule"></div>

      <div class="cs-brand">
        <div style="display:flex;align-items:center;gap:4mm;">
          <span class="cs-crest">{esc(meta.get('icon', 'T'))}</span>
          <span class="cs-co">{esc(COMPANY['name'].upper())}
            <small>{esc(legal)}</small></span>
        </div>
        <div class="cs-ids">
          TAN&nbsp;{esc(tan)}<br>PAN&nbsp;{esc(pan)}
        </div>
      </div>

      <div class="cs-hero">
        <div class="cs-eyebrow">Payroll &middot; {esc(meta.get('group', 'Statutory Filing').upper())}</div>
        <h1 class="cs-title">Professional<br><b>Tax</b></h1>
        <div class="cs-tagline">{esc(meta.get('tagline', 'State-wise PT remittance'))}</div>
        <p class="cs-sub">{esc(meta.get('subtitle', 'PT deducted, grouped by work-location state slab'))}</p>
      </div>

      <div class="cs-stage">
        <div class="cs-stage-base"></div>
        {staircase}
        <div class="cs-stage-cap">Indicative civic slab tiers</div>
      </div>

      <div class="cs-stamp">
        <div class="cs-stamp-inner">
          <div class="cs-stamp-top">Municipal</div>
          <div class="cs-stamp-star">&#9733;</div>
          <div class="cs-stamp-big">P&middot;T</div>
          <div class="cs-stamp-bot">{esc(period.get('short', period.get('label', '')))}</div>
        </div>
      </div>

      <div class="cs-period">
        {period_band_html(period, accent, soft, deep)}
      </div>

      <div class="cs-gen">Generated <b>{esc(gen)}</b> &middot; {esc(rows)} slab record(s) across states</div>

      <div class="cs-kpis">
        {kpi_tiles_html(tiles)}
      </div>

      <div class="cs-chips">
        {chips}
      </div>

      <div class="cs-foot">
        <div class="legal">{esc(legal)} &middot; {esc(addr)}</div>
        <div class="conf">Confidential &middot; Statutory remittance &middot; Internal use only</div>
      </div>
    </section>
    """
