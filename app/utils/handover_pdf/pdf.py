"""Server-side Project Handover PDF (WeasyPrint) — bright editorial magazine.

A deliberately *light*, magazine-style closure document — visually distinct from the
dark SLA dossier:

    * A vivid color-block cover (warm amber/orange) with an oversized title and a
      bottom key-facts table — bright, never dark.
    * A "Contents" page listing only the sections that actually carry data.
    * Editorial content pages on white: each section announced by a big solid
      numeral + a colored kicker + a hairline rule, a vertical running side-label,
      magazine pull-quotes, and clean tinted-header data tables.
    * A bright orange financial band, a green client-acceptance section, and a
      light sign-off grid.

Empty fields are omitted entirely (no "—" placeholders for unfilled top-level
fields, and whole sections disappear when they have no data).

System fonts only. Public entry: ``render_handover_pdf(h) -> bytes``.
"""
from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

COMPANY = {"name": "Fourreck", "tagline": "Project Handover & Closure"}

# Fourreck brand mark (rounded rect + two bars), recolored to the document palette:
# a white card with orange bars so it reads correctly on the orange cover.
_LOGO_SVG = (
    '<svg class="cv-logo" viewBox="0 0 52 34" xmlns="http://www.w3.org/2000/svg">'
    '<rect x="0" y="0" width="52" height="34" rx="7" fill="#ffffff"/>'
    '<rect x="24" y="11" width="22" height="4" rx="2" fill="#ea580c"/>'
    '<rect x="30" y="20" width="16" height="4" rx="2" fill="#ea580c"/>'
    "</svg>"
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _esc(v: Any) -> str:
    return "" if v is None else html.escape(str(v))


def _para(text: Any) -> str:
    if not text:
        return ""
    return _esc(str(text).strip()).replace("\n", "<br>")


def _fmt_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d)
        except ValueError:
            return d
    return d.strftime("%d %b %Y")


def _fmt_long_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d)
        except ValueError:
            return d
    return d.strftime("%d %B %Y")


def _term_label(start, end) -> str:
    """Human coverage span between two dates, e.g. '3 years' or '1 year 6 months'."""
    def _p(d):
        if not d:
            return None
        if isinstance(d, str):
            try:
                return datetime.fromisoformat(d)
            except ValueError:
                return None
        return d
    s, e = _p(start), _p(end)
    if not s or not e or e <= s:
        return ""
    months = (e.year - s.year) * 12 + (e.month - s.month) - (1 if e.day < s.day else 0)
    if months < 1:
        days = (e - s).days
        return f"{days} day{'s' if days != 1 else ''}"
    years, rem = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if rem:
        parts.append(f"{rem} month{'s' if rem != 1 else ''}")
    return " ".join(parts)


def _inr_group(value) -> str:
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
        last3, rest, groups = s[-3:], s[:-3], []
        while len(rest) > 2:
            groups.insert(0, rest[-2:]); rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ",".join(groups) + "," + last3
    return ("-" if neg else "") + f"{s}.{frac:02d}"


