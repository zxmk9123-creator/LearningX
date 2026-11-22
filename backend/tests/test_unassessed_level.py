"""A question level exists only when the model's judgment was validated.

Fallback coaching (no matching approved evidence, model timeout or failure,
guest classes without a model) must not borrow a level: it is recorded as
unassessed and kept out of level counts, filters and student level feedback.
"""
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-unassessed-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import classroom
from learningx import engine_adapter as nit
from learningx.api import app
from learningx.classroom import SessionQuestion
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, SessionLocal, User, uid


def validated(level):
    return {"mode": "MODEL_VALIDATED", "question_level": level, "feedback": "근거를 구체화해 보세요.",
            "follow_up_question": "두 개념은 어떤 기준으로 구별할 수 있을까요?",
            "evidence_refs": [{"id": "p1:b1", "page": 1}]}


def fallback(reason, level="analyze"):
    # Shape of runtime.coaching._fallback: a default level that was never judged.
    return {"mode": "DETERMINISTIC_FALLBACK", "question_level": level,
            "follow_up_question": "이 주장에 담긴 가정과 근거를 구분하면 어떤 질문이 생길까요?",
            "evidence_refs": [], "feedback": "질문의 전제와 비교 기준을 직접 검토해 보세요.",
            "validation": "fallback", "reason": reason}


