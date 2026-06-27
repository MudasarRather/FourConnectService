from typing import Generator, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.database import get_db
from app.utils.auth import decode_access_token
from app.models.user import User

# Security scheme for JWT Bearer token
security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Dependency to get current authenticated user from JWT token
    
    Args:
        credentials: HTTP Bearer credentials containing JWT token
        db: Database session
        
    Returns:
        Current authenticated user
        
    Raises:
        HTTPException: If token is invalid or user not found
    """
    token = credentials.credentials
    payload = decode_access_token(token)
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        # Deactivated mid-session (e.g. HR revoked ERP access during exit
        # clearance). Return 401 — NOT 400 — so the already-issued JWT is
        # treated as an authentication failure: the frontend's global 401
        # handler clears the token and redirects to login, logging the user
        # out on their next request (or within the auth heartbeat interval).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account deactivated — please sign in again",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Session invalidation on credential change. When an admin changes the user's
    # email or resets their ERP password we bump User.token_version; an issued JWT
    # carries the `tv` it was minted with, so a stale token now mismatches and 401s
    # here — the same path is_active uses, so the frontend boots the live session
    # within the auth-heartbeat window. A token with NO `tv` claim (issued before
    # this feature shipped) is treated as version 1 — the column default — so it is
    # still rejected once the user's version has been bumped, WITHOUT mass-logging-
    # out everyone: accounts whose credentials were never changed stay at version 1
    # and keep matching. (Earlier this skipped no-`tv` tokens entirely, which left a
    # pre-feature session alive even after the user's email/password was changed.)
    token_tv = payload.get("tv")
    if token_tv is None:
        token_tv = 1
    if token_tv != (user.token_version or 1):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your sign-in details were changed — please sign in again",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Dependency to ensure user is active"""
    return current_user


def get_current_superuser(
    current_user: User = Depends(get_current_user)
) -> User:
    """Dependency to ensure user is a superuser/admin"""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required"
        )
    return current_user
