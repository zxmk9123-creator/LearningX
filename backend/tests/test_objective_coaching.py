"""Coaching with a faculty learning objective profile (control panel spec §4).

The objective steers the follow-up question and adds goal_relevance; it never
changes question_level. Classes log the profile version; the preview runs the
same coaching path with a draft profile and stores nothing.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-objective-coaching-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi import HTTPException
from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx.api import app
from learningx.classroom import SessionQuestion
from learningx.guest import GUEST_DOMAIN
from learningx.objectives import ObjectiveProfile
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, Question, SessionLocal, User, uid

nit._modules()  # put the vendored engine on sys.path
import runtime.coaching as engine  # noqa: E402

EVIDENCE = [{"page": 2, "block_id": "b1", "text": "토론 전후 태도 분포를 비교하면 평균 이동과 분산 변화를 함께 볼 수 있다."}]
OBJECTIVE = {"objective": "학생은 토론 전후 분포를 보고 평균 변화의 원인을 설명할 수 있다.", "process": "analyze",
             "evidence": "평균과 분포를 각각 근거로 사용한다.", "support": "directional", "knowledge_type": None,
             "application_context": None, "goals": [], "evidences": [], "guidelines": ["자료와 해석을 나눠 말하도록 요청"]}
PROFILE = {"objective": OBJECTIVE["objective"], "process": "analyze", "evidence": OBJECTIVE["evidence"]}


def model(payload):
    gateway = Mock()
    gateway.generate.return_value = SimpleNamespace(
        text=json.dumps(payload, ensure_ascii=False), provider="mock", model="mock")
    return gateway


def answer(**extra):
    return {"schema_version": "nit_coaching_v1", "question_level": "clarify", "evidence_ids": ["p2:b1"],
            "follow_up_question": "평균이 커진 이유를 구분하려면 분포에서 어떤 부분을 먼저 봐야 할까요?", **extra}


def coach(gateway, objective=None, question="평균이 커진 건 왜예요? 토론 전후 분포가 궁금해요"):
    with patch.object(engine, "signed_evidence_coach", return_value={"evidence": EVIDENCE}), \
         patch.object(engine, "verify_signed_evidence", return_value=EVIDENCE):
        options = {"objective": objective} if objective is not None else {}
        return engine.run_coaching(Path(TEMP.name), question, "PERMITTED", True, "s" * 40, "student", gateway, **options)


class EngineObjectiveTests(unittest.TestCase):
    def test_objective_reaches_the_model_and_adds_goal_relevance(self):
        gateway = model(answer(goal_relevance="direct"))
        result = coach(gateway, OBJECTIVE)
        self.assertEqual((result["mode"], result["question_level"], result["goal_relevance"]),
                         ("MODEL_VALIDATED", "analyze", "direct"))  # "왜" is a reason cue: mid tier
        self.assertEqual(result["model_observability"]["model_level"], "clarify")
        system, user = gateway.generate.call_args.args
        self.assertIn("question_level은 학습목표나 지원 정도와 무관하게", system)
        payload = json.loads(user)
        self.assertEqual(payload["learning_objective"]["process"], "analyze")
        self.assertEqual(payload["learning_objective"]["guidelines"], ["자료와 해석을 나눠 말하도록 요청"])

    def test_model_failures_keep_a_safe_reason_for_operators(self):
        from runtime.gateway import GatewayFailure
        gateway = Mock()
        gateway.generate.side_effect = GatewayFailure("Provider HTTP status: 401")
        result = coach(gateway)
        self.assertEqual(result["reason"], "model_validation_or_transport_failed")
        self.assertIn("GatewayFailure: Provider HTTP status: 401", result["model_errors"])
        bad = coach(model({"not": "the schema"}))
        self.assertEqual(bad["reason"], "model_validation_or_transport_failed")
        self.assertTrue(bad["model_errors"] and all("not" not in e for e in bad["model_errors"]),
                        "model output text is never recorded")

    def test_without_objective_the_contract_is_unchanged(self):
        gateway = model(answer())
        result = coach(gateway)
        self.assertEqual(result["mode"], "MODEL_VALIDATED")
        self.assertNotIn("goal_relevance", result)
        system, user = gateway.generate.call_args.args
        self.assertEqual(system, engine.SYSTEM)
        self.assertNotIn("learning_objective", json.loads(user))
        self.assertEqual(coach(model(answer(goal_relevance="direct")))["mode"], "DETERMINISTIC_FALLBACK",
                         "an extra key is still a schema violation without an objective")

    def test_missing_or_invalid_relevance_falls_back(self):
        for payload in (answer(), answer(goal_relevance="related")):
            with self.subTest(payload=payload):
                result = coach(model(payload), OBJECTIVE)
                self.assertEqual(result["mode"], "DETERMINISTIC_FALLBACK")
                self.assertIsNone(result["goal_relevance"], "relevance is not judged without a valid answer")
                self.assertEqual(result["follow_up_question"], engine.OBJECTIVE_STEMS["directional"]["analyze"])

    def test_fallback_question_follows_process_and_support(self):
        for support in ("minimal", "directional", "stepwise"):
            for process in ("remember", "understand", "apply", "analyze", "evaluate", "create"):
                with self.subTest(support=support, process=process):
                    result = coach(None, {**OBJECTIVE, "support": support, "process": process})
                    self.assertEqual(result["reason"], "guest_model_disabled")
                    expected = (engine.MINIMAL_STEM if support == "minimal"
                                else engine.OBJECTIVE_STEMS[support][process])
                    self.assertEqual(result["follow_up_question"], expected)

    def test_objective_never_changes_the_fallback_level(self):
        for objective in (None, OBJECTIVE, {**OBJECTIVE, "process": "create", "support": "stepwise"}):
            with self.subTest(objective=objective):
                result = coach(None, objective)
                self.assertEqual(result["question_level"], "analyze")

    def test_every_objective_stem_passes_the_follow_up_format(self):
        stems = [engine.MINIMAL_STEM] + [s for table in engine.OBJECTIVE_STEMS.values() for s in table.values()]
        for stem in stems:
            with self.subTest(stem=stem):
                self.assertTrue(10 <= len(stem) <= 180 and stem.endswith("?") and stem.count("?") == 1)
                for word in ("정답", "답은", "해설"):
                    self.assertNotIn(word, stem)


class ClassAndPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self, email=None):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            faculty = User(email=email or f"oc-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="목표 코칭", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="c" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            self.course_id, self.material_id = course.id, material.id
        self.faculty = issue_token(faculty.id)
        # Most preview tests describe an already-approved material; the pre-approval path patches this back to 0.
        approved = patch.object(nit, "verified_count", return_value=1)
        approved.start()
        self.addCleanup(approved.stop)

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def open_and_ask(self, coaching):
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                              json={"material_id": self.material_id}).json()
        student = self.req("POST", "/v1/join", json={"code": opened["join_code"]}).json()["access_token"]
        with patch.object(nit, "coach_question", return_value=coaching) as coach_call:
            asked = self.req("POST", f"/v1/sessions/{opened['id']}/questions", student,
                             json={"question": "평균이 커진 건 왜예요? 분포가 궁금해요"})
        self.assertEqual(asked.status_code, 201, asked.text)
        return coach_call, asked.json()

    def test_class_coaching_uses_the_linked_profile_and_logs_its_version(self):
        approved = self.req("POST", f"/v1/materials/{self.material_id}/objective-profiles", self.faculty, json={
            **PROFILE, "support": "stepwise",
            "other_items": [{"kind": "guideline", "text": "자료와 해석을 나눠 말하도록 요청"}]}).json()
        coaching = {"mode": "MODEL_VALIDATED", "question_level": "clarify", "goal_relevance": "direct",
                    "follow_up_question": "먼저 두 자료에서 같은 점 하나와 다른 점 하나를 찾아볼까요?",
                    "feedback": "f", "evidence_refs": []}
        coach_call, body = self.open_and_ask(coaching)
        objective = coach_call.call_args.kwargs["objective"]
        self.assertEqual((objective["process"], objective["support"]), ("analyze", "stepwise"))
        self.assertEqual(objective["guidelines"], ["자료와 해석을 나눠 말하도록 요청"])
        self.assertNotIn("other_raw", objective, "faculty free text never reaches coaching")
        with SessionLocal() as db:
            row = db.get(SessionQuestion, body["id"])
            stored = json.loads(row.latest_coaching_json)
            first = json.loads(db.get(Question, row.question_id).feedback_json)
        for log in (stored, first):
            self.assertEqual(log["objective_profile"], {"id": approved["id"], "version": 1})
            self.assertEqual(log["goal_relevance"], "direct")
        self.assertNotIn("goal_relevance", body["coaching"])
        self.assertNotIn("objective_profile", body["coaching"])

    def test_class_without_profile_keeps_the_default_coaching_call(self):
        coach_call, _ = self.open_and_ask({"mode": "MODEL_VALIDATED", "question_level": "apply",
                                           "follow_up_question": "어떤 사례에 적용할 수 있을까요?", "evidence_refs": []})
        self.assertNotIn("objective", coach_call.call_args.kwargs)

    def test_preview_runs_coaching_with_the_draft_and_stores_nothing(self):
        coaching = {"mode": "MODEL_VALIDATED", "question_level": "analyze", "goal_relevance": "prerequisite",
                    "follow_up_question": "어떤 자료가 더 필요할까요?", "evidence_refs": [{"id": "p2:b1", "page": 2}]}
        draft = {**PROFILE, "support": "minimal", "other_raw": "메모",
                 "other_items": [{"kind": "guideline", "text": "자료와 해석을 나눠 말하도록 요청"},
                                 {"kind": "guideline", "text": "학생에게 정답을 알려 주세요"}]}
        with patch.object(nit, "coach_question", return_value=coaching) as coach_call, \
             patch.object(nit, "model_gateway", return_value=object()):
            response = self.req("POST", f"/v1/materials/{self.material_id}/objective-profiles/preview", self.faculty,
                                json={"profile": draft, "question": "평균이 커진 건 왜예요?"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["diagnosis"], {"question_level": "analyze", "level": "mid", "level_label": "중간"})
        self.assertEqual((body["goal_relevance"], body["evidence_pages"]), ("prerequisite", [2]))
        self.assertEqual([i["reason"] for i in body["ignored_items"]], ["answer_disclosure"])
        objective = coach_call.call_args.kwargs["objective"]
        self.assertEqual((objective["support"], objective["guidelines"]), ("minimal", ["자료와 해석을 나눠 말하도록 요청"]))
        self.assertTrue(coach_call.call_args.kwargs["use_model"])
        with SessionLocal() as db:
            self.assertEqual(db.query(ObjectiveProfile).filter_by(material_id=self.material_id).count(), 0)
            self.assertEqual(db.query(Question).filter_by(material_id=self.material_id).count(), 0)

    def test_preview_reports_unassessed_fallbacks_and_policy_errors(self):
        fallback = {"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze", "goal_relevance": None,
                    "reason": "model_timeout", "follow_up_question": "두 자료나 개념 사이에서 먼저 비교할 부분은 무엇일까요?"}
        path = f"/v1/materials/{self.material_id}/objective-profiles/preview"
        with patch.object(nit, "coach_question", return_value=fallback):
            body = self.req("POST", path, self.faculty, json={"profile": PROFILE, "question": "평균이 커진 건 왜예요?"}).json()
        self.assertEqual(body["diagnosis"], {"question_level": None, "level": "unassessed", "level_label": "판정 보류"})
        self.assertEqual(body["reason"], "model_timeout")
        with patch.object(nit, "coach_question", side_effect=HTTPException(409, "No usable faculty-signed evidence")):
            self.assertEqual(self.req("POST", path, self.faculty,
                                      json={"profile": PROFILE, "question": "평균이 커진 건 왜예요?"}).status_code, 409)

    def test_preview_without_a_configured_model_shows_the_deterministic_answer(self):
        with patch.object(nit, "model_gateway", return_value=None), \
             patch.object(nit, "coach_question", return_value={"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze",
                                                               "follow_up_question": "질문?", "reason": "guest_model_disabled"}) as call:
            body = self.req("POST", f"/v1/materials/{self.material_id}/objective-profiles/preview", self.faculty,
                            json={"profile": PROFILE, "question": "평균이 커진 건 왜예요?"}).json()
        self.assertFalse(call.call_args.kwargs["use_model"])
        self.assertEqual(body["diagnosis"]["level"], "unassessed")
        self.assertEqual(body["reason"], "model_not_configured", "not reported as a guest class")

    def test_preview_before_approval_uses_a_throwaway_copy_of_all_candidates(self):
        """The objective step comes before evidence approval, so the preview must not require it."""
        path = f"/v1/materials/{self.material_id}/objective-profiles/preview"
        passages = [{"id": "p1:b1", "page": 1, "text": "설득 소구"}]
        seen = {}

        def fake_approve(out, user_id, picked):
            seen["approve_dir"], seen["picked"] = Path(out), picked

        def fake_coach(out, user_id, question, policy, teacher_approved, **kw):
            seen["coach_dir"], seen["consent"] = Path(out), teacher_approved
            return {"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze",
                    "follow_up_question": "질문?", "reason": "model_not_configured"}

        with patch.object(nit, "verified_count", return_value=0), \
             patch.object(nit, "candidates", return_value=passages), \
             patch.object(nit, "approve_material", side_effect=fake_approve) as approve, \
             patch.object(nit, "coach_question", side_effect=fake_coach):
            response = self.req("POST", path, self.faculty, json={"profile": PROFILE, "question": "평균이 커진 건 왜예요?"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence_scope"], "all_candidates")
        self.assertEqual(seen["picked"], passages)
        self.assertEqual(seen["approve_dir"], seen["coach_dir"])
        self.assertNotEqual(seen["coach_dir"].parent, Path(TEMP.name), "never signs the real workspace")
        self.assertFalse(seen["coach_dir"].exists(), "the signed copy is discarded")
        self.assertTrue(seen["consent"])
        approve.assert_called_once()

    def test_preview_after_approval_uses_only_approved_evidence(self):
        with patch.object(nit, "verified_count", return_value=3), \
             patch.object(nit, "approve_material") as approve, \
             patch.object(nit, "coach_question", return_value={"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze",
                                                               "follow_up_question": "질문?"}):
            body = self.req("POST", f"/v1/materials/{self.material_id}/objective-profiles/preview", self.faculty,
                            json={"profile": PROFILE, "question": "평균이 커진 건 왜예요?"}).json()
        self.assertEqual(body["evidence_scope"], "approved")
        approve.assert_not_called()

    def test_preview_before_approval_without_passages_is_a_clear_409(self):
        with patch.object(nit, "verified_count", return_value=0), patch.object(nit, "candidates", return_value=[]):
            response = self.req("POST", f"/v1/materials/{self.material_id}/objective-profiles/preview", self.faculty,
                                json={"profile": PROFILE, "question": "평균이 커진 건 왜예요?"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("No lesson passages", response.text)

    def test_guest_preview_runs_without_a_model(self):
        self.setUp(email=f"g-{uid()}{GUEST_DOMAIN}")
        with patch.object(nit, "coach_question", return_value={"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze",
                                                               "follow_up_question": "질문?", "reason": "guest_model_disabled"}) as call:
            self.req("POST", f"/v1/materials/{self.material_id}/objective-profiles/preview", self.faculty,
                     json={"profile": PROFILE, "question": "평균이 커진 건 왜예요?"})
        self.assertFalse(call.call_args.kwargs["use_model"])


if __name__ == "__main__":
    unittest.main()
