"""Server-side Service Level Agreement PDF (WeasyPrint).

An ultra-modern, multi-page agreement document:

    * A full-bleed dramatic cover page — near-black warm ink with a gold glow,
      an oversized agreement title, a mono reference chip, a meta grid, parties
      strip and a prominent contract-value block.
    * Editorial content pages on warm cream — each section announced by a thin
      letter-spaced eyebrow + an oversized outlined numeral, refined data tables
      with tinted headers + tabular numerals, a gold financial highlight, and a
      digital-execution signature block.
    * A running footer (company line + "Page X of Y" via CSS counters) on every
      content page; the cover is footer-free.

All measurements use ``mm`` / ``pt`` so the output prints crisply to A4. Fonts
are the system sans/mono stack (no embedded display faces) — modern and safe on
Windows where WeasyPrint maps Helvetica → Arial.

Public entry: ``render_sla_pdf(sla) -> bytes``  (``sla`` is the SlaAgreement ORM
object with its ``services`` / ``escalations`` / ``penalties`` / ``signatories``
relationships loaded).
"""
from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

# WeasyPrint is imported lazily inside render_sla_pdf so the backend can boot on
# a machine that hasn't run vendor/setup_gtk.py yet.

COMPANY = {
    "name": "Fourreck",
    "legal": "Fourreck Technologies Pvt. Ltd.",
    "tagline": "Service Level Agreement",
}


# ════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ════════════════════════════════════════════════════════════════════════════


def _esc(v: Any) -> str:
    if v is None:
        return ""
    return html.escape(str(v))


def _para(text: Any) -> str:
    """Escape free text and preserve the author's line breaks as <br>."""
    if not text:
        return ""
    return _esc(str(text).strip()).replace("\n", "<br>")


def _fmt_date(d) -> str:
    if not d:
        return "—"
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d)
        except ValueError:
            return d
    return d.strftime("%d %b %Y")


def _fmt_long_date(d) -> str:
    if not d:
        return "—"
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d)
        except ValueError:
            return d
    return d.strftime("%d %B %Y")


def _inr_group(value) -> str:
    """Indian digit grouping (e.g. 1,23,45,678.00)."""
    try:
        v = Decimal(str(value or 0))
    except Exception:
        return "0.00"
    neg = v < 0
    v = abs(v)
    whole = int(v)
    frac = int(round((v - whole) * 100))
    s = str(whole)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ",".join(groups) + "," + last3
    return ("-" if neg else "") + f"{s}.{frac:02d}"


def _initials(name: Optional[str]) -> str:
    if not name:
        return "··"
    parts = [p for p in str(name).split() if p]
    if not parts:
        return "··"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _has(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (list, tuple, dict, str)):
        return len(v) > 0
    return True


def _chips(items: Iterable, *, kind: str = "") -> str:
    cls = "chip" + (f" chip-{kind}" if kind else "")
    return "".join(f'<span class="{cls}">{_esc(i)}</span>' for i in items if _esc(i))


# ════════════════════════════════════════════════════════════════════════════
# Section builders
# ════════════════════════════════════════════════════════════════════════════


def _section_head(num: str, eyebrow: str, title: str) -> str:
    return (
        '<div class="sec-head">'
        f'<div class="sec-num">{num}</div>'
        '<div class="sec-titles">'
        f'<div class="eyebrow">{_esc(eyebrow)}</div>'
        f'<h2>{_esc(title)}</h2>'
        "</div></div>"
    )


def _kv(label: str, value: str) -> str:
    return f'<div class="kv"><span class="kv-l">{_esc(label)}</span><span class="kv-v">{value}</span></div>'


def _stat_tiles(pairs) -> str:
    """Modern gold-accent stat tiles. `pairs` is a list of (label, value_html);
    labels are escaped, values are treated as raw HTML so callers can pass mono
    spans etc. Skips pairs whose value is empty and returns "" when none remain."""
    tiles = "".join(
        f'<div class="stat-tile"><span class="stat-l">{_esc(label)}</span>'
        f'<span class="stat-v">{value}</span></div>'
        for label, value in pairs if _has(value)
    )
    return f'<div class="stat-grid">{tiles}</div>' if tiles else ""


