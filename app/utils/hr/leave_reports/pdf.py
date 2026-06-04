"""PDF exporter for HR Leave Reports — six bespoke, per-report designs.

Each report has its OWN cover identity (not a shared template), all distinct
from the Attendance reports' motifs:

    leave_register     → cv-ledger     : ruled accountant's ledger / register
    department_leaves  → cv-board      : dark sports-style standings scoreboard
    balance_report     → cv-gauge      : cockpit instrument panel with ring gauge
    liability_report   → cv-statement  : formal financial statement of exposure
    comp_off_report    → cv-ticket     : perforated boarding-pass / ticket stub
    encashment_report  → cv-cheque     : bank cheque / payment voucher

A shared, accent-themed data table follows each cover. Covers are full-bleed
(named @page cover, margin 0); the table page carries margins + footer.

Imports are lazy — WeasyPrint hits libgobject at import time, so the bootstrap
runs only inside render_pdf().
"""
from __future__ import annotations

import io
from datetime import date as date_cls, datetime
from html import escape

from .data import report_meta
from .columns import columns_for


_MONEY_KEYS = {"basic_salary", "amount", "liability_amount"}
_NUM_KEYS = {
    "total_days", "days", "days_requested", "available_days", "quota",
    "opening", "accrued", "carry_forward_in", "used", "encashed",
    "adjustments", "available",
}
_INT_KEYS = {"requests", "employees_affected", "days_until_expiry"}
_PCT_KEYS = {"utilisation_pct"}
_RIGHT_KEYS = _MONEY_KEYS | _NUM_KEYS | _INT_KEYS | _PCT_KEYS

_STATUS_PALETTE = {
    "APPROVED":         ("#fef3c7", "#92400e"),
    "PENDING_MANAGER":  ("#fde68a", "#854d0e"),
    "PENDING_HR":       ("#fde68a", "#854d0e"),
    "PENDING":          ("#fde68a", "#854d0e"),
    "MANAGER_REJECTED": ("#ffe0d0", "#7c2d12"),
    "REJECTED":         ("#ffe0d0", "#7c2d12"),
    "CANCELLED":        ("#fff7ed", "#9a3412"),
    "WITHDRAWN":        ("#fff7ed", "#9a3412"),
    "DRAFT":            ("#fffbea", "#713f12"),
    "PAID":             ("#fde68a", "#78350f"),
}


# ════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ════════════════════════════════════════════════════════════════════════════

def _fmt(v, key: str | None = None) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, datetime):
        return v.strftime("%d %b %Y · %H:%M")
    if isinstance(v, date_cls):
        return v.strftime("%d %b %Y")
    if key in _MONEY_KEYS and isinstance(v, (int, float)):
        return f"₹ {v:,.2f}"
    if key in _PCT_KEYS and isinstance(v, (int, float)):
        return f"{v:g}%"
    if isinstance(v, float):
        return (f"{v:.2f}".rstrip("0").rstrip(".")) or "0"
    return escape(str(v))


def _status_pill(val: str) -> str:
    bg, fg = _STATUS_PALETTE.get(val, ("#fff7ed", "#7c2d12"))
    return f'<span class="pill" style="background:{bg};color:{fg}">{escape(str(val).replace("_"," ").title())}</span>'


def _kpi(k: str, v) -> str:
    if k.startswith("total_liability") or k.startswith("total_amount"):
        try:
            return f"₹ {float(v):,.0f}"
        except (TypeError, ValueError):
            return escape(str(v))
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".") or "0"
    if isinstance(v, int):
        return f"{v:,}"
    return escape(str(v))


def _money(v) -> str:
    try:
        return f"₹ {float(v or 0):,.0f}"
    except (TypeError, ValueError):
        return "₹ 0"


