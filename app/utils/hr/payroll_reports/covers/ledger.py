"""Cover motif "ledger" — Payroll Register.

An old-world treasury ACCOUNTING LEDGER page. Heavy double-rule gold borders,
faint horizontal ledger rule lines as a repeating-linear-gradient, a gold
wax/foil "MASTER PAY LEDGER" emblem (concentric CSS rings), an embossed serif
title, and a faux fading column of debit/credit ledger marks. Cream paper,
antique-gold ink — tactile and authoritative.

Scoped entirely under ``.cover-ledger`` so nothing leaks into the body pages.
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, kpi_tiles_html, now_stamp,
)


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#b8860b")
    soft = meta.get("accent_soft", "#fdf3d6")
    deep = meta.get("accent_deep", "#6b4e08")

    rows = shaped_count if shaped_count is not None else summary.get("rows", summary.get("headcount", 0))
    heads = summary.get("employees", summary.get("headcount", rows))
    gross = summary.get("gross", 0)
    net = summary.get("net", 0)
    deductions = summary.get("deductions", 0)
    total_cost = summary.get("total_cost", summary.get("net", 0) + summary.get("employer_cost", 0))

    tiles = [
        ("Ledger Folios", str(rows), accent),
        ("Net Disbursed", inr_compact(net), "#047857"),
        ("Gross Wages", inr_compact(gross), deep),
        ("Total Cost", inr_compact(total_cost), "#1a1410"),
    ]

    label = esc(period.get("label", ""))
    fy = esc(period.get("fy", ""))
    short = esc(period.get("short", label))
    ref = f"FRC/PAY/REGISTER/{esc(period.get('year', ''))}"

    # Faux fading column of debit / credit ledger marks down the right gutter.
    marks = []
    sample = ["Dr", "Cr", "Dr", "Cr", "Cr", "Dr", "Cr", "Dr", "Cr", "Dr", "Cr", "Cr"]
    for i, m in enumerate(sample):
        op = round(0.42 - i * 0.032, 3)
        if op < 0.04:
            op = 0.04
        marks.append(
            f'<div class="lg-mark" style="opacity:{op}">'
            f'<span class="lg-mk-key">{m}</span>'
            f'<span class="lg-mk-rule"></span></div>'
        )
    marks_col = "".join(marks)

    return f"""
    <section class="cover cover-ledger">
      <style>
        .cover-ledger {{
            background:
              repeating-linear-gradient(
                  0deg,
                  transparent 0, transparent 7.6mm,
                  {accent}1f 7.6mm, {accent}1f 7.7mm),
              radial-gradient(120mm 150mm at 28% 18%, {soft} 0%, transparent 70%),
              radial-gradient(140mm 160mm at 84% 92%, {soft} 0%, transparent 72%),
              #fbf6e6;
            color:#1a1410;
            font-family:'Georgia','Times New Roman',serif;
            padding:0;
        }}
        /* Heavy double-rule gold frame */
        .cover-ledger .lg-frame-outer {{
            position:absolute; top:9mm; left:9mm; right:9mm; bottom:9mm;
            border:1.6mm solid {deep};
            box-shadow:inset 0 0 0 0.5mm {soft};
        }}
        .cover-ledger .lg-frame-inner {{
            position:absolute; top:12.6mm; left:12.6mm; right:12.6mm; bottom:12.6mm;
            border:0.5mm solid {accent};
        }}
        .cover-ledger .lg-corner {{
            position:absolute; width:9mm; height:9mm; border:0.6mm solid {deep};
        }}
        .cover-ledger .lg-c-tl {{ top:14.5mm; left:14.5mm; border-right:none; border-bottom:none; }}
        .cover-ledger .lg-c-tr {{ top:14.5mm; right:14.5mm; border-left:none; border-bottom:none; }}
        .cover-ledger .lg-c-bl {{ bottom:14.5mm; left:14.5mm; border-right:none; border-top:none; }}
        .cover-ledger .lg-c-br {{ bottom:14.5mm; right:14.5mm; border-left:none; border-top:none; }}

        .cover-ledger .lg-inner {{
            position:absolute; top:18mm; left:20mm; right:20mm; bottom:18mm;
        }}

        /* Header bar — company + ledger reference */
        .cover-ledger .lg-head {{
            display:flex; justify-content:space-between; align-items:flex-start;
            border-bottom:0.4mm solid {accent}88; padding-bottom:3.5mm;
        }}
        .cover-ledger .lg-co {{
            font-size:8pt; letter-spacing:3.4pt; font-weight:bold;
            text-transform:uppercase; color:{deep};
        }}
        .cover-ledger .lg-co .lg-legal {{
            display:block; font-size:6.6pt; letter-spacing:1.4pt;
            color:#8a7c63; margin-top:1.4mm; font-weight:normal;
        }}
        .cover-ledger .lg-ref {{
            text-align:right; font-size:6.8pt; letter-spacing:1.2pt; color:#8a7c63;
            font-family:'Courier New',monospace;
        }}
        .cover-ledger .lg-ref b {{ color:{deep}; }}

        /* Wax / foil emblem */
        .cover-ledger .lg-emblem-wrap {{ text-align:center; margin-top:13mm; }}
        .cover-ledger .lg-emblem {{
            display:inline-block; width:34mm; height:34mm; border-radius:50%;
            position:relative;
            background:
               radial-gradient(circle at 38% 34%, #f4e3a8 0%, {accent} 42%, {deep} 100%);
            box-shadow:0 0 0 1.4mm {soft}, 0 0 0 2.2mm {deep},
                       0 1.6mm 3.4mm rgba(26,20,16,0.30);
        }}
        .cover-ledger .lg-emblem::before {{
            content:""; position:absolute; top:3mm; left:3mm; right:3mm; bottom:3mm;
            border:0.5mm dashed #fbf6e6cc; border-radius:50%;
        }}
        .cover-ledger .lg-emblem::after {{
            content:""; position:absolute; top:5.4mm; left:5.4mm; right:5.4mm; bottom:5.4mm;
            border:0.35mm solid #fbf6e688; border-radius:50%;
        }}
        .cover-ledger .lg-emblem .lg-em-txt {{
            position:absolute; top:0; left:0; right:0; bottom:0;
            display:flex; flex-direction:column; align-items:center; justify-content:center;
            color:#fff8e1;
        }}
        .cover-ledger .lg-emblem .lg-em-glyph {{ font-size:15pt; font-weight:bold; line-height:1;
            text-shadow:0 0.4mm 0.6mm rgba(26,20,16,0.5); }}
        .cover-ledger .lg-emblem .lg-em-est {{ font-size:4.6pt; letter-spacing:1.6pt;
            margin-top:1.2mm; text-transform:uppercase; opacity:0.92; }}
        .cover-ledger .lg-emblem-ribbon {{
            margin-top:5mm; font-size:6.8pt; letter-spacing:3.6pt; font-weight:bold;
            text-transform:uppercase; color:{deep};
        }}

        /* Title block — embossed serif */
        .cover-ledger .lg-eyebrow {{
            text-align:center; margin-top:9mm; font-size:7.5pt; letter-spacing:5pt;
            font-weight:bold; text-transform:uppercase; color:{accent};
            font-family:'Helvetica','Arial',sans-serif;
        }}
        .cover-ledger .lg-title {{
            text-align:center; font-size:40pt; font-weight:bold; line-height:1.02;
            margin:3.5mm 0 0; letter-spacing:-0.4pt; color:#231a0d;
            text-shadow:0 0.35mm 0 {soft}, 0 0.7mm 0.3mm rgba(184,134,11,0.28);
        }}
        .cover-ledger .lg-title .lg-flourish {{
            display:block; margin:3mm auto 0; width:54mm; text-align:center;
            color:{accent}; font-size:12pt; letter-spacing:5pt;
        }}
        .cover-ledger .lg-sub {{
            text-align:center; font-style:italic; font-size:11pt; color:#5b4a30;
            margin-top:3mm;
        }}

        /* Period ledger band */
        .cover-ledger .lg-period {{
            margin:11mm auto 0; width:150mm; border:0.5mm solid {accent};
            background:#fffdf4; box-shadow:0 1mm 2.4mm rgba(184,134,11,0.14);
            display:flex;
        }}
        .cover-ledger .lg-pcell {{ flex:1; padding:5mm 6mm; text-align:center; }}
        .cover-ledger .lg-pcell + .lg-pcell {{ border-left:0.5mm solid {accent}66; }}
        .cover-ledger .lg-pl {{ font-size:6.6pt; letter-spacing:2.4pt; font-weight:bold;
            text-transform:uppercase; color:{deep};
            font-family:'Helvetica','Arial',sans-serif; }}
        .cover-ledger .lg-pv {{ font-size:13pt; font-weight:bold; color:#1a1410; margin-top:2.2mm;
            letter-spacing:-0.1pt; }}

        .cover-ledger .lg-gen {{ text-align:center; margin-top:6mm; font-size:8pt;
            color:#6b5840; letter-spacing:0.6pt;
            font-family:'Helvetica','Arial',sans-serif; }}
        .cover-ledger .lg-gen b {{ color:{deep}; }}

        /* KPI strip — re-skin shared tiles for the ledger look */
        .cover-ledger .kpi-grid {{ width:160mm; margin:9mm auto 0; gap:4mm; }}
        .cover-ledger .kpi-tile {{
            background:#fffdf4; border:0.5mm solid {accent}99;
            border-top-width:2mm; box-shadow:0 1mm 2.2mm rgba(184,134,11,0.16);
            border-radius:0; padding:5mm 3mm 6mm;
        }}
        .cover-ledger .kpi-label {{ font-family:'Helvetica','Arial',sans-serif;
            color:{deep}; letter-spacing:1.6pt; }}
        .cover-ledger .kpi-value {{ font-family:'Georgia',serif; }}

        /* Faux fading debit/credit ledger marks down the right gutter */
        .cover-ledger .lg-marks {{
            position:absolute; top:62mm; right:6mm; width:13mm; z-index:0;
        }}
        .cover-ledger .lg-mark {{ display:flex; align-items:center; gap:1mm;
            height:7.6mm; }}
        .cover-ledger .lg-mk-key {{ font-size:6pt; font-weight:bold; color:{deep};
            font-family:'Courier New',monospace; width:5mm; }}
        .cover-ledger .lg-mk-rule {{ flex:1; height:0.4mm; background:{accent}; }}

        .cover-ledger .lg-foot {{
            position:absolute; left:20mm; right:20mm; bottom:19mm; text-align:center;
            border-top:0.4mm solid {accent}88; padding-top:3mm;
        }}
        .cover-ledger .lg-foot .lg-legal2 {{ font-size:7pt; font-weight:bold;
            letter-spacing:0.5pt; color:#5b4a30;
            font-family:'Helvetica','Arial',sans-serif; }}
        .cover-ledger .lg-foot .lg-conf {{ margin-top:1.6mm; font-size:6.6pt;
            letter-spacing:3pt; text-transform:uppercase; color:{accent};
            font-family:'Helvetica','Arial',sans-serif; }}
        .cover-ledger .lg-foot .lg-stat {{ margin-top:1.2mm; font-size:6pt;
            letter-spacing:1pt; color:#8a7c63;
            font-family:'Courier New',monospace; }}
      </style>

      <div class="lg-frame-outer"></div>
      <div class="lg-frame-inner"></div>
      <div class="lg-corner lg-c-tl"></div>
      <div class="lg-corner lg-c-tr"></div>
      <div class="lg-corner lg-c-bl"></div>
      <div class="lg-corner lg-c-br"></div>

      <div class="lg-marks">{marks_col}</div>

      <div class="lg-inner">
        <div class="lg-head">
          <div class="lg-co">{esc(COMPANY['name'])} · Treasury
            <span class="lg-legal">{esc(COMPANY['legal'])}</span>
          </div>
          <div class="lg-ref">
            LEDGER REF<br><b>{ref}</b><br>
            CIN {esc(COMPANY['cin'])}<br>PAN {esc(COMPANY['pan'])}
          </div>
        </div>

        <div class="lg-emblem-wrap">
          <div class="lg-emblem">
            <div class="lg-em-txt">
              <div class="lg-em-glyph">₹</div>
              <div class="lg-em-est">Anno {esc(period.get('year', ''))}</div>
            </div>
          </div>
          <div class="lg-emblem-ribbon">Master Pay Ledger</div>
        </div>

        <div class="lg-eyebrow">Payroll · {esc(meta.get('group', 'Core').upper())}</div>
        <h1 class="lg-title">{esc(meta.get('name', 'Payroll Register'))}
          <span class="lg-flourish">&#10086; &bull; &#10087;</span>
        </h1>
        <div class="lg-sub">{esc(meta.get('subtitle', ''))}</div>

        <div class="lg-period">
          <div class="lg-pcell">
            <div class="lg-pl">Pay Period</div>
            <div class="lg-pv">{label}</div>
          </div>
          <div class="lg-pcell">
            <div class="lg-pl">Fiscal Year</div>
            <div class="lg-pv">{fy}</div>
          </div>
          <div class="lg-pcell">
            <div class="lg-pl">Folios Posted</div>
            <div class="lg-pv">{rows} &middot; {heads} emp</div>
          </div>
        </div>

        <div class="lg-gen">Posted &amp; closed <b>{now_stamp()}</b></div>

        {kpi_tiles_html(tiles)}
      </div>

      <div class="lg-foot">
        <div class="lg-legal2">{esc(COMPANY['legal'])} &middot; {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])}</div>
        <div class="lg-conf">Confidential &middot; Payroll Treasury &middot; Internal Use Only</div>
        <div class="lg-stat">GST {esc(COMPANY['gst'])} &middot; TAN {esc(COMPANY['tan'])} &middot; {esc(COMPANY['email'])}</div>
      </div>
    </section>
    """
