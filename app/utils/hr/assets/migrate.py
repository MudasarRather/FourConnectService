"""Guarded ALTERs for the Asset Management module (Phase 5 "Asset Hangar").

``Base.metadata.create_all()`` creates NEW tables (categories, vendors, transfers,
maintenance, damages, history, audits, disposals) but never alters EXISTING ones.
The module adds additive (nullable / defaulted) columns to ``hr_assets``. This
helper adds them idempotently with ``ADD COLUMN IF NOT EXISTS`` so a deploy against
the live (remote) DB picks them up without an Alembic revision.

Called once at startup (``app/main.py``) and also runnable standalone via
``add_asset_columns.py`` at the backend root.
"""
from __future__ import annotations

from sqlalchemy import text


# (table, column, column DDL type)
_ADDITIVE_COLUMNS = [
    ("hr_assets", "category_id", "UUID"),
    ("hr_assets", "vendor_id", "UUID"),
    ("hr_assets", "department_id", "UUID"),
    ("hr_assets", "project_id", "UUID"),
    ("hr_assets", "purchase_order_no", "VARCHAR(60)"),
    ("hr_assets", "invoice_no", "VARCHAR(60)"),
    ("hr_assets", "warranty_start", "DATE"),
    ("hr_assets", "warranty_end", "DATE"),
    ("hr_assets", "depreciation_method", "VARCHAR(30)"),
    ("hr_assets", "salvage_value", "NUMERIC(12,2)"),
    ("hr_assets", "current_book_value", "NUMERIC(12,2)"),
    ("hr_assets", "building", "VARCHAR(80)"),
    ("hr_assets", "floor", "VARCHAR(40)"),
    ("hr_assets", "room", "VARCHAR(40)"),
    ("hr_assets", "tag", "VARCHAR(80)"),
    ("hr_assets", "photo_path", "VARCHAR(300)"),
    ("hr_assets", "invoice_path", "VARCHAR(300)"),
    ("hr_assets", "warranty_doc_path", "VARCHAR(300)"),
    # Self-service return request lives on the allocation (employee flags it, HR
    # completes it from the Returns tab — NO transfer record is created).
    ("hr_asset_allocations", "return_requested", "BOOLEAN DEFAULT FALSE"),
    ("hr_asset_allocations", "return_requested_at", "TIMESTAMPTZ"),
    ("hr_asset_allocations", "return_request_note", "TEXT"),
]

# (enum type name, value) — new AssetEventType members added after the type was
# first created. ``ADD VALUE IF NOT EXISTS`` is idempotent (PG 12+).
_ENUM_VALUES = [
    ("hr_asset_event_type", "RETURN_REQUESTED"),
    ("hr_asset_event_type", "RETURN_REQUEST_CANCELLED"),
]


def ensure_asset_enum_values(engine) -> list[str]:
    """Add new values to existing Postgres enum types. Idempotent + guarded.

    ``ALTER TYPE ... ADD VALUE`` historically could not run inside a transaction
    block, so each statement runs on its own AUTOCOMMIT connection. ``IF NOT
    EXISTS`` makes re-runs a no-op. Each is wrapped so one failure (e.g. SQLite in
    a test) can't block the rest or boot.
    """
    applied: list[str] = []
    for type_name, value in _ENUM_VALUES:
        stmt = f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{value}'"
        try:
            with engine.connect() as conn:
                conn = conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.execute(text(stmt))
            applied.append(stmt)
        except Exception as exc:  # noqa: BLE001 — never let an enum add crash boot
            import traceback
            print(f"[assets.migrate] skipped enum {type_name}+={value}: {exc}")
            traceback.print_exc()
    return applied


def ensure_asset_columns(engine) -> list[str]:
    """Add the Asset Management additive columns if missing. Idempotent.

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
            print(f"[assets.migrate] skipped {table}.{column}: {exc}")
            traceback.print_exc()
    return applied
