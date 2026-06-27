"""Shared helpers for the HR Performance Management module — score maths,
template snapshotting, and dict serialization (joined names aren't ORM columns)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.hr.designation import Designation
from app.models.hr.department import Department
from app.models.hr.performance_review import PerformanceReview


def emp_name(emp) -> str:
    if not emp:
        return ""
    v = getattr(emp, "full_name", None)
    if v:
        return v
    fn = getattr(emp, "first_name", "") or ""
    ln = getattr(emp, "last_name", "") or ""
    nm = f"{fn} {ln}".strip()
    if nm:
        return nm
    u = getattr(emp, "user", None)
    if u:
        return getattr(u, "full_name", None) or getattr(u, "name", None) or getattr(u, "email", "") or ""
    return getattr(emp, "employee_id", "") or ""


def user_name(u) -> str:
    if not u:
        return ""
    return getattr(u, "full_name", None) or getattr(u, "name", None) or getattr(u, "email", "") or ""


def weighted(sections, field) -> Optional[float]:
    """Weight-averaged rating across sections (ignoring un-rated ones)."""
    num = 0.0
    den = 0.0
    flat = []
    for s in (sections or []):
        r = s.get(field)
        if r is None:
            continue
        w = float(s.get("weight") or 0)
        flat.append(float(r))
        num += w * float(r)
        den += w
    if den > 0:
        return round(num / den, 2)
    if flat:
        return round(sum(flat) / len(flat), 2)
    return None


def snapshot_sections(template) -> list:
    """Freeze a template's weighted sections onto a new review."""
    out = []
    for sec in (template.sections or []):
        crit = sec.criteria_json if isinstance(sec.criteria_json, list) else []
        out.append({
            "key": str(sec.id),
            "title": sec.title,
            "section_type": sec.section_type,
            "weight": float(sec.weight or 0),
            "criteria": crit,
            "self_rating": None,
            "manager_rating": None,
            "self_comment": "",
            "manager_comment": "",
        })
    return out


def apply_scores(sections, incoming, role: str, rating_max: int) -> list:
    """Merge submitted ratings/comments into a fresh sections list (by key).
    role = 'self' | 'manager'. Returns a NEW list (so the JSONB col change is
    detected without flag_modified)."""
    rfield = "self_rating" if role == "self" else "manager_rating"
    cfield = "self_comment" if role == "self" else "manager_comment"
    by_key = {}
    for item in (incoming or []):
        by_key[item.key] = item
    fresh = []
    for s in (sections or []):
        s2 = dict(s)
        item = by_key.get(s2["key"])
        if item is not None:
            if item.rating is not None:
                s2[rfield] = max(0.0, min(float(rating_max), float(item.rating)))
            if item.comment is not None:
                s2[cfield] = item.comment
        fresh.append(s2)
    return fresh


def recompute(r: PerformanceReview) -> None:
    r.self_overall = weighted(r.sections_json, "self_rating")
    r.manager_overall = weighted(r.sections_json, "manager_rating")
    # The OFFICIAL score is the manager's assessment only. A self-rating must
    # never become the record of truth (it would let an un-reviewed employee
    # self-award a hike) — overall_score stays None until the manager scores.
    r.overall_score = r.manager_overall


def _f(v):
    return float(v) if v is not None else None


def resolve_merit_policy(db: Session, review):
    """The merit policy that governs a review: the one snapshotted at launch, else
    the org default active policy, else None (caller falls back to DEFAULT_BANDS)."""
    from app.models.hr.merit_policy import MeritPolicy
    pid = getattr(review, "merit_policy_id", None)
    if pid:
        p = db.query(MeritPolicy).filter(MeritPolicy.id == pid, MeritPolicy.is_deleted == False).first()  # noqa: E712
        if p:
            return p
    return (db.query(MeritPolicy)
            .filter(MeritPolicy.is_default == True, MeritPolicy.is_active == True, MeritPolicy.is_deleted == False)  # noqa: E712
            .first())


def merit_band_context(db: Session, review) -> dict:
    """Resolve the band a review's hike is bounded by.

    Calibration is AUTHORITATIVE when present: a CALIBRATED calibration row's
    score (calibrated_score, else performance_score) overrides the raw manager
    overall_score. Returns policy + the score used + its source + the band dict
    (hike_min_pct / hike_max_pct) + rating_max.
    """
    from app.models.hr.merit_policy import band_for_score
    from app.models.hr.performance_calibration import PerformanceCalibration, CalibrationStatus

    rating_max = review.rating_max or 5
    score = _f(review.overall_score)
    source = "manager"

    cal = (db.query(PerformanceCalibration)
           .filter(PerformanceCalibration.review_id == review.id,
                   PerformanceCalibration.status == CalibrationStatus.CALIBRATED.value,
                   PerformanceCalibration.is_deleted == False)  # noqa: E712
           .order_by(PerformanceCalibration.calibrated_at.desc().nullslast(),
                     PerformanceCalibration.updated_at.desc())
           .first())
    if cal is not None:
        cal_score = _f(cal.calibrated_score) if cal.calibrated_score is not None else _f(cal.performance_score)
        if cal_score is not None:
            score = cal_score
            rating_max = cal.rating_max or rating_max
            source = "calibration"

    policy = resolve_merit_policy(db, review)
    band = band_for_score(policy, score, rating_max)
    return {
        "policy": policy,
        "policy_id": str(policy.id) if policy is not None else None,
        "policy_name": policy.name if policy is not None else None,
        "score": score,
        "rating_max": rating_max,
        "source": source,
        "band": band,
    }


