"""HR Exit Management — Experience / Relieving letter generation.

Renders an ultra-modern, brand-true PDF via WeasyPrint (lazy import — see
CLAUDE.md GTK rule): a guilloché-engraved certificate with a segno QR linking to
the public verification endpoint. The file is stored under
``storage/exit-letters/{emp}/`` and pointed at by a ``DriveDocument``. The
exit-local ``ExitDocument`` row carries status + verification_code.

Delivery discipline (no loophole): a *generated* letter is a draft — it is NOT
mirrored into the employee's unified document vault until HR formally ISSUES it
(`publish_letter`), and the mirror is withdrawn the moment it is REVOKED
(`withdraw_letter`). That keeps every employee-facing surface gated on ISSUED.

Public:
    render_letter(db, case, doc_type, *, template_id=None, actor) -> ExitDocument
    publish_letter(db, case, xd, actor) -> EmployeeDocument | None   # vault mirror on issue
    withdraw_letter(db, xd, actor) -> None                           # pull from vault on revoke
    letter_disk_path(drive_doc) -> str | None
"""
from __future__ import annotations

import os
import re
import secrets
import uuid as _uuid
from datetime import datetime, timezone, date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.drive_document import DriveDocument
from app.models.hr.employee import Employee
from app.models.hr.employee_document import (
    EmployeeDocument, EmployeeDocumentTemplate, DocumentCategory, DocSource, DocTemplateType,
    DocVerificationStatus,
)
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_document import ExitDocument
from app.models.hr.exit_type import ExitDocStatus

SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
STORAGE_DIR = os.path.join(SERVICE_ROOT, "storage")
LETTER_DIR = os.path.join(STORAGE_DIR, "exit-letters")

# Where the QR / "scan to verify" link points. Driven by FRONTEND_BASE_URL so a
# dev box can point at its own host (localhost:5173) and the link actually
# resolves to the running SPA — production defaults to the live domain. The
# verify page itself is the PUBLIC route /exit/verify/{code}.
VERIFY_BASE = (os.environ.get("FRONTEND_BASE_URL") or "https://crm.fourreck.com").rstrip("/") + "/exit/verify"

CATEGORY_FOR = {
    DocTemplateType.EXPERIENCE_LETTER: DocumentCategory.EXPERIENCE_LETTER,
    DocTemplateType.RELIEVING_LETTER: DocumentCategory.RELIEVING_LETTER,
}


def letter_disk_path(drive_doc: DriveDocument) -> Optional[str]:
    if not drive_doc or not drive_doc.file_url:
        return None
    rel = drive_doc.file_url.lstrip("/")
    return os.path.join(SERVICE_ROOT, rel)


def _fmt(d) -> str:
    if not d:
        return "—"
    try:
        return d.strftime("%d %B %Y")
    except Exception:
        return str(d)


def _tenure(join, end) -> str:
    if not join or not end:
        return "—"
    days = (end - join).days
    if days < 0:
        return "—"
    years = days // 365
    months = (days % 365) // 30
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    return ", ".join(parts) or "less than a month"


def _placeholders(db: Session, case: ExitCase, emp: Employee) -> dict:
    name = None
    if emp and emp.user is not None:
        name = getattr(emp.user, "full_name", None) or getattr(emp.user, "name", None) or getattr(emp.user, "email", None)
    desig = emp.designation.name if emp and emp.designation else "—"
    dept = case.department.name if case.department else (emp.department.name if emp and emp.department else "—")
    join = case.joining_date_snapshot or (emp.joining_date if emp else None)
    lwd = case.last_working_date or case.exit_date or (emp.last_working_date if emp else None)
    return {
        "employee_name": name or "—",
        "employee_code": (emp.employee_code or emp.employee_id) if emp else "—",
        "designation": desig,
        "department": dept,
        "joining_date": _fmt(join),
        "last_working_date": _fmt(lwd),
        "exit_date": _fmt(case.exit_date),
        "tenure": _tenure(join, lwd),
        "issue_date": _fmt(date.today()),
        "organisation": "Fourreck",
    }


def _qr_data_uri(url: str) -> str:
    import segno
    qr = segno.make(url, error="m")
    return qr.png_data_uri(scale=3, border=2, dark="#1a1206")


