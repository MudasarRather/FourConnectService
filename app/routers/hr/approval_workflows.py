"""HR Settings — Approval Workflows.

A thin aggregator/editor over the EXISTING per-policy ``approval_chain`` JSONB on
``LeavePolicy`` / ``TravelPolicy`` / ``ClaimPolicy``. We deliberately do NOT
introduce a separate workflow table: the leave/travel/claim submit paths already
snapshot these chains onto each request, so reusing them keeps three working
flows intact while giving Settings one place to design the routing.

Chain element shape (unchanged):
    {"approver_type": "MANAGER"|"HR"|"FINANCE"|"DEPT_HEAD"|"USER",
     "approver_user_id": <uuid str|null>, "label": <str|null>,
     "min_amount": <number|null>}   # min_amount only meaningful for travel/claim
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.leave_policy import LeavePolicy
from app.models.hr.travel_policy import TravelPolicy
from app.models.hr.claim_policy import ClaimPolicy
from app.models.hr.claim_category import ClaimCategory
from app.schemas.hr.approval_workflow import ApprovalChainUpdate
from app.utils.dependencies import get_current_superuser
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/settings/approval-workflows", tags=["HR — Approval Workflows"])

ALLOWED_APPROVERS = ["MANAGER", "DEPT_HEAD", "HR", "FINANCE", "USER"]

MODULES = {
    "leave": {
        "label": "Leave", "model": LeavePolicy,
        "default": [{"approver_type": "MANAGER", "label": "Reporting manager"},
                    {"approver_type": "HR", "label": "HR"}],
        "approvers": ["MANAGER", "HR", "USER"], "amounts": False,
    },
    "travel": {
        "label": "Travel", "model": TravelPolicy,
        "default": [{"approver_type": "MANAGER", "label": "Reporting manager"},
                    {"approver_type": "HR", "label": "HR / Admin"}],
        "approvers": ["MANAGER", "DEPT_HEAD", "HR", "USER"], "amounts": True,
    },
    "reimbursement": {
        "label": "Reimbursement", "model": ClaimPolicy,
        "default": [{"approver_type": "MANAGER", "label": "Reporting manager"},
                    {"approver_type": "FINANCE", "label": "Finance"},
                    {"approver_type": "HR", "label": "HR"}],
        "approvers": ["MANAGER", "FINANCE", "HR", "USER"], "amounts": True,
    },
}


def _policy_label(module: str, p, db: Session) -> str:
    if module == "leave":
        return p.label or (p.leave_type.value if hasattr(p.leave_type, "value") else str(p.leave_type))
    if module == "travel":
        return p.policy_name or "Travel policy"
    if module == "reimbursement":
        if p.label:
            return p.label
        cat = db.query(ClaimCategory).filter(ClaimCategory.id == p.category_id).first()
        return (cat.name if cat else None) or "Claim policy"
    return "Policy"


@router.get("/")
def list_workflows(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    out = []
    for key, cfg in MODULES.items():
        model = cfg["model"]
        rows = (db.query(model)
                .filter(model.is_deleted == False)  # noqa: E712
                .order_by(model.created_at).all())
        policies = [{
            "id": str(p.id),
            "label": _policy_label(key, p, db),
            "is_active": bool(p.is_active),
            "approval_chain": p.approval_chain or [],
            "uses_default": not p.approval_chain,
        } for p in rows]
        out.append({
            "module": key, "label": cfg["label"], "default_chain": cfg["default"],
            "approver_types": cfg["approvers"], "supports_amounts": cfg["amounts"],
            "policies": policies,
        })
    return {"modules": out}


@router.patch("/{module}/{policy_id}")
def update_chain(module: str, policy_id: UUID, payload: ApprovalChainUpdate,
                 db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    cfg = MODULES.get(module)
    if not cfg:
        raise HTTPException(404, "Unknown workflow module")
    model = cfg["model"]
    policy = db.query(model).filter(model.id == policy_id).first()
    if not policy:
        raise HTTPException(404, f"{cfg['label']} policy not found")

    chain = []
    for st in payload.approval_chain:
        if st.approver_type not in ALLOWED_APPROVERS:
            raise HTTPException(400, f"Invalid approver type: {st.approver_type}")
        item = {"approver_type": st.approver_type,
                "approver_user_id": st.approver_user_id,
                "label": st.label or st.approver_type.replace("_", " ").title()}
        if cfg["amounts"] and st.min_amount is not None:
            item["min_amount"] = st.min_amount
        chain.append(item)

    db.info["audit_actor_id"] = str(admin.id)
    policy.approval_chain = chain          # whole-value assignment → no JSONB in-place mutation pitfall

    # An empty chain is a REVERT — every consumer (leave/travel/claim) treats a
    # falsy chain as "use the module default", so [] cleanly restores the default
    # routing rather than auto-approving. Surface that distinction in the ledger.
    note = (f"{module}: reverted to default routing" if not chain
            else f"{module}: {len(chain)} stage(s)")
    if payload.reason and payload.reason.strip():
        note += f" — {payload.reason.strip()[:200]}"
    log_settings_change(db, "APPROVAL_WORKFLOW", policy_id, "UPDATE", admin.id, note=note)
    db.commit()
    return {"module": module, "policy_id": str(policy_id), "approval_chain": chain,
            "uses_default": not chain}
