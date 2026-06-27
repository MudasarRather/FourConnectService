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
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


# Statuses that count as "still open" for SLA clocks and dashboards.
OPEN_TICKET_STATUSES = {
    TicketStatus.OPEN.value,
    TicketStatus.IN_PROGRESS.value,
    TicketStatus.PENDING_CUSTOMER.value,
    TicketStatus.PENDING_VENDOR.value,
    TicketStatus.ESCALATED.value,
}
TERMINAL_TICKET_STATUSES = {TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value}


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
    INTERNAL = "internal"
    API = "api"


class CommentAuthorKind(str, enum.Enum):
    STAFF = "staff"
    CUSTOMER = "customer"
    SYSTEM = "system"


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

# Notification events (consumed by app.utils.hr.notify.dispatch())
EVT_TICKET_CREATED = "SUPPORT_TICKET_CREATED"
EVT_TICKET_ASSIGNED = "SUPPORT_TICKET_ASSIGNED"
EVT_TICKET_REPLIED = "SUPPORT_TICKET_REPLIED"
EVT_TICKET_STATUS = "SUPPORT_TICKET_STATUS_CHANGED"
EVT_TICKET_ESCALATED = "SUPPORT_TICKET_ESCALATED"
EVT_TICKET_RESOLVED = "SUPPORT_TICKET_RESOLVED"
EVT_TICKET_SLA_BREACH = "SUPPORT_TICKET_SLA_BREACHED"
