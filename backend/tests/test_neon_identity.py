"""Google sign-in through Neon Auth: JWT checks, school-domain approval, one-time role choice."""
import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-neon-identity-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from learningx import neon_identity as ni
from learningx.api import app
from learningx.security import decode_token
from learningx.storage import Course, Material, Member, SessionLocal, User, uid
from learningx import engine_adapter as nit

AUTH_URL = "https://ep-test.neonauth.example.neon.tech/learningx/auth"
ORIGIN = "https://ep-test.neonauth.example.neon.tech"


def keypair(kid):
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private, {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode()}


class Creds:
    def __init__(self, token):
        self.credentials = token


class NeonIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.private, jwk = keypair("k1")
        self.jwks = {"keys": [jwk]}
        self.fetches = 0

        def fetch(url):
            self.fetches += 1
            self.assertEqual(url, AUTH_URL + "/.well-known/jwks.json")
            return {k["kid"]: ni.Ed25519PublicKey.from_public_bytes(
                base64.urlsafe_b64decode(k["x"] + "==")) for k in self.jwks["keys"]}

        ni._jwks.update(keys={}, fetched=0.0, url=None)
        self.patches = [patch.dict(os.environ, {"NIT_NEON_AUTH_URL": AUTH_URL,
                                                "NIT_GOOGLE_ALLOWED_DOMAINS": "hanyang.ac.kr, school.edu"}),
                        patch.object(ni, "_fetch_jwks", fetch)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def token(self, sub=None, key=None, kid="k1", **claims):
        now = int(time.time())
        payload = {"sub": sub or ("neon-" + uid()), "email": f"user-{uid()[:8]}@hanyang.ac.kr", "emailVerified": True,
                   "iat": now, "exp": now + 900, "iss": ORIGIN, "aud": ORIGIN, "role": "authenticated",
                   "banned": False, **claims}
        return jwt.encode(payload, key or self.private, algorithm="EdDSA", headers={"kid": kid})

    def exchange(self, token, role=None):
        body = {"token": token, **({"role": role} if role else {})}
        return self.client.post("/v1/auth/neon/session", json=body)

    def test_first_sign_in_chooses_a_role_once_then_the_database_decides(self):
        sub, email = "neon-" + uid(), f"prof-{uid()[:6]}@hanyang.ac.kr"
        first = self.exchange(self.token(sub, email=email))
        self.assertEqual(first.status_code, 403)
        self.assertEqual(first.json()["detail"], {"code": "role_required", "email": email})
        chosen = self.exchange(self.token(sub, email=email), role="faculty")
        self.assertEqual((chosen.status_code, chosen.json()["role"]), (200, "faculty"), chosen.text)
        self.assertEqual(chosen.headers["cache-control"], "no-store")
        again = self.exchange(self.token(sub, email=email), role="student")
        self.assertEqual(again.json()["role"], "faculty", "the stored role wins; a later choice is ignored")
        token = again.json()["access_token"]
        self.assertEqual(self.client.get("/v1/courses/mine", headers={"Authorization": "Bearer " + token}).status_code, 200)
        with SessionLocal() as db:
            self.assertEqual(db.query(User).filter_by(email=email).one().role, "faculty")

    def test_students_get_a_join_ticket_that_opens_nothing_else(self):
        res = self.exchange(self.token(), role="student")
        self.assertEqual(res.json()["role"], "student")
        ticket = res.json()["join_ticket"]
        self.assertNotIn("access_token", res.json())
        mine = self.client.get("/v1/courses/mine", headers={"Authorization": "Bearer " + ticket})
        self.assertEqual(mine.status_code, 403, "a join ticket is not a general login")

    def test_only_verified_school_emails_are_admitted(self):
        for claims, code in (({"email": "someone@gmail.com"}, "domain_not_allowed"),
                             ({"email": "someone@hanyang.ac.kr.evil.com"}, "domain_not_allowed"),
                             ({"email": "someone@nothanyang.ac.kr"}, "domain_not_allowed"),
                             ({"emailVerified": False}, "email_unverified"),
                             ({"email": ""}, "email_unverified")):
            with self.subTest(claims=claims):
                res = self.exchange(self.token(**claims), role="faculty")
                self.assertEqual((res.status_code, res.json()["detail"]["code"]), (403, code))
        sub_domain = self.exchange(self.token(email=f"x-{uid()[:6]}@cs.hanyang.ac.kr"), role="student")
        self.assertEqual(sub_domain.status_code, 200, "department subdomains of an allowed domain are admitted")
        other = self.exchange(self.token(email=f"x-{uid()[:6]}@school.edu"), role="student")
        self.assertEqual(other.status_code, 200, "several domains can be allowed")

    def test_an_existing_account_with_the_same_email_keeps_its_role(self):
        email = f"pilot-{uid()[:6]}@hanyang.ac.kr"
        with SessionLocal() as db:
            db.add(User(email=email, role="faculty"))
            db.commit()
        res = self.exchange(self.token(email=email))
        self.assertEqual(res.json()["role"], "faculty", "no role question for a known account")

    def test_linking_is_by_neon_user_id_after_the_first_sign_in(self):
        sub, email = "neon-" + uid(), f"t-{uid()[:6]}@hanyang.ac.kr"
        self.exchange(self.token(sub, email=email), role="faculty")
        other = self.exchange(self.token(email=email))
        self.assertEqual((other.status_code, other.json()["detail"]["code"]), (409, "account_already_linked"),
                         "another Neon user with the same email cannot take the account")

    def test_tokens_that_fail_verification_are_rejected(self):
        other_key, _ = keypair("k1")
        now = int(time.time())
        cases = {
            "wrong signature": self.token(key=other_key),
            "expired": self.token(iat=now - 2000, exp=now - 1000),
            "wrong issuer": self.token(iss="https://evil.example.com"),
            "wrong audience": self.token(aud="https://evil.example.com"),
            "banned": self.token(banned=True),
            "unknown kid": self.token(kid="nope"),
            "not eddsa": jwt.encode({"sub": "x", "iss": ORIGIN, "aud": ORIGIN, "iat": now, "exp": now + 60},
                                    "a" * 40, algorithm="HS256", headers={"kid": "k1"}),
            "garbage": "x" * 40,
        }
        for name, token in cases.items():
            with self.subTest(name):
                self.assertEqual(self.exchange(token, role="faculty").status_code, 401)

    def test_rotated_keys_are_fetched_and_jwks_is_cached(self):
        self.exchange(self.token(), role="student")
        self.exchange(self.token(), role="student")
        self.assertEqual(self.fetches, 1, "keys are cached")
        new_private, new_jwk = keypair("k2")
        self.jwks["keys"].append(new_jwk)
        ni._jwks["fetched"] -= 60  # past the short refetch guard
        res = self.exchange(self.token(key=new_private, kid="k2"))
        self.assertEqual(res.json()["detail"]["code"], "role_required", "verified with the rotated key")
        self.assertEqual(self.fetches, 2)

    def test_jwks_outage_is_a_503_not_a_login(self):
        def down(url):
            raise RuntimeError("network")
        with patch.object(ni, "_fetch_jwks", down):
            self.assertEqual(self.exchange(self.token(), role="faculty").status_code, 503)

    def test_master_accounts_choose_a_role_every_time_and_store_nothing(self):
        email = f"master-{uid()[:6]}@hanyang.ac.kr"
        sub = "neon-" + uid()
        with patch.dict(os.environ, {"NIT_MASTER_EMAILS": email}):
            first = self.exchange(self.token(sub, email=email))
            self.assertEqual(first.json()["detail"], {"code": "role_required", "email": email, "master": True})
            fac = self.exchange(self.token(sub, email=email), role="faculty").json()
            self.assertEqual((fac["role"], fac["master"]), ("faculty", True))
            again = self.exchange(self.token(sub, email=email))
            self.assertEqual(again.json()["detail"]["code"], "role_required", "the role is asked again on every sign-in")
            stu = self.exchange(self.token(sub, email=email), role="student").json()
            self.assertEqual(stu["role"], "student", "the same master can test the student side next")
            fac2 = self.exchange(self.token(sub, email=email), role="faculty").json()
            self.assertEqual(decode_token(Creds(fac["access_token"]))["sub"], decode_token(Creds(fac2["access_token"]))["sub"],
                             "classes made as faculty are kept for the next faculty sign-in")
        with SessionLocal() as db:
            self.assertIsNone(db.get(ni.GoogleIdentity, sub), "no Google link is stored for a master")
            self.assertIsNone(db.query(User).filter_by(email=email).first(), "no account is stored under the plain email")
            roles = {u.email: u.role for u in db.query(User).filter(User.email.like(email.split("@")[0] + "+%"))}
            self.assertEqual(roles, {email.replace("@", "+faculty@"): "faculty", email.replace("@", "+student@"): "student"})

    def test_built_in_master_list_matches_by_hash(self):
        self.assertTrue(ni.is_master("zxmk9123@hanyang.ac.kr"))
        self.assertFalse(ni.is_master("someone@hanyang.ac.kr"))
        import inspect
        self.assertNotIn("zxmk9123", inspect.getsource(ni), "emails are not stored in plain text")

    def test_a_google_student_joins_classes_as_the_same_account(self):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        faculty = self.exchange(self.token(), role="faculty").json()["access_token"]
        with SessionLocal() as db:
            fid = decode_token(Creds(faculty))["sub"]
            course = Course(title="집단극화", policy="PERMITTED", teacher_approved=True, created_by=fid)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=fid, role="faculty"))
            material = Material(course_id=course.id, filename="l.pdf", sha256="c" * 64, out_dir=str(folder), ingested_by=fid)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.client.post(f"/v1/courses/{course_id}/sessions", json={"material_id": material_id},
                                      headers={"Authorization": "Bearer " + faculty}).json()
        self.client.put(f"/v1/sessions/{opened['id']}/chat", json={"open": True}, headers={"Authorization": "Bearer " + faculty})
        ticket = self.exchange(self.token(), role="student").json()["join_ticket"]
        first = self.client.post("/v1/join", json={"code": opened["join_code"]}, headers={"Authorization": "Bearer " + ticket})
        again = self.client.post("/v1/join", json={"code": opened["join_code"]}, headers={"Authorization": "Bearer " + ticket})
        self.assertEqual((first.status_code, again.status_code), (200, 200), first.text)
        a, b = (decode_token(Creds(r.json()["access_token"])) for r in (first, again))
        self.assertEqual(a["sub"], b["sub"], "rejoining keeps the same student account")
        self.assertEqual(a["session_id"], opened["id"])
        anon = self.client.post("/v1/join", json={"code": opened["join_code"]})
        self.assertNotEqual(decode_token(Creds(anon.json()["access_token"]))["sub"], a["sub"], "anonymous join still works")
        wrong = self.client.post("/v1/join", json={"code": opened["join_code"]}, headers={"Authorization": "Bearer " + faculty})
        self.assertEqual(wrong.status_code, 403, "only a student ticket joins as an account")


