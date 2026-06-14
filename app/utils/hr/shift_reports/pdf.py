"""HR Shift Reports — WeasyPrint PDF rendering.

A self-contained editorial identity for the Shifts module, deliberately
distinct from the Attendance reports. Where Attendance reads like a corporate
*dossier* (white cover, thin top rail, centred title, white KPI tiles), the
Shift reports read like an *operations magazine*:

    * a full-bleed accent **masthead slab** carrying a vertical spine, an
      issue stamp, an oversized left-aligned display title and a ghosted
      mega-numeral — the "cover" of the issue;
    * a white **sheet** below the slab with an instrument-panel KPI readout
      (a single framed band, not floating tiles), a per-report *motif
      instrument* (dispatch bands / coverage radar / overtime ledger /
      nocturne hero / rotation orbit / forecast bars) and a footer;
    * a body page led by a bold **flag** (accent mark + wordmark) over a
      modern table with an accent spine on the lead column.

WeasyPrint is imported lazily after the GTK runtime is prepared (see CLAUDE.md).
"""
from __future__ import annotations

import html
from datetime import datetime

from .data import report_meta, REPORT_KEYS

COMPANY = {
    "name": "Fourreck",
    "legal": "Fourreck Technologies",
    "web": "crm.fourreck.com",
    "address_1": "Workforce Operations",
    "address_2": "Control Tower · Shifts & Rosters",
}

# Issue numbers (01..06) for the masthead stamp — ordered as the catalog.
_ISSUE_NO = {k: f"{i + 1:02d}" for i, k in enumerate(REPORT_KEYS)}


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _fmt(val, kind):
    if val is None or val == "":
        return "—"
    if kind == "hours":
        return f"{float(val):.1f}h"
    if kind == "pct":
        return f"{round(float(val))}%"
    if kind == "money":
        return f"₹{float(val):,.0f}"
    if kind == "mult":
        return f"{float(val):.2f}×".replace(".00×", "×")
    if kind == "bool":
        return "✓" if val else "—"
    if kind == "int":
        return f"{int(float(val))}"
    return _esc(val)


# ══════════════════════════════ COLUMNS ══════════════════════════════
def _columns(key: str):
    if key == "roster":
        return [
            {"label": "Code", "key": "employee_code"},
            {"label": "Employee", "key": "employee_name"},
            {"label": "Department", "key": "department"},
            {"label": "Shift", "key": "shift_name"},
            {"label": "Type", "key": "shift_type", "align": "center"},
            {"label": "Window", "key": "window", "align": "center"},
            {"label": "From", "key": "effective_from", "align": "center"},
            {"label": "Until", "key": "effective_until", "align": "center"},
        ]
    if key == "coverage":
        return [
            {"label": "Shift", "key": "shift_name"},
            {"label": "Department", "key": "department"},
            {"label": "Post", "key": "label"},
            {"label": "Required", "key": "min_staff", "align": "right"},
            {"label": "Assigned", "key": "assigned", "align": "right"},
            {"label": "Short", "key": "shortfall", "align": "right", "danger_if": lambda v: v > 0},
            {"label": "Cov %", "key": "coverage_pct", "align": "right", "fmt": "pct",
             "good_if": lambda v: v >= 100, "danger_if": lambda v: v < 70},
            {"label": "Status", "key": "status", "align": "center", "status": True},
        ]
    if key == "overtime":
        return [
            {"label": "Code", "key": "employee_code"},
            {"label": "Employee", "key": "employee_name"},
            {"label": "Department", "key": "department"},
            {"label": "Events", "key": "occurrences", "align": "right"},
            {"label": "OT hrs", "key": "ot_hours", "align": "right", "fmt": "hours"},
            {"label": "Payable", "key": "payable_hours", "align": "right", "fmt": "hours"},
            {"label": "Peak", "key": "peak_mult", "align": "right", "fmt": "mult"},
            {"label": "Weighted", "key": "weighted_hours", "align": "right", "fmt": "hours"},
            {"label": "Est. cost", "key": "est_cost", "align": "right", "fmt": "money"},
        ]
    if key == "night":
        return [
            {"label": "Code", "key": "employee_code"},
            {"label": "Employee", "key": "employee_name"},
            {"label": "Department", "key": "department"},
            {"label": "Shift", "key": "shift_name"},
            {"label": "Window", "key": "window", "align": "center"},
            {"label": "Allowance", "key": "allowance", "align": "right", "fmt": "money"},
            {"label": "OT rate", "key": "ot_rate", "align": "right", "fmt": "mult"},
            {"label": "Transport", "key": "transport", "align": "center", "fmt": "bool"},
            {"label": "Meal", "key": "meal", "align": "center", "fmt": "bool"},
        ]
    if key == "rotation":
        return [
            {"label": "Rotation", "key": "name"},
            {"label": "Cycle", "key": "cycle", "align": "center"},
            {"label": "Every", "key": "frequency_days", "align": "right"},
            {"label": "Steps", "key": "steps", "align": "right"},
            {"label": "Crew", "key": "members", "align": "right"},
            {"label": "Pattern", "key": "step_shifts"},
            {"label": "Current", "key": "current_label", "align": "center"},
            {"label": "Advanced", "key": "last_advanced", "align": "center"},
        ]
    # workforce
    return [
        {"label": "Shift", "key": "shift_name"},
        {"label": "Department", "key": "department"},
        {"label": "Skill", "key": "skill"},
        {"label": "Required", "key": "required", "align": "right"},
        {"label": "Assigned", "key": "assigned", "align": "right"},
        {"label": "Short", "key": "shortfall", "align": "right", "danger_if": lambda v: v > 0},
        {"label": "Cov %", "key": "coverage_pct", "align": "right", "fmt": "pct",
         "good_if": lambda v: v >= 100, "danger_if": lambda v: v < 70},
        {"label": "Status", "key": "status", "align": "center", "status": True},
    ]


