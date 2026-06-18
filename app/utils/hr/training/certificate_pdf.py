"""WeasyPrint certificate PDFs for HR Training & Development.

One credential, one bespoke certificate — the *visual identity changes with the
certification category*. Seven categories, seven motifs (emblem, palette, border
treatment, guilloché watermark, eyebrow label):

    TECHNICAL     → circuit medallion, double-rule frame, amber
    FUNCTIONAL    → cog rosette, beaded frame, gold-amber
    BEHAVIORAL    → wave crest, soft ribbon frame, ember
    DOMAIN        → meridian globe, compass frame, deep orange
    LANGUAGE      → quill flourish, script frame, antique gold
    CERTIFICATION → laurel + medal, classic double frame, emerald
    OTHER         → concentric rings, minimalist frame, stone

A4 landscape, full-bleed. All units in mm / pt so it prints crisp on any device.

Public entry: ``render_certificate_pdf(ec, holder, category) -> bytes``
    * ``ec``       — the EmployeeCertification ORM row (name, dates, status, …)
    * ``holder``   — emp_display() dict: {name, code, dept, desg}
    * ``category`` — the linked catalog category (or None → OTHER)

WeasyPrint is imported lazily (it shells out to GTK at import time) — see CLAUDE.md.
"""
from __future__ import annotations

import html
from datetime import date, datetime
from typing import Any, Optional


COMPANY = {
    "name": "Fourreck",
    "legal": "Fourreck Technologies Pvt. Ltd.",
    "web": "fourreck.com",
    "address": "Innovation Tower, Hyderabad, Telangana, India",
}


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════

def _esc(v: Any) -> str:
    return "" if v is None else html.escape(str(v))


def _fmt_date(d) -> str:
    if not d:
        return "—"
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except ValueError:
            return d
    return d.strftime("%d %B %Y")


def _days_to(expiry: Optional[date]) -> Optional[int]:
    if not expiry:
        return None
    if isinstance(expiry, datetime):
        expiry = expiry.date()
    return (expiry - date.today()).days


STATUS_LABELS = {
    "ACTIVE": "Active", "EXPIRING_SOON": "Expiring soon", "EXPIRED": "Expired",
    "REVOKED": "Revoked", "PENDING_RENEWAL": "Pending renewal",
}
STATUS_COLORS = {
    "ACTIVE": "#047857", "EXPIRING_SOON": "#b45309", "EXPIRED": "#b91c1c",
    "REVOKED": "#57534e", "PENDING_RENEWAL": "#c2410c",
}


# ════════════════════════════════════════════════════════════════════════════
# Per-category themes
# ════════════════════════════════════════════════════════════════════════════

CERT_THEMES = {
    "TECHNICAL": {
        "label": "Technical Proficiency", "emblem": "circuit", "frame": "double",
        "accent": "#b45309", "deep": "#78350f", "soft": "#fdf6ec", "paper2": "#fbedd6",
        "tagline": "engineered competence, verified",
    },
    "FUNCTIONAL": {
        "label": "Functional Excellence", "emblem": "cog", "frame": "beaded",
        "accent": "#d97706", "deep": "#92400e", "soft": "#fdf7ec", "paper2": "#fcefd4",
        "tagline": "operational mastery, recognised",
    },
    "BEHAVIORAL": {
        "label": "Behavioural Distinction", "emblem": "wave", "frame": "ribbon",
        "accent": "#c2410c", "deep": "#7c2d12", "soft": "#fdf3ec", "paper2": "#fbe6d6",
        "tagline": "human craft, demonstrated",
    },
    "DOMAIN": {
        "label": "Domain Authority", "emblem": "globe", "frame": "compass",
        "accent": "#ea580c", "deep": "#9a3412", "soft": "#fdf4ee", "paper2": "#fce4d6",
        "tagline": "deep expertise, attested",
    },
    "LANGUAGE": {
        "label": "Language Command", "emblem": "quill", "frame": "script",
        "accent": "#a16207", "deep": "#713f12", "soft": "#fdfaef", "paper2": "#f7eccb",
        "tagline": "eloquence, certified",
    },
    "CERTIFICATION": {
        "label": "Professional Certification", "emblem": "laurel", "frame": "classic",
        "accent": "#047857", "deep": "#064e3b", "soft": "#eefaf3", "paper2": "#d8f0e3",
        "tagline": "credential of distinction",
    },
    "OTHER": {
        "label": "Credential of Achievement", "emblem": "rings", "frame": "minimal",
        "accent": "#9a6a31", "deep": "#57391c", "soft": "#fbf7f0", "paper2": "#f1e6d4",
        "tagline": "achievement, acknowledged",
    },
}


