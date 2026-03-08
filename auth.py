"""
Authentication Module for QA-SQL API

JWT-based authentication with password hashing.
"""

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import User, APIKey, ProjectAPIKey, ProjectMember


# ============================================================================
# Configuration
# ============================================================================

# Secret key for JWT - in production, use environment variable
# Use a fixed default for development (tokens survive restarts)
SECRET_KEY = os.environ.get("QASQL_SECRET_KEY", "qasql-dev-secret-key-change-in-production-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ============================================================================
# Pydantic Models
# ============================================================================

class Token(BaseModel):
    """Token response model."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenData(BaseModel):
    """Token payload data."""
    user_id: Optional[int] = None
    username: Optional[str] = None


class UserCreate(BaseModel):
    """User registration model."""
    email: str
    username: str
    password: str
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    """User login model."""
    username: str  # Can be username or email
    password: str


class UserResponse(BaseModel):
    """User response model (no password)."""
    id: int
    email: str
    username: str
    full_name: Optional[str]
    is_active: bool
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """User update model."""
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None


# ============================================================================
# Password Functions
# ============================================================================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


# ============================================================================
# Token Functions
# ============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[TokenData]:
    """Decode and validate JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        username: str = payload.get("username")

        if user_id_str is None:
            return None

        return TokenData(user_id=int(user_id_str), username=username)
    except JWTError:
        return None
    except (ValueError, TypeError):
        return None


# ============================================================================
# User Functions
# ============================================================================

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get user by email."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """Get user by username."""
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Get user by ID."""
    return db.query(User).filter(User.id == user_id).first()


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """Authenticate user by username/email and password."""
    # Try username first
    user = get_user_by_username(db, username)

    # Try email if username not found
    if not user:
        user = get_user_by_email(db, username)

    if not user:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    return user


def create_user(db: Session, user_data: UserCreate) -> User:
    """Create a new user."""
    # Check if email exists
    if get_user_by_email(db, user_data.email):
        raise ValueError("Email already registered")

    # Check if username exists
    if get_user_by_username(db, user_data.username):
        raise ValueError("Username already taken")

    # Create user
    hashed_password = get_password_hash(user_data.password)
    user = User(
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_password,
        full_name=user_data.full_name
    )

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ============================================================================
# API Key Functions
# ============================================================================

def generate_api_key() -> str:
    """Generate a new API key."""
    return f"qasql_{secrets.token_hex(24)}"


def create_api_key(db: Session, user_id: int, name: str, expires_days: int = None) -> APIKey:
    """Create a new API key for a user."""
    key = generate_api_key()

    expires_at = None
    if expires_days:
        expires_at = datetime.utcnow() + timedelta(days=expires_days)

    api_key = APIKey(
        key=key,
        name=name,
        user_id=user_id,
        expires_at=expires_at
    )

    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key


def validate_api_key(db: Session, key: str) -> Optional[User]:
    """Validate API key and return associated user."""
    api_key = db.query(APIKey).filter(
        APIKey.key == key,
        APIKey.is_active == True
    ).first()

    if not api_key:
        return None

    # Check expiration
    if api_key.expires_at and api_key.expires_at < datetime.utcnow():
        return None

    # Update last used
    api_key.last_used_at = datetime.utcnow()
    db.commit()

    return api_key.user


# ============================================================================
# Dependency Functions
# ============================================================================

def get_current_user(
    db: Session,
    token: Optional[str] = Depends(oauth2_scheme),
    api_key: Optional[str] = Depends(api_key_header)
) -> User:
    """
    Get current authenticated user from JWT token or API key.

    Priority: API Key > JWT Token
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Try API key first
    if api_key:
        user = validate_api_key(db, api_key)
        if user:
            return user

    # Try JWT token
    if token:
        token_data = decode_token(token)
        if token_data and token_data.user_id:
            user = get_user_by_id(db, token_data.user_id)
            if user and user.is_active:
                return user

    raise credentials_exception


def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current admin user."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ============================================================================
# Project API Key Functions
# ============================================================================

def generate_project_api_key() -> str:
    """Generate a new project-specific API key."""
    return f"proj_{secrets.token_hex(24)}"


def create_project_api_key(
    db: Session,
    project_id: int,
    created_by_id: int,
    name: str,
    can_query: bool = True,
    can_execute: bool = False,
    can_view_schema: bool = True,
    rate_limit_per_hour: int = None,
    expires_days: int = None
) -> ProjectAPIKey:
    """Create a new project-specific API key."""
    key = generate_project_api_key()

    expires_at = None
    if expires_days:
        expires_at = datetime.utcnow() + timedelta(days=expires_days)

    api_key = ProjectAPIKey(
        key=key,
        name=name,
        project_id=project_id,
        created_by_id=created_by_id,
        can_query=can_query,
        can_execute=can_execute,
        can_view_schema=can_view_schema,
        rate_limit_per_hour=rate_limit_per_hour,
        expires_at=expires_at
    )

    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key


def validate_project_api_key(db: Session, key: str, project_id: int) -> Optional[ProjectAPIKey]:
    """Validate project API key and check rate limits."""
    api_key = db.query(ProjectAPIKey).filter(
        ProjectAPIKey.key == key,
        ProjectAPIKey.project_id == project_id,
        ProjectAPIKey.is_active == True
    ).first()

    if not api_key:
        return None

    # Check expiration
    if api_key.expires_at and api_key.expires_at < datetime.utcnow():
        return None

    # Check rate limit
    if api_key.rate_limit_per_hour:
        now = datetime.utcnow()

        # Reset counter if hour passed
        if api_key.rate_limit_reset_at is None or api_key.rate_limit_reset_at < now:
            api_key.queries_this_hour = 0
            api_key.rate_limit_reset_at = now + timedelta(hours=1)

        # Check if over limit
        if api_key.queries_this_hour >= api_key.rate_limit_per_hour:
            return None  # Rate limited

        # Increment counter
        api_key.queries_this_hour += 1

    # Update last used
    api_key.last_used_at = datetime.utcnow()
    db.commit()

    return api_key


# ============================================================================
# Project Access Functions
# ============================================================================

# Role permissions mapping
ROLE_PERMISSIONS = {
    "viewer": {"can_view": True, "can_query": False, "can_execute": False, "can_manage": False},
    "editor": {"can_view": True, "can_query": True, "can_execute": True, "can_manage": False},
    "admin": {"can_view": True, "can_query": True, "can_execute": True, "can_manage": True},
    "owner": {"can_view": True, "can_query": True, "can_execute": True, "can_manage": True},
}


def get_user_project_role(db: Session, user_id: int, project_id: int) -> Optional[str]:
    """Get user's role in a project."""
    from models import Project

    # Check if owner
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return None

    if project.owner_id == user_id:
        return "owner"

    # Check if member
    member = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id,
        ProjectMember.status == "accepted"
    ).first()

    if member:
        return member.role

    return None


def check_project_access(
    db: Session,
    user_id: int,
    project_id: int,
    permission: str = "can_view"
) -> bool:
    """
    Check if user has specific permission on project.

    Permissions:
    - can_view: View project details, schema
    - can_query: Generate SQL queries
    - can_execute: Execute SQL
    - can_manage: Manage members, API keys, settings
    """
    role = get_user_project_role(db, user_id, project_id)
    if not role:
        return False

    return ROLE_PERMISSIONS.get(role, {}).get(permission, False)


def invite_user_to_project(
    db: Session,
    project_id: int,
    user_id: int,
    invited_by_id: int,
    role: str = "viewer"
) -> ProjectMember:
    """Invite a user to a project."""
    # Check if already member
    existing = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id
    ).first()

    if existing:
        raise ValueError("User is already a member or has pending invite")

    if role not in ["viewer", "editor", "admin"]:
        raise ValueError("Invalid role. Must be: viewer, editor, admin")

    member = ProjectMember(
        project_id=project_id,
        user_id=user_id,
        invited_by_id=invited_by_id,
        role=role,
        status="pending"
    )

    db.add(member)
    db.commit()
    db.refresh(member)
    return member