def _parties_block(sla) -> str:
    prov_addr = _para(sla.provider_address) or "Address not provided"
    cli_addr = _para(sla.client_address) or "Address not provided"
    prov_meta = []
    if sla.provider_registration_number:
        prov_meta.append(_kv("Registration No.", _esc(sla.provider_registration_number)))
    if sla.provider_tax_id:
        prov_meta.append(_kv("Tax ID / GST", _esc(sla.provider_tax_id)))
    cli_meta = []
    if sla.client_contact_person:
        cli_meta.append(_kv("Contact Person", _esc(sla.client_contact_person)))
    if sla.client_email:
        cli_meta.append(_kv("Email", _esc(sla.client_email)))
    if sla.client_phone:
        cli_meta.append(_kv("Phone", _esc(sla.client_phone)))

    return (
        _section_head("01", "Participating Entities", "The Parties")
        + '<div class="party-grid">'
        '<div class="party-card">'
        '<div class="party-role">Service Provider</div>'
        f'<div class="party-name">{_esc(sla.provider_name) or "—"}</div>'
        f'<div class="party-addr">{prov_addr}</div>'
        f'<div class="kv-list">{"".join(prov_meta)}</div>'
        "</div>"
        '<div class="party-card client">'
        '<div class="party-role">Client</div>'
        f'<div class="party-name">{_esc(sla.client_organization_name) or "—"}</div>'
        f'<div class="party-addr">{cli_addr}</div>'
        f'<div class="kv-list">{"".join(cli_meta)}</div>'
        "</div></div>"
    )


def _overview_block(sla) -> str:
    if not _has(sla.description) and not _has(sla.services_covered):
        return ""
    parts = [_section_head("02", "Agreement Overview", "Scope & Intent")]
    if _has(sla.description):
        parts.append(f'<p class="prose">{_para(sla.description)}</p>')
    if _has(sla.services_covered):
        parts.append(
            '<div class="callout"><div class="callout-l">Services Covered</div>'
            f'<p>{_para(sla.services_covered)}</p></div>'
        )
    return "".join(parts)


