"""
QA-SQL REST API Service - Multi-Tenant Platform

A complete platform for text-to-SQL with:
- User authentication (register, login, JWT)
- Project management (CRUD with database URIs)
- SQL generation per project
- Query history tracking

Usage:
    python app.py --port 8000
"""

import os
import sys
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, status, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm, APIKeyHeader, OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
import uvicorn

# Add parent directory to path for qasql import
sys.path.insert(0, str(Path(__file__).parent.parent / "qasql-sdk"))

from qasql import QASQLEngine, __version__

from models import Base, User, Project, QueryHistory, APIKey, ProjectAPIKey, ProjectMember, create_database
from auth import (
    Token, UserCreate, UserResponse, UserUpdate, UserLogin,
    create_access_token, authenticate_user, create_user,
    get_password_hash, create_api_key, decode_token, validate_api_key, get_user_by_id,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    # Project security
    create_project_api_key, validate_project_api_key,
    check_project_access, get_user_project_role, invite_user_to_project,
    get_user_by_username, get_user_by_email
)


# ============================================================================
# Database Setup
# ============================================================================

# Global engine and session
engine = None
SessionLocal = None
# Use psycopg (v3) driver for better Unicode support on Windows
DATABASE_URL = "postgresql+psycopg://postgres:123@localhost:5432/qasql_platform"  # Default


def init_database(db_url: str = None):
    """Initialize database connection."""
    global engine, SessionLocal, DATABASE_URL

    if db_url:
        DATABASE_URL = db_url

    logger.info(f"Connecting to database: {DATABASE_URL}")

    try:
        # Create engine with appropriate settings
        if DATABASE_URL.startswith("sqlite"):
            engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
        else:
            engine = create_engine(DATABASE_URL)

        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        # Create tables
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully")

    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

    return engine


# Don't initialize on import - wait for main() or explicit call
# This prevents errors during module import


def get_db():
    """Dependency for database session."""
    if SessionLocal is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================================
# Pydantic Models
# ============================================================================

# Project Models
class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    db_type: str = Field(..., pattern="^(sqlite|postgresql)$")
    db_uri: str = Field(..., min_length=1)
    llm_provider: str = Field(default="anthropic", pattern="^(anthropic|openai)$")  # ollama commented out
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "name": "My Sales Database",
                "description": "Sales analytics database",
                "db_type": "sqlite",
                "db_uri": "sqlite:///./sales.db",
                "llm_provider": "anthropic",
                "llm_model": "claude-sonnet-4-5-20250929"
            }
        }


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    db_type: str
    is_active: bool
    is_setup_complete: bool
    tables_count: int
    llm_provider: str
    llm_model: str
    created_at: datetime
    last_query_at: Optional[datetime]

    class Config:
        from_attributes = True


class ProjectDetailResponse(ProjectResponse):
    db_uri: str
    schema_path: Optional[str]
    descriptions_path: Optional[str]


# Query Models
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    hint: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "question": "How many customers do we have?",
                "hint": None
            }
        }


class QueryResponse(BaseModel):
    id: int
    question: str
    hint: Optional[str]
    sql: str
    confidence: float
    reasoning: str
    successful_candidates: int
    total_candidates: int
    generation_time_ms: float
    created_at: datetime

    class Config:
        from_attributes = True


class ExecuteRequest(BaseModel):
    sql: str = Field(..., min_length=1)


class ExecuteResponse(BaseModel):
    columns: List[str]
    rows: List[List]
    row_count: int
    execution_time_ms: float


class TableInfo(BaseModel):
    name: str
    columns: int
    rows: int


class ColumnInfo(BaseModel):
    name: str
    type: str
    primary_key: bool = False
    description: Optional[str] = None


class SchemaResponse(BaseModel):
    table_name: str
    columns: List[ColumnInfo]
    row_count: int


# API Key Models
class APIKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    expires_days: Optional[int] = None


class APIKeyResponse(BaseModel):
    id: int
    name: str
    key: str  # Only shown on creation
    is_active: bool
    created_at: datetime
    expires_at: Optional[datetime]

    class Config:
        from_attributes = True


# Stats Models
class UserStats(BaseModel):
    total_projects: int
    total_queries: int
    queries_today: int
    queries_this_week: int


# Project Security Models
class ProjectAPIKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    can_query: bool = True
    can_execute: bool = False
    can_view_schema: bool = True
    rate_limit_per_hour: Optional[int] = Field(None, ge=1, le=10000)
    expires_days: Optional[int] = Field(None, ge=1, le=365)

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Production Read-Only",
                "can_query": True,
                "can_execute": False,
                "can_view_schema": True,
                "rate_limit_per_hour": 100
            }
        }


class ProjectAPIKeyResponse(BaseModel):
    id: int
    name: str
    key: str
    can_query: bool
    can_execute: bool
    can_view_schema: bool
    rate_limit_per_hour: Optional[int]
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]

    class Config:
        from_attributes = True


class ProjectMemberInvite(BaseModel):
    username_or_email: str = Field(..., min_length=1)
    role: str = Field(default="viewer", pattern="^(viewer|editor|admin)$")

    class Config:
        json_schema_extra = {
            "example": {
                "username_or_email": "colleague@example.com",
                "role": "editor"
            }
        }


class ProjectMemberResponse(BaseModel):
    id: int
    user_id: int
    username: str
    email: str
    role: str
    status: str
    invited_at: datetime
    accepted_at: Optional[datetime]

    class Config:
        from_attributes = True


