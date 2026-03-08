"""
Database Models for QA-SQL API

SQLAlchemy models for Users, Projects, and Query History.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

Base = declarative_base()


class User(Base):
    """User model for authentication."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")
    queries = relationship("QueryHistory", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.username}>"


class Project(Base):
    """Project model - each project connects to a user's database."""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    db_type = Column(String(50), nullable=False)  # sqlite, postgresql
    db_uri = Column(Text, nullable=False)  # Encrypted in production

    # LLM Configuration
    llm_provider = Column(String(50), default="ollama")  # ollama, anthropic, openai
    llm_model = Column(String(100), default="llama3.2:3b")
    llm_api_key = Column(Text, nullable=True)  # Encrypted in production

    # Status
    is_active = Column(Boolean, default=True)
    is_setup_complete = Column(Boolean, default=False)
    tables_count = Column(Integer, default=0)

    # Cache paths
    schema_path = Column(Text, nullable=True)
    descriptions_path = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_query_at = Column(DateTime, nullable=True)

    # Foreign Keys
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Relationships
    owner = relationship("User", back_populates="projects")
    queries = relationship("QueryHistory", back_populates="project", cascade="all, delete-orphan")
    api_keys = relationship("ProjectAPIKey", back_populates="project", cascade="all, delete-orphan")
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Project {self.name}>"


class QueryHistory(Base):
    """Query history model - stores all SQL generation requests."""
    __tablename__ = "query_history"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text, nullable=False)
    hint = Column(Text, nullable=True)
    generated_sql = Column(Text, nullable=True)
    confidence = Column(Float, default=0.0)
    reasoning = Column(Text, nullable=True)

    # Execution results
    was_executed = Column(Boolean, default=False)
    execution_success = Column(Boolean, nullable=True)
    execution_error = Column(Text, nullable=True)
    row_count = Column(Integer, nullable=True)

    # Performance
    generation_time_ms = Column(Float, nullable=True)
    execution_time_ms = Column(Float, nullable=True)
    successful_candidates = Column(Integer, default=0)
    total_candidates = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Foreign Keys
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)

    # Relationships
    user = relationship("User", back_populates="queries")
    project = relationship("Project", back_populates="queries")

    def __repr__(self):
        return f"<Query {self.id}: {self.question[:50]}>"


class APIKey(Base):
    """API Keys for programmatic access."""
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    # Foreign Keys
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Relationships
    user = relationship("User")

    def __repr__(self):
        return f"<APIKey {self.name}>"


class ProjectAPIKey(Base):
    """Project-specific API Keys for restricted access."""
    __tablename__ = "project_api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)

    # Permissions
    can_query = Column(Boolean, default=True)       # Can generate SQL
    can_execute = Column(Boolean, default=False)    # Can execute SQL
    can_view_schema = Column(Boolean, default=True) # Can view tables/schema

    # Rate limiting
    rate_limit_per_hour = Column(Integer, nullable=True)  # None = unlimited
    queries_this_hour = Column(Integer, default=0)
    rate_limit_reset_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    # Foreign Keys
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Relationships
    project = relationship("Project", back_populates="api_keys")
    created_by = relationship("User")

    def __repr__(self):
        return f"<ProjectAPIKey {self.name} for Project {self.project_id}>"


class ProjectMember(Base):
    """Project sharing - allows collaboration with different roles."""
    __tablename__ = "project_members"

    id = Column(Integer, primary_key=True, index=True)

    # Role: viewer (read-only), editor (query), admin (full access)
    role = Column(String(20), nullable=False, default="viewer")

    # Invite status
    status = Column(String(20), nullable=False, default="pending")  # pending, accepted, rejected
    invited_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)

    # Foreign Keys
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    invited_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Relationships
    project = relationship("Project", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])
    invited_by = relationship("User", foreign_keys=[invited_by_id])

    def __repr__(self):
        return f"<ProjectMember {self.user_id} in Project {self.project_id} as {self.role}>"


# Database setup
def get_database_url(db_path: str = "./qasql_platform.db") -> str:
    """Get database URL for the platform database."""
    return f"sqlite:///{db_path}"


def create_database(db_url: str = None):
    """Create database and tables."""
    if db_url is None:
        db_url = get_database_url()

    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(bind=engine)
    return engine


def get_session(engine):
    """Get database session."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()
