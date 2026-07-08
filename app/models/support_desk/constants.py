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
