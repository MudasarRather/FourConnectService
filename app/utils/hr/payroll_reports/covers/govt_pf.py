"""PDF cover — motif "govt-pf": an official EPFO Electronic Challan-cum-Return.

Designed to read like a real statutory upload artefact: a faux Government of
India / EPFO emblem rendered in CSS, a form-field grid (UAN range /
Establishment ID / Wage Month), the bold "ELECTRONIC CHALLAN CUM RETURN (ECR)"
band, monospace reference numbers and a faux barcode strip. Officialdom green.

Self-contained — all art is CSS/HTML; every selector is scoped under
``.cover-govt-pf`` so nothing leaks into the shared body table that follows.
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, kpi_tiles_html, now_stamp, month_name,
)


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#15803d")
    soft = meta.get("accent_soft", "#dcfce7")
    deep = meta.get("accent_deep", "#14532d")

    members = shaped_count if shaped_count is not None else summary.get("rows", 0)
    employees = summary.get("employees", members)
    pf_total = summary.get("pf", 0)
    gross_total = summary.get("gross", 0)

    yr = period.get("year", "")
    mo = period.get("month", 0)
    wage_month = f"{mo:02d}/{yr}" if isinstance(mo, int) and mo else esc(period.get("label", ""))
    fy = esc(period.get("fy", ""))
    label = esc(period.get("label", ""))

    # Deterministic faux statutory reference (looks official, not random).
    try:
        trrn = f"{int(yr):04d}{int(mo):02d}0000{int(members) % 100000:05d}"
    except (TypeError, ValueError):
        trrn = "20260500000000000"
    estt_id = "TNMAS" + COMPANY["tan"][-7:] + "000"
    challan_no = f"CHLN{wage_month.replace('/', '')}{int(members) % 1000:03d}" if isinstance(members, int) else "CHLN000"

    tiles = [
        ("Member Count", esc(str(members)), accent),
        ("UAN Records", esc(str(employees)), "#1a1410"),
        ("Total EPF Remittance", inr_compact(pf_total), deep),
        ("EPF Wages (Gross)", inr_compact(gross_total), "#b45309"),
    ]

    # Faux barcode — deterministic bar widths from the TRRN digits.
    bars = []
    seed = (trrn + "147258369") * 4
    x = 0.0
    for ch in seed[:84]:
        w = 0.35 + (int(ch) % 4) * 0.32
        dark = (int(ch) % 2) == 0
        if dark:
            bars.append(
                f'<span style="position:absolute;left:{x:.2f}mm;top:0;width:{w:.2f}mm;'
                f'height:10mm;background:{deep}"></span>'
            )
        x += w + 0.28
    barcode = "".join(bars)

    return f"""
    <section class="cover cover-govt-pf">
      <style>
        .cover-govt-pf {{
          font-family: Helvetica, Arial, sans-serif;
          color:#1a1410; padding:0; background:#ffffff;
        }}
        .cover-govt-pf .gpf-page {{ position:absolute; inset:0; padding:16mm 16mm 14mm; }}

        /* outer official ledger frame */
        .cover-govt-pf .gpf-frame {{
          position:absolute; top:11mm; left:11mm; right:11mm; bottom:11mm;
          border:1.6pt solid {deep};
          box-shadow: inset 0 0 0 1.1mm #ffffff, inset 0 0 0 1.3mm {accent}55;
        }}
        .cover-govt-pf .gpf-corner {{
          position:absolute; width:9mm; height:9mm; border:1.6pt solid {accent};
        }}
        .cover-govt-pf .c-tl {{ top:11mm; left:11mm; border-right:none; border-bottom:none; }}
        .cover-govt-pf .c-tr {{ top:11mm; right:11mm; border-left:none; border-bottom:none; }}
        .cover-govt-pf .c-bl {{ bottom:11mm; left:11mm; border-right:none; border-top:none; }}
        .cover-govt-pf .c-br {{ bottom:11mm; right:11mm; border-left:none; border-top:none; }}

        /* watermark guilloche texture */
        .cover-govt-pf .gpf-guilloche {{
          position:absolute; top:13mm; left:13mm; right:13mm; bottom:13mm;
          background-image:
            repeating-linear-gradient(45deg, {accent}07 0, {accent}07 0.5mm, transparent 0.5mm, transparent 3mm),
            repeating-linear-gradient(-45deg, {accent}06 0, {accent}06 0.5mm, transparent 0.5mm, transparent 3mm);
          opacity:0.7;
        }}

        /* masthead row: emblem + ministry text */
        .cover-govt-pf .gpf-mast {{ position:relative; text-align:center; }}
        .cover-govt-pf .gpf-emblem {{
          width:20mm; height:20mm; margin:0 auto 3mm; position:relative;
          border-radius:50%;
          background:
            radial-gradient(circle at 50% 50%, {soft} 0, {soft} 36%, transparent 37%),
            conic-gradient(from 0deg, {accent}, {deep}, {accent}, {deep}, {accent});
          box-shadow:0 0 0 0.7mm #fff, 0 0 0 1.2mm {deep};
        }}
        .cover-govt-pf .gpf-emblem .e-ring {{
          position:absolute; inset:2.4mm; border-radius:50%;
          border:0.5mm dashed {deep}88;
        }}
        .cover-govt-pf .gpf-emblem .e-core {{
          position:absolute; inset:5.4mm; border-radius:50%;
          background:linear-gradient(160deg, {accent}, {deep});
          color:#fff; font-size:13pt; font-weight:900; line-height:9.2mm; text-align:center;
          box-shadow: inset 0 0 0 0.4mm #ffffffaa;
        }}
        /* radiating spokes for the emblem (chakra-ish) */
        .cover-govt-pf .gpf-emblem .e-spoke {{
          position:absolute; left:50%; top:50%; width:0.45mm; height:9mm;
          background:{deep}; transform-origin:top center;
        }}
        .cover-govt-pf .gpf-ministry {{
          font-size:7pt; letter-spacing:2.6pt; font-weight:800; color:{deep}; text-transform:uppercase;
        }}
        .cover-govt-pf .gpf-org {{
          font-size:13pt; letter-spacing:1.2pt; font-weight:900; color:{accent}; margin-top:1.5mm; text-transform:uppercase;
        }}
        .cover-govt-pf .gpf-act {{
          font-size:6.5pt; letter-spacing:0.8pt; color:#6b5840; margin-top:1mm; font-style:italic;
        }}

        /* the ECR title band */
        .cover-govt-pf .gpf-band {{
          margin:6mm 0 5mm; padding:3.4mm 6mm; text-align:center; position:relative;
          background:linear-gradient(90deg, {deep}, {accent} 50%, {deep});
          color:#fff; border-radius:1mm;
          box-shadow:0 0.6mm 0 {deep};
        }}
        .cover-govt-pf .gpf-band .b-main {{ font-size:14.5pt; font-weight:900; letter-spacing:1.6pt; }}
        .cover-govt-pf .gpf-band .b-sub {{ font-size:7pt; letter-spacing:3pt; font-weight:700; opacity:0.92; margin-top:1mm; }}
        .cover-govt-pf .gpf-band::before,
        .cover-govt-pf .gpf-band::after {{
          content:""; position:absolute; top:1.4mm; bottom:1.4mm; width:0.5mm; background:#ffffffaa;
        }}
        .cover-govt-pf .gpf-band::before {{ left:2.4mm; }}
        .cover-govt-pf .gpf-band::after {{ right:2.4mm; }}

        /* boxed form-field grid */
        .cover-govt-pf .gpf-grid {{
          position:relative; display:flex; flex-wrap:wrap; gap:0;
          border:1pt solid {accent}88; border-radius:1.4mm; overflow:hidden;
          margin-bottom:5mm;
        }}
        .cover-govt-pf .gpf-cell {{
          width:50%; padding:3mm 5mm; box-sizing:border-box;
          border-bottom:0.5pt solid {accent}44; border-right:0.5pt solid {accent}44;
          background:#ffffff;
        }}
        .cover-govt-pf .gpf-cell.alt {{ background:{soft}66; }}
        .cover-govt-pf .gpf-cell .f-label {{
          font-size:6pt; letter-spacing:1.6pt; font-weight:800; color:{deep}; text-transform:uppercase;
        }}
        .cover-govt-pf .gpf-cell .f-value {{
          font-family:'Courier New', monospace; font-size:10pt; font-weight:700; color:#1a1410; margin-top:1.2mm;
          letter-spacing:0.3pt;
        }}

        /* KPI strip wrapper (reuse shared classes) */
        .cover-govt-pf .gpf-kpis {{ margin:2mm 0 5mm; }}

        /* faux barcode + ref panel */
        .cover-govt-pf .gpf-barcode-wrap {{
          position:relative; border:0.8pt solid {deep}; border-radius:1.2mm;
          padding:3mm 4mm; background:#ffffff; margin-bottom:4mm;
        }}
        .cover-govt-pf .gpf-barcode {{ position:relative; height:10mm; margin-bottom:1.6mm; }}
        .cover-govt-pf .gpf-barcode-label {{
          font-family:'Courier New', monospace; font-size:7.5pt; letter-spacing:1.6pt; color:{deep}; text-align:center;
          font-weight:700;
        }}

        .cover-govt-pf .gpf-stamp {{
          position:absolute; right:18mm; top:128mm; width:34mm; height:34mm;
          border:1.4pt solid {accent}cc; border-radius:50%;
          transform:rotate(-13deg); color:{accent}cc; text-align:center;
          box-shadow: inset 0 0 0 0.8mm {accent}55;
        }}
        .cover-govt-pf .gpf-stamp .s-top {{ font-size:6pt; letter-spacing:1.4pt; font-weight:800; margin-top:5mm; }}
        .cover-govt-pf .gpf-stamp .s-mid {{ font-size:11pt; letter-spacing:1pt; font-weight:900; margin-top:1mm; }}
        .cover-govt-pf .gpf-stamp .s-bot {{ font-size:5.5pt; letter-spacing:0.8pt; margin-top:1mm; }}

        .cover-govt-pf .gpf-gen {{
          text-align:center; font-size:8pt; color:#6b5840; letter-spacing:0.3pt; margin:1mm 0 0;
        }}

        .cover-govt-pf .gpf-foot {{
          position:absolute; left:16mm; right:16mm; bottom:14mm; text-align:center;
          border-top:0.6pt solid {accent}55; padding-top:2.4mm;
        }}
        .cover-govt-pf .gpf-foot .legal {{ font-size:7pt; font-weight:700; letter-spacing:0.4pt; color:#6b5840; }}
        .cover-govt-pf .gpf-foot .conf {{
          font-size:6.5pt; letter-spacing:2.4pt; text-transform:uppercase; color:{deep}; margin-top:1.2mm; font-weight:800;
        }}
      </style>

      <div class="gpf-frame"></div>
      <div class="gpf-guilloche"></div>
      <div class="gpf-corner c-tl"></div>
      <div class="gpf-corner c-tr"></div>
      <div class="gpf-corner c-bl"></div>
      <div class="gpf-corner c-br"></div>

      <div class="gpf-page">
        <div class="gpf-mast">
          <div class="gpf-emblem">
            <div class="e-spoke" style="transform:translate(-50%,0) rotate(0deg)"></div>
            <div class="e-spoke" style="transform:translate(-50%,0) rotate(45deg)"></div>
            <div class="e-spoke" style="transform:translate(-50%,0) rotate(90deg)"></div>
            <div class="e-spoke" style="transform:translate(-50%,0) rotate(135deg)"></div>
            <div class="e-spoke" style="transform:translate(-50%,0) rotate(180deg)"></div>
            <div class="e-spoke" style="transform:translate(-50%,0) rotate(225deg)"></div>
            <div class="e-spoke" style="transform:translate(-50%,0) rotate(270deg)"></div>
            <div class="e-spoke" style="transform:translate(-50%,0) rotate(315deg)"></div>
            <div class="e-ring"></div>
            <div class="e-core">P</div>
          </div>
          <div class="gpf-ministry">Ministry of Labour &amp; Employment · Government of India</div>
          <div class="gpf-org">Employees' Provident Fund Organisation</div>
          <div class="gpf-act">Under the Employees' Provident Funds &amp; Misc. Provisions Act, 1952</div>
        </div>

        <div class="gpf-band">
          <div class="b-main">ELECTRONIC CHALLAN CUM RETURN (ECR)</div>
          <div class="b-sub">UNIFIED PORTAL · MONTHLY CONTRIBUTION FILING</div>
        </div>

        <div class="gpf-grid">
          <div class="gpf-cell">
            <div class="f-label">Establishment ID</div>
            <div class="f-value">{esc(estt_id)}</div>
          </div>
          <div class="gpf-cell alt">
            <div class="f-label">Wage Month</div>
            <div class="f-value">{esc(wage_month)} &nbsp;·&nbsp; {label}</div>
          </div>
          <div class="gpf-cell alt">
            <div class="f-label">Establishment Name</div>
            <div class="f-value" style="font-size:8.5pt">{esc(COMPANY['legal'])}</div>
          </div>
          <div class="gpf-cell">
            <div class="f-label">TRRN (Temporary Return Ref.)</div>
            <div class="f-value">{esc(trrn)}</div>
          </div>
          <div class="gpf-cell">
            <div class="f-label">Challan No.</div>
            <div class="f-value">{esc(challan_no)}</div>
          </div>
          <div class="gpf-cell alt">
            <div class="f-label">Contribution Rate</div>
            <div class="f-value">EE 12% · EPS 8.33% · EPF 3.67%</div>
          </div>
          <div class="gpf-cell alt" style="border-right:none">
            <div class="f-label">PAN / TAN of Establishment</div>
            <div class="f-value">{esc(COMPANY['pan'])} / {esc(COMPANY['tan'])}</div>
          </div>
          <div class="gpf-cell" style="border-bottom:none;border-right:none">
            <div class="f-label">Filing Financial Year</div>
            <div class="f-value">{fy}</div>
          </div>
          <div class="gpf-cell alt" style="border-bottom:none">
            <div class="f-label">Wage Month (Numeric)</div>
            <div class="f-value">{esc(month_name(mo)) if isinstance(mo, int) else label}</div>
          </div>
        </div>

        <div class="gpf-kpis">
          {kpi_tiles_html(tiles)}
        </div>

        <div class="gpf-barcode-wrap">
          <div class="gpf-barcode">{barcode}</div>
          <div class="gpf-barcode-label">* {esc(trrn)} * &nbsp;·&nbsp; ECR ACK</div>
        </div>

        <div class="gpf-gen">Generated {now_stamp()} · System-prepared upload sheet · Total EPF payable {inr(pf_total)}</div>
      </div>

      <div class="gpf-stamp">
        <div class="s-top">EPFO · VERIFIED</div>
        <div class="s-mid">ECR</div>
        <div class="s-bot">{esc(wage_month)}</div>
      </div>

      <div class="gpf-foot">
        <div class="legal">{esc(COMPANY['legal'])} · {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])} · CIN {esc(COMPANY['cin'])}</div>
        <div class="conf">Confidential statutory record · For authorised EPFO filing only</div>
      </div>
    </section>
    """
