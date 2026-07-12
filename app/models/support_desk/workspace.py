"""Support Desk — Phase-3 workspace entities: Team, Queue, Saved View, Ticket Template.

New tables (auto-created by ``Base.metadata.create_all()`` on startup). The
``team_id`` / ``queue_id`` FOREIGN-KEY columns on ``support_tickets`` are added by
``add_ticket_team_queue_columns.py`` (create_all never alters existing tables).
"""
import uuid

from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class SdTeam(Base):
    """A support team — a named group of agents with a lead."""
    __tablename__ = "support_teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False)
    code = Column(String(40), nullable=True, unique=True, index=True)
    description = Column(Text, nullable=True)
    color = Column(String(20), nullable=True)
    lead_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    member_ids = Column(JSONB, nullable=False, default=list)   # [user_id, ...]
    member_roles = Column(JSONB, nullable=False, default=dict)  # {user_id: "lead"|"agent"|"collaborator"}

    # ── Routing — what this team handles + how it auto-distributes work ──
    request_types = Column(JSONB, nullable=False, default=list)  # [ticket_type, ...] the team handles
    category_ids = Column(JSONB, nullable=False, default=list)   # [category_uuid, ...] the team owns
    auto_assign = Column(Boolean, nullable=False, default=False)  # auto-claim matched tickets to a member?
    assignment_method = Column(String(20), nullable=False, default="round_robin")  # manual|round_robin|load_balanced
    rr_last_user_id = Column(UUID(as_uuid=True), nullable=True)   # round-robin cursor

    # ── Service profile — operating window + default SLA / priority for owned work ──
    business_hours = Column(JSONB, nullable=False, default=dict)  # {tz, days:[1..7], start:"09:00", end:"18:00"}
    default_sla_package_id = Column(UUID(as_uuid=True), ForeignKey("support_sla_packages.id"), nullable=True)
    default_priority = Column(String(20), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdTeam {self.code or self.name}>"


class SdTicketViewer(Base):
    """Live-presence heartbeat — which agents have a ticket open right now.

    Zendesk-style agent-collision detection. A row is upserted every ~25s while an
    agent has the ticket drawer / war-room console open; readers treat rows older
    than ~60s as gone. Rows are pruned opportunistically on every heartbeat, so the
    table stays tiny (bounded by concurrently-open drawers).
    """
    __tablename__ = "support_ticket_viewers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_support_ticket_viewers_tu", "ticket_id", "user_id", unique=True),)

    def __repr__(self):
        return f"<SdTicketViewer {self.ticket_id}:{self.user_id}>"


class SdQueue(Base):
    """A work queue — skill/level-based intake bucket, optionally owned by a team.

    Queue-engine columns (``tier`` … ``business_hours``) land on the live DB via
    ``add_support_queue_engine_columns.py`` (create_all never alters existing tables).
    """
    __tablename__ = "support_queues"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False)
    code = Column(String(40), nullable=True, unique=True, index=True)
    description = Column(Text, nullable=True)
    color = Column(String(20), nullable=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("support_teams.id"), nullable=True, index=True)
    auto_assign = Column(Boolean, nullable=False, default=False)   # on/off for the auto-assign engine
    assignment_method = Column(String(20), nullable=False, default="round_robin")  # manual | round_robin | load_balanced
    category_ids = Column(JSONB, nullable=False, default=list)     # categories this queue routes [category_uuid, ...]
    rr_last_user_id = Column(UUID(as_uuid=True), nullable=True)    # round-robin cursor (last auto-assigned member)

    # ── Queue engine (ServiceNow AWA / Zendesk omnichannel semantics) ──
    tier = Column(Integer, nullable=True, index=True)              # 1|2|3 support tier; NULL = untiered specialty queue
    skill_ids = Column(JSONB, nullable=False, default=list)        # skills required for auto-assignment [skill_uuid, ...]
    serve_order = Column(String(20), nullable=False, default="priority_age")  # priority_age | sla_breach (play-mode serve policy)
    queue_priority = Column(Integer, nullable=False, default=50)   # 1-100 cross-queue drain order (higher drains first)
    max_agent_load = Column(Integer, nullable=True)                # per-agent open-ticket soft cap for load_balanced
    is_default = Column(Boolean, nullable=False, default=False)    # the un-deletable fallback queue (at most one)
    business_hours = Column(JSONB, nullable=True)                  # {tz, days, start, end} override; falls back to team's

    # ── Config v2 (ServiceNow/Zendesk parity — land on the live DB via
    #    ``add_support_queue_config_v2_columns.py``; create_all never alters) ──
    sla_package_id = Column(UUID(as_uuid=True), ForeignKey("support_sla_packages.id"), nullable=True)  # per-queue SLA policy
    capacity_limit = Column(Integer, nullable=True)                # open-ticket cap; NULL = unlimited
    overflow_queue_id = Column(UUID(as_uuid=True), nullable=True)  # spill target when at capacity (one hop, no chains)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdQueue {self.code or self.name}>"


class SdSkill(Base):
    """A routing skill (Zendesk skills-based routing). ``agent_ids`` is a JSONB roster
    (same style as ``SdTeam.member_ids``) — the agents who hold this skill. Queues
    reference skills via ``SdQueue.skill_ids``; auto-assignment prefers agents holding
    ALL of a queue's skills and fails open to the whole team when none qualify."""
    __tablename__ = "support_skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False)
    code = Column(String(40), nullable=True, unique=True, index=True)
    description = Column(Text, nullable=True)
    color = Column(String(20), nullable=True)
    agent_ids = Column(JSONB, nullable=False, default=list)        # [user_id, ...]
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdSkill {self.code or self.name}>"


