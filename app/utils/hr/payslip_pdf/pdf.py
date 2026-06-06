"""Server-side payslip PDF (WeasyPrint) — "Minted Statement" design.

An ultra-modern A4 statement rendered from HTML/CSS via WeasyPrint:
  gold masthead → employee identity card → net-pay hero with take-home/deduction
  allocation bar + amount-in-words → earnings | deductions+statutory columns
  (with calc-note traces & tax-free/prorated tags) → employer-contribution &
  cost-to-company build-up → bank-disbursement footer. A faint diagonal brand
  watermark + running @page footer repeat on every page. Optional password
  encrypts the output via lazy-imported pypdf.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from app.models.hr.salary_component import ComponentType

_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]

COMPANY = {
    "name": "Fourreck Technologies",
    "tagline": "Payroll Statement",
}

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

_ENUM_NOTE = re.compile(r"^[A-Za-z]+Kind\.[A-Z0-9_]+$")


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def _three(n: int) -> str:
    h, r = divmod(n, 100)
    out = (_ONES[h] + " Hundred" if h else "")
    if r:
        out = (out + " " + _two(r)).strip()
    return out.strip()


def amount_in_words(amount) -> str:
    """Indian-system rupees-in-words (Crore / Lakh / Thousand)."""
    amount = Decimal(str(amount or 0))
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    if rupees == 0:
        words = "Zero"
    else:
        crore, rem = divmod(rupees, 10000000)
        lakh, rem = divmod(rem, 100000)
        thousand, rem = divmod(rem, 1000)
        parts = []
        if crore:
            parts.append(_three(crore) + " Crore")
        if lakh:
            parts.append(_two(lakh) + " Lakh")
        if thousand:
            parts.append(_two(thousand) + " Thousand")
        if rem:
            parts.append(_three(rem))
        words = " ".join(parts).strip()
    out = f"Rupees {words}"
    if paise:
        out += f" and {_two(paise)} Paise"
    return out + " Only"


def _inr(v) -> str:
    v = Decimal(str(v or 0))
    neg = v < 0
    v = abs(v)
    s = f"{v:,.2f}"
    return ("-" if neg else "") + s


def _clean_note(note: Optional[str]) -> str:
    """Drop bare enum dumps ("StatutoryKind.PF_EMPLOYEE") — redundant with the
    component name — but keep real calc traces ("40% of BASIC; prorated 19/31")."""
    n = (note or "").strip()
    if not n or _ENUM_NOTE.match(n):
        return ""
    return n


def _initials(name: str) -> str:
    parts = [w for w in re.split(r"\s+", (name or "").strip()) if w]
    return ("".join(w[0] for w in parts[:2]) or "E").upper()


def _rows_html(lines, kinds) -> str:
    rows = [l for l in lines if l.component_type in kinds and not l.is_employer_cost]
    if not rows:
        return '<tr><td class="muted">No items</td><td class="amt muted">0.00</td></tr>'
    out = []
    for l in rows:
        note = _clean_note(getattr(l, "calc_note", None))
        tag = ""
        if l.component_type == ComponentType.EARNING and not l.is_taxable:
            tag = '<span class="tag free">Tax-free</span>'
        elif l.full_amount and l.amount and Decimal(str(l.full_amount)) > Decimal(str(l.amount)):
            tag = '<span class="tag pro">Prorated</span>'
        sub = f'<div class="sub">{note}</div>' if note else ""
        out.append(
            f'<tr><td><div class="cn">{l.component_name}{tag}</div>{sub}</td>'
            f'<td class="amt">{_inr(l.amount)}</td></tr>'
        )
    return "".join(out)


def render_payslip_pdf(slip, *, employee_name: str, employee_code: str,
                       department: Optional[str] = None, designation: Optional[str] = None,
                       password: Optional[str] = None) -> bytes:
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433  (lazy — needs GTK on PATH)

    earnings = _rows_html(slip.lines, {ComponentType.EARNING, ComponentType.REIMBURSEMENT})
    deductions = _rows_html(slip.lines, {ComponentType.DEDUCTION})
    statutory = _rows_html(slip.lines, {ComponentType.STATUTORY_DEDUCTION})
    employer_rows = [l for l in slip.lines
                     if l.component_type == ComponentType.EMPLOYER_CONTRIBUTION or l.is_employer_cost]

    period = f"{_MONTHS[slip.period_month]} {slip.period_year}"
    net_words = amount_in_words(slip.net_pay)

    gross = Decimal(str(slip.gross_earnings or 0))
    deduct = Decimal(str(slip.total_deductions or 0))
    net = Decimal(str(slip.net_pay or 0))
    employer = Decimal(str(getattr(slip, "employer_contributions", 0) or 0))
    ctc = Decimal(str(getattr(slip, "ctc_value", 0) or 0)) or (gross + employer)
    encash = Decimal(str(getattr(slip, "encashment_amount", 0) or 0))

    net_pct = int(round(float(net / gross * 100))) if gross > 0 else 0
    net_pct = max(0, min(100, net_pct))
    ded_pct = 100 - net_pct
    initials = _initials(employee_name)
    regime = slip.tax_regime.value if slip.tax_regime else "—"
    gen_ts = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    employer_html = "".join(
        f'<tr><td><div class="cn">{l.component_name}</div></td><td class="amt">{_inr(l.amount)}</td></tr>'
        for l in employer_rows
    ) or '<tr><td class="muted">No employer contributions</td><td class="amt muted">0.00</td></tr>'

    encash_row = (
        f'<div class="kv"><span>Leave encashment</span><b>₹ {_inr(encash)}</b></div>' if encash > 0 else ""
    )

    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
    @page {{
      size: A4; margin: 13mm 12mm 16mm;
      @bottom-left {{ content: "Confidential · {COMPANY['name']}"; font-size: 7.5px; color: #b9a982; }}
      @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font-size: 7.5px; color: #b9a982; }}
    }}
    * {{ font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; box-sizing: border-box; }}
    body {{ color: #1f1710; font-size: 10.5px; margin: 0; }}
    .wm {{ position: fixed; top: 38%; left: -6%; right: 0; text-align: center; z-index: 0;
           transform: rotate(-22deg); font-size: 88px; font-weight: 800; letter-spacing: 14px;
           color: rgba(184,134,11,0.045); text-transform: uppercase; }}

    /* masthead */
    .masthead {{ position: relative; overflow: hidden; border-radius: 16px; padding: 18px 22px;
      color: #fff8e6; background: linear-gradient(120deg, #8a5a06 0%, #b8860b 42%, #f59e0b 100%);
      display: flex; justify-content: space-between; align-items: center; }}
    .masthead::after {{ content: ""; position: absolute; top: -40px; right: -30px; width: 180px; height: 180px;
      border-radius: 50%; background: radial-gradient(circle, rgba(255,255,255,0.18), transparent 65%); }}
    .brand {{ font-size: 22px; font-weight: 800; letter-spacing: -.4px; }}
    .tag {{ margin-top: 2px; font-size: 9px; text-transform: uppercase; letter-spacing: 3px; color: #ffe9b8; }}
    .mh-right {{ position: relative; text-align: right; z-index: 1; }}
    .mh-right .lbl {{ font-size: 8px; text-transform: uppercase; letter-spacing: 2px; color: #ffe3a6; }}
    .mh-right .no {{ font-size: 15px; font-weight: 800; letter-spacing: .3px; }}
    .mh-right .per {{ font-size: 10px; color: #ffedc7; margin-top: 1px; }}

    /* identity card */
    .idcard {{ display: flex; align-items: center; gap: 14px; margin-top: 14px; padding: 14px 16px;
      background: #fffdf8; border: 1px solid #efe2c4; border-radius: 14px; }}
    .avatar {{ flex: 0 0 auto; width: 46px; height: 46px; border-radius: 14px; display: flex;
      align-items: center; justify-content: center; color: #2a1c0b; font-weight: 800; font-size: 16px;
      background: linear-gradient(135deg, #fde68a, #f59e0b); box-shadow: inset 0 1px 0 rgba(255,255,255,.5); }}
    .id-main {{ flex: 1; }}
    .id-name {{ font-size: 16px; font-weight: 800; color: #1f1710; }}
    .id-role {{ font-size: 10px; color: #8a6d3b; margin-top: 1px; }}
    .id-meta {{ display: flex; gap: 18px; flex-wrap: wrap; }}
    .id-meta div {{ text-align: right; }}
    .id-meta span {{ display: block; font-size: 7.5px; text-transform: uppercase; letter-spacing: 1px; color: #b39a5e; }}
    .id-meta b {{ font-size: 10.5px; color: #2a1c0b; font-weight: 700; }}

    /* net hero */
    .nethero {{ margin-top: 12px; padding: 16px 18px; border-radius: 14px;
      background: linear-gradient(135deg, #ecfdf5 0%, #fff8e6 100%);
      border: 1px solid #bfe6cf; }}
    .nh-top {{ display: flex; justify-content: space-between; align-items: flex-end; }}
    .nh-lbl {{ font-size: 9px; text-transform: uppercase; letter-spacing: 2px; color: #047857; }}
    .nh-val {{ font-size: 30px; font-weight: 800; color: #065f46; letter-spacing: -.5px; }}
    .nh-words {{ margin-top: 3px; font-style: italic; color: #6b5840; font-size: 9.5px; }}
    .bar {{ margin-top: 12px; height: 9px; border-radius: 999px; overflow: hidden; display: flex; background: #f0e6cf; }}
    .bar .seg-net {{ background: linear-gradient(90deg, #047857, #10b981); }}
    .bar .seg-ded {{ background: linear-gradient(90deg, #9a3412, #ea580c); }}
    .bar-key {{ display: flex; gap: 18px; margin-top: 6px; font-size: 8.5px; color: #8a6d3b; }}
    .bar-key i {{ display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
    .bar-key .k-net {{ background: #10b981; }} .bar-key .k-ded {{ background: #ea580c; }}

    /* component columns */
    .cols {{ display: flex; gap: 12px; margin-top: 12px; }}
    .col {{ flex: 1; border: 1px solid #ecdfbf; border-radius: 12px; overflow: hidden; background: #fffefb; }}
    .col h3 {{ margin: 0; padding: 8px 13px; font-size: 9.5px; text-transform: uppercase; letter-spacing: 1.5px;
      color: #fff8e6; background: linear-gradient(90deg, #1f1710, #3a2a14); display: flex; justify-content: space-between; }}
    .col h3 .ct {{ color: #fbbf24; }}
    .subhead {{ padding: 6px 13px 2px; font-size: 8px; text-transform: uppercase; letter-spacing: 1.5px; color: #b39a5e; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ padding: 6px 13px; border-bottom: 1px solid #f4ecda; vertical-align: top; }}
    .cn {{ font-size: 10.5px; color: #2a1c0b; font-weight: 600; }}
    .sub {{ font-size: 8px; color: #a08a5e; margin-top: 1px; }}
    .tag {{ display: inline-block; margin-left: 6px; font-size: 7px; font-weight: 800; text-transform: uppercase;
      letter-spacing: .4px; padding: 1px 5px; border-radius: 4px; vertical-align: middle; }}
    .tag.free {{ background: #d1fae5; color: #047857; }}
    .tag.pro {{ background: #ffe4d6; color: #9a3412; }}
    .amt {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; white-space: nowrap; }}
    .muted {{ color: #b0a48c; font-style: italic; }}
    tfoot td {{ font-weight: 800; background: #faf5e9; border-top: 1.5px solid #e3cf9f; font-size: 10.5px; }}
    tfoot td.net {{ color: #047857; }} tfoot td.ded {{ color: #9a3412; }}

    /* employer + ctc */
    .lower {{ display: flex; gap: 12px; margin-top: 12px; }}
    .lower .col {{ flex: 1; }}
    .ctc {{ flex: 0 0 38%; border: 1px dashed #e3cf9f; border-radius: 12px; padding: 12px 14px; background: #fffdf6; }}
    .ctc .h {{ font-size: 9px; text-transform: uppercase; letter-spacing: 1.5px; color: #8a6d3b; margin-bottom: 8px; }}
    .kv {{ display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px dotted #ecdfbf; font-size: 10px; }}
    .kv:last-child {{ border-bottom: none; }}
    .kv span {{ color: #6b5840; }} .kv b {{ color: #2a1c0b; font-variant-numeric: tabular-nums; }}
    .kv.tot {{ margin-top: 4px; padding-top: 7px; border-top: 1.5px solid #e3cf9f; border-bottom: none; font-weight: 800; }}
    .kv.tot b {{ color: #b8860b; font-size: 12px; }}

    /* footer */
    .disburse {{ margin-top: 14px; padding: 11px 14px; border-radius: 12px; background: #1f1710; color: #e8dcc0;
      display: flex; justify-content: space-between; align-items: center; font-size: 9px; }}
    .disburse b {{ color: #fbbf24; }}
    .note {{ margin-top: 8px; text-align: center; font-size: 8px; color: #a08a5e; }}
    </style></head><body>
      <div class="wm">{COMPANY['name'].split()[0]}</div>

      <div class="masthead">
        <div><div class="brand">{COMPANY['name']}</div><div class="tag">{COMPANY['tagline']}</div></div>
        <div class="mh-right">
          <div class="lbl">Payslip No</div><div class="no">{slip.payslip_no}</div><div class="per">{period}</div>
        </div>
      </div>

      <div class="idcard">
        <div class="avatar">{initials}</div>
        <div class="id-main">
          <div class="id-name">{employee_name or '—'}</div>
          <div class="id-role">{' · '.join([x for x in [designation, department] if x]) or (employee_code or '—')}</div>
        </div>
        <div class="id-meta">
          <div><span>Code</span><b>{employee_code or '—'}</b></div>
          <div><span>Working / Paid</span><b>{slip.working_days} / {slip.paid_days}</b></div>
          <div><span>LOP</span><b>{slip.lop_days}</b></div>
          <div><span>Regime</span><b>{regime}</b></div>
          <div><span>PAN</span><b>{slip.pan or '—'}</b></div>
          <div><span>UAN</span><b>{slip.uan or '—'}</b></div>
        </div>
      </div>

      <div class="nethero">
        <div class="nh-top">
          <div><div class="nh-lbl">Net Pay Credited</div><div class="nh-words">{net_words}</div></div>
          <div class="nh-val">₹ {_inr(net)}</div>
        </div>
        <div class="bar">
          <div class="seg-net" style="width:{net_pct}%"></div>
          <div class="seg-ded" style="width:{ded_pct}%"></div>
        </div>
        <div class="bar-key">
          <span><i class="k-net"></i>Take-home {net_pct}%</span>
          <span><i class="k-ded"></i>Deductions {ded_pct}%</span>
        </div>
      </div>

      <div class="cols">
        <div class="col">
          <h3>Earnings <span class="ct">{_inr(gross)}</span></h3>
          <table><tbody>{earnings}</tbody>
            <tfoot><tr><td>Gross Earnings</td><td class="amt">{_inr(gross)}</td></tr></tfoot></table>
        </div>
        <div class="col">
          <h3>Deductions <span class="ct">{_inr(deduct)}</span></h3>
          <div class="subhead">General</div>
          <table><tbody>{deductions}</tbody></table>
          <div class="subhead">Statutory</div>
          <table><tbody>{statutory}</tbody>
            <tfoot><tr><td class="ded">Total Deductions</td><td class="amt ded">{_inr(deduct)}</td></tr></tfoot></table>
        </div>
      </div>

      <div class="lower">
        <div class="col">
          <h3>Employer Contributions <span class="ct">{_inr(employer)}</span></h3>
          <table><tbody>{employer_html}</tbody></table>
        </div>
        <div class="ctc">
          <div class="h">Cost to Company</div>
          <div class="kv"><span>Gross earnings</span><b>₹ {_inr(gross)}</b></div>
          <div class="kv"><span>Employer cost</span><b>₹ {_inr(employer)}</b></div>
          {encash_row}
          <div class="kv tot"><span>Monthly CTC</span><b>₹ {_inr(ctc)}</b></div>
        </div>
      </div>

      <div class="disburse">
        <span>Bank <b>{slip.bank_name or '—'}</b> · A/C <b>{('•••• ' + str(slip.account_number)[-4:]) if slip.account_number else '—'}</b> · IFSC <b>{slip.ifsc or '—'}</b></span>
        <span>Net credited <b>₹ {_inr(net)}</b></span>
      </div>
      <div class="note">This is a system-generated payslip and does not require a signature. Generated {gen_ts}.</div>
    </body></html>"""

    pdf_bytes = HTML(string=html).write_pdf()

    if password:
        try:
            import io
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(io.BytesIO(pdf_bytes))
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            writer.encrypt(password)
            buf = io.BytesIO()
            writer.write(buf)
            pdf_bytes = buf.getvalue()
        except Exception:
            # If pypdf isn't available, fall back to the unencrypted PDF rather than 500.
            pass
    return pdf_bytes
