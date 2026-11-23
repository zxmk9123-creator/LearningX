"""Faculty delete a course from 내 수업.

The course leaves every list and course API, its open class ends (students can
no longer join or write), and its rows stay for audit. Only the course's own
faculty can delete it, and a guest can make a new course afterwards.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-delete-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx.api import app
from learningx.classroom import ClassSession
from learningx.guest import GUEST_DOMAIN
from learningx.security import issue_token
from learningx.storage import Audit, Course, CourseDeletion, Material, Member, SessionLocal, User, uid


class CourseDeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def make_course(self, email=None, title="삭제할 수업"):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            faculty = User(email=email or f"del-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title=title, policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="a" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            return issue_token(faculty.id), course.id, material.id

    def setUp(self):
        self.faculty, self.course_id, self.material_id = self.make_course()

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def mine(self, token=None):
        return [c["id"] for c in self.req("GET", "/v1/courses/mine", token or self.faculty).json()["items"]]

    def test_a_deleted_course_leaves_the_list_and_course_apis(self):
        self.assertIn(self.course_id, self.mine())
        response = self.req("DELETE", f"/v1/courses/{self.course_id}", self.faculty)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"deleted": True, "closed_sessions": 0})
        self.assertNotIn(self.course_id, self.mine())
        for method, path in (("GET", f"/v1/courses/{self.course_id}/materials"),
                             ("GET", f"/v1/courses/{self.course_id}/sessions"),
                             ("DELETE", f"/v1/courses/{self.course_id}")):
            self.assertEqual(self.req(method, path, self.faculty).status_code, 404, path)
        with SessionLocal() as db:
            self.assertIsNotNone(db.get(Course, self.course_id), "rows are kept")
            self.assertIsNotNone(db.get(CourseDeletion, self.course_id))
            self.assertEqual(db.query(Audit).filter_by(course_id=self.course_id, action="course_deleted").count(), 1)

    def test_deleting_ends_the_open_class(self):
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                              json={"material_id": self.material_id}).json()
        student = self.req("POST", "/v1/join", json={"code": opened["join_code"]}).json()["access_token"]
        self.assertEqual(self.req("DELETE", f"/v1/courses/{self.course_id}", self.faculty).json()["closed_sessions"], 1)
        with SessionLocal() as db:
            self.assertEqual(db.get(ClassSession, opened["id"]).status, "CLOSED")
        self.assertEqual(self.req("POST", "/v1/join", json={"code": opened["join_code"]}).status_code, 404)
        self.assertEqual(self.req("GET", f"/v1/sessions/{opened['id']}/live", student).json()["status"], "CLOSED")

    def test_only_the_course_faculty_can_delete(self):
        other, _, _ = self.make_course()
        self.assertEqual(self.req("DELETE", f"/v1/courses/{self.course_id}", other).status_code, 403)
        self.assertIn(self.course_id, self.mine())

    def test_a_guest_can_make_a_new_course_after_deleting(self):
        guest, course_id, _ = self.make_course(email=f"g-{uid()}{GUEST_DOMAIN}")
        body = {"title": "두 번째 수업", "policy": "RESTRICTED_HINT_ONLY", "teacher_approved": False}
        self.assertEqual(self.req("POST", "/v1/courses", guest, json=body).status_code, 409)
        self.req("DELETE", f"/v1/courses/{course_id}", guest)
        self.assertEqual(self.req("POST", "/v1/courses", guest, json=body).status_code, 201)


if __name__ == "__main__":
    unittest.main()
