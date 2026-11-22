"""The same question must always get the same level.

Three layers: explicit level cues decide the tier (level_rules), the model is
called with temperature 0 and a fixed seed, and a class keeps the first level it
gave a normalized question (level_cache).
"""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-level-consistency-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from learningx import engine_adapter as nit  # noqa: E402
from learningx import level_cache  # noqa: E402
from learningx.storage import SessionLocal, init_db  # noqa: E402

nit._modules()
import runtime.coaching as engine  # noqa: E402
from runtime.gateway import OpenAICompatibleGateway  # noqa: E402
from runtime.level_rules import TIER, normalize, resolve_level, rule_tier  # noqa: E402

EVIDENCE = [{"page": 1, "block_id": "b1", "text": "유명인 광고 모델의 신뢰성과 매력성은 설득 효과에 영향을 준다."}]

CASES = {
    "shallow": ["공포 소구의 정의는 무엇인가요?", "신뢰성이란 무엇인가요?", "매력성의 뜻이 뭐예요?",
                "이 광고는 언제 처음 나왔나요?",
                "교안에서 말하는 공포 소구는 어떤 광고를 가리키나요?", "공포 소구에서 위협 메시지는 구체적으로 어떤 의미인가요?",
                "유명인 모델의 신뢰성은 어떤 요소로 구성되나요?"],
    "mid": ["유명인 광고 모델의 신뢰성의 설득 효과에 영향을 주는 이유는 무엇인가요?",
            "유명인 광고 모델의 신뢰성이 설득 효과에 영향을 주는 이유는 무엇인가요?",
            "신뢰성과 매력성의 차이는 무엇인가요?", "왜 유머 소구는 기억에 오래 남나요?",
            "이 이론을 우리 학교 홍보 영상에 적용하면 어떻게 되나요?"],
    "deep": ["금연 캠페인에서 공포 소구와 유머 소구 중 어느 쪽이 더 효과적일지 어떤 기준으로 판단할 수 있을까요?",
             "이 광고 전략은 타당한가요?", "신뢰성을 높이는 새로운 광고 캠페인을 설계한다면 무엇을 바꿔야 할까요?"],
}


def gateway_returning(*levels):
    gateway = Mock()
    gateway.generate.side_effect = [SimpleNamespace(text=json.dumps({
        "schema_version": "nit_coaching_v1", "question_level": level, "evidence_ids": ["p1:b1"],
        "follow_up_question": "어떤 근거를 먼저 확인하면 좋을까요?"}, ensure_ascii=False),
        provider="mock", model="mock") for level in levels]
    return gateway


def coach(question, gateway):
    with patch.object(engine, "signed_evidence_coach", return_value={"evidence": EVIDENCE}), \
         patch.object(engine, "verify_signed_evidence", return_value=EVIDENCE):
        return engine.run_coaching(Path(TEMP.name), question, "PERMITTED", True, "s" * 40, "student", gateway)


class RubricTests(unittest.TestCase):
    def test_cue_questions_get_their_tier(self):
        for tier, questions in CASES.items():
            for question in questions:
                with self.subTest(question=question):
                    self.assertEqual(rule_tier(question)[0], tier)

    def test_reason_beats_the_what_ending(self):
        self.assertEqual(rule_tier("영향을 주는 이유는 무엇인가요?"), ("mid", "reason"))

    def test_no_cue_keeps_the_model_level(self):
        self.assertEqual(resolve_level("유명인 광고 모델 이야기 좀 더 해 주세요", "apply"), ("apply", "model"))

    def test_model_level_inside_the_tier_is_kept(self):
        self.assertEqual(resolve_level("왜 그런가요?", "apply"), ("apply", "rule:reason"))

    def test_normalize_ignores_spacing_and_trailing_marks(self):
        self.assertEqual(normalize("  신뢰성이란   무엇인가요 ??"), normalize("신뢰성이란 무엇인가요"))


