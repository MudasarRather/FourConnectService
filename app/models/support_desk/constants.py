"""Support Desk — status / priority / type vocabularies.

Stored as plain String columns (NOT Postgres enum types) to avoid enum-type
drift on `create_all` and the ALTER TYPE dance when values are added later. These
str-Enums are the canonical contract; Pydantic validators enforce them at the API
boundary, and routers gate status transitions explicitly.
"""
from __future__ import annotations

import enum


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PENDING_CUSTOMER = "pending_customer"
    PENDING_VENDOR = "pending_vendor"
    ON_HOLD = "on_hold"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


# Statuses that count as "still open" for SLA clocks and dashboards.
# NOTE: ON_HOLD is deliberately NOT here — it is a paused/holding state shown on its
# own board; the SLA-pause clock arrives in a later phase, so we keep it out of the
# "open" set so its breach clock doesn't keep running while held.
OPEN_TICKET_STATUSES = {
    TicketStatus.OPEN.value,
    TicketStatus.IN_PROGRESS.value,
    TicketStatus.PENDING_CUSTOMER.value,
    TicketStatus.PENDING_VENDOR.value,
    TicketStatus.ESCALATED.value,
}
TERMINAL_TICKET_STATUSES = {TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value}
# Statuses an agent can put a ticket into where it's "parked" (no SLA progress expected).
HOLD_TICKET_STATUSES = {TicketStatus.ON_HOLD.value}

# Statuses that STOP the SLA clock ("stop the clock" — ServiceNow-style pause conditions).
# Time a ticket spends in any of these is NOT counted toward its response/resolution
# deadlines: on entry the clock freezes, on exit the deadlines are pushed out by the paused
# duration. Waiting-on-customer and On-Hold pause by universal convention; waiting-on-vendor
# pauses too by default (a third-party delay is outside the desk's control) — to make vendor
# time COUNT instead, remove PENDING_VENDOR from this set.
SLA_PAUSE_STATUSES = {
    TicketStatus.PENDING_CUSTOMER.value,
    TicketStatus.PENDING_VENDOR.value,
    TicketStatus.ON_HOLD.value,
}


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"


PRIORITY_ORDER = ["low", "medium", "high", "urgent", "critical"]


class TicketType(str, enum.Enum):
    INCIDENT = "incident"
    SERVICE_REQUEST = "service_request"
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    COMPLAINT = "complaint"
    CHANGE = "change"
    PROBLEM = "problem"
    TRAINING = "training"
    IMPLEMENTATION = "implementation"


class TicketSource(str, enum.Enum):
    PORTAL = "portal"
    EMAIL = "email"
    PHONE = "phone"
    CHAT = "chat"
    WHATSAPP = "whatsapp"
    API = "api"
    MONITORING = "monitoring"
    INTERNAL = "internal"


