"""Offboarding hook — when an employee EXITs, surface their still-held assets as
actionable return tasks.

For every allocation still ``ALLOCATED`` to the exiting employee we create an
``EMPLOYEE_TO_STORE`` transfer in ``REQUESTED`` status (a "please collect this
asset" task) and log an asset-history event. Fully guarded: any failure is
swallowed so it can never break the employee-exit flow.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.hr.asset import AssetAllocation, AllocationStatus
from app.models.hr.asset_lifecycle import (
    AssetTransfer, AssetTransferType, AssetTransferStatus, AssetEventType,
)
from app.utils.hr.assets.audit import write_asset_history


def flag_open_allocations_on_exit(
    db: Session,
    employee_id: UUID,
    actor_user_id: Optional[UUID] = None,
) -> int:
    """Create return-to-store transfer tasks for the employee's open allocations.

    Returns the number of transfers created. Adds rows to the session but does NOT
    commit — the caller's exit transaction commits. Never raises.
    """
    created = 0
    try:
        open_allocs = (
            db.query(AssetAllocation)
            .filter(
                AssetAllocation.employee_id == employee_id,
                AssetAllocation.status == AllocationStatus.ALLOCATED,
            )
            .all()
        )
        for al in open_allocs:
            # Skip if a return-to-store transfer is already pending for this asset.
            existing = (
                db.query(AssetTransfer)
                .filter(
                    AssetTransfer.asset_id == al.asset_id,
                    AssetTransfer.from_employee_id == employee_id,
                    AssetTransfer.status.in_([
                        AssetTransferStatus.REQUESTED, AssetTransferStatus.APPROVED,
                    ]),
                    AssetTransfer.is_deleted == False,  # noqa: E712
                )
                .first()
            )
            if existing:
                continue
            tr = AssetTransfer(
                asset_id=al.asset_id,
                transfer_type=AssetTransferType.EMPLOYEE_TO_STORE,
                status=AssetTransferStatus.REQUESTED,
                from_employee_id=employee_id,
                reason="Auto-generated on employee exit — collect asset.",
                old_allocation_id=al.id,
                requested_by_user_id=actor_user_id,
            )
            db.add(tr)
            db.flush()
            write_asset_history(
                db, al.asset_id, AssetEventType.TRANSFER_REQUESTED,
                actor_user_id=actor_user_id, actor_employee_id=employee_id,
                related_entity_type="transfer", related_entity_id=tr.id,
                note="Return-to-store task created on employee exit.",
            )
            created += 1
    except Exception:  # noqa: BLE001 — never break the exit flow
        import traceback
        traceback.print_exc()
    return created