class RepeatedModelDriftTests(unittest.TestCase):
    """The production drift: the same question judged analyze, clarify, clarify, analyze."""

    def test_the_same_question_gets_one_level_however_the_model_drifts(self):
        drift = ["analyze", "clarify", "clarify", "analyze", "evaluate", "apply"]
        for tier, questions in CASES.items():
            for question in questions:
                with self.subTest(question=question):
                    levels = {TIER[coach(question, gateway_returning(level))["question_level"]] for level in drift}
                    self.assertEqual(levels, {tier})

    def test_the_basis_and_the_model_level_are_recorded(self):
        result = coach(CASES["mid"][0], gateway_returning("clarify"))
        self.assertEqual(result["question_level"], "analyze")
        self.assertEqual(result["level_basis"], "rule:reason")
        self.assertEqual(result["model_observability"]["model_level"], "clarify")

    def test_the_prompt_carries_the_rubric(self):
        for word in ("clarify:", "analyze:", "evaluate:", "같은 질문에는 언제나 같은 question_level"):
            self.assertIn(word, engine.SYSTEM)


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def coaching(self, level, basis="model"):
        return {"mode": "MODEL_VALIDATED", "question_level": level, "level_basis": basis,
                "level_rubric": "level-rubric-v1"}

    def test_the_first_judgment_is_reused_for_the_same_question(self):
        with SessionLocal() as db:
            first = level_cache.stabilize(db, self.coaching("apply"), "광고 이야기 좀 더 해 주세요", "m1", None)
            again = level_cache.stabilize(db, self.coaching("evaluate"), "광고 이야기 좀 더  해 주세요?", "m1", None)
            db.commit()
        self.assertEqual(first["question_level"], "apply")
        self.assertEqual((again["question_level"], again["level_basis"]), ("apply", "cache:model"))

    def test_context_changes_start_a_new_judgment(self):
        with SessionLocal() as db:
            level_cache.stabilize(db, self.coaching("apply"), "다른 교안 질문", "m2", None)
            other_material = level_cache.stabilize(db, self.coaching("create"), "다른 교안 질문", "m3", None)
            other_profile = level_cache.stabilize(db, self.coaching("create"), "다른 교안 질문", "m2", "p:2")
            db.commit()
        self.assertEqual(other_material["question_level"], "create")
        self.assertEqual(other_profile["question_level"], "create")

    def test_fallback_coaching_is_not_cached(self):
        with SessionLocal() as db:
            fallback = {"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze"}
            self.assertIs(level_cache.stabilize(db, fallback, "보류 질문", "m4", None), fallback)
            later = level_cache.stabilize(db, self.coaching("clarify"), "보류 질문", "m4", None)
        self.assertEqual(later["question_level"], "clarify")


class GatewayTests(unittest.TestCase):
    def response(self):
        body = json.dumps({"choices": [{"message": {"content": "{}"}}], "model": "m"}).encode()
        cm = Mock()
        cm.__enter__ = Mock(return_value=io.BytesIO(body))
        cm.__exit__ = Mock(return_value=False)
        return cm

    def gateway(self, opener, **kw):
        return OpenAICompatibleGateway(base_url="https://example.test/v1", api_key="k", model="auto",
                                       opener=opener, **kw)

    def test_greedy_decoding_with_a_fixed_seed(self):
        opener = Mock(return_value=self.response())
        self.gateway(opener, seed=11).generate("s", "u")
        payload = json.loads(opener.call_args.args[0].data)
        self.assertEqual((payload["temperature"], payload["seed"]), (0, 11))

    def test_a_provider_rejecting_the_seed_gets_one_call_without_it(self):
        rejected = urllib.error.HTTPError("https://example.test", 400, "bad", {}, None)
        opener = Mock(side_effect=[rejected, self.response()])
        self.gateway(opener, seed=11).generate("s", "u")
        self.assertNotIn("seed", json.loads(opener.call_args_list[1].args[0].data))

    def test_seed_can_be_turned_off(self):
        opener = Mock(return_value=self.response())
        self.gateway(opener, seed="off").generate("s", "u")
        self.assertNotIn("seed", json.loads(opener.call_args.args[0].data))


if __name__ == "__main__":
    unittest.main()
