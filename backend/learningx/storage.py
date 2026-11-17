"""Relational storage. PostgreSQL for deployment, SQLite for isolated local pilots."""
import os
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, ForeignKey, String, Text, DateTime, Boolean, Integer, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

def uid():
    return uuid.uuid4().hex

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    policy: Mapped[str] = mapped_column(String(32), nullable=False, default="RESTRICTED_HINT_ONLY")
    teacher_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)

class Member(Base):
    __tablename__ = "members"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (UniqueConstraint("course_id", "user_id", name="uq_course_member"),)

class Material(Base):
    __tablename__ = "materials"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    out_dir: Mapped[str] = mapped_column(Text, nullable=False)
    ingested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc), nullable=False)

class Audit(Base):
    __tablename__ = "audits"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    material_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc), nullable=False)

class Question(Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    feedback_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc), nullable=False)

class Revision(Base):
    __tablename__ = "revisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc), nullable=False)

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    # Never silently write SQLite into an ephemeral serverless filesystem.
    if os.getenv("NIT_SERVERLESS_RUNTIME") == "1" or os.getenv("RENDER_SERVICE_ID"):
        raise RuntimeError("DATABASE_URL must be configured for hosted LearningX API")
    DB_URL = "sqlite:///./learningx_dev.sqlite3"
if DB_URL.startswith("postgres://"):
    DB_URL = "postgresql+psycopg://" + DB_URL[len("postgres://"):]
elif DB_URL.startswith("postgresql://"):
    DB_URL = "postgresql+psycopg://" + DB_URL[len("postgresql://"):]
engine = create_engine(DB_URL, pool_pre_ping=True, connect_args={"check_same_thread":False} if DB_URL.startswith("sqlite:") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def init_db():
    Base.metadata.create_all(engine)