def serialize(db: Session, r: PerformanceReview, maps: dict | None = None) -> dict:
    if maps is None:
        maps = {"desig": {}, "dept": {}}
    emp = r.employee
    desig_id = getattr(emp, "designation_id", None)
    dept_id = getattr(emp, "department_id", None)

    desig_name = None
    if desig_id:
        if desig_id not in maps["desig"]:
            d = db.get(Designation, desig_id)
            maps["desig"][desig_id] = getattr(d, "name", None) if d else None
        desig_name = maps["desig"][desig_id]
    dept_name = None
    if dept_id:
        if dept_id not in maps["dept"]:
            d = db.get(Department, dept_id)
            maps["dept"][dept_id] = getattr(d, "name", None) if d else None
        dept_name = maps["dept"][dept_id]

    return {
        "id": str(r.id),
        "employee_id": str(r.employee_id),
        "employee_name": emp_name(emp),
        "employee_code": getattr(emp, "employee_id", None),
        "designation_id": str(desig_id) if desig_id else None,
        "designation_name": desig_name,
        "department_id": str(dept_id) if dept_id else None,
        "department_name": dept_name,
        "reviewer_id": str(r.reviewer_id) if r.reviewer_id else None,
        "reviewer_name": user_name(r.reviewer),
        "template_id": str(r.template_id) if r.template_id else None,
        "template_code": r.template_code,
        "template_name": r.template_name,
        "cycle": r.cycle,
        "period_label": r.period_label,
        "rating_max": r.rating_max,
        "rating_labels": r.rating_labels or [],
        "sections": r.sections_json or [],
        "status": r.status,
        "self_overall": _f(r.self_overall),
        "manager_overall": _f(r.manager_overall),
        "overall_score": _f(r.overall_score),
        "self_comments": r.self_comments,
        "manager_comments": r.manager_comments,
        "ack_comments": r.ack_comments,
        "employee_ack": r.employee_ack,
        "self_submitted_at": r.self_submitted_at,
        "manager_submitted_at": r.manager_submitted_at,
        "completed_at": r.completed_at,
        "acknowledged_at": r.acknowledged_at,
        "due_date": r.due_date,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
        # merit / hike outcome
        "merit_policy_id": str(r.merit_policy_id) if getattr(r, "merit_policy_id", None) else None,
        "hike_effective_from": getattr(r, "hike_effective_from", None),
        "final_rating_band": getattr(r, "final_rating_band", None),
        "recommended_hike_pct": _f(getattr(r, "recommended_hike_pct", None)),
        "recommendation_note": getattr(r, "recommendation_note", None),
        "recommended_at": getattr(r, "recommended_at", None),
        "approved_hike_pct": _f(getattr(r, "approved_hike_pct", None)),
        "approved_at": getattr(r, "approved_at", None),
        "hike_status": getattr(r, "hike_status", "NONE") or "NONE",
        "comp_revision_id": str(r.comp_revision_id) if getattr(r, "comp_revision_id", None) else None,
        "prev_annual_ctc": _f(getattr(r, "prev_annual_ctc", None)),
        "new_annual_ctc": _f(getattr(r, "new_annual_ctc", None)),
    }


def _emp_facets(db: Session, emp, maps: dict) -> dict:
    """Shared employee name/code/designation/department lookup with a small cache."""
    desig_id = getattr(emp, "designation_id", None)
    dept_id = getattr(emp, "department_id", None)
    desig_name = None
    if desig_id:
        if desig_id not in maps["desig"]:
            d = db.get(Designation, desig_id)
            maps["desig"][desig_id] = getattr(d, "name", None) if d else None
        desig_name = maps["desig"][desig_id]
    dept_name = None
    if dept_id:
        if dept_id not in maps["dept"]:
            d = db.get(Department, dept_id)
            maps["dept"][dept_id] = getattr(d, "name", None) if d else None
        dept_name = maps["dept"][dept_id]
    return {
        "employee_name": emp_name(emp),
        "employee_code": getattr(emp, "employee_id", None),
        "designation_id": str(desig_id) if desig_id else None,
        "designation_name": desig_name,
        "department_id": str(dept_id) if dept_id else None,
        "department_name": dept_name,
    }


