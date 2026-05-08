from datetime import timedelta
import random
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.allowed_employee import AllowedEmployee
from app.schemas.user import UserCreate, UserLogin, UserResponse, UserUpdate, Token, PasswordChange, UserAdminResponse, ActivationCodeRequest
from app.utils.auth import verify_password, get_password_hash, create_access_token
from app.utils.dependencies import get_current_user
from app.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/signup", response_model=Token, status_code=status.HTTP_201_CREATED)
def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Create a new user account
    
    Args:
        user_data: User registration data
        db: Database session
        
    Returns:
        JWT access token
        
    Raises:
        HTTPException: If email already exists
    """
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Check if employee code already exists in User table
    if user_data.employee_code:
        existing_emp = db.query(User).filter(User.employee_code == user_data.employee_code).first()
        if existing_emp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Employee code already registered"
            )

        # Check against AllowedEmployee whitelist
        allowed_emp = db.query(AllowedEmployee).filter(
            AllowedEmployee.employee_code == user_data.employee_code
        ).first()

        if not allowed_emp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Employee code verification failed. Please contact administrator."
            )
            
        # Verify phone matches
        if allowed_emp.phone != user_data.phone:
             raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Phone number does not match employee records."
            )
            
        # Update allowed employee status
        allowed_emp.is_registered = True
        db.add(allowed_emp)
    
    # Create new user with all fields
    new_user = User(
        email=user_data.email,
        full_name=user_data.full_name,
        hashed_password=get_password_hash(user_data.password),
        employee_code=user_data.employee_code,
        phone=user_data.phone,
        country_code=user_data.country_code,
        address=user_data.address,
        country=user_data.country,
        state=user_data.state,
        city=user_data.city,
        gender=user_data.gender
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(new_user.id)},
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login")
def login(credentials: UserLogin):
    try:
        # Find user by email using direct psycopg2 to bypass SQLAlchemy deadlocks on Python 3.14
        import psycopg2
        import psycopg2.extras
        from app.config import get_settings
        settings = get_settings()
        
        # Use config credentials directly
        db_url = settings.DATABASE_URL
        # Parse URL manually for psycopg2 since we can't trust SQLAlchemy's parsing in this env
        # Example: postgresql://postgres:acer2gb@127.0.0.1:5432/fourreck_db
        import re
        match = re.search(r'postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)', db_url)
        if match:
            user, password, host, port, dbname = match.groups()
        else:
            # Fallback to defaults (based on user info)
            user, password, host, port, dbname = "postgres", "acer2gb", "127.0.0.1", "5432", "fourreck_db"

        try:
            conn = psycopg2.connect(
                host=host,
                user=user,
                password=password,
                port=port,
                dbname=dbname
            )
            # Use DictCursor from extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            # Fetch user details including is_superuser
            cur.execute("SELECT id, email, full_name, hashed_password, is_active, is_superuser, is_activated FROM users WHERE email = %s", (credentials.email,))
            user_row = cur.fetchone()
            cur.close()
            conn.close()
        except Exception as e:
             raise Exception(f"Database connection error: {str(e)}")

        if not user_row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Mimic User object
        class SimpleUser:
            pass
        user = SimpleUser()
        user.id = user_row['id']
        user.email = user_row['email']
        user.hashed_password = user_row['hashed_password']
        user.is_active = user_row['is_active']
        user.is_superuser = user_row['is_superuser']
        user.is_activated = user_row['is_activated']
        
        # Verify password (using the updated pbkdf2 context from utils.auth)
        if not verify_password(credentials.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Inactive user account"
            )
        
        # Create access token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=access_token_expires
        )
        
        return {
            "access_token": access_token, 
            "token_type": "bearer",
            "is_activated": user.is_activated,
            "is_superuser": user.is_superuser,
            "user": {
                "id": user.id,
                "email": user.email,
                "is_superuser": user.is_superuser
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        with open("backend_debug_err.log", "a") as f:
             f.write(f"Login Outer Error: {str(e)}\n")
             traceback.print_exc(file=f)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """
    Get current user information
    
    Args:
        current_user: Current authenticated user from dependency
        
    Returns:
        Current user data
    """
    return current_user


@router.put("/me", response_model=UserResponse)
def update_user_profile(
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update current user profile information
    """
    update_data = user_update.dict(exclude_unset=True)
    
    # Check if employee code is being changed and is unique
    if "employee_code" in update_data and update_data["employee_code"]:
        existing_emp = db.query(User).filter(
            User.employee_code == update_data["employee_code"],
            User.id != current_user.id
        ).first()
        if existing_emp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Employee code already in use"
            )
        
    for field, value in update_data.items():
        setattr(current_user, field, value)
    
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/password", status_code=status.HTTP_200_OK)
def change_password(
    passwords: PasswordChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update the current user's password with strength validation
    """
    if not verify_password(passwords.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect old password"
        )
    
    # Password strength is validated by PasswordChange schema
    current_user.hashed_password = get_password_hash(passwords.new_password)
    db.add(current_user)
    db.commit()
    
    return {"message": "Password changed successfully"}


# API for country codes (phone prefixes)
@router.get("/country-codes")
def get_country_codes():
    """
    Get list of country codes for phone number input
    """
    return [
        {"code": "US", "dial_code": "+1", "name": "United States", "max_digits": 10},
        {"code": "CA", "dial_code": "+1", "name": "Canada", "max_digits": 10},
        {"code": "GB", "dial_code": "+44", "name": "United Kingdom", "max_digits": 11},
        {"code": "IN", "dial_code": "+91", "name": "India", "max_digits": 10},
        {"code": "AU", "dial_code": "+61", "name": "Australia", "max_digits": 9},
        {"code": "DE", "dial_code": "+49", "name": "Germany", "max_digits": 11},
        {"code": "FR", "dial_code": "+33", "name": "France", "max_digits": 9},
        {"code": "JP", "dial_code": "+81", "name": "Japan", "max_digits": 10},
        {"code": "CN", "dial_code": "+86", "name": "China", "max_digits": 11},
        {"code": "BR", "dial_code": "+55", "name": "Brazil", "max_digits": 11},
        {"code": "MX", "dial_code": "+52", "name": "Mexico", "max_digits": 10},
        {"code": "RU", "dial_code": "+7", "name": "Russia", "max_digits": 10},
        {"code": "ZA", "dial_code": "+27", "name": "South Africa", "max_digits": 9},
        {"code": "AE", "dial_code": "+971", "name": "UAE", "max_digits": 9},
        {"code": "SG", "dial_code": "+65", "name": "Singapore", "max_digits": 8},
    ]


# ============== ADMIN ENDPOINTS ==============

def get_superadmin(current_user: User = Depends(get_current_user)):
    """Dependency to check if user is superadmin"""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required"
        )
    return current_user


@router.get("/admin/users", response_model=List[UserAdminResponse])
def get_all_users(
    db: Session = Depends(get_db),
    admin: User = Depends(get_superadmin)
):
    """
    Get all users (superadmin only)
    """
    users = db.query(User).order_by(User.created_at.desc()).all()
    return users


@router.get("/admin/regular-users")
def get_regular_users(
    db: Session = Depends(get_db),
    admin: User = Depends(get_superadmin)
):
    """
    Get all regular (non-admin) users for filtering (superadmin only)
    Returns simplified user data: id and full_name
    """
    users = db.query(User).filter(User.is_superuser == False).order_by(User.full_name).all()
    return [{"id": str(u.id), "full_name": u.full_name} for u in users]


@router.post("/admin/generate-code/{user_id}")
def generate_activation_code(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(get_superadmin)
):
    """
    Generate 8-digit activation code for a user (superadmin only)
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Generate 8-digit numeric code
    code = ''.join([str(random.randint(0, 9)) for _ in range(8)])
    user.activation_code = code
    db.commit()
    
    return {"activation_code": code, "user_email": user.email}


@router.post("/activate")
def activate_account(
    activation: ActivationCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Activate user account with activation code
    """
    if current_user.is_activated:
        return {"message": "Account already activated"}
    
    if not current_user.activation_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No activation code generated for this account. Contact administrator."
        )
    
    if current_user.activation_code != activation.activation_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid activation code"
        )
    
    current_user.is_activated = True
    current_user.activation_code = None  # Clear code after use
    db.commit()
    
    return {"message": "Account activated successfully"}

