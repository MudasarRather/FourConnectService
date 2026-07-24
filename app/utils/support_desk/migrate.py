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


# ── Incident Management ("Fault Grid" / "Command Funnel" desks) — command roster +
# impact-detail columns. incident_commander/comms_lead/ops_lead = the MI response roster
# (distinct from assigned_agent_id); affected_services = named systems hit ([str] JSONB);
# incident_started_at/detected_at = the real disruption clock (can predate created_at);
# compliance/security/public_impact = the SEV1/SEV2 exposure flags. UUID columns land
# bare (the ORM declares the FKs) to dodge create-order issues.
_INCIDENT_COLUMNS = [
    ("support_tickets", "incident_commander_id", "UUID"),
    ("support_tickets", "comms_lead_id", "UUID"),
    ("support_tickets", "ops_lead_id", "UUID"),
    ("support_tickets", "affected_services", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("support_tickets", "incident_started_at", "TIMESTAMPTZ"),
    ("support_tickets", "incident_detected_at", "TIMESTAMPTZ"),
    ("support_tickets", "compliance_impact", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("support_tickets", "security_impact", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("support_tickets", "public_impact", "BOOLEAN NOT NULL DEFAULT FALSE"),
    # Parent/child incident linking (one level deep) — child rows roll up under a master.
    ("support_tickets", "parent_incident_id", "UUID"),
]

_INCIDENT_INDEXES = [
    # Speeds the roles-unassigned MI stat + the commander's "my incidents" lens.
    ("ix_support_tickets_incident_commander", "support_tickets", "incident_commander_id"),
    # Speeds the per-master child rollup + the children list.
    ("ix_support_tickets_parent_incident", "support_tickets", "parent_incident_id"),
]


# ── MI-candidate proposal workflow (ServiceNow "major incident candidate" parity) —
# an owner-tier agent proposes major status; a team lead / superuser confirms or
# declines with a note. Stamps clear on decision; history lives in activity rows.
_MI_PROPOSAL_COLUMNS = [
    ("support_tickets", "mi_proposed_at", "TIMESTAMPTZ"),
    ("support_tickets", "mi_proposed_by_id", "UUID"),
    ("support_tickets", "mi_proposal_note", "VARCHAR(500)"),
]

_MI_PROPOSAL_INDEXES = [
    # Speeds the mi_proposed flag lens + the admin docket count.
    ("ix_support_tickets_mi_proposed_at", "support_tickets", "mi_proposed_at"),
]


# ── Incident Timeline desks — milestone-pin columns on the activity stream ──
# is_milestone/pinned_by_id/pinned_at let a commander curate the key beats of an
# incident directly on `support_ticket_activities`. SQL-composable (milestones=1
# feed filter, pulse counts) and indexable — deliberately NOT a JSONB set on the
# ticket. The action btree also serves the `kinds` filter, the pulse category
# case-expr and build_phase_track's action scans.
_TIMELINE_MILESTONE_COLUMNS = [
    ("support_ticket_activities", "is_milestone", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("support_ticket_activities", "pinned_by_id", "UUID"),
    ("support_ticket_activities", "pinned_at", "TIMESTAMPTZ"),
]

_TIMELINE_MILESTONE_INDEXES = [
    ("ix_support_ticket_activities_action", "support_ticket_activities", "action"),
]


# ── RCA v2 (Root Cause Analysis desks) — structured RCA + review workflow columns ──
# rca_status = the review machine (filed|validated|returned|stale; NULL = no RCA yet;
# legacy rca_summary-only rows READ as 'filed' via rca_effective_status_expr and are
# eagerly stamped by the backfill below). rca_category mirrors the PIR's
# RootCauseCategory taxonomy at ticket level; rca_five_whys / rca_factors are the
# structured methodology (JSONB lists, ServiceNow parity). filed/reviewed stamps are
# bare UUIDs per the _INCIDENT_COLUMNS convention. rca_inherited_from_problem_id =
# provenance when a problem cascade stamps the RCA (never overwrites a live filing).
_RCA_V2_COLUMNS = [
    ("support_tickets", "rca_status", "VARCHAR(20)"),
    ("support_tickets", "rca_category", "VARCHAR(40)"),
    ("support_tickets", "rca_five_whys", "JSONB"),
    ("support_tickets", "rca_factors", "JSONB"),
    ("support_tickets", "rca_filed_at", "TIMESTAMPTZ"),
    ("support_tickets", "rca_filed_by_id", "UUID"),
    ("support_tickets", "rca_reviewed_at", "TIMESTAMPTZ"),
    ("support_tickets", "rca_reviewed_by_id", "UUID"),
    ("support_tickets", "rca_review_note", "VARCHAR(500)"),
    ("support_tickets", "rca_inherited_from_problem_id", "UUID"),
]

_RCA_V2_INDEXES = [
    # Speeds the RCA board lenses (owed/pending/returned/validated) + review docket.
    ("ix_support_tickets_rca_status", "support_tickets", "rca_status"),
    # Speeds cycle-time analytics + the board's filed_at sort.
    ("ix_support_tickets_rca_filed_at", "support_tickets", "rca_filed_at"),
]


# ── PIR v2 (Post-Incident Review desks) — parity-pack document columns ──
# metrics_snapshot = the FROZEN clock/impact record stamped at submit; retro registers
# (went_well/went_wrong/contributing_factors) + participants + review-meeting fields are
# the ServiceNow-grade review-document surfaces; revisions = append-only edit trail;
# distribution = the publish fan-out receipt. All additive; JSONB registers default '[]'.
_PIR_V2_COLUMNS = [
    ("support_incident_reports", "metrics_snapshot", "JSONB"),
    ("support_incident_reports", "contributing_factors", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("support_incident_reports", "went_well", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("support_incident_reports", "went_wrong", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("support_incident_reports", "participants", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("support_incident_reports", "review_meeting_at", "TIMESTAMPTZ"),
    ("support_incident_reports", "review_meeting_notes", "TEXT"),
    ("support_incident_reports", "revisions", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("support_incident_reports", "distribution", "JSONB"),
]

_PIR_V2_INDEXES = [
    # Speeds the Chrono Desk calendar's pir_review window scan.
    ("ix_support_incident_reports_review_meeting_at",
     "support_incident_reports", "review_meeting_at"),
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


def ensure_ticket_incident_columns(engine) -> list[str]:
    """Add the incident-command roster + impact-detail columns + index (Fault Grid /
    Command Funnel desks). Idempotent + guarded."""
    return _apply_additive(engine, _INCIDENT_COLUMNS, _INCIDENT_INDEXES)


def ensure_ticket_mi_proposal_columns(engine) -> list[str]:
    """Add the MI-candidate proposal columns + index (propose → confirm/decline
    workflow on the Major Incident desks). Idempotent + guarded."""
    return _apply_additive(engine, _MI_PROPOSAL_COLUMNS, _MI_PROPOSAL_INDEXES)


def ensure_incident_tasks_table(engine) -> list[str]:
    """Create the ``support_incident_tasks`` table (response playbooks / incident tasks
    on the Critical desks) if it doesn't exist yet. A whole NEW table, so the ORM DDL
    with ``checkfirst`` is the idempotent path — no ALTER dance. Guarded: a failure
    prints and returns empty, never blocks boot."""
    try:
        from app.models.support_desk.incident import SdIncidentTask
        SdIncidentTask.__table__.create(bind=engine, checkfirst=True)
        return ["CREATE TABLE IF NOT EXISTS support_incident_tasks (ORM create, checkfirst)"]
    except Exception as exc:  # noqa: BLE001 — additive, never fatal
        print(f"[support_desk.migrate] skipped: create support_incident_tasks ({exc})")
        return []


def ensure_timeline_milestone_columns(engine) -> list[str]:
    """Add the milestone-pin columns to `support_ticket_activities` + the action btree
    + a partial index over pinned rows (Incident Timeline desks). Idempotent + guarded."""
    applied = _apply_additive(engine, _TIMELINE_MILESTONE_COLUMNS, _TIMELINE_MILESTONE_INDEXES)
    partial = ("CREATE INDEX IF NOT EXISTS ix_sta_milestone_at "
               "ON support_ticket_activities (created_at) WHERE is_milestone")
    try:
        with engine.begin() as conn:
            conn.execute(text(partial))
        applied.append(partial)
    except Exception as exc:  # noqa: BLE001 — partial indexes are PG-only; never fatal
        print(f"[support_desk.migrate] skipped: {partial} ({exc})")
    return applied


def ensure_ticket_rca_v2_columns(engine) -> list[str]:
    """Add the RCA v2 structured-capture + review-workflow columns + indexes, then
    best-effort backfill legacy rows (rca_summary present, rca_status NULL):

    - older than 90 days  → 'validated' (system-grandfathered — keeps years of
      pre-review-era RCAs out of the fresh pending-review docket; rca_reviewed_by_id
      stays NULL so grandfathering is distinguishable from a human ruling),
    - within 90 days      → 'filed' (recent filings enter the review lane),
    - rca_filed_at        → recovered from each ticket's newest 'rca_recorded'
      activity row where missing.

    If the JSONB variant fails (non-Postgres test engine) the two structured columns
    retry as TEXT so the attribute always exists. Idempotent + guarded."""
    applied = _apply_additive(engine, _RCA_V2_COLUMNS, _RCA_V2_INDEXES)
    if not any("rca_five_whys" in s for s in applied):
        applied += _apply_additive(
            engine,
            [("support_tickets", "rca_five_whys", "TEXT"),
             ("support_tickets", "rca_factors", "TEXT")],
            [],
        )
    backfills = [
        ("grandfather >90d → validated", """
            UPDATE support_tickets
            SET rca_status = 'validated'
            WHERE rca_status IS NULL
              AND rca_summary IS NOT NULL AND btrim(rca_summary) <> ''
              AND COALESCE(resolved_at, closed_at, created_at) < now() - interval '90 days'
        """),
        ("recent ≤90d → filed", """
            UPDATE support_tickets
            SET rca_status = 'filed'
            WHERE rca_status IS NULL
              AND rca_summary IS NOT NULL AND btrim(rca_summary) <> ''
        """),
        ("rca_filed_at from rca_recorded activities", """
            UPDATE support_tickets t SET rca_filed_at = a.created_at
            FROM (
                SELECT DISTINCT ON (ticket_id) ticket_id, created_at
                FROM support_ticket_activities
                WHERE action = 'rca_recorded'
                ORDER BY ticket_id, created_at DESC
            ) a
            WHERE a.ticket_id = t.id AND t.rca_status IS NOT NULL AND t.rca_filed_at IS NULL
        """),
    ]
    for label, stmt in backfills:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            applied.append(f"backfill: {label}")
        except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
            print(f"[support_desk.migrate] skipped RCA backfill ({label}): {exc}")
    return applied


def ensure_pir_v2_columns(engine) -> list[str]:
    """Add the PIR v2 parity-pack columns + review-meeting index, then backfill a stable
    ``aid`` onto every existing corrective/preventive action item that lacks one (an
    8-hex address derived from md5(pir id · kind · position) so the backfill is
    deterministic and idempotent). If the JSONB variants fail (non-Postgres test
    engine) the register columns retry as TEXT so the attribute always exists."""
    applied = _apply_additive(engine, _PIR_V2_COLUMNS, _PIR_V2_INDEXES)
    if not any("went_well" in s for s in applied):
        applied += _apply_additive(
            engine,
            [("support_incident_reports", "metrics_snapshot", "TEXT"),
             ("support_incident_reports", "contributing_factors", "TEXT"),
             ("support_incident_reports", "went_well", "TEXT"),
             ("support_incident_reports", "went_wrong", "TEXT"),
             ("support_incident_reports", "participants", "TEXT"),
             ("support_incident_reports", "revisions", "TEXT"),
             ("support_incident_reports", "distribution", "TEXT")],
            [],
        )
    backfills = [
        ("stable aid on corrective actions", """
            UPDATE support_incident_reports SET corrective_actions = (
                SELECT COALESCE(jsonb_agg(
                    CASE WHEN jsonb_typeof(e.item) = 'object' AND (e.item->>'aid') IS NULL
                         THEN e.item || jsonb_build_object(
                             'aid', left(md5(id::text || 'corrective' || (e.ord - 1)::text), 8))
                         ELSE e.item END ORDER BY e.ord), '[]'::jsonb)
                FROM jsonb_array_elements(corrective_actions) WITH ORDINALITY AS e(item, ord))
            WHERE jsonb_typeof(corrective_actions) = 'array'
              AND jsonb_array_length(corrective_actions) > 0
              AND EXISTS (SELECT 1 FROM jsonb_array_elements(corrective_actions) x
                          WHERE jsonb_typeof(x) = 'object' AND (x->>'aid') IS NULL)
        """),
        ("stable aid on preventive actions", """
            UPDATE support_incident_reports SET preventive_actions = (
                SELECT COALESCE(jsonb_agg(
                    CASE WHEN jsonb_typeof(e.item) = 'object' AND (e.item->>'aid') IS NULL
                         THEN e.item || jsonb_build_object(
                             'aid', left(md5(id::text || 'preventive' || (e.ord - 1)::text), 8))
                         ELSE e.item END ORDER BY e.ord), '[]'::jsonb)
                FROM jsonb_array_elements(preventive_actions) WITH ORDINALITY AS e(item, ord))
            WHERE jsonb_typeof(preventive_actions) = 'array'
              AND jsonb_array_length(preventive_actions) > 0
              AND EXISTS (SELECT 1 FROM jsonb_array_elements(preventive_actions) x
                          WHERE jsonb_typeof(x) = 'object' AND (x->>'aid') IS NULL)
        """),
    ]
    for label, stmt in backfills:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            applied.append(f"backfill: {label}")
        except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
            print(f"[support_desk.migrate] skipped PIR backfill ({label}): {exc}")
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
