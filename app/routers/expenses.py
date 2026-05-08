from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, extract, or_
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timezone, timedelta

from app.database import get_db
from app.utils.dependencies import get_current_user
from app.models.user import User
from app.models.expense import (
    Expense, ExpenseStatus, Priority, PaymentMethod, 
    ExpensePaymentStatus, AllocationType, VendorType
)
from app.schemas.expense import (
    ExpenseCreate,
    ExpenseUpdate,
    ExpenseResponse,
    DuplicateCheckRequest,
    DuplicateCheckResponse,
    RejectRequest,
    ReversalPreviewResponse,
    ReversalRequest,
    ReversalHistoryItem,
)

router = APIRouter(
    prefix="/expenses",
    tags=["Expenses"],
)


# ── Categories (dynamic) ──
@router.get("/categories")
def get_expense_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return distinct expense categories from existing data + defaults."""
    db_cats = (
        db.query(Expense.category)
        .filter(Expense.category.isnot(None))
        .distinct()
        .all()
    )
    existing = [c[0] for c in db_cats if c[0]]

    defaults = [
        # Construction (Hard Costs)
        "Site Preparation",
        "Materials",
        "Direct Labor",
        "Subcontractors",
        "Equipment Rental",
        "Permits & Fees",
        "Waste Management",
        # Project Management (Soft Costs)
        "Professional Fees",
        "Project Supervision",
        "Insurance",
        "Site Utilities",
        "Technology",
        "Safety (PPE)",
        "Travel/Logistics",
        # General
        "Office Supplies",
        "Marketing",
        "Training",
        "Meals & Entertainment",
        "Miscellaneous",
    ]
    merged = list(dict.fromkeys(defaults + existing))  # preserve order, no dupes
    return merged


# ── Expense Types (dynamic) ──
@router.get("/expense-types")
def get_expense_types(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return distinct expense types from existing data + defaults."""
    db_types = (
        db.query(Expense.expense_type)
        .filter(Expense.expense_type.isnot(None))
        .distinct()
        .all()
    )
    existing = [t[0] for t in db_types if t[0]]

    defaults = [
        "Reimbursement",
        "Corporate Card",
        "Petty Cash",
        "Direct Payment",
        "Pre-paid",
    ]
    merged = list(dict.fromkeys(defaults + existing))
    return merged


