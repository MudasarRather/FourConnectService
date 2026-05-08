from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from uuid import UUID
import uuid
import datetime

from app.database import get_db
from app.utils.dependencies import get_current_user
from app.models.user import User
from app.models.project import Project
from app.models.financials import (
    ProjectPayment, 
    ProjectFinancialLedger, 
    ProjectFinancialForecast,
    ProjectFinancialDocument,
    PaymentStatus,
    DocCategory
)
from app.schemas.financials import (
    ProjectPaymentCreate,
    ProjectPaymentResponse,
    ProjectFinancialLedgerResponse,
    ProjectFinancialForecastResponse,
    ProjectFinancialForecastUpdate,
    FinancialSummary,
    FinancialDocumentCreate,
    FinancialDocumentResponse
)

router = APIRouter(
    prefix="/project-financials",
    tags=["Project Financials"]
)

# --- Helpers ---
def get_project_or_404(db: Session, project_id: UUID):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

def update_forecast(db: Session, project_id: UUID):
    """
    Updates the financial forecast for a project based on current data.
    """
    forecast = db.query(ProjectFinancialForecast).filter(
        ProjectFinancialForecast.project_id == project_id
    ).first()
    
    if not forecast:
        forecast = ProjectFinancialForecast(project_id=project_id)
        db.add(forecast)
    
    # Actual cost from payments
    actual_cost = db.query(func.sum(ProjectPayment.amount_paid)).filter(
        ProjectPayment.project_id == project_id,
        ProjectPayment.status == PaymentStatus.COMPLETED
    ).scalar() or 0
    
    # Committed cost (Pending payments + Milestones?)
    # For now, let's say pending payments
    committed_cost = db.query(func.sum(ProjectPayment.amount_paid)).filter(
        ProjectPayment.project_id == project_id,
        ProjectPayment.status == PaymentStatus.PENDING
    ).scalar() or 0
    
    # Update forecast entries
    forecast.actual_cost = actual_cost
    forecast.committed_cost = committed_cost
    
    # Planned cost from project budget
    project = get_project_or_404(db, project_id)
    forecast.planned_cost = float(project.budget_amount or 0)
    
    # EAC calculation (simple: actual + remaining work based on % complete)
    if (forecast.forecast_total_cost or 0) == 0:
        forecast.forecast_total_cost = forecast.planned_cost
    
    current_forecast_total = float(forecast.forecast_total_cost or 0)
    current_planned = float(forecast.planned_cost or 0)
    forecast.expected_overrun = current_forecast_total - current_planned
    
    db.commit()
    db.refresh(forecast)
    return forecast

@router.get("/ping")
def ping_financials():
    return {"status": "ok", "message": "Financials router is reachable"}

