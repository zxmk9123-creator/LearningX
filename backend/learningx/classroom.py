"""Live classroom sessions: join codes, anonymous students, question board.

Scope (Question-up redesign MVP):
- Faculty opens a session for one faculty-signed material and gets a 6-char join code.
- Students join anonymously with the code; no name or email is collected.
- Students ask, revise (re-coached each time) and submit questions inside the session.
- Faculty sees submitted questions only, anonymised, can filter, hide and project them.
- Faculty exports the session as CSV without any student identifier.

Live slide snapshots and slide-linked question indexes use the same session permissions.
"""
import csv
import hashlib
import io
import json
import os
import secrets
import time
from typing import Annotated
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, and_, case, func, select, true
from sqlalchemy.orm import Mapped, Session, mapped_column, undefer

from . import engine_adapter as nit
from . import private_store
from .guest import is_guest
from .live_slides import LiveSlide, LiveTransition, QuestionSlide, build_snapshot, cached_live_page, notify, page_pdf, prepare_shared_page, revision, require_preview, source_pdf, visible_pages, wait_for_update, warm_live_pages
from .live_timing import LiveTimingRoute, record_phase
from .objectives import coaching_payload, link_latest_profile, session_profile
from . import suitability as fit
from . import judgments
from .security import actor, decode_token, issue_token, require_course, scheme, session
from .storage import Audit, Base, Course, Material, Member, Question, Revision, LiveReadSession, PublishSession, SessionLocal, User, publish_engine, uid

router = APIRouter(prefix="/v1", route_class=LiveTimingRoute)

JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
JOIN_LENGTH = 6
LIVE_PAGE_CONTENT_TYPE = "application/vnd.question-up.live-page"
MAX_INLINE_PAGE_BYTES = 2 * 1024 * 1024
STUDENT_TOKEN_MINUTES = 240
LEVEL_MAP = {"clarify": "shallow", "apply": "mid", "analyze": "mid",
             "evaluate": "deep", "create": "deep"}
# A level exists only when the model's judgment passed schema and policy checks.
# Fallback coaching (no matching approved evidence, model timeout or failure,
# guest classes without a model) is recorded as unassessed, never as a level.
# Its reason stays in the stored coaching JSON (mode, reason).
UNASSESSED = "unassessed"
LEVEL_LABEL = {"shallow": "얕은", "mid": "중간", "deep": "깊은", UNASSESSED: "판정 보류"}


def now():
    return datetime.now(timezone.utc)


class ClassSession(Base):
    __tablename__ = "class_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), nullable=False)
    join_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="OPEN")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    projected_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SessionParticipant(Base):
    __tablename__ = "session_participants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("session_id", "user_id", name="uq_session_participant"),)


class SessionQuestion(Base):
    __tablename__ = "session_questions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), unique=True, nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(12), nullable=False, default="shallow")
    first_level: Mapped[str] = mapped_column(String(12), nullable=False, default="shallow")
    latest_text: Mapped[str] = mapped_column(Text, nullable=False)
    latest_coaching_json: Mapped[str] = mapped_column(Text, nullable=False)
    submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    projected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class SlideConfusion(Base):
    __tablename__ = "slide_confusions"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    x: Mapped[int | None] = mapped_column(Integer)
    y: Mapped[int | None] = mapped_column(Integer)


class SlideConfusionArea(Base):
    __tablename__ = "slide_confusion_areas"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    rectangles: Mapped[str] = mapped_column(Text)


class SlideConfusionGeneration(Base):
    __tablename__ = "slide_confusion_generations"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(64), nullable=False)


def _confusion_tag(vote, generation):
    if generation is not None:
        return generation
    legacy = f"{vote.session_id}:{vote.page}:{vote.student_id}"
    return hashlib.sha256(legacy.encode()).hexdigest()


class QuestionAnchor(Base):
    __tablename__ = "question_anchors"
    question_id: Mapped[str] = mapped_column(ForeignKey("session_questions.id"), primary_key=True)
    location: Mapped[str] = mapped_column(Text)


# ---------- schemas ----------

class OpenSession(BaseModel):
    material_id: str = Field(min_length=1, max_length=64)


class SessionState(BaseModel):
    status: Literal["OPEN", "PAUSED", "CLOSED"]


class JoinRequest(BaseModel):
    code: str = Field(min_length=JOIN_LENGTH, max_length=JOIN_LENGTH)


class ShareSlide(BaseModel):
    page: int | None = Field(default=None, ge=1)
    pages: list[Annotated[int, Field(ge=1)]] | None = Field(default=None, min_length=1, max_length=16)
    expected_version: str | None = Field(default=None, max_length=64)
    batch_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")

    @model_validator(mode="after")
    def check_source(self):
        if (self.page is None) == (self.pages is None):
            raise ValueError("Provide either a page or pages")
        return self


class SlideRectangle(BaseModel):
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def check_area(self):
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("Selection rectangle must have a positive area")
        return self


