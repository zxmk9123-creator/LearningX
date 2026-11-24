"""No external S3 calls: in-memory S3 semantics + verified workspace tests."""
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from learningx import private_store
from learningx.private_store import PrivateArtifactStore, read_material_artifact


class FakeBody(io.BytesIO):
    def iter_chunks(self, chunk_size=1024*1024):
        while True:
            chunk=self.read(chunk_size)
            if not chunk:break
            yield chunk


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, **kwargs):
        self.objects[(Bucket, Key)] = Body.read() if hasattr(Body, "read") else bytes(Body)

    def get_object(self, Bucket, Key):
        self.gets = getattr(self, "gets", [])
        self.gets.append(Key)
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error":{"Code":"404","Message":"Not found"}},"HeadObject")
        return {"ContentLength":len(self.objects[(Bucket, Key)])}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        assert ClientMethod=="put_object"
        assert 0<ExpiresIn<=3600
        return "https://fake.neon.tech/upload?test=1"

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


class PrivateStoreTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "test-branch-key",
            "AWS_SECRET_ACCESS_KEY": "test-branch-secret",
            "AWS_ENDPOINT_URL_S3": "https://test.neon.invalid",
            "AWS_REGION": "ap-southeast-1",
            "NIT_STORAGE_BUCKET": "learningx-private-lessons",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        self.cache = patch.object(private_store, "CACHE_DIR", Path(cache.name))
        self.cache.start()
        self.addCleanup(self.cache.stop)
        self.fake = FakeS3()
        self.store = PrivateArtifactStore(client=self.fake)
        self.course_id = "a" * 32
        self.material_id = "b" * 32
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "source.pdf").write_bytes(b"%PDF-1.7 private teacher content")
        (self.root / "parsed").mkdir()
        (self.root / "parsed" / "document.json").write_text(
            json.dumps({"source": {"sha256": "f" * 64}, "pages": []}), encoding="utf-8"
        )

    def _publish(self):
        ref = self.store.publish_new(self.root, self.course_id, self.material_id)
        return SimpleNamespace(course_id=self.course_id, id=self.material_id, out_dir=ref)

    def test_publish_restore_and_signed_revision(self):
        record = self._publish()
        self.assertEqual(record.out_dir, f"s3://{self.store.bucket}/materials/{self.course_id}/{self.material_id}")
        with self.store.workspace(record) as parsed:
            self.assertTrue((parsed / "document.json").is_file())
            signed = parsed / "approved_evidence.json"
            signed.write_text('{"signature":"first"}', encoding="utf-8")
            self.store.publish_signed(record, parsed)
        with self.store.workspace(record) as parsed:
            self.assertEqual(json.loads((parsed / "approved_evidence.json").read_text())["signature"], "first")
            (parsed / "approved_evidence.json").write_text('{"signature":"second"}', encoding="utf-8")
            self.store.publish_signed(record, parsed)
        with self.store.workspace(record) as parsed:
            self.assertEqual(json.loads((parsed / "approved_evidence.json").read_text())["signature"], "second")
        self.assertTrue(all("private-lessons" in bucket for bucket, key in self.fake.objects))
        self.assertEqual(len([x for x in self.fake.objects if x[1].endswith("manifest.json")]), 1)

    def test_repeat_workspaces_reuse_verified_artifacts(self):
        """Only the small manifest is fetched again; lesson files come from the local cache."""
        record = self._publish()
        with self.store.workspace(record):
            pass
        first = list(self.fake.gets)
        self.assertTrue(any(k.endswith("source.pdf") for k in first))
        self.fake.gets.clear()
        for _ in range(3):
            with self.store.workspace(record) as parsed:
                self.assertTrue((parsed / "document.json").is_file())
                self.assertEqual((parsed.parent / "source.pdf").read_bytes(), b"%PDF-1.7 private teacher content")
        self.assertTrue(self.fake.gets and all(k.endswith("manifest.json") for k in self.fake.gets), self.fake.gets)
        self.fake.gets.clear()
        with patch.object(private_store, "require_store", return_value=self.store):
            self.assertEqual(read_material_artifact(record, "source.pdf", 1024), b"%PDF-1.7 private teacher content")
        self.assertTrue(all(k.endswith("manifest.json") for k in self.fake.gets))

    def test_a_changed_signature_is_fetched_once_then_cached(self):
        record = self._publish()
        with self.store.workspace(record) as parsed:
            (parsed / "approved_evidence.json").write_text('{"signature":"first"}', encoding="utf-8")
            self.store.publish_signed(record, parsed)
        self.fake.gets.clear()
        with self.store.workspace(record) as parsed:
            self.assertEqual(json.loads((parsed / "approved_evidence.json").read_text())["signature"], "first")
        self.assertEqual(len([k for k in self.fake.gets if "/signed/" in k]), 1)

    def test_editing_a_workspace_never_changes_the_cache(self):
        record = self._publish()
        with self.store.workspace(record) as parsed:
            (parsed / "document.json").write_text("changed", encoding="utf-8")
        with self.store.workspace(record) as parsed:
            self.assertIn("pages", json.loads((parsed / "document.json").read_text()))

    def test_cache_stays_under_its_size_cap(self):
        with patch.object(private_store, "CACHE_MAX_BYTES", 4000):
            for n in range(10):
                raw = bytes([n]) * 900
                private_store._cache_store(private_store._sha(raw), raw)
            total = sum(p.stat().st_size for p in private_store.CACHE_DIR.glob("*/*"))
        self.assertLessEqual(total, 4000)

    def test_artifact_mutation_is_rejected_before_engine(self):
        record = self._publish()
        key = f"materials/{self.course_id}/{self.material_id}/files/parsed/document.json"
        self.fake.objects[(self.store.bucket, key)] = b'{"tampered": true}'
        with self.assertRaises(HTTPException) as error:
            with self.store.workspace(record):
                pass
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("integrity", error.exception.detail)

    def test_material_ref_cannot_cross_courses(self):
        record = self._publish()
        record.course_id = "c" * 32
        with self.assertRaises(HTTPException) as error:
            with self.store.workspace(record):
                pass
        self.assertEqual(error.exception.status_code, 503)

    def test_unsafe_manifest_paths_are_rejected(self):
        record = self._publish()
        k = f"materials/{self.course_id}/{self.material_id}/manifest.json"
        manifest = json.loads(self.fake.objects[(self.store.bucket, k)])
        manifest["files"][0]["path"] = "parsed/../../secret.txt"
        self.fake.objects[(self.store.bucket, k)] = json.dumps(manifest).encode()
        with self.assertRaises(HTTPException):
            with self.store.workspace(record):
                pass

    def test_publish_requires_canonical_document(self):
        (self.root / "parsed" / "document.json").unlink()
        with self.assertRaises(HTTPException) as error:
            self.store.publish_new(self.root, self.course_id, self.material_id)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.fake.objects, {})
