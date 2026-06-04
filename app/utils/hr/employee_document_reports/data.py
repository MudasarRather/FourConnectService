"""Employee-Documents reports — data fetch + shaping + per-report metadata.

Mirrors the structure of ``app.utils.hr.attendance_reports.data`` but for the
HR Employee-Documents module. One fetch of the active document universe, then
per-report ``shape(key, rows)`` filters/derives the rows each report shows.

Public:
    REPORT_KEYS
    fetch_rows(db, department_id) -> list[dict]
    shape(report_key, rows)       -> list[dict]
    shape_summary(rows)           -> dict
    report_meta(key)              -> dict
    STATUS_COLORS                 -> dict
    columns(key)                  -> list[dict]   (used by pdf + csv)
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.hr.employee_document import (
    EmployeeDocument, DocumentCategory, DocVerificationStatus, CONFIDENTIAL_CATEGORIES,
)
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.user import User


# The six report keys mirror the cards rendered by the frontend reports section.
REPORT_KEYS = ("expired", "pending", "expiring", "compliance", "verification", "category")

# Reports whose body is an aggregate (segment + count) rather than a document list.
SUMMARY_KEYS = ("verification", "category")

EXPIRING_WINDOW_DAYS = 90


# ════════════════════════════════════════════════════════════════════════════
# Per-report metadata — name, copy, accent palette, cover motif
# ════════════════════════════════════════════════════════════════════════════

REPORT_META = {
    "expired": {
        "name": "Expired Documents",
        "tagline": "Lapsed validity ledger",
        "subtitle": "Every document whose validity has already passed — act now",
        "accent": "#b91c1c", "accent_soft": "#fee2e2", "accent_deep": "#7f1d1d",
        "icon": "!", "motif": "alert", "hero_kpi": "expired",
    },
    "pending": {
        "name": "Verification Queue",
        "tagline": "Awaiting reviewer sign-off",
        "subtitle": "Documents pending verification or flagged for resubmission",
        "accent": "#ca8a04", "accent_soft": "#fef9c3", "accent_deep": "#713f12",
        "icon": "?", "motif": "alert", "hero_kpi": "pending",
    },
    "expiring": {
        "name": "Expiry Watch",
        "tagline": f"Next {EXPIRING_WINDOW_DAYS} days on the horizon",
        "subtitle": "Documents approaching their expiry date — renew before they lapse",
        "accent": "#ea580c", "accent_soft": "#ffedd5", "accent_deep": "#7c2d12",
        "icon": "◷", "motif": "radar", "hero_kpi": "expiring",
    },
    "compliance": {
        "name": "Compliance Ledger",
        "tagline": "Statutory document register",
        "subtitle": "Every compliance-category record with verification standing",
        "accent": "#0d9488", "accent_soft": "#ccfbf1", "accent_deep": "#134e4a",
        "icon": "✓", "motif": "feature", "hero_kpi": "verified",
    },
    "verification": {
        "name": "Verification Digest",
        "tagline": "Status of the document estate",
        "subtitle": "Distribution of every active document across verification states",
        "accent": "#d97706", "accent_soft": "#fef3c7", "accent_deep": "#92400e",
        "icon": "◍", "motif": "digest", "hero_kpi": "verified",
    },
    "category": {
        "name": "Category Atlas",
        "tagline": "Document estate by category",
        "subtitle": "How the active document library breaks down across categories",
        "accent": "#7c3aed", "accent_soft": "#ede9fe", "accent_deep": "#4c1d95",
        "icon": "▦", "motif": "digest", "hero_kpi": "total",
    },
}

# Status → colour ramp shared by PDF rows, pills, legend.
STATUS_COLORS = {
    "VERIFIED":          {"hex": "#0d9488", "light": "#ccfbf1", "deep": "#115e59"},
    "PENDING":           {"hex": "#a16207", "light": "#fef9c3", "deep": "#713f12"},
    "RESUBMIT_REQUIRED": {"hex": "#c2410c", "light": "#ffedd5", "deep": "#7c2d12"},
    "REJECTED":          {"hex": "#b91c1c", "light": "#fee2e2", "deep": "#7f1d1d"},
    "EXPIRED":           {"hex": "#7f1d1d", "light": "#fde2e2", "deep": "#450a0a"},
}

# Friendly labels for the category enum.
CATEGORY_LABELS = {
    "KYC": "KYC", "CONTRACT": "Contract", "CERTIFICATE": "Certificate",
    "SALARY_SLIP": "Salary Slip", "EXPERIENCE_LETTER": "Experience Letter",
    "ID_PROOF": "ID Proof", "EDUCATION": "Education", "COMPLIANCE": "Compliance",
    "OTHER": "Other",
}


def report_meta(key: str) -> dict:
    return REPORT_META.get(key) or REPORT_META["expired"]


# ════════════════════════════════════════════════════════════════════════════
# Fetch — the active document universe (one query, shaped many ways)
# ════════════════════════════════════════════════════════════════════════════


def _mask_number(num: Optional[str]) -> Optional[str]:
    if not num:
        return num
    s = str(num)
    if len(s) <= 4:
        return "••••"
    return "••••" + s[-4:]


def fetch_rows(db: Session, department_id: Optional[UUID] = None) -> list[dict]:
    """All active (non-deleted, non-archived) employee documents joined with the
    employee snapshot. Confidential document numbers are masked to last-4."""
    q = (
        db.query(
            EmployeeDocument,
            User.full_name.label("emp_name"),
            Employee.employee_id.label("emp_code"),
            Department.name.label("dept"),
        )
        .join(Employee, Employee.id == EmployeeDocument.employee_id)
        .outerjoin(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .filter(
            EmployeeDocument.is_deleted == False,   # noqa: E712
            EmployeeDocument.is_archived == False,   # noqa: E712
        )
    )
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    q = q.order_by(User.full_name.asc().nullslast(), EmployeeDocument.category.asc())

    today = date.today()
    rows: list[dict] = []
    for doc, emp_name, emp_code, dept in q.all():
        cat = doc.category.value if hasattr(doc.category, "value") else str(doc.category)
        vstatus = doc.verification_status.value if hasattr(doc.verification_status, "value") else str(doc.verification_status)
        confidential = cat in {c.value for c in CONFIDENTIAL_CATEGORIES}
        number = _mask_number(doc.document_number) if confidential else doc.document_number
        days_to_expiry = (doc.expiry_date - today).days if doc.expiry_date else None
        rows.append({
            "employee_name": emp_name or "—",
            "employee_code": emp_code or "—",
            "department": dept or "—",
            "category": cat,
            "category_label": CATEGORY_LABELS.get(cat, cat.replace("_", " ").title()),
            "doc_type": (doc.doc_type or "").replace("_", " ").title(),
            "title": doc.title or "—",
            "document_number": number or "—",
            "issued_by": doc.issued_by or "—",
            "issue_date": doc.issue_date,
            "expiry_date": doc.expiry_date,
            "verification_status": vstatus,
            "days_to_expiry": days_to_expiry,
        })
    return rows


# ════════════════════════════════════════════════════════════════════════════
# Per-report shaping
# ════════════════════════════════════════════════════════════════════════════


def _is_expired(r: dict) -> bool:
    if r["verification_status"] == "EXPIRED":
        return True
    return r["days_to_expiry"] is not None and r["days_to_expiry"] < 0


def shape(report_key: str, rows: list[dict]) -> list[dict]:
    if report_key == "expired":
        out = [r for r in rows if _is_expired(r)]
        out.sort(key=lambda r: (r["days_to_expiry"] if r["days_to_expiry"] is not None else 0))
        return out

    if report_key == "pending":
        out = [r for r in rows if r["verification_status"] in ("PENDING", "RESUBMIT_REQUIRED")]
        out.sort(key=lambda r: (r["verification_status"], r["employee_name"]))
        return out

    if report_key == "expiring":
        out = [
            r for r in rows
            if r["days_to_expiry"] is not None
            and 0 <= r["days_to_expiry"] <= EXPIRING_WINDOW_DAYS
            and r["verification_status"] != "EXPIRED"
        ]
        out.sort(key=lambda r: r["days_to_expiry"])
        return out

    if report_key == "compliance":
        out = [r for r in rows if r["category"] == "COMPLIANCE"]
        out.sort(key=lambda r: (r["verification_status"], r["employee_name"]))
        return out

    if report_key == "verification":
        order = ["VERIFIED", "PENDING", "RESUBMIT_REQUIRED", "REJECTED", "EXPIRED"]
        counts = {k: 0 for k in order}
        for r in rows:
            counts[r["verification_status"]] = counts.get(r["verification_status"], 0) + 1
        total = sum(counts.values()) or 1
        return [
            {
                "segment": s.replace("_", " ").title(),
                "status_key": s,
                "value": counts.get(s, 0),
                "pct": round(counts.get(s, 0) / total * 100),
            }
            for s in order if counts.get(s, 0) > 0
        ]

    if report_key == "category":
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["category"]] = counts.get(r["category"], 0) + 1
        total = sum(counts.values()) or 1
        items = sorted(counts.items(), key=lambda kv: -kv[1])
        return [
            {
                "segment": CATEGORY_LABELS.get(c, c.title()),
                "category_key": c,
                "value": n,
                "pct": round(n / total * 100),
            }
            for c, n in items
        ]

    return rows


def shape_summary(rows: list[dict]) -> dict:
    total = len(rows)
    verified = sum(1 for r in rows if r["verification_status"] == "VERIFIED")
    pending = sum(1 for r in rows if r["verification_status"] in ("PENDING", "RESUBMIT_REQUIRED"))
    rejected = sum(1 for r in rows if r["verification_status"] == "REJECTED")
    expired = sum(1 for r in rows if _is_expired(r))
    expiring_30 = sum(
        1 for r in rows
        if r["days_to_expiry"] is not None and 0 <= r["days_to_expiry"] <= 30
    )
    employees = len({r["employee_code"] for r in rows if r["employee_code"] != "—"})
    return {
        "total": total,
        "verified": verified,
        "pending": pending,
        "rejected": rejected,
        "expired": expired,
        "expiring": expiring_30,
        "employees": employees,
        "verified_pct": round(verified / total * 100) if total else 0,
    }


# ════════════════════════════════════════════════════════════════════════════
# Column descriptors (shared by PDF + CSV)
# ════════════════════════════════════════════════════════════════════════════


def columns(key: str) -> list[dict]:
    if key in SUMMARY_KEYS:
        return [
            {"label": "Segment", "key": "segment", "align": "left",
             "status": (key == "verification")},
            {"label": "Documents", "key": "value", "align": "right"},
            {"label": "Share", "key": "pct", "align": "left", "bar": True},
        ]

    base = [
        {"label": "Code", "key": "employee_code", "align": "left"},
        {"label": "Employee", "key": "employee_name", "align": "left"},
        {"label": "Department", "key": "department", "align": "left"},
        {"label": "Category", "key": "category_label", "align": "left"},
        {"label": "Type", "key": "doc_type", "align": "left"},
        {"label": "Doc No.", "key": "document_number", "align": "left"},
        {"label": "Status", "key": "verification_status", "align": "left", "status": True},
    ]
    if key == "expiring":
        base.append({"label": "Days left", "key": "days_to_expiry", "align": "right",
                     "warn_if": lambda v: v is not None and v <= 30,
                     "danger_if": lambda v: v is not None and v <= 7})
        base.append({"label": "Expiry", "key": "expiry_date", "align": "left", "fmt": "date"})
    elif key == "expired":
        base.append({"label": "Expired on", "key": "expiry_date", "align": "left",
                     "fmt": "date", "danger_if": lambda v: True})
    else:
        base.append({"label": "Expiry", "key": "expiry_date", "align": "left", "fmt": "date"})
    return base
