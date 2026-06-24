"""Full & Final — Settlement Payment Advice.

The single-payee analog of the payroll "Salary Disbursement Advice"
(``payroll_batches._bank_pdf``). When an F&F is settled OUTSIDE payroll
(BANK_TRANSFER / CASH) the finance team has no bank file to act on — this is the
document they execute the transfer from: beneficiary + bank account + IFSC, the
earnings-vs-recoveries breakdown, the net payable, and an authorization block.

Same house style as the disbursement advice (gold masthead, KPI cards, watermark,
confidential footer) so the two read as one family. WeasyPrint is imported lazily
inside ``render_payment_advice_pdf`` per the GTK-on-PATH rule (see CLAUDE.md).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal

_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _inr(v) -> str:
    """₹ grouping in the Indian system (1,23,456.00)."""
    try:
        n = Decimal(str(v or 0))
    except Exception:
        n = Decimal(0)
    neg = n < 0
    n = abs(n)
    whole = int(n)
    frac = f"{(n - whole):.2f}"[2:]
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        import re
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = f"{head},{tail}"
    return f"{'-' if neg else ''}{s}.{frac}"


def _method_label(m: str | None) -> str:
    return {"BANK_TRANSFER": "Bank Transfer", "CASH": "Cash", "PAYROLL": "Payroll Arrear"}.get(
        (m or "PAYROLL").upper(), m or "—")


def _beneficiary(case, s):
    """Resolve the payee + bank details from the joined case/employee/settlement."""
    emp = case.employee
    name = None
    if emp and emp.user:
        name = getattr(emp.user, "full_name", None) or getattr(emp.user, "email", None)
    name = name or (emp.employee_id if emp else "") or "—"
    dept = (case.department.name if getattr(case, "department", None) else None) \
        or (emp.department.name if emp and getattr(emp, "department", None) else None) or "—"
    desig = emp.designation.name if emp and getattr(emp, "designation", None) else "—"
    return {
        "name": name,
        "code": emp.employee_id if emp else "—",
        "department": dept,
        "designation": desig,
        "bank": (emp.bank_name if emp else "") or "",
        "account": (emp.account_number if emp else "") or "",   # EncryptedString → decrypts on read
        "ifsc": (emp.ifsc if emp else "") or "",
    }


# Earnings + recoveries line descriptors (label, settlement attr, kind).
_EARN_LINES = [
    ("Pending salary", "pending_salary"),
    ("Leave encashment", "leave_encashment_amount"),
    ("Incentives", "incentives_amount"),
    ("Bonus", "bonus_amount"),
    ("Reimbursements", "reimbursements_amount"),
    ("Gratuity", "gratuity_amount"),
    ("Other earnings", "other_earnings"),
]
_REC_LINES = [
    ("Notice recovery", "notice_recovery"),
    ("Loan recovery", "loan_recovery"),
    ("Advance recovery", "advance_recovery"),
    ("Asset recovery", "asset_recovery"),
    ("Other deductions", "other_deductions"),
]


def _nz(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal(0)


def payment_advice_csv(case, s) -> str:
    """Single-row bank-transfer instruction (matches the payroll bank-file columns)."""
    b = _beneficiary(case, s)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Settlement No", "Employee Code", "Beneficiary Name", "Department",
                "Bank Name", "Account Number", "IFSC", "Net Payable (INR)", "Method", "Reference"])
    w.writerow([
        s.settlement_number, b["code"], b["name"], b["department"],
        b["bank"], b["account"], b["ifsc"], f'{_nz(s.net_amount):.2f}',
        _method_label(s.settlement_method), s.payroll_ref or "",
    ])
    return buf.getvalue()


def _advice_html(case, s) -> str:
    b = _beneficiary(case, s)
    net = _nz(s.net_amount)
    recoverable = net < 0
    earn = _nz(s.total_earnings)
    rec = _nz(s.total_recoveries)
    method = (s.settlement_method or "PAYROLL").upper()
    gen = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    lwd = case.last_working_date or case.exit_date
    lwd_s = lwd.strftime("%d %b %Y") if lwd else "—"
    period = f"{_MONTHS[s.period_month]} {s.period_year}" if s.period_month and s.period_year else "—"
    paid_s = s.paid_at.strftime("%d %b %Y") if s.paid_at else "Pending"
    appr_s = s.approved_at.strftime("%d %b %Y") if s.approved_at else "—"

    valid = bool(b["account"] and b["ifsc"])
    is_payroll = method == "PAYROLL"

    # instruction banner adapts to the channel
    if is_payroll:
        instr = (f'<div class="instr pay">This Full &amp; Final is settled through <b>payroll</b> '
                 f'as an arrear on the <b>{period}</b> pay run — it is paid in that run\'s bank file, '
                 f'not by a separate transfer. This advice is a record of the settlement.</div>')
    elif method == "CASH":
        instr = ('<div class="instr">Disburse the <b>net payable</b> below to the employee in '
                 '<b>cash</b> and capture the voucher reference on payout.</div>')
    else:
        instr = ('<div class="instr">Execute a <b>bank transfer</b> of the <b>net payable</b> below to the '
                 'beneficiary account. Record the UTR / transaction reference against this settlement once paid.</div>')

    miss = "" if (valid or is_payroll) else (
        '<div class="warn">⚠ Beneficiary bank account / IFSC is missing. Update the employee profile '
        '(HR ▸ Employee ▸ Bank details) before initiating the transfer.</div>')

    def _rows(lines, cls):
        out = []
        for label, attr in lines:
            amt = _nz(getattr(s, attr, 0))
            if amt == 0:
                continue
            sign = "−" if cls == "rec" else ""
            out.append(f'<tr><td>{label}</td><td class="amt {cls}">{sign}₹ {_inr(amt)}</td></tr>')
        return "".join(out) or '<tr><td class="muted">None</td><td class="amt muted">₹ 0.00</td></tr>'

    earn_rows = _rows(_EARN_LINES, "earn")
    rec_rows = _rows(_REC_LINES, "rec")

    net_label = "Net Recoverable (employee owes)" if recoverable else "Net Payable"
    net_cls = "rec" if recoverable else "net"
    acct_disp = b["account"] or "— missing —"
    ifsc_disp = b["ifsc"] or "— missing —"
    bank_disp = b["bank"] or "— not on file —"

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    @page {{ size: A4; margin: 13mm 12mm 16mm;
      @bottom-left {{ content: "Confidential · Fourreck Technologies — Full & Final Payment Advice"; font-size: 7.5px; color: #b9a982; }}
      @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font-size: 7.5px; color: #b9a982; }}
    }}
    * {{ font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; box-sizing: border-box; }}
    body {{ color: #1f1710; font-size: 10px; margin: 0; }}
    .wm {{ position: fixed; top: 40%; left: -6%; right: 0; text-align: center; z-index: 0; transform: rotate(-22deg);
      font-size: 76px; font-weight: 800; letter-spacing: 12px; color: rgba(184,134,11,0.045); text-transform: uppercase; }}
    .masthead {{ position: relative; overflow: hidden; border-radius: 16px; padding: 17px 22px; color: #fff8e6;
      background: linear-gradient(120deg, #8a5a06 0%, #b8860b 42%, #f59e0b 100%); display: flex; justify-content: space-between; align-items: center; }}
    .masthead::after {{ content: ""; position: absolute; top: -40px; right: -30px; width: 170px; height: 170px; border-radius: 50%;
      background: radial-gradient(circle, rgba(255,255,255,0.18), transparent 65%); }}
    .brand {{ font-size: 21px; font-weight: 800; }} .tag {{ margin-top: 2px; font-size: 9px; text-transform: uppercase; letter-spacing: 3px; color: #ffe9b8; }}
    .mh-right {{ position: relative; text-align: right; z-index: 1; }}
    .mh-right .no {{ font-size: 15px; font-weight: 800; }} .mh-right .per {{ font-size: 10px; color: #ffedc7; }}
    .mh-right .meta2 {{ font-size: 9px; color: #ffe9b8; margin-top: 4px; }}
    .instr {{ margin-top: 12px; padding: 10px 14px; border-radius: 11px; font-size: 10px; line-height: 1.5;
      background: #ecfdf5; border: 1px solid #bfe6cf; color: #155e44; }}
    .instr.pay {{ background: #fff7e8; border-color: #f0d79a; color: #7a5a16; }}
    .instr b {{ color: inherit; }}
    .warn {{ margin-top: 10px; padding: 9px 13px; border-radius: 10px; background: #fff4e0; border: 1px solid #f1c98a; color: #9a3412; font-size: 9.5px; }}
    h3 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #8a5a06; margin: 17px 0 7px; }}
    .grid2 {{ display: flex; gap: 12px; }}
    .card {{ flex: 1; border: 1px solid #e8dcc0; border-radius: 12px; padding: 13px 15px; background: #fffdf8; }}
    .card.bank {{ background: #fbfdff; border-color: #d8e6ef; }}
    .row {{ display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px dashed #f0e8d6; font-size: 10px; }}
    .row:last-child {{ border-bottom: 0; }}
    .row .l {{ color: #9a865a; }} .row .v {{ font-weight: 700; color: #1f1710; }}
    .mono {{ font-family: 'Consolas', monospace; letter-spacing: .3px; }}
    .acct {{ font-size: 13px; font-weight: 800; }}
    table.brk {{ width: 100%; border-collapse: collapse; }}
    table.brk td {{ padding: 5px 10px; border-bottom: 1px solid #f4ecda; font-size: 9.5px; }}
    .amt {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; }}
    .amt.earn {{ color: #047857; }} .amt.rec {{ color: #9a3412; }}
    .muted {{ color: #b39a5e; }}
    .sub td {{ font-weight: 800; background: #faf7ef; border-top: 1px solid #e3cf9f; }}
    .netbar {{ display: flex; justify-content: space-between; align-items: center; margin-top: 14px; padding: 14px 18px; border-radius: 14px;
      background: #ecfdf5; border: 1px solid #bfe6cf; }}
    .netbar.rec {{ background: #fdecec; border-color: #f1c0c0; }}
    .netbar .nl {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #8a6d3b; }}
    .netbar .nv {{ font-size: 26px; font-weight: 850; color: #047857; }}
    .netbar.rec .nv {{ color: #b91c1c; }}
    .sign {{ display: flex; justify-content: space-between; margin-top: 30px; }}
    .sign div {{ width: 28%; border-top: 1px solid #c9b07a; padding-top: 5px; text-align: center; font-size: 9px; color: #8a6d3b; }}
    </style></head><body>
      <div class="wm">Fourreck</div>
      <div class="masthead">
        <div><div class="brand">Fourreck Technologies</div><div class="tag">Full &amp; Final · Payment Advice</div></div>
        <div class="mh-right"><div class="no">{s.settlement_number}</div><div class="per">{case.case_number}</div>
          <div class="meta2">Status {s.status.value} · {_method_label(s.settlement_method)} · Generated {gen}</div></div>
      </div>

      {instr}
      {miss}

      <div class="grid2" style="margin-top:14px">
        <div class="card">
          <h3 style="margin-top:0">Beneficiary</h3>
          <div class="row"><span class="l">Name</span><span class="v">{b["name"]}</span></div>
          <div class="row"><span class="l">Employee code</span><span class="v mono">{b["code"]}</span></div>
          <div class="row"><span class="l">Department</span><span class="v">{b["department"]}</span></div>
          <div class="row"><span class="l">Designation</span><span class="v">{b["designation"]}</span></div>
          <div class="row"><span class="l">Last working day</span><span class="v">{lwd_s}</span></div>
        </div>
        <div class="card bank">
          <h3 style="margin-top:0">Remit to</h3>
          <div class="row"><span class="l">Bank</span><span class="v">{bank_disp}</span></div>
          <div class="row"><span class="l">Account number</span><span class="v mono acct">{acct_disp}</span></div>
          <div class="row"><span class="l">IFSC</span><span class="v mono">{ifsc_disp}</span></div>
          <div class="row"><span class="l">Reference</span><span class="v mono">{s.payroll_ref or "—"}</span></div>
        </div>
      </div>

      <h3>Settlement breakdown</h3>
      <div class="grid2">
        <div class="card" style="padding:0">
          <table class="brk">
            <tr><td colspan="2" style="font-weight:800;color:#047857;text-transform:uppercase;font-size:8.5px;letter-spacing:1px">Earnings</td></tr>
            {earn_rows}
            <tr class="sub"><td>Total earnings</td><td class="amt earn">₹ {_inr(earn)}</td></tr>
          </table>
        </div>
        <div class="card" style="padding:0">
          <table class="brk">
            <tr><td colspan="2" style="font-weight:800;color:#9a3412;text-transform:uppercase;font-size:8.5px;letter-spacing:1px">Recoveries</td></tr>
            {rec_rows}
            <tr class="sub"><td>Total recoveries</td><td class="amt rec">−₹ {_inr(rec)}</td></tr>
          </table>
        </div>
      </div>

      <div class="netbar {net_cls}">
        <div class="nl">{net_label}</div>
        <div class="nv">₹ {_inr(abs(net))}</div>
      </div>

      <div class="sign">
        <div>Prepared by<br><span style="color:#c9b07a">HR · Exit desk</span></div>
        <div>Approved by<br><span style="color:#c9b07a">{appr_s}</span></div>
        <div>Disbursed by<br><span style="color:#c9b07a">Finance · {paid_s}</span></div>
      </div>
    </body></html>"""


def render_payment_advice_pdf(case, s) -> bytes:
    """Render the F&F Payment Advice to PDF bytes (WeasyPrint, lazy GTK import)."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433 — lazy, needs GTK on PATH

    return HTML(string=_advice_html(case, s)).write_pdf()
