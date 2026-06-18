"""HR Training & Development — WeasyPrint PDF rendering.

A distinct "Learning Observatory" editorial identity: a full-bleed accent
**masthead slab** (issue stamp, oversized title, ghosted mega-numeral) over a
white **sheet** carrying a period strip, a framed KPI readout band and a
per-report **motif instrument** — eleven of them, one per report, so no two
PDFs look alike:

    ledger · funnel · dial · stars · radar · timeline · ratingarc · gauge ·
    pipeline · vault · grid

Below the cover, a body page led by a bold accent flag over a modern table
(accent spine on the lead column, status pills, traffic-light cells).

WeasyPrint is imported lazily after the GTK runtime is prepared (see CLAUDE.md).
"""
from __future__ import annotations

import html
import math
from datetime import date, datetime

from .data import report_meta, REPORT_KEYS, SELF_REPORT_KEYS

COMPANY = {"name": "Fourreck", "legal": "Fourreck Technologies", "web": "crm.fourreck.com"}
_ISSUE_NO = {k: f"{i + 1:02d}" for i, k in enumerate(REPORT_KEYS)}
# Self-service reports carry an "M#" issue stamp so they read as a personal series.
_ISSUE_NO.update({k: f"M{i + 1}" for i, k in enumerate(SELF_REPORT_KEYS)})


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _fmt(val, kind=None):
    if val is None or val == "":
        return "—"
    if kind == "pct":
        return f"{round(float(val), 1):g}%"
    if kind == "money":
        return f"₹{float(val):,.0f}"
    if kind == "bool":
        return "✓" if val else "—"
    if isinstance(val, bool):
        return "✓" if val else "—"
    if isinstance(val, date) and not isinstance(val, datetime):
        return val.strftime("%d %b %Y")
    return _esc(val)


# ══════════════════════════════ BODY TABLE ══════════════════════════════
_PILL_LABEL = {
    "COMPLETED": "Completed", "IN_PROGRESS": "In progress", "NOT_STARTED": "Not started",
    "FAILED": "Failed", "WAIVED": "Waived", "ACTIVE": "Active", "EXPIRING_SOON": "Expiring",
    "EXPIRED": "Expired", "REVOKED": "Revoked", "PENDING_RENEWAL": "Renew",
    "PENDING_APPROVAL": "Pending", "APPROVED": "Approved", "REJECTED": "Rejected",
    "RETURNED": "Returned", "CANCELLED": "Cancelled", "DRAFT": "Draft", "FULFILLED": "Fulfilled",
}


def _cell(row, c):
    v = row.get(c["key"])
    if c.get("pill"):
        tone = c["pill"].get(str(v), "neutral")
        lbl = _PILL_LABEL.get(str(v), _esc(str(v)).replace("_", " ").title() if v else "—")
        return f'<span class="pill pill-{tone}">{lbl}</span>', ""
    kind = c.get("fmt")
    klass = ""
    try:
        if c.get("danger_if") and v not in (None, "") and c["danger_if"](v):
            klass = "cell-danger"
        elif c.get("good_if") and v not in (None, "") and c["good_if"](v):
            klass = "cell-good"
    except Exception:
        pass
    return _fmt(v, kind), klass


def _table(report):
    cols = report["columns"]; rows = report["rows"]
    head = "".join(
        f'<th class="{"r" if c.get("align")=="right" else "c" if c.get("align")=="center" else ""}'
        f'{" lead" if i == 0 else ""}">{_esc(c["label"])}</th>' for i, c in enumerate(cols))
    body = []
    for i, row in enumerate(rows):
        cells = []
        for j, c in enumerate(cols):
            val, kl = _cell(row, c)
            al = "r" if c.get("align") == "right" else "c" if c.get("align") == "center" else ""
            cls = " ".join(x for x in (al, kl, ("lead" if j == 0 else "")) if x)
            cells.append(f'<td class="{cls}">{val}</td>')
        body.append(f'<tr class="{"zebra" if i % 2 else ""}">{"".join(cells)}</tr>')
    if not rows:
        body.append(f'<tr><td colspan="{len(cols)}" class="empty">No records for this period.</td></tr>')
    return f'<table class="dt"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