class ProjectMemberUpdate(BaseModel):
    role: Optional[str] = Field(None, pattern="^(viewer|editor|admin)$")


# ============================================================================
# FastAPI App with Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup/shutdown."""
    # Startup
    global engine, SessionLocal
    if engine is None:
        db_url = os.environ.get(
            "QASQL_DATABASE_URL",
            "postgresql+psycopg://postgres:123@localhost:5432/qasql_platform"
        )
        init_database(db_url)
    yield
    # Shutdown (cleanup if needed)
    logger.info("Shutting down...")


app = FastAPI(
    title="QA-SQL Platform API",
    description="""
## Multi-Tenant Text-to-SQL Platform

Create projects, connect your databases, and generate SQL from natural language.

### Features
- **User Management**: Register, login, API keys
- **Project Management**: Create projects with your database URI
- **SQL Generation**: Convert questions to SQL
- **Query History**: Track all your queries

### Quick Start
1. **Register**: `POST /api/auth/register`
2. **Login**: `POST /api/auth/login` → Get JWT token
3. **Create Project**: `POST /api/projects` with your database URI
4. **Setup Project**: `POST /api/projects/{id}/setup`
5. **Generate SQL**: `POST /api/projects/{id}/query`

### Authentication
Include `Authorization: Bearer <token>` header in all requests.
Or use API key with `X-API-Key: <key>` header.
    """,
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Engine cache for projects
_engine_cache: dict[int, QASQLEngine] = {}

# Project API key header
project_api_key_header = APIKeyHeader(name="X-Project-Key", auto_error=False)

# Auth schemes (must be at module level for Swagger UI)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
user_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ============================================================================
# Authentication Dependencies
# ============================================================================

def get_current_user_dep(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
    api_key: str = Depends(user_api_key_header)
) -> User:
    """Dependency to get current authenticated user."""
    logger.info(f"Auth attempt - token: {token[:20] if token else 'None'}..., api_key: {api_key[:10] if api_key else 'None'}...")

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Try API key first
    if api_key:
        user = validate_api_key(db, api_key)
        if user:
            logger.info(f"Auth success via API key for user: {user.username}")
            return user

    # Try JWT token
    if token:
        token_data = decode_token(token)
        logger.info(f"Token decoded: {token_data}")
        if token_data and token_data.user_id:
            user = get_user_by_id(db, token_data.user_id)
            if user and user.is_active:
                logger.info(f"Auth success via JWT for user: {user.username}")
                return user

    logger.warning("Auth failed - no valid token or API key")
    raise credentials_exception


def get_project_engine(project: Project) -> QASQLEngine:
    """Get or create QASQLEngine for a project."""
    if project.id in _engine_cache:
        return _engine_cache[project.id]

    # Set API key in environment if needed
    if project.llm_api_key:
        if project.llm_provider == "anthropic":
            os.environ["ANTHROPIC_API_KEY"] = project.llm_api_key
        elif project.llm_provider == "openai":
            os.environ["OPENAI_API_KEY"] = project.llm_api_key

    engine = QASQLEngine(
        db_uri=project.db_uri,
        llm_provider=project.llm_provider,
        llm_model=project.llm_model,
        output_dir=f"./project_data/{project.id}"
    )

    _engine_cache[project.id] = engine
    return engine


# ============================================================================
# Root Endpoints
# ============================================================================

@app.get("/", tags=["Root"])
async def root():
    """API information."""
    return {
        "name": "QA-SQL Platform API",
        "version": __version__,
        "docs": "/docs",
        "endpoints": {
            "auth": "/api/auth/*",
            "projects": "/api/projects/*",
            "user": "/api/user/*"
        }
    }


@app.get("/api/health", tags=["Health"])
async def health():
    """Health check."""
    return {
        "status": "healthy",
        "version": __version__,
        "timestamp": datetime.utcnow().isoformat()
    }


# ============================================================================
# Auth Endpoints
# ============================================================================

@app.post("/api/auth/register", tags=["Authentication"])
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user.

    - **email**: Valid email address
    - **username**: Unique username
    - **password**: Minimum 6 characters
    - **full_name**: Optional full name
    """
    if len(user_data.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    try:
        user = create_user(db, user_data)
        return {
            "success": True,
            "message": "User registered successfully",
            "data": {
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "full_name": user.full_name,
                "created_at": user.created_at
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/login", tags=["Authentication"])
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Login with username/email and password.

    Returns JWT access token.
    """
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username}
    )

    return {
        "success": True,
        "message": "Login successful",
        "data": {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email
            }
        }
    }


@app.post("/api/auth/login/json", tags=["Authentication"])
async def login_json(credentials: UserLogin, db: Session = Depends(get_db)):
    """
    Login with JSON body (alternative to form data).
    """
    try:
        user = authenticate_user(db, credentials.username, credentials.password)
        if not user:
            return {
                "success": False,
                "error": "Incorrect username or password"
            }

        access_token = create_access_token(
            data={"sub": str(user.id), "username": user.username}
        )

        return {
            "success": True,
            "message": "Login successful",
            "data": {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email
                }
            }
        }
    except Exception as e:
        logger.error(f"Login error: {e}")
        return {"success": False, "error": "Login failed"}


# ============================================================================
# User Endpoints
# ============================================================================

