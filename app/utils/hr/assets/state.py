"""Asset lifecycle state machines + transition guards.

Centralises the legal status transitions so routers can't introduce loopholes
(e.g. allocating a retired asset, double-returning, disposing an allocated asset).
Mirrors the ``_require_state`` pattern used in employees.py — raises HTTP 409 on an
illegal jump.
"""
from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException

from app.models.hr.asset import AssetStatus, AllocationStatus
from app.models.hr.asset_lifecycle import (
    AssetTransferStatus, AssetMaintenanceStatus, AssetDamageStatus,
    AssetAuditStatus, AssetDisposalStatus,
)


# ── Allowed transitions (current → set of legal targets) ──

ALLOWED_ASSET_STATUS = {
    AssetStatus.AVAILABLE: {AssetStatus.ALLOCATED, AssetStatus.RESERVED, AssetStatus.MAINTENANCE, AssetStatus.RETIRED},
    AssetStatus.RESERVED: {AssetStatus.ALLOCATED, AssetStatus.AVAILABLE, AssetStatus.MAINTENANCE, AssetStatus.RETIRED},
    AssetStatus.ALLOCATED: {AssetStatus.AVAILABLE, AssetStatus.MAINTENANCE, AssetStatus.RETIRED},
    AssetStatus.MAINTENANCE: {AssetStatus.AVAILABLE, AssetStatus.ALLOCATED, AssetStatus.RETIRED},
    AssetStatus.RETIRED: set(),  # terminal
}

ALLOWED_ALLOCATION_STATUS = {
    AllocationStatus.ALLOCATED: {AllocationStatus.RETURNED, AllocationStatus.LOST, AllocationStatus.DAMAGED},
    AllocationStatus.RETURNED: set(),
    AllocationStatus.LOST: set(),
    AllocationStatus.DAMAGED: set(),
}

ALLOWED_TRANSFER_STATUS = {
    AssetTransferStatus.REQUESTED: {AssetTransferStatus.APPROVED, AssetTransferStatus.REJECTED, AssetTransferStatus.CANCELLED},
    AssetTransferStatus.APPROVED: {AssetTransferStatus.COMPLETED, AssetTransferStatus.CANCELLED},
    AssetTransferStatus.COMPLETED: set(),
    AssetTransferStatus.REJECTED: set(),
    AssetTransferStatus.CANCELLED: set(),
}

ALLOWED_MAINTENANCE_STATUS = {
    AssetMaintenanceStatus.SCHEDULED: {AssetMaintenanceStatus.IN_PROGRESS, AssetMaintenanceStatus.CANCELLED},
    AssetMaintenanceStatus.IN_PROGRESS: {AssetMaintenanceStatus.COMPLETED, AssetMaintenanceStatus.CANCELLED},
    AssetMaintenanceStatus.COMPLETED: set(),
    AssetMaintenanceStatus.CANCELLED: set(),
}

ALLOWED_DAMAGE_STATUS = {
    AssetDamageStatus.REPORTED: {AssetDamageStatus.UNDER_REVIEW, AssetDamageStatus.REJECTED},
    AssetDamageStatus.UNDER_REVIEW: {AssetDamageStatus.IN_REPAIR, AssetDamageStatus.RESOLVED, AssetDamageStatus.REJECTED},
    AssetDamageStatus.IN_REPAIR: {AssetDamageStatus.RESOLVED, AssetDamageStatus.WRITE_OFF},
    AssetDamageStatus.RESOLVED: set(),
    AssetDamageStatus.WRITE_OFF: set(),
    AssetDamageStatus.REJECTED: set(),
}

ALLOWED_AUDIT_STATUS = {
    AssetAuditStatus.DRAFT: {AssetAuditStatus.IN_PROGRESS, AssetAuditStatus.CANCELLED},
    AssetAuditStatus.IN_PROGRESS: {AssetAuditStatus.COMPLETED, AssetAuditStatus.CANCELLED},
    AssetAuditStatus.COMPLETED: set(),
    AssetAuditStatus.CANCELLED: set(),
}

ALLOWED_DISPOSAL_STATUS = {
    AssetDisposalStatus.REQUESTED: {AssetDisposalStatus.APPROVED, AssetDisposalStatus.REJECTED, AssetDisposalStatus.CANCELLED},
    AssetDisposalStatus.APPROVED: {AssetDisposalStatus.COMPLETED, AssetDisposalStatus.CANCELLED},
    AssetDisposalStatus.COMPLETED: set(),
    AssetDisposalStatus.REJECTED: set(),
    AssetDisposalStatus.CANCELLED: set(),
}

_MAPS = {
    "asset": ALLOWED_ASSET_STATUS,
    "allocation": ALLOWED_ALLOCATION_STATUS,
    "transfer": ALLOWED_TRANSFER_STATUS,
    "maintenance": ALLOWED_MAINTENANCE_STATUS,
    "damage": ALLOWED_DAMAGE_STATUS,
    "audit": ALLOWED_AUDIT_STATUS,
    "disposal": ALLOWED_DISPOSAL_STATUS,
}


def _val(s) -> str:
    return getattr(s, "value", str(s))


def assert_transition(machine: str, current, target) -> None:
    """Raise 409 unless ``current → target`` is a legal transition for ``machine``.
    A same-state transition is rejected (callers should guard idempotency separately)."""
    allowed = _MAPS.get(machine, {})
    if target not in allowed.get(current, set()):
        raise HTTPException(
            409,
            f"Illegal {machine} transition: {_val(current)} → {_val(target)}.",
        )


def require_status(label: str, current, allowed: Iterable) -> None:
    """Raise 409 unless ``current`` is one of ``allowed``."""
    if current not in set(allowed):
        names = ", ".join(_val(a) for a in allowed)
        raise HTTPException(409, f"{label} must be one of [{names}] (is {_val(current)}).")


def next_status_on_return(alloc_status: AllocationStatus) -> AssetStatus:
    """Map an allocation return outcome to the resulting asset status.

    RETURNED → AVAILABLE, DAMAGED → MAINTENANCE (repair path), LOST → RETIRED.
    (Refines the old behaviour which retired every non-RETURNED outcome.)
    """
    if alloc_status == AllocationStatus.RETURNED:
        return AssetStatus.AVAILABLE
    if alloc_status == AllocationStatus.DAMAGED:
        return AssetStatus.MAINTENANCE
    return AssetStatus.RETIRED  # LOST