@router.get("/tax-rates")
def get_tax_rates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Returns the configured tax rates (GST percentages) from the system.
    """
    import json
    from app.models.system_setting import SystemSetting
    
    # Fetch from DB
    setting = db.query(SystemSetting).filter(SystemSetting.key == "TAX_RATES").first()
    
    if setting:
        try:
            return json.loads(setting.value)
        except:
            pass
            
    # Fallback if DB fetch fails or invalid JSON
    return {
        "GST": [0, 5, 12, 18, 28],
        "VAT": [0, 5, 10, 20] 
    }

# --- Payments ---

@router.get("/{project_id}/payments", response_model=List[ProjectPaymentResponse])
def get_project_payments(project_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        print(f"Fetching payments for project {project_id}")
        get_project_or_404(db, project_id)
        payments = db.query(ProjectPayment).filter(ProjectPayment.project_id == project_id).order_by(ProjectPayment.payment_date.desc()).all()
        print(f"Found {len(payments)} payments")
        return payments
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error fetching payments: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/payments/{payment_id}", response_model=ProjectPaymentResponse)
def get_payment_details(payment_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Fetch a single payment by its internal UUID (not the user-facing payment_id string).
    """
    payment = db.query(ProjectPayment).filter(ProjectPayment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    # Ensure user has access to this project (basic check)
    # in a real app, we'd check project permissions more strictly
    get_project_or_404(db, payment.project_id)
    
    return payment

@router.post("/{project_id}/payments", response_model=ProjectPaymentResponse)
def create_project_payment(project_id: UUID, payment_in: ProjectPaymentCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        get_project_or_404(db, project_id)
        
        # Generate ID if not present
        new_payment_id = payment_in.payment_id
        if not new_payment_id:
            # Simple ID generation: PAY-YYYY-XXXX (last 4 of UUID)
            year = datetime.datetime.now().year
            suffix = str(uuid.uuid4())[:4].upper()
            new_payment_id = f"PAY-{year}-{suffix}"
            
        payment = ProjectPayment(
            **payment_in.dict(exclude={"payment_id"}),
            project_id=project_id,
            created_by_id=current_user.id,
            payment_id=new_payment_id
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        
        # Update Ledger
        ledger_amount = payment.amount_paid
        ledger_entry = ProjectFinancialLedger(
            project_id=project_id,
            transaction_date=payment.payment_date,
            description=f"Payment to {payment.vendor_name} ({payment.payment_mode})",
            # category="Payment", # Removed invalid field
            # amount=ledger_amount, # Removed invalid field
            transaction_type="Expense", # Renamed from type="Expense"
            debit=0,
            credit=ledger_amount,
            reference_id=payment.id,
            reference_type="Payment",
            created_by_id=current_user.id
        )
        db.add(ledger_entry)
        db.commit()
        
        # Update forecast
        update_forecast(db, project_id)
        
        return payment
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
        print(f"FAILED TO CREATE PAYMENT: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server Error during payment creation: {str(e)}")

@router.put("/payments/{payment_id}", response_model=ProjectPaymentResponse)
def update_project_payment(payment_id: UUID, payment_in: ProjectPaymentCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        payment = db.query(ProjectPayment).filter(ProjectPayment.id == payment_id).first()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
            
        get_project_or_404(db, payment.project_id)
        
        # Update fields
        for key, value in payment_in.dict(exclude={"payment_id"}, exclude_unset=True).items():
            setattr(payment, key, value)
            
        # CONDITIONAL FIELD CLEANUP
        # Ensure that fields irrelevant to the current payment_mode are cleared.
        # This prevents "ghost" data from appearing if a user switches modes (e.g., Bank -> Cash).
        
        mode = (payment.payment_mode or "").strip().lower()
        
        # 1. Clear Bank Details if not Bank Transfer
        if mode != "bank transfer":
            payment.bank_name = None
            payment.account_holder_name = None
            payment.account_number = None
            payment.ifsc_swift_code = None
            
        # 2. Clear Cheque No if not Cheque
        if mode != "cheque":
            payment.cheque_no = None
            
        # 3. Explicitly clear all for Cash (covered by above, but good for clarity)
        if mode == "cash":
             payment.utr_transaction_ref = None # Cash usually has no UTR
             
        db.commit()
        db.refresh(payment)
        
        # Update Linked Ledger Entry
        ledger_entry = db.query(ProjectFinancialLedger).filter(
            ProjectFinancialLedger.reference_id == payment.id,
            ProjectFinancialLedger.reference_type == "Payment"
        ).first()
        
        if ledger_entry:
            ledger_entry.transaction_date = payment.payment_date
            ledger_entry.description = f"Payment to {payment.vendor_name} ({payment.payment_mode})"
            ledger_entry.credit = payment.amount_paid
            db.commit()
            
        # Update Forecast
        update_forecast(db, payment.project_id)
        
        return payment
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
        print(f"FAILED TO UPDATE PAYMENT: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server Error during payment update: {str(e)}")

@router.delete("/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_payment(payment_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        payment = db.query(ProjectPayment).filter(ProjectPayment.id == payment_id).first()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
            
        get_project_or_404(db, payment.project_id)
        
        # 1. Delete Linked Ledger Entry
        db.query(ProjectFinancialLedger).filter(
            ProjectFinancialLedger.reference_id == payment.id,
            ProjectFinancialLedger.reference_type == "Payment"
        ).delete()
        
        # 2. Delete Payment
        project_id = payment.project_id # Store for forecast update
        db.delete(payment)
        db.commit()
            
        # 3. Update Forecast
        update_forecast(db, project_id)
        
        return None
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
        print(f"FAILED TO DELETE PAYMENT: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server Error during payment deletion: {str(e)}")

@router.post("/payments/{payment_id}/generate-receipt", response_model=ProjectPaymentResponse)
def generate_payment_receipt(payment_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate and store a receipt PDF for a payment."""
    try:
        payment = db.query(ProjectPayment).filter(ProjectPayment.id == payment_id).first()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        
        project = get_project_or_404(db, payment.project_id)
        
        # Get related milestones
        from app.models.milestone import Milestone
        milestones = []
        if payment.milestone_ids:
            milestones = db.query(Milestone).filter(Milestone.id.in_(payment.milestone_ids)).all()
        
        # Get creator
        creator = db.query(User).filter(User.id == payment.created_by_id).first()
        
        # Generate PDF
        from app.utils.receipt_generator import generate_receipt_pdf
        import os
        
        receipt_dir = "storage/payment_receipts"
        os.makedirs(receipt_dir, exist_ok=True)
        
        filename = f"Receipt_{payment.payment_id or str(payment.id)[:8]}.pdf"
        output_path = f"{receipt_dir}/{filename}"
        
        generate_receipt_pdf(payment, project, milestones, creator, output_path)
        
        # Update payment with receipt path
        payment.receipt_pdf_path = output_path
        db.commit()
        db.refresh(payment)
        
        return payment
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
        print(f"FAILED TO GENERATE RECEIPT: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server Error generating receipt: {str(e)}")

# --- Forecast & Summary ---

@router.get("/{project_id}/financials/forecast", response_model=ProjectFinancialForecastResponse)
def get_project_forecast(project_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_project_or_404(db, project_id)
    return update_forecast(db, project_id) # Returns the updated forecast

@router.put("/{project_id}/financials/forecast", response_model=ProjectFinancialForecastResponse)
def update_project_forecast(project_id: UUID, forecast_in: ProjectFinancialForecastUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_project_or_404(db, project_id)
    forecast = db.query(ProjectFinancialForecast).filter(ProjectFinancialForecast.project_id == project_id).first()
    
    if not forecast:
        forecast = ProjectFinancialForecast(project_id=project_id)
        db.add(forecast)
        
    # Manual EAC override
    if forecast_in.forecast_total_cost is not None:
        forecast.forecast_total_cost = forecast_in.forecast_total_cost
        # Recalculate variance based on new manual EAC
        forecast.expected_overrun = forecast.forecast_total_cost - forecast.planned_cost
        
    db.commit()
    db.refresh(forecast)
    return forecast

from app.models.milestone import Milestone

@router.get("/{project_id}/financials/summary", response_model=FinancialSummary)
def get_financial_summary(project_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # 1. Get Project Context
    project = get_project_or_404(db, project_id)
    project_currency = project.currency or "USD"
    
    # 2. Total Budget (From Project Settings/Definition)
    total_budget = float(project.budget_amount or 0.0)

    # 3. Calculate Budget Utilized using SAME LOGIC as ProjectDetails page
    # This is the sum of ALL milestone budget_amounts with currency conversion
    from app.utils.currency import get_rate
    
    total_allocated = 0.0
    total_consumed = 0.0
    
    milestones = db.query(Milestone).filter(Milestone.project_id == project_id).all()
    for m in milestones:
        # Use stored percentage for consistency
        val = 0.0
        if m.contribution_percentage and m.contribution_percentage > 0 and (project.budget_amount or 0) > 0:
             val = (m.contribution_percentage / 100.0) * (project.budget_amount or 0)
        else:
             rate = get_rate(m.currency, project_currency)
             val = (m.budget_amount or 0) * rate
        
        total_allocated += val
        if m.status == 'completed':
            total_consumed += val
    
    total_allocated = round(total_allocated, 2)
    total_consumed = round(total_consumed, 2)
    
    # Actual Spend = total_consumed (sum of COMPLETED milestone budgets only)
    total_spent = total_consumed
    
    # Milestone Budget = total_allocated (sum of ALL milestone budgets)
    milestone_budget_total = total_allocated

    # 4. Derived Metrics
    # Remaining Budget = Project Budget - Actual Spend
    remaining = total_budget - total_spent
    burn_rate = (total_spent / total_budget * 100) if total_budget > 0 else 0.0
    budget_utilized_percentage = burn_rate
    milestone_allocation_percentage = (milestone_budget_total / total_budget * 100) if total_budget > 0 else 0.0
    
    # Forecast Variance is now "Remaining Budget" as per user request
    variance = remaining

    return FinancialSummary(
        total_budget=float(total_budget),
        total_spent=float(total_spent),
        remaining_budget=float(remaining),
        milestone_budget=float(milestone_budget_total),
        currency=project_currency,
        burn_rate=float(burn_rate),
        forecast_variance=float(variance),
        budget_utilized_percentage=float(budget_utilized_percentage),
        milestone_allocation_percentage=float(milestone_allocation_percentage)
    )

# --- Ledger ---

@router.get("/{project_id}/ledger", response_model=List[ProjectFinancialLedgerResponse])
def get_project_ledger(project_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_project_or_404(db, project_id)
    return db.query(ProjectFinancialLedger).filter(ProjectFinancialLedger.project_id == project_id).order_by(ProjectFinancialLedger.transaction_date.desc()).all()

# --- Documents ---

@router.get("/{project_id}/documents", response_model=List[FinancialDocumentResponse])
def get_documents(project_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        BASE_URL = "http://localhost:8000/"
        all_docs = []
        doc_counter = 1
        
        # Helper: Get user name by ID
        def get_user_name(user_id):
            if not user_id:
                return "Unknown"
            user = db.query(User).filter(User.id == user_id).first()
            return user.full_name if user else "Unknown"
        
        # Helper: Generate formatted doc ID
        def gen_doc_id():
            nonlocal doc_counter
            did = f"DOC-{doc_counter:04d}"
            doc_counter += 1
            return did
        
        # Helper: Make URL absolute
        def abs_url(path):
            if not path:
                return None
            if path.startswith("http"):
                return path
            return BASE_URL + path

        import re
        def clean_filename(path, default_base):
            if not path: return f"{default_base}.pdf"
            # Normalize path separators for Windows support
            fname = path.replace("\\", "/").split("/")[-1]
            
            # Match UUID prefix (UUID + underscore)
            match = re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}_(.+)$', fname, re.IGNORECASE)
            if match:
                return match.group(1)
            # Match strict UUID (UUID + extension)
            if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.[a-z0-9]+$', fname, re.IGNORECASE):
                ext = fname.split('.')[-1]
                return f"{default_base}.{ext}"
            return fname
        
        # 1. Manual Uploads
        manual_docs = db.query(ProjectFinancialDocument).filter(ProjectFinancialDocument.project_id == project_id).all()
        for d in manual_docs:
            all_docs.append({
                "id": d.id,
                "project_id": d.project_id,
                "category": d.category or "Other",
                "file_name": clean_filename(d.file_name, "Document"),
                "file_url": abs_url(d.file_url),
                "file_size_bytes": d.file_size_bytes,
                "uploaded_by_id": d.uploaded_by_id,
                "uploaded_by_name": get_user_name(d.uploaded_by_id),
                "document_id": gen_doc_id(),
                "uploaded_at": d.uploaded_at
            })

        # 2. Project Order
        project = db.query(Project).filter(Project.id == project_id).first()
        if project and project.project_order_path:
            fname = clean_filename(project.project_order_path, "Project_Order")
            all_docs.append({
                "id": project.id,
                "project_id": project.id,
                "category": "Contract",
                "file_name": fname,
                "file_url": abs_url(project.project_order_path),
                "file_size_bytes": 0,
                "uploaded_by_id": project.created_by_id,
                "uploaded_by_name": get_user_name(project.created_by_id),
                "document_id": gen_doc_id(),
                "uploaded_at": project.created_at
            })

        # 3. Invoice Attachments (uploaded to payments as supporting docs)
        payments = db.query(ProjectPayment).filter(ProjectPayment.project_id == project_id).all()
        for p in payments:
            if p.attachments:
                atts = p.attachments if isinstance(p.attachments, list) else []
                for idx, att in enumerate(atts):
                    if isinstance(att, dict) and att.get("file_url"):
                        all_docs.append({
                            "id": f"{p.id}_inv_{idx}", 
                            "project_id": project_id,
                            "category": "Invoice",
                            "file_name": clean_filename(att.get("file_name"), f"Invoice_{idx+1}"),
                            "file_url": abs_url(att.get("file_url")),
                            "file_size_bytes": att.get("file_size_bytes") or 0,
                            "uploaded_by_id": p.created_by_id,
                            "uploaded_by_name": get_user_name(p.created_by_id),
                            "document_id": gen_doc_id(),
                            "uploaded_at": p.created_at
                        })

        # 4. Milestones
        from app.models.milestone import Milestone
        milestones = db.query(Milestone).filter(Milestone.project_id == project_id).all()
        for m in milestones:
            if m.file_path:
                fname = clean_filename(m.file_path, f"Milestone_{m.name}")
                all_docs.append({
                    "id": f"{m.id}_milestone",
                    "project_id": project_id,
                    "category": "Milestone",
                    "file_name": fname,
                    "file_url": abs_url(m.file_path),
                    "file_size_bytes": 0,
                    "uploaded_by_id": m.created_by_id,
                    "uploaded_by_name": get_user_name(m.created_by_id),
                    "document_id": gen_doc_id(),
                    "uploaded_at": m.created_at
                })

        # 5. Payment Receipt PDFs (generated receipts)
        for p in payments:
            if p.receipt_pdf_path:
                fname = clean_filename(p.receipt_pdf_path, f"Receipt_{p.payment_id}")
                all_docs.append({
                    "id": f"{p.id}_receipt",
                    "project_id": project_id,
                    "category": "Receipt",
                    "file_name": fname,
                    "file_url": abs_url(p.receipt_pdf_path),
                    "file_size_bytes": 0,
                    "uploaded_by_id": p.created_by_id,
                    "uploaded_by_name": get_user_name(p.created_by_id),
                    "document_id": gen_doc_id(),
                    "uploaded_at": p.created_at
                })

        # Sort descending by date (Handle mixed naive/aware datetimes)
        def get_sort_key(d):
            dt = d.get("uploaded_at")
            if not dt:
                return datetime.datetime.min
            if dt.tzinfo:
                return dt.replace(tzinfo=None)
            return dt

        all_docs.sort(key=get_sort_key, reverse=True)
        
        return all_docs

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error fetching documents: {e}")
        # Return empty list or partial results if strictness allows, 
        # but better to raise to signal error OR return what we have (not possible here due to crash).
        # We will re-raise to ensure frontend knows it failed (500)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{project_id}/documents", response_model=FinancialDocumentResponse)
def upload_document(project_id: UUID, doc_in: FinancialDocumentCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = ProjectFinancialDocument(
        **doc_in.dict(),
        project_id=project_id,
        uploaded_by_id=current_user.id
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc
