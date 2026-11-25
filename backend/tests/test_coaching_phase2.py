"""Coaching phase 2: the coach remembers the question being rewritten, writes question
frames from the student's own words, and says why the question has its level.

② Conversation: a revision carries the earlier turns of the same question (first and
   previous text, previous follow-up and level) from the server's records. Repeating the
   previous follow-up word for word is rejected, and the conversation is part of the
   ledger context.
③ Frames: up to two fill-in questions with a ___ blank; anything else is dropped and the
   fixed templates are used.
④ Reason: one plain sentence for the final level; when the rubric changed the model's
   level, the rule's own reason is used instead.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-coaching-phase2-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx import judgments
from learningx.api import app
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, SessionLocal, User, uid

nit._modules()
import runtime.coaching as engine  # noqa: E402

QUESTION = "공포 소구는 왜 효과가 있나요?"
EVIDENCE = [{"page": 2, "block_id": "b1", "text": "공포 소구는 위협을 강조해 태도 변화를 이끈다."}]


def gateway(**answer):
    g = Mock()
    body = {"schema_version": "nit_coaching_v1", "question_level": "analyze", "evidence_ids": ["p2:b1"],
            "follow_up_question": "위협의 크기가 달라지면 태도 변화는 어떻게 달라질까요?", **answer}
    g.generate.return_value = SimpleNamespace(text=json.dumps(body, ensure_ascii=False), provider="mock", model="mock")
    return g


def run(g, question=QUESTION, history=None):
    with patch.object(engine, "signed_evidence_coach", return_value={"evidence": EVIDENCE}), \
         patch.object(engine, "verify_signed_evidence", return_value=EVIDENCE):
        return engine.run_coaching(Path(TEMP.name), question, "PERMITTED", True, "s" * 40, "student", g,
                                   history=history)


class FrameTests(unittest.TestCase):
    # QUESTION ("왜 효과가 있나요") is a mid question: frames must ask for a deep one.
    DEEP = ["공포 소구를 ___ 기준으로 평가하면 어떤 한계가 있나요?",
            "공포 소구와 ___ 중 어느 쪽이 ___ 제품에 더 효과적인가요?"]

    def test_valid_frames_are_kept(self):
        result = run(gateway(question_frames=self.DEEP))
        self.assertEqual(result["mode"], "MODEL_VALIDATED")
        self.assertEqual(result["question_frames"], self.DEEP)

    def test_bad_frames_are_dropped_not_fatal(self):
        frames = ["공포 소구는 왜 효과가 있나요?",                  # no blank, the student's own question
                  "정답은 ___ 때문입니다?",                        # answer leak
                  "___?",                                         # too short
                  "Why does ___ work for fear appeals?",           # not Korean
                  "공포 소구를 ___ 기준으로 평가하면 어떤 한계가 있나요",  # no question mark
                  "공포 소구를 ___ 기준으로 평가해 보세요?"]        # a command with a question mark
        result = run(gateway(question_frames=frames))
        self.assertEqual(result["mode"], "MODEL_VALIDATED")
        self.assertNotIn("question_frames", result)

    def test_frames_at_the_same_level_or_without_a_cue_are_dropped(self):
        """Seen in the production pilot: a mid question got mid frames back."""
        frames = ["___가 태도 변화를 일으키는 구체적인 과정은 무엇인가요?",   # mid (mechanism)
                  "공포 소구에서 ___가 커지면 무엇이 달라지나요?",          # no level cue
                  self.DEEP[0]]
        result = run(gateway(question_frames=frames))
        self.assertEqual(result["question_frames"], [self.DEEP[0]])

    def test_a_shallow_question_gets_mid_frames(self):
        frames = ["공포 소구가 ___에 영향을 주는 이유는 무엇인가요?", "공포 소구의 ___은 무엇인가요?"]
        result = run(gateway(question_level="clarify", question_frames=frames), question="공포 소구란 무엇인가요?")
        self.assertEqual(result["question_level"], "clarify")
        self.assertEqual(result["question_frames"], [frames[0]])

    def test_at_most_two_frames_and_blank_normalised(self):
        frames = [f"공포 소구를 ____ 기준 {i}로 평가하면 어떤가요?" for i in range(3)]
        result = run(gateway(question_frames=frames))
        self.assertEqual(len(result["question_frames"]), 2)
        self.assertTrue(all("___" in f and "____" not in f for f in result["question_frames"]))

    def test_frames_are_optional(self):
        self.assertNotIn("question_frames", run(gateway()))

    def test_unknown_keys_still_fail_the_schema(self):
        result = run(gateway(answer="정답"))
        self.assertEqual(result["mode"], "DETERMINISTIC_FALLBACK")


class ReasonTests(unittest.TestCase):
    def test_the_model_reason_is_used_when_it_names_the_final_level(self):
        result = run(gateway(level_reason="까닭을 묻고 있어서 중간 수준이에요."))
        self.assertEqual(result["question_level"], "analyze")
        self.assertEqual(result["level_reason"], "까닭을 묻고 있어서 중간 수준이에요.")

    def test_a_rule_change_uses_the_rule_reason(self):
        result = run(gateway(question_level="evaluate", level_reason="판단을 요구해서 깊은 수준이에요."))
        self.assertEqual(result["question_level"], "analyze")
        self.assertTrue(result["level_basis"].startswith("rule:"))
        self.assertEqual(result["level_reason"], engine.RULE_REASONS[result["level_basis"].split(":", 1)[1]])

    def test_bad_reasons_are_replaced_by_the_rule_reason(self):
        """Seen in the production pilot: third-person reasons, and reasons that never appeared."""
        rule = engine.RULE_REASONS["reason"]
        for reason in ("왜일까요?", "짧음", "정답은 위협 때문이에요.", "가" * 90, 7,
                       "학생이 공포 소구가 태도 변화를 일으키는 메커니즘을 원인으로 분석하려고 합니다.",
                       "원인을 묻는 질문이므로 중간 수준입니다.", "뜻을 묻는 질문이라 얕은 수준이에요.", None):
            with self.subTest(reason=reason):
                result = run(gateway(level_reason=reason) if reason is not None else gateway())
                self.assertEqual(result["mode"], "MODEL_VALIDATED")
                self.assertEqual(result["level_reason"], rule)

    def test_a_question_without_a_cue_gets_the_level_sentence(self):
        result = run(gateway(question_level="analyze"), question="공포 소구를 광고에 쓰는 장면을 떠올려 볼 수 있을까요?")
        self.assertEqual(result["level_basis"], "model")
        self.assertEqual(result["level_reason"], engine.TIER_REASONS["mid"])

    def test_every_fixed_reason_passes_the_reason_rules(self):
        for text in list(engine.RULE_REASONS.values()) + list(engine.TIER_REASONS.values()):
            with self.subTest(text=text):
                self.assertEqual(engine._clean_reason(text, "질문"), text)


class ConversationTests(unittest.TestCase):
    HISTORY = {"first_question": "이해가 안 돼요", "previous_question": "공포 소구가 뭐예요?",
               "previous_follow_up": "공포 소구가 쓰인 광고를 하나 떠올리면 무엇이 생각나나요?",
               "previous_level": "clarify", "turn": 3}

    def test_the_conversation_reaches_the_model(self):
        g = gateway()
        result = run(g, history=self.HISTORY)
        payload = json.loads(g.generate.call_args.args[1])
        self.assertEqual(payload["conversation"], self.HISTORY)
        self.assertEqual(result["conversation_turn"], 3)
        self.assertIn("conversation", g.generate.call_args.args[0])

    def test_no_history_means_no_conversation(self):
        g = gateway()
        run(g)
        self.assertNotIn("conversation", json.loads(g.generate.call_args.args[1]))

    def test_repeating_the_previous_follow_up_is_rejected(self):
        g = gateway(follow_up_question=self.HISTORY["previous_follow_up"])
        result = run(g, history=self.HISTORY)
        self.assertEqual(result["mode"], "DETERMINISTIC_FALLBACK")
        self.assertTrue(any("guardrail_repeats_follow_up" in e for e in result.get("model_errors", [])))

    def test_an_accepted_retry_keeps_the_rejection_code(self):
        bad = gateway(follow_up_question=self.HISTORY["previous_follow_up"]).generate.return_value
        good = gateway().generate.return_value
        g = Mock(); g.generate.side_effect = [bad, good]
        result = run(g, history=self.HISTORY)
        self.assertEqual(result["mode"], "MODEL_VALIDATED")
        self.assertEqual(result["model_observability"]["attempts"], 2)
        self.assertEqual(result["model_errors"], ["InvalidModelOutput: guardrail_repeats_follow_up"])

    def test_long_history_is_capped(self):
        g = gateway()
        run(g, history={"first_question": "가" * 1000, "turn": 2, "other": "x"})
        conversation = json.loads(g.generate.call_args.args[1])["conversation"]
        self.assertEqual(len(conversation["first_question"]), 400)
        self.assertNotIn("other", conversation)


class LedgerTests(unittest.TestCase):
    def test_new_fields_are_replayed(self):
        decision = judgments.coaching_decision({
            "mode": "MODEL_VALIDATED", "question_level": "analyze", "follow_up_question": "q?",
            "feedback": "f", "evidence_refs": [], "level_reason": "r", "question_frames": ["a ___?"],
            "conversation_turn": 2})
        self.assertEqual(decision["level_reason"], "r")
        self.assertEqual(decision["question_frames"], ["a ___?"])
        self.assertNotIn("conversation_turn", decision)


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
            faculty = User(email=f"p2-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="대화 코칭", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="8" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        faculty_token = issue_token(faculty.id)
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.client.post(f"/v1/courses/{course_id}/sessions", json={"material_id": material_id},
                                      headers=self.h(faculty_token)).json()
        self.sid = opened["id"]
        self.client.put(f"/v1/sessions/{self.sid}/chat", json={"open": True}, headers=self.h(faculty_token))
        self.student = self.client.post("/v1/join", json={"code": opened["join_code"]}).json()["access_token"]

    @staticmethod
    def h(token):
        return {"Authorization": "Bearer " + token}

    def coaching(self, level="clarify", follow="공포 소구가 쓰인 광고를 하나 떠올리면 무엇이 생각나나요?", **extra):
        return {"mode": "MODEL_VALIDATED", "question_level": level, "feedback": "f",
                "follow_up_question": follow, "evidence_refs": [], **extra}

    def test_revision_sends_the_earlier_turns_and_the_student_sees_reason_and_frames(self):
        with patch.object(nit, "coach_question", return_value=self.coaching()) as first:
            body = self.client.post(f"/v1/sessions/{self.sid}/questions", json={"question": "공포 소구란 무엇인가요?"},
                                    headers=self.h(self.student)).json()
        self.assertNotIn("history", first.call_args.kwargs)
        extra = {"level_reason": "까닭을 묻고 있어서 중간 수준이에요.",
                 "question_frames": ["공포 소구에서 ___가 커지면 무엇이 달라지나요?"]}
        with patch.object(nit, "coach_question",
                          return_value=self.coaching("analyze", "위협이 약하면 어떻게 될까요?", **extra)) as second:
            revised = self.client.post(f"/v1/session-questions/{body['id']}/revisions",
                                       json={"text": "공포 소구가 태도 변화를 일으키는 이유는 무엇인가요?"},
                                       headers=self.h(self.student)).json()
        history = second.call_args.kwargs["history"]
        self.assertEqual(history["first_question"], "공포 소구란 무엇인가요?")
        self.assertEqual(history["previous_question"], "공포 소구란 무엇인가요?")
        self.assertEqual(history["previous_follow_up"], self.coaching()["follow_up_question"])
        self.assertEqual((history["previous_level"], history["turn"]), ("clarify", 2))
        self.assertEqual(revised["coaching"]["level_reason"], extra["level_reason"])
        self.assertEqual(revised["coaching"]["question_frames"], extra["question_frames"])

        with patch.object(nit, "coach_question", return_value=self.coaching("analyze", "다른 예는 무엇일까요?")) as third:
            self.client.post(f"/v1/session-questions/{body['id']}/revisions",
                             json={"text": "공포 소구가 청소년에게 영향을 주는 이유는 무엇인가요?"},
                             headers=self.h(self.student))
        history = third.call_args.kwargs["history"]
        self.assertEqual(history["first_question"], "공포 소구란 무엇인가요?")
        self.assertEqual(history["previous_question"], "공포 소구가 태도 변화를 일으키는 이유는 무엇인가요?")
        self.assertEqual(history["turn"], 3)

    def test_question_requests_report_database_time(self):
        """Server-Timing tells database round trips apart from model time in production."""
        with patch.object(nit, "coach_question", return_value=self.coaching()):
            response = self.client.post(f"/v1/sessions/{self.sid}/questions", json={"question": "공포 소구란 무엇인가요?"},
                                        headers=self.h(self.student))
        timing = response.headers.get("Server-Timing", "")
        self.assertRegex(timing, r'app;dur=[\d.]+')
        self.assertRegex(timing, r'db;dur=[\d.]+;desc="\d+ queries"')
        self.assertEqual(response.headers.get("Timing-Allow-Origin"), "*")

    def test_unassessed_coaching_hides_reason_and_frames(self):
        fallback = {"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze", "feedback": "f",
                    "follow_up_question": "어떤 점이 궁금한가요?", "evidence_refs": [],
                    "level_reason": "x 이유예요.", "question_frames": ["___ 무엇인가요?"]}
        with patch.object(nit, "coach_question", return_value=fallback):
            body = self.client.post(f"/v1/sessions/{self.sid}/questions",
                                    json={"question": "공포 소구가 태도 변화를 일으키는 이유는 무엇인가요?"},
                                    headers=self.h(self.student)).json()
        self.assertIsNone(body["coaching"]["level_reason"])
        self.assertEqual(body["coaching"]["question_frames"], [])


if __name__ == "__main__":
    unittest.main()
