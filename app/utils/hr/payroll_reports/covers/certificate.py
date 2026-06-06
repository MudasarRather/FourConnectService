"""Cover motif "certificate" — Year-to-Date Earnings.

A year-end ACHIEVEMENT CERTIFICATE: an ornate gold double border with corner
flourishes, a CSS laurel wreath (two curved branches) framing the title, a
"FISCAL YEAR <fy>" ribbon banner, a foil seal, and an elegant calligraphic
serif (Georgia) title. Warm bronze/gold, ceremonial, premium.

Every selector is scoped under ``.cover-certificate`` so nothing leaks into the
shared body styles. Units are mm/pt only (WeasyPrint). All art is CSS/HTML.
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, kpi_tiles_html, now_stamp, month_name,
)


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#92400e")          # bronze
    soft = meta.get("accent_soft", "#fef3c7")       # warm parchment
    deep = meta.get("accent_deep", "#451a03")       # deep cocoa
    gold = "#b8860b"
    gold_lt = "#d9a441"
    gold_pale = "#f3e2b3"

    fy = period.get("fy", "")
    label = period.get("label", "")
    month = period.get("month", 0)
    year = period.get("year", "")

    rows = shaped_count if shaped_count is not None else summary.get("rows", summary.get("employees", 0))
    employees = summary.get("employees", rows)
    ytd_gross = summary.get("ytd_gross", 0)
    ytd_net = summary.get("ytd_net", 0)
    ytd_tds = summary.get("ytd_tds", 0)

    tiles = [
        ("Employees Honoured", esc(str(employees)), deep),
        ("YTD Gross Earnings", inr_compact(ytd_gross), gold),
        ("YTD Net Disbursed", inr_compact(ytd_net), "#047857"),
        ("YTD Tax (TDS)", inr_compact(ytd_tds), "#b91c1c"),
    ]
    kpi_strip = kpi_tiles_html(tiles)

    # how far through the fiscal year are we (months 4..3 → 1..12 progress)
    fy_month_no = (month - 3) if month >= 4 else (month + 9)
    fy_month_no = max(1, min(12, fy_month_no))

    company = esc(COMPANY["legal"].upper())
    seal_year = esc(str(year))

    return f"""
    <section class="cover cover-certificate">
      <style>
        .cover-certificate {{
          background:
            radial-gradient(ellipse 120mm 80mm at 50% 26%, {soft} 0%, #fffaf0 55%, #fdf6e8 100%);
          font-family: Georgia, 'Times New Roman', serif;
          color: {deep};
          padding: 0;
        }}
        /* ── Ornate double border with corner flourishes ── */
        .cc-frame-outer {{
          position:absolute; top:9mm; left:9mm; right:9mm; bottom:9mm;
          border: 1.4mm solid {gold};
          border-radius: 1.5mm;
        }}
        .cc-frame-mid {{
          position:absolute; top:11.4mm; left:11.4mm; right:11.4mm; bottom:11.4mm;
          border: 0.4mm solid {gold_lt};
        }}
        .cc-frame-inner {{
          position:absolute; top:13mm; left:13mm; right:13mm; bottom:13mm;
          border: 0.7mm solid {accent}88;
          border-radius: 0.8mm;
          background:
            repeating-linear-gradient(45deg, transparent 0, transparent 5.5mm,
              {gold}10 5.5mm, {gold}10 6mm);
        }}
        /* guilloché whisper inside the inner frame */
        .cc-guilloche {{
          position:absolute; top:13mm; left:13mm; right:13mm; bottom:13mm;
          background:
            radial-gradient(circle at 0% 0%, {gold}14 0, transparent 30mm),
            radial-gradient(circle at 100% 0%, {gold}14 0, transparent 30mm),
            radial-gradient(circle at 0% 100%, {gold}14 0, transparent 30mm),
            radial-gradient(circle at 100% 100%, {gold}14 0, transparent 30mm);
        }}
        /* ── Corner flourishes (four diamond + arc motifs) ── */
        .cc-corner {{ position:absolute; width:20mm; height:20mm; }}
        .cc-corner .arc {{
          position:absolute; width:14mm; height:14mm;
          border: 0.9mm solid {gold}; border-radius:50%;
        }}
        .cc-corner .dot {{
          position:absolute; width:3mm; height:3mm; background:{accent};
          transform: rotate(45deg); top:6mm; left:6mm;
        }}
        .cc-corner .pip {{
          position:absolute; width:1.4mm; height:1.4mm; background:{gold_lt};
          transform: rotate(45deg);
        }}
        .cc-tl {{ top:14mm; left:14mm; }}
        .cc-tl .arc {{ top:0; left:0; border-right-color:transparent; border-bottom-color:transparent; }}
        .cc-tr {{ top:14mm; right:14mm; }}
        .cc-tr .arc {{ top:0; right:0; border-left-color:transparent; border-bottom-color:transparent; }}
        .cc-tr .dot {{ left:auto; right:6mm; }}
        .cc-bl {{ bottom:14mm; left:14mm; }}
        .cc-bl .arc {{ bottom:0; left:0; border-right-color:transparent; border-top-color:transparent; }}
        .cc-bl .dot {{ top:auto; bottom:6mm; }}
        .cc-br {{ bottom:14mm; right:14mm; }}
        .cc-br .arc {{ bottom:0; right:0; border-left-color:transparent; border-top-color:transparent; }}
        .cc-br .dot {{ top:auto; bottom:6mm; left:auto; right:6mm; }}

        /* ── Content stack ── */
        .cc-body {{ position:absolute; top:20mm; left:22mm; right:22mm; text-align:center; }}

        .cc-company {{
          font-family:'Helvetica','Arial',sans-serif;
          font-size:7.5pt; letter-spacing:3.6pt; font-weight:700;
          color:{accent}; text-transform:uppercase; margin-bottom:1.5mm;
        }}
        .cc-crest {{
          display:inline-block; width:13mm; height:13mm; border-radius:50%;
          background: radial-gradient(circle at 35% 30%, {gold_lt}, {accent} 70%, {deep});
          color:#fff8e8; font-size:14pt; font-weight:900; line-height:13mm;
          font-family: Georgia, serif;
          box-shadow: 0 0 0 0.6mm #fff8e8, 0 0 0 1.2mm {gold};
          margin-bottom:3mm;
        }}
        .cc-eyebrow {{
          font-family:'Helvetica','Arial',sans-serif;
          font-size:8pt; letter-spacing:5pt; font-weight:800;
          color:{accent}; text-transform:uppercase; margin:2mm 0 1mm;
        }}
        .cc-eyebrow-rule {{
          width:46mm; height:0; margin:2mm auto 0;
          border-top:0.4mm solid {gold};
          position:relative;
        }}
        .cc-eyebrow-rule::after {{
          content:"\\2756"; position:absolute; top:-2.4mm; left:50%; margin-left:-2mm;
          color:{gold}; font-size:9pt; background:#fffaf0; padding:0 1.5mm;
        }}

        /* ── Laurel wreath flanking the title ── */
        .cc-title-wrap {{ position:relative; margin-top:7mm; padding:0 28mm; }}
        .cc-laurel {{ position:absolute; top:-2mm; width:24mm; height:34mm; }}
        .cc-laurel-l {{ left:2mm; }}
        .cc-laurel-r {{ right:2mm; }}
        .cc-laurel .stem {{
          position:absolute; left:11mm; top:0; width:0.7mm; height:34mm;
          background:{gold}; border-radius:0.4mm;
        }}
        .cc-laurel .leaf {{
          position:absolute; width:7.5mm; height:3.2mm; background:{accent}cc;
          border-radius:0 60% 0 60%;
        }}
        .cc-laurel-r .leaf {{ border-radius:60% 0 60% 0; }}
        .cc-laurel-l .leaf {{ left:3.5mm; }}
        .cc-laurel-r .leaf {{ right:3.5mm; }}
        .cc-l1 {{ top:2mm;  transform: rotate(38deg); }}
        .cc-l2 {{ top:8mm;  transform: rotate(30deg); }}
        .cc-l3 {{ top:14mm; transform: rotate(22deg); }}
        .cc-l4 {{ top:20mm; transform: rotate(14deg); }}
        .cc-l5 {{ top:26mm; transform: rotate(6deg); }}
        .cc-laurel-r .cc-l1 {{ transform: rotate(-38deg); }}
        .cc-laurel-r .cc-l2 {{ transform: rotate(-30deg); }}
        .cc-laurel-r .cc-l3 {{ transform: rotate(-22deg); }}
        .cc-laurel-r .cc-l4 {{ transform: rotate(-14deg); }}
        .cc-laurel-r .cc-l5 {{ transform: rotate(-6deg); }}
        .cc-laurel .berry {{
          position:absolute; width:2mm; height:2mm; border-radius:50%;
          background:{gold_lt}; top:1mm; left:9.5mm;
        }}

        .cc-presented {{
          font-style:italic; font-size:10pt; color:{accent}; margin-bottom:2mm;
          letter-spacing:0.3pt;
        }}
        .cc-title {{
          font-family: Georgia, serif; font-weight:700;
          font-size:33pt; line-height:1.05; letter-spacing:-0.3pt;
          color:{deep}; margin:0;
          text-shadow: 0 0.3mm 0 {gold_pale};
        }}
        .cc-title .amp {{ color:{gold}; font-style:italic; }}
        .cc-subtitle {{
          font-style:italic; font-size:10.5pt; color:#5a4632;
          margin:4mm auto 0; max-width:130mm; line-height:1.4;
        }}

        /* ── Fiscal-year ribbon banner ── */
        .cc-ribbon-wrap {{ margin:9mm auto 0; width:120mm; position:relative; height:15mm; }}
        .cc-ribbon {{
          position:absolute; left:14mm; right:14mm; top:0; height:13mm;
          background: linear-gradient(180deg, {accent} 0%, {deep} 100%);
          box-shadow: inset 0 0.4mm 0 {gold_lt}aa, 0 1mm 2mm {deep}33;
        }}
        .cc-ribbon .txt {{
          color:#fff7e6; text-align:center; line-height:13mm;
          font-family:'Helvetica','Arial',sans-serif;
          font-size:11pt; font-weight:800; letter-spacing:3pt;
        }}
        .cc-ribbon .txt b {{ color:{gold_lt}; }}
        .cc-ribbon-tail {{
          position:absolute; top:2mm; width:14mm; height:13mm;
          background: linear-gradient(180deg, {deep}, {accent});
        }}
        .cc-ribbon-tail.l {{ left:0;  clip-path: polygon(0 0,100% 0,100% 100%,0 100%,30% 50%); }}
        .cc-ribbon-tail.r {{ right:0; clip-path: polygon(0 0,100% 0,70% 50%,100% 100%,0 100%); }}
        .cc-fy-period {{
          font-family:'Helvetica','Arial',sans-serif;
          font-size:8pt; letter-spacing:2.5pt; font-weight:700; color:{accent};
          text-transform:uppercase; margin-top:3mm;
        }}

        /* ── Foil seal (bottom-right within frame) ── */
        .cc-seal {{ position:absolute; bottom:30mm; right:30mm; width:30mm; height:30mm; }}
        .cc-seal .disc {{
          position:absolute; top:0; left:0; width:30mm; height:30mm; border-radius:50%;
          background: radial-gradient(circle at 38% 32%, {gold_lt} 0%, {accent} 58%, {deep} 100%);
          box-shadow: 0 0 0 0.8mm #fff8e8, 0 0 0 1.4mm {gold}, 0 1mm 3mm {deep}55;
        }}
        .cc-seal .gear {{
          position:absolute; top:2.5mm; left:2.5mm; width:25mm; height:25mm; border-radius:50%;
          border: 0.5mm dashed {gold_pale}cc;
        }}
        .cc-seal .inner {{
          position:absolute; top:6mm; left:6mm; width:18mm; height:18mm; border-radius:50%;
          border:0.4mm solid {gold_pale}; text-align:center;
        }}
        .cc-seal .star {{
          color:{gold_pale}; font-size:13pt; line-height:9mm; display:block; margin-top:1.5mm;
        }}
        .cc-seal .yr {{
          color:#fff8e8; font-family:'Helvetica',sans-serif; font-weight:800;
          font-size:8.5pt; letter-spacing:1pt; display:block;
        }}
        .cc-seal .word {{
          color:{gold_pale}; font-family:'Helvetica',sans-serif; font-weight:700;
          font-size:5pt; letter-spacing:1.4pt; display:block; margin-top:0.5mm;
        }}

        /* ── Signature / attestation line (bottom-left) ── */
        .cc-attest {{ position:absolute; bottom:31mm; left:30mm; width:70mm; text-align:left; }}
        .cc-attest .sig {{
          font-family: Georgia, serif; font-style:italic; font-size:14pt;
          color:{deep}; border-bottom:0.4mm solid {accent}; padding-bottom:1mm; width:54mm;
        }}
        .cc-attest .role {{
          font-family:'Helvetica',sans-serif; font-size:7pt; letter-spacing:1.6pt;
          font-weight:700; color:{accent}; text-transform:uppercase; margin-top:1.5mm;
        }}
        .cc-attest .gen {{
          font-family:'Helvetica',sans-serif; font-size:6.8pt; color:#8a7c63;
          margin-top:1.5mm; letter-spacing:0.4pt;
        }}

        /* ── KPI strip override to fit ceremonial frame ── */
        .cc-kpis {{ position:absolute; bottom:46mm; left:22mm; right:22mm; }}
        .cc-kpis .kpi-grid {{ width:auto; gap:3.5mm; margin:0; }}
        .cc-kpis .kpi-tile {{
          background:#fffdf7; border:0.5pt solid {gold}77;
          border-top-width:2mm; border-radius:1mm;
          box-shadow:0 0.5pt 2pt {deep}1a;
        }}

        /* ── Confidential footer ── */
        .cc-foot {{
          position:absolute; bottom:15mm; left:22mm; right:22mm; text-align:center;
          font-family:'Helvetica',sans-serif;
        }}
        .cc-foot .legal {{ font-size:7pt; color:#8a7c63; font-weight:700; letter-spacing:0.4pt; }}
        .cc-foot .conf {{
          font-size:6.5pt; color:{accent}; letter-spacing:2.5pt; text-transform:uppercase;
          margin-top:1mm; font-weight:700;
        }}
      </style>

      <!-- Ornate frames -->
      <div class="cc-frame-outer"></div>
      <div class="cc-frame-mid"></div>
      <div class="cc-guilloche"></div>
      <div class="cc-frame-inner"></div>

      <!-- Corner flourishes -->
      <div class="cc-corner cc-tl"><div class="arc"></div><div class="dot"></div></div>
      <div class="cc-corner cc-tr"><div class="arc"></div><div class="dot"></div></div>
      <div class="cc-corner cc-bl"><div class="arc"></div><div class="dot"></div></div>
      <div class="cc-corner cc-br"><div class="arc"></div><div class="dot"></div></div>

      <!-- Header / brand -->
      <div class="cc-body">
        <div class="cc-crest">{esc(COMPANY.get('name', 'F')[:1])}</div>
        <div class="cc-company">{company}</div>
        <div class="cc-eyebrow">Statement of Earnings &middot; {esc(meta.get('group', 'Adjustments'))}</div>
        <div class="cc-eyebrow-rule"></div>

        <!-- Title with laurel wreath -->
        <div class="cc-title-wrap">
          <div class="cc-laurel cc-laurel-l">
            <div class="stem"></div><div class="berry"></div>
            <div class="leaf cc-l1"></div><div class="leaf cc-l2"></div>
            <div class="leaf cc-l3"></div><div class="leaf cc-l4"></div><div class="leaf cc-l5"></div>
          </div>
          <div class="cc-laurel cc-laurel-r">
            <div class="stem"></div><div class="berry"></div>
            <div class="leaf cc-l1"></div><div class="leaf cc-l2"></div>
            <div class="leaf cc-l3"></div><div class="leaf cc-l4"></div><div class="leaf cc-l5"></div>
          </div>
          <div class="cc-presented">This certifies the year-to-date payroll record of</div>
          <h1 class="cc-title">Year-to-Date <span class="amp">Earnings</span></h1>
        </div>
        <p class="cc-subtitle">{esc(meta.get('subtitle', 'Gross, deductions, net and TDS accumulated across the fiscal year'))}</p>

        <!-- Fiscal-year ribbon -->
        <div class="cc-ribbon-wrap">
          <div class="cc-ribbon-tail l"></div>
          <div class="cc-ribbon-tail r"></div>
          <div class="cc-ribbon"><div class="txt">FISCAL YEAR <b>{esc(fy)}</b></div></div>
        </div>
        <div class="cc-fy-period">Cumulative through {esc(label)} &middot; Month {fy_month_no} of 12</div>
      </div>

      <!-- KPI strip -->
      <div class="cc-kpis">{kpi_strip}</div>

      <!-- Attestation -->
      <div class="cc-attest">
        <div class="sig">Payroll &amp; Finance Office</div>
        <div class="role">Authorised &middot; Fourreck Payroll</div>
        <div class="gen">Generated {esc(now_stamp())}</div>
      </div>

      <!-- Foil seal -->
      <div class="cc-seal">
        <div class="disc"></div>
        <div class="gear"></div>
        <div class="inner">
          <span class="star">&#9733;</span>
          <span class="yr">FY {seal_year}</span>
          <span class="word">VERIFIED</span>
        </div>
      </div>

      <!-- Footer -->
      <div class="cc-foot">
        <div class="legal">{esc(COMPANY['legal'])} &middot; {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])}</div>
        <div class="conf">Confidential &middot; Internal Payroll Record</div>
      </div>
    </section>
    """