class FacultySeesGoogleStudentNames(NeonIdentityTests):
    """Submitted questions from Google students carry name and school email to the class's
    faculty board only; anonymous students, the projector and the CSV stay anonymous."""

    def test_board_shows_who_asked_only_for_google_students(self):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        faculty = self.exchange(self.token(), role="faculty").json()["access_token"]
        fh = {"Authorization": "Bearer " + faculty}
        with SessionLocal() as db:
            fid = decode_token(Creds(faculty))["sub"]
            course = Course(title="설득", policy="PERMITTED", teacher_approved=True, created_by=fid)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=fid, role="faculty"))
            material = Material(course_id=course.id, filename="l.pdf", sha256="d" * 64, out_dir=str(folder), ingested_by=fid)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.client.post(f"/v1/courses/{course_id}/sessions", json={"material_id": material_id}, headers=fh).json()
        self.client.put(f"/v1/sessions/{opened['id']}/chat", json={"open": True}, headers=fh)
        email, sub = f"kim-{uid()[:6]}@hanyang.ac.kr", "neon-" + uid()
        ticket = self.exchange(self.token(sub, email=email, name="  김  하늘 "), role="student").json()["join_ticket"]
        google = self.client.post("/v1/join", json={"code": opened["join_code"]}, headers={"Authorization": "Bearer " + ticket}).json()["access_token"]
        anon = self.client.post("/v1/join", json={"code": opened["join_code"]}).json()["access_token"]
        coaching = {"mode": "DETERMINISTIC_FALLBACK", "question_level": "analyze", "follow_up_question": "근거는?",
                    "evidence_refs": [], "feedback": "구체화해 보세요.", "reason": "guest_model_disabled"}
        ids = {}
        with patch.object(nit, "coach_question", return_value=coaching), patch.object(nit, "model_gateway", return_value=None):
            for who, tok, text in (("google", google, "광고 모델의 신뢰성은 왜 중요한가요?"), ("anon", anon, "공포 소구는 언제 효과적인가요?")):
                q = self.client.post(f"/v1/sessions/{opened['id']}/questions", json={"question": text},
                                     headers={"Authorization": "Bearer " + tok}).json()
                self.assertEqual(self.client.post(f"/v1/session-questions/{q['id']}/submit",
                                                  headers={"Authorization": "Bearer " + tok}).status_code, 200)
                ids[who] = q["id"]
        board = {i["id"]: i for i in self.client.get(f"/v1/sessions/{opened['id']}/board", headers=fh).json()["items"]}
        self.assertEqual(board[ids["google"]]["student"], {"name": "김 하늘", "email": email})
        self.assertNotIn("student", board[ids["anon"]], "anonymous joins stay anonymous")
        shown = self.client.post(f"/v1/session-questions/{ids['google']}/project", headers=fh).json()
        self.assertNotIn("student", shown["projected"], "never on the projector")
        projected = self.client.get(f"/v1/sessions/{opened['id']}/projected", headers=fh).json()
        self.assertNotIn("김 하늘", json.dumps(projected, ensure_ascii=False))
        csv_text = self.client.get(f"/v1/sessions/{opened['id']}/export", headers=fh).text
        self.assertNotIn("김 하늘", csv_text)
        self.assertNotIn(email, csv_text)
        # The name follows the Google account when it changes.
        self.exchange(self.token(sub, email=email, name="김하늘"))
        board = {i["id"]: i for i in self.client.get(f"/v1/sessions/{opened['id']}/board", headers=fh).json()["items"]}
        self.assertEqual(board[ids["google"]]["student"]["name"], "김하늘")


if __name__ == "__main__":
    unittest.main()