def _rosette_svg(stroke: str = "#ea580c", rings: int = 3) -> str:
    """A concentric guilloché rosette (sine-modulated radii) — the engraved
    security motif. Returned as inline SVG (WeasyPrint renders it reliably)."""
    import math
    size = 520.0
    c = size / 2.0
    specs = [(c - 26, 22, 18), (c - 66, 15, 28), (c - 104, 10, 42)][:max(1, rings)]
    paths = []
    for (R, amp, petals) in specs:
        steps = 540
        pts = []
        for i in range(steps + 1):
            t = (i / steps) * math.tau
            r = R + amp * math.sin(petals * t)
            x = c + r * math.cos(t)
            y = c + r * math.sin(t)
            pts.append(f"{x:.1f} {y:.1f}")
        paths.append(f'<path d="M{"L".join(pts)}Z" fill="none" stroke="{stroke}" stroke-width="1"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size:.0f} {size:.0f}">'
        f'<circle cx="{c:.0f}" cy="{c:.0f}" r="{c - 4:.0f}" fill="none" stroke="{stroke}" stroke-width="1"/>'
        + "".join(paths) + "</svg>"
    )


def _seal_svg(initial: str = "F") -> str:
    """A small embossed wax-style seal for the certificate foot."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
        '<defs><radialGradient id="wx" cx="40%" cy="36%" r="70%">'
        '<stop offset="0%" stop-color="#f59e0b"/><stop offset="70%" stop-color="#c2410c"/>'
        '<stop offset="100%" stop-color="#9a3412"/></radialGradient></defs>'
        '<path d="M60 4 L74 12 L91 9 L96 26 L113 36 L105 53 L113 72 L95 79 L88 98 '
        'L69 94 L60 114 L51 94 L32 98 L25 79 L7 72 L15 53 L7 36 L24 26 L29 9 L46 12 Z" '
        'fill="url(#wx)"/>'
        '<circle cx="60" cy="58" r="36" fill="none" stroke="#fde2c0" stroke-width="1.4" opacity="0.7"/>'
        '<circle cx="60" cy="58" r="30" fill="none" stroke="#fff3e0" stroke-width="0.8" stroke-dasharray="2 3" opacity="0.8"/>'
        f'<text x="60" y="70" text-anchor="middle" font-family="Georgia, serif" '
        f'font-size="34" font-weight="700" fill="#fff5e6">{initial}</text>'
        '</svg>'
    )


_CERT_CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Georgia, 'Times New Roman', serif; color: #2c2418; }
.page { position: relative; width: 210mm; min-height: 297mm; padding: 24mm 22mm 22mm; background: #fdfaf3; overflow: hidden; }
.bg-rose { position: absolute; top: 50%; left: 50%; width: 156mm; height: 156mm; margin: -78mm 0 0 -78mm; opacity: 0.045; }
.bg-rose svg { width: 100%; height: 100%; }
.frame { position: absolute; top: 8mm; left: 8mm; right: 8mm; bottom: 8mm; border: 1.3pt solid #ea580c; }
.frame-inner { position: absolute; top: 9.6mm; left: 9.6mm; right: 9.6mm; bottom: 9.6mm; border: 0.5pt solid #d9a066; }
.corner { position: absolute; width: 11mm; height: 11mm; border: 0 solid #b45309; }
.corner.tl { top: 6.4mm; left: 6.4mm; border-top-width: 1.4pt; border-left-width: 1.4pt; }
.corner.tr { top: 6.4mm; right: 6.4mm; border-top-width: 1.4pt; border-right-width: 1.4pt; }
.corner.bl { bottom: 6.4mm; left: 6.4mm; border-bottom-width: 1.4pt; border-left-width: 1.4pt; }
.corner.br { bottom: 6.4mm; right: 6.4mm; border-bottom-width: 1.4pt; border-right-width: 1.4pt; }
.hero { position: relative; display: flex; justify-content: space-between; align-items: center;
  padding: 8mm 9mm; border-radius: 3mm; color: #fff8ef;
  background: linear-gradient(118deg, #92400e 0%, #ea580c 52%, #f59e0b 100%); }
.brand { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; font-size: 30pt; font-weight: 800; letter-spacing: -0.5pt; line-height: 1; }
.brand-sub { font-family: 'Segoe UI', Arial, sans-serif; font-size: 7.5pt; letter-spacing: 3pt; text-transform: uppercase; opacity: 0.86; margin-top: 2.4mm; }
.hero-ref { text-align: right; font-family: 'Segoe UI', Arial, sans-serif; font-size: 8pt; line-height: 1.65; opacity: 0.94; }
.hero-ref .k { opacity: 0.7; }
.title { text-align: center; font-family: 'Segoe UI', Arial, sans-serif; font-size: 17pt; letter-spacing: 4.5pt; font-weight: 700; color: #9a3412; margin: 13mm 0 0; }
.title-rule { width: 28mm; height: 1.4pt; margin: 3.4mm auto 9mm; background: linear-gradient(90deg, rgba(234,88,12,0), #ea580c, rgba(234,88,12,0)); }
.body { font-size: 11.5pt; line-height: 1.92; }
.body p { margin: 0 0 4.5mm; }
.body b { color: #1f1810; font-weight: bold; }
.facts { display: flex; gap: 4mm; margin: 8mm 0 2mm; }
.fact { flex: 1; padding: 3.4mm 4mm; border-radius: 2mm; background: rgba(234,88,12,0.06); border: 0.5pt solid rgba(234,88,12,0.22); }
.fact .k { font-family: 'Segoe UI', Arial, sans-serif; font-size: 6.6pt; letter-spacing: 1.4pt; text-transform: uppercase; color: #a06a30; }
.fact .v { font-size: 10.5pt; font-weight: bold; color: #2c2418; margin-top: 1.2mm; }
.foot { display: flex; justify-content: space-between; align-items: flex-end; margin-top: 16mm; }
.sign { width: 60mm; }
.sign .ln { position: relative; width: 46mm; border-bottom: 1pt solid #c98a4a; height: 11mm; }
.sign .ln .sig-mark { position: absolute; left: 2mm; bottom: 0.5mm; width: 40mm; height: 13mm; }
.sign .ln .sig-mark svg { width: 100%; height: 100%; }
.sign .nm { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; font-weight: bold; margin-top: 2.2mm; }
.sign .role { font-family: 'Segoe UI', Arial, sans-serif; font-size: 8pt; color: #8a7456; margin-top: 0.6mm; }
.sign .dsig { font-family: 'Segoe UI', Arial, sans-serif; font-size: 7pt; font-weight: 600; color: #047857; margin-top: 1.6mm; }
.seal { position: absolute; left: 50%; bottom: 30mm; width: 24mm; height: 24mm; margin-left: -12mm; opacity: 0.96; }
.seal svg { width: 100%; height: 100%; }
.verify { text-align: center; font-family: 'Segoe UI', Arial, sans-serif; }
.verify img { width: 25mm; height: 25mm; display: block; margin: 0 auto 1.6mm; padding: 1.4mm; background: #fff; border: 0.5pt solid rgba(0,0,0,0.12); border-radius: 1.4mm; }
.verify .cap { font-size: 7.4pt; letter-spacing: 0.4pt; color: #8a7456; }
.verify .code { font-size: 8.4pt; letter-spacing: 1.2pt; color: #b45309; font-weight: bold; margin-top: 0.8mm; }
.verify .url { font-size: 6.4pt; color: #a08a6a; margin-top: 0.4mm; }
.micro { position: absolute; left: 12mm; right: 12mm; bottom: 13.2mm; text-align: center; overflow: hidden; white-space: nowrap;
  font-family: 'Segoe UI', Arial, sans-serif; font-size: 4pt; letter-spacing: 1.4pt; color: rgba(180,120,20,0.38); }
.ribbon { position: absolute; left: 8mm; right: 8mm; bottom: 8mm; height: 2mm; background: linear-gradient(90deg, #fcd34d, #fb923c 55%, #ea580c); }
"""


