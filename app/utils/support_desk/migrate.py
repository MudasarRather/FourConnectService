"""Guarded ALTERs for the Support Desk — pending-vendor lifecycle.

``Base.metadata.create_all()`` creates NEW tables but never alters EXISTING ones.
The "Vendor Relay Station" redesign adds additive (nullable / defaulted) columns to
``support_tickets`` so an agent's third-party hand-off becomes a real lifecycle
(dispatched-at, vendor OLA/ETA, reply-at, chase tracking, wait-reason, PO ref).

This helper adds them idempotently with ``ADD COLUMN IF NOT EXISTS`` so a deploy
against the live (remote) DB picks them up WITHOUT an Alembic revision — matching
the module's existing convention (support tables were created via ``create_all``,
not the alembic graph, so an autogenerate would try to recreate every table).

Called once at startup (``app/main.py``) and also runnable standalone via
``add_vendor_lifecycle_columns.py`` at the backend root.
"""
from __future__ import annotations

from sqlalchemy import text


# (table, column, column DDL type). All additive: nullable or defaulted.
_ADDITIVE_COLUMNS = [
    ("support_tickets", "vendor_dispatched_at", "TIMESTAMPTZ"),
    ("support_tickets", "vendor_due_at", "TIMESTAMPTZ"),
    ("support_tickets", "vendor_reply_at", "TIMESTAMPTZ"),
    ("support_tickets", "vendor_reminder_count", "INTEGER NOT NULL DEFAULT 0"),
    ("support_tickets", "last_vendor_reminder_at", "TIMESTAMPTZ"),
    ("support_tickets", "vendor_wait_reason", "VARCHAR(40)"),
    ("support_tickets", "vendor_po_ref", "VARCHAR(120)"),
    ("support_tickets", "vendor_overdue_flagged", "BOOLEAN NOT NULL DEFAULT FALSE"),
]

# (index name, table, column expression) — speeds the overdue sweep + queue sort.
_INDEXES = [
    ("ix_support_tickets_vendor_due_at", "support_tickets", "vendor_due_at"),
]


# ── On-Hold "Suspension Dock" — hold governance columns ──
# hold_reason_code = coded HoldReason taxonomy (hold_reason stays free-text detail);
# last_hold_review_at / hold_review_count = hold-review governance (extend/re-confirm).
_HOLD_COLUMNS = [
    ("support_tickets", "hold_reason_code", "VARCHAR(40)"),
    ("support_tickets", "last_hold_review_at", "TIMESTAMPTZ"),
    ("support_tickets", "hold_review_count", "INTEGER NOT NULL DEFAULT 0"),
]

_HOLD_INDEXES = [
    # Speeds the auto-resume expiry sweep (status = on_hold AND hold_until < now).
    ("ix_support_tickets_hold_until", "support_tickets", "hold_until"),
]


# ── Critical "War Room" — acknowledge + stakeholder-update cadence columns ──
# acknowledged_at/by = ServiceNow-style ACK (MTTA source; distinct from
# first_responded_at which is the customer-facing first reply).
# update_interval_minutes / next_update_due_at / last_status_update_at = the
# stakeholder status-update cadence timer armed on major incidents.
_CRITICAL_COLUMNS = [
    ("support_tickets", "acknowledged_at", "TIMESTAMPTZ"),
    ("support_tickets", "acknowledged_by_id", "UUID"),
    ("support_tickets", "update_interval_minutes", "INTEGER"),
    ("support_tickets", "next_update_due_at", "TIMESTAMPTZ"),
    ("support_tickets", "last_status_update_at", "TIMESTAMPTZ"),
]

_CRITICAL_INDEXES = [
    # Speeds the update-overdue sweep (next_update_due_at < now, non-terminal).
    ("ix_support_tickets_next_update_due", "support_tickets", "next_update_due_at"),
]


# ── Escalated "Thermal Updraft" desk — structured escalation workflow columns ──
# escalation_type/reason_code/escalated_by/escalated_to_team = the structured record;
# escalation_acknowledged_at/by = the eMTTA ack (distinct from war-room acknowledged_at);
# escalation_response_due_at = the receiving tier's ack deadline (overdue sweep);
# auto_escalated_at = once-only stamp for the SLA-breach auto-escalation sweep.
_ESCALATION_COLUMNS = [
    ("support_tickets", "escalation_type", "VARCHAR(20)"),
    ("support_tickets", "escalation_reason_code", "VARCHAR(40)"),
    ("support_tickets", "escalated_by_id", "UUID"),
    ("support_tickets", "escalated_to_team_id", "UUID"),
    ("support_tickets", "escalation_acknowledged_at", "TIMESTAMPTZ"),
    ("support_tickets", "escalation_acknowledged_by_id", "UUID"),
    ("support_tickets", "escalation_response_due_at", "TIMESTAMPTZ"),
    ("support_tickets", "auto_escalated_at", "TIMESTAMPTZ"),
]