# ══════════════════════════════ SHARED PARTS ══════════════════════════════
def _readout(items, accent, deep):
    cells = []
    for it in items:
        label, value = it[0], it[1]
        tone = it[2] if len(it) > 2 else ""
        vc = {"ok": "#047857", "warn": "#c2410c", "danger": "#b91c1c"}.get(tone, deep)
        cells.append(f'<div class="ro-cell"><div class="ro-v" style="color:{vc}">{_esc(value)}</div>'
                     f'<div class="ro-l">{_esc(label)}</div></div>')
    return (f'<div class="readout" style="border-color:{accent}">'
            f'<span class="ro-edge" style="background:{accent}"></span>{"".join(cells)}</div>')


def _period_strip(period, accent, deep):
    f, t = period.get("from"), period.get("to")
    if not f or not t:
        return (f'<div class="period"><span class="p-tag" style="background:{accent}">SCOPE</span>'
                f'<span class="p-box"><b>RANGE</b>All time</span>'
                f'<span class="p-days" style="color:{deep}">complete history</span></div>')
    return (f'<div class="period"><span class="p-tag" style="background:{accent}">PERIOD</span>'
            f'<span class="p-box"><b>FROM</b>{f.strftime("%d %b %Y")}</span>'
            f'<span class="p-arr" style="color:{accent}">→</span>'
            f'<span class="p-box"><b>TO</b>{t.strftime("%d %b %Y")}</span>'
            f'<span class="p-days" style="color:{deep}">{(t - f).days + 1} days</span></div>')


def _gen_line():
    return (f'<div class="gen">Generated {datetime.now().strftime("%d %b %Y · %I:%M %p").lstrip("0")} '
            f'· {COMPANY["legal"]} · Confidential — internal use only</div>')


def _slab(meta, report, dark=False):
    a, d = meta["accent"], meta["accent_deep"]
    issue = _ISSUE_NO.get(meta["key"], "00")
    words = _esc(meta["name"]).split(" ")
    title = (" ".join(words[:-1]) + "<br>" + words[-1]) if len(words) > 1 else words[0]
    bg = ("linear-gradient(150deg,#0b1020 0%,#161226 55%,#241a08 100%)" if dark
          else f"linear-gradient(140deg,{a} 0%,{d} 100%)")
    return f"""
    <div class="slab{' slab-dark' if dark else ''}" style="background:{bg}">
      <span class="bignum">{issue}</span>
      <span class="spine">{COMPANY['name'].upper()} · LEARNING OBSERVATORY</span>
      <div class="slab-top">
        <div class="brand"><span class="crest" style="color:{d if not dark else '#1a1004'}">{meta['icon']}</span>
          <span class="brand-txt">TRAINING &amp; DEVELOPMENT</span></div>
        <div class="issue"><span class="iss-k">REPORT</span><span class="iss-n">// {issue}</span></div>
      </div>
      <div class="eyebrow">{_esc(report.get('eyebrow', meta['name']))}</div>
      <h1 class="display">{title}</h1>
      <p class="slab-sub">{_esc(report.get('subtitle', meta['tagline']))}</p>
    </div>"""


# ── reusable mini-instruments ──
def _gauge(pct, a, d, big, small):
    pct = max(0, min(100, float(pct or 0)))
    frac = pct / 100
    ang = math.pi - frac * math.pi
    x = 60 + 50 * math.cos(ang); y = 60 - 50 * math.sin(ang)
    return f"""<svg viewBox="0 0 120 78" width="190" height="124">
      <path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="{a}33" stroke-width="11" stroke-linecap="round"/>
      <path d="M10 60 A50 50 0 0 1 {x:.1f} {y:.1f}" fill="none" stroke="{a}" stroke-width="11" stroke-linecap="round"/>
      <text x="60" y="50" text-anchor="middle" font-size="22" font-weight="900" fill="{d}">{_esc(big)}</text>
      <text x="60" y="62" text-anchor="middle" font-size="6.5" letter-spacing="1" fill="{a}">{_esc(small)}</text>
    </svg>"""


def _bars(items, a, d):
    mx = max([abs(float(v)) for _, v in items] + [1])
    out = "".join(
        f'<div class="bar"><span class="bar-k">{_esc(k)}</span>'
        f'<span class="bar-t"><i style="width:{min(100, abs(float(v)) / mx * 100):.0f}%;background:linear-gradient(90deg,{a},{d})"></i></span>'
        f'<b style="color:{d}">{_esc(v)}</b></div>' for k, v in items)
    return f'<div class="bars">{out}</div>'


