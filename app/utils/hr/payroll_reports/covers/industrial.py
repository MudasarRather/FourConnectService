"""PDF cover — motif "industrial" (Department Cost).

A dark cost-control OPS-ROOM dashboard. Charcoal stock (#0c0a09), a square
grid overlay, LED / seven-segment big figures, a CSS gauge arc for the
employer-cost load factor, and a conveyor of department cost bars built from
the actual shaped totals. Orange industrial accent, monospace numerics.

This is the single dark cover in the payroll-report family. Everything is
embedded as CSS/HTML — no external images or fonts. Print-safe units (mm/pt).
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, inr_group, kpi_tiles_html,
    now_stamp, month_name,
)


def _gauge_arc(pct: float, accent: str, track: str) -> str:
    """A 180° gauge built from two conic-gradient halves clipped to a band.

    ``pct`` is 0..100 — the share of the half-circle the accent sweep fills.
    WeasyPrint supports conic-gradient; we draw a full ring and mask the lower
    half + the centre to leave a thick top arc.
    """
    pct = max(0.0, min(100.0, float(pct)))
    # 180deg total sweep -> map pct to degrees on the visible top half
    sweep = 180.0 * (pct / 100.0)
    # The ring starts at the 9-o'clock position (270deg) going clockwise.
    fill_end = 270.0 + sweep
    grad = (
        f"conic-gradient(from 270deg,"
        f"{accent} 0deg,{accent} {sweep:.1f}deg,"
        f"{track} {sweep:.1f}deg,{track} 180deg,"
        f"transparent 180deg,transparent 360deg)"
    )
    return grad


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#ea580c")
    soft = meta.get("accent_soft", "#ffedd5")
    deep = meta.get("accent_deep", "#7c2d12")
    icon = meta.get("icon", "▦")
    name = meta.get("name", "Department Cost")
    subtitle = meta.get("subtitle", "")
    group = meta.get("group", "Analytics")

    # ── figures ──────────────────────────────────────────────────────────────
    rows = shaped_count if shaped_count is not None else int(summary.get("rows", 0) or 0)
    employees = int(summary.get("employees", summary.get("headcount", 0)) or 0)
    gross = float(summary.get("gross", 0) or 0)
    net = float(summary.get("net", 0) or 0)
    employer_cost = float(summary.get("employer_cost", 0) or 0)
    total_cost = float(summary.get("total_cost", 0) or 0)

    # employer-cost load factor: employer add-on as a share of gross payroll
    load_pct = (employer_cost / gross * 100.0) if gross else 0.0
    # avg cost per head — the headline "unit cost" of the ops floor
    cost_per_head = (total_cost / employees) if employees else 0.0

    gauge_grad = _gauge_arc(load_pct, accent, "#211c18")

    # ── department conveyor bars ───────────────────────────────────────────
    # Pull a per-department breakdown if the summary carries one; otherwise the
    # cover still renders cleanly without bars. The shared body table always
    # follows on page 2 with the full breakdown.
    dept_rows = summary.get("departments") or summary.get("rows_preview") or []
    bars_html = ""
    if isinstance(dept_rows, list) and dept_rows:
        items = []
        for d in dept_rows:
            if not isinstance(d, dict):
                continue
            items.append((
                str(d.get("department") or d.get("name") or "—"),
                float(d.get("total_cost") or d.get("gross") or 0),
            ))
        items = [i for i in items if i[1] > 0][:6]
        if items:
            peak = max(v for _, v in items) or 1.0
            track = []
            for nm, val in items:
                w = max(6.0, val / peak * 100.0)
                track.append(f"""
                <div class="cv-row">
                  <div class="cv-name">{esc(nm[:18])}</div>
                  <div class="cv-track">
                    <div class="cv-fill" style="width:{w:.1f}%"></div>
                  </div>
                  <div class="cv-val">{esc(inr_compact(val))}</div>
                </div>""")
            bars_html = (
                '<div class="cv-head">DEPARTMENT COST CONVEYOR</div>'
                '<div class="cv-wrap">' + "".join(track) + "</div>"
            )
    if not bars_html:
        # graceful fallback strip: a static set of segments so the panel never
        # looks empty even when no per-dept preview is supplied.
        seg = "".join(
            f'<span class="cv-seg" style="opacity:{0.25 + i * 0.12:.2f}"></span>'
            for i in range(6)
        )
        bars_html = (
            '<div class="cv-head">COST-CENTRE THROUGHPUT</div>'
            f'<div class="cv-strip">{seg}</div>'
            f'<div class="cv-foot">{esc(rows)} cost centres · full breakdown overleaf</div>'
        )

    # ── KPI strip (shared helper) ──────────────────────────────────────────
    tiles = [
        ("Cost Centres", f'{rows}', accent),
        ("Headcount", f'{employees}', "#e7e2da"),
        ("Total Cost", f'<span>{inr_compact(total_cost)}</span>', "#fb923c"),
        ("Cost / Head", f'<span>{inr_compact(cost_per_head)}</span>', "#34d399"),
    ]
    kpi = kpi_tiles_html(tiles)

    # seven-segment headline number = total cost, Indian-grouped
    led_total = inr_group(total_cost)
    net_pct = (net / gross * 100.0) if gross else 0.0

    ref = f"FRC/PAY/DEPTCOST/{period.get('year','')}/{period.get('month','')}"

    return f"""
