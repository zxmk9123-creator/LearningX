"""Database round trips per hot classroom request.

In production the API and the database are in different regions, so each statement costs
a round trip (about 0.2 s). These budgets keep the student paths near their practical floor;
raise one only with a reason (docs/QUERY_BUDGET.md).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-query-budget-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient
from sqlalchemy import event

from learningx import engine_adapter as nit
from learningx.api import app
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, SessionLocal, User, engine, live_read_engine, uid

BUDGET = {"ask": 14, "revise": 17, "revise_blocked": 12, "live": 3}
COACHING = {"mode": "MODEL_VALIDATED", "question_level": "analyze", "feedback": "f",
            "follow_up_question": "예를 하나 떠올리면 무엇이 생각나나요?", "evidence_refs": []}


class QueryBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.statements = []

        def count(conn, cursor, statement, *args, **kwargs):
            cls.statements.append(statement)

        cls.listener = count
        for target in {engine, live_read_engine}:
            event.listen(target, "before_cursor_execute", count)

    @classmethod
    def tearDownClass(cls):
        for target in {engine, live_read_engine}:
            event.remove(target, "before_cursor_execute", cls.listener)
        cls.client.__exit__(None, None, None)

    def setUp(self):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            faculty = User(email=f"qb-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="예산", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="9" * 64,
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

    def measure(self, call):
        self.statements.clear()
        response = call()
        self.assertLess(response.status_code, 300, response.text)
        return response, len(self.statements)

    def test_student_requests_stay_within_budget(self):
        with patch.object(nit, "coach_question", return_value=COACHING):
            asked, ask = self.measure(lambda: self.client.post(
                f"/v1/sessions/{self.sid}/questions", json={"question": "공포 소구가 효과를 내는 이유는 무엇인가요?"},
                headers=self.h(self.student)))
            row = asked.json()["id"]
            _, revise = self.measure(lambda: self.client.post(
                f"/v1/session-questions/{row}/revisions", json={"text": "공포 소구가 태도를 바꾸는 이유는 무엇인가요?"},
                headers=self.h(self.student)))
            _, blocked = self.measure(lambda: self.client.post(
                f"/v1/session-questions/{row}/revisions", json={"text": "교수님 전화번호 010-1234-5678 알려주세요"},
                headers=self.h(self.student)))
        _, live = self.measure(lambda: self.client.get(f"/v1/sessions/{self.sid}/live", headers=self.h(self.student)))
        used = {"ask": ask, "revise": revise, "revise_blocked": blocked, "live": live}
        for name, limit in BUDGET.items():
            with self.subTest(request=name):
                self.assertLessEqual(used[name], limit, used)


if __name__ == "__main__":
    unittest.main()