# ══════════════════════════════ MOTIF COVERS ══════════════════════════════
def _cover_ledger(meta, report):  # enrollments
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _wrap(meta, report, [
        _readout([("Enrollments", s["total"]), ("Completed", s["completed"], "ok"),
                  ("In progress", s["in_progress"], "warn"), ("Overdue", s["overdue"], "danger"),
                  ("Completion", f"{s['completion_rate']:g}%")], a, d),
        f'<div class="inst"><div class="inst-h">Status distribution</div>{_bars([("Completed", s["completed"]), ("In progress", s["in_progress"]), ("Not started", s["not_started"]), ("Overdue", s["overdue"])], a, d)}</div>',
    ])


def _cover_funnel(meta, report):  # completion
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    stages = [("Enrolled", s["enrolled"], 100), ("In progress", s["in_progress"], None), ("Completed", s["completed"], None)]
    base = max(s["enrolled"], 1)
    seg = "".join(
        f'<div class="fn-row"><span class="fn-l">{_esc(k)}</span>'
        f'<span class="fn-bar" style="width:{max(8, v / base * 100):.0f}%;background:linear-gradient(90deg,{a},{d})">{v}</span></div>'
        for k, v, _ in stages)
    return _wrap(meta, report, [
        _readout([("Programs", s["programs"]), ("Enrolled", s["enrolled"]),
                  ("Completed", s["completed"], "ok"), ("Completion", f"{s['completion_rate']:g}%", "ok")], a, d),
        f'<div class="inst"><div class="inst-h">Completion funnel</div><div class="funnel">{seg}</div></div>',
    ])


def _cover_dial(meta, report):  # assessments
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _wrap(meta, report, [
        _readout([("Assessments", s["assessments"]), ("Attempts", s["attempts"]),
                  ("Passed", s["passed"], "ok"), ("Pass rate", f"{s['pass_rate']:g}%"),
                  ("Avg score", f"{s['avg_score']:g}%")], a, d),
        f'<div class="inst inst-center">{_gauge(s["pass_rate"], a, d, f"{s["pass_rate"]:g}%", "PASS RATE")}'
        f'<div class="inst-cap">{s["passed"]} of {s["attempts"]} attempts cleared · avg score {s["avg_score"]:g}%</div></div>',
    ])


def _cover_stars(meta, report):  # feedback
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    full = int(round(s["avg_rating"]))
    stars = "".join(f'<span style="color:{a if i < full else "#e5d9c0"}">★</span>' for i in range(5))
    return _wrap(meta, report, [
        _readout([("Programs", s["programs"]), ("Responses", s["responses"]),
                  ("Avg rating", f"{s['avg_rating']:g}★", "ok")], a, d),
        f'<div class="inst inst-center"><div class="stars">{stars}</div>'
        f'<div class="stars-big" style="color:{d}">{s["avg_rating"]:g}<span>/ 5</span></div>'
        f'<div class="inst-cap">Top-rated: {_esc(s["top"])}</div></div>',
    ])


def _cover_radar(meta, report):  # skill_gap
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _wrap(meta, report, [
        _readout([("Skills", s["skills"]), ("Avg gap", s["avg_gap"], "warn"),
                  ("With gap", s["with_gap"]), ("Critical", s["critical"], "danger"),
                  ("Covered", s["covered"], "ok")], a, d),
        f"""<div class="inst inst-center"><svg viewBox="0 0 120 120" width="150" height="150">
          <circle cx="60" cy="60" r="54" fill="none" stroke="{a}44" stroke-width="0.8"/>
          <circle cx="60" cy="60" r="38" fill="none" stroke="{a}33" stroke-width="0.6"/>
          <circle cx="60" cy="60" r="22" fill="none" stroke="{a}33" stroke-width="0.6"/>
          <polygon points="60,12 100,42 86,96 34,96 20,42" fill="{a}22" stroke="{a}" stroke-width="1"/>
          <text x="60" y="58" text-anchor="middle" font-size="22" font-weight="900" fill="{d}">{s['avg_gap']:g}</text>
          <text x="60" y="70" text-anchor="middle" font-size="5.5" letter-spacing="1" fill="{a}">AVG GAP</text></svg>
          <div class="inst-cap">{s['critical']} skills critical (gap ≥ 2) · {s['covered']} fully covered</div></div>""",
    ])


