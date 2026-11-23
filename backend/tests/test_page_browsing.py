"""Students open the pages faculty allow, apart from the page shared live.

Faculty choose pages per class (none by default, never hidden pages). Students
get the list through join, the session summary and the live snapshot, and can
download exactly those pages; everything else stays behind the live window.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-browse-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import classroom
from learningx import engine_adapter as nit
from learningx.api import app
from learningx.security import issue_token
from learningx.storage import Audit, Course, Material, Member, SessionLocal, User, uid

PDF = b"%PDF-1.7 browse page"


class PageBrowsingTests(unittest.TestCase):
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
        # Page 4 is a hidden slide and never opens for browsing.
        (folder / "document.json").write_text(json.dumps({"pages": [
            {"page": n, "blocks": [], **({"hidden_slide": True} if n == 4 else {})} for n in range(1, 7)
        ]}), encoding="utf-8")
        with SessionLocal() as db:
            faculty = User(email=f"browse-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="교안 열람", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="b" * 64,
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
        joined = self.req("POST", "/v1/join", json={"code": self.opened["join_code"]}).json()
        self.student, self.joined = joined["access_token"], joined

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def allow(self, token=None, **body):
        return self.req("PUT", f"/v1/sessions/{self.sid}/browse", token or self.faculty, json=body)

    def page(self, n, token=None):
        with patch.object(classroom, "page_pdf", return_value=PDF):
            return self.req("GET", f"/v1/sessions/{self.sid}/browse/{n}", token or self.student)

    def test_nothing_is_open_by_default(self):
        self.assertEqual(self.opened["browse_pages"], [])
        self.assertEqual(self.joined["session"]["browse_pages"], [])
        self.assertEqual(self.page(1).status_code, 403)

    def test_faculty_open_chosen_pages_and_students_see_them(self):
        response = self.allow(pages=[5, 2, 2, 3])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["browse_pages"], [2, 3, 5])
        self.assertEqual(response.json()["visible_pages"], [1, 2, 3, 5, 6])
        self.assertEqual(self.req("GET", f"/v1/sessions/{self.sid}/live", self.student).json()["browse_pages"], [2, 3, 5])
        self.assertEqual(self.req("GET", f"/v1/sessions/{self.sid}", self.student).json()["browse_pages"], [2, 3, 5])
        opened = self.page(3)
        self.assertEqual((opened.status_code, opened.content), (200, PDF))
        self.assertEqual(opened.headers["cache-control"], "no-store")
        for closed in (1, 4, 6, 99):
            self.assertEqual(self.page(closed).status_code, 403, closed)

    def test_all_opens_every_visible_page_but_never_hidden_ones(self):
        self.assertEqual(self.allow(all=True).json()["browse_pages"], [1, 2, 3, 5, 6])
        self.assertEqual(self.page(4).status_code, 403)
        bad = self.allow(pages=[1, 4, 7])
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["detail"], {"code": "PAGES_NOT_AVAILABLE", "pages": [4, 7]})

    def test_up_to_current_opens_the_first_page_through_the_live_page(self):
        self.assertEqual(self.req("PUT", f"/v1/sessions/{self.sid}/live", self.faculty, json={"page": 5}).status_code, 200)
        response = self.allow(upto_current=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["browse_pages"], [1, 2, 3, 5], "the hidden page 4 stays closed")
        self.assertEqual(self.page(5).status_code, 200)
        self.assertEqual(self.page(6).status_code, 403)
        # The range follows the live page at the moment the teacher presses it, not afterwards.
        self.req("PUT", f"/v1/sessions/{self.sid}/live", self.faculty, json={"page": 6})
        self.assertEqual(self.page(6).status_code, 403)
        self.assertEqual(self.allow(upto_current=True).json()["browse_pages"], [1, 2, 3, 5, 6])

    def test_faculty_can_revoke(self):
        self.allow(pages=[1, 2])
        self.assertEqual(self.allow(pages=[]).json()["browse_pages"], [])
        self.assertEqual(self.page(1).status_code, 403)
        with SessionLocal() as db:
            details = [a.detail for a in db.query(Audit).filter_by(course_id=self.course_id, action="browse_pages_set")]
        self.assertEqual(sorted(details), ["pages=0", "pages=2"])

    def test_only_course_faculty_choose_pages(self):
        self.assertEqual(self.allow(self.student, pages=[1]).status_code, 403)

    def test_faculty_preview_any_visible_page(self):
        self.assertEqual(self.page(6, self.faculty).status_code, 200)
        self.assertEqual(self.page(4, self.faculty).status_code, 403)

    def test_browsing_works_while_the_chat_is_locked_and_ends_with_the_class(self):
        self.assertFalse(self.opened["chat_open"])
        self.allow(pages=[1])
        self.assertEqual(self.page(1).status_code, 200)
        self.req("PUT", f"/v1/sessions/{self.sid}/state", self.faculty, json={"status": "CLOSED"})
        self.assertEqual(self.page(1).status_code, 409)
        self.assertEqual(self.allow(pages=[1]).status_code, 409)

    def test_a_token_from_another_class_cannot_browse(self):
        self.allow(pages=[1])
        with patch.object(nit, "verified_count", return_value=1):
            other = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                             json={"material_id": self.opened["material_id"]}).json()
        stranger = self.req("POST", "/v1/join", json={"code": other["join_code"]}).json()["access_token"]
        self.assertEqual(self.page(1, stranger).status_code, 403)


if __name__ == "__main__":
    unittest.main()