def _theme(category: Optional[str]) -> dict:
    return CERT_THEMES.get((category or "OTHER").upper(), CERT_THEMES["OTHER"])


# ── emblem medallions (viewBox 0 0 100 100, currentColor strokes) ────────────

def _emblem_inner(slug: str) -> str:
    if slug == "circuit":
        return """
        <circle cx="50" cy="50" r="20" fill="none" stroke="currentColor" stroke-width="1.6"/>
        <path d="M50 18 V30 M50 70 V82 M18 50 H30 M70 50 H82 M28 28 L37 37 M72 28 L63 37 M28 72 L37 63 M72 72 L63 63"
              stroke="currentColor" stroke-width="1.6" fill="none"/>
        <circle cx="50" cy="18" r="3" fill="currentColor"/><circle cx="50" cy="82" r="3" fill="currentColor"/>
        <circle cx="18" cy="50" r="3" fill="currentColor"/><circle cx="82" cy="50" r="3" fill="currentColor"/>
        <circle cx="50" cy="50" r="7" fill="currentColor"/>"""
    if slug == "cog":
        teeth = "".join(
            f'<rect x="48.4" y="6" width="3.2" height="11" rx="1" transform="rotate({a} 50 50)" fill="currentColor"/>'
            for a in range(0, 360, 30))
        return teeth + """
        <circle cx="50" cy="50" r="26" fill="none" stroke="currentColor" stroke-width="2.4"/>
        <circle cx="50" cy="50" r="11" fill="none" stroke="currentColor" stroke-width="2.4"/>
        <circle cx="50" cy="50" r="3.4" fill="currentColor"/>"""
    if slug == "wave":
        return """
        <circle cx="50" cy="50" r="30" fill="none" stroke="currentColor" stroke-width="1.4"/>
        <path d="M22 52 Q31 40 40 52 T58 52 T76 52" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
        <path d="M24 62 Q33 51 42 62 T60 62 T76 62" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" opacity="0.6"/>
        <path d="M28 41 Q37 31 46 41 T62 41" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" opacity="0.45"/>"""
    if slug == "globe":
        return """
        <circle cx="50" cy="50" r="28" fill="none" stroke="currentColor" stroke-width="1.8"/>
        <ellipse cx="50" cy="50" rx="11" ry="28" fill="none" stroke="currentColor" stroke-width="1.2"/>
        <ellipse cx="50" cy="50" rx="22" ry="28" fill="none" stroke="currentColor" stroke-width="1.2"/>
        <line x1="22" y1="50" x2="78" y2="50" stroke="currentColor" stroke-width="1.2"/>
        <line x1="27" y1="36" x2="73" y2="36" stroke="currentColor" stroke-width="1"/>
        <line x1="27" y1="64" x2="73" y2="64" stroke="currentColor" stroke-width="1"/>"""
    if slug == "quill":
        return """
        <circle cx="50" cy="50" r="29" fill="none" stroke="currentColor" stroke-width="1.2"/>
        <path d="M34 68 C50 64 70 40 72 28 C58 32 40 46 34 68 Z" fill="none" stroke="currentColor" stroke-width="2"/>
        <path d="M40 60 C50 52 60 42 66 34" fill="none" stroke="currentColor" stroke-width="1.2"/>
        <path d="M30 70 L40 60" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>"""
    if slug == "laurel":
        return """
        <path d="M50 20 L53 30 L63 30 L55 36 L58 46 L50 40 L42 46 L45 36 L37 30 L47 30 Z" fill="currentColor"/>
        <path d="M34 70 C24 60 24 44 32 34 C30 48 34 60 42 66 Z" fill="currentColor" opacity="0.92"/>
        <path d="M66 70 C76 60 76 44 68 34 C70 48 66 60 58 66 Z" fill="currentColor" opacity="0.92"/>
        <circle cx="50" cy="58" r="9" fill="none" stroke="currentColor" stroke-width="2"/>
        <path d="M44 66 L41 78 L50 73 L59 78 L56 66" fill="currentColor"/>"""
    # rings (OTHER)
    return """
    <circle cx="50" cy="50" r="30" fill="none" stroke="currentColor" stroke-width="1.4"/>
    <circle cx="50" cy="50" r="21" fill="none" stroke="currentColor" stroke-width="1.8"/>
    <circle cx="50" cy="50" r="11" fill="none" stroke="currentColor" stroke-width="2.4"/>
    <circle cx="50" cy="50" r="3.4" fill="currentColor"/>"""


