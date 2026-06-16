"""HR Reimbursements — server-side report rendering (PDF / Excel / CSV).

A self-contained reporting engine for the Reimbursements module. Unlike the
old single shared receipt-tape cover, **every report owns a distinct visual
identity** — its own accent, its own WeasyPrint cover "motif" (vault / spectrum
/ pipeline / payslip / clawback / podium / stopwatch / ledger-roll) and its own
two-sheet Excel workbook (KPI deck + native chart on an Overview sheet, then a
fully-formatted Data sheet). The public API is unchanged so the router does not
need to be touched:

    REPORT_KEYS, REPORT_META, report_meta,
    fetch_rows(db, *, date_from, date_to, category_id, status),
    shape(key, rows), render_pdf(key, rows), render_excel(key, rows), render_csv(key, rows)
"""
from __future__ import annotations

import csv
import html as _html
import io
from collections import Counter
from datetime import date, datetime
from typing import List, Optional, Dict, Any, Callable

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.claim import Claim
from app.models.hr.claim_category import ClaimCategory
from app.models.hr.reimbursement_type import ClaimStatus

COMPANY = {"name": "Fourreck", "legal": "Fourreck Technologies Pvt. Ltd.", "web": "fourreck.com"}

# ════════════════════════════════════════════════════════════════════════════
# Registry — each report carries a unique accent triad + cover motif slug
# ════════════════════════════════════════════════════════════════════════════
REPORT_KEYS = [
    "claims_register", "settlement_summary", "by_category", "by_employee",
    "pending_approvals", "aging", "payroll_settlements", "reversals",
]

REPORT_META: Dict[str, dict] = {
    "claims_register": {
        "name": "Claims Register", "tagline": "Every claim, end to end",
        "description": "All claims with status, amounts and settlement.",
        "motif": "ledger", "accent": "#ea580c", "deep": "#7c2d12", "soft": "#fff4ec"},
    "settlement_summary": {
        "name": "Settlement Summary", "tagline": "What was paid, and how",
        "description": "Settled & paid claims with method and reference.",
        "motif": "vault", "accent": "#0d9488", "deep": "#134e4a", "soft": "#effbf8"},
    "by_category": {
        "name": "Category Analysis", "tagline": "Spend across the spectrum",
        "description": "Claim counts and amounts grouped by category.",
        "motif": "spectrum", "accent": "#7c3aed", "deep": "#4c1d95", "soft": "#f6f2ff"},
    "by_employee": {
        "name": "Employee Spend Ledger", "tagline": "Who claims the most",
        "description": "Per-employee claim volume, total claimed and settled.",
        "motif": "podium", "accent": "#4f46e5", "deep": "#312e81", "soft": "#f1f1fe"},
    "pending_approvals": {
        "name": "Pending Approvals", "tagline": "Stuck in the chain",
        "description": "Claims awaiting a decision, with current stage.",
        "motif": "pipeline", "accent": "#d97706", "deep": "#7c4a07", "soft": "#fff7ea"},
    "aging": {
        "name": "Approval Aging & TAT", "tagline": "How long claims wait",
        "description": "In-flight claims bucketed by how many days they've waited.",
        "motif": "stopwatch", "accent": "#e11d48", "deep": "#881337", "soft": "#fff1f4"},
    "payroll_settlements": {
        "name": "Payroll Settlements", "tagline": "Folded into payslips",
        "description": "Claims settled through the payroll cycle.",
        "motif": "payslip", "accent": "#15803d", "deep": "#14532d", "soft": "#f0fdf4"},
    "reversals": {
        "name": "Reversals & Clawbacks", "tagline": "Corrections on record",
        "description": "Reversed claims and the reason behind each clawback.",
        "motif": "clawback", "accent": "#b91c1c", "deep": "#7f1d1d", "soft": "#fef2f2"},
}


def report_meta(key: str) -> dict:
    return REPORT_META.get(key, {"name": key, "tagline": "", "description": "",
                                  "motif": "ledger", "accent": "#ea580c", "deep": "#7c2d12", "soft": "#fff4ec"})


# ════════════════════════════════════════════════════════════════════════════
# Helpers — money (Indian grouping), html escape, formatting
# ════════════════════════════════════════════════════════════════════════════
def esc(v: Any) -> str:
    return "" if v is None else _html.escape(str(v))


def inr_group(value) -> str:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    neg, n = n < 0, abs(round(float(value or 0)))
    s = str(int(n))
    if len(s) <= 3:
        grouped = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail
    return ("-" if neg else "") + grouped


def inr(value) -> str:
    return "₹" + inr_group(value)


def inr_compact(value) -> str:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    a, sign = abs(n), ("-" if n < 0 else "")
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f}Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f}L"
    if a >= 1e3:
        return f"{sign}₹{a / 1e3:.1f}k"
    return f"{sign}₹{int(a)}"


def fmt_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except ValueError:
            return d
    return d.strftime("%d %b %Y")


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


_MONEY_KEYS = {"amount", "approved_amount", "settled", "total_amount", "total"}


# ════════════════════════════════════════════════════════════════════════════
# Data — fetch + per-report shaping
# ════════════════════════════════════════════════════════════════════════════
def fetch_rows(db: Session, *, date_from: Optional[date] = None, date_to: Optional[date] = None,
               category_id=None, status: Optional[str] = None) -> List[dict]:
    q = (
        db.query(Claim, ClaimCategory.name.label("cat"), ClaimCategory.code.label("code"),
                 User.full_name.label("emp"), Employee.employee_id.label("emp_code"),
                 Department.name.label("dept"))
        .join(ClaimCategory, ClaimCategory.id == Claim.category_id)
        .join(Employee, Employee.id == Claim.employee_id)
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .filter(Claim.is_deleted == False)  # noqa: E712
    )
    if date_from:
        q = q.filter(Claim.expense_date >= date_from)
    if date_to:
        q = q.filter(Claim.expense_date <= date_to)
    if category_id:
        q = q.filter(Claim.category_id == category_id)
    if status:
        try:
            q = q.filter(Claim.status == ClaimStatus(status))
        except ValueError:
            pass
    rows = q.order_by(Claim.created_at.desc()).all()
    out = []
    for c, cat, code, emp, emp_code, dept in rows:
        out.append({
            "claim_number": c.claim_number, "employee": emp or "—", "employee_code": emp_code or "",
            "department": dept or "—", "category": cat, "category_code": code,
            "color_hex": getattr(c.category, "color_hex", None) if hasattr(c, "category") else None,
            "expense_date": c.expense_date.isoformat() if c.expense_date else "",
            "claim_date": c.claim_date.isoformat() if c.claim_date else "",
            "amount": float(c.amount or 0),
            "approved_amount": float(c.approved_amount) if c.approved_amount is not None else None,
            "status": c.status.value,
            "settlement_method": c.settlement_method.value if c.settlement_method else "",
            "settlement_number": c.settlement_number or "",
            "payroll_ref": c.payroll_ref or "",
            "settled_at": c.settled_at.date().isoformat() if c.settled_at else "",
            "vendor": c.vendor or "", "cost_center": c.cost_center or "",
            "reversal_reason": c.reversal_reason or "",
            "current_stage": _current_stage(c),
        })
    return out


def _current_stage(c: Claim) -> str:
    steps = c.approval_steps or []
    idx = int(c.current_step or 0)
    if c.status == ClaimStatus.PENDING_APPROVAL and 0 <= idx < len(steps):
        return steps[idx].get("label") or steps[idx].get("approver_type") or "Review"
    return ""


def _age_days(iso: str) -> int:
    if not iso:
        return 0
    try:
        return max(0, (date.today() - date.fromisoformat(iso)).days)
    except ValueError:
        return 0


def _band(age: int) -> str:
    return "0–3" if age <= 3 else "4–7" if age <= 7 else "8–14" if age <= 14 else "15+"


