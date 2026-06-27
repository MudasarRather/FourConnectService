"""Support Desk — master CRUD: Organizations, Customers, Contracts, SLA packages,
Categories. Five routers exported for the aggregation package. Admin-only.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.core import (
    SdOrganization, SdCustomer, SdContract, SdSlaPackage, SdCategory,
)
from app.models.support_desk.ticket import SdTicket
from app.models.support_desk.constants import OPEN_TICKET_STATUSES
from app.schemas.support_desk.core import (
    OrganizationCreate, OrganizationUpdate, OrganizationResponse,
    CustomerCreate, CustomerUpdate, CustomerResponse,
    ContractCreate, ContractUpdate, ContractResponse,
    SlaPackageCreate, SlaPackageUpdate, SlaPackageResponse,
    CategoryCreate, CategoryUpdate, CategoryResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.support_desk.audit import write_audit


def _org_names(db: Session, org_ids: set) -> dict:
    org_ids = {i for i in org_ids if i}
    if not org_ids:
        return {}
    rows = db.query(SdOrganization.id, SdOrganization.name).filter(SdOrganization.id.in_(org_ids)).all()
    return {str(r[0]): r[1] for r in rows}


# ═══════════════════════ Organizations ═══════════════════════
organizations_router = APIRouter(prefix="/support-desk/organizations", tags=["Support Desk — Organizations"])


@organizations_router.get("/", response_model=List[OrganizationResponse])
def list_orgs(
    q: Optional[str] = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = db.query(SdOrganization).filter(SdOrganization.is_deleted == False)  # noqa: E712
    if not include_inactive:
        query = query.filter(SdOrganization.is_active == True)  # noqa: E712
    if q:
        query = query.filter(SdOrganization.name.ilike(f"%{q.strip()}%"))
    orgs = query.order_by(SdOrganization.name).all()
    # enrich counts
    for o in orgs:
        o.customer_count = db.query(SdCustomer).filter(
            SdCustomer.organization_id == o.id, SdCustomer.is_deleted == False).count()  # noqa: E712
        o.open_ticket_count = db.query(SdTicket).filter(
            SdTicket.organization_id == o.id, SdTicket.is_deleted == False,  # noqa: E712
            SdTicket.status.in_(OPEN_TICKET_STATUSES)).count()
    return orgs


@organizations_router.post("/", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
def create_org(payload: OrganizationCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if payload.code and db.query(SdOrganization).filter(SdOrganization.code == payload.code).first():
        raise HTTPException(400, "Organization code already exists")
    org = SdOrganization(**payload.model_dump(exclude_unset=True))
    db.add(org)
    db.flush()
    write_audit(db, entity_type="organization", op="created", entity_id=org.id, actor_id=admin.id,
                details={"name": org.name})
    db.commit()
    db.refresh(org)
    return org


@organizations_router.get("/{org_id}", response_model=OrganizationResponse)
def get_org(org_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    org = db.query(SdOrganization).filter(SdOrganization.id == org_id, SdOrganization.is_deleted == False).first()  # noqa: E712
    if not org:
        raise HTTPException(404, "Organization not found")
    return org


@organizations_router.patch("/{org_id}", response_model=OrganizationResponse)
def update_org(org_id: UUID, payload: OrganizationUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    org = db.query(SdOrganization).filter(SdOrganization.id == org_id, SdOrganization.is_deleted == False).first()  # noqa: E712
    if not org:
        raise HTTPException(404, "Organization not found")
    update = payload.model_dump(exclude_unset=True)
    if update.get("code") and update["code"] != org.code and \
            db.query(SdOrganization).filter(SdOrganization.code == update["code"], SdOrganization.id != org_id).first():
        raise HTTPException(400, "Organization code already exists")
    for k, v in update.items():
        setattr(org, k, v)
    write_audit(db, entity_type="organization", op="updated", entity_id=org.id, actor_id=admin.id, details=update)
    db.commit()
    db.refresh(org)
    return org


@organizations_router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org(org_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    org = db.query(SdOrganization).filter(SdOrganization.id == org_id, SdOrganization.is_deleted == False).first()  # noqa: E712
    if not org:
        raise HTTPException(404, "Organization not found")
    open_n = db.query(SdTicket).filter(SdTicket.organization_id == org_id, SdTicket.is_deleted == False,  # noqa: E712
                                       SdTicket.status.in_(OPEN_TICKET_STATUSES)).count()
    if open_n:
        raise HTTPException(409, f"Cannot delete organization with {open_n} open tickets")
    org.is_deleted = True
    write_audit(db, entity_type="organization", op="deleted", entity_id=org.id, actor_id=admin.id, details={})
    db.commit()
    return None


# ═══════════════════════ Customers ═══════════════════════
customers_router = APIRouter(prefix="/support-desk/customers", tags=["Support Desk — Customers"])


@customers_router.get("/", response_model=List[CustomerResponse])
def list_customers(
    organization_id: Optional[UUID] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = db.query(SdCustomer).filter(SdCustomer.is_deleted == False)  # noqa: E712
    if organization_id:
        query = query.filter(SdCustomer.organization_id == organization_id)
    if q:
        query = query.filter(SdCustomer.name.ilike(f"%{q.strip()}%"))
    customers = query.order_by(SdCustomer.name).all()
    names = _org_names(db, {c.organization_id for c in customers})
    for c in customers:
        c.organization_name = names.get(str(c.organization_id))
    return customers


@customers_router.post("/", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if not db.query(SdOrganization).filter(SdOrganization.id == payload.organization_id, SdOrganization.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "Organization not found")
    c = SdCustomer(**payload.model_dump(exclude_unset=True))
    db.add(c)
    db.flush()
    write_audit(db, entity_type="customer", op="created", entity_id=c.id, actor_id=admin.id, details={"name": c.name})
    db.commit()
    db.refresh(c)
    return c


@customers_router.get("/{cust_id}", response_model=CustomerResponse)
def get_customer(cust_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = db.query(SdCustomer).filter(SdCustomer.id == cust_id, SdCustomer.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Customer not found")
    return c


@customers_router.patch("/{cust_id}", response_model=CustomerResponse)
def update_customer(cust_id: UUID, payload: CustomerUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = db.query(SdCustomer).filter(SdCustomer.id == cust_id, SdCustomer.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Customer not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return c


@customers_router.delete("/{cust_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(cust_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = db.query(SdCustomer).filter(SdCustomer.id == cust_id, SdCustomer.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Customer not found")
    c.is_deleted = True
    db.commit()
    return None


# ═══════════════════════ Contracts ═══════════════════════
contracts_router = APIRouter(prefix="/support-desk/contracts", tags=["Support Desk — Contracts"])


@contracts_router.get("/", response_model=List[ContractResponse])
def list_contracts(
    organization_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = db.query(SdContract).filter(SdContract.is_deleted == False)  # noqa: E712
    if organization_id:
        query = query.filter(SdContract.organization_id == organization_id)
    contracts = query.order_by(SdContract.created_at.desc()).all()
    names = _org_names(db, {c.organization_id for c in contracts})
    for c in contracts:
        c.organization_name = names.get(str(c.organization_id))
    return contracts


@contracts_router.post("/", response_model=ContractResponse, status_code=status.HTTP_201_CREATED)
def create_contract(payload: ContractCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if payload.contract_number and db.query(SdContract).filter(SdContract.contract_number == payload.contract_number).first():
        raise HTTPException(400, "Contract number already exists")
    c = SdContract(**payload.model_dump(exclude_unset=True))
    db.add(c)
    db.flush()
    write_audit(db, entity_type="contract", op="created", entity_id=c.id, actor_id=admin.id, details={"name": c.name})
    db.commit()
    db.refresh(c)
    return c


@contracts_router.get("/{cid}", response_model=ContractResponse)
def get_contract(cid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = db.query(SdContract).filter(SdContract.id == cid, SdContract.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Contract not found")
    return c


@contracts_router.patch("/{cid}", response_model=ContractResponse)
def update_contract(cid: UUID, payload: ContractUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = db.query(SdContract).filter(SdContract.id == cid, SdContract.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Contract not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return c


@contracts_router.delete("/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(cid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    c = db.query(SdContract).filter(SdContract.id == cid, SdContract.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Contract not found")
    c.is_deleted = True
    db.commit()
    return None


# ═══════════════════════ SLA packages ═══════════════════════
sla_router = APIRouter(prefix="/support-desk/sla-packages", tags=["Support Desk — SLA"])


@sla_router.get("/", response_model=List[SlaPackageResponse])
def list_sla(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return db.query(SdSlaPackage).filter(SdSlaPackage.is_deleted == False).order_by(SdSlaPackage.name).all()  # noqa: E712


@sla_router.post("/", response_model=SlaPackageResponse, status_code=status.HTTP_201_CREATED)
def create_sla(payload: SlaPackageCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if db.query(SdSlaPackage).filter(SdSlaPackage.name == payload.name, SdSlaPackage.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "SLA package name already exists")
    pkg = SdSlaPackage(**payload.model_dump(exclude_unset=True))
    if pkg.is_default:
        db.query(SdSlaPackage).update({SdSlaPackage.is_default: False})
    db.add(pkg)
    db.flush()
    write_audit(db, entity_type="sla_package", op="created", entity_id=pkg.id, actor_id=admin.id, details={"name": pkg.name})
    db.commit()
    db.refresh(pkg)
    return pkg


@sla_router.get("/{sid}", response_model=SlaPackageResponse)
def get_sla(sid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    pkg = db.query(SdSlaPackage).filter(SdSlaPackage.id == sid, SdSlaPackage.is_deleted == False).first()  # noqa: E712
    if not pkg:
        raise HTTPException(404, "SLA package not found")
    return pkg


@sla_router.patch("/{sid}", response_model=SlaPackageResponse)
def update_sla(sid: UUID, payload: SlaPackageUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    pkg = db.query(SdSlaPackage).filter(SdSlaPackage.id == sid, SdSlaPackage.is_deleted == False).first()  # noqa: E712
    if not pkg:
        raise HTTPException(404, "SLA package not found")
    update = payload.model_dump(exclude_unset=True)
    if update.get("is_default"):
        db.query(SdSlaPackage).filter(SdSlaPackage.id != sid).update({SdSlaPackage.is_default: False})
    for k, v in update.items():
        setattr(pkg, k, v)
    db.commit()
    db.refresh(pkg)
    return pkg


@sla_router.delete("/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sla(sid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    pkg = db.query(SdSlaPackage).filter(SdSlaPackage.id == sid, SdSlaPackage.is_deleted == False).first()  # noqa: E712
    if not pkg:
        raise HTTPException(404, "SLA package not found")
    if pkg.is_default:
        raise HTTPException(409, "Cannot delete the default SLA package")
    pkg.is_deleted = True
    db.commit()
    return None


# ═══════════════════════ Categories ═══════════════════════
categories_router = APIRouter(prefix="/support-desk/categories", tags=["Support Desk — Categories"])


@categories_router.get("/", response_model=List[CategoryResponse])
def list_categories(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return (db.query(SdCategory).filter(SdCategory.is_deleted == False)  # noqa: E712
            .order_by(SdCategory.sort_order, SdCategory.name).all())


@categories_router.post("/", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    cat = SdCategory(**payload.model_dump(exclude_unset=True))
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@categories_router.patch("/{cid}", response_model=CategoryResponse)
def update_category(cid: UUID, payload: CategoryUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    cat = db.query(SdCategory).filter(SdCategory.id == cid, SdCategory.is_deleted == False).first()  # noqa: E712
    if not cat:
        raise HTTPException(404, "Category not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(cat, k, v)
    db.commit()
    db.refresh(cat)
    return cat


@categories_router.delete("/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(cid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    cat = db.query(SdCategory).filter(SdCategory.id == cid, SdCategory.is_deleted == False).first()  # noqa: E712
    if not cat:
        raise HTTPException(404, "Category not found")
    cat.is_deleted = True
    db.commit()
    return None
