"""Guarded ALTERs for the Training & Development module.

``Base.metadata.create_all()`` creates NEW tables but never alters EXISTING ones.
The Training & Development module adds a few additive (nullable / defaulted)
columns to ``hr_training_programs`` and ``hr_training_assignments``. This helper
adds them idempotently with ``ADD COLUMN IF NOT EXISTS`` so a deploy against the
live (remote) DB picks them up without an Alembic revision.

Called once at startup (``app/main.py``) and also runnable standalone via
``add_training_columns.py`` at the backend root.
"""
from __future__ import annotations

from sqlalchemy import text


# (table, column, column DDL type)
_ADDITIVE_COLUMNS = [
    ("hr_training_programs", "delivery_mode", "VARCHAR(30)"),
    ("hr_training_programs", "is_compliance", "BOOLEAN DEFAULT FALSE"),
    ("hr_training_assignments", "enrollment_source", "VARCHAR(30)"),
    ("hr_training_assignments", "valid_until", "DATE"),
    ("hr_training_assignments", "feedback_submitted", "BOOLEAN DEFAULT FALSE"),
]


def ensure_training_columns(engine) -> list[str]:
    """Add the Training & Development additive columns if missing. Idempotent.

    Returns the list of DDL statements executed (for logging). Each statement is
    wrapped in its own try/except so one failure can't block the rest or boot.
    """
    applied: list[str] = []
    for table, column, ddl in _ADDITIVE_COLUMNS:
        stmt = f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}'
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            applied.append(stmt)
        except Exception as exc:  # noqa: BLE001 — never let a column add crash boot
            import traceback
            print(f"[training.migrate] skipped {table}.{column}: {exc}")
            traceback.print_exc()
    return applied
