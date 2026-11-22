"""The same question must always get the same level.

Three layers: explicit level cues decide the tier (level_rules), the model is
called with temperature 0 and a fixed seed, and a class keeps the first level it
gave a normalized question (root decision ledger).
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
from fastapi.testclient import TestClient  # noqa: E402

from learningx.api import app  # noqa: E402
from learningx.classroom import SessionQuestion  # noqa: E402
from learningx.security import issue_token  # noqa: E402
from learningx.storage import Course, Material, Member, SessionLocal, User, uid  # noqa: E402

nit._modules()
import runtime.coaching as engine  # noqa: E402
from runtime.gateway import OpenAICompatibleGateway  # noqa: E402
from runtime.guardrails import followup_violation  # noqa: E402
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

    def test_feedback_is_the_fixed_root_sentence(self):
        result = coach(CASES["mid"][0], gateway_returning("analyze"))
        self.assertEqual(result["feedback"], "질문의 근거와 전제를 구체화해 보세요.")

    def test_the_prompt_carries_the_rubric(self):
        for word in ("clarify:", "analyze:", "evaluate:", "같은 질문에는 언제나 같은 question_level"):
            self.assertIn(word, engine.SYSTEM)


def validated(level, follow_up="어떤 근거를 먼저 확인하면 좋을까요?"):
    return {"mode": "MODEL_VALIDATED", "question_level": level, "follow_up_question": follow_up,
            "feedback": "질문의 근거와 전제를 구체화해 보세요.", "evidence_refs": [{"id": "p1:b1", "page": 1}],
            "student_must_write_revision": True, "validation": "schema_and_policy_checked",
            "level_basis": "model", "level_rubric": "level-rubric-v1",
            "model_observability": {"provider": "mock", "model": "model-" + level}}


class RootLedgerTests(unittest.TestCase):
    """The platform owns decisions: a decided question is replayed, not re-asked."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def open_class(self):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            faculty = User(email=f"root-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="설득 커뮤니케이션", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="a" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        faculty_token = issue_token(faculty.id)
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.client.post(f"/v1/courses/{course_id}/sessions", json={"material_id": material_id},
                                      headers={"Authorization": "Bearer " + faculty_token}).json()
        return opened["join_code"], opened["id"]

    def ask(self, code, session_id, text, coaching):
        token = self.client.post("/v1/join", json={"code": code}).json()["access_token"]
        engine_call = Mock(return_value=coaching)
        with patch.object(nit, "coach_question", engine_call):
            res = self.client.post(f"/v1/sessions/{session_id}/questions", json={"question": text},
                                   headers={"Authorization": "Bearer " + token})
        self.assertEqual(res.status_code, 201, res.text)
        with SessionLocal() as db:
            stored = json.loads(db.get(SessionQuestion, res.json()["id"]).latest_coaching_json)
        return res.json(), stored, engine_call.call_count

    def test_any_model_after_the_first_decision_is_not_asked(self):
        code, sid = self.open_class()
        text = "광고 모델 이야기를 조금 더 들을 수 있을까요?"
        first, stored, calls = self.ask(code, sid, text, validated("apply", "어떤 사례에 먼저 적용해 볼까요?"))
        self.assertEqual((first["level"], calls, stored["decision_source"]), ("mid", 1, "model_proposal_accepted"))
        for drift in ("clarify", "evaluate", "create"):
            again, stored, calls = self.ask(code, sid, text + " ", validated(drift, "다른 역질문이 나올까요?"))
            self.assertEqual(calls, 0, "a decided question never reaches a model")
            self.assertEqual((again["level"], again["coaching"]["follow_up_question"], stored["decision_source"]),
                             ("mid", "어떤 사례에 먼저 적용해 볼까요?", "ledger"))
            self.assertNotIn("model_observability", stored)

    def test_a_provider_outage_still_replays_the_decision(self):
        code, sid = self.open_class()
        text = "광고 모델 사례를 하나 더 볼 수 있을까요?"
        self.ask(code, sid, text, validated("analyze"))
        outage = {"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze", "reason": "model_timeout"}
        again, _, calls = self.ask(code, sid, text, outage)
        self.assertEqual((again["level"], calls), ("mid", 0))

    def test_fallbacks_are_not_decisions(self):
        code, sid = self.open_class()
        text = "광고 모델 사례 정리가 가능할까요?"
        outage = {"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze", "reason": "model_timeout"}
        first, _, _ = self.ask(code, sid, text, outage)
        later, _, calls = self.ask(code, sid, text, validated("clarify"))
        self.assertEqual((first["level"], later["level"], calls), ("unassessed", "shallow", 1))

    def test_another_class_material_decides_afresh(self):
        text = "광고 모델 내용을 더 알려 줄 수 있을까요?"
        self.ask(*self.open_class(), text, validated("apply"))
        other, _, calls = self.ask(*self.open_class(), text, validated("evaluate"))
        self.assertEqual((other["level"], calls), ("deep", 1))

    def test_the_policy_version_names_every_root_rule(self):
        version = engine.ROOT_POLICY_VERSION
        for part in ("nit_coaching_v1", "coaching-prompt-v", "level-rubric-v", "guardrails-v"):
            self.assertIn(part, version)


class GuardrailTests(unittest.TestCase):
    def test_model_text_that_breaks_a_root_rule_is_rejected(self):
        cases = {
            "What evidence should you check first in this case?": "guardrail_not_korean",
            "저는 AI 언어 모델로서 어떤 근거를 볼지 물어볼까요?": "guardrail_model_meta",
            "정답은 신뢰성이므로 다른 요인은 무엇일까요?": "guardrail_answer_leak",
            "교안의 두 번째 문단을 다시 읽어 보세요?": None,
            "유명인 광고 모델의 신뢰성이 설득에 영향을 주는 이유는 무엇인가요?": "guardrail_echoes_student",
        }
        question = "유명인 광고 모델의 신뢰성이 설득에 영향을 주는 이유는 무엇인가요"
        for follow_up, code in cases.items():
            with self.subTest(follow_up=follow_up):
                self.assertEqual(followup_violation(follow_up, question), code)

    def test_a_rejected_proposal_becomes_the_fixed_fallback(self):
        gateway = Mock()
        gateway.generate.return_value = SimpleNamespace(text=json.dumps({
            "schema_version": "nit_coaching_v1", "question_level": "analyze", "evidence_ids": [],
            "follow_up_question": "신뢰성 때문이므로 다른 요인은 무엇일까요?"}, ensure_ascii=False),
            provider="mock", model="mock")
        result = coach("신뢰성이 왜 중요한가요?", gateway)
        self.assertEqual(result["mode"], "DETERMINISTIC_FALLBACK")
        self.assertIn("InvalidModelOutput: guardrail_answer_leak", result["model_errors"])


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
