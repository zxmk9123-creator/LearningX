"""An open tab renews its token so a class is not cut off when the token expires.

Faculty sign-in tokens last an hour; a class that ran past that point used to get
401 on the next poll or page change and the teacher page dropped back to sign-in,
ending the broadcast. A still-valid token can now be renewed with the same
identity, scope and lifetime, up to a fixed age since the original sign-in.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-renew-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

import jwt
from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx.api import app
from learningx.security import MAX_SIGN_IN_HOURS, issue_token, secret
from learningx.storage import Course, Material, Member, SessionLocal, User, uid


def claims(token):
    return jwt.decode(token, secret(), algorithms=["HS256"], audience="learningx", issuer="learningx-server")


def forged(user_id, *, iat, minutes, auth_time=None, **extra):
    body = {"sub": user_id, "aud": "learningx", "iss": "learningx-server", "iat": iat,
            "exp": iat + timedelta(minutes=minutes), **extra}
    if auth_time is not None:
        body["auth_time"] = auth_time
    return jwt.encode(body, secret(), algorithm="HS256")


class TokenRenewalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        with SessionLocal() as db:
            faculty = User(email=f"renew-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.commit()
            self.user_id = faculty.id

    def refresh(self, token):
        return self.client.post("/v1/auth/refresh", headers={"Authorization": "Bearer " + token})

    def test_a_valid_faculty_token_renews_with_the_same_lifetime(self):
        old = issue_token(self.user_id, minutes=60)
        response = self.refresh(old)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["expires_in"], 3600)
        new = claims(body["access_token"])
        self.assertEqual(new["sub"], self.user_id)
        self.assertEqual(new["auth_time"], claims(old)["auth_time"], "the original sign-in time is kept")
        self.assertNotIn("scope", new)
        self.assertEqual(self.client.get("/v1/courses/mine", headers={"Authorization": "Bearer " + body["access_token"]}).status_code, 200)

    def test_a_token_near_expiry_gets_a_later_expiry(self):
        now = datetime.now(timezone.utc)
        old = forged(self.user_id, iat=now - timedelta(minutes=55), minutes=60, auth_time=int((now - timedelta(minutes=55)).timestamp()))
        new = claims(self.refresh(old).json()["access_token"])
        self.assertGreater(new["exp"], int((now + timedelta(minutes=59)).timestamp()))

    def test_an_expired_token_cannot_renew(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(self.refresh(forged(self.user_id, iat=now - timedelta(minutes=61), minutes=60)).status_code, 401)

    def test_renewal_stops_at_the_sign_in_age_limit(self):
        now = datetime.now(timezone.utc)
        too_old = int((now - timedelta(hours=MAX_SIGN_IN_HOURS, minutes=1)).timestamp())
        self.assertEqual(self.refresh(forged(self.user_id, iat=now, minutes=60, auth_time=too_old)).status_code, 401)

    def test_tokens_issued_before_this_change_renew_from_their_issue_time(self):
        now = datetime.now(timezone.utc)
        response = self.refresh(forged(self.user_id, iat=now - timedelta(minutes=30), minutes=60))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(claims(response.json()["access_token"])["auth_time"], int((now - timedelta(minutes=30)).timestamp()))

    def test_an_inactive_user_cannot_renew(self):
        token = issue_token(self.user_id, minutes=60)
        with SessionLocal() as db:
            db.get(User, self.user_id).active = False
            db.commit()
        self.assertEqual(self.refresh(token).status_code, 401)

    def test_a_student_class_token_keeps_its_class_scope(self):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            course = Course(title="갱신", policy="PERMITTED", teacher_approved=True, created_by=self.user_id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=self.user_id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="9" * 64,
                                out_dir=str(folder), ingested_by=self.user_id)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        faculty = issue_token(self.user_id)
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.client.post(f"/v1/courses/{course_id}/sessions", json={"material_id": material_id},
                                      headers={"Authorization": "Bearer " + faculty}).json()
        student = self.client.post("/v1/join", json={"code": opened["join_code"]}).json()["access_token"]
        response = self.refresh(student)
        self.assertEqual(response.status_code, 200, response.text)
        new = claims(response.json()["access_token"])
        self.assertEqual((new["scope"], new["session_id"]), ("classroom", opened["id"]))
        self.assertEqual(response.json()["expires_in"], 240 * 60)
        # Paused classes still renew; only question writes are blocked.
        self.client.put(f"/v1/sessions/{opened['id']}/state", json={"status": "PAUSED"},
                        headers={"Authorization": "Bearer " + faculty})
        self.assertEqual(self.refresh(student).status_code, 200)


if __name__ == "__main__":
    unittest.main()