def _cover_timeline(meta, report):  # certifications
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    tot = max(s["total"], 1)
    segs = [("Active", s["active"], "#047857"), ("Expiring", s["expiring"], "#c2410c"), ("Expired", s["expired"], "#b91c1c")]
    bar = "".join(f'<span style="width:{v / tot * 100:.0f}%;background:{col}" title="{k}"></span>' for k, v, col in segs if v)
    legend = "".join(f'<span class="hz-l"><i style="background:{col}"></i>{k} · {v}</span>' for k, v, col in segs)
    return _wrap(meta, report, [
        _readout([("Credentials", s["total"]), ("Active", s["active"], "ok"),
                  ("Expiring", s["expiring"], "warn"), ("Expired", s["expired"], "danger")], a, d),
        f'<div class="inst"><div class="inst-h">Expiry horizon</div><div class="horizon">{bar}</div>'
        f'<div class="hz-legend">{legend}</div>'
        f'<div class="inst-cap">Nearest renewal in {_esc(s["soonest_days"])} day(s)</div></div>',
    ])


def _cover_ratingarc(meta, report):  # trainers
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    pct = s["avg_rating"] / 5 * 100
    return _wrap(meta, report, [
        _readout([("Trainers", s["trainers"]), ("Active", s["active"], "ok"),
                  ("Avg rating", f"{s['avg_rating']:g}★"), ("Ratings", s["responses"])], a, d),
        f'<div class="inst inst-center">{_gauge(pct, a, d, f"{s["avg_rating"]:g}", "AVG ★ / 5")}'
        f'<div class="inst-cap">{s["rated"]} trainer(s) rated across {s["responses"]} reviews</div></div>',
    ])


def _cover_gauge(meta, report):  # compliance
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _wrap(meta, report, [
        _readout([("Programs", s["programs"]), ("Eligible", s["eligible"]),
                  ("Compliant", s["compliant"], "ok"), ("Overdue", s["overdue"], "danger"),
                  ("Coverage", f"{s['coverage']:g}%")], a, d),
        f'<div class="inst inst-center">{_gauge(s["coverage"], a, d, f"{s["coverage"]:g}%", "COVERAGE")}'
        f'<div class="inst-cap">{s["compliant"]} of {s["eligible"]} eligible are current · {s["overdue"]} overdue</div></div>',
    ])


def _cover_pipeline(meta, report):  # requests
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    stations = [("Pending", s["pending"]), ("Approved", s["approved"]), ("Fulfilled", s["fulfilled"])]
    st = "".join(
        f'<div class="st"><div class="st-dot" style="background:{a}">{v}</div><div class="st-l">{_esc(k)}</div></div>'
        + ('<div class="st-link" style="background:linear-gradient(90deg,' + a + ',' + d + ')"></div>' if i < 2 else '')
        for i, (k, v) in enumerate(stations))
    return _wrap(meta, report, [
        _readout([("Requests", s["total"]), ("Pending", s["pending"], "warn"),
                  ("Approved", s["approved"], "ok"), ("Fulfilled", s["fulfilled"], "ok"),
                  ("Rejected", s["rejected"], "danger")], a, d),
        f'<div class="inst"><div class="inst-h">Approval pipeline</div><div class="pipe">{st}</div>'
        f'<div class="inst-cap">{s["fulfil_rate"]:g}% of decided requests converted into enrolments</div></div>',
    ])


def _cover_vault(meta, report):  # budget
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    alloc = max(s["allocated"], 1)
    sp = s["spent"] / alloc * 100; cm = s["committed"] / alloc * 100
    return _wrap(meta, report, [
        _readout([("Budgets", s["budgets"]), ("Allocated", f"₹{s['allocated']:,.0f}"),
                  ("Spent", f"₹{s['spent']:,.0f}", "warn"), ("Committed", f"₹{s['committed']:,.0f}"),
                  ("Utilization", f"{s['utilization']:g}%", "danger" if s["utilization"] > 100 else "ok")], a, d),
        f'<div class="inst"><div class="inst-h">Allocation fuel</div>'
        f'<div class="fuel"><span class="fuel-s" style="width:{min(100, sp):.0f}%;background:{d}"></span>'
        f'<span class="fuel-c" style="width:{min(100 - min(100, sp), cm):.0f}%;background:{a}"></span></div>'
        f'<div class="hz-legend"><span class="hz-l"><i style="background:{d}"></i>Spent {s["spent"] / alloc * 100:.0f}%</span>'
        f'<span class="hz-l"><i style="background:{a}"></i>Committed {cm:.0f}%</span>'
        f'<span class="hz-l"><i style="background:#e5d9c0"></i>Free ₹{s["available"]:,.0f}</span></div>'
        f'<div class="inst-cap">{s["utilization"]:g}% of ₹{s["allocated"]:,.0f} committed or spent</div></div>',
    ])


