"""HR Performance — Calibration & the 9-box talent grid (admin / superuser).

Seed calibration rows from completed reviews, then place each employee on the
performance × potential grid. The review is never mutated — calibration is a
separate, auditable layer with an optional committee score override.

Distinct prefix /hr/performance-calibration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.models.hr.employee import Employee
from app.models.hr.performance_review import PerformanceReview, PerformanceReviewStatus
from app.models.hr.performance_calibration import (
    PerformanceCalibration, CalibrationStatus, BOX_LABELS, compute_box, band_from_score,
)
from app.schemas.hr.performance_calibration import CalibrationUpsert, CalibrationMove, SeedCalibrationBody
from app.utils.hr.performance_service import serialize_calibration, emp_name

router = APIRouter(prefix="/hr/performance-calibration", tags=["HR — Performance Calibration"])

S = PerformanceReviewStatus


def _now():
    return datetime.now(timezone.utc)


def _load(db: Session, cal_id: UUID) -> PerformanceCalibration:
    c = db.query(PerformanceCalibration).filter(
        PerformanceCalibration.id == cal_id, PerformanceCalibration.is_deleted == False,  # noqa: E712
    ).first()
    if not c:
        raise HTTPException(404, "Calibration entry not found")
    return c


def _rows(db: Session, cycle, period_label):
    q = db.query(PerformanceCalibration).filter(PerformanceCalibration.is_deleted == False)  # noqa: E712
    if cycle:
        q = q.filter(PerformanceCalibration.cycle == cycle)
    if period_label:
        q = q.filter(PerformanceCalibration.period_label == period_label)
    return q.all()


@router.get("/")
def list_calibration(
    cycle: Optional[str] = None,
    period_label: Optional[str] = None,
    page: int = 1,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(PerformanceCalibration).filter(PerformanceCalibration.is_deleted == False)  # noqa: E712
    if cycle:
        q = q.filter(PerformanceCalibration.cycle == cycle)
    if period_label:
        q = q.filter(PerformanceCalibration.period_label == period_label)
    total = q.count()
    rows = q.order_by(PerformanceCalibration.box.desc()).offset((page - 1) * limit).limit(limit).all()
    maps = {"desig": {}, "dept": {}}
    return {"items": [serialize_calibration(db, c, maps) for c in rows], "total": total}


@router.get("/cycles")
def calibration_cycles(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Distinct cycle/period groups that have completed reviews (calibration targets)."""
    rows = (
        db.query(PerformanceReview.cycle, PerformanceReview.period_label)
        .filter(
            PerformanceReview.is_deleted == False,  # noqa: E712
            PerformanceReview.status.in_([S.COMPLETED.value, S.ACKNOWLEDGED.value]),
        ).distinct().all()
    )
    seen = []
    for cyc, period in rows:
        seen.append({"cycle": cyc, "period_label": period})
    return {"items": seen}