_ESCALATION_INDEXES = [
    # Speeds the escalation-response-overdue sweep (unacked AND due < now).
    ("ix_support_tickets_esc_response_due", "support_tickets", "escalation_response_due_at"),
    # Speeds dwell/oldest-age stats + the escalated desk's default sort.
    ("ix_support_tickets_escalated_at", "support_tickets", "escalated_at"),
]


# ── Reopened "Möbius Loop" desk — reopen-lifecycle metadata columns ──
# reopen_source = WHO put it back (requester|agent|portal|auto); reopen_reason_code = the
# coded verdict on the failed fix (ReopenReason; reopen_reason stays free text);
# last_reopened_at/by = the cycle stamp; reopen_latency_ms = resolved→reopen gap of the
# LAST cycle (time-to-reopen KPI); prev_resolution_* = the failed-fix snapshot preserved
# when the live resolution fields are cleared for the fresh cycle.
# DDL-SAFETY: reopened_count / reopen_reason exist in the model but never had DDL anywhere
# (model-only) — on a DB predating those model lines, scope=reopened and both reopen routes
# would 500. Guarded here so every deploy self-heals.
_REOPEN_COLUMNS = [
    ("support_tickets", "reopened_count", "INTEGER NOT NULL DEFAULT 0"),
    ("support_tickets", "reopen_reason", "TEXT"),
    ("support_tickets", "reopen_reason_code", "VARCHAR(40)"),
    ("support_tickets", "reopen_source", "VARCHAR(20)"),
    ("support_tickets", "last_reopened_at", "TIMESTAMPTZ"),
    ("support_tickets", "last_reopened_by_id", "UUID"),
    ("support_tickets", "reopen_latency_ms", "BIGINT"),
    ("support_tickets", "prev_resolution_code", "VARCHAR(40)"),
    ("support_tickets", "prev_resolution_summary", "TEXT"),
    ("support_tickets", "prev_resolved_at", "TIMESTAMPTZ"),
]

_REOPEN_INDEXES = [
    # Speeds the Reopened desk's default sort (latest cycle first).
    ("ix_support_tickets_last_reopened_at", "support_tickets", "last_reopened_at"),
    # Speeds scope=reopened (reopened_count > 0) + the chronic (>=2) lens.
    ("ix_support_tickets_reopened_count", "support_tickets", "reopened_count"),
]


# ── Closed "Archive of Record" desk — Zendesk-style follow-up linkage ──
# follow_up_of_id = the closed ticket this new case follows up on. Closed records are
# immutable for requesters; a follow-up is the sanctioned way to continue the story.
# FK kept in a separate guarded statement: on engines where the constraint fails
# (SQLite tests) the plain UUID column still lands and the feature degrades safely.
_FOLLOWUP_COLUMNS = [
    ("support_tickets", "follow_up_of_id", "UUID REFERENCES support_tickets(id)"),
]

_FOLLOWUP_INDEXES = [
    # Speeds the children lookup (WHERE follow_up_of_id = :id) in the certificate chain.
    ("ix_support_tickets_follow_up_of_id", "support_tickets", "follow_up_of_id"),
]