_COLUMNS = {
    "claims_register": [
        ("claim_number", "Claim #"), ("employee", "Employee"), ("department", "Dept"),
        ("category", "Category"), ("expense_date", "Expense Date"), ("amount", "Amount"),
        ("status", "Status"), ("settlement_method", "Settlement"), ("settled_at", "Settled On")],
    "settlement_summary": [
        ("claim_number", "Claim #"), ("employee", "Employee"), ("category", "Category"),
        ("approved_amount", "Settled Amount"), ("settlement_method", "Method"),
        ("settlement_number", "Settlement #"), ("payroll_ref", "Payroll Ref"), ("settled_at", "Settled On")],
    "by_category": [
        ("category", "Category"), ("count", "Claims"), ("amount", "Total Amount"), ("settled", "Settled Amount")],
    "by_employee": [
        ("rank", "#"), ("employee", "Employee"), ("employee_code", "Code"), ("department", "Dept"),
        ("count", "Claims"), ("amount", "Total Claimed"), ("settled", "Settled")],
    "pending_approvals": [
        ("claim_number", "Claim #"), ("employee", "Employee"), ("department", "Dept"),
        ("category", "Category"), ("amount", "Amount"), ("current_stage", "Awaiting"), ("claim_date", "Submitted")],
    "aging": [
        ("claim_number", "Claim #"), ("employee", "Employee"), ("category", "Category"),
        ("current_stage", "Awaiting"), ("amount", "Amount"), ("submitted", "Submitted"), ("age_days", "Age (days)")],
    "payroll_settlements": [
        ("claim_number", "Claim #"), ("employee", "Employee"), ("category", "Category"),
        ("approved_amount", "Amount"), ("payroll_ref", "Payroll Ref"), ("settled_at", "Settled On"), ("status", "Status")],
    "reversals": [
        ("claim_number", "Claim #"), ("employee", "Employee"), ("category", "Category"),
        ("approved_amount", "Amount"), ("reversal_reason", "Reason")],
}


def shape(key: str, rows: List[dict]) -> List[dict]:
    if key == "claims_register":
        return rows
    if key == "settlement_summary":
        return [r for r in rows if r["status"] in ("SETTLED", "PAID")]
    if key == "pending_approvals":
        return [r for r in rows if r["status"] == "PENDING_APPROVAL"]
    if key == "payroll_settlements":
        return [r for r in rows if r["settlement_method"] == "PAYROLL"]
    if key == "reversals":
        return [r for r in rows if r["status"] == "REVERSED"]
    if key == "by_category":
        agg: Dict[str, dict] = {}
        for r in rows:
            g = agg.setdefault(r["category"], {"category": r["category"], "count": 0, "amount": 0.0,
                                               "settled": 0.0, "color_hex": r.get("color_hex")})
            g["count"] += 1
            g["amount"] += r["amount"]
            if r["status"] in ("SETTLED", "PAID") and r["approved_amount"]:
                g["settled"] += r["approved_amount"]
        return sorted(agg.values(), key=lambda g: -g["amount"])
    if key == "by_employee":
        agg2: Dict[tuple, dict] = {}
        for r in rows:
            k = (r["employee"], r.get("employee_code") or "", r.get("department") or "—")
            g = agg2.setdefault(k, {"employee": k[0], "employee_code": k[1], "department": k[2],
                                    "count": 0, "amount": 0.0, "settled": 0.0})
            g["count"] += 1
            g["amount"] += r["amount"]
            if r["status"] in ("SETTLED", "PAID") and r["approved_amount"]:
                g["settled"] += r["approved_amount"]
        out = sorted(agg2.values(), key=lambda g: -g["amount"])
        for i, g in enumerate(out, 1):
            g["rank"] = i
        return out
    if key == "aging":
        out = []
        for r in rows:
            if r["status"] != "PENDING_APPROVAL":
                continue
            age = _age_days(r.get("claim_date") or r.get("expense_date"))
            out.append({**r, "submitted": r.get("claim_date") or "", "age_days": age, "age_band": _band(age)})
        return sorted(out, key=lambda x: -x["age_days"])
    return rows


def summarize(key: str, rows: List[dict]) -> dict:
    s: Dict[str, Any] = {"count": len(rows)}
    if key == "by_category":
        s.update(categories=len(rows), total_amount=sum(r["amount"] for r in rows),
                 total_settled=sum(r["settled"] for r in rows),
                 top=(rows[0]["category"] if rows else "—"))
        return s
    if key == "by_employee":
        s.update(employees=len(rows), total_amount=sum(r["amount"] for r in rows),
                 total_settled=sum(r["settled"] for r in rows),
                 top=(rows[0]["employee"] if rows else "—"))
        return s
    if key == "aging":
        bands = {"0–3": 0, "4–7": 0, "8–14": 0, "15+": 0}
        for r in rows:
            bands[r["age_band"]] += 1
        s.update(total_amount=sum(r["amount"] for r in rows),
                 oldest=max((r["age_days"] for r in rows), default=0),
                 avg_age=round(sum(r["age_days"] for r in rows) / len(rows), 1) if rows else 0,
                 bands=bands)
        return s
    s["total_amount"] = sum((r.get("amount") or 0) for r in rows)
    s["total_settled"] = sum((r.get("approved_amount") or 0) for r in rows if r.get("status") in ("SETTLED", "PAID"))
    s["by_status"] = dict(Counter(r["status"] for r in rows))
    s["by_method"] = dict(Counter(r["settlement_method"] for r in rows if r.get("settlement_method")))
    s["by_stage"] = dict(Counter(r["current_stage"] for r in rows if r.get("current_stage")))
    s["employees"] = len({r["employee"] for r in rows})
    return s


# ════════════════════════════════════════════════════════════════════════════
# CSV
# ════════════════════════════════════════════════════════════════════════════
def render_csv(key: str, rows: List[dict]) -> bytes:
    cols = _COLUMNS.get(key, _COLUMNS["claims_register"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in cols])
    for r in rows:
        w.writerow([_fmt(r.get(k)) for k, _ in cols])
    return buf.getvalue().encode("utf-8-sig")


# ════════════════════════════════════════════════════════════════════════════
# PDF — a bespoke, editorial layout for EVERY report (no shared template).
# Each report owns a distinct composition: dark vault, color-blocked spectrum,
# ranked leaderboard, pipeline rail, data-viz gauge, payslip document, hazard
# editorial. Covers AND body layouts differ per report.
# ════════════════════════════════════════════════════════════════════════════
_STATUS_INK = {
    "DRAFT": ("#78716c", "#f4f4f3"), "PENDING_APPROVAL": ("#b45309", "#fff5e6"),
    "APPROVED": ("#047857", "#e7f8f0"), "RETURNED": ("#c2410c", "#fff1e8"),
    "REJECTED": ("#b91c1c", "#fdecec"), "SETTLED": ("#0f766e", "#e7f7f4"),
    "PAID": ("#15803d", "#e9f9ef"), "CANCELLED": ("#78716c", "#f4f4f3"),
    "REVERSED": ("#7c3aed", "#f3eeff"),
}


def _pill(status: str) -> str:
    ink, bg = _STATUS_INK.get(status, ("#78716c", "#f4f4f3"))
    label = status.replace("_", " ").title()
    return f'<span class="pill" style="color:{ink};background:{bg};border-color:{ink}40">{esc(label)}</span>'


_FONT = "'Segoe UI','Helvetica Neue','Arial',sans-serif"