def _seal_svg(theme: dict, size_mm: float = 34, opacity: float = 1.0) -> str:
    inner = _emblem_inner(theme["emblem"])
    return f"""
    <svg viewBox="0 0 100 100" style="width:{size_mm}mm;height:{size_mm}mm;color:{theme['accent']};opacity:{opacity}">
      <circle cx="50" cy="50" r="47" fill="none" stroke="{theme['accent']}" stroke-width="1" opacity="0.55"/>
      <circle cx="50" cy="50" r="43" fill="none" stroke="{theme['accent']}" stroke-width="2.5" stroke-dasharray="1 3"/>
      {inner}
    </svg>"""


# ── frame border treatment per category ─────────────────────────────────────

def _frame_css(theme: dict) -> str:
    a, d = theme["accent"], theme["deep"]
    f = theme["frame"]
    if f == "double":
        return f"border: 1.4mm solid {a}; box-shadow: inset 0 0 0 0.5mm {theme['soft']}, inset 0 0 0 1.1mm {d};"
    if f == "beaded":
        return f"border: 0.9mm solid {a}; box-shadow: inset 0 0 0 1.2mm {theme['soft']}, inset 0 0 0 1.5mm {a}; border-radius: 2mm;"
    if f == "ribbon":
        return f"border: 1.1mm solid {a}; box-shadow: inset 0 0 0 2.2mm {theme['soft']}, inset 0 0 0 2.5mm {d}; border-radius: 4mm;"
    if f == "compass":
        return f"border: 1mm double {a}; box-shadow: inset 0 0 0 1mm {theme['soft']}, inset 0 0 0 1.4mm {a};"
    if f == "script":
        return f"border: 0.6mm solid {d}; box-shadow: inset 0 0 0 0.8mm {theme['soft']}, inset 0 0 0 1.4mm {a}, inset 0 0 0 1.6mm {theme['soft']}, inset 0 0 0 2mm {d};"
    if f == "classic":
        return f"border: 1.6mm double {a}; box-shadow: inset 0 0 0 1.2mm {theme['soft']}, inset 0 0 0 1.6mm {d};"
    return f"border: 0.7mm solid {a};"  # minimal


def _corner(theme: dict, rotate: int) -> str:
    a = theme["accent"]
    return f"""
    <svg viewBox="0 0 60 60" class="corner" style="transform:rotate({rotate}deg);color:{a}">
      <path d="M4 28 L4 8 Q4 4 8 4 L28 4" fill="none" stroke="{a}" stroke-width="1.6"/>
      <path d="M11 22 L11 13 Q11 11 13 11 L22 11" fill="none" stroke="{a}" stroke-width="1" opacity="0.6"/>
      <circle cx="8" cy="8" r="2.2" fill="{a}"/>
    </svg>"""


# ════════════════════════════════════════════════════════════════════════════
# Renderer
# ════════════════════════════════════════════════════════════════════════════