@app.get("/api/user/me", tags=["User"])
async def get_me(current_user: User = Depends(get_current_user_dep)):
    """Get current user profile."""
    return {
        "success": True,
        "data": {
            "id": current_user.id,
            "email": current_user.email,
            "username": current_user.username,
            "full_name": current_user.full_name,
            "is_active": current_user.is_active,
            "is_admin": current_user.is_admin,
            "created_at": current_user.created_at
        }
    }


@app.put("/api/user/me", tags=["User"])
async def update_me(
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Update current user profile."""
    try:
        if user_update.email:
            current_user.email = user_update.email
        if user_update.full_name:
            current_user.full_name = user_update.full_name
        if user_update.password:
            current_user.hashed_password = get_password_hash(user_update.password)

        db.commit()
        db.refresh(current_user)

        return {
            "success": True,
            "message": "Profile updated successfully",
            "data": {
                "id": current_user.id,
                "email": current_user.email,
                "username": current_user.username,
                "full_name": current_user.full_name,
                "updated_at": current_user.updated_at
            }
        }
    except Exception as e:
        logger.error(f"Update profile error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to update profile"}


@app.get("/api/user/stats", tags=["User"])
async def get_user_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Get user statistics."""
    try:
        today = datetime.utcnow().date()
        week_ago = datetime.utcnow() - timedelta(days=7)

        total_projects = db.query(Project).filter(Project.owner_id == current_user.id).count()
        total_queries = db.query(QueryHistory).filter(QueryHistory.user_id == current_user.id).count()

        queries_today = db.query(QueryHistory).filter(
            QueryHistory.user_id == current_user.id,
            QueryHistory.created_at >= datetime.combine(today, datetime.min.time())
        ).count()

        queries_this_week = db.query(QueryHistory).filter(
            QueryHistory.user_id == current_user.id,
            QueryHistory.created_at >= week_ago
        ).count()

        return {
            "success": True,
            "data": {
                "total_projects": total_projects,
                "total_queries": total_queries,
                "queries_today": queries_today,
                "queries_this_week": queries_this_week
            }
        }
    except Exception as e:
        logger.error(f"Get stats error: {e}")
        return {"success": False, "error": "Failed to get statistics"}


# ============================================================================
# API Key Endpoints
# ============================================================================

@app.post("/api/user/api-keys", tags=["API Keys"])
async def create_user_api_key(
    key_data: APIKeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Create a new API key."""
    try:
        api_key = create_api_key(db, current_user.id, key_data.name, key_data.expires_days)
        return {
            "success": True,
            "message": "API key created successfully",
            "data": {
                "id": api_key.id,
                "name": api_key.name,
                "key": api_key.key,
                "is_active": api_key.is_active,
                "created_at": api_key.created_at,
                "expires_at": api_key.expires_at
            }
        }
    except Exception as e:
        logger.error(f"Create API key error: {e}")
        return {"success": False, "error": "Failed to create API key"}


@app.get("/api/user/api-keys", tags=["API Keys"])
async def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """List all API keys (keys are masked)."""
    try:
        keys = db.query(APIKey).filter(APIKey.user_id == current_user.id).all()
        return {
            "success": True,
            "count": len(keys),
            "data": [
                {
                    "id": k.id,
                    "name": k.name,
                    "key_preview": k.key[:12] + "...",
                    "is_active": k.is_active,
                    "created_at": k.created_at,
                    "last_used_at": k.last_used_at,
                    "expires_at": k.expires_at
                }
                for k in keys
            ]
        }
    except Exception as e:
        logger.error(f"List API keys error: {e}")
        return {"success": False, "error": "Failed to list API keys"}


@app.delete("/api/user/api-keys/{key_id}", tags=["API Keys"])
async def delete_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Delete an API key."""
    try:
        api_key = db.query(APIKey).filter(
            APIKey.id == key_id,
            APIKey.user_id == current_user.id
        ).first()

        if not api_key:
            return {"success": False, "error": "API key not found"}

        db.delete(api_key)
        db.commit()
        return {"success": True, "message": "API key deleted successfully"}
    except Exception as e:
        logger.error(f"Delete API key error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to delete API key"}


# ============================================================================
# Project Endpoints
# ============================================================================

@app.post("/api/projects", tags=["Projects"])
async def create_project(
    project_data: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """
    Create a new project with database connection.

    After creation, call `/api/projects/{id}/setup` to extract schema.
    """
    try:
        # Set default model based on provider (SDK default: anthropic/claude-sonnet-4-5-20250929)
        llm_model = project_data.llm_model
        if not llm_model:
            defaults = {"anthropic": "claude-sonnet-4-5-20250929", "openai": "gpt-4o"}  # ollama commented out
            llm_model = defaults.get(project_data.llm_provider, "claude-sonnet-4-5-20250929")

        project = Project(
            name=project_data.name,
            description=project_data.description,
            db_type=project_data.db_type,
            db_uri=project_data.db_uri,
            llm_provider=project_data.llm_provider,
            llm_model=llm_model,
            llm_api_key=project_data.llm_api_key,
            owner_id=current_user.id
        )

        db.add(project)
        db.commit()
        db.refresh(project)

        return {
            "success": True,
            "message": "Project created successfully",
            "data": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "db_type": project.db_type,
                "is_setup_complete": project.is_setup_complete,
                "llm_provider": project.llm_provider,
                "llm_model": project.llm_model,
                "created_at": project.created_at
            }
        }
    except Exception as e:
        logger.error(f"Create project error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to create project"}


@app.get("/api/projects", tags=["Projects"])
async def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """List all projects for current user (owned and shared)."""
    try:
        # Get owned projects
        owned = db.query(Project).filter(Project.owner_id == current_user.id).all()

        # Get shared projects (accepted membership)
        shared_memberships = db.query(ProjectMember).filter(
            ProjectMember.user_id == current_user.id,
            ProjectMember.status == "accepted"
        ).all()
        shared = [m.project for m in shared_memberships]

        all_projects = owned + shared

        return {
            "success": True,
            "count": len(all_projects),
            "data": [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "db_type": p.db_type,
                    "is_active": p.is_active,
                    "is_setup_complete": p.is_setup_complete,
                    "tables_count": p.tables_count,
                    "llm_provider": p.llm_provider,
                    "llm_model": p.llm_model,
                    "created_at": p.created_at,
                    "last_query_at": p.last_query_at,
                    "is_owner": p.owner_id == current_user.id
                }
                for p in all_projects
            ]
        }
    except Exception as e:
        logger.error(f"List projects error: {e}")
        return {"success": False, "error": "Failed to list projects"}


@app.get("/api/projects/{project_id}", tags=["Projects"])
async def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Get project details."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_view"):
            return {"success": False, "error": "Project not found or access denied"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        return {
            "success": True,
            "data": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "db_type": project.db_type,
                "db_uri": project.db_uri,
                "is_active": project.is_active,
                "is_setup_complete": project.is_setup_complete,
                "tables_count": project.tables_count,
                "llm_provider": project.llm_provider,
                "llm_model": project.llm_model,
                "schema_path": project.schema_path,
                "descriptions_path": project.descriptions_path,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
                "last_query_at": project.last_query_at,
                "is_owner": project.owner_id == current_user.id
            }
        }
    except Exception as e:
        logger.error(f"Get project error: {e}")
        return {"success": False, "error": "Failed to get project"}