# ─────────────────────────── Goals / OKRs ───────────────────────────

def goal_progress_from_value(start, current, target, metric_type=None) -> float:
    """Derive 0..100 progress for a measurable goal/KR."""
    mt = (metric_type or "").upper()
    if mt in ("MILESTONE", "BOOLEAN"):
        return 100.0 if (current and float(current) >= 1) else 0.0
    s = float(start) if start is not None else 0.0
    c = float(current) if current is not None else s
    t = float(target) if target is not None else None
    if t is None or t == s:
        # no measurable target — fall back to current treated as a % (clamped)
        return round(max(0.0, min(100.0, c)), 1)
    pct = (c - s) / (t - s) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def derive_goal_status(progress, due_date, current_status=None) -> str:
    """Heuristic ON_TRACK / AT_RISK / OFF_TRACK / ACHIEVED from progress + deadline."""
    if current_status in ("ACHIEVED", "MISSED", "CANCELLED"):
        return current_status
    p = float(progress or 0)
    if p >= 100:
        return "ACHIEVED"
    if current_status == "DRAFT":
        return "DRAFT"
    if p >= 70:
        return "ON_TRACK"
    if p >= 40:
        return "AT_RISK"
    return "OFF_TRACK"


def recompute_objective(objective, children) -> None:
    """Roll up an objective's progress as the weighted mean of its KRs."""
    kids = [c for c in (children or []) if not getattr(c, "is_deleted", False)]
    if not kids:
        return
    num = 0.0
    den = 0.0
    for c in kids:
        w = float(c.weight or 0) or 1.0
        num += w * float(c.progress or 0)
        den += w
    objective.progress = round(num / den, 1) if den else 0.0
    objective.status = derive_goal_status(objective.progress, objective.due_date, objective.status)


def serialize_goal(db: Session, g, maps: dict | None = None, with_children: bool = False) -> dict:
    if maps is None:
        maps = {"desig": {}, "dept": {}}
    out = {
        "id": str(g.id),
        "employee_id": str(g.employee_id),
        "parent_id": str(g.parent_id) if g.parent_id else None,
        "goal_type": g.goal_type,
        "title": g.title,
        "description": g.description,
        "category": g.category,
        "cycle": g.cycle,
        "period_label": g.period_label,
        "weight": _f(g.weight),
        "metric_type": g.metric_type,
        "start_value": _f(g.start_value),
        "target_value": _f(g.target_value),
        "current_value": _f(g.current_value),
        "unit": g.unit,
        "progress": _f(g.progress),
        "status": g.status,
        "start_date": g.start_date,
        "due_date": g.due_date,
        "review_id": str(g.review_id) if g.review_id else None,
        "check_ins": g.check_ins_json or [],
        "created_at": g.created_at,
        "updated_at": g.updated_at,
    }
    out.update(_emp_facets(db, g.employee, maps))
    if with_children:
        kids = sorted(
            [c for c in (g.children or []) if not getattr(c, "is_deleted", False)],
            key=lambda c: (c.created_at or g.created_at),
        )
        out["key_results"] = [serialize_goal(db, c, maps) for c in kids]
    return out


# ─────────────────────────── 360 Feedback ───────────────────────────

def feedback_rollup(req) -> dict:
    resps = [r for r in (req.responses or [])]
    invited = len(resps)
    submitted = [r for r in resps if r.status == "SUBMITTED"]
    declined = sum(1 for r in resps if r.status == "DECLINED")
    pending = sum(1 for r in resps if r.status == "PENDING")

    # average per competency across submitted responses
    by_key = {}
    for r in submitted:
        for item in (r.ratings_json or []):
            k = item.get("key")
            if k is None or item.get("rating") is None:
                continue
            slot = by_key.setdefault(k, {"key": k, "label": item.get("label") or k, "sum": 0.0, "count": 0})
            slot["sum"] += float(item["rating"])
            slot["count"] += 1
    by_competency = [
        {"key": v["key"], "label": v["label"], "avg": round(v["sum"] / v["count"], 2), "count": v["count"]}
        for v in by_key.values() if v["count"]
    ]
    overall_vals = [float(r.overall_rating) for r in submitted if r.overall_rating is not None]
    by_rel = {}
    for r in submitted:
        by_rel[r.relationship_type] = by_rel.get(r.relationship_type, 0) + 1

    return {
        "invited": invited,
        "submitted": len(submitted),
        "declined": declined,
        "pending": pending,
        "response_rate": round(len(submitted) / invited * 100, 1) if invited else 0,
        "overall_avg": round(sum(overall_vals) / len(overall_vals), 2) if overall_vals else None,
        "by_competency": sorted(by_competency, key=lambda x: x["avg"], reverse=True),
        "by_relationship": by_rel,
    }


