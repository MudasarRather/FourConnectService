"""Support Desk routers package — aggregates every sub-router under one
APIRouter so main.py registers the whole module with a single include_router.

Each sub-router declares its own ``/support-desk/<resource>`` (admin),
``/support-desk/me/<resource>`` (self-service) or ``/public/support/<resource>``
(no-auth client portal) prefix. Importing this package also imports the models
(via the routers) so ``Base.metadata.create_all()`` creates the tables on boot.

Specific / self-service / public routers register BEFORE the broad tickets
router — defensive against any future shared-root shadowing.
"""
from fastapi import APIRouter

from app.routers.support_desk.dashboard import router as _dashboard_router
from app.routers.support_desk.tickets_self import router as _tickets_self_router
from app.routers.support_desk.self_catalog import router as _self_catalog_router
from app.routers.support_desk.public_portal import router as _public_portal_router
from app.routers.support_desk.masters import (
    organizations_router as _organizations_router,
    customers_router as _customers_router,
    contracts_router as _contracts_router,
    sla_router as _sla_router,
    categories_router as _categories_router,
)
from app.routers.support_desk.catalog import (
    kb_categories_router as _kb_categories_router,
    articles_router as _articles_router,
    service_items_router as _service_items_router,
    service_requests_router as _service_requests_router,
)
from app.routers.support_desk.itil import (
    change_router as _change_router,
    problem_router as _problem_router,
    asset_router as _asset_router,
)
from app.routers.support_desk.ops import (
    announcements_router as _announcements_router,
    automation_router as _automation_router,
    settings_router as _settings_router,
)
from app.routers.support_desk.audit import router as _audit_router
from app.routers.support_desk.integrations import router as _integrations_router
from app.routers.support_desk.workspace import (
    teams_router as _teams_router,
    queues_router as _queues_router,
    saved_views_router as _saved_views_router,
)
from app.routers.support_desk.queue_ops import (
    skills_router as _skills_router,
    agent_status_router as _agent_status_router,
    queue_ops_router as _queue_ops_router,
    ticket_tier_router as _ticket_tier_router,
)
from app.routers.support_desk.l2_ops import l2_router as _l2_router
from app.routers.support_desk.l3_ops import l3_router as _l3_router
from app.routers.support_desk.templates import templates_router as _templates_router
from app.routers.support_desk.incidents import router as _incidents_router
from app.routers.support_desk.tickets import router as _tickets_router

router = APIRouter()

# Dashboard + self-service + public (distinct prefixes, registered first)
router.include_router(_dashboard_router)
router.include_router(_tickets_self_router)   # /support-desk/me/tickets
router.include_router(_self_catalog_router)   # /support-desk/me/knowledge-base + /announcements
router.include_router(_public_portal_router)  # /public/support

# Masters
router.include_router(_organizations_router)
router.include_router(_customers_router)
router.include_router(_contracts_router)
router.include_router(_sla_router)
router.include_router(_categories_router)

# Knowledge Base + Service Catalog
router.include_router(_kb_categories_router)
router.include_router(_articles_router)
router.include_router(_service_items_router)
router.include_router(_service_requests_router)

# ITIL
router.include_router(_change_router)
router.include_router(_problem_router)
router.include_router(_asset_router)

# Ops
router.include_router(_announcements_router)
router.include_router(_automation_router)
router.include_router(_settings_router)
router.include_router(_audit_router)
router.include_router(_integrations_router)  # /tickets/{id}/to-task, /service-requests/{id}/to-invoice

# Phase-3 workspace entities
router.include_router(_teams_router)
# Queue Engine: literal /queues/{overview,tier/*} routes register BEFORE the CRUD
# queues router, and /tickets/{id}/{skip,tier-*} + /tickets/skip-report BEFORE the
# broad tickets router (route-shadowing discipline).
router.include_router(_queue_ops_router)
router.include_router(_queues_router)
router.include_router(_skills_router)
router.include_router(_agent_status_router)
router.include_router(_ticket_tier_router)
# L2 workbench (/tickets/{id}/{worklogs,watch,watchers,swarm*}) — literal suffixes,
# registered before the broad tickets router like the tier routes above.
router.include_router(_l2_router)
# L3 workbench (/tickets/{id}/handoff-dossier + /problems/{pid}/resolve-linked) —
# literal suffixes, registered before the broad tickets router like l2_ops above.
router.include_router(_l3_router)
router.include_router(_saved_views_router)
router.include_router(_templates_router)
# Incident Management (Fault Grid / Command Funnel): /support-desk/incidents/* literals +
# /tickets/{id}/{incident-roles,incident-impact,decision,pir} suffixes — registered
# BEFORE the broad tickets router (route-shadowing discipline, same as integrations).
router.include_router(_incidents_router)

# Broad tickets router (has /support-desk/tickets/{ticket_id}) — last.
router.include_router(_tickets_router)