@app.put("/api/projects/{project_id}", tags=["Projects"])
async def update_project(
    project_id: int,
    project_update: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Update project settings. Requires admin access."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        if project_update.name:
            project.name = project_update.name
        if project_update.description is not None:
            project.description = project_update.description
        if project_update.llm_provider:
            project.llm_provider = project_update.llm_provider
            _engine_cache.pop(project_id, None)
        if project_update.llm_model:
            project.llm_model = project_update.llm_model
            _engine_cache.pop(project_id, None)
        if project_update.llm_api_key is not None:
            project.llm_api_key = project_update.llm_api_key
            _engine_cache.pop(project_id, None)

        db.commit()
        db.refresh(project)

        return {
            "success": True,
            "message": "Project updated successfully",
            "data": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "db_type": project.db_type,
                "is_active": project.is_active,
                "is_setup_complete": project.is_setup_complete,
                "tables_count": project.tables_count,
                "llm_provider": project.llm_provider,
                "llm_model": project.llm_model,
                "created_at": project.created_at,
                "updated_at": project.updated_at
            }
        }
    except Exception as e:
        logger.error(f"Update project error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to update project"}


@app.delete("/api/projects/{project_id}", tags=["Projects"])
async def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Delete a project and all its query history."""
    try:
        project = db.query(Project).filter(
            Project.id == project_id,
            Project.owner_id == current_user.id
        ).first()

        if not project:
            return {"success": False, "error": "Project not found or you are not the owner"}

        project_name = project.name

        # Clear engine cache
        _engine_cache.pop(project_id, None)

        db.delete(project)
        db.commit()

        return {
            "success": True,
            "message": f"Project '{project_name}' deleted successfully"
        }
    except Exception as e:
        logger.error(f"Delete project error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to delete project"}


# ============================================================================
# Project Setup & Schema Endpoints
# ============================================================================

@app.post("/api/projects/{project_id}/setup", tags=["Projects"])
async def setup_project(
    project_id: int,
    force: bool = Query(False, description="Force regenerate schema"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """
    Setup project: extract schema and generate descriptions.

    This is required before running queries. Requires admin access.
    """
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        # Clear cache if force
        if force:
            _engine_cache.pop(project_id, None)

        engine = get_project_engine(project)
        result = engine.setup(force=force)

        # Update project
        project.is_setup_complete = result.success
        project.tables_count = result.tables_found or 0
        project.schema_path = result.schema_path
        project.descriptions_path = result.descriptions_path
        db.commit()

        return {
            "success": result.success,
            "message": "Project setup completed" if result.success else "Project setup failed",
            "data": {
                "database_name": result.database_name,
                "tables_found": result.tables_found,
                "schema_path": result.schema_path,
                "descriptions_path": result.descriptions_path
            },
            "errors": result.errors if result.errors else None
        }

    except Exception as e:
        logger.error(f"Setup project error: {e}")
        return {"success": False, "error": f"Setup failed: {str(e)}"}


@app.get("/api/projects/{project_id}/tables", tags=["Schema"])
async def list_project_tables(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """List all tables in project database."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_view"):
            return {"success": False, "error": "Project not found or access denied"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        if not project.is_setup_complete:
            return {"success": False, "error": "Project setup not complete. Call /setup first."}

        engine = get_project_engine(project)
        if not engine._initialized:
            engine.setup()

        tables = engine.get_tables()
        schema = engine.get_schema()

        table_list = [
            {
                "name": t,
                "columns": len(schema.get(t, {}).get("columns", [])),
                "rows": schema.get(t, {}).get("row_count", 0)
            }
            for t in tables
        ]

        return {
            "success": True,
            "project_id": project_id,
            "count": len(table_list),
            "data": table_list
        }
    except Exception as e:
        logger.error(f"List tables error: {e}")
        return {"success": False, "error": "Failed to list tables"}


@app.get("/api/projects/{project_id}/schema/{table_name}", tags=["Schema"])
async def get_project_table_schema(
    project_id: int,
    table_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Get schema for a specific table."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_view"):
            return {"success": False, "error": "Project not found or access denied"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        engine = get_project_engine(project)
        if not engine._initialized:
            engine.setup()

        schema = engine.get_schema()
        profile = engine.get_profile()

        if table_name not in schema:
            return {"success": False, "error": f"Table '{table_name}' not found"}

        table_info = schema[table_name]
        table_profile = profile.get("tables", {}).get(table_name, {})

        columns = []
        for col in table_info.get("columns", []):
            desc = None
            for prof_col in table_profile.get("columns", []):
                if prof_col.get("name") == col.get("name"):
                    desc = prof_col.get("description")
                    break

            columns.append({
                "name": col.get("name", ""),
                "type": col.get("type", "TEXT"),
                "primary_key": col.get("primary_key", False),
                "description": desc
            })

        return {
            "success": True,
            "data": {
                "table_name": table_name,
                "columns": columns,
                "row_count": table_info.get("row_count", 0)
            }
        }
    except Exception as e:
        logger.error(f"Get table schema error: {e}")
        return {"success": False, "error": "Failed to get table schema"}


# ============================================================================
# Query Endpoints
# ============================================================================

@app.post("/api/projects/{project_id}/query", tags=["Query"])
async def query_project(
    project_id: int,
    request: QueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """
    Generate SQL from natural language question.

    - Without hint: 4 SQL candidates
    - With hint: 5 SQL candidates (includes SME strategy)

    Requires editor or admin access.
    """
    try:
        if not check_project_access(db, current_user.id, project_id, "can_query"):
            return {"success": False, "error": "Query access required"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        if not project.is_setup_complete:
            return {"success": False, "error": "Project setup not complete. Call /setup first."}

        engine = get_project_engine(project)
        if not engine._initialized:
            engine.setup()

        # Check for general questions
        if _is_general_question(request.question, engine):
            return {"success": False, "error": "This appears to be a general question, not a database query."}

        start_time = time.time()
        result = engine.query(question=request.question, hint=request.hint)
        generation_time = (time.time() - start_time) * 1000

        # Save to history
        query_history = QueryHistory(
            question=request.question,
            hint=request.hint,
            generated_sql=result.sql,
            confidence=result.confidence,
            reasoning=result.reasoning,
            generation_time_ms=generation_time,
            successful_candidates=result.successful_candidates,
            total_candidates=result.total_candidates,
            user_id=current_user.id,
            project_id=project_id
        )
        db.add(query_history)

        # Update project last query time
        project.last_query_at = datetime.utcnow()
        db.commit()
        db.refresh(query_history)

        return {
            "success": True,
            "message": "SQL generated successfully",
            "data": {
                "id": query_history.id,
                "question": request.question,
                "hint": request.hint,
                "sql": result.sql or "",
                "confidence": result.confidence,
                "reasoning": result.reasoning or "",
                "successful_candidates": result.successful_candidates,
                "total_candidates": result.total_candidates,
                "generation_time_ms": round(generation_time, 2),
                "created_at": query_history.created_at
            }
        }

    except Exception as e:
        logger.error(f"Query project error: {e}")
        return {"success": False, "error": f"Query failed: {str(e)}"}


@app.post("/api/projects/{project_id}/execute", tags=["Query"])
async def execute_project_sql(
    project_id: int,
    request: ExecuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Execute SQL query on project database. Requires editor or admin access."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_execute"):
            return {"success": False, "error": "Execute access required"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        engine = get_project_engine(project)
        if not engine._initialized:
            engine.setup()

        start_time = time.time()
        rows, columns = engine.execute_sql(request.sql)
        execution_time = (time.time() - start_time) * 1000

        return {
            "success": True,
            "message": "SQL executed successfully",
            "data": {
                "columns": columns,
                "rows": [list(row) for row in rows],
                "row_count": len(rows),
                "execution_time_ms": round(execution_time, 2)
            }
        }

    except Exception as e:
        logger.error(f"Execute SQL error: {e}")
        return {"success": False, "error": f"SQL Error: {str(e)}"}


@app.get("/api/projects/{project_id}/history", tags=["Query"])
async def get_query_history(
    project_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Get query history for a project."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_view"):
            return {"success": False, "error": "Project not found or access denied"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        queries = db.query(QueryHistory).filter(
            QueryHistory.project_id == project_id
        ).order_by(QueryHistory.created_at.desc()).offset(offset).limit(limit).all()

        total = db.query(QueryHistory).filter(QueryHistory.project_id == project_id).count()

        return {
            "success": True,
            "total": total,
            "count": len(queries),
            "limit": limit,
            "offset": offset,
            "data": [
                {
                    "id": q.id,
                    "question": q.question,
                    "sql": q.generated_sql,
                    "confidence": q.confidence,
                    "generation_time_ms": q.generation_time_ms,
                    "created_at": q.created_at
                }
                for q in queries
            ]
        }
    except Exception as e:
        logger.error(f"Get query history error: {e}")
        return {"success": False, "error": "Failed to get query history"}


# ============================================================================
# Project API Key Endpoints
# ============================================================================

@app.post("/api/projects/{project_id}/api-keys", tags=["Project Security"])
async def create_project_key(
    project_id: int,
    key_data: ProjectAPIKeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """
    Create a project-specific API key.

    Project keys provide restricted access to a single project.

    **Permissions:**
    - `can_query`: Allow SQL generation
    - `can_execute`: Allow SQL execution
    - `can_view_schema`: Allow viewing tables/schema

    **Rate Limiting:**
    - Set `rate_limit_per_hour` to limit requests (1-10000)

    Only project owner or admin can create keys.
    """
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        api_key = create_project_api_key(
            db=db,
            project_id=project_id,
            created_by_id=current_user.id,
            name=key_data.name,
            can_query=key_data.can_query,
            can_execute=key_data.can_execute,
            can_view_schema=key_data.can_view_schema,
            rate_limit_per_hour=key_data.rate_limit_per_hour,
            expires_days=key_data.expires_days
        )

        return {
            "success": True,
            "message": "Project API key created successfully",
            "data": {
                "id": api_key.id,
                "name": api_key.name,
                "key": api_key.key,
                "can_query": api_key.can_query,
                "can_execute": api_key.can_execute,
                "can_view_schema": api_key.can_view_schema,
                "rate_limit_per_hour": api_key.rate_limit_per_hour,
                "is_active": api_key.is_active,
                "created_at": api_key.created_at,
                "expires_at": api_key.expires_at
            }
        }
    except Exception as e:
        logger.error(f"Create project API key error: {e}")
        return {"success": False, "error": "Failed to create project API key"}


@app.get("/api/projects/{project_id}/api-keys", tags=["Project Security"])
async def list_project_keys(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """List all API keys for a project (keys are masked)."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        keys = db.query(ProjectAPIKey).filter(ProjectAPIKey.project_id == project_id).all()
        return {
            "success": True,
            "count": len(keys),
            "data": [
                {
                    "id": k.id,
                    "name": k.name,
                    "key_preview": k.key[:12] + "...",
                    "can_query": k.can_query,
                    "can_execute": k.can_execute,
                    "can_view_schema": k.can_view_schema,
                    "rate_limit_per_hour": k.rate_limit_per_hour,
                    "queries_this_hour": k.queries_this_hour,
                    "is_active": k.is_active,
                    "created_at": k.created_at,
                    "last_used_at": k.last_used_at,
                    "expires_at": k.expires_at
                }
                for k in keys
            ]
        }
    except Exception as e:
        logger.error(f"List project API keys error: {e}")
        return {"success": False, "error": "Failed to list project API keys"}


@app.delete("/api/projects/{project_id}/api-keys/{key_id}", tags=["Project Security"])
async def delete_project_key(
    project_id: int,
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Revoke a project API key."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        api_key = db.query(ProjectAPIKey).filter(
            ProjectAPIKey.id == key_id,
            ProjectAPIKey.project_id == project_id
        ).first()

        if not api_key:
            return {"success": False, "error": "API key not found"}

        db.delete(api_key)
        db.commit()
        return {"success": True, "message": "Project API key deleted successfully"}
    except Exception as e:
        logger.error(f"Delete project API key error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to delete project API key"}


@app.put("/api/projects/{project_id}/api-keys/{key_id}/toggle", tags=["Project Security"])
async def toggle_project_key(
    project_id: int,
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Enable or disable a project API key."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        api_key = db.query(ProjectAPIKey).filter(
            ProjectAPIKey.id == key_id,
            ProjectAPIKey.project_id == project_id
        ).first()

        if not api_key:
            return {"success": False, "error": "API key not found"}

        api_key.is_active = not api_key.is_active
        db.commit()

        return {
            "success": True,
            "message": f"API key {'enabled' if api_key.is_active else 'disabled'}",
            "data": {"is_active": api_key.is_active}
        }
    except Exception as e:
        logger.error(f"Toggle project API key error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to toggle project API key"}


# ============================================================================
# Project Member/Sharing Endpoints
# ============================================================================

@app.post("/api/projects/{project_id}/members", tags=["Project Security"])
async def invite_member(
    project_id: int,
    invite_data: ProjectMemberInvite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """
    Invite a user to a project.

    **Roles:**
    - `viewer`: Can view project details and schema
    - `editor`: Can generate and execute SQL queries
    - `admin`: Full access including member management

    Only project owner or admin can invite members.
    """
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        # Find user
        user = get_user_by_username(db, invite_data.username_or_email)
        if not user:
            user = get_user_by_email(db, invite_data.username_or_email)

        if not user:
            return {"success": False, "error": "User not found"}

        if user.id == current_user.id:
            return {"success": False, "error": "Cannot invite yourself"}

        member = invite_user_to_project(
            db=db,
            project_id=project_id,
            user_id=user.id,
            invited_by_id=current_user.id,
            role=invite_data.role
        )

        return {
            "success": True,
            "message": f"Invitation sent to {user.username}",
            "data": {
                "member_id": member.id,
                "role": member.role,
                "status": member.status
            }
        }

    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Invite member error: {e}")
        return {"success": False, "error": "Failed to invite member"}


@app.get("/api/projects/{project_id}/members", tags=["Project Security"])
async def list_members(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """List all members of a project."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_view"):
            return {"success": False, "error": "Access denied"}

        # Get project with owner
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        members = db.query(ProjectMember).filter(ProjectMember.project_id == project_id).all()

        result = [
            {
                "id": 0,
                "user_id": project.owner_id,
                "username": project.owner.username,
                "email": project.owner.email,
                "role": "owner",
                "status": "accepted",
                "is_owner": True
            }
        ]

        for m in members:
            result.append({
                "id": m.id,
                "user_id": m.user_id,
                "username": m.user.username,
                "email": m.user.email,
                "role": m.role,
                "status": m.status,
                "invited_at": m.invited_at,
                "accepted_at": m.accepted_at,
                "is_owner": False
            })

        return {
            "success": True,
            "count": len(result),
            "data": result
        }
    except Exception as e:
        logger.error(f"List members error: {e}")
        return {"success": False, "error": "Failed to list members"}


@app.put("/api/projects/{project_id}/members/{member_id}", tags=["Project Security"])
async def update_member(
    project_id: int,
    member_id: int,
    update_data: ProjectMemberUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Update a member's role."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        member = db.query(ProjectMember).filter(
            ProjectMember.id == member_id,
            ProjectMember.project_id == project_id
        ).first()

        if not member:
            return {"success": False, "error": "Member not found"}

        if update_data.role:
            member.role = update_data.role

        db.commit()
        return {
            "success": True,
            "message": "Member updated successfully",
            "data": {"role": member.role}
        }
    except Exception as e:
        logger.error(f"Update member error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to update member"}


@app.delete("/api/projects/{project_id}/members/{member_id}", tags=["Project Security"])
async def remove_member(
    project_id: int,
    member_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Remove a member from a project."""
    try:
        if not check_project_access(db, current_user.id, project_id, "can_manage"):
            return {"success": False, "error": "Admin access required"}

        member = db.query(ProjectMember).filter(
            ProjectMember.id == member_id,
            ProjectMember.project_id == project_id
        ).first()

        if not member:
            return {"success": False, "error": "Member not found"}

        db.delete(member)
        db.commit()
        return {"success": True, "message": "Member removed successfully"}
    except Exception as e:
        logger.error(f"Remove member error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to remove member"}


# ============================================================================
# Project Invitation Response Endpoints
# ============================================================================

@app.get("/api/user/invitations", tags=["User"])
async def list_my_invitations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """List all pending project invitations for current user."""
    try:
        invitations = db.query(ProjectMember).filter(
            ProjectMember.user_id == current_user.id,
            ProjectMember.status == "pending"
        ).all()

        return {
            "success": True,
            "count": len(invitations),
            "data": [
                {
                    "id": inv.id,
                    "project_id": inv.project_id,
                    "project_name": inv.project.name,
                    "role": inv.role,
                    "invited_by": inv.invited_by.username,
                    "invited_at": inv.invited_at
                }
                for inv in invitations
            ]
        }
    except Exception as e:
        logger.error(f"List invitations error: {e}")
        return {"success": False, "error": "Failed to list invitations"}


@app.post("/api/user/invitations/{invitation_id}/accept", tags=["User"])
async def accept_invitation(
    invitation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Accept a project invitation."""
    try:
        invitation = db.query(ProjectMember).filter(
            ProjectMember.id == invitation_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.status == "pending"
        ).first()

        if not invitation:
            return {"success": False, "error": "Invitation not found"}

        invitation.status = "accepted"
        invitation.accepted_at = datetime.utcnow()
        db.commit()

        return {
            "success": True,
            "message": f"Joined project: {invitation.project.name}",
            "data": {
                "project_id": invitation.project_id,
                "project_name": invitation.project.name,
                "role": invitation.role
            }
        }
    except Exception as e:
        logger.error(f"Accept invitation error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to accept invitation"}


@app.post("/api/user/invitations/{invitation_id}/reject", tags=["User"])
async def reject_invitation(
    invitation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_dep)
):
    """Reject a project invitation."""
    try:
        invitation = db.query(ProjectMember).filter(
            ProjectMember.id == invitation_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.status == "pending"
        ).first()

        if not invitation:
            return {"success": False, "error": "Invitation not found"}

        invitation.status = "rejected"
        db.commit()

        return {"success": True, "message": "Invitation rejected"}
    except Exception as e:
        logger.error(f"Reject invitation error: {e}")
        db.rollback()
        return {"success": False, "error": "Failed to reject invitation"}


# ============================================================================
# Project Access with Project API Key
# ============================================================================

@app.post("/api/projects/{project_id}/query/key", tags=["Query"])
async def query_project_with_key(
    project_id: int,
    request: QueryRequest,
    db: Session = Depends(get_db),
    project_key: str = Depends(project_api_key_header)
):
    """
    Generate SQL using a project API key.

    Include `X-Project-Key: <key>` header instead of user auth.
    """
    try:
        if not project_key:
            return {"success": False, "error": "X-Project-Key header required"}

        # Validate project key
        api_key = validate_project_api_key(db, project_key, project_id)
        if not api_key:
            return {"success": False, "error": "Invalid API key, expired, or rate limited"}

        if not api_key.can_query:
            return {"success": False, "error": "API key does not have query permission"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        if not project.is_setup_complete:
            return {"success": False, "error": "Project setup not complete"}

        engine = get_project_engine(project)
        if not engine._initialized:
            engine.setup()

        # Check for general questions
        if _is_general_question(request.question, engine):
            return {"success": False, "error": "This appears to be a general question, not a database query."}

        start_time = time.time()
        result = engine.query(question=request.question, hint=request.hint)
        generation_time = (time.time() - start_time) * 1000

        # Save to history (use key creator as user)
        query_history = QueryHistory(
            question=request.question,
            hint=request.hint,
            generated_sql=result.sql,
            confidence=result.confidence,
            reasoning=result.reasoning,
            generation_time_ms=generation_time,
            successful_candidates=result.successful_candidates,
            total_candidates=result.total_candidates,
            user_id=api_key.created_by_id,
            project_id=project_id
        )
        db.add(query_history)
        project.last_query_at = datetime.utcnow()
        db.commit()
        db.refresh(query_history)

        return {
            "success": True,
            "message": "SQL generated successfully",
            "data": {
                "id": query_history.id,
                "question": request.question,
                "hint": request.hint,
                "sql": result.sql or "",
                "confidence": result.confidence,
                "reasoning": result.reasoning or "",
                "successful_candidates": result.successful_candidates,
                "total_candidates": result.total_candidates,
                "generation_time_ms": round(generation_time, 2),
                "created_at": query_history.created_at
            }
        }

    except Exception as e:
        logger.error(f"Query with key error: {e}")
        return {"success": False, "error": f"Query failed: {str(e)}"}


@app.post("/api/projects/{project_id}/execute/key", tags=["Query"])
async def execute_project_sql_with_key(
    project_id: int,
    request: ExecuteRequest,
    db: Session = Depends(get_db),
    project_key: str = Depends(project_api_key_header)
):
    """
    Execute SQL using a project API key.

    Include `X-Project-Key: <key>` header instead of user auth.
    """
    try:
        if not project_key:
            return {"success": False, "error": "X-Project-Key header required"}

        api_key = validate_project_api_key(db, project_key, project_id)
        if not api_key:
            return {"success": False, "error": "Invalid API key, expired, or rate limited"}

        if not api_key.can_execute:
            return {"success": False, "error": "API key does not have execute permission"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"success": False, "error": "Project not found"}

        engine = get_project_engine(project)
        if not engine._initialized:
            engine.setup()

        start_time = time.time()
        rows, columns = engine.execute_sql(request.sql)
        execution_time = (time.time() - start_time) * 1000

        return {
            "success": True,
            "message": "SQL executed successfully",
            "data": {
                "columns": columns,
                "rows": [list(row) for row in rows],
                "row_count": len(rows),
                "execution_time_ms": round(execution_time, 2)
            }
        }

    except Exception as e:
        logger.error(f"Execute with key error: {e}")
        return {"success": False, "error": f"SQL Error: {str(e)}"}


@app.get("/api/projects/{project_id}/tables/key", tags=["Schema"])
async def list_project_tables_with_key(
    project_id: int,
    db: Session = Depends(get_db),
    project_key: str = Depends(project_api_key_header)
):
    """List tables using a project API key."""
    try:
        if not project_key:
            return {"success": False, "error": "X-Project-Key header required"}

        api_key = validate_project_api_key(db, project_key, project_id)
        if not api_key:
            return {"success": False, "error": "Invalid API key"}

        if not api_key.can_view_schema:
            return {"success": False, "error": "API key does not have schema view permission"}

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or not project.is_setup_complete:
            return {"success": False, "error": "Project not found or not setup"}

        engine = get_project_engine(project)
        if not engine._initialized:
            engine.setup()

        tables = engine.get_tables()
        schema = engine.get_schema()

        table_list = [
            {
                "name": t,
                "columns": len(schema.get(t, {}).get("columns", [])),
                "rows": schema.get(t, {}).get("row_count", 0)
            }
            for t in tables
        ]

        return {
            "success": True,
            "project_id": project_id,
            "count": len(table_list),
            "data": table_list
        }
    except Exception as e:
        logger.error(f"List tables with key error: {e}")
        return {"success": False, "error": "Failed to list tables"}


# ============================================================================
# Helper Functions
# ============================================================================

def _is_general_question(text: str, engine: QASQLEngine) -> bool:
    """Use LLM to check if question is general (not database-related)."""
    prompt = f"""Classify this input as DATABASE (needs SQL) or GENERAL (chitchat/off-topic):
Input: "{text}"
Reply with one word: DATABASE or GENERAL"""

    try:
        response = engine.llm_client.complete(prompt=prompt, max_tokens=10)
        return "GENERAL" in response.upper()
    except:
        return False


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="QA-SQL Platform API")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload")
    parser.add_argument(
        "--db-url",
        default=None,
        help="Platform database URL (default: postgresql+psycopg://postgres:123@localhost:5432/qasql_platform)"
    )
    parser.add_argument(
        "--sqlite",
        action="store_true",
        help="Use SQLite database (for local development)"
    )

    args = parser.parse_args()

    # Set database URL
    if args.db_url:
        db_url = args.db_url
    elif args.sqlite:
        db_url = "sqlite:///./qasql_platform.db"
    else:
        db_url = os.environ.get(
            "QASQL_DATABASE_URL",
            "postgresql+psycopg://postgres:123@localhost:5432/qasql_platform"
        )

    os.environ["QASQL_DATABASE_URL"] = db_url

    # Initialize database before starting
    try:
        init_database(db_url)
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}")
        print("Make sure PostgreSQL is running and the database exists.")
        print("Or use --sqlite flag for local development.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("QA-SQL Platform API")
    print(f"{'='*60}")
    print(f"Server:   http://{args.host}:{args.port}")
    print(f"Docs:     http://{args.host}:{args.port}/docs")
    print(f"Database: {db_url}")
    print(f"{'='*60}\n")

    uvicorn.run("app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
