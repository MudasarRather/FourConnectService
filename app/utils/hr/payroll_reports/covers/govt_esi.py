"""ESI Contribution Statement cover — motif "govt-esi".

An ESIC government contribution statement styled like a state-issued medical
insurance card. Blue officialdom, a CSS health-shield / medical-cross emblem,
an insurance-card header band, boxed statutory fields (Employer Code /
Contribution Period), a perforated edge of repeating dots, and monospace IP
numbers. Deliberately distinct from the PF green form.

Public entry point::

    render(meta, summary, period, shaped_count=None) -> str
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, inr_group, kpi_tiles_html, now_stamp,
    fmt_long_date,
)


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#0369a1")
    soft = meta.get("accent_soft", "#e0f2fe")
    deep = meta.get("accent_deep", "#0c4a6e")

    members = shaped_count if shaped_count is not None else summary.get("rows", summary.get("employees", 0))
    employees = summary.get("employees", members)
    esi_total = summary.get("esi", 0) or 0

    # ESI split: EE = 0.75% of wages, ER = 3.25%; total split ≈ 0.75 : 3.25 = 18.75% : 81.25%.
    ee_share = esi_total * (0.75 / 4.0)
    er_share = esi_total * (3.25 / 4.0)

    tiles = [
        ("Insured Members", inr_group(members), accent),
        ("Employees", inr_group(employees), deep),
        ("EE Contribution", inr_compact(ee_share), "#0e7490"),
        ("Total Payable", inr_compact(esi_total), "#0f766e"),
    ]

    period_label = esc(period.get("label", ""))
    fy = esc(period.get("fy", ""))
    short = esc(period.get("short", period.get("label", "")))

    # A contribution period for ESIC is one of two half-year windows (Apr–Sep,
    # Oct–Mar). Derive a human label from the month for the boxed field.
    month = int(period.get("month", 0) or 0)
    if 4 <= month <= 9:
        contrib_window = "Apr – Sep"
    else:
        contrib_window = "Oct – Mar"

    css = f""".cover-govt-esi {{
        font-family:'Helvetica','Arial',sans-serif;
        background:
            radial-gradient(circle at 86% 8%, {soft} 0%, rgba(255,255,255,0) 42%),
            linear-gradient(180deg, #ffffff 0%, #f6fbff 60%, #eef7fd 100%);
    }}
    .cover-govt-esi .ge-perf-l, .cover-govt-esi .ge-perf-r {{
        position:absolute; top:0; bottom:0; width:6mm;
        background-image: repeating-linear-gradient(
            to bottom, {accent}33 0mm, {accent}33 1.4mm, transparent 1.4mm, transparent 5mm);
    }}
    .cover-govt-esi .ge-perf-l {{ left:7mm; }}
    .cover-govt-esi .ge-perf-r {{ right:7mm; }}
    .cover-govt-esi .ge-topbar {{
        position:absolute; top:0; left:0; right:0; height:13mm;
        background:linear-gradient(90deg, {deep} 0%, {accent} 55%, #0ea5e9 100%);
    }}
    .cover-govt-esi .ge-topbar-strip {{
        position:absolute; top:13mm; left:0; right:0; height:2mm;
        background:repeating-linear-gradient(90deg, {deep} 0 6mm, #ffffff 6mm 7mm);
        opacity:.55;
    }}
    .cover-govt-esi .ge-botbar {{
        position:absolute; bottom:0; left:0; right:0; height:7mm;
        background:linear-gradient(90deg, {accent}, {deep});
    }}

    /* ── Government header line ── */
    .cover-govt-esi .ge-gov {{
        position:absolute; top:20mm; left:20mm; right:20mm; text-align:center;
    }}
    .cover-govt-esi .ge-gov .seal-row {{
        font-size:7pt; letter-spacing:3.5pt; font-weight:800;
        color:{deep}; text-transform:uppercase;
    }}
    .cover-govt-esi .ge-gov .authority {{
        margin-top:1.5mm; font-size:9pt; letter-spacing:2pt; font-weight:800;
        color:{accent}; text-transform:uppercase;
    }}

    /* ── Insurance-card panel ── */
    .cover-govt-esi .ge-card {{
        position:absolute; top:34mm; left:20mm; right:20mm; height:62mm;
        border:1pt solid {accent}66; border-radius:4mm;
        background:linear-gradient(135deg, #ffffff 0%, {soft} 100%);
        box-shadow:0 2pt 9pt rgba(3,105,161,0.13);
        overflow:hidden;
    }}
    .cover-govt-esi .ge-card-tab {{
        position:absolute; top:0; left:0; right:0; height:9mm;
        background:linear-gradient(90deg, {deep}, {accent});
        color:#fff; font-size:7.5pt; letter-spacing:2.5pt; font-weight:800;
        text-transform:uppercase; line-height:9mm; padding-left:8mm;
    }}
    .cover-govt-esi .ge-card-tab .card-no {{
        position:absolute; right:8mm; top:0; font-family:'Courier New',monospace;
        letter-spacing:1pt; font-weight:700; font-size:7.5pt;
    }}
    /* health-shield + medical cross emblem */
    .cover-govt-esi .ge-shield {{
        position:absolute; top:16mm; left:9mm; width:30mm; height:34mm;
        background:linear-gradient(160deg, {accent} 0%, {deep} 100%);
        border-radius:5mm 5mm 14mm 14mm / 5mm 5mm 24mm 24mm;
        box-shadow:0 2pt 6pt rgba(12,74,110,0.32);
    }}
    .cover-govt-esi .ge-shield::before {{
        content:""; position:absolute; top:2mm; left:2mm; right:2mm; bottom:2mm;
        border:0.7pt solid rgba(255,255,255,0.55);
        border-radius:4mm 4mm 12mm 12mm / 4mm 4mm 20mm 20mm;
    }}
    .cover-govt-esi .ge-cross-v {{
        position:absolute; top:9mm; left:13mm; width:4mm; height:16mm;
        background:#ffffff; border-radius:1mm;
    }}
    .cover-govt-esi .ge-cross-h {{
        position:absolute; top:15mm; left:7mm; width:16mm; height:4mm;
        background:#ffffff; border-radius:1mm;
    }}
    .cover-govt-esi .ge-title-wrap {{
        position:absolute; top:15mm; left:46mm; right:8mm;
    }}
    .cover-govt-esi .ge-eyebrow {{
        font-size:7pt; letter-spacing:3pt; font-weight:800; color:{accent};
        text-transform:uppercase;
    }}
    .cover-govt-esi .ge-title {{
        margin:2mm 0 2mm; font-size:25pt; font-weight:900; color:#0b2942;
        line-height:1.02; letter-spacing:-0.4pt;
    }}
    .cover-govt-esi .ge-sub {{
        font-size:9pt; font-style:italic; color:{deep};
    }}
    .cover-govt-esi .ge-rate-row {{
        position:absolute; bottom:6mm; left:46mm; right:8mm;
        display:flex; gap:4mm;
    }}
    .cover-govt-esi .ge-rate {{
        flex:1; border:0.7pt solid {accent}55; border-radius:2mm;
        background:rgba(255,255,255,0.7); padding:2.5mm 3mm;
    }}
    .cover-govt-esi .ge-rate .rk {{
        font-size:6.5pt; letter-spacing:1.5pt; font-weight:800; color:{deep};
        text-transform:uppercase;
    }}
    .cover-govt-esi .ge-rate .rv {{
        font-size:13pt; font-weight:900; color:{accent}; margin-top:0.6mm;
        font-family:'Courier New',monospace;
    }}

    /* ── Boxed statutory fields ── */
    .cover-govt-esi .ge-fields {{
        position:absolute; top:101mm; left:20mm; right:20mm;
        display:flex; gap:4mm;
    }}
    .cover-govt-esi .ge-field {{
        flex:1; border:0.8pt solid {accent}55; border-top:1.8mm solid {accent};
        border-radius:1.5mm; background:#ffffff; padding:3mm 3.5mm 3.5mm;
    }}
    .cover-govt-esi .ge-field .fk {{
        font-size:6.5pt; letter-spacing:1.5pt; font-weight:800; color:{deep};
        text-transform:uppercase;
    }}
    .cover-govt-esi .ge-field .fv {{
        margin-top:1.5mm; font-size:11pt; font-weight:800; color:#0b2942;
        font-family:'Courier New',monospace; letter-spacing:0.2pt;
    }}
    .cover-govt-esi .ge-field .fv.small {{ font-size:9pt; }}

    .cover-govt-esi .ge-gen {{
        position:absolute; top:120mm; left:20mm; right:20mm; text-align:center;
        font-size:8pt; color:{deep}; letter-spacing:0.4pt;
    }}

    /* KPI strip override (positioned) */
    .cover-govt-esi .ge-kpi {{
        position:absolute; top:130mm; left:20mm; right:20mm;
    }}
    .cover-govt-esi .ge-kpi .kpi-grid {{ width:auto; margin:0; }}
    .cover-govt-esi .ge-kpi .kpi-tile {{
        border:0.7pt solid {accent}40; border-top-width:2.5mm;
        background:#ffffff; box-shadow:0 1pt 4pt rgba(3,105,161,0.10);
    }}

    /* ── Declaration / footer ── */
    .cover-govt-esi .ge-decl {{
        position:absolute; bottom:30mm; left:20mm; right:20mm;
        border:0.6pt dashed {accent}66; border-radius:2mm;
        background:rgba(224,242,254,0.45); padding:4mm 5mm;
    }}
    .cover-govt-esi .ge-decl .dh {{
        font-size:7pt; letter-spacing:2pt; font-weight:800; color:{deep};
        text-transform:uppercase; margin-bottom:1.5mm;
    }}
    .cover-govt-esi .ge-decl .dt {{
        font-size:7.5pt; color:#334155; line-height:1.45;
    }}
    .cover-govt-esi .ge-sign {{
        position:absolute; bottom:13mm; right:20mm; text-align:center;
        width:55mm;
    }}
    .cover-govt-esi .ge-sign .sig-line {{
        border-top:0.8pt solid {deep}; margin-bottom:1.5mm;
    }}
    .cover-govt-esi .ge-sign .sig-cap {{
        font-size:6.5pt; letter-spacing:1pt; font-weight:700; color:{deep};
        text-transform:uppercase;
    }}
    .cover-govt-esi .ge-foot {{
        position:absolute; bottom:11mm; left:20mm; width:95mm;
        font-size:6.8pt; color:{deep};
    }}
    .cover-govt-esi .ge-foot .lg {{ font-weight:700; }}
    .cover-govt-esi .ge-foot .cf {{
        margin-top:1mm; letter-spacing:2pt; text-transform:uppercase;
        font-size:6.2pt; color:{accent};
    }}
    """

    return f"""
    <section class="cover cover-govt-esi">
        <style>{css}</style>
        <div class="ge-topbar"></div>
        <div class="ge-topbar-strip"></div>
        <div class="ge-botbar"></div>
        <div class="ge-perf-l"></div>
        <div class="ge-perf-r"></div>

        <div class="ge-gov">
            <div class="seal-row">★  Employees' State Insurance Corporation  ★</div>
            <div class="authority">Ministry of Labour &amp; Employment · Government of India</div>
        </div>

        <div class="ge-card">
            <div class="ge-card-tab">
                ESIC Medical Benefit · Contribution Card
                <span class="card-no">FORM&nbsp;ESIC-{esc(period.get('year', ''))}</span>
            </div>
            <div class="ge-shield">
                <div class="ge-cross-v"></div>
                <div class="ge-cross-h"></div>
            </div>
            <div class="ge-title-wrap">
                <div class="ge-eyebrow">Statutory Filing · Monthly Return</div>
                <h1 class="ge-title">{esc(meta.get('name', 'ESI Contribution Statement'))}</h1>
                <div class="ge-sub">{esc(meta.get('subtitle', 'Insurable wages · 0.75% EE · 3.25% ER per member'))}</div>
            </div>
            <div class="ge-rate-row">
                <div class="ge-rate"><div class="rk">Employee Share</div><div class="rv">0.75%</div></div>
                <div class="ge-rate"><div class="rk">Employer Share</div><div class="rv">3.25%</div></div>
                <div class="ge-rate"><div class="rk">Wage Ceiling</div><div class="rv">₹21,000</div></div>
            </div>
        </div>

        <div class="ge-fields">
            <div class="ge-field">
                <div class="fk">Employer Code No.</div>
                <div class="fv">31-00098765-001</div>
            </div>
            <div class="ge-field">
                <div class="fk">Contribution Month</div>
                <div class="fv small">{period_label}</div>
            </div>
            <div class="ge-field">
                <div class="fk">Contribution Period</div>
                <div class="fv small">{contrib_window} · FY {fy}</div>
            </div>
        </div>

        <div class="ge-gen">Generated {now_stamp()} · Return reference period {short}</div>

        <div class="ge-kpi">
            {kpi_tiles_html(tiles)}
        </div>

        <div class="ge-decl">
            <div class="dh">Declaration by Principal Employer</div>
            <div class="dt">Certified that the particulars of insurable wages and the
            contributions of {esc(inr_group(members))} insured member(s) shown above are true and
            correct to the best of knowledge, and that the employee &amp; employer
            shares aggregating {esc(inr(esi_total))} have been computed at the
            prescribed rates under the ESI Act, 1948.</div>
        </div>

        <div class="ge-sign">
            <div class="sig-line"></div>
            <div class="sig-cap">Authorised Signatory · {esc(COMPANY['name'])}</div>
        </div>

        <div class="ge-foot">
            <div class="lg">{esc(COMPANY['legal'])} · PAN {esc(COMPANY['pan'])} · TAN {esc(COMPANY['tan'])}</div>
            <div class="cf">Confidential · Statutory filing · Not for circulation</div>
        </div>
    </section>
    """