def render_certificate_pdf(ec, holder: dict, category: Optional[str]) -> bytes:
    """Render a single-page A4-landscape certificate as PDF bytes."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433

    theme = _theme(category)
    a, d, soft, paper2 = theme["accent"], theme["deep"], theme["soft"], theme["paper2"]

    name = _esc(ec.name or "Credential")
    authority = _esc(ec.issuing_authority or COMPANY["name"])
    holder_name = _esc(holder.get("name") or "—")
    holder_sub = " · ".join([s for s in [holder.get("code"), holder.get("desg"), holder.get("dept")] if s])
    cert_no = _esc(ec.certificate_number or f"FRK-{str(ec.id)[:8].upper()}")
    status = (ec.status.value if hasattr(ec.status, "value") else str(ec.status or "ACTIVE")).upper()
    status_label = STATUS_LABELS.get(status, status.replace("_", " ").title())
    status_color = STATUS_COLORS.get(status, a)
    issued = _fmt_date(ec.issue_date)
    expires = _fmt_date(ec.expiry_date) if ec.expiry_date else "No expiry"
    days = _days_to(ec.expiry_date)
    expiry_note = ""
    if days is not None:
        expiry_note = (f"valid · {days} days remaining" if days > 0
                       else ("expires today" if days == 0 else f"lapsed {abs(days)} days ago"))
    verify_id = str(ec.id)
    gen = date.today().strftime("%d %b %Y")

    seal = _seal_svg(theme, size_mm=30)
    watermark = _seal_svg(theme, size_mm=120, opacity=0.05)
    corners = "".join([
        f'<div class="corner-slot tl">{_corner(theme, 0)}</div>',
        f'<div class="corner-slot tr">{_corner(theme, 90)}</div>',
        f'<div class="corner-slot br">{_corner(theme, 180)}</div>',
        f'<div class="corner-slot bl">{_corner(theme, 270)}</div>',
    ])

    css = f"""
    @page {{ size: A4 landscape; margin: 0; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: "Helvetica Neue", Arial, sans-serif; color: {d}; }}
    .sheet {{ position: relative; width: 297mm; height: 210mm; overflow: hidden;
      background:
        radial-gradient(120% 90% at 50% -10%, {soft} 0%, {paper2} 75%, {soft} 100%); }}
    .frame {{ position: absolute; top: 8mm; right: 8mm; bottom: 8mm; left: 8mm; {_frame_css(theme)} }}
    .watermark {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%,-46%);
      z-index: 0; pointer-events: none; }}
    .corner-slot {{ position: absolute; z-index: 2; }}
    .corner {{ width: 16mm; height: 16mm; }}
    .corner-slot.tl {{ top: 12mm; left: 12mm; }}
    .corner-slot.tr {{ top: 12mm; right: 12mm; }}
    .corner-slot.br {{ bottom: 12mm; right: 12mm; }}
    .corner-slot.bl {{ bottom: 12mm; left: 12mm; }}

    .content {{ position: absolute; top: 8mm; right: 8mm; bottom: 8mm; left: 8mm; z-index: 3; padding: 15mm 24mm 13mm; }}
    .cert-main {{ width: 100%; text-align: center; }}
    .cert-bottom {{ width: 100%; margin-top: 17mm; }}

    .brand {{ display: flex; align-items: center; justify-content: center; gap: 2.4mm; font-size: 10pt; font-weight: 700;
      letter-spacing: 0.32em; text-transform: uppercase; color: {a}; }}
    .brand .bdot {{ width: 2.6mm; height: 2.6mm; border-radius: 50%; background: {a}; }}

    .seal {{ margin: 5mm 0 2mm; }}
    .eyebrow {{ font-size: 11pt; font-weight: 700; letter-spacing: 0.3em; text-transform: uppercase; color: {a}; }}
    .title {{ font-family: Georgia, "Times New Roman", serif; font-size: 30pt; font-weight: 700;
      letter-spacing: 0.04em; color: {d}; margin-top: 1mm; }}
    .flourish {{ display: flex; align-items: center; justify-content: center; gap: 3mm; margin: 4mm 0 3mm; }}
    .flourish .ln {{ width: 34mm; height: 0.4mm; background: linear-gradient(90deg, transparent, {a}); }}
    .flourish .ln.r {{ background: linear-gradient(90deg, {a}, transparent); }}
    .flourish .dia {{ width: 2.6mm; height: 2.6mm; background: {a}; transform: rotate(45deg); }}

    .lead {{ font-size: 11pt; color: {d}; opacity: 0.72; font-style: italic; }}
    .holder {{ font-family: Georgia, "Times New Roman", serif; font-size: 40pt; font-weight: 700;
      color: {d}; line-height: 1.05; margin: 2.5mm 0; }}
    .holder-sub {{ font-size: 9.5pt; letter-spacing: 0.14em; text-transform: uppercase; color: {a}; }}
    .midline {{ font-size: 11pt; color: {d}; opacity: 0.72; margin-top: 4mm; }}
    .cred {{ font-family: Georgia, "Times New Roman", serif; font-size: 21pt; font-weight: 700; color: {a}; margin-top: 1.5mm; }}
    .auth {{ font-size: 10.5pt; color: {d}; opacity: 0.8; margin-top: 1mm; }}
    .tagline {{ font-size: 8.5pt; letter-spacing: 0.22em; text-transform: uppercase; color: {a}; opacity: 0.7; margin-top: 2mm; }}

    .meta {{ display: flex; gap: 0; width: 100%;
      border-top: 0.3mm solid {a}; border-bottom: 0.3mm solid {a}; }}
    .meta .cell {{ flex: 1; padding: 4mm 3mm; text-align: center; }}
    .meta .cell + .cell {{ border-left: 0.3mm solid {a}; }}
    .meta .k {{ font-size: 7pt; letter-spacing: 0.16em; text-transform: uppercase; color: {a}; }}
    .meta .v {{ font-size: 11pt; font-weight: 700; color: {d}; margin-top: 1.4mm; }}
    .pill {{ display: inline-block; padding: 1mm 3mm; border-radius: 6mm; font-size: 8.5pt; font-weight: 700;
      color: #fff; background: {{status_color}}; }}

    .foot {{ display: flex; align-items: flex-end; justify-content: space-between; width: 100%;
      margin-top: 7mm; }}
    .sign {{ width: 56mm; text-align: center; }}
    .sign .ln {{ border-top: 0.4mm solid {d}; padding-top: 1.6mm; font-size: 8pt; letter-spacing: 0.1em;
      text-transform: uppercase; color: {d}; opacity: 0.85; }}
    .sign .role {{ font-size: 7pt; color: {a}; margin-top: 0.6mm; letter-spacing: 0.12em; text-transform: uppercase; }}
    .verify {{ text-align: center; flex: 1; padding: 0 4mm; }}
    .verify .vlbl {{ font-size: 6.5pt; letter-spacing: 0.18em; text-transform: uppercase; color: {a}; }}
    .verify .vid {{ font-family: "Courier New", monospace; font-size: 7.5pt; color: {d}; margin-top: 0.8mm; word-break: break-all; }}
    .verify .gen {{ font-size: 6.5pt; color: {d}; opacity: 0.6; margin-top: 1mm; }}
    """

    full = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <title>Certificate · {name} · {COMPANY['name']}</title>
    <style>{css.replace('{status_color}', status_color)}</style></head>
    <body>
      <div class="sheet">
        <div class="frame"></div>
        <div class="watermark">{watermark}</div>
        {corners}
        <div class="content">
          <div class="cert-main">
          <div class="brand"><span class="bdot"></span> {COMPANY['name']} · Learning &amp; Development</div>
          <div class="seal">{seal}</div>
          <div class="eyebrow">Certificate of {_esc(theme['label'])}</div>
          <div class="flourish"><span class="ln"></span><span class="dia"></span><span class="ln r"></span></div>
          <div class="lead">This is to certify that</div>
          <div class="holder">{holder_name}</div>
          <div class="holder-sub">{_esc(holder_sub)}</div>
          <div class="midline">has earned and holds the credential</div>
          <div class="cred">{name}</div>
          <div class="auth">issued by {authority}</div>
          <div class="tagline">{_esc(theme['tagline'])}</div>
          </div>

          <div class="cert-bottom">
          <div class="meta">
            <div class="cell"><div class="k">Issued</div><div class="v">{issued}</div></div>
            <div class="cell"><div class="k">{'Valid through' if ec.expiry_date else 'Validity'}</div><div class="v">{expires}</div></div>
            <div class="cell"><div class="k">Certificate №</div><div class="v">{cert_no}</div></div>
            <div class="cell"><div class="k">Status</div><div class="v"><span class="pill">{status_label}</span></div></div>
          </div>

          <div class="foot">
            <div class="sign"><div class="ln">{authority}</div><div class="role">Issuing Authority</div></div>
            <div class="verify">
              <div class="vlbl">Verification ID {('· ' + expiry_note) if expiry_note else ''}</div>
              <div class="vid">{_esc(verify_id)}</div>
              <div class="gen">Generated {gen} · {COMPANY['legal']} · {COMPANY['web']}</div>
            </div>
            <div class="sign"><div class="ln">{COMPANY['name']} People Team</div><div class="role">Head of HR</div></div>
          </div>
          </div>
        </div>
      </div>
    </body></html>"""

    return HTML(string=full).write_pdf()