# ── Summary / Stats ──
@router.get("/summary")
def get_expense_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return aggregated expense stats for the current user."""
    import datetime
    from sqlalchemy import extract, or_

    # Admins see all their own, or maybe all users? The requirements say "hide from submitting user",
    # but admins need to see them to approve them. Wait, right now expenses has user_id.
    # If a user submits an expense, it's under their user_id. An admin looking at /api/expenses under the admin panel 
    # might be hitting a different endpoint or the same one but without user_id filtering? 
    # Let's check how admin gets expenses. Wait, if admin uses `list_expenses`, they usually get all if no user_id filter is applied. 
    # But currently `list_expenses` filters by `Expense.user_id == current_user.id`. 
    # If admin needs to see all submitted expenses, we should remove the `user_id == current_user.id` filter for superusers.
    active_filters = (
        Expense.expense_status != ExpenseStatus.DRAFT,
        Expense.expense_status != ExpenseStatus.REJECTED,
        Expense.expense_status != ExpenseStatus.REVERSED,
        Expense.expense_status != ExpenseStatus.VOID,
        Expense.expense_status != ExpenseStatus.PENDING_REVERSAL,
        or_(Expense.is_reversal == False, Expense.is_reversal.is_(None))
    )

    if current_user.is_superuser:
        base = db.query(Expense).filter(*active_filters)
    else:
        # Normal user sees their own, BUT hide >= 50k if not approved
        base = db.query(Expense).filter(
            Expense.user_id == current_user.id,
            *active_filters,
            or_(
                func.coalesce(Expense.total_after_tax, Expense.amount) < 50000,
                Expense.expense_status == ExpenseStatus.APPROVED
            )
        )

    # Use total_after_tax to include GST, defaulting to amount if missing
    gross_amount_col = func.coalesce(Expense.total_after_tax, Expense.amount)
    
    total_amount = base.with_entities(func.coalesce(func.sum(gross_amount_col), 0)).scalar()
    total_count = base.count()

    today = datetime.date.today()
    six_months_ago = today.replace(day=1) - datetime.timedelta(days=150)
    monthly_query = db.query(
        extract('year', Expense.expense_date).label('year'),
        extract('month', Expense.expense_date).label('month'),
        func.coalesce(func.sum(gross_amount_col), 0).label('total'),
        func.count(Expense.id).label('count'),
    ).filter(
        Expense.expense_date >= six_months_ago,
        *active_filters
    )
    
    if not current_user.is_superuser:
        monthly_query = monthly_query.filter(
            Expense.user_id == current_user.id,
            or_(gross_amount_col < 50000, Expense.expense_status == ExpenseStatus.APPROVED)
        )

    monthly_rows = monthly_query.group_by('year', 'month').order_by('year', 'month').all()
    monthly = [{"year": int(r.year), "month": int(r.month), "total": float(r.total), "count": int(r.count)} for r in monthly_rows]

    cat_query = db.query(
        Expense.category, 
        func.coalesce(func.sum(gross_amount_col), 0).label('total'), 
        func.count(Expense.id).label('count')
    ).filter(
        Expense.category.isnot(None),
        *active_filters
    )
    
    if not current_user.is_superuser:
        cat_query = cat_query.filter(
            Expense.user_id == current_user.id,
            or_(gross_amount_col < 50000, Expense.expense_status == ExpenseStatus.APPROVED)
        )
        
    cat_rows = cat_query.group_by(Expense.category).order_by(func.sum(gross_amount_col).desc()).all()
    categories = [{"category": r.category, "total": float(r.total), "count": int(r.count)} for r in cat_rows]

    status_query = db.query(Expense.expense_status, func.count(Expense.id).label('count')).filter(
        *active_filters
    )
    if not current_user.is_superuser:
        status_query = status_query.filter(
            Expense.user_id == current_user.id
        )
    status_rows = status_query.group_by(Expense.expense_status).all()
    statuses = {str(r.expense_status.value if hasattr(r.expense_status, 'value') else r.expense_status): int(r.count) for r in status_rows}

    pm_query = db.query(Expense.payment_method, func.coalesce(func.sum(gross_amount_col), 0).label('total')).filter(
        *active_filters
    )
    if not current_user.is_superuser:
        pm_query = pm_query.filter(
            Expense.user_id == current_user.id,
            or_(gross_amount_col < 50000, Expense.expense_status == ExpenseStatus.APPROVED)
        )
    pm_rows = pm_query.group_by(Expense.payment_method).all()
    payment_methods = {str(r.payment_method.value if hasattr(r.payment_method, 'value') else r.payment_method): float(r.total) for r in pm_rows}

    return {
        "total_amount": float(total_amount),
        "total_count": total_count,
        "monthly": monthly,
        "categories": categories,
        "statuses": statuses,
        "payment_methods": payment_methods,
    }


# ── Duplicate Invoice Check ──
@router.post("/check-duplicate", response_model=DuplicateCheckResponse)
def check_duplicate_invoice(
    body: DuplicateCheckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Expense).filter(
        Expense.invoice_number == body.invoice_number,
        Expense.user_id == current_user.id,
    )
    if body.vendor_name:
        query = query.filter(
            func.lower(Expense.vendor_name) == body.vendor_name.lower()
        )

    match = query.first()
    if match:
        return DuplicateCheckResponse(
            is_duplicate=True,
            matching_expense_id=str(match.id),
            matching_title=match.title,
        )
    return DuplicateCheckResponse(is_duplicate=False)


# ── CRUD ──
@router.post("/", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED)
def create_expense(
    expense_in: ExpenseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        amount = float(expense_in.amount or 0)
        exchange_rate = float(expense_in.exchange_rate or 1.0)
        
        # Check if incoming amount already includes tax, or user provided total explicit
        tax_amount = 0.0
        total_after_tax = amount
        base_amount = amount * exchange_rate

        if expense_in.tax_applicable and expense_in.tax_percentage:
            tax_perc = float(expense_in.tax_percentage) / 100.0
            
            # If front-end already sent base amount explicitly, use that 
            if expense_in.base_amount is not None and expense_in.base_amount > 0:
                base_amount = float(expense_in.base_amount) * exchange_rate
                tax_amount = base_amount * tax_perc
                total_after_tax = base_amount + tax_amount
            # Or if front-end sent total explicitly
            elif expense_in.total_after_tax is not None and expense_in.total_after_tax > 0:
                 total_after_tax = float(expense_in.total_after_tax)
                 base_amount = total_after_tax / (1 + tax_perc)
                 tax_amount = total_after_tax - base_amount
            # Default auto calculate assuming amount is base
            else:
                 tax_amount = base_amount * tax_perc
                 total_after_tax = base_amount + tax_amount

        data = expense_in.dict()

        # Clean up empty strings → None for optional fields
        for key in [
            "project_id", "task_id", "department", "cost_center", "expense_type",
            "description", "vendor_name", "vendor_type", "vendor_contact",
            "vendor_tax_id", "invoice_number", "invoice_date", "purchase_order_ref",
            "tax_type", "notes",
        ]:
            if key in data and data[key] == "":
                data[key] = None

        data["base_amount"] = base_amount
        data["tax_amount"] = tax_amount
        data["total_after_tax"] = total_after_tax
        data["user_id"] = current_user.id

        # Enforce Admin Approval Workflow for >= 50k (but ONLY if user is not saving as draft)
        if total_after_tax >= 50000 and not current_user.is_superuser and data.get("expense_status") != "draft":
            data["expense_status"] = "submitted"

        # Convert lowercase string values → Python Enum instances for SQLAlchemy Enum columns
        # PostgreSQL stores these as UPPERCASE (CASH, MEDIUM, APPROVED, etc.)
        enum_mappings = {
            "priority": Priority,
            "payment_method": PaymentMethod,
            "payment_status": ExpensePaymentStatus,
            "expense_status": ExpenseStatus,
            "allocation_type": AllocationType,
        }
        for field, enum_cls in enum_mappings.items():
            val = data.get(field)
            if val and isinstance(val, str):
                try:
                    data[field] = enum_cls(val.upper() if val.upper() in [e.value for e in enum_cls] else val)
                except (ValueError, KeyError):
                    data[field] = enum_cls(val)

        # vendor_type is nullable, handle separately
        if data.get("vendor_type") and isinstance(data["vendor_type"], str):
            try:
                data["vendor_type"] = VendorType(data["vendor_type"].upper())
            except (ValueError, KeyError):
                data["vendor_type"] = None

        # Convert allocation/attachment Pydantic models to dicts for JSON column
        if data.get("allocations"):
            data["allocations"] = [a.dict() if hasattr(a, "dict") else a for a in data["allocations"]]
        else:
            data["allocations"] = None
        if data.get("attachments"):
            data["attachments"] = [a.dict() if hasattr(a, "dict") else a for a in data["attachments"]]
        else:
            data["attachments"] = None

        expense = Expense(**data)
        db.add(expense)
        db.commit()
        db.refresh(expense)
        
        # Trigger Notification for Admins if >= 50k (only for actual submissions, not drafts)
        if total_after_tax >= 50000 and not current_user.is_superuser and str(expense.expense_status.value) != "draft":
            from app.models.notification import Notification
            from app.models.user import User
            admins = db.query(User).filter(User.is_superuser == True).all()
            for admin in admins:
                notif = Notification(
                    user_id=admin.id,
                    type="expense_approval",
                    title="Large Expense Approval Required",
                    message=f"{current_user.full_name} submitted an expense of ₹{total_after_tax:,.2f} requiring your approval.",
                    action_url="/admin/expenses/all"
                )
                db.add(notif)
            db.commit()

        return expense
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create expense: {str(e)}")


@router.get("/", response_model=List[ExpenseResponse])
def list_expenses(
    skip: int = 0,
    limit: int = 50,
    expense_status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import or_
    
    if current_user.is_superuser:
        query = db.query(Expense).options(joinedload(Expense.created_by))
    else:
        gross_amount_col = func.coalesce(Expense.total_after_tax, Expense.amount)
        query = db.query(Expense).options(joinedload(Expense.created_by)).filter(
            Expense.user_id == current_user.id,
            or_(
                Expense.expense_status == ExpenseStatus.DRAFT,
                gross_amount_col < 50000,
                Expense.expense_status == ExpenseStatus.APPROVED,
                Expense.expense_status == ExpenseStatus.REJECTED
            )
        )
        
    if expense_status:
        query = query.filter(Expense.expense_status == expense_status)
    else:
        query = query.filter(
            Expense.expense_status != ExpenseStatus.DRAFT,
            Expense.expense_status != ExpenseStatus.REJECTED,
            Expense.expense_status != ExpenseStatus.REVERSED,
            Expense.expense_status != ExpenseStatus.VOID,
            Expense.expense_status != ExpenseStatus.PENDING_REVERSAL,
            or_(Expense.is_reversal == False, Expense.is_reversal.is_(None)),
        )

    results = query.order_by(Expense.created_at.desc()).offset(skip).limit(limit).all()

    # Scrub internal notes for non-creators (e.g., admins)
    for exp in results:
        if getattr(exp, "is_internal_note", False) and exp.user_id != current_user.id:
            exp.notes = None

    return results


@router.get("/{expense_id}", response_model=ExpenseResponse)
def get_expense(
    expense_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    expense = db.query(Expense).options(joinedload(Expense.created_by)).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")

    # Scrub internal note for non-creators (e.g., admins)
    if getattr(expense, "is_internal_note", False) and expense.user_id != current_user.id:
        expense.notes = None

    return expense


@router.post("/{expense_id}/approve", response_model=ExpenseResponse)
def approve_expense(
    expense_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve a submitted expense. Admin only."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only admins can approve expenses")
        
    expense = db.query(Expense).options(joinedload(Expense.created_by)).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
        
    expense.expense_status = ExpenseStatus.APPROVED
    db.commit()
    db.refresh(expense)
    
    # Notify the user
    from app.models.notification import Notification
    notif = Notification(
        user_id=expense.user_id,
        type="expense_approved",
        title="Expense Approved",
        message=f"Your expense '{expense.title}' for ₹{(expense.total_after_tax or expense.amount):,.2f} has been approved by {current_user.full_name}.",
        action_url="/user/expenses/all"
    )
    db.add(notif)
    db.commit()
    
    return expense


@router.post("/{expense_id}/reject", response_model=ExpenseResponse)
def reject_expense(
    expense_id: UUID,
    body: RejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reject a submitted expense. Admin only."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only admins can reject expenses")
        
    expense = db.query(Expense).options(joinedload(Expense.created_by)).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
        
    expense.expense_status = ExpenseStatus.REJECTED
    expense.rejection_reason = body.reason
    db.commit()
    db.refresh(expense)
    
    # Notify the user
    from app.models.notification import Notification
    notif = Notification(
        user_id=expense.user_id,
        type="expense_rejected",
        title="Expense Rejected",
        message=f"Your expense '{expense.title}' for ₹{(expense.total_after_tax or expense.amount):,.2f} was rejected by {current_user.full_name}. Reason: {body.reason}",
        action_url="/user/expenses/rejectedexpenses"
    )
    db.add(notif)
    db.commit()
    
    return expense


# ── Reversal Endpoints ──

@router.get("/{expense_id}/reversal-preview", response_model=ReversalPreviewResponse)
def get_reversal_preview(
    expense_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a preview of the reversal impact for an expense."""
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
        
    if not current_user.is_superuser:
        if expense.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="You can only reverse your own expenses.")
        
        # Check 24 hour limit for regular users
        now = datetime.now(timezone.utc)
        created_at = expense.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (now - created_at).total_seconds() > 24 * 3600:
            raise HTTPException(status_code=403, detail="Regular users can only reverse expenses within 24 hours of creation.")
    
    # Calculate already reversed amount
    already_reversed = db.query(func.coalesce(func.sum(Expense.reversed_amount), 0)).filter(
        Expense.reversal_parent_id == expense_id,
        Expense.is_reversal == True,
        Expense.expense_status == ExpenseStatus.REVERSED
    ).scalar() or 0
    
    # If we reduce the DB amount upon reversal, current amount IS the remaining amount.
    current_amount = float(expense.total_after_tax or expense.amount)
    already_reversed_float = float(already_reversed)
    original_amount = current_amount + already_reversed_float
    
    return ReversalPreviewResponse(
        expense_id=str(expense.id),
        title=expense.title,
        vendor_name=expense.vendor_name,
        category=expense.category,
        original_amount=original_amount,
        already_reversed_amount=already_reversed_float,
        remaining_reversible_amount=current_amount,
        expense_status=expense.expense_status.value if hasattr(expense.expense_status, 'value') else str(expense.expense_status),
        expense_date=expense.expense_date,
        currency=expense.currency,
        is_fully_reversed=expense.is_fully_reversed,
    )