class ImpactUrgency(str, enum.Enum):
    """Shared scale for the ITIL impact + urgency fields (drives priority guidance)."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResolutionCode(str, enum.Enum):
    SOLVED = "solved"
    WORKAROUND = "workaround"
    NO_FAULT_FOUND = "no_fault_found"
    DUPLICATE = "duplicate"
    NOT_REPRODUCIBLE = "not_reproducible"
    CONFIGURATION = "configuration"
    KNOWN_ERROR = "known_error"
    CANCELLED = "cancelled"
    # Auto-resolved by the pending-customer sweep when a requester went silent past the
    # auto-close window (Zendesk "closed for inactivity" / ServiceNow "resolved by inactivity").
    NO_RESPONSE = "no_response"


class RootCauseCategory(str, enum.Enum):
    HARDWARE = "hardware"
    SOFTWARE = "software"
    NETWORK = "network"
    USER_ERROR = "user_error"
    VENDOR = "vendor"
    CONFIGURATION = "configuration"
    OTHER = "other"


class CommentAuthorKind(str, enum.Enum):
    STAFF = "staff"
    CUSTOMER = "customer"
    SYSTEM = "system"
    # A third-party vendor communication logged on the ticket (Zendesk-style side
    # conversation). Always stored is_internal=True → NEVER surfaces on the client portal.
    VENDOR = "vendor"


class ChangeStatus(str, enum.Enum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    IMPLEMENTED = "implemented"
    CLOSED = "closed"
    REJECTED = "rejected"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProblemStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    KNOWN_ERROR = "known_error"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ServiceRequestStatus(str, enum.Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLOSED = "closed"
    REJECTED = "rejected"


class ArticleVisibility(str, enum.Enum):
    PUBLIC = "public"
    CUSTOMER = "customer"
    INTERNAL = "internal"


class ArticleStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AnnouncementAudience(str, enum.Enum):
    ALL = "all"
    ORGANIZATION = "organization"
    CONTRACT = "contract"
    USERS = "users"


# Module key consumed by app.utils.hr.numbering.next_number()
NUMBERING_MODULE_TICKET = "SUPPORT_TICKET"
NUMBERING_MODULE_CHANGE = "SUPPORT_CHANGE"
NUMBERING_MODULE_PROBLEM = "SUPPORT_PROBLEM"
NUMBERING_MODULE_SERVICE_REQ = "SUPPORT_SERVICE_REQUEST"

# ── Resolved → auto-close: a resolved ticket stays reopenable for this many days,
#    then auto-closes (ServiceNow/Zendesk "resolved → closed" window). ──
SUPPORT_RESOLVED_AUTOCLOSE_DAYS = 3

# ── Pending-customer silence → auto-resolve: a ticket that has been awaiting a customer
#    reply for this many days (measured from when it entered the pause / the last customer
#    reply) is auto-resolved with resolution_code=no_response. From PENDING_WARN_DAYS onward a
#    daily "we'll close this soon" nudge goes to the requester first. Mirrors Zendesk's
#    "pending → solved after N days of no reply" and gives a 3-day (7−4) warning runway.
PENDING_AUTO_CLOSE_DAYS = 7
PENDING_WARN_DAYS = 4

# ── Pending-vendor: structured hand-off reasons (ServiceNow "hold reason" codes). The
#    canonical set the desk records for WHY a ticket is blocked on a third party. ──
class VendorWaitReason(str, enum.Enum):
    AWAITING_QUOTE = "awaiting_quote"
    AWAITING_PARTS = "awaiting_parts"
    AWAITING_RMA = "awaiting_rma"
    AWAITING_FIX = "awaiting_fix"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INFO = "awaiting_info"
    OTHER = "other"


VENDOR_WAIT_REASONS = [r.value for r in VendorWaitReason]

# When a ticket has a vendor ETA (vendor_due_at) but no explicit one is set, the sweep
# treats a hand-off as "at risk / overdue" using this default runway from dispatch.
VENDOR_DEFAULT_SLA_DAYS = 3

# ── On-Hold: structured hold reasons (ServiceNow "on hold reason" codes). The canonical
#    taxonomy the desk records for WHY a ticket is deliberately parked. hold_reason stays
#    the free-text detail; hold_reason_code is the coded category driving analytics. ──
class HoldReason(str, enum.Enum):
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_CHANGE = "awaiting_change"
    AWAITING_PARTS = "awaiting_parts"
    AWAITING_THIRD_PARTY = "awaiting_third_party"
    CUSTOMER_REQUESTED = "customer_requested"
    INTERNAL_REVIEW = "internal_review"
    SCHEDULED_MAINTENANCE = "scheduled_maintenance"
    LEGAL_COMPLIANCE = "legal_compliance"
    OTHER = "other"


HOLD_REASON_CODES = [r.value for r in HoldReason]


class SkipReason(str, enum.Enum):
    """Play-mode skip reasons (Zendesk guided-mode skip audit)."""
    NOT_MY_SKILL = "not_my_skill"
    NEED_INFO = "need_info"
    DUPLICATE_SUSPECT = "duplicate_suspect"
    BLOCKED = "blocked"
    OTHER = "other"


SKIP_REASON_CODES = [r.value for r in SkipReason]

# Tier de-escalation (send-back) reasons — L2→L1 / L3→L2 returns are reason-coded so
# the receiving tier knows WHY the ticket came back.
class TierDescendReason(str, enum.Enum):
    RESOLVED_AT_TIER = "resolved_at_tier"
    MISROUTED = "misrouted"
    NEEDS_BASIC_TROUBLESHOOTING = "needs_basic_troubleshooting"
    CUSTOMER_ACTION_DONE = "customer_action_done"
    OTHER = "other"


TIER_DESCEND_REASON_CODES = [r.value for r in TierDescendReason]

# A ticket on hold this many days without a hold review (extend/re-confirm) is flagged
# STALE — surfaced on the On-Hold board and nudged by the stale-hold sweep. Holds with a
# hold_until release date auto-resume via the expiry sweep instead.
STALE_HOLD_DAYS = 7

# ── Escalation ("Thermal Updraft" desk): structured escalation vocabulary. ──
# escalation_type distinguishes ITIL's two escalation directions; escalation_reason_code is
# the coded taxonomy driving analytics (escalation_reason stays the free-text detail).
class EscalationType(str, enum.Enum):
    HIERARCHICAL = "hierarchical"   # up the tiers — same team, higher authority
    FUNCTIONAL = "functional"       # sideways — to a specialist team


ESCALATION_TYPES = [t.value for t in EscalationType]


class EscalationReason(str, enum.Enum):
    SLA_RISK = "sla_risk"                 # about to breach
    SLA_BREACH = "sla_breach"             # already breached (the auto-sweep uses this)
    CUSTOMER_REQUEST = "customer_request"
    COMPLEXITY = "complexity"
    EXPERTISE = "expertise"               # needs a specialist (functional)
    VENDOR_STALL = "vendor_stall"         # third party overdue (vendor sweep uses this)
    REPEAT_INCIDENT = "repeat_incident"
    VIP = "vip"
    MAJOR_INCIDENT = "major_incident"
    OTHER = "other"


ESCALATION_REASON_CODES = [r.value for r in EscalationReason]

# Escalation response clock — minutes the receiving tier has to ACKNOWLEDGE an escalation,
# by ticket priority. Per-priority beats per-level: the level counter is unbounded and
# priority already encodes urgency. Overridable per-escalation via TicketEscalate.response_minutes.
ESCALATION_RESPONSE_MINUTES = {
    "critical": 30, "urgent": 60, "high": 120, "medium": 240, "low": 480,
}
ESCALATION_RESPONSE_DEFAULT_MINUTES = 240

# Which SLA-breach kinds AUTO-escalate (the breach sweep). Resolution-only by default —
# response breaches are frequent and would flood the escalated desk; they surface as the
# "breach candidates" stat/lens for manual escalation instead.
AUTO_ESCALATE_BREACH_KINDS = {"resolution"}

# ── Reopened ("Möbius Loop" desk): structured reopen vocabulary. ──
# reopen_source records WHO put the ticket back on the desk; reopen_reason_code is the
# coded verdict on the failed fix (reopen_reason stays the free-text detail).
class ReopenSource(str, enum.Enum):
    REQUESTER = "requester"   # the requester's self-service Reopen action
    AGENT = "agent"           # an agent/console reopen (incl. bulk + set-status paths)
    PORTAL = "portal"         # customer replied to a RESOLVED ticket → auto-reopen
    AUTO = "auto"             # reserved for future automation rules (no writer yet)


REOPEN_SOURCES = [s.value for s in ReopenSource]


class ReopenReason(str, enum.Enum):
    NOT_FIXED = "not_fixed"                     # the fix never worked
    RECURRED = "recurred"                       # worked, then the issue came back
    PARTIAL_FIX = "partial_fix"                 # some of it fixed, some not
    WRONG_RESOLUTION = "wrong_resolution"       # solved the wrong thing
    PREMATURE_CLOSURE = "premature_closure"     # resolved before it was actually done
    NEW_INFO = "new_info"                       # customer supplied new information
    CUSTOMER_UNSATISFIED = "customer_unsatisfied"
    FOLLOW_UP = "follow_up"                     # related follow-up work on the same case
    OTHER = "other"


REOPEN_REASON_CODES = [r.value for r in ReopenReason]

# A ticket reopened this many times (or more) is CHRONIC — the Reopened desk's
# repeat-offender flag (drives the chronic lens/rail + guided-run ranking).
CHRONIC_REOPEN_THRESHOLD = 2


# ── Team Ops desk: first-class agent→agent HANDOFF taxonomy (ServiceNow/Zendesk-style
#    audited transfer — the coded reason powers the desk's rebalance analytics; the
#    free-text note stays in the "handoff" activity detail). ──
class HandoffReason(str, enum.Enum):
    WORKLOAD_BALANCE = "workload_balance"       # spreading an overloaded queue
    EXPERTISE = "expertise"                     # teammate owns this skill/domain
    AVAILABILITY = "availability"               # owner OOO / off-shift / on leave
    SHIFT_CHANGE = "shift_change"               # end-of-shift handover
    CUSTOMER_REQUEST = "customer_request"       # requester asked for someone else
    ESCALATION_PREP = "escalation_prep"         # staging for a tier lift
    OTHER = "other"


HANDOFF_REASON_CODES = [r.value for r in HandoffReason]

# Team Ops "idle" threshold — an active ticket with no update for this many hours is
# flagged stale on the team queue (drives the Idle lens + per-agent idle counts).
TEAM_IDLE_HOURS = 24
# Team Ops "due soon" horizon — resolution due within this many hours (clock running).
TEAM_DUE_SOON_HOURS = 4


# ── Archived ("Deep Storage" desk): archive = the is_deleted soft-delete flag, orthogonal
#    to status. archive_reason_code is the coded taxonomy driving the desk's analytics
#    (the free-text reason stays in the "archived" activity detail). ──
class ArchiveReason(str, enum.Enum):
    SPAM = "spam"
    DUPLICATE = "duplicate"
    CREATED_IN_ERROR = "created_in_error"
    TEST_TICKET = "test_ticket"
    RESOLVED_OFF_PLATFORM = "resolved_off_platform"
    OBSOLETE = "obsolete"
    COMPLIANCE = "compliance"
    AUTO_RETENTION = "auto_retention"   # ONLY the closed→archived retention sweep writes this
    OTHER = "other"


ARCHIVE_REASON_CODES = [r.value for r in ArchiveReason]

# Retention windows (ServiceNow/Zendesk-style record lifecycle):
#   closed  --SUPPORT_CLOSED_AUTOARCHIVE_DAYS-->  archived (auto_retention, legal-hold exempt)
#   archived --SUPPORT_ARCHIVE_RETENTION_DAYS-->  purge-ELIGIBLE (superuser purge only; never automatic)
# EXPIRING_SOON flags records within this many days of purge eligibility (retention rail).
SUPPORT_CLOSED_AUTOARCHIVE_DAYS = 120
SUPPORT_ARCHIVE_RETENTION_DAYS = 180
SUPPORT_ARCHIVE_EXPIRING_SOON_DAYS = 14


# ── Impact × Urgency → Priority matrix (ITIL; mirrors the standard P1–P4 grid).
#    Both axes use the 4-level scale low|medium|high|critical. The derived priority
#    is one of our priorities (critical=P1, high=P2, medium=P3, low=P4). ──
IMPACT_URGENCY_MATRIX = {
    # urgency → { impact → priority }
    "critical": {"critical": "critical", "high": "critical", "medium": "high",   "low": "medium"},
    "high":     {"critical": "critical", "high": "high",     "medium": "medium", "low": "low"},
    "medium":   {"critical": "high",     "high": "high",     "medium": "medium", "low": "low"},
    "low":      {"critical": "medium",   "high": "medium",   "medium": "low",    "low": "low"},
}


def priority_from_matrix(impact, urgency):
    """Derive a ticket priority from impact × urgency, or None if either is unset/unknown."""
    if not impact or not urgency:
        return None
    return IMPACT_URGENCY_MATRIX.get(str(urgency), {}).get(str(impact))


# ── Incident Management ("Fault Grid" / "Command Funnel" desks) ──
# SEV1–SEV4 is a DERIVED classification, never a stored column — a 4th severity axis
# would drift against priority / impact×urgency / business_impact. The single source
# of truth is app.utils.support_desk.incidents.ticket_sev():
#   SEV1 = is_major_incident · SEV2 = priority critical (non-MI) · SEV3 = high · SEV4 = medium|low
SEV_FROM_PRIORITY = {"critical": 2, "urgent": 3, "high": 3, "medium": 4, "low": 4}


class PirStatus(str, enum.Enum):
    """Post-Incident Report lifecycle: draft → in_review → approved → published.
    Reject sends an in_review report back to draft (with the reviewer's note)."""
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    PUBLISHED = "published"


PIR_STATUSES = [s.value for s in PirStatus]

# Module key consumed by app.utils.hr.numbering.next_number()
NUMBERING_MODULE_PIR = "SUPPORT_PIR"

# A terminal SEV1 / major incident older than this many days without a PIR draft is
# flagged by the pir-missing sweep (nudges the commander/owner, 24h-throttled).
PIR_REQUIRED_AFTER_DAYS = 3


class IncidentRole(str, enum.Enum):
    """MI command roster (ServiceNow MIM / PagerDuty response-roles parity)."""
    COMMANDER = "commander"      # incident_commander_id — owns the response
    COMMS_LEAD = "comms_lead"    # comms_lead_id — owns stakeholder updates
    OPS_LEAD = "ops_lead"        # ops_lead_id — owns the technical bridge


class DecisionKind(str, enum.Enum):
    """Decision-log taxonomy — command decisions recorded as immutable activity rows
    (action='decision_logged'). DR/failover/BCP invocations are RECORDED here, not
    automated (there is no infra-automation seam in this stack)."""
    MITIGATION = "mitigation"
    ESCALATE_EXECUTIVE = "escalate_executive"
    FAILOVER = "failover"
    ACTIVATE_DR = "activate_dr"
    INVOKE_BCP = "invoke_bcp"
    ROLLBACK = "rollback"
    VENDOR_ENGAGED = "vendor_engaged"
    COMMS = "comms"
    STAND_DOWN = "stand_down"
    OTHER = "other"


DECISION_KINDS = [k.value for k in DecisionKind]


# ── Response playbooks / incident tasks ("Fault Grid" / "Command Funnel" desks) ──
# An incident TASK is one check-off row on a live response (table support_incident_tasks).
# Statuses: open → done (stamps done_at/by) | open → skipped (the tombstone — there is
# no DELETE; a skipped row keeps the paper trail). done→open is an audited correction.
INCIDENT_TASK_STATUSES = ("open", "done", "skipped")

# Curated response playbooks (ServiceNow playbook parity). SNAPSHOT-ON-APPLY: applying a
# playbook copies its task titles into support_incident_tasks rows stamped with the
# template_key — later edits to this dict never rewrite history already on a ticket.
# Keys are frozen API contract (GET /incidents/playbooks + apply-template payloads).
INCIDENT_PLAYBOOKS = {
    "sev1_bridge": {
        "label": "SEV1 Bridge Standup",
        "description": "Stand up the full major-incident command structure: ownership, "
                       "war room, roster, cadence, impact record and the first mitigation call.",
        "tasks": [
            "Acknowledge the incident — a named responder owns eyes on it",
            "Assign the incident commander",
            "Open the war-room bridge and post the join link on the ticket",
            "Staff the comms lead and ops lead seats",
            "Arm the stakeholder update cadence (30 minutes or tighter)",
            "Record the impact assessment: affected services, users, business impact",
            "Identify the last known-good change and evaluate rollback",
            "Log the first mitigation decision in the decision log",
        ],
    },
    "sev2_response": {
        "label": "SEV2 First Response",
        "description": "The first-hour drill for a critical (SEV2) incident: own it, size it, "
                       "check precedent, communicate, and decide the escalation question early.",
        "tasks": [
            "Acknowledge the incident and confirm ownership",
            "Confirm severity against impact and urgency — is this really SEV2?",
            "Record affected services and the affected-user count",
            "Check similar past incidents for a known fix or workaround",
            "Post the first internal status note with what is known so far",
            "Decide: mitigate at this tier or propose the major-incident desk",
        ],
    },
    "security_exposure": {
        "label": "Security Exposure Response",
        "description": "Containment-first drill when an incident carries security exposure: "
                       "isolate, preserve evidence, notify the right owners, assess the blast radius.",
        "tasks": [
            "Flag security impact on the incident record",
            "Contain: isolate affected systems or revoke exposed credentials",
            "Preserve logs and evidence BEFORE remediation changes them",
            "Notify the security owner and the compliance stakeholder",
            "Assess data exposure: what data, whose, and how much",
            "Log the containment decision in the decision log",
            "Book the post-incident review with security in the room",
        ],
    },
    "public_comms": {
        "label": "Public Communications",
        "description": "Outward-facing incident comms: a staffed comms seat, an approved holding "
                       "statement, an honoured cadence, and a clean all-clear.",
        "tasks": [
            "Flag public impact on the incident record",
            "Staff the comms lead seat",
            "Draft the holding statement and get it approved",
            "Publish the first customer-facing status update",
            "Arm a stakeholder update cadence — and honour it",
            "Log every outbound statement on the comms trail",
            "Publish the all-clear and close the loop with stakeholders",
        ],
    },
}


# Notification events (consumed by app.utils.hr.notify.dispatch())
EVT_TICKET_CREATED = "SUPPORT_TICKET_CREATED"
EVT_TICKET_ASSIGNED = "SUPPORT_TICKET_ASSIGNED"
EVT_TICKET_REPLIED = "SUPPORT_TICKET_REPLIED"
EVT_TICKET_STATUS = "SUPPORT_TICKET_STATUS_CHANGED"
EVT_TICKET_ESCALATED = "SUPPORT_TICKET_ESCALATED"
EVT_TICKET_RESOLVED = "SUPPORT_TICKET_RESOLVED"
EVT_TICKET_SLA_BREACH = "SUPPORT_TICKET_SLA_BREACHED"
EVT_TICKET_MERGED = "SUPPORT_TICKET_MERGED"
EVT_TICKET_REOPENED = "SUPPORT_TICKET_REOPENED"
EVT_TICKET_ARCHIVED = "SUPPORT_TICKET_ARCHIVED"
EVT_TICKET_RESTORED = "SUPPORT_TICKET_RESTORED"

# Team roster events (Team Command admin desk — membership changes must not be silent)
EVT_TEAM_MEMBER_ADDED = "SUPPORT_TEAM_MEMBER_ADDED"
EVT_TEAM_MEMBER_REMOVED = "SUPPORT_TEAM_MEMBER_REMOVED"
EVT_TEAM_LEAD_ASSIGNED = "SUPPORT_TEAM_LEAD_ASSIGNED"

# Incident Management events (Fault Grid / Command Funnel desks)
EVT_INCIDENT_ROLES_ASSIGNED = "SUPPORT_INCIDENT_ROLES_ASSIGNED"
EVT_INCIDENT_DECISION = "SUPPORT_INCIDENT_DECISION_LOGGED"
EVT_INCIDENT_IMPACT = "SUPPORT_INCIDENT_IMPACT_STAMPED"
EVT_INCIDENT_CADENCE = "SUPPORT_INCIDENT_CADENCE_CHANGED"
EVT_PIR_SUBMITTED = "SUPPORT_PIR_SUBMITTED"
EVT_PIR_APPROVED = "SUPPORT_PIR_APPROVED"
EVT_PIR_REJECTED = "SUPPORT_PIR_REJECTED"
EVT_PIR_PUBLISHED = "SUPPORT_PIR_PUBLISHED"
EVT_PIR_OVERDUE = "SUPPORT_PIR_OVERDUE"

# Major-incident command extensions (proposal workflow / stakeholder broadcast /
# PIR action-item tracker). Declared MI keeps EVT_TICKET_ESCALATED for back-compat;
# EVT_INCIDENT_DECLARED is the incident-specific signal layered on top.
EVT_INCIDENT_DECLARED = "SUPPORT_INCIDENT_DECLARED"
EVT_INCIDENT_MI_PROPOSED = "SUPPORT_INCIDENT_MI_PROPOSED"
EVT_INCIDENT_MI_DECLINED = "SUPPORT_INCIDENT_MI_DECLINED"
EVT_INCIDENT_STATUS_UPDATE = "SUPPORT_INCIDENT_STATUS_UPDATE"
EVT_PIR_ACTION_UPDATED = "SUPPORT_PIR_ACTION_UPDATED"
EVT_PIR_ACTION_OVERDUE = "SUPPORT_PIR_ACTION_OVERDUE"

# Critical-desk extensions (response playbooks + severity reclassification).
# EVT_INCIDENT_TASK_ASSIGNED is a personal ping (a named person got a job) — deliberately
# NOT webhook-mirrored; EVT_INCIDENT_SEV_CHANGED is command-relevant and rides the uplink.
EVT_INCIDENT_TASK_ASSIGNED = "SUPPORT_INCIDENT_TASK_ASSIGNED"
EVT_INCIDENT_SEV_CHANGED = "SUPPORT_INCIDENT_SEV_CHANGED"

# RCA v2 (RCA desks). FILED fans to the team leads for review and rides the uplink;
# VALIDATED closes the loop to the filer and rides the uplink; RETURNED is a personal
# "your filing came back" ping — deliberately NOT webhook-mirrored (task-assigned precedent).
EVT_RCA_FILED = "SUPPORT_RCA_FILED"
EVT_RCA_VALIDATED = "SUPPORT_RCA_VALIDATED"
EVT_RCA_RETURNED = "SUPPORT_RCA_RETURNED"


# ═══════════════════════ Incident Timeline — event taxonomy ═══════════════════════
# The catalog is the READ-SIDE registry over `support_ticket_activities.action`:
# label/category/tone drive the timeline desks' chips and rendering, `milestone`
# marks the actions a commander may pin, `system` marks sweep/automation-only
# writers. It is NEVER a write-side constraint — `_log_activity` stays free —
# but every NEW writer must register its action here or its events render with
# TIMELINE_DEFAULT_META and its kind can't be filtered (the timeline `kinds`
# param 422-validates against these keys).
TIMELINE_CATEGORIES = ("lifecycle", "command", "comms", "sla", "governance", "system")


def _tl(label: str, category: str, tone: str, milestone: bool = False,
        system: bool = False) -> dict:
    return {"label": label, "category": category, "tone": tone,
            "milestone": milestone, "system": system}


ACTIVITY_CATALOG: dict[str, dict] = {
    # ── lifecycle ──
    "created": _tl("Fault raised", "lifecycle", "amber", milestone=True),
    "updated": _tl("Record updated", "lifecycle", "dim"),
    "status_changed": _tl("Status moved", "lifecycle", "dim", milestone=True),
    "resolved": _tl("Resolved", "lifecycle", "live", milestone=True),
    "reopened": _tl("Reopened", "lifecycle", "arc", milestone=True),
    "restored": _tl("Restored from archive", "lifecycle", "live"),
    "archived": _tl("Archived", "lifecycle", "dim"),
    "merged": _tl("Merged", "lifecycle", "dim"),
    "withdrawn": _tl("Withdrawn", "lifecycle", "dim"),
    "follow_up_created": _tl("Follow-up opened", "lifecycle", "hi"),
    "parent_incident_resolved": _tl("Master incident resolved", "lifecycle", "live"),
    "requester_changed": _tl("Requester changed", "lifecycle", "dim"),
    # ── command ──
    "major_incident": _tl("MI declared", "command", "arc", milestone=True),
    "mi_proposed": _tl("MI proposed", "command", "warn"),
    "mi_confirmed": _tl("MI confirmed", "command", "arc", milestone=True),
    "mi_declined": _tl("MI declined", "command", "dim"),
    "mi_withdrawn": _tl("MI proposal withdrawn", "command", "dim"),
    "incident_roles_set": _tl("Roster staffed", "command", "amber", milestone=True),
    "incident_impact_set": _tl("Impact stamped", "command", "warn", milestone=True),
    "decision_logged": _tl("Decision logged", "command", "warn", milestone=True),
    "incident_sev_changed": _tl("SEV reclassified", "command", "warn", milestone=True),
    "incident_linked": _tl("Linked to master", "command", "hi", milestone=True),
    "incident_unlinked": _tl("Unlinked from master", "command", "dim"),
    "child_incident_linked": _tl("Child incident coupled", "command", "hi"),
    "task_added": _tl("Task added", "command", "dim"),
    "task_status": _tl("Task moved", "command", "dim"),
    "task_assigned": _tl("Task assigned", "command", "dim"),
    "playbook_applied": _tl("Playbook applied", "command", "amber", milestone=True),
    "swarm_started": _tl("Swarm started", "command", "amber"),
    "swarm_joined": _tl("Swarm joined", "command", "dim"),
    "swarm_ended": _tl("Swarm ended", "command", "dim"),
    "acknowledged": _tl("Acknowledged", "command", "live", milestone=True),
    "escalated": _tl("Escalated", "command", "warn", milestone=True),
    "de_escalated": _tl("De-escalated", "command", "dim", milestone=True),
    "escalation_acknowledged": _tl("Escalation acknowledged", "command", "live"),
    "tier_moved": _tl("Tier moved", "command", "warn"),
    "handoff": _tl("Handed off", "command", "warn"),
    "assigned": _tl("Assigned", "command", "amber"),
    "unassigned": _tl("Unassigned", "command", "dim"),
    "routed": _tl("Queue-routed", "command", "dim"),
    "skipped": _tl("Skipped in queue", "command", "dim"),
    "collaborator_added": _tl("Collaborator added", "command", "dim"),
    "collaborator_removed": _tl("Collaborator removed", "command", "dim"),
    "problem_cascade_resolved": _tl("Problem cascade resolve", "command", "live"),
    # ── comms ──
    "replied": _tl("Customer reply", "comms", "hi"),
    "internal_note": _tl("Internal note", "comms", "dim"),
    "status_update": _tl("Status update posted", "comms", "amber", milestone=True),
    "comment_redacted": _tl("Comment redacted", "comms", "dim"),
    "reminded": _tl("Reminder fired", "comms", "dim"),
    "owner_nudge": _tl("Owner nudged", "comms", "warn"),
    "watcher_added": _tl("Watcher added", "comms", "dim"),
    "watcher_removed": _tl("Watcher removed", "comms", "dim"),
    "csat": _tl("CSAT received", "comms", "hi"),
    "template_run": _tl("Template applied", "comms", "dim"),
    # ── sla ──
    "sla_breached": _tl("SLA breached", "sla", "arc", milestone=True, system=True),
    "sla_reclassed": _tl("SLA reclassified", "sla", "warn", system=True),
    "update_overdue": _tl("Update cadence overdue", "sla", "arc", system=True),
    "escalation_response_overdue": _tl("Escalation response overdue", "sla", "arc", system=True),
    "vendor_overdue": _tl("Vendor overdue", "sla", "arc", system=True),
    "hold_review_due": _tl("Hold review due", "sla", "warn", system=True),
    "hold_extended": _tl("Hold extended", "sla", "dim"),
    "vendor_dispatched": _tl("Vendor dispatched", "sla", "warn"),
    "vendor_chased": _tl("Vendor chased", "sla", "warn"),
    "vendor_replied": _tl("Vendor replied", "sla", "live"),
    "time_logged": _tl("Worklog added", "sla", "dim"),
    "time_log_removed": _tl("Worklog removed", "sla", "dim"),
    # ── governance ──
    "pir_created": _tl("PIR opened", "governance", "hi"),
    "pir_updated": _tl("PIR updated", "governance", "dim"),
    "pir_submitted": _tl("PIR submitted", "governance", "hi"),
    "pir_approved": _tl("PIR approved", "governance", "live"),
    "pir_rejected": _tl("PIR rejected", "governance", "arc"),
    "pir_published": _tl("PIR published", "governance", "hi", milestone=True),
    "pir_overdue": _tl("PIR overdue", "governance", "arc", system=True),
    "pir_action_status": _tl("PIR action moved", "governance", "dim"),
    "pir_action_overdue": _tl("PIR action overdue", "governance", "arc", system=True),
    "pir_meeting_set": _tl("PIR review scheduled", "governance", "hi"),
    "rca_recorded": _tl("RCA recorded", "governance", "hi", milestone=True),
    "rca_revised": _tl("RCA revised", "governance", "dim"),
    "rca_validated": _tl("RCA validated", "governance", "live", milestone=True),
    "rca_returned": _tl("RCA returned", "governance", "warn"),
    "rca_invalidated": _tl("RCA gone stale", "governance", "warn", system=True),
    "rca_inherited": _tl("RCA inherited from problem", "governance", "hi"),
    "cluster_promoted": _tl("Recurrence promoted to problem", "governance", "hi"),
    "kb_promoted": _tl("Promoted to KB", "governance", "hi"),
    "legal_hold_set": _tl("Legal hold placed", "governance", "warn"),
    "legal_hold_released": _tl("Legal hold released", "governance", "dim"),
    "linked_task": _tl("Task linked", "governance", "dim"),
    # ── system ──
    "rule_fired": _tl("Automation rule fired", "system", "dim", system=True),
}

# Unknown actions are never hidden from the feed — they render with this fallback
# (forward-compat for writers that land before their catalog entry does).
TIMELINE_DEFAULT_META = {"label": None, "category": "system", "tone": "dim",
                         "milestone": False, "system": False}

# Per-ticket cap on pinned milestone events (a curated spine, not a second feed).
MILESTONES_PER_TICKET = 12
