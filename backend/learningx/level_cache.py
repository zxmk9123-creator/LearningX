"""Same question, same level.

The first validated level for a normalized question is kept per class material,
objective profile version and rubric version. Later identical questions reuse it,
so a student or a faculty member never sees the same question judged differently.
A new rubric version or profile version starts a fresh judgment.
"""
import hashlib
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .storage import Base


def now():
    return datetime.now(timezone.utc)


class QuestionLevelJudgment(Base):
    __tablename__ = "question_level_judgments"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_level: Mapped[str] = mapped_column(String(16), nullable=False)
    basis: Mapped[str] = mapped_column(String(48), nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


def judgment_key(normalized_question: str, material_id: str, profile: str, rubric: str) -> str:
    raw = "\x1f".join((rubric, material_id or "", profile or "", normalized_question))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stabilize(db, coaching: dict, question: str, material_id: str, profile: str | None) -> dict:
    """Return coaching whose question_level matches the stored judgment for this question."""
    if coaching.get("mode") != "MODEL_VALIDATED" or not coaching.get("question_level"):
        return coaching
    from .engine_adapter import level_rubric  # noqa: PLC0415
    current, normalize = level_rubric()
    rubric = coaching.get("level_rubric") or current
    key = judgment_key(normalize(question), material_id, profile or "", rubric)
    seen = db.get(QuestionLevelJudgment, key)
    if seen:
        if seen.question_level == coaching["question_level"]:
            return coaching
        return {**coaching, "question_level": seen.question_level, "level_basis": "cache:" + seen.basis}
    try:
        with db.begin_nested():
            db.add(QuestionLevelJudgment(key=key, question_level=coaching["question_level"],
                                         basis=str(coaching.get("level_basis") or "model")[:48],
                                         rubric_version=rubric[:32]))
    except IntegrityError:
        # A concurrent identical question stored its judgment first: use that one.
        seen = db.get(QuestionLevelJudgment, key)
        if seen and seen.question_level != coaching["question_level"]:
            return {**coaching, "question_level": seen.question_level, "level_basis": "cache:" + seen.basis}
    return coaching