def _has(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return len(v.strip()) > 0
    if isinstance(v, (list, tuple, dict)):
        return len(v) > 0
    return True


def _g(obj, attr, default=None):
    return getattr(obj, attr, default)


# Only emit a kv row when the value is actually present.
# NOTE: callers pass values already escaped via _esc(...) (or raw HTML for the
# `raw` cases), so we must NOT escape again here or "&" renders as "&amp;".
def _field(label: str, value: Any, *, raw: bool = False) -> str:
    if not _has(value):
        return ""
    return f'<div class="kv"><span class="kv-l">{_esc(label)}</span><span class="kv-v">{value}</span></div>'


def _kv_block(*fields: str) -> str:
    rows = [f for f in fields if f]
    return f'<div class="kv-grid">{"".join(rows)}</div>' if rows else ""


def _pill(value: Optional[str], *, kind: str = "auto") -> str:
    if not _has(value):
        return ""
    key = str(value).strip().lower()
    cls = "p-amber"
    if kind == "auto":
        if key in ("delivered", "completed", "paid", "approved", "signed", "yes"):
            cls = "p-green"
        elif key in ("partial", "in progress", "pending", "internal review", "medium"):
            cls = "p-amber"
        elif key in ("not delivered", "failed", "rejected", "overdue", "high"):
            cls = "p-red"
        elif key in ("low",):
            cls = "p-green"
        else:
            cls = "p-grey"
    return f'<span class="pill {cls}">{_esc(value)}</span>'


def _band(num: str, kicker: str, title: str, *, kind: str = "") -> str:
    return (
        f'<div class="band {kind}">'
        f'<span class="band-num">{num}</span>'
        '<span class="band-titles">'
        f'<span class="kicker">{_esc(kicker)}</span>'
        f'<span class="band-title">{_esc(title)}</span>'
        "</span></div>"
    )


def _table(headers: list[str], rows: list[list[str]], widths: Optional[list[str]] = None, *, accent: str = "amber") -> str:
    if not rows:
        return ""
    ths = "".join(
        f'<th{f" style=\"width:{widths[i]}\"" if widths and i < len(widths) else ""}>{_esc(h)}</th>'
        for i, h in enumerate(headers)
    )
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="data t-{accent}"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>'


def _cell(v) -> str:
    return _esc(v) if _has(v) else '<span class="dim">—</span>'


# ── sections (each returns "" when it has no data) ──────────────────────────

def _overview(h) -> str:
    body = _kv_block(
        _field("Project Code", _esc(h.project_code)),
        _field("Client", _esc(h.client_organization)),
        _field("Department", _esc(h.department)),
        _field("Project Manager", _esc(h.project_manager)),
        _field("Official Reseller", _esc(_g(h, "system_vendor"))),
        _field("Start Date", _fmt_date(h.start_date)),
        _field("Completion Date", _fmt_date(h.completion_date)),
        _field("Version", _esc(h.version)),
    )
    quote = ""
    if _has(h.project_summary):
        quote = f'<div class="pullquote"><span class="pq-mark">“</span>{_para(h.project_summary)}</div>'
    if not body and not quote:
        return ""
    return ('<section class="sec">' + _band("01", "The Engagement", "Project Overview")
            + body + quote + "</section>")


def _stakeholders(h) -> str:
    items = [s for s in (h.stakeholders or []) if _has(s.name)]
    if not items:
        return ""
    rows = [[f'<b>{_esc(s.name)}</b>', _cell(s.role), _cell(s.organization),
             f'{_esc(s.phone)}{("<br>" + _esc(s.email)) if _has(s.email) else ""}' if _has(s.phone) or _has(s.email) else '<span class="dim">—</span>']
            for s in items]
    return ('<section class="sec">' + _band("02", "People", "Stakeholders & Contacts")
            + _table(["Name", "Role", "Organization", "Reach"], rows, ["26%", "22%", "26%", "26%"]) + "</section>")


def _modules(h) -> str:
    items = [m for m in (h.modules or []) if _has(m.module_name)]
    if not items:
        return ""
    rows = [[f'<b>{_esc(m.module_name)}</b>', _pill(m.status) or '<span class="dim">—</span>',
             _cell(_fmt_date(m.delivery_date)), _cell(m.description)] for m in items]
    return ('<section class="sec">' + _band("03", "Delivered Scope", "Modules & Features")
            + _table(["Module", "Status", "Delivered", "Description"], rows, ["26%", "14%", "16%", "44%"]) + "</section>")


def _architecture(h) -> str:
    body = _kv_block(
        _field("Backend", _esc(h.tech_stack_backend)),
        _field("Frontend", _esc(h.tech_stack_frontend)),
        _field("Database", _esc(h.tech_stack_database)),
        _field("Diagram", f'<span class="mono small">{_esc(h.architecture_diagram_url)}</span>' if _has(h.architecture_diagram_url) else None, raw=True),
    )
    desc = f'<p class="prose">{_para(h.architecture_description)}</p>' if _has(h.architecture_description) else ""
    if not body and not desc:
        return ""
    return ('<section class="sec">' + _band("04", "How It's Built", "Technical Architecture")
            + body + desc + "</section>")


def _infrastructure(h) -> str:
    servers = [s for s in (h.servers or []) if _has(s.server_name)]
    if not servers:
        return ""
    rows = [[f'<b>{_esc(s.server_name)}</b>', f'<span class="mono small">{_cell(s.ip_address)}</span>',
             _cell(s.role), _cell(s.os), _cell(s.location or s.hosting_type)] for s in servers]
    return ('<section class="sec">' + _band("05", "Where It Runs", "Infrastructure")
            + _table(["Server", "IP", "Role", "OS", "Location"], rows, ["24%", "20%", "18%", "18%", "20%"]) + "</section>")


def _assets(h) -> str:
    assets = [a for a in (h.assets or []) if _has(a.asset_name)]
    if not assets:
        return ""
    rows = [[f'<b>{_esc(a.asset_name)}</b>', _cell(a.model), f'<span class="mono small">{_cell(a.serial_number)}</span>',
             _cell(a.quantity), _cell(a.assigned_to), _cell(a.location)] for a in assets]
    return ('<section class="sec">' + _band("06", "Handed Over", "Assets Handover")
            + _table(["Asset", "Model", "Serial", "Qty", "Assigned", "Location"], rows, ["22%", "16%", "18%", "8%", "18%", "18%"]) + "</section>")


def _credentials(h) -> str:
    items = [c for c in (h.credentials or []) if _has(c.system)]
    if not items:
        return ""
    rows = [[f'<b>{_esc(c.system)}</b>', f'<span class="mono small">{_cell(c.username)}</span>',
             _pill(c.access_level, kind="grey") or '<span class="dim">—</span>',
             f'<span class="mono small">{_cell(c.password)}</span>', _cell(c.delivered_to)] for c in items]
    return ('<section class="sec">' + _band("07", "Access & Secrets", "System Credentials")
            + '<div class="note">Confidential — these credentials grant system access. Store this document securely and rotate passwords after handover.</div>'
            + _table(["System", "Username", "Access", "Password", "Delivered To"], rows, ["24%", "24%", "14%", "18%", "20%"]) + "</section>")


_OPS_GROUPS = [
    ("Backup & Recovery", [
        ("Backup Frequency", "backup_frequency"),
        ("Backup Location", "backup_location"),
        ("Backup Type", "backup_type"),
    ]),
    ("Monitoring & Alerting", [
        ("Monitoring Tools", "monitoring_tools"),
        ("Alerting", "alert_system"),
        ("Dashboard", "dashboard_url"),
    ]),
    ("Maintenance & Patching", [
        ("Maintenance Schedule", "maintenance_schedule"),
        ("Patch Management", "patch_management_plan"),
    ]),
]


def _operations(h) -> str:
    groups_html = []
    for group_title, fields in _OPS_GROUPS:
        tiles = [
            f'<div class="ops-tile"><span class="ops-l">{_esc(label)}</span>'
            f'<span class="ops-v">{_esc(_g(h, attr))}</span></div>'
            for label, attr in fields if _has(_g(h, attr))
        ]
        if not tiles:
            continue
        groups_html.append(
            f'<div class="ops-group"><div class="ops-gh">{_esc(group_title)}</div>'
            f'<div class="ops-grid">{"".join(tiles)}</div></div>'
        )
    if not groups_html:
        return ""
    return ('<section class="sec">' + _band("09", "Keeping It Alive", "Operations & Maintenance")
            + "".join(groups_html) + "</section>")


def _documents(h) -> str:
    items = [d for d in (h.documents or []) if _has(d.document_name)]
    if not items:
        return ""
    rows = [[f'<b>{_esc(d.document_name)}</b>', _cell(d.doc_type), _cell(d.version),
             f'<span class="mono small">{_cell(d.link_url)}</span>'] for d in items]
    return ('<section class="sec">' + _band("08", "Knowledge Base", "Documentation")
            + _table(["Document", "Type", "Version", "Link"], rows, ["30%", "22%", "14%", "34%"]) + "</section>")


def _training(h) -> str:
    items = [t for t in (h.training or []) if _has(t.topic)]
    if not items:
        return ""
    rows = [[f'<b>{_esc(t.topic)}</b>', _cell(t.trainer), _cell(_fmt_date(t.training_date)),
             _cell(t.training_mode), _cell(t.participants), _pill(t.completion_status) or '<span class="dim">—</span>'] for t in items]
    return ('<section class="sec">' + _band("11", "Enablement", "Training & Knowledge Transfer")
            + _table(["Topic", "Trainer", "Date", "Mode", "People", "Status"], rows, ["24%", "16%", "14%", "12%", "18%", "16%"]) + "</section>")


def _support(h) -> str:
    # Only render when an SLA is actually linked to the handover.
    if not _has(_g(h, "sla_id")):
        return ""
    stype = _esc(h.support_type)
    start = _fmt_date(h.support_start_date)
    end = _fmt_date(h.support_end_date)
    if not (_has(stype) or _has(start) or _has(end)):
        return ""

    # Hero card — support type + computed coverage term.
    term = _term_label(h.support_start_date, h.support_end_date)
    term_badge = (
        f'<div class="sla-hero-r"><span class="sla-term-v">{_esc(term)}</span>'
        '<span class="sla-term-l">Coverage Term</span></div>'
    ) if term else ""
    hero = (
        '<div class="sla-hero"><div class="sla-hero-l">'
        '<span class="sla-eyebrow">Support Coverage</span>'
        f'<span class="sla-type">{stype or "Active Support"}</span>'
        f'</div>{term_badge}</div>'
    )

    # Coverage timeline — begins → ends.
    nodes = []
    if _has(start):
        nodes.append('<div class="sla-tl-node"><span class="sla-tl-dot"></span>'
                     f'<span class="sla-tl-l">Coverage Begins</span><span class="sla-tl-v">{start}</span></div>')
    if _has(end):
        nodes.append('<div class="sla-tl-node end"><span class="sla-tl-dot"></span>'
                     f'<span class="sla-tl-l">Coverage Ends</span><span class="sla-tl-v">{end}</span></div>')
    timeline = ""
    if len(nodes) == 2:
        timeline = f'<div class="sla-timeline">{nodes[0]}<div class="sla-tl-arrow">&#8594;</div>{nodes[1]}</div>'
    elif nodes:
        timeline = f'<div class="sla-timeline">{nodes[0]}</div>'

    return ('<section class="sec">' + _band("10", "Post Go-Live", "Support & SLA")
            + hero + timeline + "</section>")


def _financial(h) -> str:
    has_value = (h.total_project_value or 0) > 0
    invoices = [i for i in (h.financial_invoices or []) if _has(i.invoice_no) and (i.amount or 0) > 0]
    if not has_value and not invoices:
        return ""
    out = '<section class="sec">' + _band("12", "Commercials", "Financial Closure")
    if has_value:
        cur = _esc(h.currency) or "INR"
        received = h.amount_received or 0
        pending = h.pending_amount if h.pending_amount is not None else ((h.total_project_value or 0) - received)
        split = ""
        if _has(h.amount_received) or _has(h.pending_amount):
            split = ('<div class="fin-split">'
                     f'<div><span>Received</span><b>{cur} {_inr_group(received)}</b></div>'
                     f'<div><span>Pending</span><b>{cur} {_inr_group(pending)}</b></div></div>')
        out += ('<div class="fin">'
                '<div class="fin-l">Total Project Value</div>'
                f'<div class="fin-v"><span>{cur}</span>{_inr_group(h.total_project_value)}</div>'
                f'{split}</div>')
    if invoices:
        rows = [[f'<span class="mono"><b>{_esc(i.invoice_no)}</b></span>', _cell(_fmt_date(i.invoice_date)),
                 f'<span class="mono">{_esc(h.currency) or "INR"} {_inr_group(i.amount)}</span>',
                 _pill(i.status) or '<span class="dim">—</span>'] for i in invoices]
        out += '<div class="subhead">Invoice Ledger</div>' + _table(
            ["Invoice No.", "Date", "Amount", "Status"], rows, ["30%", "24%", "26%", "20%"])
    return out + "</section>"


def _risks(h) -> str:
    items = [i for i in (h.issues or []) if _has(i.issue_desc)]
    if not items:
        return ""
    rows = [[_cell(i.issue_type), f'<b>{_para(i.issue_desc)}</b>', _pill(i.impact, kind="auto") or '<span class="dim">—</span>',
             _cell(i.owner), _cell(i.expected_resolution)] for i in items]
    return ('<section class="sec">' + _band("13", "Open Items", "Residual Risks & Pending")
            + _table(["Type", "Description", "Impact", "Owner", "Resolution"], rows, ["16%", "34%", "12%", "18%", "20%"]) + "</section>")


def _rating_pill(value) -> str:
    if not _has(value):
        return '<span class="dim">Not rated</span>'
    key = str(value).strip().lower()
    cls = "p-amber"
    if key in ("excellent", "outstanding", "good", "very good"):
        cls = "p-green"
    elif key in ("satisfactory", "average", "fair"):
        cls = "p-amber"
    elif key in ("needs improvement", "poor", "unsatisfactory"):
        cls = "p-red"
    return f'<span class="pill {cls}">{_esc(value)}</span>'


_FEEDBACK_CRITERIA = [
    "Quality of installation & commissioning",
    "Adherence to the agreed timelines",
    "Technical competence of the project team",
    "Responsiveness & communication",
    "Training & knowledge transfer",
    "Quality & completeness of documentation",
    "Post go-live support & handholding",
    "Value for money",
    "Overall satisfaction with the delivery",
]
_FEEDBACK_SCALE = ["Excellent", "Good", "Satisfactory", "Needs Work", "Poor"]


def _acceptance(h) -> str:
    """A printable, corporate client feedback & acceptance form (filled by the
    client by hand). Order: Project Overview → Delivered Items → Client Feedback."""
    items = [d for d in (_g(h, "deliverables", []) or []) if _has(d.item_name)]
    modules = [m for m in (h.modules or []) if _has(m.module_name)]
    if not items and not modules:
        return ""
    if not items:  # fall back to delivered scope when the client list wasn't curated
        items = [type("D", (), {"item_name": m.module_name, "category": "Module", "status": m.status})() for m in modules]

    out = '<section class="sec">' + _band("14", "Client Acceptance", "Client Remarks & Acceptance", kind="green")

    # (a) Project overview recap — structured green tiles
    recap_fields = [
        ("Project", _esc(h.project_name)),
        ("Code", _esc(h.project_code)),
        ("Client", _esc(h.client_organization)),
        ("Delivered By", _esc(_g(h, "system_vendor")) or COMPANY["name"]),
        ("Completion Date", _fmt_date(h.completion_date)),
    ]
    recap = "".join(
        f'<div class="acc-tile"><span class="acc-l">{_esc(label)}</span>'
        f'<span class="acc-v">{value}</span></div>'
        for label, value in recap_fields if _has(value)
    )
    out += ('<div class="subhead green-sub">Project Overview</div>'
            f'<div class="acc-grid">{recap}</div>')

    # (b) Delivered items (no per-item remark — kept concise)
    rows = [[f'<b>{_esc(d.item_name)}</b>', _cell(d.category), _pill(d.status) or '<span class="dim">—</span>'] for d in items]
    out += '<div class="subhead green-sub">Delivered Items</div>' + _table(
        ["Delivered Item", "Category", "Status"], rows, ["56%", "24%", "20%"], accent="green")

    # (c) Client feedback & satisfaction survey — completed by the client, by hand
    head = "".join(f'<th class="rate-col">{_esc(s)}</th>' for s in _FEEDBACK_SCALE)
    ticks = "".join('<td class="rate-col"><span class="tick"></span></td>' for _ in _FEEDBACK_SCALE)
    body = "".join(f'<tr><td class="crit">{_esc(c)}</td>{ticks}</tr>' for c in _FEEDBACK_CRITERIA)

    # Generous lined writing areas for free-text remarks.
    def _open_q(label: str) -> str:
        lines = "".join('<div class="rule"></div>' for _ in range(4))
        return (f'<div class="open-q"><div class="oq-l">{_esc(label)}</div>'
                f'<div class="oq-box">{lines}</div></div>')

    out += (
        '<div class="subhead green-sub">Client Feedback &amp; Satisfaction Survey</div>'
        '<p class="form-note">To be completed by the client. Please tick (✓) the box that best reflects your experience.</p>'
        f'<table class="matrix"><thead><tr><th>Assessment Criterion</th>{head}</tr></thead><tbody>{body}</tbody></table>'
        '<div class="recommend"><span class="rq">Would you recommend us to others?</span>'
        '<span class="opt"><span class="box"></span> Yes</span>'
        '<span class="opt"><span class="box"></span> No</span>'
        '<span class="opt"><span class="box"></span> Maybe</span></div>'
        '<div class="subhead green-sub">In Your Own Words</div>'
        + _open_q("What did we do well?")
        + _open_q("What could we improve?")
        + _open_q("Additional comments")
        + '<div class="accept-box">'
        '<div class="accept-txt">I confirm that the deliverables listed above have been received, inspected and accepted, '
        'and that the feedback provided reflects our experience of this engagement.</div>'
        '<div class="accept-sig">'
        '<div class="sg"><div class="sg-line"></div><span>Client Name</span></div>'
        '<div class="sg"><div class="sg-line"></div><span>Designation</span></div>'
        '<div class="sg"><div class="sg-line"></div><span>Signature</span></div>'
        '<div class="sg"><div class="sg-line"></div><span>Date</span></div>'
        '</div></div>'
    )
    return out + "</section>"


def _signoff(h) -> str:
    items = [a for a in (h.approvals or []) if _has(a.name)][:4]
    if not items:
        return ""
    cards = []
    for a in items:
        if a.has_signed:
            mark = ('<div class="sig-ok"><span class="sig-dot"></span>'
                    f'Digitally verified{(" · " + _fmt_date(a.signature_date)) if _has(a.signature_date) else ""}</div>')
        else:
            mark = '<div class="sig-pend">Pending formal signature</div>'
        cards.append(
            '<div class="sig-card">'
            f'<div class="sig-role">{_esc(a.party) or "Authorizing Party"}</div>'
            f'<div class="sig-name">{_esc(a.name)}</div>'
            f'<div class="sig-title">{_esc(a.designation)}</div>'
            f"{mark}<div class='sig-line'></div><div class='sig-sub'>Signature</div></div>"
        )
    return ('<section class="sec">' + _band("15", "Execution", "Digital Sign-off")
            + '<p class="prose">The undersigned acknowledge completion and formal handover of the project deliverables, '
            'accepting the scope, assets and conditions documented herein.</p>'
            + f'<div class="sig-grid">{"".join(cards)}</div></section>')


# ── contents ────────────────────────────────────────────────────────────────

def _contents(sections: list[tuple[str, str]]) -> str:
    if not sections:
        return ""
    rows = "".join(
        f'<div class="toc-row"><span class="toc-num">{num}</span><span class="toc-title">{_esc(title)}</span><span class="toc-dot"></span></div>'
        for num, title in sections
    )
    return ('<section class="contents">'
            '<div class="toc-head"><span class="kicker">In this document</span><h2>Contents</h2></div>'
            f'<div class="toc-list">{rows}</div>'
            '<div class="toc-word">CLOSURE<br>REPORT</div>'
            "</section>")


# ── cover ───────────────────────────────────────────────────────────────────

def _cover(h) -> str:
    title = _esc(h.project_name) or "Project Handover"
    status = (_esc(h.status) or "Draft").upper()
    facts = []
    if _has(h.project_code):
        facts.append(("Project Code", _esc(h.project_code)))
    if _has(h.completion_date):
        facts.append(("Completed", _fmt_long_date(h.completion_date)))
    if _has(_g(h, "system_vendor")):
        facts.append(("Official Reseller", _esc(h.system_vendor)))
    if (h.total_project_value or 0) > 0:
        facts.append(("Project Value", f"{_esc(h.currency) or 'INR'} {_inr_group(h.total_project_value)}"))
    facts.append(("Status", status))
    facts_html = "".join(
        f'<div class="cf-row"><div class="cf-l">{_esc(l)}</div><div class="cf-v">{v}</div></div>'
        for l, v in facts
    )
    return (
        '<section class="cover">'
        '<div class="cover-block">'
        '<div class="cover-top">'
        f'<div class="cv-kicker">{COMPANY["tagline"]}</div>'
        f'{_LOGO_SVG}'
        "</div>"
        '<div class="cover-ghost">HANDOVER</div>'
        f'<h1 class="cover-title">{title}</h1>'
        f'<div class="cover-client">{_esc(h.client_organization) or ""}</div>'
        "</div>"
        '<div class="cover-foot">'
        f'<div class="cover-facts">{facts_html}</div>'
        f'<div class="cover-by">Prepared by <b>{COMPANY["name"]}</b>{(" · " + _esc(h.system_vendor)) if _has(_g(h, "system_vendor")) else ""}</div>'
        "</div>"
        "</section>"
    )


# ── stylesheet (LIGHT magazine) ──────────────────────────────────────────────

_CSS = """
@page {
    size: A4; margin: 18mm 15mm 16mm;
    @bottom-left { content: "Fourreck · Project Handover"; font-family: 'Consolas','Courier New',monospace; font-size: 7pt; color: #b8a890; }
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font-family: 'Consolas','Courier New',monospace; font-size: 7pt; color: #b8a890; letter-spacing: .5pt; }
}
@page cover { margin: 0; @bottom-left { content: ""; } @bottom-right { content: ""; } }
@page nochrome { @bottom-left { content: ""; } @bottom-right { content: ""; } }

* { box-sizing: border-box; }
body { margin: 0; font-family: 'Segoe UI','Helvetica Neue',Arial,sans-serif; color: #241c12; font-size: 9.5pt; line-height: 1.6; background: #fff; }
.mono { font-family: 'Consolas','Courier New',monospace; }
.small { font-size: 8pt; }
.dim { color: #b6a88f; }

/* ── COVER (bright) ── */
.cover { page: cover; width: 210mm; height: 297mm; display: flex; flex-direction: column; background: #fff; }
.cover-block { position: relative; flex: 1; padding: 20mm 18mm 14mm;
    background: linear-gradient(150deg, #fb923c 0%, #f97316 45%, #ea580c 100%); color: #fff; overflow: hidden; }
.cover-top { display: flex; justify-content: space-between; align-items: flex-start; position: relative; z-index: 2; }
.cv-kicker { font-family: 'Consolas','Courier New',monospace; font-size: 8pt; letter-spacing: 3pt; text-transform: uppercase; color: rgba(255,255,255,0.9); }
.cv-mark { width: 13mm; height: 13mm; border-radius: 3mm; background: #1a1208; display: flex; align-items: center; justify-content: center; }
.cv-mark span { color: #fb923c; font-weight: 900; font-size: 18pt; }
.cv-logo { width: 18mm; height: auto; filter: drop-shadow(0 1mm 3mm rgba(120,40,0,0.25)); }
.cover-ghost { position: absolute; right: -6mm; bottom: 30mm; font-size: 78pt; font-weight: 900; letter-spacing: -2pt;
    color: rgba(255,255,255,0.12); text-transform: uppercase; line-height: 1; }
.cover-title { position: relative; z-index: 2; margin: 40mm 0 0; font-size: 50pt; font-weight: 900; line-height: 0.98;
    letter-spacing: -2pt; text-transform: uppercase; max-width: 165mm; text-shadow: 0 2mm 8mm rgba(120,40,0,0.18); }
.cover-client { position: relative; z-index: 2; margin-top: 6mm; font-size: 13pt; font-weight: 600; color: rgba(255,255,255,0.92); }
.cover-foot { padding: 12mm 18mm 16mm; background: #fff; }
.cover-facts { display: flex; flex-wrap: wrap; gap: 0; border-top: 2pt solid #1a1208; }
.cf-row { width: 50%; display: flex; gap: 6mm; padding: 4mm 0; border-bottom: 0.4pt solid #efe4d4; }
.cf-row:nth-child(odd) { padding-right: 8mm; }
.cf-l { width: 36mm; flex-shrink: 0; font-family: 'Consolas','Courier New',monospace; font-size: 7.5pt; letter-spacing: 1.5pt; text-transform: uppercase; color: #c2410c; padding-top: 1pt; }
.cf-v { font-size: 11pt; font-weight: 700; color: #1a1208; }
.cover-by { margin-top: 8mm; font-size: 9pt; color: #8a7a60; }
.cover-by b { color: #c2410c; }

/* ── CONTENTS ── */
.contents { page: nochrome; position: relative; padding-top: 6mm; min-height: 250mm; }
.toc-head .kicker { display: block; }
.toc-head h2 { font-size: 40pt; font-weight: 900; letter-spacing: -1.5pt; margin: 1mm 0 10mm; color: #1a1208; text-transform: uppercase; }
.toc-list { max-width: 150mm; }
.toc-row { display: flex; align-items: baseline; gap: 5mm; padding: 4mm 0; border-bottom: 0.5pt solid #efe4d4; }
.toc-num { font-family: 'Consolas','Courier New',monospace; font-size: 11pt; font-weight: 700; color: #f97316; width: 12mm; }
.toc-title { font-size: 13pt; font-weight: 600; color: #241c12; }
.toc-word { position: absolute; right: 0; bottom: 24mm; font-size: 60pt; font-weight: 900; line-height: 0.9; text-align: right;
    color: #fff4ea; letter-spacing: -2pt; text-transform: uppercase; z-index: -1; }

/* ── SECTION HEADER BAND ── */
.sec { break-inside: auto; }
.band { display: flex; align-items: center; gap: 5mm; margin: 12mm 0 5mm; padding-bottom: 3mm;
    border-bottom: 2pt solid #1a1208; break-after: avoid; }
.sec:first-of-type .band { margin-top: 0; }
.band-num { font-size: 30pt; font-weight: 900; color: #f97316; line-height: 0.8; letter-spacing: -1pt; }
.band.green .band-num { color: #16a34a; }
.band.green { border-bottom-color: #16a34a; }
.band-titles { display: flex; flex-direction: column; }
.kicker { font-family: 'Consolas','Courier New',monospace; font-size: 7.5pt; letter-spacing: 2.2pt; text-transform: uppercase; color: #c2410c; }
.band.green .kicker { color: #15803d; }
.band-title { font-size: 17pt; font-weight: 800; letter-spacing: -0.5pt; color: #1a1208; }
.subhead { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; letter-spacing: 1.8pt; text-transform: uppercase; color: #c2410c; margin: 5mm 0 0; }
.subhead.green-sub { color: #15803d; }

/* Operations & Maintenance — grouped structured tiles (orange) */
.ops-group { margin: 4mm 0 0; break-inside: avoid; }
.ops-gh { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; letter-spacing: 1.6pt; text-transform: uppercase;
    color: #9a5a12; padding-bottom: 1.5mm; margin-bottom: 3mm; border-bottom: 0.5pt solid #fde3c6; }
.ops-grid { display: flex; flex-wrap: wrap; gap: 3.5mm; }
.ops-tile { flex: 1 1 calc(33.333% - 3.5mm); min-width: 48mm; padding: 3.5mm 4.5mm; background: #fff7ed;
    border: 0.5pt solid #fde3c6; border-left: 2.4pt solid #f97316; border-radius: 0 2.5mm 2.5mm 0; break-inside: avoid; }
.ops-l { display: block; font-family: 'Consolas','Courier New',monospace; font-size: 6.4pt; letter-spacing: 1.4pt;
    text-transform: uppercase; color: #c2410c; margin-bottom: 1.6mm; }
.ops-v { display: block; font-size: 10.5pt; font-weight: 700; color: #1a1208; line-height: 1.35; word-wrap: break-word; }

/* Support & SLA — modern coverage hero + timeline (orange) */
.sla-hero { display: flex; align-items: center; justify-content: space-between; margin: 4mm 0 0;
    padding: 6mm 7mm; border-radius: 3mm; color: #fff; break-inside: avoid;
    background: linear-gradient(135deg, #fb923c, #ea580c); box-shadow: 0 2mm 6mm rgba(234,88,12,0.18); }
.sla-hero-l { flex: 1; }
.sla-eyebrow { display: block; font-family: 'Consolas','Courier New',monospace; font-size: 7pt; letter-spacing: 2.5pt;
    text-transform: uppercase; color: rgba(255,255,255,0.85); margin-bottom: 2mm; }
.sla-type { display: block; font-size: 19pt; font-weight: 900; letter-spacing: -0.5pt; line-height: 1.05; }
.sla-hero-r { text-align: right; padding-left: 6mm; margin-left: 6mm; border-left: 0.5pt solid rgba(255,255,255,0.35); flex-shrink: 0; }
.sla-term-v { display: block; font-size: 16pt; font-weight: 900; line-height: 1; }
.sla-term-l { display: block; font-family: 'Consolas','Courier New',monospace; font-size: 6.4pt; letter-spacing: 1.4pt;
    text-transform: uppercase; color: rgba(255,255,255,0.85); margin-top: 1.8mm; }
.sla-timeline { display: flex; align-items: stretch; margin: 3.5mm 0 1mm; break-inside: avoid; }
.sla-tl-node { flex: 1; padding: 4mm 5mm; background: #fff7ed; border: 0.5pt solid #fde3c6;
    border-left: 2.4pt solid #f97316; border-radius: 0 2.5mm 2.5mm 0; }
.sla-tl-node.end { text-align: right; border-left: 0.5pt solid #fde3c6; border-right: 2.4pt solid #f97316; border-radius: 2.5mm 0 0 2.5mm; }
.sla-tl-dot { display: inline-block; width: 2.4mm; height: 2.4mm; border-radius: 50%; background: #f97316; margin-bottom: 2mm; }
.sla-tl-l { display: block; font-family: 'Consolas','Courier New',monospace; font-size: 6.4pt; letter-spacing: 1.4pt;
    text-transform: uppercase; color: #c2410c; margin-bottom: 1.6mm; }
.sla-tl-v { display: block; font-size: 11pt; font-weight: 700; color: #1a1208; }
.sla-tl-arrow { align-self: center; flex: 0 0 12mm; text-align: center; font-size: 15pt; font-weight: 900; color: #f97316; }

/* Client acceptance — printable feedback & satisfaction form (filled by hand) */
.form-note { font-size: 8.5pt; color: #5a6b5f; margin: 1.5mm 0 3mm; }
table.matrix { width: 100%; border-collapse: collapse; margin: 1mm 0 4mm; break-inside: avoid; }
table.matrix th { font-size: 6.8pt; font-weight: 800; text-transform: uppercase; letter-spacing: .4pt;
    padding: 2.5mm 1.5mm; color: #15803d; background: #eafaf0; border: 0.4pt solid #cdebd5; text-align: center; }
table.matrix th:first-child { text-align: left; }
table.matrix td { font-size: 8.4pt; padding: 2.6mm 2mm; border: 0.4pt solid #e4f1e8; color: #2b3b30; }
table.matrix td.crit { font-weight: 600; }
table.matrix td.rate-col { text-align: center; width: 13%; }
table.matrix tbody tr:nth-child(even) { background: #f6fcf8; }
.matrix .tick { display: inline-block; width: 3.6mm; height: 3.6mm; border: 0.6pt solid #9cbfa6; border-radius: 50%; }

/* Acceptance — green project-overview recap tiles */
.acc-grid { display: flex; flex-wrap: wrap; gap: 3.5mm; margin: 4mm 0 1mm; }
.acc-tile { flex: 1 1 calc(33.333% - 3.5mm); min-width: 48mm; padding: 3.5mm 4.5mm; background: #f4fbf6;
    border: 0.5pt solid #cdebd5; border-left: 2.4pt solid #16a34a; border-radius: 0 2.5mm 2.5mm 0; break-inside: avoid; }
.acc-l { display: block; font-family: 'Consolas','Courier New',monospace; font-size: 6.4pt; letter-spacing: 1.4pt;
    text-transform: uppercase; color: #15803d; margin-bottom: 1.6mm; }
.acc-v { display: block; font-size: 10pt; font-weight: 700; color: #14271a; line-height: 1.35; word-wrap: break-word; }

.recommend { display: flex; align-items: center; gap: 7mm; margin: 4mm 0 5mm; padding: 3mm 4mm; font-size: 9pt;
    background: #f4fbf6; border: 0.5pt solid #cdebd5; border-radius: 2.5mm; }
.recommend .rq { font-weight: 700; color: #1a3a24; }
.recommend .opt { display: inline-flex; align-items: center; gap: 2mm; color: #2b3b30; }
.recommend .box { display: inline-block; width: 3.4mm; height: 3.4mm; border: 0.6pt solid #9cbfa6; border-radius: 1mm; }

/* Generous free-text writing areas */
.open-q { margin: 0 0 5mm; break-inside: avoid; }
.oq-l { font-size: 9pt; font-weight: 800; color: #15803d; margin-bottom: 4mm; }
.oq-box { padding-top: 1mm; }
.oq-box .rule { border-bottom: 0.5pt dashed #c4ddca; margin-bottom: 6.5mm; }
.accept-box { margin-top: 3mm; padding: 5mm; border: 0.5pt solid #cdebd5; border-radius: 3mm; background: #f4fbf6; break-inside: avoid; }
.accept-txt { font-size: 8.8pt; color: #2f5236; margin-bottom: 9mm; line-height: 1.5; }
.accept-sig { display: flex; gap: 7mm; }
.sg { flex: 1; }
.sg-line { border-bottom: 0.6pt solid #8aa893; height: 7mm; }
.sg span { font-size: 7.2pt; color: #5a6b5f; text-transform: uppercase; letter-spacing: 1pt; }

.prose { margin: 0 0 4mm; color: #3a2f22; text-align: justify; }
.pullquote { position: relative; margin: 4mm 0 0; padding: 5mm 6mm 5mm 12mm; background: #fff7ed; border-left: 2.5pt solid #f97316;
    border-radius: 0 3mm 3mm 0; font-size: 11.5pt; font-weight: 500; color: #5a4327; font-style: italic; }
.pullquote.green { background: #effaf1; border-left-color: #16a34a; color: #2f5236; }
.pq-mark { position: absolute; left: 4mm; top: 2mm; font-size: 28pt; font-weight: 900; color: #f9b27a; font-style: normal; }
.pullquote.green .pq-mark { color: #86d99a; }
.pq-by { display: block; margin-top: 3mm; font-size: 8.5pt; font-style: normal; font-weight: 700; color: #15803d; letter-spacing: .3pt; }
.note { margin: 1mm 0 3mm; padding: 2.5mm 4mm; border-radius: 2mm; background: #fff7ed; color: #9a5a12; font-size: 8pt; border: 0.4pt solid #fde3c6; }

/* kv */
.kv-grid { display: flex; flex-wrap: wrap; gap: 3.5mm 12mm; margin-bottom: 1mm; }
.kv { display: flex; flex-direction: column; gap: 0.5mm; min-width: 58mm; }
.kv-l { font-family: 'Consolas','Courier New',monospace; font-size: 6.6pt; letter-spacing: 1.5pt; text-transform: uppercase; color: #b07a3a; }
.kv-v { font-size: 9.5pt; color: #1a1208; font-weight: 600; }

/* tables */
table.data { width: 100%; border-collapse: collapse; margin: 3mm 0 2mm; }
table.data th { font-size: 7.2pt; font-weight: 800; letter-spacing: 1pt; text-transform: uppercase; text-align: left; padding: 3mm 3.5mm; color: #fff; }
table.t-amber thead tr { background: linear-gradient(90deg, #fb923c, #ea580c); }
table.t-green thead tr { background: linear-gradient(90deg, #34d399, #16a34a); }
table.data td { padding: 2.8mm 3.5mm; font-size: 8.6pt; border-bottom: 0.4pt solid #f0e6d6; vertical-align: top; color: #3a2f22; }
table.data tbody tr:nth-child(even) { background: #fdf8f1; }
table.t-green tbody tr:nth-child(even) { background: #f1faf3; }
table.data b { color: #1a1208; }

/* pills */
.pill { display: inline-block; padding: 0.8mm 2.6mm; border-radius: 4mm; font-size: 7.6pt; font-weight: 800; letter-spacing: .3pt; }
.p-green { background: #dcfce7; color: #15803d; }
.p-amber { background: #ffedd5; color: #c2410c; }
.p-red { background: #fee2e2; color: #b91c1c; }
.p-grey { background: #eee6d8; color: #6b5d47; }

/* finance — bright orange band (not dark) */
.fin { padding: 6mm 7mm; border-radius: 3mm; color: #fff; break-inside: avoid;
    background: linear-gradient(135deg, #fb923c, #ea580c); box-shadow: 0 2mm 6mm rgba(234,88,12,0.18); }
.fin-l { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; letter-spacing: 2.5pt; text-transform: uppercase; color: rgba(255,255,255,0.85); margin-bottom: 2mm; }
.fin-v { font-size: 28pt; font-weight: 900; letter-spacing: -1pt; }
.fin-v span { font-size: 12pt; font-weight: 700; color: rgba(255,255,255,0.85); margin-right: 3mm; vertical-align: 5pt; }
.fin-split { display: flex; gap: 14mm; margin-top: 4mm; padding-top: 4mm; border-top: 0.5pt solid rgba(255,255,255,0.3); }
.fin-split span { font-family: 'Consolas','Courier New',monospace; font-size: 6.6pt; letter-spacing: 1.5pt; text-transform: uppercase; color: rgba(255,255,255,0.85); display: block; }
.fin-split b { font-size: 12pt; }

/* sign-off (light cards) */
.sig-grid { display: flex; flex-wrap: wrap; gap: 5mm; margin-top: 4mm; }
.sig-card { flex: 1; min-width: 70mm; padding: 5mm; border: 0.5pt solid #f0e0c8; border-radius: 3mm; background: #fffaf3; break-inside: avoid; }
.sig-role { font-family: 'Consolas','Courier New',monospace; font-size: 6.6pt; letter-spacing: 1.5pt; text-transform: uppercase; color: #c2410c; margin-bottom: 2mm; }
.sig-name { font-size: 12pt; font-weight: 800; color: #1a1208; }
.sig-title { font-size: 8.5pt; color: #b07a3a; font-weight: 600; margin-bottom: 4mm; }
.sig-ok { font-family: 'Consolas','Courier New',monospace; font-size: 7pt; color: #15803d; font-weight: 700; }
.sig-dot { display: inline-block; width: 2mm; height: 2mm; border-radius: 50%; background: #16a34a; margin-right: 1.5mm; }
.sig-pend { font-size: 8.5pt; font-style: italic; color: #b6a88f; }
.sig-line { height: 0.5pt; background: #d8c4a4; margin: 8mm 0 1.5mm; }
.sig-sub { font-size: 7.5pt; color: #b07a3a; }
"""


# ── public entry ─────────────────────────────────────────────────────────────

def render_handover_pdf(h) -> bytes:
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433 (lazy — needs GTK on PATH)

    builders = [
        ("01", "Project Overview", _overview),
        ("02", "Stakeholders & Contacts", _stakeholders),
        ("03", "Modules & Features", _modules),
        ("04", "Technical Architecture", _architecture),
        ("05", "Infrastructure", _infrastructure),
        ("06", "Assets Handover", _assets),
        ("07", "System Credentials", _credentials),
        ("08", "Documentation", _documents),
        ("09", "Operations & Maintenance", _operations),
        ("10", "Support & SLA", _support),
        ("11", "Training & Knowledge Transfer", _training),
        ("12", "Financial Closure", _financial),
        ("13", "Residual Risks & Pending", _risks),
        ("14", "Client Remarks & Acceptance", _acceptance),
        ("15", "Digital Sign-off", _signoff),
    ]
    rendered = [(num, title, fn(h)) for num, title, fn in builders]
    present = [(num, title, html_) for num, title, html_ in rendered if html_]
    toc = [(num, title) for num, title, _ in present]

    body = (
        _cover(h)
        + _contents(toc)
        + '<main class="doc">'
        + "".join(html_ for _, _, html_ in present)
        + "</main>"
    )
    doc = ('<!doctype html><html><head><meta charset="utf-8">'
           f"<style>{_CSS}</style></head><body>{body}</body></html>")
    return HTML(string=doc).write_pdf()
