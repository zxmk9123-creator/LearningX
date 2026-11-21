"""PostgreSQL-only checks for live slide publishing.

The PostgreSQL publish path (_publish_pipelined) does not run on SQLite, so
these tests run only when DATABASE_URL points at PostgreSQL. They check that
the pipelined path and the ORM path decide identically, including when the
session or permissions change between lookup and write, and that concurrent
publishes stay ordered.

    DATABASE_URL=postgresql://... python -m unittest tests.test_publish_postgres -v

Run them in their own process: tests/test_api.py pins SQLite on import, so a
full discover run binds SQLite and these tests skip.
"""
import json
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-publish-pg-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import classroom
from learningx import engine_adapter as nit
from learningx.api import app
from learningx.classroom import ClassSession
from learningx.live_slides import LiveSlide, LiveTransition
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, SessionLocal, User, engine, uid

# Decide by the engine actually bound: another test module imported first may
# have pinned DATABASE_URL to SQLite for the whole process.
POSTGRES = engine.dialect.name == "postgresql"

PATHS = ("pipelined", "orm")


@contextmanager
def publish_path(name):
    """Force share_slide down one write path; both are valid on PostgreSQL."""
    if name == "pipelined":
        yield
        return
    with patch.object(classroom.publish_engine.dialect, "name", "orm-path"):
        yield