_BASE_CSS = f"""
@page {{ size: A4 landscape; margin: 16mm 15mm 17mm;
  @bottom-left {{ content: "FOURRECK · REIMBURSEMENTS"; font-size: 7pt; letter-spacing: 2.5px; color: #b9a890; }}
  @bottom-right {{ content: counter(page) " / " counter(pages); font-size: 7pt; letter-spacing: 1px; color: #b9a890; }} }}
@page :first {{ margin: 0; @bottom-left {{ content: ""; }} @bottom-right {{ content: ""; }} }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ font-family: {_FONT}; color: #16110a; }}
.cover {{ position: relative; width: 297mm; height: 210mm; overflow: hidden; page-break-after: always; }}
.num {{ font-variant-numeric: tabular-nums; }}
/* shared body section intro */
.sec {{ margin: 0 0 6mm; }}
.sec .skicker {{ font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; font-weight: 700; }}
.sec h2 {{ font-size: 23pt; font-weight: 800; margin: 1mm 0 0; letter-spacing: -0.6px; }}
.sec .meta {{ font-size: 8.5pt; color: #9a7b5a; margin-top: 1.5mm; letter-spacing: .3px; }}
.sec .accentrule {{ height: 3pt; width: 36mm; margin-top: 3mm; border-radius: 2pt; }}
table.dt {{ width: 100%; border-collapse: collapse; font-size: 8.6pt; }}
table.dt td {{ padding: 5pt 6pt; color: #3a2415; }}
table.dt td.n, table.dt th.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
.pill {{ font-size: 7pt; font-weight: 700; padding: 1.5pt 6pt; border-radius: 999px; border: .75pt solid; white-space: nowrap; }}
.totbar {{ margin-top: 5mm; display: flex; justify-content: flex-end; gap: 8mm; font-size: 9.5pt; font-weight: 700; padding-top: 3mm; }}
.empty {{ padding: 18mm; text-align: center; color: #9a7b5a; font-size: 11pt; border: 1.5pt dashed #d8cdb8; border-radius: 4mm; }}
"""


# ── COVER 01 · claims_register — light editorial register w/ TOC ───────────
def _cov_register(meta, s, rows):
    a = meta["accent"]
    toc = "".join(
        f'<div class="cr-row"><span class="cr-n">{esc(r["claim_number"])}</span>'
        f'<span class="cr-emp">{esc((r["employee"] or "")[:22])}</span>'
        f'<span class="cr-amt">{inr(r["amount"])}</span></div>' for r in rows[:8])
    css = f"""
    .cr {{ background: #fbf7ef; color: #1a1208; }}
    .cr .top {{ position: absolute; top: 15mm; left: 18mm; right: 18mm; display: flex; justify-content: space-between;
      font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #9a7b5a; }}
    .cr .top b {{ color: {a}; }}
    .cr .ghost {{ position: absolute; left: 12mm; top: 70mm; font-size: 210pt; font-weight: 800; color: {a}10;
      line-height: .8; letter-spacing: -8px; }}
    .cr .kick {{ position: absolute; left: 18mm; top: 70mm; font-size: 10pt; letter-spacing: 5px; text-transform: uppercase; color: {a}; font-weight: 700; }}
    .cr h1 {{ position: absolute; left: 16mm; top: 78mm; font-size: 66pt; font-weight: 800; letter-spacing: -2.5px; margin: 0; line-height: .9; }}
    .cr .rule {{ position: absolute; left: 18mm; top: 130mm; width: 54mm; height: 4pt; background: {a}; border-radius: 2pt; }}
    .cr .desc {{ position: absolute; left: 18mm; top: 136mm; width: 118mm; font-size: 11pt; color: #6b5234; }}
    .cr .toc {{ position: absolute; right: 18mm; top: 60mm; width: 112mm; }}
    .cr .toc-h {{ font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #9a7b5a; margin-bottom: 2mm; }}
    .cr-row {{ display: flex; align-items: baseline; gap: 6mm; padding: 2.6mm 0; border-bottom: .75pt solid {a}30; }}
    .cr-n {{ font-family: monospace; font-size: 8.5pt; color: {a}; width: 34mm; }}
    .cr-emp {{ flex: 1; font-size: 9.5pt; color: #2a1c10; }}
    .cr-amt {{ font-size: 9.5pt; font-weight: 700; }}
    .cr .stats {{ position: absolute; left: 18mm; bottom: 16mm; right: 18mm; display: flex; gap: 16mm;
      border-top: 2pt solid #1a1208; padding-top: 5mm; }}
    .cr .st .v {{ font-size: 24pt; font-weight: 800; }}
    .cr .st .l {{ font-size: 8pt; letter-spacing: 1.5px; text-transform: uppercase; color: #9a7b5a; }}
    """
    html = f"""
    <section class="cover cr">
      <div class="ghost num">{s['count']}</div>
      <div class="top"><span><b>FOURRECK</b> · Reimbursements</span><span>Report 01 · Confidential</span></div>
      <div class="kick">The complete ledger</div>
      <h1>Claims<br>Register</h1>
      <div class="rule"></div>
      <div class="desc">{esc(meta['description'])}</div>
      <div class="toc"><div class="toc-h">Most recent on the tape</div>
        {toc or '<div style="color:#b09877;font-size:9.5pt;">No claims in range.</div>'}</div>
      <div class="stats">
        <div class="st"><div class="v num">{s['count']}</div><div class="l">Claims</div></div>
        <div class="st"><div class="v">{inr_compact(s['total_amount'])}</div><div class="l">Total claimed</div></div>
        <div class="st"><div class="v">{inr_compact(s['total_settled'])}</div><div class="l">Settled</div></div>
        <div class="st"><div class="v num">{s.get('employees', 0)}</div><div class="l">Employees</div></div>
      </div>
    </section>"""
    return css, html


# ── COVER 02 · settlement_summary — DARK vault, giant money numeral ────────
def _cov_vault(meta, s, rows):
    a = meta["accent"]
    methods = sorted(s.get("by_method", {}).items(), key=lambda x: -x[1])
    chips = "".join(f'<span class="vt-chip">{esc(m.replace("_", " ").title())} · {c}</span>' for m, c in methods) \
        or '<span class="vt-chip">No settlements yet</span>'
    val = inr_compact(s['total_settled'] or s['total_amount'])
    css = f"""
    .vt {{ background: radial-gradient(95mm 95mm at 76% 42%, {a}55, #061b18 58%), #04130f; color: #eafff8; }}
    .vt .top {{ position: absolute; top: 15mm; left: 18mm; right: 18mm; display: flex; justify-content: space-between;
      font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #67c9b8; }}
    .vt .kick {{ position: absolute; left: 18mm; top: 56mm; font-size: 11pt; letter-spacing: 6px; text-transform: uppercase; color: {a}; font-weight: 700; }}
    .vt h1 {{ position: absolute; left: 17mm; top: 64mm; font-size: 44pt; font-weight: 800; margin: 0; color: #fff; letter-spacing: -1px; }}
    .vt .money {{ position: absolute; left: 18mm; top: 96mm; font-size: 92pt; font-weight: 800; color: #fff; letter-spacing: -4px; line-height: .82; }}
    .vt .sub {{ position: absolute; left: 18mm; top: 152mm; font-size: 11.5pt; color: #9fded2; max-width: 130mm; }}
    .vt .chips {{ position: absolute; left: 18mm; bottom: 18mm; display: flex; gap: 3mm; flex-wrap: wrap; }}
    .vt-chip {{ font-size: 8.5pt; padding: 2mm 4mm; border-radius: 999px; background: {a}26; color: #d7fff5; border: .75pt solid {a}66; }}
    .vt .rings {{ position: absolute; right: 16mm; top: 50mm; }}
    """
    html = f"""
    <section class="cover vt">
      <svg class="rings" width="230" height="230" viewBox="0 0 220 220">
        <circle cx="110" cy="110" r="100" fill="none" stroke="{a}40" stroke-width="2"/>
        <circle cx="110" cy="110" r="76" fill="none" stroke="{a}88" stroke-width="2" stroke-dasharray="3 6"/>
        <circle cx="110" cy="110" r="52" fill="{a}26" stroke="{a}" stroke-width="3"/>
        <text x="110" y="122" text-anchor="middle" font-size="40" font-weight="800" fill="#fff">₹</text>
      </svg>
      <div class="top"><span>FOURRECK · Disbursement Desk</span><span>Report 02 · Confidential</span></div>
      <div class="kick">Total disbursed</div>
      <h1>Settlement Summary</h1>
      <div class="money">{val}</div>
      <div class="sub">{esc(meta['description'])} · {s['count']} settlements across {s.get('employees', 0)} employees.</div>
      <div class="chips">{chips}</div>
    </section>"""
    return css, html


