"""Server-side Form-16 / tax certificate PDF (WeasyPrint) — "Tax Ledger" design.

A branded A4 tax certificate rendered from HTML/CSS via WeasyPrint:
  gold masthead → employee + deductor identity → TDS hero → salary & tax
  computation (Part B) → statutory deduction summary → month-by-month TDS table.

WeasyPrint is imported lazily inside the render function (after the GTK
bootstrap) — never at import time. Mirrors app/utils/hr/payslip_pdf/pdf.py.

NOTE: this is a clean internal tax statement, not the exact TRACES Form-16
layout. The headline computation uses the same engine as the Tax Studio.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

COMPANY = {
    "name": "Fourreck Technologies",
    "tagline": "Form 16 · Tax Certificate",
}


def _inr(v) -> str:
    v = Decimal(str(v or 0))
    neg = v < 0
    s = f"{abs(v):,.2f}"
    return ("-" if neg else "") + s


def _initials(name: str) -> str:
    parts = [w for w in (name or "").strip().split() if w]
    return ("".join(w[0] for w in parts[:2]) or "E").upper()


def render_form16_pdf(agg: dict, *, employee_name: str, employee_code: str,
                      fiscal_year: str, projection: Optional[dict] = None,
                      designation: Optional[str] = None, department: Optional[str] = None) -> bytes:
    """Render a Form-16-style PDF from the FY statutory aggregate (see
    ``app.utils.hr.tax_summary.aggregate_statutory``) + an optional tax projection
    ``{regime, annual_gross, taxable_income, annual_tax, std_deduction}``."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433  (lazy — needs GTK on PATH)

    g = lambda k: Decimal(str(agg.get(k) or 0))  # noqa: E731
    tds = g("tds")
    gross = g("gross")
    regime = (projection or {}).get("regime") or agg.get("regime") or "—"
    proj = projection or {}
    annual_gross = Decimal(str(proj.get("annual_gross") or gross or 0))
    taxable = Decimal(str(proj.get("taxable_income") or 0))
    annual_tax = Decimal(str(proj.get("annual_tax") or 0))
    std_ded = max(Decimal(0), annual_gross - taxable) if taxable else Decimal(0)
    balance = annual_tax - tds
    initials = _initials(employee_name)
    gen_ts = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    # statutory summary rows
    stat_rows = [
        ("Tax Deducted at Source (TDS)", tds),
        ("Provident Fund — Employee", g("pf_employee")),
        ("Provident Fund — Employer", g("pf_employer")),
        ("ESI — Employee", g("esi_employee")),
        ("ESI — Employer", g("esi_employer")),
        ("Professional Tax", g("professional_tax")),
        ("Labour Welfare Fund", g("lwf")),
    ]
    stat_html = "".join(
        f'<tr><td>{label}</td><td class="amt">{_inr(val)}</td></tr>'
        for label, val in stat_rows if val and val != 0
    ) or '<tr><td class="muted">No statutory deductions recorded</td><td class="amt muted">0.00</td></tr>'

    # month-by-month TDS / PF
    month_html = "".join(
        f'<tr><td>{m["label"]} {m["year"]}</td>'
        f'<td class="amt">{_inr(m["gross"])}</td>'
        f'<td class="amt">{_inr(m["pf"])}</td>'
        f'<td class="amt">{_inr(m["tds"])}</td></tr>'
        for m in (agg.get("months") or [])
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
           transform: rotate(-22deg); font-size: 80px; font-weight: 800; letter-spacing: 12px;
           color: rgba(184,134,11,0.05); text-transform: uppercase; }}
    .masthead {{ position: relative; overflow: hidden; border-radius: 16px; padding: 18px 22px; color: #fff8e6;
      background: linear-gradient(120deg, #8a5a06 0%, #b8860b 42%, #f59e0b 100%);
      display: flex; justify-content: space-between; align-items: center; }}
    .masthead::after {{ content: ""; position: absolute; top: -40px; right: -30px; width: 180px; height: 180px;
      border-radius: 50%; background: radial-gradient(circle, rgba(255,255,255,0.18), transparent 65%); }}
    .brand {{ font-size: 22px; font-weight: 800; letter-spacing: -.4px; }}
    .tag {{ margin-top: 2px; font-size: 9px; text-transform: uppercase; letter-spacing: 3px; color: #ffe9b8; }}
    .mh-right {{ position: relative; text-align: right; z-index: 1; }}
    .mh-right .lbl {{ font-size: 8px; text-transform: uppercase; letter-spacing: 2px; color: #ffe3a6; }}
    .mh-right .no {{ font-size: 15px; font-weight: 800; }}
    .mh-right .per {{ font-size: 10px; color: #ffedc7; margin-top: 1px; }}
    .idcard {{ display: flex; align-items: center; gap: 14px; margin-top: 14px; padding: 14px 16px;
      background: #fffdf8; border: 1px solid #efe2c4; border-radius: 14px; }}
    .avatar {{ flex: 0 0 auto; width: 46px; height: 46px; border-radius: 14px; display: flex;
      align-items: center; justify-content: center; color: #2a1c0b; font-weight: 800; font-size: 16px;
      background: linear-gradient(135deg, #fde68a, #f59e0b); }}
    .id-main {{ flex: 1; }}
    .id-name {{ font-size: 16px; font-weight: 800; color: #1f1710; }}
    .id-role {{ font-size: 10px; color: #8a6d3b; margin-top: 1px; }}
    .id-meta {{ display: flex; gap: 16px; flex-wrap: wrap; }}
    .id-meta div {{ text-align: right; }}
    .id-meta span {{ display: block; font-size: 7.5px; text-transform: uppercase; letter-spacing: 1px; color: #b39a5e; }}
    .id-meta b {{ font-size: 10.5px; color: #2a1c0b; font-weight: 700; }}
    .hero {{ margin-top: 12px; padding: 16px 18px; border-radius: 14px;
      background: linear-gradient(135deg, #fef2f2 0%, #fff8e6 100%); border: 1px solid #f3cccc; }}
    .hero .lbl {{ font-size: 9px; text-transform: uppercase; letter-spacing: 2px; color: #9a3412; }}
    .hero .val {{ font-size: 30px; font-weight: 800; color: #9a3412; letter-spacing: -.5px; }}
    .hero .sub {{ margin-top: 3px; font-size: 9.5px; color: #6b5840; }}
    .cols {{ display: flex; gap: 12px; margin-top: 12px; }}
    .col {{ flex: 1; border: 1px solid #ecdfbf; border-radius: 12px; overflow: hidden; background: #fffefb; }}
    .col h3 {{ margin: 0; padding: 8px 13px; font-size: 9.5px; text-transform: uppercase; letter-spacing: 1.5px;
      color: #fff8e6; background: linear-gradient(90deg, #1f1710, #3a2a14); display: flex; justify-content: space-between; }}
    .col h3 .ct {{ color: #fbbf24; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td, th {{ padding: 6px 13px; border-bottom: 1px solid #f4ecda; text-align: left; }}
    th {{ font-size: 7.5px; text-transform: uppercase; letter-spacing: 1px; color: #b39a5e; background: #faf5e9; }}
    .amt {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; white-space: nowrap; }}
    .muted {{ color: #b0a48c; font-style: italic; }}
    .kv {{ display: flex; justify-content: space-between; padding: 7px 13px; border-bottom: 1px dotted #ecdfbf; font-size: 10.5px; }}
    .kv span {{ color: #6b5840; }} .kv b {{ color: #2a1c0b; font-variant-numeric: tabular-nums; }}
    .kv.hl {{ background: #faf5e9; font-weight: 800; }} .kv.hl b {{ color: #b8860b; }}
    .mt {{ margin-top: 12px; }}
    .note {{ margin-top: 10px; text-align: center; font-size: 8px; color: #a08a5e; }}
    </style></head><body>
      <div class="wm">{COMPANY['name'].split()[0]}</div>

      <div class="masthead">
        <div><div class="brand">{COMPANY['name']}</div><div class="tag">{COMPANY['tagline']}</div></div>
        <div class="mh-right"><div class="lbl">Financial Year</div><div class="no">{fiscal_year}</div>
          <div class="per">Regime: {regime}</div></div>
      </div>

      <div class="idcard">
        <div class="avatar">{initials}</div>
        <div class="id-main">
          <div class="id-name">{employee_name or '—'}</div>
          <div class="id-role">{' · '.join([x for x in [designation, department] if x]) or (employee_code or '—')}</div>
        </div>
        <div class="id-meta">
          <div><span>Code</span><b>{employee_code or '—'}</b></div>
          <div><span>PAN</span><b>{agg.get('pan') or '—'}</b></div>
          <div><span>UAN</span><b>{agg.get('uan') or '—'}</b></div>
          <div><span>PF No</span><b>{agg.get('pf_number') or '—'}</b></div>
          <div><span>ESIC No</span><b>{agg.get('esic_number') or '—'}</b></div>
        </div>
      </div>

      <div class="hero">
        <div class="lbl">Total Tax Deducted at Source (FY {fiscal_year})</div>
        <div class="val">₹ {_inr(tds)}</div>
        <div class="sub">Across {agg.get('slips_count', 0)} released payslip(s) · gross paid ₹ {_inr(gross)}</div>
      </div>

      <div class="cols">
        <div class="col">
          <h3>Salary &amp; Tax Computation (Part B)</h3>
          <div class="kv"><span>Annual gross salary</span><b>₹ {_inr(annual_gross)}</b></div>
          <div class="kv"><span>− Deductions / standard deduction</span><b>₹ {_inr(std_ded)}</b></div>
          <div class="kv"><span>Taxable income</span><b>₹ {_inr(taxable)}</b></div>
          <div class="kv"><span>Income-tax payable</span><b>₹ {_inr(annual_tax)}</b></div>
          <div class="kv"><span>TDS deducted so far</span><b>₹ {_inr(tds)}</b></div>
          <div class="kv hl"><span>{'Balance payable' if balance >= 0 else 'Excess deducted'}</span><b>₹ {_inr(abs(balance))}</b></div>
        </div>
        <div class="col">
          <h3>Statutory Deductions <span class="ct">FY {fiscal_year}</span></h3>
          <table><tbody>{stat_html}</tbody></table>
        </div>
      </div>

      <div class="col mt">
        <h3>Month-by-Month TDS &amp; PF</h3>
        <table>
          <thead><tr><th>Month</th><th class="amt">Gross</th><th class="amt">PF (emp)</th><th class="amt">TDS</th></tr></thead>
          <tbody>{month_html or '<tr><td class="muted">No released payslips this year</td><td></td><td></td><td></td></tr>'}</tbody>
        </table>
      </div>

      <div class="note">This is a system-generated tax statement and does not require a signature. Generated {gen_ts}.</div>
    </body></html>"""

    return HTML(string=html).write_pdf()
