"""HR Exit Reports — data layer (the single source of truth).

Mirrors the travel_reports / asset_reports contract so the router + renderers
stay symmetrical:

    rows     = fetch_rows(db, key, date_from=, date_to=, department_id=)
    summary  = shape_summary(db, key, rows)        # carries a `tiles` KPI strip
    columns  = columns_for(key)                    # rich descriptors (align/fmt/status/bar/tone)
    meta     = report_meta(key)                    # name/group/icon/tagline/subtitle/accent/soft/deep/motif
    ov       = overview(db, ...)                    # console KPIs + per-report cover stat trios

Each report owns a UNIQUE cover `motif` (see pdf.py COVER_RENDERERS) so no two
exported PDFs look alike, and a warm accent triplet (accent/soft/deep) that is
the report's colour identity across cover, body, Excel and the frontend cards.
The whole exit module is warm amber/orange (emerald = positive/finance,
red = risk) — no purple, matching exit-theme.css.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_settlement import ExitSettlement
from app.models.hr.exit_interview import ExitInterview
from app.models.hr.exit_clearance import ExitClearanceItem
from app.models.hr.exit_type import (
    ExitCaseStatus, SettlementStatus, InterviewStatus, ClearanceItemStatus,
    ResignationType,
)
from app.utils.hr.exit_management import service as svc


# ─────────────────────────────────────────────────────────────────────────────
# Report registry — order defines deck/gallery order on the frontend.
# ─────────────────────────────────────────────────────────────────────────────
REPORT_META: Dict[str, Dict[str, Any]] = {
    "exit-register": {
        "name": "Exit Register", "group": "Registry", "icon": "DoorOpen",
        "tagline": "Every separation on record",
        "subtitle": "The master register of separation cases across the window — who, why, and when.",
        "accent": "#f59e0b", "soft": "#fef3c7", "deep": "#92400e", "motif": "gateway",
    },
    "exit-reasons": {
        "name": "Exit Reasons", "group": "Registry", "icon": "Compass",
        "tagline": "Why people choose to leave",
        "subtitle": "Reason categories ranked by frequency — the qualitative pulse of attrition.",
        "accent": "#fb923c", "soft": "#ffedd5", "deep": "#9a3412", "motif": "compass",
    },
    "tenure-analysis": {
        "name": "Tenure at Exit", "group": "Registry", "icon": "Layers",
        "tagline": "How long leavers stayed",
        "subtitle": "Service-length bands at the point of exit — early-attrition risk at a glance.",
        "accent": "#d97706", "soft": "#fef0d9", "deep": "#78350f", "motif": "strata",
    },
    "rehire-register": {
        "name": "Rehire Eligibility", "group": "Registry", "icon": "UserRoundCheck",
        "tagline": "Who we'd welcome back",
        "subtitle": "Rehire disposition for decided separations — the boomerang talent pool.",
        "accent": "#16a34a", "soft": "#dcfce7", "deep": "#14532d", "motif": "boomerang",
    },
    "attrition-analysis": {
        "name": "Attrition Trend", "group": "Analytics", "icon": "Activity",
        "tagline": "The turnover pulse, month by month",
        "subtitle": "Relieved versus still-in-process separations charted across the timeline.",
        "accent": "#ea580c", "soft": "#ffe3d1", "deep": "#7c2d12", "motif": "pulse",
    },
    "attrition-by-department": {
        "name": "Department Attrition", "group": "Analytics", "icon": "Building2",
        "tagline": "Where the workforce is thinning",
        "subtitle": "Exit volume and relieved counts ranked by department — hotspots first.",
        "accent": "#b45309", "soft": "#fde9cf", "deep": "#7c2d12", "motif": "atlas",
    },
    "separation-type": {
        "name": "Separation Type Mix", "group": "Analytics", "icon": "Split",
        "tagline": "Voluntary vs involuntary",
        "subtitle": "How separations are classified — resignation, retirement, termination and more.",
        "accent": "#a16207", "soft": "#fef9c3", "deep": "#713f12", "motif": "prism",
    },
    "interview-insights": {
        "name": "Exit Interview Insights", "group": "Analytics", "icon": "MessagesSquare",
        "tagline": "Sentiment, eNPS & dimension scores",
        "subtitle": "Completed exit-interview ratings, recommendation signal and headline eNPS.",
        "accent": "#ca8a04", "soft": "#fef9c3", "deep": "#854d0e", "motif": "voiceprint",
    },
    "notice-tracker": {
        "name": "Notice Period Tracker", "group": "Offboarding", "icon": "CalendarClock",
        "tagline": "Everyone serving notice + days left",
        "subtitle": "Live notice-period roster with last-working-date countdown and clearance progress.",
        "accent": "#f97316", "soft": "#ffedd5", "deep": "#7c2d12", "motif": "hourglass",
    },
    "clearance-status": {
        "name": "Clearance Status", "group": "Offboarding", "icon": "ClipboardCheck",
        "tagline": "No-dues progress & blockers",
        "subtitle": "Per-case clearance completion, cleared-of-total items and outstanding blocks.",
        "accent": "#d97706", "soft": "#fef3c7", "deep": "#92400e", "motif": "lattice",
    },
    "final-settlement-register": {
        "name": "Final Settlement Register", "group": "Finance", "icon": "Scale",
        "tagline": "F&F earnings, recoveries & net payable",
        "subtitle": "Full-and-final reconciliation — what the company owes and what it recovers.",
        "accent": "#0e9f6e", "soft": "#d1fae5", "deep": "#065f46", "motif": "mint",
    },
}
REPORT_KEYS = tuple(REPORT_META.keys())
_GROUP_ORDER = ["Registry", "Analytics", "Offboarding", "Finance"]


def report_meta(key: str) -> Dict[str, Any]:
    m = REPORT_META.get(key) or REPORT_META["exit-register"]
    return {"key": key if key in REPORT_META else "exit-register", **m}


def report_index() -> List[Dict[str, Any]]:
    out = []
    for key, m in REPORT_META.items():
        out.append({
            "key": key, "name": m["name"], "group": m["group"], "icon": m["icon"],
            "description": m["subtitle"], "tagline": m["tagline"],
            "accent": m["accent"], "soft": m["soft"], "deep": m["deep"], "motif": m["motif"],
        })
    out.sort(key=lambda r: (_GROUP_ORDER.index(r["group"]) if r["group"] in _GROUP_ORDER else 99))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Status colour map — shared by PDF pills, Excel pills and the frontend legend.
# (light = pill bg, deep = pill text, hex = border)
# ─────────────────────────────────────────────────────────────────────────────
STATUS_COLORS: Dict[str, Dict[str, str]] = {
    # ── Exit-case lifecycle ──
    "DRAFT": {"hex": "#9ca3af", "light": "#f1f5f9", "deep": "#374151"},
    "SUBMITTED": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "MANAGER_REVIEW": {"hex": "#f59e0b", "light": "#fef3c7", "deep": "#92400e"},
    "ACCEPTED": {"hex": "#fb923c", "light": "#ffedd5", "deep": "#9a3412"},
    "NOTICE_PERIOD": {"hex": "#ea580c", "light": "#ffe3d1", "deep": "#7c2d12"},
    "CLEARANCE": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "SETTLEMENT": {"hex": "#0e9f6e", "light": "#d1fae5", "deep": "#065f46"},
    "COMPLETED": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "WITHDRAWN": {"hex": "#6b7280", "light": "#f1f5f9", "deep": "#374151"},
    "REJECTED": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    "CANCELLED": {"hex": "#6b7280", "light": "#f1f5f9", "deep": "#374151"},
    # ── Settlement lifecycle ──
    "VERIFIED": {"hex": "#f97316", "light": "#ffedd5", "deep": "#7c2d12"},
    "APPROVED": {"hex": "#0e9f6e", "light": "#d1fae5", "deep": "#065f46"},
    "PAID": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "CLOSED": {"hex": "#15803d", "light": "#dcfce7", "deep": "#14532d"},
    "REVERSED": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    # ── Interview lifecycle ──
    "PENDING": {"hex": "#9ca3af", "light": "#f1f5f9", "deep": "#374151"},
    "SCHEDULED": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "IN_PROGRESS": {"hex": "#f97316", "light": "#ffedd5", "deep": "#7c2d12"},
    "SKIPPED": {"hex": "#6b7280", "light": "#f1f5f9", "deep": "#374151"},
    # ── Rehire disposition ──
    "ELIGIBLE": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "NOT_ELIGIBLE": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    "UNDECIDED": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    # ── Recommendation signal ──
    "YES": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "NO": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    "—": {"hex": "#9ca3af", "light": "#f1f5f9", "deep": "#374151"},
    # ── Separation classification ──
    "VOLUNTARY": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "INVOLUNTARY": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    "MUTUAL": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    # ── Tenure bands (used as pills in tenure report) ──
    "< 6 mo": {"hex": "#dc2626", "light": "#fee2e2", "deep": "#7f1d1d"},
    "6–12 mo": {"hex": "#ea580c", "light": "#ffe3d1", "deep": "#7c2d12"},
    "1–2 yr": {"hex": "#d97706", "light": "#fef3c7", "deep": "#92400e"},
    "2–5 yr": {"hex": "#16a34a", "light": "#dcfce7", "deep": "#14532d"},
    "5+ yr": {"hex": "#15803d", "light": "#dcfce7", "deep": "#14532d"},
}
_DEFAULT_COLOR = {"hex": "#9ca3af", "light": "#f1f5f9", "deep": "#374151"}


def status_color(value) -> Dict[str, str]:
    return STATUS_COLORS.get(str(value), _DEFAULT_COLOR)


# ─────────────────────────────────────────────────────────────────────────────
# Column descriptors — align/fmt/status/mono/bar + tone predicates.
# ─────────────────────────────────────────────────────────────────────────────
def _c(key, label, align="left", **kw):
    return {"key": key, "label": label, "align": align, **kw}


COLUMNS: Dict[str, List[Dict[str, Any]]] = {
    "exit-register": [
        _c("case_number", "Case", "left", mono=True),
        _c("employee", "Employee"),
        _c("department", "Department"),
        _c("type", "Type"),
        _c("reason", "Reason"),
        _c("tenure_months", "Tenure (mo)", "right", fmt="int"),
        _c("status", "Status", "center", status=True),
        _c("filed", "Filed", "center"),
        _c("lwd", "Last day", "center"),
    ],
    "exit-reasons": [
        _c("reason", "Reason"),
        _c("count", "Count", "right", fmt="int"),
        _c("share", "Share", "right", fmt="pct", bar=True),
    ],
    "tenure-analysis": [
        _c("band", "Tenure band", "left", status=True),
        _c("exits", "Leavers", "right", fmt="int", bar=True),
        _c("share", "Share", "right", fmt="pct"),
        _c("relieved", "Relieved", "right", fmt="int"),
    ],
    "rehire-register": [
        _c("case_number", "Case", "left", mono=True),
        _c("employee", "Employee"),
        _c("department", "Department"),
        _c("type", "Type"),
        _c("rehire", "Rehire", "center", status=True),
        _c("status", "Case status", "center", status=True),
        _c("lwd", "Last day", "center"),
    ],
    "attrition-analysis": [
        _c("month", "Month"),
        _c("relieved", "Relieved", "right", fmt="int", bar=True),
        _c("active_exits", "In process", "right", fmt="int"),
        _c("total", "Total", "right", fmt="int"),
    ],
    "attrition-by-department": [
        _c("department", "Department"),
        _c("exits", "Exits", "right", fmt="int", bar=True),
        _c("relieved", "Relieved", "right", fmt="int"),
        _c("share", "Share", "right", fmt="pct"),
    ],
    "separation-type": [
        _c("type", "Separation type"),
        _c("klass", "Class", "center", status=True),
        _c("count", "Count", "right", fmt="int", bar=True),
        _c("share", "Share", "right", fmt="pct"),
    ],
    "interview-insights": [
        _c("employee", "Employee"),
        _c("department", "Department"),
        _c("overall", "Overall", "right", fmt="num1", good_if=lambda v: _num(v) >= 4, warn_if=lambda v: 0 < _num(v) < 3),
        _c("management", "Mgmt", "right", fmt="num1"),
        _c("culture", "Culture", "right", fmt="num1"),
        _c("growth", "Growth", "right", fmt="num1"),
        _c("compensation", "Comp", "right", fmt="num1"),
        _c("recommend", "Recommend", "center", status=True),
        _c("reason", "Primary reason"),
    ],
    "notice-tracker": [
        _c("employee", "Employee"),
        _c("case_number", "Case", "left", mono=True),
        _c("department", "Department"),
        _c("lwd", "Last day", "center"),
        _c("days_remaining", "Days left", "right", fmt="int",
           danger_if=lambda v: isinstance(v, (int, float)) and v < 0,
           warn_if=lambda v: isinstance(v, (int, float)) and 0 <= v <= 7),
        _c("clearance", "Clearance", "right", fmt="pct", bar=True),
    ],
    "clearance-status": [
        _c("case_number", "Case", "left", mono=True),
        _c("employee", "Employee"),
        _c("department", "Department"),
        _c("status", "Stage", "center", status=True),
        _c("progress", "Progress", "right", fmt="pct", bar=True),
        _c("cleared", "Cleared", "center"),
        _c("blocked", "Blocked", "right", fmt="int", danger_if=lambda v: _num(v) > 0),
    ],
    "final-settlement-register": [
        _c("settlement_number", "F&F", "left", mono=True),
        _c("employee", "Employee"),
        _c("earnings", "Earnings", "right", fmt="inr"),
        _c("recoveries", "Recoveries", "right", fmt="inr"),
        _c("net", "Net payable", "right", fmt="inr",
           good_if=lambda v: _num(v) > 0, danger_if=lambda v: _num(v) < 0),
        _c("status", "Status", "center", status=True),
    ],
}


def columns_for(key: str) -> List[Dict[str, Any]]:
    return COLUMNS.get(key, [])


# JSON-safe descriptors for the API (strips the lambda tone predicates that
# the renderers use internally but FastAPI can't serialise).
_PUBLIC_COL_KEYS = ("key", "label", "align", "fmt", "status", "bar", "mono")


def columns_public(key: str) -> List[Dict[str, Any]]:
    out = []
    for c in COLUMNS.get(key, []):
        out.append({k: c[k] for k in _PUBLIC_COL_KEYS if k in c})
    return out


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Classification helpers
# ─────────────────────────────────────────────────────────────────────────────
_VOLUNTARY_TYPES = {
    ResignationType.VOLUNTARY, ResignationType.RETIREMENT,
    ResignationType.CONTRACT_COMPLETION, ResignationType.TRANSFER,
}
_INVOLUNTARY_TYPES = {ResignationType.TERMINATION, ResignationType.PROBATION_EXIT}
# MUTUAL_SEPARATION → "MUTUAL"

_CLOSED_NEG = (ExitCaseStatus.REJECTED, ExitCaseStatus.WITHDRAWN, ExitCaseStatus.CANCELLED)


def _classify_type(t: Optional[ResignationType]) -> str:
    if t in _VOLUNTARY_TYPES:
        return "VOLUNTARY"
    if t in _INVOLUNTARY_TYPES:
        return "INVOLUNTARY"
    return "MUTUAL"


def _pretty_enum(v) -> str:
    return str(v).replace("_", " ").title() if v is not None else "—"


def _emp_name(case: ExitCase) -> str:
    lbl = svc.employee_label(case.employee)
    return lbl["employee_name"] or lbl["employee_code"] or "—"


def _tenure_months(case: ExitCase) -> Optional[int]:
    start = case.joining_date_snapshot or (case.employee.joining_date if case.employee else None)
    if not start:
        return None
    end = case.exit_date or case.last_working_date or date.today()
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


_TENURE_BANDS = ["< 6 mo", "6–12 mo", "1–2 yr", "2–5 yr", "5+ yr"]


def _tenure_band(months: Optional[int]) -> Optional[str]:
    if months is None:
        return None
    if months < 6:
        return "< 6 mo"
    if months < 12:
        return "6–12 mo"
    if months < 24:
        return "1–2 yr"
    if months < 60:
        return "2–5 yr"
    return "5+ yr"


# ─────────────────────────────────────────────────────────────────────────────
# Base query
# ─────────────────────────────────────────────────────────────────────────────
def _base_q(db: Session, date_from, date_to, department_id):
    q = (db.query(ExitCase)
         .options(joinedload(ExitCase.employee).joinedload(Employee.user),
                  joinedload(ExitCase.department))
         .filter(ExitCase.is_deleted == False))  # noqa: E712
    if department_id:
        q = q.filter(ExitCase.department_id == department_id)
    if date_from:
        q = q.filter(ExitCase.resignation_date >= date_from)
    if date_to:
        q = q.filter(ExitCase.resignation_date <= date_to)
    return q


# ─────────────────────────────────────────────────────────────────────────────
# Row fetchers
# ─────────────────────────────────────────────────────────────────────────────
def fetch_rows(db: Session, key: str, *, date_from=None, date_to=None, department_id=None) -> List[Dict[str, Any]]:
    cases = _base_q(db, date_from, date_to, department_id).all()

    if key == "exit-register":
        out = []
        for c in sorted(cases, key=lambda x: (x.resignation_date or date.min), reverse=True):
            out.append({
                "case_number": c.case_number, "employee": _emp_name(c),
                "department": c.department.name if c.department else "—",
                "type": _pretty_enum(c.resignation_type.value if c.resignation_type else None),
                "reason": _pretty_enum(c.reason_category.value) if c.reason_category else "—",
                "tenure_months": _tenure_months(c),
                "status": c.status.value,
                "filed": c.resignation_date.isoformat() if c.resignation_date else "—",
                "lwd": c.last_working_date.isoformat() if c.last_working_date else "—",
            })
        return out

    if key == "exit-reasons":
        counts: Dict[str, int] = defaultdict(int)
        for c in cases:
            if c.reason_category:
                counts[c.reason_category.value] += 1
        total = sum(counts.values()) or 1
        return [{"reason": _pretty_enum(k), "count": v, "share": round(v * 100 / total, 1)}
                for k, v in sorted(counts.items(), key=lambda x: -x[1])]

    if key == "tenure-analysis":
        buckets: Dict[str, Dict[str, int]] = {b: {"band": b, "exits": 0, "relieved": 0} for b in _TENURE_BANDS}
        graded = 0
        for c in cases:
            band = _tenure_band(_tenure_months(c))
            if not band:
                continue
            graded += 1
            buckets[band]["exits"] += 1
            if c.status == ExitCaseStatus.COMPLETED:
                buckets[band]["relieved"] += 1
        total = graded or 1
        rows = []
        for b in _TENURE_BANDS:
            d = buckets[b]
            if d["exits"] == 0:
                continue
            rows.append({**d, "share": round(d["exits"] * 100 / total, 1)})
        return rows

    if key == "rehire-register":
        out = []
        for c in cases:
            # Only cases where a disposition is meaningful (accepted onward).
            if c.status in (ExitCaseStatus.DRAFT, ExitCaseStatus.SUBMITTED, ExitCaseStatus.MANAGER_REVIEW):
                continue
            if c.eligible_for_rehire is True:
                rehire = "ELIGIBLE"
            elif c.eligible_for_rehire is False:
                rehire = "NOT_ELIGIBLE"
            else:
                rehire = "UNDECIDED"
            out.append({
                "case_number": c.case_number, "employee": _emp_name(c),
                "department": c.department.name if c.department else "—",
                "type": _pretty_enum(c.resignation_type.value if c.resignation_type else None),
                "rehire": rehire, "status": c.status.value,
                "lwd": c.last_working_date.isoformat() if c.last_working_date else "—",
            })
        order = {"ELIGIBLE": 0, "UNDECIDED": 1, "NOT_ELIGIBLE": 2}
        return sorted(out, key=lambda r: order.get(r["rehire"], 3))

    if key == "attrition-analysis":
        buckets: Dict[str, Dict[str, int]] = {}
        for c in cases:
            d = c.exit_date or c.resignation_date
            if not d:
                continue
            mk = d.strftime("%Y-%m")
            b = buckets.setdefault(mk, {"relieved": 0, "active_exits": 0})
            if c.status == ExitCaseStatus.COMPLETED:
                b["relieved"] += 1
            elif c.status not in _CLOSED_NEG:
                b["active_exits"] += 1
        return [{"month": k, "relieved": v["relieved"], "active_exits": v["active_exits"],
                 "total": v["relieved"] + v["active_exits"]}
                for k, v in sorted(buckets.items())]

    if key == "attrition-by-department":
        buckets: Dict[str, Dict[str, Any]] = {}
        for c in cases:
            dn = c.department.name if c.department else "Unassigned"
            b = buckets.setdefault(dn, {"department": dn, "exits": 0, "relieved": 0})
            b["exits"] += 1
            if c.status == ExitCaseStatus.COMPLETED:
                b["relieved"] += 1
        total = sum(b["exits"] for b in buckets.values()) or 1
        rows = sorted(buckets.values(), key=lambda x: -x["exits"])
        for r in rows:
            r["share"] = round(r["exits"] * 100 / total, 1)
        return rows

    if key == "separation-type":
        counts: Dict[str, int] = defaultdict(int)
        klass: Dict[str, str] = {}
        for c in cases:
            if not c.resignation_type:
                continue
            tv = c.resignation_type.value
            counts[tv] += 1
            klass[tv] = _classify_type(c.resignation_type)
        total = sum(counts.values()) or 1
        return [{"type": _pretty_enum(k), "klass": klass[k], "count": v,
                 "share": round(v * 100 / total, 1)}
                for k, v in sorted(counts.items(), key=lambda x: -x[1])]

    if key == "interview-insights":
        case_ids = [c.id for c in cases]
        if not case_ids:
            return []
        ivs = (db.query(ExitInterview)
               .filter(ExitInterview.exit_case_id.in_(case_ids),
                       ExitInterview.status == InterviewStatus.COMPLETED)
               .all())
        by_case = {c.id: c for c in cases}
        out = []
        for iv in ivs:
            c = by_case.get(iv.exit_case_id)
            if not c:
                continue
            r = iv.ratings or {}
            rec = "YES" if iv.would_recommend is True else ("NO" if iv.would_recommend is False else "—")
            reason = (_pretty_enum(iv.primary_reason_category.value) if iv.primary_reason_category
                      else (_pretty_enum(c.reason_category.value) if c.reason_category else "—"))
            out.append({
                "employee": _emp_name(c),
                "department": c.department.name if c.department else "—",
                "overall": round(_num(r.get("overall")), 1) or None,
                "management": round(_num(r.get("management")), 1) or None,
                "culture": round(_num(r.get("culture")), 1) or None,
                "growth": round(_num(r.get("growth")), 1) or None,
                "compensation": round(_num(r.get("compensation")), 1) or None,
                "recommend": rec, "reason": reason,
            })
        return sorted(out, key=lambda x: -(x["overall"] or 0))

    if key == "notice-tracker":
        today = date.today()
        out = []
        for c in cases:
            if c.status != ExitCaseStatus.NOTICE_PERIOD:
                continue
            dl = (c.last_working_date - today).days if c.last_working_date else None
            out.append({
                "employee": _emp_name(c), "case_number": c.case_number,
                "department": c.department.name if c.department else "—",
                "lwd": c.last_working_date.isoformat() if c.last_working_date else "—",
                "days_remaining": dl if dl is not None else "—",
                "clearance": c.clearance_progress_pct or 0,
            })
        return sorted(out, key=lambda x: (not isinstance(x["days_remaining"], int),
                                          x["days_remaining"] if isinstance(x["days_remaining"], int) else 9999))

    if key == "clearance-status":
        active = [c for c in cases if c.status in (
            ExitCaseStatus.NOTICE_PERIOD, ExitCaseStatus.CLEARANCE,
            ExitCaseStatus.SETTLEMENT, ExitCaseStatus.ACCEPTED)]
        ids = [c.id for c in active]
        items_by_case: Dict[Any, List[ExitClearanceItem]] = defaultdict(list)
        if ids:
            for it in db.query(ExitClearanceItem).filter(ExitClearanceItem.exit_case_id.in_(ids)).all():
                items_by_case[it.exit_case_id].append(it)
        out = []
        for c in active:
            items = items_by_case.get(c.id, [])
            applicable = [it for it in items if it.status != ClearanceItemStatus.NA]
            cleared = sum(1 for it in applicable if it.status == ClearanceItemStatus.CLEARED)
            blocked = sum(1 for it in items if it.status == ClearanceItemStatus.BLOCKED)
            out.append({
                "case_number": c.case_number, "employee": _emp_name(c),
                "department": c.department.name if c.department else "—",
                "status": c.status.value, "progress": c.clearance_progress_pct or 0,
                "cleared": f"{cleared}/{len(applicable)}" if applicable else "—",
                "blocked": blocked,
            })
        return sorted(out, key=lambda x: (-x["blocked"], -x["progress"]))

    if key == "final-settlement-register":
        q = (db.query(ExitSettlement).join(ExitCase, ExitCase.id == ExitSettlement.exit_case_id)
             .options(joinedload(ExitSettlement.employee).joinedload(Employee.user))
             .filter(ExitCase.is_deleted == False, ExitSettlement.is_deleted == False))  # noqa: E712
        if department_id:
            q = q.filter(ExitCase.department_id == department_id)
        if date_from:
            q = q.filter(ExitCase.resignation_date >= date_from)
        if date_to:
            q = q.filter(ExitCase.resignation_date <= date_to)
        out = []
        for s in q.all():
            lbl = svc.employee_label(s.employee)
            out.append({
                "settlement_number": s.settlement_number,
                "employee": lbl["employee_name"] or lbl["employee_code"] or "—",
                "earnings": float(s.total_earnings or 0), "recoveries": float(s.total_recoveries or 0),
                "net": float(s.net_amount or 0), "status": s.status.value,
            })
        return sorted(out, key=lambda x: -x["net"])

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Summaries (each carries a `tiles` KPI strip → cover hero + body cards + Excel)
# ─────────────────────────────────────────────────────────────────────────────
def shape_summary(db: Session, key: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def tiles(*t):
        return {"tiles": list(t)}

    if key == "exit-register":
        relieved = sum(1 for r in rows if r["status"] == "COMPLETED")
        inproc = sum(1 for r in rows if r["status"] not in ("COMPLETED", "REJECTED", "WITHDRAWN", "CANCELLED"))
        vol = sum(1 for r in rows if r["type"] in ("Voluntary", "Retirement", "Contract Completion", "Transfer"))
        volpct = round(vol * 100 / len(rows), 1) if rows else 0
        return tiles(("Cases", len(rows), "int"), ("Relieved", relieved, "int"),
                     ("In process", inproc, "int"), ("Voluntary", volpct, "pct"))

    if key == "exit-reasons":
        top = rows[0]["share"] if rows else 0
        return tiles(("Distinct reasons", len(rows), "int"),
                     ("Cases w/ reason", sum(r["count"] for r in rows), "int"),
                     ("Top reason", top, "pct"))

    if key == "tenure-analysis":
        total = sum(r["exits"] for r in rows)
        early = sum(r["exits"] for r in rows if r["band"] in ("< 6 mo", "6–12 mo"))
        earlypct = round(early * 100 / total, 1) if total else 0
        return tiles(("Leavers", total, "int"), ("< 1 yr exits", early, "int"),
                     ("Early-exit", earlypct, "pct"),
                     ("Bands", len(rows), "int"))

    if key == "rehire-register":
        elig = sum(1 for r in rows if r["rehire"] == "ELIGIBLE")
        no = sum(1 for r in rows if r["rehire"] == "NOT_ELIGIBLE")
        pct = round(elig * 100 / len(rows), 1) if rows else 0
        return tiles(("Decided", len(rows), "int"), ("Eligible", elig, "int"),
                     ("Not eligible", no, "int"), ("Eligible", pct, "pct"))

    if key == "attrition-analysis":
        return tiles(("Months", len(rows), "int"),
                     ("Relieved", sum(r["relieved"] for r in rows), "int"),
                     ("In process", sum(r["active_exits"] for r in rows), "int"),
                     ("Total exits", sum(r["total"] for r in rows), "int"))

    if key == "attrition-by-department":
        top = rows[0]["share"] if rows else 0
        return tiles(("Departments", len(rows), "int"),
                     ("Total exits", sum(r["exits"] for r in rows), "int"),
                     ("Relieved", sum(r["relieved"] for r in rows), "int"),
                     ("Top dept", top, "pct"))

    if key == "separation-type":
        vol = sum(r["count"] for r in rows if r["klass"] == "VOLUNTARY")
        invol = sum(r["count"] for r in rows if r["klass"] == "INVOLUNTARY")
        return tiles(("Types", len(rows), "int"),
                     ("Total", sum(r["count"] for r in rows), "int"),
                     ("Voluntary", vol, "int"), ("Involuntary", invol, "int"))

    if key == "interview-insights":
        n = len(rows)
        avg = round(sum(r["overall"] or 0 for r in rows) / n, 1) if n else 0
        recs = [r for r in rows if r["recommend"] in ("YES", "NO")]
        proms = sum(1 for r in recs if r["recommend"] == "YES")
        recpct = round(proms * 100 / len(recs), 1) if recs else 0
        enps = round((proms - (len(recs) - proms)) * 100 / len(recs)) if recs else 0
        return tiles(("Interviews", n, "int"), ("Avg overall", avg, "num1"),
                     ("Recommend", recpct, "pct"), ("eNPS", enps, "int"))

    if key == "notice-tracker":
        ints = [r["days_remaining"] for r in rows if isinstance(r["days_remaining"], int)]
        overdue = sum(1 for d in ints if d < 0)
        avg = round(sum(ints) / len(ints)) if ints else 0
        short = sum(1 for d in ints if 0 <= d <= 7)
        return tiles(("Serving notice", len(rows), "int"), ("Overdue", overdue, "int"),
                     ("Avg days left", avg, "int"), ("Within 7d", short, "int"))

    if key == "clearance-status":
        n = len(rows)
        avg = round(sum(r["progress"] for r in rows) / n) if n else 0
        blocked = sum(1 for r in rows if r["blocked"] > 0)
        ready = sum(1 for r in rows if r["progress"] >= 100)
        return tiles(("In clearance", n, "int"), ("Avg progress", avg, "pct"),
                     ("With blocks", blocked, "int"), ("Fully cleared", ready, "int"))

    if key == "final-settlement-register":
        return tiles(("Settlements", len(rows), "int"),
                     ("Earnings", round(sum(r["earnings"] for r in rows), 2), "inr"),
                     ("Recoveries", round(sum(r["recoveries"] for r in rows), 2), "inr"),
                     ("Net payable", round(sum(r["net"] for r in rows), 2), "inr"))

    return tiles(("Rows", len(rows), "int"))


# ─────────────────────────────────────────────────────────────────────────────
# Overview — console KPIs + per-report cover stat trios.
# Cover trios are the first 3 `tiles` from each report's own summary, so the
# numbers on a report's card always match the numbers inside the report.
# ─────────────────────────────────────────────────────────────────────────────
def overview(db: Session, *, date_from=None, date_to=None, department_id=None) -> Dict[str, Any]:
    cases = _base_q(db, date_from, date_to, department_id).all()
    total = len(cases)
    relieved = sum(1 for c in cases if c.status == ExitCaseStatus.COMPLETED)
    active = sum(1 for c in cases if c.status not in (ExitCaseStatus.COMPLETED, *_CLOSED_NEG))
    serving_notice = sum(1 for c in cases if c.status == ExitCaseStatus.NOTICE_PERIOD)
    in_clearance = sum(1 for c in cases if c.status == ExitCaseStatus.CLEARANCE)
    rehire_elig = sum(1 for c in cases if c.eligible_for_rehire is True)

    tenures = [m for m in (_tenure_months(c) for c in cases) if m is not None]
    avg_tenure = round(sum(tenures) / len(tenures)) if tenures else 0

    # Net F&F + completed interviews via cheap aggregate queries scoped to these cases.
    case_ids = [c.id for c in cases]
    net_fnf = 0.0
    interviews_done = 0
    if case_ids:
        for s in (db.query(ExitSettlement)
                  .filter(ExitSettlement.exit_case_id.in_(case_ids),
                          ExitSettlement.is_deleted == False).all()):  # noqa: E712
            net_fnf += float(s.net_amount or 0)
        interviews_done = (db.query(ExitInterview)
                           .filter(ExitInterview.exit_case_id.in_(case_ids),
                                   ExitInterview.status == InterviewStatus.COMPLETED).count())

    kpis = {
        "total_cases": total, "relieved": relieved, "active": active,
        "serving_notice": serving_notice, "in_clearance": in_clearance,
        "rehire_eligible": rehire_elig, "avg_tenure_months": avg_tenure,
        "net_fnf": round(net_fnf, 2), "interviews_done": interviews_done,
    }

    reports = []
    for key in REPORT_KEYS:
        m = REPORT_META[key]
        rows = fetch_rows(db, key, date_from=date_from, date_to=date_to, department_id=department_id)
        summary = shape_summary(db, key, rows)
        trio = (summary.get("tiles") or [])[:3]
        reports.append({
            "key": key, "name": m["name"], "group": m["group"], "icon": m["icon"],
            "tagline": m["tagline"], "subtitle": m["subtitle"],
            "accent": m["accent"], "soft": m["soft"], "deep": m["deep"], "motif": m["motif"],
            "count": len(rows),
            "stats": [{"label": t[0], "value": t[1], "kind": t[2]} for t in trio],
        })

    # Keep legacy top-level keys the old frontend read, plus the rich payload.
    return {
        "total_cases": total, "relieved": relieved, "active": active,
        "kpis": kpis, "generated_at": datetime.now().isoformat(),
        "groups": _GROUP_ORDER, "reports": reports,
    }
