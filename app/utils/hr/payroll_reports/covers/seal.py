"""Cover motif "seal" — a formal STATUTORY COMPLIANCE CERTIFICATE.

A large embossed CSS seal (radial-gradient disc + concentric ring borders +
rim tick marks + centre monogram), a guilloche-style fine border woven from
repeating-linear-gradients, and a ribbon banner reading "STATUTORY · CERTIFIED".
Teal + bronze. Built entirely from CSS/HTML — no external images or fonts.

Public entry point:
    render(meta, summary, period, shaped_count=None) -> str  (one <section>)
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, inr_group, kpi_tiles_html,
    period_band_html, chips_html, now_stamp, fmt_date, fmt_long_date, month_name,
)

# Bronze companions to the teal accent (seal/wax/embossing tones).
_BRONZE = "#9a6b2f"
_BRONZE_DEEP = "#6b4a1f"
_BRONZE_SOFT = "#f3e6cf"


def _seal_disc(accent: str, deep: str, icon: str) -> str:
    """The embossed circular seal: disc + 2 ring borders + 40 rim ticks + monogram."""
    # 40 tick marks evenly around the rim, rendered as thin bars rotated about centre.
    ticks = "".join(
        f'<span class="cs-tick" style="transform:rotate({i * 9}deg) translateY(-21mm)">'
        f'</span>'
        for i in range(40)
    )
    # A ring of certification words around the inner band.
    arc_words = " ✦ STATUTORY COMPLIANCE ✦ CERTIFIED FOR FILING "
    return f"""
    <div class="cs-seal">
        <div class="cs-seal-glow"></div>
        <div class="cs-ring-outer"></div>
        <div class="cs-ticks">{ticks}</div>
        <div class="cs-ring-inner"></div>
        <div class="cs-bandtext">{esc(arc_words)}</div>
        <div class="cs-core">
            <div class="cs-monogram">{esc(icon)}</div>
            <div class="cs-core-sub">FRC</div>
        </div>
    </div>
    """


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#0d9488")
    soft = meta.get("accent_soft", "#ccfbf1")
    deep = meta.get("accent_deep", "#134e4a")
    icon = meta.get("icon", "✓")
    name = meta.get("name", "Statutory Summary")
    subtitle = meta.get("subtitle", "")

    rows = shaped_count if shaped_count is not None else summary.get("rows", summary.get("employees", 0))
    employees = summary.get("employees", summary.get("rows", rows))
    pf = float(summary.get("pf", 0) or 0)
    esi = float(summary.get("esi", 0) or 0)
    pt = float(summary.get("pt", 0) or 0)
    tds = float(summary.get("tds", 0) or 0)
    total_remit = pf + esi + pt + tds

    tiles = [
        ("Employees Covered", esc(str(employees)), accent),
        ("PF Remittance", inr_compact(pf), accent),
        ("PT + ESI", inr_compact(pt + esi), _BRONZE),
        ("TDS Deducted", inr_compact(tds), "#b91c1c"),
    ]

    chips = [
        ("EPFO · ECR Ready", soft, deep),
        ("ESIC Compliant", _BRONZE_SOFT, _BRONZE_DEEP),
        ("Form 24Q · TDS", "#fee2e2", "#7f1d1d"),
        ("State PT", soft, deep),
    ]

    fy_label = esc(period.get("fy", ""))
    period_label = esc(period.get("label", ""))

    return f"""
    <section class="cover cover-seal">
      <style>
        .cover-seal {{
            background:
                radial-gradient(120mm 90mm at 50% 30%, {soft}55, transparent 70%),
                linear-gradient(180deg, #fffdf9 0%, #fbf7ee 100%);
            font-family:'Helvetica','Arial',sans-serif;
        }}
        /* ── Guilloche fine border woven from layered repeating gradients ── */
        .cs-guilloche {{
            position:absolute; top:7mm; left:7mm; right:7mm; bottom:7mm;
            border:0.5mm solid {accent}; border-radius:2mm;
            background:
                repeating-linear-gradient(45deg, {accent}14 0, {accent}14 0.5mm, transparent 0.5mm, transparent 2.4mm),
                repeating-linear-gradient(-45deg, {_BRONZE}12 0, {_BRONZE}12 0.5mm, transparent 0.5mm, transparent 2.4mm);
            background-clip:border-box;
        }}
        .cs-guilloche::before {{
            content:""; position:absolute; top:1.6mm; left:1.6mm; right:1.6mm; bottom:1.6mm;
            border:0.3mm solid {_BRONZE}; border-radius:1.4mm;
        }}
        .cs-corner {{
            position:absolute; width:14mm; height:14mm; border:0.6mm solid {deep};
        }}
        .cs-corner.tl {{ top:10mm; left:10mm; border-right:none; border-bottom:none; }}
        .cs-corner.tr {{ top:10mm; right:10mm; border-left:none; border-bottom:none; }}
        .cs-corner.bl {{ bottom:10mm; left:10mm; border-right:none; border-top:none; }}
        .cs-corner.br {{ bottom:10mm; right:10mm; border-left:none; border-top:none; }}
        .cs-corner::after {{
            content:"✦"; position:absolute; color:{_BRONZE}; font-size:8pt;
        }}
        .cs-corner.tl::after {{ top:-2mm; left:-2mm; }}
        .cs-corner.tr::after {{ top:-2mm; right:-2mm; }}
        .cs-corner.bl::after {{ bottom:-2mm; left:-2mm; }}
        .cs-corner.br::after {{ bottom:-2mm; right:-2mm; }}

        /* ── Top brand band ── */
        .cs-top {{ position:absolute; top:14mm; left:0; right:0; text-align:center; }}
        .cs-crest {{
            display:inline-block; width:13mm; height:13mm; border-radius:50%;
            line-height:13mm; text-align:center; font-size:14pt; font-weight:900; color:#fff;
            background:radial-gradient(circle at 35% 30%, {accent}, {deep});
            box-shadow:0 0.6mm 1.6mm rgba(19,78,74,0.35);
            border:0.5mm solid #fff;
        }}
        .cs-company {{
            margin-top:3mm; font-size:8pt; letter-spacing:3.5pt; font-weight:800;
            color:{deep}; text-transform:uppercase;
        }}
        .cs-reg {{
            margin-top:1.5mm; font-size:6.6pt; letter-spacing:1pt; color:{_BRONZE_DEEP};
        }}

        /* ── Eyebrow + title ── */
        .cs-eyebrow {{
            position:absolute; top:42mm; left:0; right:0; text-align:center;
            font-size:8pt; letter-spacing:5pt; font-weight:800; color:{_BRONZE};
            text-transform:uppercase;
        }}
        .cs-title {{
            position:absolute; top:48mm; left:0; right:0; text-align:center;
            font-family:Georgia,'Times New Roman',serif;
            font-size:33pt; font-weight:900; color:{deep};
            letter-spacing:-0.4pt; line-height:1.04; margin:0;
        }}
        .cs-rule {{
            position:absolute; top:64mm; left:50%; width:46mm; height:0;
            margin-left:-23mm; border-top:0.4mm solid {_BRONZE};
        }}
        .cs-rule::before, .cs-rule::after {{
            content:"◆"; position:absolute; top:-2.6mm; color:{accent}; font-size:7pt;
        }}
        .cs-rule::before {{ left:-5mm; }}
        .cs-rule::after {{ right:-5mm; }}
        .cs-subtitle {{
            position:absolute; top:67mm; left:0; right:0; text-align:center;
            font-style:italic; font-size:10.5pt; color:#5b5345;
        }}

        /* ── The embossed seal ── */
        .cs-seal {{
            position:absolute; top:80mm; left:50%; width:54mm; height:54mm;
            margin-left:-27mm;
        }}
        .cs-seal-glow {{
            position:absolute; top:-3mm; left:-3mm; right:-3mm; bottom:-3mm;
            border-radius:50%;
            background:radial-gradient(circle, {soft}aa, transparent 70%);
        }}
        .cs-ring-outer {{
            position:absolute; top:0; left:0; width:54mm; height:54mm; border-radius:50%;
            background:radial-gradient(circle at 38% 32%, #ffffff, {soft} 55%, {accent}33 100%);
            border:1.4mm solid {accent};
            box-shadow:inset 0 0 3mm {accent}55, 0 0.8mm 2mm rgba(19,78,74,0.25);
        }}
        .cs-ticks {{
            position:absolute; top:0; left:0; width:54mm; height:54mm;
        }}
        .cs-tick {{
            position:absolute; top:27mm; left:50%; width:0.5mm; height:2.4mm;
            margin-left:-0.25mm; background:{deep}; transform-origin:50% 21mm;
        }}
        .cs-ring-inner {{
            position:absolute; top:7mm; left:7mm; width:40mm; height:40mm; border-radius:50%;
            border:0.6mm solid {_BRONZE};
            box-shadow:inset 0 0 1.5mm {_BRONZE}55;
        }}
        .cs-bandtext {{
            position:absolute; top:11mm; left:11mm; width:32mm; height:32mm;
            line-height:1.1; font-size:4.3pt; letter-spacing:0.4pt; font-weight:800;
            color:{deep}; text-align:center; text-transform:uppercase;
            display:flex; align-items:flex-start; justify-content:center;
            padding-top:0.6mm;
        }}
        .cs-core {{
            position:absolute; top:15mm; left:15mm; width:24mm; height:24mm; border-radius:50%;
            background:radial-gradient(circle at 38% 32%, {accent}, {deep});
            border:0.6mm solid #fff;
            box-shadow:0 0.6mm 1.4mm rgba(19,78,74,0.4);
            text-align:center;
        }}
        .cs-monogram {{
            margin-top:4mm; font-size:18pt; font-weight:900; color:#fff; line-height:1;
        }}
        .cs-core-sub {{
            margin-top:0.6mm; font-size:6pt; letter-spacing:2.5pt; font-weight:800;
            color:{soft};
        }}

        /* ── Ribbon banner ── */
        .cs-ribbon {{
            position:absolute; top:131mm; left:50%; width:96mm; margin-left:-48mm;
            height:11mm; line-height:11mm; text-align:center;
            background:linear-gradient(180deg, {accent}, {deep});
            color:#fff; font-size:10pt; font-weight:900; letter-spacing:3.5pt;
            text-transform:uppercase;
            box-shadow:0 0.8mm 1.8mm rgba(19,78,74,0.3);
        }}
        .cs-ribbon::before, .cs-ribbon::after {{
            content:""; position:absolute; top:0; width:0; height:0;
            border-top:5.5mm solid {deep}; border-bottom:5.5mm solid {deep};
        }}
        .cs-ribbon::before {{ left:-7mm; border-left:7mm solid transparent; }}
        .cs-ribbon::after  {{ right:-7mm; border-right:7mm solid transparent; }}
        .cs-ribbon-tail {{
            position:absolute; top:11mm; width:7mm; height:5mm;
            background:{_BRONZE_DEEP};
        }}
        .cs-ribbon-tail.l {{ left:6mm; clip-path:polygon(0 0,100% 0,100% 100%); }}
        .cs-ribbon-tail.r {{ right:6mm; clip-path:polygon(0 0,100% 0,0 100%); }}

        /* ── Period band ── */
        .cs-period {{
            position:absolute; top:150mm; left:50%; width:150mm; margin-left:-75mm;
            background:#fff; border:0.4mm solid {accent}66; border-radius:2mm;
            padding:5mm 8mm; display:flex; justify-content:space-between; align-items:center;
            box-shadow:0 0.6mm 1.6mm rgba(19,78,74,0.08);
        }}
        .cs-period .lbl {{
            font-size:6.6pt; letter-spacing:2pt; font-weight:800; color:{_BRONZE_DEEP};
            text-transform:uppercase;
        }}
        .cs-period .val {{
            font-size:12pt; font-weight:900; color:{deep}; margin-top:1.4mm;
            font-family:Georgia,serif;
        }}
        .cs-period .arr {{ font-size:13pt; color:{accent}; }}
        .cs-generated {{
            position:absolute; top:168mm; left:0; right:0; text-align:center;
            font-size:8pt; color:#6b6356; letter-spacing:0.4pt;
        }}

        /* ── KPI strip ── */
        .cover-seal .kpi-grid {{ position:absolute; top:178mm; left:50%; margin-left:-85mm; }}
        .cover-seal .kpi-tile {{
            background:#fff; border:0.4mm solid {_BRONZE}55;
        }}
        /* ── Chips ── */
        .cover-seal .chip-row {{ position:absolute; top:212mm; left:50%; margin-left:-85mm; }}

        /* ── Attestation strip ── */
        .cs-attest {{
            position:absolute; bottom:30mm; left:14mm; right:14mm;
            display:flex; justify-content:space-between; align-items:flex-end;
        }}
        .cs-attest .line {{
            width:54mm; border-top:0.4mm solid {deep}; padding-top:1.4mm;
            font-size:6.8pt; letter-spacing:0.6pt; color:#5b5345; text-align:center;
        }}
        .cs-attest .ref {{
            text-align:center; font-size:6.6pt; color:{_BRONZE_DEEP}; letter-spacing:0.8pt;
        }}

        /* ── Footer ── */
        .cs-footer {{
            position:absolute; bottom:11mm; left:0; right:0; text-align:center;
        }}
        .cs-footer .legal {{
            font-size:7pt; font-weight:700; color:{deep}; letter-spacing:0.4pt;
        }}
        .cs-footer .ids {{ font-size:6.4pt; color:{_BRONZE_DEEP}; margin-top:1mm; letter-spacing:0.6pt; }}
        .cs-footer .conf {{
            margin-top:1.4mm; font-size:6.6pt; letter-spacing:3pt; color:#b91c1c;
            font-weight:800; text-transform:uppercase;
        }}
      </style>

      <div class="cs-guilloche"></div>
      <div class="cs-corner tl"></div>
      <div class="cs-corner tr"></div>
      <div class="cs-corner bl"></div>
      <div class="cs-corner br"></div>

      <div class="cs-top">
        <span class="cs-crest">{esc(icon)}</span>
        <div class="cs-company">{esc(COMPANY['legal'].upper())}</div>
        <div class="cs-reg">CIN {esc(COMPANY['cin'])} &nbsp;·&nbsp; PAN {esc(COMPANY['pan'])} &nbsp;·&nbsp; TAN {esc(COMPANY['tan'])}</div>
      </div>

      <div class="cs-eyebrow">PAYROLL · STATUTORY COMPLIANCE</div>
      <h1 class="cs-title">{esc(name)}</h1>
      <div class="cs-rule"></div>
      <div class="cs-subtitle">{esc(subtitle)}</div>

      {_seal_disc(accent, deep, icon)}

      <div class="cs-ribbon">
        STATUTORY · CERTIFIED
        <span class="cs-ribbon-tail l"></span>
        <span class="cs-ribbon-tail r"></span>
      </div>

      <div class="cs-period">
        <div>
          <div class="lbl">Pay Period</div>
          <div class="val">{period_label}</div>
        </div>
        <div class="arr">⟶</div>
        <div style="text-align:center">
          <div class="lbl">Total Remittance</div>
          <div class="val">{esc(inr_compact(total_remit))}</div>
        </div>
        <div class="arr">⟶</div>
        <div style="text-align:right">
          <div class="lbl">Fiscal Year</div>
          <div class="val">{fy_label}</div>
        </div>
      </div>

      <div class="cs-generated">Generated {esc(now_stamp())} &nbsp;·&nbsp; {esc(str(rows))} line item(s)</div>

      {kpi_tiles_html(tiles)}
      {chips_html(chips)}

      <div class="cs-attest">
        <div>
          <div class="line">Prepared by · Payroll</div>
        </div>
        <div class="ref">
          REF &nbsp;FRC/STAT/{esc(str(period.get('year','')))}/{esc(str(period.get('month','')).zfill(2))}<br/>
          {esc(COMPANY['email'])}
        </div>
        <div>
          <div class="line">Authorised Signatory</div>
        </div>
      </div>

      <div class="cs-footer">
        <div class="legal">{esc(COMPANY['legal'])} &nbsp;·&nbsp; {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])}</div>
        <div class="ids">GSTIN {esc(COMPANY['gst'])} &nbsp;·&nbsp; {esc(COMPANY['web'])}</div>
        <div class="conf">Confidential · Statutory Filing Document · Internal Use Only</div>
      </div>
    </section>
    """
