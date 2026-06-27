"""Support Desk models package.

Importing this package registers every Support Desk table on ``Base.metadata``
so the startup ``create_all()`` creates them. The router package imports from
here, and main.py imports the router package — so the chain is complete.
"""
from app.models.support_desk.core import (
    SdOrganization, SdCustomer, SdSlaPackage, SdContract, SdCategory,
)
from app.models.support_desk.ticket import (
    SdTicket, SdTicketComment, SdTicketActivity,
)
from app.models.support_desk.catalog import (
    SdKbCategory, SdKnowledgeArticle, SdServiceItem, SdServiceRequest,
)
from app.models.support_desk.itil import (
    SdChangeRequest, SdProblem, SdCustomerAsset,
)
from app.models.support_desk.ops import (
    SdAnnouncement, SdAutomationRule, SdSetting,
)

__all__ = [
    "SdOrganization", "SdCustomer", "SdSlaPackage", "SdContract", "SdCategory",
    "SdTicket", "SdTicketComment", "SdTicketActivity",
    "SdKbCategory", "SdKnowledgeArticle", "SdServiceItem", "SdServiceRequest",
    "SdChangeRequest", "SdProblem", "SdCustomerAsset",
    "SdAnnouncement", "SdAutomationRule", "SdSetting",
]
