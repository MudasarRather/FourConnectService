"""HR Performance — Calibration & the 9-box talent grid.

After reviews are completed, managers/HR calibrate to normalise ratings across
teams. Each calibration row places one employee on the 9-box grid:

    performance_band (x, 1=Low 2=Med 3=High, from the review's overall score)
    potential_band   (y, 1=Low 2=Med 3=High, an HR/manager judgement)
    box = (potential_band - 1) * 3 + performance_band   →  1..9

``calibrated_score`` lets a calibration committee override the raw weighted score
(the review itself is never mutated — calibration is a separate, auditable layer).

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Numeric, Integer, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class CalibrationStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CALIBRATED = "CALIBRATED"


# 9-box quadrant labels keyed by box number (1..9).
BOX_LABELS = {
    1: "Risk",            2: "Inconsistent Player",  3: "Workhorse",
    4: "Dilemma",         5: "Core Player",          6: "High Performer",
    7: "Enigma",          8: "Growth Employee",      9: "Star",
}


def compute_box(performance_band: int, potential_band: int) -> int:
    p = max(1, min(3, int(performance_band or 1)))
    q = max(1, min(3, int(potential_band or 1)))
    return (q - 1) * 3 + p


def band_from_score(score, rating_max) -> int:
    """Map a 0..rating_max score to a 1/2/3 performance band."""
    if score is None or not rating_max:
        return 2
    frac = float(score) / float(rating_max)
    if frac >= 0.75:
        return 3
    if frac >= 0.5:
        return 2
    return 1


class PerformanceCalibration(Base):
    __tablename__ = "hr_performance_calibrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    review_id = Column(UUID(as_uuid=True), ForeignKey("hr_performance_reviews.id", ondelete="CASCADE"), nullable=True, index=True)

    cycle = Column(String(20), nullable=False, default="ANNUAL", index=True)
    period_label = Column(String(60), nullable=True)

    # snapshot of the source score + the calibrated overlay
    performance_score = Column(Numeric(5, 2), nullable=True)   # raw review overall (snapshot)
    rating_max = Column(Integer, nullable=False, default=5)
    calibrated_score = Column(Numeric(5, 2), nullable=True)    # optional committee override

    performance_band = Column(Integer, nullable=False, default=2)   # 1..3 (x axis)
    potential_band = Column(Integer, nullable=False, default=2)     # 1..3 (y axis)
    box = Column(Integer, nullable=False, default=5)                # 1..9

    note = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default=CalibrationStatus.DRAFT.value, index=True)

    calibrated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    calibrated_at = Column(DateTime(timezone=True), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])
    review = relationship("PerformanceReview", foreign_keys=[review_id])

    def __repr__(self):
        return f"<PerformanceCalibration {self.employee_id} box={self.box}>"
