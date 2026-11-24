"""Coaching phase 1: starter questions get coaching, and no matched passage no longer
means a canned sentence.

① A definition ("○○이란 무엇인가요?"), a question with an unclear target ("이건 왜
   그래요?") or a statement ("이해가 안 돼요") used to be blocked like harmful input.
   They are now coached as starters; harmful reasons still block, and the board gate is
   unchanged (a starter is sent only after it is rewritten).
⑤ When no approved passage matched, the engine returned an abstract fixed sentence
   without asking the model. It now coaches the question itself with nothing to cite;
   only classes without a model fall back, with plainer fixed sentences.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-coaching-phase1-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx import suitability as fit
from learningx.api import app
from learningx.classroom import SessionQuestion
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, SessionLocal, User, uid

nit._modules()
import runtime.coaching as engine  # noqa: E402

VALIDATED = {"mode": "MODEL_VALIDATED", "question_level": "clarify", "feedback": "f",
             "follow_up_question": "공포 소구가 쓰인 광고를 하나 떠올리면 무엇이 생각나나요?", "evidence_refs": []}


class RoutingTests(unittest.TestCase):
    def test_starters_are_coached(self):
        for text in ("공포 소구란 무엇인가요?", "이건 왜 그래요?", "이해가 안 돼요", "이 부분이 헷갈려요."):
            with self.subTest(text=text):
                self.assertEqual(fit.coaching_route(text), "starter")

    def test_real_questions_are_coached_as_before(self):
        self.assertEqual(fit.coaching_route("유명인 모델의 신뢰성이 설득 효과에 영향을 주는 이유는 무엇인가요?"), "coach")

    def test_harm_still_blocks_even_beside_a_starter(self):
        for text in ("공포 소구란 무엇인가요? 내 번호 010-1234-5678", "교수님 사랑해요", "ㅋㅋㅋㅋ", "개노잼",
                     "ㅇㅇ", "배고프다"):
            with self.subTest(text=text):
                self.assertEqual(fit.coaching_route(text), "block")

    def test_route_agrees_with_the_board_gate(self):
        """Never coach something the board gate calls harmful."""
        for text in ("공포 소구란 무엇인가요? 내 번호 010-1234-5678", "교수님 사랑해요", "공포 소구란 무엇인가요?"):
            harmful = set(fit.intake_reasons(text)) - fit.STARTER_REASONS
            if harmful:
                self.assertEqual(fit.coaching_route(text), "block", text)


class EngineNoEvidenceTests(unittest.TestCase):
    def run_engine(self, gateway, evidence=(), question="이건 시험에 나올 만한 내용인가요?"):
        with patch.object(engine, "signed_evidence_coach", return_value={"evidence": list(evidence)}), \
             patch.object(engine, "verify_signed_evidence", return_value=list(evidence)):
            return engine.run_coaching(Path(TEMP.name), question, "PERMITTED", True, "s" * 40, "student", gateway)

    def model(self, **answer):
        gateway = Mock()
        body = {"schema_version": "nit_coaching_v1", "question_level": "clarify", "evidence_ids": [],
                "follow_up_question": "이 내용이 수업의 어느 부분과 이어진다고 생각하나요?", **answer}
        gateway.generate.return_value = SimpleNamespace(text=json.dumps(body, ensure_ascii=False),
                                                        provider="mock", model="mock")
        return gateway

    def test_no_matched_passage_still_asks_the_model(self):
        gateway = self.model()
        result = self.run_engine(gateway)
        self.assertEqual(result["mode"], "MODEL_VALIDATED")
        self.assertEqual((result["evidence_mode"], result["evidence_refs"]), ("none", []))
        payload = json.loads(gateway.generate.call_args.args[1])
        self.assertEqual((payload["evidence_mode"], payload["allowed_evidence"]), ("none", []))
        self.assertIn("evidence_mode가 none", gateway.generate.call_args.args[0])

    def test_the_model_cannot_cite_anything_without_a_match(self):
        result = self.run_engine(self.model(evidence_ids=["p1:b1"]))
        self.assertEqual((result["mode"], result["reason"]), ("DETERMINISTIC_FALLBACK", "model_validation_or_transport_failed"))

    def test_without_a_model_the_plain_fixed_sentence_is_used(self):
        result = self.run_engine(None)
        self.assertEqual((result["mode"], result["reason"]), ("DETERMINISTIC_FALLBACK", "no_matching_approved_evidence"))
        self.assertEqual(result["follow_up_question"], "이 개념이 어디에 쓰이는지와 왜 중요한지 중 어느 쪽이 더 궁금한가요?")

    def test_matched_passages_are_unchanged(self):
        evidence = [{"page": 2, "block_id": "b1", "text": "공포 소구는 위협을 강조해 태도 변화를 이끈다."}]
        result = self.run_engine(self.model(evidence_ids=["p2:b1"]), evidence, "공포 소구는 왜 효과가 있나요?")
        self.assertEqual((result["evidence_mode"], result["evidence_refs"]), ("matched", [{"id": "p2:b1", "page": 2}]))

    def test_policy_version_moved_on(self):
        self.assertIn("coaching-prompt-v3", engine.ROOT_POLICY_VERSION)

    def test_plain_stems_pass_the_follow_up_rules(self):
        for level, stem in engine.STEMS.items():
            with self.subTest(level=level):
                self.assertTrue(stem.endswith("?") and 10 <= len(stem) <= 180)
                self.assertIsNone(engine.followup_violation(stem, "질문"))


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            faculty = User(email=f"p1-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="출발 질문", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="7" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        self.faculty = issue_token(faculty.id)
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.client.post(f"/v1/courses/{course_id}/sessions", json={"material_id": material_id},
                                      headers=self.h(self.faculty)).json()
        self.sid = opened["id"]
        self.client.put(f"/v1/sessions/{self.sid}/chat", json={"open": True}, headers=self.h(self.faculty))
        self.student = self.client.post("/v1/join", json={"code": opened["join_code"]}).json()["access_token"]

    @staticmethod
    def h(token):
        return {"Authorization": "Bearer " + token}

    def ask(self, text, coaching=VALIDATED):
        with patch.object(nit, "coach_question", return_value=coaching) as call:
            response = self.client.post(f"/v1/sessions/{self.sid}/questions", json={"question": text},
                                        headers=self.h(self.student))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), call

    def test_a_starter_is_coached_but_cannot_be_sent_yet(self):
        body, call = self.ask("공포 소구란 무엇인가요?")
        call.assert_called_once()
        self.assertTrue(body["starter"])
        self.assertIsNone(body["message"])
        self.assertEqual(body["coaching"]["follow_up_question"], VALIDATED["follow_up_question"])
        self.assertEqual(body["suitability"]["status"], "needs_revision")
        sent = self.client.post(f"/v1/session-questions/{body['id']}/submit",
                                json={"revision_id": body["revision_id"]}, headers=self.h(self.student))
        self.assertNotEqual(sent.status_code, 200)
        with SessionLocal() as db:
            self.assertFalse(db.get(SessionQuestion, body["id"]).submitted)

    def test_rewriting_a_starter_continues_the_same_question(self):
        body, _ = self.ask("이해가 안 돼요")
        with patch.object(nit, "coach_question", return_value={**VALIDATED, "question_level": "analyze"}):
            revised = self.client.post(f"/v1/session-questions/{body['id']}/revisions",
                                       json={"text": "공포 소구가 태도 변화를 일으키는 이유는 무엇인가요?"},
                                       headers=self.h(self.student)).json()
        self.assertEqual(revised["id"], body["id"])
        self.assertFalse(revised["starter"])

    def test_harmful_input_still_skips_coaching(self):
        body, call = self.ask("공포 소구란 무엇인가요? 내 번호 010-1234-5678")
        call.assert_not_called()
        self.assertFalse(body["starter"])
        self.assertIsNone(body["coaching"])
        self.assertEqual(body["message"], fit.REJECTION_MESSAGE)


if __name__ == "__main__":
    unittest.main()
