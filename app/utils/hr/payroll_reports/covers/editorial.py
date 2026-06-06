"""PDF cover — motif "editorial" for the Salary Sheet report.

A high-end magazine editorial spread: twin gold bands top and bottom, a
centered masthead rule with the pay period as a kicker, a very large Georgia
serif display title, an italic justified standfirst with a drop-cap flourish,
a corporate KPI strip, and a confidential colophon footer. Lots of negative
space, confident typography, zero clutter.

Renders for an A4 page (210mm x 297mm). All art is CSS — no external assets.
WeasyPrint-safe: mm/pt units only, every selector scoped under .cover-editorial.
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, inr_group, kpi_tiles_html,
    now_stamp, month_name,
)


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#d97706")
    soft = meta.get("accent_soft", "#fef3c7")
    deep = meta.get("accent_deep", "#92400e")

    name = esc(meta.get("name", "Salary Sheet"))
    tagline = esc(meta.get("tagline", "Earnings & deductions breakdown"))
    subtitle = meta.get("subtitle", "Head-wise build-up of every employee's pay")

    label = esc(period.get("label", ""))
    fy = esc(period.get("fy", ""))
    short = esc(period.get("short", label))
    year = period.get("year", "")
    mon = esc(month_name(period.get("month", 0)).upper()) if period.get("month") else short

    # ── KPIs ───────────────────────────────────────────────────────────────
    rows = shaped_count if shaped_count is not None else summary.get("rows", 0)
    employees = summary.get("employees", rows)
    gross = summary.get("gross", 0)
    net = summary.get("net", 0)
    deductions = summary.get("deductions", 0)
    avg_net = summary.get("avg_net", 0)

    tiles = [
        ("Employees Paid", f"{int(employees or 0)}", deep),
        ("Gross Earnings", inr_compact(gross), "#b8860b"),
        ("Total Deductions", inr_compact(deductions), "#b91c1c"),
        ("Net Disbursed", inr_compact(net), "#047857"),
    ]
    kpis = kpi_tiles_html(tiles)

    # secondary supporting figures rendered as an editorial "by the numbers" rule
    net_pct = (net / gross * 100) if gross else 0.0
    ded_pct = (deductions / gross * 100) if gross else 0.0

    # standfirst standfirst with a drop cap — first letter pulled out
    sf = esc(subtitle)
    drop, rest = ("", sf)
    if sf:
        drop, rest = sf[0], sf[1:]

    generated = esc(now_stamp())
    legal = esc(COMPANY["legal"])
    addr = f"{esc(COMPANY['address_1'])} · {esc(COMPANY['address_2'])}"
    crest = esc(COMPANY.get("icon", "≣") if False else meta.get("icon", "≣"))

    css = f"""
    <style>
    .cover-editorial {{
        width:210mm; height:297mm; position:relative; overflow:hidden;
        background:#fffdf8; page-break-after:always;
        font-family:'Helvetica','Arial',sans-serif; color:#1a1410;
        padding:0; margin:0;
    }}
    /* ── twin gold bands ── */
    .cover-editorial .band {{
        position:absolute; left:0; right:0; height:11mm;
        background:linear-gradient(90deg,{deep} 0%,{accent} 45%,{accent} 55%,{deep} 100%);
    }}
    .cover-editorial .band.top {{ top:0; }}
    .cover-editorial .band.bottom {{ bottom:0; }}
    .cover-editorial .hairline {{
        position:absolute; left:0; right:0; height:0.5mm; background:{accent};
    }}
    .cover-editorial .hairline.top {{ top:13mm; }}
    .cover-editorial .hairline.bottom {{ bottom:13mm; }}
    /* faint editorial texture — vertical ruling like a printed page guide */
    .cover-editorial .texture {{
        position:absolute; top:14mm; bottom:14mm; left:0; right:0;
        background:repeating-linear-gradient(0deg, transparent 0, transparent 7.4mm,
            rgba(217,119,6,0.035) 7.4mm, rgba(217,119,6,0.035) 7.5mm);
    }}
    /* ── masthead ── */
    .cover-editorial .masthead {{
        position:absolute; top:20mm; left:24mm; right:24mm; text-align:center;
    }}
    .cover-editorial .crest {{
        display:inline-block; width:15mm; height:15mm; line-height:15mm;
        border:0.8pt solid {accent}; border-radius:50%;
        font-family:Georgia,serif; font-size:17pt; font-weight:700;
        color:{deep}; background:{soft}; margin-bottom:3.5mm;
    }}
    .cover-editorial .pub {{
        font-size:8pt; letter-spacing:5pt; font-weight:800; color:{deep};
        text-transform:uppercase;
    }}
    .cover-editorial .pub-sub {{
        font-size:6.5pt; letter-spacing:2.5pt; font-weight:600; color:#8a7c63;
        text-transform:uppercase; margin-top:1.5mm;
    }}
    .cover-editorial .masthead-rule {{
        margin:5mm auto 0; width:130mm; border:0; border-top:1.2pt solid {deep};
        position:relative;
    }}
    .cover-editorial .kicker {{
        position:absolute; top:-2.6mm; left:50%; margin-left:-30mm; width:60mm;
        background:#fffdf8; text-align:center;
        font-size:8pt; letter-spacing:3pt; font-weight:800; color:{accent};
        text-transform:uppercase;
    }}
    /* ── editorial title block ── */
    .cover-editorial .lede {{
        position:absolute; top:70mm; left:22mm; right:22mm; text-align:center;
    }}
    .cover-editorial .vol {{
        font-size:7.5pt; letter-spacing:3pt; font-weight:700; color:#8a7c63;
        text-transform:uppercase; margin-bottom:6mm;
    }}
    .cover-editorial .display {{
        font-family:Georgia,'Times New Roman',serif;
        font-size:58pt; font-weight:700; line-height:0.98; letter-spacing:-1.5pt;
        color:#1a1410; margin:0;
    }}
    .cover-editorial .display .em {{ font-style:italic; color:{deep}; }}
    .cover-editorial .tagline {{
        font-family:Georgia,serif; font-size:13pt; font-style:italic;
        color:{accent}; margin:5mm 0 0; letter-spacing:0.2pt;
    }}
    /* standfirst — justified two-column-feel paragraph with drop cap */
    .cover-editorial .standfirst {{
        margin:8mm auto 0; width:150mm; text-align:justify; text-align-last:center;
        font-family:Georgia,serif; font-size:11pt; font-style:italic; line-height:1.55;
        color:#3a322a;
    }}
    .cover-editorial .standfirst .dropcap {{
        font-family:Georgia,serif; font-style:normal; font-size:30pt; font-weight:700;
        color:{accent}; line-height:1; float:left; padding:1mm 2mm 0 0; margin-top:-1mm;
    }}
    /* by-the-numbers editorial rule */
    .cover-editorial .bynum {{
        position:absolute; top:172mm; left:30mm; right:30mm;
        display:flex; justify-content:space-between; align-items:baseline;
        border-top:0.6pt solid {accent}66; border-bottom:0.6pt solid {accent}66;
        padding:3.5mm 0;
    }}
    .cover-editorial .bynum .seg {{ text-align:center; flex:1; }}
    .cover-editorial .bynum .seg .v {{
        font-family:Georgia,serif; font-size:16pt; font-weight:700; color:{deep};
        letter-spacing:-0.3pt;
    }}
    .cover-editorial .bynum .seg .l {{
        font-size:6.5pt; letter-spacing:2pt; font-weight:700; color:#8a7c63;
        text-transform:uppercase; margin-top:1.5mm;
    }}
    .cover-editorial .bynum .divider {{
        width:0.5pt; align-self:stretch; background:{accent}44; margin:0 2mm;
    }}
    /* period band */
    .cover-editorial .period {{
        position:absolute; top:188mm; left:30mm; right:30mm;
        background:{soft}; border:0.8pt solid {accent}66; border-radius:2mm;
        padding:4.5mm 7mm; display:flex; justify-content:space-between; align-items:center;
    }}
    .cover-editorial .period .blk .l {{
        font-size:6.5pt; letter-spacing:2.5pt; font-weight:800; color:{deep};
        text-transform:uppercase;
    }}
    .cover-editorial .period .blk .v {{
        font-size:12pt; font-weight:800; color:#1a1410; margin-top:1.5mm; letter-spacing:-0.1pt;
    }}
    .cover-editorial .period .arrow {{ font-size:13pt; color:{accent}; }}
    .cover-editorial .period .blk.r {{ text-align:right; }}
    /* KPI strip wrapper — re-use shared .kpi-grid look but reposition */
    .cover-editorial .kpi-wrap {{ position:absolute; top:208mm; left:20mm; right:20mm; }}
    .cover-editorial .kpi-wrap .kpi-grid {{ width:170mm; margin:0 auto; gap:4mm; display:flex; }}
    .cover-editorial .kpi-wrap .kpi-tile {{
        flex:1; padding:5mm 3mm 5.5mm; background:#ffffff; text-align:center;
        border:0.6pt solid #d8cdb5; border-top:2mm solid {accent};
    }}
    .cover-editorial .kpi-wrap .kpi-label {{
        font-size:6.5pt; letter-spacing:1.2pt; font-weight:800; color:#6b5840;
        text-transform:uppercase; margin:0 0 2mm;
    }}
    .cover-editorial .kpi-wrap .kpi-value {{
        font-size:16pt; font-weight:900; line-height:1; letter-spacing:-0.4pt;
        font-variant-numeric:tabular-nums;
    }}
    .cover-editorial .generated {{
        position:absolute; top:248mm; left:0; right:0; text-align:center;
        font-size:8pt; color:#8a7c63; letter-spacing:0.4pt;
    }}
    /* colophon footer */
    .cover-editorial .colophon {{
        position:absolute; left:24mm; right:24mm; bottom:18mm; text-align:center;
    }}
    .cover-editorial .colophon .legal {{
        font-size:7.5pt; font-weight:700; color:#6b5840; letter-spacing:0.3pt;
    }}
    .cover-editorial .colophon .addr {{
        font-size:6.8pt; color:#8a7c63; margin-top:1mm; letter-spacing:0.2pt;
    }}
    .cover-editorial .colophon .ids {{
        font-size:6.3pt; color:#a89a80; margin-top:1.5mm; letter-spacing:0.6pt;
    }}
    .cover-editorial .colophon .conf {{
        display:inline-block; margin-top:2.5mm; padding:1.2mm 4mm;
        background:{deep}; color:{soft}; border-radius:6mm;
        font-size:6.5pt; font-weight:800; letter-spacing:2.5pt; text-transform:uppercase;
    }}
    </style>
    """

    html = f"""
    <section class="cover-editorial">
        {css}
        <div class="texture"></div>
        <div class="band top"></div>
        <div class="hairline top"></div>
        <div class="band bottom"></div>
        <div class="hairline bottom"></div>

        <div class="masthead">
            <span class="crest">{crest}</span>
            <div class="pub">{esc(COMPANY['name'])} &nbsp;PAYROLL&nbsp; REVIEW</div>
            <div class="pub-sub">{esc(meta.get('group', 'Core').upper())} EDITION · CONFIDENTIAL</div>
            <hr class="masthead-rule">
            <div class="kicker">{label} &nbsp;·&nbsp; FY {fy}</div>
        </div>

        <div class="lede">
            <div class="vol">Vol. {esc(str(year))} &nbsp;·&nbsp; {mon} ISSUE &nbsp;·&nbsp; {int(rows or 0)} ENTRIES</div>
            <h1 class="display">{name.split(' ')[0]} <span class="em">{' '.join(name.split(' ')[1:]) or 'Sheet'}</span></h1>
            <div class="tagline">{tagline}</div>
            <p class="standfirst"><span class="dropcap">{esc(drop)}</span>{esc(rest)} — a complete head-wise build-up of basic, HRA and allowances against every statutory and voluntary deduction, reconciled to net pay for the period.</p>
        </div>

        <div class="bynum">
            <div class="seg"><div class="v">{inr_group(round(avg_net))}</div><div class="l">Average Net / Head</div></div>
            <div class="divider"></div>
            <div class="seg"><div class="v">{net_pct:.1f}%</div><div class="l">Net of Gross</div></div>
            <div class="divider"></div>
            <div class="seg"><div class="v">{ded_pct:.1f}%</div><div class="l">Deduction Load</div></div>
            <div class="divider"></div>
            <div class="seg"><div class="v">{inr_compact(gross)}</div><div class="l">Gross Run</div></div>
        </div>

        <div class="period">
            <div class="blk"><div class="l">Pay Period</div><div class="v">{label}</div></div>
            <div class="arrow">⟶</div>
            <div class="blk r"><div class="l">Fiscal Year</div><div class="v">{fy}</div></div>
        </div>

        <div class="kpi-wrap">{kpis}</div>

        <div class="generated">Generated {generated}</div>

        <div class="colophon">
            <div class="legal">{legal}</div>
            <div class="addr">{addr}</div>
            <div class="ids">CIN {esc(COMPANY['cin'])} &nbsp;·&nbsp; PAN {esc(COMPANY['pan'])} &nbsp;·&nbsp; TAN {esc(COMPANY['tan'])} &nbsp;·&nbsp; {esc(COMPANY['web'])}</div>
            <span class="conf">Confidential · Internal Use Only</span>
        </div>
    </section>
    """
    return html
