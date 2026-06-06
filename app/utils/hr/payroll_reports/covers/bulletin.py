"""Cover motif: BULLETIN — a newsroom financial broadsheet.

A broadsheet masthead with a double rule, a dark stock-ticker band running
monospace up/down-triangle movers, a huge delta glyph, and a "vs <prior month>"
kicker. Caution-yellow. Reads like a market report filed by a finance desk.

Public entry: render(meta, summary, period, shaped_count=None) -> str
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, inr_group, kpi_tiles_html, now_stamp,
    fmt_long_date, month_name,
)


def _prior_month_label(period: dict) -> str:
    """'May 2026' → 'April 2026' for the 'vs ...' kicker."""
    try:
        y = int(period.get("year"))
        m = int(period.get("month"))
    except (TypeError, ValueError):
        return "prior period"
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{month_name(pm)} {py}"


def render(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#ca8a04")
    soft = meta.get("accent_soft", "#fef9c3")
    deep = meta.get("accent_deep", "#713f12")

    rows = shaped_count if shaped_count is not None else summary.get("rows", 0)
    movers = int(summary.get("movers", 0) or 0)
    employees = int(summary.get("employees", 0) or 0)
    net_delta = float(summary.get("net_delta", 0) or 0)
    net = float(summary.get("net", 0) or 0)

    # net movement direction drives the masthead delta colour
    up = net_delta > 0
    flat = net_delta == 0
    move_color = "#b91c1c" if (net_delta < 0) else ("#047857" if up else "#6b7280")
    move_arrow = "▲" if up else ("▼" if net_delta < 0 else "▶")
    move_word = "GAINS" if up else ("DECLINES" if net_delta < 0 else "HOLDS FLAT")

    prior = _prior_month_label(period)
    issue_no = f"{period.get('year', '')}·{int(period.get('month', 0) or 0):02d}"

    # ── KPI strip (4 tiles) via the shared helper ──
    tiles = [
        ("Movers", str(movers), accent),
        ("On Payroll", str(employees), "#1a1410"),
        ("Net Movement", inr_compact(net_delta), move_color),
        ("Net Payout", inr_compact(net), "#047857"),
    ]
    kpi = kpi_tiles_html(tiles)

    # ── stock-ticker band: synthesise headline tape from the summary ──
    steady = max(0, rows - movers)
    avg_move = (net_delta / movers) if movers else 0.0
    ticker_items = [
        ("NETPAY", "▲" if up else ("▼" if net_delta < 0 else "▶"),
         inr_compact(abs(net_delta)), move_color),
        ("MOVERS", "●", f"{movers}/{rows}", accent),
        ("STEADY", "■", str(steady), "#6b7280"),
        ("HEADS", "◆", str(employees), "#cbb27a"),
        ("AVG/MOVE", "▲" if avg_move > 0 else ("▼" if avg_move < 0 else "▶"),
         inr_compact(abs(avg_move)), move_color),
        ("PAYOUT", "▶", inr_compact(net), "#cbb27a"),
    ]
    ticker = "".join(
        f'<span class="tk-item">'
        f'<span class="tk-sym">{esc(sym)}</span>'
        f'<span class="tk-tag">{esc(tag)}</span>'
        f'<span class="tk-val" style="color:{col}">{esc(val)}</span>'
        f'</span><span class="tk-sep">·</span>'
        for tag, sym, val, col in ticker_items
    )

    legal = esc(COMPANY["legal"])
    addr = f"{esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])}"
    regline = f"CIN {esc(COMPANY['cin'])}  ·  PAN {esc(COMPANY['pan'])}  ·  {esc(COMPANY['web'])}"

    return f"""
    <section class="cover cover-bulletin">
      <style>
        .cover-bulletin {{
            padding:0; background:#fffdf5;
            font-family:'Georgia','Times New Roman',serif; color:#161310;
        }}
        .cover-bulletin .cb-edge-l {{ position:absolute; top:0; bottom:0; left:0; width:5mm; background:linear-gradient(180deg,{accent},{deep}); }}
        .cover-bulletin .cb-edge-r {{ position:absolute; top:0; bottom:0; right:0; width:5mm; background:linear-gradient(180deg,{deep},{accent}); }}

        /* ── Masthead ── */
        .cover-bulletin .cb-masthead {{ position:absolute; top:0; left:5mm; right:5mm; padding:11mm 14mm 0; }}
        .cover-bulletin .cb-tagrow {{ display:flex; justify-content:space-between; align-items:baseline;
            font-family:'Helvetica','Arial',sans-serif; font-size:7pt; letter-spacing:2.6pt;
            font-weight:700; color:{deep}; text-transform:uppercase; }}
        .cover-bulletin .cb-tagrow .right {{ color:#8a7c63; letter-spacing:1.6pt; }}
        .cover-bulletin .cb-rule-double {{ border-top:1.4pt solid #161310; border-bottom:0.6pt solid #161310;
            height:1.6mm; margin:2.5mm 0 0; }}
        .cover-bulletin .cb-paper-name {{ text-align:center; font-size:42pt; font-weight:900; line-height:0.98;
            letter-spacing:-1pt; margin:5mm 0 1mm; color:#161310; }}
        .cover-bulletin .cb-paper-name .amp {{ color:{accent}; }}
        .cover-bulletin .cb-paper-sub {{ text-align:center; font-family:'Helvetica','Arial',sans-serif;
            font-size:7.5pt; letter-spacing:4pt; font-weight:700; color:{deep}; text-transform:uppercase; }}
        .cover-bulletin .cb-rule-thin {{ border-top:0.6pt solid #161310; border-bottom:1.4pt solid #161310;
            height:1.6mm; margin:3.5mm 0 0; }}

        /* ── Ticker band ── */
        .cover-bulletin .cb-ticker {{ position:absolute; top:73mm; left:5mm; right:5mm;
            background:#13110d; padding:3.4mm 6mm; overflow:hidden; white-space:nowrap;
            border-top:0.8mm solid {accent}; border-bottom:0.4mm solid {deep}; }}
        .cover-bulletin .cb-ticker .tk-item {{ font-family:'Courier New',monospace; font-size:8.5pt; }}
        .cover-bulletin .cb-ticker .tk-sym {{ color:{accent}; font-weight:700; margin-right:1mm; }}
        .cover-bulletin .cb-ticker .tk-tag {{ color:#cbb27a; font-weight:700; letter-spacing:0.6pt; margin-right:1.4mm; }}
        .cover-bulletin .cb-ticker .tk-val {{ font-weight:700; }}
        .cover-bulletin .cb-ticker .tk-sep {{ color:#5a4f3a; margin:0 3mm; }}
        .cover-bulletin .cb-ticker-tag {{ position:absolute; top:73mm; left:5mm; background:{accent};
            color:#13110d; font-family:'Helvetica','Arial',sans-serif; font-size:6.5pt; font-weight:900;
            letter-spacing:1.6pt; padding:1mm 3mm; z-index:2; }}

        /* ── Lead column ── */
        .cover-bulletin .cb-lead {{ position:absolute; top:96mm; left:5mm; right:5mm; padding:0 14mm; }}
        .cover-bulletin .cb-kicker {{ font-family:'Helvetica','Arial',sans-serif; font-size:8pt;
            letter-spacing:2.4pt; font-weight:800; text-transform:uppercase; color:{accent}; margin-bottom:2mm; }}
        .cover-bulletin .cb-headline {{ font-size:34pt; font-weight:900; line-height:1.02; letter-spacing:-0.6pt;
            margin:0 0 3mm; color:#161310; }}
        .cover-bulletin .cb-deck {{ font-style:italic; font-size:11.5pt; color:#4b4438; line-height:1.35;
            margin:0 0 6mm; border-left:0.8mm solid {accent}; padding-left:4mm; }}

        /* ── Delta hero block ── */
        .cover-bulletin .cb-hero {{ position:relative; margin:4mm 0 2mm; height:34mm;
            background:{soft}; border:1.2pt solid {accent}; border-left:3mm solid {deep};
            display:flex; align-items:center; padding:0 8mm; overflow:hidden; }}
        .cover-bulletin .cb-hero::after {{ content:""; position:absolute; right:-6mm; top:-12mm; width:54mm; height:58mm;
            background:repeating-linear-gradient(45deg,{accent}22 0,{accent}22 1.4mm,transparent 1.4mm,transparent 4mm); }}
        .cover-bulletin .cb-delta-glyph {{ font-size:62pt; font-weight:900; line-height:1; color:{deep};
            margin-right:8mm; font-family:'Helvetica','Arial',sans-serif; }}
        .cover-bulletin .cb-hero-body {{ position:relative; z-index:1; }}
        .cover-bulletin .cb-hero-eyebrow {{ font-family:'Helvetica','Arial',sans-serif; font-size:7pt;
            letter-spacing:2.4pt; font-weight:800; color:{deep}; text-transform:uppercase; }}
        .cover-bulletin .cb-hero-figure {{ font-family:'Helvetica','Arial',sans-serif; font-size:30pt;
            font-weight:900; letter-spacing:-0.8pt; line-height:1.05; color:{move_color};
            font-variant-numeric:tabular-nums; }}
        .cover-bulletin .cb-hero-figure .arr {{ font-size:22pt; }}
        .cover-bulletin .cb-hero-note {{ font-family:'Helvetica','Arial',sans-serif; font-size:8pt;
            color:#4b4438; letter-spacing:0.4pt; margin-top:1mm; }}

        /* ── Issue band (period / FY / generated) ── */
        .cover-bulletin .cb-issue {{ display:flex; justify-content:space-between; align-items:stretch;
            margin:6mm 0 0; border-top:1pt solid #161310; border-bottom:1pt solid #161310; }}
        .cover-bulletin .cb-issue .col {{ flex:1; padding:3mm 4mm; border-right:0.4pt solid #c9bfa6;
            font-family:'Helvetica','Arial',sans-serif; }}
        .cover-bulletin .cb-issue .col:last-child {{ border-right:0; }}
        .cover-bulletin .cb-issue .lab {{ font-size:6.5pt; letter-spacing:2pt; font-weight:800; color:{deep}; text-transform:uppercase; }}
        .cover-bulletin .cb-issue .val {{ font-size:11pt; font-weight:800; color:#161310; margin-top:1mm; letter-spacing:-0.2pt; }}

        /* ── KPI grid override to sit on cream ── */
        .cover-bulletin .kpi-grid {{ width:auto; margin:7mm 0 0; }}

        /* ── Footer / colophon ── */
        .cover-bulletin .cb-foot {{ position:absolute; bottom:0; left:5mm; right:5mm;
            background:#13110d; color:#cbb27a; padding:4mm 14mm 4.5mm; }}
        .cover-bulletin .cb-foot .cb-foot-rule {{ height:0.6mm; background:{accent}; margin-bottom:3mm; }}
        .cover-bulletin .cb-foot .legal {{ font-family:'Helvetica','Arial',sans-serif; font-size:7.5pt;
            font-weight:700; letter-spacing:0.4pt; color:#f4ecd6; }}
        .cover-bulletin .cb-foot .addr {{ font-family:'Helvetica','Arial',sans-serif; font-size:7pt;
            color:#a9966c; margin-top:0.8mm; }}
        .cover-bulletin .cb-foot .reg {{ font-family:'Courier New',monospace; font-size:6.6pt;
            color:#8a7c52; margin-top:1.4mm; letter-spacing:0.4pt; }}
        .cover-bulletin .cb-foot .conf {{ position:absolute; right:14mm; bottom:4.5mm; font-family:'Helvetica','Arial',sans-serif;
            font-size:7pt; letter-spacing:2.4pt; font-weight:800; color:{accent}; text-transform:uppercase; }}
      </style>

      <div class="cb-edge-l"></div>
      <div class="cb-edge-r"></div>

      <div class="cb-masthead">
        <div class="cb-tagrow">
          <span>The Payroll Bulletin</span>
          <span class="right">No. {esc(issue_no)} · {esc(fmt_long_date(None) or now_stamp().split('·')[0].strip())}</span>
        </div>
        <div class="cb-rule-double"></div>
        <div class="cb-paper-name">{esc(COMPANY['name'])} <span class="amp">&middot;</span> LEDGER</div>
        <div class="cb-paper-sub">A Monthly Market Report on Compensation &amp; Net Pay Movement</div>
        <div class="cb-rule-thin"></div>
      </div>

      <div class="cb-ticker-tag">LIVE TAPE</div>
      <div class="cb-ticker">{ticker}{ticker}</div>

      <div class="cb-lead">
        <div class="cb-kicker">Analytics Desk &middot; vs {esc(prior)}</div>
        <h1 class="cb-headline">{esc(meta.get('name', 'Variance Report'))}</h1>
        <p class="cb-deck">{esc(meta.get('subtitle', 'Net pay shift versus the prior pay period, per employee.'))}
          Movement read against the {esc(prior)} pay run across {esc(str(employees))} employees on payroll.</p>

        <div class="cb-hero">
          <div class="cb-delta-glyph">&Delta;</div>
          <div class="cb-hero-body">
            <div class="cb-hero-eyebrow">Aggregate Net Pay {esc(move_word)}</div>
            <div class="cb-hero-figure"><span class="arr">{esc(move_arrow)}</span> {esc(inr(abs(net_delta)))}</div>
            <div class="cb-hero-note">{esc(str(movers))} of {esc(str(rows))} employees moved · vs {esc(prior)}</div>
          </div>
        </div>

        <div class="cb-issue">
          <div class="col"><div class="lab">Pay Period</div><div class="val">{esc(period.get('label', ''))}</div></div>
          <div class="col"><div class="lab">Fiscal Year</div><div class="val">FY {esc(period.get('fy', ''))}</div></div>
          <div class="col"><div class="lab">Prior Issue</div><div class="val">{esc(prior)}</div></div>
          <div class="col"><div class="lab">Filed</div><div class="val">{esc(now_stamp())}</div></div>
        </div>

        {kpi}
      </div>

      <div class="cb-foot">
        <div class="cb-foot-rule"></div>
        <div class="legal">{legal}</div>
        <div class="addr">{addr}</div>
        <div class="reg">{regline}</div>
        <div class="conf">Confidential</div>
      </div>
    </section>
    """