# ── COVER 03 · by_category — color-blocked spectrum band ───────────────────
_PALETTE = ["#7c3aed", "#0d9488", "#ea580c", "#2563eb", "#db2777", "#16a34a", "#d97706", "#0891b2", "#9333ea", "#dc2626"]


def _cov_spectrum(meta, s, rows):
    a = meta["accent"]
    tot = s["total_amount"] or 1
    segs = "".join(
        f'<div class="sp-seg" style="width:{max(3, r["amount"] / tot * 100):.2f}%;background:{r.get("color_hex") or _PALETTE[i % len(_PALETTE)]};"></div>'
        for i, r in enumerate(rows))
    labels = "".join(
        f'<div class="sp-l" style="border-top-color:{r.get("color_hex") or _PALETTE[i % len(_PALETTE)]}">'
        f'<div class="sp-pct">{r["amount"] / tot * 100:.0f}%</div><div class="sp-nm">{esc(r["category"])}</div>'
        f'<div class="sp-amt">{inr_compact(r["amount"])}</div></div>' for i, r in enumerate(rows[:6]))
    css = f"""
    .sp {{ background: #faf7ff; color: #1c1033; }}
    .sp .top {{ position: absolute; top: 15mm; left: 18mm; right: 18mm; display: flex; justify-content: space-between;
      font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #8b7bb0; }}
    .sp .top b {{ color: {a}; }}
    .sp .kick {{ position: absolute; left: 18mm; top: 42mm; font-size: 10pt; letter-spacing: 5px; text-transform: uppercase; color: {a}; font-weight: 700; }}
    .sp h1 {{ position: absolute; left: 17mm; top: 48mm; font-size: 58pt; font-weight: 800; margin: 0; letter-spacing: -2px; color: #1c1033; line-height: .9; }}
    .sp .count {{ position: absolute; right: 18mm; top: 50mm; text-align: right; }}
    .sp .count .v {{ font-size: 76pt; font-weight: 800; color: {a}; line-height: .78; }}
    .sp .count .l {{ font-size: 9pt; letter-spacing: 2px; text-transform: uppercase; color: #8b7bb0; }}
    .sp .bar {{ position: absolute; left: 18mm; right: 18mm; top: 108mm; height: 20mm; display: flex; border-radius: 4mm; overflow: hidden; box-shadow: 0 6mm 18mm {a}22; }}
    .sp-seg {{ height: 100%; }}
    .sp .labels {{ position: absolute; left: 18mm; right: 18mm; top: 136mm; display: flex; gap: 5mm; }}
    .sp-l {{ flex: 1; border-top: 4pt solid; padding-top: 3mm; }}
    .sp-pct {{ font-size: 22pt; font-weight: 800; color: #1c1033; }}
    .sp-nm {{ font-size: 10pt; font-weight: 600; color: #3a2c52; }}
    .sp-amt {{ font-size: 9pt; color: #8b7bb0; }}
    """
    html = f"""
    <section class="cover sp">
      <div class="top"><span><b>FOURRECK</b> · Spend taxonomy</span><span>Report 03 · Confidential</span></div>
      <div class="kick">Spend spectrum</div>
      <h1>Category<br>Analysis</h1>
      <div class="count"><div class="v num">{s.get('categories', 0)}</div><div class="l">Categories</div></div>
      <div class="bar">{segs}</div>
      <div class="labels">{labels or '<div style="color:#8b7bb0;">No category spend recorded.</div>'}</div>
    </section>"""
    return css, html


# ── COVER 04 · by_employee — ranked leaderboard ────────────────────────────
_MEDALS = ["#f59e0b", "#94a3b8", "#b45309"]


def _cov_podium(meta, s, rows):
    a = meta["accent"]
    top = rows[:5]
    maxa = (top[0]["amount"] if top else 1) or 1
    lead = ""
    for i, r in enumerate(top):
        w = max(8, r["amount"] / maxa * 100)
        rc = _MEDALS[i] if i < 3 else a
        lead += (f'<div class="pd-row"><span class="pd-rank" style="background:{rc}">{i + 1}</span>'
                 f'<span class="pd-nm">{esc((r["employee"] or "")[:24])}</span>'
                 f'<span class="pd-bar"><i style="width:{w:.0f}%;background:{a}"></i></span>'
                 f'<span class="pd-amt">{inr_compact(r["amount"])}</span></div>')
    css = f"""
    .pd {{ background: #f3f3fe; color: #1a1640; }}
    .pd .top {{ position: absolute; top: 15mm; left: 18mm; right: 18mm; display: flex; justify-content: space-between;
      font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #7c79b8; }}
    .pd .top b {{ color: {a}; }}
    .pd .ghost {{ position: absolute; right: 16mm; top: 30mm; font-size: 150pt; font-weight: 800; color: {a}12; line-height: .8; }}
    .pd .kick {{ position: absolute; left: 18mm; top: 42mm; font-size: 10pt; letter-spacing: 5px; text-transform: uppercase; color: {a}; font-weight: 700; }}
    .pd h1 {{ position: absolute; left: 17mm; top: 48mm; font-size: 54pt; font-weight: 800; margin: 0; letter-spacing: -2px; color: #1a1640; line-height: .9; }}
    .pd .board {{ position: absolute; left: 18mm; right: 18mm; top: 100mm; }}
    .pd-row {{ display: flex; align-items: center; gap: 5mm; padding: 3mm 0; border-bottom: .75pt solid {a}26; }}
    .pd-rank {{ width: 9mm; height: 9mm; border-radius: 50%; color: #fff; font-weight: 800; font-size: 11pt; display: flex; align-items: center; justify-content: center; }}
    .pd-nm {{ width: 64mm; font-size: 11pt; font-weight: 600; }}
    .pd-bar {{ flex: 1; height: 7pt; background: {a}14; border-radius: 999px; overflow: hidden; }}
    .pd-bar i {{ display: block; height: 100%; border-radius: 999px; }}
    .pd-amt {{ width: 28mm; text-align: right; font-size: 11pt; font-weight: 800; }}
    """
    html = f"""
    <section class="cover pd">
      <div class="ghost num">{s.get('employees', 0)}</div>
      <div class="top"><span><b>FOURRECK</b> · People &amp; spend</span><span>Report 04 · Confidential</span></div>
      <div class="kick">Who claims the most</div>
      <h1>Employee<br>Spend Ledger</h1>
      <div class="board">{lead or '<div style="color:#7c79b8;">No claims in range.</div>'}</div>
    </section>"""
    return css, html