_PILL = {
    "OK": ("good", "OK"), "COVERED": ("good", "Covered"),
    "WARN": ("warn", "Watch"), "GAP": ("warn", "Gap"),
    "CRITICAL": ("danger", "Critical"),
}


def _cell(row, c):
    v = row.get(c["key"])
    kind = c.get("fmt")
    if c.get("status"):
        tone, lbl = _PILL.get(str(v), ("neutral", str(v)))
        return f'<span class="pill pill-{tone}">{_esc(lbl)}</span>', ""
    text = _fmt(v, kind)
    klass = ""
    try:
        if c.get("danger_if") and v is not None and c["danger_if"](v):
            klass = "cell-danger"
        elif c.get("good_if") and v is not None and c["good_if"](v):
            klass = "cell-good"
        elif c.get("warn_if") and v is not None and c["warn_if"](v):
            klass = "cell-warn"
    except Exception:
        pass
    return _esc(text) if kind is None else text, klass


def _table_html(key, rows):
    cols = _columns(key)
    head = "".join(
        f'<th class="{"r" if c.get("align")=="right" else "c" if c.get("align")=="center" else ""}'
        f'{" lead" if i == 0 else ""}">{_esc(c["label"])}</th>'
        for i, c in enumerate(cols))
    body = []
    for i, row in enumerate(rows):
        cells = []
        for j, c in enumerate(cols):
            val, kl = _cell(row, c)
            al = "r" if c.get("align") == "right" else "c" if c.get("align") == "center" else ""
            lead = "lead" if j == 0 else ""
            cls = " ".join(x for x in (al, kl, lead) if x)
            cells.append(f'<td class="{cls}">{val}</td>')
        body.append(f'<tr class="{"zebra" if i % 2 else ""}">{"".join(cells)}</tr>')
    if not rows:
        body.append(f'<tr><td colspan="{len(cols)}" class="empty">No records for this period.</td></tr>')
    return (f'<table class="data-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


# ══════════════════════════════ SHARED COVER PARTS ══════════════════════════════
def _readout(items, accent, deep):
    """A single framed instrument band of KPIs (not floating tiles).
    items: list of (label, value, tone?) — tone in {'', 'ok','warn','danger'}.
    """
    cells = []
    for it in items:
        label, value = it[0], it[1]
        tone = it[2] if len(it) > 2 else ""
        vc = {"ok": "#0d9488", "warn": "#ea580c", "danger": "#b91c1c"}.get(tone, deep)
        cells.append(
            f'<div class="ro-cell"><div class="ro-v" style="color:{vc}">{_esc(value)}</div>'
            f'<div class="ro-l">{_esc(label)}</div></div>')
    return (f'<div class="readout" style="border-color:{accent}">'
            f'<span class="ro-edge" style="background:{accent}"></span>{"".join(cells)}</div>')


def _period_strip(period, accent, deep):
    f, t = period["from"], period["to"]
    return (f'<div class="period">'
            f'<span class="p-tag" style="background:{accent}">PERIOD</span>'
            f'<span class="p-box"><b>FROM</b>{f.strftime("%d %b %Y")}</span>'
            f'<span class="p-arr" style="color:{accent}">→</span>'
            f'<span class="p-box"><b>TO</b>{t.strftime("%d %b %Y")}</span>'
            f'<span class="p-days" style="color:{deep}">{(t - f).days + 1} days</span></div>')


def _gen_line():
    return (f'<div class="gen">Generated {datetime.now().strftime("%d %b %Y · %I:%M %p").lstrip("0")} '
            f'· {COMPANY["legal"]} · Confidential — internal use only</div>')


def _slab(meta, key, period, eyebrow, dark=False):
    """The full-bleed accent masthead. Returns the opening slab markup; the
    caller appends nothing — instrument content goes in the sheet below."""
    a, d = meta["accent"], meta["accent_deep"]
    issue = _ISSUE_NO.get(key, "00")
    title_words = _esc(meta["name"]).split(" ")
    if len(title_words) > 1:
        title = " ".join(title_words[:-1]) + "<br>" + title_words[-1]
    else:
        title = title_words[0]
    bg = ("linear-gradient(150deg,#0b1020 0%,#161226 55%,#241a08 100%)" if dark
          else f"linear-gradient(140deg,{a} 0%,{d} 100%)")
    spine_col = "#fde68a" if dark else "#ffffffcc"
    return f"""
    <div class="slab{' slab-dark' if dark else ''}" style="background:{bg}">
      <span class="bignum">{issue}</span>
      <span class="spine" style="color:{spine_col}">{COMPANY['name'].upper()} · CONTROL TOWER</span>
      <div class="slab-top">
        <div class="brand"><span class="crest" style="color:{d if not dark else '#1a1004'}">{meta['icon']}</span>
          <span class="brand-txt">SHIFTS &amp; ROSTERS</span></div>
        <div class="issue"><span class="iss-k">SHIFT OPS</span><span class="iss-n">// {issue}</span></div>
      </div>
      <div class="eyebrow">{_esc(eyebrow)}</div>
      <h1 class="display">{title}</h1>
      <p class="slab-sub">{_esc(meta['subtitle'])}</p>
    </div>"""


# ══════════════════════════════ MOTIF INSTRUMENTS (sheet area) ═══════════════════
def _inst_dispatch(meta, summary):
    a, d = meta["accent"], meta["accent_deep"]
    bands = "".join(
        f'<div class="disp-band"><span class="db-k">{_esc(k)}</span>'
        f'<span class="db-bar"><i style="width:{min(100, v * 12)}%;background:linear-gradient(90deg,{a},{d})"></i></span>'
        f'<span class="db-v" style="color:{d}">{v}</span></div>'
        for k, v in list((summary.get("by_type") or {}).items())[:6]) or \
        '<div class="db-empty">No assignments in this period.</div>'
    return f"""<div class="inst">
      <div class="inst-h">Assignments by shift type</div>
      <div class="disp-list">{bands}</div></div>"""


def _inst_radar(meta, summary):
    a, d = meta["accent"], meta["accent_deep"]
    cov = summary["assigned"] / summary["required"] * 100 if summary.get("required") else 100
    return f"""<div class="inst inst-center">
      <svg viewBox="0 0 120 120" width="150" height="150">
        <circle cx="60" cy="60" r="56" fill="none" stroke="{a}55" stroke-width="0.8"/>
        <circle cx="60" cy="60" r="40" fill="none" stroke="{a}44" stroke-width="0.6"/>
        <circle cx="60" cy="60" r="22" fill="none" stroke="{a}33" stroke-width="0.6"/>
        <line x1="60" y1="6" x2="60" y2="114" stroke="{a}33" stroke-width="0.5"/>
        <line x1="6" y1="60" x2="114" y2="60" stroke="{a}33" stroke-width="0.5"/>
        <path d="M60 60 L60 8 A52 52 0 0 1 104 86 Z" fill="{a}22"/>
        <text x="60" y="58" text-anchor="middle" font-size="20" font-weight="900" fill="{d}">{round(cov)}%</text>
        <text x="60" y="72" text-anchor="middle" font-size="6" fill="{a}" letter-spacing="1">COVERAGE</text>
      </svg>
      <div class="inst-cap">Staffing scope as of {_esc(summary.get('on_date', ''))}</div></div>"""


def _inst_ledger(meta, summary):
    a, d, s = meta["accent"], meta["accent_deep"], meta["accent_soft"]
    return f"""<div class="inst">
      <div class="ledger-hero" style="border-color:{a};background:{s}">
        <div class="lh-big" style="color:{d}">{summary['payable_hours']:.1f}<span>h payable</span></div>
        <div class="lh-stamp" style="border-color:{a};color:{a}">{summary['rules_active']} rules<br>engaged</div>
      </div>
      <div class="inst-cap">Est. cost uses monthly CTC ÷ 26 ÷ 8 where available.</div></div>"""


def _inst_nocturne(meta, summary):
    a = meta["accent"]
    return f"""<div class="inst inst-center">
      <div class="noc-hero">₹{summary['allowance_per_night']:,.0f}<span>allowance / night · crew total</span></div>
      <div class="inst-cap" style="color:#cbb994">After-dark workforce · {summary['with_policy']} on a night policy</div></div>"""


def _inst_orbit(meta, summary):
    import math
    a, d, s = meta["accent"], meta["accent_deep"], meta["accent_soft"]
    n = max(1, summary["rows"])
    dots = "".join(
        f'<circle cx="{60 + 42 * math.cos(i / n * 6.283 - 1.5708):.1f}" '
        f'cy="{60 + 42 * math.sin(i / n * 6.283 - 1.5708):.1f}" r="4" '
        f'fill="{a if i == 0 else "#fff"}" stroke="{d}" stroke-width="1"/>'
        for i in range(min(n, 12)))
    return f"""<div class="inst inst-center">
      <svg viewBox="0 0 120 120" width="150" height="150">
        <circle cx="60" cy="60" r="42" fill="none" stroke="{a}66" stroke-width="0.8" stroke-dasharray="2 2"/>
        <circle cx="60" cy="60" r="20" fill="{s}" stroke="{a}" stroke-width="1"/>
        <text x="60" y="58" text-anchor="middle" font-size="13" font-weight="900" fill="{d}">{summary['rows']}</text>
        <text x="60" y="70" text-anchor="middle" font-size="5.5" fill="{a}" letter-spacing="1">ROTATIONS</text>
        {dots}
      </svg>
      <div class="inst-cap">{summary['members']} crew across {summary['total_steps']} steps</div></div>"""


def _inst_forecast(meta, summary):
    a, d = meta["accent"], meta["accent_deep"]
    req, asg = summary.get("required", 0), summary.get("assigned", 0)
    mx = max(req, asg, 1)
    return f"""<div class="inst">
      <div class="inst-h">Demand vs supply</div>
      <div class="fc-bar"><span class="fcb-l">Required</span>
        <span class="fcb-t"><i style="width:{req / mx * 100:.0f}%;background:{d}"></i></span>
        <b style="color:{d}">{req}</b></div>
      <div class="fc-bar"><span class="fcb-l">Assigned</span>
        <span class="fcb-t"><i style="width:{asg / mx * 100:.0f}%;background:{a}"></i></span>
        <b style="color:{d}">{asg}</b></div>
      <div class="inst-cap">{summary.get('coverage_pct', 0)}% covered as of {_esc(summary.get('on_date', ''))}</div></div>"""


# ══════════════════════════════ COVERS ══════════════════════════════
def _cover_dispatch(meta, summary, period):
    a, d = meta["accent"], meta["accent_deep"]
    return f"""
    <section class="cover">
      {_slab(meta, "roster", period, 'OPERATIONS · DISPATCH SHEET · ' + period['from'].strftime('%b %Y').upper())}
      <div class="sheet">
        {_period_strip(period, a, d)}
        {_readout([('Assignments', summary['rows']), ('Employees', summary['employees']),
                   ('Shifts', summary['shifts']), ('Night posts', summary['night'], 'warn'),
                   ('Open-ended', summary['open_ended'])], a, d)}
        {_inst_dispatch(meta, summary)}
        {_gen_line()}
      </div>
    </section>"""


def _cover_radar(meta, summary, period):
    a, d = meta["accent"], meta["accent_deep"]
    return f"""
    <section class="cover">
      {_slab(meta, "coverage", period, 'CONTROL TOWER · STAFFING SCOPE')}
      <div class="sheet">
        {_period_strip(period, a, d)}
        {_readout([('Posts', summary['rows']), ('Required', summary['required']),
                   ('Assigned', summary['assigned'], 'ok'),
                   ('Shortfall', summary['total_shortfall'], 'danger' if summary['total_shortfall'] else 'ok'),
                   ('Critical', summary['critical'], 'danger' if summary['critical'] else 'ok')], a, d)}
        {_inst_radar(meta, summary)}
        {_gen_line()}
      </div>
    </section>"""


def _cover_ledger(meta, summary, period):
    a, d = meta["accent"], meta["accent_deep"]
    return f"""
    <section class="cover">
      {_slab(meta, "overtime", period, 'COMPENSATION · OVERTIME LEDGER · ' + period['from'].strftime('%b %Y').upper())}
      <div class="sheet">
        {_period_strip(period, a, d)}
        {_readout([('Employees', summary['employees']), ('OT events', summary['occurrences']),
                   ('OT hours', f"{summary['ot_hours']:.1f}"),
                   ('Weighted', f"{summary['weighted_hours']:.1f}", 'warn'),
                   ('Est. cost', f"₹{summary['est_cost']:,.0f}", 'danger')], a, d)}
        {_inst_ledger(meta, summary)}
        {_gen_line()}
      </div>
    </section>"""


def _cover_nocturne(meta, summary, period):
    a, d = meta["accent"], meta["accent_deep"]
    stars = "".join(
        f'<circle cx="{(i * 53 % 320) + 6}" cy="{(i * 37 % 70) + 6}" r="{0.6 + (i % 3) * 0.4}" '
        f'fill="#fde68a" opacity="{0.4 + (i % 4) * 0.15}"/>'
        for i in range(30))
    return f"""
    <section class="cover cover-dark">
      <div class="noc-sky"><svg viewBox="0 0 340 90" preserveAspectRatio="none" width="100%" height="100%">
        {stars}<circle cx="296" cy="30" r="16" fill="#fde68a"/><circle cx="289" cy="26" r="14" fill="#161226"/>
      </svg></div>
      {_slab(meta, "night", period, 'AFTER DARK · NIGHT OPERATIONS', dark=True)}
      <div class="sheet sheet-dark">
        {_period_strip(period, a, '#fde68a')}
        {_readout([('Night crew', summary['employees']), ('Night shifts', summary['shifts']),
                   ('With policy', summary['with_policy'], 'ok'), ('Transport', summary['transport']),
                   ('Meal-eligible', summary['meal'])], a, '#fde68a')}
        {_inst_nocturne(meta, summary)}
        {_gen_line()}
      </div>
    </section>"""


def _cover_orbit(meta, summary, period):
    a, d = meta["accent"], meta["accent_deep"]
    return f"""
    <section class="cover">
      {_slab(meta, "rotation", period, 'SCHEDULING · ROTATION ORBITS')}
      <div class="sheet">
        {_period_strip(period, a, d)}
        {_readout([('Rotations', summary['rows']), ('Crew members', summary['members']),
                   ('Total steps', summary['total_steps']), ('Live', summary['active_now'], 'ok')], a, d)}
        {_inst_orbit(meta, summary)}
        {_gen_line()}
      </div>
    </section>"""


def _cover_forecast(meta, summary, period):
    a, d = meta["accent"], meta["accent_deep"]
    return f"""
    <section class="cover">
      {_slab(meta, "workforce", period, 'PLANNING · DEMAND vs SUPPLY')}
      <div class="sheet">
        {_period_strip(period, a, d)}
        {_readout([('Posts', summary['rows']), ('Required', summary.get('required', 0)),
                   ('Assigned', summary.get('assigned', 0), 'ok'),
                   ('Shortfall', summary['shortfall'], 'danger' if summary['shortfall'] else 'ok'),
                   ('Gaps', summary['gaps'], 'warn' if summary['gaps'] else 'ok')], a, d)}
        {_inst_forecast(meta, summary)}
        {_gen_line()}
      </div>
    </section>"""


COVER_RENDERERS = {
    "dispatch": _cover_dispatch,
    "radar": _cover_radar,
    "ledger": _cover_ledger,
    "nocturne": _cover_nocturne,
    "orbit": _cover_orbit,
    "forecast": _cover_forecast,
}


# ══════════════════════════════ BODY ══════════════════════════════
def _body(key, rows, summary, theme, period):
    a, d = theme["accent"], theme["accent_deep"]
    return f"""
    <section class="body">
      <div class="flag">
        <span class="flag-mark" style="background:{a}">{theme['icon']}</span>
        <div class="flag-txt">
          <div class="flag-name" style="color:{d}">{_esc(theme['name'])}</div>
          <div class="flag-tag">DETAIL LEDGER · {summary.get('rows', len(rows))} RECORDS</div>
        </div>
        <div class="flag-period">{period['from'].strftime('%d %b %Y')} – {period['to'].strftime('%d %b %Y')}</div>
      </div>
      {_table_html(key, rows)}
    </section>"""


# ══════════════════════════════ CSS ══════════════════════════════
def _base_css(accent, deep, soft):
    css = """
@page { size: A4; margin: 15mm 13mm 16mm 13mm;
  @bottom-left { content: "%LEGAL% · %WEB%"; font-size: 7pt; color: #9a8a6a; }
  @bottom-center { content: "Shifts & Rosters · Control Tower"; font-size: 7pt; color: #c4b59a; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 7pt; color: #9a8a6a; } }
@page :first { margin: 0; @bottom-left { content: ""; } @bottom-center { content: ""; } @bottom-right { content: ""; } }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #1a1410; margin: 0; }

/* ── cover scaffold ── */
.cover { position: relative; width: 210mm; min-height: 297mm; page-break-after: always; background: #fff; }

/* ── masthead slab (full-bleed accent) ── */
.slab { position: relative; padding: 20mm 20mm 16mm 24mm; color: #fff; overflow: hidden; }
.slab .bignum { position: absolute; right: 8mm; bottom: -16mm; font-size: 150pt; font-weight: 900;
  color: #ffffff14; letter-spacing: -4pt; line-height: 1; }
.spine { position: absolute; left: 7mm; bottom: 20mm; transform-origin: left bottom; transform: rotate(-90deg);
  font-size: 6.6pt; letter-spacing: 3.4pt; font-weight: 800; white-space: nowrap; }
.slab-top { position: relative; display: flex; align-items: center; justify-content: space-between; }
.brand { display: flex; align-items: center; gap: 3mm; }
.crest { width: 11mm; height: 11mm; border-radius: 2.6mm; background: #fff; font-weight: 900; font-size: 15pt;
  display: flex; align-items: center; justify-content: center; }
.brand-txt { font-size: 8.5pt; font-weight: 800; letter-spacing: 2.4pt; color: #ffffffe6; }
.issue { text-align: right; line-height: 1.1; }
.iss-k { display: block; font-size: 6.6pt; letter-spacing: 2.4pt; font-weight: 800; color: #ffffffcc; }
.iss-n { display: block; font-size: 16pt; font-weight: 900; color: #fff; }
.eyebrow { position: relative; margin-top: 14mm; font-size: 7.6pt; letter-spacing: 3.2pt; font-weight: 900;
  text-transform: uppercase; color: #ffffffdd; }
.eyebrow::before { content: ""; display: inline-block; width: 12mm; height: 1.4pt; background: #ffffffcc;
  vertical-align: middle; margin-right: 4mm; }
.display { position: relative; font-size: 52pt; font-weight: 900; letter-spacing: -1.6pt; line-height: 0.96;
  margin: 4mm 0 4mm; color: #fff; }
.slab-sub { position: relative; font-size: 11pt; font-weight: 500; color: #ffffffe0; margin: 0; max-width: 150mm; }

/* ── sheet (white area below slab) ── */
.sheet { position: relative; padding: 9mm 20mm 18mm 24mm; }

/* ── period strip ── */
.period { display: flex; align-items: center; gap: 4mm; margin: 0 0 7mm; }
.p-tag { color: #fff; font-size: 7pt; font-weight: 900; letter-spacing: 2pt; padding: 1.6mm 3.2mm; border-radius: 1.5mm; }
.p-box { display: flex; flex-direction: column; font-size: 10.5pt; font-weight: 800; color: #1a1410; }
.p-box b { font-size: 6pt; letter-spacing: 1.5pt; color: #9a8a6a; font-weight: 800; margin-bottom: 0.3mm; }
.p-arr { font-size: 13pt; font-weight: 900; }
.p-days { font-size: 8pt; font-weight: 800; margin-left: auto; padding: 1.4mm 3.4mm; border-radius: 4mm;
  background: %SOFT%; }

/* ── KPI readout band ── */
.readout { position: relative; display: flex; border: 1.4pt solid; border-radius: 3mm; overflow: hidden;
  margin: 0 0 8mm; background: #fff; box-shadow: 0 1.5mm 4mm #0000000d; }
.ro-edge { position: absolute; left: 0; top: 0; bottom: 0; width: 1.8mm; }
.ro-cell { flex: 1 1 0; padding: 5mm 3mm 4.4mm; text-align: center; border-left: 0.6pt solid #00000012; }
.ro-cell:first-child { border-left: none; padding-left: 5mm; }
.ro-v { font-size: 23pt; font-weight: 900; line-height: 1; }
.ro-l { font-size: 6.6pt; font-weight: 800; letter-spacing: 0.8pt; text-transform: uppercase; color: #6b5840; margin-top: 2mm; }

/* ── instrument panel ── */
.inst { margin: 0 0 6mm; }
.inst-center { text-align: center; }
.inst-h { font-size: 8.5pt; font-weight: 900; text-transform: uppercase; letter-spacing: 1.8pt; color: #6b5840;
  margin-bottom: 4mm; padding-bottom: 1.6mm; border-bottom: 1pt solid #00000014; }
.inst-cap { font-size: 7.6pt; color: #9a8a6a; font-style: italic; margin-top: 3mm; }

.disp-band { display: flex; align-items: center; gap: 3mm; margin-bottom: 3mm; }
.db-k { flex: 0 0 30mm; font-size: 8.6pt; font-weight: 800; }
.db-bar { flex: 1; height: 5mm; background: #00000010; border-radius: 2.5mm; overflow: hidden; }
.db-bar i { display: block; height: 100%; border-radius: 2.5mm; }
.db-v { flex: 0 0 8mm; text-align: right; font-weight: 900; font-size: 10pt; }
.db-empty { color: #9a8a6a; font-style: italic; font-size: 9pt; }

.ledger-hero { display: flex; align-items: center; justify-content: space-between; gap: 6mm;
  padding: 7mm 9mm; border: 1.6pt solid; border-radius: 3mm; margin-bottom: 2mm; }
.lh-big { font-size: 36pt; font-weight: 900; line-height: 1; }
.lh-big span { font-size: 11pt; font-weight: 700; margin-left: 2mm; }
.lh-stamp { font-size: 9pt; font-weight: 900; text-transform: uppercase; text-align: center; padding: 3mm 5mm;
  border: 1.8pt solid; border-radius: 2mm; transform: rotate(-5deg); letter-spacing: 1pt; }

.noc-hero { font-size: 34pt; font-weight: 900; color: #fde68a; margin: 2mm 0; }
.noc-hero span { display: block; font-size: 9pt; font-weight: 700; color: #cbb994; letter-spacing: 1pt; margin-top: 1mm; }

.fc-bar { display: flex; align-items: center; gap: 3mm; margin-bottom: 3.5mm; }
.fcb-l { flex: 0 0 24mm; font-size: 9pt; font-weight: 800; }
.fcb-t { flex: 1; height: 7mm; background: #00000010; border-radius: 2mm; overflow: hidden; }
.fcb-t i { display: block; height: 100%; border-radius: 2mm; }
.fc-bar b { flex: 0 0 12mm; text-align: right; font-size: 13pt; font-weight: 900; }

.gen { position: absolute; left: 24mm; right: 20mm; bottom: 9mm; text-align: center; font-size: 7.4pt;
  color: #9a8a6a; border-top: 0.8pt solid #00000014; padding-top: 3mm; }

/* ── dark (nocturne) cover ── */
.cover-dark { background: #161226; }
.cover-dark .noc-sky { position: absolute; top: 0; left: 0; right: 0; height: 90px; z-index: 0; }
.cover-dark .slab { z-index: 1; }
.cover-dark .crest { background: %ACCENT%; }
.sheet-dark { color: #fde68a; }
.sheet-dark .readout { background: #ffffff0a; border-color: %ACCENT% !important; box-shadow: none; }
.sheet-dark .ro-cell { border-left-color: #fde68a22; }
.sheet-dark .ro-l { color: #cbb994; }
.sheet-dark .p-box, .sheet-dark .p-box b { color: #fde68a; }
.sheet-dark .p-days { background: #ffffff12; color: #fde68a !important; }
.sheet-dark .inst-h { color: #cbb994; border-bottom-color: #fde68a22; }
.sheet-dark .gen { color: #9a8a6a; border-top-color: #fde68a22; }

/* ── body page ── */
.body { padding: 2mm 0; }
.flag { display: flex; align-items: center; gap: 4mm; margin-bottom: 5mm; padding-bottom: 3mm;
  border-bottom: 2.4pt solid %ACCENT%; }
.flag-mark { width: 12mm; height: 12mm; border-radius: 2.6mm; color: #fff; font-weight: 900; font-size: 16pt;
  display: flex; align-items: center; justify-content: center; }
.flag-txt { flex: 1; }
.flag-name { font-size: 17pt; font-weight: 900; letter-spacing: -0.4pt; }
.flag-tag { font-size: 7pt; font-weight: 800; letter-spacing: 1.6pt; color: #9a8a6a; margin-top: 0.6mm; }
.flag-period { font-size: 8.5pt; font-weight: 800; color: #6b5840; text-align: right; }

.data-table { width: 100%; border-collapse: collapse; font-size: 7.6pt; }
.data-table th { background: %ACCENT%; color: #fff; text-align: left; padding: 2.4mm 1.8mm; font-size: 6.8pt;
  text-transform: uppercase; letter-spacing: 0.6pt; font-weight: 800; }
.data-table th.r { text-align: right; } .data-table th.c { text-align: center; }
.data-table th.lead { border-left: 2.4pt solid %DEEP%; }
.data-table td { padding: 2mm 1.8mm; border-bottom: 0.6pt solid #00000010; }
.data-table td.lead { border-left: 2.4pt solid %ACCENT%; font-weight: 700; }
.data-table td.r { text-align: right; font-variant-numeric: tabular-nums; }
.data-table td.c { text-align: center; }
.data-table tr.zebra td { background: %SOFT%66; }
.data-table tbody tr { page-break-inside: avoid; }
.data-table td.empty { text-align: center; color: #9a8a6a; font-style: italic; padding: 10mm; border-left: none; }
.cell-danger { background: #fee2e2; color: #7f1d1d; font-weight: 800; }
.cell-warn { background: #fef9c3; color: #713f12; font-weight: 700; }
.cell-good { background: #ccfbf1; color: #115e59; font-weight: 700; }
.pill { display: inline-block; padding: 0.8mm 2.6mm; border-radius: 4mm; font-size: 6.4pt; font-weight: 800;
  text-transform: uppercase; letter-spacing: 0.4pt; }
.pill-good { background: #ccfbf1; color: #115e59; }
.pill-warn { background: #ffedd5; color: #9a3412; }
.pill-danger { background: #fee2e2; color: #991b1b; }
.pill-neutral { background: #e2e8f0; color: #475569; }
"""
    return (css.replace("%ACCENT%", accent).replace("%DEEP%", deep).replace("%SOFT%", soft)
            .replace("%LEGAL%", COMPANY["legal"]).replace("%WEB%", COMPANY["web"]))


def render_pdf(report_key: str, rows: list, summary: dict, meta_arg: dict) -> bytes:
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433 — lazy after GTK PATH prep

    theme = report_meta(report_key)
    period = meta_arg["period"]
    cover_fn = COVER_RENDERERS.get(theme["motif"], _cover_dispatch)
    cover = cover_fn(theme, summary, period)
    body = _body(report_key, rows, summary, theme, period)
    css = _base_css(theme["accent"], theme["accent_deep"], theme["accent_soft"])

    full = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <title>{_esc(theme['name'])} · {COMPANY['name']}</title><style>{css}</style></head>
    <body>{cover}{body}</body></html>"""
    return HTML(string=full).write_pdf()
