"""Body-table column descriptors — shared by the PDF body table and the CSV.

Each descriptor: {label, key, align, fmt, status, warn_if/danger_if/good_if}.
``fmt`` names a formatter in pdf.FORMATTERS (inr / inr_p / days / pct /
signed_pct / date) or None for raw text. The CSV exporter ignores formatting
and writes the raw value, but reuses the same (label, key) order so the PDF
and CSV always agree on columns.
"""
from __future__ import annotations


def body_columns(key: str) -> list[dict]:
    if key == "register":
        return [
            {"label": "Payslip", "key": "payslip_no", "align": "left"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Dept", "key": "department", "align": "left"},
            {"label": "Paid", "key": "paid_days", "align": "right", "fmt": "days"},
            {"label": "LOP", "key": "lop_days", "align": "right", "fmt": "days", "danger_if": lambda v: v > 0},
            {"label": "Gross", "key": "gross", "align": "right", "fmt": "inr"},
            {"label": "PF", "key": "pf_employee", "align": "right", "fmt": "inr"},
            {"label": "ESI", "key": "esi_employee", "align": "right", "fmt": "inr"},
            {"label": "PT", "key": "pt", "align": "right", "fmt": "inr"},
            {"label": "TDS", "key": "tds", "align": "right", "fmt": "inr"},
            {"label": "Deductions", "key": "deductions_total", "align": "right", "fmt": "inr"},
            {"label": "Net Pay", "key": "net", "align": "right", "fmt": "inr", "good_if": lambda v: v > 0},
        ]
    if key == "salary-sheet":
        return [
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Dept", "key": "department", "align": "left"},
            {"label": "Basic", "key": "basic", "align": "right", "fmt": "inr"},
            {"label": "HRA", "key": "hra", "align": "right", "fmt": "inr"},
            {"label": "Other Earn", "key": "other_earnings", "align": "right", "fmt": "inr"},
            {"label": "Gross", "key": "gross", "align": "right", "fmt": "inr"},
            {"label": "PF", "key": "pf_employee", "align": "right", "fmt": "inr"},
            {"label": "ESI", "key": "esi_employee", "align": "right", "fmt": "inr"},
            {"label": "PT", "key": "pt", "align": "right", "fmt": "inr"},
            {"label": "TDS", "key": "tds", "align": "right", "fmt": "inr"},
            {"label": "Deductions", "key": "deductions_total", "align": "right", "fmt": "inr"},
            {"label": "Net Pay", "key": "net", "align": "right", "fmt": "inr", "good_if": lambda v: v > 0},
        ]
    if key == "statutory":
        return [
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "PAN", "key": "pan", "align": "left"},
            {"label": "UAN", "key": "uan", "align": "left"},
            {"label": "PF (EE)", "key": "pf_employee", "align": "right", "fmt": "inr"},
            {"label": "PF (ER)", "key": "pf_employer", "align": "right", "fmt": "inr"},
            {"label": "ESI (EE)", "key": "esi_employee", "align": "right", "fmt": "inr"},
            {"label": "ESI (ER)", "key": "esi_employer", "align": "right", "fmt": "inr"},
            {"label": "PT", "key": "pt", "align": "right", "fmt": "inr"},
            {"label": "TDS", "key": "tds", "align": "right", "fmt": "inr"},
            {"label": "Statutory", "key": "statutory_total", "align": "right", "fmt": "inr"},
        ]
    if key == "pf-ecr":
        return [
            {"label": "UAN", "key": "uan", "align": "left"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Member", "key": "employee_name", "align": "left"},
            {"label": "Gross Wages", "key": "gross_wages", "align": "right", "fmt": "inr"},
            {"label": "EPF Wages", "key": "epf_wages", "align": "right", "fmt": "inr"},
            {"label": "EPS Wages", "key": "eps_wages", "align": "right", "fmt": "inr"},
            {"label": "EE PF", "key": "ee_pf", "align": "right", "fmt": "inr"},
            {"label": "ER EPS", "key": "er_eps", "align": "right", "fmt": "inr"},
            {"label": "ER EPF", "key": "er_epf", "align": "right", "fmt": "inr"},
            {"label": "NCP", "key": "ncp_days", "align": "right", "fmt": "days", "warn_if": lambda v: v > 0},
        ]
    if key == "esi":
        return [
            {"label": "ESIC No.", "key": "esic_number", "align": "left"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Member", "key": "employee_name", "align": "left"},
            {"label": "ESI Wages", "key": "esi_wages", "align": "right", "fmt": "inr"},
            {"label": "EE 0.75%", "key": "ee_esi", "align": "right", "fmt": "inr"},
            {"label": "ER 3.25%", "key": "er_esi", "align": "right", "fmt": "inr"},
            {"label": "Total", "key": "total_esi", "align": "right", "fmt": "inr"},
            {"label": "Paid Days", "key": "paid_days", "align": "right", "fmt": "days"},
        ]
    if key == "professional-tax":
        return [
            {"label": "State", "key": "state", "align": "left"},
            {"label": "Location", "key": "location", "align": "left"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Gross", "key": "gross", "align": "right", "fmt": "inr"},
            {"label": "PT", "key": "pt", "align": "right", "fmt": "inr"},
        ]
    if key == "tds-24q":
        return [
            {"label": "PAN", "key": "pan", "align": "left"},
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Taxable Gross", "key": "taxable_gross", "align": "right", "fmt": "inr"},
            {"label": "TDS (Period)", "key": "tds_period", "align": "right", "fmt": "inr"},
            {"label": "TDS (YTD)", "key": "tds_ytd", "align": "right", "fmt": "inr"},
            {"label": "Gross (YTD)", "key": "gross_ytd", "align": "right", "fmt": "inr"},
        ]
    if key == "department-cost":
        return [
            {"label": "Department", "key": "department", "align": "left"},
            {"label": "Heads", "key": "headcount", "align": "right"},
            {"label": "Gross", "key": "gross", "align": "right", "fmt": "inr"},
            {"label": "Deductions", "key": "deductions", "align": "right", "fmt": "inr"},
            {"label": "Net", "key": "net", "align": "right", "fmt": "inr"},
            {"label": "Employer Cost", "key": "employer_cost", "align": "right", "fmt": "inr"},
            {"label": "Total Cost", "key": "total_cost", "align": "right", "fmt": "inr"},
        ]
    if key == "variance":
        return [
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Dept", "key": "department", "align": "left"},
            {"label": "Prev Net", "key": "prev_net", "align": "right", "fmt": "inr"},
            {"label": "Curr Net", "key": "curr_net", "align": "right", "fmt": "inr"},
            {"label": "Change", "key": "delta", "align": "right", "fmt": "inr",
             "good_if": lambda v: v > 0, "danger_if": lambda v: v < 0},
            {"label": "Change %", "key": "delta_pct", "align": "right", "fmt": "signed_pct"},
            {"label": "Status", "key": "status", "align": "left", "status": True},
        ]
    if key == "ctc-summary":
        return [
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Dept", "key": "department", "align": "left"},
            {"label": "Designation", "key": "designation", "align": "left"},
            {"label": "Annual CTC", "key": "annual_ctc", "align": "right", "fmt": "inr"},
            {"label": "Monthly CTC", "key": "monthly_ctc", "align": "right", "fmt": "inr"},
            {"label": "Monthly Gross", "key": "monthly_gross", "align": "right", "fmt": "inr"},
            {"label": "Basic", "key": "basic", "align": "right", "fmt": "inr"},
            {"label": "Regime", "key": "tax_regime", "align": "left"},
        ]
    if key == "headcount":
        return [
            {"label": "Department", "key": "department", "align": "left"},
            {"label": "Heads", "key": "headcount", "align": "right"},
            {"label": "Head %", "key": "headcount_pct", "align": "right", "fmt": "pct"},
            {"label": "Total Cost", "key": "total_cost", "align": "right", "fmt": "inr"},
            {"label": "Cost %", "key": "cost_pct", "align": "right", "fmt": "pct"},
            {"label": "Avg Cost / Head", "key": "avg_cost", "align": "right", "fmt": "inr"},
        ]
    if key == "adjustments":
        return [
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Type", "key": "adjustment_type", "align": "left", "status": True},
            {"label": "Sub-type", "key": "sub_type", "align": "left"},
            {"label": "Title", "key": "title", "align": "left"},
            {"label": "Amount", "key": "amount", "align": "right", "fmt": "inr"},
            {"label": "Status", "key": "status", "align": "left", "status": True},
        ]
    if key == "ytd-earnings":
        return [
            {"label": "Code", "key": "employee_code", "align": "left"},
            {"label": "Employee", "key": "employee_name", "align": "left"},
            {"label": "Dept", "key": "department", "align": "left"},
            {"label": "Months", "key": "months_paid", "align": "right"},
            {"label": "YTD Gross", "key": "ytd_gross", "align": "right", "fmt": "inr"},
            {"label": "YTD Deductions", "key": "ytd_deductions", "align": "right", "fmt": "inr"},
            {"label": "YTD Net", "key": "ytd_net", "align": "right", "fmt": "inr", "good_if": lambda v: v > 0},
            {"label": "YTD TDS", "key": "ytd_tds", "align": "right", "fmt": "inr"},
        ]
    # fallback
    return [
        {"label": "Code", "key": "employee_code", "align": "left"},
        {"label": "Employee", "key": "employee_name", "align": "left"},
    ]


# Reports whose tables are wide enough to want landscape body pages.
WIDE_REPORTS = {"register", "salary-sheet", "statutory", "pf-ecr"}

# Status / category pill palette (payroll-themed) for the `status` columns.
PILL_COLORS = {
    # variance
    "UP":     {"bg": "#dcfce7", "fg": "#14532d"},
    "DOWN":   {"bg": "#fee2e2", "fg": "#7f1d1d"},
    "FLAT":   {"bg": "#f1f5f9", "fg": "#334155"},
    "JOINED": {"bg": "#e0f2fe", "fg": "#0c4a6e"},
    "EXITED": {"bg": "#fef3c7", "fg": "#854d0e"},
    # adjustment types
    "BONUS":        {"bg": "#fef3c7", "fg": "#92400e"},
    "INCENTIVE":    {"bg": "#dcfce7", "fg": "#14532d"},
    "VARIABLE_PAY": {"bg": "#cffafe", "fg": "#155e75"},
    "ARREAR":       {"bg": "#ede9fe", "fg": "#4c1d95"},
    "DEDUCTION":    {"bg": "#fee2e2", "fg": "#7f1d1d"},
    # adjustment status
    "DRAFT":    {"bg": "#f1f5f9", "fg": "#334155"},
    "APPROVED": {"bg": "#dcfce7", "fg": "#14532d"},
    "PAID":     {"bg": "#ccfbf1", "fg": "#115e59"},
}