# ── COVER 05 · pending_approvals — pipeline stage rail ─────────────────────
def _cov_pipeline(meta, s, rows):
    a, deep = meta["accent"], meta["deep"]
    stages = ["Submitted", "Manager", "Finance", "Approved"]
    counts = s.get("by_stage", {})
    oldest = max((_age_days(r.get("claim_date")) for r in rows), default=0)
    n = len(stages)
    nodes = ""
    for i, st in enumerate(stages):
        x = 8 + i * (84 / (n - 1))
        if i == 0:
            c = s["count"]
        elif i == n - 1:
            c = 0
        else:
            c = sum(v for k, v in counts.items() if st.lower() in (k or "").lower())
        nodes += f'<div class="pp-node" style="left:{x:.1f}%"><span class="pp-dot">{c}</span><span class="pp-lab">{st}</span></div>'
    css = f"""
    .pp {{ background: #fffaf0; color: #3a2407; }}
    .pp .top {{ position: absolute; top: 15mm; left: 18mm; right: 18mm; display: flex; justify-content: space-between;
      font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #b08a52; }}
    .pp .top b {{ color: {a}; }}
    .pp .kick {{ position: absolute; left: 18mm; top: 42mm; font-size: 10pt; letter-spacing: 5px; text-transform: uppercase; color: {a}; font-weight: 700; }}
    .pp h1 {{ position: absolute; left: 17mm; top: 48mm; font-size: 58pt; font-weight: 800; margin: 0; letter-spacing: -2px; color: #3a2407; line-height: .9; }}
    .pp .big {{ position: absolute; right: 18mm; top: 50mm; text-align: right; }}
    .pp .big .v {{ font-size: 82pt; font-weight: 800; color: {a}; line-height: .78; }}
    .pp .big .l {{ font-size: 9pt; letter-spacing: 2px; text-transform: uppercase; color: #b08a52; }}
    .pp .rail {{ position: absolute; left: 18mm; right: 18mm; top: 118mm; height: 30mm; }}
    .pp .railline {{ position: absolute; left: 8%; right: 8%; top: 8mm; height: 2pt; background: {a}55; }}
    .pp-node {{ position: absolute; top: 0; transform: translateX(-50%); text-align: center; }}
    .pp-dot {{ display: flex; align-items: center; justify-content: center; width: 16mm; height: 16mm; border-radius: 50%;
      background: {a}14; border: 2pt solid {a}; color: {deep}; font-weight: 800; font-size: 13pt; margin: 0 auto; }}
    .pp-lab {{ display: block; font-size: 9pt; color: #7c5a2a; margin-top: 2mm; }}
    .pp .oldest {{ position: absolute; left: 18mm; bottom: 18mm; font-size: 10.5pt; color: #6b4a1a; }}
    .pp .oldest b {{ color: {a}; font-size: 14pt; }}
    """
    html = f"""
    <section class="cover pp">
      <div class="top"><span><b>FOURRECK</b> · Decision desk</span><span>Report 05 · Confidential</span></div>
      <div class="kick">Awaiting decision</div>
      <h1>Pending<br>Approvals</h1>
      <div class="big"><div class="v num">{s['count']}</div><div class="l">In the queue</div></div>
      <div class="rail"><div class="railline"></div>{nodes}</div>
      <div class="oldest">Oldest claim has waited <b>{oldest} days</b> · {inr_compact(s['total_amount'])} of value held in the pipeline.</div>
    </section>"""
    return css, html


# ── COVER 06 · aging — DARK data-viz, gauge + histogram ────────────────────
def _cov_stopwatch(meta, s, rows):
    a = meta["accent"]
    bands = s.get("bands", {})
    maxb = (max(bands.values()) if bands else 1) or 1
    pal = {"0–3": "#22c55e", "4–7": "#eab308", "8–14": "#f97316", "15+": "#ef4444"}
    bars = ""
    for label in ["0–3", "4–7", "8–14", "15+"]:
        v = bands.get(label, 0)
        h = 8 + (v / maxb) * 40
        bars += (f'<div class="ag-bcol"><div class="ag-bv">{v}</div>'
                 f'<div class="ag-bar" style="height:{h:.0f}mm;background:{pal[label]}"></div>'
                 f'<div class="ag-bl">{label} d</div></div>')
    oldest = s.get("oldest", 0)
    dash = min(1.0, oldest / 30) * 402
    css = f"""
    .ag {{ background: radial-gradient(72mm 72mm at 78% 38%, {a}40, #14060b 60%), #0c0508; color: #ffe6ec; }}
    .ag .top {{ position: absolute; top: 15mm; left: 18mm; right: 18mm; display: flex; justify-content: space-between;
      font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #d98aa0; }}
    .ag .kick {{ position: absolute; left: 18mm; top: 54mm; font-size: 11pt; letter-spacing: 6px; text-transform: uppercase; color: {a}; font-weight: 700; }}
    .ag h1 {{ position: absolute; left: 17mm; top: 60mm; font-size: 48pt; font-weight: 800; margin: 0; color: #fff; letter-spacing: -1.5px; line-height: .92; }}
    .ag .sub {{ position: absolute; left: 18mm; top: 100mm; color: #e4a9b8; font-size: 11pt; max-width: 120mm; }}
    .ag .hist {{ position: absolute; left: 18mm; bottom: 22mm; display: flex; align-items: flex-end; gap: 8mm; }}
    .ag-bcol {{ text-align: center; }}
    .ag-bv {{ font-size: 14pt; font-weight: 800; color: #fff; }}
    .ag-bar {{ width: 18mm; border-radius: 2mm 2mm 0 0; margin: 2mm auto 0; }}
    .ag-bl {{ font-size: 8.5pt; color: #d98aa0; margin-top: 2mm; }}
    .ag .gauge {{ position: absolute; right: 26mm; top: 62mm; }}
    """
    html = f"""
    <section class="cover ag">
      <svg class="gauge" width="210" height="210" viewBox="0 0 160 160">
        <circle cx="80" cy="80" r="64" fill="none" stroke="{a}33" stroke-width="13"/>
        <circle cx="80" cy="80" r="64" fill="none" stroke="{a}" stroke-width="13" stroke-linecap="round"
          stroke-dasharray="{dash:.0f} 402" transform="rotate(-90 80 80)"/>
        <text x="80" y="74" text-anchor="middle" font-size="42" font-weight="800" fill="#fff">{oldest}</text>
        <text x="80" y="98" text-anchor="middle" font-size="10" fill="#d98aa0">days oldest</text>
      </svg>
      <div class="top"><span>FOURRECK · Turnaround</span><span>Report 06 · Confidential</span></div>
      <div class="kick">How long claims wait</div>
      <h1>Approval Aging<br>&amp; TAT</h1>
      <div class="sub">{s['count']} claims in flight · average wait {s.get('avg_age', 0)} days · {inr_compact(s['total_amount'])} held.</div>
      <div class="hist">{bars}</div>
    </section>"""
    return css, html


# ── COVER 07 · payroll_settlements — payslip document hero ─────────────────
def _cov_payslip(meta, s, rows):
    a, deep = meta["accent"], meta["deep"]
    lines = "".join(
        f'<div class="ps-line"><span>{esc((r["employee"] or "")[:22])}</span><b>{inr(r["approved_amount"] or r["amount"])}</b></div>'
        for r in rows[:6])
    net = inr_compact(s['total_settled'] or s['total_amount'])
    css = f"""
    .ps {{ background: #eef9f0; color: #0f2e1a; }}
    .ps .top {{ position: absolute; top: 15mm; left: 18mm; right: 18mm; display: flex; justify-content: space-between;
      font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #5a8a6a; }}
    .ps .top b {{ color: {a}; }}
    .ps .kick {{ position: absolute; left: 18mm; top: 48mm; font-size: 10pt; letter-spacing: 5px; text-transform: uppercase; color: {a}; font-weight: 700; }}
    .ps h1 {{ position: absolute; left: 17mm; top: 54mm; font-size: 52pt; font-weight: 800; margin: 0; letter-spacing: -2px; color: #0f2e1a; line-height: .9; }}
    .ps .total {{ position: absolute; left: 18mm; top: 122mm; }}
    .ps .total .v {{ font-size: 62pt; font-weight: 800; color: {a}; line-height: .78; }}
    .ps .total .l {{ font-size: 9pt; letter-spacing: 2px; text-transform: uppercase; color: #5a8a6a; }}
    .ps .stub {{ position: absolute; right: 18mm; top: 50mm; width: 104mm; background: #fff; border: 1pt solid {a}33;
      border-radius: 3mm; padding: 7mm; box-shadow: 0 8mm 22mm {a}1f; }}
    .ps .stub-h {{ border-bottom: 2pt solid {a}; padding-bottom: 2mm; margin-bottom: 3mm; font-weight: 800; letter-spacing: 1px; font-size: 9pt; color: {deep}; }}
    .ps-line {{ display: flex; justify-content: space-between; padding: 2mm 0; border-bottom: .75pt dotted {a}40; font-size: 9.5pt; color: #0f2e1a; }}
    .ps .stub-net {{ display: flex; justify-content: space-between; margin-top: 3mm; font-weight: 800; color: {a}; font-size: 12pt; }}
    .ps .perf {{ position: absolute; left: 0; right: 0; bottom: -1px; height: 7px;
      background-image: radial-gradient(circle at 6px 7px, transparent 4px, {a}22 4.5px); background-size: 12px 7px; }}
    """
    html = f"""
    <section class="cover ps">
      <div class="top"><span><b>FOURRECK</b> · Payroll cycle</span><span>Report 07 · Confidential</span></div>
      <div class="kick">Folded into payslips</div>
      <h1>Payroll<br>Settlements</h1>
      <div class="total"><div class="v">{net}</div><div class="l">Net folded into pay</div></div>
      <div class="stub">
        <div class="stub-h">PAYROLL STUB · CLAIMS · {s['count']}</div>
        {lines or '<div style="color:#5a8a6a;font-size:9.5pt;">No payroll settlements.</div>'}
        <div class="stub-net"><span>NET</span><span>{net}</span></div>
        <span class="perf"></span>
      </div>
    </section>"""
    return css, html


