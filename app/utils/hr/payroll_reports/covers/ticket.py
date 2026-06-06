"""Adjustments Register — PDF cover, motif "ticket".

A carnival / event-ticket stub for the one-off pay-run amounts (bonus,
incentive, arrears, deductions). Scalloped perforation notches run down both
seams via radial-gradients, a dashed tear-line splits the main stub from the
"ADMIT ONE" counterfoil, a faux barcode anchors the foot, and the whole thing
sits in rose / crimson tones. Fun but refined — a real document an Indian HR /
finance team would file.

Public entry point:
    render(meta, summary, period, shaped_count=None) -> str  (one <section>)

Only the package's blessed helpers are imported (see CLAUDE.md contract).
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, kpi_tiles_html, now_stamp, month_name,
)


def _barcode(seed: str, *, bars: int = 58) -> str:
    """A deterministic faux Code-128-ish barcode built from inline divs.

    Widths are derived from the seed string so the same period always yields
    the same pattern (looks intentional, not random). Pure CSS — no images.
    """
    widths = (0.45, 0.9, 1.4, 0.7)
    cols = []
    s = (seed * 6)[: bars]
    x = 0.0
    for i, ch in enumerate(s):
        w = widths[(ord(ch) + i) % len(widths)]
        ink = (ord(ch) + i) % 5 != 0  # ~80% bars, rest are gaps
        cols.append(
            f'<span style="display:inline-block;width:{w:.2f}mm;height:11mm;'
            f'background:{"#1a0a10" if ink else "transparent"};vertical-align:top"></span>'
        )
        x += w
    return "".join(cols)


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#e11d48")
    soft = meta.get("accent_soft", "#ffe4e6")
    deep = meta.get("accent_deep", "#881337")
    name = meta.get("name", "Adjustments Register")
    subtitle = meta.get("subtitle", "Every one-off amount posted to the pay run")
    group = meta.get("group", "Adjustments")

    rows = shaped_count if shaped_count is not None else summary.get("rows", 0)
    employees = summary.get("employees", 0)
    additions = float(summary.get("additions", 0) or 0)
    deductions = float(summary.get("deductions", 0) or 0)
    net_impact = summary.get("net_impact")
    if net_impact is None:
        net_impact = additions - deductions

    # KPI strip — additions (emerald), deductions (crimson), net impact.
    tiles = [
        ("Postings", str(rows), accent),
        ("Employees", str(employees), "#1a0a10"),
        ("Additions", inr_compact(additions), "#047857"),
        ("Deductions", inr_compact(deductions), "#b91c1c"),
    ]

    label = period.get("label", f"{month_name(period.get('month', 1))} {period.get('year', '')}")
    fy = period.get("fy", "")
    short = period.get("short", label)
    serial = f"ADJ-{period.get('year','')}{period.get('month',0):02d}".upper()

    net_color = "#047857" if net_impact >= 0 else "#b91c1c"
    net_sign = "+" if net_impact >= 0 else "−"

    # Perforation notch backgrounds (radial-gradient scallops) reused twice.
    notch_left = (
        f"radial-gradient(circle at 0 50%, transparent 1.9mm, {accent}00 2.0mm) 0 0 / 100% 6mm repeat-y"
    )

    css = f"""
    <style>
    .cover-ticket {{
        width:210mm; height:297mm; position:relative; overflow:hidden;
        background:
            radial-gradient(circle at 84% 8%, {soft} 0, transparent 42%),
            radial-gradient(circle at 12% 96%, {soft} 0, transparent 38%),
            repeating-linear-gradient(45deg, #fff6f8 0, #fff6f8 5mm, #fffafb 5mm, #fffafb 10mm);
        font-family:'Helvetica','Arial',sans-serif; color:#1a0a10;
    }}
    .cover-ticket .tk-edge-top {{
        position:absolute; top:0; left:0; right:0; height:10mm;
        background:linear-gradient(90deg,{deep},{accent} 55%,{deep});
    }}
    .cover-ticket .tk-edge-bot {{
        position:absolute; bottom:0; left:0; right:0; height:6mm;
        background:linear-gradient(90deg,{deep},{accent} 55%,{deep});
    }}
    /* The ticket card itself */
    .cover-ticket .tk-card {{
        position:absolute; top:24mm; left:18mm; width:174mm; height:226mm;
        background:#fffdfd;
        border:0.5mm solid {accent}66;
        border-radius:5mm;
        box-shadow:0 2mm 9mm rgba(136,19,55,0.16);
    }}
    /* Scalloped perforations down both vertical seams of the card */
    .cover-ticket .tk-perf {{
        position:absolute; top:6mm; bottom:6mm; width:6mm;
        background:radial-gradient(circle at 50% 3mm, #fff6f8 1.7mm, transparent 1.8mm);
        background-size:6mm 6mm; background-repeat:repeat-y;
    }}
    .cover-ticket .tk-perf.l {{ left:-3mm; }}
    .cover-ticket .tk-perf.r {{ right:-3mm; }}
    /* Counterfoil stub on the right, split by a vertical tear-line */
    .cover-ticket .tk-stub {{
        position:absolute; top:0; right:0; bottom:0; width:46mm;
        border-left:0.4mm dashed {accent}aa;
        background:
            radial-gradient(circle at 90% 6%, {soft} 0, transparent 60%),
            linear-gradient(180deg,{accent} 0,{deep} 100%);
        border-radius:0 5mm 5mm 0;
        color:#fff;
    }}
    /* Tear-line notch circles where the dashed seam meets card edges */
    .cover-ticket .tk-notch {{
        position:absolute; width:7mm; height:7mm; border-radius:50%;
        background:#fff6f8; right:42.5mm;
    }}
    .cover-ticket .tk-notch.top {{ top:-3.5mm; }}
    .cover-ticket .tk-notch.bot {{ bottom:-3.5mm; }}

    .cover-ticket .tk-body {{ position:absolute; top:0; left:0; right:46mm; bottom:0; padding:14mm 13mm 12mm 16mm; }}

    .cover-ticket .tk-brand {{ display:flex; align-items:center; }}
    .cover-ticket .tk-crest {{
        width:13mm; height:13mm; border-radius:3mm; background:{accent};
        color:#fff; font-size:18pt; font-weight:900; text-align:center;
        line-height:13mm; box-shadow:0 1mm 3mm {accent}55;
    }}
    .cover-ticket .tk-co {{ margin-left:4mm; }}
    .cover-ticket .tk-co .nm {{ font-size:9.5pt; font-weight:900; letter-spacing:0.4pt; color:{deep}; }}
    .cover-ticket .tk-co .le {{ font-size:6.6pt; letter-spacing:1.6pt; font-weight:700; color:#9a7480; text-transform:uppercase; margin-top:0.6mm; }}

    .cover-ticket .tk-eyebrow {{
        margin-top:14mm; font-size:8pt; letter-spacing:3pt; font-weight:800;
        text-transform:uppercase; color:{accent};
    }}
    .cover-ticket .tk-eyebrow .dot {{ color:{deep}; }}
    .cover-ticket .tk-title {{
        margin:3mm 0 0; font-size:37pt; font-weight:900; line-height:1.02;
        letter-spacing:-0.8pt; color:#1a0a10;
    }}
    .cover-ticket .tk-title .amp {{ color:{accent}; }}
    .cover-ticket .tk-sub {{ margin:4mm 0 0; font-style:italic; font-size:11pt; color:#6b4651; max-width:118mm; }}

    /* Pay-period ticket chips */
    .cover-ticket .tk-period {{
        margin-top:11mm; display:flex; gap:6mm;
    }}
    .cover-ticket .tk-pchip {{
        flex:1; padding:5mm 6mm; border-radius:3mm;
        background:{soft}; border:0.4mm solid {accent}55;
    }}
    .cover-ticket .tk-pchip .lab {{ font-size:6.6pt; letter-spacing:2pt; font-weight:800; text-transform:uppercase; color:{deep}; }}
    .cover-ticket .tk-pchip .val {{ font-size:14pt; font-weight:900; color:#1a0a10; margin-top:1.5mm; letter-spacing:-0.2pt; }}

    .cover-ticket .tk-gen {{ margin-top:7mm; font-size:8.5pt; color:#9a7480; letter-spacing:0.3pt; }}

    /* Lower stack — KPIs + net ribbon + barcode flow together so they can never
       overlap regardless of value width/height (was 3 colliding absolute boxes). */
    .cover-ticket .tk-lower {{ position:absolute; left:16mm; right:50mm; bottom:12mm; }}
    .cover-ticket .tk-kpis .kpi-grid {{ width:100%; margin:0; gap:3mm; display:flex; }}
    .cover-ticket .tk-kpis .kpi-tile {{ flex:1; min-width:0; border-radius:2mm; border-top-width:2mm; padding:3.5mm 1.5mm 4mm; }}
    .cover-ticket .tk-kpis .kpi-label {{ font-size:6pt; letter-spacing:0.8pt; margin:1.5mm 0 2mm; }}
    .cover-ticket .tk-kpis .kpi-value {{ font-size:13pt; white-space:nowrap; }}

    /* Net-impact ribbon */
    .cover-ticket .tk-net {{
        margin-top:5mm; background:#1a0a10; color:#fff; border-radius:3mm; padding:4mm 7mm;
        display:flex; justify-content:space-between; align-items:center; gap:6mm;
    }}
    .cover-ticket .tk-net .lab {{ font-size:7.5pt; letter-spacing:2.2pt; font-weight:800; text-transform:uppercase; color:{soft}; }}
    .cover-ticket .tk-net .val {{ font-size:18pt; font-weight:900; letter-spacing:-0.4pt; font-variant-numeric:tabular-nums; white-space:nowrap; }}

    /* Barcode foot */
    .cover-ticket .tk-barcode {{ margin-top:5mm; text-align:left; }}
    .cover-ticket .tk-barcode .serial {{ font-family:'Courier New',monospace; font-size:7.5pt; letter-spacing:2pt; color:#6b4651; margin-top:1.5mm; }}

    /* Stub interior — ADMIT ONE counterfoil */
    .cover-ticket .tk-stub .si {{ position:absolute; inset:0; padding:16mm 6mm; text-align:center; }}
    .cover-ticket .tk-stub .admit {{
        font-size:8pt; letter-spacing:4pt; font-weight:800; text-transform:uppercase; opacity:0.92;
    }}
    .cover-ticket .tk-stub .one {{ font-size:24pt; font-weight:900; letter-spacing:-0.5pt; margin-top:2mm; line-height:1; }}
    .cover-ticket .tk-stub .rule {{ width:22mm; height:0.6mm; background:#fff; opacity:0.55; margin:6mm auto; border-radius:0.3mm; }}
    .cover-ticket .tk-stub .types {{ margin-top:2mm; }}
    .cover-ticket .tk-stub .types .t {{
        display:block; font-size:9pt; font-weight:900; letter-spacing:1.2pt;
        text-transform:uppercase; margin:3.5mm 0; opacity:0.96;
    }}
    .cover-ticket .tk-stub .types .t small {{ display:block; font-size:6pt; letter-spacing:1.5pt; font-weight:700; opacity:0.7; margin-top:0.6mm; }}
    /* Vertical serial running up the stub */
    .cover-ticket .tk-stub .vserial {{
        position:absolute; bottom:14mm; left:0; right:0; text-align:center;
        font-family:'Courier New',monospace; font-size:7pt; letter-spacing:3pt; opacity:0.85;
    }}
    .cover-ticket .tk-stub .stub-perf {{
        position:absolute; left:-3mm; top:6mm; bottom:6mm; width:6mm;
        background:radial-gradient(circle at 50% 3mm, #fff6f8 1.7mm, transparent 1.8mm);
        background-size:6mm 6mm; background-repeat:repeat-y;
    }}

    /* Footer */
    .cover-ticket .tk-footer {{
        position:absolute; left:18mm; right:18mm; bottom:8.5mm; text-align:center;
        font-size:7.2pt; color:#9a7480;
    }}
    .cover-ticket .tk-footer .legal {{ font-weight:700; letter-spacing:0.4pt; }}
    .cover-ticket .tk-footer .conf {{ margin-top:1mm; font-size:6.8pt; letter-spacing:2.4pt; text-transform:uppercase; color:{deep}; }}
    </style>
    """

    types_html = "".join(
        f'<span class="t">{lab}<small>{sub}</small></span>'
        for lab, sub in (
            ("BONUS", "performance"),
            ("INCENTIVE", "variable pay"),
            ("ARREARS", "back-dated"),
            ("DEDUCTION", "recovery"),
        )
    )

    return css + f"""
    <section class="cover cover-ticket">
        <div class="tk-edge-top"></div>
        <div class="tk-edge-bot"></div>

        <div class="tk-card">
            <div class="tk-perf l"></div>
            <div class="tk-perf r"></div>
            <div class="tk-notch top"></div>
            <div class="tk-notch bot"></div>

            <div class="tk-body">
                <div class="tk-brand">
                    <div class="tk-crest">{esc(meta.get('icon', '✦'))}</div>
                    <div class="tk-co">
                        <div class="nm">{esc(COMPANY['name'])}</div>
                        <div class="le">{esc(COMPANY['legal'])}</div>
                    </div>
                </div>

                <div class="tk-eyebrow">PAYROLL <span class="dot">·</span> {esc(group.upper())}</div>
                <h1 class="tk-title">Adjustments <span class="amp">&amp;</span><br>Register</h1>
                <p class="tk-sub">{esc(subtitle)}</p>

                <div class="tk-period">
                    <div class="tk-pchip">
                        <div class="lab">Pay Period</div>
                        <div class="val">{esc(label)}</div>
                    </div>
                    <div class="tk-pchip">
                        <div class="lab">Fiscal Year</div>
                        <div class="val">FY {esc(fy)}</div>
                    </div>
                </div>

                <div class="tk-gen">Issued {esc(now_stamp())}</div>
            </div>

            <div class="tk-lower">
                <div class="tk-kpis">{kpi_tiles_html(tiles)}</div>

                <div class="tk-net">
                    <div class="lab">Net Impact on Pay Run</div>
                    <div class="val" style="color:{net_color}">{net_sign}&nbsp;{esc(inr(abs(net_impact)))}</div>
                </div>

                <div class="tk-barcode">
                    <div>{_barcode(serial)}</div>
                    <div class="serial">{esc(serial)} · {esc(short)}</div>
                </div>
            </div>

            <div class="tk-stub">
                <div class="stub-perf"></div>
                <div class="si">
                    <div class="admit">Admit One</div>
                    <div class="one">PAYRUN</div>
                    <div class="rule"></div>
                    <div class="types">{types_html}</div>
                    <div class="rule"></div>
                    <div class="vserial">{esc(serial)}</div>
                </div>
            </div>
        </div>

        <div class="tk-footer">
            <div class="legal">{esc(COMPANY['legal'])} · {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])}</div>
            <div class="conf">Confidential · Internal payroll use only</div>
        </div>
    </section>
    """