def _cover_grid(meta, report):  # department
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    top = report["rows"][:6]
    board = "".join(
        f'<div class="bar"><span class="bar-k">{_esc(r["department"])}</span>'
        f'<span class="bar-t"><i style="width:{r["completion_rate"]:.0f}%;background:linear-gradient(90deg,{a},{d})"></i></span>'
        f'<b style="color:{d}">{r["completion_rate"]:g}%</b></div>' for r in top) or '<div class="db-empty">No departments.</div>'
    return _wrap(meta, report, [
        _readout([("Departments", s["departments"]), ("People", s["employees"]),
                  ("Assigned", s["assignments"]), ("Completion", f"{s['completion_rate']:g}%", "ok"),
                  ("Active certs", s["active_certs"])], a, d),
        f'<div class="inst"><div class="inst-h">Completion leaderboard</div><div class="bars">{board}</div></div>',
    ])


def _wrap(meta, report, blocks):
    a, d = meta["accent"], meta["accent_deep"]
    return f"""<section class="cover">{_slab(meta, report)}
      <div class="sheet">{_period_strip(report["period"], a, d)}{"".join(blocks)}{_gen_line()}</div></section>"""


# ════════════════════ SELF-SERVICE MOTIFS (personal series) ════════════════════
def _cover_transcript(meta, report):  # my_record — a completion gauge + learning-hours stamp
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    return _wrap(meta, report, [
        _readout([("Programs", s["total"]), ("Completed", s["completed"], "ok"),
                  ("In progress", s["in_progress"], "warn"), ("Overdue", s["overdue"], "danger"),
                  ("Completion", f"{s['completion_rate']:g}%")], a, d),
        f'<div class="inst inst-center">{_gauge(s["completion_rate"], a, d, f"{s["completion_rate"]:g}%", "COMPLETE")}'
        f'<div class="hours" style="border-color:{a}"><b style="color:{d}">{s["hours"]:g}</b><span>LEARNING HOURS LOGGED</span></div>'
        f'<div class="inst-cap">{s["completed"]} of {s["total"]} programs complete · {s["not_started"]} not started</div></div>',
    ])


def _cover_passport(meta, report):  # my_skills — at-target ring + widest gaps
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    top = [(r["skill"], r["gap"]) for r in report["rows"][:5] if isinstance(r.get("gap"), (int, float)) and r["gap"] > 0]
    bars = _bars(top, a, d) if top else '<div class="db-empty">Every tracked competency is at or above target.</div>'
    pct = round(s["at_target"] / s["skills"] * 100) if s["skills"] else 0
    return _wrap(meta, report, [
        _readout([("Skills", s["skills"]), ("At target", s["at_target"], "ok"),
                  ("With gap", s["with_gap"], "warn"), ("Mastered", s.get("mastered", 0), "ok"),
                  ("Avg gap", s["avg_gap"])], a, d),
        f'<div class="inst inst-center">{_gauge(pct, a, d, f"{pct:g}%", "AT TARGET")}'
        f'<div class="inst-cap">{s["at_target"]} of {s["skills"]} competencies meet their required level</div></div>',
        f'<div class="inst"><div class="inst-h">Widest competency gaps</div>{bars}</div>',
    ])


def _cover_portfolio(meta, report):  # my_credentials — a wallet of credential seals
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    bands = [("Active", s["active"], "#047857"), ("Expiring", s["expiring"], "#c2410c"), ("Expired", s["expired"], "#b91c1c")]
    seals = "".join(f'<span class="seal" style="background:{col}">✚</span>'
                    for _, n, col in bands for _ in range(min(int(n or 0), 8)))
    seals = seals or '<span class="db-empty">No credentials logged yet — add yours from the Learning Hub.</span>'
    legend = "".join(f'<span class="hz-l"><i style="background:{col}"></i>{k} · {n}</span>' for k, n, col in bands)
    return _wrap(meta, report, [
        _readout([("Credentials", s["total"]), ("Active", s["active"], "ok"),
                  ("Expiring", s["expiring"], "warn"), ("Expired", s["expired"], "danger")], a, d),
        f'<div class="inst"><div class="inst-h">Credential wallet</div><div class="seals">{seals}</div>'
        f'<div class="hz-legend">{legend}</div>'
        f'<div class="inst-cap">Nearest renewal in {_esc(s["soonest_days"])} day(s)</div></div>',
    ])


