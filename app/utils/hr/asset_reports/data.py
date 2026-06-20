"""HR Asset Management — report catalog, metadata and data shapers.

Sixteen reports covering the whole asset estate so the Reports hub mirrors the
module with no blind spots. Each shaper returns a uniform shape so the CSV /
Excel / PDF renderers stay generic:

    {key, title, subtitle, eyebrow, columns, rows, summary, period}

Column descriptors carry an optional ``fmt`` ("money" | "int" | "pct", omit for
text) and ``align`` ("right" for numerics, else "left") so the Excel / PDF
renderers can type, format and align cells without per-report code.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.asset import (
    Asset, AssetAllocation, AssetStatus, AllocationStatus, AssetCondition,
)
from app.models.hr.asset_lifecycle import (
    AssetCategory, Vendor, AssetTransfer, AssetMaintenance, AssetDamage,
    AssetAudit, AssetDisposal, AssetMaintenanceStatus, AssetDamageStatus,
)


# ════════════════════════════ METADATA ════════════════════════════
REPORT_META: Dict[str, dict] = {
    "estate_overview": {
        "name": "Estate Overview", "tagline": "Executive one-pager across the whole asset estate",
        "eyebrow": "OVERVIEW · ESTATE DASHBOARD", "group": "overview", "icon": "E", "motif": "observatory",
        "accent": "#fbbf24", "accent_deep": "#b45309", "accent_soft": "#fff7e6",
    },
    "inventory_register": {
        "name": "Asset Register", "tagline": "The full inventory with status, condition, owner & value",
        "eyebrow": "INVENTORY · MASTER LEDGER", "group": "inventory", "icon": "I", "motif": "register",
        "accent": "#fbbf24", "accent_deep": "#b45309", "accent_soft": "#fff7e6",
    },
    "category_distribution": {
        "name": "Category Distribution", "tagline": "How the estate spreads across categories by count & value",
        "eyebrow": "INVENTORY · CLASSIFICATION", "group": "inventory", "icon": "C", "motif": "spectrum",
        "accent": "#fbbf24", "accent_deep": "#b45309", "accent_soft": "#fff7e6",
    },
    "allocation_register": {
        "name": "Allocation Register", "tagline": "Who holds what, since when, and how long it has been out",
        "eyebrow": "ALLOCATION · FIELD LEDGER", "group": "allocation", "icon": "A", "motif": "field",
        "accent": "#f59e0b", "accent_deep": "#92400e", "accent_soft": "#fef3c7",
    },
    "allocation_by_department": {
        "name": "Allocation by Department", "tagline": "Asset exposure and field-deployment per department",
        "eyebrow": "ALLOCATION · DEPARTMENT MAP", "group": "allocation", "icon": "Y", "motif": "map",
        "accent": "#fb923c", "accent_deep": "#c2410c", "accent_soft": "#fff1e6",
    },
    "unacknowledged": {
        "name": "Unacknowledged Assets", "tagline": "Issued assets still awaiting employee sign-off",
        "eyebrow": "ALLOCATION · SIGN-OFF GAP", "group": "allocation", "icon": "U", "motif": "signoff",
        "accent": "#fb923c", "accent_deep": "#c2410c", "accent_soft": "#fff1e6",
    },
    "maintenance_log": {
        "name": "Maintenance Log", "tagline": "Repairs & preventive jobs with cost and downtime",
        "eyebrow": "LIFECYCLE · SERVICE LOG", "group": "lifecycle", "icon": "M", "motif": "servicebay",
        "accent": "#9aa1ab", "accent_deep": "#4b515b", "accent_soft": "#eef1f5",
    },
    "damage_log": {
        "name": "Damage & Loss Log", "tagline": "Incidents by severity, status and recovery",
        "eyebrow": "LIFECYCLE · INCIDENT LOG", "group": "lifecycle", "icon": "D", "motif": "incident",
        "accent": "#f87171", "accent_deep": "#b91c1c", "accent_soft": "#fee2e2",
    },
    "transfers_log": {
        "name": "Transfers Log", "tagline": "Movements between employees, locations & departments",
        "eyebrow": "LIFECYCLE · MOVEMENT LOG", "group": "lifecycle", "icon": "T", "motif": "relay",
        "accent": "#fbbf24", "accent_deep": "#b45309", "accent_soft": "#fff7e6",
    },
    "financial_valuation": {
        "name": "Asset Valuation", "tagline": "Straight-line book value & depreciation",
        "eyebrow": "FINANCIAL · VALUATION", "group": "financial", "icon": "V", "motif": "valuation",
        "accent": "#34d399", "accent_deep": "#047857", "accent_soft": "#ecfdf5",
    },
    "asset_aging": {
        "name": "Asset Aging", "tagline": "Estate age strata and refresh candidates",
        "eyebrow": "FINANCIAL · AGE STRATA", "group": "financial", "icon": "G", "motif": "strata",
        "accent": "#f59e0b", "accent_deep": "#92400e", "accent_soft": "#fef3c7",
    },
    "vendor_spend": {
        "name": "Vendor Spend", "tagline": "Procurement concentration & supplier ratings",
        "eyebrow": "FINANCIAL · SUPPLY SPEND", "group": "financial", "icon": "S", "motif": "constellation",
        "accent": "#34d399", "accent_deep": "#047857", "accent_soft": "#ecfdf5",
    },
    "warranty_expiry": {
        "name": "Warranty Expiry", "tagline": "Coverage horizon — what lapses and when",
        "eyebrow": "COMPLIANCE · WARRANTY HORIZON", "group": "compliance", "icon": "W", "motif": "horizon",
        "accent": "#fcd34d", "accent_deep": "#a16207", "accent_soft": "#fefce8",
    },
    "compliance": {
        "name": "Data Quality", "tagline": "Records missing key fields or running past warranty",
        "eyebrow": "COMPLIANCE · DATA HYGIENE", "group": "compliance", "icon": "Q", "motif": "hygiene",
        "accent": "#ea580c", "accent_deep": "#9a3412", "accent_soft": "#ffedd5",
    },
    "audit_reconciliation": {
        "name": "Audit Reconciliation", "tagline": "Physical-audit outcomes: found, missing, mismatched",
        "eyebrow": "GOVERNANCE · RECONCILIATION", "group": "governance", "icon": "R", "motif": "reconcile",
        "accent": "#fb923c", "accent_deep": "#9a3412", "accent_soft": "#ffedd5",
    },
    "disposal_register": {
        "name": "Disposal Register", "tagline": "Retired & disposed assets with method and value",
        "eyebrow": "GOVERNANCE · DISPOSAL LEDGER", "group": "governance", "icon": "X", "motif": "foundry",
        "accent": "#6b7280", "accent_deep": "#374151", "accent_soft": "#f3f4f6",
    },
}

REPORT_KEYS = list(REPORT_META.keys())
REPORTS = [{"key": k, **v} for k, v in REPORT_META.items()]


def report_meta(key: str) -> dict:
    """Return the meta for ``key`` with ``"key"`` injected so renderers can read
    ``meta["key"]`` (issue number, slab) without a separate argument."""
    m = REPORT_META.get(key)
    if m is None:
        return {"key": key}
    return {"key": key, **m}


# ════════════════════════════ HELPERS ════════════════════════════
def _employee_names(db: Session) -> Dict[UUID, str]:
    rows = db.query(Employee.id, User.full_name).join(User, Employee.user_id == User.id).all()
    return {r[0]: r[1] for r in rows}


def _vendor_map(db: Session) -> Dict[UUID, Vendor]:
    return {v.id: v for v in db.query(Vendor).all()}


def _vendor_names(db: Session) -> Dict[UUID, str]:
    return {r[0]: r[1] for r in db.query(Vendor.id, Vendor.name).all()}


def _department_names(db: Session) -> Dict[UUID, str]:
    return {r[0]: r[1] for r in db.query(Department.id, Department.name).all()}


def _category_map(db: Session) -> Dict[UUID, AssetCategory]:
    return {c.id: c for c in db.query(AssetCategory).all()}


def _asset_codes(db: Session) -> Dict[UUID, str]:
    return {r[0]: r[1] for r in db.query(Asset.id, Asset.asset_code).all()}


def _period(filters: dict) -> dict:
    f, t = filters.get("from"), filters.get("to")
    if f and t:
        return {"label": f"{f.isoformat()} → {t.isoformat()}", "from": f.isoformat(), "to": t.isoformat()}
    return {"label": "All time", "from": None, "to": None}


def _months_between(d: Optional[date], today: date) -> int:
    if not d:
        return 0
    return max(0, (today.year - d.year) * 12 + (today.month - d.month))


def _dept_filter(q, filters: dict):
    """Apply the optional department_id filter on Asset-based reports. Null-safe."""
    dep = filters.get("department_id")
    if dep:
        q = q.filter(Asset.department_id == dep)
    return q


def _book_value(a: Asset, cats: Dict[UUID, AssetCategory], today: date) -> float:
    """Straight-line book value with category useful-life; falls back to stored
    current_book_value, then to cost when no life is known."""
    cost = float(a.purchase_cost or 0)
    salvage = float(a.salvage_value or 0)
    life = cats[a.category_id].useful_life_months if (a.category_id in cats and cats[a.category_id].useful_life_months) else None
    if a.current_book_value is not None:
        return float(a.current_book_value)
    if life:
        monthly = max(0.0, (cost - salvage) / life)
        return max(salvage, cost - monthly * _months_between(a.purchase_date, today))
    return cost


# ════════════════════════════ SHAPERS ════════════════════════════
def _estate_overview(db: Session, filters: dict) -> dict:
    """Executive one-pager. One row per AssetStatus, plus estate-wide headline KPIs."""
    today = date.today()
    cats = _category_map(db)
    q = _dept_filter(db.query(Asset).filter(Asset.is_deleted == False), filters)  # noqa: E712
    assets = q.all()
    total = len(assets)
    value = sum(float(a.purchase_cost or 0) for a in assets)
    book_value = sum(_book_value(a, cats, today) for a in assets)
    by_status: Dict[str, int] = {}
    by_status_value: Dict[str, float] = {}
    lapsed_warranty = 0
    for a in assets:
        sv = a.status.value
        by_status[sv] = by_status.get(sv, 0) + 1
        by_status_value[sv] = by_status_value.get(sv, 0.0) + float(a.purchase_cost or 0)
        if a.warranty_end and a.warranty_end < today:
            lapsed_warranty += 1

    # Allocation-driven KPIs (estate-wide, not dept-scoped — note: allocations carry no dept).
    overdue = db.query(AssetAllocation).filter(
        AssetAllocation.status == AllocationStatus.ALLOCATED,
        AssetAllocation.expected_return_date != None,  # noqa: E711
        AssetAllocation.expected_return_date < today,
    ).count()
    open_maintenance = db.query(AssetMaintenance).filter(
        AssetMaintenance.is_deleted == False,  # noqa: E712
        AssetMaintenance.status.notin_([AssetMaintenanceStatus.COMPLETED, AssetMaintenanceStatus.CANCELLED]),
    ).count()

    order = [s.value for s in AssetStatus]
    rows = []
    for seg in order:
        cnt = by_status.get(seg, 0)
        rows.append({
            "segment": seg, "count": cnt, "value": round(by_status_value.get(seg, 0.0), 2),
            "share": round(cnt / total * 100, 1) if total else 0.0,
        })
    return {
        "key": "estate_overview", "title": "Estate Overview",
        "subtitle": f"{total} assets · value ₹{value:,.0f} · book ₹{book_value:,.0f}",
        "eyebrow": REPORT_META["estate_overview"]["eyebrow"],
        "columns": [
            {"key": "segment", "label": "Segment", "align": "left"},
            {"key": "count", "label": "Count", "fmt": "int", "align": "right"},
            {"key": "value", "label": "Value", "fmt": "money", "align": "right"},
            {"key": "share", "label": "Share %", "fmt": "pct", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "assets": total, "value": round(value, 2), "book_value": round(book_value, 2),
            "allocated": by_status.get(AssetStatus.ALLOCATED.value, 0),
            "available": by_status.get(AssetStatus.AVAILABLE.value, 0),
            "maintenance": by_status.get(AssetStatus.MAINTENANCE.value, 0),
            "retired": by_status.get(AssetStatus.RETIRED.value, 0),
            "overdue": overdue, "open_maintenance": open_maintenance, "lapsed_warranty": lapsed_warranty,
        },
        "period": _period(filters),
    }


def _inventory_register(db: Session, filters: dict) -> dict:
    cats = _category_map(db)
    vendors = _vendor_names(db)
    emps = _employee_names(db)
    q = _dept_filter(db.query(Asset).filter(Asset.is_deleted == False), filters)  # noqa: E712
    assets = q.order_by(Asset.asset_code.asc()).all()
    rows, by_status, by_condition, total_value = [], {}, {}, 0.0
    allocated = available = 0
    for a in assets:
        by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
        by_condition[a.condition.value] = by_condition.get(a.condition.value, 0) + 1
        if a.status == AssetStatus.ALLOCATED:
            allocated += 1
        elif a.status == AssetStatus.AVAILABLE:
            available += 1
        total_value += float(a.purchase_cost or 0)
        rows.append({
            "asset_code": a.asset_code, "asset_type": a.asset_type.value, "brand": a.brand, "model": a.model,
            "serial_number": a.serial_number, "status": a.status.value, "condition": a.condition.value,
            "category": cats[a.category_id].name if a.category_id in cats else None,
            "vendor": vendors.get(a.vendor_id), "holder": emps.get(a.assigned_employee_id),
            "purchase_cost": float(a.purchase_cost) if a.purchase_cost is not None else None,
        })
    return {
        "key": "inventory_register", "title": "Asset Register",
        "subtitle": f"{len(assets)} assets · total value ₹{total_value:,.0f}",
        "eyebrow": REPORT_META["inventory_register"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "asset_type", "label": "Type", "align": "left"},
            {"key": "brand", "label": "Brand", "align": "left"}, {"key": "model", "label": "Model", "align": "left"},
            {"key": "serial_number", "label": "Serial", "align": "left"}, {"key": "status", "label": "Status", "align": "left"},
            {"key": "condition", "label": "Condition", "align": "left"}, {"key": "category", "label": "Category", "align": "left"},
            {"key": "vendor", "label": "Vendor", "align": "left"}, {"key": "holder", "label": "Holder", "align": "left"},
            {"key": "purchase_cost", "label": "Cost", "fmt": "money", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "total": len(assets), "total_value": round(total_value, 2),
            "allocated": allocated, "available": available,
            "by_status": by_status, "by_condition": by_condition,
        },
        "period": _period(filters),
    }


def _category_distribution(db: Session, filters: dict) -> dict:
    cats = _category_map(db)
    q = _dept_filter(db.query(Asset).filter(Asset.is_deleted == False), filters)  # noqa: E712
    assets = q.all()
    buckets: Dict[Any, dict] = {}
    total_value = 0.0
    for a in assets:
        cost = float(a.purchase_cost or 0)
        total_value += cost
        cid = a.category_id if a.category_id in cats else None
        b = buckets.setdefault(cid, {"count": 0, "value": 0.0})
        b["count"] += 1
        b["value"] += cost
    total_assets = len(assets)
    rows = []
    for cid, b in buckets.items():
        cat = cats.get(cid)
        rows.append({
            "category": cat.name if cat else "(Uncategorised)",
            "code": cat.code if cat else None,
            "asset_count": b["count"],
            "value": round(b["value"], 2),
            "share": round(b["count"] / total_assets * 100, 1) if total_assets else 0.0,
            "depreciation_method": (cat.depreciation_method if cat else None),
            "useful_life_months": (cat.useful_life_months if cat else None),
        })
    rows.sort(key=lambda r: r["asset_count"], reverse=True)
    top = rows[0]["category"] if rows else "—"
    return {
        "key": "category_distribution", "title": "Category Distribution",
        "subtitle": f"{len(rows)} categories · {total_assets} assets · value ₹{total_value:,.0f}",
        "eyebrow": REPORT_META["category_distribution"]["eyebrow"],
        "columns": [
            {"key": "category", "label": "Category", "align": "left"},
            {"key": "code", "label": "Code", "align": "left"},
            {"key": "asset_count", "label": "Assets", "fmt": "int", "align": "right"},
            {"key": "value", "label": "Value", "fmt": "money", "align": "right"},
            {"key": "share", "label": "Share %", "fmt": "pct", "align": "right"},
            {"key": "depreciation_method", "label": "Depreciation", "align": "left"},
            {"key": "useful_life_months", "label": "Useful life (mo)", "fmt": "int", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "categories": len(rows), "total_assets": total_assets,
            "total_value": round(total_value, 2), "top_category": top,
        },
        "period": _period(filters),
    }


def _allocation_register(db: Session, filters: dict) -> dict:
    # NOTE: allocations carry no department, so department_id is not applied here.
    codes = _asset_codes(db)
    emps = _employee_names(db)
    today = date.today()
    q = db.query(AssetAllocation).filter(AssetAllocation.status == AllocationStatus.ALLOCATED)
    rows = []
    overdue = acknowledged = unacknowledged = 0
    days_out_total = 0
    for al in q.order_by(AssetAllocation.allocated_date.asc()).all():
        days_out = (today - al.allocated_date).days if al.allocated_date else 0
        days_out_total += days_out
        is_overdue = bool(al.expected_return_date and al.expected_return_date < today)
        if is_overdue:
            overdue += 1
        if al.acknowledged_by_employee:
            acknowledged += 1
        else:
            unacknowledged += 1
        rows.append({
            "asset_code": codes.get(al.asset_id), "holder": emps.get(al.employee_id),
            "allocated_date": al.allocated_date, "expected_return_date": al.expected_return_date,
            "days_out": days_out, "overdue": is_overdue, "acknowledged": al.acknowledged_by_employee,
        })
    return {
        "key": "allocation_register", "title": "Allocation Register",
        "subtitle": f"{len(rows)} assets in the field · {overdue} overdue",
        "eyebrow": REPORT_META["allocation_register"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "holder", "label": "Holder", "align": "left"},
            {"key": "allocated_date", "label": "Since", "align": "left"}, {"key": "expected_return_date", "label": "Due", "align": "left"},
            {"key": "days_out", "label": "Days out", "fmt": "int", "align": "right"},
            {"key": "overdue", "label": "Overdue", "align": "left"}, {"key": "acknowledged", "label": "Ack'd", "align": "left"},
        ],
        "rows": rows,
        "summary": {
            "total": len(rows), "overdue": overdue, "acknowledged": acknowledged,
            "unacknowledged": unacknowledged,
            "avg_days_out": int(round(days_out_total / len(rows))) if rows else 0,
        },
        "period": _period(filters),
    }


def _allocation_by_department(db: Session, filters: dict) -> dict:
    depts = _department_names(db)
    assets = db.query(Asset).filter(Asset.is_deleted == False).all()  # noqa: E712
    buckets: Dict[Any, dict] = {}
    total_value = 0.0
    for a in assets:
        cost = float(a.purchase_cost or 0)
        total_value += cost
        did = a.department_id if a.department_id in depts else None
        b = buckets.setdefault(did, {"count": 0, "allocated": 0, "available": 0, "value": 0.0})
        b["count"] += 1
        b["value"] += cost
        if a.status == AssetStatus.ALLOCATED:
            b["allocated"] += 1
        elif a.status == AssetStatus.AVAILABLE:
            b["available"] += 1
    rows = []
    for did, b in buckets.items():
        rows.append({
            "department": depts.get(did, "(Unassigned)") if did else "(Unassigned)",
            "asset_count": b["count"], "allocated": b["allocated"],
            "available": b["available"], "value": round(b["value"], 2),
        })
    rows.sort(key=lambda r: r["asset_count"], reverse=True)
    top = rows[0]["department"] if rows else "—"
    return {
        "key": "allocation_by_department", "title": "Allocation by Department",
        "subtitle": f"{len(rows)} departments · {len(assets)} assets · value ₹{total_value:,.0f}",
        "eyebrow": REPORT_META["allocation_by_department"]["eyebrow"],
        "columns": [
            {"key": "department", "label": "Department", "align": "left"},
            {"key": "asset_count", "label": "Assets", "fmt": "int", "align": "right"},
            {"key": "allocated", "label": "Allocated", "fmt": "int", "align": "right"},
            {"key": "available", "label": "Available", "fmt": "int", "align": "right"},
            {"key": "value", "label": "Value", "fmt": "money", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "departments": len(rows), "total_assets": len(assets),
            "total_value": round(total_value, 2), "top_department": top,
        },
        "period": _period(filters),
    }


def _unacknowledged(db: Session, filters: dict) -> dict:
    codes = _asset_codes(db)
    emps = _employee_names(db)
    today = date.today()
    q = db.query(AssetAllocation).filter(
        AssetAllocation.status == AllocationStatus.ALLOCATED,
        AssetAllocation.acknowledged_by_employee == False,  # noqa: E712
    )
    rows = []
    pend_total = max_pend = over_7d = 0
    for al in q.order_by(AssetAllocation.allocated_date.asc()).all():
        days_pending = (today - al.allocated_date).days if al.allocated_date else 0
        pend_total += days_pending
        max_pend = max(max_pend, days_pending)
        if days_pending > 7:
            over_7d += 1
        rows.append({
            "asset_code": codes.get(al.asset_id), "holder": emps.get(al.employee_id),
            "allocated_date": al.allocated_date, "days_pending": days_pending,
        })
    return {
        "key": "unacknowledged", "title": "Unacknowledged Assets",
        "subtitle": f"{len(rows)} assets awaiting employee sign-off",
        "eyebrow": REPORT_META["unacknowledged"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "holder", "label": "Holder", "align": "left"},
            {"key": "allocated_date", "label": "Issued", "align": "left"},
            {"key": "days_pending", "label": "Days pending", "fmt": "int", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "total": len(rows),
            "avg_days_pending": int(round(pend_total / len(rows))) if rows else 0,
            "max_days_pending": max_pend, "over_7d": over_7d,
        },
        "period": _period(filters),
    }


def _maintenance_log(db: Session, filters: dict) -> dict:
    codes = _asset_codes(db)
    vendors = _vendor_names(db)
    q = db.query(AssetMaintenance).filter(AssetMaintenance.is_deleted == False)  # noqa: E712
    if filters.get("from"):
        q = q.filter(AssetMaintenance.reported_date >= filters["from"])
    if filters.get("to"):
        q = q.filter(AssetMaintenance.reported_date <= filters["to"])
    rows, total_cost, by_status, by_type = [], 0.0, {}, {}
    completed = open_jobs = 0
    for m in q.order_by(AssetMaintenance.created_at.desc()).all():
        total_cost += float(m.cost or 0)
        by_status[m.status.value] = by_status.get(m.status.value, 0) + 1
        by_type[m.maintenance_type.value] = by_type.get(m.maintenance_type.value, 0) + 1
        if m.status == AssetMaintenanceStatus.COMPLETED:
            completed += 1
        elif m.status != AssetMaintenanceStatus.CANCELLED:
            open_jobs += 1
        rows.append({
            "asset_code": codes.get(m.asset_id), "type": m.maintenance_type.value, "status": m.status.value,
            "vendor": vendors.get(m.vendor_id), "scheduled_date": m.scheduled_date,
            "completed_date": m.completed_date, "cost": float(m.cost) if m.cost is not None else None,
        })
    return {
        "key": "maintenance_log", "title": "Maintenance Log",
        "subtitle": f"{len(rows)} jobs · total cost ₹{total_cost:,.0f}",
        "eyebrow": REPORT_META["maintenance_log"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "type", "label": "Type", "align": "left"},
            {"key": "status", "label": "Status", "align": "left"}, {"key": "vendor", "label": "Vendor", "align": "left"},
            {"key": "scheduled_date", "label": "Scheduled", "align": "left"}, {"key": "completed_date", "label": "Completed", "align": "left"},
            {"key": "cost", "label": "Cost", "fmt": "money", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "total": len(rows), "total_cost": round(total_cost, 2),
            "completed": completed, "open": open_jobs, "by_status": by_status, "by_type": by_type,
        },
        "period": _period(filters),
    }


def _damage_log(db: Session, filters: dict) -> dict:
    codes = _asset_codes(db)
    q = db.query(AssetDamage).filter(AssetDamage.is_deleted == False)  # noqa: E712
    if filters.get("from"):
        q = q.filter(AssetDamage.reported_date >= filters["from"])
    if filters.get("to"):
        q = q.filter(AssetDamage.reported_date <= filters["to"])
    rows, recovery, by_severity = [], 0.0, {}
    open_inc = resolved = writeoff = 0
    _closed = (AssetDamageStatus.RESOLVED, AssetDamageStatus.WRITE_OFF, AssetDamageStatus.REJECTED)
    for d in q.order_by(AssetDamage.created_at.desc()).all():
        recovery += float(d.recovery_amount or 0)
        by_severity[d.severity.value] = by_severity.get(d.severity.value, 0) + 1
        if d.status == AssetDamageStatus.RESOLVED:
            resolved += 1
        if d.status == AssetDamageStatus.WRITE_OFF:
            writeoff += 1
        if d.status not in _closed:
            open_inc += 1
        rows.append({
            "asset_code": codes.get(d.asset_id), "severity": d.severity.value, "status": d.status.value,
            "title": d.title, "reported_date": d.reported_date, "resolved_date": d.resolved_date,
            "recovery_amount": float(d.recovery_amount) if d.recovery_amount is not None else None,
        })
    return {
        "key": "damage_log", "title": "Damage & Loss Log",
        "subtitle": f"{len(rows)} incidents · ₹{recovery:,.0f} recovered",
        "eyebrow": REPORT_META["damage_log"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "severity", "label": "Severity", "align": "left"},
            {"key": "status", "label": "Status", "align": "left"}, {"key": "title", "label": "Title", "align": "left"},
            {"key": "reported_date", "label": "Reported", "align": "left"}, {"key": "resolved_date", "label": "Resolved", "align": "left"},
            {"key": "recovery_amount", "label": "Recovered", "fmt": "money", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "total": len(rows), "recovery": round(recovery, 2), "by_severity": by_severity,
            "open": open_inc, "resolved": resolved, "writeoff": writeoff,
        },
        "period": _period(filters),
    }


def _transfers_log(db: Session, filters: dict) -> dict:
    codes = _asset_codes(db)
    emps = _employee_names(db)
    q = db.query(AssetTransfer).filter(AssetTransfer.is_deleted == False)  # noqa: E712
    rows, by_type, by_status = [], {}, {}
    completed = 0
    for t in q.order_by(AssetTransfer.created_at.desc()).all():
        by_type[t.transfer_type.value] = by_type.get(t.transfer_type.value, 0) + 1
        by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
        if t.status.value == "COMPLETED":
            completed += 1
        rows.append({
            "asset_code": codes.get(t.asset_id), "type": t.transfer_type.value, "status": t.status.value,
            "from_holder": emps.get(t.from_employee_id), "to_holder": emps.get(t.to_employee_id),
            "effective_date": t.effective_date,
        })
    return {
        "key": "transfers_log", "title": "Transfers Log",
        "subtitle": f"{len(rows)} movements",
        "eyebrow": REPORT_META["transfers_log"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "type", "label": "Type", "align": "left"},
            {"key": "status", "label": "Status", "align": "left"}, {"key": "from_holder", "label": "From", "align": "left"},
            {"key": "to_holder", "label": "To", "align": "left"}, {"key": "effective_date", "label": "Effective", "align": "left"},
        ],
        "rows": rows,
        "summary": {"total": len(rows), "by_type": by_type, "by_status": by_status, "completed": completed},
        "period": _period(filters),
    }


def _warranty_expiry(db: Session, filters: dict) -> dict:
    today = date.today()
    q = db.query(Asset).filter(Asset.is_deleted == False, Asset.warranty_end != None)  # noqa: E711, E712
    q = _dept_filter(q, filters)
    rows = []
    lapsed = soon = quarter = safe = 0
    for a in q.order_by(Asset.warranty_end.asc()).all():
        days_left = (a.warranty_end - today).days
        if days_left < 0:
            lapsed += 1
        elif days_left <= 30:
            soon += 1
        elif days_left <= 90:
            quarter += 1
        else:
            safe += 1
        rows.append({
            "asset_code": a.asset_code, "asset_type": a.asset_type.value, "status": a.status.value,
            "warranty_end": a.warranty_end, "days_left": days_left,
        })
    return {
        "key": "warranty_expiry", "title": "Warranty Expiry",
        "subtitle": f"{len(rows)} covered assets · {lapsed} already lapsed",
        "eyebrow": REPORT_META["warranty_expiry"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "asset_type", "label": "Type", "align": "left"},
            {"key": "status", "label": "Status", "align": "left"}, {"key": "warranty_end", "label": "Expires", "align": "left"},
            {"key": "days_left", "label": "Days left", "fmt": "int", "align": "right"},
        ],
        "rows": rows,
        "summary": {"total": len(rows), "lapsed": lapsed, "soon": soon, "quarter": quarter, "safe": safe},
        "period": _period(filters),
    }


def _financial_valuation(db: Session, filters: dict) -> dict:
    cats = _category_map(db)
    today = date.today()
    q = db.query(Asset).filter(Asset.is_deleted == False, Asset.purchase_cost != None)  # noqa: E711, E712
    q = _dept_filter(q, filters)
    rows, total_cost, total_book = [], 0.0, 0.0
    age_total = 0
    for a in q.order_by(Asset.asset_code.asc()).all():
        cost = float(a.purchase_cost or 0)
        age = _months_between(a.purchase_date, today)
        age_total += age
        book = _book_value(a, cats, today)
        total_cost += cost
        total_book += book
        rows.append({
            "asset_code": a.asset_code, "purchase_date": a.purchase_date, "purchase_cost": round(cost, 2),
            "age_months": age, "book_value": round(book, 2),
        })
    return {
        "key": "financial_valuation", "title": "Asset Valuation",
        "subtitle": f"Cost ₹{total_cost:,.0f} · book ₹{total_book:,.0f}",
        "eyebrow": REPORT_META["financial_valuation"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "purchase_date", "label": "Purchased", "align": "left"},
            {"key": "purchase_cost", "label": "Cost", "fmt": "money", "align": "right"},
            {"key": "age_months", "label": "Age (mo)", "fmt": "int", "align": "right"},
            {"key": "book_value", "label": "Book value", "fmt": "money", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "total_cost": round(total_cost, 2), "total_book": round(total_book, 2), "count": len(rows),
            "depreciation": round(total_cost - total_book, 2),
            "avg_age": int(round(age_total / len(rows))) if rows else 0,
        },
        "period": _period(filters),
    }


def _asset_aging(db: Session, filters: dict) -> dict:
    today = date.today()
    q = db.query(Asset).filter(Asset.is_deleted == False, Asset.purchase_date != None)  # noqa: E711, E712
    q = _dept_filter(q, filters)
    rows = []
    b_0_12 = b_12_24 = b_24_36 = b_36_48 = b_48_plus = 0
    age_total = refresh = 0
    for a in q.order_by(Asset.purchase_date.asc()).all():
        age = _months_between(a.purchase_date, today)
        age_total += age
        if age < 12:
            bucket = "0-12"; b_0_12 += 1
        elif age < 24:
            bucket = "12-24"; b_12_24 += 1
        elif age < 36:
            bucket = "24-36"; b_24_36 += 1
        elif age < 48:
            bucket = "36-48"; b_36_48 += 1
        else:
            bucket = "48+"; b_48_plus += 1
        if age >= 36:
            refresh += 1
        rows.append({
            "asset_code": a.asset_code, "asset_type": a.asset_type.value, "purchase_date": a.purchase_date,
            "age_months": age, "bucket": bucket, "status": a.status.value,
        })
    return {
        "key": "asset_aging", "title": "Asset Aging",
        "subtitle": f"{len(rows)} dated assets · {refresh} refresh candidates",
        "eyebrow": REPORT_META["asset_aging"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "asset_type", "label": "Type", "align": "left"},
            {"key": "purchase_date", "label": "Purchased", "align": "left"},
            {"key": "age_months", "label": "Age (mo)", "fmt": "int", "align": "right"},
            {"key": "bucket", "label": "Bracket", "align": "left"}, {"key": "status", "label": "Status", "align": "left"},
        ],
        "rows": rows,
        "summary": {
            "total": len(rows), "avg_age_months": int(round(age_total / len(rows))) if rows else 0,
            "b_0_12": b_0_12, "b_12_24": b_12_24, "b_24_36": b_24_36, "b_36_48": b_36_48,
            "b_48_plus": b_48_plus, "refresh_candidates": refresh,
        },
        "period": _period(filters),
    }


def _vendor_spend(db: Session, filters: dict) -> dict:
    vendors = _vendor_map(db)
    assets = db.query(Asset).filter(Asset.is_deleted == False).all()  # noqa: E712
    buckets: Dict[Any, dict] = {}
    total_spend = 0.0
    for a in assets:
        cost = float(a.purchase_cost or 0)
        total_spend += cost
        vid = a.vendor_id if a.vendor_id in vendors else None
        b = buckets.setdefault(vid, {"count": 0, "spend": 0.0})
        b["count"] += 1
        b["spend"] += cost
    rows = []
    ratings = []
    for vid, b in buckets.items():
        v = vendors.get(vid)
        if v and v.rating is not None:
            ratings.append(v.rating)
        rows.append({
            "vendor": v.name if v else "(No vendor)",
            "asset_count": b["count"], "total_spend": round(b["spend"], 2),
            "rating": (v.rating if v else None),
            "is_active": (v.is_active if v else None),
        })
    rows.sort(key=lambda r: r["total_spend"], reverse=True)
    top = rows[0]["vendor"] if rows else "—"
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0.0
    return {
        "key": "vendor_spend", "title": "Vendor Spend",
        "subtitle": f"{len(rows)} vendors · spend ₹{total_spend:,.0f}",
        "eyebrow": REPORT_META["vendor_spend"]["eyebrow"],
        "columns": [
            {"key": "vendor", "label": "Vendor", "align": "left"},
            {"key": "asset_count", "label": "Assets", "fmt": "int", "align": "right"},
            {"key": "total_spend", "label": "Spend", "fmt": "money", "align": "right"},
            {"key": "rating", "label": "Rating", "fmt": "int", "align": "right"},
            {"key": "is_active", "label": "Active", "align": "left"},
        ],
        "rows": rows,
        "summary": {
            "vendors": len(rows), "total_spend": round(total_spend, 2),
            "top_vendor": top, "avg_rating": avg_rating,
        },
        "period": _period(filters),
    }


def _compliance(db: Session, filters: dict) -> dict:
    today = date.today()
    q = db.query(Asset).filter(Asset.is_deleted == False)  # noqa: E712
    q = _dept_filter(q, filters)
    rows, by_issue = [], {}
    for a in q.order_by(Asset.asset_code.asc()).all():
        issues = []
        if not a.serial_number:
            issues.append("no serial")
        if not a.tag:
            issues.append("no tag")
        if not a.category_id:
            issues.append("no category")
        if not a.invoice_no and not a.invoice_path:
            issues.append("no invoice")
        if a.warranty_end and a.warranty_end < today and a.status == AssetStatus.ALLOCATED:
            issues.append("in use past warranty")
        if issues:
            for label in issues:
                by_issue[label] = by_issue.get(label, 0) + 1
            rows.append({"asset_code": a.asset_code, "asset_type": a.asset_type.value,
                         "status": a.status.value, "issues": ", ".join(issues)})
    return {
        "key": "compliance", "title": "Data Quality",
        "subtitle": f"{len(rows)} records need attention",
        "eyebrow": REPORT_META["compliance"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "asset_type", "label": "Type", "align": "left"},
            {"key": "status", "label": "Status", "align": "left"}, {"key": "issues", "label": "Issues", "align": "left"},
        ],
        "rows": rows,
        "summary": {"total": len(rows), "by_issue": by_issue},
        "period": _period(filters),
    }


def _audit_reconciliation(db: Session, filters: dict) -> dict:
    rows = []
    expected = found = missing = mismatched = 0
    for a in db.query(AssetAudit).filter(AssetAudit.is_deleted == False).order_by(AssetAudit.created_at.desc()).all():  # noqa: E712
        expected += a.total_expected or 0
        found += a.total_found or 0
        missing += a.total_missing or 0
        mismatched += a.total_mismatched or 0
        rows.append({
            "name": a.name, "status": a.status.value, "scheduled_date": a.scheduled_date,
            "expected": a.total_expected, "found": a.total_found, "missing": a.total_missing,
            "mismatched": a.total_mismatched,
        })
    accuracy = round(found / expected * 100, 1) if expected else 0.0
    return {
        "key": "audit_reconciliation", "title": "Audit Reconciliation",
        "subtitle": f"{len(rows)} audits · {accuracy:g}% accuracy",
        "eyebrow": REPORT_META["audit_reconciliation"]["eyebrow"],
        "columns": [
            {"key": "name", "label": "Audit", "align": "left"}, {"key": "status", "label": "Status", "align": "left"},
            {"key": "scheduled_date", "label": "Scheduled", "align": "left"},
            {"key": "expected", "label": "Expected", "fmt": "int", "align": "right"},
            {"key": "found", "label": "Found", "fmt": "int", "align": "right"},
            {"key": "missing", "label": "Missing", "fmt": "int", "align": "right"},
            {"key": "mismatched", "label": "Mismatch", "fmt": "int", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "total": len(rows), "expected": expected, "found": found,
            "missing": missing, "mismatched": mismatched, "accuracy": accuracy,
        },
        "period": _period(filters),
    }


def _disposal_register(db: Session, filters: dict) -> dict:
    codes = _asset_codes(db)
    rows, total_sale, total_book, by_method = [], 0.0, 0.0, {}
    for d in db.query(AssetDisposal).filter(AssetDisposal.is_deleted == False).order_by(AssetDisposal.created_at.desc()).all():  # noqa: E712
        total_sale += float(d.sale_value or 0)
        total_book += float(d.book_value or 0)
        by_method[d.disposal_method.value] = by_method.get(d.disposal_method.value, 0) + 1
        rows.append({
            "asset_code": codes.get(d.asset_id), "method": d.disposal_method.value, "status": d.status.value,
            "request_date": d.request_date, "disposed_date": d.disposed_date,
            "sale_value": float(d.sale_value) if d.sale_value is not None else None,
        })
    recovery_pct = round(total_sale / total_book * 100, 1) if total_book else 0.0
    return {
        "key": "disposal_register", "title": "Disposal Register",
        "subtitle": f"{len(rows)} disposals · ₹{total_sale:,.0f} recovered",
        "eyebrow": REPORT_META["disposal_register"]["eyebrow"],
        "columns": [
            {"key": "asset_code", "label": "Code", "align": "left"}, {"key": "method", "label": "Method", "align": "left"},
            {"key": "status", "label": "Status", "align": "left"}, {"key": "request_date", "label": "Requested", "align": "left"},
            {"key": "disposed_date", "label": "Disposed", "align": "left"},
            {"key": "sale_value", "label": "Sale value", "fmt": "money", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "total": len(rows), "total_sale": round(total_sale, 2),
            "total_book": round(total_book, 2), "recovery_pct": recovery_pct, "by_method": by_method,
        },
        "period": _period(filters),
    }


_SHAPERS = {
    "estate_overview": _estate_overview,
    "inventory_register": _inventory_register,
    "category_distribution": _category_distribution,
    "allocation_register": _allocation_register,
    "allocation_by_department": _allocation_by_department,
    "unacknowledged": _unacknowledged,
    "maintenance_log": _maintenance_log,
    "damage_log": _damage_log,
    "transfers_log": _transfers_log,
    "financial_valuation": _financial_valuation,
    "asset_aging": _asset_aging,
    "vendor_spend": _vendor_spend,
    "warranty_expiry": _warranty_expiry,
    "compliance": _compliance,
    "audit_reconciliation": _audit_reconciliation,
    "disposal_register": _disposal_register,
}


def build_report(db: Session, key: str, filters: Optional[dict] = None) -> dict:
    if key not in _SHAPERS:
        raise HTTPException(404, f"Unknown report '{key}'")
    return _SHAPERS[key](db, filters or {})


def build_overview(db: Session) -> dict:
    """A cross-report snapshot for the Reports hub landing page: estate totals
    plus per-report row-count + flat (non-nested) summary."""
    estate = build_report(db, "estate_overview", {})
    reports: Dict[str, dict] = {}
    for key in REPORT_KEYS:
        rep = build_report(db, key, {})
        flat = {k: v for k, v in rep.get("summary", {}).items() if not isinstance(v, dict)}
        reports[key] = {"count": len(rep.get("rows", [])), "summary": flat}
    return {
        "generated": date.today().isoformat(),
        "totals": estate["summary"],
        "reports": reports,
    }
