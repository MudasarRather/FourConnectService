"""Per-motif PDF *interior* designs for HR Payroll Reports.

Covers (``covers/<motif>.py``) give each report a unique first page; this module
gives each report a unique BODY so the documents stop looking identical past the
cover. Rather than 13 fragile hand-written modules, it is one well-tested engine
driven by a per-motif *recipe* (font family, opener style, table skin, analytics
widgets, page texture). Each recipe yields a visually distinct interior —
serif accounting ledger, magazine editorial, official statutory form, blueprint
grid, boarding-pass ticket, embossed certificate, classified dossier, etc. — while
sharing one robust, WeasyPrint-safe layout core.

Public entry: ``render_body(report_key, shaped_rows, summary, theme, period)``.
``KIT_CSS`` is injected once by ``pdf.render_pdf`` so every body can use the
``.rk-*`` building blocks. Defensive: any unknown motif falls back to a clean
generic interior, so the export endpoint never 500s.
"""
from __future__ import annotations

from datetime import datetime

from .common import (
    COMPANY, esc, inr, inr_group, inr_compact, fmt_date, fmt_days, fmt_pct,
    fmt_signed_pct, now_stamp,
)
from .columns import body_columns, WIDE_REPORTS, PILL_COLORS


# ════════════════════════════════════════════════════════════════════════════
# Cell formatting (mirrors pdf.FORMATTERS so bodies render numbers identically)
# ════════════════════════════════════════════════════════════════════════════

FORMATTERS = {
    "inr": lambda v: inr(v),
    "inr_p": lambda v: inr(v, paise=True),
    "days": fmt_days,
    "pct": fmt_pct,
    "signed_pct": fmt_signed_pct,
    "date": fmt_date,
}


def _cell(row: dict, col: dict) -> tuple[str, str]:
    raw = row.get(col["key"])
    if col.get("status"):
        status = str(raw or "")
        pc = PILL_COLORS.get(status, {"bg": "#f1f5f9", "fg": "#334155"})
        pill = (f'<span class="rk-pill" style="background:{pc["bg"]};color:{pc["fg"]}">'
                f'{esc(status.replace("_", " "))}</span>')
        return pill, ""
    fmt = col.get("fmt")
    if fmt:
        val = FORMATTERS[fmt](raw)
    elif raw is None or raw == "":
        val = "—"
    else:
        val = esc(raw)
    klass = ""
    num = raw if isinstance(raw, (int, float)) else 0
    if col.get("danger_if") and col["danger_if"](num):
        klass = "cell-danger"
    elif col.get("warn_if") and col["warn_if"](num):
        klass = "cell-warn"
    elif col.get("good_if") and col["good_if"](num):
        klass = "cell-good"
    return val, klass


def _align(c: dict) -> str:
    return "r" if c["align"] == "right" else "c" if c["align"] == "center" else ""


def rk_table(report_key: str, rows: list[dict], *, totals: bool = True) -> str:
    cols = body_columns(report_key)
    head = "".join(f'<th class="{_align(c)}">{esc(c["label"])}</th>' for c in cols)
    body = []
    for i, row in enumerate(rows):
        cells = []
        for c in cols:
            html_val, klass = _cell(row, c)
            cls = (_align(c) + " " + klass).strip()
            cells.append(f'<td class="{cls}">{html_val}</td>')
        zebra = "zebra" if i % 2 == 1 else ""
        body.append(f'<tr class="{zebra}">{"".join(cells)}</tr>')
    tot = _totals_row(report_key, rows) if totals else ""
    if not rows:
        return '<div class="rk-empty">No records found for the selected pay period.</div>'
    return (f'<table class="rk-tbl"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}{tot}</tbody></table>')


