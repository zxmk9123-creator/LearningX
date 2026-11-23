"""Students receive the page being shared now, and nothing else.

Covers every student-facing live response (join, poll, combined inline page,
partial fast path, separate PDF download) and the window between the access
lookup and PDF generation, where the presenter may move on or access may be
revoked.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-student-scope-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import classroom
from learningx import engine_adapter as nit
from learningx.api import app
from learningx.classroom import ClassSession, STUDENT_SLIDE_FIELDS
from learningx.live_slides import LiveSlide
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, SessionLocal, User, uid

PDF = b"%PDF-1.7 current page"


def frame_metadata(response):
    end = 4 + int.from_bytes(response.content[:4], "big")
    return json.loads(response.content[4:end]), response.content[end:]


class StudentLiveScopeTests(unittest.TestCase):
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
        (folder / "document.json").write_text(json.dumps({"pages": [
            {"page": n, "blocks": []} for n in range(1, 5)
        ]}), encoding="utf-8")
        with SessionLocal() as db:
            faculty = User(email=f"scope-fac-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="교안 범위", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="d" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        self.faculty = issue_token(faculty.id)
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{course_id}/sessions", self.faculty,
                              json={"material_id": material_id}).json()
        self.session_id = opened["id"]
        self.req("PUT", f"/v1/sessions/{self.session_id}/chat", self.faculty, json={"open": True})
        self.path = f"/v1/sessions/{self.session_id}/live"
        # Page 2 is shared with presenter-only fields and legacy content alongside.
        self.assertEqual(self.req("PUT", self.path, self.faculty, json={"page": 2}).status_code, 200)
        with SessionLocal() as db:
            slide = db.get(LiveSlide, self.session_id)
            content = json.loads(slide.content)
            content.update(text="현재 쪽 대체 텍스트", notes="교수자 메모", outline=[1, 2, 3, 4])
            slide.content = json.dumps(content, ensure_ascii=False)
            db.commit()
        self.joined = self.req("POST", "/v1/join", json={"code": opened["join_code"]}).json()
        self.student = self.joined["access_token"]
        with SessionLocal() as db:
            self.student_id = db.query(classroom.SessionParticipant).filter_by(
                session_id=self.session_id).one().user_id

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def current(self):
        return self.req("GET", self.path, self.faculty).json()["slide"]

    def assert_student_slide(self, slide):
        self.assertTrue(set(slide) <= STUDENT_SLIDE_FIELDS, sorted(set(slide) - STUDENT_SLIDE_FIELDS))
        self.assertEqual(slide["page"], 2)
        self.assertEqual(slide["text"], "현재 쪽 대체 텍스트", "current-page fallback content stays")

    # ---------- every student response carries current-page fields only ----------

    def test_student_responses_carry_only_current_page_fields(self):
        faculty_slide = self.current()
        for key in ("before", "after", "notes", "outline", "visible_pages"):
            self.assertIn(key, faculty_slide, "presenter keeps navigation fields")
        self.assert_student_slide(self.joined["initial_live"]["slide"])
        self.assert_student_slide(self.req("GET", self.path, self.student).json()["slide"])
        with patch.object(classroom, "cached_live_page", return_value=PDF):
            packed = self.req("GET", self.path, self.student, params={"inline_page": True})
            metadata, pdf = frame_metadata(packed)
            self.assert_student_slide(metadata["slide"])
            self.assertEqual(pdf, PDF)
            stamp = metadata["revision"]
            self.req("PUT", self.path, self.faculty, json={"page": 2})
            with SessionLocal() as db:
                slide = db.get(LiveSlide, self.session_id)
                slide.content = json.dumps({**json.loads(slide.content), "notes": "교수자 메모"})
                db.commit()
            fast = self.req("GET", self.path, self.student, params={
                "inline_page": True, "partial_page": True, "version": metadata["slide"]["version"], "after": stamp})
        fast_metadata, _ = frame_metadata(fast)
        self.assertTrue(fast_metadata["partial"])
        self.assertTrue(set(fast_metadata["slide"]) <= STUDENT_SLIDE_FIELDS)

    def test_students_never_receive_past_transitions(self):
        first = self.current()["version"]
        for page in (3, 4, 1):
            self.req("PUT", self.path, self.faculty, json={"page": page})
        student = self.req("GET", self.path, self.student, params={"cursor": first}).json()
        self.assertEqual(student["transitions"], [])
        faculty = self.req("GET", self.path, self.faculty, params={"cursor": first}).json()
        self.assertEqual([item["page"] for item in faculty["transitions"]], [3, 4, 1])

    # ---------- separate PDF download ----------

    def test_student_pdf_is_limited_to_the_current_version_and_page(self):
        version = self.current()["version"]
        page = f"{self.path}/page/{version}"
        with patch.object(classroom, "page_pdf", return_value=PDF), \
             patch.object(classroom, "cached_live_page", return_value=PDF):
            self.assertEqual(self.req("GET", page, self.student).content, PDF)
            self.assertEqual(self.req("GET", page, self.student, params={"page": 2}).status_code, 200)
            for other in (1, 3, 4):
                self.assertEqual(self.req("GET", page, self.student, params={"page": other}).status_code, 403)
            self.assertEqual(self.req("GET", page, self.faculty, params={"page": 3}).status_code, 200)
            self.req("PUT", self.path, self.faculty, json={"page": 3})
            self.assertEqual(self.req("GET", page, self.student).status_code, 409, "past version")
            self.assertEqual(self.req("GET", page, self.faculty).status_code, 200, "presenter catch-up")
            latest = f"{self.path}/page/{self.current()['version']}"
            self.assertEqual(self.req("GET", latest, self.student).status_code, 200)
            self.req("PUT", f"/v1/sessions/{self.session_id}/state", self.faculty, json={"status": "CLOSED"})
            self.assertEqual(self.req("GET", latest, self.student).status_code, 409, "closed class")

    def test_student_pdf_blocked_after_access_is_revoked(self):
        latest = f"{self.path}/page/{self.current()['version']}"
        with patch.object(classroom, "page_pdf", return_value=PDF), \
             patch.object(classroom, "cached_live_page", return_value=PDF):
            with SessionLocal() as db:
                db.get(User, self.student_id).active = False
                db.commit()
            self.assertEqual(self.req("GET", latest, self.student).status_code, 401)
            self.assertEqual(self.req("GET", self.path, self.student).status_code, 401)

    # ---------- changes while an uncached page is being prepared ----------

    def race(self, change):
        """Fetch an uncached page while `change` happens during PDF generation."""
        version = self.current()["version"]

        def generate(material, page):
            change()
            return PDF

        with patch.object(classroom, "cached_live_page", return_value=None), \
             patch.object(classroom, "page_pdf", side_effect=generate):
            return self.req("GET", f"{self.path}/page/{version}", self.student)

    def test_uncached_page_rechecks_after_presenter_moves_on(self):
        def move():
            with SessionLocal() as db:
                db.get(LiveSlide, self.session_id).version = uid()
                db.commit()
        self.assertEqual(self.race(move).status_code, 409)

    def test_uncached_page_rechecks_after_class_closes(self):
        def close():
            with SessionLocal() as db:
                db.get(ClassSession, self.session_id).status = "CLOSED"
                db.commit()
        self.assertEqual(self.race(close).status_code, 409)

    def test_uncached_page_rechecks_after_access_is_revoked(self):
        def revoke():
            with SessionLocal() as db:
                db.get(User, self.student_id).active = False
                db.commit()
        self.assertEqual(self.race(revoke).status_code, 401)

    def test_uncached_page_without_changes_is_served(self):
        response = self.race(lambda: None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertIn("recheck;dur=", response.headers["server-timing"])

    def test_cache_hit_returns_the_bytes_it_found(self):
        """A hit must not be followed by a second read that could miss after an eviction."""
        version = self.current()["version"]

        def evicted_then_loaded(material, page):
            with SessionLocal() as db:
                db.get(LiveSlide, self.session_id).version = uid()
                db.commit()
            return b"%PDF-1.7 read again from storage"

        with patch.object(classroom, "cached_live_page", return_value=PDF), \
             patch.object(classroom, "page_pdf", side_effect=evicted_then_loaded) as load:
            response = self.req("GET", f"{self.path}/page/{version}", self.student)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        load.assert_not_called()

    def test_cached_page_skips_the_recheck(self):
        version = self.current()["version"]
        with patch.object(classroom, "cached_live_page", return_value=PDF), \
             patch.object(classroom, "page_pdf", return_value=PDF), \
             patch.object(classroom, "_live_reader", wraps=classroom._live_reader) as reader:
            response = self.req("GET", f"{self.path}/page/{version}", self.student)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(reader.call_count, 1, "no extra database round trip on the cached path")
        self.assertNotIn("recheck", response.headers.get("server-timing", ""))


if __name__ == "__main__":
    unittest.main()
