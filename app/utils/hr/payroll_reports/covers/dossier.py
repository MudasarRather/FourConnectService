"""TDS · Form 24Q cover — motif "dossier".

A sealed, confidential tax dossier rendered as a manila folder: a die-cut
folder tab at the top, a diagonal double-bordered "CONFIDENTIAL · 24Q" rubber
stamp, a CSS paperclip, faint redaction bars over the masthead, and a
deduction-quarter framing strip. Purple ink officialdom throughout.

Everything is pure CSS/HTML — no external images or fonts. Selectors are scoped
under ``.cover-dossier`` so nothing leaks into the shared body table.
"""
from __future__ import annotations

from ..common import (
    COMPANY, esc, inr, inr_compact, kpi_tiles_html, now_stamp, month_name,
)


def _quarter(month: int) -> tuple[str, str]:
    """India TDS quarter for a calendar month → (Q-label, month-range)."""
    if month in (4, 5, 6):
        return "Q1", "Apr – Jun"
    if month in (7, 8, 9):
        return "Q2", "Jul – Sep"
    if month in (10, 11, 12):
        return "Q3", "Oct – Dec"
    return "Q4", "Jan – Mar"


def render(meta: dict, summary: dict, period: dict,
           shaped_count: int | None = None) -> str:
    accent = meta.get("accent", "#9333ea")
    soft = meta.get("accent_soft", "#f3e8ff")
    deep = meta.get("accent_deep", "#581c87")

    year = int(period.get("year", 0) or 0)
    month = int(period.get("month", 0) or 0)
    fy = esc(period.get("fy", ""))
    label = esc(period.get("label", ""))
    qtr, qmonths = _quarter(month)

    rows = shaped_count if shaped_count is not None else summary.get("rows", 0)
    employees = summary.get("employees", rows)
    tds = summary.get("tds", 0)
    gross = summary.get("gross", 0)

    tiles = [
        ("Deductees", str(employees), accent),
        ("PAN Records", str(rows), deep),
        ("TDS Deducted", inr_compact(tds), "#b91c1c"),
        ("Taxable Gross", inr_compact(gross), "#047857"),
    ]
    kpi_html = kpi_tiles_html(tiles)

    # Stamp date — official-form long stamp.
    stamp_date = now_stamp()
    challan_ref = f"24Q / {qtr} / FY{fy} / FRC-{COMPANY['tan']}"

    return f"""
    <section class="cover cover-dossier">
      <style>
        .cover-dossier {{
            background:
              radial-gradient(120% 80% at 12% 0%, {soft} 0%, #efe7dc 42%, #e7dcc9 100%);
            font-family:'Helvetica','Arial',sans-serif;
        }}
        /* manila folder body */
        .cover-dossier .folder {{
            position:absolute; top:24mm; left:13mm; right:13mm; bottom:14mm;
            background:linear-gradient(160deg,#f3e9d4 0%,#ecdfc4 55%,#e3d3b2 100%);
            border:0.6pt solid #c7b58e;
            border-radius:1.5mm 5mm 2mm 2mm;
            box-shadow:0 1.4mm 4mm rgba(60,45,20,0.22), inset 0 0 0 0.4pt #fff7e6;
        }}
        /* die-cut folder tab */
        .cover-dossier .tab {{
            position:absolute; top:14mm; left:13mm;
            width:78mm; height:12mm;
            background:linear-gradient(160deg,#f3e9d4,#e6d6b4);
            border:0.6pt solid #c7b58e; border-bottom:none;
            border-radius:4mm 7mm 0 0;
            box-shadow:0 -0.4mm 1.5mm rgba(60,45,20,0.14);
        }}
        .cover-dossier .tab-text {{
            position:absolute; top:4.6mm; left:8mm;
            font-size:8pt; letter-spacing:2.2pt; font-weight:800;
            color:{deep}; text-transform:uppercase;
        }}
        .cover-dossier .tab-dot {{
            position:absolute; top:4.4mm; right:7mm;
            width:3.4mm; height:3.4mm; border-radius:50%;
            background:{accent}; box-shadow:0 0 0 0.8mm {soft};
        }}
        /* manila ruled texture inside folder */
        .cover-dossier .ruling {{
            position:absolute; top:38mm; left:23mm; right:23mm; bottom:60mm;
            background:repeating-linear-gradient(
              to bottom, transparent 0, transparent 7.4mm,
              rgba(120,90,40,0.10) 7.4mm, rgba(120,90,40,0.10) 7.7mm);
            opacity:0.5; pointer-events:none;
        }}
        /* paperclip — top-right, two nested rounded rects */
        .cover-dossier .clip {{
            position:absolute; top:18.5mm; right:30mm;
            width:9mm; height:24mm; z-index:6;
            transform:rotate(8deg);
        }}
        .cover-dossier .clip .c-out {{
            position:absolute; inset:0;
            border:1.5pt solid #8c8c98; border-radius:4.5mm;
            border-bottom-color:#6f6f7d;
        }}
        .cover-dossier .clip .c-in {{
            position:absolute; top:3.2mm; left:2.4mm; right:2.4mm; bottom:6mm;
            border:1.5pt solid #a7a7b4; border-top:none; border-radius:0 0 2.6mm 2.6mm;
        }}
        /* CONFIDENTIAL rubber stamp — rotated double border */
        .cover-dossier .stamp {{
            position:absolute; top:128mm; right:20mm;
            width:62mm; padding:5mm 3mm 4.4mm;
            text-align:center;
            border:1.4pt solid #b3262f; outline:0.8pt solid #b3262f; outline-offset:2pt;
            border-radius:2mm;
            color:#b3262f; transform:rotate(-11deg);
            opacity:0.82; z-index:7;
            box-shadow:inset 0 0 0 0.4pt rgba(179,38,47,0.4);
        }}
        .cover-dossier .stamp .s-top {{
            font-size:8pt; letter-spacing:3.4pt; font-weight:900;
            border-bottom:0.8pt solid #b3262f; padding-bottom:1.6mm; margin-bottom:1.8mm;
        }}
        .cover-dossier .stamp .s-mid {{
            font-size:18pt; font-weight:900; letter-spacing:1.5pt; line-height:1;
        }}
        .cover-dossier .stamp .s-bot {{
            font-size:7pt; letter-spacing:2.6pt; font-weight:800;
            margin-top:1.8mm; border-top:0.8pt solid #b3262f; padding-top:1.6mm;
        }}
        /* redaction bars over the masthead */
        .cover-dossier .redact {{
            position:absolute; height:2.4mm; background:#1d1505; opacity:0.16;
            border-radius:0.5mm;
        }}
        .cover-dossier .r1 {{ top:31mm; right:24mm; width:30mm; transform:rotate(-1deg); }}
        .cover-dossier .r2 {{ top:35.5mm; right:24mm; width:18mm; }}
        /* content stack */
        .cover-dossier .stack {{
            position:absolute; top:46mm; left:24mm; right:24mm;
        }}
        .cover-dossier .crest-row {{ display:flex; align-items:center; gap:5mm; }}
        .cover-dossier .ink-seal {{
            width:17mm; height:17mm; border-radius:50%;
            background:radial-gradient(circle at 35% 30%, {accent}, {deep});
            color:#fff; font-size:15pt; font-weight:900;
            text-align:center; line-height:17mm;
            box-shadow:0 1mm 2.5mm rgba(88,28,135,0.4), inset 0 0 0 1pt rgba(255,255,255,0.35);
        }}
        .cover-dossier .org {{ }}
        .cover-dossier .org .legal {{
            font-size:9.5pt; font-weight:800; color:#231a08; letter-spacing:0.2pt;
        }}
        .cover-dossier .org .sub {{
            font-size:7pt; color:#6b5a35; letter-spacing:0.6pt; margin-top:0.8mm;
        }}
        .cover-dossier .eyebrow {{
            margin-top:11mm; font-size:8pt; letter-spacing:3.4pt; font-weight:900;
            color:{accent}; text-transform:uppercase;
        }}
        .cover-dossier .title {{
            margin:2.5mm 0 0; font-size:33pt; font-weight:900; line-height:1.02;
            color:#1d1505; letter-spacing:-0.6pt;
        }}
        .cover-dossier .title .form {{ color:{deep}; }}
        .cover-dossier .subtitle {{
            margin:3mm 0 0; font-size:10pt; font-style:italic; color:#574620;
            max-width:120mm;
        }}
        /* deduction-quarter framing strip */
        .cover-dossier .qframe {{
            margin-top:9mm; display:flex; border:0.8pt solid {accent}66;
            border-radius:2mm; overflow:hidden; width:152mm;
            background:rgba(255,253,247,0.7);
            box-shadow:0 0.8mm 2mm rgba(60,45,20,0.12);
        }}
        .cover-dossier .qcell {{
            flex:1; padding:4.4mm 5mm; border-right:0.6pt solid {accent}33;
        }}
        .cover-dossier .qcell:last-child {{ border-right:none; }}
        .cover-dossier .qcell .k {{
            font-size:6.5pt; letter-spacing:1.8pt; font-weight:900;
            color:{deep}; text-transform:uppercase;
        }}
        .cover-dossier .qcell .v {{
            font-size:12pt; font-weight:900; color:#1d1505; margin-top:1.6mm;
            letter-spacing:-0.2pt;
        }}
        .cover-dossier .qcell .v small {{ font-size:8pt; color:#6b5a35; font-weight:700; }}
        .cover-dossier .qbadge {{
            background:linear-gradient(160deg,{accent},{deep}); color:#fff;
            display:flex; flex-direction:column; justify-content:center;
            padding:4.4mm 6mm; flex:0 0 auto;
        }}
        .cover-dossier .qbadge .q {{ font-size:17pt; font-weight:900; line-height:1; letter-spacing:0.5pt; }}
        .cover-dossier .qbadge .qm {{ font-size:7pt; letter-spacing:1.4pt; margin-top:1.6mm; opacity:0.9; }}
        /* generated + challan ref line */
        .cover-dossier .meta-line {{
            margin-top:6mm; font-size:8pt; color:#6b5a35; letter-spacing:0.4pt;
        }}
        .cover-dossier .meta-line b {{ color:{deep}; font-weight:800; }}
        /* KPI strip placement (uses shared .kpi-* classes) */
        .cover-dossier .kpi-wrap {{ position:absolute; left:24mm; right:24mm; bottom:34mm; }}
        .cover-dossier .kpi-wrap .kpi-grid {{ width:auto; margin:0; }}
        /* footer */
        .cover-dossier .foot {{
            position:absolute; left:24mm; right:24mm; bottom:18mm;
            border-top:0.6pt solid {accent}44; padding-top:3mm;
            font-size:7pt; color:#6b5a35; text-align:center; letter-spacing:0.3pt;
        }}
        .cover-dossier .foot .conf {{
            margin-top:1.4mm; font-size:6.6pt; letter-spacing:2.4pt; font-weight:800;
            color:#b3262f; text-transform:uppercase;
        }}
      </style>

      <div class="folder"></div>
      <div class="tab">
        <div class="tab-text">Tax File · TDS</div>
        <div class="tab-dot"></div>
      </div>
      <div class="ruling"></div>

      <div class="clip"><div class="c-out"></div><div class="c-in"></div></div>

      <div class="redact r1"></div>
      <div class="redact r2"></div>

      <div class="stamp">
        <div class="s-top">Confidential</div>
        <div class="s-mid">FORM&nbsp;24Q</div>
        <div class="s-bot">{esc(qtr)} · FY {fy}</div>
      </div>

      <div class="stack">
        <div class="crest-row">
          <span class="ink-seal">{esc(meta.get('icon', '₹'))}</span>
          <div class="org">
            <div class="legal">{esc(COMPANY['legal'])}</div>
            <div class="sub">TAN {esc(COMPANY['tan'])} · PAN {esc(COMPANY['pan'])} · {esc(COMPANY['web'])}</div>
          </div>
        </div>

        <div class="eyebrow">Statutory Filing · Tax Deducted at Source</div>
        <h1 class="title">TDS Statement<br><span class="form">Form 24Q</span></h1>
        <p class="subtitle">{esc(meta.get('subtitle', 'PAN-wise tax deducted at source — period and year-to-date'))}</p>

        <div class="qframe">
          <div class="qbadge">
            <div class="q">{esc(qtr)}</div>
            <div class="qm">{esc(qmonths)}</div>
          </div>
          <div class="qcell">
            <div class="k">Pay Period</div>
            <div class="v">{label or esc(month_name(month) + ' ' + str(year))}</div>
          </div>
          <div class="qcell">
            <div class="k">Financial Year</div>
            <div class="v">{fy}</div>
          </div>
          <div class="qcell">
            <div class="k">Quarter Coverage</div>
            <div class="v">{esc(qmonths)} <small>{esc(qtr)}</small></div>
          </div>
        </div>

        <div class="meta-line">
          Challan ref&nbsp; <b>{esc(challan_ref)}</b><br>
          Compiled&nbsp; <b>{esc(stamp_date)}</b> &nbsp;·&nbsp; Deductor&nbsp; <b>{esc(COMPANY['name'])}</b>
        </div>
      </div>

      <div class="kpi-wrap">{kpi_html}</div>

      <div class="foot">
        {esc(COMPANY['legal'])} · {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])} · CIN {esc(COMPANY['cin'])}
        <div class="conf">Confidential · Sealed Tax File · For Authorised Filing Only</div>
      </div>
    </section>
    """