def _signature_svg(stroke: str = "#10243f") -> str:
    """A stylized handwritten 'authorised signatory' signature flourish (ink)."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 70" preserveAspectRatio="xMidYMid meet">'
        '<path d="M8 48 C 20 16 30 18 32 42 C 33 56 27 60 26 50 C 25 40 40 24 52 42 '
        'C 60 54 51 58 50 48 C 49 36 65 22 78 42 C 85 53 75 58 73 48 '
        'C 71 35 94 22 106 44 C 112 54 120 30 134 36 C 148 42 152 30 164 41 '
        'C 175 50 184 30 202 27" '
        f'fill="none" stroke="{stroke}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="M150 56 C 168 52 188 52 208 54" fill="none" stroke="{stroke}" stroke-width="1.1" '
        'stroke-linecap="round" opacity="0.65"/>'
        '</svg>'
    )


def _sign_block(org: str, ph: dict, code: str) -> str:
    """Authorised-signatory block with a rendered ink signature ON the line plus a
    'digitally signed' attestation tied to the verification reference."""
    sig = _signature_svg()
    ref = f" &middot; Ref {code}" if code else ""
    return (
        '<div class="sign">'
        f'<div class="ln"><span class="sig-mark">{sig}</span></div>'
        '<div class="nm">Authorised Signatory</div>'
        f'<div class="role">{org} &middot; Human Resources</div>'
        f'<div class="dsig">&#10003; Digitally signed &middot; {ph["issue_date"]}{ref}</div>'
        '</div>'
    )


def _builtin_html(doc_type: DocTemplateType, ph: dict, verify_url: str, qr_uri: str, code: str = "") -> str:
    is_rel = doc_type == DocTemplateType.RELIEVING_LETTER
    heading = "RELIEVING LETTER" if is_rel else "EXPERIENCE &amp; SERVICE CERTIFICATE"
    org = ph['organisation']
    if is_rel:
        body = f"""
          <p>This is to certify that <b>{ph['employee_name']}</b> ({ph['employee_code']}),
          who served as <b>{ph['designation']}</b> in the {ph['department']} department,
          has been relieved from the services of {org} with effect from
          <b>{ph['last_working_date']}</b>.</p>
          <p>All company dues and no-dues clearances have been completed and the full &amp;
          final settlement has been processed. We confirm the employee is relieved of all
          duties and responsibilities as of the last working day.</p>
          <p>We wish {ph['employee_name']} success in all future endeavours.</p>
        """
        facts = (f'<div class="fact"><div class="k">Designation</div><div class="v">{ph["designation"]}</div></div>'
                 f'<div class="fact"><div class="k">Department</div><div class="v">{ph["department"]}</div></div>'
                 f'<div class="fact"><div class="k">Relieved on</div><div class="v">{ph["last_working_date"]}</div></div>')
    else:
        body = f"""
          <p>This is to certify that <b>{ph['employee_name']}</b> ({ph['employee_code']})
          was employed with {org} as <b>{ph['designation']}</b> in the
          {ph['department']} department.</p>
          <p>The period of service was from <b>{ph['joining_date']}</b> to
          <b>{ph['last_working_date']}</b>, a total tenure of <b>{ph['tenure']}</b>.</p>
          <p>During the tenure, the employee's conduct and performance were found to be
          satisfactory. This certificate is issued on request.</p>
        """
        facts = (f'<div class="fact"><div class="k">Designation</div><div class="v">{ph["designation"]}</div></div>'
                 f'<div class="fact"><div class="k">Department</div><div class="v">{ph["department"]}</div></div>'
                 f'<div class="fact"><div class="k">Tenure</div><div class="v">{ph["tenure"]}</div></div>')
    micro = ("&middot; " + f"{org.upper()} &middot; VERIFIED CREDENTIAL ") * 14
    code_html = f'<div class="code">{code}</div>' if code else ''
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_CERT_CSS}</style></head><body>
      <div class="page">
        <div class="bg-rose">{_rosette_svg()}</div>
        <div class="frame"></div><div class="frame-inner"></div>
        <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
        <div class="hero">
          <div><div class="brand">{org}</div><div class="brand-sub">Human Resources &middot; Office of the Registrar</div></div>
          <div class="hero-ref"><div><span class="k">Ref</span> &nbsp;{ph['employee_code']}</div><div><span class="k">Date</span> &nbsp;{ph['issue_date']}</div></div>
        </div>
        <div class="title">{heading}</div><div class="title-rule"></div>
        <div class="body">{body}</div>
        <div class="facts">{facts}</div>
        <div class="seal">{_seal_svg(org[:1].upper() or 'F')}</div>
        <div class="foot">
          {_sign_block(org, ph, code)}
          <div class="verify"><img src="{qr_uri}" alt="QR"/><div class="cap">Scan to verify authenticity</div>{code_html}<div class="url">{verify_url}</div></div>
        </div>
        <div class="micro">{micro}</div>
        <div class="ribbon"></div>
      </div>
    </body></html>"""