def _services_block(sla) -> str:
    services = list(sla.services or [])
    if not services:
        return ""
    parts = [_section_head("03", "Service Scope & Commitments", "Services")]
    rows = "".join(
        "<tr>"
        f'<td class="strong">{_esc(s.service_name)}</td>'
        f'<td>{_esc(s.service_category) or "—"}</td>'
        f'<td class="muted">{_para(s.description) or "—"}</td>'
        "</tr>"
        for s in services
    )
    parts.append(
        '<table class="data"><thead><tr>'
        '<th style="width:30%">Service</th>'
        '<th style="width:20%">Category</th>'
        '<th>Description</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )

    for svc in services:
        metrics = list(svc.metrics or [])
        if not metrics:
            continue
        mrows = "".join(
            "<tr>"
            f'<td>{_pill(m.priority_level)}</td>'
            f'<td>{_esc(m.response_time) or "—"}</td>'
            f'<td>{_esc(m.resolution_time) or "—"}</td>'
            f'<td class="strong">{_esc(m.uptime_commitment) or "—"}</td>'
            f'<td class="muted">{_esc(m.measurement_method) or "—"}</td>'
            "</tr>"
            for m in metrics
        )
        parts.append(
            f'<div class="metric-label">Service-level targets · {_esc(svc.service_name)}</div>'
            '<table class="data"><thead><tr>'
            '<th style="width:16%">Priority</th>'
            '<th style="width:18%">Response</th>'
            '<th style="width:18%">Resolution</th>'
            '<th style="width:18%">Uptime</th>'
            '<th>Measured by</th>'
            f"</tr></thead><tbody>{mrows}</tbody></table>"
        )
    return "".join(parts)


def _pill(value: Optional[str]) -> str:
    if not value:
        return '<span class="pill pill-neutral">—</span>'
    key = str(value).strip().lower()
    cls = "pill-neutral"
    if key in ("critical", "p1", "urgent"):
        cls = "pill-danger"
    elif key in ("high", "p2"):
        cls = "pill-warn"
    elif key in ("medium", "p3"):
        cls = "pill-gold"
    elif key in ("low", "p4"):
        cls = "pill-good"
    return f'<span class="pill {cls}">{_esc(value)}</span>'


def _support_block(sla) -> str:
    escalations = list(sla.escalations or [])
    if not escalations:
        return ""
    parts = [_section_head("04", "Support & Escalation", "Support Matrix")]
    gs = escalations[0]
    avail = _esc(gs.support_availability) or "Standard"
    hours = ""
    if (gs.support_availability or "").lower() not in ("24x7", "24/7"):
        if gs.support_start_time or gs.support_end_time:
            hours = (
                f'<span class="band-sub">{_esc(gs.support_start_time) or "09:00"} '
                f'– {_esc(gs.support_end_time) or "17:00"} '
                f'({_esc(gs.timezone) or "IST"})</span>'
            )
    parts.append(
        '<div class="band">'
        '<span class="band-label">Support Availability</span>'
        f'<span class="band-value">{avail}</span>{hours}</div>'
    )
    rows = "".join(
        "<tr>"
        f'<td class="strong">{_esc(e.level) or "—"}</td>'
        f'<td>{_esc(e.role) or "—"}</td>'
        f'<td>{_esc(e.contact_person) or "—"}</td>'
        f'<td class="mono small">{_esc(e.phone) or ""}{("<br>" + _esc(e.email)) if e.email else ""}</td>'
        f'<td>{_esc(e.response_time) or "—"}</td>'
        "</tr>"
        for e in escalations
    )
    parts.append(
        '<table class="data"><thead><tr>'
        '<th style="width:12%">Tier</th>'
        '<th style="width:20%">Role</th>'
        '<th style="width:22%">Contact</th>'
        '<th style="width:28%">Reach</th>'
        '<th>Max Response</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )
    return "".join(parts)


def _monitoring_block(sla) -> str:
    if not (_has(sla.monitoring_tools) or sla.monitoring_dashboard_url or sla.reporting_frequency
            or sla.report_delivery_method or sla.alert_notification_email):
        return ""
    pairs = []
    if sla.reporting_frequency:
        pairs.append(("Review Frequency", _esc(sla.reporting_frequency)))
    if sla.report_delivery_method:
        pairs.append(("Delivery Method", _esc(sla.report_delivery_method)))
    if sla.alert_notification_email:
        pairs.append(("Automated Alerts", _esc(sla.alert_notification_email)))
    if sla.monitoring_dashboard_url:
        pairs.append(("Dashboard", f'<span class="mono small">{_esc(sla.monitoring_dashboard_url)}</span>'))
    tools = ""
    if _has(sla.monitoring_tools):
        tools = f'<div class="chip-row"><span class="chip-row-l">Measurement Tools</span>{_chips(sla.monitoring_tools)}</div>'
    return (
        _section_head("05", "Monitoring & Reporting", "Telemetry")
        + _stat_tiles(pairs)
        + tools
    )


def _security_block(sla) -> str:
    if not (_has(sla.compliance_standards) or _has(sla.security_measures)
            or sla.data_retention_policy or sla.incident_reporting_time):
        return ""
    parts = [_section_head("06", "Security & Compliance", "Safeguards")]
    pairs = []
    if sla.data_retention_policy:
        pairs.append(("Data Retention", _esc(sla.data_retention_policy)))
    if sla.incident_reporting_time:
        pairs.append(("Incident Reporting Window", _esc(sla.incident_reporting_time)))
    tiles = _stat_tiles(pairs)
    if tiles:
        parts.append(tiles)
    if _has(sla.security_measures):
        parts.append(f'<div class="chip-row"><span class="chip-row-l">Security Protocols</span>{_chips(sla.security_measures, kind="good")}</div>')
    if _has(sla.compliance_standards):
        parts.append(f'<div class="chip-row"><span class="chip-row-l">Compliance Standards</span>{_chips(sla.compliance_standards, kind="good")}</div>')
    return "".join(parts)


def _commercials_block(sla) -> str:
    if not _has(sla.agreement_value):
        return ""
    currency = _esc(sla.currency) or "INR"
    freq = f' · {_esc(sla.billing_frequency)}' if sla.billing_frequency else ""
    return (
        _section_head("07", "Commercials", "Financials")
        + '<div class="finance">'
        '<div class="finance-l">Total Contract Value</div>'
        f'<div class="finance-v"><span class="finance-cur">{currency}</span>{_inr_group(sla.agreement_value)}<span class="finance-freq">{freq}</span></div>'
        f'<div class="finance-meta">Payment Method · {_esc(sla.payment_method) or "Not specified"}</div>'
        "</div>"
    )


def _penalties_block(sla) -> str:
    penalties = list(sla.penalties or [])
    if not penalties:
        return ""
    rows = "".join(
        "<tr>"
        f'<td class="strong">{_esc(p.sla_violation) or "—"}</td>'
        f'<td>{_esc(p.penalty_type) or "—"}</td>'
        f'<td class="mono">{_esc(p.penalty_value) or "—"}</td>'
        f'<td class="mono">{_esc(p.maximum_limit) or "—"}</td>'
        "</tr>"
        for p in penalties
    )
    return (
        _section_head("08", "Breach Remedies", "Service Credits & Penalties")
        + '<p class="prose">Where the service levels committed above are not met, the following '
        'service credits and breach remedies apply.</p>'
        + '<table class="data"><thead><tr>'
        '<th style="width:34%">Breach / Violation</th>'
        '<th style="width:22%">Action Applied</th>'
        '<th style="width:22%">Penalty</th>'
        '<th>Hard Limit</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _legal_block(sla) -> str:
    clauses = [
        ("Limitation of Liability", sla.liability_limit),
        ("Termination Conditions", sla.termination_conditions),
        ("Confidentiality", sla.confidentiality_clause),
        ("Intellectual Property", sla.intellectual_property_clause),
        ("Force Majeure", sla.force_majeure_clause),
    ]
    clauses = [(t, c) for t, c in clauses if _has(c)]
    if not clauses:
        return ""
    parts = [_section_head("09", "Legal & Terms", "Conditions")]
    for i, (title, content) in enumerate(clauses, start=1):
        parts.append(
            '<div class="clause">'
            f'<div class="clause-t"><span class="clause-n">9.{i}</span>{_esc(title)}</div>'
            f'<p>{_para(content)}</p>'
            "</div>"
        )
    return "".join(parts)


def _signatures_block(sla) -> str:
    signatories = list(sla.signatories or [])[:2]
    if not signatories:
        return ""
    cards = []
    for idx, sig in enumerate(signatories):
        party = (sig.party or "").lower()
        if "client" in party:
            mark = '<div class="sig-pending">To be executed by client</div>'
        else:
            stamp = f"SIGNED-SLA-{_esc(sla.contract_reference) or 'REF'}-{idx + 1}".upper().replace(" ", "")
            mark = (
                f'<div class="sig-digital"><span class="sig-dot"></span>{stamp}</div>'
            )
        cards.append(
            '<div class="sig-card">'
            f'<div class="sig-role">Authorized for · {_esc(sig.party) or "—"}</div>'
            f'<div class="sig-name">{_esc(sig.name) or "Signatory Name"}</div>'
            f'<div class="sig-title">{_esc(sig.designation) or "Title"}</div>'
            f"{mark}"
            '<div class="sig-line"></div>'
            '<div class="sig-date">Date executed</div>'
            "</div>"
        )
    return (
        '<div class="sig-section">'
        + _section_head("10", "Digital Execution", "Authorization")
        + '<p class="prose">This agreement has been digitally authorized. The signatures below '
        'serve as binding acceptance of all terms, conditions and service levels outlined herein.</p>'
        + f'<div class="sig-grid">{"".join(cards)}</div>'
        + "</div>"
    )


# ════════════════════════════════════════════════════════════════════════════
# Cover
# ════════════════════════════════════════════════════════════════════════════


def _cover(sla) -> str:
    title = _esc(sla.title) or "Service Level Agreement"
    subtitle_bits = [b for b in [_esc(sla.client_organization_name), _esc(sla.agreement_type)] if b]
    subtitle = " · ".join(subtitle_bits) or "Master Service Agreement"
    status = (_esc(sla.status) or "Draft").upper()
    value_block = ""
    if _has(sla.agreement_value):
        currency = _esc(sla.currency) or "INR"
        value_block = (
            '<div class="cover-value">'
            '<div class="cover-value-l">Contract Value</div>'
            f'<div class="cover-value-v"><span>{currency}</span>{_inr_group(sla.agreement_value)}</div>'
            "</div>"
        )

    return (
        '<section class="cover">'
        '<div class="cover-glow"></div>'
        '<div class="cover-top">'
        '<div class="brand-mark">'
        '<span class="brand-dot"></span>'
        f'<span class="brand-name">{COMPANY["name"]}</span>'
        "</div>"
        f'<div class="cover-eyebrow">{COMPANY["tagline"]}</div>'
        "</div>"
        '<div class="cover-mid">'
        f'<h1 class="cover-title">{title}</h1>'
        f'<div class="cover-sub">{subtitle}</div>'
        '<div class="cover-rule"></div>'
        '<div class="cover-meta">'
        f'<div class="cm"><span>Reference</span><b>{_esc(sla.contract_reference) or "—"}</b></div>'
        f'<div class="cm"><span>Effective</span><b>{_fmt_long_date(sla.start_date)}</b></div>'
        f'<div class="cm"><span>Expires</span><b>{_fmt_long_date(sla.end_date)}</b></div>'
        f'<div class="cm"><span>Version</span><b>v{_esc(sla.version) or "1.0"}</b></div>'
        f'<div class="cm"><span>Status</span><b class="status-{status.lower()}">{status}</b></div>'
        f'<div class="cm"><span>Renewal</span><b>{_esc(sla.renewal_type) or "Manual"}</b></div>'
        "</div>"
        f"{value_block}"
        "</div>"
        '<div class="cover-bottom">'
        '<div class="cp">'
        '<span class="cp-l">Service Provider</span>'
        f'<span class="cp-v">{_esc(sla.provider_name) or "—"}</span>'
        "</div>"
        '<div class="cp-arrow">⟶</div>'
        '<div class="cp right">'
        '<span class="cp-l">Client</span>'
        f'<span class="cp-v">{_esc(sla.client_organization_name) or "—"}</span>'
        "</div>"
        "</div>"
        "</section>"
    )


# ════════════════════════════════════════════════════════════════════════════
# Stylesheet
# ════════════════════════════════════════════════════════════════════════════

_CSS = """
@page {
    size: A4;
    margin: 20mm 16mm 18mm;
    @bottom-left { content: "Fourreck SLA Engine"; font-family: 'Consolas','Courier New',monospace;
        font-size: 7pt; color: #a89a80; }
    @bottom-right { content: "Page " counter(page) " of " counter(pages);
        font-family: 'Consolas','Courier New',monospace; font-size: 7pt; color: #a89a80; letter-spacing: .5pt; }
    @bottom-center { content: ""; }
}
@page cover { margin: 0;
    @bottom-left { content: ""; } @bottom-right { content: ""; } @bottom-center { content: ""; }
}

* { box-sizing: border-box; }
html { -weasy-hyphens: none; }
body {
    margin: 0;
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    color: #2a2118;
    font-size: 9.5pt;
    line-height: 1.55;
}
.mono { font-family: 'Consolas','Courier New',monospace; }
.small { font-size: 8pt; }
.strong { font-weight: 700; color: #1a1410; }
.muted { color: #7a6c57; }

/* ───────────────────────── COVER ───────────────────────── */
.cover {
    page: cover;
    position: relative;
    width: 210mm; height: 297mm;
    background: radial-gradient(120% 80% at 78% 8%, #2a2114 0%, #1a1510 38%, #0d0b07 100%);
    color: #f6efe1;
    padding: 22mm 20mm 18mm;
    display: flex; flex-direction: column;
    overflow: hidden;
}
.cover-glow {
    position: absolute; top: -40mm; right: -40mm; width: 130mm; height: 130mm;
    background: radial-gradient(circle, rgba(245,158,11,0.34) 0%, rgba(245,158,11,0) 70%);
}
.cover-top { display: flex; justify-content: space-between; align-items: center; position: relative; z-index: 2; }
.brand-mark { display: flex; align-items: center; gap: 3mm; }
.brand-dot { width: 4mm; height: 4mm; border-radius: 1mm;
    background: linear-gradient(135deg, #fbbf24, #d97706); display: inline-block; }
.brand-name { font-size: 14pt; font-weight: 800; letter-spacing: 2pt; text-transform: uppercase; color: #fff; }
.cover-eyebrow { font-family: 'Consolas','Courier New',monospace; font-size: 7.5pt; letter-spacing: 3pt;
    text-transform: uppercase; color: #f5b942; }
.cover-mid { flex: 1; display: flex; flex-direction: column; justify-content: center; position: relative; z-index: 2; }
.cover-title {
    font-size: 46pt; font-weight: 800; line-height: 1.02; letter-spacing: -1.5pt;
    margin: 0 0 5mm; color: #ffffff; max-width: 165mm;
}
.cover-sub { font-size: 12pt; color: #cdbfa6; font-weight: 500; letter-spacing: .3pt; }
.cover-rule { height: 1.2pt; width: 40mm; margin: 9mm 0;
    background: linear-gradient(90deg, #f59e0b, rgba(245,158,11,0)); }
.cover-meta { display: flex; flex-wrap: wrap; gap: 7mm 14mm; max-width: 165mm; }
.cm { display: flex; flex-direction: column; gap: 1.5mm; }
.cm span { font-family: 'Consolas','Courier New',monospace; font-size: 6.8pt; letter-spacing: 1.8pt;
    text-transform: uppercase; color: #9b8e76; }
.cm b { font-size: 11pt; font-weight: 700; color: #f1e7d4; }
.status-approved { color: #6ee7a8 !important; }
.status-pending { color: #fbbf24 !important; }
.status-rejected { color: #f87171 !important; }
.status-draft { color: #cbb994 !important; }
.cover-value { margin-top: 12mm; padding: 7mm 8mm; border-radius: 3mm;
    background: linear-gradient(135deg, rgba(245,158,11,0.16), rgba(120,53,15,0.10));
    border: 0.4pt solid rgba(245,158,11,0.45); max-width: 110mm; }
.cover-value-l { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; letter-spacing: 2.5pt;
    text-transform: uppercase; color: #f5b942; margin-bottom: 2mm; }
.cover-value-v { font-size: 30pt; font-weight: 800; color: #fff; letter-spacing: -1pt; }
.cover-value-v span { font-size: 13pt; color: #cdbfa6; font-weight: 600; margin-right: 3mm; vertical-align: 6pt; }
.cover-bottom { display: flex; align-items: center; gap: 8mm; position: relative; z-index: 2;
    padding-top: 7mm; border-top: 0.4pt solid rgba(255,255,255,0.12); }
.cp { display: flex; flex-direction: column; gap: 1.5mm; flex: 1; }
.cp.right { text-align: right; align-items: flex-end; }
.cp-l { font-family: 'Consolas','Courier New',monospace; font-size: 6.8pt; letter-spacing: 2pt;
    text-transform: uppercase; color: #9b8e76; }
.cp-v { font-size: 11pt; font-weight: 700; color: #f1e7d4; }
.cp-arrow { color: #f59e0b; font-size: 16pt; }

/* ───────────────────────── CONTENT ───────────────────────── */
.sec-head { display: flex; align-items: flex-start; gap: 5mm; margin: 11mm 0 5mm;
    break-after: avoid; }
.sec-head:first-of-type { margin-top: 0; }
.sec-num { font-size: 30pt; font-weight: 800; line-height: 0.9; letter-spacing: -1pt;
    color: #e7bf6a; min-width: 18mm; }
.sec-titles { padding-top: 1.5mm; }
.eyebrow { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; letter-spacing: 2.2pt;
    text-transform: uppercase; color: #b07d22; margin-bottom: 1mm; }
.sec-titles h2 { font-size: 17pt; font-weight: 800; letter-spacing: -0.5pt; margin: 0; color: #1a1410; }

.prose { margin: 0 0 4mm; color: #3d3225; text-align: justify; }
.callout { margin: 3mm 0 0; padding: 4mm 5mm; border-left: 1.5pt solid #f59e0b;
    background: #fdf6e6; border-radius: 0 2mm 2mm 0; }
.callout-l { font-family: 'Consolas','Courier New',monospace; font-size: 6.8pt; letter-spacing: 2pt;
    text-transform: uppercase; color: #b07d22; margin-bottom: 1.5mm; }
.callout p { margin: 0; color: #3d3225; }

/* key/value */
.kv-list { display: flex; flex-direction: column; gap: 2mm; }
.kv-list.two-col { flex-direction: row; flex-wrap: wrap; gap: 3mm 12mm; }
.kv { display: flex; flex-direction: column; gap: 0.5mm; }
.kv-l { font-family: 'Consolas','Courier New',monospace; font-size: 6.6pt; letter-spacing: 1.5pt;
    text-transform: uppercase; color: #9b8a6f; }
.kv-v { font-size: 9.5pt; color: #1a1410; font-weight: 600; }

/* stat tiles (telemetry / safeguards) — modern gold-accent cards */
.stat-grid { display: flex; flex-wrap: wrap; gap: 3.5mm; margin: 4mm 0 0; }
.stat-tile { flex: 1 1 40%; min-width: 55mm; padding: 4mm 5mm; background: #fdfaf2;
    border: 0.4pt solid #ece0c6; border-left: 2.4pt solid #d97706; border-radius: 0 2.5mm 2.5mm 0; break-inside: avoid; }
.stat-l { display: block; font-family: 'Consolas','Courier New',monospace; font-size: 6.6pt; letter-spacing: 1.5pt;
    text-transform: uppercase; color: #b07d22; margin-bottom: 1.6mm; }
.stat-v { display: block; font-size: 10.5pt; font-weight: 700; color: #1a1410; line-height: 1.35; word-wrap: break-word; }

/* chips */
.chip-row { margin-top: 4mm; }
.chip-row-l { font-family: 'Consolas','Courier New',monospace; font-size: 6.6pt; letter-spacing: 1.5pt;
    text-transform: uppercase; color: #9b8a6f; display: block; margin-bottom: 2mm; }
.chip { display: inline-block; padding: 1.4mm 3mm; margin: 0 1.5mm 1.5mm 0; border-radius: 5mm;
    font-size: 8pt; font-weight: 600; background: #f4ead2; color: #8a5a09; border: 0.3pt solid #e4cf9f; }
.chip-good { background: #e7f5ec; color: #15803d; border-color: #bfe4cb; }

/* parties */
.party-grid { display: flex; gap: 5mm; }
.party-card { flex: 1; padding: 5mm; border: 0.4pt solid #ece0c6; border-radius: 3mm;
    background: #fdfaf2; break-inside: avoid; }
.party-card.client { background: #fdf6e6; border-color: #ecd9a8; }
.party-role { font-family: 'Consolas','Courier New',monospace; font-size: 6.8pt; letter-spacing: 2pt;
    text-transform: uppercase; color: #b07d22; margin-bottom: 2mm; }
.party-name { font-size: 13pt; font-weight: 800; color: #1a1410; margin-bottom: 1.5mm; letter-spacing: -0.3pt; }
.party-addr { font-size: 8.5pt; color: #6b5d47; line-height: 1.5; margin-bottom: 3mm; }

/* tables */
table.data { width: 100%; border-collapse: collapse; margin: 3mm 0 2mm; break-inside: auto; }
table.data thead { background: #1a1410; }
table.data th { color: #f5d99a; font-size: 7.2pt; font-weight: 700; letter-spacing: 1pt;
    text-transform: uppercase; text-align: left; padding: 3mm 3.5mm; }
table.data td { padding: 2.8mm 3.5mm; font-size: 8.7pt; border-bottom: 0.3pt solid #ede3cf;
    vertical-align: top; color: #3d3225; }
table.data tbody tr:nth-child(even) { background: #fbf6ea; }
.metric-label { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; letter-spacing: 1.5pt;
    text-transform: uppercase; color: #b07d22; margin: 5mm 0 0; }

/* pills */
.pill { display: inline-block; padding: 0.8mm 2.6mm; border-radius: 4mm; font-size: 7.6pt; font-weight: 700;
    letter-spacing: .3pt; }
.pill-neutral { background: #efe7d6; color: #6b5d47; }
.pill-gold { background: #fcefcf; color: #92610a; }
.pill-good { background: #e7f5ec; color: #15803d; }
.pill-warn { background: #fdeccb; color: #b45309; }
.pill-danger { background: #fbe1e1; color: #b91c1c; }

/* support band */
.band { display: flex; align-items: baseline; gap: 4mm; padding: 3.5mm 5mm; border-radius: 2.5mm;
    background: linear-gradient(135deg, #f59e0b, #d97706); margin: 1mm 0 1mm; }
.band-label { font-family: 'Consolas','Courier New',monospace; font-size: 6.8pt; letter-spacing: 2pt;
    text-transform: uppercase; color: rgba(255,255,255,0.85); }
.band-value { font-size: 12pt; font-weight: 800; color: #fff; }
.band-sub { font-size: 8.5pt; color: rgba(255,255,255,0.9); margin-left: auto; }

/* finance */
.finance { padding: 6mm 7mm; border-radius: 3mm; background: #1a1410; color: #fff;
    break-inside: avoid; position: relative; overflow: hidden; }
.finance-l { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; letter-spacing: 2.5pt;
    text-transform: uppercase; color: #f5b942; margin-bottom: 2mm; }
.finance-v { font-size: 28pt; font-weight: 800; letter-spacing: -1pt; color: #fff; }
.finance-cur { font-size: 12pt; font-weight: 600; color: #cdbfa6; margin-right: 3mm; vertical-align: 5pt; }
.finance-freq { font-size: 11pt; font-weight: 500; color: #cdbfa6; }
.finance-meta { margin-top: 2.5mm; font-size: 8.5pt; color: #b8a888; }

/* legal */
.clause { margin: 0 0 4mm; break-inside: avoid; }
.clause-t { font-size: 10.5pt; font-weight: 800; color: #1a1410; margin-bottom: 1mm; }
.clause-n { font-family: 'Consolas','Courier New',monospace; font-size: 8.5pt; color: #b07d22;
    margin-right: 2.5mm; }
.clause p { margin: 0; color: #3d3225; text-align: justify; }

/* signatures */
.sig-section { break-inside: avoid; }
.sig-grid { display: flex; gap: 5mm; margin-top: 4mm; }
.sig-card { flex: 1; padding: 5mm; border: 0.4pt solid #ece0c6; border-radius: 3mm; background: #fdfaf2; }
.sig-role { font-family: 'Consolas','Courier New',monospace; font-size: 6.6pt; letter-spacing: 1.5pt;
    text-transform: uppercase; color: #b07d22; margin-bottom: 2mm; }
.sig-name { font-size: 12pt; font-weight: 800; color: #1a1410; }
.sig-title { font-size: 8.5pt; color: #b45309; font-weight: 600; margin-bottom: 4mm; }
.sig-digital { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; color: #6b5d47;
    word-break: break-all; }
.sig-dot { display: inline-block; width: 2mm; height: 2mm; border-radius: 50%; background: #16a34a;
    margin-right: 1.5mm; }
.sig-pending { font-size: 8.5pt; font-style: italic; color: #a08a66; }
.sig-line { height: 0.4pt; background: #c9b896; margin: 8mm 0 1.5mm; }
.sig-date { font-size: 7.5pt; color: #9b8a6f; }
"""


# ════════════════════════════════════════════════════════════════════════════
# Public entry
# ════════════════════════════════════════════════════════════════════════════


def render_sla_pdf(sla) -> bytes:
    """Render the SLA agreement to PDF bytes via WeasyPrint."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433 (lazy — needs GTK on PATH)

    body = (
        _cover(sla)
        + '<main class="doc">'
        + _parties_block(sla)
        + _overview_block(sla)
        + _services_block(sla)
        + _support_block(sla)
        + _monitoring_block(sla)
        + _security_block(sla)
        + _commercials_block(sla)
        + _penalties_block(sla)
        + _legal_block(sla)
        + _signatures_block(sla)
        + "</main>"
    )

    doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )
    return HTML(string=doc).write_pdf()