@router.get("/grid")
def nine_box(
    cycle: Optional[str] = None,
    period_label: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    rows = _rows(db, cycle, period_label)
    maps = {"desig": {}, "dept": {}}
    cells = {
        b: {"box": b, "label": BOX_LABELS[b], "members": []}
        for b in range(1, 10)
    }
    # bell-curve readout: score floored into 1..5 buckets over calibrated/raw scores
    dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for c in rows:
        cells[c.box]["members"].append(serialize_calibration(db, c, maps))
        sc = c.calibrated_score if c.calibrated_score is not None else c.performance_score
        if sc is None:
            continue
        b = max(1, min(5, int(float(sc))))
        dist[b] += 1
    return {
        "cells": [cells[b] for b in range(1, 10)],
        "total": len(rows),
        "calibrated": sum(1 for c in rows if c.status == CalibrationStatus.CALIBRATED.value),
        "distribution": [{"band": k, "count": v} for k, v in dist.items()],
        "cycle": cycle,
        "period_label": period_label,
    }


@router.post("/seed")
def seed_from_reviews(payload: SeedCalibrationBody, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Create calibration rows for every completed review in a cycle/period that
    isn't already on the grid. Idempotent — existing rows are left untouched."""
    q = db.query(PerformanceReview).filter(
        PerformanceReview.is_deleted == False,  # noqa: E712
        PerformanceReview.status.in_([S.COMPLETED.value, S.ACKNOWLEDGED.value]),
    )
    if payload.cycle:
        q = q.filter(PerformanceReview.cycle == payload.cycle)
    if payload.period_label:
        q = q.filter(PerformanceReview.period_label == payload.period_label)
    reviews = q.all()
    created = 0
    for r in reviews:
        exists = db.query(PerformanceCalibration).filter(
            PerformanceCalibration.review_id == r.id, PerformanceCalibration.is_deleted == False,  # noqa: E712
        ).first()
        if exists:
            continue
        pband = band_from_score(r.overall_score, r.rating_max)
        c = PerformanceCalibration(
            employee_id=r.employee_id, review_id=r.id, cycle=r.cycle, period_label=r.period_label,
            performance_score=r.overall_score, rating_max=r.rating_max or 5,
            performance_band=pband, potential_band=2, box=compute_box(pband, 2),
            status=CalibrationStatus.DRAFT.value,
        )
        db.add(c)
        created += 1
    db.commit()
    return {"created": created, "total_reviews": len(reviews)}


@router.post("/", status_code=201)
def upsert(payload: CalibrationUpsert, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    review = None
    if payload.review_id:
        review = db.query(PerformanceReview).filter(PerformanceReview.id == payload.review_id).first()

    cycle = payload.cycle or (review.cycle if review else "ANNUAL")
    period = payload.period_label or (review.period_label if review else None)

    # find existing row for this employee+cycle (+review)
    existing = db.query(PerformanceCalibration).filter(
        PerformanceCalibration.employee_id == emp.id,
        PerformanceCalibration.cycle == cycle,
        PerformanceCalibration.is_deleted == False,  # noqa: E712
    )
    if payload.review_id:
        existing = existing.filter(PerformanceCalibration.review_id == payload.review_id)
    c = existing.first()

    score = review.overall_score if review else (c.performance_score if c else None)
    rmax = (review.rating_max if review else (c.rating_max if c else 5)) or 5
    pband = payload.performance_band or band_from_score(payload.calibrated_score or score, rmax)
    qband = payload.potential_band or 2
    box = compute_box(pband, qband)

    if not c:
        c = PerformanceCalibration(
            employee_id=emp.id, review_id=payload.review_id, cycle=cycle, period_label=period,
            performance_score=score, rating_max=rmax,
        )
        db.add(c)
    c.performance_band = pband
    c.potential_band = qband
    c.box = box
    if payload.calibrated_score is not None:
        c.calibrated_score = payload.calibrated_score
    if payload.note is not None:
        c.note = payload.note
    if payload.status:
        c.status = payload.status
        if payload.status == CalibrationStatus.CALIBRATED.value:
            c.calibrated_by_id = admin.id
            c.calibrated_at = _now()
    db.commit()
    db.refresh(c)
    return serialize_calibration(db, c)


@router.patch("/{cal_id}/move")
def move_chip(cal_id: UUID, payload: CalibrationMove, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = _load(db, cal_id)
    c.performance_band = max(1, min(3, payload.performance_band))
    c.potential_band = max(1, min(3, payload.potential_band))
    c.box = compute_box(c.performance_band, c.potential_band)
    if payload.note is not None:
        c.note = payload.note
    db.commit()
    db.refresh(c)
    return serialize_calibration(db, c)


@router.post("/{cal_id}/calibrate")
def mark_calibrated(cal_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = _load(db, cal_id)
    c.status = CalibrationStatus.CALIBRATED.value
    c.calibrated_by_id = admin.id
    c.calibrated_at = _now()
    db.commit()
    db.refresh(c)
    return serialize_calibration(db, c)


@router.delete("/{cal_id}", status_code=204)
def delete_calibration(cal_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = _load(db, cal_id)
    c.is_deleted = True
    db.commit()