def _totals_row(report_key: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    cols = body_columns(report_key)
    SUM_FMT = {"inr", "inr_p"}
    cells, first = [], True
    for c in cols:
        if first:
            cells.append('<td class="tot-label">TOTAL</td>'); first = False; continue
        fmt = c.get("fmt"); align = _align(c)
        if fmt in SUM_FMT:
            t = sum(float(r.get(c["key"]) or 0) for r in rows)
            cells.append(f'<td class="tot-val {align}">{inr(t)}</td>')
        elif c["key"] in ("headcount", "months_paid"):
            t = sum(float(r.get(c["key"]) or 0) for r in rows)
            cells.append(f'<td class="tot-val {align}">{int(t)}</td>')
        elif fmt == "days":
            t = sum(float(r.get(c["key"]) or 0) for r in rows)
            cells.append(f'<td class="tot-val {align}">{fmt_days(t)}</td>')
        else:
            cells.append(f'<td class="tot-blank {align}"></td>')
    return f'<tr class="tot-row">{"".join(cells)}</tr>'


# ════════════════════════════════════════════════════════════════════════════
# Analytics widgets (themeable via the .pbody wrapper's CSS vars)
# ════════════════════════════════════════════════════════════════════════════

def rk_kpis(tiles: list) -> str:
    """tiles = [(label, value_html, color|None)]."""
    if not tiles:
        return ""
    cells = "".join(
        f'<div class="rk-kpi"{f" style=\"border-top-color:{c}\"" if c else ""}>'
        f'<div class="l">{esc(l)}</div><div class="v"{f" style=\"color:{c}\"" if c else ""}>{v}</div></div>'
        for l, v, c in tiles)
    return f'<div class="rk-kpis">{cells}</div>'


def rk_strip(stats: list) -> str:
    """stats = [(label, value_html)]; a hairline figure strip."""
    if not stats:
        return ""
    segs = []
    for i, (l, v) in enumerate(stats):
        if i:
            segs.append('<div class="d"></div>')
        segs.append(f'<div class="s"><div class="v">{v}</div><div class="l">{esc(l)}</div></div>')
    return f'<div class="rk-strip">{"".join(segs)}</div>'


def rk_bars(items: list, *, title: str = "") -> str:
    """Horizontal bar chart. items = [(label, value_number, value_html)]."""
    items = [it for it in items if it]
    if not items:
        return ""
    mx = max((float(v) for _, v, _ in items), default=0) or 1
    bars = []
    for label, val, vhtml in items:
        w = max(2.0, float(val) / mx * 100.0)
        bars.append(
            f'<div class="rk-bar"><div class="bl">{esc(label)}</div>'
            f'<div class="bt"><div class="bf" style="width:{w:.1f}%"></div></div>'
            f'<div class="bv">{vhtml}</div></div>')
    head = f'<div class="rk-wtitle">{esc(title)}</div>' if title else ""
    return f'<div class="rk-bars">{head}{"".join(bars)}</div>'


def rk_donut(segments: list, *, center_label: str = "", center_value: str = "", title: str = "") -> str:
    """segments = [(label, value_number, color)]; inline-SVG donut + legend."""
    segments = [s for s in segments if s and float(s[1]) > 0]
    if not segments:
        return ""
    total = sum(float(v) for _, v, _ in segments) or 1
    R, C = 16, 2 * 3.14159265 * 16
    off = 0.0
    arcs = []
    for _, val, color in segments:
        frac = float(val) / total
        dash = frac * C
        arcs.append(
            f'<circle cx="21" cy="21" r="{R}" fill="none" stroke="{color}" stroke-width="9" '
            f'stroke-dasharray="{dash:.2f} {C - dash:.2f}" stroke-dashoffset="{-off:.2f}" '
            f'transform="rotate(-90 21 21)"/>')
        off += dash
    center = (f'<div class="rk-donut-c"><div class="dv">{esc(center_value)}</div>'
              f'<div class="dl">{esc(center_label)}</div></div>') if (center_value or center_label) else ""
    legend = "".join(
        f'<div class="li"><span class="sw" style="background:{color}"></span>'
        f'{esc(label)}</div>' for label, _, color in segments)
    head = f'<div class="rk-wtitle">{esc(title)}</div>' if title else ""
    return (f'<div class="rk-donut-block">{head}<div class="rk-donut-wrap">'
            f'<div class="rk-donut"><svg viewBox="0 0 42 42">{"".join(arcs)}</svg>{center}</div>'
            f'<div class="rk-legend">{legend}</div></div></div>')


def rk_callout(text: str, *, kicker: str = "") -> str:
    k = f'<span class="ck">{esc(kicker)}</span>' if kicker else ""
    return f'<div class="rk-callout">{k}{esc(text)}</div>'


# ════════════════════════════════════════════════════════════════════════════
# Generic analytics derivation (so every report can show *some* chart)
# ════════════════════════════════════════════════════════════════════════════

def _primary_money_col(report_key: str) -> str | None:
    """The headline money column for a report (used to build bar charts)."""
    prefs = {
        "register": "net", "salary-sheet": "net", "statutory": "statutory_total",
        "pf-ecr": "ee_pf", "esi": "total_esi", "professional-tax": "pt",
        "tds-24q": "tds_period", "department-cost": "total_cost", "variance": "delta",
        "ctc-summary": "annual_ctc", "headcount": "total_cost",
        "adjustments": "amount", "ytd-earnings": "ytd_net",
    }
    return prefs.get(report_key)


def _label_col(report_key: str) -> str:
    return {"department-cost": "department", "headcount": "department"}.get(report_key, "employee_name")


def _top_bars(report_key: str, rows: list[dict], n: int = 8) -> list:
    col = _primary_money_col(report_key)
    if not col or not rows:
        return []
    lc = _label_col(report_key)
    ordered = sorted(rows, key=lambda r: abs(float(r.get(col) or 0)), reverse=True)[:n]
    return [(r.get(lc, "—"), abs(float(r.get(col) or 0)), inr_compact(r.get(col) or 0)) for r in ordered]


def _composition_donut(summary: dict) -> str:
    net = float(summary.get("net", 0) or 0)
    ded = float(summary.get("deductions", 0) or 0)
    empr = float(summary.get("employer_cost", 0) or 0)
    if net <= 0 and ded <= 0 and empr <= 0:
        return ""
    total = net + ded + empr
    pct = f"{net / total * 100:.0f}%" if total else ""
    return rk_donut(
        [("Net pay", net, "#0d9488"), ("Deductions", ded, "#b45309"), ("Employer cost", empr, "#6b4e08")],
        center_label="net of cost", center_value=pct, title="Cost composition")


def _summary_strip(report_key: str, summary: dict) -> list:
    """A motif-agnostic figure strip from whatever the summary carries."""
    out = []
    if summary.get("employees") is not None:
        out.append(("Employees", str(summary.get("employees", 0))))
    if report_key == "ctc-summary":
        out += [("Annual CTC", inr_compact(summary.get("annual_ctc", 0))),
                ("Monthly CTC", inr_compact(summary.get("monthly_ctc", 0))),
                ("Avg CTC", inr_compact(summary.get("avg_ctc", 0)))]
        return out
    if report_key == "adjustments":
        out += [("Additions", inr_compact(summary.get("additions", 0))),
                ("Deductions", inr_compact(summary.get("deductions", 0))),
                ("Net impact", inr_compact(summary.get("net_impact", 0)))]
        return out
    if report_key == "ytd-earnings":
        out += [("YTD Gross", inr_compact(summary.get("ytd_gross", 0))),
                ("YTD Net", inr_compact(summary.get("ytd_net", 0))),
                ("YTD TDS", inr_compact(summary.get("ytd_tds", 0)))]
        return out
    if report_key == "variance":
        out += [("Movers", str(summary.get("movers", 0))),
                ("Net Δ", inr_compact(summary.get("net_delta", 0))),
                ("Net pay", inr_compact(summary.get("net", 0)))]
        return out
    for l, k in (("Gross", "gross"), ("Deductions", "deductions"), ("Net pay", "net")):
        if summary.get(k):
            out.append((l, inr_compact(summary[k])))
    return out[:4]


def _widgets_html(names: list, report_key: str, rows: list[dict], summary: dict) -> str:
    out = []
    for name in names:
        if name == "strip":
            out.append(rk_strip(_summary_strip(report_key, summary)))
        elif name == "kpi":
            strip = _summary_strip(report_key, summary)
            out.append(rk_kpis([(l, v, None) for l, v in strip]))
        elif name == "bars":
            label = _label_col(report_key)
            title = "By department" if label == "department" else "Top by value"
            out.append(rk_bars(_top_bars(report_key, rows), title=title))
        elif name == "donut":
            out.append(_composition_donut(summary))
    return "".join(o for o in out if o)


# ════════════════════════════════════════════════════════════════════════════
# Section ref + small helpers
# ════════════════════════════════════════════════════════════════════════════

def _ref(report_key: str, period: dict) -> str:
    return f"FRC/PAY/{report_key.upper().replace('-', '')}/{period.get('year', '')}/{datetime.now().strftime('%m%d%H%M')}"


def _count_line(rows, summary, period) -> str:
    n = len(rows)
    emp = summary.get("employees", summary.get("rows", n))
    return (f'{n} record{"" if n == 1 else "s"} · {emp} employee(s) · {esc(period.get("label", ""))}')


# ════════════════════════════════════════════════════════════════════════════
# OPENERS — one bespoke section-opener per motif (more added incrementally)
# ════════════════════════════════════════════════════════════════════════════

def _op_generic(theme, summary, period, report_key, rows) -> str:
    a, d = theme["accent"], theme["accent_deep"]
    return f"""
    <div class="op op-generic">
      <div class="op-head">
        <div class="op-co">{esc(COMPANY['name'])} · {esc(theme['name'])}</div>
        <div class="op-ref">Ref {esc(_ref(report_key, period))} · FY {esc(period.get('fy',''))}</div>
      </div>
      <div class="op-eyebrow" style="color:{a}">PAYROLL · {esc(theme.get('group','').upper())} · {esc(period.get('label',''))}</div>
      <h2 class="op-title" style="color:{d}">{esc(theme['name'])}</h2>
      <div class="op-rule" style="background:{a}"></div>
      <p class="op-sub">{_count_line(rows, summary, period)}</p>
    </div>"""


def _op_ledger(theme, summary, period, report_key, rows) -> str:
    a, d, soft = theme["accent"], theme["accent_deep"], theme["accent_soft"]
    return f"""
    <div class="op op-ledger">
      <div class="lo-rail"></div>
      <div class="lo-top">
        <div class="lo-folio">FOLIO<br><b>{esc(period.get('short',''))}</b></div>
        <div class="lo-center">
          <div class="lo-eyebrow">{esc(COMPANY['name'])} Treasury · Posted Ledger</div>
          <h2 class="lo-title">{esc(theme['name'])}</h2>
          <div class="lo-flourish">&#10086; &bull; &#10087;</div>
        </div>
        <div class="lo-ref">{esc(_ref(report_key, period))}<br>FY {esc(period.get('fy',''))}</div>
      </div>
      <div class="lo-sub">{esc(theme['subtitle'])} — {_count_line(rows, summary, period)}.</div>
    </div>"""


def _op_editorial(theme, summary, period, report_key, rows) -> str:
    a, d = theme["accent"], theme["accent_deep"]
    name = esc(theme["name"]); first = name.split(" ")[0]; restn = " ".join(name.split(" ")[1:])
    return f"""
    <div class="op op-editorial">
      <div class="eo-kicker"><span>{esc(COMPANY['name'])} Payroll Review</span><span>{esc(period.get('label',''))} · FY {esc(period.get('fy',''))}</span></div>
      <div class="eo-rule"></div>
      <h2 class="eo-title">{first} <span class="em" style="color:{d}">{restn or ''}</span></h2>
      <p class="eo-stand"><span class="dc" style="color:{a}">{esc(theme.get('tagline','')[:1])}</span>{esc(theme.get('tagline','')[1:])}. {esc(theme['subtitle'])} — reconciled across {_count_line(rows, summary, period)}.</p>
    </div>"""


def _op_seal(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-seal">
      <div class="se-seal"><div class="se-in"><span class="se-tick">&#10003;</span><span class="se-w">Certified</span></div></div>
      <div class="se-body">
        <div class="se-eyebrow">{esc(COMPANY['name'])} · Statutory Compliance Statement</div>
        <h2 class="se-title">{esc(theme['name'])}</h2>
        <div class="se-sub">{esc(theme['subtitle'])} — {_count_line(rows, summary, period)}.</div>
        <div class="se-tags"><span>PF</span><span>ESI</span><span>PT</span><span>TDS</span>
          <span class="se-period">{esc(period.get('label',''))} · FY {esc(period.get('fy',''))}</span></div>
      </div>
    </div>"""


def _op_form(title_line, form_tag, fields):
    def _r(theme, summary, period, report_key, rows):
        cells = "".join(f'<div class="gf"><span>{esc(l)}</span><b>{v}</b></div>' for l, v in fields(theme, summary, period, rows))
        return f"""
        <div class="op op-govt">
          <div class="go-band">{esc(title_line)}</div>
          <div class="go-row"><div class="go-title">{esc(theme['name'])}</div><div class="go-form">{esc(form_tag)}</div></div>
          <div class="go-fields">{cells}</div>
        </div>"""
    return _r


_op_govt_pf = _op_form(
    "Employees' Provident Fund Organisation · Ministry of Labour",
    "FORM · ECR",
    lambda t, s, p, rows: [
        ("Establishment", esc(COMPANY['legal'])),
        ("Wage Month", esc(p.get('label', ''))),
        ("Members", str(len(rows))),
        ("TRRN", esc(_ref(t.get('motif', 'pf'), p))),
    ])

_op_govt_esi = _op_form(
    "Employees' State Insurance Corporation · Monthly Return",
    "ESIC · MC",
    lambda t, s, p, rows: [
        ("Employer", esc(COMPANY['legal'])),
        ("Contribution Period", esc(p.get('label', ''))),
        ("Insured Persons", str(len(rows))),
        ("Challan Ref", esc(_ref('esi', p))),
    ])


def _op_slab(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-slab">
      <div class="sl-left">
        <div class="sl-eyebrow">State Professional Tax · Remittance</div>
        <h2 class="sl-title">{esc(theme['name'])}</h2>
        <div class="sl-sub">{esc(theme['subtitle'])} — {_count_line(rows, summary, period)}.</div>
      </div>
      <div class="sl-steps">
        <div class="st s1"><span>up to 15k</span><b>NIL</b></div>
        <div class="st s2"><span>15k – 20k</span><b>&#8377;150</b></div>
        <div class="st s3"><span>20k &amp; above</span><b>&#8377;200</b></div>
      </div>
    </div>"""


def _op_dossier(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-dossier">
      <div class="do-tab">FORM 24Q</div>
      <div class="do-sheet">
        <div class="do-top"><span class="do-class">Confidential</span><span class="do-ref">{esc(_ref(report_key, period))}</span></div>
        <h2 class="do-title">{esc(theme['name'])}</h2>
        <div class="do-redact"></div>
        <div class="do-sub">{esc(theme['subtitle'])} — {_count_line(rows, summary, period)}.</div>
        <div class="do-stamp">QUARTERLY · FY {esc(period.get('fy',''))}</div>
      </div>
    </div>"""


def _op_industrial(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-industrial">
      <div class="in-stripe"></div>
      <div class="in-row">
        <div><div class="in-eyebrow">Cost-Centre Control · {esc(COMPANY['name'])}</div>
          <h2 class="in-title">{esc(theme['name'])}</h2></div>
        <div class="in-code">C C<br><b>{esc(period.get('short',''))}</b></div>
      </div>
      <div class="in-sub">{esc(theme['subtitle'])} — {_count_line(rows, summary, period)}.</div>
    </div>"""


def _op_bulletin(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-bulletin">
      <div class="bu-mast">The {esc(COMPANY['name'])} Bulletin</div>
      <div class="bu-dateline"><span>Vol. {esc(period.get('year',''))}</span>
        <span>{esc(period.get('label',''))} · FY {esc(period.get('fy',''))}</span>
        <span>{summary.get('movers', len(rows))} movements tracked</span></div>
      <div class="bu-rule"></div>
      <h2 class="bu-head">{esc(theme['name'])}: {esc(theme['subtitle'])}</h2>
      <div class="bu-sub">{_count_line(rows, summary, period)}.</div>
    </div>"""


def _op_postcard(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-postcard">
      <div class="pc-left">
        <div class="pc-eyebrow">Greetings from Compensation</div>
        <h2 class="pc-title">{esc(theme['name'])}</h2>
        <div class="pc-sub">{esc(theme['subtitle'])} — {_count_line(rows, summary, period)}.</div>
      </div>
      <div class="pc-right">
        <div class="pc-stamp">&#8377;<span>{esc(period.get('short',''))}</span></div>
        <div class="pc-mark">{esc(COMPANY['name'])}<br>FY {esc(period.get('fy',''))}</div>
      </div>
    </div>"""


def _op_blueprint(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-blueprint">
      <div class="bp-grid"></div>
      <div class="bp-head"><div class="bp-eyebrow">Workforce Schematic · {esc(COMPANY['name'])}</div>
        <h2 class="bp-title">{esc(theme['name'])}</h2></div>
      <div class="bp-block">
        <div class="bp-cell big"><span>Drawing</span><b>{esc(theme['tagline'])}</b></div>
        <div class="bp-cell"><span>Period</span><b>{esc(period.get('label',''))}</b></div>
        <div class="bp-cell"><span>Sheet</span><b>{len(rows)} rec</b></div>
        <div class="bp-cell"><span>Ref</span><b>{esc(_ref(report_key, period))}</b></div>
      </div>
    </div>"""


def _op_ticket(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-ticket">
      <div class="tk-main">
        <div class="tk-eyebrow">Payroll Adjustments · Pass</div>
        <h2 class="tk-title">{esc(theme['name'])}</h2>
        <div class="tk-fields"><div><span>Period</span><b>{esc(period.get('label',''))}</b></div>
          <div><span>Entries</span><b>{len(rows)}</b></div>
          <div><span>FY</span><b>{esc(period.get('fy',''))}</b></div></div>
      </div>
      <div class="tk-perf"></div>
      <div class="tk-stub"><div class="tk-barcode"></div><div class="tk-stub-l">STUB · {esc(period.get('short',''))}</div></div>
    </div>"""


def _op_certificate(theme, summary, period, report_key, rows) -> str:
    return f"""
    <div class="op op-certificate">
      <div class="ce-frame">
        <div class="ce-eyebrow">{esc(COMPANY['name'])} · Office of Payroll</div>
        <div class="ce-pre">This certifies the</div>
        <h2 class="ce-title">{esc(theme['name'])}</h2>
        <div class="ce-sub">{esc(theme['subtitle'])}</div>
        <div class="ce-meta">{esc(period.get('label',''))} &nbsp;·&nbsp; FY {esc(period.get('fy',''))} &nbsp;·&nbsp; {_count_line(rows, summary, period)}</div>
        <div class="ce-ribbon">&#9733;</div>
      </div>
    </div>"""


OPENERS = {
    "generic": _op_generic,
    "ledger": _op_ledger,
    "editorial": _op_editorial,
    "seal": _op_seal,
    "govt-pf": _op_govt_pf,
    "govt-esi": _op_govt_esi,
    "slab": _op_slab,
    "dossier": _op_dossier,
    "industrial": _op_industrial,
    "bulletin": _op_bulletin,
    "postcard": _op_postcard,
    "blueprint": _op_blueprint,
    "ticket": _op_ticket,
    "certificate": _op_certificate,
}


# ════════════════════════════════════════════════════════════════════════════
# RECIPES — motif → interior design language
# ════════════════════════════════════════════════════════════════════════════

_SERIF = "'Georgia','Times New Roman',serif"
_SANS = "'Helvetica','Arial',sans-serif"
_MONO = "'Courier New','Consolas',monospace"

BODY_RECIPES = {
    "ledger":      {"font": _SERIF, "opener": "ledger",    "skin": "ruled",   "widgets": ["strip", "bars"]},
    "editorial":   {"font": _SERIF, "opener": "editorial", "skin": "minimal", "widgets": ["kpi", "bars"]},
    "seal":        {"font": _SERIF, "opener": "seal",      "skin": "boxed",   "widgets": ["strip", "donut"]},
    "govt-pf":     {"font": _MONO,  "opener": "govt-pf",   "skin": "form",    "widgets": ["strip"]},
    "govt-esi":    {"font": _MONO,  "opener": "govt-esi",  "skin": "form",    "widgets": ["strip"]},
    "slab":        {"font": _SANS,  "opener": "slab",      "skin": "minimal", "widgets": ["bars"]},
    "dossier":     {"font": _MONO,  "opener": "dossier",   "skin": "dark",    "widgets": ["strip", "bars"]},
    "industrial":  {"font": _SANS,  "opener": "industrial",  "skin": "boxed",   "widgets": ["bars", "strip"]},
    "bulletin":    {"font": _SERIF, "opener": "bulletin",    "skin": "minimal", "widgets": ["kpi", "bars"]},
    "postcard":    {"font": _SANS,  "opener": "postcard",    "skin": "minimal", "widgets": ["donut", "strip"]},
    "blueprint":   {"font": _MONO,  "opener": "blueprint",   "skin": "boxed",   "widgets": ["bars", "strip"]},
    "ticket":      {"font": _SANS,  "opener": "ticket",      "skin": "ticket",  "widgets": ["strip"]},
    "certificate": {"font": _SERIF, "opener": "certificate", "skin": "ruled",   "widgets": ["strip"]},
}


def _recipe(motif: str) -> dict:
    return BODY_RECIPES.get(motif) or {"font": _SANS, "opener": "generic", "skin": "minimal", "widgets": ["strip", "bars"]}


# ════════════════════════════════════════════════════════════════════════════
# Public entry
# ════════════════════════════════════════════════════════════════════════════

def render_body(report_key: str, shaped_rows: list[dict], summary: dict, theme: dict, period: dict) -> str:
    motif = theme.get("motif", "ledger")
    rec = _recipe(motif)
    opener_fn = OPENERS.get(rec["opener"], _op_generic)
    opener = opener_fn(theme, summary, period, report_key, shaped_rows)
    widgets = _widgets_html(rec["widgets"], report_key, shaped_rows, summary) if shaped_rows else ""
    table = rk_table(report_key, shaped_rows)

    wide = "body-wide" if report_key in WIDE_REPORTS else ""
    a, d, soft = theme["accent"], theme["accent_deep"], theme["accent_soft"]
    style = (f"--rk-accent:{a};--rk-deep:{d};--rk-soft:{soft};"
             f"font-family:{rec['font']};")
    motif_cls = "m-" + motif.replace("-", "_")
    return (f'<section class="pbody {wide} skin-{rec["skin"]} {motif_cls}" style="{style}">'
            f'{opener}{widgets}{table}</section>')


# ════════════════════════════════════════════════════════════════════════════
# KIT_CSS — injected once by pdf.render_pdf; shared by every body
# ════════════════════════════════════════════════════════════════════════════

KIT_CSS = """
/* ============ payroll report BODY kit ============ */
.pbody { position:relative; }
.pbody .rk-wtitle { font-size:7pt; letter-spacing:2pt; font-weight:800; text-transform:uppercase;
    color:var(--rk-deep); margin:0 0 2.5mm; }

/* ---- table (themeable) ---- */
.rk-tbl { width:100%; border-collapse:collapse; margin-top:4mm; font-size:8pt; table-layout:auto; }
.rk-tbl thead { display:table-header-group; }
.rk-tbl tbody tr { page-break-inside:avoid; break-inside:avoid; }
.rk-tbl th { background:var(--rk-deep); color:#fff; text-align:left; padding:2.4mm 1.8mm;
    font-weight:800; font-size:7pt; letter-spacing:.5pt; text-transform:uppercase; }
.rk-tbl th.r { text-align:right; } .rk-tbl th.c { text-align:center; }
.rk-tbl td { padding:1.7mm 1.8mm; border-bottom:.4pt solid #e6dcc6; vertical-align:middle; color:#1a1410; }
.rk-tbl td.r { text-align:right; font-variant-numeric:tabular-nums; }
.rk-tbl td.c { text-align:center; }
.rk-tbl tr.zebra td { background:#fbf6ea; }
.rk-tbl td.cell-danger { background:#fee2e2; color:#7f1d1d; font-weight:800; border-left:1.2pt solid #b91c1c; }
.rk-tbl td.cell-warn { background:#fef9c3; color:#713f12; font-weight:800; border-left:1.2pt solid #a16207; }
.rk-tbl td.cell-good { background:#ccfbf1; color:#115e59; font-weight:800; border-left:1.2pt solid #0d9488; }
.rk-tbl .tot-row td { background:var(--rk-deep); color:#fde68a; font-weight:900; padding:2.2mm 1.8mm; border:0; }
.rk-tbl .tot-row td.tot-label { letter-spacing:1.5pt; }
.rk-tbl .tot-row td.r { text-align:right; }
.rk-pill { display:inline-block; padding:1mm 2.5mm; border-radius:6mm; font-size:6.8pt; font-weight:800;
    letter-spacing:.6pt; text-transform:uppercase; line-height:1.1; }
.rk-empty { margin:30mm 0; text-align:center; color:#8a7c63; font-style:italic; }
.body-wide .rk-tbl { font-size:7.2pt; }
.body-wide .rk-tbl th { font-size:6.8pt; padding:2mm 1.4mm; }
.body-wide .rk-tbl td { padding:1.4mm 1.4mm; }

/* ---- table skins ---- */
.skin-minimal .rk-tbl th { background:transparent; color:var(--rk-deep); border-bottom:1.2pt solid var(--rk-accent); }
.skin-minimal .rk-tbl tr.zebra td { background:transparent; }
.skin-minimal .rk-tbl td { border-bottom:.3pt solid #ece3cf; }
.skin-minimal .rk-tbl .tot-row td { background:transparent; color:var(--rk-deep); border-top:1.2pt solid var(--rk-deep); }

.skin-ruled .rk-tbl th { background:transparent; color:var(--rk-deep); border-top:.8pt solid var(--rk-deep);
    border-bottom:.8pt solid var(--rk-deep); }
.skin-ruled .rk-tbl td { border-bottom:.4pt solid #d9c9a4; }
.skin-ruled .rk-tbl tr.zebra td { background:#fffdf4; }
.skin-ruled .rk-tbl .tot-row td { background:var(--rk-soft); color:var(--rk-deep); border-top:1pt solid var(--rk-deep);
    border-bottom:1pt double var(--rk-deep); }

.skin-boxed .rk-tbl th { background:var(--rk-deep); color:#fff; border:.5pt solid var(--rk-deep); }
.skin-boxed .rk-tbl td { border:.5pt solid #c9bda0; }
.skin-boxed .rk-tbl tr.zebra td { background:#faf5ea; }

.skin-form .rk-tbl { font-family:'Courier New',monospace; }
.skin-form .rk-tbl th { background:var(--rk-soft); color:var(--rk-deep); border:.5pt solid var(--rk-deep); font-size:6.6pt; }
.skin-form .rk-tbl td { border:.5pt solid #b9c4ba; font-size:7.4pt; }
.skin-form .rk-tbl tr.zebra td { background:#f4faf6; }
.skin-form .rk-tbl .tot-row td { background:var(--rk-deep); color:#fff; }

.skin-dark .rk-tbl th { background:#1a1410; color:var(--rk-accent); border-bottom:1.5pt solid var(--rk-accent); }
.skin-dark .rk-tbl td { border-bottom:.4pt solid #d8cdb5; }
.skin-dark .rk-tbl tr.zebra td { background:#f6f1e6; }
.skin-dark .rk-tbl .tot-row td { background:#1a1410; color:var(--rk-accent); }

.skin-ticket .rk-tbl th { background:var(--rk-deep); color:#fff; border:0; border-bottom:1mm dotted var(--rk-accent); }
.skin-ticket .rk-tbl td { border-bottom:.4pt dashed #cbb894; }
.skin-ticket .rk-tbl tr.zebra td { background:#fff7f8; }

/* ---- KPI band ---- */
.rk-kpis { display:flex; gap:4mm; margin:6mm 0 0; break-inside:avoid; }
.rk-kpi { flex:1; padding:4mm 3mm 5mm; border:.6pt solid #d8cdb5; border-top:2mm solid var(--rk-accent);
    text-align:center; background:#fff; }
.rk-kpi .l { font-size:6.3pt; letter-spacing:1.2pt; font-weight:800; color:#6b5840; text-transform:uppercase; margin-bottom:2mm; }
.rk-kpi .v { font-size:15pt; font-weight:900; line-height:1; color:var(--rk-deep); font-variant-numeric:tabular-nums; }

/* ---- figure strip ---- */
.rk-strip { display:flex; align-items:stretch; border-top:.6pt solid var(--rk-accent);
    border-bottom:.6pt solid var(--rk-accent); margin:6mm 0 0; padding:3.5mm 0; break-inside:avoid; }
.rk-strip .s { flex:1; text-align:center; }
.rk-strip .s .v { font-size:14pt; font-weight:800; color:var(--rk-deep); letter-spacing:-.2pt; font-variant-numeric:tabular-nums; }
.rk-strip .s .l { font-size:6.2pt; letter-spacing:1.6pt; text-transform:uppercase; color:#8a7c63; margin-top:1.4mm; }
.rk-strip .d { width:.5pt; background:#d8cdb5; margin:0 1mm; }

/* ---- horizontal bars ---- */
.rk-bars { margin:6mm 0 0; break-inside:avoid; }
.rk-bar { display:flex; align-items:center; gap:3mm; margin:0 0 2.2mm; }
.rk-bar .bl { width:44mm; font-size:7.4pt; color:#3a322a; text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.rk-bar .bt { flex:1; height:4.4mm; background:#efe6d3; border-radius:1mm; overflow:hidden; }
.rk-bar .bf { height:100%; background:linear-gradient(90deg,var(--rk-accent),var(--rk-deep)); border-radius:1mm; }
.rk-bar .bv { width:24mm; font-size:7.4pt; font-weight:700; color:var(--rk-deep); text-align:right; font-variant-numeric:tabular-nums; }

/* ---- donut ---- */
.rk-donut-block { margin:6mm 0 0; break-inside:avoid; }
.rk-donut-wrap { display:flex; align-items:center; gap:9mm; }
.rk-donut { position:relative; width:34mm; height:34mm; flex-shrink:0; }
.rk-donut svg { width:34mm; height:34mm; }
.rk-donut-c { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; }
.rk-donut-c .dv { font-size:12pt; font-weight:900; color:var(--rk-deep); }
.rk-donut-c .dl { font-size:5.6pt; letter-spacing:1pt; text-transform:uppercase; color:#8a7c63; margin-top:.6mm; }
.rk-legend .li { display:flex; align-items:center; gap:2.4mm; font-size:8.5pt; color:#3a322a; margin-bottom:2mm; }
.rk-legend .sw { width:3.2mm; height:3.2mm; border-radius:.6mm; display:inline-block; }

/* ---- callout ---- */
.rk-callout { margin:6mm 0 0; padding:3.5mm 5mm; background:var(--rk-soft); border-left:1.2mm solid var(--rk-accent);
    font-size:8.5pt; line-height:1.5; color:#3a322a; break-inside:avoid; }
.rk-callout .ck { display:block; font-size:6.4pt; letter-spacing:2pt; font-weight:800; text-transform:uppercase;
    color:var(--rk-deep); margin-bottom:1.5mm; }

/* ============ OPENERS ============ */
/* generic */
.op-generic .op-head { display:flex; justify-content:space-between; align-items:baseline;
    border-bottom:.6pt solid #d8cdb5; padding-bottom:2.5mm; }
.op-generic .op-co { font-size:11pt; font-weight:800; letter-spacing:-.2pt; }
.op-generic .op-ref { font-size:7pt; color:#8a7c63; font-family:'Courier New',monospace; }
.op-generic .op-eyebrow { font-size:7pt; letter-spacing:2.5pt; font-weight:800; text-transform:uppercase; margin:6mm 0 1.5mm; }
.op-generic .op-title { font-size:26pt; font-weight:900; letter-spacing:-.5pt; line-height:1.04; margin:0; }
.op-generic .op-rule { width:26mm; height:1.1mm; margin:3mm 0 3mm; border-radius:.6mm; }
.op-generic .op-sub { margin:0; font-size:9pt; color:#5b4a30; }

/* ledger */
.op-ledger { position:relative; padding:0 0 2mm; }
.op-ledger .lo-rail { position:absolute; left:0; top:0; bottom:0; width:1mm; background:var(--rk-accent); }
.op-ledger .lo-top { display:flex; justify-content:space-between; align-items:flex-start; padding-left:6mm;
    border-bottom:.8pt double var(--rk-deep); padding-bottom:3mm; }
.op-ledger .lo-folio, .op-ledger .lo-ref { font-family:'Courier New',monospace; font-size:6.6pt; color:#8a7c63;
    letter-spacing:.6pt; line-height:1.5; }
.op-ledger .lo-folio b { color:var(--rk-deep); font-size:9pt; }
.op-ledger .lo-ref { text-align:right; }
.op-ledger .lo-center { text-align:center; }
.op-ledger .lo-eyebrow { font-family:'Helvetica',sans-serif; font-size:6.8pt; letter-spacing:3.4pt;
    text-transform:uppercase; color:var(--rk-accent); }
.op-ledger .lo-title { font-size:27pt; font-weight:bold; color:#231a0d; margin:2mm 0 0; letter-spacing:-.3pt; }
.op-ledger .lo-flourish { color:var(--rk-accent); font-size:11pt; letter-spacing:4pt; margin-top:1mm; }
.op-ledger .lo-sub { padding-left:6mm; font-style:italic; font-size:9.5pt; color:#5b4a30; margin-top:3mm; }

/* editorial */
.op-editorial .eo-kicker { display:flex; justify-content:space-between; font-family:'Helvetica',sans-serif;
    font-size:7pt; letter-spacing:2.5pt; font-weight:800; text-transform:uppercase; color:var(--rk-accent); }
.op-editorial .eo-rule { height:1.2pt; background:var(--rk-deep); margin:2.5mm 0 4mm; }
.op-editorial .eo-title { font-size:34pt; font-weight:700; line-height:1; letter-spacing:-1pt; color:#1a1410; margin:0; }
.op-editorial .eo-title .em { font-style:italic; }
.op-editorial .eo-stand { font-size:10.5pt; line-height:1.55; color:#3a322a; margin:5mm 0 0; text-align:justify; }
.op-editorial .eo-stand .dc { font-size:24pt; font-weight:700; float:left; line-height:.9; padding:1mm 2mm 0 0; }

/* seal (statutory) */
.op-seal { display:flex; align-items:center; gap:8mm; border-bottom:.8pt solid var(--rk-accent); padding-bottom:5mm; }
.op-seal .se-seal { width:30mm; height:30mm; border-radius:50%; flex-shrink:0;
    background:radial-gradient(circle at 38% 34%,#aef0e6 0%,var(--rk-accent) 46%,var(--rk-deep) 100%);
    box-shadow:0 0 0 1.2mm #fff,0 0 0 2mm var(--rk-deep); position:relative; }
.op-seal .se-in { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; color:#fff; }
.op-seal .se-in::before { content:""; position:absolute; inset:2.6mm; border:.4mm dashed #ffffffcc; border-radius:50%; }
.op-seal .se-tick { font-size:13pt; font-weight:bold; line-height:1; }
.op-seal .se-w { font-size:4.6pt; letter-spacing:1.8pt; text-transform:uppercase; margin-top:1mm; }
.op-seal .se-eyebrow { font-family:'Helvetica',sans-serif; font-size:7pt; letter-spacing:3pt; text-transform:uppercase; color:var(--rk-accent); font-weight:800; }
.op-seal .se-title { font-size:26pt; font-weight:bold; color:#16302c; margin:2mm 0 0; letter-spacing:-.3pt; }
.op-seal .se-sub { font-style:italic; font-size:9.5pt; color:#3f5b54; margin-top:2mm; }
.op-seal .se-tags { margin-top:3mm; display:flex; align-items:center; gap:2mm; }
.op-seal .se-tags span { font-family:'Helvetica',sans-serif; font-size:6.6pt; font-weight:800; letter-spacing:1pt;
    padding:1mm 2.6mm; border:.5pt solid var(--rk-accent); border-radius:5mm; color:var(--rk-deep); }
.op-seal .se-tags .se-period { border:0; color:#8a7c63; letter-spacing:.6pt; font-weight:600; }

/* govt forms (PF / ESI) */
.op-govt { border:.6mm solid var(--rk-deep); }
.op-govt .go-band { background:var(--rk-deep); color:#fff; font-family:'Helvetica',sans-serif; font-size:7pt;
    font-weight:800; letter-spacing:1.6pt; text-transform:uppercase; padding:2.4mm 4mm; text-align:center; }
.op-govt .go-row { display:flex; justify-content:space-between; align-items:center; padding:3.5mm 4mm 2mm;
    border-bottom:.4pt solid var(--rk-deep); }
.op-govt .go-title { font-size:16pt; font-weight:800; color:var(--rk-deep); letter-spacing:-.2pt; }
.op-govt .go-form { font-family:'Courier New',monospace; font-size:8pt; font-weight:700; color:#fff;
    background:var(--rk-accent); padding:1mm 3mm; border-radius:1mm; }
.op-govt .go-fields { display:flex; flex-wrap:wrap; }
.op-govt .gf { width:50%; box-sizing:border-box; padding:2.6mm 4mm; border-bottom:.3pt solid #c4ccc4; }
.op-govt .gf:nth-child(odd) { border-right:.3pt solid #c4ccc4; }
.op-govt .gf span { display:block; font-family:'Helvetica',sans-serif; font-size:6pt; letter-spacing:1.4pt;
    text-transform:uppercase; color:#6b7d6e; margin-bottom:1mm; }
.op-govt .gf b { font-family:'Courier New',monospace; font-size:9pt; color:#1a1410; }

/* slab (professional tax) */
.op-slab { display:flex; justify-content:space-between; align-items:flex-end; gap:8mm;
    border-bottom:1.2pt solid var(--rk-accent); padding-bottom:4mm; }
.op-slab .sl-eyebrow { font-size:7pt; letter-spacing:2.5pt; font-weight:800; text-transform:uppercase; color:var(--rk-accent); }
.op-slab .sl-title { font-size:26pt; font-weight:900; color:var(--rk-deep); margin:2mm 0 0; letter-spacing:-.4pt; }
.op-slab .sl-sub { font-size:9pt; color:#5b4a30; margin-top:2mm; }
.op-slab .sl-steps { display:flex; align-items:flex-end; gap:2mm; flex-shrink:0; }
.op-slab .st { width:20mm; text-align:center; color:#fff; border-radius:1mm 1mm 0 0; padding:2mm 1mm; }
.op-slab .st span { display:block; font-size:6pt; letter-spacing:.4pt; opacity:.92; }
.op-slab .st b { font-size:10pt; }
.op-slab .st.s1 { height:11mm; background:var(--rk-accent)99; }
.op-slab .st.s2 { height:16mm; background:var(--rk-accent); }
.op-slab .st.s3 { height:22mm; background:var(--rk-deep); }

/* dossier (TDS 24Q) */
.op-dossier { position:relative; padding-top:5mm; }
.op-dossier .do-tab { display:inline-block; background:var(--rk-deep); color:#fff; font-family:'Courier New',monospace;
    font-size:7pt; font-weight:700; letter-spacing:2pt; padding:1.4mm 5mm 1.2mm; border-radius:1.5mm 1.5mm 0 0; }
.op-dossier .do-sheet { border:.5mm solid var(--rk-deep); background:#faf7fb; padding:4mm 5mm 5mm; position:relative; }
.op-dossier .do-top { display:flex; justify-content:space-between; align-items:center; }
.op-dossier .do-class { font-family:'Courier New',monospace; font-size:7pt; font-weight:700; letter-spacing:2.5pt;
    text-transform:uppercase; color:#fff; background:var(--rk-accent); padding:.8mm 3mm; }
.op-dossier .do-ref { font-family:'Courier New',monospace; font-size:6.6pt; color:#8a7c63; }
.op-dossier .do-title { font-size:24pt; font-weight:900; color:var(--rk-deep); margin:3mm 0 0; letter-spacing:-.3pt;
    font-family:'Helvetica',sans-serif; }
.op-dossier .do-redact { height:3mm; width:62mm; background:#1a1410; margin:2.5mm 0; opacity:.82; }
.op-dossier .do-sub { font-family:'Courier New',monospace; font-size:8pt; color:#4b4b5a; }
.op-dossier .do-stamp { position:absolute; right:5mm; bottom:4mm; border:.5mm solid var(--rk-accent);
    color:var(--rk-accent); font-family:'Courier New',monospace; font-size:7pt; font-weight:700; letter-spacing:1.5pt;
    padding:1.5mm 3mm; transform:rotate(-7deg); border-radius:1mm; }

/* industrial (department cost) */
.op-industrial .in-stripe { height:4mm; background:repeating-linear-gradient(45deg,var(--rk-deep) 0,var(--rk-deep) 3mm,#1a1410 3mm,#1a1410 6mm); }
.op-industrial .in-row { display:flex; justify-content:space-between; align-items:center; margin-top:4mm; }
.op-industrial .in-eyebrow { font-size:7pt; letter-spacing:2.5pt; font-weight:800; text-transform:uppercase; color:var(--rk-accent); }
.op-industrial .in-title { font-size:30pt; font-weight:900; color:var(--rk-deep); margin:1.5mm 0 0; letter-spacing:-.6pt; text-transform:uppercase; }
.op-industrial .in-code { text-align:center; font-family:'Helvetica',sans-serif; font-size:6.5pt; letter-spacing:2pt; color:#8a7c63;
    border:.5mm solid var(--rk-deep); padding:2mm 3mm; }
.op-industrial .in-code b { display:block; font-size:11pt; color:var(--rk-deep); letter-spacing:0; margin-top:1mm; }
.op-industrial .in-sub { font-size:9pt; color:#5b4a30; margin-top:3mm; border-top:.4pt solid #d8cdb5; padding-top:2.5mm; }

/* bulletin (variance) */
.op-bulletin { text-align:center; }
.op-bulletin .bu-mast { font-size:30pt; font-weight:900; letter-spacing:-.5pt; color:#1a1410; }
.op-bulletin .bu-dateline { display:flex; justify-content:space-between; font-family:'Helvetica',sans-serif; font-size:6.6pt;
    letter-spacing:1.4pt; text-transform:uppercase; color:#8a7c63; border-top:.5pt solid #1a1410; border-bottom:.5pt solid #1a1410;
    padding:1.4mm 0; margin-top:2mm; }
.op-bulletin .bu-rule { height:1.4pt; background:#1a1410; margin:0 0 4mm; }
.op-bulletin .bu-head { font-size:17pt; font-weight:700; color:var(--rk-deep); margin:0; line-height:1.15; }
.op-bulletin .bu-sub { font-style:italic; font-size:9pt; color:#5b4a30; margin-top:2mm; }

/* postcard (CTC) */
.op-postcard { display:flex; gap:7mm; border:.5mm solid var(--rk-accent); padding:5mm 6mm; }
.op-postcard .pc-left { flex:1; border-right:.4mm dashed var(--rk-accent)99; padding-right:7mm; }
.op-postcard .pc-eyebrow { font-size:7pt; letter-spacing:2.5pt; font-weight:800; text-transform:uppercase; color:var(--rk-accent); }
.op-postcard .pc-title { font-size:25pt; font-weight:900; color:var(--rk-deep); margin:2mm 0 0; letter-spacing:-.4pt; }
.op-postcard .pc-sub { font-size:9pt; color:#5b4a30; margin-top:2mm; }
.op-postcard .pc-right { width:34mm; flex-shrink:0; display:flex; flex-direction:column; align-items:center; gap:3mm; }
.op-postcard .pc-stamp { width:20mm; height:24mm; border:.6mm dashed var(--rk-deep); display:flex; flex-direction:column;
    align-items:center; justify-content:center; color:var(--rk-deep); font-size:15pt; font-weight:900; }
.op-postcard .pc-stamp span { font-size:6pt; letter-spacing:1pt; font-weight:700; margin-top:1mm; }
.op-postcard .pc-mark { width:24mm; height:24mm; border-radius:50%; border:.5mm solid var(--rk-accent); display:flex;
    align-items:center; justify-content:center; text-align:center; font-size:6.2pt; letter-spacing:.6pt; color:var(--rk-accent);
    text-transform:uppercase; line-height:1.4; }

/* blueprint (headcount) */
.op-blueprint { position:relative; border:.5mm solid var(--rk-deep); padding:5mm; overflow:hidden; }
.op-blueprint .bp-grid { position:absolute; inset:0; opacity:.5;
    background:repeating-linear-gradient(0deg,transparent 0,transparent 5mm,var(--rk-accent)22 5mm,var(--rk-accent)22 5.2mm),
               repeating-linear-gradient(90deg,transparent 0,transparent 5mm,var(--rk-accent)22 5mm,var(--rk-accent)22 5.2mm); }
.op-blueprint .bp-head { position:relative; }
.op-blueprint .bp-eyebrow { font-family:'Courier New',monospace; font-size:7pt; letter-spacing:2.5pt; text-transform:uppercase; color:var(--rk-accent); }
.op-blueprint .bp-title { font-size:27pt; font-weight:900; color:var(--rk-deep); margin:1.5mm 0 0; letter-spacing:-.4pt; }
.op-blueprint .bp-block { position:relative; display:flex; margin-top:4mm; border:.4mm solid var(--rk-deep); background:#fffdf8cc; }
.op-blueprint .bp-cell { flex:1; padding:2.4mm 3mm; border-right:.4mm solid var(--rk-deep); font-family:'Courier New',monospace; }
.op-blueprint .bp-cell.big { flex:2; }
.op-blueprint .bp-cell:last-child { border-right:0; }
.op-blueprint .bp-cell span { display:block; font-size:5.6pt; letter-spacing:1.4pt; text-transform:uppercase; color:#6b5840; margin-bottom:1mm; }
.op-blueprint .bp-cell b { font-size:8pt; color:var(--rk-deep); }

/* ticket (adjustments) */
.op-ticket { display:flex; align-items:stretch; border:.5mm solid var(--rk-accent); border-radius:2mm; overflow:hidden; }
.op-ticket .tk-main { flex:1; padding:5mm 6mm; }
.op-ticket .tk-eyebrow { font-size:7pt; letter-spacing:2.5pt; font-weight:800; text-transform:uppercase; color:var(--rk-accent); }
.op-ticket .tk-title { font-size:25pt; font-weight:900; color:var(--rk-deep); margin:2mm 0 3mm; letter-spacing:-.4pt; }
.op-ticket .tk-fields { display:flex; gap:8mm; }
.op-ticket .tk-fields span { display:block; font-size:6pt; letter-spacing:1.4pt; text-transform:uppercase; color:#8a7c63; }
.op-ticket .tk-fields b { font-size:10pt; color:#1a1410; }
.op-ticket .tk-perf { width:0; border-left:.6mm dashed var(--rk-accent); }
.op-ticket .tk-stub { width:34mm; flex-shrink:0; background:var(--rk-soft); padding:5mm 4mm; display:flex; flex-direction:column;
    align-items:center; justify-content:center; gap:3mm; }
.op-ticket .tk-barcode { width:26mm; height:11mm;
    background:repeating-linear-gradient(90deg,#1a1410 0,#1a1410 .5mm,transparent .5mm,transparent 1.1mm,#1a1410 1.1mm,#1a1410 1.9mm,transparent 1.9mm,transparent 2.6mm); }
.op-ticket .tk-stub-l { font-family:'Courier New',monospace; font-size:6.6pt; letter-spacing:1.4pt; color:var(--rk-deep); }

/* certificate (YTD) */
.op-certificate { padding:2mm; }
.op-certificate .ce-frame { border:1.1mm double var(--rk-accent); padding:7mm 8mm 8mm; text-align:center; position:relative;
    box-shadow:inset 0 0 0 .4mm var(--rk-soft); }
.op-certificate .ce-eyebrow { font-family:'Helvetica',sans-serif; font-size:7pt; letter-spacing:3.5pt; text-transform:uppercase; color:var(--rk-accent); font-weight:800; }
.op-certificate .ce-pre { font-style:italic; font-size:9.5pt; color:#8a7c63; margin-top:4mm; }
.op-certificate .ce-title { font-size:30pt; font-weight:bold; color:var(--rk-deep); margin:1.5mm 0 0; letter-spacing:-.3pt; }
.op-certificate .ce-sub { font-style:italic; font-size:11pt; color:#5b4a30; margin-top:2mm; }
.op-certificate .ce-meta { font-family:'Helvetica',sans-serif; font-size:7.5pt; letter-spacing:.6pt; color:#8a7c63; margin-top:4mm; }
.op-certificate .ce-ribbon { width:11mm; height:11mm; border-radius:50%; background:var(--rk-accent); color:#fff; margin:5mm auto 0;
    display:flex; align-items:center; justify-content:center; font-size:9pt; box-shadow:0 0 0 1.2mm var(--rk-soft); }
"""