# ── Relieving letter: a deliberately DIFFERENT document from the experience
# certificate — a centred laurel crest, a guilloché wave band, an emerald
# clearance-attestation block, an angled "RELIEVED · NO DUES" stamp and an
# emerald→gold palette (release = cleared). Structurally symmetric vs the
# experience cert's left wordmark + amber rosette/wax-seal layout.

def _crest_svg() -> str:
    """An emerald laurel-and-check release crest."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
        '<circle cx="60" cy="60" r="42" fill="none" stroke="#047857" stroke-width="2"/>'
        '<circle cx="60" cy="60" r="35" fill="none" stroke="#059669" stroke-width="0.7" stroke-dasharray="1.4 3"/>'
        '<path d="M42 90 C26 78 24 54 38 40" fill="none" stroke="#059669" stroke-width="1.6" stroke-linecap="round"/>'
        '<ellipse cx="30" cy="72" rx="4.4" ry="2" fill="#059669" transform="rotate(40 30 72)"/>'
        '<ellipse cx="28" cy="62" rx="4.4" ry="2" fill="#059669" transform="rotate(15 28 62)"/>'
        '<ellipse cx="30" cy="52" rx="4.4" ry="2" fill="#059669" transform="rotate(-10 30 52)"/>'
        '<ellipse cx="35" cy="44" rx="4.2" ry="1.9" fill="#10b981" transform="rotate(-32 35 44)"/>'
        '<path d="M78 90 C94 78 96 54 82 40" fill="none" stroke="#059669" stroke-width="1.6" stroke-linecap="round"/>'
        '<ellipse cx="90" cy="72" rx="4.4" ry="2" fill="#059669" transform="rotate(-40 90 72)"/>'
        '<ellipse cx="92" cy="62" rx="4.4" ry="2" fill="#059669" transform="rotate(-15 92 62)"/>'
        '<ellipse cx="90" cy="52" rx="4.4" ry="2" fill="#059669" transform="rotate(10 90 52)"/>'
        '<ellipse cx="85" cy="44" rx="4.2" ry="1.9" fill="#10b981" transform="rotate(32 85 44)"/>'
        '<path d="M48 60 l8 9 l17 -20" fill="none" stroke="#047857" stroke-width="4.4" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )


def _guilloche_band_svg(stroke: str = "#059669") -> str:
    import math
    w, h, cy = 300, 24, 12

    def wave(phase, amp):
        pts = []
        for i in range(0, w + 1, 3):
            y = cy + amp * math.sin(i / w * math.pi * 9 + phase)
            pts.append(f"{i} {y:.1f}")
        return "M" + "L".join(pts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
        f'<path d="{wave(0, 8)}" fill="none" stroke="{stroke}" stroke-width="0.8"/>'
        f'<path d="{wave(math.pi, 8)}" fill="none" stroke="{stroke}" stroke-width="0.8"/>'
        f'<path d="{wave(math.pi / 2, 4)}" fill="none" stroke="#b45309" stroke-width="0.6" opacity="0.7"/>'
        '</svg>'
    )


_RELIEVING_CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Georgia, 'Times New Roman', serif; color: #21271f; }
.page { position: relative; width: 210mm; min-height: 297mm; padding: 22mm 20mm 22mm 27mm; background: #fbfcf8; overflow: hidden; }
.wm { position: absolute; top: 52%; left: 54%; width: 150mm; height: 150mm; margin: -75mm 0 0 -75mm; opacity: 0.05; }
.wm svg { width: 100%; height: 100%; }
.spine { position: absolute; top: 8mm; bottom: 8mm; left: 9mm; width: 2.4mm; border-radius: 2mm; background: linear-gradient(180deg, #047857, #059669 52%, #b45309); }
.rule-top { position: absolute; top: 10mm; left: 16mm; right: 9mm; height: 0.7pt; background: #047857; opacity: 0.45; }
.corner { position: absolute; width: 10mm; height: 10mm; border: 0 solid #047857; }
.corner.tr { top: 7mm; right: 7mm; border-top-width: 1.3pt; border-right-width: 1.3pt; }
.corner.br { bottom: 7mm; right: 7mm; border-bottom-width: 1.3pt; border-right-width: 1.3pt; }
.head { text-align: center; position: relative; }
.crest { width: 26mm; height: 26mm; margin: 0 auto 3mm; }
.crest svg { width: 100%; height: 100%; }
.brand { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; font-size: 26pt; font-weight: 800; letter-spacing: -0.4pt; color: #064e3b; line-height: 1; }
.brand-sub { font-family: 'Segoe UI', Arial, sans-serif; font-size: 7.4pt; letter-spacing: 3pt; text-transform: uppercase; color: #6b7b5e; margin-top: 2.2mm; }
.refline { font-family: 'Segoe UI', Arial, sans-serif; font-size: 7.8pt; letter-spacing: 0.4pt; color: #6b7b5e; margin-top: 3.2mm; }
.refline b { color: #064e3b; }
.title { text-align: center; font-family: 'Segoe UI', Arial, sans-serif; font-size: 18pt; letter-spacing: 6pt; font-weight: 700; color: #065f46; margin: 11mm 0 2mm; }
.band { width: 64mm; height: 6mm; margin: 0 auto 9mm; }
.band svg { width: 100%; height: 100%; }
.body { font-size: 11.5pt; line-height: 1.95; }
.body p { margin: 0 0 4.6mm; }
.body b { color: #15200f; font-weight: bold; }
.attest { margin: 8mm 0 3mm; border: 0.6pt solid rgba(5,150,105,0.32); border-radius: 2.6mm; padding: 4.2mm 5mm; background: rgba(5,150,105,0.05); }
.attest .h { font-family: 'Segoe UI', Arial, sans-serif; font-size: 7pt; letter-spacing: 2pt; text-transform: uppercase; color: #047857; margin-bottom: 2.8mm; }
.attest .row { font-size: 9.6pt; color: #21271f; margin-bottom: 2mm; }
.attest .row:last-child { margin-bottom: 0; }
.tick { display: inline-block; width: 4.6mm; height: 4.6mm; border-radius: 50%; background: #059669; color: #fff; text-align: center; line-height: 4.6mm; font-size: 6.8pt; font-weight: bold; margin-right: 2.6mm; }
.foot { display: flex; justify-content: space-between; align-items: flex-end; margin-top: 12mm; position: relative; }
.sign { width: 60mm; }
.sign .ln { position: relative; width: 46mm; border-bottom: 1pt solid #6e9e84; height: 11mm; }
.sign .ln .sig-mark { position: absolute; left: 2mm; bottom: 0.5mm; width: 40mm; height: 13mm; }
.sign .ln .sig-mark svg { width: 100%; height: 100%; }
.sign .nm { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; font-weight: bold; color: #15200f; margin-top: 2.2mm; }
.sign .role { font-family: 'Segoe UI', Arial, sans-serif; font-size: 8pt; color: #6b7b5e; margin-top: 0.6mm; }
.sign .dsig { font-family: 'Segoe UI', Arial, sans-serif; font-size: 7pt; font-weight: 600; color: #047857; margin-top: 1.6mm; }
.stamp { position: absolute; left: 50%; bottom: 7mm; margin-left: -21mm; transform: rotate(-13deg); border: 1.6pt solid #047857; color: #047857; border-radius: 2mm; padding: 2.6mm 4.2mm; text-align: center; opacity: 0.85; }
.stamp .a { font-family: 'Segoe UI', Arial, sans-serif; font-size: 12.5pt; font-weight: 800; letter-spacing: 2.4pt; line-height: 1; }
.stamp .b { font-family: 'Segoe UI', Arial, sans-serif; font-size: 6pt; letter-spacing: 2pt; margin-top: 1.2mm; }
.verify { text-align: center; font-family: 'Segoe UI', Arial, sans-serif; }
.verify img { width: 25mm; height: 25mm; display: block; margin: 0 auto 1.6mm; padding: 1.4mm; background: #fff; border: 0.6pt solid rgba(5,150,105,0.3); border-radius: 1.4mm; }
.verify .cap { font-size: 7.4pt; color: #6b7b5e; }
.verify .code { font-size: 8.4pt; letter-spacing: 1.2pt; color: #047857; font-weight: bold; margin-top: 0.8mm; }
.verify .url { font-size: 6.4pt; color: #8ba07e; margin-top: 0.4mm; }
.micro { position: absolute; left: 16mm; right: 9mm; bottom: 12.6mm; text-align: center; overflow: hidden; white-space: nowrap;
  font-family: 'Segoe UI', Arial, sans-serif; font-size: 4pt; letter-spacing: 1.4pt; color: rgba(5,120,80,0.34); }
.ribbon { position: absolute; left: 9mm; right: 8mm; bottom: 8mm; height: 2mm; background: linear-gradient(90deg, #34d399, #059669 55%, #b45309); }
"""