# ── COVER 08 · reversals — hazard editorial ────────────────────────────────
def _cov_clawback(meta, s, rows):
    a = meta["accent"]
    css = f"""
    .rv {{ background: #fff4f4; color: #3a0f0f; }}
    .rv .haz {{ position: absolute; top: 0; left: 0; right: 0; height: 24mm;
      background: repeating-linear-gradient(45deg, {a} 0 9mm, #fff4f4 9mm 18mm); opacity: .16; }}
    .rv .top {{ position: absolute; top: 15mm; left: 18mm; right: 18mm; display: flex; justify-content: space-between;
      font-size: 8pt; letter-spacing: 3px; text-transform: uppercase; color: #a86a6a; }}
    .rv .top b {{ color: {a}; }}
    .rv .ghost {{ position: absolute; right: 6mm; bottom: 2mm; font-size: 250pt; font-weight: 800; color: {a}10; line-height: .7; }}
    .rv .kick {{ position: absolute; left: 18mm; top: 62mm; font-size: 11pt; letter-spacing: 6px; text-transform: uppercase; color: {a}; font-weight: 700; }}
    .rv h1 {{ position: absolute; left: 16mm; top: 68mm; font-size: 78pt; font-weight: 800; margin: 0; letter-spacing: -3px; color: #3a0f0f; line-height: .9; }}
    .rv .sub {{ position: absolute; left: 18mm; top: 150mm; max-width: 132mm; color: #7a3a3a; font-size: 11pt; }}
    .rv .stat {{ position: absolute; left: 18mm; bottom: 18mm; display: flex; gap: 16mm; }}
    .rv .stat .v {{ font-size: 26pt; font-weight: 800; color: {a}; }}
    .rv .stat .l {{ font-size: 8pt; letter-spacing: 1.5px; text-transform: uppercase; color: #a86a6a; }}
    """
    html = f"""
    <section class="cover rv">
      <div class="haz"></div>
      <div class="ghost num">{s['count']}</div>
      <div class="top"><span><b>FOURRECK</b> · Clawbacks</span><span>Report 08 · Confidential</span></div>
      <div class="kick">Corrections on record</div>
      <h1>Reversals</h1>
      <div class="sub">{esc(meta['description'])} Every reversal carries an auditable reason and is netted back from the employee.</div>
      <div class="stat">
        <div><div class="v num">{s['count']}</div><div class="l">Reversed</div></div>
        <div><div class="v">{inr_compact(s['total_amount'])}</div><div class="l">Clawed back</div></div>
        <div><div class="v num">{s.get('employees', 0)}</div><div class="l">Employees</div></div>
      </div>
    </section>"""
    return css, html


_COVERS = {
    "claims_register": _cov_register, "settlement_summary": _cov_vault,
    "by_category": _cov_spectrum, "by_employee": _cov_podium,
    "pending_approvals": _cov_pipeline, "aging": _cov_stopwatch,
    "payroll_settlements": _cov_payslip, "reversals": _cov_clawback,
}


# ── shared body section intro ───────────────────────────────────────────────
def _sec_intro(meta, subtitle, n):
    a, deep = meta["accent"], meta["deep"]
    return (f'<div class="sec"><div class="skicker" style="color:{a}">{esc(meta["tagline"])}</div>'
            f'<h2 style="color:{deep}">{esc(meta["name"])}</h2>'
            f'<div class="meta">{esc(subtitle)} · {n} records · generated {fmt_date(date.today())}</div>'
            f'<div class="accentrule" style="background:{a}"></div></div>')


# ── body A · themed data table (register / settlement / pending / payroll / reversals)
def _body_table(key, meta, s, rows):
    a, soft, deep = meta["accent"], meta["soft"], meta["deep"]
    cols = _COLUMNS.get(key, _COLUMNS["claims_register"])
    head = "".join(
        f'<th class="{"n" if k in _MONEY_KEYS or k in ("count", "age_days", "rank") else ""}">{esc(l)}</th>'
        for k, l in cols)
    body = ""
    for r in rows:
        tds = ""
        for k, _ in cols:
            v = r.get(k)
            if k == "status":
                tds += f"<td>{_pill(str(v))}</td>"
            elif k in _MONEY_KEYS and isinstance(v, (int, float)):
                tds += f'<td class="n">{_fmt(float(v))}</td>'
            elif k in ("count", "rank"):
                tds += f'<td class="n">{esc(v)}</td>'
            elif k in ("expense_date", "claim_date", "settled_at", "submitted"):
                tds += f"<td>{esc(fmt_date(v))}</td>"
            else:
                tds += f"<td>{esc(v)}</td>"
        body += f"<tr>{tds}</tr>"
    css = f"""
    table.dt th {{ background: {a}; color: #fff; padding: 5.5pt 6pt; text-align: left; font-size: 7.6pt; letter-spacing: .4px; text-transform: uppercase; }}
    table.dt td {{ border-bottom: .75pt solid {a}1f; }}
    table.dt tr:nth-child(even) td {{ background: {soft}; }}
    .totbar {{ border-top: 1pt dashed {a}66; color: {deep}; }} .totbar .ta {{ color: {a}; }}
    """
    intro = _sec_intro(meta, "Full detail", len(rows))
    if not rows:
        return css, intro + '<div class="empty">No records matched the selected filters.</div>'
    total = sum((r.get("amount") or 0) for r in rows)
    settled = sum((r.get("approved_amount") or 0) for r in rows if r.get("status") in ("SETTLED", "PAID"))
    html = (intro + f'<table class="dt"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
            f'<div class="totbar"><span>Records: {len(rows)}</span>'
            f'<span>Total: <span class="ta">{inr(total)}</span></span>'
            f'<span>Settled: <span class="ta">{inr(settled)}</span></span></div>')
    return css, html


# ── body B · ranked leaderboard (by_employee) ──────────────────────────────
def _body_leaderboard(key, meta, s, rows):
    a, deep = meta["accent"], meta["deep"]
    maxa = (rows[0]["amount"] if rows else 1) or 1
    items = ""
    for i, r in enumerate(rows):
        w = max(4, r["amount"] / maxa * 100)
        rc = _MEDALS[i] if i < 3 else a
        items += (f'<div class="lb-row"><span class="lb-rank" style="background:{rc}">{r["rank"]}</span>'
                  f'<div class="lb-mid"><div class="lb-nm">{esc(r["employee"])} <span class="lb-dept">{esc(r["department"])}</span></div>'
                  f'<div class="lb-bar"><i style="width:{w:.1f}%;background:{a}"></i></div></div>'
                  f'<div class="lb-r"><div class="lb-amt">{inr(r["amount"])}</div>'
                  f'<div class="lb-sub">{r["count"]} claims · {inr_compact(r["settled"])} settled</div></div></div>')
    css = f"""
    .lb-row {{ display: flex; align-items: center; gap: 5mm; padding: 3.5mm 0; border-bottom: .75pt solid {a}1f; }}
    .lb-rank {{ width: 9mm; height: 9mm; border-radius: 50%; color: #fff; font-weight: 800; font-size: 11pt; display: flex; align-items: center; justify-content: center; }}
    .lb-mid {{ flex: 1; }}
    .lb-nm {{ font-size: 11pt; font-weight: 700; color: {deep}; }}
    .lb-dept {{ font-size: 8.5pt; color: #9a7b5a; font-weight: 400; }}
    .lb-bar {{ height: 6pt; background: {a}14; border-radius: 999px; overflow: hidden; margin-top: 2mm; }}
    .lb-bar i {{ display: block; height: 100%; border-radius: 999px; }}
    .lb-r {{ text-align: right; width: 48mm; }}
    .lb-amt {{ font-size: 12pt; font-weight: 800; color: {deep}; }}
    .lb-sub {{ font-size: 8pt; color: #9a7b5a; }}
    """
    intro = _sec_intro(meta, "Ranked by total claimed", len(rows))
    if not rows:
        return css, intro + '<div class="empty">No claims in range.</div>'
    return css, intro + f'<div class="lb">{items}</div>'