@unittest.skipUnless(POSTGRES, "PostgreSQL DATABASE_URL required")
class PostgresPublishTests(unittest.TestCase):
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
            {"page": 1, "blocks": []}, {"page": 2, "blocks": [], "hidden_slide": True},
            {"page": 3, "blocks": []}, {"page": 4, "blocks": []}, {"page": 5, "blocks": []},
        ]}), encoding="utf-8")
        with SessionLocal() as db:
            faculty, co_teacher, outsider = (User(email=f"pg-{kind}-{uid()}@test.local", role="faculty")
                                             for kind in ("fac", "co", "out"))
            db.add_all([faculty, co_teacher, outsider])
            db.flush()
            course = Course(title="송출 검증", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add_all([Member(course_id=course.id, user_id=faculty.id, role="faculty"),
                        Member(course_id=course.id, user_id=co_teacher.id, role="faculty")])
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="c" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            self.course_id, self.material_id = course.id, material.id
            self.faculty_id, self.co_id, self.outsider_id = faculty.id, co_teacher.id, outsider.id
        self.faculty = issue_token(self.faculty_id)
        self.co_teacher = issue_token(self.co_id)
        self.outsider = issue_token(self.outsider_id)

    # ---------- helpers ----------

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def open_session(self):
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                              json={"material_id": self.material_id})
        self.assertEqual(opened.status_code, 201, opened.text)
        body = opened.json()
        student = self.req("POST", "/v1/join", json={"code": body["join_code"]}).json()["access_token"]
        return body["id"], body["initial_live"]["slide"]["version"], student

    def publish(self, path_name, session_id, token, **body):
        with publish_path(path_name):
            return self.req("PUT", f"/v1/sessions/{session_id}/live", token, json=body)

    def history(self, session_id):
        with SessionLocal() as db:
            rows = (db.query(LiveTransition).filter_by(session_id=session_id)
                    .order_by(LiveTransition.position).all())
            slide = db.get(LiveSlide, session_id)
            return ([(r.position, r.page, r.version, json.loads(r.content)) for r in rows],
                    (slide.page, slide.version, json.loads(slide.content)) if slide else None)

    @staticmethod
    def normalize(value, names):
        """Replace random version identifiers with stable labels so paths can be compared."""
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        for raw, label in names.items():
            text = text.replace(raw, label)
        return json.loads(text)

    def mutate(self, action, session_id):
        with SessionLocal() as db:
            if action == "close":
                db.get(ClassSession, session_id).status = "CLOSED"
            elif action == "revoke":
                db.query(Member).filter_by(course_id=self.course_id, user_id=self.faculty_id).delete()
            elif action == "deactivate":
                db.get(User, self.faculty_id).active = False
            db.commit()

    def restore(self):
        with SessionLocal() as db:
            db.get(User, self.faculty_id).active = True
            if not db.query(Member).filter_by(course_id=self.course_id, user_id=self.faculty_id).first():
                db.add(Member(course_id=self.course_id, user_id=self.faculty_id, role="faculty"))
            db.commit()

    # ---------- the two write paths agree ----------

    def scripted_trace(self, path_name):
        session_id, initial, student = self.open_session()
        batch = {key: str(uuid.uuid4()) for key in "ABCD"}
        names = {initial: "INITIAL", session_id: "SESSION", **{v: f"BATCH_{k}" for k, v in batch.items()}}
        steps = [
            ("first publish", self.faculty, {"page": 3, "batch_id": batch["A"], "expected_version": initial}),
            ("replay same batch", self.faculty, {"page": 3, "batch_id": batch["A"], "expected_version": initial}),
            ("reuse batch for other pages", self.faculty, {"page": 4, "batch_id": batch["A"]}),
            ("stale expected version", self.faculty, {"page": 4, "batch_id": batch["B"], "expected_version": initial}),
            ("multi-page batch", self.faculty, {"pages": [4, 1, 3], "batch_id": batch["C"],
                                                "expected_version": f"{batch['A']}:0"}),
            ("hidden page", self.faculty, {"page": 2}),
            ("missing page", self.faculty, {"page": 9}),
            ("co-teacher publish", self.co_teacher, {"page": 5, "batch_id": batch["D"]}),
            ("non-member faculty", self.outsider, {"page": 1}),
            ("student token", student, {"page": 1}),
        ]
        trace = []
        for label, token, body in steps:
            response = self.publish(path_name, session_id, token, **body)
            trace.append((label, response.status_code,
                          self.normalize(response.json(), names) if response.status_code == 200 else None))
        unknown = self.publish(path_name, "missing-session", self.faculty, page=1)
        trace.append(("unknown session", unknown.status_code, None))
        self.req("PUT", f"/v1/sessions/{session_id}/state", self.faculty, json={"status": "CLOSED"})
        closed = self.publish(path_name, session_id, self.faculty, page=1)
        trace.append(("closed session", closed.status_code, None))
        return trace, self.normalize(self.history(session_id), names)

    def test_paths_return_identical_results_and_history(self):
        results = {name: self.scripted_trace(name) for name in PATHS}
        self.assertEqual(results["pipelined"], results["orm"])
        trace, (transitions, slide) = results["pipelined"]
        statuses = {label: status for label, status, _ in trace}
        self.assertEqual(statuses, {
            "first publish": 200, "replay same batch": 200, "reuse batch for other pages": 409,
            "stale expected version": 409, "multi-page batch": 200, "hidden page": 422, "missing page": 422,
            "co-teacher publish": 200, "non-member faculty": 403, "student token": 403,
            "unknown session": 404, "closed session": 409,
        })
        self.assertEqual(trace[0][2], trace[1][2], "a replayed batch must return the original result")
        self.assertEqual([position for position, *_ in transitions], list(range(1, len(transitions) + 1)))
        self.assertEqual([page for _, page, *_ in transitions], [1, 3, 4, 1, 3, 5])
        self.assertEqual(slide[1], transitions[-1][2], "live slide must equal the newest transition")

    # ---------- changes between lookup and write ----------

    def test_write_rechecks_state_changed_after_lookup(self):
        """Closing, revoking or deactivating after the access lookup must still block the write."""
        expected = {"close": 409, "revoke": 403, "deactivate": 401}
        for path_name in PATHS:
            for action, status in expected.items():
                with self.subTest(path=path_name, action=action):
                    session_id, initial, _ = self.open_session()
                    real_prepare = classroom.prepare_shared_page

                    def change_then_prepare(*args, action=action, session_id=session_id):
                        self.mutate(action, session_id)
                        return real_prepare(*args)

                    with patch.object(classroom, "prepare_shared_page", side_effect=change_then_prepare):
                        response = self.publish(path_name, session_id, self.faculty, page=3,
                                                batch_id=str(uuid.uuid4()))
                    self.restore()
                    self.assertEqual(response.status_code, status, response.text)
                    transitions, slide = self.history(session_id)
                    self.assertEqual(transitions, [], "a rejected publish must not write history")
                    self.assertEqual(slide[1], initial)

    # ---------- concurrency ----------

    def concurrent(self, path_name, bodies):
        session_id, initial, _ = self.open_session()
        barrier = threading.Barrier(len(bodies))

        def send(body):
            barrier.wait()
            return self.req("PUT", f"/v1/sessions/{session_id}/live", self.faculty,
                            json={key: (initial if value == "INITIAL" else value) for key, value in body.items()})

        with publish_path(path_name), ThreadPoolExecutor(len(bodies)) as pool:
            responses = list(pool.map(send, bodies))
        return session_id, initial, responses

    def test_concurrent_publishes_with_same_expected_version_allow_one(self):
        for path_name in PATHS:
            with self.subTest(path=path_name):
                bodies = [{"page": page, "batch_id": str(uuid.uuid4()), "expected_version": "INITIAL"}
                          for page in (3, 4, 5, 3, 4, 5)]
                session_id, initial, responses = self.concurrent(path_name, bodies)
                statuses = sorted(r.status_code for r in responses)
                self.assertEqual(statuses, [200] + [409] * 5, [r.text for r in responses])
                transitions, slide = self.history(session_id)
                self.assertEqual([(p, v) for p, _, v, _ in transitions][0], (1, initial))
                self.assertEqual(len(transitions), 2)
                winner = next(r.json() for r in responses if r.status_code == 200)
                self.assertEqual(slide[1], winner["version"])

    def test_concurrent_publishes_stay_contiguous_and_ordered(self):
        for path_name in PATHS:
            with self.subTest(path=path_name):
                bodies = [{"page": (3, 4, 5)[i % 3], "batch_id": str(uuid.uuid4())} for i in range(8)]
                session_id, _, responses = self.concurrent(path_name, bodies)
                self.assertTrue(all(r.status_code == 200 for r in responses), [r.text for r in responses])
                transitions, slide = self.history(session_id)
                self.assertEqual([p for p, *_ in transitions], list(range(1, 10)))
                sequences = sorted(r.json()["sequence"] for r in responses)
                self.assertEqual(sequences, list(range(2, 10)))
                for position, _, version, content in transitions[1:]:
                    self.assertEqual(content["sequence"], position)
                self.assertEqual(slide[1], transitions[-1][2])

    def test_concurrent_duplicate_batch_writes_once(self):
        for path_name in PATHS:
            with self.subTest(path=path_name):
                batch = str(uuid.uuid4())
                session_id, _, responses = self.concurrent(
                    path_name, [{"pages": [3, 4], "batch_id": batch}] * 6)
                self.assertTrue(all(r.status_code == 200 for r in responses), [r.text for r in responses])
                self.assertEqual(len({json.dumps(r.json(), sort_keys=True) for r in responses}), 1)
                transitions, _ = self.history(session_id)
                self.assertEqual([page for _, page, *_ in transitions], [1, 3, 4])


if __name__ == "__main__":
    unittest.main()
