"""Root decision ledger: the platform, not the model, owns every judgment.

A model only proposes. Once a proposal passes the root guardrails it is written
here, and every later request with the same normalized question in the same
context returns that decision without calling a model again, whichever model
the provider routes to and even while the provider is down.

Key = kind + root policy version + context fingerprint + normalized question.
Changing a policy (rubric, prompt, suitability rules) bumps its version and
starts a fresh ledger; context changes (material, objective profile version,
course policy, anchor) give a different fingerprint. Fallbacks are never stored.
See docs/ROOT_GUARDRAILS.md.
"""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .storage import Base

COACHING = "coaching"
SUITABILITY = "suitability"
# Fields of a coaching decision replayed to later identical questions. Model
# observability is per call and is not replayed.
COACHING_FIELDS = ("mode", "question_level", "follow_up_question", "feedback", "evidence_refs",
                   "student_must_write_revision", "validation", "goal_relevance", "level_basis", "level_rubric",
                   "level_reason", "question_frames", "evidence_mode")


def now():
    return datetime.now(timezone.utc)


class RootJudgment(Base):
    __tablename__ = "root_judgments"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    decision_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


def _normalizer():
    from .engine_adapter import level_rubric  # noqa: PLC0415  (engine path set up lazily)
    return level_rubric()


def coaching_policy() -> str:
    from .engine_adapter import coaching_policy_version  # noqa: PLC0415
    return coaching_policy_version()


def key_for(kind: str, policy: str, context: dict, question: str) -> str:
    _, normalize = _normalizer()
    raw = json.dumps([kind, policy, context, normalize(question)], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def recall(db, key: str):
    row = db.get(RootJudgment, key)
    return json.loads(row.decision_json) if row else None


def record(db, key: str, kind: str, policy: str, decision: dict) -> dict:
    """Store a decision; if an identical request stored one first, that one wins."""
    try:
        with db.begin_nested():
            db.add(RootJudgment(key=key, kind=kind, policy_version=policy[:96],
                                decision_json=json.dumps(decision, ensure_ascii=False, sort_keys=True)))
    except IntegrityError:
        return recall(db, key) or decision
    return decision


def coaching_decision(coaching: dict) -> dict | None:
    """The replayable part of a coaching answer, or None when it is not a root decision."""
    if coaching.get("mode") != "MODEL_VALIDATED" or not coaching.get("question_level"):
        return None
    return {k: coaching[k] for k in COACHING_FIELDS if k in coaching}
