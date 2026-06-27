"""HR numbering helper — formats the next ID for a module from its NumberingSeries.

``next_number(db, module)`` returns a formatted string and advances the counter
INSIDE the caller's transaction (atomic with the row being numbered), or returns
``None`` when no active series is configured — so callers keep their existing
PG-sequence / MAX+1 generation unchanged until an admin opts in.

The counter advance is wrapped in a SAVEPOINT: any failure rolls back just the
increment and re-raises, so the caller's except can fall through to legacy logic
without corrupting the outer transaction.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.hr.numbering_series import NumberingSeries


def _fy_string(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1   # India FY: Apr–Mar
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def preview(series: NumberingSeries, n: int, d: date | None = None) -> str:
    """Render what a given counter value would look like (pure, no DB)."""
    d = d or date.today()
    sep = series.separator or ""
    parts = []
    if series.prefix:
        parts.append(series.prefix)
    if series.include_year:
        parts.append(str(d.year))
    if series.include_month:
        parts.append(f"{d.month:02d}")
    parts.append(str(int(n)).zfill(int(series.padding or 0)))
    out = sep.join(parts) if sep else "".join(parts)
    if series.suffix:
        out = f"{out}{sep}{series.suffix}" if sep else f"{out}{series.suffix}"
    return out


def next_number(db: Session, module: str):
    """Allocate + format the next id for ``module``; None if no active series."""
    series = (db.query(NumberingSeries)
              .filter(NumberingSeries.module == module,
                      NumberingSeries.is_active == True,     # noqa: E712
                      NumberingSeries.is_deleted == False)   # noqa: E712
              .first())
    if not series:
        return None
    today = date.today()
    sp = db.begin_nested()
    try:
        if series.financial_year_reset:
            fy = _fy_string(today)
            if series.last_reset_fy != fy:
                series.current_number = 0
                series.last_reset_fy = fy
        series.current_number = int(series.current_number or 0) + 1
        db.flush()
        sp.commit()
        return preview(series, series.current_number, today)
    except Exception:
        sp.rollback()
        raise