class UnassessedLevelTests(unittest.TestCase):
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
            faculty = User(email=f"lvl-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="판정 보류", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="f" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        self.faculty = issue_token(faculty.id)
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{course_id}/sessions", self.faculty,
                              json={"material_id": material_id}).json()
        self.session_id = opened["id"]
        self.code = opened["join_code"]

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def ask(self, coaching, text=None):
        # Distinct texts: the same question in a class reuses its first level (level_cache).
        self._asked = getattr(self, "_asked", 0) + 1
        text = text or f"{self._asked}번 실험에서 두 집단의 차이는 무엇인가요?"
        student = self.req("POST", "/v1/join", json={"code": self.code}).json()["access_token"]
        with patch.object(nit, "coach_question", return_value=coaching):
            asked = self.req("POST", f"/v1/sessions/{self.session_id}/questions", student, json={"question": text})
        self.assertEqual(asked.status_code, 201, asked.text)
        return student, asked.json()

    def submit(self, student, row_id):
        self.assertEqual(self.req("POST", f"/v1/session-questions/{row_id}/submit", student).status_code, 200)

    def test_every_fallback_reason_is_unassessed(self):
        for reason, level in [("no_matching_approved_evidence", "clarify"), ("explicit_page_not_approved", "clarify"),
                              ("guest_model_disabled", "analyze"), ("model_timeout", "analyze"),
                              ("model_validation_or_transport_failed", "analyze")]:
            with self.subTest(reason=reason):
                _, body = self.ask(fallback(reason, level))
                self.assertEqual(body["level"], "unassessed")
                self.assertEqual(body["coaching"]["level"], "unassessed")
                self.assertIsNone(body["coaching"]["question_level"], "no borrowed level reaches the student")
                self.assertTrue(body["coaching"]["follow_up_question"], "the fallback question is still shown")
                with SessionLocal() as db:
                    row = db.get(SessionQuestion, body["id"])
                    self.assertEqual((row.level, row.first_level), ("unassessed", "unassessed"))
                    self.assertEqual(json.loads(row.latest_coaching_json)["reason"], reason,
                                     "the failure reason is kept apart from the judgment")

    def test_a_class_without_a_configured_model_still_coaches(self):
        """A faculty (non-guest) class on a server with no model gets the deterministic coaching, not a 503."""
        student = self.req("POST", "/v1/join", json={"code": self.code}).json()["access_token"]
        calls = []
        def coach(out, sid, text, policy, approved, *, use_model=True, objective=None):
            calls.append(use_model)
            return fallback("guest_model_disabled") if not use_model else validated("analyze")
        with patch.object(nit, "model_gateway", return_value=None), patch.object(nit, "coach_question", side_effect=coach):
            asked = self.req("POST", f"/v1/sessions/{self.session_id}/questions", student,
                             json={"question": "이 실험에서 두 집단의 차이는 무엇인가요?"})
        self.assertEqual(asked.status_code, 201, asked.text)
        self.assertEqual(calls, [False], "no model call is attempted without a configured model")
        self.assertEqual(asked.json()["level"], "unassessed")
        with patch.object(nit, "model_gateway", return_value=object()), patch.object(nit, "coach_question", side_effect=coach):
            with_model = self.req("POST", f"/v1/sessions/{self.session_id}/questions", student,
                                  json={"question": "두 집단의 평균 차이는 어떤 근거로 설명하나요?"})
        self.assertEqual(with_model.status_code, 201, with_model.text)
        self.assertTrue(calls[-1], "a configured model is still used")

    def test_validated_levels_are_unchanged(self):
        for model_level, level in [("clarify", "shallow"), ("apply", "mid"), ("analyze", "mid"),
                                   ("evaluate", "deep"), ("create", "deep")]:
            with self.subTest(model_level=model_level):
                _, body = self.ask(validated(model_level))
                self.assertEqual(body["level"], level)
                self.assertEqual(body["coaching"]["question_level"], model_level)
        _, unknown = self.ask(validated("memorize"))
        self.assertEqual(unknown["level"], "unassessed", "an unknown model level is not guessed")

    def test_board_counts_filters_and_csv_keep_unassessed_apart(self):
        deep_student, deep = self.ask(validated("evaluate"))
        guest_student, guest = self.ask(fallback("guest_model_disabled"))
        none_student, no_evidence = self.ask(fallback("no_matching_approved_evidence", "clarify"))
        for student, body in ((deep_student, deep), (guest_student, guest), (none_student, no_evidence)):
            self.submit(student, body["id"])
        board = self.req("GET", f"/v1/sessions/{self.session_id}/board", self.faculty).json()
        self.assertEqual(board["counts"], {"all": 3, "shallow": 0, "mid": 0, "deep": 1, "unassessed": 2})
        self.assertEqual(board["session"]["questions_deep"], 1)
        labels = {item["id"]: item["level_label"] for item in board["items"]}
        self.assertEqual(labels[guest["id"]], "판정 보류")
        for level, expected in (("unassessed", {guest["id"], no_evidence["id"]}), ("mid", set()),
                                ("shallow", set()), ("deep", {deep["id"]})):
            filtered = self.req("GET", f"/v1/sessions/{self.session_id}/board", self.faculty,
                                params={"level": level}).json()["items"]
            self.assertEqual({item["id"] for item in filtered}, expected, level)
        export = self.req("GET", f"/v1/sessions/{self.session_id}/export", self.faculty)
        rows = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))[1:]
        self.assertEqual(sorted(row[1] for row in rows), sorted(["깊은", "판정 보류", "판정 보류"]))
        index = self.req("GET", f"/v1/sessions/{self.session_id}/live", self.faculty).json()["index"]
        self.assertTrue(all(item["level"] in classroom.LEVEL_LABEL for item in index))

    def test_revision_can_move_between_assessed_and_unassessed(self):
        student, body = self.ask(fallback("model_timeout"))
        revise = f"/v1/session-questions/{body['id']}/revisions"
        with patch.object(nit, "coach_question", return_value=validated("analyze")):
            revised = self.req("POST", revise, student, json={"text": "두 집단의 차이를 어떤 자료로 확인할 수 있나요?"}).json()
        self.assertEqual(revised["level"], "mid")
        with patch.object(nit, "coach_question", return_value=fallback("model_timeout")):
            again = self.req("POST", revise, student, json={"text": "두 집단의 차이를 다른 자료로도 확인할 수 있나요?"}).json()
        self.assertEqual(again["level"], "unassessed", "a later failed judgment does not keep an old level")
        with SessionLocal() as db:
            self.assertEqual(db.get(SessionQuestion, body["id"]).first_level, "unassessed")
        mine = self.req("GET", f"/v1/sessions/{self.session_id}/mine", student).json()["items"][0]
        self.assertEqual((mine["level"], mine["level_label"]), ("unassessed", "판정 보류"))

    def test_existing_rows_are_marked_by_the_correction_script(self):
        from scripts.mark_unassessed_levels import mark
        validated_student, kept = self.ask(validated("evaluate"))
        _, legacy = self.ask(fallback("no_matching_approved_evidence", "clarify"))
        _, mixed = self.ask(validated("analyze"))
        with SessionLocal() as db:
            # Rows written before this change: fallback coaching stored a borrowed level.
            row = db.get(SessionQuestion, legacy["id"])
            row.level, row.first_level = "shallow", "shallow"
            # First coaching fell back, a later revision was validated.
            row = db.get(SessionQuestion, mixed["id"])
            question = db.get(classroom.Question, row.question_id)
            question.feedback_json = json.dumps(fallback("model_timeout"))
            row.first_level = "mid"
            db.commit()
        ids = {kept["id"], legacy["id"], mixed["id"]}

        def levels():
            with SessionLocal() as db:
                return {r.id: (r.level, r.first_level) for r in db.query(SessionQuestion).filter(SessionQuestion.id.in_(ids))}

        before = levels()
        with SessionLocal() as db:
            dry = mark(db)
        self.assertGreaterEqual(dry["level_marked"], 1)
        self.assertEqual(levels(), before, "a dry run writes nothing")
        with SessionLocal() as db:
            mark(db, apply=True)
        after = levels()
        self.assertEqual(after[kept["id"]], ("deep", "deep"))
        self.assertEqual(after[legacy["id"]], ("unassessed", "unassessed"))
        self.assertEqual(after[mixed["id"]], ("mid", "unassessed"))
        with SessionLocal() as db:
            again = mark(db, apply=True)
        self.assertEqual((again["level_marked"], again["first_level_marked"]), (0, 0), "idempotent")


if __name__ == "__main__":
    unittest.main()