def _inr_words(num) -> str:
    """Indian-system rupees-in-words (crore/lakh/thousand)."""
    n = int(round(float(num or 0)))
    if n == 0:
        return "Rupees Zero Only"
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
            "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
            "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
            "Eighty", "Ninety"]

    def two(x):
        if x < 20:
            return ones[x]
        return (tens[x // 10] + (" " + ones[x % 10] if x % 10 else "")).strip()

    def three(x):  # 0..999
        h = x // 100
        r = x % 100
        out = ""
        if h:
            out = ones[h] + " Hundred"
            if r:
                out += " " + two(r)
        else:
            out = two(r)
        return out

    parts = []
    crore = n // 10_000_000
    n %= 10_000_000
    lakh = n // 100_000
    n %= 100_000
    thousand = n // 1000
    n %= 1000
    hundred = n
    if crore:
        parts.append(three(crore) + " Crore")
    if lakh:
        parts.append(two(lakh) + " Lakh")
    if thousand:
        parts.append(two(thousand) + " Thousand")
    if hundred:
        parts.append(three(hundred))
    return "Rupees " + " ".join(parts).strip() + " Only"


def _bar_series(rows, cat_key, val_key, top_n=8):
    agg: dict[str, float] = {}
    for r in rows:
        cat = r.get(cat_key) or "—"
        try:
            agg[str(cat)] = agg.get(str(cat), 0.0) + float(r.get(val_key) or 0)
        except (TypeError, ValueError):
            continue
    items = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return items


# ════════════════════════════════════════════════════════════════════════════
# Shared data table (themed by accent)
# ════════════════════════════════════════════════════════════════════════════

def _totals_row(cols, rows) -> str:
    if not rows:
        return ""
    sumable = _MONEY_KEYS | _NUM_KEYS | _INT_KEYS
    sums = {key: sum(float(r.get(key) or 0) for r in rows)
            for _l, key, _w in cols if key in sumable}
    if not sums:
        return ""
    cells, first = [], True
    for _label, key, _w in cols:
        if first:
            cells.append('<td class="tot-lbl">TOTAL</td>')
            first = False
        elif key in sums:
            cells.append(f'<td class="tot-num">{_fmt(sums[key], key)}</td>')
        else:
            cells.append("<td></td>")
    return f'<tr class="totals">{"".join(cells)}</tr>'


def _data_table(report_key: str, rows: list[dict], theme: dict) -> str:
    cols = columns_for(report_key)
    head = "".join(
        f'<th class="{"num" if key in _RIGHT_KEYS else ""}">{escape(label)}</th>'
        for label, key, _w in cols
    )
    body = []
    for i, row in enumerate(rows):
        cells = []
        for label, key, _w in cols:
            val = row.get(key)
            if key == "status":
                cells.append(f"<td>{_status_pill(val) if val else '—'}</td>")
            else:
                cls = ' class="num"' if key in _RIGHT_KEYS else ""
                cells.append(f"<td{cls}>{_fmt(val, key)}</td>")
        body.append(f'<tr{" class=zebra" if i % 2 else ""}>{"".join(cells)}</tr>')
    body_html = "\n".join(body) if body else (
        f"<tr><td colspan='{len(cols)}' class='empty'>No rows in this window</td></tr>")
    return (
        '<section class="sheet">'
        f'<div class="sheet-head"><span class="sh-name">{escape(theme["name"])}</span>'
        f'<span class="sh-count">· {len(rows):,} rows · ledger detail</span></div>'
        f'<table><thead><tr>{head}</tr></thead><tbody>{body_html}{_totals_row(cols, rows)}</tbody></table>'
        '</section>'
    )


# ════════════════════════════════════════════════════════════════════════════
# Cover renderers — one bespoke design per report
# ════════════════════════════════════════════════════════════════════════════

def _kpi_items(summary, limit=6):
    return list(summary.items())[:limit]


def _cover_ledger(theme, summary, period, rows):
    """leave_register — ruled accountant's ledger / register book."""
    entries = ""
    for k, v in _kpi_items(summary):
        entries += (
            '<div class="le"><span class="le-k">' + escape(k.replace("_", " ").title()) + '</span>'
            '<span class="le-dots"></span>'
            '<span class="le-v">' + _kpi(k, v) + '</span></div>'
        )
    return f"""
<section class="cover cv-ledger">
  <div class="lg-rules"></div>
  <div class="lg-margin"></div>
  <div class="lg-body">
    <div class="lg-brand">FOURRECK&nbsp;HR&nbsp;·&nbsp;LEAVE&nbsp;LEDGER&nbsp;Nº&nbsp;03</div>
    <h1 class="lg-title">Leave&nbsp;Register</h1>
    <div class="lg-doublerule"></div>
    <div class="lg-tag">{escape(theme['tagline'])} — every request, journalled.</div>
    <div class="lg-entries">{entries}</div>
    <div class="lg-foot">
      <span><b>FOLIO PERIOD</b>&nbsp; {period['from']:%d %b %Y} — {period['to']:%d %b %Y}</span>
      <span><b>POSTED</b>&nbsp; {datetime.now():%d %b %Y · %H:%M}</span>
      <span><b>ENTRIES</b>&nbsp; {len(rows):,}</span>
    </div>
  </div>
  <div class="lg-seal"><span>{escape(str(theme.get('icon') or 'L'))}</span><small>LEAVE · REGISTER</small></div>
</section>"""


def _cover_board(theme, summary, period, rows):
    """department_leaves — dark sports-style standings scoreboard (data-driven)."""
    standings = _bar_series(rows, "department", "days", top_n=8)
    top = max((v for _, v in standings), default=1) or 1
    rows_html = ""
    for i, (dept, days) in enumerate(standings, 1):
        pct = max(4, days / top * 100)
        rows_html += (
            '<div class="bd-row">'
            f'<span class="bd-rank">{i:02d}</span>'
            f'<span class="bd-name">{escape(dept)}</span>'
            f'<span class="bd-track"><span class="bd-fill" style="width:{pct:.1f}%"></span></span>'
            f'<span class="bd-val">{days:g}</span></div>'
        )
    if not standings:
        rows_html = '<div class="bd-empty">No approved leave in this window</div>'
    chips = ""
    for k, v in _kpi_items(summary, 4):
        chips += (f'<div class="bd-chip"><span class="bd-cv">{_kpi(k, v)}</span>'
                  f'<span class="bd-cl">{escape(k.replace("_"," ").title())}</span></div>')
    return f"""
<section class="cover cv-board">
  <div class="bd-glow"></div>
  <div class="bd-top">
    <span class="bd-eye">FOURRECK HR · LEAGUE TABLE</span>
    <h1 class="bd-title">Department&nbsp;Leaves</h1>
    <div class="bd-tag">{escape(theme['tagline'])}</div>
  </div>
  <div class="bd-chips">{chips}</div>
  <div class="bd-board">
    <div class="bd-board-head">STANDINGS · APPROVED DAYS BY DEPARTMENT</div>
    {rows_html}
  </div>
  <div class="bd-foot">SEASON {period['from']:%d %b %Y} → {period['to']:%d %b %Y} &nbsp;·&nbsp; GENERATED {datetime.now():%d %b · %H:%M}</div>
</section>"""


def _cover_gauge(theme, summary, period, rows):
    """balance_report — cockpit instrument panel with a ring gauge."""
    accent = theme["accent"]
    soft = theme["accent_soft"]
    used = float(summary.get("total_used", 0) or 0)
    quota = float(summary.get("total_quota", 0) or 0)
    avail = float(summary.get("total_available", 0) or 0)
    util = (used / quota * 100) if quota > 0 else 0
    util = max(0, min(100, util))
    circ = 2 * 3.14159 * 52
    off = circ - (util / 100) * circ
    readouts = ""
    for k, v in [("total_available", avail), ("total_used", used), ("total_quota", quota),
                 ("employees", summary.get("employees", 0))]:
        readouts += (f'<div class="gg-read"><span class="gg-rl">{k.replace("total_","").replace("_"," ").title()}</span>'
                     f'<span class="gg-rv">{_kpi(k, v)}</span></div>')
    return f"""
<section class="cover cv-gauge">
  <div class="gg-head">
    <span class="gg-eye">FOURRECK HR · BALANCE INSTRUMENTATION</span>
    <h1 class="gg-title">Balance&nbsp;Snapshot</h1>
    <div class="gg-tag">{escape(theme['tagline'])}</div>
  </div>
  <div class="gg-panel">
    <div class="gg-dial">
      <svg viewBox="0 0 130 130">
        <circle cx="65" cy="65" r="52" fill="none" stroke="{soft}" stroke-width="12"/>
        <circle cx="65" cy="65" r="52" fill="none" stroke="{accent}" stroke-width="12"
                stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{off:.1f}"
                transform="rotate(-90 65 65)"/>
      </svg>
      <div class="gg-dial-mid"><span class="gg-pct">{util:.0f}%</span><span class="gg-pl">UTILISED</span></div>
    </div>
    <div class="gg-reads">{readouts}</div>
  </div>
  <div class="gg-foot"><b>FY SNAPSHOT</b> {period['from']:%d %b %Y} → {period['to']:%d %b %Y} &nbsp;·&nbsp; <b>GENERATED</b> {datetime.now():%d %b %Y · %H:%M} &nbsp;·&nbsp; <b>ROWS</b> {len(rows):,}</div>
</section>"""


def _cover_statement(theme, summary, period, rows):
    """liability_report — formal financial statement of exposure."""
    total = float(summary.get("total_liability", 0) or 0)
    lines = ""
    for k, v in _kpi_items(summary):
        is_money = k.startswith("total_liability")
        lines += (f'<tr><td class="st-k">{escape(k.replace("_"," ").title())}</td>'
                  f'<td class="st-v">{(_money(v) if is_money else _kpi(k, v))}</td></tr>')
    return f"""
<section class="cover cv-statement">
  <div class="st-letterhead">
    <div class="st-co">FOURRECK TECHNOLOGIES PVT. LTD.</div>
    <div class="st-doc">STATEMENT OF LEAVE LIABILITY</div>
  </div>
  <div class="st-hr"></div>
  <h1 class="st-title">Liability&nbsp;Report</h1>
  <div class="st-tag">{escape(theme['subtitle'])}</div>
  <div class="st-asat">AS AT {period['to']:%d %B %Y}</div>

  <div class="st-grid">
    <div class="st-hero">
      <div class="st-hero-l">TOTAL PAYROLL EXPOSURE</div>
      <div class="st-hero-v">{_money(total)}</div>
      <div class="st-hero-w">{_inr_words(total)}</div>
    </div>
    <table class="st-ledger"><tbody>{lines}</tbody></table>
  </div>
  <div class="st-stamp">CONFIDENTIAL · PAYROLL</div>
  <div class="st-foot">Period {period['from']:%d %b %Y} → {period['to']:%d %b %Y} · Generated {datetime.now():%d %b %Y · %H:%M} · {len(rows):,} accounts</div>
</section>"""


def _cover_ticket(theme, summary, period, rows):
    """comp_off_report — perforated boarding-pass / ticket stub."""
    fields = ""
    for k, v in _kpi_items(summary, 4):
        fields += (f'<div class="tk-field"><span class="tk-fl">{escape(k.replace("_"," ").upper())}</span>'
                   f'<span class="tk-fv">{_kpi(k, v)}</span></div>')
    total_days = _kpi("total_days", summary.get("total_days", 0))
    return f"""
<section class="cover cv-ticket">
  <div class="tk">
    <div class="tk-main">
      <div class="tk-head">
        <span class="tk-eye">FOURRECK HR · COMP-OFF PASS</span>
        <span class="tk-cls">CLASS · TIME-OFF</span>
      </div>
      <h1 class="tk-title">Comp-Off&nbsp;Ledger</h1>
      <div class="tk-tag">{escape(theme['tagline'])}</div>
      <div class="tk-fields">{fields}</div>
      <div class="tk-barcode"></div>
      <div class="tk-route">EARNED&nbsp;&nbsp;✈&nbsp;&nbsp;CREDITED&nbsp;&nbsp;✈&nbsp;&nbsp;EXPIRES</div>
    </div>
    <div class="tk-perf"></div>
    <div class="tk-stub">
      <div class="tk-stub-eye">STUB</div>
      <div class="tk-stub-num">{total_days}</div>
      <div class="tk-stub-lbl">COMP-OFF DAYS</div>
      <div class="tk-stub-meta">{period['from']:%d %b} — {period['to']:%d %b %Y}</div>
      <div class="tk-stub-barcode"></div>
    </div>
  </div>
  <div class="tk-foot">Generated {datetime.now():%d %b %Y · %H:%M} · {len(rows):,} grants in window</div>
</section>"""


def _cover_cheque(theme, summary, period, rows):
    """encashment_report — bank cheque / payment voucher."""
    total = float(summary.get("total_amount", 0) or 0)
    emps = summary.get("employees", 0)
    return f"""
<section class="cover cv-cheque">
  <div class="cq">
    <div class="cq-watermark">VOUCHER</div>
    <div class="cq-top">
      <div class="cq-bank">
        <div class="cq-bank-name">FOURRECK PAYROLL BANK</div>
        <div class="cq-bank-sub">LEAVE ENCASHMENT VOUCHER · NON-NEGOTIABLE</div>
      </div>
      <div class="cq-date">{datetime.now():%d · %m · %Y}</div>
    </div>
    <div class="cq-pay">
      <span class="cq-pay-l">PAY</span>
      <span class="cq-pay-v">{emps} employee(s) · against accrued leave</span>
    </div>
    <div class="cq-words">
      <span class="cq-words-l">RUPEES</span>
      <span class="cq-words-v">{_inr_words(total)}</span>
    </div>
    <div class="cq-amtbox"><span class="cq-amt-cur">₹</span>{total:,.0f}</div>
    <div class="cq-bottom">
      <div class="cq-sign"><div class="cq-sign-line"></div>Authorised Signatory · HR Payroll</div>
      <div class="cq-stub">
        <span><b>Days</b> {_kpi('total_days', summary.get('total_days', 0))}</span>
        <span><b>Paid</b> {summary.get('paid', 0)}</span>
        <span><b>Pending</b> {summary.get('pending', 0)}</span>
      </div>
    </div>
    <div class="cq-micr">⑆ 04062026 ⑆ FOURRECK ⑇ LEAVE-ENCASH ⑈ {period['from']:%Y%m%d}-{period['to']:%Y%m%d} ⑆</div>
  </div>
  <div class="cq-foot">Period {period['from']:%d %b %Y} → {period['to']:%d %b %Y} · Generated {datetime.now():%d %b %Y · %H:%M} · {len(rows):,} vouchers</div>
</section>"""


COVER_RENDERERS = {
    "register": _cover_ledger,
    "team": _cover_board,
    "ledger": _cover_gauge,
    "finance": _cover_statement,
    "ticker": _cover_ticket,
    "voucher": _cover_cheque,
}


# ════════════════════════════════════════════════════════════════════════════
# CSS (tokenised — __AC__ accent, __DP__ deep, __SF__ soft)
# ════════════════════════════════════════════════════════════════════════════

_CSS = r"""
@page cover { size: A3 landscape; margin: 0; }
@page data  {
  size: A3 landscape; margin: 16mm 16mm 16mm 16mm;
  @top-right { content: "FOURRECK HRMS · LEAVE · __NAME__"; color: __DP__; font-size: 8px; font-weight: 700; letter-spacing: .12em; font-family: 'Inter',sans-serif; }
  @bottom-left { content: "__FROM__ → __TO__ · generated __GEN__"; color: #b08a4a; font-size: 8.5px; font-family: 'Inter',sans-serif; }
  @bottom-right { content: "Page " counter(page) " / " counter(pages); color: #b08a4a; font-size: 8.5px; font-family: 'Inter',sans-serif; }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: 'Inter','Helvetica',sans-serif; color: #2a1a0a; }
.cover { page: cover; page-break-after: always; position: relative; width: 420mm; height: 296mm; overflow: hidden; }
.sheet { page: data; }

/* shared table */
.sheet-head { display: flex; align-items: baseline; gap: 8px; margin: 0 0 4mm; }
.sheet-head .sh-name { font-size: 13px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: __DP__; }
.sheet-head .sh-count { font-size: 10px; color: #b08a4a; }
table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 8.6px; border: 1px solid __AC__55; border-radius: 3mm; overflow: hidden; }
thead th { background: linear-gradient(180deg, __DP__, #160d06); color: #fff7e6; padding: 7px 9px; text-align: left; font-weight: 700; letter-spacing: .04em; }
thead th.num { text-align: right; }
tbody td { padding: 5.5px 9px; border-bottom: 1px solid #f0e3cf; color: #2a1a0a; vertical-align: top; }
tbody td.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr.zebra td { background: __SF__66; }
tr { page-break-inside: avoid; }
.empty { text-align: center; color: #b08a4a; padding: 28px 0 !important; font-style: italic; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 8px; font-weight: 800; letter-spacing: .04em; }
tr.totals td { background: __DP__; color: #fff7e6; font-weight: 800; padding: 7px 9px; }
tr.totals td.tot-lbl { letter-spacing: .18em; font-size: 8px; }
tr.totals td.tot-num { text-align: right; font-variant-numeric: tabular-nums; }

/* ───────── 1 · LEDGER (register) ───────── */
.cv-ledger { background: #fdf7e8; }
.lg-rules { position: absolute; inset: 0; background: repeating-linear-gradient(180deg, transparent 0 13.6mm, __AC__22 13.6mm 13.8mm); }
.lg-margin { position: absolute; top: 0; bottom: 0; left: 34mm; width: 0.6mm; background: #c0392b; opacity: .55; }
.lg-margin::after { content: ""; position: absolute; top: 0; bottom: 0; left: 1.6mm; width: 0.3mm; background: #c0392b; opacity: .4; }
.lg-body { position: relative; padding: 22mm 30mm 0 44mm; }
.lg-brand { font-family: 'Courier New', monospace; font-size: 10px; letter-spacing: .26em; color: __DP__; }
.lg-title { font-family: 'Georgia','Times New Roman',serif; font-size: 74px; font-weight: 800; color: #1f1206; margin: 6mm 0 0; letter-spacing: -.01em; }
.lg-doublerule { height: 0; border-top: 1.4mm double __DP__; width: 150mm; margin: 4mm 0 3mm; }
.lg-tag { font-style: italic; font-size: 15px; color: __DP__; }
.lg-entries { margin: 14mm 0 0; width: 230mm; }
.le { display: flex; align-items: baseline; gap: 4mm; padding: 2.4mm 0; font-size: 13px; }
.le-k { font-weight: 700; color: #3a2410; letter-spacing: .02em; white-space: nowrap; }
.le-dots { flex: 1; border-bottom: 0.4mm dotted #b9a878; transform: translateY(-2px); }
.le-v { font-family: 'Courier New', monospace; font-weight: 800; font-size: 17px; color: __DP__; }
.lg-foot { position: absolute; left: 44mm; bottom: 16mm; font-family: 'Courier New', monospace; font-size: 9.5px; color: #6b4a22; }
.lg-foot span { margin-right: 14mm; }
.lg-foot b { color: __DP__; }
.lg-seal { position: absolute; right: 34mm; top: 30mm; width: 56mm; height: 56mm; border: 1.4mm solid __AC__; border-radius: 50%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: __DP__; transform: rotate(-11deg); opacity: .82; background: radial-gradient(circle, __SF__ 0%, transparent 72%); }
.lg-seal span { font-family: 'Georgia',serif; font-size: 40px; font-weight: 800; line-height: 1; }
.lg-seal small { font-size: 8px; letter-spacing: .24em; margin-top: 2mm; }
.lg-seal::before { content: ""; position: absolute; inset: 3mm; border: 0.4mm dashed __AC__; border-radius: 50%; }

/* ───────── 2 · BOARD (department) ───────── */
.cv-board { background: radial-gradient(60% 90% at 80% 0%, __AC__33, transparent 60%), linear-gradient(150deg, #160d06 0%, #241204 60%, #110a05 100%); color: #fff3df; padding: 22mm 28mm; }
.bd-glow { position: absolute; top: -40mm; right: -20mm; width: 200mm; height: 200mm; background: radial-gradient(circle, __AC__33, transparent 65%); filter: blur(10mm); }
.bd-top { position: relative; }
.bd-eye { display: inline-block; padding: 4px 12px; border: 1px solid __AC__66; border-radius: 999px; color: __AC__; font-size: 9px; font-weight: 800; letter-spacing: .24em; }
.bd-title { font-size: 64px; font-weight: 900; letter-spacing: -.03em; margin: 6mm 0 1mm; color: #fff7ea; text-transform: uppercase; }
.bd-tag { font-style: italic; color: __SF__; font-size: 14px; opacity: .85; }
.bd-chips { display: flex; gap: 8mm; margin: 8mm 0 6mm; }
.bd-chip { border-left: 1mm solid __AC__; padding-left: 4mm; }
.bd-cv { display: block; font-size: 30px; font-weight: 800; color: __AC__; font-variant-numeric: tabular-nums; }
.bd-cl { font-size: 8.5px; letter-spacing: .16em; text-transform: uppercase; color: #c79a54; }
.bd-board { margin-top: 4mm; border: 1px solid __AC__44; border-radius: 4mm; padding: 6mm 8mm; background: rgba(255,250,235,.04); }
.bd-board-head { font-size: 9px; letter-spacing: .22em; color: #c79a54; margin-bottom: 4mm; }
.bd-row { display: flex; align-items: center; gap: 4mm; padding: 2.6mm 0; border-bottom: 0.3mm solid rgba(255,255,255,.06); }
.bd-rank { font-family: 'Courier New',monospace; font-size: 14px; font-weight: 800; color: __AC__; width: 12mm; }
.bd-name { width: 70mm; font-size: 13px; font-weight: 700; color: #fff3df; }
.bd-track { flex: 1; height: 6mm; background: rgba(255,255,255,.07); border-radius: 3mm; overflow: hidden; }
.bd-fill { display: block; height: 100%; background: linear-gradient(90deg, __AC__, #fde047); border-radius: 3mm; }
.bd-val { width: 22mm; text-align: right; font-family: 'Courier New',monospace; font-size: 15px; font-weight: 800; color: #fff7ea; }
.bd-empty { color: #c79a54; font-style: italic; padding: 8mm 0; text-align: center; }
.bd-foot { position: absolute; left: 28mm; bottom: 14mm; font-size: 9px; letter-spacing: .12em; color: #c79a54; }

/* ───────── 3 · GAUGE (balance) ───────── */
.cv-gauge { background: linear-gradient(180deg, #fffdf6, #fff4e0); padding: 24mm 30mm; }
.cv-gauge::before { content: ""; position: absolute; inset: 10mm; border: 0.5mm solid __AC__44; border-radius: 6mm; }
.gg-head { position: relative; }
.gg-eye { display: inline-block; padding: 4px 12px; border-radius: 999px; background: __SF__; border: 1px solid __AC__; color: __DP__; font-size: 9px; font-weight: 800; letter-spacing: .2em; }
.gg-title { font-size: 60px; font-weight: 800; color: __DP__; margin: 6mm 0 1mm; letter-spacing: -.02em; }
.gg-tag { font-style: italic; color: #6b4a22; font-size: 14px; }
.gg-panel { position: relative; display: flex; align-items: center; gap: 24mm; margin-top: 14mm; }
.gg-dial { position: relative; width: 110mm; height: 110mm; flex: 0 0 110mm; }
.gg-dial svg { width: 110mm; height: 110mm; }
.gg-dial-mid { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.gg-pct { font-size: 52px; font-weight: 800; color: __DP__; letter-spacing: -.03em; }
.gg-pl { font-size: 10px; letter-spacing: .24em; color: #b08a4a; }
.gg-reads { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm 10mm; flex: 1; }
.gg-read { border-left: 1mm solid __AC__; padding-left: 5mm; }
.gg-rl { display: block; font-size: 9px; letter-spacing: .16em; text-transform: uppercase; color: #8a6a32; }
.gg-rv { font-size: 34px; font-weight: 800; color: __DP__; font-variant-numeric: tabular-nums; }
.gg-foot { position: absolute; left: 30mm; bottom: 16mm; font-size: 9.5px; color: #8a6a32; }
.gg-foot b { color: __DP__; }

/* ───────── 4 · STATEMENT (liability) ───────── */
.cv-statement { background: #fffdf7; padding: 20mm 28mm; }
.st-letterhead { display: flex; justify-content: space-between; align-items: baseline; }
.st-co { font-size: 13px; font-weight: 800; letter-spacing: .04em; color: #1f1206; }
.st-doc { font-size: 10px; letter-spacing: .26em; color: __DP__; }
.st-hr { height: 1.2mm; background: __DP__; margin: 3mm 0 0; }
.st-title { font-family: 'Georgia',serif; font-size: 58px; font-weight: 800; color: __DP__; margin: 8mm 0 1mm; }
.st-tag { font-size: 12px; color: #6b4a22; }
.st-asat { margin-top: 2mm; font-family: 'Courier New',monospace; font-size: 11px; letter-spacing: .14em; color: #8a6a32; }
.st-grid { display: flex; gap: 16mm; margin-top: 12mm; align-items: stretch; }
.st-hero { flex: 1.1; border: 0.6mm solid __AC__; border-radius: 4mm; padding: 10mm; background: linear-gradient(160deg, __SF__, #fffdf7); }
.st-hero-l { font-size: 10px; letter-spacing: .2em; color: #8a6a32; }
.st-hero-v { font-size: 56px; font-weight: 800; color: __DP__; letter-spacing: -.02em; margin: 3mm 0; }
.st-hero-w { font-size: 12px; font-style: italic; color: __DP__; }
.st-ledger { flex: 1; border-collapse: collapse; align-self: center; }
.st-ledger td { padding: 3mm 2mm; border-bottom: 0.4mm solid #e8d8ba; font-size: 13px; }
.st-ledger .st-k { color: #5a3a18; }
.st-ledger .st-v { text-align: right; font-family: 'Courier New',monospace; font-weight: 800; color: __DP__; }
.st-ledger tr:last-child td { border-bottom: 1.6mm double __DP__; }
.st-stamp { position: absolute; right: 36mm; top: 64mm; border: 1mm solid #c0392b; color: #c0392b; padding: 2mm 5mm; font-size: 13px; font-weight: 800; letter-spacing: .2em; transform: rotate(-9deg); opacity: .7; border-radius: 2mm; }
.st-foot { position: absolute; left: 28mm; bottom: 14mm; font-size: 9.5px; color: #8a6a32; }

/* ───────── 5 · TICKET (comp-off) ───────── */
.cv-ticket { background: repeating-linear-gradient(45deg, __SF__, __SF__ 6mm, #fff7ea 6mm, #fff7ea 12mm); display: flex; align-items: center; justify-content: center; }
.tk { display: flex; width: 320mm; height: 150mm; background: #fffdf7; border-radius: 6mm; overflow: hidden; box-shadow: 0 8mm 24mm rgba(120,53,15,.18); }
.tk-main { flex: 1; padding: 16mm 18mm; position: relative; background: linear-gradient(135deg, #fffdf7, __SF__); }
.tk-head { display: flex; justify-content: space-between; align-items: center; }
.tk-eye { font-size: 9px; letter-spacing: .22em; color: __DP__; font-weight: 800; }
.tk-cls { font-family: 'Courier New',monospace; font-size: 9px; letter-spacing: .1em; color: #fff; background: __DP__; padding: 1.5mm 3mm; border-radius: 1mm; }
.tk-title { font-size: 52px; font-weight: 800; color: __DP__; margin: 8mm 0 1mm; letter-spacing: -.02em; }
.tk-tag { font-style: italic; color: #6b4a22; font-size: 13px; }
.tk-fields { display: flex; gap: 12mm; margin: 12mm 0 0; }
.tk-field { } .tk-fl { display: block; font-size: 8px; letter-spacing: .18em; color: #a07a3a; }
.tk-fv { font-size: 26px; font-weight: 800; color: __DP__; font-variant-numeric: tabular-nums; }
.tk-route { margin-top: 10mm; font-family: 'Courier New',monospace; font-size: 11px; letter-spacing: .12em; color: __AC__; font-weight: 800; }
.tk-barcode { position: absolute; right: 18mm; top: 16mm; width: 40mm; height: 12mm; background: repeating-linear-gradient(90deg, #1f1206 0 0.7mm, transparent 0.7mm 1.6mm); opacity: .8; }
.tk-perf { width: 0; border-left: 0.6mm dashed #c8a96a; position: relative; background: radial-gradient(circle at center, transparent 2.2mm, #fffdf7 2.3mm) ; }
.tk-stub { width: 86mm; background: linear-gradient(160deg, __DP__, #160d06); color: #fff3df; padding: 16mm 12mm; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; position: relative; }
.tk-stub-eye { font-size: 9px; letter-spacing: .26em; color: __AC__; }
.tk-stub-num { font-size: 66px; font-weight: 900; color: #fff7ea; line-height: 1; margin: 3mm 0; }
.tk-stub-lbl { font-size: 9px; letter-spacing: .18em; color: #c79a54; }
.tk-stub-meta { margin-top: 6mm; font-family: 'Courier New',monospace; font-size: 10px; color: __SF__; }
.tk-stub-barcode { margin-top: 8mm; width: 56mm; height: 9mm; background: repeating-linear-gradient(90deg, #fff3df 0 0.7mm, transparent 0.7mm 1.7mm); opacity: .7; }
.tk-foot { position: absolute; bottom: 12mm; left: 0; right: 0; text-align: center; font-size: 9px; color: #8a6a32; }

/* ───────── 6 · CHEQUE (encashment) ───────── */
.cv-cheque { background: linear-gradient(160deg, #efe6d2, #e6d8bc); display: flex; align-items: center; justify-content: center; }
.cq { position: relative; width: 330mm; height: 150mm; background: linear-gradient(135deg, #fffef9 0%, __SF__ 100%); border: 0.6mm solid __AC__; border-radius: 3mm; padding: 14mm 16mm; box-shadow: 0 6mm 20mm rgba(120,53,15,.22); overflow: hidden; }
.cq::before { content: ""; position: absolute; inset: 0; background: repeating-linear-gradient(45deg, __AC__08 0 4mm, transparent 4mm 8mm); pointer-events: none; }
.cq-watermark { position: absolute; right: -6mm; bottom: -14mm; font-size: 150px; font-weight: 900; color: __AC__18; letter-spacing: -.04em; }
.cq-top { display: flex; justify-content: space-between; align-items: flex-start; position: relative; }
.cq-bank-name { font-size: 20px; font-weight: 800; color: __DP__; letter-spacing: .02em; }
.cq-bank-sub { font-size: 9px; letter-spacing: .14em; color: #8a6a32; margin-top: 1mm; }
.cq-date { font-family: 'Courier New',monospace; font-size: 14px; letter-spacing: .14em; color: __DP__; border: 0.5mm solid __AC__; padding: 2mm 4mm; border-radius: 1.5mm; }
.cq-pay { display: flex; align-items: baseline; gap: 6mm; margin-top: 14mm; position: relative; }
.cq-pay-l { font-size: 11px; letter-spacing: .2em; color: #8a6a32; }
.cq-pay-v { flex: 1; border-bottom: 0.4mm solid #c8a96a; font-size: 18px; font-weight: 700; color: #2a1a0a; padding-bottom: 1mm; }
.cq-words { display: flex; align-items: baseline; gap: 6mm; margin-top: 8mm; position: relative; }
.cq-words-l { font-size: 11px; letter-spacing: .2em; color: #8a6a32; }
.cq-words-v { flex: 1; border-bottom: 0.4mm solid #c8a96a; font-size: 15px; font-style: italic; color: __DP__; padding-bottom: 1mm; }
.cq-amtbox { position: absolute; right: 16mm; top: 58mm; border: 0.8mm solid __DP__; border-radius: 2mm; padding: 4mm 8mm; font-size: 40px; font-weight: 800; color: __DP__; background: #fffef9; font-variant-numeric: tabular-nums; }
.cq-amt-cur { font-size: 26px; margin-right: 2mm; }
.cq-bottom { display: flex; justify-content: space-between; align-items: flex-end; margin-top: 22mm; position: relative; }
.cq-sign { font-size: 10px; color: #6b4a22; text-align: center; }
.cq-sign-line { width: 60mm; border-top: 0.5mm solid #2a1a0a; margin-bottom: 1.5mm; }
.cq-stub { display: flex; gap: 8mm; font-size: 11px; color: #6b4a22; }
.cq-stub b { color: __DP__; }
.cq-micr { position: absolute; left: 16mm; right: 16mm; bottom: 8mm; font-family: 'Courier New',monospace; font-size: 12px; letter-spacing: .08em; color: #3a2410; border-top: 0.4mm solid #c8a96a; padding-top: 2mm; }
.cq-foot { position: absolute; bottom: 7mm; left: 0; right: 0; text-align: center; font-size: 9px; color: #7c5a2a; }
"""


def _build_html(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> str:
    theme = report_meta(report_key)
    period = meta.get("period") or meta
    from_d = period["from"]
    to_d = period["to"]

    css = (
        _CSS.replace("__AC__", theme["accent"])
        .replace("__DP__", theme["accent_deep"])
        .replace("__SF__", theme["accent_soft"])
        .replace("__NAME__", escape(theme["name"]))
        .replace("__FROM__", from_d.isoformat())
        .replace("__TO__", to_d.isoformat())
        .replace("__GEN__", datetime.now().strftime("%d %b %Y · %H:%M"))
    )

    renderer = COVER_RENDERERS.get(theme.get("motif"), _cover_ledger)
    cover = renderer(theme, summary, period, shaped_rows)
    table = _data_table(report_key, shaped_rows, theme)

    return (
        f'<!doctype html><html><head><meta charset="utf-8"/>'
        f'<title>Fourreck Leave · {escape(theme["name"])}</title>'
        f"<style>{css}</style></head><body>{cover}{table}</body></html>"
    )


def render_pdf(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> bytes:
    # Lazy import — WeasyPrint hits libgobject at import time. Bootstrap PATH
    # so this works on Windows dev machines too.
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML

    html = _build_html(report_key, shaped_rows, summary, meta)
    buf = io.BytesIO()
    HTML(string=html).write_pdf(buf)
    return buf.getvalue()