# ── body C · category cards (by_category) ──────────────────────────────────
def _body_cards(key, meta, s, rows):
    a, deep = meta["accent"], meta["deep"]
    tot = s["total_amount"] or 1
    cards = ""
    for i, r in enumerate(rows):
        col = r.get("color_hex") or _PALETTE[i % len(_PALETTE)]
        pct = r["amount"] / tot * 100
        cards += (f'<div class="cc-card" style="border-top-color:{col}">'
                  f'<div class="cc-nm">{esc(r["category"])}</div>'
                  f'<div class="cc-amt">{inr(r["amount"])}</div>'
                  f'<div class="cc-bar"><i style="width:{max(3, pct):.0f}%;background:{col}"></i></div>'
                  f'<div class="cc-meta">{r["count"]} claims · {pct:.0f}% · {inr_compact(r["settled"])} settled</div></div>')
    css = f"""
    .cc-grid {{ display: flex; flex-wrap: wrap; gap: 5mm; }}
    .cc-card {{ width: calc(33.33% - 3.4mm); border: 1pt solid #eee2cf; border-top: 4pt solid; border-radius: 3mm; padding: 5mm; background: #fff; }}
    .cc-nm {{ font-size: 11pt; font-weight: 700; color: {deep}; }}
    .cc-amt {{ font-size: 18pt; font-weight: 800; color: {deep}; margin: 1mm 0; }}
    .cc-bar {{ height: 6pt; background: #f0ebe0; border-radius: 999px; overflow: hidden; }}
    .cc-bar i {{ display: block; height: 100%; }}
    .cc-meta {{ font-size: 8.5pt; color: #9a7b5a; margin-top: 2mm; }}
    """
    intro = _sec_intro(meta, "Per-category breakdown", len(rows))
    if not rows:
        return css, intro + '<div class="empty">No category spend recorded.</div>'
    return css, intro + f'<div class="cc-grid">{cards}</div>'


# ── body D · age-banded sections (aging) ───────────────────────────────────
def _body_banded(key, meta, s, rows):
    a, deep = meta["accent"], meta["deep"]
    pal = {"0–3": "#16a34a", "4–7": "#d97706", "8–14": "#ea580c", "15+": "#dc2626"}
    order = ["15+", "8–14", "4–7", "0–3"]
    groups = {k: [] for k in order}
    for r in rows:
        groups.setdefault(r["age_band"], []).append(r)
    blocks = ""
    for band in order:
        grp = groups.get(band) or []
        if not grp:
            continue
        rh = "".join(
            f'<tr><td class="bn">{esc(r["claim_number"])}</td><td>{esc(r["employee"])}</td><td>{esc(r["category"])}</td>'
            f'<td>{esc(r["current_stage"])}</td><td class="n">{_fmt(r["amount"])}</td><td>{esc(fmt_date(r["submitted"]))}</td>'
            f'<td class="n"><b style="color:{pal[band]}">{r["age_days"]}d</b></td></tr>' for r in grp)
        blocks += (f'<div class="bd-band"><div class="bd-h" style="border-left-color:{pal[band]}">'
                   f'<span class="bd-t" style="color:{pal[band]}">{band} days</span>'
                   f'<span class="bd-c">{len(grp)} claims · {inr_compact(sum(g["amount"] for g in grp))}</span></div>'
                   f'<table class="dt bd-tbl"><tr><th>Claim #</th><th>Employee</th><th>Category</th><th>Awaiting</th>'
                   f'<th class="n">Amount</th><th>Submitted</th><th class="n">Age</th></tr>{rh}</table></div>')
    css = f"""
    .bd-band {{ margin-bottom: 6mm; }}
    .bd-h {{ display: flex; justify-content: space-between; align-items: baseline; border-left: 4pt solid; padding: 1mm 0 1mm 4mm; margin-bottom: 1mm; }}
    .bd-t {{ font-size: 13pt; font-weight: 800; }}
    .bd-c {{ font-size: 9pt; color: #9a7b5a; }}
    .bd-tbl th {{ background: {a}12; color: {deep}; padding: 4pt 6pt; text-align: left; font-size: 7.4pt; text-transform: uppercase; letter-spacing: .4px; }}
    .bd-tbl td {{ border-bottom: .75pt solid #eee2cf; }}
    .bd-tbl .bn {{ font-family: monospace; color: {a}; }}
    """
    intro = _sec_intro(meta, "Grouped by wait time · oldest first", len(rows))
    if not rows:
        return css, intro + '<div class="empty">No claims in flight.</div>'
    return css, intro + blocks


_BODIES = {
    "claims_register": _body_table, "settlement_summary": _body_table,
    "pending_approvals": _body_table, "payroll_settlements": _body_table, "reversals": _body_table,
    "by_employee": _body_leaderboard, "by_category": _body_cards, "aging": _body_banded,
}


def render_pdf(key: str, rows: List[dict]) -> bytes:
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML

    meta = report_meta(key)
    s = summarize(key, rows)
    cov_css, cov_html = _COVERS.get(key, _cov_register)(meta, s, rows)
    body_css, body_html = _BODIES.get(key, _body_table)(key, meta, s, rows)
    css = _BASE_CSS + cov_css + body_css
    html_doc = (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>'
                f'<body>{cov_html}{body_html}</body></html>')
    return HTML(string=html_doc).write_pdf()



# ════════════════════════════════════════════════════════════════════════════
# Excel — two-sheet workbook (Overview KPI deck + chart, then Data)
# ════════════════════════════════════════════════════════════════════════════
def _excel_series(key: str, rows: List[dict], s: dict):
    """(chart_title, chart_type, [(label, value)]) for the Overview chart, or None."""
    if key == "by_category":
        return "Spend by category", "pie", [(r["category"], round(r["amount"], 2)) for r in rows[:10]]
    if key == "by_employee":
        return "Top claimants", "bar", [(r["employee"], round(r["amount"], 2)) for r in rows[:10]]
    if key == "aging":
        b = s.get("bands", {})
        return "Claims by age band", "column", [(k, b.get(k, 0)) for k in ["0–3", "4–7", "8–14", "15+"]]
    if key == "settlement_summary":
        m = s.get("by_method", {})
        return "Settlement methods", "pie", [(k.replace("_", " ").title(), v) for k, v in m.items()]
    if key == "claims_register":
        st = s.get("by_status", {})
        return "Claims by status", "column", [(k.replace("_", " ").title(), v) for k, v in st.items()]
    if key == "pending_approvals":
        stg = s.get("by_stage", {})
        return "Pending by stage", "column", [(k, v) for k, v in stg.items()] or [("Review", s["count"])]
    if key == "payroll_settlements":
        return "Payroll claims", "column", [("Settled", s["count"])]
    return None