<section class="cover cover-industrial">
  <style>
    .cover-industrial {{
      background:#0c0a09;
      color:#e7e2da;
      font-family:'Courier New','Helvetica',monospace;
      padding:0;
    }}
    /* square engineering grid + faint diagonal hatch */
    .cover-industrial .ind-grid {{
      position:absolute; top:0; left:0; right:0; bottom:0;
      background-image:
        repeating-linear-gradient(0deg, rgba(234,88,12,0.07) 0 0.2mm, transparent 0.2mm 9mm),
        repeating-linear-gradient(90deg, rgba(234,88,12,0.07) 0 0.2mm, transparent 0.2mm 9mm);
    }}
    .cover-industrial .ind-hatch {{
      position:absolute; top:0; left:0; right:0; bottom:0;
      background-image:repeating-linear-gradient(135deg, rgba(255,255,255,0.018) 0 2mm, transparent 2mm 6mm);
    }}
    .cover-industrial .ind-vignette {{
      position:absolute; top:0; left:0; right:0; bottom:0;
      background:radial-gradient(120% 80% at 50% -10%, rgba(234,88,12,0.16), transparent 60%);
    }}
    /* top + bottom industrial rails */
    .cover-industrial .ind-rail-top {{
      position:absolute; top:0; left:0; right:0; height:13mm;
      background:linear-gradient(90deg,{accent},{deep});
      border-bottom:0.6mm solid #1a1410;
    }}
    .cover-industrial .ind-rail-top .stud {{
      position:absolute; top:5mm; width:1.6mm; height:1.6mm; border-radius:50%;
      background:rgba(12,10,9,0.55);
    }}
    .cover-industrial .ind-caution {{
      position:absolute; bottom:0; left:0; right:0; height:5mm;
      background:repeating-linear-gradient(45deg,{accent} 0 5mm,#1a1410 5mm 10mm);
      opacity:0.9;
    }}

    .cover-industrial .ind-inner {{ position:relative; padding:20mm 18mm 14mm; }}

    /* masthead */
    .cover-industrial .ind-brand {{ display:flex; align-items:center; gap:5mm; margin-top:2mm; }}
    .cover-industrial .ind-crest {{
      width:15mm; height:15mm; border:0.7mm solid {accent};
      background:linear-gradient(135deg,{deep},#0c0a09);
      color:{accent}; font-size:16pt; font-weight:900;
      text-align:center; line-height:15mm;
      box-shadow:0 0 0 0.6mm rgba(234,88,12,0.25), inset 0 0 4mm rgba(234,88,12,0.18);
    }}
    .cover-industrial .ind-brand .co {{
      font-size:7.5pt; letter-spacing:3pt; font-weight:700; color:#8a8170; text-transform:uppercase;
    }}
    .cover-industrial .ind-brand .co b {{ color:#e7e2da; display:block; font-size:9.5pt; letter-spacing:2pt; margin-top:1mm; }}
    .cover-industrial .ind-status {{
      margin-left:auto; text-align:right; font-size:7pt; letter-spacing:1.5pt; color:#8a8170;
    }}
    .cover-industrial .ind-status .live {{
      display:inline-block; width:2mm; height:2mm; border-radius:50%;
      background:{accent}; box-shadow:0 0 2mm {accent}; margin-right:1.5mm;
    }}

    .cover-industrial .ind-eyebrow {{
      margin-top:14mm; font-size:8pt; letter-spacing:5pt; font-weight:800;
      color:{accent}; text-transform:uppercase;
    }}
    .cover-industrial .ind-title {{
      font-size:40pt; font-weight:900; line-height:0.98; letter-spacing:-1pt;
      margin:2mm 0 2mm; color:#fff; text-transform:uppercase;
      font-family:'Helvetica','Arial',sans-serif;
    }}
    .cover-industrial .ind-sub {{
      font-size:9.5pt; color:#b9b1a4; letter-spacing:0.4pt; font-family:'Helvetica',sans-serif;
      max-width:120mm;
    }}

    /* console: gauge + LED readout */
    .cover-industrial .ind-console {{
      margin-top:12mm; display:flex; gap:7mm; align-items:stretch;
    }}
    .cover-industrial .ind-panel {{
      border:0.5mm solid #2a2118; background:rgba(20,16,12,0.6);
      padding:6mm 6mm 5mm; position:relative;
    }}
    .cover-industrial .ind-panel .ptag {{
      position:absolute; top:-2.4mm; left:5mm; background:#0c0a09; padding:0 2mm;
      font-size:6.5pt; letter-spacing:2pt; color:{accent}; font-weight:800;
    }}
    /* gauge */
    .ind-gauge-box {{ width:60mm; text-align:center; }}
    .ind-gauge {{
      width:46mm; height:23mm; margin:3mm auto 0; position:relative; overflow:hidden;
    }}
    .ind-gauge .ring {{
      width:46mm; height:46mm; border-radius:50%;
      background:{gauge_grad};
    }}
    .ind-gauge .hole {{
      position:absolute; top:9mm; left:9mm; width:28mm; height:28mm; border-radius:50%;
      background:#0c0a09; border:0.4mm solid #2a2118;
    }}
    .ind-gauge-read {{ margin-top:-9mm; position:relative; z-index:2; }}
    .ind-gauge-read .big {{
      font-size:21pt; font-weight:900; color:{accent}; letter-spacing:-0.5pt;
      font-family:'Courier New',monospace;
    }}
    .ind-gauge-read .lbl {{ font-size:6.5pt; letter-spacing:2pt; color:#8a8170; margin-top:0.5mm; }}
    .ind-gauge-foot {{ margin-top:4mm; font-size:6.8pt; letter-spacing:1pt; color:#8a8170; }}

    /* LED readout */
    .ind-led-box {{ flex:1; }}
    .ind-led-label {{ font-size:6.8pt; letter-spacing:3pt; color:#8a8170; margin-bottom:2mm; }}
    .ind-led {{
      font-family:'Courier New',monospace; font-weight:900;
      font-size:33pt; letter-spacing:1pt; line-height:1;
      color:{accent}; text-shadow:0 0 3mm rgba(234,88,12,0.45);
    }}
    .ind-led .unit {{ font-size:13pt; color:#e7e2da; text-shadow:none; }}
    .ind-led-sub {{
      margin-top:4mm; display:flex; gap:8mm; font-family:'Helvetica',sans-serif;
    }}
    .ind-led-sub .it {{ }}
    .ind-led-sub .k {{ font-size:6.5pt; letter-spacing:1.5pt; color:#8a8170; text-transform:uppercase; }}
    .ind-led-sub .v {{ font-size:11pt; font-weight:800; color:#e7e2da; margin-top:0.8mm; font-family:'Courier New',monospace; }}
    .ind-led-sub .v.net {{ color:#34d399; }}

    /* conveyor */
    .cover-industrial .cv-wrap {{ margin-top:9mm; }}
    .cover-industrial .cv-head {{
      font-size:7pt; letter-spacing:3pt; color:{accent}; font-weight:800; margin-top:9mm; margin-bottom:3mm;
    }}
    .cover-industrial .cv-row {{ display:flex; align-items:center; gap:3mm; margin-bottom:2.4mm; }}
    .cover-industrial .cv-name {{
      width:34mm; font-size:7.5pt; color:#cfc8bc; font-family:'Helvetica',sans-serif;
      font-weight:700; letter-spacing:0.3pt; text-transform:uppercase; white-space:nowrap; overflow:hidden;
    }}
    .cover-industrial .cv-track {{
      flex:1; height:4.4mm; background:#1a1410; border:0.3mm solid #2a2118; position:relative; overflow:hidden;
    }}
    .cover-industrial .cv-fill {{
      height:100%;
      background:repeating-linear-gradient(90deg,{accent} 0 3mm,{deep} 3mm 4mm);
      box-shadow:inset 0 0 2mm rgba(0,0,0,0.4);
    }}
    .cover-industrial .cv-val {{
      width:24mm; text-align:right; font-size:8pt; font-weight:900; color:{accent};
      font-family:'Courier New',monospace;
    }}
    .cover-industrial .cv-strip {{ display:flex; gap:2mm; margin-top:2mm; }}
    .cover-industrial .cv-seg {{ flex:1; height:5mm; background:{accent}; }}
    .cover-industrial .cv-foot {{ margin-top:2.5mm; font-size:7pt; color:#8a8170; letter-spacing:0.6pt; }}

    /* KPI strip override for dark stock */
    .cover-industrial .kpi-grid {{ margin-top:11mm; }}
    .cover-industrial .kpi-tile {{
      background:rgba(20,16,12,0.75); border:0.4mm solid #2a2118;
      box-shadow:none;
    }}
    .cover-industrial .kpi-label {{ color:#8a8170; }}
    .cover-industrial .kpi-value {{ font-family:'Courier New',monospace; }}

    /* period band */
    .cover-industrial .ind-period {{
      margin-top:11mm; display:flex; justify-content:space-between; align-items:center;
      border:0.5mm solid {accent}; background:rgba(234,88,12,0.06);
      padding:5mm 7mm;
    }}
    .cover-industrial .ind-period .k {{ font-size:6.8pt; letter-spacing:2pt; color:{accent}; font-weight:800; }}
    .cover-industrial .ind-period .v {{ font-size:13pt; font-weight:900; color:#fff; margin-top:1mm; }}
    .cover-industrial .ind-period .arrow {{ color:{accent}; font-size:15pt; }}
    .cover-industrial .ind-gen {{ margin-top:5mm; font-size:7.5pt; color:#8a8170; letter-spacing:0.6pt; }}

    /* footer */
    .cover-industrial .ind-foot {{
      position:absolute; left:18mm; right:18mm; bottom:9mm;
      border-top:0.4mm solid #2a2118; padding-top:3mm;
      font-size:7pt; color:#8a8170; font-family:'Helvetica',sans-serif;
      display:flex; justify-content:space-between; align-items:flex-end;
    }}
    .cover-industrial .ind-foot .conf {{
      letter-spacing:2pt; text-transform:uppercase; color:{accent}; font-weight:800; font-size:6.8pt;
    }}
    .cover-industrial .ind-foot .leg b {{ color:#cfc8bc; }}
  </style>

  <div class="ind-grid"></div>
  <div class="ind-hatch"></div>
  <div class="ind-vignette"></div>
  <div class="ind-rail-top">
    <span class="stud" style="left:8mm"></span><span class="stud" style="left:18mm"></span>
    <span class="stud" style="right:8mm"></span><span class="stud" style="right:18mm"></span>
  </div>
  <div class="ind-caution"></div>

  <div class="ind-inner">
    <div class="ind-brand">
      <div class="ind-crest">{esc(icon)}</div>
      <div class="co">{esc(COMPANY['legal'].upper())}
        <b>{esc(COMPANY['name'])} · PAYROLL OPS</b>
      </div>
      <div class="ind-status">
        <div><span class="live"></span>COST CONTROL · ONLINE</div>
        <div style="margin-top:1.5mm">REF {esc(ref)}</div>
      </div>
    </div>

    <div class="ind-eyebrow">PAYROLL · {esc(group.upper())}</div>
    <h1 class="ind-title">{esc(name)}</h1>
    <p class="ind-sub">{esc(subtitle)}</p>

    <div class="ind-console">
      <div class="ind-panel ind-gauge-box">
        <span class="ptag">LOAD FACTOR</span>
        <div class="ind-gauge">
          <div class="ring"></div>
          <div class="hole"></div>
        </div>
        <div class="ind-gauge-read">
          <div class="big">{load_pct:.1f}%</div>
          <div class="lbl">EMPLOYER ADD-ON / GROSS</div>
        </div>
        <div class="ind-gauge-foot">Employer cost {inr_compact(employer_cost)}</div>
      </div>

      <div class="ind-panel ind-led-box">
        <span class="ptag">TOTAL COST-TO-COMPANY</span>
        <div class="ind-led-label">FULLY-LOADED PAYROLL · {esc(period.get('label',''))}</div>
        <div class="ind-led"><span class="unit">₹</span>{esc(led_total)}</div>
        <div class="ind-led-sub">
          <div class="it"><div class="k">Gross</div><div class="v">{esc(inr_group(gross))}</div></div>
          <div class="it"><div class="k">Net Disbursed</div><div class="v net">{esc(inr_group(net))}</div></div>
          <div class="it"><div class="k">Take-home %</div><div class="v">{net_pct:.0f}%</div></div>
        </div>
        {bars_html}
      </div>
    </div>

    {kpi}

    <div class="ind-period">
      <div>
        <div class="k">PAY PERIOD</div>
        <div class="v">{esc(period.get('label',''))}</div>
      </div>
      <div class="arrow">⟶</div>
      <div style="text-align:right">
        <div class="k">FISCAL YEAR</div>
        <div class="v">{esc(period.get('fy',''))}</div>
      </div>
    </div>
    <div class="ind-gen">GENERATED {esc(now_stamp())} · SYSTEM-OF-RECORD EXPORT · UNAUDITED</div>
  </div>

  <div class="ind-foot">
    <div class="leg">
      <b>{esc(COMPANY['legal'])}</b> · {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])}<br>
      CIN {esc(COMPANY['cin'])} · GSTIN {esc(COMPANY['gst'])} · {esc(COMPANY['web'])}
    </div>
    <div class="conf">Confidential · Internal</div>
  </div>
</section>
"""