# ── Templates "Copperplate Studio" — template lifecycle / analytics / defaults ──
# status = draft|active|archived lifecycle (is_active stays mirrored for API stability);
# usage_count / last_used_at / last_used_by_id = apply-flow analytics;
# default_sla_package_id / default_assignee_id = extra pre-fill defaults;
# icon / accent / pinned / sort_order = library card identity + ordering;
# version / revisions = versioning-lite (content edits snapshot the previous cut).
# template_id on support_tickets = provenance stamp for tickets born from a template.
# UUID columns land bare (the ORM declares the FKs) to dodge create-order issues.
_TEMPLATE_STUDIO_COLUMNS = [
    ("support_ticket_templates", "status", "VARCHAR(16) NOT NULL DEFAULT 'active'"),
    ("support_ticket_templates", "usage_count", "INTEGER NOT NULL DEFAULT 0"),
    ("support_ticket_templates", "last_used_at", "TIMESTAMPTZ"),
    ("support_ticket_templates", "last_used_by_id", "UUID"),
    ("support_ticket_templates", "default_sla_package_id", "UUID"),
    ("support_ticket_templates", "default_assignee_id", "UUID"),
    ("support_ticket_templates", "icon", "VARCHAR(40)"),
    ("support_ticket_templates", "accent", "VARCHAR(20)"),
    ("support_ticket_templates", "pinned", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("support_ticket_templates", "sort_order", "INTEGER NOT NULL DEFAULT 0"),
    ("support_ticket_templates", "version", "INTEGER NOT NULL DEFAULT 1"),
    ("support_ticket_templates", "revisions", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    # visibility scope (agent Template Desk): global | team | personal.
    # 'global' is the correct historical backfill — every pre-existing template was desk-wide.
    ("support_ticket_templates", "visibility", "VARCHAR(16) NOT NULL DEFAULT 'global'"),
    ("support_tickets", "template_id", "UUID"),
]

_TEMPLATE_STUDIO_INDEXES = [
    # Speeds the status lenses + stats group-by.
    ("ix_support_ticket_templates_status", "support_ticket_templates", "status"),
    # Speeds the agent-desk visibility seal.
    ("ix_support_ticket_templates_visibility", "support_ticket_templates", "visibility"),
    # Speeds the tickets-born-from-templates conversion stats.
    ("ix_support_tickets_template_id", "support_tickets", "template_id"),
]


def _apply_additive(engine, columns, indexes) -> list[str]:
    """Run guarded ADD COLUMN / CREATE INDEX statements, one transaction each."""
    applied: list[str] = []
    for table, column, ddl in columns:
        stmt = f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}'
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            applied.append(stmt)
        except Exception as exc:  # noqa: BLE001 — additive, never fatal
            print(f"[support_desk.migrate] skipped: {stmt} ({exc})")
    for name, table, expr in indexes:
        stmt = f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({expr})'
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            applied.append(stmt)
        except Exception as exc:  # noqa: BLE001
            print(f"[support_desk.migrate] skipped: {stmt} ({exc})")
    return applied


def ensure_ticket_hold_columns(engine) -> list[str]:
    """Add the on-hold governance columns + hold_until index. Idempotent + guarded."""
    return _apply_additive(engine, _HOLD_COLUMNS, _HOLD_INDEXES)


def ensure_ticket_critical_columns(engine) -> list[str]:
    """Add the war-room ACK + update-cadence columns + index. Idempotent + guarded."""
    return _apply_additive(engine, _CRITICAL_COLUMNS, _CRITICAL_INDEXES)


def ensure_ticket_escalation_columns(engine) -> list[str]:
    """Add the structured-escalation workflow columns + indexes. Idempotent + guarded."""
    return _apply_additive(engine, _ESCALATION_COLUMNS, _ESCALATION_INDEXES)


def ensure_ticket_reopen_columns(engine) -> list[str]:
    """Add the reopen-lifecycle columns + indexes, then best-effort backfill the cycle
    stamp for tickets reopened BEFORE these columns existed: last_reopened_at/reopen_source
    are recovered from each ticket's newest action='reopened' activity row (detail->>'by'
    = 'requester' → requester, else agent). Idempotent + guarded (never blocks boot)."""
    applied = _apply_additive(engine, _REOPEN_COLUMNS, _REOPEN_INDEXES)
    backfill = """
        UPDATE support_tickets t SET
            last_reopened_at = a.created_at,
            reopen_source = CASE WHEN a.detail->>'by' = 'requester' THEN 'requester' ELSE 'agent' END
        FROM (
            SELECT DISTINCT ON (ticket_id) ticket_id, created_at, detail
            FROM support_ticket_activities
            WHERE action = 'reopened'
            ORDER BY ticket_id, created_at DESC
        ) a
        WHERE a.ticket_id = t.id AND t.reopened_count > 0 AND t.last_reopened_at IS NULL
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(backfill))
        applied.append("backfill: last_reopened_at/reopen_source from activities")
    except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
        print(f"[support_desk.migrate] skipped reopen backfill ({exc})")
    return applied


def ensure_ticket_followup_columns(engine) -> list[str]:
    """Add the follow-up linkage column + index (Closed desk). Idempotent + guarded.

    If the FK variant fails (e.g. non-Postgres test engine), retry as a plain UUID
    column so the API never 500s on the missing attribute."""
    applied = _apply_additive(engine, _FOLLOWUP_COLUMNS, _FOLLOWUP_INDEXES)
    if not applied:
        applied = _apply_additive(
            engine, [("support_tickets", "follow_up_of_id", "UUID")], _FOLLOWUP_INDEXES
        )
    return applied


def ensure_template_studio_columns(engine) -> list[str]:
    """Add the Template Studio lifecycle/analytics/defaults columns + indexes, then
    backfill: templates deactivated BEFORE the status column existed become
    status='archived' (rows already carrying draft/archived are never touched).
    Idempotent + guarded (never blocks boot)."""
    applied = _apply_additive(engine, _TEMPLATE_STUDIO_COLUMNS, _TEMPLATE_STUDIO_INDEXES)
    backfill = """
        UPDATE support_ticket_templates
        SET status = 'archived'
        WHERE is_active = FALSE AND status = 'active'
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(backfill))
        applied.append("backfill: inactive templates -> status='archived'")
    except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
        print(f"[support_desk.migrate] skipped template-status backfill ({exc})")
    return applied


def ensure_ticket_vendor_columns(engine) -> list[str]:
    """Add the pending-vendor lifecycle columns + index. Idempotent + guarded.

    Each statement runs in its own transaction so one failure (e.g. SQLite in a
    test, or a permissions hiccup) can't block the rest or the app boot.
    """
    applied: list[str] = []
    for table, column, ddl in _ADDITIVE_COLUMNS:
        stmt = f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}'
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            applied.append(stmt)
        except Exception as exc:  # noqa: BLE001 — additive, never fatal
            print(f"[support_desk.migrate] skipped: {stmt} ({exc})")
    for name, table, expr in _INDEXES:
        stmt = f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({expr})'
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            applied.append(stmt)
        except Exception as exc:  # noqa: BLE001
            print(f"[support_desk.migrate] skipped: {stmt} ({exc})")
    return applied
