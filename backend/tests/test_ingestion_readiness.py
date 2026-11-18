"""Regression coverage for answer isolation, lease exhaustion and partial coverage."""
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from test_api import app
from learningx.ingest_queue import _claim, MAX_ATTEMPTS, process_next_job
from learningx.storage import SessionLocal, User, Course, IngestJob, init_db
from test_engine_regressions import ROOT
from content_roles import classify_page


class AnswerClassificationTests(unittest.TestCase):
    def test_answer_labels_are_excluded(self):
        for label in ("정답:", "해설:", "모범 답안：", "예시 답안:", "Answer key:", "Solution:"):
            with self.subTest(label=label):
                page = {"page": 2, "blocks": [{"type": "text", "text":
                    label + " 의제설정 이론은 미디어 의제가 수용자의 중요도 인식에 영향을 준다고 설명한다."}],
                    "visual_stats": {}}
                self.assertEqual(classify_page(page), "provided_answer")

    def test_ordinary_discussion_of_answers_remains_eligible_for_review(self):
        page = {"page": 2, "blocks": [{"type": "text", "text":
            "학생은 정답을 암기하기보다 근거와 전제를 비교하며 비판적 사고를 기를 수 있다."}],
            "visual_stats": {}}
        self.assertEqual(classify_page(page), "learning_material")


class LeaseRecoveryTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with SessionLocal() as db:
            user = User(email="lease-" + uuid.uuid4().hex + "@test.local", role="faculty")
            db.add(user)
            db.flush()
            course = Course(title="Lease regression", created_by=user.id)
            db.add(course)
            db.flush()
            self.course = course.id
            self.user = user.id
            db.commit()

    def job(self, attempts, expired=True):
        with SessionLocal() as db:
            row = IngestJob(course_id=self.course, uploaded_by=self.user,
                filename="lease.pdf", media_type="application/pdf", expected_size=1,
                source_key="test/" + uuid.uuid4().hex, status="RUNNING", attempts=attempts,
                lease_until=datetime.now(timezone.utc) + timedelta(seconds=-60 if expired else 3600))
            db.add(row)
            db.commit()
            return row.id

    def test_unaddressed_poll_closes_exhausted_job(self):
        job_id = self.job(MAX_ATTEMPTS)
        process_next_job()
        with SessionLocal() as db:
            row = db.get(IngestJob, job_id)
            self.assertEqual(row.status, "FAILED")
            self.assertEqual(row.error_code, "WORKER_TIMEOUT")
            self.assertIsNone(row.lease_until)

    def test_unexpired_last_attempt_keeps_running(self):
        job_id = self.job(MAX_ATTEMPTS, expired=False)
        with SessionLocal() as db:
            self.assertIsNone(_claim(db, job_id))
            self.assertEqual(db.get(IngestJob, job_id).status, "RUNNING")

    def test_expired_retryable_attempt_is_reclaimed(self):
        job_id = self.job(MAX_ATTEMPTS - 1)
        with SessionLocal() as db:
            self.assertEqual(_claim(db, job_id), job_id)
            self.assertEqual(db.get(IngestJob, job_id).attempts, MAX_ATTEMPTS)
