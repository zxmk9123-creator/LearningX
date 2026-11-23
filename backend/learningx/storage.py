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

class CourseDeletion(Base):
    """A course its faculty deleted from 내 수업. The course disappears from every
    list and API, but its rows stay for audit and recovery. A separate table keeps
    courses unchanged (create_all adds tables, not columns)."""
    __tablename__ = "course_deletions"
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), primary_key=True)
    deleted_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

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


class IngestJob(Base):
    """Upload is separate from parsing. No background work runs in a request."""
    __tablename__ = "ingest_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    expected_size: Mapped[int] = mapped_column(Integer, nullable=False)
    source_key: Mapped[str] = mapped_column(String(380), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(28), default="AWAITING_UPLOAD", nullable=False, index=True)
    material_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    coverage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc), onupdate=lambda:datetime.now(timezone.utc), nullable=False)


DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    # Never silently write SQLite into an ephemeral serverless filesystem.
    if (os.getenv("NIT_SERVERLESS_RUNTIME") == "1" or os.getenv("RENDER_SERVICE_ID")
            or os.getenv("NIT_HOSTED_RUNTIME") == "1"):
        raise RuntimeError("DATABASE_URL must be configured for hosted LearningX API")
    DB_URL = "sqlite:///./learningx_dev.sqlite3"
if DB_URL.startswith("postgres://"):
    DB_URL = "postgresql+psycopg://" + DB_URL[len("postgres://"):]
elif DB_URL.startswith("postgresql://"):
    DB_URL = "postgresql+psycopg://" + DB_URL[len("postgresql://"):]
_SQLITE = DB_URL.startswith("sqlite:")
_CONNECT = {"check_same_thread": False} if _SQLITE else {}
engine = create_engine(DB_URL, pool_pre_ping=True, connect_args=_CONNECT)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _live_engine(size, overflow, **options):
    # Slide delivery lanes. Every database round trip is on the teacher-to-student
    # critical path: no pre-ping query, and no periodic recycling, because a new
    # connection costs about ten round trips. Dead connections are retried by the
    # caller. Separate pools keep a classroom's poll burst from queueing the
    # teacher's publish.
    if not _SQLITE:
        options.update(pool_pre_ping=False, pool_recycle=int(os.getenv("LIVE_POOL_RECYCLE", "-1")),
                       pool_size=size, max_overflow=overflow)
    return create_engine(DB_URL, connect_args=_CONNECT, **options)


# Read-only access checks: autocommit avoids BEGIN and ROLLBACK round trips. Size
# the pool for a whole class waking at once; overflow connections are discarded.
live_read_engine = _live_engine(int(os.getenv("LIVE_READ_POOL", "40")), 10, isolation_level="AUTOCOMMIT")
LiveReadSession = sessionmaker(bind=live_read_engine, expire_on_commit=False)
publish_engine = _live_engine(3, 3)
PublishSession = sessionmaker(bind=publish_engine, expire_on_commit=False)


def warm_live_pools():
    """Open the delivery pools before the first class instead of during a page change."""
    if _SQLITE:
        return
    from concurrent.futures import ThreadPoolExecutor

    targets = [live_read_engine] * live_read_engine.pool.size() + [publish_engine] * publish_engine.pool.size()
    # Hold every connection at once so each ping opens a distinct pooled connection.
    with ThreadPoolExecutor(len(targets)) as pool:
        connections = list(pool.map(lambda target: _hold(target), targets))
    for connection in connections:
        if connection is not None:
            connection.close()


def _hold(target):
    from sqlalchemy import text
    try:
        connection = target.connect()
        connection.execute(text("select 1"))
        return connection
    except Exception:
        return None  # The request path reports database errors.

def init_db():
    Base.metadata.create_all(engine)
