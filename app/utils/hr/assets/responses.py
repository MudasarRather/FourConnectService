"""Response builders for the Asset Management routers — derive display labels
(employee/category/vendor names, asset codes) so routers stay thin and the
response contract is built in one place."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.asset import Asset, AssetAllocation
from app.models.hr.asset_lifecycle import (
    AssetCategory, Vendor, AssetTransfer, AssetMaintenance, AssetDamage, AssetHistory,
    AssetAuditItem, AssetDisposal,
)
from app.schemas.hr.asset import AssetResponse, AssetAllocationResponse
from app.schemas.hr.asset_lifecycle import (
    AssetCategoryResponse, VendorResponse, AssetTransferResponse,
    AssetMaintenanceResponse, AssetDamageResponse, AssetHistoryResponse,
    AssetAuditItemResponse, AssetDisposalResponse,
)


def employee_label(db: Session, employee_id: Optional[UUID]) -> Optional[str]:
    if not employee_id:
        return None
    row = (
        db.query(User.full_name)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.id == employee_id)
        .first()
    )
    return row[0] if row else None


def user_label(db: Session, user_id: Optional[UUID]) -> Optional[str]:
    if not user_id:
        return None
    row = db.query(User.full_name).filter(User.id == user_id).first()
    return row[0] if row else None


def asset_code(db: Session, asset_id: Optional[UUID]) -> Optional[str]:
    if not asset_id:
        return None
    row = db.query(Asset.asset_code).filter(Asset.id == asset_id).first()
    return row[0] if row else None


def to_asset_response(db: Session, a: Asset) -> AssetResponse:
    cat_name = a.category.name if getattr(a, "category", None) else None
    vendor_name = a.vendor.name if getattr(a, "vendor", None) else None
    return AssetResponse(
        id=a.id, asset_code=a.asset_code, asset_type=a.asset_type,
        brand=a.brand, model=a.model, serial_number=a.serial_number,
        purchase_date=a.purchase_date, purchase_cost=a.purchase_cost,
        condition=a.condition, status=a.status,
        assigned_employee_id=a.assigned_employee_id,
        assigned_employee_name=employee_label(db, a.assigned_employee_id),
        location_id=a.location_id, notes=a.notes,
        category_id=a.category_id, category_name=cat_name,
        vendor_id=a.vendor_id, vendor_name=vendor_name,
        department_id=a.department_id, project_id=a.project_id,
        purchase_order_no=a.purchase_order_no, invoice_no=a.invoice_no,
        warranty_start=a.warranty_start, warranty_end=a.warranty_end,
        depreciation_method=a.depreciation_method,
        salvage_value=a.salvage_value, current_book_value=a.current_book_value,
        building=a.building, floor=a.floor, room=a.room, tag=a.tag,
        photo_path=a.photo_path, invoice_path=a.invoice_path,
        warranty_doc_path=a.warranty_doc_path,
        created_at=a.created_at, updated_at=a.updated_at,
    )


def to_alloc_response(db: Session, al: AssetAllocation, asset: Optional[Asset] = None) -> AssetAllocationResponse:
    a = asset or db.query(Asset).filter(Asset.id == al.asset_id).first()
    return AssetAllocationResponse(
        id=al.id, asset_id=al.asset_id,
        asset_code=a.asset_code if a else None, asset_type=a.asset_type if a else None,
        brand=a.brand if a else None, model=a.model if a else None,
        serial_number=a.serial_number if a else None, warranty_end=a.warranty_end if a else None,
        employee_id=al.employee_id, employee_name=employee_label(db, al.employee_id),
        process_id=al.process_id, allocated_date=al.allocated_date,
        expected_return_date=al.expected_return_date, returned_date=al.returned_date,
        condition_on_issue=al.condition_on_issue, condition_on_return=al.condition_on_return,
        status=al.status, acknowledged_by_employee=al.acknowledged_by_employee,
        acknowledged_at=al.acknowledged_at,
        return_requested=bool(getattr(al, "return_requested", False)),
        return_requested_at=getattr(al, "return_requested_at", None),
        return_request_note=getattr(al, "return_request_note", None),
        notes=al.notes, created_at=al.created_at,
    )


def to_category_response(c: AssetCategory, asset_count: Optional[int] = None) -> AssetCategoryResponse:
    r = AssetCategoryResponse.model_validate(c)
    r.asset_count = asset_count
    return r


def to_vendor_response(v: Vendor, asset_count: Optional[int] = None) -> VendorResponse:
    r = VendorResponse.model_validate(v)
    r.asset_count = asset_count
    return r


def to_transfer_response(db: Session, t: AssetTransfer) -> AssetTransferResponse:
    return AssetTransferResponse(
        id=t.id, asset_id=t.asset_id, asset_code=asset_code(db, t.asset_id),
        transfer_type=t.transfer_type, status=t.status,
        from_employee_id=t.from_employee_id, from_employee_name=employee_label(db, t.from_employee_id),
        to_employee_id=t.to_employee_id, to_employee_name=employee_label(db, t.to_employee_id),
        from_location_id=t.from_location_id, to_location_id=t.to_location_id,
        from_department_id=t.from_department_id, to_department_id=t.to_department_id,
        reason=t.reason, effective_date=t.effective_date, notes=t.notes,
        created_at=t.created_at,
    )


def to_maintenance_response(db: Session, m: AssetMaintenance) -> AssetMaintenanceResponse:
    vendor_name = m.vendor.name if getattr(m, "vendor", None) else None
    return AssetMaintenanceResponse(
        id=m.id, asset_id=m.asset_id, asset_code=asset_code(db, m.asset_id),
        maintenance_type=m.maintenance_type, status=m.status,
        vendor_id=m.vendor_id, vendor_name=vendor_name, damage_id=m.damage_id,
        reported_date=m.reported_date, scheduled_date=m.scheduled_date,
        started_date=m.started_date, completed_date=m.completed_date,
        cost=m.cost, description=m.description, resolution_notes=m.resolution_notes,
        condition_before=m.condition_before, condition_after=m.condition_after,
        attachments=m.attachments or [], created_at=m.created_at,
    )


def to_damage_response(db: Session, d: AssetDamage) -> AssetDamageResponse:
    reporter = employee_label(db, d.reported_by_employee_id) or user_label(db, d.reported_by_user_id)
    return AssetDamageResponse(
        id=d.id, asset_id=d.asset_id, asset_code=asset_code(db, d.asset_id),
        allocation_id=d.allocation_id, severity=d.severity, status=d.status,
        reported_by_employee_id=d.reported_by_employee_id, reported_by_name=reporter,
        title=d.title, description=d.description, attachments=d.attachments or [],
        reported_date=d.reported_date, resolved_date=d.resolved_date,
        resolution_notes=d.resolution_notes, liable_employee=d.liable_employee,
        recovery_amount=d.recovery_amount, created_at=d.created_at,
    )


def to_audit_item_response(db: Session, it: AssetAuditItem) -> AssetAuditItemResponse:
    return AssetAuditItemResponse(
        id=it.id, audit_id=it.audit_id, asset_id=it.asset_id, asset_code=asset_code(db, it.asset_id),
        expected_status=it.expected_status, result=it.result, found_condition=it.found_condition,
        scanned_at=it.scanned_at, remarks=it.remarks,
    )


def to_disposal_response(db: Session, d: AssetDisposal) -> AssetDisposalResponse:
    return AssetDisposalResponse(
        id=d.id, asset_id=d.asset_id, asset_code=asset_code(db, d.asset_id),
        disposal_method=d.disposal_method, status=d.status, reason=d.reason,
        request_date=d.request_date, approved_date=d.approved_date, disposed_date=d.disposed_date,
        sale_value=d.sale_value, book_value=d.book_value, buyer=d.buyer,
        attachments=d.attachments or [], notes=d.notes, created_at=d.created_at,
    )


def to_history_response(db: Session, h: AssetHistory) -> AssetHistoryResponse:
    return AssetHistoryResponse(
        id=h.id, asset_id=h.asset_id, event_type=h.event_type,
        actor_user_id=h.actor_user_id, actor_name=user_label(db, h.actor_user_id),
        actor_employee_id=h.actor_employee_id, actor_employee_name=employee_label(db, h.actor_employee_id),
        from_status=h.from_status, to_status=h.to_status,
        related_entity_type=h.related_entity_type, related_entity_id=h.related_entity_id,
        payload=h.payload or {}, note=h.note, created_at=h.created_at,
    )
