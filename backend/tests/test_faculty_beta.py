"""Faculty invite, upload queue, real parser, and signed review integration."""
import hashlib
import json
import os
import secrets
import unittest
from unittest.mock import patch

import fitz

from test_api import app
from learningx import engine_adapter, private_store
from learningx.northflank_worker import poll_once
from learningx.storage import Material, SessionLocal, User
from fastapi.testclient import TestClient
from test_store import FakeS3


class FacultyBetaTests(unittest.TestCase):
    def test_upload_and_signed_review(self):
        with fitz.open() as pdf:
            cover = pdf.new_page()
            cover.insert_text((72, 85), "Media Literacy", fontsize=22)
            lesson = pdf.new_page()
            lesson.insert_textbox(
                fitz.Rect(72, 72, 520, 400),
                "Agenda setting describes how repeated news coverage changes which public "
                "issues audiences regard as important. Researchers compare the prominence "
                "of topics in media with the prominence of those topics in public surveys. "
                "Several explanations can account for the resulting association, including "
                "shared responses to an external event. Longitudinal studies measure "
                "coverage before and after the change in public attention.",
                fontsize=14,
            )
            payload = pdf.tobytes()

        env = {
            "NIT_WORKER_BACKEND": "northflank_poll",
            "NIT_REVIEW_SIGNING_KEY": secrets.token_hex(32),
            "AWS_ACCESS_KEY_ID": "local-test-key",
            "AWS_SECRET_ACCESS_KEY": "local-test-secret",
            "AWS_ENDPOINT_URL_S3": "https://test.neon.invalid",
            "AWS_REGION": "ap-southeast-1",
            "NIT_STORAGE_BUCKET": "learningx-private-lessons",
        }
        with TestClient(app) as client, patch.dict(os.environ, env):
            with SessionLocal() as db:
                faculty = User(email="beta-faculty@test.local", role="faculty")
                db.add(faculty)
                db.flush()
                faculty_id = faculty.id
                db.commit()
            code = secrets.token_urlsafe(32)
            invites = json.dumps({hashlib.sha256(code.encode()).hexdigest(): faculty_id})
            storage = FakeS3()
            store = private_store.PrivateArtifactStore(client=storage)
            with patch.dict(os.environ, {"NIT_BETA_FACULTY_INVITES": invites}), patch.object(
                private_store, "PrivateArtifactStore", return_value=store
            ):
                endpoint = "/v1/beta/faculty/session"
                assert client.post(endpoint, json={"code": secrets.token_urlsafe(32)}).status_code == 401
                session = client.post(endpoint, json={"code": code})
                assert session.status_code == 200, session.text
                assert session.headers["Cache-Control"] == "no-store"
                headers = {"Authorization": "Bearer " + session.json()["access_token"]}
                course = client.post("/v1/courses", json={"title": "Media Literacy"}, headers=headers)
                assert course.status_code == 201, course.text
                course_id = course.json()["id"]
                uploads = f"/v1/courses/{course_id}/uploads"
                init = client.post(uploads + "/initiate", headers=headers, json={
                    "filename": "lesson.pdf", "size_bytes": len(payload),
                })
                assert init.status_code == 201, init.text
                upload = init.json()
                upload_id = upload["upload_id"]
                path = uploads + "/" + upload_id
                storage.put_object(
                    Bucket=store.bucket, Key=f"incoming/{course_id}/{upload_id}/source.pdf",
                    Body=payload, ContentType=upload["headers"]["Content-Type"],
                )
                queued = client.post(path + "/complete", headers=headers)
                assert queued.status_code == 202, queued.text
                assert queued.json()["status"] == "QUEUED"
                assert poll_once()["result"] == "READY"
                status = client.get(path, headers=headers).json()
                assert status["status"] == "READY"
                assert status["analysis_coverage"]["native_extraction"] == "COMPLETE"
                assert status["analysis_coverage"]["vision"]["status"] == "NOT_REQUIRED"
                assert status["analysis_coverage"]["semantic_analysis"] == "NOT_RUN"
                assert status["analysis_coverage"]["faculty_approval_required"] is True
                material_id = status["material_id"]
                url = f"/v1/materials/{material_id}"
                candidates = client.get(url + "/candidates", headers=headers).json()["eligible_candidates"]
                assert candidates
                selected = next(item for item in candidates if "Agenda setting" in item["preview"])
                approved = client.post(url + "/approve", headers=headers, json={"items": [selected]})
                assert approved.status_code == 200, approved.text
                assert approved.json()["approved_count"] == 1
                with SessionLocal() as db, store.workspace(db.get(Material, material_id)) as parsed:
                    assert engine_adapter.verified_count(parsed) == 1