def _excel_kpis(key: str, s: dict):
    if key == "by_category":
        return [("Categories", s.get("categories", 0), False), ("Total spend", s["total_amount"], True),
                ("Settled", s["total_settled"], True), ("Top bucket", s.get("top", "—"), None)]
    if key == "by_employee":
        return [("Employees", s.get("employees", 0), False), ("Total claimed", s["total_amount"], True),
                ("Settled", s["total_settled"], True), ("Top claimant", s.get("top", "—"), None)]
    if key == "aging":
        return [("In flight", s["count"], False), ("Oldest (days)", s.get("oldest", 0), False),
                ("Avg wait (days)", s.get("avg_age", 0), False), ("Value waiting", s["total_amount"], True)]
    return [("Records", s["count"], False), ("Total amount", s.get("total_amount", 0), True),
            ("Settled value", s.get("total_settled", 0), True), ("Employees", s.get("employees", 0), False)]


def render_excel(key: str, rows: List[dict]) -> bytes:
    import xlsxwriter
    meta = report_meta(key)
    accent, deep, soft = meta["accent"], meta["deep"], meta["soft"]
    s = summarize(key, rows)
    cols = _COLUMNS.get(key, _COLUMNS["claims_register"])

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})

    f_title = wb.add_format({"bold": True, "font_size": 20, "font_color": "#ffffff", "bg_color": accent,
                             "align": "left", "valign": "vcenter", "indent": 1})
    f_sub = wb.add_format({"font_size": 10, "italic": True, "font_color": "#ffffff", "bg_color": accent,
                           "valign": "vcenter", "indent": 1})
    _card_label = {"top": 5, "top_color": accent, "left": 1, "right": 1, "left_color": "#e7dccb",
                   "right_color": "#e7dccb", "bg_color": "#fffdf9", "font_size": 8, "font_color": "#9a7b5a",
                   "bold": True, "indent": 1, "valign": "vcenter"}
    _card_val = {"left": 1, "right": 1, "bottom": 1, "left_color": "#e7dccb", "right_color": "#e7dccb",
                 "bottom_color": "#e7dccb", "bg_color": "#fffdf9", "font_size": 17, "bold": True,
                 "font_color": deep, "indent": 1, "valign": "vcenter"}
    f_klabel = wb.add_format(_card_label)
    f_kval = wb.add_format(_card_val)
    f_kval_m = wb.add_format({**_card_val, "font_size": 15, "num_format": '"₹"#,##0'})
    f_section = wb.add_format({"bold": True, "font_size": 11, "font_color": deep})

    # ── Sheet 1: Overview ──
    ov = wb.add_worksheet("Overview")
    ov.hide_gridlines(2)
    ov.set_column("A:A", 2)
    ov.set_column("B:I", 16)
    ov.merge_range(1, 1, 2, 8, f"Reimbursements · {meta['name']}", f_title)
    ov.merge_range(3, 1, 3, 8, f"{meta['description']}   ·   Generated {fmt_date(date.today())}", f_sub)

    kpis = _excel_kpis(key, s)
    r0 = 5
    ov.set_row(r0, 18)
    ov.set_row(r0 + 1, 30)
    for i, (label, val, is_money) in enumerate(kpis):
        c0 = 1 + i * 2
        ov.merge_range(r0, c0, r0, c0 + 1, label.upper(), f_klabel)
        if is_money is None:
            ov.merge_range(r0 + 1, c0, r0 + 1, c0 + 1, str(val), f_kval)
        elif is_money:
            ov.merge_range(r0 + 1, c0, r0 + 1, c0 + 1, float(val or 0), f_kval_m)
        else:
            ov.merge_range(r0 + 1, c0, r0 + 1, c0 + 1, val, f_kval)

    series = _excel_series(key, rows, s)
    if series and series[2]:
        title, ctype, data = series
        ov.write(r0 + 4, 1, "BREAKDOWN", f_section)
        hdr_b = wb.add_format({"bold": True, "bg_color": accent, "font_color": "#fff", "border": 1})
        cell_b = wb.add_format({"border": 1, "border_color": "#e7dccb"})
        num_b = wb.add_format({"border": 1, "border_color": "#e7dccb", "num_format": "#,##0.00"})
        br = r0 + 5
        ov.write(br, 1, "Label", hdr_b); ov.write(br, 2, "Value", hdr_b)
        for j, (lab, val) in enumerate(data, 1):
            ov.write(br + j, 1, str(lab), cell_b)
            ov.write_number(br + j, 2, float(val or 0), num_b)
        chart = wb.add_chart({"type": ctype if ctype != "bar" else "bar"})
        chart.add_series({
            "name": title,
            "categories": ["Overview", br + 1, 1, br + len(data), 1],
            "values": ["Overview", br + 1, 2, br + len(data), 2],
            "points": [{"fill": {"color": c}} for c in
                       ["#7c3aed", "#0d9488", "#ea580c", "#2563eb", "#db2777", "#16a34a", "#d97706", "#0891b2", "#9333ea", "#dc2626"]],
            "data_labels": {"value": True} if ctype == "pie" else {"value": False},
        })
        chart.set_title({"name": title})
        chart.set_legend({"position": "bottom" if ctype == "pie" else "none"})
        chart.set_size({"width": 460, "height": 300})
        ov.insert_chart(br, 4, chart)

    # ── Sheet 2: Data ──
    ws = wb.add_worksheet("Data")
    ws.hide_gridlines(2)
    f_hdr = wb.add_format({"bold": True, "bg_color": accent, "font_color": "#fff", "border": 1,
                           "border_color": "#ffffff", "align": "left", "valign": "vcenter", "font_size": 9})
    f_cell = wb.add_format({"border": 1, "border_color": "#eee2cf", "font_size": 9, "valign": "vcenter"})
    f_money = wb.add_format({"border": 1, "border_color": "#eee2cf", "font_size": 9, "num_format": '"₹"#,##0.00', "align": "right"})
    f_int = wb.add_format({"border": 1, "border_color": "#eee2cf", "font_size": 9, "align": "right"})
    f_zebra = wb.add_format({"border": 1, "border_color": "#eee2cf", "font_size": 9, "bg_color": soft, "valign": "vcenter"})
    f_money_z = wb.add_format({"border": 1, "border_color": "#eee2cf", "font_size": 9, "num_format": '"₹"#,##0.00',
                               "align": "right", "bg_color": soft})
    f_int_z = wb.add_format({"border": 1, "border_color": "#eee2cf", "font_size": 9, "align": "right", "bg_color": soft})

    for ci, (_, label) in enumerate(cols):
        ws.write(0, ci, label, f_hdr)
    int_keys = {"count", "rank", "age_days"}
    for ri, r in enumerate(rows, start=1):
        zebra = ri % 2 == 0
        for ci, (k, _) in enumerate(cols):
            v = r.get(k)
            if k in _MONEY_KEYS and isinstance(v, (int, float)):
                ws.write_number(ri, ci, float(v), f_money_z if zebra else f_money)
            elif k in int_keys and isinstance(v, (int, float)):
                ws.write_number(ri, ci, int(v), f_int_z if zebra else f_int)
            elif k in ("expense_date", "claim_date", "settled_at", "submitted"):
                ws.write(ri, ci, fmt_date(v), f_zebra if zebra else f_cell)
            else:
                ws.write(ri, ci, _fmt(v), f_zebra if zebra else f_cell)

    last_row = len(rows)
    ncols = len(cols)
    # widths
    for ci, (k, _) in enumerate(cols):
        ws.set_column(ci, ci, 28 if k in ("reversal_reason",) else 22 if k in ("employee", "category") else 14)
    if last_row >= 1:
        ws.autofilter(0, 0, last_row, ncols - 1)
        # data-bar on money columns
        for ci, (k, _) in enumerate(cols):
            if k in _MONEY_KEYS:
                ws.conditional_format(1, ci, last_row, ci, {"type": "data_bar", "bar_color": accent})
        # status colour coding
        if any(k == "status" for k, _ in cols):
            sc = next(ci for ci, (k, _) in enumerate(cols) if k == "status")
            for st, (ink, _bg) in _STATUS_INK.items():
                ws.conditional_format(1, sc, last_row, sc, {
                    "type": "text", "criteria": "containing", "value": st.replace("_", " ").title(),
                    "format": wb.add_format({"font_color": ink, "bold": True})})
    ws.freeze_panes(1, 0)
    wb.close()
    return buf.getvalue()
