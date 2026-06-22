"""Travel Reports — data layer.

Single source of truth for: report keys, per-report metadata (name, group,
accent triplet, cover motif), column descriptors, row fetch/shape, KPI summary,
and the landing-page ``overview`` aggregate that feeds the cinematic "Dispatch
Bureau" console on the frontend.

Each report draws from a different slice of the travel domain (requests,
bookings, DA, advances, settlements) so fetching is per-report rather than the
single-table fan-out the attendance package uses; the public contract still
mirrors attendance_reports so the router + renderers stay symmetrical:

    rows     = fetch_rows(db, key, date_from, date_to, department_id)
    summary  = shape_summary(key, rows)
    columns  = columns_for(key)
    meta     = report_meta(key)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from uuid import UUID

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_booking import TravelBooking
from app.models.hr.travel_advance import TravelAdvance
from app.models.hr.travel_da import TravelDaRecord
from app.models.hr.travel_settlement import TravelSettlement


# ─────────────────────────────────────────────────────────────────────────────
# Report registry — order defines deck order on the frontend.
# Each report owns a unique cover `motif` (see pdf.py COVER_RENDERERS) so no two
# exported PDFs look alike.  accent / soft / deep form the cover colour identity.
# ─────────────────────────────────────────────────────────────────────────────
REPORT_META: Dict[str, Dict[str, Any]] = {
    "travel-requests": {
        "name": "Travel Request Register", "group": "Travel", "icon": "TR",
        "tagline": "Every tour, route, status and estimate",
        "subtitle": "The master register of travel requests across the window.",
        "accent": "#f59e0b", "soft": "#fef3c7", "deep": "#92400e", "motif": "manifest",
    },
    "booking-register": {
        "name": "Booking Register", "group": "Travel", "icon": "BK",
        "tagline": "Flights, hotels, rail & cabs booked",
        "subtitle": "Centralised bookings with vendor, mode and confirmed spend.",
        "accent": "#fb923c", "soft": "#ffedd5", "deep": "#9a3412", "motif": "ticket",
    },
    "employee-history": {
        "name": "Employee Travel History", "group": "Travel", "icon": "EH",
        "tagline": "Per-traveller tour ledger",
        "subtitle": "Tours, days and spend rolled up by employee.",
        "accent": "#b45309", "soft": "#fde9cf", "deep": "#7c2d12", "motif": "passport",
    },
    "department-travel": {
        "name": "Department Travel", "group": "Travel", "icon": "DT",
        "tagline": "Travel volume & spend by department",
        "subtitle": "Where the organisation moves — and what it costs each unit.",
        "accent": "#d97706", "soft": "#fef0d9", "deep": "#78350f", "motif": "atlas",
    },
    "route-analysis": {
        "name": "Route & Destination Analysis", "group": "Travel", "icon": "RA",
        "tagline": "Busiest corridors by trips & spend",
        "subtitle": "Origin→destination corridors ranked across the window.",
        "accent": "#ea580c", "soft": "#ffe3d1", "deep": "#7c2d12", "motif": "airway",
    },
    "travel-cost": {
        "name": "Cost — Estimate vs Actual", "group": "Finance", "icon": "TC",
        "tagline": "Budgeted against settled spend",
        "subtitle": "Estimate, actual and variance per tour — over/under at a glance.",
        "accent": "#a16207", "soft": "#fef9c3", "deep": "#713f12", "motif": "ledger",
    },
    "da-report": {
        "name": "Daily Allowance Ledger", "group": "Finance", "icon": "DA",
        "tagline": "Per-diem computed & approved",
        "subtitle": "City-tier per-diem computation and approval ledger.",
        "accent": "#ca8a04", "soft": "#fef9c3", "deep": "#854d0e", "motif": "perdiem",
    },
    "advance-report": {
        "name": "Travel Advance Ledger", "group": "Finance", "icon": "AD",
        "tagline": "Requested · released · recovered",
        "subtitle": "Cash advances across their full disbursement lifecycle.",
        "accent": "#16a34a", "soft": "#dcfce7", "deep": "#14532d", "motif": "vault",
    },
    "advance-outstanding": {
        "name": "Outstanding Advances — Aging", "group": "Finance", "icon": "AO",
        "tagline": "Released but unsettled — recovery risk",
        "subtitle": "Live advances aged into buckets; older = higher recovery risk.",
        "accent": "#dc2626", "soft": "#fee2e2", "deep": "#7f1d1d", "motif": "aging",
    },
    "settlement-report": {
        "name": "Settlement Closure", "group": "Finance", "icon": "ST",
        "tagline": "Payable vs recoverable closure",
        "subtitle": "Expense reconciliation — who the company owes, who owes back.",
        "accent": "#0e9f6e", "soft": "#d1fae5", "deep": "#065f46", "motif": "clearing",
    },
    "frequent-travelers": {
        "name": "Frequent Travellers", "group": "Management", "icon": "FT",
        "tagline": "Top movers by tours & spend",
        "subtitle": "The organisation's most-travelled people, ranked.",
        "accent": "#f97316", "soft": "#ffedd5", "deep": "#7c2d12", "motif": "leaderboard",
    },
    "approval-tat": {
        "name": "Approval Turnaround", "group": "Management", "icon": "AT",
        "tagline": "Decision speed & pending queue age",
        "subtitle": "How fast travel clears approval — and what's still waiting.",
        "accent": "#92400e", "soft": "#fde9cf", "deep": "#78350f", "motif": "tower",
    },
}
REPORT_KEYS = tuple(REPORT_META.keys())
_GROUP_ORDER = ["Travel", "Finance", "Management"]


def report_meta(key: str) -> Dict[str, Any]:
    m = REPORT_META.get(key) or REPORT_META["travel-requests"]
    return {"key": key if key in REPORT_META else "travel-requests", **m}


def report_index() -> List[Dict[str, Any]]:
    """Lightweight list used by the frontend deck (key/name/group/desc/accent/motif/icon)."""
    out = []
    for key, m in REPORT_META.items():
        out.append({
            "key": key, "name": m["name"], "group": m["group"],
            "description": m["subtitle"], "tagline": m["tagline"],
            "accent": m["accent"], "soft": m["soft"], "deep": m["deep"],
            "motif": m["motif"], "icon": m["icon"],
        })
    out.sort(key=lambda r: (_GROUP_ORDER.index(r["group"]) if r["group"] in _GROUP_ORDER else 99))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Status colour map — shared by PDF pills, Excel pills and the legend.
# ─────────────────────────────────────────────────────────────────────────────
STATUS_COLORS: Dict[str, Dict[str, str]] = {
    # Request lifecycle
    "DRAFT": {"hex": "#6b7280", "light": "#f1f5f9", "deep": "#374151"},
    "PENDING_APPROVAL": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "APPROVED": {"hex": "#0e9f6e", "light": "#d1fae5", "deep": "#065f46"},
    "RETURNED": {"hex": "#ea580c", "light": "#ffedd5", "deep": "#9a3412"},
    "REJECTED": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    "IN_PROGRESS": {"hex": "#f97316", "light": "#ffedd5", "deep": "#7c2d12"},
    "COMPLETED": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "CANCELLED": {"hex": "#6b7280", "light": "#f1f5f9", "deep": "#374151"},
    # Advance lifecycle
    "REQUESTED": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "RELEASED": {"hex": "#f97316", "light": "#ffedd5", "deep": "#7c2d12"},
    "SETTLED": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "RECOVERED": {"hex": "#0e9f6e", "light": "#d1fae5", "deep": "#065f46"},
    # DA lifecycle
    "COMPUTED": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "PAID": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "REVERSED": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    # Settlement lifecycle
    "SUBMITTED": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "VERIFIED": {"hex": "#f97316", "light": "#ffedd5", "deep": "#7c2d12"},
    # Booking lifecycle
    "BOOKED": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "CONFIRMED": {"hex": "#0e9f6e", "light": "#d1fae5", "deep": "#065f46"},
    "PENDING": {"hex": "#6b7280", "light": "#f1f5f9", "deep": "#374151"},
    # Aging buckets (advance-outstanding `bucket` column)
    "0-15 days": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "16-30 days": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "31-60 days": {"hex": "#ea580c", "light": "#ffedd5", "deep": "#9a3412"},
    "60+ days": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
}


def status_color(value: str) -> Dict[str, str]:
    return STATUS_COLORS.get(str(value), {"hex": "#6b7280", "light": "#f1f5f9", "deep": "#374151"})


# ─────────────────────────────────────────────────────────────────────────────
# Column descriptors — single source of truth for PDF / Excel / CSV.
#   fmt: None | 'inr' | 'int' | 'date' | 'pct' | 'days'
#   status=True renders a coloured pill; *_if predicates flag a cell.
# ─────────────────────────────────────────────────────────────────────────────
_COLUMNS: Dict[str, List[Dict[str, Any]]] = {
    "travel-requests": [
        {"label": "Reference", "key": "ref", "align": "left", "mono": True},
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Type", "key": "type", "align": "left"},
        {"label": "From", "key": "from_loc", "align": "left"},
        {"label": "To", "key": "to_loc", "align": "left"},
        {"label": "Departure", "key": "departure", "align": "center"},
        {"label": "Days", "key": "days", "align": "center", "fmt": "int"},
        {"label": "Priority", "key": "priority", "align": "center"},
        {"label": "Status", "key": "status", "align": "center", "status": True},
        {"label": "Est. Cost", "key": "est_cost", "align": "right", "fmt": "inr"},
    ],
    "booking-register": [
        {"label": "Booking #", "key": "booking_no", "align": "left", "mono": True},
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Mode", "key": "mode", "align": "left"},
        {"label": "Vendor", "key": "vendor", "align": "left"},
        {"label": "Detail", "key": "detail", "align": "left"},
        {"label": "Travel date", "key": "travel_date", "align": "center"},
        {"label": "Status", "key": "status", "align": "center", "status": True},
        {"label": "Total", "key": "total", "align": "right", "fmt": "inr"},
    ],
    "employee-history": [
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Tours", "key": "tours", "align": "center", "fmt": "int"},
        {"label": "Total Days", "key": "days", "align": "center", "fmt": "int"},
        {"label": "Avg Days/Tour", "key": "avg_days", "align": "center", "fmt": "days"},
        {"label": "Total Est.", "key": "est_cost", "align": "right", "fmt": "inr"},
        {"label": "Last trip", "key": "last_trip", "align": "center"},
    ],
    "department-travel": [
        {"label": "Department", "key": "department", "align": "left"},
        {"label": "Tours", "key": "tours", "align": "center", "fmt": "int"},
        {"label": "Travellers", "key": "travellers", "align": "center", "fmt": "int"},
        {"label": "Total Est.", "key": "est_cost", "align": "right", "fmt": "inr"},
        {"label": "Avg / Tour", "key": "avg_cost", "align": "right", "fmt": "inr"},
        {"label": "Share %", "key": "share_pct", "align": "right", "fmt": "pct", "bar": True},
    ],
    "route-analysis": [
        {"label": "Route", "key": "route", "align": "left"},
        {"label": "Trips", "key": "trips", "align": "center", "fmt": "int"},
        {"label": "Travellers", "key": "travellers", "align": "center", "fmt": "int"},
        {"label": "Total Est.", "key": "est_cost", "align": "right", "fmt": "inr"},
        {"label": "Avg / Trip", "key": "avg_cost", "align": "right", "fmt": "inr"},
    ],
    "travel-cost": [
        {"label": "Reference", "key": "ref", "align": "left", "mono": True},
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Departure", "key": "departure", "align": "center"},
        {"label": "Estimated", "key": "estimated", "align": "right", "fmt": "inr"},
        {"label": "Actual", "key": "actual", "align": "right", "fmt": "inr"},
        {"label": "Variance", "key": "variance", "align": "right", "fmt": "inr",
         "danger_if": lambda v: (v or 0) > 0, "good_if": lambda v: (v or 0) < 0},
        {"label": "Var %", "key": "variance_pct", "align": "right", "fmt": "pct",
         "danger_if": lambda v: (v or 0) > 0},
        {"label": "Status", "key": "status", "align": "center", "status": True},
    ],
    "da-report": [
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "City Tier", "key": "city_tier", "align": "left"},
        {"label": "Days", "key": "days", "align": "center", "fmt": "int"},
        {"label": "Daily Rate", "key": "rate", "align": "right", "fmt": "inr"},
        {"label": "Eligible DA", "key": "eligible", "align": "right", "fmt": "inr"},
        {"label": "Approved DA", "key": "approved", "align": "right", "fmt": "inr"},
        {"label": "Status", "key": "status", "align": "center", "status": True},
    ],
    "advance-report": [
        {"label": "Advance #", "key": "advance_no", "align": "left", "mono": True},
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Requested", "key": "requested", "align": "right", "fmt": "inr"},
        {"label": "Approved", "key": "approved", "align": "right", "fmt": "inr"},
        {"label": "Recovered", "key": "recovered", "align": "right", "fmt": "inr"},
        {"label": "Status", "key": "status", "align": "center", "status": True},
    ],
    "advance-outstanding": [
        {"label": "Advance #", "key": "advance_no", "align": "left", "mono": True},
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Released", "key": "released", "align": "right", "fmt": "inr"},
        {"label": "Released on", "key": "released_on", "align": "center"},
        {"label": "Age (days)", "key": "age_days", "align": "center", "fmt": "int",
         "danger_if": lambda v: (v or 0) > 30, "warn_if": lambda v: 15 < (v or 0) <= 30},
        {"label": "Bucket", "key": "bucket", "align": "center", "status": True},
    ],
    "settlement-report": [
        {"label": "Settlement #", "key": "settlement_no", "align": "left", "mono": True},
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Advance", "key": "advance", "align": "right", "fmt": "inr"},
        {"label": "Expense", "key": "expense", "align": "right", "fmt": "inr"},
        {"label": "DA", "key": "da", "align": "right", "fmt": "inr"},
        {"label": "Payable", "key": "payable", "align": "right", "fmt": "inr",
         "good_if": lambda v: (v or 0) > 0},
        {"label": "Recoverable", "key": "recoverable", "align": "right", "fmt": "inr",
         "danger_if": lambda v: (v or 0) > 0},
        {"label": "Status", "key": "status", "align": "center", "status": True},
    ],
    "frequent-travelers": [
        {"label": "Rank", "key": "rank", "align": "center", "fmt": "int", "mono": True},
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Tours", "key": "tours", "align": "center", "fmt": "int"},
        {"label": "Total Days", "key": "days", "align": "center", "fmt": "int"},
        {"label": "Total Est.", "key": "est_cost", "align": "right", "fmt": "inr"},
        {"label": "Avg / Tour", "key": "avg_cost", "align": "right", "fmt": "inr"},
    ],
    "approval-tat": [
        {"label": "Reference", "key": "ref", "align": "left", "mono": True},
        {"label": "Employee", "key": "employee", "align": "left"},
        {"label": "Submitted", "key": "submitted", "align": "center"},
        {"label": "Decided", "key": "decided", "align": "center"},
        {"label": "TAT (hrs)", "key": "tat_hours", "align": "center", "fmt": "days",
         "warn_if": lambda v: v is not None and v > 48},
        {"label": "Stage", "key": "stage", "align": "center"},
        {"label": "Status", "key": "status", "align": "center", "status": True},
    ],
}


def columns_for(key: str) -> List[Dict[str, Any]]:
    return _COLUMNS.get(key, [])


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _dstr(d) -> str:
    if not d:
        return "—"
    try:
        return d.strftime("%d %b %Y")
    except Exception:
        return str(d)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _emp_names(db: Session, employee_ids) -> Dict[UUID, str]:
    ids = [i for i in set(employee_ids) if i is not None]
    if not ids:
        return {}
    rows = (db.query(Employee.id, User.full_name)
            .join(User, User.id == Employee.user_id)
            .filter(Employee.id.in_(ids)).all())
    return {r[0]: (r[1] or "—") for r in rows}


def _emp_dept_for_reqs(db: Session, request_ids) -> Dict[UUID, Dict[str, Any]]:
    """{request_id: {employee_id, department_id}} — for joining child records."""
    ids = [i for i in set(request_ids) if i is not None]
    if not ids:
        return {}
    rows = (db.query(TravelRequest.id, TravelRequest.employee_id, TravelRequest.department_id)
            .filter(TravelRequest.id.in_(ids)).all())
    return {r[0]: {"employee_id": r[1], "department_id": r[2]} for r in rows}


def _enum_val(x) -> str:
    return getattr(x, "value", x) if x is not None else "—"


def _bucket(days: int) -> str:
    if days <= 15:
        return "0-15 days"
    if days <= 30:
        return "16-30 days"
    if days <= 60:
        return "31-60 days"
    return "60+ days"


# ─────────────────────────────────────────────────────────────────────────────
# fetch + shape
# ─────────────────────────────────────────────────────────────────────────────
def _req_base(db: Session, df, dt, dept):
    q = db.query(TravelRequest).filter(TravelRequest.is_deleted == False)  # noqa: E712
    if df:
        q = q.filter(TravelRequest.departure_date >= df)
    if dt:
        q = q.filter(TravelRequest.departure_date <= dt)
    if dept:
        q = q.filter(TravelRequest.department_id == dept)
    return q


def fetch_rows(db: Session, key: str, df: Optional[date], dt: Optional[date],
               dept: Optional[UUID]) -> List[Dict[str, Any]]:
    """Return shaped row dicts (keys match columns_for(key))."""

    if key == "travel-requests":
        recs = _req_base(db, df, dt, dept).order_by(TravelRequest.departure_date.desc()).all()
        names = _emp_names(db, {r.employee_id for r in recs})
        return [{
            "ref": r.travel_reference_number, "employee": names.get(r.employee_id, "—"),
            "type": r.travel_type or "—", "from_loc": r.from_location or "—",
            "to_loc": r.to_location or "—", "departure": _dstr(r.departure_date),
            "days": int(r.num_days or 0), "priority": _enum_val(r.priority).title() if r.priority else "—",
            "status": _enum_val(r.status), "est_cost": _f(r.est_total_cost),
        } for r in recs]

    if key == "booking-register":
        q = db.query(TravelBooking).filter(TravelBooking.is_deleted == False)  # noqa: E712
        if df:
            q = q.filter(TravelBooking.travel_date >= df)
        if dt:
            q = q.filter(TravelBooking.travel_date <= dt)
        bks = q.order_by(TravelBooking.travel_date.desc()).all()
        rmap = _emp_dept_for_reqs(db, {b.travel_request_id for b in bks})
        if dept:
            bks = [b for b in bks if rmap.get(b.travel_request_id, {}).get("department_id") == dept]
        names = _emp_names(db, {rmap.get(b.travel_request_id, {}).get("employee_id") for b in bks})
        out = []
        for b in bks:
            eid = rmap.get(b.travel_request_id, {}).get("employee_id")
            if b.booking_type and _enum_val(b.booking_type) == "HOTEL":
                detail = b.hotel_name or "Hotel stay"
            else:
                detail = f"{b.from_place or '—'} → {b.to_place or '—'}"
            out.append({
                "booking_no": b.booking_number, "employee": names.get(eid, "—"),
                "mode": _enum_val(b.booking_type).title(), "vendor": b.vendor or "—",
                "detail": detail, "travel_date": _dstr(b.travel_date),
                "status": _enum_val(b.status), "total": _f(b.total_cost),
            })
        return out

    if key == "employee-history":
        q = (db.query(TravelRequest.employee_id,
                      sa_func.count(TravelRequest.id),
                      sa_func.coalesce(sa_func.sum(TravelRequest.num_days), 0),
                      sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0),
                      sa_func.max(TravelRequest.departure_date))
             .filter(TravelRequest.is_deleted == False))  # noqa: E712
        if df:
            q = q.filter(TravelRequest.departure_date >= df)
        if dt:
            q = q.filter(TravelRequest.departure_date <= dt)
        if dept:
            q = q.filter(TravelRequest.department_id == dept)
        rows = q.group_by(TravelRequest.employee_id).all()
        names = _emp_names(db, {r[0] for r in rows})
        out = [{
            "employee": names.get(r[0], "—"), "tours": int(r[1] or 0),
            "days": int(r[2] or 0), "avg_days": round((r[2] or 0) / r[1], 1) if r[1] else 0,
            "est_cost": _f(r[3]), "last_trip": _dstr(r[4]),
        } for r in rows]
        out.sort(key=lambda x: x["est_cost"], reverse=True)
        return out

    if key == "department-travel":
        q = (db.query(Department.name,
                      sa_func.count(TravelRequest.id),
                      sa_func.count(sa_func.distinct(TravelRequest.employee_id)),
                      sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0))
             .join(TravelRequest, TravelRequest.department_id == Department.id)
             .filter(TravelRequest.is_deleted == False))  # noqa: E712
        if df:
            q = q.filter(TravelRequest.departure_date >= df)
        if dt:
            q = q.filter(TravelRequest.departure_date <= dt)
        if dept:
            q = q.filter(TravelRequest.department_id == dept)
        rows = q.group_by(Department.name).order_by(sa_func.count(TravelRequest.id).desc()).all()
        total_spend = sum(_f(r[3]) for r in rows) or 1
        return [{
            "department": r[0] or "—", "tours": int(r[1] or 0), "travellers": int(r[2] or 0),
            "est_cost": _f(r[3]), "avg_cost": round(_f(r[3]) / r[1], 0) if r[1] else 0,
            "share_pct": round(_f(r[3]) / total_spend * 100, 1),
        } for r in rows]

    if key == "route-analysis":
        recs = _req_base(db, df, dt, dept).all()
        agg: Dict[str, Dict[str, Any]] = {}
        for r in recs:
            route = f"{r.from_location or '—'} → {r.to_location or '—'}"
            a = agg.setdefault(route, {"trips": 0, "emps": set(), "est": 0.0})
            a["trips"] += 1
            a["emps"].add(r.employee_id)
            a["est"] += _f(r.est_total_cost)
        out = [{
            "route": route, "trips": a["trips"], "travellers": len(a["emps"]),
            "est_cost": round(a["est"], 0), "avg_cost": round(a["est"] / a["trips"], 0) if a["trips"] else 0,
        } for route, a in agg.items()]
        out.sort(key=lambda x: (x["trips"], x["est_cost"]), reverse=True)
        return out

    if key == "travel-cost":
        recs = _req_base(db, df, dt, dept).order_by(TravelRequest.departure_date.desc()).all()
        names = _emp_names(db, {r.employee_id for r in recs})
        # Settlement actuals keyed by request
        st_map = {s.travel_request_id: s for s in
                  db.query(TravelSettlement).filter(
                      TravelSettlement.is_deleted == False,  # noqa: E712
                      TravelSettlement.travel_request_id.in_([r.id for r in recs] or [None])).all()}
        out = []
        for r in recs:
            st = st_map.get(r.id)
            est = _f(r.est_total_cost)
            actual = _f(st.approved_expense or st.total_expense) if st else 0.0
            variance = round(actual - est, 0) if actual else 0.0
            var_pct = round((variance / est) * 100, 1) if est else 0.0
            out.append({
                "ref": r.travel_reference_number, "employee": names.get(r.employee_id, "—"),
                "departure": _dstr(r.departure_date), "estimated": est, "actual": actual,
                "variance": variance, "variance_pct": var_pct, "status": _enum_val(r.status),
            })
        return out

    if key == "da-report":
        q = db.query(TravelDaRecord).filter(TravelDaRecord.is_deleted == False)  # noqa: E712
        if df:
            q = q.filter(sa_func.date(TravelDaRecord.created_at) >= df)
        if dt:
            q = q.filter(sa_func.date(TravelDaRecord.created_at) <= dt)
        recs = q.all()
        if dept:
            rmap = _emp_dept_for_reqs(db, {r.travel_request_id for r in recs})
            recs = [r for r in recs if rmap.get(r.travel_request_id, {}).get("department_id") == dept]
        names = _emp_names(db, {r.employee_id for r in recs})
        return [{
            "employee": names.get(r.employee_id, "—"), "city_tier": _enum_val(r.city_category).replace("_", " ").title(),
            "days": int(r.travel_days or 0), "rate": _f(r.daily_rate),
            "eligible": _f(r.eligible_da), "approved": _f(r.approved_da),
            "status": _enum_val(r.status),
        } for r in recs]

    if key == "advance-report":
        q = db.query(TravelAdvance).filter(TravelAdvance.is_deleted == False)  # noqa: E712
        if df:
            q = q.filter(sa_func.date(TravelAdvance.created_at) >= df)
        if dt:
            q = q.filter(sa_func.date(TravelAdvance.created_at) <= dt)
        recs = q.all()
        if dept:
            rmap = _emp_dept_for_reqs(db, {r.travel_request_id for r in recs})
            recs = [r for r in recs if rmap.get(r.travel_request_id, {}).get("department_id") == dept]
        names = _emp_names(db, {r.employee_id for r in recs})
        return [{
            "advance_no": r.advance_number, "employee": names.get(r.employee_id, "—"),
            "requested": _f(r.advance_amount), "approved": _f(r.approved_amount),
            "recovered": _f(r.recovered_amount), "status": _enum_val(r.status),
        } for r in recs]

    if key == "advance-outstanding":
        recs = (db.query(TravelAdvance).filter(
            TravelAdvance.is_deleted == False,  # noqa: E712
            TravelAdvance.status == "RELEASED").all())
        if dept:
            rmap = _emp_dept_for_reqs(db, {r.travel_request_id for r in recs})
            recs = [r for r in recs if rmap.get(r.travel_request_id, {}).get("department_id") == dept]
        names = _emp_names(db, {r.employee_id for r in recs})
        now = _now_utc()
        out = []
        for r in recs:
            rel = _aware(r.released_at)
            age = (now - rel).days if rel else 0
            out.append({
                "advance_no": r.advance_number, "employee": names.get(r.employee_id, "—"),
                "released": _f(r.approved_amount or r.advance_amount),
                "released_on": _dstr(r.released_at), "age_days": age, "bucket": _bucket(age),
            })
        out.sort(key=lambda x: x["age_days"], reverse=True)
        return out

    if key == "settlement-report":
        q = db.query(TravelSettlement).filter(TravelSettlement.is_deleted == False)  # noqa: E712
        if df:
            q = q.filter(sa_func.date(TravelSettlement.created_at) >= df)
        if dt:
            q = q.filter(sa_func.date(TravelSettlement.created_at) <= dt)
        recs = q.all()
        if dept:
            rmap = _emp_dept_for_reqs(db, {r.travel_request_id for r in recs})
            recs = [r for r in recs if rmap.get(r.travel_request_id, {}).get("department_id") == dept]
        names = _emp_names(db, {r.employee_id for r in recs})
        return [{
            "settlement_no": r.settlement_number, "employee": names.get(r.employee_id, "—"),
            "advance": _f(r.advance_received), "expense": _f(r.approved_expense or r.total_expense),
            "da": _f(r.da_amount), "payable": _f(r.payable_amount),
            "recoverable": _f(r.recoverable_amount), "status": _enum_val(r.status),
        } for r in recs]

    if key == "frequent-travelers":
        q = (db.query(TravelRequest.employee_id,
                      sa_func.count(TravelRequest.id),
                      sa_func.coalesce(sa_func.sum(TravelRequest.num_days), 0),
                      sa_func.coalesce(sa_func.sum(TravelRequest.est_total_cost), 0))
             .filter(TravelRequest.is_deleted == False))  # noqa: E712
        if df:
            q = q.filter(TravelRequest.departure_date >= df)
        if dt:
            q = q.filter(TravelRequest.departure_date <= dt)
        if dept:
            q = q.filter(TravelRequest.department_id == dept)
        rows = (q.group_by(TravelRequest.employee_id)
                .order_by(sa_func.count(TravelRequest.id).desc()).limit(50).all())
        names = _emp_names(db, {r[0] for r in rows})
        return [{
            "rank": i + 1, "employee": names.get(r[0], "—"), "tours": int(r[1] or 0),
            "days": int(r[2] or 0), "est_cost": _f(r[3]),
            "avg_cost": round(_f(r[3]) / r[1], 0) if r[1] else 0,
        } for i, r in enumerate(rows)]

    if key == "approval-tat":
        recs = (_req_base(db, df, dt, dept)
                .filter(TravelRequest.submitted_at.isnot(None))
                .order_by(TravelRequest.submitted_at.desc()).all())
        names = _emp_names(db, {r.employee_id for r in recs})
        now = _now_utc()
        out = []
        for r in recs:
            sub = _aware(r.submitted_at)
            dec = _aware(r.approved_at) or _aware(r.rejected_at) or _aware(getattr(r, "cancelled_at", None))
            if dec and sub:
                tat = round((dec - sub).total_seconds() / 3600, 1)
                decided = _dstr(dec.date())
            elif sub and _enum_val(r.status) == "PENDING_APPROVAL":
                tat = round((now - sub).total_seconds() / 3600, 1)
                decided = "pending"
            else:
                tat = None
                decided = "—"
            steps = r.approval_steps or []
            stage = f"{min(int(r.current_step or 0) + 1, len(steps))}/{len(steps)}" if steps else "—"
            out.append({
                "ref": r.travel_reference_number, "employee": names.get(r.employee_id, "—"),
                "submitted": _dstr(sub.date()) if sub else "—", "decided": decided,
                "tat_hours": tat, "stage": stage, "status": _enum_val(r.status),
            })
        return out

    return []


# ─────────────────────────────────────────────────────────────────────────────
# summary (KPI tiles + body extras). `tiles` is generic: (label, value, kind)
# kind ∈ 'inr' | 'int' | 'pct' | 'days'. Renderers format generically.
# ─────────────────────────────────────────────────────────────────────────────
def _sum(rows, key):
    return sum(_f(r.get(key)) for r in rows)


def shape_summary(key: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    s: Dict[str, Any] = {"rows": n, "tiles": []}

    if key == "travel-requests":
        travellers = len({r["employee"] for r in rows})
        approved = sum(1 for r in rows if r["status"] in ("APPROVED", "IN_PROGRESS", "COMPLETED"))
        s["tiles"] = [("Requests", n, "int"), ("Travellers", travellers, "int"),
                      ("Approved", approved, "int"), ("Est. spend", _sum(rows, "est_cost"), "inr")]
    elif key == "booking-register":
        confirmed = sum(1 for r in rows if r["status"] in ("CONFIRMED", "BOOKED", "COMPLETED"))
        s["tiles"] = [("Bookings", n, "int"), ("Travellers", len({r["employee"] for r in rows}), "int"),
                      ("Confirmed", confirmed, "int"), ("Booked spend", _sum(rows, "total"), "inr")]
    elif key == "employee-history":
        s["tiles"] = [("Travellers", n, "int"), ("Tours", int(_sum(rows, "tours")), "int"),
                      ("Total days", int(_sum(rows, "days")), "int"), ("Est. spend", _sum(rows, "est_cost"), "inr")]
    elif key == "department-travel":
        s["tiles"] = [("Departments", n, "int"), ("Tours", int(_sum(rows, "tours")), "int"),
                      ("Travellers", int(_sum(rows, "travellers")), "int"), ("Est. spend", _sum(rows, "est_cost"), "inr")]
    elif key == "route-analysis":
        top = rows[0]["route"] if rows else "—"
        s["top_route"] = top
        s["tiles"] = [("Corridors", n, "int"), ("Trips", int(_sum(rows, "trips")), "int"),
                      ("Travellers", int(_sum(rows, "travellers")), "int"), ("Est. spend", _sum(rows, "est_cost"), "inr")]
    elif key == "travel-cost":
        over = sum(1 for r in rows if (r["variance"] or 0) > 0)
        s["tiles"] = [("Tours", n, "int"), ("Estimated", _sum(rows, "estimated"), "inr"),
                      ("Actual", _sum(rows, "actual"), "inr"), ("Over budget", over, "int")]
        s["total_variance"] = round(_sum(rows, "variance"), 0)
    elif key == "da-report":
        paid = sum(1 for r in rows if r["status"] in ("PAID", "APPROVED"))
        s["tiles"] = [("DA records", n, "int"), ("Total days", int(_sum(rows, "days")), "int"),
                      ("Eligible DA", _sum(rows, "eligible"), "inr"), ("Approved DA", _sum(rows, "approved"), "inr")]
        s["paid"] = paid
    elif key == "advance-report":
        s["tiles"] = [("Advances", n, "int"), ("Requested", _sum(rows, "requested"), "inr"),
                      ("Approved", _sum(rows, "approved"), "inr"), ("Recovered", _sum(rows, "recovered"), "inr")]
    elif key == "advance-outstanding":
        over30 = sum(1 for r in rows if (r["age_days"] or 0) > 30)
        oldest = max((r["age_days"] for r in rows), default=0)
        s["tiles"] = [("Outstanding", n, "int"), ("Exposure", _sum(rows, "released"), "inr"),
                      ("Over 30 days", over30, "int"), ("Oldest", oldest, "days")]
    elif key == "settlement-report":
        settled = sum(1 for r in rows if r["status"] in ("SETTLED", "PAID"))
        s["tiles"] = [("Settlements", n, "int"), ("Payable", _sum(rows, "payable"), "inr"),
                      ("Recoverable", _sum(rows, "recoverable"), "inr"), ("Closed", settled, "int")]
    elif key == "frequent-travelers":
        top = rows[0]["employee"] if rows else "—"
        s["top_traveller"] = top
        s["tiles"] = [("Travellers", n, "int"), ("Tours", int(_sum(rows, "tours")), "int"),
                      ("Total days", int(_sum(rows, "days")), "int"), ("Est. spend", _sum(rows, "est_cost"), "inr")]
    elif key == "approval-tat":
        decided = [r["tat_hours"] for r in rows if r["decided"] not in ("pending", "—") and r["tat_hours"] is not None]
        pending = sum(1 for r in rows if r["decided"] == "pending")
        avg = round(sum(decided) / len(decided), 1) if decided else 0
        oldest = max((r["tat_hours"] or 0 for r in rows if r["decided"] == "pending"), default=0)
        s["tiles"] = [("Submitted", n, "int"), ("Avg TAT (hrs)", avg, "days"),
                      ("Pending", pending, "int"), ("Oldest wait (hrs)", round(oldest, 1), "days")]

    if not s["tiles"]:
        s["tiles"] = [("Records", n, "int")]
    return s


# ─────────────────────────────────────────────────────────────────────────────
# overview — single aggregate for the "Dispatch Bureau" console (lenses + press
# throughput + status mix + per-report counts).
# ─────────────────────────────────────────────────────────────────────────────
def _month_buckets(df: date, dt: date) -> List[date]:
    cur = date(df.year, df.month, 1)
    end = date(dt.year, dt.month, 1)
    out = []
    while cur <= end and len(out) < 18:
        out.append(cur)
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return out


def overview(db: Session, df: Optional[date], dt: Optional[date], dept: Optional[UUID]) -> Dict[str, Any]:
    reqs = _req_base(db, df, dt, dept).all()
    req_ids = [r.id for r in reqs]
    req_id_set = set(req_ids)
    days = (dt - df).days + 1 if (df and dt) else None

    # KPIs
    tours = len(reqs)
    travellers = len({r.employee_id for r in reqs})
    est_spend = sum(_f(r.est_total_cost) for r in reqs)
    dept_ids = {r.department_id for r in reqs if r.department_id}

    da_q = db.query(TravelDaRecord).filter(TravelDaRecord.is_deleted == False)  # noqa: E712
    if df and dt:
        da_q = da_q.filter(sa_func.date(TravelDaRecord.created_at).between(df, dt))
    da_rows = da_q.all()
    if dept:
        da_rows = [d for d in da_rows if d.travel_request_id in req_id_set]
    da_paid = sum(_f(d.approved_da) for d in da_rows if _enum_val(d.status) in ("PAID", "APPROVED"))

    adv_out = db.query(TravelAdvance).filter(
        TravelAdvance.is_deleted == False, TravelAdvance.status == "RELEASED").all()  # noqa: E712
    if dept:
        adv_out = [a for a in adv_out if a.travel_request_id in req_id_set]
    advances_out = sum(_f(a.approved_amount or a.advance_amount) for a in adv_out)

    st_q = db.query(TravelSettlement).filter(TravelSettlement.is_deleted == False)  # noqa: E712
    if df and dt:
        st_q = st_q.filter(sa_func.date(TravelSettlement.created_at).between(df, dt))
    setts = st_q.all()
    if dept:
        setts = [s for s in setts if s.travel_request_id in req_id_set]
    actual_spend = sum(_f(s.approved_expense or s.total_expense) for s in setts)
    settlements_pending = sum(1 for s in setts if _enum_val(s.status) in ("SUBMITTED", "VERIFIED", "DRAFT"))

    bk_q = db.query(TravelBooking).filter(TravelBooking.is_deleted == False)  # noqa: E712
    if df and dt:
        bk_q = bk_q.filter(TravelBooking.travel_date.between(df, dt))
    bks = bk_q.all()
    if dept:
        bks = [b for b in bks if b.travel_request_id in req_id_set]

    adv_all_q = db.query(TravelAdvance.id).filter(TravelAdvance.is_deleted == False)  # noqa: E712
    if df and dt:
        adv_all_q = adv_all_q.filter(sa_func.date(TravelAdvance.created_at).between(df, dt))
    adv_all = adv_all_q.all()
    if dept:
        adv_ids = {a.travel_request_id for a in db.query(TravelAdvance).filter(
            TravelAdvance.id.in_([a[0] for a in adv_all] or [None])).all() if a.travel_request_id in req_id_set}
        adv_count = len(adv_ids)
    else:
        adv_count = len(adv_all)

    # Status mix (request lifecycle)
    mix = defaultdict(int)
    for r in reqs:
        mix[_enum_val(r.status)] += 1
    status_mix = [{"key": k, "n": v, "hex": status_color(k)["hex"]}
                  for k, v in sorted(mix.items(), key=lambda kv: -kv[1])]

    # Monthly throughput (tours + spend) — drives the press "feed".
    # All-time → span the actual data (min→max departure) or fall back to ~12mo.
    deps = [r.departure_date for r in reqs if r.departure_date]
    if df and dt:
        bf, bt = df, dt
    elif deps:
        bf, bt = min(deps), max(deps)
    else:
        bt = date.today()
        bf = date(bt.year - 1, bt.month, 1)
    monthly = []
    for b in _month_buckets(bf, bt):
        nxt = date(b.year + 1, 1, 1) if b.month == 12 else date(b.year, b.month + 1, 1)
        seg = [r for r in reqs if r.departure_date and b <= r.departure_date < nxt]
        monthly.append({"label": b.strftime("%b"), "tours": len(seg),
                        "spend": round(sum(_f(r.est_total_cost) for r in seg), 0)})

    # Top departments
    dmap = defaultdict(lambda: {"tours": 0, "spend": 0.0})
    dnames = {d.id: d.name for d in db.query(Department.id, Department.name).all()}
    for r in reqs:
        slot = dmap[r.department_id]
        slot["tours"] += 1
        slot["spend"] += _f(r.est_total_cost)
    top_departments = sorted(
        [{"name": dnames.get(k, "Unassigned"), "tours": v["tours"], "spend": round(v["spend"], 0)}
         for k, v in dmap.items()], key=lambda x: -x["spend"])[:6]

    # Per-report record counts (cheap derivations from already-loaded sets)
    counts = {
        "travel-requests": tours,
        "booking-register": len(bks),
        "employee-history": travellers,
        "department-travel": len(dept_ids),
        "route-analysis": len({f"{r.from_location}->{r.to_location}" for r in reqs}),
        "travel-cost": tours,
        "da-report": len(da_rows),
        "advance-report": adv_count,
        "advance-outstanding": len(adv_out),
        "settlement-report": len(setts),
        "frequent-travelers": travellers,
        "approval-tat": sum(1 for r in reqs if r.submitted_at is not None),
    }

    return {
        "from": df.isoformat() if df else None, "to": dt.isoformat() if dt else None, "days": days,
        "kpis": {
            "tours": tours, "travellers": travellers, "departments": len(dept_ids),
            "est_spend": round(est_spend, 0), "actual_spend": round(actual_spend, 0),
            "da_paid": round(da_paid, 0), "advances_out": round(advances_out, 0),
            "settlements_pending": settlements_pending, "bookings": len(bks),
        },
        "counts": counts,
        "status_mix": status_mix,
        "monthly": monthly,
        "top_departments": top_departments,
    }