def _cover_journey(meta, report):  # my_requests — a request lifecycle path
    a, d = meta["accent"], meta["accent_deep"]; s = report["summary"]
    stations = [("Submitted", s["total"]), ("Pending", s["pending"]), ("Approved", s["approved"]), ("Fulfilled", s["fulfilled"])]
    st = "".join(
        f'<div class="st"><div class="st-dot" style="background:{a}">{v}</div><div class="st-l">{_esc(k)}</div></div>'
        + ('<div class="st-link" style="background:linear-gradient(90deg,' + a + ',' + d + ')"></div>' if i < len(stations) - 1 else '')
        for i, (k, v) in enumerate(stations))
    return _wrap(meta, report, [
        _readout([("Requests", s["total"]), ("Pending", s["pending"], "warn"),
                  ("Approved", s["approved"], "ok"), ("Fulfilled", s["fulfilled"], "ok"),
                  ("Rejected", s["rejected"], "danger")], a, d),
        f'<div class="inst"><div class="inst-h">Request journey</div><div class="pipe">{st}</div>'
        f'<div class="inst-cap">{s["fulfil_rate"]:g}% of decided nominations turned into enrolments</div></div>',
    ])


COVER_RENDERERS = {
    "ledger": _cover_ledger, "funnel": _cover_funnel, "dial": _cover_dial, "stars": _cover_stars,
    "radar": _cover_radar, "timeline": _cover_timeline, "ratingarc": _cover_ratingarc,
    "gauge": _cover_gauge, "pipeline": _cover_pipeline, "vault": _cover_vault, "grid": _cover_grid,
    # self-service
    "transcript": _cover_transcript, "passport": _cover_passport,
    "portfolio": _cover_portfolio, "journey": _cover_journey,
}


# ══════════════════════════════ BODY ══════════════════════════════
def _body(meta, report):
    a, d = meta["accent"], meta["accent_deep"]
    return f"""<section class="bodypage">
      <div class="flag"><span class="flag-mark" style="background:{a}">{meta['icon']}</span>
        <div class="flag-txt"><div class="flag-name" style="color:{d}">{_esc(meta['name'])}</div>
          <div class="flag-tag">DETAIL LEDGER · {len(report['rows'])} RECORDS</div></div>
        <div class="flag-period">{_esc(report['period']['label'])}</div></div>
      {_table(report)}</section>"""


