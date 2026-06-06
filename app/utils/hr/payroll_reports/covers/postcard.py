"""CTC Summary — PDF cover, motif "postcard".

A premium travel-postcard / boarding-pass cover for the CTC Summary report.
Art: a rotated dashed postage stamp with a CSS postmark ring, a dotted tear
divider running the width of the card, and a "CTC" boarding-pass stub on the
right with a punched perforation column. Cyan / teal tones, playful-but-premium.

Self-contained: every selector is scoped under ``.cover-postcard`` so the inline
<style> can't leak into the shared body table. mm / pt units only (WeasyPrint).
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, inr_group, kpi_tiles_html,
    period_band_html, chips_html, now_stamp, fmt_long_date, month_name,
)


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#0891b2")
    soft = meta.get("accent_soft", "#cffafe")
    deep = meta.get("accent_deep", "#155e75")
    icon = meta.get("icon", "◈")

    employees = summary.get("employees", 0)
    rows = shaped_count if shaped_count is not None else summary.get("rows", employees)
    annual = summary.get("annual_ctc", 0) or 0
    monthly = summary.get("monthly_ctc", 0) or 0
    avg = summary.get("avg_ctc", 0) or 0

    label = period.get("label", "")
    fy = period.get("fy", "")
    short = period.get("short", label)
    year = period.get("year", "")
    month = period.get("month", "")
    mname = month_name(month) if isinstance(month, int) else str(month)

    tiles = kpi_tiles_html([
        ("Compensation Records", str(rows), accent),
        ("Active Employees", str(employees), "#0e7490"),
        ("Annual CTC", inr_compact(annual), deep),
        ("Avg CTC / Head", inr_compact(avg), "#0891b2"),
    ])

    chips = chips_html([
        ("Snapshot · " + esc(label), soft, deep),
        ("Annual + Monthly", "#e0fbff", "#0e7490"),
        ("New & Old Regime", "#ffffff", deep),
    ])

    # Boarding-pass stub figures (right perforated panel)
    stub_monthly = inr_compact(monthly)
    stub_annual = inr_compact(annual)

    css = f"""
    <style>
    .cover-postcard {{
        width:210mm; height:297mm; position:relative; overflow:hidden;
        background:
            radial-gradient(circle at 12% 8%, {soft} 0%, rgba(207,250,254,0) 42%),
            radial-gradient(circle at 92% 96%, {soft} 0%, rgba(207,250,254,0) 38%),
            linear-gradient(160deg, #ffffff 0%, #f3fcff 55%, #ffffff 100%);
        page-break-after:always; font-family:'Helvetica','Arial',sans-serif; color:#0b2b33;
    }}
    /* faint dotted "airmail" border frame */
    .cp-frame {{
        position:absolute; top:9mm; left:9mm; right:9mm; bottom:9mm;
        border:0.5mm dashed {accent}; border-radius:3mm;
    }}
    .cp-airmail {{
        position:absolute; top:9mm; left:9mm; right:9mm; height:3mm;
        background:repeating-linear-gradient(45deg, {deep} 0 4mm, #ffffff 4mm 8mm, {accent} 8mm 12mm, #ffffff 12mm 16mm);
        opacity:0.55; border-radius:3mm 3mm 0 0;
    }}
    .cp-airmail-b {{
        position:absolute; bottom:9mm; left:9mm; right:9mm; height:3mm;
        background:repeating-linear-gradient(45deg, {deep} 0 4mm, #ffffff 4mm 8mm, {accent} 8mm 12mm, #ffffff 12mm 16mm);
        opacity:0.55; border-radius:0 0 3mm 3mm;
    }}

    /* ── header band: brand + DESTINATION line ── */
    .cp-brand {{ position:absolute; top:20mm; left:20mm; right:20mm; display:flex; align-items:center; }}
    .cp-crest {{
        width:13mm; height:13mm; border-radius:2.6mm; background:{accent};
        color:#ffffff; text-align:center; line-height:13mm; font-size:15pt; font-weight:900;
        box-shadow:0 1pt 4pt rgba(8,145,178,0.35); transform:rotate(-4deg);
    }}
    .cp-brandtext {{ margin-left:5mm; }}
    .cp-company {{ font-size:9pt; letter-spacing:3pt; font-weight:800; color:{deep}; text-transform:uppercase; }}
    .cp-tagline {{ font-size:7.5pt; letter-spacing:1pt; color:#5b7e87; margin-top:1mm; }}

    /* postmark + stamp art, top-right of card */
    .cp-stamp {{
        position:absolute; top:18mm; right:18mm; width:34mm; height:40mm;
        background:linear-gradient(155deg, #ffffff 0%, {soft} 100%);
        border:0.7mm dashed {deep}; transform:rotate(6deg);
        box-shadow:0 2pt 6pt rgba(11,43,51,0.15);
        padding:2.4mm; text-align:center;
    }}
    .cp-stamp-inner {{ border:0.3mm solid {accent}; height:100%; padding-top:4mm; }}
    .cp-stamp-icon {{ font-size:20pt; font-weight:900; color:{deep}; line-height:1; }}
    .cp-stamp-cap {{ font-size:6pt; letter-spacing:1.4pt; font-weight:800; color:{accent}; margin-top:2mm; text-transform:uppercase; }}
    .cp-stamp-val {{ font-size:9pt; font-weight:900; color:{deep}; margin-top:1mm; }}
    .cp-postmark {{
        position:absolute; top:14mm; right:8mm; width:24mm; height:24mm; border-radius:50%;
        border:0.6mm solid {deep}; transform:rotate(-12deg); opacity:0.62;
    }}
    .cp-postmark::before {{
        content:""; position:absolute; top:4mm; left:4mm; right:4mm; bottom:4mm;
        border-radius:50%; border:0.3mm dashed {deep};
    }}
    .cp-postmark-top {{ position:absolute; top:2mm; left:0; right:0; text-align:center; font-size:4.6pt; letter-spacing:1pt; font-weight:800; color:{deep}; }}
    .cp-postmark-mid {{ position:absolute; top:10mm; left:0; right:0; text-align:center; font-size:6pt; font-weight:900; color:{deep}; }}
    .cp-postmark-bot {{ position:absolute; bottom:2mm; left:0; right:0; text-align:center; font-size:4.6pt; letter-spacing:1pt; font-weight:800; color:{deep}; }}

    /* ── headline block ── */
    .cp-eyebrow {{ position:absolute; top:46mm; left:20mm; font-size:8pt; letter-spacing:3.5pt; font-weight:800; color:{accent}; text-transform:uppercase; }}
    .cp-title {{ position:absolute; top:51mm; left:20mm; right:20mm; font-size:46pt; font-weight:900; letter-spacing:-1pt; line-height:0.98; color:#0b2b33; }}
    .cp-sub {{ position:absolute; top:74mm; left:20mm; right:62mm; font-size:11pt; font-style:italic; color:#3f6671; line-height:1.4; }}

    /* ── boarding-pass card with perforated stub ── */
    .cp-pass {{
        position:absolute; top:96mm; left:20mm; right:20mm; height:40mm;
        background:#ffffff; border:0.6mm solid {accent}66; border-radius:3mm;
        box-shadow:0 3pt 10pt rgba(8,145,178,0.14); overflow:hidden;
    }}
    .cp-pass-band {{ position:absolute; top:0; left:0; width:100%; height:7mm; background:linear-gradient(90deg, {deep}, {accent}); }}
    .cp-pass-bandtext {{ position:absolute; top:1.7mm; left:6mm; font-size:7pt; letter-spacing:2.5pt; font-weight:800; color:#ffffff; text-transform:uppercase; }}
    .cp-pass-ref {{ position:absolute; top:1.7mm; right:46mm; font-size:7pt; letter-spacing:1.5pt; font-weight:700; color:#cffafe; }}
    .cp-pass-main {{ position:absolute; top:11mm; left:6mm; right:42mm; }}
    .cp-leg {{ display:inline-block; width:46%; vertical-align:top; }}
    .cp-leg .k {{ font-size:6.5pt; letter-spacing:1.6pt; font-weight:800; color:{accent}; text-transform:uppercase; }}
    .cp-leg .v {{ font-size:14pt; font-weight:900; color:#0b2b33; margin-top:1.5mm; letter-spacing:-0.3pt; }}
    .cp-leg .v small {{ font-size:8pt; font-weight:700; color:#5b7e87; }}
    .cp-arrow {{ display:inline-block; width:6%; text-align:center; font-size:13pt; color:{accent}; vertical-align:top; padding-top:5mm; }}

    /* perforation + right stub */
    .cp-perf {{
        position:absolute; top:7mm; right:40mm; bottom:0; width:0;
        border-left:0.5mm dashed {accent};
    }}
    .cp-perf::before, .cp-perf::after {{
        content:""; position:absolute; left:-2mm; width:4mm; height:4mm; border-radius:50%;
        background:#f3fcff; border:0.4mm solid {accent}55;
    }}
    .cp-perf::before {{ top:-2mm; }}
    .cp-perf::after  {{ bottom:-2mm; }}
    .cp-stub {{
        position:absolute; top:7mm; right:0; bottom:0; width:40mm;
        background:linear-gradient(160deg, {soft} 0%, #ffffff 100%);
        text-align:center; padding-top:4mm;
    }}
    .cp-stub-cap {{ font-size:6pt; letter-spacing:2pt; font-weight:800; color:{deep}; text-transform:uppercase; }}
    .cp-stub-icon {{ font-size:18pt; font-weight:900; color:{accent}; margin:1.5mm 0; }}
    .cp-stub-val {{ font-size:12pt; font-weight:900; color:{deep}; letter-spacing:-0.3pt; }}
    .cp-stub-sub {{ font-size:6.5pt; color:#5b7e87; margin-top:0.5mm; }}
    .cp-barcode {{
        position:absolute; bottom:3mm; left:5mm; right:5mm; height:5mm;
        background:repeating-linear-gradient(90deg, {deep} 0 0.5mm, transparent 0.5mm 0.9mm, {deep} 0.9mm 1.7mm, transparent 1.7mm 2.4mm);
        opacity:0.78;
    }}

    /* ── period band ── */
    .cp-period {{ position:absolute; top:142mm; left:20mm; right:20mm; }}
    .cp-period .cover-period {{ width:auto; margin:0; }}

    /* ── kpi strip ── */
    .cp-kpi {{ position:absolute; top:166mm; left:20mm; right:20mm; }}
    .cp-kpi .kpi-grid {{ width:auto; margin:0; }}
    .cp-kpi .kpi-tile {{ border-radius:1.5mm; }}

    /* ── chips + generated ── */
    .cp-chips {{ position:absolute; top:208mm; left:20mm; right:20mm; }}
    .cp-chips .chip-row {{ width:auto; margin:0; justify-content:flex-start; }}
    .cp-gen {{ position:absolute; top:220mm; left:20mm; font-size:8.5pt; color:#5b7e87; letter-spacing:0.3pt; }}

    /* tear divider across the lower third */
    .cp-tear {{
        position:absolute; top:233mm; left:14mm; right:14mm; height:0;
        border-top:0.5mm dashed {accent}88;
    }}

    /* ── footer / registry block ── */
    .cp-footer {{ position:absolute; bottom:16mm; left:20mm; right:20mm; }}
    .cp-reg {{ font-size:7pt; color:#5b7e87; letter-spacing:0.3pt; line-height:1.6; }}
    .cp-reg b {{ color:{deep}; }}
    .cp-conf {{ margin-top:2.5mm; font-size:7pt; letter-spacing:2.5pt; font-weight:800; color:{accent}; text-transform:uppercase; }}
    </style>
    """

    return f"""
    <section class="cover-postcard">
        {css}
        <div class="cp-airmail"></div>
        <div class="cp-airmail-b"></div>
        <div class="cp-frame"></div>

        <div class="cp-brand">
            <div class="cp-crest">{esc(icon)}</div>
            <div class="cp-brandtext">
                <div class="cp-company">{esc(COMPANY['legal'].upper())}</div>
                <div class="cp-tagline">{esc(meta.get('tagline', 'Compensation snapshot'))} · Payroll · {esc(meta.get('group', 'Analytics'))}</div>
            </div>
        </div>

        <div class="cp-postmark">
            <div class="cp-postmark-top">FOURRECK · PAY</div>
            <div class="cp-postmark-mid">{esc(short)}</div>
            <div class="cp-postmark-bot">FY {esc(fy)}</div>
        </div>
        <div class="cp-stamp">
            <div class="cp-stamp-inner">
                <div class="cp-stamp-icon">₹</div>
                <div class="cp-stamp-cap">Cost to Company</div>
                <div class="cp-stamp-val">{esc(str(employees))} heads</div>
            </div>
        </div>

        <div class="cp-eyebrow">Payroll · {esc(meta.get('group', 'Analytics').upper())}</div>
        <h1 class="cp-title">{esc(meta.get('name', 'CTC Summary'))}</h1>
        <p class="cp-sub">{esc(meta.get('subtitle', 'Active CTC build-up — annual · monthly · basic · regime'))}</p>

        <div class="cp-pass">
            <div class="cp-pass-band"></div>
            <div class="cp-pass-bandtext">CTC Boarding Pass</div>
            <div class="cp-pass-ref">SNAPSHOT · {esc(mname.upper())} {esc(str(year))}</div>
            <div class="cp-pass-main">
                <div class="cp-leg">
                    <div class="k">Total Annual CTC</div>
                    <div class="v">{inr_compact(annual)}</div>
                </div>
                <div class="cp-arrow">✈</div>
                <div class="cp-leg">
                    <div class="k">Total Monthly CTC</div>
                    <div class="v">{inr_compact(monthly)}</div>
                </div>
            </div>
            <div class="cp-perf"></div>
            <div class="cp-stub">
                <div class="cp-stub-cap">Avg / Head</div>
                <div class="cp-stub-icon">◈</div>
                <div class="cp-stub-val">{stub_annual}</div>
                <div class="cp-stub-sub">{inr(avg)} per annum</div>
                <div class="cp-barcode"></div>
            </div>
        </div>

        <div class="cp-period">{period_band_html(period, accent, soft, deep)}</div>

        <div class="cp-kpi">{tiles}</div>

        <div class="cp-chips">{chips}</div>
        <div class="cp-gen">Generated {now_stamp()} · {esc(str(rows))} record(s)</div>

        <div class="cp-tear"></div>

        <div class="cp-footer">
            <div class="cp-reg">
                <b>{esc(COMPANY['legal'])}</b> · {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])}<br>
                CIN {esc(COMPANY['cin'])} · PAN {esc(COMPANY['pan'])} · TAN {esc(COMPANY['tan'])} · {esc(COMPANY['email'])} · {esc(COMPANY['web'])}
            </div>
            <div class="cp-conf">Confidential · Internal use only · Do not distribute</div>
        </div>
    </section>
    """
