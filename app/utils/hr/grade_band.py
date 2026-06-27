"""Grade CTC-band enforcement.

A single guard so every path that sets an employee's pay (employee create/update,
lifecycle promotion, and compensation revisions) validates the ANNUAL CTC against
the assigned grade's ``min_ctc`` / ``max_ctc`` band. Grade bands are stored as
annual figures. The guard is a no-op when there's no grade, no amount, or the
grade carries no band — so it never blocks edits unrelated to pay.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.hr.grade import Grade


def _fmt(v) -> str:
    try:
        return f"₹{Decimal(str(v)):,.0f}"
    except (InvalidOperation, ValueError, TypeError):
        return f"₹{v}"


def assert_ctc_in_grade_band(db: Session, grade_id, annual_ctc) -> None:
    """Raise 422 when ``annual_ctc`` falls outside the grade's configured band.

    No-op when grade_id / amount is missing, the amount is non-positive, the
    grade is missing/deleted, or the grade has no min/max set.
    """
    if grade_id is None or annual_ctc in (None, ""):
        return
    try:
        val = Decimal(str(annual_ctc))
    except (InvalidOperation, ValueError, TypeError):
        return
    if val <= 0:
        return
    grade = db.query(Grade).filter(
        Grade.id == grade_id, Grade.is_deleted == False,  # noqa: E712
    ).first()
    if not grade:
        return
    lo, hi = grade.min_ctc, grade.max_ctc
    if lo is not None and val < Decimal(str(lo)):
        raise HTTPException(
            422,
            f"Annual CTC {_fmt(val)} is below the {grade.code} grade band minimum {_fmt(lo)}. "
            "Pick a grade whose band covers this CTC, or adjust the amount.",
        )
    if hi is not None and val > Decimal(str(hi)):
        raise HTTPException(
            422,
            f"Annual CTC {_fmt(val)} is above the {grade.code} grade band maximum {_fmt(hi)}. "
            "Pick a grade whose band covers this CTC, or adjust the amount.",
        )