class SdAgentStatus(Base):
    """Unified agent status (Zendesk agent statuses). One row per agent; absent row
    reads as 'online' so the desk works before anyone touches the toggle. Auto-assign
    skips away/offline agents and fails open to everyone when nobody is online."""
    __tablename__ = "support_agent_status"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    status = Column(String(16), nullable=False, default="online")  # online | away | focus | offline
    status_note = Column(String(200), nullable=True)
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdAgentStatus {self.user_id}:{self.status}>"


class SdTicketSkip(Base):
    """One row per play-mode skip (Zendesk skip-with-reason audit). Serve-next excludes
    a ticket the caller skipped today; supervisors read the per-agent skip report."""
    __tablename__ = "support_ticket_skips"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    reason_code = Column(String(40), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (Index("ix_support_ticket_skips_user_day", "user_id", "created_at"),)

    def __repr__(self):
        return f"<SdTicketSkip {self.ticket_id}:{self.user_id}>"


class SdSavedView(Base):
    """A per-agent saved filter+columns+sort over a ticket scope."""
    __tablename__ = "support_saved_views"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    scope = Column(String(40), nullable=False, default="all")
    filters = Column(JSONB, nullable=False, default=dict)
    columns = Column(JSONB, nullable=False, default=list)
    sort_by = Column(String(40), nullable=True)
    sort_dir = Column(String(8), nullable=True)
    is_shared = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (Index("ix_support_saved_views_owner", "owner_user_id", "is_shared"),)

    def __repr__(self):
        return f"<SdSavedView {self.name}>"


class SdTicketTemplate(Base):
    """A reusable ticket template that pre-fills the create form.

    "Copperplate Studio" lifecycle: ``status`` (draft|active|archived) is the source
    of truth; ``is_active`` is kept mirrored (== status 'active') for API stability.
    ``version``/``revisions`` are versioning-lite — content edits (subject/body/
    checklist) snapshot the PREVIOUS cut onto ``revisions`` (capped at 10).
    ``usage_count``/``last_used_*`` are stamped by the apply flow. New columns land
    on the live DB via ``ensure_template_studio_columns`` (create_all never alters).
    """
    __tablename__ = "support_ticket_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("support_categories.id"), nullable=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("support_teams.id"), nullable=True)
    ticket_type = Column(String(30), nullable=True)
    priority = Column(String(20), nullable=True)
    subject = Column(String(300), nullable=True)        # subject template
    body = Column(Text, nullable=True)                  # description template
    tags = Column(JSONB, nullable=False, default=list)
    checklist = Column(JSONB, nullable=False, default=list)   # [{text, done}]

    # ── Lifecycle + analytics + defaults (Template Studio) ──
    status = Column(String(16), nullable=False, default="active", index=True)  # draft|active|archived
    usage_count = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_used_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    default_sla_package_id = Column(UUID(as_uuid=True), ForeignKey("support_sla_packages.id"), nullable=True)
    default_assignee_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    icon = Column(String(40), nullable=True)            # lucide key OR emoji
    accent = Column(String(20), nullable=True)          # hex card identity
    pinned = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=1)
    revisions = Column(JSONB, nullable=False, default=list)  # [{subject, body, checklist, edited_by, edited_at, version}]

    # ── Visibility scope (agent Template Desk) ──
    # 'global'  — everyone on the desk (admin-authored library; the historical default)
    # 'team'    — members/lead of ``team_id`` only
    # 'personal'— its ``created_by_id`` owner only (Zendesk personal macros)
    # Lands on the live DB via ``ensure_template_studio_columns`` (create_all never alters).
    visibility = Column(String(16), nullable=False, default="global", index=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdTicketTemplate {self.name}>"


class SdTemplateFavorite(Base):
    """Per-agent template star (Zendesk-style favorites) — pure preference, no audit.

    NEW table — auto-created by ``Base.metadata.create_all()``; unlike the GLOBAL
    ``SdTicketTemplate.pinned`` flag (admin curation), a favorite is one agent's own.
    """
    __tablename__ = "support_template_favorites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("support_ticket_templates.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "template_id", name="uq_support_template_fav"),)

    def __repr__(self):
        return f"<SdTemplateFavorite {self.user_id}:{self.template_id}>"


class SdTemplateUsageEvent(Base):
    """One row per template use — powers per-agent analytics the desk-global
    ``usage_count`` / ``last_used_by_id`` pair can't answer (my top used, my recent).

    ``kind``: 'apply' (new-ticket prefill) | 'macro' (run on an existing ticket).
    ``ticket_id`` is a bare UUID (no FK) to dodge create-order issues — stamped only
    for macro runs. NEW table — auto-created by ``Base.metadata.create_all()``.
    """
    __tablename__ = "support_template_usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("support_ticket_templates.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    ticket_id = Column(UUID(as_uuid=True), nullable=True)
    kind = Column(String(10), nullable=False, default="apply")
    used_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (Index("ix_support_template_usage_user", "user_id", "used_at"),)

    def __repr__(self):
        return f"<SdTemplateUsageEvent {self.template_id}:{self.kind}>"
