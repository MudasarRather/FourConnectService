"""Column descriptors shared by CSV + Excel renderers.

Each entry: (Header, key, width_px) — keeps the CSV header order and Excel
column widths in lock-step.
"""
from __future__ import annotations


def columns_for(report_key: str) -> list[tuple]:
    if report_key == "leave_register":
        return [
            ("Reference",       "reference_no",      120),
            ("Employee Code",   "employee_code",     110),
            ("Employee",        "employee_name",     180),
            ("Department",      "department",        140),
            ("Leave Type",      "leave_type",        110),
            ("From",            "from_date",         100),
            ("To",              "to_date",           100),
            ("Days",             "total_days",         70),
            ("Status",          "status",            130),
            ("Manager",         "manager_decision",  100),
            ("HR",              "hr_decision",       100),
            ("Override?",       "is_admin_override",  80),
            ("Reason",          "reason",            260),
            ("Applied On",      "created_at",        140),
        ]
    if report_key == "department_leaves":
        return [
            ("Department",          "department",          200),
            ("Leave Type",          "leave_type",          120),
            ("Requests",            "requests",             90),
            ("Days (Approved)",     "days",                110),
            ("Employees Affected",  "employees_affected",  130),
        ]
    if report_key == "balance_report":
        return [
            ("Fiscal Year",     "fiscal_year",   100),
            ("Employee Code",   "employee_code", 110),
            ("Employee",        "employee_name", 180),
            ("Department",      "department",    140),
            ("Leave Type",      "leave_type",    110),
            ("Quota",           "quota",          70),
            ("Opening",         "opening",        80),
            ("Accrued",         "accrued",        80),
            ("CF In",           "carry_forward_in", 80),
            ("Used",            "used",           70),
            ("Encashed",        "encashed",       80),
            ("Adj",             "adjustments",    70),
            ("Available",       "available",      90),
            ("Util %",          "utilisation_pct", 80),
        ]
    if report_key == "liability_report":
        return [
            ("Fiscal Year",     "fiscal_year",     100),
            ("Employee Code",   "employee_code",   110),
            ("Employee",        "employee_name",   180),
            ("Department",      "department",      140),
            ("Leave Type",      "leave_type",      110),
            ("Available Days",  "available_days",  110),
            ("Basic Salary",    "basic_salary",    130),
            ("Liability ₹",     "liability_amount", 140),
        ]
    if report_key == "comp_off_report":
        return [
            ("Employee Code",   "employee_code",      110),
            ("Employee",        "employee_name",      180),
            ("Department",      "department",         140),
            ("Earned On",       "earned_on",          110),
            ("Expires On",      "expires_on",         110),
            ("Days",             "days",                70),
            ("Source",          "source",              90),
            ("Days Until Expiry","days_until_expiry", 120),
            ("Expired",         "is_expired",          80),
            ("Note",            "note",               260),
        ]
    if report_key == "encashment_report":
        return [
            ("Reference",       "reference_no",       120),
            ("Employee Code",   "employee_code",      110),
            ("Employee",        "employee_name",      180),
            ("Department",      "department",         140),
            ("Leave Type",      "leave_type",         110),
            ("Fiscal Year",     "fiscal_year",         90),
            ("Days",             "days_requested",      80),
            ("Basic ₹",          "basic_salary",       120),
            ("Amount ₹",         "amount",             130),
            ("Status",           "status",              90),
            ("Decided",         "decided_at",         140),
            ("Paid",            "paid_at",            140),
            ("Payroll Ref",     "payroll_ref",        130),
        ]
    return [("Key", "key", 100), ("Value", "value", 100)]