class SlideAnchor(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    quote: str = Field(default="", max_length=240)
    rects: list[SlideRectangle] = Field(default_factory=list, max_length=2048)


class ConfusionRequest(BaseModel):
    page: int = Field(ge=1)
    anchor: SlideAnchor | None = None


class SessionAsk(BaseModel):
    slide_page: int | None = Field(default=None, ge=1)
    anchor: SlideAnchor | None = None
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    # Optional client key: a network retransmission returns the first question.
    request_id: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class SessionRevise(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class SuitabilityRecheck(BaseModel):
    revision_id: str = Field(min_length=1, max_length=64)


class SubmitRef(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    revision_id: str = Field(min_length=1, max_length=64)


class SubmitBatchIn(BaseModel):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    items: list[SubmitRef] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique(self):
        if len({item.question_id for item in self.items}) != len(self.items):
            raise ValueError("duplicate question ids")
        return self


class SingleSubmit(BaseModel):
    revision_id: str | None = Field(default=None, min_length=1, max_length=64)


# ---------- helpers ----------

def _audit(db, who, course, action, material=None, detail=""):
    db.add(Audit(actor_id=who, course_id=course, action=action, material_id=material, detail=detail[:400]))


def _session_or_404(db, session_id) -> ClassSession:
    s = db.get(ClassSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


def _faculty_session(db, session_id, user) -> ClassSession:
    s = _session_or_404(db, session_id)
    require_course(db, s.course_id, user, "faculty")
    return s


def _participant_session(db, session_id, user) -> ClassSession:
    s = _session_or_404(db, session_id)
    require_course(db, s.course_id, user, "student")
    if not db.query(SessionParticipant).filter_by(session_id=s.id, user_id=user.id).first():
        raise HTTPException(403, "Not a participant of this session")
    return s


def _new_code(db) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(JOIN_ALPHABET) for _ in range(JOIN_LENGTH))
        if not db.query(ClassSession).filter_by(join_code=code).first():
            return code
    raise HTTPException(503, "Could not allocate a join code")


def _level(coaching: dict) -> str:
    if coaching.get("mode") != "MODEL_VALIDATED":
        return UNASSESSED
    return LEVEL_MAP.get(str(coaching.get("question_level", "")), UNASSESSED)


def _student_coaching(coaching: dict) -> dict:
    """Only fields a student screen needs; never model metadata."""
    level = _level(coaching)
    return {
        "mode": coaching.get("mode"),
        "question_level": None if level == UNASSESSED else coaching.get("question_level"),
        "level": level,
        "follow_up_question": coaching.get("follow_up_question"),
        "feedback": coaching.get("feedback"),
        "evidence_refs": [{"page": r.get("page")} for r in coaching.get("evidence_refs", []) if isinstance(r, dict)],
    }


def _coach(db, s: ClassSession, user: User, text: str) -> dict:
    m = db.get(Material, s.material_id)
    c = db.get(Course, s.course_id)
    if not m or not c:
        raise HTTPException(404, "Session material not found")
    profile = session_profile(db, s.id)
    objective = coaching_payload(profile) if profile else None
    with private_store.material_workspace(m) as out:
        # Root decision ledger first: an identical question in the same context gets the
        # decision already made, without a model call (docs/ROOT_GUARDRAILS.md).
        policy = judgments.coaching_policy()
        context = {"material": m.id, "material_sha256": m.sha256, "evidence": nit.evidence_fingerprint(out),
                   "course_policy": c.policy, "teacher_approved": bool(c.teacher_approved),
                   "profile": f"{profile.id}:{profile.version}" if profile else None}
        key = judgments.key_for(judgments.COACHING, policy, context, text)
        decided = judgments.recall(db, key)
        if decided is None:
            owner = db.get(User, c.created_by)
            options = {"objective": objective} if objective else {}
            # Guest classes and deployments without a configured model use the deterministic coaching
            # (recorded as unassessed) instead of failing the student's question with a 503.
            use_model = not (owner and is_guest(owner)) and nit.model_gateway() is not None
            coaching = nit.coach_question(out, user.id, text, c.policy, c.teacher_approved, use_model=use_model, **options)
            proposal = judgments.coaching_decision(coaching)
            if proposal is not None:
                decided = judgments.record(db, key, judgments.COACHING, policy, proposal)
                if decided != proposal:  # a concurrent identical question was decided first
                    coaching = {**decided, "decision_source": "ledger"}
                else:
                    coaching = {**coaching, "decision_source": "model_proposal_accepted"}
        else:
            coaching = {**decided, "decision_source": "ledger"}
    coaching = {**coaching, "root_policy": policy}
    if profile:
        # Coaching logs record the profile version so results can be reviewed with the same settings.
        coaching = {**coaching, "objective_profile": {"id": profile.id, "version": profile.version}}
    return coaching


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _public_session(db, s: ClassSession, faculty=False) -> dict:
    return _public_sessions(db, [s], faculty)[0]


def _public_sessions(db, sessions, faculty=False) -> list[dict]:
    """Session summaries with a fixed number of queries, however many questions exist."""
    if not sessions:
        return []
    ids = [s.id for s in sessions]
    titles = dict(db.query(Course.id, Course.title)
                  .filter(Course.id.in_({s.course_id for s in sessions})).all())
    stats = {}
    if faculty:
        participants = dict(db.query(SessionParticipant.session_id, func.count())
                            .filter(SessionParticipant.session_id.in_(ids))
                            .group_by(SessionParticipant.session_id).all())
        submitted = SessionQuestion.submitted.is_(True)

        def submitted_where(condition):
            return func.coalesce(func.sum(case((and_(submitted, condition), 1), else_=0)), 0)

        # A question whose original row is missing counts as unrevised, as before.
        rows = (db.query(SessionQuestion.session_id,
                         submitted_where(true()),
                         submitted_where(SessionQuestion.level == "deep"),
                         submitted_where(and_(Question.id.isnot(None),
                                              SessionQuestion.latest_text != Question.text)),
                         submitted_where(SessionQuestion.projected_at.isnot(None)))
                .outerjoin(Question, Question.id == SessionQuestion.question_id)
                .filter(SessionQuestion.session_id.in_(ids))
                .group_by(SessionQuestion.session_id).all())
        counts = {row[0]: [int(value) for value in row[1:]] for row in rows}
        for session_id in ids:
            total, deep, revised, projected = counts.get(session_id, (0, 0, 0, 0))
            stats[session_id] = {"participants": participants.get(session_id, 0),
                                 "questions_submitted": total, "questions_deep": deep,
                                 "questions_revised": revised, "questions_projected": projected}
    result = []
    for s in sessions:
        data = {"id": s.id, "course_id": s.course_id, "course_title": titles.get(s.course_id, ""),
                "status": s.status, "created_at": _iso(s.created_at), "closed_at": _iso(s.closed_at)}
        if faculty:
            item = stats[s.id]
            data.update({
                "join_code": s.join_code if s.status != "CLOSED" else None,
                "material_id": s.material_id,
                "participants": item["participants"],
                "questions_submitted": item["questions_submitted"],
                "questions_deep": item["questions_deep"],
                "questions_revised": item["questions_revised"],
                "questions_projected": item["questions_projected"],
                "projected_id": s.projected_id,
            })
        result.append(data)
    return result


def _original_text(db, row: SessionQuestion) -> str:
    return _original_texts(db, [row])[row.question_id]


def _original_texts(db, rows) -> dict[str, str]:
    """First-asked text for each question, read in bounded batches instead of per row."""
    wanted = list({row.question_id for row in rows})
    found = {}
    for start in range(0, len(wanted), 500):
        found.update(db.query(Question.id, Question.text)
                     .filter(Question.id.in_(wanted[start:start + 500])).all())
    return {row.question_id: found.get(row.question_id, row.latest_text) for row in rows}


def _board_item(db, row: SessionQuestion, originals: dict[str, str] | None = None) -> dict:
    original = (originals if originals is not None else _original_texts(db, [row]))[row.question_id]
    return {"id": row.id, "level": row.level, "level_label": LEVEL_LABEL[row.level],
            "text": row.latest_text,
            "original": original if original != row.latest_text else None,
            "submitted_at": _iso(row.submitted_at), "hidden": row.hidden,
            "projected": row.projected_at is not None}


# ---------- join throttling (per-process, best effort) ----------

_FAILED_JOINS: dict[str, deque] = defaultdict(deque)
JOIN_FAIL_WINDOW = 60.0
JOIN_FAIL_LIMIT = 10


def _throttle(request: Request):
    key = request.client.host if request.client else "unknown"
    q = _FAILED_JOINS[key]
    t = time.monotonic()
    while q and t - q[0] > JOIN_FAIL_WINDOW:
        q.popleft()
    if len(q) >= JOIN_FAIL_LIMIT:
        raise HTTPException(429, "Too many attempts; wait a minute")
    return key, q


# ---------- faculty endpoints ----------

@router.post("/courses/{course_id}/sessions", status_code=201)
def open_session(course_id: str, data: OpenSession, background_tasks: BackgroundTasks, db: Session = Depends(session), user: User = Depends(actor)):
    require_course(db, course_id, user, "faculty")
    c = db.get(Course, course_id)
    m = db.get(Material, data.material_id)
    if not c or not m or m.course_id != course_id:
        raise HTTPException(404, "Material not found")
    if c.policy == "PROHIBITED" or (c.policy != "PERMITTED" and not c.teacher_approved):
        raise HTTPException(409, "Allow AI question coaching for this course before opening a session")
    with private_store.material_workspace(m) as out:
        if nit.verified_count(out) <= 0:
            raise HTTPException(409, "Approve evidence for this material before opening a session")
    require_preview(m)
    pages = visible_pages(m)
    if not pages:
        raise HTTPException(422, "No visible pages are available")
    first = pages[0]
    total, content = build_snapshot(m, first)
    db.expunge(m)
    for old in db.query(ClassSession).filter(ClassSession.course_id == course_id,
                                             ClassSession.status != "CLOSED").all():
        old.status = "CLOSED"
        old.closed_at = now()
    s = ClassSession(course_id=course_id, material_id=m.id, join_code=_new_code(db), created_by=user.id)
    db.add(s)
    db.flush()
    first_slide = LiveSlide(session_id=s.id, page=first, total=total, version=uid(), content=json.dumps(content))
    db.add(first_slide)
    # The class keeps the material's latest approved objective profile version;
    # without one, coaching follows the existing default flow.
    profile = link_latest_profile(db, s.id, m.id)
    fit.fix_policy(db, s.id)
    _audit(db, user.id, course_id, "session_opened", m.id,
           detail=f"objective_profile_version={profile.version}" if profile else "")
    db.commit()
    background_tasks.add_task(warm_live_pages, m, pages)
    return {**_public_session(db, s, faculty=True),
            "objective_profile": {"id": profile.id, "version": profile.version} if profile else None,
            "initial_live": {"status": s.status, "slide": {**_slide_metadata(first_slide), "visible_pages": pages},
                             "index": []}}


@router.get("/courses/{course_id}/sessions")
def list_sessions(course_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    require_course(db, course_id, user, "faculty")
    rows = (db.query(ClassSession).filter_by(course_id=course_id)
            .order_by(ClassSession.created_at.desc()).limit(50).all())
    return {"items": _public_sessions(db, rows, faculty=True)}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    s = _session_or_404(db, session_id)
    member = db.query(Member).filter_by(course_id=s.course_id, user_id=user.id).first()
    if not member:
        raise HTTPException(403, "Course access denied")
    return _public_session(db, s, faculty=member.role == "faculty")


@router.put("/sessions/{session_id}/state")
def set_state(session_id: str, data: SessionState, db: Session = Depends(session), user: User = Depends(actor)):
    s = _faculty_session(db, session_id, user)
    db.refresh(s, with_for_update=True)
    if s.status == "CLOSED":
        raise HTTPException(409, "Session already closed")
    s.status = data.status
    if data.status == "CLOSED":
        s.closed_at = now()
    _audit(db, user.id, s.course_id, "session_" + data.status.lower(), s.material_id)
    db.commit()
    notify(s.id)
    return _public_session(db, s, faculty=True)


def _live_reader(db, credentials, session_id, *, include_page=False):
    claims = decode_token(credentials)
    query = (
        select(User, ClassSession, Member.role, SessionParticipant.user_id)
        .select_from(User)
        .outerjoin(ClassSession, ClassSession.id == session_id)
        .outerjoin(Member, (Member.course_id == ClassSession.course_id) & (Member.user_id == User.id))
        .outerjoin(SessionParticipant,
                   (SessionParticipant.session_id == ClassSession.id) & (SessionParticipant.user_id == User.id))
        .where(User.id == claims["sub"])
    )
    if include_page:
        query = (query.add_columns(LiveSlide, Material)
                 .outerjoin(LiveSlide, LiveSlide.session_id == ClassSession.id)
                 .outerjoin(Material, Material.id == ClassSession.material_id)
                 .options(undefer(LiveSlide.content)))
    row = db.execute(query).one_or_none()
    if row is None or not row[0].active:
        raise HTTPException(401, "Inactive or unknown user")
    user, current, member_role, participant, *page_data = row
    scoped = claims.get("scope") is not None or user.email.endswith("@join.question-up.local")
    if scoped:
        if claims.get("scope") != "classroom" or not claims.get("session_id"):
            raise HTTPException(403, "Session-scoped authentication required")
        if claims["session_id"] != session_id:
            raise HTTPException(403, "Token belongs to a different session")
        if participant is None:
            raise HTTPException(403, "Not a participant of this session")
    if current is None:
        raise HTTPException(404, "Session not found")
    faculty = member_role == "faculty"
    if not faculty:
        if member_role is None or (member_role != "student" and user.role != "admin"):
            raise HTTPException(403, "Course access denied")
        if participant is None:
            raise HTTPException(403, "Not a participant of this session")
    return user, current, faculty, *page_data


def _read_live(request, credentials, session_id, version, cursor, queued, *, access_only=False, inline_page=False, partial_page=False):
    # Keep checkout, authorization and release on one worker. A parked poll must
    # not hold a connection while it queues for a worker to build its response.
    record_phase(request, "worker_queue", queued)
    started = time.perf_counter()
    with LiveReadSession() as db:
        try:
            user, current, faculty, *page_data = _live_reader(db, credentials, session_id, include_page=inline_page)
        except DBAPIError as exc:
            # Delivery pools are not pre-pinged; replace a connection the server closed.
            if not exc.connection_invalidated:
                raise
            db.close()
            user, current, faculty, *page_data = _live_reader(db, credentials, session_id, include_page=inline_page)
        record_phase(request, "auth", started)
        started = time.perf_counter()
        if access_only:
            return
        if partial_page and not faculty and current.status == "OPEN" and page_data:
            current_slide, material = page_data
            if current_slide is not None and current_slide.version != version:
                slide = _student_slide(_slide_metadata(current_slide, version))
                prepared = cached_live_page(material, slide["page"]) if slide.get("pdf") else None
                if prepared and len(prepared) <= MAX_INLINE_PAGE_BYTES:
                    record_phase(request, "partial_page", started)
                    return {"status": current.status, "slide": slide, "partial": True}, prepared
        result = _snapshot(db, current, user, faculty, version, cursor)
        record_phase(request, "snapshot", started)
        slide = result["slide"]
        if inline_page and not faculty and slide and slide["version"] != version and slide.get("pdf"):
            _, material = page_data
            prepared = cached_live_page(material, slide["page"])
            if prepared is not None and len(prepared) <= MAX_INLINE_PAGE_BYTES:
                return result, prepared
        return result, None


@router.get("/sessions/{session_id}/live")
async def live_snapshot(session_id: str, request: Request, response: Response, version: str = "",
                        after: str | None = None, index_version: str = "", cursor: str | None = None,
                        inline_page: bool = False, partial_page: bool = False,
                        credentials: HTTPAuthorizationCredentials | None = Depends(scheme)):
    if after is not None and after == revision(session_id) and (cursor is None or cursor == version):
        await run_in_threadpool(_read_live, request, credentials, session_id, version, cursor, time.perf_counter(), access_only=True)
        started = time.perf_counter()
        await wait_for_update(session_id, after)
        record_phase(request, "poll_wait", started)
    stamp = revision(session_id)
    snapshot, pdf = await run_in_threadpool(
        _read_live, request, credentials, session_id, version, cursor, time.perf_counter(), inline_page=inline_page,
        partial_page=partial_page and inline_page and bool(version) and after is not None and cursor is None,
    )
    response.headers["Cache-Control"] = "no-store"
    result = {**snapshot, "revision": stamp}
    if "index" in snapshot:
        index_stamp = hashlib.sha256(json.dumps(snapshot["index"], ensure_ascii=False).encode()).hexdigest()
        if index_version == index_stamp:
            del result["index"]
        result["index_version"] = index_stamp
    if pdf is not None:
        metadata = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        # Four-byte big-endian metadata length, UTF-8 JSON, then one raw PDF page.
        return Response(b"".join((len(metadata).to_bytes(4, "big"), metadata, pdf)),
                        media_type=LIVE_PAGE_CONTENT_TYPE,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    return result


# Students receive only what the current page needs. Neighbour page numbers and
# past transitions are faculty navigation data; text/image are the current
# page's legacy non-PDF content.
STUDENT_SLIDE_FIELDS = frozenset({"page", "total", "version", "sequence", "pdf", "text", "image"})


def _student_slide(metadata):
    if metadata is None:
        return None
    return {key: value for key, value in metadata.items() if key in STUDENT_SLIDE_FIELDS}


def _slide_metadata(slide, version=""):
    if slide is None:
        return None
    metadata = {"page": slide.page, "total": slide.total, "version": slide.version}
    content = json.loads(slide.content)
    if version != slide.version:
        metadata.update(content)
    elif "sequence" in content:
        metadata["sequence"] = content["sequence"]
    return metadata


def _snapshot(db, current, user, faculty, version, cursor=None):
    session_id = current.id
    if current.status == "CLOSED":
        return {"status": current.status, "slide": None, "index": []}
    slide = db.get(LiveSlide, session_id, options=[undefer(LiveSlide.content)])
    query = db.query(SessionQuestion.id, SessionQuestion.level, QuestionSlide.page,
                     SessionQuestion.latest_text, QuestionAnchor.location).join(
        QuestionSlide, QuestionSlide.question_id == SessionQuestion.id
    ).outerjoin(QuestionAnchor, QuestionAnchor.question_id == SessionQuestion.id).filter(
        SessionQuestion.session_id == session_id, SessionQuestion.level != UNASSESSED,
    )
    if faculty:
        query = query.filter(SessionQuestion.submitted.is_(True), SessionQuestion.hidden.is_(False))
    else:
        query = query.filter(SessionQuestion.student_id == user.id)
    order_time = SessionQuestion.submitted_at if faculty else SessionQuestion.created_at
    index = [{"id": row.id, "level": row.level, "page": row.page, "text": row.latest_text,
              "anchor": json.loads(row.location) if row.location else None}
             for row in query.order_by(order_time, SessionQuestion.id).limit(500)]
    confusion = None
    if slide:
        votes = db.query(SlideConfusion).filter_by(session_id=session_id, page=slide.page)
        vote_rows = votes.outerjoin(
            SlideConfusionArea, (SlideConfusion.session_id == SlideConfusionArea.session_id)
            & (SlideConfusion.page == SlideConfusionArea.page)
            & (SlideConfusion.student_id == SlideConfusionArea.student_id),
        ).outerjoin(
            SlideConfusionGeneration, (SlideConfusion.session_id == SlideConfusionGeneration.session_id)
            & (SlideConfusion.page == SlideConfusionGeneration.page)
            & (SlideConfusion.student_id == SlideConfusionGeneration.student_id),
        ).with_entities(SlideConfusion, SlideConfusionArea.rectangles, SlideConfusionGeneration.tag_id)
        rows = (vote_rows if faculty else vote_rows.filter(SlideConfusion.student_id == user.id)).all()
        if faculty or rows:
            confusion = {"count": len(rows)} if faculty else {"mine": True}
            spots = {}
            for vote, rects, generation in rows:
                cell = (vote.x, vote.y) if vote.x is not None else (-1, -1)
                x, y = (vote.x / 20, vote.y / 20) if vote.x is not None else (.05, .08)
                spot = spots.setdefault(cell, {"x": x, "y": y, "count": 0, "tag_ids": []})
                spot["count"] += 1
                spot["tag_ids"].append(_confusion_tag(vote, generation))
                if rects and ("rects_json" not in spot or rects < spot["rects_json"]):
                    spot["rects_json"] = rects
            for spot in spots.values():
                spot["tag_ids"].sort()
                if "rects_json" in spot:
                    spot["rects"] = json.loads(spot.pop("rects_json"))
            confusion["spots"] = [spots[key] for key in sorted(spots)]
    snapshot = _slide_metadata(slide, version)
    if not faculty:
        snapshot = _student_slide(snapshot)
    if faculty and snapshot and snapshot.get("pdf"):
        snapshot["visible_pages"] = visible_pages(db.get(Material, current.material_id))
    transitions = []
    # Past transitions let the presenter's view catch up; students follow the current page only.
    if faculty and cursor and slide and cursor != slide.version:
        history = db.query(LiveTransition).filter_by(session_id=session_id)
        prior = history.filter_by(version=cursor).first()
        latest = history.filter_by(version=slide.version).first()
        if prior and latest:
            transitions = [_slide_metadata(item) for item in history.filter(
                LiveTransition.position > prior.position,
                LiveTransition.position <= latest.position,
            ).order_by(LiveTransition.position).limit(32)]
    return {"status": current.status, "slide": snapshot, "index": index, "confusion": confusion, "transitions": transitions}


@router.get("/sessions/{session_id}/live/document")
def live_document(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    current = _faculty_session(db, session_id, user)
    if current.status == "CLOSED":
        raise HTTPException(409, "Class has ended")
    material = db.get(Material, current.material_id)
    pdf = source_pdf(material)
    slide = db.get(LiveSlide, session_id)
    # Bring sessions opened before automatic sharing onto the same page format.
    if slide is None or "after" not in json.loads(slide.content):
        db.refresh(current, with_for_update=True)
        if current.status == "CLOSED":
            raise HTTPException(409, "Class has ended")
        slide = db.get(LiveSlide, session_id, populate_existing=True)
        if slide is None or "after" not in json.loads(slide.content):
            pages = visible_pages(material)
            if not pages:
                raise HTTPException(422, "No visible pages are available")
            number = slide.page if slide and slide.page in pages else pages[0]
            total, content = build_snapshot(material, number)
            db.merge(LiveSlide(session_id=session_id, page=number, total=total, version=uid(),
                               content=json.dumps(content)))
            db.commit()
            notify(session_id)
    return _pdf_response(pdf)


@router.get("/sessions/{session_id}/live/page/{version}")
def live_page(session_id: str, version: str, request: Request, page: int | None = None,
              db: Session = Depends(session),
              credentials: HTTPAuthorizationCredentials | None = Depends(scheme)):
    started = time.perf_counter()
    _, current, faculty, slide, material = _live_reader(db, credentials, session_id, include_page=True)
    record_phase(request, "auth", started)
    started = time.perf_counter()
    if current.status == "CLOSED" or not slide:
        raise HTTPException(409, "This page is no longer being shared")
    historical = slide.version != version
    if historical and not faculty:
        # Students receive the page being shared now; a stale version refetches the latest state.
        raise HTTPException(409, "This page is no longer being shared")
    if historical:
        slide = db.query(LiveTransition).filter_by(session_id=session_id, version=version).first()
        if not slide:
            raise HTTPException(409, "This page is no longer being shared")
    content = json.loads(slide.content)
    if not content.get("pdf"):
        raise HTTPException(404, "No PDF for this page")
    target = slide.page if page is None else page
    if faculty and not historical:
        allowed = (slide.page, content.get("before"), content.get("after"))
    else:
        allowed = (slide.page,)
    if target not in allowed:
        raise HTTPException(403, "This page is outside the active slide window")
    db.expunge(material)
    db.rollback()
    record_phase(request, "lookup", started)
    started = time.perf_counter()
    # One cache read decides both the hit and the bytes returned: checking and
    # then reading again could miss after an eviction and skip the re-check.
    content = cached_live_page(material, target)
    if content is not None:
        record_phase(request, "pdf", started)
        return _pdf_response(content)
    content = page_pdf(material, target)
    record_phase(request, "pdf", started)
    if not faculty:
        # Loading or splitting an uncached page can take long enough for the
        # presenter to move on or for access to be revoked. Re-check before
        # returning bytes; the cached path above answers within the same instant.
        started = time.perf_counter()
        _, current, _, slide, _ = _live_reader(db, credentials, session_id, include_page=True)
        db.rollback()
        if current.status == "CLOSED" or not slide or slide.version != version:
            raise HTTPException(409, "This page is no longer being shared")
        record_phase(request, "recheck", started)
    return _pdf_response(content)


def _pdf_response(content):
    return Response(content, media_type="application/pdf",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": 'attachment; filename="lesson.pdf"'})


# ---------- live publish ----------
# share_slide checks access with a read-only lookup, prepares the page, then
# writes under a row lock on the session. PostgreSQL writes in two pipelined
# round trips; other databases use the ORM. Both write paths re-run the same
# rules below inside the lock, so a class closed or a permission revoked
# after the lookup still blocks the write.

def _require_publisher(claims, active, email, user_role, member_role):
    """Identity and role rules shared by the lookup and both write paths."""
    if not active:
        raise HTTPException(401, "Inactive or unknown user")
    if claims.get("scope") is not None or email.endswith("@join.question-up.local"):
        raise HTTPException(403, "Session token cannot access this endpoint")
    if member_role is None or (member_role != "faculty" and user_role != "admin"):
        raise HTTPException(403, "Course access denied")


def _require_open(status):
    if status == "CLOSED":
        raise HTTPException(409, "Class has ended")


def _replayed_result(previous, snapshots):
    """Return the stored result for a batch already written, or 409 if it was used for other pages.

    `previous` holds the batch's (position, version, page, total, content) rows in position order.
    """
    if [item[2] for item in previous] != [page for page, _, _ in snapshots]:
        raise HTTPException(409, "Batch identifier already used")
    position, version, page, total, content = previous[-1]
    return {"page": page, "total": total, "version": version, **json.loads(content), "sequence": position}


def _require_expected_version(current_version, expected_version):
    if expected_version is not None and (current_version is None or current_version != expected_version):
        raise HTTPException(409, "The shared page changed. Refresh before publishing again.")


def _plan_publish(batch_id, snapshots, last_position, initial):
    """Transition rows to insert and the new live slide, from the locked current state.

    `initial` is the current live slide as (version, page, total, content), or None.
    The first publish also records the opening slide as position 1 so history starts there.
    Returns (rows, slide): rows are (position, version, page, total, encoded content);
    slide is (page, total, version, content dict, encoded content).
    """
    position = last_position or 0
    rows = []
    if position == 0 and initial is not None:
        position = 1
        version, page, total, content = initial
        rows.append((1, version, page, total, content))
    for offset, (page, total, content) in enumerate(snapshots):
        version = f"{batch_id}:{offset}"
        content = {**content, "sequence": position + offset + 1}
        encoded = json.dumps(content, ensure_ascii=False)
        rows.append((position + offset + 1, version, page, total, encoded))
    return rows, (page, total, version, content, encoded)


def _publish_response(slide):
    page, total, version, content, _ = slide
    return {"page": page, "total": total, "version": version, **content}


def _publisher(db, credentials, session_id):
    """Read-only access lookup before the page is prepared; the write re-checks under lock."""
    claims = decode_token(credentials)
    row = db.execute(
        select(User, Member.role, ClassSession, Material)
        .select_from(User)
        .outerjoin(ClassSession, ClassSession.id == session_id)
        .outerjoin(Member, (Member.course_id == ClassSession.course_id) & (Member.user_id == User.id))
        .outerjoin(Material, Material.id == ClassSession.material_id)
        .where(User.id == claims["sub"])
    ).one_or_none()
    if row is None:
        raise HTTPException(401, "Inactive or unknown user")
    user, member_role, current, material = row
    if not user.active:
        raise HTTPException(401, "Inactive or unknown user")
    if claims.get("scope") is not None or user.email.endswith("@join.question-up.local"):
        raise HTTPException(403, "Session token cannot access this endpoint")
    if current is None:
        raise HTTPException(404, "Session not found")
    _require_publisher(claims, user.active, user.email, user.role, member_role)
    return current, material


def _notify_published_page(session_id, material, page, prepared):
    # An in-flight preparation may be queued behind other pages even though
    # this page is already served from cache. Do not delay those students.
    try:
        if cached_live_page(material, page) is None:
            prepared.result(timeout=1.5)
    except Exception:
        pass  # Never hold the notification on a slow storage read.
    notify(session_id)


_PUBLISH_LOCK = "select id from class_sessions where id = %s for update"
_PUBLISH_READ = """
select cs.status, u.active, u.email, u.role, m.role, ls.page, ls.total, ls.version, ls.content,
       (select max(t.position) from live_transitions t where t.session_id = cs.id),
       (select count(*) from live_transitions t
         where t.session_id = cs.id and left(t.version, length(%s)) = %s)
from class_sessions cs
join users u on u.id = %s
left join members m on m.course_id = cs.course_id and m.user_id = u.id
left join live_slides ls on ls.session_id = cs.id
where cs.id = %s"""
_PUBLISH_REPLAYED = ("select position, version, page, total, content from live_transitions "
                     "where session_id = %s and left(version, length(%s)) = %s order by position")
_TRANSITION_INSERT = ("insert into live_transitions (session_id, position, version, page, total, content) "
                      "values (%s, %s, %s, %s, %s, %s)")


def _publish_pipelined(session_id, claims, batch_id, snapshots, expected_version):
    """PostgreSQL publish in two round trips: lock+read, then write+commit.

    The lock statement is queued before the read so the read's snapshot
    follows any earlier publisher.
    """
    import psycopg

    for attempt in (0, 1):
        raw = publish_engine.raw_connection()
        conn = raw.driver_connection
        sent_writes = False
        try:
            # Explicit BEGIN/COMMIT on an autocommit connection: psycopg otherwise
            # waits for its implicit BEGIN, adding a round trip.
            conn.autocommit = True
            with conn.pipeline() as pipe:
                prefix = batch_id + ":"
                conn.execute("begin")
                lock = conn.execute(_PUBLISH_LOCK, (session_id,))
                read = conn.execute(_PUBLISH_READ, (prefix, prefix, claims["sub"], session_id))
                pipe.sync()
                if lock.fetchone() is None:
                    raise HTTPException(404, "Session not found")
                row = read.fetchone()
                if row is None:
                    raise HTTPException(401, "Inactive or unknown user")
                (status, active, email, user_role, member_role,
                 page0, total0, version0, content0, last_position, replayed) = row
                _require_publisher(claims, active, email, user_role, member_role)
                _require_open(status)
                if replayed:
                    previous = conn.execute(_PUBLISH_REPLAYED, (session_id, prefix, prefix)).fetchall()
                    result = _replayed_result(previous, snapshots)
                    conn.execute("rollback")
                    return result, False
                _require_expected_version(version0, expected_version)
                initial = None if version0 is None else (version0, page0, total0, content0)
                rows, slide = _plan_publish(batch_id, snapshots, last_position, initial)
                for position, version, page, total, content in rows:
                    conn.execute(_TRANSITION_INSERT, (session_id, position, version, page, total, content))
                page, total, version, _, encoded = slide
                if initial is None:
                    conn.execute("insert into live_slides (session_id, page, total, version, content) "
                                 "values (%s, %s, %s, %s, %s)", (session_id, page, total, version, encoded))
                else:
                    conn.execute("update live_slides set page = %s, total = %s, version = %s, content = %s "
                                 "where session_id = %s", (page, total, version, encoded, session_id))
                sent_writes = True
                conn.execute("commit")
            return _publish_response(slide), True
        except psycopg.OperationalError:
            # A pooled connection the server dropped fails before any write is sent.
            raw.invalidate()
            if attempt or sent_writes:
                raise
        except BaseException:
            if not conn.closed and conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.execute("rollback")
            raise
        finally:
            if not conn.closed:
                conn.autocommit = False
            raw.close()


def _publish_orm(session_id, claims, batch_id, snapshots, expected_version):
    """Publish through the ORM (SQLite and any non-PostgreSQL database)."""
    with PublishSession() as db:
        transitions = select(LiveTransition).where(LiveTransition.session_id == ClassSession.id)
        last_position = transitions.with_only_columns(func.max(LiveTransition.position)).scalar_subquery()
        replayed = transitions.with_only_columns(func.count()).where(
            LiveTransition.version.startswith(batch_id + ":")).scalar_subquery()
        # Acquire the lock before reading the slide and history so PostgreSQL
        # takes their statement snapshot after any concurrent publisher commits.
        locked = db.execute(select(ClassSession.id).where(ClassSession.id == session_id)
                            .with_for_update()).scalar_one_or_none()
        if locked is None:
            raise HTTPException(404, "Session not found")
        row = db.execute(
            select(ClassSession, User, Member.role, LiveSlide, last_position, replayed)
            .select_from(ClassSession)
            .join(User, User.id == claims["sub"])
            .outerjoin(Member, (Member.course_id == ClassSession.course_id) & (Member.user_id == User.id))
            .outerjoin(LiveSlide, LiveSlide.session_id == ClassSession.id)
            .where(ClassSession.id == session_id)
            .options(undefer(LiveSlide.content))
        ).one_or_none()
        if row is None:
            raise HTTPException(401, "Inactive or unknown user")
        current, user, member_role, initial, position, replayed = row
        _require_publisher(claims, user.active, user.email, user.role, member_role)
        _require_open(current.status)
        if replayed:
            previous = (db.query(LiveTransition).filter_by(session_id=session_id)
                        .filter(LiveTransition.version.startswith(batch_id + ":"))
                        .order_by(LiveTransition.position).all())
            return _replayed_result([(item.position, item.version, item.page, item.total, item.content)
                                     for item in previous], snapshots), False
        _require_expected_version(initial.version if initial else None, expected_version)
        state = None if initial is None else (initial.version, initial.page, initial.total, initial.content)
        rows, slide = _plan_publish(batch_id, snapshots, position, state)
        for sequence, version, page, total, content in rows:
            db.add(LiveTransition(session_id=session_id, position=sequence, version=version,
                                  page=page, total=total, content=content))
        page, total, version, _, encoded = slide
        if initial is None:
            db.add(LiveSlide(session_id=session_id, page=page, total=total, version=version, content=encoded))
        else:
            initial.page, initial.total, initial.version, initial.content = page, total, version, encoded
        db.commit()
    return _publish_response(slide), True


@router.put("/sessions/{session_id}/live")
def share_slide(session_id: str, data: ShareSlide, request: Request,
                credentials: HTTPAuthorizationCredentials | None = Depends(scheme)):
    started = time.perf_counter()
    with LiveReadSession() as db:
        current, material = _publisher(db, credentials, session_id)
        _require_open(current.status)
        db.expunge(material)
    record_phase(request, "lookup", started)
    started = time.perf_counter()
    snapshots = [(page, *build_snapshot(material, page)) for page in (data.pages or [data.page])]
    record_phase(request, "prepare", started)
    last_page, _, last_content = snapshots[-1]
    # Students fetch the page bytes from memory the moment they are notified.
    prepared = prepare_shared_page(material, last_page, last_content.get("before"), last_content.get("after"))
    started = time.perf_counter()
    batch_id = data.batch_id or uid()
    claims = decode_token(credentials)
    write = _publish_pipelined if publish_engine.dialect.name == "postgresql" else _publish_orm
    result, wrote = write(session_id, claims, batch_id, snapshots, data.expected_version)
    record_phase(request, "write", started)
    if wrote:
        _notify_published_page(session_id, material, last_page, prepared)
    return result


@router.get("/sessions/{session_id}/board")
def board(session_id: str, level: Literal["all", "shallow", "mid", "deep", "unassessed"] = "all",
          include_hidden: bool = False, db: Session = Depends(session), user: User = Depends(actor)):
    s = _faculty_session(db, session_id, user)
    q = db.query(SessionQuestion).filter_by(session_id=s.id, submitted=True)
    rows = q.order_by(SessionQuestion.submitted_at, SessionQuestion.id).all()
    counts = {"all": 0, "shallow": 0, "mid": 0, "deep": 0, UNASSESSED: 0}
    selected = []
    for r in rows:
        if r.hidden and not include_hidden:
            continue
        if not r.hidden:
            counts["all"] += 1
            counts[r.level] += 1
        if level == "all" or r.level == level:
            selected.append(r)
    originals = _original_texts(db, selected)
    students = _google_students(db, selected)
    # Only this faculty board carries who asked; the projector and CSV use _board_item alone.
    items = [{**_board_item(db, r, originals),
              **({"student": students[r.student_id]} if r.student_id in students else {})} for r in selected]
    return {"session": _public_session(db, s, faculty=True), "counts": counts, "items": items}


def _google_students(db, rows) -> dict:
    """Name and school email of students who joined with Google, in one query.
    Anonymous joins (and anything else without a school account) stay anonymous."""
    from .neon_identity import UserProfile
    ids = {r.student_id for r in rows}
    if not ids:
        return {}
    found = (db.query(User.id, User.email, UserProfile.display_name)
             .outerjoin(UserProfile, UserProfile.user_id == User.id)
             .filter(User.id.in_(ids), User.role == "student", ~User.email.like("%@join.question-up.local")).all())
    out = {}
    for uid_, email, name in found:
        local, _, domain = email.partition("@")
        shown_email = local.split("+", 1)[0] + "@" + domain  # a master's working account shows its school email
        out[uid_] = {"name": name or local.split("+", 1)[0], "email": shown_email}
    return out


def _faculty_row(db, row_id, user):
    row = db.get(SessionQuestion, row_id)
    if not row:
        raise HTTPException(404, "Question not found")
    s = _faculty_session(db, row.session_id, user)
    return row, s


@router.post("/session-questions/{row_id}/hide")
def hide(row_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _faculty_row(db, row_id, user)
    row.hidden = True
    if s.projected_id == row.id:
        s.projected_id = None
    _audit(db, user.id, s.course_id, "session_question_hidden", s.material_id)
    db.commit()
    notify(s.id)
    return {"id": row.id, "hidden": True}


@router.post("/session-questions/{row_id}/unhide")
def unhide(row_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _faculty_row(db, row_id, user)
    row.hidden = False
    db.commit()
    notify(s.id)
    return {"id": row.id, "hidden": False}


class ProjectQuestions(BaseModel):
    displayed_ids: list[str] = Field(default_factory=list, max_length=100)


@router.post("/session-questions/{row_id}/project")
def project(row_id: str, data: ProjectQuestions | None = None, db: Session = Depends(session),
            user: User = Depends(actor)):
    row, s = _faculty_row(db, row_id, user)
    if not row.submitted or row.hidden:
        raise HTTPException(409, "Only visible submitted questions can be projected")
    stamp = now()
    row.projected_at = row.projected_at or stamp
    if data and data.displayed_ids:
        (db.query(SessionQuestion)
         .filter(SessionQuestion.session_id == s.id, SessionQuestion.id.in_(data.displayed_ids),
                 SessionQuestion.submitted.is_(True), SessionQuestion.hidden.is_(False),
                 SessionQuestion.projected_at.is_(None))
         .update({SessionQuestion.projected_at: stamp}, synchronize_session=False))
    s.projected_id = row.id
    db.commit()
    return {"session_id": s.id, "projected": _board_item(db, row)}


@router.get("/sessions/{session_id}/projected")
def projected(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    s = _faculty_session(db, session_id, user)
    row = db.get(SessionQuestion, s.projected_id) if s.projected_id else None
    previous = nxt = None
    if row:
        visible = (db.query(SessionQuestion.id)
                   .filter(SessionQuestion.session_id == s.id, SessionQuestion.submitted.is_(True),
                           SessionQuestion.hidden.is_(False))
                   .order_by(SessionQuestion.submitted_at, SessionQuestion.id).all())
        ids = [item.id for item in visible]
        if len(ids) > 1 and row.id in ids:
            current = ids.index(row.id)
            previous = ids[(current - 1) % len(ids)]
            nxt = ids[(current + 1) % len(ids)]
    return {"session": _public_session(db, s, faculty=True),
            "item": _board_item(db, row) if row else None, "previous_id": previous, "next_id": nxt}


def _csv_cell(text):
    # Spreadsheet applications evaluate leading formulas even in quoted CSV cells.
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


@router.get("/sessions/{session_id}/export")
def export(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    s = _faculty_session(db, session_id, user)
    rows = (db.query(SessionQuestion).filter_by(session_id=s.id, submitted=True, hidden=False)
            .order_by(SessionQuestion.submitted_at).all())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["제출 시각", "질문 수준", "처음 질문", "다시 쓴 질문", "화면에 띄움"])
    originals = _original_texts(db, rows)
    for r in rows:
        original = originals[r.question_id]
        w.writerow([_iso(r.submitted_at), LEVEL_LABEL[r.level], _csv_cell(original),
                    _csv_cell(r.latest_text) if r.latest_text != original else "",
                    "예" if r.projected_at else ""])
    _audit(db, user.id, s.course_id, "session_exported", s.material_id)
    db.commit()
    body = "﻿" + buf.getvalue()
    return Response(content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="question-up-{s.id[:8]}.csv"',
                             "Cache-Control": "no-store"})


# ---------- student endpoints ----------

def _google_student(db, credentials):
    """The Google student behind an optional join ticket, or None for an anonymous join."""
    if credentials is None:
        return None
    claims = decode_token(credentials)
    if claims.get("scope") != "student_account":
        raise HTTPException(403, "Join with a student account ticket or anonymously")
    user = db.get(User, claims["sub"])
    if not user or not user.active or user.role != "student":
        raise HTTPException(403, "Student account is not active")
    return user


@router.post("/join")
def join(data: JoinRequest, request: Request, response: Response, db: Session = Depends(session),
         credentials: HTTPAuthorizationCredentials | None = Depends(scheme)):
    key, failures = _throttle(request)
    code = data.code.strip().upper()
    s = db.query(ClassSession).filter_by(join_code=code).first() if all(ch in JOIN_ALPHABET for ch in code) else None
    if not s or s.status == "CLOSED":
        failures.append(time.monotonic())
        raise HTTPException(404, "No open class with this code")
    if s.status == "PAUSED":
        raise HTTPException(423, "The class is not taking questions right now")
    student = _google_student(db, credentials)
    if student is None:
        student = User(email=f"anon+{uid()}@join.question-up.local", role="student", active=True)
        db.add(student)
        db.flush()
    member = db.query(Member).filter_by(course_id=s.course_id, user_id=student.id).first()
    if member is None:
        db.add(Member(course_id=s.course_id, user_id=student.id, role="student"))
    elif member.role != "student":
        raise HTTPException(409, "This account already has another role in the course")
    if not db.query(SessionParticipant).filter_by(session_id=s.id, user_id=student.id).first():
        db.add(SessionParticipant(session_id=s.id, user_id=student.id))
    # Other students and the faculty board never see who asked: questions stay anonymous on screen.
    _audit(db, student.id, s.course_id,
           "student_joined_with_google" if credentials else "student_joined_anonymously", s.material_id)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": issue_token(student.id, minutes=STUDENT_TOKEN_MINUTES, session_id=s.id),
            "session": _public_session(db, s),
            "initial_live": {"status": s.status, "slide": _student_slide(_slide_metadata(db.get(LiveSlide, s.id))),
                             "index": []}}


@router.put("/sessions/{session_id}/live/confusion")
def flag_confusion(session_id: str, data: ConfusionRequest, db: Session = Depends(session), user: User = Depends(actor)):
    current = _participant_session(db, session_id, user)
    # Serialize duplicate votes together with live state changes.
    db.refresh(current, with_for_update=True)
    if current.status != "OPEN":
        raise HTTPException(423, "The class is not taking feedback right now")
    slide = db.get(LiveSlide, session_id)
    # Accept a reaction when the presenter advances before the student submits it.
    if not slide or (slide.page != data.page and not db.query(LiveTransition).filter_by(
        session_id=session_id, page=data.page,
    ).first()):
        raise HTTPException(409, "The page has not been shared")
    anchor = data.anchor
    x = round(anchor.x * 20) if anchor else None
    y = round(anchor.y * 20) if anchor else None
    key = (session_id, data.page, user.id)
    vote = db.get(SlideConfusion, key)
    area = db.get(SlideConfusionArea, key)
    rects = json.dumps([rect.model_dump() for rect in anchor.rects]) if anchor and anchor.rects else None
    if vote and (vote.x, vote.y) == (x, y) and (area.rectangles if area else None) == rects:
        return {"page": data.page}
    db.merge(SlideConfusion(session_id=session_id, page=data.page, student_id=user.id, x=x, y=y))
    db.merge(SlideConfusionGeneration(session_id=session_id, page=data.page, student_id=user.id, tag_id=uid()))
    if rects:
        db.merge(SlideConfusionArea(session_id=session_id, page=data.page, student_id=user.id, rectangles=rects))
    elif area:
        db.delete(area)
    db.commit()
    notify(session_id)
    return {"page": data.page}


class ConfusionAcknowledgement(BaseModel):
    page: int = Field(ge=1)
    version: str = Field(min_length=1, max_length=64)
    tag_ids: list[str] = Field(min_length=1, max_length=5000)


@router.post("/sessions/{session_id}/live/confusion/acknowledge")
def acknowledge_confusion(session_id: str, data: ConfusionAcknowledgement,
                          credentials: HTTPAuthorizationCredentials | None = Depends(scheme),
                          db: Session = Depends(session)):
    # Use the same lock as tag updates, session closure, and page publishing.
    locked = db.execute(select(ClassSession.id).where(ClassSession.id == session_id)
                        .with_for_update()).scalar_one_or_none()
    if locked is None:
        raise HTTPException(404, "Session not found")
    user, current, faculty, slide, _ = _live_reader(db, credentials, session_id, include_page=True)
    if current.status == "CLOSED" or not slide or slide.version != data.version or slide.page != data.page:
        raise HTTPException(409, "This page is no longer being shared")
    votes = db.query(SlideConfusion, SlideConfusionGeneration.tag_id).outerjoin(
        SlideConfusionGeneration, (SlideConfusion.session_id == SlideConfusionGeneration.session_id)
        & (SlideConfusion.page == SlideConfusionGeneration.page)
        & (SlideConfusion.student_id == SlideConfusionGeneration.student_id),
    ).filter(SlideConfusion.session_id == session_id, SlideConfusion.page == data.page)
    if not faculty:
        votes = votes.filter(SlideConfusion.student_id == user.id)
    target_ids = set(data.tag_ids)
    students = [vote.student_id for vote, generation in votes
                if _confusion_tag(vote, generation) in target_ids]
    removed = len(students)
    if students:
        for model in (SlideConfusionArea, SlideConfusionGeneration, SlideConfusion):
            db.query(model).filter(model.session_id == session_id, model.page == data.page,
                                   model.student_id.in_(students)).delete(synchronize_session=False)
    db.commit()
    if removed:
        notify(session_id)
    return {"removed": removed}


def _lock_open(db, s):
    """Share the session-row lock with state changes so a close and a write are ordered."""
    db.refresh(s, with_for_update=True)
    if s.status != "OPEN":
        raise HTTPException(423, "The class is not taking questions right now")


def _dispatch_check(background_tasks, row_id, revision_id):
    # The Northflank worker polls queued checks; without it, run after the response.
    if not fit.worker_dispatches():
        background_tasks.add_task(fit.process, row_id, revision_id)


def _question_response(db, row):
    head, check = fit.current(db, row.id)
    state = fit.effective_check(row.latest_text, check, check.policy_version,
                                anchored=fit.has_anchor(db, row.id)) if check else None
    blocked = state and state["status"] == "needs_revision"
    waiting = state and state["status"] == "pending"
    return {"id": row.id, "level": None if blocked or waiting else row.level,
            "coaching": None if blocked or waiting else _student_coaching(json.loads(row.latest_coaching_json)),
            "message": fit.REJECTION_MESSAGE if blocked else None,
            "revision_id": head.revision_id if head else None, "suitability": state}


@router.post("/sessions/{session_id}/questions", status_code=201)
def session_ask(session_id: str, data: SessionAsk, background_tasks: BackgroundTasks,
                db: Session = Depends(session), user: User = Depends(actor)):
    s = _participant_session(db, session_id, user)
    if s.status != "OPEN":
        raise HTTPException(423, "The class is not taking questions right now")
    if data.request_id:
        seen = db.get(fit.AskRequest, (s.id, user.id, data.request_id))
        if seen:
            return _question_response(db, db.get(SessionQuestion, seen.session_question_id))
    slide = db.get(LiveSlide, session_id)
    page = slide.page if slide else None
    if data.slide_page is not None and (slide is None or data.slide_page > slide.total):
        raise HTTPException(422, "Invalid question slide")
    page = data.slide_page or page
    if data.anchor and (data.slide_page is None or page not in visible_pages(db.get(Material, s.material_id))):
        raise HTTPException(422, "Invalid question anchor")
    anchored = bool(data.anchor and data.anchor.quote.strip())
    coaching = ({"mode": "suitability_blocked"} if fit.intake_reasons(data.question, anchored=anchored)
                else _coach(db, s, user, data.question))
    _lock_open(db, s)
    q = Question(course_id=s.course_id, material_id=s.material_id, student_id=user.id,
                 text=data.question, feedback_json=json.dumps(coaching, ensure_ascii=False))
    db.add(q)
    db.flush()
    lvl = _level(coaching)
    row = SessionQuestion(session_id=s.id, question_id=q.id, student_id=user.id, level=lvl, first_level=lvl,
                          latest_text=data.question, latest_coaching_json=json.dumps(coaching, ensure_ascii=False))
    db.add(row)
    db.flush()
    if data.request_id:
        db.add(fit.AskRequest(session_id=s.id, student_id=user.id, request_id=data.request_id,
                              session_question_id=row.id))
    if page is not None:
        db.add(QuestionSlide(question_id=row.id, page=page))
        if data.anchor:
            db.add(QuestionAnchor(question_id=row.id, location=data.anchor.model_dump_json(exclude_unset=True)))
    head, check, queued = fit.new_draft(db, row, s, data.question)
    _audit(db, user.id, s.course_id, "session_question_coached", s.material_id, detail=str(coaching.get("mode")))
    try:
        db.commit()
    except IntegrityError:
        # Two copies of the same retransmitted request: the first one wins.
        db.rollback()
        seen = db.get(fit.AskRequest, (s.id, user.id, data.request_id)) if data.request_id else None
        if not seen:
            raise
        return _question_response(db, db.get(SessionQuestion, seen.session_question_id))
    if queued:
        _dispatch_check(background_tasks, row.id, head.revision_id)
    notify(s.id)
    return _question_response(db, row)


def _own_row(db, row_id, user):
    row = db.get(SessionQuestion, row_id)
    if not row or row.student_id != user.id:
        raise HTTPException(404, "Question not found")
    s = _participant_session(db, row.session_id, user)
    return row, s


@router.post("/session-questions/{row_id}/revisions", status_code=201)
def session_revise(row_id: str, data: SessionRevise, background_tasks: BackgroundTasks,
                   db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _own_row(db, row_id, user)
    if s.status != "OPEN":
        raise HTTPException(423, "The class is not taking questions right now")
    if row.submitted:
        raise HTTPException(409, "Question already submitted")
    coaching = ({"mode": "suitability_blocked"} if fit.intake_reasons(data.text, anchored=fit.has_anchor(db, row.id))
                else _coach(db, s, user, data.text))
    _lock_open(db, s)
    db.refresh(row, with_for_update=True)  # same order as submits: session row, then question rows
    if row.submitted:
        raise HTTPException(409, "Question already submitted")
    db.add(Revision(question_id=row.question_id, student_id=user.id, text=data.text))
    row.latest_text = data.text
    row.level = _level(coaching)
    row.latest_coaching_json = json.dumps(coaching, ensure_ascii=False)
    head, check, queued = fit.new_draft(db, row, s, data.text)
    _audit(db, user.id, s.course_id, "session_question_revised", s.material_id)
    db.commit()
    if queued:
        _dispatch_check(background_tasks, row.id, head.revision_id)
    notify(s.id)
    return _question_response(db, row)


@router.post("/session-questions/{row_id}/suitability/check")
def recheck_suitability(row_id: str, data: SuitabilityRecheck, background_tasks: BackgroundTasks,
                        db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _own_row(db, row_id, user)
    _lock_open(db, s)
    db.refresh(row, with_for_update=True)
    if row.submitted:
        raise HTTPException(409, {"code": "already_submitted"})
    policy = fit.fix_policy(db, s.id)
    head, check = fit.ensure_head(db, row, policy)
    if head.revision_id != data.revision_id:
        db.rollback()
        raise HTTPException(409, {"code": "stale_revision"})
    queued = False
    # Decided results and a check already running are returned as they are (no duplicate runs).
    if check.status == "pending" and not fit.in_flight(check):
        queued = fit.evaluate(db, row, s, check, row.latest_text)
    db.commit()
    if queued:
        _dispatch_check(background_tasks, row.id, head.revision_id)
    if check.status != "pending" or queued:
        notify(s.id)
    return {"id": row.id, "revision_id": head.revision_id, "suitability": fit.effective_check(row.latest_text, check, policy, anchored=fit.has_anchor(db, row.id))}


def _submit_problem(db, row, revision_id, policy):
    """Validate the current revision using the same decision exposed to students."""
    if row.submitted:
        return "already_submitted"
    head, check = fit.current(db, row.id)
    current_revision = head.revision_id if head else fit.legacy_revision(row.id)
    if revision_id is not None and revision_id != current_revision:
        return "stale_revision"
    state = fit.effective_check(row.latest_text, check, policy, anchored=fit.has_anchor(db, row.id))
    if state["status"] == "needs_revision":
        return "not_eligible"
    if state["status"] != "eligible":
        return "check_pending"
    if state["policy_version"] != policy:
        return "policy_changed"
    return None


def _mark_submitted(db, s, user, row):
    row.submitted = True
    row.submitted_at = now()
    _audit(db, user.id, s.course_id, "session_question_submitted", s.material_id)


@router.post("/session-questions/{row_id}/submit")
def session_submit(row_id: str, data: SingleSubmit | None = None,
                   db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _own_row(db, row_id, user)
    if s.status != "OPEN":
        raise HTTPException(423, "The class is not taking questions right now")
    _lock_open(db, s)
    db.refresh(row, with_for_update=True)
    if not row.submitted:
        revision_id = data.revision_id if data else None
        problem = _submit_problem(db, row, revision_id, fit.fix_policy(db, s.id))
        if problem:
            db.rollback()
            raise HTTPException(409, {"code": problem})
        _mark_submitted(db, s, user, row)
        db.commit()
        notify(s.id)
    head, _ = fit.current(db, row.id)
    return {"id": row.id, "submitted": True, "level": row.level, "text": row.latest_text,
            "original": _original_text(db, row), "revision_id": head.revision_id if head else None,
            "submitted_at": _iso(row.submitted_at)}


@router.post("/sessions/{session_id}/questions/submit-batch")
def submit_batch(session_id: str, data: SubmitBatchIn, db: Session = Depends(session), user: User = Depends(actor)):
    s = _participant_session(db, session_id, user)
    _lock_open(db, s)
    items = [[item.question_id, item.revision_id] for item in data.items]
    replay = db.get(fit.SubmitBatch, (s.id, user.id, data.request_id))
    if replay:
        if json.loads(replay.items_json) != items:
            raise HTTPException(409, {"code": "request_id_reused"})
        return json.loads(replay.response_json)
    ids = sorted(item.question_id for item in data.items)
    rows = {r.id: r for r in db.query(SessionQuestion).filter(SessionQuestion.id.in_(ids))
            .order_by(SessionQuestion.id).with_for_update()}
    # Another student's or another class's id rejects the whole batch without saying which.
    if len(rows) != len(ids) or any(r.student_id != user.id or r.session_id != s.id for r in rows.values()):
        db.rollback()
        raise HTTPException(404, "Question not found")
    policy = fit.fix_policy(db, s.id)
    problems = [{"question_id": qid, "code": code} for qid, rid in items
                if (code := _submit_problem(db, rows[qid], rid, policy))]
    if problems:
        db.rollback()
        raise HTTPException(409, {"code": "batch_not_submitted", "items": problems})
    for qid, _ in items:
        _mark_submitted(db, s, user, rows[qid])
    response = {"request_id": data.request_id,
                "submitted": [{"question_id": qid, "revision_id": rid, "submitted_at": _iso(rows[qid].submitted_at)}
                              for qid, rid in items]}
    db.add(fit.SubmitBatch(session_id=s.id, student_id=user.id, request_id=data.request_id,
                           items_json=json.dumps(items), response_json=json.dumps(response)))
    db.commit()
    notify(s.id)  # one live update per batch
    return response


@router.get("/sessions/{session_id}/mine")
def mine(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    s = _participant_session(db, session_id, user)
    rows = (db.query(SessionQuestion).filter_by(session_id=s.id, student_id=user.id)
            .order_by(SessionQuestion.created_at.desc(), SessionQuestion.id.desc()).all())
    pages = dict(db.query(QuestionSlide.question_id, QuestionSlide.page).join(
        SessionQuestion, SessionQuestion.id == QuestionSlide.question_id
    ).filter(SessionQuestion.session_id == session_id, SessionQuestion.student_id == user.id).all())
    originals = _original_texts(db, rows)
    policy = fit.fix_policy(db, s.id)
    # Unsent questions written before checks existed show as pending with a check path;
    # sent ones keep their state and get no converted result.
    drafts = fit.drafts_for(db, rows, policy)
    db.commit()  # the class's policy row on first read
    items = []
    for row in rows:
        revision_id, state = drafts[row.id]
        blocked = not row.submitted and state["status"] == "needs_revision"
        waiting = not row.submitted and state["status"] == "pending"
        show_coaching = not (blocked or waiting)
        items.append({
            "id": row.id, "slide_page": pages.get(row.id), "level": row.level if show_coaching else None,
            "level_label": LEVEL_LABEL[row.level] if show_coaching else None,
            "text": row.latest_text if not blocked else None,
            "original": originals[row.question_id] if not blocked else None,
            "submitted": row.submitted, "submitted_at": _iso(row.submitted_at),
            "revision_id": revision_id, "suitability": state,
            "message": fit.REJECTION_MESSAGE if blocked else None,
            "coaching": _student_coaching(json.loads(row.latest_coaching_json)) if show_coaching else None,
        })
    return {"session": _public_session(db, s),
            "suitability": {"enforced": True, "policy_version": policy}, "items": items}
