"""Faculty hold the students' question chat.

A class opens with the chat locked: students can join and follow the slides, but
cannot ask, revise, re-check or send questions until the teacher unlocks it at
question time. The teacher can lock it again; the state reaches students through
the session summary and the live snapshot.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-chat-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx.api import app
from learningx.storage import Audit, Course, Material, Member, SessionLocal, User, uid

COACHING = {"mode": "MODEL_VALIDATED", "question_level": "analyze", "feedback": "f",
            "follow_up_question": "두 개념은 어떤 기준으로 구별할 수 있을까요?",
            "evidence_refs": [{"id": "p1:b1", "page": 1}]}


class ChatAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        from learningx.security import issue_token
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            faculty = User(email=f"chat-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="채팅 권한", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="e" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            self.course_id = course.id
            material_id = material.id
        self.faculty = issue_token(faculty.id)
        with patch.object(nit, "verified_count", return_value=1):
            self.opened = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                                   json={"material_id": material_id}).json()
        self.sid = self.opened["id"]
        self.student = self.req("POST", "/v1/join", json={"code": self.opened["join_code"]}).json()["access_token"]

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def ask(self, text="토론 전후 두 집단의 평균 차이는 무엇 때문인가요?"):
        with patch.object(nit, "coach_question", return_value=COACHING):
            return self.req("POST", f"/v1/sessions/{self.sid}/questions", self.student, json={"question": text})

    def chat(self, open_, token=None):
        return self.req("PUT", f"/v1/sessions/{self.sid}/chat", token or self.faculty, json={"open": open_})

    def test_a_class_opens_with_the_chat_locked(self):
        self.assertFalse(self.opened["chat_open"])
        asked = self.ask()
        self.assertEqual((asked.status_code, asked.json()["detail"]), (423, "CHAT_LOCKED"))
        self.assertFalse(self.req("GET", f"/v1/sessions/{self.sid}", self.student).json()["chat_open"])
        self.assertFalse(self.req("GET", f"/v1/sessions/{self.sid}/live", self.student).json()["chat_open"])

    def test_students_still_join_and_follow_the_slides_while_locked(self):
        again = self.req("POST", "/v1/join", json={"code": self.opened["join_code"]})
        self.assertEqual(again.status_code, 200, again.text)
        live = self.req("GET", f"/v1/sessions/{self.sid}/live", self.student)
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["status"], "OPEN")

    def test_the_teacher_unlocks_and_locks_the_chat(self):
        opened = self.chat(True)
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertTrue(opened.json()["chat_open"])
        self.assertTrue(self.req("GET", f"/v1/sessions/{self.sid}/live", self.student).json()["chat_open"])
        asked = self.ask()
        self.assertEqual(asked.status_code, 201, asked.text)
        row_id = asked.json()["id"]
        self.assertFalse(self.chat(False).json()["chat_open"])
        revised = self.req("POST", f"/v1/session-questions/{row_id}/revisions", self.student,
                           json={"text": "토론 전후 두 집단의 평균 차이는 어떤 조건 때문인가요?"})
        self.assertEqual((revised.status_code, revised.json()["detail"]), (423, "CHAT_LOCKED"))
        submitted = self.req("POST", f"/v1/session-questions/{row_id}/submit", self.student)
        self.assertEqual(submitted.status_code, 423)
        batch = self.req("POST", f"/v1/sessions/{self.sid}/questions/submit-batch", self.student,
                         json={"request_id": "batch-" + uid()[:12], "items": [{"question_id": row_id, "revision_id": asked.json()["revision_id"] or "r"}]})
        self.assertEqual(batch.status_code, 423, batch.text)
        with SessionLocal() as db:
            actions = [a.action for a in db.query(Audit).filter_by(course_id=self.course_id)
                       .order_by(Audit.created_at).all()]
        self.assertEqual([a for a in actions if a.startswith("chat_")], ["chat_opened", "chat_locked"])

    def test_only_course_faculty_hold_the_switch(self):
        self.assertEqual(self.chat(True, self.student).status_code, 403)
        self.assertEqual(self.ask().status_code, 423)

    def test_a_closed_class_cannot_be_unlocked(self):
        self.chat(True)
        self.req("PUT", f"/v1/sessions/{self.sid}/state", self.faculty, json={"status": "CLOSED"})
        self.assertEqual(self.chat(True).status_code, 409)
        listed = self.req("GET", f"/v1/courses/{self.course_id}/sessions", self.faculty).json()["items"]
        self.assertFalse(listed[0]["chat_open"])

    def test_a_new_class_starts_locked_again(self):
        self.chat(True)
        with patch.object(nit, "verified_count", return_value=1):
            again = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                             json={"material_id": self.opened["material_id"]}).json()
        self.assertFalse(again["chat_open"])


if __name__ == "__main__":
    unittest.main()