def _relieving_html(ph: dict, verify_url: str, qr_uri: str, code: str = "") -> str:
    org = ph['organisation']
    body = f"""
      <p>This is to certify that <b>{ph['employee_name']}</b> ({ph['employee_code']}), who served as
      <b>{ph['designation']}</b> in the {ph['department']} department, has been duly <b>relieved</b>
      from the services of {org} with effect from the close of business on
      <b>{ph['last_working_date']}</b>.</p>
      <p>The employee was associated with the organisation from <b>{ph['joining_date']}</b> to
      <b>{ph['last_working_date']}</b>. All organisational dues and no-dues clearances have been
      completed and the full &amp; final settlement has been duly processed and closed.</p>
      <p>We confirm that the employee stands relieved of all duties and responsibilities as of the
      last working day, and we extend our best wishes for their future endeavours.</p>
    """
    attest = (
        '<div class="attest"><div class="h">Clearance Attestation</div>'
        '<div class="row"><span class="tick">&#10003;</span>No-dues clearance completed across all departments</div>'
        '<div class="row"><span class="tick">&#10003;</span>Full &amp; Final settlement processed &amp; closed</div>'
        '<div class="row"><span class="tick">&#10003;</span>Company assets returned / accounted for</div></div>'
    )
    micro = ("&middot; " + f"{org.upper()} &middot; RELIEVING &middot; NO DUES ") * 12
    code_html = f'<div class="code">{code}</div>' if code else ''
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_RELIEVING_CSS}</style></head><body>
      <div class="page">
        <div class="wm">{_crest_svg()}</div>
        <div class="spine"></div><div class="rule-top"></div>
        <span class="corner tr"></span><span class="corner br"></span>
        <div class="head">
          <div class="crest">{_crest_svg()}</div>
          <div class="brand">{org}</div>
          <div class="brand-sub">Human Resources &middot; Office of the Registrar</div>
          <div class="refline">Ref&nbsp;<b>{ph['employee_code']}</b> &nbsp;&middot;&nbsp; Date&nbsp;<b>{ph['issue_date']}</b></div>
        </div>
        <div class="title">RELIEVING LETTER</div>
        <div class="band">{_guilloche_band_svg()}</div>
        <div class="body">{body}</div>
        {attest}
        <div class="foot">
          {_sign_block(org, ph, code)}
          <div class="stamp"><div class="a">RELIEVED</div><div class="b">NO DUES &middot; CLEARED</div></div>
          <div class="verify"><img src="{qr_uri}" alt="QR"/><div class="cap">Scan to verify authenticity</div>{code_html}<div class="url">{verify_url}</div></div>
        </div>
        <div class="micro">{micro}</div>
        <div class="ribbon"></div>
      </div>
    </body></html>"""


def _resolve_template_html(db: Session, doc_type: DocTemplateType, template_id, ph, verify_url, qr_uri, code="") -> str:
    # Honour a custom template ONLY when one is explicitly selected for this
    # generation. We deliberately do NOT auto-pick "the most recent active
    # template of this type" — that silently let half-finished stub templates
    # hijack the branded, ultra-modern builtin design. The builtin is the
    # canonical default; a custom template is opt-in per generate.
    tpl = None
    if template_id:
        tpl = db.query(EmployeeDocumentTemplate).filter(
            EmployeeDocumentTemplate.id == template_id,
            EmployeeDocumentTemplate.is_deleted == False,  # noqa: E712
        ).first()
    if tpl and tpl.body:
        html = tpl.body
        ph2 = dict(ph, verification_url=verify_url, verification_code=code)
        for k, v in ph2.items():
            html = re.sub(r"\{\{\s*" + re.escape(k) + r"\s*\}\}", str(v), html)
        # ensure the QR is present (append a footer if the template omits it)
        if "{{qr}}" in html:
            html = html.replace("{{qr}}", f'<img src="{qr_uri}" style="width:84px;height:84px"/>')
        return html
    if doc_type == DocTemplateType.RELIEVING_LETTER:
        return _relieving_html(ph, verify_url, qr_uri, code)
    return _builtin_html(doc_type, ph, verify_url, qr_uri, code)


def render_letter(db: Session, case: ExitCase, doc_type: DocTemplateType, *,
                  template_id=None, actor: User) -> ExitDocument:
    """Render + store a letter PDF, wire DriveDocument + EmployeeDocument + ExitDocument."""
    emp = case.employee or db.query(Employee).filter(Employee.id == case.employee_id).first()
    ph = _placeholders(db, case, emp)

    code = secrets.token_hex(8).upper()
    verify_url = f"{VERIFY_BASE}/{code}"
    qr_uri = _qr_data_uri(verify_url)
    html = _resolve_template_html(db, doc_type, template_id, ph, verify_url, qr_uri, code)

    # Render PDF (lazy WeasyPrint import — GTK PATH prepared at startup).
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML
    pdf_bytes = HTML(string=html).write_pdf()

    # Persist to disk.
    dest_dir = os.path.join(LETTER_DIR, str(case.employee_id))
    os.makedirs(dest_dir, exist_ok=True)
    slug = "relieving" if doc_type == DocTemplateType.RELIEVING_LETTER else "experience"
    fname = f"{slug}_{case.case_number}_{_uuid.uuid4().hex[:8]}.pdf"
    with open(os.path.join(dest_dir, fname), "wb") as f:
        f.write(pdf_bytes)
    file_url = f"/storage/exit-letters/{case.employee_id}/{fname}"

    title = f"{'Relieving Letter' if doc_type == DocTemplateType.RELIEVING_LETTER else 'Experience Letter'} — {ph['employee_name']}"
    drive = DriveDocument(
        title=title, file_name=fname, file_url=file_url, file_type="pdf",
        file_size=len(pdf_bytes), mime_type="application/pdf", category="HR",
        status="Active", is_confidential=False, uploaded_by=actor.id,
        employee_id=case.employee_id, version_number=1,
    )
    db.add(drive)
    db.flush()

    # NOTE: a *generated* letter is a draft — it is intentionally NOT mirrored
    # into the employee's unified vault here. `publish_letter()` mints that
    # mirror on ISSUE so the employee never sees an un-issued draft, and
    # `withdraw_letter()` removes it on REVOKE.
    # Update / create the ExitDocument pointer.
    xd = next((d for d in (case.documents or []) if d.doc_type == doc_type), None)
    if xd is None:
        xd = ExitDocument(exit_case_id=case.id, doc_type=doc_type)
        db.add(xd)
    xd.status = ExitDocStatus.GENERATED
    xd.template_id = template_id
    xd.drive_document_id = drive.id
    xd.employee_document_id = None   # (re)minted into the vault at issue time
    xd.verification_code = code
    xd.content_snapshot = ph
    xd.revoked_at = None
    xd.revoke_reason = None
    db.flush()
    return xd


def publish_letter(db: Session, case: ExitCase, xd: ExitDocument, actor: User) -> Optional[EmployeeDocument]:
    """Mirror an ISSUED letter into the unified document vault (idempotent).

    Called on *issue* — not generate — so the employee only sees the credential
    in their personal archive once it is formally released. This closes the
    loophole where a generated-but-unissued draft (or a revoked one) was
    downloadable from the docs hub.
    """
    if xd is None or not xd.drive_document_id:
        return None
    if xd.employee_document_id:
        existing = db.query(EmployeeDocument).filter(EmployeeDocument.id == xd.employee_document_id).first()
        if existing and not existing.is_deleted:
            return existing
    ph = xd.content_snapshot or {}
    is_rel = xd.doc_type == DocTemplateType.RELIEVING_LETTER
    title = f"{'Relieving Letter' if is_rel else 'Experience Letter'} — {ph.get('employee_name') or '—'}"
    edoc = EmployeeDocument(
        employee_id=case.employee_id,
        category=CATEGORY_FOR[xd.doc_type],
        doc_type=xd.doc_type.value,
        title=title,
        drive_document_id=xd.drive_document_id,
        issue_date=date.today(),
        source=DocSource.GENERATED,
        verification_status=DocVerificationStatus.VERIFIED,
        attributes={"exit_case": case.case_number, "verification_code": xd.verification_code},
        created_by_id=actor.id,
    )
    db.add(edoc)
    db.flush()
    xd.employee_document_id = edoc.id
    db.flush()
    return edoc


def withdraw_letter(db: Session, xd: ExitDocument, actor: User) -> None:
    """Pull a revoked letter back out of the employee's vault (soft delete)."""
    if xd is None or not xd.employee_document_id:
        return
    edoc = db.query(EmployeeDocument).filter(EmployeeDocument.id == xd.employee_document_id).first()
    if edoc and not edoc.is_deleted:
        edoc.is_deleted = True
        edoc.deleted_at = datetime.now(timezone.utc)
        edoc.deleted_by_id = actor.id
        db.flush()