# ══════════════════════════════ CSS ══════════════════════════════
def _css(a, d, s):
    css = """
@page { size: A4; margin: 14mm 12mm 15mm;
  @bottom-left { content: "%LEGAL% · %WEB%"; font-size: 7pt; color: #9a8a6a; }
  @bottom-center { content: "Learning Observatory · Training & Development"; font-size: 7pt; color: #c4b59a; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 7pt; color: #9a8a6a; } }
@page :first { margin: 0; @bottom-left { content: ""; } @bottom-center { content: ""; } @bottom-right { content: ""; } }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #1a1410; margin: 0; }
.cover { position: relative; width: 210mm; min-height: 297mm; page-break-after: always; background: #fff; }
.slab { position: relative; padding: 20mm 18mm 15mm 22mm; color: #fff; overflow: hidden; }
.slab .bignum { position: absolute; right: 8mm; bottom: -16mm; font-size: 150pt; font-weight: 900; color: #ffffff14; letter-spacing: -4pt; line-height: 1; }
.spine { position: absolute; left: 6.5mm; bottom: 18mm; transform-origin: left bottom; transform: rotate(-90deg); font-size: 6.4pt; letter-spacing: 3pt; font-weight: 800; white-space: nowrap; color: #ffffffcc; }
.slab-top { position: relative; display: flex; align-items: center; justify-content: space-between; }
.brand { display: flex; align-items: center; gap: 3mm; }
.crest { width: 11mm; height: 11mm; border-radius: 2.6mm; background: #fff; font-weight: 900; font-size: 15pt; display: flex; align-items: center; justify-content: center; }
.brand-txt { font-size: 8.4pt; font-weight: 800; letter-spacing: 2.2pt; color: #ffffffe6; }
.issue { text-align: right; line-height: 1.1; }
.iss-k { display: block; font-size: 6.4pt; letter-spacing: 2.2pt; font-weight: 800; color: #ffffffcc; }
.iss-n { display: block; font-size: 16pt; font-weight: 900; color: #fff; }
.eyebrow { position: relative; margin-top: 13mm; font-size: 7.4pt; letter-spacing: 3pt; font-weight: 900; text-transform: uppercase; color: #ffffffdd; }
.eyebrow::before { content: ""; display: inline-block; width: 11mm; height: 1.4pt; background: #ffffffcc; vertical-align: middle; margin-right: 3.5mm; }
.display { position: relative; font-size: 48pt; font-weight: 900; letter-spacing: -1.4pt; line-height: 0.96; margin: 4mm 0; color: #fff; }
.slab-sub { position: relative; font-size: 10.5pt; font-weight: 500; color: #ffffffe0; margin: 0; max-width: 150mm; }
.sheet { position: relative; padding: 9mm 18mm 18mm 22mm; }
.period { display: flex; align-items: center; gap: 4mm; margin: 0 0 7mm; }
.p-tag { color: #fff; font-size: 7pt; font-weight: 900; letter-spacing: 2pt; padding: 1.6mm 3.2mm; border-radius: 1.5mm; }
.p-box { display: flex; flex-direction: column; font-size: 10.5pt; font-weight: 800; }
.p-box b { font-size: 6pt; letter-spacing: 1.5pt; color: #9a8a6a; font-weight: 800; }
.p-arr { font-size: 13pt; font-weight: 900; }
.p-days { font-size: 8pt; font-weight: 800; margin-left: auto; padding: 1.4mm 3.4mm; border-radius: 4mm; background: %SOFT%; }
.readout { position: relative; display: flex; border: 1.4pt solid; border-radius: 3mm; overflow: hidden; margin: 0 0 8mm; background: #fff; box-shadow: 0 1.5mm 4mm #0000000d; }
.ro-edge { position: absolute; left: 0; top: 0; bottom: 0; width: 1.8mm; }
.ro-cell { flex: 1 1 0; padding: 5mm 2.5mm 4.4mm; text-align: center; border-left: 0.6pt solid #00000012; }
.ro-cell:first-child { border-left: none; padding-left: 5mm; }
.ro-v { font-size: 20pt; font-weight: 900; line-height: 1; }
.ro-l { font-size: 6.4pt; font-weight: 800; letter-spacing: 0.8pt; text-transform: uppercase; color: #6b5840; margin-top: 2mm; }
.inst { margin: 0 0 6mm; }
.inst-center { text-align: center; }
.inst-h { font-size: 8.4pt; font-weight: 900; text-transform: uppercase; letter-spacing: 1.6pt; color: #6b5840; margin-bottom: 4mm; padding-bottom: 1.6mm; border-bottom: 1pt solid #00000014; }
.inst-cap { font-size: 7.6pt; color: #9a8a6a; font-style: italic; margin-top: 3mm; }
.bars { display: flex; flex-direction: column; gap: 3mm; }
.bar { display: flex; align-items: center; gap: 3mm; }
.bar-k { flex: 0 0 38mm; font-size: 8.4pt; font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-t { flex: 1; height: 5mm; background: #00000010; border-radius: 2.5mm; overflow: hidden; }
.bar-t i { display: block; height: 100%; border-radius: 2.5mm; }
.bar b { flex: 0 0 14mm; text-align: right; font-weight: 900; font-size: 9.5pt; }
.db-empty { color: #9a8a6a; font-style: italic; font-size: 9pt; }
.funnel { display: flex; flex-direction: column; gap: 3mm; align-items: center; }
.fn-row { display: flex; align-items: center; gap: 3mm; width: 100%; }
.fn-l { flex: 0 0 30mm; font-size: 8.4pt; font-weight: 800; }
.fn-bar { height: 9mm; border-radius: 1.6mm; color: #fff; font-weight: 900; font-size: 10pt; display: flex; align-items: center; padding-left: 3mm; }
.stars { font-size: 26pt; letter-spacing: 2pt; }
.stars-big { font-size: 36pt; font-weight: 900; line-height: 1; margin-top: 1mm; }
.stars-big span { font-size: 12pt; font-weight: 700; color: #9a8a6a; margin-left: 2mm; }
.horizon { display: flex; height: 9mm; border-radius: 2mm; overflow: hidden; background: #00000010; }
.horizon span { height: 100%; }
.hz-legend { display: flex; gap: 6mm; margin-top: 3mm; flex-wrap: wrap; }
.hz-l { display: inline-flex; align-items: center; gap: 1.8mm; font-size: 8pt; font-weight: 700; color: #6b5840; }
.hz-l i { width: 3mm; height: 3mm; border-radius: 0.8mm; display: inline-block; }
.fuel { display: flex; height: 11mm; border-radius: 2.4mm; overflow: hidden; background: #e5d9c0; }
.fuel span { height: 100%; }
.hours { display: inline-flex; flex-direction: column; align-items: center; margin: 3mm auto 0; padding: 3mm 7mm; border: 1.4pt solid; border-radius: 3mm; }
.hours b { font-size: 24pt; font-weight: 900; line-height: 1; }
.hours span { font-size: 6.4pt; font-weight: 800; letter-spacing: 1.4pt; color: #6b5840; margin-top: 1.6mm; }
.seals { display: flex; flex-wrap: wrap; gap: 2.6mm; }
.seal { width: 9.5mm; height: 9.5mm; border-radius: 50%; color: #fff; font-weight: 900; font-size: 10pt; display: flex; align-items: center; justify-content: center; box-shadow: inset 0 0 0 1mm #ffffff44; }
.pipe { display: flex; align-items: center; }
.st { text-align: center; }
.st-dot { width: 15mm; height: 15mm; border-radius: 50%; color: #fff; font-weight: 900; font-size: 14pt; display: flex; align-items: center; justify-content: center; }
.st-l { font-size: 7.6pt; font-weight: 800; margin-top: 2mm; color: #6b5840; }
.st-link { flex: 1; height: 1.6mm; border-radius: 1mm; margin: 0 2mm 6mm; }
.gen { position: absolute; left: 22mm; right: 18mm; bottom: 9mm; text-align: center; font-size: 7.4pt; color: #9a8a6a; border-top: 0.8pt solid #00000014; padding-top: 3mm; }
.bodypage { padding: 2mm 0; }
.flag { display: flex; align-items: center; gap: 4mm; margin-bottom: 5mm; padding-bottom: 3mm; border-bottom: 2.4pt solid %ACCENT%; }
.flag-mark { width: 12mm; height: 12mm; border-radius: 2.6mm; color: #fff; font-weight: 900; font-size: 16pt; display: flex; align-items: center; justify-content: center; }
.flag-txt { flex: 1; }
.flag-name { font-size: 17pt; font-weight: 900; letter-spacing: -0.4pt; }
.flag-tag { font-size: 7pt; font-weight: 800; letter-spacing: 1.6pt; color: #9a8a6a; margin-top: 0.6mm; }
.flag-period { font-size: 8.5pt; font-weight: 800; color: #6b5840; text-align: right; }
.dt { width: 100%; border-collapse: collapse; font-size: 7.6pt; }
.dt th { background: %ACCENT%; color: #fff; text-align: left; padding: 2.4mm 1.8mm; font-size: 6.8pt; text-transform: uppercase; letter-spacing: 0.6pt; font-weight: 800; }
.dt th.r { text-align: right; } .dt th.c { text-align: center; }
.dt th.lead { border-left: 2.4pt solid %DEEP%; }
.dt td { padding: 2mm 1.8mm; border-bottom: 0.6pt solid #00000010; }
.dt td.lead { border-left: 2.4pt solid %ACCENT%; font-weight: 700; }
.dt td.r { text-align: right; font-variant-numeric: tabular-nums; }
.dt td.c { text-align: center; }
.dt tr.zebra td { background: %SOFT%88; }
.dt tbody tr { page-break-inside: avoid; }
.dt td.empty { text-align: center; color: #9a8a6a; font-style: italic; padding: 10mm; border-left: none; }
.cell-danger { background: #fee2e2; color: #7f1d1d; font-weight: 800; }
.cell-good { background: #ccfbf1; color: #115e59; font-weight: 700; }
.pill { display: inline-block; padding: 0.8mm 2.6mm; border-radius: 4mm; font-size: 6.4pt; font-weight: 800; text-transform: uppercase; letter-spacing: 0.4pt; }
.pill-good { background: #ccfbf1; color: #115e59; }
.pill-warn { background: #ffedd5; color: #9a3412; }
.pill-danger { background: #fee2e2; color: #991b1b; }
.pill-neutral { background: #e2e8f0; color: #475569; }
"""
    return (css.replace("%ACCENT%", a).replace("%DEEP%", d).replace("%SOFT%", s)
            .replace("%LEGAL%", COMPANY["legal"]).replace("%WEB%", COMPANY["web"]))


def render_pdf(report: dict, key: str) -> bytes:
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433 — lazy after GTK PATH prep

    meta = report_meta(key)
    cover_fn = COVER_RENDERERS.get(meta["motif"], _cover_ledger)
    cover = cover_fn(meta, report)
    body = _body(meta, report)
    css = _css(meta["accent"], meta["accent_deep"], meta["accent_soft"])
    full = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{_esc(meta["name"])} · {COMPANY["name"]}</title><style>{css}</style></head>'
            f'<body>{cover}{body}</body></html>')
    return HTML(string=full).write_pdf()