@router.post("/{expense_id}/reverse", response_model=ExpenseResponse)
def reverse_expense(
    expense_id: UUID,
    body: ReversalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a reversal for an approved expense."""
    expense = db.query(Expense).options(joinedload(Expense.created_by)).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
        
    if not current_user.is_superuser:
        if expense.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="You can only reverse your own expenses.")
        
        # Check 24 hour limit for regular users
        now = datetime.now(timezone.utc)
        created_at = expense.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (now - created_at).total_seconds() > 24 * 3600:
            raise HTTPException(status_code=403, detail="Regular users can only reverse expenses within 24 hours of creation.")
    
    # Validation
    status_val = expense.expense_status.value if hasattr(expense.expense_status, 'value') else str(expense.expense_status)
    if status_val not in ('approved',):
        raise HTTPException(status_code=400, detail=f"Cannot reverse expense with status '{status_val}'. Only approved expenses can be reversed.")
    if expense.is_fully_reversed:
        raise HTTPException(status_code=400, detail="This expense has already been fully reversed.")
    
    # Calculate remaining reversible amount
    already_reversed = db.query(func.coalesce(func.sum(Expense.reversed_amount), 0)).filter(
        Expense.reversal_parent_id == expense_id,
        Expense.is_reversal == True,
        Expense.expense_status == ExpenseStatus.REVERSED
    ).scalar() or 0
    
    remaining = float(expense.total_after_tax or expense.amount)
    original_amount = remaining + float(already_reversed)
    
    # Determine reversal amount
    if body.reversal_type == "FULL":
        reversal_amount = remaining
    elif body.reversal_type == "PARTIAL":
        if not body.reversed_amount or body.reversed_amount <= 0:
            raise HTTPException(status_code=400, detail="Partial reversal requires a positive reversed_amount.")
        if body.reversed_amount > remaining:
            raise HTTPException(
                status_code=400,
                detail=f"Reversal amount ({body.reversed_amount}) exceeds remaining reversible balance ({remaining:.2f})."
            )
        reversal_amount = body.reversed_amount
    else:
        raise HTTPException(status_code=400, detail="reversal_type must be 'FULL' or 'PARTIAL'.")
    
    # Build reason text
    reason_text = body.reason_category
    if body.reason_details:
        reason_text += f" — {body.reason_details}"
    if body.refund_received:
        reason_text += f" | Refund received: {body.refund_mode or 'N/A'}, Ref: {body.refund_reference or 'N/A'}"
    
    now = datetime.now(timezone.utc)
    
    # Create reversal expense record
    reversal = Expense(
        title=f"REVERSAL: {expense.title}",
        category=expense.category,
        expense_date=expense.expense_date,
        project_id=expense.project_id,
        task_id=expense.task_id,
        department=expense.department,
        cost_center=expense.cost_center,
        expense_type=expense.expense_type,
        priority=expense.priority,
        description=f"Reversal of expense '{expense.title}'",
        amount=-reversal_amount,
        currency=expense.currency,
        exchange_rate=expense.exchange_rate,
        base_amount=-reversal_amount,
        payment_method=expense.payment_method,
        payment_status=expense.payment_status,
        expense_status=ExpenseStatus.REVERSED,
        vendor_name=expense.vendor_name,
        vendor_type=expense.vendor_type,
        tax_applicable=False,
        allocation_type=expense.allocation_type,
        notes=reason_text,
        is_internal_note=True,
        user_id=expense.user_id,  # Important: inherit original owner so they can see the reversal
        is_reversal=True,
        reversal_parent_id=expense.id,
        reversal_type=body.reversal_type,
        reversal_reason=reason_text,
        reversal_date=now,
        reversed_amount=reversal_amount,
        attachments=expense.attachments,
    )
    db.add(reversal)
    
    # Check if fully reversed after this
    total_reversed_after = float(already_reversed) + reversal_amount
    if total_reversed_after >= original_amount:
        expense.is_fully_reversed = True
        expense.expense_status = ExpenseStatus.REVERSED
        
    # Deduct reversal amount proportionately from the original expense
    if remaining > 0:
        ratio = (remaining - reversal_amount) / remaining
        expense.amount = float(expense.amount or 0) * ratio
        if expense.total_after_tax:
            expense.total_after_tax = float(expense.total_after_tax) * ratio
        if expense.base_amount:
            expense.base_amount = float(expense.base_amount) * ratio
        if expense.tax_amount:
            expense.tax_amount = float(expense.tax_amount) * ratio
    
    db.flush()  # Get reversal.id
    
    # Create opposite ledger entry (if expense has a project)
    if expense.project_id:
        from app.models.financials import ProjectFinancialLedger
        
        # Find original ledger entry
        original_ledger = db.query(ProjectFinancialLedger).filter(
            ProjectFinancialLedger.reference_id == expense.id,
            ProjectFinancialLedger.reference_type == "expense"
        ).first()
        
        reversal_ledger = ProjectFinancialLedger(
            project_id=expense.project_id,
            transaction_type="Expense Reversal",
            description=f"Reversal: {expense.title} ({body.reversal_type})",
            debit=0,
            credit=reversal_amount,
            balance=-reversal_amount,
            reference_id=reversal.id,
            reference_type="expense_reversal",
            reversal_reference_id=original_ledger.id if original_ledger else None,
            created_by_id=current_user.id,
        )
        db.add(reversal_ledger)
    
    # Audit log
    from app.models.audit_log import AuditLog
    import json
    audit = AuditLog(
        user_id=current_user.id,
        action="expense_reversal_created",
        entity_type="expense",
        entity_id=expense.id,
        details=json.dumps({
            "reversal_id": str(reversal.id),
            "reversal_type": body.reversal_type,
            "reversed_amount": reversal_amount,
            "original_amount": original_amount,
            "reason": reason_text,
        })
    )
    db.add(audit)
    
    # Notification to expense owner (only if someone else reversed it)
    if str(expense.user_id) != str(current_user.id):
        from app.models.notification import Notification
        notif = Notification(
            user_id=expense.user_id,
            type="expense_reversed",
            title="Expense Reversed",
            message=f"Your expense '{expense.title}' for \u20b9{original_amount:,.2f} has been {'fully' if body.reversal_type == 'FULL' else 'partially'} reversed by {current_user.full_name}. Amount: \u20b9{reversal_amount:,.2f}. Reason: {body.reason_category}",
            action_url="/user/expenses/void"
        )
        db.add(notif)
    
    db.commit()
    db.refresh(reversal)
    
    return reversal


@router.get("/{expense_id}/reversal-history", response_model=List[ReversalHistoryItem])
def get_reversal_history(
    expense_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all reversal records for an expense."""
    reversals = db.query(Expense).options(joinedload(Expense.created_by)).filter(
        Expense.reversal_parent_id == expense_id,
        Expense.is_reversal == True,
    ).order_by(Expense.created_at.desc()).all()
    
    return reversals

@router.put("/{expense_id}", response_model=ExpenseResponse)
def update_expense(
    expense_id: UUID,
    expense_in: ExpenseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    expense = db.query(Expense).options(joinedload(Expense.created_by)).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")

    # ── 24 Hour Edit Restriction ──
    if not current_user.is_superuser:
        now_utc = datetime.now(timezone.utc)
        created_utc = expense.created_at.replace(tzinfo=timezone.utc) if expense.created_at.tzinfo is None else expense.created_at.astimezone(timezone.utc)
        if (now_utc - created_utc) > timedelta(hours=24):
            raise HTTPException(status_code=403, detail="Expenses cannot be edited after 24 hours of creation.")

    update_data = expense_in.dict(exclude_unset=True)

    # Convert nested models
    if "allocations" in update_data and update_data["allocations"]:
        update_data["allocations"] = [a.dict() if hasattr(a, "dict") else a for a in update_data["allocations"]]
    if "attachments" in update_data and update_data["attachments"]:
        update_data["attachments"] = [a.dict() if hasattr(a, "dict") else a for a in update_data["attachments"]]

    for key, value in update_data.items():
        setattr(expense, key, value)

    # Recalculate
    expense.base_amount = float(expense.amount or 0) * float(expense.exchange_rate or 1)
    if expense.tax_applicable and expense.tax_percentage:
        expense.tax_amount = float(expense.amount or 0) * (float(expense.tax_percentage) / 100)
        expense.total_after_tax = float(expense.amount or 0) + float(expense.tax_amount) - float(expense.withholding_tax or 0)

    db.commit()
    db.refresh(expense)

    # Notify admins when a draft is submitted for approval (>= 50k)
    new_status = str(expense.expense_status.value if hasattr(expense.expense_status, 'value') else expense.expense_status)
    total = float(expense.total_after_tax or expense.amount or 0)
    if new_status == "submitted" and total >= 50000 and not current_user.is_superuser:
        from app.models.notification import Notification
        admins = db.query(User).filter(User.is_superuser == True).all()
        for admin in admins:
            notif = Notification(
                user_id=admin.id,
                type="expense_approval",
                title="Expense Approval Required",
                message=f"{current_user.full_name} submitted an expense '{expense.title}' of ₹{total:,.2f} requiring your approval.",
                action_url="/admin/expenses/all"
            )
            db.add(notif)
        db.commit()

    return expense


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(
    expense_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
        
    # ── 24 Hour Delete Restriction ──
    if not current_user.is_superuser:
        now_utc = datetime.now(timezone.utc)
        created_utc = expense.created_at.replace(tzinfo=timezone.utc) if expense.created_at.tzinfo is None else expense.created_at.astimezone(timezone.utc)
        if (now_utc - created_utc) > timedelta(hours=24):
            raise HTTPException(status_code=403, detail="Expenses cannot be deleted after 24 hours of creation.")

    db.delete(expense)
    db.commit()
    return None