def serialize_feedback_response(r, anonymize: bool) -> dict:
    name = "Anonymous" if anonymize else (r.reviewer_name or (user_name(r.reviewer_user) if r.reviewer_user else "—"))
    return {
        "id": str(r.id),
        "request_id": str(r.request_id),
        "reviewer_user_id": None if anonymize else (str(r.reviewer_user_id) if r.reviewer_user_id else None),
        "reviewer_employee_id": None if anonymize else (str(r.reviewer_employee_id) if r.reviewer_employee_id else None),
        "reviewer_name": name,
        "relationship_type": r.relationship_type,
        "status": r.status,
        "ratings": r.ratings_json or [],
        "overall_rating": _f(r.overall_rating),
        "strengths": r.strengths,
        "improvements": r.improvements,
        "comments": r.comments,
        "submitted_at": r.submitted_at,
        "created_at": r.created_at,
    }


def serialize_feedback_request(db: Session, req, maps: dict | None = None,
                               include_responses: bool = False, force_reveal: bool = False) -> dict:
    if maps is None:
        maps = {"desig": {}, "dept": {}}
    anon = bool(req.anonymous) and not force_reveal
    out = {
        "id": str(req.id),
        "employee_id": str(req.employee_id),
        "review_id": str(req.review_id) if req.review_id else None,
        "cycle": req.cycle,
        "period_label": req.period_label,
        "title": req.title,
        "prompt": req.prompt,
        "competencies": req.competencies_json or [],
        "rating_max": _f(req.rating_max),
        "anonymous": bool(req.anonymous),
        "status": req.status,
        "due_date": req.due_date,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "rollup": feedback_rollup(req),
    }
    out.update(_emp_facets(db, req.employee, maps))
    if include_responses:
        out["responses"] = [serialize_feedback_response(r, anon) for r in (req.responses or [])]
    return out


# ─────────────────────────── Calibration / 9-box ───────────────────────────

def serialize_calibration(db: Session, c, maps: dict | None = None) -> dict:
    if maps is None:
        maps = {"desig": {}, "dept": {}}
    out = {
        "id": str(c.id),
        "employee_id": str(c.employee_id),
        "review_id": str(c.review_id) if c.review_id else None,
        "cycle": c.cycle,
        "period_label": c.period_label,
        "performance_score": _f(c.performance_score),
        "calibrated_score": _f(c.calibrated_score),
        "rating_max": c.rating_max,
        "performance_band": c.performance_band,
        "potential_band": c.potential_band,
        "box": c.box,
        "note": c.note,
        "status": c.status,
        "calibrated_at": c.calibrated_at,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }
    out.update(_emp_facets(db, c.employee, maps))
    return out


# ─────────────────────────── PIP ───────────────────────────

def serialize_pip(db: Session, p, maps: dict | None = None) -> dict:
    if maps is None:
        maps = {"desig": {}, "dept": {}}
    out = {
        "id": str(p.id),
        "employee_id": str(p.employee_id),
        "review_id": str(p.review_id) if p.review_id else None,
        "manager_id": str(p.manager_id) if p.manager_id else None,
        "manager_name": user_name(p.manager) if p.manager else None,
        "title": p.title,
        "reason": p.reason,
        "expectations": p.expectations,
        "support": p.support,
        "status": p.status,
        "start_date": p.start_date,
        "end_date": p.end_date,
        "objectives": p.objectives_json or [],
        "check_ins": p.check_ins_json or [],
        "outcome": p.outcome,
        "employee_ack_at": getattr(p, "employee_ack_at", None),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }
    out.update(_emp_facets(db, p.employee, maps))
    return out


def latest_review_summary(db: Session, employee_id) -> Optional[dict]:
    """Latest COMPLETED/ACKNOWLEDGED review for an employee — for the promote
    evidence hint."""
    r = (
        db.query(PerformanceReview)
        .filter(
            PerformanceReview.employee_id == employee_id,
            PerformanceReview.is_deleted == False,  # noqa: E712
            PerformanceReview.status.in_(["COMPLETED", "ACKNOWLEDGED"]),
            PerformanceReview.overall_score.isnot(None),
        )
        .order_by(PerformanceReview.completed_at.desc().nullslast(), PerformanceReview.updated_at.desc())
        .first()
    )
    if not r:
        return None
    return {
        "id": str(r.id),
        "overall_score": _f(r.overall_score),
        "rating_max": r.rating_max,
        "cycle": r.cycle,
        "period_label": r.period_label,
        "template_name": r.template_name,
        "status": r.status,
        "completed_at": r.completed_at,
    }
