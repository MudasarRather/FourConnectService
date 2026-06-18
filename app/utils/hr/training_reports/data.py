"""HR Training & Development — report catalog, metadata and data shapers.

Eleven reports, one per major Training tab so the Reports hub mirrors the whole
module with no blind spots:

    engagement  → enrollments · completion · assessments · feedback
    capability  → skill_gap · certifications · trainers
    governance  → compliance · requests · budget
    executive   → department (cross-cutting scorecard)

Each shaper returns a uniform shape so the CSV / Excel / PDF renderers stay
generic:

    {key, title, subtitle, eyebrow, columns, rows, summary, period}

`summary` carries the KPIs + motif data each PDF cover instrument needs.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.training import TrainingProgram, TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.certification import EmployeeCertification, CertificationStatus
from app.models.hr.skill import Skill, EmployeeSkill
from app.models.hr.trainer import Trainer
from app.models.hr.training_feedback import TrainingFeedback
from app.models.hr.training_request import TrainingRequest
from app.models.hr.assessment import Assessment, AssessmentResult
from app.models.hr.training_budget import TrainingBudget
from app.models.hr.compliance_training import ComplianceTraining, FREQUENCY_MONTHS
from app.utils.hr.training.flow import add_months
from app.utils.hr.training.service import resolve_eligible_employee_ids


# ════════════════════════════ METADATA ════════════════════════════
# accent / accent_deep / accent_soft drive the PDF cover; motif picks the
# cover renderer; icon is the crest glyph; group clusters the frontend cards.
REPORT_META: Dict[str, dict] = {
    "enrollments": {
        "name": "Training Enrollments", "tagline": "Every assignment with status, due-date & completion",
        "eyebrow": "ENGAGEMENT · ASSIGNMENT LEDGER", "group": "engagement", "motif": "ledger", "icon": "E",
        "accent": "#fbbf24", "accent_deep": "#b45309", "accent_soft": "#fff7e6",
    },
    "completion": {
        "name": "Completion Summary", "tagline": "Per-program completion funnel & rate",
        "eyebrow": "ENGAGEMENT · COMPLETION FUNNEL", "group": "engagement", "motif": "funnel", "icon": "C",
        "accent": "#34d399", "accent_deep": "#047857", "accent_soft": "#ecfdf5",
    },
    "assessments": {
        "name": "Assessment Results", "tagline": "Attempts, pass-rate & average score per assessment",
        "eyebrow": "ENGAGEMENT · EXAMINATION DECK", "group": "engagement", "motif": "dial", "icon": "A",
        "accent": "#eab308", "accent_deep": "#a16207", "accent_soft": "#fefce8",
    },
    "feedback": {
        "name": "Feedback Summary", "tagline": "Learner ratings & sentiment by program",
        "eyebrow": "ENGAGEMENT · RESONANCE CHAMBER", "group": "engagement", "motif": "stars", "icon": "F",
        "accent": "#facc15", "accent_deep": "#a16207", "accent_soft": "#fefce8",
    },
    "skill_gap": {
        "name": "Skill Gap Report", "tagline": "Average competency gaps across the matrix",
        "eyebrow": "CAPABILITY · COMPETENCY RADAR", "group": "capability", "motif": "radar", "icon": "S",
        "accent": "#fb923c", "accent_deep": "#c2410c", "accent_soft": "#fff1e6",
    },
    "certifications": {
        "name": "Certification Register", "tagline": "Held credentials & expiry horizon",
        "eyebrow": "CAPABILITY · CREDENTIAL VAULT", "group": "capability", "motif": "timeline", "icon": "K",
        "accent": "#f59e0b", "accent_deep": "#92400e", "accent_soft": "#fef3c7",
    },
    "trainers": {
        "name": "Trainer Performance", "tagline": "Ratings, reach & sentiment by trainer",
        "eyebrow": "CAPABILITY · FACULTY DECK", "group": "capability", "motif": "ratingarc", "icon": "T",
        "accent": "#d97706", "accent_deep": "#92400e", "accent_soft": "#fef3c7",
    },
    "compliance": {
        "name": "Compliance Readiness", "tagline": "Mandatory-training coverage by program",
        "eyebrow": "GOVERNANCE · READINESS GAUGE", "group": "governance", "motif": "gauge", "icon": "G",
        "accent": "#ea580c", "accent_deep": "#9a3412", "accent_soft": "#ffedd5",
    },
    "requests": {
        "name": "Training Requests", "tagline": "Approval pipeline, decisions & fulfilment",
        "eyebrow": "GOVERNANCE · APPROVAL PIPELINE", "group": "governance", "motif": "pipeline", "icon": "R",
        "accent": "#f97316", "accent_deep": "#c2410c", "accent_soft": "#fff1e6",
    },
    "budget": {
        "name": "Budget Utilization", "tagline": "Allocated vs spent & committed by budget",
        "eyebrow": "GOVERNANCE · TREASURY VAULT", "group": "governance", "motif": "vault", "icon": "₹",
        "accent": "#f59e0b", "accent_deep": "#b45309", "accent_soft": "#fef3c7",
    },
    "department": {
        "name": "Department Scorecard", "tagline": "Learning health rolled up per department",
        "eyebrow": "EXECUTIVE · DEPARTMENT SCORECARD", "group": "executive", "motif": "grid", "icon": "D",
        "accent": "#fb923c", "accent_deep": "#9a3412", "accent_soft": "#fff1e6",
    },
}
REPORT_KEYS = list(REPORT_META.keys())

# Public catalog for the API / frontend.
REPORTS = [
    {"key": k, "name": m["name"], "tagline": m["tagline"], "accent": m["accent"],
     "accent_deep": m["accent_deep"], "motif": m["motif"], "group": m["group"]}
    for k, m in REPORT_META.items()
]

# ──────────────────────── SELF-SERVICE (employee-scoped) ────────────────────────
# A personal counterpart to the admin hub — every report is filtered to the
# caller's own data only. Each gets a distinct cover motif so an employee's
# Learning Record, Skill Passport, Credential Portfolio and Request Journey
# never look alike (mirrors the admin-side "no two PDFs alike" rule).
SELF_REPORT_META: Dict[str, dict] = {
    "my_record": {
        "name": "My Learning Record", "tagline": "Your complete training transcript & completion",
        "eyebrow": "PERSONAL · LEARNING TRANSCRIPT", "group": "personal", "motif": "transcript", "icon": "R",
        "accent": "#fbbf24", "accent_deep": "#b45309", "accent_soft": "#fff7e6",
    },
    "my_skills": {
        "name": "My Skill Passport", "tagline": "Your competencies, targets & growth gaps",
        "eyebrow": "PERSONAL · COMPETENCY PASSPORT", "group": "personal", "motif": "passport", "icon": "S",
        "accent": "#f59e0b", "accent_deep": "#92400e", "accent_soft": "#fef3c7",
    },
    "my_credentials": {
        "name": "My Credential Portfolio", "tagline": "Certifications you hold & their renewal horizon",
        "eyebrow": "PERSONAL · CREDENTIAL PORTFOLIO", "group": "personal", "motif": "portfolio", "icon": "C",
        "accent": "#fb923c", "accent_deep": "#c2410c", "accent_soft": "#fff1e6",
    },
    "my_requests": {
        "name": "My Training Requests", "tagline": "Your nominations & their approval journey",
        "eyebrow": "PERSONAL · REQUEST JOURNEY", "group": "personal", "motif": "journey", "icon": "Q",
        "accent": "#eab308", "accent_deep": "#a16207", "accent_soft": "#fefce8",
    },
}
SELF_REPORT_KEYS = list(SELF_REPORT_META.keys())
SELF_REPORTS = [
    {"key": k, "name": m["name"], "tagline": m["tagline"], "accent": m["accent"],
     "accent_deep": m["accent_deep"], "motif": m["motif"], "group": m["group"]}
    for k, m in SELF_REPORT_META.items()
]

_ALL_META = {**REPORT_META, **SELF_REPORT_META}


def report_meta(key: str) -> dict:
    return {"key": key, **_ALL_META.get(key, REPORT_META["enrollments"])}


# ──────────────────────────── helpers ────────────────────────────
def _pct(n, d) -> float:
    return round((n or 0) / d * 100, 1) if d else 0.0


def _period(f: dict) -> dict:
    return {"from": f.get("from"), "to": f.get("to"),
            "label": (f"{f['from'].isoformat()} → {f['to'].isoformat()}"
                      if f.get("from") and f.get("to") else "All time")}


_OPEN = (TrainingAssignmentStatus.NOT_STARTED, TrainingAssignmentStatus.IN_PROGRESS)

_ENROLL_STATUS_PILL = {
    "COMPLETED": "good", "IN_PROGRESS": "warn", "NOT_STARTED": "neutral",
    "FAILED": "danger", "WAIVED": "neutral",
}
_REQ_STATUS_PILL = {
    "FULFILLED": "good", "APPROVED": "good", "PENDING_APPROVAL": "warn",
    "RETURNED": "warn", "REJECTED": "danger", "CANCELLED": "neutral", "DRAFT": "neutral",
}
_CERT_STATUS_PILL = {
    "ACTIVE": "good", "EXPIRING_SOON": "warn", "EXPIRED": "danger",
    "REVOKED": "neutral", "PENDING_RENEWAL": "warn",
}


# ════════════════════════════ SHAPERS ════════════════════════════
def _enrollments(db: Session, f: dict) -> dict:
    today = date.today()
    q = (
        db.query(TrainingAssignment, TrainingProgram, User.full_name,
                 Employee.employee_id, Department.name)
        .join(TrainingProgram, TrainingProgram.id == TrainingAssignment.program_id)
        .join(Employee, Employee.id == TrainingAssignment.employee_id)
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
    )
    if f.get("department_id"):
        q = q.filter(Employee.department_id == f["department_id"])
    if f.get("from"):
        q = q.filter(TrainingAssignment.assigned_date >= f["from"])
    if f.get("to"):
        q = q.filter(TrainingAssignment.assigned_date <= f["to"])
    rows, s = [], {"completed": 0, "in_progress": 0, "not_started": 0, "failed": 0, "waived": 0, "overdue": 0}
    for a, p, name, code, dept in q.order_by(TrainingAssignment.assigned_date.desc()).limit(5000).all():
        st = a.status.value if a.status else "NOT_STARTED"
        overdue = a.status in _OPEN and a.due_date and a.due_date < today
        if st == "COMPLETED": s["completed"] += 1
        elif st == "IN_PROGRESS": s["in_progress"] += 1
        elif st == "FAILED": s["failed"] += 1
        elif st == "WAIVED": s["waived"] += 1
        else: s["not_started"] += 1
        if overdue: s["overdue"] += 1
        rows.append({
            "employee": name, "code": code, "department": dept, "program": p.name,
            "type": p.training_type.value if p.training_type else "",
            "status": st, "assigned": a.assigned_date, "due": a.due_date,
            "completion": a.completion_date, "score": a.score,
        })
    total = len(rows)
    summary = {**s, "total": total, "completion_rate": _pct(s["completed"], total)}
    return {
        "key": "enrollments", "title": "Training Enrollments",
        "subtitle": f"{total} enrollment(s) · {summary['completion_rate']}% completed",
        "eyebrow": REPORT_META["enrollments"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "employee", "label": "Employee"}, {"key": "code", "label": "Code"},
            {"key": "department", "label": "Department"}, {"key": "program", "label": "Program"},
            {"key": "type", "label": "Type"}, {"key": "status", "label": "Status", "align": "center", "pill": _ENROLL_STATUS_PILL},
            {"key": "assigned", "label": "Assigned", "align": "center"}, {"key": "due", "label": "Due", "align": "center"},
            {"key": "completion", "label": "Completed", "align": "center"}, {"key": "score", "label": "Score", "align": "right"},
        ],
        "rows": rows, "summary": summary,
    }


def _completion(db: Session, f: dict) -> dict:
    S = TrainingAssignmentStatus
    q = (
        db.query(TrainingProgram.name, func.count(TrainingAssignment.id),
                 func.sum(case((TrainingAssignment.status == S.COMPLETED, 1), else_=0)),
                 func.sum(case((TrainingAssignment.status == S.IN_PROGRESS, 1), else_=0)),
                 func.sum(case((TrainingAssignment.status == S.NOT_STARTED, 1), else_=0)))
        .join(TrainingAssignment, TrainingAssignment.program_id == TrainingProgram.id)
        .group_by(TrainingProgram.name)
        .order_by(func.count(TrainingAssignment.id).desc())
    )
    rows, tot_enrolled, tot_comp = [], 0, 0
    for name, total, comp, inprog, notst in q.all():
        total, comp = int(total or 0), int(comp or 0)
        tot_enrolled += total; tot_comp += comp
        rows.append({
            "program": name, "total": total, "completed": comp,
            "in_progress": int(inprog or 0), "not_started": int(notst or 0),
            "completion_rate": _pct(comp, total),
        })
    summary = {"programs": len(rows), "enrolled": tot_enrolled, "completed": tot_comp,
               "in_progress": sum(r["in_progress"] for r in rows),
               "not_started": sum(r["not_started"] for r in rows),
               "completion_rate": _pct(tot_comp, tot_enrolled)}
    return {
        "key": "completion", "title": "Completion Summary",
        "subtitle": f"{len(rows)} program(s) · {summary['completion_rate']}% completed overall",
        "eyebrow": REPORT_META["completion"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "program", "label": "Program"}, {"key": "total", "label": "Enrolled", "align": "right"},
            {"key": "completed", "label": "Completed", "align": "right"}, {"key": "in_progress", "label": "In progress", "align": "right"},
            {"key": "not_started", "label": "Not started", "align": "right"},
            {"key": "completion_rate", "label": "Completion", "align": "right", "fmt": "pct", "good_if": lambda v: v >= 80, "danger_if": lambda v: v < 40},
        ],
        "rows": rows, "summary": summary,
    }


def _skill_gap(db: Session, f: dict) -> dict:
    q = (
        db.query(Skill.name, Skill.category, func.avg(EmployeeSkill.required_level),
                 func.avg(EmployeeSkill.current_level), func.avg(EmployeeSkill.gap),
                 func.sum(case((EmployeeSkill.gap > 0, 1), else_=0)), func.count(EmployeeSkill.id))
        .join(EmployeeSkill, EmployeeSkill.skill_id == Skill.id)
        .join(Employee, Employee.id == EmployeeSkill.employee_id)
        .filter(Skill.is_deleted == False, Employee.is_deleted == False)  # noqa: E712
    )
    if f.get("department_id"):
        q = q.filter(Employee.department_id == f["department_id"])
    q = q.group_by(Skill.name, Skill.category).order_by(func.avg(EmployeeSkill.gap).desc().nullslast())
    rows, gaps, withgap_tot, crit = [], [], 0, 0
    for name, cat, areq, acur, agap, withgap, total in q.all():
        agapf = round(float(agap), 2) if agap is not None else 0.0
        gaps.append(agapf); withgap_tot += int(withgap or 0)
        if agapf >= 2: crit += 1
        rows.append({
            "skill": name, "category": cat.value if cat else "",
            "avg_required": round(float(areq), 2) if areq is not None else "",
            "avg_current": round(float(acur), 2) if acur is not None else "",
            "avg_gap": agapf, "with_gap": int(withgap or 0), "employees": int(total or 0),
        })
    summary = {"skills": len(rows), "avg_gap": round(sum(gaps) / len(gaps), 2) if gaps else 0,
               "with_gap": withgap_tot, "critical": crit,
               "covered": len([g for g in gaps if g <= 0.5])}
    return {
        "key": "skill_gap", "title": "Skill Gap Report",
        "subtitle": f"{len(rows)} skill(s) · avg gap {summary['avg_gap']} · {crit} critical",
        "eyebrow": REPORT_META["skill_gap"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "skill", "label": "Skill"}, {"key": "category", "label": "Category"},
            {"key": "avg_required", "label": "Req.", "align": "right"}, {"key": "avg_current", "label": "Current", "align": "right"},
            {"key": "avg_gap", "label": "Gap", "align": "right", "danger_if": lambda v: isinstance(v, (int, float)) and v >= 2, "good_if": lambda v: isinstance(v, (int, float)) and v <= 0.5},
            {"key": "with_gap", "label": "With gap", "align": "right"}, {"key": "employees", "label": "People", "align": "right"},
        ],
        "rows": rows, "summary": summary,
    }


def _certifications(db: Session, f: dict) -> dict:
    today = date.today()
    q = (
        db.query(EmployeeCertification, User.full_name, Employee.employee_id, Department.name)
        .join(Employee, Employee.id == EmployeeCertification.employee_id)
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .filter(EmployeeCertification.is_deleted == False)  # noqa: E712
    )
    if f.get("department_id"):
        q = q.filter(Employee.department_id == f["department_id"])
    rows, s = [], {"active": 0, "expiring": 0, "expired": 0}
    soonest = None
    for ec, name, code, dept in q.order_by(EmployeeCertification.expiry_date.asc().nullslast()).limit(5000).all():
        days = (ec.expiry_date - today).days if ec.expiry_date else None
        st = ec.status.value if ec.status else "ACTIVE"
        if st == "ACTIVE": s["active"] += 1
        elif st == "EXPIRING_SOON": s["expiring"] += 1
        elif st == "EXPIRED": s["expired"] += 1
        if days is not None and days >= 0 and (soonest is None or days < soonest):
            soonest = days
        rows.append({
            "employee": name, "code": code, "department": dept, "certification": ec.name,
            "authority": ec.issuing_authority, "issue": ec.issue_date, "expiry": ec.expiry_date,
            "status": st, "days_to_expiry": days,
        })
    summary = {**s, "total": len(rows), "soonest_days": soonest if soonest is not None else "—"}
    return {
        "key": "certifications", "title": "Certification Register",
        "subtitle": f"{len(rows)} credential(s) · {s['active']} active · {s['expiring']} expiring",
        "eyebrow": REPORT_META["certifications"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "employee", "label": "Employee"}, {"key": "code", "label": "Code"},
            {"key": "department", "label": "Department"}, {"key": "certification", "label": "Certification"},
            {"key": "authority", "label": "Authority"}, {"key": "issue", "label": "Issued", "align": "center"},
            {"key": "expiry", "label": "Expires", "align": "center"},
            {"key": "status", "label": "Status", "align": "center", "pill": _CERT_STATUS_PILL},
            {"key": "days_to_expiry", "label": "Days left", "align": "right", "danger_if": lambda v: isinstance(v, (int, float)) and v < 0, "good_if": lambda v: isinstance(v, (int, float)) and v > 90},
        ],
        "rows": rows, "summary": summary,
    }


def _compliance(db: Session, f: dict) -> dict:
    today = date.today()
    configs = db.query(ComplianceTraining).filter(ComplianceTraining.is_deleted == False).all()  # noqa: E712
    rows, tot_elig, tot_comp = [], 0, 0
    for cfg in configs:
        prog = db.query(TrainingProgram).filter(TrainingProgram.id == cfg.program_id).first()
        emp_ids = resolve_eligible_employee_ids(db, cfg.applies_to)
        eligible = len(emp_ids); months = FREQUENCY_MONTHS.get(cfg.frequency); compliant = 0
        for eid in emp_ids:
            last = db.query(TrainingAssignment.completion_date).filter(
                TrainingAssignment.employee_id == eid, TrainingAssignment.program_id == cfg.program_id,
                TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED,
            ).order_by(TrainingAssignment.completion_date.desc()).first()
            lc = last[0] if last else None
            if lc is None:
                continue
            if months is None or add_months(lc, months) >= today:
                compliant += 1
        tot_elig += eligible; tot_comp += compliant
        rows.append({
            "program": prog.name if prog else "—", "frequency": cfg.frequency.value,
            "eligible": eligible, "compliant": compliant, "overdue": eligible - compliant,
            "coverage": _pct(compliant, eligible), "auto_reassign": cfg.auto_reassign,
        })
    summary = {"programs": len(rows), "eligible": tot_elig, "compliant": tot_comp,
               "overdue": tot_elig - tot_comp, "coverage": _pct(tot_comp, tot_elig)}
    return {
        "key": "compliance", "title": "Compliance Readiness",
        "subtitle": f"{len(rows)} mandatory program(s) · {summary['coverage']}% covered",
        "eyebrow": REPORT_META["compliance"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "program", "label": "Program"}, {"key": "frequency", "label": "Frequency", "align": "center"},
            {"key": "eligible", "label": "Eligible", "align": "right"}, {"key": "compliant", "label": "Compliant", "align": "right"},
            {"key": "overdue", "label": "Overdue", "align": "right", "danger_if": lambda v: v > 0},
            {"key": "coverage", "label": "Coverage", "align": "right", "fmt": "pct", "good_if": lambda v: v >= 90, "danger_if": lambda v: v < 60},
            {"key": "auto_reassign", "label": "Auto", "align": "center", "fmt": "bool"},
        ],
        "rows": rows, "summary": summary,
    }


def _feedback(db: Session, f: dict) -> dict:
    q = (
        db.query(TrainingProgram.name, func.avg(TrainingFeedback.rating),
                 func.avg(TrainingFeedback.content_rating), func.avg(TrainingFeedback.trainer_rating),
                 func.avg(TrainingFeedback.relevance_rating), func.count(TrainingFeedback.id))
        .join(TrainingProgram, TrainingProgram.id == TrainingFeedback.program_id)
        .group_by(TrainingProgram.name)
        .order_by(func.avg(TrainingFeedback.rating).desc())
    )
    rows, wsum, wn = [], 0.0, 0
    for name, avg_r, avg_c, avg_t, avg_rel, cnt in q.all():
        cnt = int(cnt or 0)
        if avg_r is not None:
            wsum += float(avg_r) * cnt; wn += cnt
        rows.append({
            "program": name, "avg_rating": round(float(avg_r), 2) if avg_r is not None else "",
            "avg_content": round(float(avg_c), 2) if avg_c is not None else "",
            "avg_trainer": round(float(avg_t), 2) if avg_t is not None else "",
            "avg_relevance": round(float(avg_rel), 2) if avg_rel is not None else "",
            "responses": cnt,
        })
    summary = {"programs": len(rows), "responses": wn, "avg_rating": round(wsum / wn, 2) if wn else 0,
               "top": rows[0]["program"] if rows else "—"}
    return {
        "key": "feedback", "title": "Feedback Summary",
        "subtitle": f"{len(rows)} program(s) rated · {summary['avg_rating']}★ average",
        "eyebrow": REPORT_META["feedback"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "program", "label": "Program"},
            {"key": "avg_rating", "label": "Overall", "align": "right", "good_if": lambda v: isinstance(v, (int, float)) and v >= 4, "danger_if": lambda v: isinstance(v, (int, float)) and v < 2.5},
            {"key": "avg_content", "label": "Content", "align": "right"}, {"key": "avg_trainer", "label": "Trainer", "align": "right"},
            {"key": "avg_relevance", "label": "Relevance", "align": "right"}, {"key": "responses", "label": "Responses", "align": "right"},
        ],
        "rows": rows, "summary": summary,
    }


def _trainers(db: Session, f: dict) -> dict:
    fb = (
        db.query(TrainingFeedback.trainer_id, func.count(TrainingFeedback.id),
                 func.avg(TrainingFeedback.trainer_rating))
        .filter(TrainingFeedback.trainer_id.isnot(None))
        .group_by(TrainingFeedback.trainer_id).all()
    )
    fb_map = {tid: (int(c or 0), round(float(a), 2) if a is not None else None) for tid, c, a in fb}
    trainers = db.query(Trainer).filter(Trainer.is_deleted == False).order_by(  # noqa: E712
        Trainer.rating_avg.desc().nullslast()).all()
    rows, rated, wsum, wn, resp = [], 0, 0.0, 0, 0
    for t in trainers:
        rc = int(t.rating_count or 0); ra = round(float(t.rating_avg), 2) if t.rating_avg else None
        fc, fa = fb_map.get(t.id, (0, None))
        resp += fc
        if ra is not None and rc:
            rated += 1; wsum += ra * rc; wn += rc
        rows.append({
            "trainer": t.name, "type": t.trainer_type.value if t.trainer_type else "",
            "organization": t.organization or "", "specialization": t.specialization or "",
            "rating": ra if ra is not None else "", "ratings": rc,
            "feedback_avg": fa if fa is not None else "", "active": t.is_active,
        })
    summary = {"trainers": len(rows), "active": len([r for r in rows if r["active"]]),
               "rated": rated, "avg_rating": round(wsum / wn, 2) if wn else 0, "responses": resp}
    return {
        "key": "trainers", "title": "Trainer Performance",
        "subtitle": f"{len(rows)} trainer(s) · {summary['avg_rating']}★ average · {resp} ratings",
        "eyebrow": REPORT_META["trainers"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "trainer", "label": "Trainer"}, {"key": "type", "label": "Type", "align": "center"},
            {"key": "organization", "label": "Organization"}, {"key": "specialization", "label": "Specialization"},
            {"key": "rating", "label": "Rating", "align": "right", "good_if": lambda v: isinstance(v, (int, float)) and v >= 4},
            {"key": "ratings", "label": "Reviews", "align": "right"}, {"key": "feedback_avg", "label": "Session★", "align": "right"},
            {"key": "active", "label": "Active", "align": "center", "fmt": "bool"},
        ],
        "rows": rows, "summary": summary,
    }


def _requests(db: Session, f: dict) -> dict:
    q = (
        db.query(TrainingRequest, User.full_name, TrainingProgram.name)
        .join(Employee, Employee.id == TrainingRequest.employee_id)
        .join(User, User.id == Employee.user_id)
        .outerjoin(TrainingProgram, TrainingProgram.id == TrainingRequest.program_id)
        .filter(TrainingRequest.is_deleted == False)  # noqa: E712
    )
    if f.get("department_id"):
        q = q.filter(Employee.department_id == f["department_id"])
    if f.get("from"):
        q = q.filter(TrainingRequest.created_at >= f["from"])
    if f.get("to"):
        q = q.filter(TrainingRequest.created_at <= f["to"])
    rows, by = [], {}
    for r, emp, pname in q.order_by(TrainingRequest.created_at.desc()).limit(5000).all():
        st = r.status.value if r.status else "DRAFT"
        by[st] = by.get(st, 0) + 1
        rows.append({
            "number": r.request_number, "employee": emp,
            "subject": pname or r.title or r.external_provider or "—",
            "status": st, "cost": float(r.estimated_cost) if r.estimated_cost else "",
            "preferred": r.preferred_start_date,
            "submitted": r.submitted_at.date() if r.submitted_at else "",
        })
    total = len(rows)
    decided = by.get("APPROVED", 0) + by.get("FULFILLED", 0) + by.get("REJECTED", 0)
    summary = {"total": total, "pending": by.get("PENDING_APPROVAL", 0), "approved": by.get("APPROVED", 0),
               "fulfilled": by.get("FULFILLED", 0), "rejected": by.get("REJECTED", 0),
               "returned": by.get("RETURNED", 0), "draft": by.get("DRAFT", 0),
               "fulfil_rate": _pct(by.get("FULFILLED", 0), decided)}
    return {
        "key": "requests", "title": "Training Requests",
        "subtitle": f"{total} request(s) · {summary['pending']} awaiting decision · {summary['fulfil_rate']}% fulfilled",
        "eyebrow": REPORT_META["requests"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "number", "label": "Number"}, {"key": "employee", "label": "Employee"},
            {"key": "subject", "label": "Program / Subject"},
            {"key": "status", "label": "Status", "align": "center", "pill": _REQ_STATUS_PILL},
            {"key": "cost", "label": "Est. cost", "align": "right", "fmt": "money"},
            {"key": "preferred", "label": "Preferred", "align": "center"}, {"key": "submitted", "label": "Submitted", "align": "center"},
        ],
        "rows": rows, "summary": summary,
    }


def _assessments(db: Session, f: dict) -> dict:
    res = (
        db.query(AssessmentResult.assessment_id, func.count(AssessmentResult.id),
                 func.sum(case((AssessmentResult.passed == True, 1), else_=0)),  # noqa: E712
                 func.avg(AssessmentResult.score))
        .group_by(AssessmentResult.assessment_id).all()
    )
    rmap = {aid: (int(c or 0), int(p or 0), float(av) if av is not None else None) for aid, c, p, av in res}
    assessments = (
        db.query(Assessment, TrainingProgram.name)
        .outerjoin(TrainingProgram, TrainingProgram.id == Assessment.program_id)
        .filter(Assessment.is_deleted == False)  # noqa: E712
        .order_by(Assessment.created_at.desc()).all()
    )
    rows, t_att, t_pass = [], 0, 0
    score_pcts = []
    for a, pname in assessments:
        att, passed, avg = rmap.get(a.id, (0, 0, None))
        t_att += att; t_pass += passed
        mx = float(a.max_score) if a.max_score else None
        avg_pct = round(avg / mx * 100, 1) if (avg is not None and mx) else None
        if avg_pct is not None and att:
            score_pcts.append(avg_pct)
        rows.append({
            "assessment": a.title, "program": pname or "—",
            "type": a.assessment_type.value if a.assessment_type else "",
            "pass_score": float(a.pass_score) if a.pass_score else "",
            "max_score": float(a.max_score) if a.max_score else "",
            "attempts": att, "passed": passed, "pass_rate": _pct(passed, att),
            "avg_score": round(avg, 1) if avg is not None else "",
            "active": a.is_active,
        })
    summary = {"assessments": len(rows), "attempts": t_att, "passed": t_pass,
               "pass_rate": _pct(t_pass, t_att),
               "avg_score": round(sum(score_pcts) / len(score_pcts), 1) if score_pcts else 0}
    return {
        "key": "assessments", "title": "Assessment Results",
        "subtitle": f"{len(rows)} assessment(s) · {t_att} attempts · {summary['pass_rate']}% pass-rate",
        "eyebrow": REPORT_META["assessments"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "assessment", "label": "Assessment"}, {"key": "program", "label": "Program"},
            {"key": "type", "label": "Type", "align": "center"}, {"key": "pass_score", "label": "Pass", "align": "right"},
            {"key": "max_score", "label": "Max", "align": "right"}, {"key": "attempts", "label": "Attempts", "align": "right"},
            {"key": "passed", "label": "Passed", "align": "right"},
            {"key": "pass_rate", "label": "Pass %", "align": "right", "fmt": "pct", "good_if": lambda v: v >= 80, "danger_if": lambda v: v < 50},
            {"key": "avg_score", "label": "Avg score", "align": "right"}, {"key": "active", "label": "Active", "align": "center", "fmt": "bool"},
        ],
        "rows": rows, "summary": summary,
    }


def _budget(db: Session, f: dict) -> dict:
    q = db.query(TrainingBudget, Department.name).outerjoin(
        Department, Department.id == TrainingBudget.department_id).filter(
        TrainingBudget.is_deleted == False)  # noqa: E712
    if f.get("department_id"):
        q = q.filter(TrainingBudget.department_id == f["department_id"])
    rows, t_alloc, t_spent, t_comm = [], 0.0, 0.0, 0.0
    for b, dept in q.order_by(TrainingBudget.fiscal_year.desc(), TrainingBudget.period_index.asc()).all():
        alloc = float(b.allocated_amount or 0); spent = float(b.spent_amount or 0); comm = float(b.committed_amount or 0)
        t_alloc += alloc; t_spent += spent; t_comm += comm
        rows.append({
            "name": b.name, "period": f"{b.period_type.value} {b.fiscal_year}" + (f" · P{b.period_index}" if b.period_index else ""),
            "department": dept or "Org-wide", "allocated": alloc, "spent": spent, "committed": comm,
            "available": round(alloc - spent - comm, 2),
            "utilization": _pct(spent + comm, alloc), "active": b.is_active,
        })
    summary = {"budgets": len(rows), "allocated": round(t_alloc, 2), "spent": round(t_spent, 2),
               "committed": round(t_comm, 2), "available": round(t_alloc - t_spent - t_comm, 2),
               "utilization": _pct(t_spent + t_comm, t_alloc)}
    return {
        "key": "budget", "title": "Budget Utilization",
        "subtitle": f"{len(rows)} budget(s) · {summary['utilization']}% utilized of ₹{summary['allocated']:,.0f}",
        "eyebrow": REPORT_META["budget"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "name", "label": "Budget"}, {"key": "period", "label": "Period", "align": "center"},
            {"key": "department", "label": "Department"}, {"key": "allocated", "label": "Allocated", "align": "right", "fmt": "money"},
            {"key": "spent", "label": "Spent", "align": "right", "fmt": "money"}, {"key": "committed", "label": "Committed", "align": "right", "fmt": "money"},
            {"key": "available", "label": "Available", "align": "right", "fmt": "money", "danger_if": lambda v: isinstance(v, (int, float)) and v < 0},
            {"key": "utilization", "label": "Used", "align": "right", "fmt": "pct", "danger_if": lambda v: v > 100, "good_if": lambda v: 60 <= v <= 100},
        ],
        "rows": rows, "summary": summary,
    }


def _department(db: Session, f: dict) -> dict:
    today = date.today()
    depts = db.query(Department).order_by(Department.name.asc()).all()
    rows = []
    tot_emp = tot_assign = tot_comp = tot_cert = 0
    for d in depts:
        emp_ids = [r[0] for r in db.query(Employee.id).filter(
            Employee.department_id == d.id, Employee.is_deleted == False).all()]  # noqa: E712
        if not emp_ids:
            continue
        a_total = db.query(func.count(TrainingAssignment.id)).filter(
            TrainingAssignment.employee_id.in_(emp_ids)).scalar() or 0
        a_comp = db.query(func.count(TrainingAssignment.id)).filter(
            TrainingAssignment.employee_id.in_(emp_ids),
            TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED).scalar() or 0
        certs = db.query(func.count(EmployeeCertification.id)).filter(
            EmployeeCertification.employee_id.in_(emp_ids),
            EmployeeCertification.is_deleted == False,  # noqa: E712
            EmployeeCertification.status == CertificationStatus.ACTIVE).scalar() or 0
        avg_gap = db.query(func.avg(EmployeeSkill.gap)).filter(
            EmployeeSkill.employee_id.in_(emp_ids)).scalar()
        tot_emp += len(emp_ids); tot_assign += int(a_total); tot_comp += int(a_comp); tot_cert += int(certs)
        rows.append({
            "department": d.name, "employees": len(emp_ids), "assignments": int(a_total),
            "completed": int(a_comp), "completion_rate": _pct(a_comp, a_total),
            "active_certs": int(certs), "avg_gap": round(float(avg_gap), 2) if avg_gap is not None else 0,
        })
    rows.sort(key=lambda r: r["completion_rate"], reverse=True)
    summary = {"departments": len(rows), "employees": tot_emp, "assignments": tot_assign,
               "completed": tot_comp, "completion_rate": _pct(tot_comp, tot_assign), "active_certs": tot_cert}
    return {
        "key": "department", "title": "Department Scorecard",
        "subtitle": f"{len(rows)} department(s) · {tot_emp} people · {summary['completion_rate']}% completed",
        "eyebrow": REPORT_META["department"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "department", "label": "Department"}, {"key": "employees", "label": "People", "align": "right"},
            {"key": "assignments", "label": "Assigned", "align": "right"}, {"key": "completed", "label": "Completed", "align": "right"},
            {"key": "completion_rate", "label": "Completion", "align": "right", "fmt": "pct", "good_if": lambda v: v >= 80, "danger_if": lambda v: v < 40},
            {"key": "active_certs", "label": "Active certs", "align": "right"},
            {"key": "avg_gap", "label": "Avg gap", "align": "right", "danger_if": lambda v: isinstance(v, (int, float)) and v >= 2},
        ],
        "rows": rows, "summary": summary,
    }


_BUILDERS = {
    "enrollments": _enrollments, "completion": _completion, "skill_gap": _skill_gap,
    "certifications": _certifications, "compliance": _compliance, "feedback": _feedback,
    "trainers": _trainers, "requests": _requests, "assessments": _assessments,
    "budget": _budget, "department": _department,
}


def build_report(db: Session, key: str, filters: dict) -> dict:
    if key not in _BUILDERS:
        raise HTTPException(404, f"Unknown report '{key}'")
    return _BUILDERS[key](db, filters or {})


# ════════════════════════════ SELF-SERVICE SHAPERS ════════════════════════════
# Every query is hard-filtered to ``f["employee_id"]`` — an employee can only
# ever export their own learning data. ``employee_name`` / ``employee_code`` ride
# along in the filters for the report's title line.
def _self_who(f: dict) -> str:
    name = f.get("employee_name") or "You"
    code = f.get("employee_code")
    return f"{name}" + (f" · {code}" if code else "")


def _my_record(db: Session, f: dict) -> dict:
    eid = f["employee_id"]
    today = date.today()
    q = (
        db.query(TrainingAssignment, TrainingProgram)
        .join(TrainingProgram, TrainingProgram.id == TrainingAssignment.program_id)
        .filter(TrainingAssignment.employee_id == eid)
    )
    if f.get("from"):
        q = q.filter(TrainingAssignment.assigned_date >= f["from"])
    if f.get("to"):
        q = q.filter(TrainingAssignment.assigned_date <= f["to"])
    rows = {"completed": 0, "in_progress": 0, "not_started": 0, "failed": 0, "waived": 0, "overdue": 0}
    out, hours = [], 0.0
    for a, p in q.order_by(TrainingAssignment.assigned_date.desc()).limit(5000).all():
        st = a.status.value if a.status else "NOT_STARTED"
        overdue = a.status in _OPEN and a.due_date and a.due_date < today
        if st == "COMPLETED":
            rows["completed"] += 1
            if p.duration_hours:
                hours += float(p.duration_hours)
        elif st == "IN_PROGRESS":
            rows["in_progress"] += 1
        elif st == "FAILED":
            rows["failed"] += 1
        elif st == "WAIVED":
            rows["waived"] += 1
        else:
            rows["not_started"] += 1
        if overdue:
            rows["overdue"] += 1
        out.append({
            "program": p.name, "type": p.training_type.value if p.training_type else "",
            "source": (a.enrollment_source or "MANUAL").replace("_", " ").title(),
            "status": st, "assigned": a.assigned_date, "due": a.due_date,
            "completion": a.completion_date,
            "score": float(a.score) if a.score is not None else "",
        })
    total = len(out)
    summary = {**rows, "total": total, "completion_rate": _pct(rows["completed"], total),
               "hours": round(hours, 1)}
    return {
        "key": "my_record", "title": "My Learning Record",
        "subtitle": f"{_self_who(f)} · {total} program(s) · {summary['completion_rate']}% complete · {summary['hours']:g} learning hours",
        "eyebrow": SELF_REPORT_META["my_record"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "program", "label": "Program"}, {"key": "type", "label": "Type", "align": "center"},
            {"key": "source", "label": "Source", "align": "center"},
            {"key": "status", "label": "Status", "align": "center", "pill": _ENROLL_STATUS_PILL},
            {"key": "assigned", "label": "Assigned", "align": "center"}, {"key": "due", "label": "Due", "align": "center"},
            {"key": "completion", "label": "Completed", "align": "center"}, {"key": "score", "label": "Score", "align": "right"},
        ],
        "rows": out, "summary": summary,
    }


def _my_skills(db: Session, f: dict) -> dict:
    eid = f["employee_id"]
    q = (
        db.query(EmployeeSkill, Skill)
        .join(Skill, Skill.id == EmployeeSkill.skill_id)
        .filter(EmployeeSkill.employee_id == eid)
        .order_by(EmployeeSkill.gap.desc().nullslast())
    )
    out, gaps, with_gap, at_target = [], [], 0, 0
    for es, sk in q.all():
        gap = int(es.gap) if es.gap is not None else 0
        if gap > 0:
            with_gap += 1
        else:
            at_target += 1
        gaps.append(gap)
        out.append({
            "skill": sk.name, "category": sk.category.value if sk.category else "",
            "current": es.current_level if es.current_level is not None else "",
            "required": es.required_level if es.required_level is not None else "",
            "max": sk.max_level, "gap": gap,
            "assessed": es.last_assessed_date,
        })
    total = len(out)
    summary = {"skills": total, "at_target": at_target, "with_gap": with_gap,
               "avg_gap": round(sum(gaps) / len(gaps), 2) if gaps else 0,
               "mastered": len([r for r in out if r["current"] not in ("", None) and r["max"] and r["current"] >= r["max"]])}
    return {
        "key": "my_skills", "title": "My Skill Passport",
        "subtitle": f"{_self_who(f)} · {total} competenc(ies) · {at_target} at target · avg gap {summary['avg_gap']}",
        "eyebrow": SELF_REPORT_META["my_skills"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "skill", "label": "Skill"}, {"key": "category", "label": "Category", "align": "center"},
            {"key": "current", "label": "Current", "align": "right"}, {"key": "required", "label": "Required", "align": "right"},
            {"key": "max", "label": "Max", "align": "right"},
            {"key": "gap", "label": "Gap", "align": "right", "danger_if": lambda v: isinstance(v, (int, float)) and v >= 2, "good_if": lambda v: isinstance(v, (int, float)) and v <= 0},
            {"key": "assessed", "label": "Last assessed", "align": "center"},
        ],
        "rows": out, "summary": summary,
    }


def _my_credentials(db: Session, f: dict) -> dict:
    eid = f["employee_id"]
    today = date.today()
    q = db.query(EmployeeCertification).filter(
        EmployeeCertification.employee_id == eid,
        EmployeeCertification.is_deleted == False,  # noqa: E712
    ).order_by(EmployeeCertification.expiry_date.asc().nullslast())
    out, s, soonest = [], {"active": 0, "expiring": 0, "expired": 0}, None
    for ec in q.all():
        days = (ec.expiry_date - today).days if ec.expiry_date else None
        st = ec.status.value if ec.status else "ACTIVE"
        if st == "ACTIVE":
            s["active"] += 1
        elif st == "EXPIRING_SOON":
            s["expiring"] += 1
        elif st == "EXPIRED":
            s["expired"] += 1
        if days is not None and days >= 0 and (soonest is None or days < soonest):
            soonest = days
        out.append({
            "certification": ec.name, "authority": ec.issuing_authority or "",
            "number": ec.certificate_number or "", "issue": ec.issue_date, "expiry": ec.expiry_date,
            "status": st, "days_to_expiry": days,
        })
    summary = {**s, "total": len(out), "soonest_days": soonest if soonest is not None else "—"}
    return {
        "key": "my_credentials", "title": "My Credential Portfolio",
        "subtitle": f"{_self_who(f)} · {len(out)} credential(s) · {s['active']} active · {s['expiring']} expiring",
        "eyebrow": SELF_REPORT_META["my_credentials"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "certification", "label": "Certification"}, {"key": "authority", "label": "Authority"},
            {"key": "number", "label": "Number"}, {"key": "issue", "label": "Issued", "align": "center"},
            {"key": "expiry", "label": "Expires", "align": "center"},
            {"key": "status", "label": "Status", "align": "center", "pill": _CERT_STATUS_PILL},
            {"key": "days_to_expiry", "label": "Days left", "align": "right", "danger_if": lambda v: isinstance(v, (int, float)) and v < 0, "good_if": lambda v: isinstance(v, (int, float)) and v > 90},
        ],
        "rows": out, "summary": summary,
    }


def _my_requests(db: Session, f: dict) -> dict:
    eid = f["employee_id"]
    q = (
        db.query(TrainingRequest, TrainingProgram.name)
        .outerjoin(TrainingProgram, TrainingProgram.id == TrainingRequest.program_id)
        .filter(TrainingRequest.employee_id == eid, TrainingRequest.is_deleted == False)  # noqa: E712
    )
    if f.get("from"):
        q = q.filter(TrainingRequest.created_at >= f["from"])
    if f.get("to"):
        q = q.filter(TrainingRequest.created_at <= f["to"])
    out, by = [], {}
    for r, pname in q.order_by(TrainingRequest.created_at.desc()).limit(5000).all():
        st = r.status.value if r.status else "DRAFT"
        by[st] = by.get(st, 0) + 1
        out.append({
            "number": r.request_number,
            "subject": pname or r.title or r.external_provider or "—",
            "provider": r.external_provider or ("Internal" if r.program_id else "—"),
            "status": st, "cost": float(r.estimated_cost) if r.estimated_cost else "",
            "preferred": r.preferred_start_date,
            "submitted": r.submitted_at.date() if r.submitted_at else "",
        })
    total = len(out)
    decided = by.get("APPROVED", 0) + by.get("FULFILLED", 0) + by.get("REJECTED", 0)
    summary = {"total": total, "pending": by.get("PENDING_APPROVAL", 0), "approved": by.get("APPROVED", 0),
               "fulfilled": by.get("FULFILLED", 0), "rejected": by.get("REJECTED", 0),
               "returned": by.get("RETURNED", 0), "draft": by.get("DRAFT", 0),
               "fulfil_rate": _pct(by.get("FULFILLED", 0), decided)}
    return {
        "key": "my_requests", "title": "My Training Requests",
        "subtitle": f"{_self_who(f)} · {total} request(s) · {summary['pending']} awaiting · {summary['fulfil_rate']}% fulfilled",
        "eyebrow": SELF_REPORT_META["my_requests"]["eyebrow"], "period": _period(f),
        "columns": [
            {"key": "number", "label": "Number"}, {"key": "subject", "label": "Program / Subject"},
            {"key": "provider", "label": "Provider", "align": "center"},
            {"key": "status", "label": "Status", "align": "center", "pill": _REQ_STATUS_PILL},
            {"key": "cost", "label": "Est. cost", "align": "right", "fmt": "money"},
            {"key": "preferred", "label": "Preferred", "align": "center"}, {"key": "submitted", "label": "Submitted", "align": "center"},
        ],
        "rows": out, "summary": summary,
    }


_SELF_BUILDERS = {
    "my_record": _my_record, "my_skills": _my_skills,
    "my_credentials": _my_credentials, "my_requests": _my_requests,
}


def build_self_report(db: Session, key: str, filters: dict) -> dict:
    if key not in _SELF_BUILDERS:
        raise HTTPException(404, f"Unknown report '{key}'")
    if not (filters or {}).get("employee_id"):
        raise HTTPException(400, "Self-service report requires an employee context")
    return _SELF_BUILDERS[key](db, filters)
