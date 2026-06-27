"""HR Settings — Merit & Increment Policy (the company appraisal→pay policy).

This is the governance layer that converts a performance rating into a salary
hike. An appraisal *template* defines HOW you score (weighted sections, rating
scale); a *merit policy* defines WHAT a score earns — the rating→hike% bands and
the org-wide merit budget guardrail.

Band boundaries are expressed as a FRACTION of ``rating_max`` (0..1) so a single
policy works across templates with different rating scales (e.g. a 4.2/5 and an
8.4/10 both resolve to frac 0.84). This mirrors
``performance_calibration.band_from_score`` which also works in fractions.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


# Seed for a fresh policy / when none is configured. Bands are ordered high→low.
# frac_min/frac_max are fractions of rating_max; hike_*_pct are percentages.
DEFAULT_BANDS = [
    {"key": "EXCEPTIONAL", "label": "Exceptional", "frac_min": 0.90, "frac_max": 1.01,
     "hike_min_pct": 12, "hike_max_pct": 18, "auto_pip": False},
    {"key": "EXCEEDS", "label": "Exceeds", "frac_min": 0.70, "frac_max": 0.90,
     "hike_min_pct": 8, "hike_max_pct": 12, "auto_pip": False},
    {"key": "MEETS", "label": "Meets expectations", "frac_min": 0.50, "frac_max": 0.70,
     "hike_min_pct": 4, "hike_max_pct": 8, "auto_pip": False},
    {"key": "PARTIAL", "label": "Partially meets", "frac_min": 0.30, "frac_max": 0.50,
     "hike_min_pct": 0, "hike_max_pct": 3, "auto_pip": False},
    {"key": "BELOW", "label": "Below expectations", "frac_min": 0.0, "frac_max": 0.30,
     "hike_min_pct": 0, "hike_max_pct": 0, "auto_pip": True},
]


def band_for_score(policy, score, rating_max):
    """Resolve which merit band a score lands in.

    ``policy`` may be a MeritPolicy instance, a plain dict ({"bands": [...]}), or
    None (falls back to DEFAULT_BANDS). Returns the band dict, or None if the
    score is None / un-resolvable.
    """
    if score is None or not rating_max:
        return None
    bands = None
    if policy is not None:
        bands = policy.get("bands") if isinstance(policy, dict) else getattr(policy, "bands", None)
    if not bands:
        bands = DEFAULT_BANDS
    try:
        frac = float(score) / float(rating_max)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    frac = max(0.0, min(1.0, frac))
    # Highest band whose [frac_min, frac_max) covers the score; frac_max is
    # exclusive except the top band (frac_max > 1.0) so a perfect score lands.
    for b in sorted(bands, key=lambda x: float(x.get("frac_min", 0)), reverse=True):
        lo = float(b.get("frac_min", 0))
        hi = float(b.get("frac_max", 1.01))
        if frac >= lo and (frac < hi or hi > 1.0):
            return b
    # Fallback: lowest band.
    return sorted(bands, key=lambda x: float(x.get("frac_min", 0)))[0] if bands else None


class MeritPolicy(Base):
    __tablename__ = "hr_merit_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    # org merit budget guardrail — % of total payroll the cycle's hikes may spend
    merit_budget_pct = Column(Numeric(5, 2), nullable=True)

    # rating→hike bands: [{key, label, frac_min, frac_max, hike_min_pct, hike_max_pct, auto_pip}]
    bands = Column(JSONB, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_default = Column(Boolean, nullable=False, default=False, index=True)  # the cycle-launch default
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    def __repr__(self):
        return f"<MeritPolicy {self.name} default={self.is_default}>"
