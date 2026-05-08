from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
import os
import uuid
import shutil
from app.utils.dependencies import get_current_active_user
from app.models.user import User

router = APIRouter(
    prefix="/uploads",
    tags=["Uploads"]
)

# Storage directory (can be configured for cloud buckets later)
STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage")

# Ensure storage directory exists
os.makedirs(STORAGE_DIR, exist_ok=True)

@router.post("/file")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Generic file upload endpoint for notes and other attachments.
    Supports PDF and Images.
    """
    allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp'}
    ext = os.path.splitext(file.filename)[1].lower()
    
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type. Allowed: PDF, Images.")
        
    file_content = await file.read()
    
    # Validate Size (5MB)
    if len(file_content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")
        
    # Generate Filename
    unique_filename = f"{uuid.uuid4()}{ext}"
    # Store in 'notes' subdirectory as requested
    file_path = os.path.join(STORAGE_DIR, "notes", unique_filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    try:
        with open(file_path, "wb") as f:
            f.write(file_content)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")
         
    # Return URL/Path
    return {
        "success": True, 
        "file_path": f"storage/notes/{unique_filename}",
        "file_url": f"/storage/notes/{unique_filename}", 
        "original_filename": file.filename,
        "file_size": len(file_content)
    }

# ... keep existing endpoints ...

# PDF magic bytes (header)
PDF_MAGIC_BYTES = b'%PDF-'

def validate_pdf_magic_bytes(file_content: bytes) -> bool:
    """
    Validate that file content starts with PDF magic bytes.
    This prevents fake PDFs (files with .pdf extension but different content).
    """
    return file_content[:5] == PDF_MAGIC_BYTES

@router.post("/project-order")
async def upload_project_order(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload a project order PDF file.
    
    Validates:
    - File extension is .pdf
    - File content starts with PDF magic bytes (%PDF-)
    
    Returns the relative path to store in database.
    """
    
    # Validate file extension
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(
            status_code=400, 
            detail="Only PDF files are allowed. File must have .pdf extension."
        )
    
    # Read file content
    file_content = await file.read()
    
    # Validate PDF magic bytes (check if it's really a PDF)
    if not validate_pdf_magic_bytes(file_content):
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF file. The file content does not match PDF format. Please upload a valid PDF."
        )
    
    # Validate file size (max 5MB)
    max_size = 5 * 1024 * 1024  # 5MB
    if len(file_content) > max_size:
        raise HTTPException(
            status_code=400,
            detail="File too large. Maximum file size is 5MB."
        )
    
    # Generate unique filename
    file_ext = ".pdf"
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(STORAGE_DIR, "project_orders", unique_filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    # Save file
    try:
        with open(file_path, "wb") as f:
            f.write(file_content)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save file: {str(e)}"
        )
    
    # Return relative path for database storage
    # This path can be used with cloud storage buckets when deployed
    relative_path = f"storage/project_orders/{unique_filename}"
    
    return {
        "success": True,
        "file_path": relative_path,
        "original_filename": file.filename,
        "file_size": len(file_content)
    }

@router.delete("/project-order/{filename}")
async def delete_project_order(
    filename: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete a project order PDF file.
    """
    file_path = os.path.join(STORAGE_DIR, "project_orders", filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        os.remove(file_path)
        return {"success": True, "message": "File deleted"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete file: {str(e)}"
        )
@router.post("/payment-document")
async def upload_payment_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload a payment document (Invoice, Receipt, etc).
    Supports PDF, JPG, PNG.
    """
    allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
    ext = os.path.splitext(file.filename)[1].lower()
    
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type. Allowed: PDF, JPG, PNG.")
        
    file_content = await file.read()
    
    # Validate Size (5MB)
    if len(file_content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")
        
    # Generate Filename
    unique_filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(STORAGE_DIR, "payment_documents", unique_filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    try:
        with open(file_path, "wb") as f:
            f.write(file_content)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")
         
    # Return URL/Path
    return {
        "success": True, 
        "file_path": f"storage/payment_documents/{unique_filename}",
        "original_filename": file.filename,
        "file_size": len(file_content)
    }

@router.post("/expense-attachment")
async def upload_expense_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload an expense attachment (Invoice, Receipt, Image).
    Supports PDF, JPG, PNG, WEBP, GIF.
    """
    allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.gif'}
    ext = os.path.splitext(file.filename)[1].lower()
    
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type. Allowed: PDF, Images.")
        
    file_content = await file.read()
    
    # Validate Size (5MB)
    if len(file_content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")
        
    # Generate Filename
    unique_filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(STORAGE_DIR, "expenses", unique_filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    try:
        with open(file_path, "wb") as f:
             f.write(file_content)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")
         
    # Return URL/Path to store in DB attachments JSON
    return {
        "success": True, 
        "file_path": f"storage/expenses/{unique_filename}", # Server path
        "file_url": f"http://localhost:8000/storage/expenses/{unique_filename}", # Static serve path
        "original_filename": file.filename,
        "file_size": len(file_content)
    }


@router.post("/task-attachment")
async def upload_task_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload a task attachment. Stores in storage/Tasks/.
    """
    allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.gif', '.xlsx', '.xls', '.csv', '.doc', '.docx', '.txt', '.log'}
    ext = os.path.splitext(file.filename)[1].lower()
    
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type.")
        
    file_content = await file.read()
    
    if len(file_content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 10MB.")
        
    unique_filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(STORAGE_DIR, "Tasks", unique_filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    try:
        with open(file_path, "wb") as f:
             f.write(file_content)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")
         
    return {
        "success": True, 
        "file_path": f"storage/Tasks/{unique_filename}",
        "file_url": f"http://localhost:8000/storage/Tasks/{unique_filename}",
        "original_filename": file.filename,
        "file_size": len(file_content)
    }

@router.post("/dpr-attachment")
async def upload_dpr_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload a DPR attachment. Stores in storage/DPR/.
    """
    allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.gif', '.xlsx', '.xls', '.csv', '.doc', '.docx', '.txt'}
    ext = os.path.splitext(file.filename)[1].lower()
    
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type.")
        
    file_content = await file.read()
    
    if len(file_content) > 15 * 1024 * 1024: # Increased to 15MB for enterprise docs
        raise HTTPException(status_code=400, detail="File too large. Max 15MB.")
        
    unique_filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(STORAGE_DIR, "DPR", unique_filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    try:
        with open(file_path, "wb") as f:
             f.write(file_content)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")
         
    return {
        "success": True, 
        "file_path": f"storage/DPR/{unique_filename}",
        "file_url": f"http://localhost:8000/storage/DPR/{unique_filename}",
        "original_filename": file.filename,
        "file_size": len(file_content)
    }
